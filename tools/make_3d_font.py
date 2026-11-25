"""The 3D editor's text font (0.87.0): Barlow SemiBold's outlines as flat polygons in JSON.

Text in a design (`"shape": "text"`) must come out the same in the browser (frontend/src/model3d/geometry.ts) and
in the sandbox (hub3d design), so both read these polygons instead of a font file: no curve maths and no font
library on either side - the curves are flattened here, once. Run by hand when the font or its glyphs change:

    python3 -m venv /tmp/fv && /tmp/fv/bin/pip install fonttools
    /tmp/fv/bin/python tools/make_3d_font.py

It reads the Barlow the frontend already has (@fontsource/barlow, SIL Open Font License 1.1) and writes
frontend/src/model3d/font-barlow-600.json and services/sandbox/hub3d-font.json - the same bytes; a test keeps them so.
"""
from __future__ import annotations

import json
from pathlib import Path

from fontTools.pens.basePen import decomposeQuadraticSegment, decomposeSuperBezierSegment
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "frontend/node_modules/@fontsource/barlow/files/barlow-latin-600-normal.woff"
TARGETS = (REPO / "frontend/src/model3d/font-barlow-600.json", REPO / "services/sandbox/hub3d-font.json")
QUAD_STEPS, CUBIC_STEPS = 6, 8                       # straight pieces per curve: smooth at text sizes that print
EXTRA = "€–—‘’‚“”„•…‰‹›™"


def _quad(p0, p1, p2, steps):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
            for t in (i / steps for i in range(1, steps + 1))]


def _cubic(p0, p1, p2, p3, steps):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def contours(glyphset, name: str) -> list[list[int]]:
    pen = DecomposingRecordingPen(glyphset)
    glyphset[name].draw(pen)
    out, cur, start = [], [], None
    for op, args in pen.value:
        if op == "moveTo":
            cur, start = [args[0]], args[0]
        elif op == "lineTo":
            cur.append(args[0])
        elif op == "qCurveTo":
            if args[-1] is None:                       # a contour of off-curve points only
                continue
            for off, on in decomposeQuadraticSegment(args):
                cur += _quad(cur[-1], off, on, QUAD_STEPS)
        elif op == "curveTo":
            for c1, c2, on in decomposeSuperBezierSegment(args):
                cur += _cubic(cur[-1], c1, c2, on, CUBIC_STEPS)
        elif op in ("closePath", "endPath"):
            if cur and start is not None and cur[-1] == start and len(cur) > 1:
                cur = cur[:-1]
            if len(cur) >= 3:
                out.append([v for x, y in cur for v in (round(x), round(y))])
            cur, start = [], None
    return out


def main() -> None:
    font = TTFont(str(SOURCE))
    cmap, glyphset = font.getBestCmap(), font.getGlyphSet()
    chars = [chr(c) for c in range(0x20, 0x7F)] + [chr(c) for c in range(0xA0, 0x100)] + list(EXTRA)
    glyphs = {}
    for ch in chars:
        name = cmap.get(ord(ch))
        if name is None:
            continue
        glyphs[ch] = {"a": round(font["hmtx"][name][0]), "c": contours(glyphset, name)}
    hhea = font["hhea"]
    data = {
        "name": "Barlow SemiBold",
        "license": "SIL Open Font License 1.1 - Copyright 2017 The Barlow Project Authors "
                   "(https://github.com/jpt/barlow). Outlines flattened to polygons by tools/make_3d_font.py.",
        "unitsPerEm": font["head"].unitsPerEm, "ascender": hhea.ascent, "descender": hhea.descent,
        "glyphs": glyphs,
    }
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    for t in TARGETS:
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(text, encoding="utf-8")
        print(f"wrote {t.relative_to(REPO)}: {len(glyphs)} glyphs, {len(text):,} bytes")


if __name__ == "__main__":
    main()
