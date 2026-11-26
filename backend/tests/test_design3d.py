"""Designs from the 3D editor (0.87.0): the browser (frontend/src/model3d/geometry.ts, run here with node) and the
sandbox (services/sandbox/hub3d.py's design) read a .3d.json the same way and build the same solid from it - what
the assistant checks is what the editor shows and exports. The geometry needs manifold3d (Python) and manifold-3d
(frontend/node_modules); where either is missing those tests are skipped."""
from __future__ import annotations

import importlib.util
import json
import math
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "services" / "sandbox" / "hub3d.py"
GEOMETRY = ROOT / "frontend" / "src" / "model3d" / "geometry.ts"
FILES = ROOT / "frontend" / "src" / "model3d" / "files.ts"
MANIFOLD_JS = ROOT / "frontend" / "node_modules" / "manifold-3d" / "manifold.js"
FONTS = ROOT / "frontend" / "src" / "model3d" / "fonts"


@pytest.fixture(scope="module")
def h3():
    spec = importlib.util.spec_from_file_location("hub3d", SOURCE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hub3d"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("hub3d", None)


def node_or_skip() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    if tuple(int(x) for x in out.lstrip("v").split(".")[:2]) < (22, 6):
        pytest.skip(f"node {out} cannot run TypeScript (--experimental-strip-types needs 22.6)")
    return node


def run_js(script: str, payload) -> dict:
    """`script` with geometry.ts as G, files.ts as F (and manifold-3d as wasm, when it is there), reading `payload`
    from stdin."""
    node = node_or_skip()
    head = (f'import * as G from {json.dumps(str(GEOMETRY))};\nimport * as F from {json.dumps(str(FILES))};\n'
            'import { readFileSync } from "node:fs";\n')
    if "wasm" in script:
        if not MANIFOLD_JS.exists():
            pytest.skip("frontend/node_modules has no manifold-3d (npm ci in frontend/)")
        head += f"import Module from {json.dumps(str(MANIFOLD_JS))};\nconst wasm = await Module(); wasm.setup();\n"
    if "wasm" in script or "fonts" in script:          # every font, loaded: as the editor loads those a design uses
        head += (f"const FONTS = {json.dumps(str(FONTS))};\n"
                 "const fonts = { index: JSON.parse(readFileSync(FONTS + '/index.json', 'utf8')), loaded: {} };\n"
                 "for (const f of (await import('node:fs')).readdirSync(FONTS)) if (f.endsWith('.json') && "
                 "f !== 'index.json') fonts.loaded[f.slice(0, -5)] = JSON.parse(readFileSync(FONTS + '/' + f, 'utf8'));\n")
    head += "const input = JSON.parse(readFileSync(0, 'utf8'));\n"
    out = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e",
                          head + script], input=json.dumps(payload), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout)


def test_the_fonts_are_the_same_in_both_places(h3):
    sandbox = ROOT / "services" / "sandbox" / "fonts"
    names = sorted(f.name for f in FONTS.iterdir())
    assert names == sorted(f.name for f in sandbox.iterdir()) and "OFL.txt" in names
    for name in names:
        assert (FONTS / name).read_bytes() == (sandbox / name).read_bytes(), f"{name}: run tools/make_3d_font.py again"
    index = json.loads((FONTS / "index.json").read_text(encoding="utf-8"))
    assert list(index["families"]) == list(h3.FONT_FAMILIES) and index["default"] == h3.FONT_FAMILIES[0]
    files = {f for fam in index["families"].values() for k, f in fam.items() if k != "name"}
    assert files == {n[:-5] for n in names if n.endswith(".json") and n not in ("index.json", "previews.json")}
    previews = json.loads((FONTS / "previews.json").read_text(encoding="utf-8"))      # the Font box's list (0.99.0)
    assert list(previews) == list(index["families"])
    for fid, fam in index["families"].items():
        assert set(fam["name"]) - {" "} <= set(previews[fid]["glyphs"]) <= set(fam["name"])      # its name's letters
    for file in files:
        font = json.loads((FONTS / f"{file}.json").read_text(encoding="utf-8"))
        assert font["unitsPerEm"] == 1000 and all(ch in font["glyphs"] for ch in "AZaz09æøåÆØÅ?.,-+°"), file
        assert font["license"].startswith("SIL Open Font License 1.1 - Copyright ") and 500 < font["capHeight"] < 900
    assert index["families"]["sans"]["regular"] == "barlow-600"          # 0.87's one font: its texts stay as they were
    docker = (ROOT / "services" / "sandbox" / "Dockerfile").read_text()
    assert "COPY fonts/ /opt/hub3d/fonts/" in docker
    js = run_js("process.stdout.write(JSON.stringify({families: G.FONT_FAMILIES, aliases: G.FONT_ALIASES}));", {})
    assert js == {"families": list(h3.FONT_FAMILIES), "aliases": h3.FONT_ALIASES}


ODD = {"format": "slq-hub-3d", "version": 1, "printer": "bambu-p2s", "bed": [256, 256, 256], "parts": [
    {"id": "a", "shape": "box"},                                                    # all defaults
    {"id": "a", "shape": "box", "size": [10, -4, 0], "radius": 2, "sides": 7.5},     # the same id; odd numbers
    {"shape": "blob", "size": [1, 2], "at": [1, 2, "x"]},                           # no id, no such shape
    {"id": "c", "shape": "cone", "top": 3, "sides": 1000, "rotate": [10, 20, 30], "mirror": True, "hole": True},
    {"id": "t", "shape": "text", "size": [0, 8, 1.5], "text": 7},
    {"id": "m", "shape": "mesh"},
    {"id": "g", "shape": "group", "at": [5, 5, 5], "size": [9, 9, 9], "parts": [
        {"id": "s", "shape": "sphere", "sides": 2.5, "hidden": True}, "not a part"]},
    # 0.88.0: outlines, repeats, and keys a model guessed (chat 328: type, radius, height, position, rotation)
    {"id": "o", "shape": "outline", "points": [[0, 0], [10, 0], [5, 8]], "holes": [[[4, 2], [6, 2], [5, 4]], [[1, 1]]],
     "size": [0, -3, 2.5], "repeat": {"count": 6.4, "angle": 720, "around": [1, 2]}},
    {"id": "o2", "shape": "outline", "points": [[0, 0], [1, 1]], "repeat": {"count": 1}},
    {"id": "row", "shape": "box", "repeat": {"count": 500, "step": [10, 0, 0], "around": [9, 9]}},
    {"id": "bad", "shape": "box", "repeat": "six", "type": "cylinder", "position": [1, 2, 3]},
    {"id": "gr", "shape": "group", "repeat": {"count": 3, "angle": 90}, "radius": 4, "parts": []}]}
GEOMETRIC = ("id", "shape", "size", "at", "rotate", "mirror", "hole", "hidden", "sides", "radius", "top", "text",
             "font", "bold", "italic", "align", "letter_spacing", "line_spacing", "file", "points", "holes", "repeat")


def flat(parts: list) -> list[dict]:
    out = []
    for p in parts:
        out.append({k: p[k] for k in GEOMETRIC if k in p})
        out += flat(p.get("parts", []))
    return out


def test_both_read_a_design_with_the_same_defaults(h3):
    js = run_js("const r = G.readDesign(JSON.stringify(input)); "
                "process.stdout.write(JSON.stringify({parts: r.design.parts, problems: r.problems, "
                "text: G.writeDesign(r.design)}));", ODD)
    py, problems = h3.read_design(json.dumps(ODD))
    assert flat(js["parts"]) == flat(py["parts"])
    assert len(js["problems"]) == len(problems) == 7, (js["problems"], problems)
    assert problems[0] == js["problems"][0] == ("1 part has keys the format does not use (position, type) - run "
                                                "hub3d format: shape, size, at, rotate ...")
    # chat 328's first try: 31 parts in words of its own - two lines, not 31
    guessed = {"parts": [{"id": f"arm_{i}", "type": "cylinder", "radius": 3, "height": 100, "position": [0, 0, 0],
                          "rotation": [0, 90, 0]} for i in range(31)]}
    _, said = h3.read_design(json.dumps(guessed))
    js_said = run_js("process.stdout.write(JSON.stringify(G.readDesign(JSON.stringify(input)).problems));", guessed)
    assert said == [("31 parts have keys the format does not use (height, position, rotation, type) - run hub3d "
                     "format: shape, size, at, rotate ..."),
                    "31 parts have no shape the format knows (arm_0, arm_1, arm_2 ...) - taken as boxes"]
    assert js_said == [said[0], said[1] + "."]
    by_id = {p["id"]: p for p in flat(py["parts"])}
    assert by_id["a"]["size"] == [20, 20, 20] and by_id["a"]["at"] == [0, 0, 10]
    assert by_id["p2"]["size"] == [10, 4, 0.01] and by_id["p2"]["sides"] == 8 and by_id["p2"]["radius"] == 2
    assert by_id["p3"]["shape"] == "box" and by_id["p3"]["at"] == [0, 0, 10]
    assert by_id["c"]["top"] == 1 and by_id["c"]["sides"] == 256 and by_id["c"]["mirror"] and by_id["c"]["hole"]
    assert by_id["t"]["size"] == [0, 8, 1.5] and by_id["t"]["text"] == "Text"
    assert by_id["s"]["sides"] == 3 and by_id["s"]["hidden"]
    o = by_id["o"]                         # an outline: 0 keeps its points' size; a hole of one point left out
    assert o["size"] == [0, 0, 2.5] and o["at"] == [0, 0, 1.25] and len(o["holes"]) == 1
    assert o["repeat"] == {"count": 6, "around": [1, 2]}                       # 720 degrees: all round
    assert "points" not in by_id["o2"] and "repeat" not in by_id["o2"]           # too few points; one copy is none
    assert by_id["row"]["repeat"] == {"count": 100, "step": [10, 0, 0]}          # a row: around means nothing
    assert "repeat" not in by_id["bad"] and by_id["gr"]["repeat"] == {"count": 3, "angle": 90}
    assert "radius" not in by_id["gr"]
    # What the editor writes reads back as it was, and a group keeps no place of its own.
    again, _ = h3.read_design(js["text"])
    assert flat(again["parts"]) == flat(py["parts"])
    assert '"size": [20, 20, 20]' in js["text"] and '"at": [5, 5, 5]' not in js["text"]
    assert '"points": [[0, 0], [10, 0], [5, 8]]' in js["text"] and '"holes": [\n' in js["text"]
    assert '"around": [1, 2]' in js["text"] and '"angle"' not in js["text"].split('"id": "o"')[1].split('"id"')[0]


SAMPLE = {"format": "slq-hub-3d", "version": 1, "bed": [256, 256, 256], "parts": [
    {"id": "base", "shape": "box", "size": [60, 40, 10], "at": [0, 0, 5], "radius": 2},
    {"id": "hole", "shape": "cylinder", "size": [8, 8, 20], "at": [15, 0, 5], "hole": True},
    {"id": "peg", "shape": "cone", "size": [10, 10, 15], "at": [-15, 0, 17.5], "top": 0.4, "rotate": [0, 0, 45]},
    {"id": "ramp", "shape": "wedge", "size": [20, 10, 10], "at": [0, 25, 5], "rotate": [0, 0, 180], "mirror": True},
    {"id": "ball", "shape": "sphere", "size": [12, 12, 14], "at": [0, -26, 7]},
    {"id": "label", "shape": "text", "text": "SLQ æøå\nHub", "size": [0, 8, 2], "at": [-5, 0, 11],
     "rotate": [0, 0, 10]},
    {"id": "gone", "shape": "box", "size": [99, 99, 99], "hidden": True},
    {"id": "g", "shape": "group", "parts": [
        {"id": "post", "shape": "box", "size": [6, 6, 30], "at": [25, 15, 15], "rotate": [0, 5, 0]},
        {"id": "slot", "shape": "box", "size": [2, 10, 10], "at": [25, 15, 25], "hole": True},
        {"id": "inner", "shape": "group", "hole": True, "parts": [
            {"id": "bore", "shape": "cylinder", "size": [3, 3, 40], "at": [25, 15, 15], "sides": 24}]}]},
    {"id": "cube", "shape": "mesh", "file": "cube.stl", "size": [10, 10, 5], "at": [-25, 15, 2.5],
     "rotate": [0, 0, 30], "mirror": True},
    {"id": "sheet", "shape": "mesh", "file": "open.stl", "size": [8, 8, 1], "at": [-25, -15, 0.5]}]}


# 0.88.0: outlines (holes, scaled, turned, mirrored, drawn either way round, crossing themselves), repeats (round, over
# an angle, in a row, of a hole, within a repeated group) - and a group of solids alone in the design, which built
# different triangles in the two before every join was worked out where it is made.
MORE = [
    {"parts": [{"id": "o", "shape": "outline", "points": [[0, 0], [30, 0], [30, 20], [15, 30], [0, 20]],
                "holes": [[[5, 5], [10, 5], [10, 10], [5, 10]], [[20, 12], [22, 18], [25, 12]]],
                "size": [45, 0, 4], "at": [5, -3, 2], "rotate": [0, 0, 20], "mirror": True},
               {"id": "bow", "shape": "outline", "points": [[0, 0], [10, 10], [10, 0], [0, 10]], "size": [0, 0, 2],
                "at": [-30, 0, 1]},
               {"id": "cw", "shape": "outline", "points": [[0, 0], [0, 8], [8, 8], [8, 0]], "at": [-30, 20, 1],
                "holes": [[[2, 2], [6, 2], [6, 6], [2, 6]]]}]},
    {"parts": [{"id": "plate", "shape": "cylinder", "size": [60, 60, 3], "at": [0, 0, 1.5]},
               {"id": "h", "shape": "cylinder", "size": [5, 5, 5], "at": [22, 0, 1.5], "hole": True,
                "repeat": {"count": 8, "around": [0, 0]}},
               {"id": "fan", "shape": "box", "size": [20, 4, 3], "at": [15, 0, 4.5],
                "repeat": {"count": 4, "angle": 90, "around": [0, 0]}},
               {"id": "row", "shape": "cylinder", "size": [6, 6, 10], "at": [-25, -40, 5],
                "repeat": {"count": 5, "step": [12, 0, 0]}}]},
    {"parts": [{"id": "g", "shape": "group", "repeat": {"count": 3, "around": [0, 0]}, "parts": [
        {"id": "rung", "shape": "box", "size": [10, 2, 2], "at": [20, 0, 1], "repeat": {"count": 4, "step": [0, 4, 0]}},
        {"id": "self", "shape": "wedge", "size": [8, 6, 4], "at": [32, 3, 2], "rotate": [0, 0, 30],
         "repeat": {"count": 5}}]}]},
    {"parts": [{"id": "base", "shape": "box", "size": [40, 40, 4], "at": [0, 0, 2]},
               {"id": "g", "shape": "group", "parts": [
                   {"id": "a", "shape": "box", "size": [10, 10, 10], "at": [10, 10, 8]},
                   {"id": "b", "shape": "sphere", "size": [12, 12, 12], "at": [-10, -10, 8]}]}]},
]


def write_stl(path: Path, v: np.ndarray, f: np.ndarray) -> None:
    out = bytearray(b"\0" * 80 + struct.pack("<I", len(f)))
    for t in f:
        out += struct.pack("<12fH", 0, 0, 0, *v[t[0]], *v[t[1]], *v[t[2]], 0)
    path.write_bytes(bytes(out))


def meshes(folder: Path) -> None:
    import trimesh
    cube = trimesh.creation.box(extents=(4, 6, 2))
    write_stl(folder / "cube.stl", np.asarray(cube.vertices), np.asarray(cube.faces))
    write_stl(folder / "open.stl", np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float),
              np.array([[0, 2, 1], [0, 1, 3]]))                           # two triangles: not a solid


BUILD_JS = """
const meshes = new Map();
for (const [name, b64] of Object.entries(input.files)) meshes.set(name, G.readStl(Buffer.from(b64, "base64")));
const out = input.designs.map((d) => {
  const { design } = G.readDesign(JSON.stringify(d));
  const b = G.build(design, { wasm, fonts, meshes });
  const owners = {};
  for (const id of b.triPart) owners[id] = (owners[id] || 0) + 1;
  return { triangles: b.triangles.length / 3, volume: b.volume, box: b.box, loose: b.loose.map((l) => l.part),
           problems: b.problems.length, owners };
});
process.stdout.write(JSON.stringify(out));
"""


def test_the_editor_and_hub3d_build_the_same_solid(h3, tmp_path):
    pytest.importorskip("manifold3d")
    import base64
    meshes(tmp_path)
    files = {n: base64.b64encode((tmp_path / n).read_bytes()).decode() for n in ("cube.stl", "open.stl")}
    variants = [SAMPLE,
                {"parts": [{"id": "t", "shape": "text", "text": "Bambu P2S", "size": [40, 6, 1]}]},
                {"parts": [{"id": "w", "shape": "wedge", "size": [30, 10, 20], "rotate": [90, 0, 0], "at": [1, 2, 3]},
                           {"id": "h", "shape": "sphere", "size": [10, 10, 10], "at": [1, 2, 3], "hole": True}]},
                {"parts": [{"id": "only-hole", "shape": "box", "hole": True}]},
                json.loads(h3.DESIGN_EXAMPLE),                  # hub3d format's examples
                json.loads(h3.DESIGN_SNOWFLAKE),
                *MORE,
                json.loads(h3.DESIGN_TREE)]
    js = run_js(BUILD_JS, {"files": files, "designs": variants})
    for d, j in zip(variants, js):
        design, _ = h3.read_design(json.dumps(d))
        b = h3.build_design(design, tmp_path)
        if b.solid is None:
            assert j["triangles"] == 0 and j["volume"] == 0
            continue
        mesh = b.solid.to_mesh()
        v = np.asarray(mesh.vert_properties)[:, :3]
        assert j["triangles"] == len(mesh.tri_verts), d
        assert j["volume"] == pytest.approx(b.solid.volume(), rel=1e-9, abs=1e-6)
        lo, hi = v.min(0), v.max(0)
        loose = [x for _, x, _ in b.loose]
        if loose:
            lo = np.minimum(lo, np.min([x.min(0) for x in loose], axis=0))
            hi = np.maximum(hi, np.max([x.max(0) for x in loose], axis=0))
        assert np.allclose(j["box"], [lo, hi], atol=1e-4), (j["box"], lo, hi)
        assert j["loose"] == [p for p, _, _ in b.loose] and j["problems"] == len(b.problems)
    sample = js[0]
    assert sample["loose"] == ["sheet"] and sample["volume"] > 25_000
    assert {"base", "hole", "label", "slot", "bore", "cube"} <= set(sample["owners"]) and "gone" not in sample["owners"]
    snow = js[5]                                      # every copy's triangles are its part's
    assert {"core", "window", "spine", "twig1", "twig2", "twig3", "twig4", "gem", "eye"} == set(snow["owners"])
    assert np.allclose(snow["box"], [[-60, -51.9615, 0], [60, 51.9615, 3]], atol=1e-3), snow["box"]
    assert js[7]["volume"] > 0 and {"plate", "h", "fan", "row"} == set(js[7]["owners"])


MOVES_JS = """
const out = {};
const box = { id: "b", shape: "wedge", size: [30, 10, 20], at: [10, 0, 10], rotate: [0, 0, 30] };
const once = G.mirrorPart(box, 0, [0, 0, 0]), twice = G.mirrorPart(once, 0, [0, 0, 0]);
out.once = once; out.twice = twice;
const turned = G.transformPart(box, [0, 0, 0], [5, 0, 0], G.rotation([0, 0, 90]), 2);
out.turned = turned;
out.extent = G.extent(box);
const build = (parts) => { const b = G.build({ format: G.FORMAT, version: 1, parts }, { wasm, fonts, meshes: new Map() });
  return { volume: b.volume, box: b.box }; };
out.built = [build([box]), build([once]), build([twice]), build([turned])];
out.width = G.textMeasure({ id: "t", shape: "text", text: "SLQ", size: [0, 10, 2], at: [0, 0, 1], rotate: [0, 0, 0] },
  fonts).width;
process.stdout.write(JSON.stringify(out));
"""


def test_moving_mirroring_and_turning_keep_the_shape():
    out = run_js(MOVES_JS, {})
    plain, once, twice, turned = out["built"]
    assert out["once"]["mirror"] is True and "mirror" not in out["twice"] or not out["twice"].get("mirror")
    assert once["volume"] == pytest.approx(plain["volume"]) == pytest.approx(twice["volume"])
    lo, hi = plain["box"]
    assert once["box"][0][0] == pytest.approx(-hi[0], abs=1e-4) and once["box"][1][0] == pytest.approx(-lo[0], abs=1e-4)
    assert np.allclose(twice["box"], plain["box"], atol=1e-4)
    assert turned["volume"] == pytest.approx(plain["volume"] * 8, rel=1e-6)          # twice the size: 8x
    assert out["turned"]["at"] == pytest.approx([5, 20, 20]) and out["turned"]["rotate"] == pytest.approx([0, 0, 120])
    assert np.allclose(out["extent"], plain["box"], atol=1e-3)                         # the turned box is the solid's
    assert 15 < out["width"] < 25


def test_the_design_command(h3, tmp_path, capsys, monkeypatch):
    pytest.importorskip("manifold3d")
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    meshes(tmp_path)
    f = tmp_path / "stand.3d.json"
    f.write_text(json.dumps(SAMPLE))
    code = h3.main(["design", str(f), "--views", str(tmp_path / "v.png"), "--3mf", str(tmp_path / "out.3mf")])
    out = capsys.readouterr().out
    assert code == 1                                            # a loose mesh: not ready as one solid
    assert out.startswith("stand.3d.json: 2 box, 1 cylinder hole, 1 cone, 1 wedge, 1 sphere, 1 text, 2 group, "
                          "1 box hole, 1 cylinder, 2 mesh; laid out for a bed (256 x 256 x 256 mm)"), out
    assert "- sheet: open.stl is not a closed solid" in out and (tmp_path / "v.png").stat().st_size > 10_000
    assert "\ntext label: 2 lines in Barlow SemiBold, " in out and " x 8.0 x 2.0 mm - its capitals " in out
    assert (tmp_path / "out.3mf").exists()
    solid = h3.design(f, quiet=True)
    assert solid.volume() > 25_000
    (tmp_path / "empty.3d.json").write_text('{"parts": [{"id": "h", "shape": "box", "hole": true}]}')
    with pytest.raises(h3.NotPrintable):
        h3.design(tmp_path / "empty.3d.json", quiet=True)


POINT_JS = """
process.stdout.write(JSON.stringify({
  lid: F.pointMark("Lid", "p3", false, [12, -4, 20], [0, 0, 1]),
  tag: F.pointMark("Tag", "tag", false, [3, -11, 1.5], [0, -0.9999, 0.001]),
  hole: F.pointMark("box p2", "p2", true, [1.005, 99.999, 123.45], [0.6, 0, 0.8]),
  paths: [F.inFolder("/workspace/projects/3/hub/a.3d.json", { kind: "project", id: 3 }),
          F.inFolder("./x.3d.json", { kind: "chat", id: 9 }),
          F.inFolder("/workspace/chats/12/sub/y.3d.json", { kind: "chat", id: 12 }),
          F.inFolder("/workspace/projects/31/z.3d.json", { kind: "project", id: 3 }),
          F.inFolder("/workspace/projects/3/old/projects/3/w.3d.json", { kind: "project", id: 3 })],
  text: G.transformPart({ id: "t", shape: "text", text: "SLQ", size: [0, 10, 2], at: [0, 0, 1], rotate: [0, 0, 0] },
                        [0, 0, 0], [0, 0, 0], null, 2).size,
}));
"""


def test_the_editor_says_spots_as_the_assistant_is_told(h3):
    """"Point at" in the editor puts a spot into the message in the words the prompt and `hub3d format` show."""
    from app.agent.prompt import print3d_text
    out = run_js(POINT_JS, {})
    assert out["lid"] in print3d_text(True, False)
    assert out["tag"] in h3.DESIGN_GUIDE
    assert out["hole"] == ('[3D point: part "box p2" (id p2, a hole) at x 1, y 100, z 123.5 mm, on a face facing '
                           '+X (right), upwards]')
    # the assistant may name a file by the sandbox's path: the editor opens it as the Hub reads it
    assert out["paths"] == ["hub/a.3d.json", "x.3d.json", "sub/y.3d.json", "workspace/projects/31/z.3d.json",
                            "old/projects/3/w.3d.json"]
    assert out["text"] == [0, 20, 4]                 # a text's width 0 (it follows its letters) stays 0 when scaled


def test_hub3d_format_is_what_both_read(h3, capsys):
    """`hub3d format`'s example reads without a problem in the editor and in hub3d, and names what the prompt does."""
    assert h3.main(["format"]) == 0
    guide = capsys.readouterr().out
    assert guide == h3.DESIGN_GUIDE and all(x in guide for x in (h3.DESIGN_EXAMPLE, h3.DESIGN_SNOWFLAKE, h3.DESIGN_TREE))
    for example in (h3.DESIGN_EXAMPLE, h3.DESIGN_SNOWFLAKE, h3.DESIGN_TREE):
        _, problems = h3.read_design(example)
        assert problems == []
        js = run_js("""process.stdout.write(JSON.stringify(G.readDesign(input.text).problems));""", {"text": example})
        assert js == []
    for shape in ("box", "cylinder", "cone", "sphere", "wedge", "text", "outline", "mesh", "group"):
        assert f"  {shape} " in guide and f"| {shape}" in guide or shape == "box"
    assert "  repeat    copies of the part" in guide and "linear_extrude(size z, center=true) polygon(points)" in guide
    assert "Not cylinders lying down" in guide                         # chat 328
    assert "tiers that step out" in guide and '{"bottom": 17}' in guide   # 0.93.1: the tree
    assert 'hole\'s {"vertical": 5, "bottom": 2} - never its edges at the opening' in guide   # 0.95.0: a hole's lines
    keys = (ROOT / "frontend" / "src" / "model3d" / "geometry.ts").read_text()
    assert all(f'"{k}"' in keys for k in h3.PART_KEYS)                  # the two know the same keys
    assert h3.MAX_COPIES == 100 and "export const MAX_COPIES = 100;" in keys
    printers = (ROOT / "frontend" / "src" / "model3d" / "printers.ts").read_text()
    for pid in ("bambu-p2s", "bambu-p1s", "bambu-x1", "bambu-a1", "bambu-a1-mini", "bambu-h2s", "bambu-h2d"):
        assert f'id: "{pid}"' in printers and pid in guide


REPEAT_MOVES_JS = """
const fan = { id: "fan", shape: "box", size: [20, 4, 3], at: [15, 0, 1.5], rotate: [0, 0, 0],
  repeat: { count: 4, angle: 90, around: [0, 0] } };
const arm = { id: "arm", shape: "outline", size: [0, 0, 3], at: [0, 0, 1.5], rotate: [0, 0, 0],
  points: [[0, -2], [40, -2], [44, 0], [40, 2], [0, 2]], repeat: { count: 6 } };
const g = { id: "g", shape: "group", size: [0, 0, 0], at: [0, 0, 0], rotate: [0, 0, 0], repeat: { count: 3 },
  parts: [{ id: "b", shape: "box", size: [10, 3, 3], at: [12, 0, 1.5], rotate: [0, 0, 0] }] };
const kit = { wasm, fonts, meshes: new Map() };
const one = (p) => { const b = G.build({ format: G.FORMAT, version: 1, parts: [p] }, kit); return { volume: b.volume,
  box: b.box, tri: b.triangles.length / 3, rests: b.rests }; };
const turn = G.rotation([0, 0, 30]);
const out = {};
for (const [name, p] of Object.entries({ fan, arm, g })) {
  out[name] = { plain: one(p), extent: G.extent(p),
    moved: one(G.transformPart(p, [0, 0, 0], [30, -20, 5], null)),
    turned: one(G.transformPart(p, [10, 10, 0], [0, 0, 0], turn)),
    bigger: one(G.transformPart(p, [0, 0, 0], [0, 0, 0], null, 2)),
    mirrored: one(G.mirrorPart(p, 0, [5, 0, 0])), mirroredY: one(G.mirrorPart(p, 1, [0, 7, 0])),
    movedRepeat: G.transformPart(p, [0, 0, 0], [30, -20, 5], null).repeat,
    size: G.partSize(p) };
}
process.stdout.write(JSON.stringify(out));
"""


def test_copies_move_turn_grow_and_mirror_with_their_part():
    """Moving, turning, sizing or mirroring a repeated part or group in the editor takes its copies along: the solid
    keeps its volume and moves as a whole (its centre of turning, or its row, is changed with it)."""
    out = run_js(REPEAT_MOVES_JS, {})
    for name, r in out.items():
        v = r["plain"]["volume"]
        assert r["moved"]["volume"] == pytest.approx(v, rel=1e-6) and r["turned"]["volume"] == pytest.approx(v, rel=1e-6)
        assert r["mirrored"]["volume"] == pytest.approx(v, rel=1e-6) == r["mirroredY"]["volume"], name
        assert r["bigger"]["volume"] == pytest.approx(v * 8, rel=1e-4), name
        lo, hi = r["plain"]["box"]
        mlo, mhi = r["moved"]["box"]
        assert np.allclose(np.array(mlo) - lo, [30, -20, 5], atol=1e-3) and np.allclose(np.array(mhi) - hi, [30, -20, 5],
                                                                                         atol=1e-3), name
        xlo, xhi = r["mirrored"]["box"]                                   # mirrored through x = 5
        assert xlo[0] == pytest.approx(10 - hi[0], abs=1e-3) and xhi[0] == pytest.approx(10 - lo[0], abs=1e-3), name
        elo, ehi = r["extent"]                                            # the editor's box of it holds the solid
        assert all(elo[i] <= lo[i] + 1e-3 and hi[i] <= ehi[i] + 1e-3 for i in range(3)), name
    assert out["fan"]["movedRepeat"] == {"count": 4, "angle": 90, "around": [30, -20]}
    assert out["g"]["movedRepeat"] == {"count": 3, "around": [30, -20]}         # a group's 0, 0 became a point
    assert out["arm"]["movedRepeat"] == {"count": 6}                            # a part's own at moves with it
    assert out["arm"]["size"] == [44, 4, 3]                                     # an outline's 0s: as drawn
    assert out["arm"]["plain"]["rests"]["contact"] == pytest.approx(out["arm"]["plain"]["rests"]["footprint"], rel=1e-6)


def test_what_a_solid_stands_on(h3, tmp_path, capsys, monkeypatch):
    """Chat 328: a snowflake of 31 cylinders lying down, centred at z 2 - 6 mm thick, 1 mm below the plate - came out
    "READY for the slicer". hub3d now says what it stands on, and a design that leaves the plate; the editor
    measures the same."""
    pytest.importorskip("manifold3d")
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    rods = {"parts": [{"id": f"rod{i}", "shape": "cylinder", "size": [6, 6, 100], "at": [0, 0, 2],
                       "rotate": [0, 90, 60 * i]} for i in range(3)]}
    flat = {"parts": [{"id": "plate", "shape": "box", "size": [60, 40, 3], "at": [0, 0, 1.5], "radius": 0.6}]}
    high = {"parts": [{"id": "plate", "shape": "box", "size": [60, 40, 3], "at": [0, 0, 4]}]}
    reports = []
    for name, d in (("rods", rods), ("flat", flat), ("high", high)):
        f = tmp_path / f"{name}.3d.json"
        f.write_text(json.dumps(d))
        assert h3.main(["design", str(f)]) == 0
        reports.append(capsys.readouterr().out)
    rods_out, flat_out, high_out = reports
    assert "! it stands on" in rods_out and "round or pointed underneath" in rods_out
    assert "! its lowest point is at z -1.00 mm, below the plate" in rods_out and "READY" not in rods_out
    assert "stands on" not in flat_out and "lowest point" not in flat_out and "READY for the slicer." in flat_out
    assert "! its lowest point is at z 2.50 mm, above the plate" in high_out
    report = h3.check(h3.design(tmp_path / "rods.3d.json", quiet=True))
    assert 0 < report.contact < 0.4 * report.footprint
    js = run_js("""const out = input.map((d) => { const { design } = G.readDesign(JSON.stringify(d));
      return G.build(design, { wasm, fonts, meshes: new Map() }).rests; }); process.stdout.write(JSON.stringify(out));""",
                [rods, flat])
    assert js[0]["contact"] == pytest.approx(report.contact, rel=1e-3)
    assert js[0]["footprint"] == pytest.approx(report.footprint, rel=1e-3)
    assert js[1]["contact"] == pytest.approx(js[1]["footprint"], rel=0.05)
    # a file of its own (an STL) is told the same - but not where it lies: slicers put it on the plate themselves
    pen = h3.check(h3.design(tmp_path / "flat.3d.json", quiet=True))
    assert not any("stands on" in w for w in pen.warnings)


# 0.89.0: text in six fonts, bold and italic, its lines aligned and spaced. Chat 330: "the text must be bigger, and
# in italics" - there was one font, and the format's "letter height" was the height of all the lines: two lines at
# 20 had capitals 6.6 mm tall on a 100 mm plate, and nothing said so.
TEXTS = [{"parts": [{"id": "t", "shape": "text", "text": "Søren\nLyder ÆØÅ 08", "size": [0, 20, 2], "font": f,
                     "bold": b, "italic": i}]}
         for f in ("sans", "serif", "rounded", "script", "stencil", "mono") for b in (False, True) for i in (False, True)]
TEXTS += [
    {"parts": [{"id": "t", "shape": "text", "text": "Venstre\nhøjre side\nx", "align": "left", "size": [50, 0, 2]}]},
    {"parts": [{"id": "t", "shape": "text", "text": "Venstre\nhøjre side\nx", "align": "right", "size": [50, 30, 2],
                "letter_spacing": 1.3, "line_spacing": 0.8}]},
    {"parts": [{"id": "t", "shape": "text", "text": "Stående", "font": "Arial:style=Bold Italic", "size": [0, 8, 1],
                "rotate": [90, 0, 0], "at": [0, 0, 4], "mirror": True}]},
    {"parts": [{"id": "plate", "shape": "box", "size": [100, 100, 3], "at": [0, 0, 1.5]},
               {"id": "t", "shape": "text", "text": "Søren\nLyder", "size": [80, 0, 2], "at": [0, 0, 3.9],
                "italic": True},
               {"id": "cut", "shape": "text", "text": "HUB", "font": "stencil", "size": [40, 0, 5], "at": [0, -38, 1.5],
                "hole": True}]},
]
# 0.99.0: ten fonts more (Søren: "Kan vi få nogle flere skrifttyper?") - each upright and bold, where it has one
NEW_FONTS = ("condensed", "geometric", "classic", "handwriting", "marker", "comic", "western", "blackletter", "futuristic",
             "pixel")
TEXTS += [{"parts": [{"id": "t", "shape": "text", "text": "Søren æøå\nÆØÅ 08", "size": [0, 20, 2], "font": f, "bold": b}]}
          for f in NEW_FONTS for b in (False, True)]
TEXT_JS = """
const out = input.map((d) => {
  const r = G.readDesign(JSON.stringify(d));
  const b = G.build(r.design, { wasm, fonts, meshes: new Map() });
  const t = r.design.parts.find((p) => p.id === "t");
  return { triangles: b.triangles.length / 3, volume: b.volume, box: b.box, problems: r.problems.length + b.problems.length,
           measure: G.textMeasure(t, fonts) };
});
process.stdout.write(JSON.stringify(out));
"""


def test_text_in_every_font_and_style_builds_the_same_in_both(h3):
    pytest.importorskip("manifold3d")
    js = run_js(TEXT_JS, TEXTS)
    names = set()
    for d, j in zip(TEXTS, js):
        design, problems = h3.read_design(json.dumps(d))
        b = h3.build_design(design, ".")
        mesh = b.solid.to_mesh()
        v = np.asarray(mesh.vert_properties)[:, :3]
        assert j["triangles"] == len(mesh.tri_verts), d
        assert j["volume"] == pytest.approx(b.solid.volume(), rel=1e-9, abs=1e-6)
        assert np.allclose(j["box"], [v.min(0), v.max(0)], atol=1e-4)
        assert j["problems"] == len(problems + b.problems) == 0, d
        m = h3._text_measure(next(p for p in design["parts"] if p["id"] == "t"))
        for k in ("width", "height", "capitals"):
            assert j["measure"][k] == pytest.approx(m[k], rel=1e-12), (k, d)
        assert j["measure"]["font"] == m["font"] and j["measure"]["lines"] == m["lines"]
        names.add(m["font"])
    # every family in its own font; a bold where there is one; an italic, or the upright slanted
    assert {"Barlow SemiBold", "Barlow ExtraBold Italic", "Bitter SemiBold Italic", "Nunito Black",
            "Pacifico Regular", "Saira Stencil One Regular", "JetBrains Mono ExtraBold"} <= names
    assert {"Oswald Medium", "Oswald Bold", "Montserrat SemiBold", "Montserrat ExtraBold", "Cinzel SemiBold",
            "Cinzel Black", "Caveat Medium", "Caveat Bold", "Knewave Regular", "Bangers Regular",
            "Holtwood One SC Regular", "Grenze Gotisch SemiBold", "Grenze Gotisch ExtraBold", "Oxanium SemiBold",
            "Oxanium ExtraBold", "Pixelify Sans Medium", "Pixelify Sans Bold"} <= names
    for j in js[-20:]:                                  # each new font draws its letters (æøå: the fonts test)
        assert j["measure"]["capitals"] > 4 and j["triangles"] > 500
    widths = [j["measure"]["width"] for j in js[:24]]
    assert widths[12] == widths[14] != widths[13] == widths[15]          # script: no bold; italic slants it
    assert js[0]["measure"]["capitals"] == pytest.approx(6.64, abs=0.01)  # chat 330's: 20 high, two lines
    assert js[24]["measure"]["width"] == pytest.approx(50) and js[24]["measure"]["height"] > 30   # its height follows
    plate = js[27]
    assert plate["measure"]["width"] == pytest.approx(80) and plate["measure"]["capitals"] == pytest.approx(22.7, abs=0.1)


ODD_TEXT = {"parts": [
    {"id": "a", "shape": "text", "text": "x", "font": "Arial:style=Bold Italic"},       # OpenSCAD's way: sans, both
    {"id": "b", "shape": "text", "text": "x", "font": "Comic Sans", "align": "justify", "size": [0, 0, 2]},
    {"id": "c", "shape": "text", "text": "x", "font": "Pacifico", "bold": True, "align": "Right", "letter_spacing": 9,
     "line_spacing": "wide"},
    {"id": "d", "shape": "text", "text": "x", "font": "serif", "italic": "yes", "align": "centre", "letter_spacing": 1,
     "line_spacing": 0.8},
    {"id": "e", "shape": "text", "text": "x", "font": 7, "size": [-30, 12, 1]},
    {"id": "f", "shape": "box", "font": "serif", "bold": True},                         # known keys, no text: unused
    {"id": "g", "shape": "text", "text": "x", "font": "JetBrains Mono ExtraBold", "align": "left"}]}


def test_both_read_text_options_the_same(h3):
    js = run_js("const r = G.readDesign(JSON.stringify(input)); process.stdout.write(JSON.stringify({parts: "
                "r.design.parts, problems: r.problems, text: G.writeDesign(r.design)}));", ODD_TEXT)
    py, problems = h3.read_design(json.dumps(ODD_TEXT))
    assert flat(js["parts"]) == flat(py["parts"])
    assert len(js["problems"]) == len(problems) == 6, (js["problems"], problems)
    assert [x.split(":")[0] for x in problems] == ["b", "b", "b", "c", "c", "e"]
    assert ('no font "Comic Sans" - the fonts are sans, condensed, geometric, rounded, serif, classic, script, '
            'handwriting, marker, stencil, comic, western, blackletter, futuristic, pixel, mono; drawn in sans') in problems[1]
    by_id = {p["id"]: p for p in py["parts"]}
    assert by_id["a"]["size"] == [0, 10, 2] and by_id["a"]["bold"] and by_id["a"]["italic"] and "font" not in by_id["a"]
    assert by_id["b"]["size"] == [0, 10, 2] and "font" not in by_id["b"] and "align" not in by_id["b"]
    c = by_id["c"]
    assert c["font"] == "script" and c["bold"] and c["align"] == "right" and c["letter_spacing"] == 4
    assert "line_spacing" not in c
    d = by_id["d"]
    assert d["font"] == "serif" and "italic" not in d and "align" not in d and "letter_spacing" not in d
    assert d["line_spacing"] == 0.8 and by_id["e"]["size"] == [0, 12, 1]
    assert by_id["g"] == {**by_id["g"], "font": "mono", "bold": True, "align": "left"}
    again, said = h3.read_design(js["text"])                        # what the editor writes reads back as it was
    assert flat(again["parts"]) == flat(py["parts"]) and said == []
    assert '"font": "script",\n' in js["text"] and '"letter_spacing": 4' in js["text"] and '"font": "sans"' not in js["text"]


LAYOUT_JS = """
const base = { id: "t", shape: "text", size: [0, 10, 2], at: [0, 0, 1], rotate: [0, 0, 0] };
const font = fonts.loaded["barlow-600"];
const lines = (p) => {                       // each line's ink from x to x: line 1 above y -300, line 2 below
  const cs = G.textContours({ ...base, ...p }, font);
  const span = (sel) => { const xs = cs.filter(sel).flat().map((q) => q[0]); return [Math.min(...xs), Math.max(...xs)]; };
  return [span((c) => c[0][1] > -300), span((c) => c[0][1] <= -300), Math.min(...cs.flat().map((q) => q[1]))];
};
process.stdout.write(JSON.stringify({
  left: lines({ text: "WW\\nW", align: "left" }), right: lines({ text: "WW\\nW", align: "right" }),
  centre: lines({ text: "WW\\nW" }), wide: lines({ text: "WW\\nW", letter_spacing: 2 }),
  apart: lines({ text: "WW\\nW", line_spacing: 1.5 }), w: font.glyphs["W"].a,
  contours: G.textContours({ ...base, text: "Hej\\nmed dig", align: "right", letter_spacing: 1.2, line_spacing: 0.9 },
                           font, G.SLANT),
}));
"""


def test_text_lines_align_and_space_as_said(h3):
    out = run_js(LAYOUT_JS, {})
    (l1, l2, _), (r1, r2, _), (c1, c2, low) = out["left"], out["right"], out["centre"]
    assert l1[0] == pytest.approx(l2[0]) and r1[1] == pytest.approx(r2[1])           # left: they start together ...
    assert (c1[0] + c1[1]) / 2 == pytest.approx((c2[0] + c2[1]) / 2, abs=1)           # ... centred: share a middle
    (w1, _, _), (_, _, lower) = out["wide"], out["apart"]
    assert (w1[1] - w1[0]) - (c1[1] - c1[0]) == pytest.approx(out["w"])               # one gap more: one advance
    assert lower == pytest.approx(low - 600)                                          # 1.5 lines of 1200 down
    p = {"id": "t", "shape": "text", "text": "Hej\nmed dig", "align": "right", "letter_spacing": 1.2, "line_spacing": 0.9}
    py = h3._text_contours(p, h3._font("barlow-600"), h3.SLANT)
    assert py == [[tuple(q) for q in c] for c in out["contours"]]                     # to the last bit


EDIT_JS = """
const t = { id: "t", shape: "text", text: "SLQ", size: [0, 10, 2], at: [0, 0, 1], rotate: [0, 0, 0] };
const m = (p) => G.textMeasure(p, fonts);
const own = m(t).width, caps = m(t).capitals;
const fixed = { ...t, size: [own, 10, 2] }, stretched = { ...t, size: [own * 2, 10, 2] };
const wide = { ...t, size: [60, 0, 2] };
const longer = G.retext(t, { text: "SLQ Hub\\nVojens" }, fonts), serif = G.retext(fixed, { text: "SLQ Hub", font: "serif" }, fonts);
process.stdout.write(JSON.stringify({
  own, caps, capitals: m(G.withCapitals(t, 15, fonts)),
  longer: [longer.size, m(longer).capitals], wide: [G.retext(wide, { bold: true }, fonts).size, m(G.retext(wide, { bold: true }, fonts)).capitals],
  serif: [serif.size, m(serif).capitals, m({ ...serif, size: [0, serif.size[1], 2] }).width],
  stretched: G.retext(stretched, { text: "SLQ Hub" }, fonts).size,
  high: G.partSize({ ...t, size: [70, 0, 2] }, fonts), file: G.fontFile({ ...t, font: "script", italic: true }, fonts.index),
}));
"""


def test_the_editor_sizes_text_by_its_letters():
    """The editor's "Letters" sets how tall the capitals come out; a new text, font or style keeps them that tall -
    the text grows around them - and a text stretched to a box of its own stays in it."""
    out = run_js(EDIT_JS, {})
    assert out["capitals"]["capitals"] == pytest.approx(15) and out["capitals"]["width"] > out["own"] * 1.4
    size, caps = out["longer"]                         # two lines now: twice as high and more, the letters as they were
    assert size[0] == 0 and size[1] > 20 and caps == pytest.approx(out["caps"])
    size, caps = out["wide"]                           # its width was the given one: bolder, it is wider
    assert size[0] > 60 and size[1] == 0
    size, caps, natural = out["serif"]                 # a box the letters fitted: they still do, as tall
    assert caps == pytest.approx(out["caps"]) and size[0] == pytest.approx(natural) and size[1] != 10
    assert out["stretched"][0] == pytest.approx(out["own"] * 2) and out["stretched"][1] == 10
    assert out["high"][0] == 70 and 10 < out["high"][1] < 40 and out["high"][2] == 2
    assert out["file"] == {"file": "pacifico-400", "slant": pytest.approx(0.1583844, abs=1e-7)}


def test_flat_things_are_drawn_straight_down(h3, tmp_path, capsys, monkeypatch):
    """Chat 330: from the front and the side a plate is a line, and its letters were small at an angle above. A flat
    design is drawn straight down first - its letters show there - and hub3d design says how big each text came
    out."""
    pytest.importorskip("manifold3d")
    from PIL import Image
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    plate = {"parts": [{"id": "plate", "shape": "box", "size": [100, 100, 3], "at": [0, 0, 1.5]},
                       {"id": "name", "shape": "text", "text": "Søren\nLyder", "size": [0, 20, 2], "at": [0, 0, 4]}]}
    bare = {"parts": plate["parts"][:1]}
    panels = []
    for name, d in (("plate", plate), ("bare", bare)):
        f = tmp_path / f"{name}.3d.json"
        f.write_text(json.dumps(d))
        h3.main(["design", str(f), "--views", str(tmp_path / f"{name}.png")])
        out = capsys.readouterr().out
        assert "straight down, from above and from the front" in out
        img = np.asarray(Image.open(tmp_path / f"{name}.png").convert("L"), dtype=np.int16)
        panels.append(img[:, :img.shape[1] // 3])
        if name == "plate":
            assert "text name: 2 lines in Barlow SemiBold, 23.8 x 20.0 x 2.0 mm - its capitals 6.6 mm tall" in out
    changed = (np.abs(panels[0] - panels[1]) > 12).mean()
    assert changed > 0.01, changed                         # the letters, seen from straight above
    cube = tmp_path / "cube.3d.json"
    cube.write_text(json.dumps({"parts": [{"id": "c", "shape": "box"}]}))
    h3.main(["design", str(cube), "--views", str(tmp_path / "cube.png")])
    assert "from the front, the side and above" in capsys.readouterr().out


# 0.91.0: fillets and chamfers on a part's own edges. Søren: "Er det muligt at lave en funktion hvor man efterfølgende
# kan lave fillet på et kant/side?"
EDGED = [
    {"parts": [{"id": "b", "shape": "box", "size": [40, 30, 20], "at": [0, 0, 10], "fillet": {"top": 4}}]},
    {"parts": [{"id": "b", "shape": "box", "size": [40, 30, 20], "at": [0, 0, 10], "fillet": {"all": 4}}]},
    {"parts": [{"id": "b", "shape": "box", "size": [40, 30, 20], "at": [0, 0, 10], "fillet": {"vertical": 6},
                "chamfer": {"top": 2, "bottom-front": 3}}]},
    {"parts": [{"id": "b", "shape": "box", "size": [40, 30, 20], "at": [0, 0, 10], "fillet": {"top": 4, "vertical": 4}}]},
    {"parts": [{"id": "b", "shape": "box", "size": [40, 30, 20], "at": [0, 0, 10], "fillet": {"top": 6, "vertical": 3},
                "sides": 16}]},
    {"parts": [{"id": "w", "shape": "wedge", "size": [40, 30, 20], "at": [0, 0, 10], "fillet": {"all": 2}}]},
    {"parts": [{"id": "w", "shape": "wedge", "size": [40, 30, 20], "at": [0, 0, 10], "chamfer": {"slope": 1.5},
                "fillet": {"bottom-left": 3}, "rotate": [0, 0, 30], "mirror": True}]},
    {"parts": [{"id": "c", "shape": "cylinder", "size": [30, 30, 20], "at": [0, 0, 10], "fillet": {"top": 4},
                "chamfer": {"bottom": 1}}]},
    {"parts": [{"id": "c", "shape": "cylinder", "size": [30, 20, 20], "at": [0, 0, 10], "sides": 6, "chamfer": {"all": 1}}]},
    {"parts": [{"id": "k", "shape": "cone", "size": [30, 30, 20], "at": [0, 0, 10], "top": 0.4, "fillet": {"all": 3}}]},
    {"parts": [{"id": "k", "shape": "cone", "size": [30, 30, 20], "at": [0, 0, 10], "fillet": {"top": 3, "bottom": 2}}]},
    {"parts": [{"id": "o", "shape": "outline", "size": [0, 0, 4], "points": [[0, 0], [40, 0], [40, 30], [20, 15], [0, 30]],
                "holes": [[[8, 5], [14, 5], [14, 11], [8, 11]]], "fillet": {"corners": 3}}]},
    {"parts": [{"id": "o", "shape": "outline", "size": [60, 0, 3], "points": [[0, -2], [52, -2], [60, 0], [52, 2], [0, 2]],
                "chamfer": {"vertical": 0.8}, "repeat": {"count": 6, "around": [0, 0]}}]},
    {"parts": [{"id": "b", "shape": "box", "size": [40, 30, 20], "at": [0, 0, 10], "fillet": {"top": 40}}]},
    {"parts": [{"id": "plate", "shape": "box", "size": [60, 40, 4], "at": [0, 0, 2], "fillet": {"vertical": 5},
                "chamfer": {"top": 0.6}},
               {"id": "h", "shape": "cylinder", "size": [6, 6, 10], "at": [20, 10, 2], "hole": True,
                "repeat": {"count": 4, "around": [0, 0]}},
               {"id": "post", "shape": "cylinder", "size": [10, 10, 15], "at": [0, 0, 11], "fillet": {"top": 2}},
               {"id": "g", "shape": "group", "parts": [
                   {"id": "w", "shape": "wedge", "size": [12, 8, 8], "at": [-24, -14, 8], "fillet": {"slope": 1}}]}]},
    {"parts": [{"id": "x", "shape": "box", "size": [10, 10, 10], "fillet": {"upper": 2, "top": -1}, "chamfer": [1]},
               {"id": "y", "shape": "sphere", "fillet": {"all": 1}},
               {"id": "z", "shape": "box", "radius": 2, "fillet": {"top": 1}},
               {"id": "t", "shape": "text", "text": "A", "chamfer": {"all": 1}, "size": [0, 10, 2], "at": [30, 0, 1]}]},
    {"parts": [{"id": "r", "shape": "box", "size": [40, 30, 20], "at": [0, 0, 10], "radius": 4, "sides": 32}]},
    # 0.95.0: a hole's lines inside what it cuts - Søren's spare-parts box, its pocket's corners and floor rounded
    {"parts": [{"id": "kasse", "shape": "box", "size": [100, 50, 40], "at": [0, 0, 20]},
               {"id": "inner", "name": "Innerum", "shape": "box", "size": [94, 44, 37], "at": [0, 0, 21.5], "hole": True,
                "fillet": {"vertical": 5, "bottom": 2}}]},
    {"parts": [{"id": "plate", "shape": "box", "size": [80, 60, 12], "at": [0, 0, 6], "rotate": [0, 0, 15]},
               {"id": "drill", "shape": "cylinder", "size": [8, 8, 20], "at": [25, 0, 14], "hole": True,
                "fillet": {"bottom": 1.5}, "repeat": {"count": 4, "around": [0, 0]}},
               {"id": "ramp", "shape": "wedge", "size": [30, 16, 8], "at": [0, 0, 9], "hole": True, "mirror": True,
                "chamfer": {"bottom": 1}},
               {"id": "g", "shape": "group", "hole": True, "parts": [
                   {"id": "slot", "shape": "outline", "points": [[0, 0], [20, 0], [20, 6], [0, 6]], "size": [0, 0, 8],
                    "at": [-10, -26, 10], "fillet": {"corners": 2}}]}]},
]


def test_fillets_and_chamfers_build_the_same_in_both(h3):
    pytest.importorskip("manifold3d")
    js = run_js(BUILD_JS, {"files": {}, "designs": EDGED})
    for d, j in zip(EDGED, js):
        design, problems = h3.read_design(json.dumps(d))
        b = h3.build_design(design, ".")
        mesh = b.solid.to_mesh()
        v = np.asarray(mesh.vert_properties)[:, :3]
        assert j["triangles"] == len(mesh.tri_verts), d
        assert j["volume"] == pytest.approx(b.solid.volume(), rel=1e-9, abs=1e-6)
        assert np.allclose(j["box"], [v.min(0), v.max(0)], atol=1e-4)
        assert j["problems"] == len(b.problems), (d, b.problems)
    said = run_js("process.stdout.write(JSON.stringify(input.map((d) => G.readDesign(JSON.stringify(d)).problems.length)));",
                  EDGED)
    assert said == [len(h3.read_design(json.dumps(d))[1]) for d in EDGED]
    vol = [j["volume"] for j in js]
    box = 40 * 30 * 20
    assert vol[0] < box and vol[3] < vol[0]                         # rounder: less
    assert js[1]["triangles"] == js[16]["triangles"] and vol[1] == pytest.approx(vol[16], rel=1e-12)  # all = radius
    assert js[0]["box"] == [[-20, -15, 0], [20, 15, 20]]              # its size stays
    assert js[13]["problems"] == 1 and said[15] == 6                   # too big: made smaller; names it did not know
    assert np.allclose(js[9]["box"][1][:2], [13.3594, 13.3594], atol=1e-3)  # a cone's rounded rim: in from 15


EDGE_JS = """
const box = { id: "b", shape: "box", size: [40, 30, 20], at: [5, -3, 10], rotate: [0, 0, 90], mirror: true };
const read = G.readDesign(JSON.stringify({ parts: [{ id: "n", shape: "box", fillet: { "Front_Top": 1, "LEFT back": 2,
  top: 3, vertical: 1 }, chamfer: { "bottom left": 0.5, all: 0.2 } }, { id: "o", shape: "outline", points: [[0, 0], [9, 0], [0, 9]],
  fillet: { corners: 1 } }, { id: "w", shape: "wedge", chamfer: { "slope front": 1 } }] }));
const n = read.design.parts[0];
const at = (p, v) => { const m = G.rotation(p.rotate); const l = [p.mirror ? -v[0] : v[0], v[1], v[2]];
  const w = G.apply(m, l); return [w[0] + p.at[0], w[1] + p.at[1], w[2] + p.at[2]]; };
const ring = { id: "c", shape: "cylinder", size: [10, 10, 4], at: [20, 0, 2], rotate: [0, 0, 0], repeat: { count: 4, around: [0, 0] } };
process.stdout.write(JSON.stringify({
  names: [n.fillet, n.chamfer, read.design.parts[1].fillet, read.design.parts[2].chamfer],
  plan: G.edgePlan(n), problems: read.problems,
  near: [G.nearestEdge(box, at(box, [-19.6, 0, 9.8])), G.nearestEdge(box, at(box, [20, 15, 0])),
         G.nearestEdge(box, at(box, [-20, -15, 3]))],
  copy: G.nearestEdge(ring, G.copyOf(ring, 2, [24.9, 0, 3.9])),
  middle: G.nearestEdge(ring, G.copyOf(ring, 2, [20, 0, 4])),
  wedge: G.nearestEdge({ id: "w", shape: "wedge", size: [40, 30, 20], at: [0, 0, 10], rotate: [0, 0, 0] }, [0, -15, 10]),
  ties: G.edgePlan({ ...box, fillet: { top: 2, front: 3 }, chamfer: { top: 1, left: 4 } }),
}));
"""


def test_edge_names_and_picking_an_edge(h3):
    """Edge names as a model may write them; which name wins; the editor's Pick edges on a turned, mirrored and
    repeated part."""
    out = run_js(EDGE_JS, {})
    fillet, chamfer, corners, wedge = out["names"]
    assert fillet == {"top-front": 1, "back-left": 2, "top": 3, "vertical": 1} and chamfer == {"bottom-left": 0.5, "all": 0.2}
    assert corners == {"vertical": 1} and wedge == {"front-slope": 1} and out["problems"] == []
    plan = out["plan"]
    assert plan["top-front"] == {"kind": "fillet", "size": 1}           # the edge itself over its face
    assert plan["top-back"] == {"kind": "fillet", "size": 3}            # the face over all
    assert plan["bottom-left"] == {"kind": "chamfer", "size": 0.5} and plan["bottom-front"] == {"kind": "chamfer", "size": 0.2}
    assert plan["back-left"] == {"kind": "fillet", "size": 2} and plan["front-right"] == {"kind": "fillet", "size": 1}
    py = h3._edge_plan(h3.read_design(json.dumps({"parts": [{"id": "n", "shape": "box", "fillet": fillet,
                                                               "chamfer": chamfer}]}))[0]["parts"][0])
    assert {k: {"kind": v[0], "size": v[1]} for k, v in py.items()} == plan
    ties = out["ties"]                     # as exact: a fillet over a chamfer, then the larger
    assert ties["top-front"] == {"kind": "fillet", "size": 3} and ties["top-back"] == {"kind": "fillet", "size": 2}
    assert ties["top-left"] == {"kind": "fillet", "size": 2} and ties["back-left"] == {"kind": "chamfer", "size": 4}
    py = h3._edge_plan({"shape": "box", "fillet": {"top": 2, "front": 3}, "chamfer": {"top": 1, "left": 4}})
    assert {k: {"kind": v[0], "size": v[1]} for k, v in py.items()} == ties
    near = [x["edge"] for x in out["near"]]
    assert near == ["top-left", "back-right", "front-left"], out["near"]   # in its own frame: mirrored, turned 90
    assert out["copy"]["edge"] == "top" and out["copy"]["distance"] < 0.2 and out["wedge"]["edge"] == "front-slope"
    # 0.94.0: measured to the rim itself - the middle of a cylinder's top is its radius from it, so a click there picks
    # the part, not the line
    assert out["middle"]["edge"] == "top" and out["middle"]["distance"] == pytest.approx(5, abs=0.01)


# 0.94.0, Søren: "Under lines, skal jeg have mulighed for at trykke 'Resize' på en linie, og så skal der komme en input
# box op, hvor jeg kan taste den længde ind jeg vil have. Enter laver det om."
LINES_JS = """
const box = { id: "b", shape: "box", size: [40, 30, 20], at: [5, -3, 10], rotate: [0, 0, 90], mirror: true };
const wedge = { id: "w", shape: "wedge", size: [40, 30, 20], at: [0, 0, 10], rotate: [0, 0, 0] };
const cyl = { id: "c", shape: "cylinder", size: [10, 10, 4], at: [20, 0, 2], rotate: [0, 0, 0] };
const cone = { id: "k", shape: "cone", size: [20, 20, 10], top: 0.5, at: [0, 0, 5], rotate: [0, 0, 0] };
const outline = { id: "o", shape: "outline", points: [[0, 0], [9, 0], [9, 6], [0, 6]], size: [0, 0, 3], at: [0, 0, 1.5], rotate: [0, 0, 0] };
const lying = { ...box, rotate: [90, 0, 0], at: [0, 0, 15] };       // its own Y is up: its bottom stays all the same
const r = (p, e, v) => { const q = G.withEdgeLength(p, e, v); return typeof q === "string" ? q
  : { size: q.size.map((x) => Math.round(x * 1e6) / 1e6), at: q.at.map((x) => Math.round(x * 1e6) / 1e6), top: q.top ?? null,
      bottom: Math.round(G.extent(q)[0][2] * 1e6) / 1e6 }; };
process.stdout.write(JSON.stringify({
  lengths: [G.edgeLength(box, "top-front"), G.edgeLength(box, "top-left"), G.edgeLength(box, "front-left"),
            G.edgeLength(wedge, "front-slope"), G.edgeLength(cyl, "top"), G.edgeLength(cone, "top"),
            G.edgeLength(cone, "bottom"), G.edgeLength(outline, "vertical"), G.edgeLength(box, "nonsense"),
            G.edgeLength({ ...cone, top: 0 }, "top")],
  x: r(box, "top-front", 50), y: r(box, "bottom-left", 12), z: r(box, "front-left", 30), lying: r(lying, "top-left", 40),
  slope: r(wedge, "front-slope", 2 * Math.hypot(40, 20)), wedgeUp: r(wedge, "left-front", 5),
  rim: r(cyl, "bottom", 20), coneBottom: r(cone, "bottom", 40), coneTop: r(cone, "top", 16), tooWide: r(cone, "top", 30),
  thick: r(outline, "vertical", 5), short: r(box, "top-front", 0), none: r(box, "nonsense", 5),
}));
"""


def test_resizing_a_line(h3):
    """Resize (0.94.0): a picked line made as long as typed - a box's or wedge's edge, the size along it (a slope its run
    and rise alike); a rim, its diameter (a cone's other rim keeping its own); an outline's corners, its thickness - the
    part's bottom staying where it was."""
    out = run_js(LINES_JS, {})
    L = out["lengths"]
    assert [x and (round(x["length"], 6), x["what"]) for x in L[:8]] == [
        (40, "Length"), (30, "Length"), (20, "Length"), (round(math.hypot(40, 20), 6), "Length"), (10, "Diameter"),
        (10, "Diameter"), (20, "Diameter"), (3, "Length")]
    assert L[8] is None and L[9] is None                 # no such line; a cone's point has no rim
    assert out["x"] == {"size": [50, 30, 20], "at": [5, -3, 10], "top": None, "bottom": 0}
    assert out["y"]["size"] == [40, 12, 20] and out["y"]["at"] == [5, -3, 10]
    assert out["z"] == {"size": [40, 30, 30], "at": [5, -3, 15], "top": None, "bottom": 0}      # taller, on the plate still
    assert out["lying"]["size"] == [40, 40, 20] and out["lying"]["bottom"] == 0 and out["lying"]["at"][2] == 20
    assert out["slope"]["size"] == [80, 30, 40] and out["slope"]["bottom"] == 0
    assert out["wedgeUp"]["size"] == [40, 30, 5] and out["wedgeUp"]["bottom"] == 0
    assert out["rim"]["size"] == [20, 20, 4]
    assert out["coneBottom"]["size"] == [40, 40, 10] and out["coneBottom"]["top"] == 0.25        # its top stays 10 across
    assert out["coneTop"]["top"] == 0.8 and out["coneTop"]["size"] == [20, 20, 10]
    assert "cannot be wider than the bottom" in out["tooWide"]
    assert out["thick"]["size"] == [0, 0, 5] and out["thick"]["bottom"] == 0
    assert "too short" in out["short"] and "no length" in out["none"]


# 0.92.0: "I would like to pick a line FIRST, and then pick what happens to it (resize, fillet and so on)". The editor
# keeps picked edges by part; Fillet, Chamfer and Take off rewrite the part's names with treatEdges.
TREAT_JS = """
const box = { id: "b", shape: "box", size: [40, 30, 20], at: [5, -3, 10], rotate: [0, 0, 90], mirror: true };
const wedge = { id: "w", shape: "wedge", size: [40, 30, 20], at: [0, 0, 10], rotate: [0, 0, 0] };
const cyl = { id: "c", shape: "cylinder", size: [10, 10, 4], at: [20, 0, 2], rotate: [0, 0, 0] };
const outline = { id: "o", shape: "outline", points: [[0, 0], [9, 0], [9, 6], [0, 6]], size: [0, 0, 3], at: [0, 0, 1.5], rotate: [0, 0, 0] };
const pick = (p, edges, spec) => { const q = G.treatEdges(p, edges, spec); return { fillet: q.fillet ?? null, chamfer: q.chamfer ?? null }; };
const fillet = (size) => ({ kind: "fillet", size }), chamfer = (size) => ({ kind: "chamfer", size });
// every way of treating a box's or wedge's edges - two kinds, three sizes or none - comes back as the same plan
let seed = 7, wrong = 0, tried = 0;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
for (const p of [box, wedge]) for (let n = 0; n < 400; n++) {
  const plan = {};
  for (const e of G.EDGES[p.shape]) { const r = rnd(); if (r < 0.45) plan[e] = { kind: rnd() < 0.5 ? "fillet" : "chamfer", size: [1, 2, 3][Math.floor(rnd() * 3)] }; }
  const back = G.edgePlan({ ...p, ...G.compactEdges(p.shape, plan) });
  tried++;
  if (JSON.stringify(Object.entries(back).sort()) !== JSON.stringify(Object.entries(plan).sort())) wrong++;
}
const lines = (p, names) => G.edgeLines(p, names).map((l) => l.map((v) => v.map((x) => Math.round(x * 1e6) / 1e6)));
const at = (p, v) => { const w = G.apply(G.rotation(p.rotate), [p.mirror ? -v[0] : v[0], v[1], v[2]]);
  return [w[0] + p.at[0], w[1] + p.at[1], w[2] + p.at[2]].map((x) => Math.round(x * 1e6) / 1e6); };
const ring = { ...cyl, sides: 8, repeat: { count: 4, around: [0, 0] } };
process.stdout.write(JSON.stringify({
  wrong, tried,
  top: pick({ ...box, fillet: { "top-front": 2, front: 1 }, chamfer: { top: 1 } }, G.edgesOf("box", "top"), fillet(3)),
  all: pick({ ...box, fillet: { "top-front": 2 }, chamfer: { bottom: 1 } }, G.EDGES.box, chamfer(0.5)),
  off: pick({ ...box, fillet: { top: 2 } }, ["top-front"], null),
  none: pick({ ...box, fillet: { top: 2 } }, G.edgesOf("box", "top"), null),
  vertical: pick(box, G.edgesOf("box", "vertical"), fillet(4)),
  mixed: pick(box, ["top-front", "top-back", "top-left", "top-right", "front-left"], chamfer(1)),
  slope: pick(wedge, G.edgesOf("wedge", "slope"), chamfer(1)),
  rims: pick(cyl, ["top", "bottom"], fillet(1)),
  corners: pick(outline, ["vertical"], fillet(1)),
  resized: pick({ ...box, fillet: { vertical: 4, "top-front": 4 }, chamfer: { bottom: 1 } }, ["front-left", "top-front"], fillet(2)),
  topFront: lines(box, ["top-front"]), ends: [at(box, [-20, -15, 10]), at(box, [20, -15, 10])],
  slopeLines: lines(wedge, ["front-slope", "back-slope"]).length,
  rings: lines(ring, ["top"]), corner: lines(outline, ["vertical"]),
  unknown: lines(box, ["nonsense", "top", "top-back"]).length,
}));
"""


def test_edges_picked_first_then_treated(h3):
    """Pick edges first (0.92.0): treatEdges sets or takes off a fillet or chamfer on just the picked edges and writes
    the part's names afresh - the fewest, none overlapping - so every plan reads back as it was, in both readers;
    edgeLines draws the picked edges where they are."""
    out = run_js(TREAT_JS, {})
    assert out["wrong"] == 0 and out["tried"] == 800
    assert out["top"] == {"fillet": {"top": 3, "bottom-front": 1, "front-left": 1, "front-right": 1}, "chamfer": None}
    assert out["all"] == {"fillet": None, "chamfer": {"all": 0.5}}
    assert out["off"] == {"fillet": {"top-back": 2, "top-left": 2, "top-right": 2}, "chamfer": None}
    assert out["none"] == {"fillet": None, "chamfer": None}
    assert out["vertical"] == {"fillet": {"vertical": 4}, "chamfer": None}
    assert out["mixed"] == {"fillet": None, "chamfer": {"top": 1, "front-left": 1}}
    assert out["slope"] == {"fillet": None, "chamfer": {"slope": 1}}
    assert out["rims"] == {"fillet": {"all": 1}, "chamfer": None} and out["corners"] == {"fillet": {"vertical": 1}, "chamfer": None}
    r = out["resized"]                        # a new size for two of them: the rest keep theirs
    assert r == {"fillet": {"top-front": 2, "front-left": 2, "front-right": 4, "back-left": 4, "back-right": 4},
                 "chamfer": {"bottom": 1}}, r
    f, c = (lambda n: ("fillet", n)), (lambda n: ("chamfer", n))
    tops = ["top-front", "top-back", "top-left", "top-right"]
    for got, plan in ((out["top"], {**{e: f(3) for e in tops}, "bottom-front": f(1), "front-left": f(1), "front-right": f(1)}),
                      (out["mixed"], {**{e: c(1) for e in tops}, "front-left": c(1)}),
                      (out["resized"], {"top-front": f(2), "front-left": f(2), "front-right": f(4), "back-left": f(4),
                                        "back-right": f(4), **{e: c(1) for e in h3.EDGES["box"] if e.startswith("bottom")}})):
        part = {"id": "b", "shape": "box", "size": [40, 30, 20], **{k: v for k, v in got.items() if v}}
        design, problems = h3.read_design(json.dumps({"parts": [part]}))     # hub3d reads what the editor writes
        assert problems == [] and h3._edge_plan(design["parts"][0]) == plan, got
    assert out["topFront"] == [out["ends"]]                        # turned 90 and mirrored, as it is drawn
    assert out["slopeLines"] == 2 and out["unknown"] == 1              # edges by their own names only
    rings = out["rings"]                       # a cylinder's top rim on each of its four copies, round the plate's middle
    assert len(rings) == 4 and all(len(r) == 9 for r in rings)
    assert all(v[2] == 4 for r in rings for v in r)
    assert np.allclose(np.mean([v[:2] for v in rings[0][:8]], 0), [20, 0], atol=1e-6)
    assert np.allclose(np.mean([v[:2] for v in rings[2][:8]], 0), [-20, 0], atol=1e-6)
    assert len(out["corner"]) == 4 and all(line[0][2] == 0 and line[1][2] == 3 for line in out["corner"])


# 0.92.0: "Is it not possible to pick a single letter and change it - by clicking it?" Letters apart makes a text a
# text part per letter, each where it was: the solid is the same.
APART = [
    {"parts": [{"id": "t", "shape": "text", "text": "Søren 08", "size": [0, 12, 2], "at": [0, 0, 1]}]},
    {"parts": [{"id": "t", "shape": "text", "text": "Ab\ncDé", "font": "serif", "bold": True, "italic": True,
                "align": "right", "letter_spacing": 1.3, "line_spacing": 0.8, "size": [0, 25, 2], "at": [3, 4, 1]}]},
    {"parts": [{"id": "t", "shape": "text", "text": "HUB", "font": "stencil", "size": [60, 10, 1.5], "at": [0, 0, 0.75]}]},
    {"parts": [{"id": "t", "shape": "text", "text": "Stående", "font": "rounded", "italic": True, "size": [0, 8, 1],
                "rotate": [90, 0, 30], "at": [5, -3, 8], "mirror": True, "color": "#c0392b"}]},
    {"parts": [{"id": "t", "shape": "text", "text": "Søren", "font": "script", "size": [0, 15, 2], "at": [0, 0, 1]}]},
    {"parts": [{"id": "t", "shape": "text", "text": "AB", "size": [0, 6, 1], "at": [30, 0, 0.5],
                "repeat": {"count": 3, "angle": 90}}]},
    {"parts": [{"id": "plate", "shape": "box", "size": [80, 30, 3], "at": [0, 0, 1.5]},
               {"id": "t", "shape": "text", "text": "CUT", "font": "mono", "size": [50, 0, 4], "at": [0, 0, 2],
                "hole": True}]},
]
APART_JS = """
const out = input.map((d) => {
  const { design } = G.readDesign(JSON.stringify(d));
  const kit = { wasm, fonts, meshes: new Map() };
  const t = design.parts.find((p) => p.id === "t");
  const letters = G.lettersApart(t, fonts, G.allIds(design.parts));
  const apart = { ...design, parts: design.parts.flatMap((p) => (p.id === "t" ? letters : [p])) };
  const file = G.writeDesign(apart);
  const a = G.build(design, kit), b = G.build(apart, kit), c = G.build(G.readDesign(file).design, kit);
  const both = G.build({ ...design, parts: [...design.parts, ...letters] }, kit);   // over the text: nothing more
  return { was: { volume: a.volume, box: a.box }, now: { volume: b.volume, box: b.box }, both: both.volume,
           saved: { volume: c.volume, box: c.box, triangles: c.triangles.length / 3, problems: c.problems.length }, letters, file };
});
process.stdout.write(JSON.stringify(out));
"""


def test_letters_apart_make_the_same_solid(h3):
    """Letters apart (0.92.0): each letter a text part of its own, just where it was - in any font and style, two
    lines aligned and spaced, stretched, turned and mirrored, joined-up script, repeated, as a hole - builds what the
    text built, in the editor and in hub3d."""
    pytest.importorskip("manifold3d")
    js = run_js(APART_JS, APART)
    for d, j in zip(APART, js):
        assert j["now"]["volume"] == pytest.approx(j["was"]["volume"], rel=1e-7), d
        assert np.allclose(j["now"]["box"], j["was"]["box"], atol=1e-6), d
        assert j["both"] == pytest.approx(j["was"]["volume"], rel=1e-6), d     # each letter just where it was
        saved = j["saved"]                            # the file keeps 0.001 mm
        assert saved["volume"] == pytest.approx(j["was"]["volume"], rel=1e-3) and np.allclose(saved["box"], j["was"]["box"], atol=2e-3)
        design, problems = h3.read_design(j["file"])          # and hub3d builds the letters the same
        b = h3.build_design(design, ".")
        mesh = b.solid.to_mesh()
        v = np.asarray(mesh.vert_properties)[:, :3]
        assert problems == [] and saved["problems"] == len(b.problems) == 0
        assert saved["triangles"] == len(mesh.tri_verts), d
        assert saved["volume"] == pytest.approx(b.solid.volume(), rel=1e-9, abs=1e-6)
        assert np.allclose(saved["box"], [v.min(0), v.max(0)], atol=1e-4)
    names = [[x["text"] for x in j["letters"]] for j in js]
    assert names[0] == list("Søren08") and names[1] == list("AbcDé") and names[4] == list("Søren")
    ids = [x["id"] for j in js for x in j["letters"]]
    assert all(i != "t" for i in ids) and len(set(ids[:7])) == 7
    assert all(x["size"][0] == 0 for x in js[0]["letters"])             # each one's width follows it
    assert all(x["size"][0] > 0 for x in js[2]["letters"])              # stretched: its own
    turned = js[3]["letters"][0]
    assert turned["rotate"] == [90, 0, 30] and turned["mirror"] and turned["italic"] and turned["color"] == "#c0392b"
    assert all(x["repeat"] == {"count": 3, "angle": 90, "around": [30, 0]} for x in js[5]["letters"])
    assert all(x.get("hole") for x in js[6]["letters"]) and js[6]["now"]["volume"] < 80 * 30 * 3
    js_tables = run_js("process.stdout.write(JSON.stringify({faces: G.EDGE_FACES, edges: G.EDGES}));", {})
    assert js_tables["faces"] == {k: list(v) for k, v in h3.EDGE_FACES.items()}
    assert js_tables["edges"] == {k: list(v) for k, v in h3.EDGES.items()}


# 0.95.0, Søren: "Jeg skulle gerne kunne vælge linierne inde i kassen også, så jeg kan lave fillet på dem" - a hole's
# lines inside the part it cuts are picked and rounded as a part's own are: a fillet on the cutter rounds the pocket's
# inside corners. Its lines at an opening are not (yet): there the cutter rounded would leave a lip round it.
BOX = {"id": "box", "shape": "box", "size": [100, 50, 40], "at": [0, 0, 20]}
POCKET = {"id": "h", "shape": "box", "size": [94, 44, 37], "at": [0, 0, 21.5], "hole": True}
TOPS = ["top-front", "top-back", "top-left", "top-right"]
HOLE_CASES = {
    "open": ([BOX, POCKET], TOPS),
    "tall": ([BOX, {**POCKET, "size": [94, 44, 197], "at": [0, 0, 101.5]}], TOPS),          # far out of the top
    "void": ([BOX, {**POCKET, "size": [80, 30, 20], "at": [0, 0, 20]}], []),                # shut in: all inside
    "turned": ([{**BOX, "rotate": [0, 0, 30]}, {**POCKET, "rotate": [0, 0, 30], "mirror": True}], TOPS),
    "slot": ([BOX, {"id": "h", "shape": "box", "size": [20, 80, 10], "at": [0, 0, 40], "hole": True}],   # across the top
             TOPS + ["bottom-front", "bottom-back", "front-left", "front-right", "back-left", "back-right"]),
    "drill": ([BOX, {"id": "h", "shape": "cylinder", "size": [8, 8, 30], "at": [30, 0, 30], "hole": True,
                     "repeat": {"count": 3, "step": [-30, 0, 0]}}], ["top"]),
    "ramp": ([BOX, {"id": "h", "shape": "wedge", "size": [40, 20, 20], "at": [0, 0, 30], "hole": True}], ["left-slope"]),
    "pocket": ([BOX, {"id": "h", "shape": "outline", "points": [[0, 0], [30, 0], [30, 20], [15, 10], [0, 20]],
                      "size": [0, 0, 20], "at": [-15, -10, 35], "hole": True}], []),
    "through": ([BOX, {"id": "h", "shape": "outline", "points": [[0, 0], [30, 0], [30, 20], [0, 20]], "size": [0, 0, 20],
                       "at": [35, -10, 35], "hole": True}], ["vertical"]),           # out of its side: not every corner
    "group": ([BOX, {"id": "g", "shape": "group", "hole": True, "parts": [{**POCKET, "hole": False}]}], TOPS),
}
HOLE_JS = """
const kit = { wasm, fonts, meshes: new Map() };
const read = (parts) => G.readDesign(JSON.stringify({ parts })).design;
const find = (ps, id) => { for (const p of ps) { if (p.id === id) return p; const r = p.parts && find(p.parts, id); if (r) return r; } return null; };
const out = { open: {} };
for (const [name, [parts]] of Object.entries(input)) {
  const d = read(parts), b = G.build(d, kit), h = find(d.parts, "h");
  out.open[name] = G.holeEdges(h, G.EDGES[h.shape], b).open;
}
// all twelve picked, Fillet: the eight inside get it
const d = read(input.open[0]), sharp = G.build(d, kit), h = find(d.parts, "h");
const split = G.holeEdges(h, G.EDGES.box, sharp);
const treated = G.treatEdges(h, split.inside, { kind: "fillet", size: 3 });
const as = (q) => G.build({ ...d, parts: d.parts.map((p) => (p.id === "h" ? q : p)) }, kit);
const inside = as(treated), all = as(G.treatEdges(h, G.EDGES.box, { kind: "fillet", size: 3 }));
out.inside = split.inside;
out.treated = { fillet: treated.fillet ?? null, chamfer: treated.chamfer ?? null };
out.volumes = { sharp: sharp.volume, inside: inside.volume, all: all.volume };
const lip = [0, -21.9, 39.9];                       // just in from the opening's front rim
out.lip = { inside: G.insideMesh(inside, lip), all: G.insideMesh(all, lip) };
// insideMesh against the open box worked out by hand, both turned 30 degrees
const t = read(input.turned[0].map((p) => ({ ...p, mirror: false }))), tb = G.build(t, kit);
const c = Math.cos(Math.PI / 6), s = Math.sin(Math.PI / 6);
let seed = 3, wrong = 0;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
for (let k = 0; k < 3000; k++) {
  const v = [(rnd() - 0.5) * 140, (rnd() - 0.5) * 140, rnd() * 50 - 5];
  const x = c * v[0] + s * v[1], y = -s * v[0] + c * v[1];
  const want = Math.abs(x) < 50 && Math.abs(y) < 25 && v[2] > 0 && v[2] < 40
    && !(Math.abs(x) < 47 && Math.abs(y) < 22 && v[2] > 3);
  if (G.insideMesh(tb, v) !== want) wrong++;
}
out.wrong = wrong;
out.near = G.nearestEdge({ ...d.parts[1], hole: true }, [10, -22.5, 41]);
const line = [[[0, -10, 0], [0, 10, 0]]];          // the pointer's ray: what is a few pixels from it on the screen
out.ray = [G.nearestToRay(line, [5, 0, 10], [0, 0, -1]), G.nearestToRay(line, [0, 20, 10], [0, 0, -1]),
           G.nearestToRay([[[0, 0, 0], [0, 0, 10]]], [3, 0, -5], [0, 0, 1]), G.nearestToRay([[[0, 0, 20], [10, 0, 20]]], [0, 0, 0], [0, 0, -1])];
process.stdout.write(JSON.stringify(out));
"""


def _find(parts: list, pid: str):
    for p in parts:
        if p["id"] == pid:
            return p
        got = _find(p.get("parts", []), pid)
        if got:
            return got
    return None


def test_a_holes_lines_inside_what_it_cuts(h3, tmp_path, capsys):
    """A hole's lines (0.95.0): those with the part it cuts just outside them - a pocket's upright corners and floor
    edges, a drilled hole's bottom, a shut-in void's every one - are inside; those at an opening (or out in the air) are
    not, and a fillet given only the inside ones rounds the pocket without the lip a fillet at the rim leaves. hub3d
    tells them apart the same way, and `hub3d design` warns of a hole's fillet at an opening."""
    pytest.importorskip("manifold3d")
    out = run_js(HOLE_JS, HOLE_CASES)
    for name, (parts, want) in HOLE_CASES.items():
        assert sorted(out["open"][name]) == sorted(want), (name, out["open"][name])
        design, _ = h3.read_design(json.dumps({"parts": parts}))
        m = h3.build_design(design, ".").solid.to_mesh()
        tri = np.asarray(m.vert_properties, dtype=np.float64)[:, :3][np.asarray(m.tri_verts)]
        p = _find(design["parts"], "h")
        _, py_open = h3.hole_edges(p, h3.EDGES[p["shape"]], tri)
        assert sorted(py_open) == sorted(want), (name, py_open)
    lipped = {"parts": [BOX, {**POCKET, "fillet": {"all": 3}}]}
    f = tmp_path / "kasse.3d.json"
    for d, warned in ((lipped, True), ({"parts": [BOX, {**POCKET, "fillet": {"vertical": 5, "bottom": 2}}]}, False)):
        f.write_text(json.dumps(d))
        h3.main(["design", str(f)])
        said = capsys.readouterr().out
        assert ("! hole h: its fillet on top-front, top-back, top-left, top-right is at an opening" in said) == warned, said
        assert ("READY for the slicer." in said) != warned
    assert sorted(out["inside"]) == sorted(e for e in h3.EDGES["box"] if e not in TOPS)
    assert out["treated"] == {"fillet": {"vertical": 3, "bottom": 3}, "chamfer": None}
    vol = out["volumes"]
    assert vol["sharp"] == pytest.approx(100 * 50 * 40 - 94 * 44 * 37)
    assert vol["sharp"] < vol["inside"] < vol["all"]           # round inside corners keep a little; a lip more
    assert out["lip"] == {"inside": False, "all": True}
    assert out["wrong"] == 0
    near = out["near"]
    assert near["edge"] == "top-front" and np.allclose(near["point"], [10, -22, 40]) and near["distance"] == pytest.approx(math.hypot(0.5, 1))
    across, beyond, along, behind = out["ray"]     # past it, past its end, along it, behind where the ray starts
    assert across["distance"] == pytest.approx(5) and np.allclose(across["point"], [0, 0, 0])
    assert beyond["distance"] == pytest.approx(10) and np.allclose(beyond["point"], [0, 10, 0])
    assert along["distance"] == pytest.approx(3) and behind["distance"] == pytest.approx(20)


# 0.97.0, Søren: "Når jeg sætter text ind, kan jeg så vælge hvilken flade den skal ligge på? Jeg kan heller ikke lige få
# den drejet til at sidde 90 grader, hvis jeg trækker den med musen" - On a face, and Turn's 90 degree steps.
FACE_JS = """
const kit = { wasm, fonts, meshes: new Map() };
const box = { id: "box", shape: "box", size: [100, 50, 50], at: [0, 0, 25], rotate: [0, 0, 0] };
const text = { id: "t", shape: "text", text: "Skruer", size: [0, 10, 2], at: [70, 0, 1], rotate: [0, 0, 30], mirror: true };
const r = (v) => v.map((x) => Math.round(x * 1e4) / 1e4);
const boxOf = (p) => G.buildPart(p, kit).box;
const place = (p, point, normal, right = [1, 0, 0]) => G.onFace(p, point, normal, right, boxOf(p));
const built = (parts) => G.build({ format: G.FORMAT, version: 1, parts }, kit);
const placed = {
  front: place(text, [0, -25, 30], [0, -1, 0]), right: place(text, [50, 0, 30], [1, 0, 0]),
  top: place(text, [0, 0, 50], [0, 0, 1], [0, 1, 0]), under: place(text, [0, 0, 0], [0, 0, -1]),
  hole: place({ ...text, hole: true }, [0, -25, 30], [0, -1, 0]),
  slope: place(text, [0, 0, 30], [0.4472136, 0, 0.8944272]),
};
const out = { placed: {} };
for (const [name, p] of Object.entries(placed)) out.placed[name] = { rotate: r(p.rotate), at: r(p.at), mirror: p.mirror ?? null, box: boxOf(p).map(r) };
const both = built([box, placed.front]), alone = built([box]), t = built([placed.front]);
out.joined = [both.volume, alone.volume + t.volume];
const cut = built([box, placed.hole]);
out.cut = [cut.volume, alone.volume];
const g = { id: "g", shape: "group", parts: [{ id: "a", shape: "box", size: [20, 10, 4], at: [0, 0, 2], rotate: [0, 0, 0] },
  { id: "b", shape: "cylinder", size: [6, 6, 6], at: [5, 0, 7], rotate: [0, 0, 0] }] };
out.group = boxOf(place(g, [50, 0, 25], [1, 0, 0])).map(r);
const flat = { id: "f", shape: "text", text: "Hej", size: [0, 10, 2], at: [0, 0, 1], rotate: [0, 0, 0] };
const brick = { id: "k", shape: "box", size: [40, 20, 10], at: [0, 0, 5], rotate: [0, 0, 0] };
const high = { ...brick, at: [0, 0, 50] };
const turn = (p, axis, sign) => { const q = G.turned90(p, axis, sign, boxOf(p)); return { rotate: r(q.rotate ?? [0, 0, 0]), box: boxOf(q).map(r) }; };
out.turns = { stand: turn(flat, 0, 1), left: turn(brick, 2, 1), right: turn(brick, 2, -1), tip: turn(brick, 1, 1),
  air: turn(high, 0, -1), group: turn(g, 2, 1), back: G.turned90(G.turned90(brick, 0, 1), 0, -1).rotate };
process.stdout.write(JSON.stringify(out));
"""


def test_on_a_face_and_turned_90(h3):
    """On a face (0.97.0): a text put on a box's front, its right, its top, its underside or a slope is turned to lie
    on it reading across it (upright on a wall, mirrored no more), 0.2 mm into it so they join - a hole text sinks in
    as deep as it is thick; a group goes as one. Turn: 90 degrees about Z, X or Y, a part on the plate stays on it."""
    pytest.importorskip("manifold3d")
    out = run_js(FACE_JS, {})
    p = out["placed"]
    assert p["front"]["rotate"] == [90, 0, 0] and p["front"]["mirror"] is None
    (x0, y0, z0), (x1, y1, z1) = p["front"]["box"]
    assert (y0, y1) == (-26.8, -24.8) and x1 - x0 > z1 - z0 and abs((z0 + z1) / 2 - 30) < 0.5   # on it, across, upright
    assert p["right"]["rotate"] == [90, 0, 90]
    (x0, y0, z0), (x1, y1, z1) = p["right"]["box"]
    assert (x0, x1) == (49.8, 51.8) and y1 - y0 > z1 - z0
    assert p["top"]["rotate"] == [0, 0, 90] and p["top"]["box"][0][2] == 49.8 and p["top"]["box"][1][2] == 51.8
    assert p["under"]["rotate"] == [180, 0, 0] and p["under"]["box"][0][2] == -1.8 and p["under"]["box"][1][2] == 0.2
    assert p["hole"]["box"][0][1] == -25.2 and p["hole"]["box"][1][1] == -23.2                   # sunk 1.8 mm in
    n = np.array([0.4472136, 0, 0.8944272])                                                      # a slope: its normal
    lo, hi = np.array(p["slope"]["box"])
    assert np.dot((lo + hi) / 2 - np.array([0, 0, 30]), n) == pytest.approx(0.8, abs=0.05)
    joined, alone = out["joined"]
    assert joined < alone                                       # 0.2 mm into the box: one piece, not two touching
    cut, whole = out["cut"]
    assert cut < whole
    (x0, _, _), (x1, _, _) = out["group"]
    assert x0 == pytest.approx(49.8) and x1 == pytest.approx(49.8 + 10)                          # its 10 mm height now across
    t = out["turns"]
    assert t["stand"]["rotate"] == [90, 0, 0] and t["stand"]["box"][0][2] == 0                   # a flat text stands up
    assert t["stand"]["box"][1][1] - t["stand"]["box"][0][1] == pytest.approx(2, abs=1e-3)
    for name, rz in (("left", 90), ("right", -90)):
        (x0, y0, z0), (x1, y1, z1) = t[name]["box"]
        assert t[name]["rotate"] == [0, 0, rz] and (x1 - x0, y1 - y0, z0) == (20, 40, 0)
    (x0, y0, z0), (x1, y1, z1) = t["tip"]["box"]
    assert (x1 - x0, z1 - z0, z0) == (10, 40, 0)
    (x0, y0, z0), (x1, y1, z1) = t["air"]["box"]
    assert (z0 + z1) / 2 == 50 and (y1 - y0, z1 - z0) == (10, 20)                                 # in the air: its middle stays
    assert t["back"] == [0, 0, 0]
    (x0, y0, _), (x1, y1, _) = t["group"]["box"]
    assert (x1 - x0, y1 - y0) == pytest.approx((10, 20))


# ---------------------------------------------------------------- on a face of a box (0.98.0)

def faces_design(h3, box: dict) -> dict:
    """On each face of `box`: a round hole through it, from the first edge across and the second up; a square boss
    turned on it, from the second edge across, in the middle up; on the top, a hole on that boss - and a text on the
    front, in the middle."""
    parts = [box]
    for i, face in enumerate(h3.FACES):
        (lo0, hi0), (lo1, hi1) = h3.FACE_FRAMES[face][3]
        parts.append({"id": f"h-{face}", "shape": "cylinder", "size": [6, 6, 1], "hole": True, "sides": 32,
                      "on": {"part": "box", "face": face, "from": {lo0: 12 + i, hi1: 7}, "through": True}})
        parts.append({"id": f"s-{face}", "shape": "box", "size": [8, 5, 1],
                      "on": {"part": "box", "face": face, "from": {hi0: 9}, "depth": 3, "angle": 30 * i}})
    parts.append({"id": "pin", "shape": "cylinder", "size": [2, 2, 1], "hole": True, "sides": 16,
                  "on": {"part": "s-top", "face": "top", "depth": 2}})
    parts.append({"id": "word", "shape": "text", "text": "SLQ", "size": [0, 6, 1],
                  "on": {"part": "box", "face": "front", "depth": 0.8}})
    return {"parts": parts}


FACES_JS = """
const out = input.designs.map((d) => {
  const r = G.readDesign(JSON.stringify(d));
  const b = G.build(r.design, { wasm, fonts, meshes: new Map() });
  const again = G.readDesign(G.writeDesign(r.design));
  return { parts: r.design.parts, problems: r.problems, volume: b.volume, triangles: b.triangles.length / 3,
           box: b.box, text: G.writeDesign(r.design), again: G.writeDesign(again.design) };
});
process.stdout.write(JSON.stringify(out));
"""


def test_parts_on_a_face_are_placed_and_built_the_same_in_both(h3):
    """A part kept on a face of a box (0.98.0): the editor and hub3d put it in the same place - on each of the six faces
    of a box turned and mirrored, from either edge or the middle, turned on the face, through the box or so deep or so
    high, on a boss on the box - and build the same solid. The file keeps "on" on one line and reads back the same."""
    pytest.importorskip("manifold3d")
    plain = {"id": "box", "shape": "box", "size": [80, 40, 30], "at": [0, 0, 15]}
    turned = {"id": "box", "shape": "box", "size": [80, 40, 30], "at": [3, -2, 25], "rotate": [10, 20, 33], "mirror": True}
    designs = [faces_design(h3, plain), faces_design(h3, turned)]
    js = run_js(FACES_JS, {"designs": designs})
    for d, j in zip(designs, js):
        py, problems = h3.read_design(json.dumps(d))
        assert problems == [] and j["problems"] == []
        for a, b in zip(py["parts"], j["parts"]):
            assert a["id"] == b["id"] and a.get("on") == b.get("on")
            for key in ("at", "rotate", "size"):
                assert a[key] == pytest.approx(b[key], abs=1e-9), (a["id"], key, a[key], b[key])
        solid = h3.build_design(py).solid
        assert j["volume"] == pytest.approx(solid.volume(), rel=1e-9)
        assert j["triangles"] == len(solid.to_mesh().tri_verts)
        assert j["again"] == j["text"]                                   # read back: the same file
        assert '"on": {"part": "box", "face": "front", "from": {"left": 14, "top": 7}, "through": true}' in j["text"]
    by = {p["id"]: p for p in js[0]["parts"]}
    assert by["h-front"]["at"] == [-26, 0, 23] and by["h-front"]["size"] == [6, 6, 40.4]     # 14 from left, 7 from top
    assert by["h-front"]["rotate"] == [90, 0, 0]                                             # its Z out of the front
    assert by["h-top"]["at"] == [-28, 13, 15] and by["h-top"]["size"][2] == 30.4             # 12 from left, 7 from back
    assert by["h-right"]["at"] == [0, -3, 23] and by["h-right"]["size"][2] == 80.4           # 17 from the front
    s = by["s-top"]                                                       # 9 from the right, the middle front to back
    assert s["at"] == [31, 0, 31.4] and s["size"] == [8, 5, 3.2] and s["rotate"] == [0, 0, 0]
    assert by["s-bottom"]["rotate"] == [180, 0, -30]                     # turned 30 degrees on the underside
    assert by["pin"]["at"] == [31, 0, 32.1] and by["pin"]["size"][2] == 2.2   # in the boss's top, 2 mm deep
    w = by["word"]
    assert w["at"] == [0, -20.3, 15] and w["rotate"] == [90, 0, 0] and w["size"][2] == 1  # 0.8 out of the front's middle
    hollow = js[0]["volume"]
    whole = h3.build_design(h3.read_design(json.dumps({"parts": [plain]}))[0]).solid.volume()
    assert hollow != pytest.approx(whole)


EDITS_JS = """
const box = { id: "b", shape: "box", size: [80, 40, 30], at: [0, 0, 15], rotate: [0, 0, 0] };
const hole = { id: "h", shape: "cylinder", size: [6, 6, 1], at: [0, 0, 0], rotate: [0, 0, 0], hole: true, sides: 32,
  on: { part: "b", face: "front", from: { left: 20, top: 10 }, depth: 5 } };
const get = (ps, id) => { for (const p of ps) { if (p.id === id) return p; const q = p.parts && get(p.parts, id); if (q) return q; } };
const edit = (ps, id, fn) => ps.map((p) => (p.id === id ? fn(p) : p.parts ? { ...p, parts: edit(p.parts, id, fn) } : p));
const step = (ps, id, fn) => G.settle(edit(ps, id, fn), undefined, ps);
const out = {};
let cur = G.settle([box, hole]);
out.placed = get(cur, "h");
out.moved = get(step(cur, "h", (p) => G.transformPart(p, [0, 0, 0], [5, 3, -2], null)), "h");
const boxMoved = step(cur, "b", (p) => G.transformPart(p, [0, 0, 0], [10, 0, 0], null));
out.boxMoved = get(boxMoved, "h");
const both = G.settle(cur.map((p) => G.transformPart(p, [0, 0, 0], [7, 7, 7], null)), undefined, cur);
out.both = get(both, "h");
const boxTurned = step(cur, "b", (p) => G.transformPart(p, p.at, [0, 0, 0], G.rotation([0, 0, 90])));
out.boxTurned = get(boxTurned, "h");
out.boxGrown = get(step(cur, "b", (p) => ({ ...p, size: [100, 50, 40] })), "h");
out.spun = get(step(cur, "h", (p) => G.transformPart(p, p.at, [0, 0, 0], G.rotation([0, 90, 0]))), "h");
out.tipped = get(step(cur, "h", (p) => G.turned90(p, 2, 1)), "h");
out.deeper = get(step(cur, "h", (p) => ({ ...p, size: [6, 6, 8] })), "h");
out.typed = get(step(cur, "h", (p) => ({ ...p, on: { ...p.on, from: { right: 5, bottom: 4 } } })), "h");
out.gone = get(G.settle(cur.filter((p) => p.id !== "b"), undefined, cur), "h");
const through = G.settle([box, { ...hole, on: { part: "b", face: "front", through: true } }]);
out.through = get(through, "h");
out.throughGrown = get(step(through, "b", (p) => ({ ...p, size: [80, 60, 30] })), "h");
out.mirrored = get(step(cur, "h", (p) => G.mirrorPart(p, 0, [0, 0, 0])), "h");
// a copy respotted where it was put; riders; insertAfter in a group
out.copy = G.respot(G.transformPart({ ...out.placed, id: "h2" }, [0, 0, 0], [5, 5, 0], null), box).on;
const boss = { id: "s", shape: "box", size: [8, 8, 1], at: [0, 0, 0], rotate: [0, 0, 0], on: { part: "b", face: "top", depth: 4 } };
const pin = { id: "p", shape: "cylinder", size: [2, 2, 1], at: [0, 0, 0], rotate: [0, 0, 0], hole: true,
  on: { part: "s", face: "top", depth: 2 } };
const tree = [{ id: "g", shape: "group", size: [0, 0, 0], at: [0, 0, 0], rotate: [0, 0, 0], parts: [box, boss] }, pin, hole];
out.riders = T.riders(tree, ["g"]).map((p) => p.id);
out.inserted = T.insertAfter(tree, "b", { id: "new", shape: "box" })[0].parts.map((p) => p.id);
// the face a click is on, the spot it is at, a text set upright
const tb = { ...box, rotate: [0, 0, 30] };
const FR = G.faceFrame(tb, "front");
const at = FR.at(-13.3, 7.6);
out.clicked = [G.faceAt(tb, at, FR.N), G.spotAt(tb, "front", at), G.faceAt(tb, at, [0, 0, 1]),
  G.faceAt(tb, [at[0] - 10 * FR.N[0], at[1] - 10 * FR.N[1], at[2]], FR.N)];
out.angles = [G.faceAngle(box, "top", [0, -1, 0]), G.faceAngle(box, "top", [1, 0, 0]),
  G.faceAngle({ ...box, rotate: [180, 0, 0] }, "front", [1, 0, 0]), G.faceAngle(box, "right", [0, 0, 1])];
out.marks = G.faceMarks(box, "front", { left: 20, top: 10 }, true, 6).dims;
process.stdout.write(JSON.stringify(out));
"""


def test_a_part_on_a_face_goes_with_its_box_and_keeps_what_is_done_to_it(tmp_path):
    """In the editor (0.98.0): a box moved, turned or grown takes what is on its faces with it, the distances kept; a
    part on a face moved is moved on it (its distances follow, out of the face it goes back onto it), turned about the
    face's normal keeps the turn, turned off the face is free, sized in Z gets that depth; its box gone, it stays. A
    click on a box's face picks the face and the spot; a text on a face is set upright."""
    tree = tmp_path / "tree.ts"                         # tree.ts, its geometry import a path node finds
    tree.write_text(GEOMETRY.with_name("tree.ts").read_text(encoding="utf-8").replace('from "./geometry"',
                    f"from {json.dumps(str(GEOMETRY))}"), encoding="utf-8")
    out = run_js(f"import * as T from {json.dumps(str(tree))};\n" + EDITS_JS, {})
    assert out["placed"]["at"] == [-20, -17.6, 20] and out["placed"]["size"] == [6, 6, 5.2]   # 5 deep, 0.2 out
    assert out["moved"]["on"]["from"] == {"left": 25, "top": 12} and out["moved"]["at"] == [-15, -17.6, 18]
    assert out["boxMoved"]["at"] == [-10, -17.6, 20] and out["boxMoved"]["on"]["from"] == {"left": 20, "top": 10}
    assert out["both"]["on"]["from"] == {"left": 20, "top": 10} and out["both"]["at"] == [-13, -10.6, 27]
    assert out["boxTurned"]["at"] == [17.6, -20, 20] and out["boxTurned"]["rotate"] == [90, 0, 90]
    assert out["boxGrown"]["at"] == [-30, -22.6, 25]                       # 20 from the left, 10 from the top still
    assert "on" not in out["tipped"] and out["tipped"]["rotate"] == [90, 0, 90]   # turned off the face: free
    assert out["spun"]["on"]["angle"] == -90 and out["spun"]["at"] == out["placed"]["at"]   # turned on it: kept
    assert out["deeper"]["on"]["depth"] == 7.8 and out["deeper"]["size"][2] == 8
    assert out["typed"]["at"] == [35, -17.6, 4]
    assert out["gone"]["at"] == out["placed"]["at"] and "on" not in out["gone"]
    assert out["through"]["at"] == [0, 0, 15] and out["through"]["size"][2] == 40.4          # the middle of the front
    assert out["throughGrown"]["size"][2] == 60.4
    assert out["mirrored"]["on"]["from"] == {"left": 60, "top": 10} and "mirror" not in out["mirrored"]
    assert out["copy"]["from"] == {"left": 25, "top": 10}                  # 5 mm on across the face; its 5 in y is out
    assert out["riders"] == ["p", "h"] and out["inserted"] == ["b", "new", "s"]
    face, spot, wrong, inside = out["clicked"]
    assert face == "front" and spot == {"left": 27, "top": 7} and wrong is None and inside is None
    assert out["angles"] == [-90, 0, 180, 0]
    assert [d["mm"] for d in out["marks"]] == [20, 10]


ODD_ON = {"parts": [
    {"id": "b", "shape": "box", "size": [40, 20, 10], "at": [0, 0, 5]},
    {"id": "c", "shape": "cylinder", "size": [10, 10, 10], "at": [50, 0, 5]},
    {"id": "f1", "shape": "box", "on": {"part": "b", "face": "side"}},
    {"id": "f2", "shape": "box", "on": {"part": "b", "face": "Top", "from": 3, "x": 1}},
    {"id": "f3", "shape": "box", "on": {"part": "b", "face": "top", "from": {"left": 1, "right": 2, "up": 3, "front": "a"}}},
    {"id": "f4", "shape": "box", "on": {"part": "b", "face": "front", "depth": -1, "angle": "x"}},
    {"id": "f5", "shape": "box", "on": None},
    {"id": "f6", "shape": "group", "on": {"part": "b", "face": "top"}, "parts": []},
    {"id": "f7", "shape": "box", "on": {"part": "nope", "face": "top"}},
    {"id": "f8", "shape": "box", "on": {"part": "c", "face": "top"}},
    {"id": "f9", "shape": "box", "on": {"part": "f10", "face": "top"}},
    {"id": "f10", "shape": "box", "on": {"part": "f9", "face": "top"}},
    {"id": "f11", "shape": "box", "size": [4, 4, 3], "on": {"part": "b", "face": "top", "angle": 720}},
]}


def test_both_read_on_the_same_and_say_the_same(h3):
    js = run_js("const r = G.readDesign(JSON.stringify(input)); "
                "process.stdout.write(JSON.stringify({parts: r.design.parts, problems: r.problems}));", ODD_ON)
    py, problems = h3.read_design(json.dumps(ODD_ON))
    assert [x.rstrip(".") for x in js["problems"]] == problems, (js["problems"], problems)
    assert [x.split(":")[0] for x in problems] == ["f1", "f2", "f2", "f3", "f3", "f3", "f4", "f4", "f5", "f6", "f7",
                                                 "f8", "f9"]
    assert 'f3: "from" is mm from an edge - "front" is not a number; left out' in problems
    for a, b in zip(flat(py["parts"]), flat(js["parts"])):
        assert a.get("on") == b.get("on") and a["at"] == pytest.approx(b["at"]) and a["rotate"] == pytest.approx(b["rotate"])
    by = {p["id"]: p for p in py["parts"]}
    assert by["f2"]["on"] == {"part": "b", "face": "top", "depth": 20}       # its own size z: as high as it is
    assert by["f3"]["on"]["from"] == {"left": 1}
    assert "on" not in by["f9"] and by["f10"]["on"]["part"] == "f9"          # round in a circle: the first freed
    assert by["f11"]["on"] == {"part": "b", "face": "top", "depth": 3} and by["f11"]["at"] == [0, 0, 11.4]
    assert 'on        kept on a face of a box' in h3.DESIGN_GUIDE


# ---------------------------------------------------------------- the sketch (0.100.0)

def node_module(tmp_path, name: str) -> str:
    """A module of the editor's beside geometry.ts, its "./geometry" import a path node finds."""
    out = tmp_path / name
    out.write_text(GEOMETRY.with_name(name).read_text(encoding="utf-8").replace('from "./geometry"',
                   f"from {json.dumps(str(GEOMETRY))}"), encoding="utf-8")
    return json.dumps(str(out))


SKETCH_JS = """
const rect = { kind: "rect", a: [0, 0], b: [40, 20] }, hole = { kind: "circle", c: [10, 10], r: 4 };
const island = { kind: "circle", c: [10, 10], r: 2 }, apart = { kind: "circle", c: [60, 10], r: 5 };
const cross = { kind: "rect", a: [35, 15], b: [50, 30] }, open = { kind: "line", pts: [[0, 50], [10, 50]], closed: false };
const tri = { kind: "line", pts: [[0, 30], [10, 30], [5, 40]], closed: true };
const out = {};
out.regions = S.sketchRegions([rect, hole, island, apart, cross, tri, open]).map((r) => [r.outer.length, r.holes.length]);
let n = 0;
const id = () => `s${++n}`;
const make = (plane, shapes, box, how) => S.sketchParts(plane, shapes, box, { cut: false, depth: 5, through: false, reach: 100, ...how },
  id, "Sketch 1", "#f80");
out.planes = ["top", "front", "right"].map((kind) => make({ kind }, [rect, hole], null, {})[0]);
out.cut = make({ kind: "top" }, [rect], null, { cut: true, depth: 3 })[0];
out.through = make({ kind: "top" }, [rect], null, { cut: true, through: true })[0];
out.names = make({ kind: "top" }, [rect, apart, tri], null, {}).map((p) => p.name);
const plain = { id: "b", shape: "box", size: [80, 40, 30], at: [0, 0, 15], rotate: [0, 0, 0] };
const turned = { id: "b", shape: "box", size: [80, 40, 30], at: [5, 3, 25], rotate: [10, 20, 33], mirror: true };
out.faces = [];
for (const box of [plain, turned]) for (const face of ["front", "top", "left", "bottom"]) {
  const plane = { kind: "face", part: "b", face }, f = S.planeFrame(plane, box);
  const parts = make(plane, [{ kind: "circle", c: [12.5, -6], r: 3 }], box, { depth: 4 });
  const placed = G.settle([box, ...parts])[1];
  const want = S.to3d(f, [12.5, -6]).map((v, i) => v + f.N[i] * (4.2 / 2 - 0.2));
  out.faces.push(Math.max(...placed.at.map((v, i) => Math.abs(v - want[i]))));
}
out.middle = make({ kind: "face", part: "b", face: "front" }, [{ kind: "rect", a: [-5, -5], b: [5, 5] }], plain,
  { cut: true, through: true })[0].on;
// a ray from the camera onto a plane, back where it came from
const fr = S.planeFrame({ kind: "face", part: "b", face: "right" }, turned);
const spot = S.to3d(fr, [7, -3]);
out.ray = S.rayOnPlane(fr, [spot[0] + 50 * fr.N[0], spot[1] + 50 * fr.N[1], spot[2] + 50 * fr.N[2]],
  [-fr.N[0], -fr.N[1], -fr.N[2]]);
out.away = S.rayOnPlane(fr, spot.map((v, i) => v + 50 * fr.N[i]), fr.N);
out.dist = [S.distanceTo(rect, [20, -2]), S.distanceTo(hole, [10, 15]), S.distanceTo(open, [5, 53])];
out.snaps = S.snapPoints([rect, hole]).length;
// a face's sketch, cut and extruded, written as the editor writes it
const design = { format: "slq-hub-3d", version: 1, parts: [turned,
  ...make({ kind: "face", part: "b", face: "front" }, [{ kind: "rect", a: [-20, -8], b: [16, 9] }, { kind: "circle", c: [0, 0], r: 4 }],
    turned, { cut: true, depth: 6 }),
  ...make({ kind: "face", part: "b", face: "top" }, [{ kind: "line", pts: [[-10, -5], [12, -5], [0, 9]], closed: true }], turned, { depth: 3 })] };
const read = G.readDesign(JSON.stringify(design));
out.text = G.writeDesign(read.design);
out.parts = read.design.parts;
out.volume = G.build(read.design, { wasm, fonts, meshes: new Map() }).volume;
process.stdout.write(JSON.stringify(out));
"""


def test_a_sketch_makes_parts(h3, tmp_path):
    """A sketch (0.100.0): a closed shape in another is a hole, one in that hole an island, shapes apart or crossing a
    part each, an open line nothing; extruded on the plate it stands on it, on the front or right plane it stands out
    toward you, cut it goes into what is there; on a box's face - turned and mirrored too - each part is kept there
    where it was drawn, the middle staying the middle; and hub3d reads and builds the parts the same."""
    pytest.importorskip("manifold3d")
    out = run_js(f"import * as S from {node_module(tmp_path, 'sketch.ts')};\n" + SKETCH_JS, {})
    assert out["regions"] == [[4, 1], [64, 0], [64, 0], [4, 0], [3, 0]]       # the rect with its hole, the island ...
    top, front, right = out["planes"]
    assert top["at"] == [20, 10, 2.5] and top["rotate"] == [0, 0, 0] and top["size"] == [0, 0, 5]
    assert top["points"] == [[-20, -10], [20, -10], [20, 10], [-20, 10]] and len(top["holes"]) == 1
    assert front["at"] == [20, -2.5, 10] and front["rotate"] == [90, 0, 0]
    assert right["at"] == [2.5, 20, 10] and right["rotate"] == [90, 0, 90]
    assert out["cut"]["hole"] and out["cut"]["at"] == [20, 10, 1.4] and out["cut"]["size"] == [0, 0, 3.2]
    assert out["through"]["at"] == [20, 10, 0] and out["through"]["size"] == [0, 0, 200.4]
    assert out["names"] == ["Sketch 1", "Sketch 1 (2)", "Sketch 1 (3)"]
    assert max(out["faces"]) < 1e-3                                           # where it was drawn, on every face
    assert out["middle"] == {"part": "b", "face": "front", "through": True}   # the middle: no distances
    assert out["ray"] == pytest.approx([7, -3]) and out["away"] is None
    assert out["dist"] == pytest.approx([2, 1, 3]) and out["snaps"] == 6
    assert '"on": {"part": "b", "face": "front", "from": {' in out["text"]
    py, problems = h3.read_design(out["text"])
    assert problems == []
    for a, b in zip(py["parts"], out["parts"]):
        assert a.get("on") == b.get("on") and a["at"] == pytest.approx(b["at"], abs=1e-9)
        assert a["rotate"] == pytest.approx(b["rotate"], abs=1e-9)
    assert out["volume"] == pytest.approx(h3.build_design(py).solid.volume(), rel=1e-9)
