"""Draws a movie's speech bubbles, captions and end card (SLQ Hub's Movies, 0.73.0). The Hub copies this file into
the cut's folder and runs it in the sandbox, where the DejaVu fonts are - the Hub's own image has no font with æ, ø
and å:

    python3 render_overlays.py overlays.json

Each item in the list is one PNG the size of the video ("out"): a speech bubble, a narration box or a caption on a
transparent picture, or the end card - the episode's title on its last keyframe, blurred and darkened, or on a plain
colour. The Hub never imports this file; ffmpeg lays the pictures over the film while each line is heard."""
from __future__ import annotations

import json
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = "/usr/share/fonts/truetype/dejavu/"
INK = (25, 25, 25, 255)
PAPER = (255, 255, 255, 250)
NOTE = (255, 232, 140, 250)              # the narrator's box
NAME = (35, 85, 170, 255)


def font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(FONTS + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), size)
    except OSError:
        return ImageFont.load_default(size)


def wrap(draw, text: str, fnt, width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if line and draw.textlength(test, font=fnt) > width:
            lines.append(line)
            line = word
        else:
            line = test
    return lines + ([line] if line else [])


def fitted(draw, text: str, size: int, width: float, most: int, bold: bool = False) -> tuple:
    """The text's lines in the largest size up to `size` that fits in `most` lines (at least 60 % of it)."""
    smallest = max(10, int(size * 0.6))
    for s in range(size, smallest - 1, -1):
        f = font(s, bold)
        lines = wrap(draw, text, f, width)
        if len(lines) <= most or s == smallest:
            return f, s, lines or [""]
    return font(size, bold), size, [text]


def bubble(it: dict, narration: bool = False) -> Image.Image:
    w, h = it["width"], it["height"]
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    base = max(16, round(min(w, h) * 0.05))
    pad = round(base * 0.6)
    f, size, lines = fitted(d, it["text"], base, w * 0.8 - 2 * pad, 4)
    name = "" if narration else (it.get("name") or "")
    nf = font(max(11, round(size * 0.68)), True)
    lh, nh = round(size * 1.24), (round(nf.size * 1.4) if name else 0)
    tw = max([d.textlength(x, font=f) for x in lines] + [d.textlength(name, font=nf) if name else 0])
    bw, bh = round(tw + 2 * pad), round(nh + lh * len(lines) + 2 * pad - size * 0.24)
    x0, y0 = (w - bw) // 2, round(h * 0.05)
    stroke = max(2, round(size / 11))
    if narration:
        d.rectangle([x0, y0, x0 + bw, y0 + bh], fill=NOTE, outline=INK, width=stroke)
    else:
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=round(size * 0.9), fill=PAPER, outline=INK,
                            width=stroke)
        # the tail: down from the bubble's lower edge, a third of the way in
        tx, ty = x0 + bw * 0.34, y0 + bh
        tail = [(tx - size * 0.42, ty - stroke), (tx + size * 0.38, ty - stroke), (tx - size * 0.62, ty + size * 1.15)]
        d.polygon(tail, fill=PAPER, outline=INK, width=stroke)
        d.line([(tail[0][0] + stroke, ty - stroke * 0.5), (tail[1][0] - stroke * 1.5, ty - stroke * 0.5)], fill=PAPER,
               width=stroke + 2)
    y = y0 + pad
    if name:
        d.text((w / 2, y), name, font=nf, fill=NAME, anchor="ma")
        y += nh
    for line in lines:
        d.text((w / 2, y), line, font=f, fill=INK, anchor="ma")
        y += lh
    return img


def caption(it: dict) -> Image.Image:
    w, h = it["width"], it["height"]
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    base = max(14, round(min(w, h) * 0.042))
    pad = round(base * 0.5)
    text = f"{it['name']}: {it['text']}" if it.get("name") else it["text"]
    f, size, lines = fitted(d, text, base, w * 0.9 - 2 * pad, 3)
    lh = round(size * 1.24)
    tw = max(d.textlength(x, font=f) for x in lines)
    bw, bh = round(tw + 2 * pad), round(lh * len(lines) + 2 * pad - size * 0.24)
    x0, y1 = (w - bw) // 2, h - round(h * 0.07)
    d.rounded_rectangle([x0, y1 - bh, x0 + bw, y1], radius=round(size * 0.35), fill=(0, 0, 0, 170))
    y = y1 - bh + pad
    for line in lines:
        d.text((w / 2, y), line, font=f, fill=(255, 255, 255, 255), anchor="ma")
        y += lh
    return img


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def backdrop(path: str | None, w: int, h: int) -> Image.Image:
    """The last keyframe, blurred and darkened - or, without one, a dark blue from top to bottom."""
    if path:
        try:
            with Image.open(path) as bg:
                img = cover(bg.convert("RGB"), w, h).filter(ImageFilter.GaussianBlur(max(4, w // 45)))
            return Image.blend(img, Image.new("RGB", (w, h), (10, 12, 24)), 0.5)
        except (OSError, ValueError):
            pass
    img = Image.new("RGB", (w, h))
    top, bottom = (52, 72, 128), (16, 20, 44)
    for y in range(h):
        k = y / max(1, h - 1)
        img.paste(tuple(round(a + (b - a) * k) for a, b in zip(top, bottom)), (0, y, w, y + 1))
    return img


def end_card(it: dict) -> Image.Image:
    w, h = it["width"], it["height"]
    img = backdrop(it.get("background"), w, h)
    d = ImageDraw.Draw(img)
    base = max(20, round(min(w, h) * 0.085))
    f, size, lines = fitted(d, it.get("title") or "", base, w * 0.84, 3, bold=True)
    sf = font(max(12, round(min(w, h) * 0.042)))
    lh = round(size * 1.18)
    block = lh * len(lines)
    y = (h - block) // 2
    shadow = max(2, size // 18)
    if it.get("series"):
        d.text((w / 2, y - round(sf.size * 1.9)), it["series"], font=sf, fill=(225, 228, 240), anchor="ma")
    for line in lines:
        d.text((w / 2 + shadow, y + shadow), line, font=f, fill=(0, 0, 0), anchor="ma")
        d.text((w / 2, y), line, font=f, fill=(255, 255, 255), anchor="ma")
        y += lh
    return img


def main(path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
    for it in items:
        kind = it["kind"]
        img = (end_card(it) if kind == "end" else caption(it) if kind == "caption"
               else bubble(it, narration=kind == "narration"))
        img.save(it["out"])


if __name__ == "__main__":
    main(sys.argv[1])
