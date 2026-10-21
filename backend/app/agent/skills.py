"""Skills: folders with a SKILL.md (front matter `name`, `description`) and
any files it refers to - the same format as Claude's and Hermes' skills.

They live in <data>/skills/<name>/. Only the name and description of the
skills enabled for a chat or project go into the prompt; the model loads a
skill's text with the `skill` tool when it needs it."""
from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_SKILL_BYTES = 50 * 1024 * 1024


def parse_front_matter(text: str) -> tuple[dict, str]:
    """A small YAML subset: `key: value`, folded (>) and literal (|) blocks."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head, body = text[3:end].strip("\n"), text[end + 4:].lstrip("\n")
    meta: dict = {}
    key = None
    block: list[str] = []
    style = None

    def flush():
        if key is not None and style:
            meta[key] = (" " if style == ">" else "\n").join(s.strip() for s in block).strip()

    for line in head.splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            flush()
            key, val = m.group(1), m.group(2).strip()
            style, block = None, []
            if val in (">", "|", ">-", "|-"):
                style = val[0]
            else:
                meta[key] = val.strip("'\"")
        elif style and key:
            block.append(line)
        elif key and key in meta and line.strip():           # continuation of a plain scalar
            meta[key] = f"{meta[key]} {line.strip()}"
    flush()
    return meta, body


class Skills:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _dir(self, name: str) -> Path:
        if not NAME_RE.match(name or ""):
            raise ValueError(f"not a skill name: {name!r}")
        return self.root / name

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            f = d / "SKILL.md"
            if not f.is_file():
                continue
            meta, _ = parse_front_matter(f.read_text(encoding="utf-8", errors="replace"))
            files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and p.name != "SKILL.md")
            out.append({"name": d.name, "title": meta.get("name") or d.name,
                        "description": meta.get("description", "")[:1024], "files": files[:200],
                        "enabled": d.name not in self._disabled()})
        return out

    # Skills are offered in every chat unless switched off here; a project or
    # a chat can still add a switched-off skill (see chosen()).
    def _disabled(self) -> set[str]:
        try:
            return set(json.loads((self.root / ".disabled.json").read_text()))
        except (OSError, ValueError):
            return set()

    def set_enabled(self, name: str, enabled: bool) -> None:
        if not (self._dir(name) / "SKILL.md").is_file():
            raise FileNotFoundError(name)
        off = self._disabled()
        off.discard(name) if enabled else off.add(name)
        (self.root / ".disabled.json").write_text(json.dumps(sorted(off)))

    def chosen(self, chat: dict | None, project: dict | None) -> list[dict]:
        """The skills offered to the model: switched on globally, plus those the
        project or the chat adds."""
        extra = set((chat or {}).get("settings", {}).get("skills") or [])
        extra |= set((project or {}).get("settings", {}).get("skills") or [])
        return [s for s in self.list() if s["enabled"] or s["name"] in extra]

    def get(self, name: str) -> str:
        f = self._dir(name) / "SKILL.md"
        if not f.is_file():
            raise FileNotFoundError(name)
        return f.read_text(encoding="utf-8", errors="replace")

    def read_file(self, name: str, rel: str) -> bytes:
        d = self._dir(name)
        p = (d / rel).resolve()
        if d.resolve() not in p.parents or not p.is_file():
            raise FileNotFoundError(rel)
        return p.read_bytes()

    def install(self, data: bytes, filename: str) -> str:
        """A .zip/.skill bundle (with SKILL.md at its root or in one folder),
        or a bare SKILL.md. Returns the skill's folder name."""
        if filename.lower().endswith((".zip", ".skill")) or data[:2] == b"PK":
            return self._install_zip(data)
        meta, _ = parse_front_matter(data.decode("utf-8", "replace"))
        name = _slug(meta.get("name") or Path(filename).stem)
        d = self._dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_bytes(data)
        return name

    def _install_zip(self, data: bytes) -> str:
        if len(data) > MAX_SKILL_BYTES:
            raise ValueError("the bundle is larger than 50 MB")
        z = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in z.namelist() if not n.endswith("/")]
        if sum(i.file_size for i in z.infolist()) > 4 * MAX_SKILL_BYTES:
            raise ValueError("the bundle unpacks to more than 200 MB")
        skill_md = sorted((n for n in names if PurePosixPath(n).name == "SKILL.md"), key=lambda n: n.count("/"))
        if not skill_md:
            raise ValueError("no SKILL.md in the bundle")
        prefix = str(PurePosixPath(skill_md[0]).parent)
        prefix = "" if prefix == "." else prefix + "/"
        meta, _ = parse_front_matter(z.read(skill_md[0]).decode("utf-8", "replace"))
        name = _slug(meta.get("name") or (prefix.strip("/").split("/")[-1] if prefix else "skill"))
        d = self._dir(name)
        tmp = d.with_name(d.name + ".installing")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for n in names:
            if prefix and not n.startswith(prefix):
                continue
            rel = n[len(prefix):]
            if not rel or rel.startswith("/") or ".." in PurePosixPath(rel).parts:
                continue
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
        shutil.rmtree(d, ignore_errors=True)
        tmp.rename(d)
        return name

    def delete(self, name: str) -> bool:
        d = self._dir(name)
        if not d.exists():
            return False
        shutil.rmtree(d)
        return True


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-.")
    return (s or "skill")[:64]
