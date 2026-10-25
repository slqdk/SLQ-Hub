#!/usr/bin/env python3
"""Open a web page or app from the working folder in a headless browser and look at it (Playwright with Chromium's
headless shell, both from the image). The Hub's web_app tool runs it in the sandbox like a bash command.

    pageshot --json REQUEST     open a page; REQUEST is JSON (below), the result is JSON on stdout
    pageshot --check            exit code 0 when the browser starts

REQUEST: {"root": dir, "path": "index.html", "steps": [step], "screen": [w, h], "timeout": s, "out": dir,
          "online": bool, "sandbox": "allow-scripts ..."}

sandbox: the page runs as in an <iframe sandbox="..."> with those flags (a Content-Security-Policy sandbox on
its documents): how the Hub's side panel runs an artifact - no storage, no cookies, its own origin.

The page is served from root at http://page.local/ by the browser itself: every request to page.local is answered
from the folder - no server, no port, nothing that needs the network - so a page that fetch()es its own files
(level data, JSON) works as on a web server, also offline, which a file:// page does not. Nothing outside root is
served. Other addresses (a CDN, an API) load only when online, and are listed as blocked when not. (That is the
browser's own blocking, which does not cover everything a page can open - WebRTC, a prefetch: offline, the Hub
runs pageshot itself without the internet.)

The steps, run one by one:

    {"click": [x, y]} or {"click": "Start"} (a CSS selector, else text on the page); "double": true,
                                          "button": "right"
    {"type": "text"}                      {"key": "ArrowLeft"} or {"key": "Space Space Enter"} or {"key": "ctrl+a"}
    {"hold": "ArrowRight", "seconds": 1.5}   a key held down (a game's movement)
    {"move": [x, y]}                      {"scroll": [x, y, dy]}   (dy > 0: down, in pixels)
    {"wait": seconds}                     {"shot": "label"}
    {"eval": "JavaScript expression"}     its value as JSON - the state of a game, a computed style

Coordinates are CSS pixels of the viewport, the same as in the screenshots. A screenshot is taken when the page
has loaded, at every shot step, and after the last step. What the page wrote to its console, uncaught errors
with where they happened, requests that failed (a missing file is a 404), alert() and confirm() texts (dismissed)
and the page's visible text come back too - also from a page that stops answering (a script that never ends):
what came before is printed when the time is up.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import time
from urllib.parse import quote, unquote, urlparse

HOST = "page.local"
SCREEN = (1280, 800)
MAX_STEPS, MAX_SHOTS, MAX_TEXT = 40, 8, 2000
MAX_CONSOLE, MAX_LINE, MAX_HOLD, MAX_WAIT = 60, 600, 10.0, 30.0
# seconds past the run's time before what there is is printed anyway: a step begun just before it and the last
# screenshot (15 s each at most) - a wait or a hold is cut to the time that is left
GRACE = float(os.environ.get("PAGESHOT_GRACE") or 40)
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json", ".wasm": "application/wasm",
         ".css": "text/css", ".html": "text/html", ".htm": "text/html", ".svg": "image/svg+xml",
         ".map": "application/json", ".txt": "text/plain", ".csv": "text/csv", ".xml": "application/xml"}
MODS = {"ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift", "cmd": "Meta", "meta": "Meta",
        "super": "Meta", "win": "Meta"}
KEYS = {"return": "Enter", "enter": "Enter", "esc": "Escape", "escape": "Escape", "space": "Space", "tab": "Tab",
        "backspace": "Backspace", "delete": "Delete", "del": "Delete", "up": "ArrowUp", "down": "ArrowDown",
        "left": "ArrowLeft", "right": "ArrowRight", "home": "Home", "end": "End", "pageup": "PageUp",
        "pagedown": "PageDown", "insert": "Insert"}
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/ms-playwright")
# Where an error without a stack happened - a script that does not parse: Chromium's pageerror says only "Unexpected
# identifier 'Doram'" (chat 79: an apostrophe in a name broke data.js and with it the whole game). The page's own
# error event knows the file and line; this hands them over through the console, under a mark.
WHERE = "\u0000pageshot-where:"
WHERE_SCRIPT = """window.addEventListener("error", function (e) {
  if (!(e instanceof ErrorEvent) || !e.filename) return;
  console.error(%s + JSON.stringify({message: String(e.message || ""), file: e.filename, line: e.lineno, col: e.colno}));
}, true);""" % json.dumps(WHERE)


class Fail(Exception):
    pass


def clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} more characters]"


def key_name(key: str) -> str:
    """'ctrl+s' -> 'Control+s', 'Return' -> 'Enter', 'ArrowLeft' as it is (Playwright's names)."""
    parts = [p for p in re.split(r"\+(?=.)", key.strip()) if p]
    out = [MODS.get(p.lower(), p) for p in parts[:-1]]
    last = parts[-1] if parts else key
    out.append(KEYS.get(last.lower(), last) if len(last) > 1 else last)
    return "+".join(out)


def served(root: str, url: str) -> tuple[int, bytes, str]:
    """(status, body, content type) of a page.local address, from root - never outside it: the file itself, after
    a folder's index.html is added and every link on the way is followed, must be inside."""
    try:
        rel = unquote(urlparse(url).path or "/").lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        base = os.path.realpath(root)
        full = os.path.realpath(os.path.join(base, rel))
        if os.path.isdir(full):
            full = os.path.realpath(os.path.join(full, "index.html"))
        if full != base and not full.startswith(base + os.sep):
            return 403, b"Outside the working folder", "text/plain"
        if not os.path.isfile(full):
            return 404, b"Not found", "text/plain"
        ext = os.path.splitext(full)[1].lower()
        kind = TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in ("application/json", "application/xml", "image/svg+xml"):
            kind += "; charset=utf-8"
        with open(full, "rb") as f:
            return 200, f.read(), kind
    except ValueError:                                 # a NUL in the address
        return 400, b"Bad address", "text/plain"
    except OSError:                                    # not readable
        return 403, b"Not readable", "text/plain"


def address(rel: str) -> str:
    """The page.local address of a file in root: its name escaped, so "spil #1.html" and "50%41.html" are those."""
    return f"http://{HOST}/" + quote(rel.lstrip("/"), safe="/")


class Session:
    def __init__(self, page, out: str, shots: list, deadline: float = float("inf")):
        self.page, self.out, self.shots, self.deadline = page, out, shots, deadline

    def left(self, secs: float) -> float:
        """secs, or what is left of the run's time if that is less."""
        return max(0.0, min(secs, self.deadline - time.monotonic()))

    def shot(self, label: str) -> str:
        if len(self.shots) >= MAX_SHOTS:
            return f"no screenshot ({MAX_SHOTS} is the most a run takes)"
        n = len(self.shots) + 1
        path = os.path.join(self.out, f"{n}.png")
        self.page.screenshot(path=path, timeout=15000)
        vp = self.page.viewport_size or {"width": 0, "height": 0}
        self.shots.append({"file": f"{n}.png", "label": label, "width": vp["width"], "height": vp["height"]})
        return f"screenshot {n}" + (f" ({label})" if label else "")

    def point(self, value) -> tuple[float, float]:
        if not (isinstance(value, list) and len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2])):
            raise Fail(f"expected [x, y], got {json.dumps(value)[:80]}")
        return float(value[0]), float(value[1])

    def click(self, s: dict) -> str:
        target, page = s["click"], self.page
        button = s.get("button") if s.get("button") in ("left", "right", "middle") else "left"
        count = 2 if s.get("double") else 1
        if isinstance(target, list):
            x, y = self.point(target)
            page.mouse.click(x, y, button=button, click_count=count)
            return f"clicked at ({x:g}, {y:g})"
        text = str(target)
        loc = None
        if not text.startswith(("text=", "xpath=", "//")):     # a CSS selector that finds something, else text
            try:
                if page.locator(text).count():
                    loc = page.locator(text).first
            except Exception:  # noqa: BLE001 - not CSS ("Spil igen!"): words on the page
                loc = None
        if loc is None:
            loc = page.get_by_text(text, exact=True).first
            if not loc.count():
                loc = page.get_by_text(text).first
            if not loc.count():
                raise Fail(f"{text!r} not found on the page (neither a CSS selector nor text on it)")
        box = loc.bounding_box()
        loc.click(button=button, click_count=count, timeout=5000)
        where = f" at ({box['x'] + box['width'] / 2:.0f}, {box['y'] + box['height'] / 2:.0f})" if box else ""
        return f"clicked {text!r}{where}"

    def step(self, s: dict) -> str:
        page = self.page
        if "click" in s:
            return self.click(s)
        if "type" in s:
            page.keyboard.type(str(s["type"]), delay=15)
            return f"typed {clip(s['type'], 60)!r}"
        if "key" in s:
            keys = [key_name(k) for k in str(s["key"]).split()]
            for k in keys:
                page.keyboard.press(k)
            return "pressed " + " ".join(keys)
        if "hold" in s:
            # held as a keyboard holds a key: one press, then after a quarter second repeats 30 times a second (a
            # game that moves on each keydown moves on, one that watches keydown and keyup sees it held)
            k = key_name(str(s["hold"]))
            secs = max(0.05, self.left(min(MAX_HOLD, float(s.get("seconds") or 1))))
            end = time.monotonic() + secs
            page.keyboard.down(k)
            page.wait_for_timeout(min(250, secs * 1000))
            while time.monotonic() < end:
                page.keyboard.down(k)                   # pressed again while down: a repeat
                page.wait_for_timeout(min(33, max(1, (end - time.monotonic()) * 1000)))
            page.keyboard.up(k)
            return f"held {k} for {secs:g} s"
        if "move" in s:
            x, y = self.point(s["move"])
            page.mouse.move(x, y)
            return f"moved the mouse to ({x:g}, {y:g})"
        if "scroll" in s:
            v = s["scroll"]
            x, y = self.point(v)
            dy = float(v[2]) if len(v) > 2 and isinstance(v[2], (int, float)) else 300.0
            page.mouse.move(x, y)
            page.mouse.wheel(0, dy)
            return f"scrolled {dy:g} px at ({x:g}, {y:g})"
        if "wait" in s:
            secs = self.left(max(0.0, min(MAX_WAIT, float(s["wait"] or 0))))
            page.wait_for_timeout(secs * 1000)
            return f"waited {secs:g} s"
        if "shot" in s:
            return self.shot(str(s.get("shot") or ""))
        if "eval" in s:
            value = page.evaluate(str(s["eval"]))
            return "value: " + clip(json.dumps(value, ensure_ascii=False, default=str), MAX_TEXT)
        raise Fail("a step needs one of: click, type, key, hold, move, scroll, wait, shot, eval")


def new_result() -> dict:
    return {"ok": False, "shots": [], "steps": [], "console": [], "errors": [], "failed": [], "blocked": [],
            "dialogs": [], "console_counts": {}}


def say(result: dict, once: threading.Lock) -> bool:
    """Print the result, once (the run's end and the time-up guard may both get here). False when already done."""
    if not once.acquire(blocking=False):
        return False
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    return True


def give_up(result: dict, t0: float, once: threading.Lock) -> None:
    """The time is up and the page does not answer (a script that never ends, an eval that never settles): what
    came before, and out - the browser goes with the process."""
    for _ in range(5):                                 # the run may still be adding to it
        try:
            snapshot = json.loads(json.dumps(result, ensure_ascii=False, default=str))
            break
        except RuntimeError:
            time.sleep(0.05)
    else:
        snapshot = new_result()
    before = f" (before that: {snapshot['error']})" if snapshot.get("error") else ""
    doing = snapshot.pop("doing", None)
    snapshot.update(ok=False, seconds=round(time.monotonic() - t0, 1),
                    error=f"the page stopped answering{' at ' + doing if doing else ''} - a script that never ends, "
                          f"or an eval that never gives a value; what is here came before{before}")
    if say(snapshot, once):
        os._exit(0)


def run(req: dict, result: dict, once: threading.Lock | None = None) -> dict:
    t0 = time.monotonic()
    root = os.path.realpath(str(req.get("root") or os.getcwd()))
    if not os.path.isdir(root):
        raise Fail(f"no such folder: {root}")
    rel = str(req.get("path") or "index.html").lstrip("/")
    status, _, _ = served(root, address(rel))
    if status != 200:
        raise Fail(f"{rel} is not a file in the working folder" if status == 404 else f"{rel} is outside it")
    steps = req.get("steps") or []
    if not isinstance(steps, list) or len(steps) > MAX_STEPS or not all(isinstance(s, dict) for s in steps):
        raise Fail(f"steps must be a list of at most {MAX_STEPS} objects")
    try:
        width, height = (int(v) for v in (req.get("screen") or SCREEN))
    except (TypeError, ValueError) as exc:
        raise Fail("screen is [width, height]") from exc
    screen = {"width": max(320, min(2560, width)), "height": max(240, min(1600, height))}
    timeout = max(5.0, min(300.0, float(req.get("timeout") or 30)))
    online = bool(req.get("online"))
    sandbox = str(req.get("sandbox") or "").strip()
    if sandbox and not re.fullmatch(r"[a-z]+(-[a-z]+)*( [a-z]+(-[a-z]+)*)*", sandbox):
        raise Fail("sandbox is iframe sandbox flags, e.g. \"allow-scripts allow-forms\"")
    out = os.path.abspath(req.get("out") or os.path.join(os.getcwd(), ".hub", "web"))
    os.makedirs(out, exist_ok=True)
    for old in os.listdir(out):
        if old.endswith(".png"):
            os.unlink(os.path.join(out, old))
    from playwright.sync_api import Error as PwError          # after the request is checked
    from playwright.sync_api import sync_playwright
    deadline = time.monotonic() + timeout
    guard = threading.Timer(timeout + GRACE, give_up, (result, t0, once or threading.Lock()))
    guard.daemon = True
    guard.start()
    counts = result["console_counts"]
    wheres: list[dict] = []

    def note(kind: str, item) -> None:
        bucket = result[kind]
        if len(bucket) < MAX_CONSOLE:
            bucket.append(item)

    def route(r) -> None:
        try:
            url = r.request.url
            if urlparse(url).hostname == HOST:
                code, body, kind = served(root, url)
                headers = {"content-type": kind, "cache-control": "no-store"}
                if sandbox and r.request.resource_type == "document":
                    headers["content-security-policy"] = "sandbox " + sandbox
                r.fulfill(status=code, body=body, headers=headers)
            elif online:
                r.continue_()
            else:
                note("blocked", clip(url, 200))
                r.abort("internetdisconnected")
        except Exception:  # noqa: BLE001 - a request the page gave up on; nothing in it may end the run
            pass

    def console(msg) -> None:
        if msg.text.startswith(WHERE):
            try:
                wheres.append(json.loads(msg.text[len(WHERE):]))
            except ValueError:
                pass
            return
        counts[msg.type] = counts.get(msg.type, 0) + 1
        loc = msg.location or {}
        where = f"{loc.get('url', '').replace(f'http://{HOST}/', '')}:{loc.get('lineNumber', 0) + 1}" \
            if loc.get("url") else ""
        note("console", {"type": msg.type, "text": clip(msg.text, MAX_LINE), "where": where})

    def page_error(exc) -> None:
        stack = (getattr(exc, "stack", "") or "").replace(f"http://{HOST}/", "")
        note("errors", {"message": clip(getattr(exc, "message", str(exc)), MAX_LINE),
                        "stack": clip("\n".join(stack.splitlines()[:6]), 1200)})

    def failed(request) -> None:
        if urlparse(request.url).hostname != HOST and not online:
            return                                     # listed as blocked
        note("failed", {"url": clip(request.url.replace(f"http://{HOST}/", ""), 200),
                        "error": clip(request.failure or "failed", 200)})

    def response(resp) -> None:
        if resp.status >= 400:
            note("failed", {"url": clip(resp.url.replace(f"http://{HOST}/", ""), 200), "status": resp.status})

    def dialog(d) -> None:
        note("dialogs", {"type": d.type, "text": clip(d.message, MAX_LINE)})
        try:
            d.dismiss()
        except PwError:
            pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-gpu", "--no-first-run", "--mute-audio"])
            try:
                context = browser.new_context(viewport=screen, device_scale_factor=1, service_workers="block")
                context.route("**/*", route)
                context.add_init_script(WHERE_SCRIPT)
                page = context.new_page()
                page.on("console", console)
                page.on("pageerror", page_error)
                page.on("requestfailed", failed)
                page.on("response", response)
                page.on("dialog", dialog)
                s = Session(page, out, result["shots"], deadline)
                t_load = time.monotonic()
                try:
                    page.goto(address(rel), wait_until="load", timeout=max(1000, (deadline - time.monotonic()) * 1000))
                    result["ok"] = True
                except PwError as exc:
                    result["error"] = "the page did not finish loading: " + clip(str(exc).splitlines()[0], 300)
                result["load_ms"] = round((time.monotonic() - t_load) * 1000)
                try:
                    page.wait_for_load_state("networkidle",
                                             timeout=min(3000, max(100, (deadline - time.monotonic()) * 1000)))
                except PwError:
                    pass                               # a page that keeps loading (polling, streaming) - go on
                page.wait_for_timeout(500)             # its first frames
                result["doing"] = "its title, after loading"
                result["title"] = clip(page.title(), 200)
                s.shot("loaded")
                stopped = False
                for i, step in enumerate(steps, 1):
                    if time.monotonic() >= deadline:
                        result["steps"].append({"step": i, "error": f"not done: the {timeout:g} s were up"})
                        stopped = True
                        break
                    result["doing"] = f"step {i} ({clip(json.dumps(step, ensure_ascii=False), 120)})"
                    try:
                        result["steps"].append({"step": i, "did": s.step(step)})
                    except (Fail, PwError, ValueError, TypeError) as exc:
                        result["steps"].append({"step": i, "error": clip(str(exc).splitlines()[0], 400)})
                result["doing"] = "the last screenshot and the page's text"
                if stopped or (steps and "shot" not in steps[-1]):
                    s.shot("when the time was up" if stopped else "end")
                try:
                    result["text"] = clip(page.evaluate("document.body ? document.body.innerText : ''") or "",
                                          MAX_TEXT)
                except PwError:
                    pass
                result["url"] = unquote(page.url.replace(f"http://{HOST}/", ""))
            finally:
                browser.close()
    finally:
        guard.cancel()
        result.pop("doing", None)
        for e in result["errors"]:                     # a stack says where; one without gets the page's word for it
            w = next((w for w in wheres if not e["stack"] and w.get("message", "").endswith(e["message"])), None)
            if w:
                e["stack"] = f"at {str(w.get('file', '')).replace(f'http://{HOST}/', '')}:{w.get('line')}:{w.get('col')}"
                wheres.remove(w)
        result["seconds"] = round(time.monotonic() - t0, 1)
    return result


def check() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:  # noqa: BLE001 - any reason the browser does not start
        return False


def main(argv: list[str]) -> int:
    if argv[:1] == ["--check"]:
        return 0 if check() else 1
    if argv[:1] != ["--json"] or len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    result, once = new_result(), threading.Lock()
    try:
        req = json.loads(sys.stdin.read() if argv[1] == "-" else argv[1])
        if not isinstance(req, dict):
            raise Fail("the request must be a JSON object")
        run(req, result, once)
    except Fail as exc:
        result.update(ok=False, error=str(exc))
    except ValueError as exc:
        result.update(ok=False, error=f"bad request: {exc}")
    except Exception as exc:  # noqa: BLE001 - the browser itself failed: say so, with what came before
        result.update(ok=False, error=f"the browser failed: {exc.__class__.__name__}: {clip(str(exc), 400)}")
    say(result, once)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
