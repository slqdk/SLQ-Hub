"""Libraries: documents the agent can search (multimodal RAG).

Every document becomes
* text chunks, embedded with Qwen3-Embedding (CPU), and
* page images (PDF pages, pictures), embedded with Qwen3-VL-Embedding - so
  a diagram, a table or a scanned page can be found by what it shows.

A search embeds the question both ways, fuses the two rankings (reciprocal
rank fusion), re-ranks the best text chunks with Qwen3-Reranker and returns
the hits - with the best page images, which the vision model then reads
itself. Indexing runs one document at a time in the background; with
page_images = "gpu" the image embedder runs on the card as a background
queue job (fast, but it unloads the chat model while it works).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from .. import db
from ..gpu.runner import AUX_PORTS, RunnerError
from ..llm.client import LlmError
from ..projects import blobs
from . import extract

log = logging.getLogger("hub.rag")

DOC_INSTRUCTION = "Represent the user's input."
QUERY_INSTRUCTION_VL = "Retrieve document pages that are relevant to the user's query."
RRF_K = 60
GPU_SLICE_S = 90          # give the card back this often while indexing on the GPU


class LibraryError(ValueError):
    pass


def _vl_input(instruction: str, text: str = "", image: bytes | None = None) -> dict:
    """Qwen3-VL-Embedding's chat format, written out (llama-server adds the
    content parts as they are; it does not apply a template for embeddings)."""
    parts: list[dict] = [{"type": "text", "text": f"<|im_start|>system\n{instruction}<|im_end|>\n<|im_start|>user\n"}]
    if image is not None:
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}})
    parts.append({"type": "text", "text": f"{text}<|im_end|>\n<|im_start|>assistant\n"})
    return {"content": parts}


def _vec(v) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(a))
    return a / n if n else a


def _pack(v: np.ndarray) -> bytes:
    return v.astype(np.float16).tobytes()


def _unpack(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float16).astype(np.float32)


@dataclass
class _Index:
    version: int
    chunk_ids: list[int] = field(default_factory=list)
    chunk_mat: np.ndarray | None = None
    page_ids: list[int] = field(default_factory=list)
    page_mat: np.ndarray | None = None


@dataclass
class Hit:
    doc_id: int
    filename: str
    page: int
    text: str
    score: float
    image: str | None = None             # page image sha
    chunk_id: int | None = None
    via: list[str] = field(default_factory=list)

    def ref(self) -> str:
        return f"{self.filename}, page {self.page}" if self.page else self.filename


class Libraries:
    def __init__(self, hub):
        self.hub = hub
        self._cache: dict[int, _Index] = {}
        self._versions: dict[int, int] = {}
        self._wake: asyncio.Event | None = None
        self._worker: asyncio.Task | None = None
        self.current: dict | None = None

    # ------------------------------------------------------------ settings
    def cfg(self, key: str):
        return self.hub.settings.get("libraries", key)

    def available(self) -> bool:
        return self.hub.aux.available("embed-text")

    # ------------------------------------------------------------ libraries
    def list(self) -> list[dict]:
        with db.connect() as con:
            rows = con.execute(
                "SELECT l.*, COUNT(d.id) docs, SUM(d.status='ready') ready, SUM(d.status IN ('queued','indexing')) busy,"
                " SUM(d.status='error') errors, COALESCE(SUM(d.size),0) bytes, COALESCE(SUM(d.pages),0) pages"
                " FROM libraries l LEFT JOIN library_docs d ON d.library_id=l.id GROUP BY l.id ORDER BY l.name").fetchall()
        return [self._lib(r) for r in rows]

    @staticmethod
    def _lib(r) -> dict:
        d = dict(r)
        try:
            d["settings"] = json.loads(d.get("settings") or "{}")
        except ValueError:
            d["settings"] = {}
        for k in ("docs", "ready", "busy", "errors"):
            if k in d:
                d[k] = int(d[k] or 0)
        return d

    def get(self, lib_id: int) -> dict | None:
        with db.connect() as con:
            r = con.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
        return self._lib(r) if r else None

    def create(self, name: str, description: str = "") -> dict:
        name = " ".join((name or "").split())[:120]
        if not name:
            raise LibraryError("a library needs a name")
        now = db.utcnow()
        with db.connect() as con:
            cur = con.execute("INSERT INTO libraries (name, description, settings, created_at, updated_at)"
                              " VALUES (?,?,?,?,?)", (name, description or "", "{}", now, now))
        return self.get(cur.lastrowid)

    def update(self, lib_id: int, name: str | None = None, description: str | None = None) -> dict | None:
        lib = self.get(lib_id)
        if not lib:
            return None
        with db.connect() as con:
            con.execute("UPDATE libraries SET name=?, description=?, updated_at=? WHERE id=?",
                        (" ".join((name or lib["name"]).split())[:120], lib["description"] if description is None
                         else description, db.utcnow(), lib_id))
        return self.get(lib_id)

    def delete(self, lib_id: int) -> bool:
        with db.connect() as con:
            n = con.execute("DELETE FROM libraries WHERE id=?", (lib_id,)).rowcount
        self._cache.pop(lib_id, None)
        return n > 0

    # ------------------------------------------------------------ documents
    def docs(self, lib_id: int) -> list[dict]:
        with db.connect() as con:
            rows = con.execute("SELECT id, library_id, filename, sha, mime, size, pages, chunks, status, progress,"
                               " error, created_at, indexed_at FROM library_docs WHERE library_id=? ORDER BY filename",
                               (lib_id,)).fetchall()
        return [dict(r) for r in rows]

    def doc(self, doc_id: int) -> dict | None:
        with db.connect() as con:
            r = con.execute("SELECT * FROM library_docs WHERE id=?", (doc_id,)).fetchone()
        return dict(r) if r else None

    def add(self, lib_id: int, data: bytes, filename: str, mime: str) -> dict:
        if not self.get(lib_id):
            raise LibraryError("no such library")
        extract.kind_of(filename, mime)               # refuse what we cannot read, now
        sha = blobs.store(data)
        with db.connect() as con:
            old = con.execute("SELECT id FROM library_docs WHERE library_id=? AND filename=?",
                              (lib_id, filename)).fetchone()
            if old:                                   # a new version of the same file replaces it
                con.execute("UPDATE library_docs SET sha=?, mime=?, size=?, status='queued', progress=0, error=NULL"
                            " WHERE id=?", (sha, mime, len(data), old["id"]))
                doc_id = old["id"]
            else:
                doc_id = con.execute("INSERT INTO library_docs (library_id, filename, sha, mime, size, created_at)"
                                     " VALUES (?,?,?,?,?,?)",
                                     (lib_id, filename, sha, mime, len(data), db.utcnow())).lastrowid
        self.kick()
        return self.doc(doc_id)

    def remove(self, doc_id: int) -> bool:
        d = self.doc(doc_id)
        if not d:
            return False
        with db.connect() as con:
            con.execute("DELETE FROM library_docs WHERE id=?", (doc_id,))
        self._bump(d["library_id"])
        return True

    def reindex(self, doc_id: int) -> bool:
        with db.connect() as con:
            n = con.execute("UPDATE library_docs SET status='queued', progress=0, error=NULL WHERE id=?",
                            (doc_id,)).rowcount
        self.kick()
        return n > 0

    def _set(self, doc_id: int, **fields) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        with db.connect() as con:
            con.execute(f"UPDATE library_docs SET {sets} WHERE id=?", (*fields.values(), doc_id))

    def _bump(self, lib_id: int) -> None:
        self._versions[lib_id] = self._versions.get(lib_id, 0) + 1
        self._cache.pop(lib_id, None)

    # ------------------------------------------------------------ indexing
    def start(self) -> None:
        with db.connect() as con:                     # whatever was cut off by a restart starts again
            con.execute("UPDATE library_docs SET status='queued' WHERE status='indexing'")
        self.kick()

    def kick(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._wake is None or self._worker is None or self._worker.done():
            self._wake = asyncio.Event()
            self._worker = loop.create_task(self._run(), name="library-indexer")
        self._wake.set()

    async def _run(self) -> None:
        while True:
            with db.connect() as con:
                r = con.execute("SELECT * FROM library_docs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if r is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 300)
                except asyncio.TimeoutError:
                    pass
                continue
            doc = dict(r)
            self.current = doc
            try:
                await self._index(doc)
            except asyncio.CancelledError:
                self._set(doc["id"], status="queued")
                raise
            except (extract.Unsupported, LibraryError, LlmError, RunnerError, OSError) as exc:
                self._set(doc["id"], status="error", error=str(exc)[:500])
            except Exception as exc:  # noqa: BLE001
                log.exception("indexing %s failed", doc["filename"])
                self._set(doc["id"], status="error", error=f"{type(exc).__name__}: {exc}"[:500])
            finally:
                self.current = None

    async def _index(self, doc: dict) -> None:
        self._set(doc["id"], status="indexing", progress=0.0, error=None)
        mode = self.cfg("page_images") or "cpu"
        data = blobs.read(doc["sha"])
        pages = await asyncio.to_thread(extract.pages, data, doc["filename"], doc["mime"], mode != "off")
        pieces = [(p.number, i, t) for p in pages for i, t in enumerate(extract.chunks(p.text))]
        with_images = [p for p in pages if p.image] if mode != "off" else []
        total = max(1, len(pieces) + len(with_images) * 4)          # a page image costs more than a chunk
        done = 0

        vectors: list[bytes] = []
        if pieces:
            base = await self.hub.aux.ensure("embed-text")
            for i in range(0, len(pieces), 16):
                batch = [t for _, _, t in pieces[i:i + 16]]
                for v in await self.hub.llm.embeddings(base, batch):
                    vectors.append(_pack(_vec(v)))
                self.hub.aux.touch("embed-text")
                done += len(batch)
                self._set(doc["id"], progress=round(done / total, 3))

        page_rows: list[tuple] = []
        vl_model = None
        if with_images and self.hub.aux.available("embed-vl"):
            vl_model = "embed-vl"
            async for page, vec in self._embed_pages(doc, with_images, mode):
                sha = blobs.store(page.image)
                page_rows.append((page.number, sha, page.width, page.height, _pack(vec)))
                done += 4
                self._set(doc["id"], progress=round(min(done / total, 0.99), 3))
        elif with_images:                            # no image embedder: keep the pictures for the model
            for page in with_images:
                page_rows.append((page.number, blobs.store(page.image), page.width, page.height, None))

        meta = {"text_model": "embed-text" if pieces else None, "vl_model": vl_model, "mode": mode}
        with db.transaction() as con:
            con.execute("DELETE FROM library_chunks WHERE doc_id=?", (doc["id"],))
            con.execute("DELETE FROM library_pages WHERE doc_id=?", (doc["id"],))
            con.executemany("INSERT INTO library_chunks (doc_id, page, ord, text, emb) VALUES (?,?,?,?,?)",
                            [(doc["id"], pg, o, t, v) for (pg, o, t), v in zip(pieces, vectors)])
            con.executemany("INSERT INTO library_pages (doc_id, page, image, width, height, emb) VALUES (?,?,?,?,?,?)",
                            [(doc["id"], *row) for row in page_rows])
            con.execute("UPDATE library_docs SET status='ready', progress=1, pages=?, chunks=?, meta=?, indexed_at=?,"
                        " error=NULL WHERE id=?", (len(pages), len(pieces), json.dumps(meta), db.utcnow(), doc["id"]))
        self._bump(doc["library_id"])

    async def _embed_pages(self, doc: dict, pages: list, mode: str):
        if mode != "gpu":
            base = await self.hub.aux.ensure("embed-vl")
            for p in pages:
                v = (await self.hub.llm.embeddings(base, [_vl_input(DOC_INSTRUCTION, image=p.image)]))[0]
                self.hub.aux.touch("embed-vl")
                yield p, _vec(v)
            return
        base = self.hub.runner.base_url(AUX_PORTS["embed-gpu"])
        i = 0
        while i < len(pages):
            ticket = await self.hub.scheduler.acquire("library", "index", "embedgpu:embed-vl", "background",
                                                      label=f"index {doc['filename']}"[:80])
            try:
                t0 = time.monotonic()
                while i < len(pages) and time.monotonic() - t0 < GPU_SLICE_S:
                    v = (await self.hub.llm.embeddings(base, [_vl_input(DOC_INSTRUCTION, image=pages[i].image)]))[0]
                    yield pages[i], _vec(v)
                    i += 1
            finally:
                await ticket.release()

    # ------------------------------------------------------------ search
    def _index_for(self, lib_id: int) -> _Index:
        ver = self._versions.get(lib_id, 0)
        cached = self._cache.get(lib_id)
        if cached and cached.version == ver:
            return cached
        idx = _Index(ver)
        with db.connect() as con:
            rows = con.execute("SELECT c.id, c.emb FROM library_chunks c JOIN library_docs d ON d.id=c.doc_id"
                               " WHERE d.library_id=? AND d.status='ready' AND c.emb IS NOT NULL", (lib_id,)).fetchall()
            prow = con.execute("SELECT p.id, p.emb FROM library_pages p JOIN library_docs d ON d.id=p.doc_id"
                               " WHERE d.library_id=? AND d.status='ready' AND p.emb IS NOT NULL", (lib_id,)).fetchall()
        idx.chunk_ids, idx.chunk_mat = self._matrix(rows)
        idx.page_ids, idx.page_mat = self._matrix(prow)
        self._cache[lib_id] = idx
        return idx

    @staticmethod
    def _matrix(rows) -> tuple[list[int], np.ndarray | None]:
        if not rows:
            return [], None
        vecs = [_unpack(r["emb"]) for r in rows]
        dim = max(set(len(v) for v in vecs), key=[len(v) for v in vecs].count)   # the common size wins
        keep = [(r["id"], v) for r, v in zip(rows, vecs) if len(v) == dim]
        return [k for k, _ in keep], np.stack([v for _, v in keep])

    async def search(self, lib_ids: list[int], query: str, k: int | None = None) -> list[Hit]:
        query = " ".join((query or "").split())
        if not query:
            raise LibraryError("the query is empty")
        k = int(k or self.cfg("results") or 6)
        indexes = [self._index_for(i) for i in lib_ids]
        chunk_rank: list[tuple[int, float]] = []
        page_rank: list[tuple[int, float]] = []
        if any(ix.chunk_mat is not None for ix in indexes):
            base = await self.hub.aux.ensure("embed-text")
            instr = (self.hub.catalog.get("aux", "embed-text") or {}).get("query_instruction") or \
                "Given a search query, retrieve relevant passages that answer the query"
            qv = _vec((await self.hub.llm.embeddings(base, [f"Instruct: {instr}\nQuery:{query}"]))[0])
            for ix in indexes:
                if ix.chunk_mat is not None and ix.chunk_mat.shape[1] == qv.shape[0]:
                    s = ix.chunk_mat @ qv
                    for j in np.argsort(-s)[:40]:
                        chunk_rank.append((ix.chunk_ids[j], float(s[j])))
        if any(ix.page_mat is not None for ix in indexes) and self.hub.aux.available("embed-vl"):
            base = await self.hub.aux.ensure("embed-vl")
            pv = _vec((await self.hub.llm.embeddings(base, [_vl_input(QUERY_INSTRUCTION_VL, text=query)]))[0])
            for ix in indexes:
                if ix.page_mat is not None and ix.page_mat.shape[1] == pv.shape[0]:
                    s = ix.page_mat @ pv
                    for j in np.argsort(-s)[:12]:
                        page_rank.append((ix.page_ids[j], float(s[j])))
        chunk_rank.sort(key=lambda x: -x[1])
        page_rank.sort(key=lambda x: -x[1])
        hits = self._fuse(chunk_rank[:40], page_rank[:12])
        if self.cfg("rerank") and self.hub.aux.available("rerank") and len(hits) > 1:
            hits = await self._rerank(query, hits[:24])
        return hits[:k]

    def _fuse(self, chunks: list[tuple[int, float]], pages: list[tuple[int, float]]) -> list[Hit]:
        with db.connect() as con:
            crow = {r["id"]: r for r in con.execute(
                f"SELECT c.id, c.doc_id, c.page, c.text, d.filename FROM library_chunks c JOIN library_docs d"
                f" ON d.id=c.doc_id WHERE c.id IN ({','.join('?' * len(chunks)) or 'NULL'})",
                [c for c, _ in chunks])}
            prow = {r["id"]: r for r in con.execute(
                f"SELECT p.id, p.doc_id, p.page, p.image, d.filename FROM library_pages p JOIN library_docs d"
                f" ON d.id=p.doc_id WHERE p.id IN ({','.join('?' * len(pages)) or 'NULL'})", [p for p, _ in pages])}
            doc_ids = sorted({r["doc_id"] for r in crow.values()})
            images = {(r["doc_id"], r["page"]): r["image"] for r in con.execute(
                f"SELECT doc_id, page, image FROM library_pages WHERE doc_id IN "
                f"({','.join('?' * len(doc_ids)) or 'NULL'})", doc_ids)}
        hits: dict[tuple, Hit] = {}
        score: dict[tuple, float] = {}
        for rank, (cid, sim) in enumerate(chunks):
            r = crow.get(cid)
            if r is None:
                continue
            key = ("c", cid)
            hits[key] = Hit(r["doc_id"], r["filename"], r["page"], r["text"], sim,
                            images.get((r["doc_id"], r["page"])), cid, ["text"])
            score[key] = 1 / (RRF_K + rank)
        for rank, (pid, sim) in enumerate(pages):
            r = prow.get(pid)
            if r is None:
                continue
            same = [key for key, h in hits.items() if h.doc_id == r["doc_id"] and h.page == r["page"]]
            if same:                                  # the page's best chunk gets the page's vote
                best = max(same, key=lambda key: score[key])
                score[best] += 1 / (RRF_K + rank)
                hits[best].via.append("page")
            else:
                key = ("p", pid)
                hits[key] = Hit(r["doc_id"], r["filename"], r["page"], "", sim, r["image"], None, ["page"])
                score[key] = 1 / (RRF_K + rank)
        order = sorted(hits, key=lambda key: -score[key])
        out = []
        for key in order:
            h = hits[key]
            h.score = round(score[key] * RRF_K, 4)
            out.append(h)
        return out

    async def _rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        """Re-rank the text hits. What the re-ranker calls irrelevant goes
        below the page-only hits (scanned pages, pictures): those it cannot
        judge, and the visual search found them."""
        texted = [h for h in hits if h.text]
        if len(texted) < 2:
            return hits
        try:
            base = await self.hub.aux.ensure("rerank")
            res = await self.hub.llm.rerank(base, query, [h.text[:4000] for h in texted])
        except (LlmError, RunnerError) as exc:
            log.warning("rerank failed: %s", exc)
            return hits
        scores = {int(r["index"]): float(r.get("relevance_score", 0)) for r in res if "index" in r}
        if not scores:
            return hits
        # llama.cpp gives log-odds for Qwen3-Reranker; some builds give a probability.
        cut = 0.5 if all(0.0 <= v <= 1.0 for v in scores.values()) else 0.0
        ranked = sorted(range(len(texted)), key=lambda i: -scores.get(i, -1e9))
        for i in ranked:
            texted[i].score = round(scores.get(i, 0.0), 4)
        good = [texted[i] for i in ranked if scores.get(i, -1e9) > cut]
        weak = [texted[i] for i in ranked if scores.get(i, -1e9) <= cut]
        return good + [h for h in hits if not h.text] + weak
