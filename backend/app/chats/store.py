"""Persistence for chats.

A message's content is a JSON list of blocks:

    {"type": "text", "text": ...}
    {"type": "image", "sha": ..., "mime": ..., "name": ...}        (user upload; "ref": true and
                                                                    "path" for a picture from the folder)
    {"type": "file", "sha": ..., "mime": ..., "name": ..., "size": ...}
    {"type": "file_ref", "path": ..., "name": ..., "size": ..., "mime": ...,
     "sha": ..., "lines": ..., "inline": true}                      (a file of the working folder; with
                                                                    "inline", its text then, in blobs)
    {"type": "thinking", "text": ...}                               (assistant)
    {"type": "tool_call", "id": ..., "name": ..., "arguments": "<json>"}
    {"type": "tool_result", "tool_call_id": ..., "name": ..., "text": ...,
     "images": [{"sha", "mime"}], "is_error": bool}                 (role "tool")
    {"type": "summary", "text": ...}                                (compaction)
"""
from __future__ import annotations

import io
import json

from PIL import Image

from .. import db
from ..projects import blobs

CHAT_DEFAULTS = {"model": None, "thinking": None, "effort": None, "skills": [], "libraries": [], "image_model": None}


def _chat(r) -> dict:
    d = dict(r)
    try:
        d["settings"] = {**CHAT_DEFAULTS, **json.loads(d.get("settings") or "{}")}
    except ValueError:
        d["settings"] = dict(CHAT_DEFAULTS)
    return d


def create_chat(project_id: int | None = None, title: str = "New chat", settings: dict | None = None) -> dict:
    now = db.utcnow()
    with db.connect() as con:
        cur = con.execute("INSERT INTO chats (project_id, title, settings, created_at, updated_at) VALUES (?,?,?,?,?)",
                          (project_id, title[:200], json.dumps({**CHAT_DEFAULTS, **(settings or {})}), now, now))
    return get_chat(cur.lastrowid)


def get_chat(chat_id: int) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    return _chat(r) if r else None


def list_chats(project_id: int | None = None, q: str = "", limit: int = 200, all_projects: bool = False) -> list[dict]:
    sql = ("SELECT c.id, c.project_id, c.title, c.updated_at, c.created_at, p.name project_name FROM chats c"
           " LEFT JOIN projects p ON p.id=c.project_id WHERE c.archived=0")
    params: list = []
    if not all_projects:
        if project_id is None:
            sql += " AND c.project_id IS NULL"
        else:
            sql += " AND c.project_id=?"
            params.append(project_id)
    if q:
        sql += " AND (c.title LIKE ? OR c.id IN (SELECT chat_id FROM messages WHERE content LIKE ?))"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY c.updated_at DESC LIMIT ?"
    params.append(limit)
    with db.connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def update_chat(chat_id: int, title: str | None = None, settings: dict | None = None,
                summary: str | None = None, summary_upto: int | None = None) -> dict | None:
    c = get_chat(chat_id)
    if not c:
        return None
    fields: dict = {"updated_at": db.utcnow()}
    if title is not None:
        fields["title"] = " ".join(title.split())[:200] or c["title"]
    if settings is not None:
        fields["settings"] = json.dumps({**c["settings"], **{k: v for k, v in settings.items() if k in CHAT_DEFAULTS}})
    if summary is not None:
        fields["summary"] = summary
    if summary_upto is not None:
        fields["summary_upto"] = summary_upto
    sets = ", ".join(f"{k}=?" for k in fields)
    with db.connect() as con:
        con.execute(f"UPDATE chats SET {sets} WHERE id=?", (*fields.values(), chat_id))
    return get_chat(chat_id)


def delete_chat(chat_id: int) -> bool:
    with db.connect() as con:
        return con.execute("DELETE FROM chats WHERE id=?", (chat_id,)).rowcount > 0


# ---------------------------------------------------------------- messages

def add_message(chat_id: int, role: str, content: list[dict], run_id: str | None = None) -> dict:
    now = db.utcnow()
    with db.connect() as con:
        cur = con.execute("INSERT INTO messages (chat_id, role, content, run_id, created_at) VALUES (?,?,?,?,?)",
                          (chat_id, role, json.dumps(content, ensure_ascii=False), run_id, now))
        con.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
    return {"id": cur.lastrowid, "chat_id": chat_id, "role": role, "content": content, "run_id": run_id,
            "created_at": now}


def update_message(message_id: int, content: list[dict]) -> None:
    with db.connect() as con:
        con.execute("UPDATE messages SET content=? WHERE id=?", (json.dumps(content, ensure_ascii=False), message_id))


def messages(chat_id: int, after_id: int = 0) -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT * FROM messages WHERE chat_id=? AND id>? ORDER BY id", (chat_id, after_id)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["content"] = json.loads(d["content"])
        out.append(d)
    return out


def delete_messages_after(chat_id: int, message_id: int) -> int:
    with db.connect() as con:
        return con.execute("DELETE FROM messages WHERE chat_id=? AND id>?", (chat_id, message_id)).rowcount


# ---------------------------------------------------------------- runs

def run_started(run_id: str, chat_id: int, model: str) -> None:
    with db.connect() as con:
        con.execute("INSERT INTO runs (id, chat_id, status, model, started_at) VALUES (?,?,?,?,?)",
                    (run_id, chat_id, "running", model, db.utcnow()))


def run_finished(run_id: str, status: str, steps: list, usage: dict, error: str | None = None) -> None:
    with db.connect() as con:
        con.execute("UPDATE runs SET status=?, ended_at=?, steps=?, usage=?, error=? WHERE id=?",
                    (status, db.utcnow(), json.dumps(steps), json.dumps(usage), error, run_id))


def runs_for_chat(chat_id: int) -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT * FROM runs WHERE chat_id=? ORDER BY started_at", (chat_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["steps"] = json.loads(d["steps"] or "[]")
        d["usage"] = json.loads(d["usage"] or "{}")
        out.append(d)
    return out


# ---------------------------------------------------------------- artifacts

def save_artifact(chat_id: int, artifact_id: str, title: str, kind: str, content: str) -> int:
    with db.connect() as con:
        r = con.execute("SELECT MAX(version) FROM artifacts WHERE chat_id=? AND id=?", (chat_id, artifact_id)).fetchone()
        version = (r[0] or 0) + 1
        con.execute("INSERT INTO artifacts (id, chat_id, version, title, type, content, created_at) VALUES (?,?,?,?,?,?,?)",
                    (artifact_id, chat_id, version, title[:200], kind, content, db.utcnow()))
    return version


def get_artifact(chat_id: int, artifact_id: str, version: int | None = None) -> dict | None:
    with db.connect() as con:
        if version:
            r = con.execute("SELECT * FROM artifacts WHERE chat_id=? AND id=? AND version=?",
                            (chat_id, artifact_id, version)).fetchone()
        else:
            r = con.execute("SELECT * FROM artifacts WHERE chat_id=? AND id=? ORDER BY version DESC LIMIT 1",
                            (chat_id, artifact_id)).fetchone()
    return dict(r) if r else None


def list_artifacts(chat_id: int) -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT id, title, type, MAX(version) version, MAX(created_at) updated FROM artifacts"
                           " WHERE chat_id=? GROUP BY id ORDER BY updated", (chat_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- uploads (and generated images)

def save_upload(data: bytes, filename: str, mime: str) -> dict:
    sha = blobs.store(data)
    width = height = None
    if mime.startswith("image/"):
        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
        except Exception:  # noqa: BLE001 - not every "image" opens
            pass
    with db.connect() as con:
        con.execute("INSERT OR IGNORE INTO uploads (sha, filename, mime, size, width, height, created_at)"
                    " VALUES (?,?,?,?,?,?,?)", (sha, filename[:200], mime, len(data), width, height, db.utcnow()))
    return {"sha": sha, "name": filename, "mime": mime, "size": len(data), "width": width, "height": height}


def get_upload(sha: str) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM uploads WHERE sha=?", (sha,)).fetchone()
    return dict(r) if r else None
