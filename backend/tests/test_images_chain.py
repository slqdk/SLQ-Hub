"""0.32.1 - a picture made or edited in a plain chat is a file, and the next change starts from it. Søren: after an
edit, "change it" again without attaching the picture edited his original once more. Outside a project an edit was
shown but not saved, and its result named no file, so the only picture the model could name was the attachment."""
from __future__ import annotations

import io
import re

from PIL import Image

from .mocks import _png
from .test_agent import finish, new_chat, say
from .test_audio import chat_dir

RED, BLUE, GREEN = (200, 30, 30, 255), (30, 30, 200, 255), (30, 200, 30, 255)
SAVED = re.compile(r"Saved as (images/\S+\.png) - to change a picture further, edit that file\.")


def colour(data: bytes) -> tuple:
    with Image.open(io.BytesIO(data)) as img:
        return img.convert("RGBA").getpixel((5, 5))


def tool_text(run) -> str:
    return next(e["data"]["text"] for e in run.events if e["type"] == "tool_result")


async def test_the_next_change_starts_from_the_edited_picture(signed_in, app, fakes, tmp_path):
    fakes.comfy["files"] = tmp_path / "comfy"                  # the fake keeps what it was given
    fakes.comfy["picture_bytes"] = _png((20, 10), BLUE)        # what the first edit gives
    up = (await signed_in.post("/hub/api/uploads", files={"file": ("photo.png", _png((20, 10), RED),
                                                                    "image/png")})).json()
    c = await new_chat(signed_in)                              # a plain chat, not a project
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_edit", "arguments": {"prompt": "make it blue",
                                                             "images": [".hub/attachments/photo.png"]}}]},
        {"content": "Blue now."}, {"content": "Photo"}]
    run = await finish(app, await say(signed_in, c["id"], "Make it blue", attachments=[up["sha"]]))
    assert run.status == "done", run.events[-1]
    m = SAVED.search(tool_text(run))
    assert m, tool_text(run)
    first = m.group(1)
    folder = chat_dir(app, c["id"])
    assert colour((folder / first).read_bytes()) == BLUE
    card = [f["path"] for e in run.events if e["type"] == "files" for f in e["data"]["files"]]
    assert first in card                                       # under the answer: open, download, add to chat
    edit = next(t for t in fakes.llama["requests"][0]["tools"] if t["function"]["name"] == "image_edit")
    assert "edit its newest file (the one its result named), not the original" in edit["function"]["description"]
    assert "put first the picture whose scene and framing should stay" in edit["function"]["description"]

    # the next turn: the history names the edited file, and changing it again starts from it
    fakes.llama["requests"].clear()
    fakes.comfy["picture_bytes"] = _png((20, 10), GREEN)
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_edit", "arguments": {"prompt": "make it darker", "images": [first]}}]},
        {"content": "Darker."}]
    before = set((tmp_path / "comfy" / "input").iterdir())
    run = await finish(app, await say(signed_in, c["id"], "Now make it darker"))
    assert run.status == "done", run.events[-1]
    history = fakes.llama["requests"][0]["messages"]
    assert any(m["role"] == "tool" and first in m["content"] for m in history)
    sent = set((tmp_path / "comfy" / "input").iterdir()) - before
    assert len(sent) == 1 and colour(next(iter(sent)).read_bytes()) == BLUE   # the edit went in, not the red original
    second = SAVED.search(tool_text(run)).group(1)
    assert second != first and colour((folder / second).read_bytes()) == GREEN
    assert colour((folder / first).read_bytes()) == BLUE      # the earlier version stays


async def test_a_picture_made_in_a_plain_chat_is_a_file(signed_in, app, fakes):
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a red fox in snow", "fast": True}}]},
        {"content": "Here is your fox."}, {"content": "Fox"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Draw a fox"))
    m = SAVED.search(tool_text(run))
    assert m and m.group(1).endswith("-a-red-fox-in-snow.png"), tool_text(run)
    assert (chat_dir(app, c["id"]) / m.group(1)).read_bytes().startswith(b"\x89PNG")
