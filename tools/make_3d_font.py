"""The 3D editor's text fonts (0.87.0, more of them 0.89.0 and 0.99.0): each font's outlines as flat polygons in JSON.

Text in a design (`"shape": "text"`) must come out the same in the browser (frontend/src/model3d/geometry.ts) and
in the sandbox (hub3d design), so both read these polygons instead of font files: no curve maths and no font
library on either side - the curves are flattened here, once (previews.json: each family's name in its own letters,
for the editor's Font box - one small file, not every font). Run by hand when a font or its glyphs change:

    python3 -m venv /tmp/fv && /tmp/fv/bin/pip install fonttools
    mkdir -p /tmp/fonts && (cd /tmp/fonts && npm install @fontsource/bitter@5.3.0 @fontsource/nunito@5.3.0 \\
        @fontsource/pacifico@5.3.0 @fontsource/saira-stencil-one@5.3.0 @fontsource/oswald@5.3.0 \\
        @fontsource/montserrat@5.3.0 @fontsource/cinzel@5.3.0 @fontsource/caveat@5.3.0 @fontsource/knewave@5.3.0 \\
        @fontsource/bangers@5.3.0 @fontsource/holtwood-one-sc@5.3.0 @fontsource/grenze-gotisch@5.3.0 \\
        @fontsource/oxanium@5.3.0 @fontsource/pixelify-sans@5.3.0)
    /tmp/fv/bin/python tools/make_3d_font.py /tmp/fonts/node_modules/@fontsource

A font here must be under the SIL Open Font License 1.1 without a Reserved Font Name - the polygons are a Modified
Version of it, which may not carry such a name (OFL condition 3): Permanent Marker (Apache), Rye, UnifrakturMaguntia,
Audiowide and Press Start 2P (reserved names) were left out for that - and must have æ, ø and å (Orbitron has no ø).
The script stops if not.

Barlow and JetBrains Mono come from the frontend's own node_modules (@fontsource/barlow, @fontsource/jetbrains-mono);
the others from the folder given. All are under the SIL Open Font License 1.1. It writes fonts/ (index.json, one
file per font, OFL.txt) into frontend/src/model3d/ and services/sandbox/ - the same bytes; a test keeps them so.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from fontTools.pens.basePen import decomposeQuadraticSegment, decomposeSuperBezierSegment
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

REPO = Path(__file__).resolve().parents[1]
OWN = REPO / "frontend/node_modules/@fontsource"
TARGETS = (REPO / "frontend/src/model3d/fonts", REPO / "services/sandbox/fonts")
QUAD_STEPS, CUBIC_STEPS = 6, 8                       # straight pieces per curve: smooth at text sizes that print
EXTRA = "€–—‘’‚“”„•…‰‹›™"
WEIGHTS = {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}

# The families a design's "font" names, in the editor's order: (font, its @fontsource package, {style: (weight,
# italic)}). Weights that print well - no hairlines; "bold" is clearly heavier. A family without an italic is
# slanted by the builds instead (geometry.ts SLANT).
# 0.99.0, Søren: "Kan vi få nogle flere skrifttyper?" - ten more, sans to serif to hand to display, mono last.
FAMILIES = {
    "sans": ("Barlow", "barlow", {"regular": (600, False), "bold": (800, False), "italic": (600, True),
                                  "bold-italic": (800, True)}),
    "condensed": ("Oswald", "oswald", {"regular": (500, False), "bold": (700, False)}),
    "geometric": ("Montserrat", "montserrat", {"regular": (600, False), "bold": (800, False), "italic": (600, True),
                                               "bold-italic": (800, True)}),
    "rounded": ("Nunito", "nunito", {"regular": (700, False), "bold": (900, False), "italic": (700, True),
                                     "bold-italic": (900, True)}),
    "serif": ("Bitter", "bitter", {"regular": (600, False), "bold": (800, False), "italic": (600, True),
                                   "bold-italic": (800, True)}),
    "classic": ("Cinzel", "cinzel", {"regular": (600, False), "bold": (900, False)}),
    "script": ("Pacifico", "pacifico", {"regular": (400, False)}),
    "handwriting": ("Caveat", "caveat", {"regular": (500, False), "bold": (700, False)}),
    "marker": ("Knewave", "knewave", {"regular": (400, False)}),
    "stencil": ("Saira Stencil One", "saira-stencil-one", {"regular": (400, False)}),
    "comic": ("Bangers", "bangers", {"regular": (400, False)}),
    "western": ("Holtwood One SC", "holtwood-one-sc", {"regular": (400, False)}),
    "blackletter": ("Grenze Gotisch", "grenze-gotisch", {"regular": (600, False), "bold": (800, False)}),
    "futuristic": ("Oxanium", "oxanium", {"regular": (600, False), "bold": (800, False)}),
    "pixel": ("Pixelify Sans", "pixelify-sans", {"regular": (500, False), "bold": (700, False)}),
    "mono": ("JetBrains Mono", "jetbrains-mono", {"regular": (700, False), "bold": (800, False),
                                                  "italic": (700, True), "bold-italic": (800, True)}),
}
DANISH = "æøåÆØÅ"


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


def contours(glyphset, name: str, k: float = 1.0) -> list[list[int]]:
    """A glyph's outlines, flattened: [x, y, x, y, ...] per contour, in units of `k` of the font's own."""
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
                out.append([v for x, y in cur for v in (round(x * k), round(y * k))])
            cur, start = [], None
    return out


def licence(package: str, extra: Path | None) -> None:
    """Stops unless the font is OFL 1.1 without a Reserved Font Name (see the docstring)."""
    for root in ([extra] if extra else []) + [OWN]:
        d = root / package
        if (d / "package.json").exists():
            lic = json.loads((d / "package.json").read_text(encoding="utf-8")).get("license")
            text = (d / "LICENSE").read_text(encoding="utf-8", errors="replace") if (d / "LICENSE").exists() else ""
            reserved = "Reserved Font Name" in text.split("This Font Software")[0]
            if lic != "OFL-1.1" or reserved:
                sys.exit(f"@fontsource/{package}: {lic}{' with a Reserved Font Name' if reserved else ''} - only OFL 1.1 "
                         "fonts without one can be made into polygons here (see this file's docstring)")
            return
    sys.exit(f"no @fontsource/{package}: install it (see this file's docstring)")


def source(package: str, weight: int, italic: bool, extra: Path | None) -> Path:
    name = f"{package}-latin-{weight}-{'italic' if italic else 'normal'}.woff"
    for root in ([extra] if extra else []) + [OWN]:
        p = root / package / "files" / name
        if p.exists():
            return p
    sys.exit(f"no {name}: install @fontsource/{package} (see this file's docstring)")


def flatten(path: Path, family: str, style: tuple[int, bool]) -> tuple[dict, str]:
    font = TTFont(str(path))
    cmap, glyphset = font.getBestCmap(), font.getGlyphSet()
    chars = [chr(c) for c in range(0x20, 0x7F)] + [chr(c) for c in range(0xA0, 0x100)] + list(EXTRA)
    k = 1000 / font["head"].unitsPerEm               # every font in thousandths of its em (0.99.0: some have 2048)
    glyphs = {}
    for ch in chars:
        name = cmap.get(ord(ch))
        if name is None:
            continue
        glyphs[ch] = {"a": round(font["hmtx"][name][0] * k), "c": contours(glyphset, name, k)}
    if any(ch not in glyphs for ch in DANISH):
        sys.exit(f"{path.name} has no {''.join(ch for ch in DANISH if ch not in glyphs)}: a name would print with a ?")
    hhea, os2 = font["hhea"], font["OS/2"]
    # How tall its capitals are - the text's "Capitals" in the editor: the font's own word for it, unless its
    # flat-topped capitals say otherwise by more than 5 % (0.99.0: Pixelify Sans says 700 of 1000 for 633, Caveat 610
    # for 661; Rye, left out, said 177).
    cap = round(getattr(os2, "sCapHeight", 0) * k)
    tops, bottoms = [], []
    for ch in "EFHIKLTZ":
        ys = [v for c in glyphs.get(ch, {}).get("c", []) for v in c[1::2]]
        if ys:
            tops.append(max(ys))
            bottoms.append(min(ys))
    ink = statistics.median(tops) - max(0, statistics.median(bottoms))
    if not cap or abs(cap - ink) > 0.05 * ink:
        cap = round(ink)
    # its notice on one line, without the licence's own words some fonts put after it (Rye, UnifrakturMaguntia)
    copyright_ = " ".join((font["name"].getDebugName(0) or "").split("This Font Software")[0].split()).rstrip(".")
    weight, italic = style
    data = {
        "name": f"{family} {WEIGHTS[weight]}{' Italic' if italic else ''}",
        "license": f"SIL Open Font License 1.1 - {copyright_}. Outlines flattened to polygons by "
                   "tools/make_3d_font.py.",
        "unitsPerEm": 1000, "ascender": round(hhea.ascent * k), "descender": round(hhea.descent * k),
        "capHeight": cap, "glyphs": glyphs,
    }
    return data, copyright_


def main() -> None:
    extra = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    files: dict[str, str] = {}
    index: dict = {"default": "sans", "families": {}}
    previews: dict = {}
    notices: list[str] = []
    for fid, (family, package, styles) in FAMILIES.items():
        licence(package, extra)
        entry: dict = {"name": family}
        for style, (weight, italic) in styles.items():
            stem = f"{package}-{weight}{'-italic' if italic else ''}"
            data, notice = flatten(source(package, weight, italic, extra), family, (weight, italic))
            files[f"{stem}.json"] = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
            entry[style] = stem
            if notice and notice not in notices:
                notices.append(notice)
            if style == "regular":                   # its name in its own letters: the Font box's list (0.99.0)
                previews[fid] = {**{k: data[k] for k in ("unitsPerEm", "ascender", "descender", "capHeight")},
                                 "glyphs": {ch: g for ch, g in data["glyphs"].items() if ch in family}}
        index["families"][fid] = entry
    files["index.json"] = json.dumps(index, ensure_ascii=False, indent=1) + "\n"
    files["previews.json"] = json.dumps(previews, ensure_ascii=False, separators=(",", ":")) + "\n"
    lic = (OWN / "barlow/LICENSE").read_text(encoding="utf-8")
    body = lic[lic.index("-----"):]
    files["OFL.txt"] = ("The fonts here are polygons made by tools/make_3d_font.py from these fonts:\n\n"
                        + "\n".join(f"  {n}" for n in notices)
                        + "\n\nThis Font Software is licensed under the SIL Open Font License, Version 1.1.\n"
                          "This license is copied below, and is also available with a FAQ at:\n"
                          "http://scripts.sil.org/OFL\n\n\n" + body)
    for t in TARGETS:
        t.mkdir(parents=True, exist_ok=True)
        for old in t.glob("*.json"):
            if old.name not in files:
                old.unlink()
        for name, text in files.items():
            (t / name).write_text(text, encoding="utf-8")
        total = sum(len(v.encode()) for v in files.values())
        print(f"wrote {t.relative_to(REPO)}: {len(files)} files, {total:,} bytes")


if __name__ == "__main__":
    main()
