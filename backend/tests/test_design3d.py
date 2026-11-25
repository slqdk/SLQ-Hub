"""Designs from the 3D editor (0.87.0): the browser (frontend/src/model3d/geometry.ts, run here with node) and the
sandbox (services/sandbox/hub3d.py's design) read a .3d.json the same way and build the same solid from it - what
the assistant checks is what the editor shows and exports. The geometry needs manifold3d (Python) and manifold-3d
(frontend/node_modules); where either is missing those tests are skipped."""
from __future__ import annotations

import importlib.util
import json
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
FONT = ROOT / "frontend" / "src" / "model3d" / "font-barlow-600.json"


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
        head += (f"import Module from {json.dumps(str(MANIFOLD_JS))};\nconst wasm = await Module(); wasm.setup();\n"
                 f"const font = JSON.parse(readFileSync({json.dumps(str(FONT))}, 'utf8'));\n")
    head += "const input = JSON.parse(readFileSync(0, 'utf8'));\n"
    out = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e",
                          head + script], input=json.dumps(payload), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout)


def test_the_font_is_the_same_in_both_places():
    sandbox = ROOT / "services" / "sandbox" / "hub3d-font.json"
    assert FONT.read_bytes() == sandbox.read_bytes(), "tools/make_3d_font.py writes both: run it again"
    font = json.loads(FONT.read_text(encoding="utf-8"))
    assert font["unitsPerEm"] == 1000 and all(ch in font["glyphs"] for ch in "AZaz09æøåÆØÅ?.,-+°")
    docker = (ROOT / "services" / "sandbox" / "Dockerfile").read_text()
    assert "COPY hub3d-font.json /opt/hub3d/hub3d-font.json" in docker


ODD = {"format": "slq-hub-3d", "version": 1, "printer": "bambu-p2s", "bed": [256, 256, 256], "parts": [
    {"id": "a", "shape": "box"},                                                    # all defaults
    {"id": "a", "shape": "box", "size": [10, -4, 0], "radius": 2, "sides": 7.5},     # the same id; odd numbers
    {"shape": "blob", "size": [1, 2], "at": [1, 2, "x"]},                           # no id, no such shape
    {"id": "c", "shape": "cone", "top": 3, "sides": 1000, "rotate": [10, 20, 30], "mirror": True, "hole": True},
    {"id": "t", "shape": "text", "size": [0, 8, 1.5], "text": 7},
    {"id": "m", "shape": "mesh"},
    {"id": "g", "shape": "group", "at": [5, 5, 5], "size": [9, 9, 9], "parts": [
        {"id": "s", "shape": "sphere", "sides": 2.5, "hidden": True}, "not a part"]}]}
GEOMETRIC = ("id", "shape", "size", "at", "rotate", "mirror", "hole", "hidden", "sides", "radius", "top", "text",
             "file")


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
    assert len(js["problems"]) == len(problems) == 3
    by_id = {p["id"]: p for p in flat(py["parts"])}
    assert by_id["a"]["size"] == [20, 20, 20] and by_id["a"]["at"] == [0, 0, 10]
    assert by_id["p2"]["size"] == [10, 4, 0.01] and by_id["p2"]["sides"] == 8 and by_id["p2"]["radius"] == 2
    assert by_id["p3"]["shape"] == "box" and by_id["p3"]["at"] == [0, 0, 10]
    assert by_id["c"]["top"] == 1 and by_id["c"]["sides"] == 256 and by_id["c"]["mirror"] and by_id["c"]["hole"]
    assert by_id["t"]["size"] == [0, 8, 1.5] and by_id["t"]["text"] == "Text"
    assert by_id["s"]["sides"] == 3 and by_id["s"]["hidden"]
    # What the editor writes reads back as it was, and a group keeps no place of its own.
    again, _ = h3.read_design(js["text"])
    assert flat(again["parts"]) == flat(py["parts"])
    assert '"size": [20, 20, 20]' in js["text"] and '"at": [5, 5, 5]' not in js["text"]


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
  const b = G.build(design, { wasm, font, meshes });
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
                json.loads(h3.DESIGN_EXAMPLE)]                  # hub3d format's example
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


MOVES_JS = """
const out = {};
const box = { id: "b", shape: "wedge", size: [30, 10, 20], at: [10, 0, 10], rotate: [0, 0, 30] };
const once = G.mirrorPart(box, 0, [0, 0, 0]), twice = G.mirrorPart(once, 0, [0, 0, 0]);
out.once = once; out.twice = twice;
const turned = G.transformPart(box, [0, 0, 0], [5, 0, 0], G.rotation([0, 0, 90]), 2);
out.turned = turned;
out.extent = G.extent(box);
const build = (parts) => { const b = G.build({ format: G.FORMAT, version: 1, parts }, { wasm, font, meshes: new Map() });
  return { volume: b.volume, box: b.box }; };
out.built = [build([box]), build([once]), build([twice]), build([turned])];
out.width = G.textWidth("SLQ", 10, font);
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
    assert guide == h3.DESIGN_GUIDE and h3.DESIGN_EXAMPLE in guide
    _, problems = h3.read_design(h3.DESIGN_EXAMPLE)
    assert problems == []
    js = run_js("""process.stdout.write(JSON.stringify(G.readDesign(input.text).problems));""",
                {"text": h3.DESIGN_EXAMPLE})
    assert js == []
    for shape in ("box", "cylinder", "cone", "sphere", "wedge", "text", "mesh", "group"):
        assert f"  {shape} " in guide or f"| {shape}" in guide
    printers = (ROOT / "frontend" / "src" / "model3d" / "printers.ts").read_text()
    for pid in ("bambu-p2s", "bambu-p1s", "bambu-x1", "bambu-a1", "bambu-a1-mini", "bambu-h2s", "bambu-h2d"):
        assert f'id: "{pid}"' in printers and pid in guide
