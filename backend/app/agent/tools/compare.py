"""A page's screenshot against the picture it should look like (0.35.0, web_app's compare). Søren gave his assistant
three screenshots of Championship Manager 98 to copy (chat 135); it made a "reference" with image_generate, sampled
one pixel per bash call and never set its page beside the original. This gives it what a person sees side by side:
how much matches, where it differs most and the colours on each side, and a picture of the two with the
differences marked - of the whole picture, or of one part of it (compare_area: the title bar, or everything but a
photo the page does not have)."""
from __future__ import annotations

import io
import math
import re

from PIL import Image, ImageChops, ImageOps

SAME = 24             # a channel this close counts as the same colour (screenshots are often compressed)
CELLS = 48            # about this many cells for "where it differs", as square as the area allows
WORST = 6             # cells named
SHOW_W, SHOW_H = 640, 480   # each of the three panels in the picture, at most this big
ZOOM = 4              # a small area is shown up to this many times larger, pixel for pixel


def hexc(c) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in c[:3])


def _median(img: Image.Image) -> tuple[int, int, int]:
    """The colour most of a cell has, roughly: each channel's median (text over a face leaves the face)."""
    hist = img.histogram()
    n = img.width * img.height
    out = []
    for ch in range(3):
        h, seen = hist[ch * 256:(ch + 1) * 256], 0
        for v, count in enumerate(h):
            seen += count
            if seen * 2 >= n:
                out.append(v)
                break
    return tuple(out)


def area_box(raw, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """compare_area - "x,y,width,height" in the picture's pixels (a list of four numbers will do) - as a box in it,
    clipped to the picture. None when not given. Raises ValueError."""
    if raw is None or not str(raw).strip() or raw == []:
        return None
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(raw))
    if len(nums) != 4:
        raise ValueError(f"compare_area {raw!r}: give x,y,width,height in the picture's pixels, e.g. \"0,0,640,60\" "
                         "for a strip across the top")
    x, y, w, h = (int(float(n)) for n in nums)
    box = (max(0, x), max(0, y), min(size[0], x + w), min(size[1], y + h))
    if box[2] - box[0] < 2 or box[3] - box[1] < 2:
        raise ValueError(f"compare_area {raw!r} is not inside the {size[0]}x{size[1]} picture (x,y,width,height)")
    return box


def _grid(w: int, h: int) -> tuple[int, int]:
    """Cells across and down: about CELLS of them, about square (8x6 for 640x480, 16x3 for a 640x60 strip)."""
    cols = max(1, min(16, round(math.sqrt(CELLS * w / h))))
    rows = max(1, min(12, round(CELLS / cols)))
    return min(cols, w), min(rows, h)


def compare(page: bytes, original: bytes, area: tuple[int, int, int, int] | None = None) -> tuple[str, bytes, dict]:
    """(the report, the picture: original | page | differences, the figures). The page is scaled to the original's
    size when they differ - the report says so. With an area (a box from area_box) only that part of both counts;
    places in the report are still the original's pixels."""
    ref = ImageOps.exif_transpose(Image.open(io.BytesIO(original))).convert("RGB")
    got = Image.open(io.BytesIO(page)).convert("RGB")
    lines = []
    if got.size != ref.size:
        lines.append(f"The page's screenshot is {got.width}x{got.height} and the original {ref.width}x{ref.height}: "
                     f"compared at the original's size. Open the page at it (screen: \"{ref.width}x{ref.height}\") "
                     "to compare pixel for pixel.")
        got = got.resize(ref.size, Image.LANCZOS)
    full = (ref.width, ref.height)
    ox, oy = 0, 0
    if area is not None:
        area = (max(0, area[0]), max(0, area[1]), min(full[0], area[2]), min(full[1], area[3]))
        area = area if area[2] > area[0] and area[3] > area[1] else None
    if area is not None:
        ox, oy = area[0], area[1]
        ref, got = ref.crop(area), got.crop(area)
    diff = ImageChops.difference(ref, got)
    r, g, b = diff.split()
    worst = ImageChops.lighter(ImageChops.lighter(r, g), b)          # each pixel's largest channel difference
    hist = worst.histogram()
    total = ref.width * ref.height
    same = sum(hist[:SAME + 1]) / total
    mean = sum(v * n for v, n in enumerate(hist)) / total
    figures = {"same": round(same * 100, 1), "mean_difference": round(mean, 1)}
    where = (f", x {ox}-{ox + ref.width}, y {oy}-{oy + ref.height} of it" if area is not None else "")
    lines.insert(0, f"Compared with the original ({full[0]}x{full[1]}){where}: {figures['same']}% of the pixels match "
                    f"(within {SAME} per colour channel), the mean difference is {figures['mean_difference']} of "
                    "255.")
    cols, rows = _grid(ref.width, ref.height)
    cells = []
    for j in range(rows):
        for i in range(cols):
            box = (ref.width * i // cols, ref.height * j // rows, ref.width * (i + 1) // cols,
                   ref.height * (j + 1) // rows)
            h = worst.crop(box).histogram()
            n = (box[2] - box[0]) * (box[3] - box[1]) or 1
            cells.append((sum(h[:SAME + 1]) / n, box))
    cells.sort(key=lambda c: c[0])
    named = [c for c in cells[:WORST] if c[0] < 0.97]
    if named:
        lines.append("Where it differs most (x and y in the original's pixels; the colour most of each area has):")
        for share, box in named:
            lines.append(f"- x {box[0] + ox}-{box[2] + ox}, y {box[1] + oy}-{box[3] + oy}: {round(share * 100)}% "
                         f"match - original {hexc(_median(ref.crop(box)))}, page {hexc(_median(got.crop(box)))}")
    else:
        lines.append("No area differs much.")
    lines.append("The picture shows the original, the page and the differences (red: where they differ)"
                 + (" - that part of them." if area is not None else "."))
    figures["cells"] = [{"box": [box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy], "same": round(share * 100, 1)}
                        for share, box in named]
    return "\n".join(lines), _picture(ref, got, worst), figures


def _picture(ref: Image.Image, got: Image.Image, worst: Image.Image) -> bytes:
    """Original | page | the page in grey with the differences in red. Each at most SHOW_W x SHOW_H; a small area
    larger, pixel for pixel; a wide strip's three one above the other."""
    w, h = ref.size
    k = min(SHOW_W / w, SHOW_H / h)
    k = max(1, min(ZOOM, int(k))) if k >= 1 else k
    size = (max(1, round(w * k)), max(1, round(h * k)))
    how = Image.NEAREST if k >= 1 else Image.LANCZOS
    a, b = ref.resize(size, how), got.resize(size, how)
    mask = worst.resize(size, Image.NEAREST if k >= 1 else Image.BILINEAR)\
        .point(lambda v: 0 if v <= SAME else min(255, 128 + (v - SAME) * 4))     # red only where it counts
    grey = ImageOps.grayscale(b).point(lambda v: v // 2 + 40).convert("RGB")
    marked = Image.composite(Image.new("RGB", size, (230, 30, 30)), grey, mask)
    gap = 8
    stacked = w > 2 * h
    out = Image.new("RGB", (size[0], size[1] * 3 + gap * 2) if stacked else (size[0] * 3 + gap * 2, size[1]),
                    (255, 255, 255))
    for n, part in enumerate((a, b, marked)):
        out.paste(part, (0, n * (size[1] + gap)) if stacked else (n * (size[0] + gap), 0))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()
