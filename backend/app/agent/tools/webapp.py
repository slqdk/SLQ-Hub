"""web_app: a web page or app from the working folder, or an artifact, opened in a headless browser in the sandbox
(pageshot there: Playwright with Chromium's headless shell), clicked, typed and played, and shown to the model in
screenshots with what its console said, its errors and the requests that failed (0.29.0: the assistant could build a
browser game it could not see - chat 78's goal)."""
from __future__ import annotations

import io
import json
import re
import shlex
import shutil
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from ...chats import store as chats
from .base import Tool, ToolContext, ToolResult, clip, obj, register
from .shell import SandboxError, sandbox_exec

MAX_STEPS, MAX_SHOTS = 40, 8
SCREEN = (1280, 800)
PANEL = (760, 820)                  # the side panel on a big screen (min(46vw, 760px) wide, below its head)
STEP_KEYS = {"click", "type", "key", "hold", "move", "scroll", "wait", "shot", "eval"}
PAGES = (".html", ".htm")
RUNNABLE = ("html", "react", "svg", "mermaid")
FRAME = "allow-scripts allow-forms allow-modals allow-downloads"   # the panel's <iframe sandbox>; online + popups


async def refresh(hub) -> None:
    """Whether the sandbox has the browser (its daemon's /health says browser: pageshot --check), asked every few
    minutes - before a turn picks its tools and writes its prompt."""
    at, _ = hub.extras.get("web_app", (float("-inf"), False))
    if time.monotonic() - at < 300:
        return
    try:
        async with httpx.AsyncClient(timeout=5, transport=hub.transports.sandbox) as c:
            r = await c.get(hub.env.sandbox_url + "/health")
        ok = r.json().get("browser") is True
    except (httpx.HTTPError, ValueError, AttributeError):
        ok = False
    hub.extras["web_app"] = (time.monotonic(), ok)


def ready(hub) -> bool:
    return bool(hub.extras.get("web_app", (0.0, False))[1])


def _available(ctx: ToolContext) -> bool:
    """The owner's chats, where the sandbox has the browser (a person's turn runs confined, without it for now)."""
    return getattr(ctx.engine, "device", None) is None and ready(ctx.hub)


# ---------------------------------------------------------------- an artifact, as the side panel shows it

HEAD = '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
SHOW_ERRORS = '''<script>window.addEventListener("error", function (e) {
    var p = document.createElement("pre");
    p.style.cssText = "color:#c22b2b;white-space:pre-wrap;padding:12px;margin:0;font:13px ui-monospace,monospace";
    p.textContent = "Error: " + (e.message || e.error);
    document.body.prepend(p);
  });</script>'''
REACT = '''<script src="/artifact-runtime/react.js"></script><script src="/artifact-runtime/react-dom.js"></script>
<script src="/artifact-runtime/babel.js"></script><script src="/artifact-runtime/tailwind.js"></script>
<style>body{margin:0;font-family:system-ui,sans-serif;color:#20262a}</style></head>
<body><div id="root"></div><script>
(function () {
  var src = %s;
  try {
    var out = Babel.transform(src, { filename: "artifact.tsx", presets: [["react", { runtime: "classic" }],
      "typescript"], plugins: ["transform-modules-commonjs"] }).code;
    var module = { exports: {} };
    var req = function (name) {
      if (name === "react") return React;
      if (name === "react-dom" || name === "react-dom/client") return ReactDOM;
      throw new Error("An artifact can only import from react (it tried " + name + ")");
    };
    new Function("module", "exports", "require", "React", out)(module, module.exports, req, React);
    var App = module.exports.default || Object.values(module.exports).find(function (v) { return typeof v === "function"; });
    if (!App) throw new Error("The component needs a default export");
    ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(App));
  } catch (e) {
    console.error(e);
    var p = document.createElement("pre");
    p.style.cssText = "color:#c22b2b;white-space:pre-wrap;padding:12px;margin:0;font:13px ui-monospace,monospace";
    p.textContent = String(e && e.message || e);
    document.body.prepend(p);
  }
})();
</script></body></html>'''


ORIGIN = "http://page.local"                # where pageshot serves the page (its artifact runtime too)
OFFLINE_GUARD = """<script>(function(){
try{["RTCPeerConnection","webkitRTCPeerConnection","RTCDataChannel","RTCRtpSender","RTCRtpReceiver",
"RTCIceTransport","RTCSctpTransport","RTCDtlsTransport"].forEach(function(k){
try{Object.defineProperty(window,k,{value:undefined,configurable:false,writable:false});}catch(e){}});}catch(e){}
try{var F="iframe,frame,object,embed";function strip(n){if(n.nodeType!==1)return;
if(n.matches&&n.matches(F))n.remove();else if(n.querySelectorAll)n.querySelectorAll(F).forEach(function(el){el.remove();});}
new MutationObserver(function(ms){ms.forEach(function(m){m.addedNodes.forEach(strip);});}).observe(document,{childList:true,subtree:true});}catch(e){}
})();</script>"""


def offline_policy(origin: str) -> str:
    """The panel's Content-Security-Policy while the assistant asks before going online (offlinePolicy)."""
    return "; ".join(["default-src 'none'", f"script-src 'unsafe-inline' 'unsafe-eval' {origin}/artifact-runtime/",
                      f"style-src 'unsafe-inline' {origin}/artifact-runtime/", f"img-src data: blob: {origin}",
                      "font-src data: blob:", "media-src data: blob:", "connect-src 'none'", "frame-src 'none'",
                      "form-action 'none'", "base-uri 'none'"])


def with_policy(doc: str, policy: str) -> str:
    """The policy and the guard first in the page, after a doctype (withPolicy)."""
    head = f'<meta http-equiv="Content-Security-Policy" content="{policy}">{OFFLINE_GUARD}'
    m = re.match(r"\s*<!doctype[^>]*>", doc, re.I)
    return m.group(0) + head + doc[m.end():] if m else head + doc


def _escape(s: str) -> str:
    return s.translate({ord("&"): "&amp;", ord("<"): "&lt;", ord(">"): "&gt;", ord('"'): "&quot;", ord("'"): "&#39;"})


def artifact_doc(kind: str, content: str, offline: bool = False, origin: str = ORIGIN) -> str:
    """The page the side panel runs for an artifact - with its offline policy while the assistant asks before going
    online: artifactDoc in frontend/src/components/ArtifactPanel.tsx, kept the same (a test runs that one with node
    and compares)."""
    doc = _build(kind, content)
    return with_policy(doc, offline_policy(origin)) if offline else doc


def _build(kind: str, content: str) -> str:
    """buildDoc."""
    if kind == "html":
        if re.search(r"<html[\s>]|<!doctype", content, re.I):
            return content
        return (f'<!doctype html><html><head>{HEAD}{SHOW_ERRORS}<script src="/artifact-runtime/tailwind.js"></script>\n'
                f'<style>body{{font-family:system-ui,sans-serif;margin:16px;color:#20262a}}</style></head><body>{content}'
                '</body></html>')
    if kind == "svg":
        return (f'<!doctype html><html><head>{HEAD}<style>html,body{{margin:0;height:100%}}body{{display:grid;'
                'place-items:center}\nsvg{max-width:100%;max-height:100vh;height:auto}</style></head><body>'
                f'{content}</body></html>')
    if kind == "mermaid":
        return (f'<!doctype html><html><head>{HEAD}{SHOW_ERRORS}<script src="/artifact-runtime/mermaid.js"></script>\n'
                '<style>body{margin:16px;font-family:system-ui,sans-serif;display:flex;justify-content:center}</style>'
                f'</head>\n<body><pre class="mermaid">{_escape(content)}</pre>\n<script>mermaid.initialize({{ '
                'startOnLoad: true, theme: "neutral", securityLevel: "strict" });</script></body></html>')
    source = json.dumps(content, ensure_ascii=False).replace("<", "\\u003c")
    return f"<!doctype html><html><head>{HEAD}{SHOW_ERRORS}\n" + REACT % source


def _artifact_page(ctx: ToolContext, aid: str, folder: Path, offline: bool) -> str:
    """Writes an artifact into folder as the panel runs it: index.html and the Hub's artifact runtime it loads.
    Returns how to name it. Raises ValueError."""
    art = chats.get_artifact(ctx.chat["id"], aid)
    if not art:
        known = ", ".join(a["id"] for a in chats.list_artifacts(ctx.chat["id"]))
        raise ValueError(f"There is no artifact {aid!r} in this chat" + (f" (it has: {known})" if known else ""))
    if art["type"] not in RUNNABLE:
        raise ValueError(f"{aid!r} is a {art['type']} artifact; html, react, svg and mermaid ones run in a browser")
    doc = artifact_doc(art["type"], art["content"], offline)
    names = sorted(set(re.findall(r"/artifact-runtime/([\w.-]+)", doc)))
    runtime = ctx.hub.env.static_dir / "artifact-runtime" if ctx.hub.env.static_dir else None
    if names and (runtime is None or not runtime.is_dir()):
        raise ValueError("The Hub's artifact runtime is missing (the web UI is not built), so artifacts cannot be "
                         "opened here")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(doc, encoding="utf-8")
    for name in names:                          # one the page names that the Hub does not have is a 404 to report
        if (runtime / name).is_file():
            (folder / "artifact-runtime").mkdir(exist_ok=True)
            shutil.copyfile(runtime / name, folder / "artifact-runtime" / name)
    return f"the artifact {aid!r} (version {art['version']}, {art['type']}) as the side panel shows it"


# ---------------------------------------------------------------- the tool

def _steps(raw) -> list[dict]:
    """The steps as pageshot takes them. A model may send them as a JSON string. Raises ValueError."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError as exc:
            raise ValueError(f"steps is not valid JSON ({exc}): give a list of steps") from exc
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_STEPS:
        raise ValueError(f"steps must be a list of at most {MAX_STEPS} steps")
    for i, step in enumerate(raw, 1):
        if not isinstance(step, dict) or not (set(step) & STEP_KEYS):
            raise ValueError(f"step {i} must be an object with one of: {', '.join(sorted(STEP_KEYS))}")
    return raw


def _fit(size) -> list[int]:
    """A size within the screens the browser opens (320x240 to 2560x1600)."""
    return [max(320, min(2560, int(size[0]))), max(240, min(1600, int(size[1])))]


def _screen(raw, default: tuple[int, int]) -> list[int]:
    m = re.fullmatch(r"\s*(\d{3,4})\s*[x×*]\s*(\d{3,4})\s*", str(raw or ""))
    if not m:
        return list(default)
    return _fit((m.group(1), m.group(2)))


def _sees(ctx: ToolContext) -> bool:
    from ..loop import vision
    return vision(ctx.model) or getattr(ctx.engine, "cloud", None) is not None


def _report(res: dict, shots: int, sees: bool, name: str) -> str:
    """pageshot's answer in words: what loaded, each step, what the page said and what went wrong."""
    lines = [f"Opened {name}" + (f" - \"{res['title']}\"" if res.get("title") else "")
             + (f", loaded in {res['load_ms']} ms." if res.get("load_ms") is not None else ".")]
    for s in res.get("steps") or []:
        lines.append(f"Step {s.get('step')}: " + (f"FAILED - {s['error']}" if s.get("error") else str(s.get("did"))))
    for i, shot in enumerate((res.get("shots") or [])[:shots], 1):
        lines.append(f"Screenshot {i}" + (f" ({shot['label']})" if shot.get("label") else "")
                     + f": {shot.get('width')}x{shot.get('height')} px")
    errors = res.get("errors") or []
    if errors:
        lines.append(f"Uncaught errors ({len(errors)}):")
        lines += [f"- {e.get('message')}" + (f"\n  {e['stack'].replace(chr(10), chr(10) + '  ')}"
                                             if e.get("stack") else "") for e in errors]
    console = res.get("console") or []
    if console:
        counts = ", ".join(f"{n} {k}" for k, n in (res.get("console_counts") or {}).items())
        lines.append(f"Console ({counts}):")
        lines += [f"- [{c.get('type')}] {c.get('text')}" + (f" ({c['where']})" if c.get("where") else "")
                  for c in console]
    failed = res.get("failed") or []
    if failed:
        lines.append("Requests that failed:")
        lines += [f"- {f.get('url')}: " + (f"HTTP {f['status']}" if f.get("status") else str(f.get("error")))
                  for f in failed]
    if res.get("blocked"):
        lines.append("Not loaded, offline: " + ", ".join(res["blocked"]))
    for d in res.get("dialogs") or []:
        lines.append(f"The page's {d.get('type')} (dismissed): {d.get('text')}")
    if res.get("error"):
        lines.append("Problem: " + str(res["error"]))
    if not errors and not failed and res.get("ok"):
        lines.append("No uncaught errors and no failed requests.")
    if res.get("text") and not sees:
        lines.append("Text on the page:\n" + clip(res["text"], 2000)[0])
    return "\n".join(lines)


def _original(ctx: ToolContext, ref, area_raw) -> tuple[bytes | None, tuple[int, int] | None, tuple | None]:
    """compare's picture (a path in the working folder, or an attached picture's id), its size - the page opens at
    it unless screen says otherwise - and compare_area's box in it. Raises ValueError."""
    ref = str(ref or "").strip()
    if not ref:
        if area_raw not in (None, "", []):
            raise ValueError("compare_area is a part of compare's picture: give compare (the picture) too")
        return None, None, None
    from .compare import area_box
    from .images import _image_bytes
    try:
        data, _ = _image_bytes(ctx, ref)
        with Image.open(io.BytesIO(data)) as img:
            size = ImageOps.exif_transpose(img).size          # as compare turns it (a phone's photo)
    except FileNotFoundError as exc:
        raise ValueError(f"compare: no such picture in the working folder: {ref}") from exc
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError(f"compare: {ref} is not a picture the Hub can read ({exc})") from exc
    return data, size, area_box(area_raw, size)


def _compared(last: bytes | None, original: bytes, ref: str, area, images: list) -> str:
    """The page's last screenshot against compare's picture (or the part of it compare_area gave), and the picture
    of the two (0.35.0)."""
    if last is None:
        return f"Not compared with {ref}: there was no screenshot of the page."
    from .compare import compare
    from .images import _shown
    try:
        report, picture, _ = compare(last, original, area)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        return f"Not compared with {ref}: {exc}"
    images.append(_shown(picture, "compared.png"))
    return f"The last screenshot against {ref}:\n{report}"


def _timeout(raw) -> int:
    try:
        return max(5, min(120, int(float(raw or 30))))
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout is seconds, e.g. 30") from exc


def _clear(wd, rel: str) -> None:
    """A run's folder removed - only the one it made: not through a link put in its way."""
    p = wd.root
    for part in rel.split("/"):
        p = p / part
        if p.is_symlink():
            return
    shutil.rmtree(p, ignore_errors=True)


def _sweep(wd) -> None:
    """Runs' folders older than ten minutes: a stopped turn's (the browser may have written on after the stop)."""
    web = wd.root / ".hub" / "web"
    if (wd.root / ".hub").is_symlink() or web.is_symlink() or not web.is_dir():
        return
    for d in web.iterdir():
        try:
            if not d.is_symlink() and d.is_dir() and d.stat().st_mtime < time.time() - 600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _page(wd, target: str, root_arg: str) -> tuple[Path, Path]:
    """(the folder served as the site, the page) for a target in the working folder. Raises ValueError."""
    path = wd.resolve(target)
    if path.is_dir():
        path = path / "index.html"
    if not path.is_file():
        raise ValueError(f"No such page in the working folder: {target}")
    if path.suffix.lower() not in PAGES:
        raise ValueError(f"{target} is not a web page (.html)")
    root = wd.resolve(root_arg) if root_arg else path.parent
    if not root.is_dir() or root.resolve() not in path.resolve().parents:
        raise ValueError(f"root must be a folder that holds {target}")
    return root, path


async def web_app(ctx: ToolContext, args: dict) -> ToolResult:
    from .. import internet
    wd = ctx.workdir
    target = str(args.get("target") or "").strip()
    aid = str(args.get("artifact") or "").strip()
    if bool(target) == bool(aid):
        return ToolResult("Give target (an .html file in the working folder, or a folder with index.html) or "
                          "artifact (an artifact's id) - one of them", is_error=True)
    run_id = re.sub(r"[^A-Za-z0-9_-]", "", ctx.call_id)[-32:] or str(int(time.time()))
    out_rel = f".hub/web/{run_id}"
    _sweep(wd)
    try:
        try:
            steps = _steps(args.get("steps"))
            timeout = _timeout(args.get("timeout"))
            original, original_size, area = _original(ctx, args.get("compare"), args.get("compare_area"))
            if aid:
                root = wd.resolve(f"{out_rel}/page")
                name = _artifact_page(ctx, aid, root, internet.asking(ctx.settings))
                path = root / "index.html"
            else:
                root, path = _page(wd, target, str(args.get("root") or "").strip())
                name = wd.rel(path)
        except ValueError as exc:                     # JSON in steps that is not JSON is one too
            return ToolResult(str(exc), is_error=True)
        site = wd.rel(root)
        default = _fit(original_size) if original_size else (PANEL if aid else SCREEN)    # compare's: its size
        req = {"root": wd.sandbox_path if site == "." else f"{wd.sandbox_path}/{site}",
               "path": path.resolve().relative_to(root.resolve()).as_posix(), "steps": steps,
               "screen": _screen(args.get("screen"), default), "timeout": timeout,
               "out": f"{wd.sandbox_path}/{out_rel}", "online": bool(ctx.online)}
        if aid:                                       # the panel's frame: popups only while the internet is allowed
            req["sandbox"] = FRAME + ("" if internet.asking(ctx.settings) else " allow-popups")
        try:
            res = await sandbox_exec(ctx, "pageshot --json " + shlex.quote(json.dumps(req)), wd.sandbox_path,
                                     timeout + 60, offline=not ctx.online, end_leftovers=ctx.online_once)
        except SandboxError as exc:
            return ToolResult(str(exc), is_error=True)
        try:
            lines = (res.get("stdout") or "").strip().splitlines()
            answer = json.loads(lines[-1]) if lines else None
        except ValueError:
            answer = None
        if not isinstance(answer, dict):
            detail = clip(((res.get("stdout") or "") + "\n" + (res.get("stderr") or "")).strip(), 3000)[0]
            why = " (timed out)" if res.get("timed_out") else ""
            return ToolResult(f"The browser gave no result{why}, exit code {res.get('exit_code')}:\n{detail}",
                              is_error=True)
        images: list = []
        last = None
        try:
            from .images import _shown
            for i in range(1, min(MAX_SHOTS, len(answer.get("shots") or [])) + 1):
                p = wd.resolve(f"{out_rel}/{i}.png")  # only where pageshot was told to write, inside the folder
                if not p.is_file() or p.stat().st_size > 20_000_000:
                    break
                data = p.read_bytes()
                with Image.open(io.BytesIO(data)) as img:
                    img.verify()
                images.append(_shown(data, f"page-{i}.png"))
                last = data
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            pass                                      # the report, with the pictures that were whole
    finally:
        _clear(wd, out_rel)                           # also when the turn is stopped
    sees = _sees(ctx)
    text = _report(answer, len(images), sees, name)
    if images and sees:
        text += "\nThe screenshots are shown below; click coordinates are pixels in them."
    if original is not None:
        text += "\n\n" + _compared(last, original, args.get("compare"), area, images)
    return ToolResult(text, images=images, is_error=not answer.get("ok"))     # the user sees them in any case


TEXT = ("Open a web page, browser app or game in a browser (Chromium) and see it: screenshots, what its console says, "
        "uncaught errors with where they happened, requests that failed (a missing file) and alert() texts. Use it to "
        "check every page, game or html/react artifact you make before you hand it over. target: an .html file in "
        "the working folder, or a folder with index.html; its folder is served as a web server's root would be "
        "(http://page.local/, or give root), so fetch() of its own files works. Or artifact: an artifact's id - it "
        "opens as the side panel shows it, 760x820. You get a screenshot when it has loaded, at each shot step and at "
        "the end. steps act on it in order: {\"click\": [x, y]} or {\"click\": \"Start\"} (a CSS selector, or text on "
        "the page), with \"double\": true or \"button\": \"right\"; {\"type\": \"text\"}; {\"key\": \"Enter\"} (or "
        "\"ArrowLeft ArrowLeft\", \"ctrl+s\", \"Space\"); {\"hold\": \"ArrowRight\", \"seconds\": 1.5} (a key held "
        "down, as in a game); {\"move\": [x, y]}; {\"scroll\": [x, y, 300]}; {\"wait\": 2}; {\"shot\": \"label\"}; "
        "{\"eval\": \"window.game.score\"} (a JavaScript expression's value - check state you cannot see). "
        "Coordinates are pixels in the screenshots. Each call opens the page afresh, so give every step for what you "
        "want to see in one call. To make a page look like a picture - a screenshot of an original the user gave - "
        "give compare: that picture's path; the page opens at its size and the last screenshot is compared with it: "
        "how much matches, where it differs most with the colours on each side, and the two side by side with the "
        "differences marked. compare_area compares one part of the picture only (\"x,y,width,height\" in its "
        "pixels): to get one part right (the title bar), or when the rest differs on purpose (a photo you do not "
        "have).")
PARAMS = obj({"target": {"type": "string", "description": "An .html file in the working folder, or a folder with "
                                                          "index.html"},
              "artifact": {"type": "string", "description": "Instead of target: an html, react, svg or mermaid "
                                                            "artifact's id"},
              "steps": {"type": "array", "items": {"type": "object"},
                        "description": "e.g. [{\"click\": \"New game\"}, {\"hold\": \"ArrowRight\", \"seconds\": 1}, "
                                       "{\"eval\": \"window.game.x\"}, {\"shot\": \"moved\"}]"},
              "screen": {"type": "string", "description": "Viewport size, default 1280x800 (760x820 for an "
                                                          "artifact; 390x844 for a phone)"},
              "root": {"type": "string", "description": "The folder served as the site's root, if not the page's "
                                                        "own (one that holds the page)"},
              "timeout": {"type": "integer", "description": "Seconds for the whole run (default 30, max 120)"},
              "compare": {"type": "string", "description": "A picture in the working folder (e.g. an attached "
                                                           "screenshot of the original) to compare the last "
                                                           "screenshot with"},
              "compare_area": {"type": "string", "description": "Only this part of compare's picture: "
                                                                "\"x,y,width,height\" in its pixels, e.g. "
                                                                "\"0,0,640,60\""}})
ASKS = (" While the user has you ask before going online, the page loads only its own files - anything from the web "
        "(a CDN, an API) is blocked and listed as such.")


def _dynamic(ctx: ToolContext) -> tuple[str, dict]:
    from .. import internet
    return (TEXT + ASKS if internet.asking(ctx.settings) else TEXT), PARAMS


register(Tool("web_app", TEXT, PARAMS, web_app, available=_available, dynamic=_dynamic))
