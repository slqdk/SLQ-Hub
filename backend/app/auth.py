"""Accounts, sessions, API keys and sync device tokens.

One admin account, created from HUB_ADMIN_USER / HUB_ADMIN_PASSWORD_HASH on
the first start (install.sh writes the hash). Browser sessions are random
tokens in an HttpOnly cookie; only their SHA-256 is stored. API keys
(slqhub_...) and sync tokens (slqsync_...) are shown once and stored hashed.
"""
from __future__ import annotations

import base64
import collections
import hashlib
import logging
import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db

log = logging.getLogger("hub.auth")

SESSION_COOKIE = "hub_session"
SESSION_DAYS = 30
API_KEY_PREFIX = "slqhub_"
SYNC_TOKEN_PREFIX = "slqsync_"
PRIORITIES = ("interactive", "normal", "background")


# ---------------------------------------------------------------- passwords

def hash_password(password: str, n: int = 2**15, r: int = 8, p: int = 1) -> str:
    """scrypt:n:r:p:salt:key - colons, not dollars: the hash travels through
    .env files, where Docker Compose would read $... as variables."""
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p,
                        maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt:{n}:{r}:{p}:{base64.b64encode(salt).decode()}:{base64.b64encode(dk).decode()}"


def _parts(stored: str) -> list[str] | None:
    sep = ":" if stored.startswith("scrypt:") else "$" if stored.startswith("scrypt$") else None
    if sep is None:
        return None
    parts = stored.split(sep)
    return parts if len(parts) == 6 and all(parts) else None


def hash_is_valid(stored: str) -> bool:
    parts = _parts(stored or "")
    if not parts:
        return False
    try:
        int(parts[1]), int(parts[2]), int(parts[3])
        base64.b64decode(parts[4], validate=True), base64.b64decode(parts[5], validate=True)
    except (ValueError, TypeError):
        return False
    return True


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = _parts(stored or "")
        if not parts:
            return False
        _, n, r, p, salt_b64, dk_b64 = parts
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r),
                            p=int(p), maxmem=64 * 1024 * 1024, dklen=len(base64.b64decode(dk_b64)))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- users

def ensure_admin(username: str, password_hash: str) -> None:
    """Create the admin from the environment when there is no user yet."""
    if not username or not password_hash:
        return
    if not hash_is_valid(password_hash):
        log.error("HUB_ADMIN_PASSWORD_HASH is not a valid password hash, so no admin was created. Set a new "
                  "password with: python -m app.cli set-password (or sign up from the LAN in the web UI).")
        return
    with db.connect() as con:
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        con.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                    (username, password_hash, db.utcnow()))


def user_count() -> int:
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user(username: str) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(r) if r else None


def set_password(username: str, password: str) -> bool:
    with db.connect() as con:
        cur = con.execute("UPDATE users SET password_hash=? WHERE username=?",
                          (hash_password(password), username))
        if cur.rowcount:
            con.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)",
                        (username,))
        return cur.rowcount > 0


def create_user(username: str, password: str) -> None:
    with db.connect() as con:
        con.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                    (username, hash_password(password), db.utcnow()))


# ---------------------------------------------------------------- login throttle

class Throttle:
    """At most `limit` failed logins per address in `window` seconds."""

    def __init__(self, limit: int = 8, window: float = 600):
        self.limit, self.window = limit, window
        self._fails: dict[str, collections.deque] = {}
        self._lock = threading.Lock()

    def blocked(self, ip: str) -> bool:
        with self._lock:
            q = self._fails.get(ip)
            now = time.time()
            while q and now - q[0] > self.window:
                q.popleft()
            return bool(q) and len(q) >= self.limit

    def fail(self, ip: str) -> None:
        with self._lock:
            self._fails.setdefault(ip, collections.deque()).append(time.time())

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)


throttle = Throttle()


# ---------------------------------------------------------------- sessions

def create_session(user_id: int, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with db.connect() as con:
        con.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen, user_agent)"
                    " VALUES (?,?,?,?,?,?)",
                    (_sha(token), user_id, now.isoformat(timespec="seconds"),
                     (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"),
                     now.isoformat(timespec="seconds"), (user_agent or "")[:200]))
    return token


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as con:
        r = con.execute("SELECT s.token_hash, s.expires_at, s.last_seen, u.id, u.username"
                        " FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
                        (_sha(token),)).fetchone()
        if not r or r["expires_at"] < now:
            return None
        # Sliding expiry, written at most once an hour.
        if (r["last_seen"] or "")[:13] != now[:13]:
            new_exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
            con.execute("UPDATE sessions SET last_seen=?, expires_at=? WHERE token_hash=?",
                        (now, new_exp, r["token_hash"]))
    return {"id": r["id"], "username": r["username"]}


def delete_session(token: str | None) -> None:
    if token:
        with db.connect() as con:
            con.execute("DELETE FROM sessions WHERE token_hash=?", (_sha(token),))


# ---------------------------------------------------------------- API keys

def create_api_key(name: str, priority: str = "normal", allowed_ips: str = "") -> dict:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise ValueError("a key needs a name")
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
    raw = API_KEY_PREFIX + secrets.token_urlsafe(30)
    with db.connect() as con:
        try:
            cur = con.execute("INSERT INTO api_keys (name, key_hash, prefix, priority, allowed_ips, created_at)"
                              " VALUES (?,?,?,?,?,?)",
                              (name, _sha(raw), raw[:14], priority, allowed_ips.strip(), db.utcnow()))
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise ValueError(f"there is already a key called {name!r}") from exc
            raise
    return {"id": cur.lastrowid, "name": name, "key": raw, "priority": priority}


def list_api_keys() -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT id, name, prefix, priority, allowed_ips, created_at, last_used, enabled"
                           " FROM api_keys ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def update_api_key(key_id: int, **fields) -> bool:
    allowed = {k: v for k, v in fields.items() if k in ("priority", "allowed_ips", "enabled", "name")}
    if "priority" in allowed and allowed["priority"] not in PRIORITIES:
        raise ValueError("bad priority")
    if not allowed:
        return False
    sets = ", ".join(f"{k}=?" for k in allowed)
    with db.connect() as con:
        return con.execute(f"UPDATE api_keys SET {sets} WHERE id=?",
                           (*allowed.values(), key_id)).rowcount > 0


def delete_api_key(key_id: int) -> bool:
    with db.connect() as con:
        return con.execute("DELETE FROM api_keys WHERE id=?", (key_id,)).rowcount > 0


_last_used_write: dict[int, float] = {}


def verify_api_key(raw: str | None) -> dict | None:
    if not raw or not raw.startswith(API_KEY_PREFIX):
        return None
    with db.connect() as con:
        r = con.execute("SELECT * FROM api_keys WHERE key_hash=? AND enabled=1", (_sha(raw),)).fetchone()
        if not r:
            return None
        if time.time() - _last_used_write.get(r["id"], 0) > 60:
            _last_used_write[r["id"]] = time.time()
            con.execute("UPDATE api_keys SET last_used=? WHERE id=?", (db.utcnow(), r["id"]))
    return dict(r)


# ---------------------------------------------------------------- sync device tokens

def create_sync_token(user_id: int, name: str) -> dict:
    name = " ".join((name or "").split())[:60] or "device"
    raw = SYNC_TOKEN_PREFIX + secrets.token_urlsafe(30)
    with db.connect() as con:
        cur = con.execute("INSERT INTO sync_tokens (user_id, name, token_hash, created_at) VALUES (?,?,?,?)",
                          (user_id, name, _sha(raw), db.utcnow()))
    return {"id": cur.lastrowid, "name": name, "token": raw}


def list_sync_tokens(user_id: int) -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT id, name, created_at, last_used FROM sync_tokens WHERE user_id=?"
                           " ORDER BY id", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def revoke_sync_token(user_id: int, token_id: int) -> bool:
    with db.connect() as con:
        return con.execute("DELETE FROM sync_tokens WHERE user_id=? AND id=?",
                           (user_id, token_id)).rowcount > 0


def user_for_sync_token(raw: str | None) -> dict | None:
    if not raw or not raw.startswith(SYNC_TOKEN_PREFIX):
        return None
    with db.connect() as con:
        r = con.execute("SELECT t.id tid, u.id, u.username FROM sync_tokens t JOIN users u ON u.id=t.user_id"
                        " WHERE t.token_hash=?", (_sha(raw),)).fetchone()
        if not r:
            return None
        con.execute("UPDATE sync_tokens SET last_used=? WHERE id=?", (db.utcnow(), r["tid"]))
    return {"id": r["id"], "username": r["username"]}
