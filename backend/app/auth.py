"""Accounts, sessions, API keys and sync device tokens.

One admin account (the owner), created from HUB_ADMIN_USER /
HUB_ADMIN_PASSWORD_HASH on the first start (install.sh writes the hash).
The owner can add people (role "member"): they sign in with their own user
name and password, use the chat from their device (their chats are kept
there, not on the Hub), and may be limited to the home network. Browser
sessions are random tokens in an HttpOnly cookie; only their SHA-256 is
stored. API keys (slqhub_...) and sync tokens (slqsync_...) are shown once
and stored hashed.
"""
from __future__ import annotations

import base64
import collections
import hashlib
import logging
import hmac
import os
import re
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
MEMBER = "member"
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,39}$")
MIN_PASSWORD = 10


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
    """By user name, ignoring case (people type it on a phone)."""
    with db.connect() as con:
        r = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if r is None:
            r = con.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
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


# ---------------------------------------------------------------- people (members)

def _check_username(con, username: str, user_id: int | None = None) -> str:
    username = (username or "").strip()
    if not USERNAME.match(username):
        raise ValueError("A user name is 2-40 letters, digits, dots, dashes or underscores.")
    r = con.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
    if r and r["id"] != user_id:
        raise ValueError(f"The user name {username!r} is taken.")
    return username


def _check_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"Use at least {MIN_PASSWORD} characters for the password.")
    return password


def create_person(display_name: str, username: str, password: str, away: bool = False,
                  may_run_code: bool = False) -> dict:
    name = " ".join((display_name or "").split())[:60]
    _check_password(password)
    with db.connect() as con:
        username = _check_username(con, username)
        cur = con.execute("INSERT INTO users (username, password_hash, created_at, role, display_name, away,"
                          " may_run_code) VALUES (?,?,?,?,?,?,?)",
                          (username, hash_password(password), db.utcnow(), MEMBER, name or username,
                           int(bool(away)), int(bool(may_run_code))))
    return get_person(cur.lastrowid)


def get_person(user_id: int) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT u.id, u.username, u.display_name, u.away, u.may_run_code, u.created_at,"
                        " (SELECT MAX(last_seen) FROM sessions s WHERE s.user_id=u.id) last_seen"
                        " FROM users u WHERE u.id=? AND u.role=?", (user_id, MEMBER)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["away"], d["may_run_code"] = bool(d["away"]), bool(d["may_run_code"])
    return d


def list_people() -> list[dict]:
    with db.connect() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM users WHERE role=? ORDER BY lower(display_name)",
                                            (MEMBER,)).fetchall()]
    return [p for p in (get_person(i) for i in ids) if p]


def update_person(user_id: int, display_name: str | None = None, username: str | None = None,
                  password: str | None = None, away: bool | None = None, may_run_code: bool | None = None) -> dict | None:
    if get_person(user_id) is None:
        return None
    fields: dict = {}
    with db.connect() as con:
        if display_name is not None:
            fields["display_name"] = " ".join(display_name.split())[:60]
        if username is not None:
            fields["username"] = _check_username(con, username, user_id)
        if password is not None:
            fields["password_hash"] = hash_password(_check_password(password))
        if away is not None:
            fields["away"] = int(bool(away))
        if may_run_code is not None:
            fields["may_run_code"] = int(bool(may_run_code))
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE users SET {sets} WHERE id=? AND role=?", (*fields.values(), user_id, MEMBER))
        if password is not None:
            con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))     # a new password signs out
    return get_person(user_id)


def sign_out_everywhere(user_id: int) -> int:
    with db.connect() as con:
        return con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,)).rowcount


def delete_person(user_id: int) -> bool:
    with db.connect() as con:
        return con.execute("DELETE FROM users WHERE id=? AND role=?", (user_id, MEMBER)).rowcount > 0


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
        r = con.execute("SELECT s.token_hash, s.expires_at, s.last_seen, u.id, u.username, u.role, u.display_name,"
                        " u.away, u.may_run_code FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
                        (_sha(token),)).fetchone()
        if not r or r["expires_at"] < now:
            return None
        # Sliding expiry, written at most once an hour.
        if (r["last_seen"] or "")[:13] != now[:13]:
            new_exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
            con.execute("UPDATE sessions SET last_seen=?, expires_at=? WHERE token_hash=?",
                        (now, new_exp, r["token_hash"]))
    return public_user(r)


def public_user(r) -> dict:
    """What the rest of the Hub knows about a signed-in user."""
    d = dict(r)
    return {"id": d["id"], "username": d["username"], "role": d.get("role") or "admin",
            "display_name": d.get("display_name") or "", "away": bool(d.get("away", 1)),
            "may_run_code": bool(d.get("may_run_code", 0))}


def is_member(user: dict | None) -> bool:
    return bool(user) and user.get("role") == MEMBER


def display_name(user: dict | None) -> str:
    if not user:
        return ""
    return user.get("display_name") or user.get("username") or ""


def delete_session(token: str | None) -> None:
    if token:
        with db.connect() as con:
            con.execute("DELETE FROM sessions WHERE token_hash=?", (_sha(token),))


# ---------------------------------------------------------------- API keys

def create_api_key(name: str, priority: str = "normal", allowed_ips: str = "", away: bool = False) -> dict:
    """away: the key also works through the proxy (hub.slq.dk), from outside the home network."""
    name = " ".join((name or "").split())[:60]
    if not name:
        raise ValueError("a key needs a name")
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
    raw = API_KEY_PREFIX + secrets.token_urlsafe(30)
    with db.connect() as con:
        try:
            cur = con.execute("INSERT INTO api_keys (name, key_hash, prefix, priority, allowed_ips, away, created_at)"
                              " VALUES (?,?,?,?,?,?,?)",
                              (name, _sha(raw), raw[:14], priority, allowed_ips.strip(), int(bool(away)),
                               db.utcnow()))
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise ValueError(f"there is already a key called {name!r}") from exc
            raise
    return {"id": cur.lastrowid, "name": name, "key": raw, "priority": priority, "away": bool(away)}


def list_api_keys() -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT id, name, prefix, priority, allowed_ips, away, created_at, last_used, enabled"
                           " FROM api_keys ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def update_api_key(key_id: int, **fields) -> bool:
    allowed = {k: v for k, v in fields.items() if k in ("priority", "allowed_ips", "enabled", "name", "away")}
    if "allowed_ips" in allowed:
        allowed["allowed_ips"] = str(allowed["allowed_ips"] or "").strip()
    for flag in ("enabled", "away"):
        if flag in allowed:
            allowed[flag] = int(bool(allowed[flag]))
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


def find_api_key(raw: str | None) -> dict | None:
    """The enabled key with this value, without marking it used."""
    if not raw or not raw.startswith(API_KEY_PREFIX):
        return None
    with db.connect() as con:
        r = con.execute("SELECT * FROM api_keys WHERE key_hash=? AND enabled=1", (_sha(raw),)).fetchone()
    return dict(r) if r else None


def touch_api_key(key_id: int) -> None:
    """Remember when a key was last let in (written at most once a minute)."""
    if time.time() - _last_used_write.get(key_id, 0) <= 60:
        return
    _last_used_write[key_id] = time.time()
    with db.connect() as con:
        con.execute("UPDATE api_keys SET last_used=? WHERE id=?", (db.utcnow(), key_id))



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
        r = con.execute("SELECT t.id tid, u.id, u.username, u.role, u.display_name, u.away, u.may_run_code"
                        " FROM sync_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=?",
                        (_sha(raw),)).fetchone()
        if not r:
            return None
        con.execute("UPDATE sync_tokens SET last_used=? WHERE id=?", (db.utcnow(), r["tid"]))
    return public_user(r)
