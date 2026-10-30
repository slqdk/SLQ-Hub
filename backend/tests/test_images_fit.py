"""0.22.2 - edits and cut-outs at the original's size. Qwen-Image and FLUX.2 klein work at about one megapixel: both
test runs' 768x512 ball came back at 1248x832 from the cut-out (and the edit), and the second run's model told the
user it was the original's size."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.agent.tools.images import _size_note
from app.images import pipeline as P

from .mocks import _png
from .test_agent import finish, new_chat, say
from .test_audio import chat_dir


def _cutout(size=(1248, 832)) -> bytes:
    """A red ball on nothing, as the background removal gives it."""
    img = Image.new("RGBA", size, (255, 255, 255, 0))
    w, h = size
    ImageDraw.Draw(img).ellipse((w * 0.4, h * 0.3, w * 0.6, h * 0.7), fill=(200, 30, 30, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def test_a_cut_out_is_scaled_to_the_original_and_keeps_its_transparency():
    (name, data), = P._fit([("hub-cutout_00001_.png", _cutout())], _png((768, 512)))[0]
    img = _open(data)
    assert name == "hub-cutout_00001_.png" and img.size == (768, 512) and img.mode == "RGBA"
    assert img.getpixel((2, 2))[3] == 0 and img.getpixel((384, 256)) == (200, 30, 30, 255)
    # the edge is red fading out, not darkened by the transparent white around it
    edge = [img.getpixel((x, 256)) for x in range(300, 330) if 0 < img.getpixel((x, 256))[3] < 255]
    assert edge and all(px[0] >= 190 and px[1] <= 40 for px in edge), edge


def test_an_edit_keeps_the_original_size_and_a_big_original_keeps_the_models():
    buf = io.BytesIO()
    Image.new("RGB", (1248, 832), (30, 30, 200)).save(buf, "PNG")              # SaveImage writes RGB
    edited = buf.getvalue()
    out, source = P._fit([("e.png", edited)], _png((768, 512), (200, 30, 30, 255)))
    assert source == (768, 512) and _open(out[0][1]).size == (768, 512) and _open(out[0][1]).mode == "RGB"
    out, source = P._fit([("e.png", edited)], _png((3000, 2000)))           # 6 MP: not blown up from 1 MP
    assert source == (3000, 2000) and out[0][1] == edited
    out, _ = P._fit([("e.png", edited)], _png((1248, 832)))                  # the same size: untouched
    assert out[0][1] == edited
    assert _size_note({"width": 768, "height": 512, "source": (768, 512)}) == "768x512 px, the original's size"
    assert _size_note({"width": 1248, "height": 832, "source": (3000, 2000)}) == \
        "1248x832 px - the original is 3000x2000; pictures that big stay at the model's size"


async def test_the_tools_give_the_original_size_and_say_so(signed_in, app, fakes):
    fakes.comfy["picture_bytes"] = _cutout()
    items = await app.state.hub.extras["images"].remove_background(_png((768, 512), (200, 30, 30, 255)))
    assert (items[0]["width"], items[0]["height"], items[0]["source"]) == (768, 512, (768, 512))
    c = await new_chat(signed_in)
    folder = chat_dir(app, c["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ball.png").write_bytes(_png((768, 512), (200, 30, 30, 255)))
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_remove_background", "arguments": {"path": "ball.png"}}]},
        {"tool_calls": [{"name": "image_edit", "arguments": {"prompt": "make the ball blue", "image": "ball.png"}}]},
        {"content": "Done."}]
    run = await finish(app, await say(signed_in, c["id"], "Remove the background, then make the ball blue"))
    assert run.status == "done", run.events[-1]
    cut, edit = [e["data"] for e in run.events if e["type"] == "tool_result"]
    assert cut["ok"] and "(PNG with transparency, 768x512 px, the original's size)" in cut["text"], cut["text"]
    assert edit["ok"] and "ready with Qwen-Image 2.1 (1, 768x512 px, the original's size)" in edit["text"], edit["text"]
    assert _open((folder / "ball-cutout.png").read_bytes()).size == (768, 512)
