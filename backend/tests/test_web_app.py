"""Web pages, browser games and artifacts in a browser (0.29.0): web_app in the Hub, pageshot.py in the sandbox
image (Playwright with Chromium's headless shell). The sandbox is faked for the Hub's side; pageshot's own parts are
tested as they are, and run for real in a real browser where Playwright and Chromium are installed (else skipped)."""
from __future__ import annotations

import dataclasses
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from app import cli
from app.agent.tools import webapp
from app.chats import store as chats

from .test_agent import finish, new_chat, say
from .test_internet import answer, approvals, asking, result_of
from .test_people import add_person, client_from, sign_in, turn, user_msg

REPO = Path(__file__).resolve().parents[2]
PAGESHOT = REPO / "services" / "sandbox" / "pageshot.py"
spec = importlib.util.spec_from_file_location("sandbox_pageshot", PAGESHOT)
pageshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pageshot)


def png(w: int = 40, h: int = 30) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def fake_pageshot(app, fakes, answer_: dict, shots: int = 2, look=None):
    """The sandbox runs pageshot: it writes the screenshots where it was told and prints its JSON. look(req, ws)
    sees the folders before the Hub clears them."""
    def handler(body):
        if not body["cmd"].startswith("pageshot --json "):
            return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 1, "truncated": False}
        req = json.loads(shlex.split(body["cmd"])[2])
        fakes.sandbox["pageshot"] = req
        ws = app.state.hub.env.workspace_dir
        if look:
            look(req, ws)
        out = ws / req["out"].removeprefix("/workspace/")
        out.mkdir(parents=True, exist_ok=True)
        for i in range(1, shots + 1):
            (out / f"{i}.png").write_bytes(png(40 + i, 30))
        return {"exit_code": 0, "stdout": json.dumps(answer_) + "\n", "stderr": "", "timed_out": False,
                "duration_ms": 3000, "truncated": False}
    fakes.sandbox["handler"] = handler


OPENED = {"ok": True, "title": "Snow game", "url": "index.html", "load_ms": 412,
          "shots": [{"file": "1.png", "label": "loaded", "width": 1280, "height": 800},
                    {"file": "2.png", "label": "end", "width": 1280, "height": 800}],
          "steps": [{"step": 1, "did": "clicked 'Start' at (640, 400)"},
                    {"step": 2, "error": "'Pause' not found on the page (neither a CSS selector nor text on it)"},
                    {"step": 3, "did": "value: 42"}],
          "console": [{"type": "log", "text": "level 1 loaded", "where": "game.js:12"}],
          "console_counts": {"log": 1},
          "errors": [{"message": "TypeError: player is undefined",
                      "stack": "TypeError: player is undefined\n    at update (game.js:88:5)"}],
          "failed": [{"url": "img/snow.png", "status": 404}], "blocked": [],
          "dialogs": [{"type": "alert", "text": "Game over"}], "text": "Snow game\nScore: 42", "seconds": 3.1}


def call(fakes, args: dict, title: str = "Spil") -> None:
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"tool_calls": [{"name": "web_app", "arguments": args}]}, {"content": "Set."},
                              {"content": title}]


def tool_names(fakes) -> set[str]:
    return {t["function"]["name"] for t in fakes.llama["requests"][0].get("tools") or []}


def browser(app, fakes) -> None:
    fakes.sandbox["browser"] = True
    app.state.hub.extras.pop("web_app", None)                       # asked again, not cached


def game(folder: Path) -> None:
    (folder / "game" / "data").mkdir(parents=True, exist_ok=True)
    (folder / "game" / "index.html").write_text("<!doctype html><title>Snow game</title><script src=game.js></script>")
    (folder / "game" / "game.js").write_text("window.game = {score: 42};\n")
    (folder / "game" / "data" / "level.json").write_text("{}")


# ---------------------------------------------------------------- the Hub's side, with the sandbox faked

async def test_only_where_the_sandbox_has_a_browser(signed_in, app, fakes):
    fakes.llama["replies"] = [{"content": "Hej."}, {"content": "Hej"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hej"))
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "web_app" not in tool_names(fakes) and "web_app" not in system
    assert "Before you call a task done, check it the way the user will use it: run the program or its tests, open " \
           "or play what you made, compare results" in system
    browser(app, fakes)
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hej."}]
    await finish(app, await say(signed_in, c["id"], "Hej igen"))
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "web_app" in tool_names(fakes)
    assert "open or play what you made (web_app for web pages and browser games), compare" in system
    fakes.sandbox["windows"] = True
    app.state.hub.extras.pop("windows_app", None)
    fakes.llama["requests"].clear()
    fakes.llama["replies"] = [{"content": "Hej."}]
    await finish(app, await say(signed_in, c["id"], "Og igen"))
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "(web_app for web pages and browser games, windows_app for Windows programs)" in system


async def test_the_working_rules_are_in_the_prompt(signed_in, app, fakes):
    """What the assistant was missing next to Claude's own way of working (0.29.0): existing documentation and tools
    first, a notes file in long work, checking the way the user will use it, and saying what was not checked."""
    fakes.llama["replies"] = [{"content": "Hej."}, {"content": "Hej"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hej"))
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "look for its documentation and existing tools first" in system
    assert "keep what you find in NOTES.md in the working folder as you go" in system          # named exactly (0.31.0)
    assert "Measure with code rather than reason at length" in system
    assert "what you did, what you checked and what you could not check" in system
    # 0.33.0: Søren's CM98 folder was 14 files side by side, a test script among them
    assert "Keep the folder in order, the way the user will look for things" in system
    assert "documentation and write-ups (a file format described, a report) in docs/" in system       # (0.34.0)
    # 0.35.0: chat 135 made a "reference" with image_generate, and rewrote game.js twice while the task was the look
    assert "that picture is the reference - never one you make with image_generate" in system
    assert "When the task is how it looks, change how it looks and leave how it works alone." in system
    assert "Do not move the user's own files unless they ask. Scratch and test files go in .hub/tmp/" in system


async def test_a_page_is_opened_and_its_report_read(signed_in, app, fakes):
    browser(app, fakes)
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    game(folder)
    fake_pageshot(app, fakes, OPENED)
    steps = [{"click": "Start"}, {"click": "Pause"}, {"eval": "window.game.score"}]
    call(fakes, {"target": "game", "steps": steps})
    run = await finish(app, await say(signed_in, c["id"], "Prøv spillet"))
    res = result_of(run, "web_app")
    req = fakes.sandbox["pageshot"]
    assert req["root"] == f"/workspace/chats/{c['id']}/game" and req["path"] == "index.html"   # its folder is the site
    assert req["steps"] == steps and req["screen"] == [1280, 800] and req["timeout"] == 30
    assert req["out"].startswith(f"/workspace/chats/{c['id']}/.hub/web/") and req["online"] is True
    assert "sandbox" not in req
    ex = fakes.sandbox["execs"][-1]
    assert ex["timeout"] == 30 + 60 and "offline" not in ex
    assert len(res["images"]) == 2 and res["ok"] is True              # a failed step is news, not a failed call
    text = res["text"]
    assert text.startswith('Opened game/index.html - "Snow game", loaded in 412 ms.')
    assert "Step 1: clicked 'Start' at (640, 400)" in text and "Step 2: FAILED - 'Pause' not found" in text
    assert "Step 3: value: 42" in text and "Screenshot 2 (end): 1280x800 px" in text
    assert "Uncaught errors (1):\n- TypeError: player is undefined\n  TypeError: player is undefined\n" \
           "      at update (game.js:88:5)" in text
    assert "Console (1 log):\n- [log] level 1 loaded (game.js:12)" in text
    assert "Requests that failed:\n- img/snow.png: HTTP 404" in text
    assert "The page's alert (dismissed): Game over" in text
    assert "No uncaught errors" not in text
    assert not (folder / ".hub" / "web").exists() or not any((folder / ".hub" / "web").iterdir())


def picture(w: int, h: int, bar: tuple | None = None) -> bytes:
    """A dark screen with a bar across its top, as a title bar."""
    from PIL import ImageDraw
    img = Image.new("RGB", (w, h), (40, 40, 40))
    if bar:
        ImageDraw.Draw(img).rectangle((0, 0, w - 1, h * 50 // 480), fill=bar)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


RED, BLUE = (170, 2, 15), (1, 32, 218)


def test_a_page_against_the_picture_it_should_look_like():
    """0.35.0 - Søren's chat 135: three screenshots of the original to copy, and no way to set the page beside
    them. How much matches, where it differs most with the colours on each side, and a picture of the two."""
    from app.agent.tools.compare import compare
    original = picture(640, 480, RED)
    text, shown, figures = compare(original, original)
    assert figures["same"] == 100.0 and figures["mean_difference"] == 0 and "No area differs much" in text
    text, shown, figures = compare(picture(640, 480, BLUE), original)              # the club's colour wrong
    assert 85 < figures["same"] < 95
    assert "- x 0-80, y 0-80: 36% match - original #aa020f, page #0120da" in text, text
    assert len(figures["cells"]) == 6 and "y 80-160" not in text                    # only where it differs
    with Image.open(io.BytesIO(shown)) as img:
        assert img.size == (640 * 3 + 16, 480)                                     # original | page | marked
        assert img.getpixel((2 * 648 + 10, 10))[0] > 200 and img.getpixel((2 * 648 + 10, 300))[0] < 120
    text, _, figures = compare(picture(1280, 960, RED), original)                   # the page at another size
    assert "compared at the original's size. Open the page at it (screen: \"640x480\")" in text
    assert figures["same"] > 99
    near = Image.new("RGB", (640, 480), (52, 52, 52))                               # 12 off everywhere: the same
    buf = io.BytesIO()
    near.save(buf, "PNG")
    _, shown, figures = compare(buf.getvalue(), picture(640, 480))
    assert figures["same"] == 100.0
    with Image.open(io.BytesIO(shown)) as img:                                     # and no red for it
        r, g, b = img.convert("RGB").getpixel((2 * 648 + 10, 300))
        assert r == g == b, (r, g, b)


def test_one_part_of_the_picture():
    """compare_area: the title bar alone, or all but the original's photo - which the page does not have, and which
    would otherwise fill the report. Places stay the original's pixels."""
    from app.agent.tools.compare import area_box, compare
    size = (640, 480)
    assert area_box(None, size) is None and area_box("", size) is None and area_box([], size) is None
    assert area_box("0,0,640,60", size) == (0, 0, 640, 60) == area_box([0, 0, 640, 60], size)
    assert area_box("[0, 0, 640, 60]", size) == area_box("0 0 640 60", size) == (0, 0, 640, 60)
    assert area_box("600,-10,100,100", size) == (600, 0, 640, 90)                    # clipped to the picture
    for bad in ("0,0,640", "the title bar", "700,0,10,10", "0,0,1,1"):
        with pytest.raises(ValueError):
            area_box(bad, size)
    with pytest.raises(ValueError, match=r'give x,y,width,height in the picture\'s pixels, e.g. "0,0,640,60"'):
        area_box("top", size)
    original = picture(640, 480, RED)
    page = Image.new("RGB", size, (90, 90, 90))                                       # the right bar, not the photo
    from PIL import ImageDraw
    ImageDraw.Draw(page).rectangle((0, 0, 639, 50), fill=RED)
    buf = io.BytesIO()
    page.save(buf, "PNG")
    page = buf.getvalue()
    text, _, figures = compare(page, original)
    assert figures["same"] < 15
    text, shown, figures = compare(page, original, (0, 0, 640, 50))
    assert text.startswith("Compared with the original (640x480), x 0-640, y 0-50 of it: 100.0% of the pixels match")
    assert "No area differs much" in text and text.endswith("(red: where they differ) - that part of them.")
    with Image.open(io.BytesIO(shown)) as img:
        assert img.size == (640, 50 * 3 + 16)                                        # a strip: one above the other
    text, shown, figures = compare(picture(640, 480, BLUE), original, area_box("100,0,200,100", size))
    assert "x 100-300, y 0-100 of it" in text and figures["cells"]
    assert all(100 <= c["box"][0] < c["box"][2] <= 300 and c["box"][3] <= 100 for c in figures["cells"])
    assert "- x 100-120, y 0-20: 0% match - original #aa020f, page #0120da" in text, text          # 20x20 cells
    text, shown, _ = compare(picture(640, 480, BLUE), original, area_box("10,10,20,20", size))   # small: larger
    with Image.open(io.BytesIO(shown)) as img:
        assert img.size == (80 * 3 + 16, 80)


async def test_a_page_is_compared_with_the_original(signed_in, app, fakes):
    browser(app, fakes)
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    game(folder)
    (folder / ".hub" / "attachments").mkdir(parents=True)
    (folder / ".hub" / "attachments" / "original.png").write_bytes(picture(640, 480, RED))
    fake_pageshot(app, fakes, {**OPENED, "errors": [], "failed": [], "dialogs": [], "steps": []})
    call(fakes, {"target": "game", "compare": ".hub/attachments/original.png"})
    run = await finish(app, await say(signed_in, c["id"], "Få det til at ligne originalen"))
    res = result_of(run, "web_app")
    assert fakes.sandbox["pageshot"]["screen"] == [640, 480]                       # at the original's size
    assert "The last screenshot against .hub/attachments/original.png:\nCompared with the original (640x480)" \
        in res["text"]
    assert "The page's screenshot is 42x30 and the original 640x480" in res["text"]
    assert "Where it differs most" in res["text"] and len(res["images"]) == 3       # the two shots and the picture
    tool = next(t for t in fakes.llama["requests"][0]["tools"] if t["function"]["name"] == "web_app")["function"]
    assert "compare" in tool["parameters"]["properties"] and "give compare: that picture's path" in tool["description"]
    call(fakes, {"target": "game", "compare": "nope.png"})                            # no such picture: said, no run
    run = await finish(app, await say(signed_in, c["id"], "Igen"))
    res = result_of(run, "web_app")
    assert not res["ok"] and res["text"] == "compare: no such picture in the working folder: nope.png"
    assert "compare_area" in tool["parameters"]["properties"]
    assert "compare_area compares one part" in tool["description"]
    call(fakes, {"target": "game", "compare": ".hub/attachments/original.png", "compare_area": "0,0,640,60"})
    run = await finish(app, await say(signed_in, c["id"], "Titlen"))
    res = result_of(run, "web_app")
    assert "Compared with the original (640x480), x 0-640, y 0-60 of it:" in res["text"]
    runs = len(fakes.sandbox["execs"])
    for args, error in (({"compare_area": "0,0,640,60"}, "compare_area is a part of compare's picture: give compare "
                                                         "(the picture) too"),
                        ({"compare": ".hub/attachments/original.png", "compare_area": "the top"},
                         "compare_area 'the top': give x,y,width,height in the picture's pixels")):
        call(fakes, {"target": "game", **args})
        run = await finish(app, await say(signed_in, c["id"], "Igen"))
        res = result_of(run, "web_app")
        assert not res["ok"] and res["text"].startswith(error), res["text"]
    assert len(fakes.sandbox["execs"]) == runs                                         # no browser run for those


async def test_a_clean_page_says_so_and_a_blind_model_reads_its_text(signed_in, app, fakes, monkeypatch):
    browser(app, fakes)
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    game(folder)
    clean = {**OPENED, "errors": [], "failed": [], "dialogs": [], "steps": []}
    fake_pageshot(app, fakes, clean)
    monkeypatch.setattr(webapp, "_sees", lambda ctx: False)
    call(fakes, {"target": "game/index.html", "screen": "390x844"})
    run = await finish(app, await say(signed_in, c["id"], "Prøv igen"))
    text = result_of(run, "web_app")["text"]
    assert fakes.sandbox["pageshot"]["screen"] == [390, 844]
    assert "No uncaught errors and no failed requests." in text
    assert "Text on the page:\nSnow game\nScore: 42" in text and "click coordinates" not in text


async def test_where_the_site_starts_and_what_is_refused(signed_in, app, fakes):
    browser(app, fakes)
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    game(folder)
    (folder / "notes.txt").write_text("not a page")
    fake_pageshot(app, fakes, OPENED)
    call(fakes, {"target": "game/index.html", "root": ".", "steps": '[{"key": "Space"}]', "timeout": 999})
    await finish(app, await say(signed_in, c["id"], "Fra roden"))
    req = fakes.sandbox["pageshot"]
    assert req["root"] == f"/workspace/chats/{c['id']}" and req["path"] == "game/index.html"
    assert req["steps"] == [{"key": "Space"}] and req["timeout"] == 120
    n = len(fakes.sandbox["execs"])
    for bad, why in (({"target": "notes.txt"}, "is not a web page"), ({"target": "nope.html"}, "No such page"),
                     ({"target": "../../etc/passwd"}, "outside the working folder"),
                     ({"target": "game", "root": "game/data"}, "root must be a folder that holds"),
                     ({}, "Give target"), ({"target": "game", "artifact": "x"}, "one of them"),
                     ({"target": "game", "steps": [{"jump": 1}]}, "step 1 must be an object"),
                     ({"target": "game", "steps": "not json"}, "steps is not valid JSON")):
        call(fakes, bad)
        run = await finish(app, await say(signed_in, c["id"], "Igen"))
        res = result_of(run, "web_app")
        assert not res["ok"] and why in res["text"], (bad, res["text"])
    assert len(fakes.sandbox["execs"]) == n                          # none of them reached the sandbox


async def test_nothing_is_left_behind(signed_in, app, fakes, tmp_path):
    """A bad timeout is refused before an artifact's page is written; a run a stopped turn left (the browser may
    have written on) is swept by the next call."""
    import os
    import time
    browser(app, fakes)
    static = tmp_path / "dist"
    (static / "artifact-runtime").mkdir(parents=True)
    (static / "artifact-runtime" / "tailwind.js").write_text("//")
    app.state.hub.env = dataclasses.replace(app.state.hub.env, static_dir=static)
    c = await new_chat(signed_in)
    folder = app.state.hub.env.workspace_dir / "chats" / str(c["id"])
    game(folder)
    chats.save_artifact(c["id"], "p", "P", "html", "<p>hej</p>")
    old = folder / ".hub" / "web" / "call_old"
    old.mkdir(parents=True)
    (old / "1.png").write_bytes(png())
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    fresh = folder / ".hub" / "web" / "call_running"
    fresh.mkdir()
    call(fakes, {"artifact": "p", "timeout": "30s"})
    run = await finish(app, await say(signed_in, c["id"], "Prøv"))
    assert "timeout is seconds" in result_of(run, "web_app")["text"]
    assert sorted(p.name for p in (folder / ".hub" / "web").iterdir()) == ["call_running"]   # the old one swept


async def test_no_answer_from_the_browser(signed_in, app, fakes):
    browser(app, fakes)
    c = await new_chat(signed_in)
    game(app.state.hub.env.workspace_dir / "chats" / str(c["id"]))
    fakes.sandbox["handler"] = lambda body: {"exit_code": 137, "stdout": "", "stderr": "Killed", "timed_out": True,
                                             "duration_ms": 9, "truncated": False}
    call(fakes, {"target": "game"})
    run = await finish(app, await say(signed_in, c["id"], "Start"))
    res = result_of(run, "web_app")
    assert not res["ok"] and "gave no result (timed out)" in res["text"] and "Killed" in res["text"]
    fake_pageshot(app, fakes, {"ok": False, "error": "game/index.html is not a file in the working folder"}, shots=0)
    call(fakes, {"target": "game"})
    run = await finish(app, await say(signed_in, c["id"], "Igen"))
    res = result_of(run, "web_app")
    assert not res["ok"] and "Problem: game/index.html is not a file" in res["text"]


async def test_an_artifact_opens_as_the_side_panel_shows_it(signed_in, app, fakes, tmp_path):
    browser(app, fakes)
    static = tmp_path / "dist"
    (static / "artifact-runtime").mkdir(parents=True)
    for name in ("react.js", "react-dom.js", "babel.js", "tailwind.js", "mermaid.js"):
        (static / "artifact-runtime" / name).write_text(f"// {name}\n")
    hub = app.state.hub
    hub.env = dataclasses.replace(hub.env, static_dir=static)
    c = await new_chat(signed_in)
    src = 'export default function App() { return <button className="p-4">Snow</button>; }'
    chats.save_artifact(c["id"], "snow", "Snow", "react", "old")
    chats.save_artifact(c["id"], "snow", "Snow", "react", src)
    chats.save_artifact(c["id"], "notes", "Notes", "markdown", "# Notes")
    seen = {}

    def look(req, ws):
        page = ws / req["root"].removeprefix("/workspace/")
        seen["index"] = (page / req["path"]).read_text()
        runtime = page / "artifact-runtime"
        seen["runtime"] = sorted(p.name for p in runtime.iterdir()) if runtime.is_dir() else []

    fake_pageshot(app, fakes, {**OPENED, "url": "index.html"}, look=look)
    call(fakes, {"artifact": "snow", "steps": [{"click": "Snow"}]})
    run = await finish(app, await say(signed_in, c["id"], "Prøv artefaktet"))
    res = result_of(run, "web_app")
    req = fakes.sandbox["pageshot"]
    assert req["root"].startswith(f"/workspace/chats/{c['id']}/.hub/web/") and req["root"].endswith("/page")
    assert req["path"] == "index.html" and req["screen"] == [760, 820]
    assert req["sandbox"] == "allow-scripts allow-forms allow-modals allow-downloads allow-popups"
    assert seen["index"] == webapp.artifact_doc("react", src)              # the newest version, as the panel builds it
    assert seen["runtime"] == ["babel.js", "react-dom.js", "react.js", "tailwind.js"]
    assert res["text"].startswith("Opened the artifact 'snow' (version 2, react) as the side panel shows it")
    folder = hub.env.workspace_dir / "chats" / str(c["id"])
    assert not any((folder / ".hub" / "web").iterdir())
    n = len(fakes.sandbox["execs"])
    for aid, why in (("nope", "There is no artifact 'nope' in this chat (it has: notes, snow)"),
                     ("notes", "'notes' is a markdown artifact")):
        call(fakes, {"artifact": aid})
        run = await finish(app, await say(signed_in, c["id"], "Igen"))
        assert why in result_of(run, "web_app")["text"]
    hub.env = dataclasses.replace(hub.env, static_dir=None)               # no built web UI: no runtime to give it
    call(fakes, {"artifact": "snow"})
    run = await finish(app, await say(signed_in, c["id"], "Igen"))
    assert "The Hub's artifact runtime is missing" in result_of(run, "web_app")["text"]
    assert len(fakes.sandbox["execs"]) == n
    chats.save_artifact(c["id"], "page", "Page", "html", "<!doctype html><html><body>Hej</body></html>")
    call(fakes, {"artifact": "page"})                                    # a whole page needs no runtime
    run = await finish(app, await say(signed_in, c["id"], "Og den"))
    assert result_of(run, "web_app")["ok"] and seen["index"] == "<!doctype html><html><body>Hej</body></html>"


async def test_asking_first_it_runs_offline(signed_in, app, fakes):
    """Like code: no internet and no question - the page gets its own files, the rest is listed as blocked."""
    asking(app)
    browser(app, fakes)
    c = await new_chat(signed_in)
    game(app.state.hub.env.workspace_dir / "chats" / str(c["id"]))
    fake_pageshot(app, fakes, {**OPENED, "blocked": ["https://cdn.example.com/phaser.js"]})
    chats.save_artifact(c["id"], "page", "Page", "html", "<!doctype html><p>Hej</p>")
    call(fakes, {"target": "game"})
    run = await finish(app, await say(signed_in, c["id"], "Kør"))
    assert not approvals(run) and fakes.sandbox["execs"][-1]["offline"] is True
    assert fakes.sandbox["pageshot"]["online"] is False
    assert "Not loaded, offline: https://cdn.example.com/phaser.js" in result_of(run, "web_app")["text"]
    tools = {t["function"]["name"]: t["function"] for t in fakes.llama["requests"][0]["tools"]}
    assert "anything from the web (a CDN, an API) is blocked" in tools["web_app"]["description"]
    seen = {}
    fake_pageshot(app, fakes, OPENED, look=lambda req, ws: seen.update(
        index=(ws / req["root"].removeprefix("/workspace/") / req["path"]).read_text()))
    call(fakes, {"artifact": "page"})
    await finish(app, await say(signed_in, c["id"], "Og artefaktet"))
    assert fakes.sandbox["pageshot"]["sandbox"] == webapp.FRAME              # no popups while asking, as the panel
    assert seen["index"] == webapp.artifact_doc("html", "<!doctype html><p>Hej</p>", offline=True)
    assert seen["index"].startswith('<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src')
    fakes.sandbox["offline"] = False                                         # a sandbox that cannot keep it off
    app.state.hub.extras.pop("offline_code", None)
    call(fakes, {"target": "game", "steps": [{"click": "Start"}, {"eval": "window.game.score"}]})
    rid = await say(signed_in, c["id"], "Og igen")
    q = await answer(signed_in, app, rid, "allow")
    assert q["tool"] == "web_app" and q["ask"]["unblockable"]
    assert q["ask"]["detail"] == "Open game in a browser\nJavaScript it runs in the page:\nwindow.game.score"
    await finish(app, rid)
    assert "offline" not in fakes.sandbox["execs"][-1] and fakes.sandbox["pageshot"]["online"] is True


async def test_not_in_a_persons_chat(signed_in, app, fakes):
    browser(app, fakes)
    await add_person(signed_in, may_run_code=True)
    fakes.llama["replies"] = [{"content": "Hej."}]
    async with client_from(app) as phone:
        await sign_in(phone)
        await turn(app, phone, {"title": "Spil", "messages": [user_msg("Hej")]})
    assert "bash" in tool_names(fakes) and "web_app" not in tool_names(fakes)
    assert "web_app" not in fakes.llama["requests"][0]["messages"][0]["content"]


def test_the_question_survives_odd_steps():
    from app.agent import internet
    for steps in (5, True, "7", "[1, {\"eval\": \"x\"}]", None):
        ask, online = internet.question("web_app", {"target": "g", "steps": steps}, lambda key: False, False)
        assert ask["detail"].startswith("Open g in a browser") and online is True, steps


def test_steps_and_screen_sizes():
    assert webapp._steps('[{"click": [1, 2]}]') == [{"click": [1, 2]}] and webapp._steps(None) == []
    assert webapp._steps("") == []
    with pytest.raises(ValueError, match="at most 40"):
        webapp._steps([{"wait": 1}] * 41)
    assert webapp._screen("1024 x 600", webapp.SCREEN) == [1024, 600]
    assert webapp._screen("99999x1", webapp.SCREEN) == [1280, 800]                 # not a size: the default
    assert webapp._screen("5000x5000", webapp.SCREEN) == [2560, 1600]
    assert webapp._screen(None, webapp.PANEL) == [760, 820]


# ---------------------------------------------------------------- the artifact page, as the panel builds it

def test_the_artifact_page_is_the_panels_own():
    """artifact_doc is ArtifactPanel.tsx's artifactDoc (buildDoc, and the offline policy while the assistant asks
    first) in Python: the TypeScript, run with node, gives the same pages."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    panel = (REPO / "frontend" / "src" / "components" / "ArtifactPanel.tsx").read_text(encoding="utf-8")
    md = (REPO / "frontend" / "src" / "lib" / "markdown.ts").read_text(encoding="utf-8")
    start, end = panel.find("export function offlinePolicy("), panel.find("export function ArtifactPanel(")
    esc = re.search(r"\nexport function escapeHtml\(s: string\): string \{\n(.*?)\n\}\n", md, re.S)
    assert start > 0 and end > start and esc, "the panel's page builders moved: keep artifact_doc in step"
    code = panel[start:end].replace("export function", "function")
    for typed, plain in (("(origin: string): string", "(origin)"), ("(doc: string, policy: string): string", "(doc, policy)"),
                         ("(type: string, content: string, offline = false): string", "(type, content, offline = false)"),
                         ("(type: string, content: string): string", "(type, content)")):
        assert typed in code, typed
        code = code.replace(typed, plain)
    js = (f"const window = {{ location: {{ origin: {json.dumps(webapp.ORIGIN)} }} }};\n"
          f"function escapeHtml(s) {{\n{esc.group(1).replace(')[ch]!)', ')[ch])')}\n}}\n{code}\n"
          "const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
          "process.stdout.write(JSON.stringify(cases.map(([t, c, o]) => artifactDoc(t, c, o))));\n")
    cases = [["html", "<h1 class='p-4'>Hej & farvel</h1>"], ["html", "<!DOCTYPE html><html><body>x</body></html>"],
             ["html", "<HTML lang=da>y</HTML>"], ["svg", "<svg viewBox='0 0 9 9'><circle r='4'/></svg>"],
             ["mermaid", "graph TD; A-->B & C\n  B-->\"D's\" <b>"],
             ["react", "export default function App() { return <p>\"</script>\" \\ \u2028 æøå \u0001 {'<'}</p>; }"]]
    cases = [[t, c, o] for t, c in cases for o in (False, True)]
    out = subprocess.run([node, "-e", js], input=json.dumps(cases), capture_output=True, text=True, check=True,
                         timeout=60).stdout
    assert [webapp.artifact_doc(t, c, o) for t, c, o in cases] == json.loads(out)


# ---------------------------------------------------------------- pageshot.py: its parts, without a browser

def test_only_the_folder_is_served(tmp_path):
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    (site / "index.html").write_text("<p>hej</p>")
    (site / "data" / "level.json").write_text('{"a": 1}')
    (site / "data" / "index.html").write_text("<p>data</p>")
    (tmp_path / "secret.txt").write_text("no")
    (site / "out.txt").symlink_to(tmp_path / "secret.txt")
    s = pageshot.served
    assert s(str(site), "http://page.local/") == (200, b"<p>hej</p>", "text/html; charset=utf-8")
    assert s(str(site), "http://page.local/data/level.json?v=2")[::2] == (200, "application/json; charset=utf-8")
    assert s(str(site), "http://page.local/data")[1] == b"<p>data</p>"
    assert s(str(site), "http://page.local/nope.js")[0] == 404
    (site / "sub").mkdir()
    (site / "sub" / "index.html").symlink_to(tmp_path / "secret.txt")        # a folder's index.html that leads out
    for bad in ("http://page.local/../secret.txt", "http://page.local/%2e%2e/secret.txt", "http://page.local/out.txt",
                "http://page.local/sub", "http://page.local/sub/"):
        assert s(str(site), bad)[0] == 403, bad
    assert s(str(site), "http://page.local/%00")[0] == 400
    assert pageshot.address("spil #1.html") == "http://page.local/spil%20%231.html"
    assert pageshot.address("50%41.html") == "http://page.local/50%2541.html"


def test_key_names():
    assert pageshot.key_name("ctrl+s") == "Control+s" and pageshot.key_name("Return") == "Enter"
    assert pageshot.key_name("ArrowLeft") == "ArrowLeft" and pageshot.key_name("space") == "Space"
    assert pageshot.key_name("shift+Tab") == "Shift+Tab" and pageshot.key_name("a") == "a"
    assert pageshot.key_name("ctrl++") == "Control++"


def test_a_bad_request_is_an_answer_not_a_traceback(tmp_path, capsys):
    """Checked before the browser is started (so also where Playwright is not installed)."""
    (tmp_path / "index.html").write_text("<p>hej</p>")
    for req, why in (([1], "must be a JSON object"), ({"root": str(tmp_path / "none")}, "no such folder"),
                     ({"root": str(tmp_path), "path": "../x.html"}, "is outside it"),
                     ({"root": str(tmp_path), "path": "nope.html"}, "is not a file in the working folder"),
                     ({"root": str(tmp_path), "steps": {"click": "x"}}, "steps must be a list"),
                     ({"root": str(tmp_path), "screen": "big"}, "screen is [width, height]"),
                     ({"root": str(tmp_path), "sandbox": "allow-scripts; x"}, "sandbox is iframe sandbox flags")):
        assert pageshot.main(["--json", json.dumps(req)]) == 0
        res = json.loads(capsys.readouterr().out)
        assert res["ok"] is False and why in res["error"], (req, res)
    assert pageshot.main(["--json", "{bad"]) == 0 and "bad request" in json.loads(capsys.readouterr().out)["error"]
    assert pageshot.main([]) == 2


# ---------------------------------------------------------------- pageshot.py in a real browser

def _browsers() -> str | None:
    for p in (os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), "/opt/ms-playwright", os.path.expanduser("~/.cache/ms-playwright")):
        if p and os.path.isdir(p):
            return p
    return None


@pytest.fixture(scope="module")
def real_browser():
    """(a Python whose Playwright starts the Chromium installed here, its environment) - this one's or the system's:
    a Playwright release wants its own browser build."""
    where = _browsers()
    if where is None:
        pytest.skip("no Playwright browsers here")
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": where}
    for python in dict.fromkeys(p for p in (sys.executable, shutil.which("python3")) if p):
        check = subprocess.run([python, "-I", str(PAGESHOT), "--check"], env=env, timeout=120, capture_output=True)
        if check.returncode == 0:
            return python, env
    pytest.skip("Playwright's Chromium does not start here")


def shoot(browser_: tuple[str, dict], req: dict) -> dict:
    python, env = browser_
    r = subprocess.run([python, "-I", str(PAGESHOT), "--json", json.dumps(req)], env=env, capture_output=True,
                       text=True, timeout=180)
    return json.loads(r.stdout.strip().splitlines()[-1])


GAME_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Snow</title></head>
<body><h1>Snow</h1><button id="start">Start</button> <span id="x">0</span>
<img src="img/missing.png" alt=""><script src="names.js"></script><script src="game.js"></script></body></html>"""
GAME_JS = """window.game = { x: 0, level: null, repeats: 0 };
fetch("data/level.json").then((r) => r.json()).then((d) => { game.level = d.name; console.log("level " + d.name + " loaded"); });
document.addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight") { game.x += 1; if (e.repeat) game.repeats += 1; document.getElementById("x").textContent = game.x; }
});
document.getElementById("start").addEventListener("click", () => { setTimeout(() => { null.boom(); }, 0); alert("Started"); });
"""


def test_a_game_in_a_real_browser(real_browser, tmp_path):
    site = tmp_path / "game"
    (site / "data").mkdir(parents=True)
    (site / "index.html").write_text(GAME_HTML)
    (site / "game.js").write_text(GAME_JS)
    (site / "names.js").write_text("const NAMES = ['Peter Schmeichel',\n  'Japhet N'Doram'];\n")      # chat 79's data.js
    (site / "data" / "level.json").write_text('{"name": "Vojens"}')
    steps = [{"click": "Start"}, {"hold": "ArrowRight", "seconds": 0.6},
             {"eval": "[game.level, game.x > 3, game.repeats > 0]"}, {"shot": "moved"}]
    res = shoot(real_browser, {"root": str(site), "path": "index.html", "steps": steps, "out": str(tmp_path / "out"),
                               "screen": [800, 600], "timeout": 60})
    assert res["ok"] is True and res["title"] == "Snow" and res["url"] == "index.html", res
    did = [s.get("did") or s.get("error") for s in res["steps"]]
    assert did[0].startswith("clicked 'Start' at (") and did[1] == "held ArrowRight for 0.6 s"
    assert did[2] == 'value: ["Vojens", true, true]'                  # its own file fetched; a key held repeats
    assert [s["label"] for s in res["shots"]] == ["loaded", "moved"] and res["shots"][0]["width"] == 800
    assert sorted(os.listdir(tmp_path / "out")) == ["1.png", "2.png"]
    assert any("null" in e["message"] and "game.js" in e["stack"] for e in res["errors"]), res["errors"]
    assert {"message": "Unexpected identifier 'Doram'", "stack": "at names.js:2:13"} in res["errors"]   # where, too
    assert {"url": "img/missing.png", "status": 404} in res["failed"]
    assert {"type": "log", "text": "level Vojens loaded", "where": "game.js:2"} in res["console"]
    assert res["dialogs"] == [{"type": "alert", "text": "Started"}] and "Snow" in res["text"]


def test_a_page_that_stops_answering_still_gives_what_came_before(real_browser, tmp_path):
    python, env = real_browser
    env = {**env, "PAGESHOT_GRACE": "2"}
    (tmp_path / "index.html").write_text("<!doctype html><title>Loop</title><button>Go</button>"
                                         "<script>console.log('ready')</script>")
    req = {"root": str(tmp_path), "out": str(tmp_path / "out"), "timeout": 5,
           "steps": [{"click": "Go"}, {"eval": "new Promise(() => {})"}, {"click": "Go"}]}
    res = shoot((python, env), req)                                           # an eval that never gives a value
    assert res["ok"] is False and "stopped answering at step 2 ({\"eval\": \"new Promise" in res["error"], res
    assert [s["label"] for s in res["shots"]] == ["loaded"] and res["steps"][0]["did"].startswith("clicked 'Go'")
    assert res["console"][0]["text"] == "ready" and res["title"] == "Loop"
    (tmp_path / "spin.html").write_text("<!doctype html><title>Spin</title><script>while (true) {}</script>")
    res = shoot((python, env), {**req, "path": "spin.html", "steps": []})   # a script that never ends
    assert res["ok"] is False and "stopped answering" in res["error"] and "did not finish loading" in res["error"]


def test_a_long_wait_is_cut_to_the_time_left(real_browser, tmp_path):
    """A quiet page with waits longer than the run's time is not taken for one that stopped answering."""
    python, env = real_browser
    (tmp_path / "index.html").write_text("<!doctype html><title>Quiet</title>")
    req = {"root": str(tmp_path), "out": str(tmp_path / "out"), "timeout": 5,
           "steps": [{"wait": 3}, {"wait": 25}, {"shot": "after"}]}
    res = shoot((python, {**env, "PAGESHOT_GRACE": "3"}), req)
    assert res["ok"] is True and [s["label"] for s in res["shots"]] == ["loaded", "when the time was up"], res
    assert res["steps"][0]["did"] == "waited 3 s" and float(res["steps"][1]["did"].split()[1]) < 3
    assert res["steps"][2] == {"step": 3, "error": "not done: the 5 s were up"}


def test_a_name_with_a_hash_opens(real_browser, tmp_path):
    (tmp_path / "spil #1.html").write_text("<!doctype html><title>Et</title>")
    res = shoot(real_browser, {"root": str(tmp_path), "path": "spil #1.html", "out": str(tmp_path / "out")})
    assert res["ok"] and res["title"] == "Et" and res["url"] == "spil #1.html", res


def test_offline_the_web_is_blocked_and_named(real_browser, tmp_path):
    (tmp_path / "index.html").write_text('<!doctype html><title>CDN</title>'
                                         '<script src="https://cdn.jsdelivr.net/npm/nothing@1/x.js"></script>')
    res = shoot(real_browser, {"root": str(tmp_path), "out": str(tmp_path / "out"), "online": False})
    assert res["ok"] and res["blocked"] == ["https://cdn.jsdelivr.net/npm/nothing@1/x.js"] and res["failed"] == []


def test_a_sandboxed_page_is_like_the_side_panel(real_browser, tmp_path):
    """The panel runs an artifact in <iframe sandbox="allow-scripts ...">: no storage, its own origin - and so does
    web_app, so a game that would fail there for its localStorage fails here too."""
    (tmp_path / "index.html").write_text("<!doctype html><script>try { localStorage.setItem('k', '1'); "
                                         "window.stored = 'yes'; } catch (e) { window.stored = e.name; }</script>")
    req = {"root": str(tmp_path), "out": str(tmp_path / "out"), "steps": [{"eval": "window.stored"}]}
    assert shoot(real_browser, req)["steps"][0]["did"] == 'value: "yes"'
    res = shoot(real_browser, {**req, "sandbox": webapp.FRAME})
    assert res["ok"] and res["steps"][0]["did"] == 'value: "SecurityError"', res


# ---------------------------------------------------------------- the self-test's line

def test_the_selftest_line():
    said = "value: " + json.dumps(cli.WEB_EXPECT, ensure_ascii=False)
    ok = {"ok": True, "seconds": 2.1, "errors": [], "failed": [], "steps": [{"step": 5, "did": said}]}
    status, what, detail, hint = cli.web_verdict(ok, True)
    assert (status, what, hint) == ("PASS", "web pages", "") and "opened offline in Chromium" in detail
    typed = {**ok, "steps": [{"step": 5, "did": 'value: ["Sren", "2", "hello"]'}]}
    assert cli.web_verdict(typed, False)[0] == "FAIL" and "arrived as \"Sren\"" in cli.web_verdict(typed, False)[2]
    unfetched = {**ok, "steps": [{"step": 5, "did": json.dumps(cli.WEB_EXPECT[:2] + [None])}]}
    unfetched["steps"][0]["did"] = "value: " + unfetched["steps"][0]["did"]
    assert "could not fetch() its own data file" in cli.web_verdict(unfetched, False)[2]
    broken = {**ok, "errors": [{"message": "ReferenceError: x is not defined"}]}
    assert cli.web_verdict(broken, False)[:2] == ("FAIL", "web pages")
    missed = {**ok, "steps": [{"step": 3, "error": "'Count' not found"}, {"step": 5, "did": 'value: ["a", "1"]'}]}
    assert cli.web_verdict(missed, False)[0] == "WARN"
    assert cli.web_verdict({"ok": False, "error": "the browser failed: TimeoutError"}, False) == (
        "FAIL", "web pages", "the browser failed: TimeoutError", "docker compose logs sandbox")
