"""Instagram (0.76.0, Søren: "Are there tools where i automatically can upload to platforms, like Instagram ... when i
approve the post?" - "have the Hub post there directly").

A persona's post goes to its own Instagram account when the owner approves it, straight from the Hub: Meta's
Instagram API with Instagram Login, through an app of the owner's own in Meta's developer dashboard. Its Instagram
app ID and secret are kept like the download sign-ins (settings.json on the VM, section "instagram", masked, in no
export); the app stays in development mode, for the professional accounts it lists as testers - no review.

* Connecting: the owner signs in to the persona's account at instagram.com (force_reauth: not his own account the
  browser is signed in with), Instagram sends the browser back to the Hub's address on the internet
  (HUB_PUBLIC_URL) with a code, the Hub swaps it for a token good for 60 days and keeps it in
  data/personas/<id>/instagram.json (never in persona.json, never shown) - and renews it every few days.
* Posting: Instagram fetches the pictures and videos itself, from a public address. The Hub makes each ready (a
  JPEG between 4:5 and 1.91:1, at most 1440 wide; a video re-encoded by ffmpeg in the sandbox to what Reels take)
  and hands Instagram a signed link to it under /hub/media/persona/ that works for two hours. One picture or video
  is a post or a reel, more are a carousel; Instagram's id and link are kept with the post. Nothing is taken back:
  the API cannot delete a post.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import logging
import math
import os
import re
import secrets
import shlex
import shutil
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

log = logging.getLogger(__name__)

GRAPH = "https://graph.instagram.com"
VERSION = "v26.0"
AUTHORIZE = "https://www.instagram.com/oauth/authorize"
CODE_URL = "https://api.instagram.com/oauth/access_token"
SCOPES = "instagram_business_basic,instagram_business_content_publish"
CALLBACK = "/hub/api/personas/instagram/callback"
TMP = "instagram-tmp"                  # the sandbox's folder for videos made ready (sandbox_job)
CAPTION_MAX = 2200                     # Instagram's limits for a caption
HASHTAGS_MAX = 30
MENTIONS_MAX = 20
RATIO_MIN, RATIO_MAX = 4 / 5, 1.91     # a picture's width / height
WIDTH_MIN, WIDTH_MAX = 320, 1440
JPEG_MAX = 8 * 1024 * 1024
LINK_TTL = 2 * 3600                    # a signed link to a file made ready for Instagram
STATE_TTL = 15 * 60                    # a sign-in started on the Hub
REFRESH_AFTER = 5 * 86400              # a token is renewed after this (good for 60 days; at least a day old)
CHECK_EVERY = 3600                     # how often the schedule looks at the tokens
POLL = {"picture": 3, "video": 15}     # seconds before the next look at a container Instagram is making ready,
POLL_MAX = {"picture": 30, "video": 60}  # doubled each time up to this (Meta: not more than once a minute)
WAIT = {"picture": 180, "video": 900}  # and the most it waits
APP_ID_RE = re.compile(r"^\d{5,25}$")
APP_SECRET_RE = re.compile(r"^[A-Za-z0-9]{16,64}$")
NAME_RE = re.compile(r"^s[0-9a-f]{8}-ig-\d{1,2}\.(?:jpg|mp4)$")      # what the signed links may name
HASHTAG = re.compile(r"(?<![\w&])#\w+")
MENTION = re.compile(r"(?<![\w.])@[\w.]+")
SECRET_PARAMS = re.compile(r"((?:access_token|client_secret|code)=)[^&\s\"']+")


class InstagramError(Exception):
    def __init__(self, message: str, status: int = 502, code: int | None = None, expired: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.expired = expired


class _Redact(logging.Filter):
    """httpx writes each request's address to the log at INFO: Instagram's token travels in the address of its GET
    requests (as Meta's documentation sends it) and must not end up in the Hub's log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(SECRET_PARAMS.sub(r"\1***", str(a)) if "=" in str(a) else a for a in record.args)
        if isinstance(record.msg, str) and "=" in record.msg:
            record.msg = SECRET_PARAMS.sub(r"\1***", record.msg)
        return True


if not any(isinstance(f, _Redact) for f in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(_Redact())


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def caption_problem(text: str) -> str:
    """Why Instagram would refuse the post's text as its caption ("" when it will take it)."""
    n = utf16_len(text)
    if n > CAPTION_MAX:
        return f"Instagram takes at most {CAPTION_MAX:,} characters - this text has {n:,}. Shorten it (Text)."
    tags = len(HASHTAG.findall(text))
    if tags > HASHTAGS_MAX:
        return f"Instagram takes at most {HASHTAGS_MAX} hashtags - this text has {tags}. Take some out (Text)."
    names = len(MENTION.findall(text))
    if names > MENTIONS_MAX:
        return f"Instagram takes at most {MENTIONS_MAX} @names - this text has {names}. Take some out (Text)."
    return ""


def prepare_picture(data: bytes) -> bytes:
    """A post's picture as Instagram takes it: a JPEG (sRGB, no transparency - white behind it), between 4:5 and
    1.91:1 (_within), 320 to 1440 wide, at most 8 MB. A GIF gives its first frame."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as src:
        src.seek(0)
        img = ImageOps.exif_transpose(src)
        if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            img = Image.new("RGB", rgba.size, (255, 255, 255))
            img.paste(rgba, mask=rgba.getchannel("A"))
        else:
            img = img.convert("RGB")
    img = _within(img)
    w, h = img.size
    if w > WIDTH_MAX or w < WIDTH_MIN:            # the height rounded away from the nearer limit of the shape
        nw = WIDTH_MAX if w > WIDTH_MAX else WIDTH_MIN
        nh = h * nw / w
        img = _within(img.resize((nw, max(1, math.floor(nh) if w < h else math.ceil(nh))), Image.LANCZOS))
    out = b""
    for quality in (92, 85, 75, 60):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        if len(out) <= JPEG_MAX:
            break
    return out


def _within(img):
    """Cut to between 4:5 and 1.91:1: a taller picture loses mostly its bottom (faces are up there), a wider one
    both sides."""
    w, h = img.size
    if w / h < RATIO_MIN:
        nh = int(w / RATIO_MIN)
        top = (h - nh) // 4
        return img.crop((0, top, w, top + nh))
    if w / h > RATIO_MAX:
        nw = int(h * RATIO_MAX)
        left = (w - nw) // 2
        return img.crop((left, 0, left + nw, h))
    return img


def reel_command(src: str, dst: str) -> str:
    """ffmpeg's command for a video as Reels take it: H.264 4:2:0 at 30 frames a second (the video model makes 16
    or 24; Reels want 23 to 60), at most 1920 wide, at least 3 seconds (a shorter one holds its last frame), the
    moov atom first and no edit lists, AAC sound - the video's own, or silence."""
    s, d = shlex.quote(src), shlex.quote(dst)
    video = ('-map 0:v:0 -vf "$VF" -c:v libx264 -preset veryfast -crf 20 -profile:v high -movflags +faststart '
             "-use_editlist 0")
    return (f"D=$(ffprobe -v error -protocol_whitelist file -show_entries format=duration -of csv=p=0 {s}); "
            "PAD=$(awk -v d=\"$D\" 'BEGIN { if (d != \"\" && d + 0 < 3.2) printf \"%.2f\", 3.2 - d }'); "
            "VF=\"fps=30,scale='trunc(min(1920,iw)/2)*2':-2:flags=lanczos,format=yuv420p\"; "
            "if [ -n \"$PAD\" ]; then VF=\"$VF,tpad=stop_mode=clone:stop_duration=$PAD\"; fi; "
            f"if ffprobe -v error -protocol_whitelist file -select_streams a:0 -show_entries stream=codec_type "
            f"-of csv=p=0 {s} | grep -q audio; then "
            f"ffmpeg -nostdin -v error -y -protocol_whitelist file -i {s} {video} -map 0:a:0 -c:a aac -b:a 128k "
            f"-ar 48000 -ac 2 {d}; else "
            f"ffmpeg -nostdin -v error -y -protocol_whitelist file -i {s} -f lavfi -i anullsrc=r=48000:cl=stereo "
            f"{video} -map 1:a:0 -shortest -c:a aac -b:a 128k {d}; fi")


def _meta_error(body: dict, status: int) -> InstagramError:
    """Meta's error as one line: {"error": {"message", "code", "error_subcode", "error_user_msg"}} from the Graph
    API, {"error_type", "code", "error_message"} from the sign-in's."""
    err = body.get("error") if isinstance(body.get("error"), dict) else body
    msg = (err.get("error_user_msg") or err.get("message") or err.get("error_message")
           or f"Instagram answered {status}.")
    code = err.get("code") if isinstance(err.get("code"), int) else None
    expired = code == 190 or err.get("type") == "OAuthException" and "expired" in str(msg).lower()
    if expired:
        msg = f"The Instagram sign-in has run out or was withdrawn - connect the account again. ({msg})"
    return InstagramError(" ".join(str(msg).split())[:500], 502, code, expired)


class InstagramJobs:
    """The Instagram half of Personas (store.py): the app, each persona's account, posting a post."""

    hub = None                            # (Personas has these)
    ig_states: dict[str, dict]            # sign-ins started: state -> {pid, back, exp}
    ig_jobs: dict[str, asyncio.Task]      # posts being posted: "<id>/<post>" -> the job
    ig_checked: float                     # when the tokens were last looked at
    ig_settling: asyncio.Task | None      # posts a restart cut off, being asked about (ig_after_start)

    # ------------------------------------------------------------ the app (Settings)
    def ig_app(self) -> tuple[str, str]:
        s = self.hub.settings.get("instagram") or {}
        return str(s.get("app_id") or "").strip(), str(s.get("app_secret") or "").strip()

    def ig_redirect(self) -> str:
        base = (self.hub.env.public_url or "").rstrip("/")
        return f"{base}{CALLBACK}" if base.startswith("https://") else ""

    def ig_info(self) -> dict:
        app_id, secret = self.ig_app()
        redirect = self.ig_redirect()
        return {"app_id": app_id, "secret_set": bool(secret), "redirect_uri": redirect,
                "public_url": self.hub.env.public_url or "", "ready": bool(app_id and secret and redirect)}

    def set_ig_app(self, app_id: str | None, app_secret: str | None) -> dict:
        from .store import PersonaError
        values = {}
        if app_id is not None:
            app_id = app_id.strip()
            if app_id and not APP_ID_RE.match(app_id):
                raise PersonaError("The Instagram app ID is a number (Meta's dashboard: Instagram -> API setup with "
                                   "Instagram login).")
            values["app_id"] = app_id
        if app_secret is not None and app_secret.strip():           # empty: the one kept stays
            if not APP_SECRET_RE.match(app_secret.strip()):
                raise PersonaError("That does not look like the Instagram app secret (letters and digits, from "
                                   "Meta's dashboard, next to the app ID).")
            values["app_secret"] = app_secret.strip()
        if values:
            self.hub.settings.update("instagram", values)
        return self.ig_info()

    # ------------------------------------------------------------ a persona's account
    def _ig_path(self, pid: str) -> Path:
        return self._dir(pid) / "instagram.json"

    def ig_of(self, pid: str) -> dict | None:
        """The account the persona posts to, with its token - for the Hub's own use, never for the page."""
        try:
            data = json.loads(self._ig_path(pid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("token") and data.get("user_id") else None

    def _ig_write(self, pid: str, data: dict) -> None:
        d = self._dir(pid)
        if not (d / "persona.json").is_file():
            return
        tmp = d / f".instagram.{secrets.token_hex(3)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        tmp.replace(d / "instagram.json")

    def ig_public(self, pid: str) -> dict | None:
        """What the page shows of the account: its name, until when the sign-in holds, what is wrong with it."""
        c = self.ig_of(pid)
        if c is None:
            return None
        problem = c.get("problem") or ""
        if not problem and c.get("expires") and c["expires"] < time.time():
            problem = "The Instagram sign-in has run out - connect the account again."
        return {"username": c.get("username") or "", "account_type": c.get("account_type") or "",
                "expires": c.get("expires"), "connected": c.get("connected"), "problem": problem}

    def ig_username(self, pid: str) -> str:
        """The account a post of the persona's can go to now ("" when none, or its sign-in needs doing again)."""
        c = self.ig_public(pid)
        if c is None or c["problem"]:
            return ""
        return c["username"] or "instagram"

    def ig_connect_url(self, pid: str, back: str) -> str:
        """Instagram's sign-in page for the persona's account; `back`: the address the Hub was opened at, where the
        browser goes once it is done."""
        from .store import PersonaError
        self.load(pid)
        app_id, secret = self.ig_app()
        if not (app_id and secret):
            raise PersonaError("Give the Hub your Instagram app's ID and secret first.", 409)
        redirect = self.ig_redirect()
        if not redirect:
            raise PersonaError("Instagram sends you back to the Hub's address on the internet: set HUB_PUBLIC_URL "
                               "(https://...) in /srv/slq-hub/.env.", 409)
        o = urlparse(back or "")
        back = f"{o.scheme}://{o.netloc}" if o.scheme in ("http", "https") and o.netloc else ""
        now = time.time()
        self.ig_states = {k: v for k, v in self.ig_states.items() if v["exp"] > now}
        state = secrets.token_urlsafe(24)
        self.ig_states[state] = {"pid": pid, "back": back, "exp": now + STATE_TTL}
        return f"{AUTHORIZE}?" + urlencode({"client_id": app_id, "redirect_uri": redirect, "response_type": "code",
                                            "scope": SCOPES, "state": state, "force_reauth": "true"})

    async def ig_callback(self, code: str, state: str, error: str = "", why: str = "") -> tuple[str, str, str]:
        """Instagram sent the browser back: the code swapped for a token good for 60 days, the account kept with
        the persona -> (persona, where the browser goes, what went wrong or "")."""
        from .store import PersonaError
        st = self.ig_states.pop(state or "", None)
        if st is None or st["exp"] < time.time():
            raise PersonaError("This sign-in was used already or is too old - press Connect Instagram again.")
        pid, back = st["pid"], st["back"]
        if error or not code:
            return pid, back, f"Instagram: {why or error or 'no code came back'}"
        app_id, secret = self.ig_app()
        try:
            short = await self._ig_call("POST", CODE_URL, data={
                "client_id": app_id, "client_secret": secret, "grant_type": "authorization_code",
                "redirect_uri": self.ig_redirect(), "code": code.split("#")[0]})
            item = (short.get("data") or [short])[0] if isinstance(short.get("data"), list) else short
            allowed = str(item.get("permissions") or "")
            if allowed and "instagram_business_content_publish" not in allowed:
                return pid, back, ("Instagram did not allow the Hub to post (instagram_business_content_publish): "
                                   "connect again and allow it.")
            long = await self._ig_call("GET", f"{GRAPH}/access_token", params={
                "grant_type": "ig_exchange_token", "client_secret": secret, "access_token": item.get("access_token")})
            token = long.get("access_token")
            me = await self._ig_call("GET", f"{GRAPH}/{VERSION}/me", params={
                "fields": "user_id,username,account_type", "access_token": token})
            if isinstance(me.get("data"), list) and me["data"]:          # flat, or in a list as some pages show it
                me = me["data"][0]
        except InstagramError as exc:
            return pid, back, str(exc)
        if not token or not (me.get("user_id") or me.get("id")):
            return pid, back, "Instagram's answer had no token or account in it."
        now = int(time.time())
        try:
            async with self._lock(pid):
                self.load(pid)                          # deleted meanwhile: nothing kept
                self._ig_write(pid, {"user_id": str(me.get("user_id") or me.get("id")),
                                     "username": me.get("username") or "", "account_type": me.get("account_type") or "",
                                     "token": token, "expires": now + int(long.get("expires_in") or 60 * 86400),
                                     "refreshed": now, "connected": now, "problem": ""})
        except PersonaError:
            return pid, back, "The persona was deleted meanwhile."
        return pid, back, ""

    async def ig_disconnect(self, pid: str) -> None:
        """The Hub forgets the account (Instagram keeps the app among the account's apps until it is removed
        there: Settings -> Apps and websites)."""
        from .store import PersonaError
        async with self._lock(pid):
            self.load(pid)
            if any(k.startswith(f"{pid}/") for k in self.ig_jobs):
                raise PersonaError("A post is being put on Instagram - wait for it first.", 409)
            self._ig_path(pid).unlink(missing_ok=True)

    async def _ig_update(self, pid: str, held: str, **fields) -> bool:
        """The account's file changed - if it still holds the token `held`: a renewal or a failure that took a while
        does not bring back an account the owner disconnected meanwhile, or spoil one connected anew."""
        from .store import PersonaError
        try:
            async with self._lock(pid):
                cur = self.ig_of(pid)
                if cur is None or cur.get("token") != held:
                    return False
                self._ig_write(pid, {**cur, **fields})
                return True
        except PersonaError:
            return False

    async def ig_tick(self, now: float | None = None, force: bool = False) -> list[str]:
        """From the schedule: tokens renewed when they are a few days old (each is good for 60 days from its last
        renewal), a run-out one marked -> the personas whose token was renewed."""
        now = time.time() if now is None else now
        if not force and now - self.ig_checked < CHECK_EVERY:
            return []
        self.ig_checked = now
        done = []
        for item in self.list():
            pid = item["id"]
            c = self.ig_of(pid)
            if c is None or c.get("problem"):
                continue
            if c.get("expires") and c["expires"] < now:
                await self._ig_update(pid, c["token"], problem="The Instagram sign-in has run out - connect the "
                                                               "account again.")
                continue
            if now - (c.get("refreshed") or 0) < max(REFRESH_AFTER, 86400):
                continue
            try:
                r = await self._ig_call("GET", f"{GRAPH}/refresh_access_token", params={
                    "grant_type": "ig_refresh_token", "access_token": c["token"]})
            except InstagramError as exc:
                if exc.expired:
                    await self._ig_update(pid, c["token"], problem=str(exc))
                else:
                    log.warning("instagram: the token of %s was not renewed: %s", pid, exc)
                continue
            if r.get("access_token") and await self._ig_update(
                    pid, c["token"], token=r["access_token"], refreshed=int(now),
                    expires=int(now) + int(r.get("expires_in") or 60 * 86400)):
                done.append(pid)
        return done

    # ------------------------------------------------------------ Instagram's API
    async def _ig_call(self, method: str, url: str, params: dict | None = None, data: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15),
                                         transport=self.hub.transports.web) as c:
                r = await c.request(method, url, params=params, data=data)
        except httpx.HTTPError as exc:
            raise InstagramError(f"Could not reach Instagram ({exc.__class__.__name__}).") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if r.status_code >= 400 or "error" in body or "error_type" in body:
            raise _meta_error(body, r.status_code)
        return body

    async def _ig_wait(self, container: str, token: str, kind: str) -> None:
        """Until Instagram has made the container ready (it fetches the file, a video takes a while) - asked again
        after a pause that grows."""
        deadline = time.monotonic() + WAIT[kind]
        pause = POLL[kind]
        while True:
            r = await self._ig_call("GET", f"{GRAPH}/{VERSION}/{container}",
                                    params={"fields": "status_code,status", "access_token": token})
            code = r.get("status_code")
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Instagram could not use the {kind}: {r.get('status') or code}.")
            if time.monotonic() >= deadline:
                raise InstagramError(f"Instagram was still making the {kind} ready after {WAIT[kind] // 60} minutes - "
                                     "try again later.")
            await asyncio.sleep(pause)
            pause = min(pause * 2, POLL_MAX[kind])

    async def _ig_status(self, container: str, token: str) -> str:
        """A container's status_code: FINISHED (ready), PUBLISHED, IN_PROGRESS, ERROR, EXPIRED - "" when Instagram
        could not be asked."""
        try:
            r = await self._ig_call("GET", f"{GRAPH}/{VERSION}/{container}",
                                    params={"fields": "status_code", "access_token": token})
        except InstagramError:
            return ""
        return str(r.get("status_code") or "")

    def _fetch_hint(self, msg: str) -> str:
        """Instagram fetches the files itself: when that failed, the address it fetched them from is the suspect."""
        if re.search(r"download|fetch|\buri\b|\burl\b|image_url|video_url|2207052|2207003", msg, re.I):
            return (f"{msg} Instagram fetches the file from {self.hub.env.public_url} - that address must be reachable "
                    "from the internet.")
        return msg

    # ------------------------------------------------------------ posting
    def ig_posting(self, pid: str, sid: str) -> bool:
        job = self.ig_jobs.get(f"{pid}/{sid}")
        return job is not None and not job.done()

    def ig_link(self, pid: str, rel: str, now: float | None = None) -> str:
        """A link Instagram can fetch a file made ready for it with, for LINK_TTL - signed, no sign-in."""
        name = rel.split("/", 1)[1]
        exp = int((time.time() if now is None else now) + LINK_TTL)
        sig = hmac.new(self.hub.env.secret_key.encode(), f"persona-post:{pid}:{name}:{exp}".encode(),
                       hashlib.sha256).hexdigest()[:32]
        return f"{self.hub.env.public_url.rstrip('/')}/hub/media/persona/{pid}/{name}?exp={exp}&sig={sig}"

    def ig_link_ok(self, pid: str, name: str, exp: int, sig: str) -> bool:
        if not NAME_RE.match(name or "") or exp < time.time():
            return False
        want = hmac.new(self.hub.env.secret_key.encode(), f"persona-post:{pid}:{name}:{exp}".encode(),
                        hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(want.encode(), (sig or "").encode())     # bytes: any text a caller sends

    async def ig_publish(self, pid: str, sid: str) -> dict:
        """The owner approved the post for Instagram: approved, and on its way (a job)."""
        from .store import PersonaError
        self.load(pid)
        c = self.ig_public(pid)
        if c is None:
            raise PersonaError("Connect the persona's Instagram account first (Posts).", 409)
        if c["problem"]:
            raise PersonaError(c["problem"], 409)
        if not self.ig_redirect():
            raise PersonaError("Instagram fetches the pictures from the Hub's address on the internet: set "
                               "HUB_PUBLIC_URL (https://...) in /srv/slq-hub/.env.", 409)
        async with self._lock(pid):
            posts = self.posts_of(pid)
            post = next((s for s in posts if s["id"] == sid), None)
            if post is None:
                raise PersonaError("No such post.", 404)
            ig = post.get("instagram") or {}
            if self.ig_posting(pid, sid) or ig.get("state") == "posting":
                raise PersonaError("It is being posted.", 409)
            if ig.get("state") == "posted":
                raise PersonaError("It is on Instagram already.", 409)
            if not post.get("media"):
                raise PersonaError("Instagram needs a picture or a video - this post is text only.")
            why = caption_problem(post["text"])
            if why:
                raise PersonaError(why)
            post["status"] = "approved"
            post["instagram"] = {**ig, "state": "posting", "started": int(time.time()), "error": "",
                                 "publishing": False}             # (a container made before stays: it is reused)
            self._write_posts(pid, posts)
        key = f"{pid}/{sid}"
        self.ig_jobs[key] = asyncio.get_running_loop().create_task(self._ig_job(pid, sid), name=f"instagram-{key}")
        return {**post, "persona": self.post_face(pid)}

    async def _ig_set(self, pid: str, sid: str, **fields) -> None:
        from .store import PersonaError
        try:
            async with self._lock(pid):
                posts = self.posts_of(pid)
                post = next((s for s in posts if s["id"] == sid), None)
                if post is None:
                    return
                post["instagram"] = {**(post.get("instagram") or {}), **fields}
                self._write_posts(pid, posts)
        except PersonaError:
            pass

    def _ig_post(self, pid: str, sid: str) -> dict:
        try:
            post = next((s for s in self.posts_of(pid) if s["id"] == sid), None)
        except Exception:  # noqa: BLE001 - the persona went away
            post = None
        return (post or {}).get("instagram") or {}

    async def _ig_job(self, pid: str, sid: str) -> None:
        """The post made ready and put on Instagram. Published once: the container Instagram publishes is kept with
        the post before it is published, and asked about when anything goes wrong after that - PUBLISHED is posted,
        FINISHED is published as it is on Post again (no second post, no new upload)."""
        made: list[str] = []
        token = ""
        try:
            c = self.ig_of(pid)
            post = next((s for s in self.posts_of(pid) if s["id"] == sid), None)
            if c is None or post is None:
                raise InstagramError("The account or the post went away meanwhile.")
            ig, token, caption = c["user_id"], c["token"], post["text"]
            container = (post.get("instagram") or {}).get("container") or ""
            if container:                              # made the time before: how did that end?
                status = await self._ig_status(container, token)
                if status == "PUBLISHED":
                    await self._ig_posted(pid, sid, ig, token, caption, "")
                    return
                if status != "FINISHED":
                    container = ""
            if not container:
                items = []
                for i, m in enumerate(post["media"], 1):
                    src = self.file(pid, m["file"])
                    if m["kind"] == "picture":
                        data = await asyncio.to_thread(lambda p=src: prepare_picture(p.read_bytes()))
                        rel = f"posts/{sid}-ig-{i}.jpg"
                    else:
                        data = await self._ig_video(src)
                        rel = f"posts/{sid}-ig-{i}.mp4"
                    await asyncio.to_thread(self._put, pid, rel, data)
                    made.append(rel)
                    items.append((m["kind"], self.ig_link(pid, rel)))
                container = await self._ig_make(ig, token, caption, items)
                await self._ig_set(pid, sid, container=container)
            await self._ig_set(pid, sid, publishing=True)
            try:
                media_id = str((await self._ig_call("POST", f"{GRAPH}/{VERSION}/{ig}/media_publish",
                                                    data={"creation_id": container, "access_token": token}))["id"])
            except InstagramError:
                if await self._ig_status(container, token) != "PUBLISHED":
                    raise
                media_id = ""                          # it went through all the same
            await self._ig_posted(pid, sid, ig, token, caption, media_id)
        except InstagramError as exc:
            if exc.expired and token:
                await self._ig_update(pid, token, problem=str(exc))
            await self._ig_set(pid, sid, state="failed", publishing=False, error=self._fetch_hint(str(exc)))
        except asyncio.CancelledError:
            if not self._ig_post(pid, sid).get("publishing"):
                await self._ig_set(pid, sid, state="failed", error="Stopped before it was posted.")
            raise                                      # (publishing: the next start asks Instagram - ig_after_start)
        except Exception as exc:  # noqa: BLE001 - the post says why, whatever it was
            log.exception("instagram: posting %s/%s", pid, sid)
            if self._ig_post(pid, sid).get("publishing"):
                await self._ig_set(pid, sid, state="failed", publishing=False,
                                   error=f"It stopped while it was being published ({exc}) - look on Instagram "
                                         "before posting it again.")
            else:
                await self._ig_set(pid, sid, state="failed", error=f"It stopped: {exc}")
        finally:
            for rel in made:
                try:
                    self.path(pid, rel).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
            self.ig_jobs.pop(f"{pid}/{sid}", None)

    async def _ig_make(self, ig: str, token: str, caption: str, items: list[tuple[str, str]]) -> str:
        """The container Instagram publishes, ready: one picture or reel, or a carousel of them."""
        media = f"{GRAPH}/{VERSION}/{ig}/media"
        if len(items) == 1:
            kind, url = items[0]
            fields = ({"image_url": url} if kind == "picture"
                      else {"media_type": "REELS", "video_url": url, "share_to_feed": "true"})
            container = (await self._ig_call("POST", media, data={**fields, "caption": caption,
                                                                   "access_token": token}))["id"]
            await self._ig_wait(container, token, kind)
            return str(container)
        children = []
        for kind, url in items:
            fields = {"image_url": url} if kind == "picture" else {"media_type": "VIDEO", "video_url": url}
            r = await self._ig_call("POST", media, data={**fields, "is_carousel_item": "true", "access_token": token})
            children.append((str(r["id"]), kind))
        for child, kind in children:
            await self._ig_wait(child, token, kind)
        container = (await self._ig_call("POST", media, data={
            "media_type": "CAROUSEL", "children": ",".join(x for x, _ in children), "caption": caption,
            "access_token": token}))["id"]
        await self._ig_wait(str(container), token, "picture")
        return str(container)

    async def _ig_posted(self, pid: str, sid: str, ig: str, token: str, caption: str, media_id: str) -> None:
        """On Instagram: its id kept at once, then its link. Without the id (published, but the answer was lost),
        both are looked for among the account's newest posts by the caption."""
        link = ""
        if media_id:
            await self._ig_set(pid, sid, id=media_id)
            try:
                link = (await self._ig_call("GET", f"{GRAPH}/{VERSION}/{media_id}",
                                            params={"fields": "permalink", "access_token": token})).get("permalink")
            except InstagramError as exc:
                log.warning("instagram: no link for %s/%s: %s", pid, sid, exc)
        else:
            try:
                r = await self._ig_call("GET", f"{GRAPH}/{VERSION}/{ig}/media", params={
                    "fields": "id,caption,permalink", "limit": "10", "access_token": token})
                hit = next((m for m in r.get("data") or [] if (m.get("caption") or "") == caption), {})
                media_id, link = str(hit.get("id") or ""), str(hit.get("permalink") or "")
            except InstagramError as exc:
                log.warning("instagram: %s/%s not found among the newest: %s", pid, sid, exc)
        await self._ig_set(pid, sid, state="posted", id=media_id, permalink=str(link or ""), at=int(time.time()),
                           error="", publishing=False)

    async def _ig_video(self, src: Path) -> bytes:
        """The video re-encoded for Reels by ffmpeg in the sandbox, in a folder of its own (the persona's folder is
        out of the sandbox's reach)."""
        from .. import sandbox_job
        from ..projects import store as projects
        folder = sandbox_job.tmp_folder(self.hub, TMP)
        try:
            name = "in" + src.suffix.lower()
            data = await asyncio.to_thread(src.read_bytes)
            await asyncio.to_thread(projects.safe_write, folder, folder / name, data)
            return await sandbox_job.run(self.hub, folder, reel_command(name, "reel.mp4"), 900, "reel.mp4",
                                         "Making the video ready for Instagram failed")
        except sandbox_job.SandboxJobError as exc:
            raise InstagramError(str(exc)) from None
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    async def ig_after_start(self) -> None:
        """A startup hook: files made ready for Instagram go, and a post a restart cut off while it was being posted
        is settled - posted when Instagram's id came back before; else Instagram is asked about its container (in the
        background: the start does not wait for Instagram)."""
        from .. import sandbox_job
        from .store import PersonaError
        sandbox_job.sweep(self.hub, TMP)
        todo = []
        for item in self.list():
            pid = item["id"]
            d = self._dir(pid) / "posts"
            for f in d.glob("*-ig-*") if d.is_dir() else []:
                if f.is_file() and not f.is_symlink():
                    f.unlink(missing_ok=True)
            if not any((s.get("instagram") or {}).get("state") == "posting" for s in self.posts_of(pid)):
                continue
            try:
                async with self._lock(pid):
                    posts = self.posts_of(pid)
                    for s in posts:
                        ig = s.get("instagram") or {}
                        if ig.get("state") != "posting":
                            continue
                        if ig.get("id"):
                            ig.update(state="posted", publishing=False, at=ig.get("at") or int(time.time()))
                        elif ig.get("publishing") and ig.get("container"):
                            todo.append((pid, s["id"], ig["container"], s["text"]))
                        else:
                            ig.update(state="failed", publishing=False, error="Stopped before it was posted.")
                    self._write_posts(pid, posts)
            except PersonaError:
                continue
        if todo:
            self.ig_settling = asyncio.get_running_loop().create_task(self._ig_settle(todo), name="instagram-settle")

    async def _ig_settle(self, todo: list[tuple[str, str, str, str]]) -> None:
        for pid, sid, container, caption in todo:
            try:
                c = self.ig_of(pid)
                status = await self._ig_status(container, c["token"]) if c else ""
                if status == "PUBLISHED":
                    await self._ig_posted(pid, sid, c["user_id"], c["token"], caption, "")
                elif status == "FINISHED":
                    await self._ig_set(pid, sid, state="failed", publishing=False,
                                       error="Stopped before it was posted - Post again posts it.")
                else:
                    await self._ig_set(pid, sid, state="failed", publishing=False,
                                       error="Stopped: the Hub restarted while it was being posted - look on "
                                             "Instagram before posting it again.")
            except Exception:  # noqa: BLE001 - the next one all the same
                log.exception("instagram: settling %s/%s", pid, sid)
