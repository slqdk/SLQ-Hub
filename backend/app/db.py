"""SQLite: one file (<data>/hub.db), WAL, numbered migrations.

Connections are short-lived (`with db.connect() as con:`); SQLite is fast
enough that the async endpoints call it directly.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_path: Path | None = None
_init_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def configure(path: Path) -> None:
    global _path
    _path = path
    path.parent.mkdir(parents=True, exist_ok=True)
    with _init_lock:
        with connect() as con:
            migrate(con)


@contextmanager
def connect():
    if _path is None:
        raise RuntimeError("db.configure() has not been called")
    con = sqlite3.connect(_path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
    finally:
        con.close()


@contextmanager
def transaction():
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise


_M001_CORE = """
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen  TEXT,
            user_agent TEXT
        );
        CREATE TABLE api_keys (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            key_hash    TEXT NOT NULL UNIQUE,
            prefix      TEXT NOT NULL,
            priority    TEXT NOT NULL DEFAULT 'normal',
            allowed_ips TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            last_used   TEXT,
            enabled     INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE sync_tokens (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name       TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_used  TEXT
        );
        CREATE TABLE projects (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            instructions TEXT NOT NULL DEFAULT '',
            settings     TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            archived     INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE chats (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            title      TEXT NOT NULL DEFAULT 'New chat',
            settings   TEXT NOT NULL DEFAULT '{}',
            summary    TEXT NOT NULL DEFAULT '',
            summary_upto INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived   INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX chats_project ON chats(project_id, updated_at);
        CREATE TABLE messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            run_id     TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX messages_chat ON messages(chat_id, id);
        CREATE TABLE runs (
            id         TEXT PRIMARY KEY,
            chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            status     TEXT NOT NULL,
            model      TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            ended_at   TEXT,
            steps      TEXT NOT NULL DEFAULT '[]',
            usage      TEXT NOT NULL DEFAULT '{}',
            error      TEXT
        );
        CREATE INDEX runs_chat ON runs(chat_id, started_at);
        CREATE TABLE artifacts (
            id         TEXT NOT NULL,
            chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
            version    INTEGER NOT NULL,
            title      TEXT NOT NULL,
            type       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, id, version)
        );
        CREATE TABLE uploads (
            sha        TEXT PRIMARY KEY,
            filename   TEXT NOT NULL,
            mime       TEXT NOT NULL,
            size       INTEGER NOT NULL,
            width      INTEGER,
            height     INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE sync_revs (
            project_id INTEGER NOT NULL,
            rev        INTEGER NOT NULL,
            author     TEXT NOT NULL,
            origin     TEXT NOT NULL,
            message    TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, rev)
        );
        CREATE TABLE sync_changes (
            project_id INTEGER NOT NULL,
            rev        INTEGER NOT NULL,
            path       TEXT NOT NULL,
            sha        TEXT,
            size       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (project_id, rev, path)
        );
        CREATE INDEX sync_changes_path ON sync_changes (project_id, path, rev);
        CREATE INDEX sync_changes_sha ON sync_changes (project_id, sha);
        CREATE TABLE jobs_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            owner         TEXT NOT NULL,
            kind          TEXT NOT NULL,
            need          TEXT NOT NULL,
            priority      TEXT NOT NULL,
            label         TEXT NOT NULL DEFAULT '',
            enqueued_at   REAL NOT NULL,
            started_at    REAL,
            ended_at      REAL,
            state         TEXT NOT NULL,
            error         TEXT
        );
        CREATE INDEX jobs_log_time ON jobs_log(enqueued_at);
        CREATE TABLE tool_permissions (
            tool       TEXT PRIMARY KEY,
            decision   TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
"""


_M002_LIBRARIES = """
        CREATE TABLE libraries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            settings    TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE TABLE library_docs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
            filename   TEXT NOT NULL,
            sha        TEXT NOT NULL,
            mime       TEXT NOT NULL,
            size       INTEGER NOT NULL DEFAULT 0,
            pages      INTEGER NOT NULL DEFAULT 0,
            chunks     INTEGER NOT NULL DEFAULT 0,
            status     TEXT NOT NULL DEFAULT 'queued',
            progress   REAL NOT NULL DEFAULT 0,
            error      TEXT,
            meta       TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            indexed_at TEXT
        );
        CREATE INDEX library_docs_lib ON library_docs(library_id);
        CREATE TABLE library_chunks (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id  INTEGER NOT NULL REFERENCES library_docs(id) ON DELETE CASCADE,
            page    INTEGER NOT NULL,
            ord     INTEGER NOT NULL,
            text    TEXT NOT NULL,
            emb     BLOB
        );
        CREATE INDEX library_chunks_doc ON library_chunks(doc_id, page);
        CREATE TABLE library_pages (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id  INTEGER NOT NULL REFERENCES library_docs(id) ON DELETE CASCADE,
            page    INTEGER NOT NULL,
            image   TEXT NOT NULL,
            width   INTEGER,
            height  INTEGER,
            emb     BLOB
        );
        CREATE INDEX library_pages_doc ON library_pages(doc_id, page);
"""


MIGRATIONS = [
    (1, _M001_CORE),
    (2, _M002_LIBRARIES),
]


def migrate(con: sqlite3.Connection) -> None:
    """Each migration runs as one script inside its own transaction.
    (executescript() commits any open transaction first, so BEGIN/COMMIT
    are part of the script rather than issued around it.)"""
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    done = {r[0] for r in con.execute("SELECT version FROM schema_migrations")}
    for version, sql in MIGRATIONS:
        if version in done:
            continue
        script = (f"BEGIN IMMEDIATE;\n{sql}\n"
                  f"INSERT INTO schema_migrations (version, applied_at) VALUES ({int(version)}, '{utcnow()}');\n"
                  "COMMIT;")
        try:
            con.executescript(script)
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
