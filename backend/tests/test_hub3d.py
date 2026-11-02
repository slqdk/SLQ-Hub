"""hub3d, the sandbox's helper for 3D models to print (0.67.1, chat 255): what it counts as a closed solid, the files
it writes and reads back, the picture (the Hub's own, kept the same), the command line, how the image installs it,
and what the assistant is told. manifold3d is in the sandbox, not here: the recipe's examples run in the image's
build (hub3d selftest) and below only where manifold3d is installed."""
from __future__ import annotations

import importlib.util
import io
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from app.model3d import mesh as hub_mesh

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "services" / "sandbox" / "hub3d.py"


@pytest.fixture(scope="module")
def h3():
    spec = importlib.util.spec_from_file_location("hub3d", SOURCE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hub3d"] = mod                      # the recipe's examples import it by name
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("hub3d", None)


def box(extents=(10, 20, 30), at=(0, 0, 0)):
    b = trimesh.creation.box(extents=extents)
    b.apply_translation(np.asarray(at, dtype=float) + np.asarray(extents, dtype=float) / 2)
    return np.asarray(b.vertices, dtype=float), np.asarray(b.faces, dtype=np.int64)


def joined(*meshes):
    vs, fs, n = [], [], 0
    for v, f in meshes:
        vs.append(v)
        fs.append(f + n)
        n += len(v)
    return np.vstack(vs), np.vstack(fs)


# ---------------------------------------------------------------- what counts

def test_a_closed_box_is_ready(h3):
    r = h3.check(box())
    assert r.ok and r.watertight and r.triangles == 12 and r.parts == 1 and not r.warnings
    assert r.size == (10, 20, 30) and abs(r.volume - 6000) < 1e-6
    text = str(r)
    assert "size 10.0 x 20.0 x 30.0 mm" in text and "volume 6.0 cm3" in text and text.endswith("READY for the slicer.")


def test_holes_inward_triangles_and_shared_edges_are_counted(h3):
    """Chat 255's file in small: what is wrong is counted and said, never "validated"."""
    v, f = box()
    holed = h3.check((v, f[1:]))
    assert not holed.ok and holed.open_edges == 3 and "the surface has holes" in str(holed)
    assert str(holed).splitlines()[-1].startswith("NOT READY")
    one = f.copy()
    one[0] = one[0, ::-1]
    flipped = h3.check((v, one))
    assert not flipped.ok and flipped.flipped_edges == 3 and flipped.open_edges == 0
    assert "some triangles face inwards" in str(flipped)
    inside_out = h3.check((v, f[:, ::-1]))
    assert inside_out.watertight and inside_out.volume < 0 and "faces[:, ::-1]" in inside_out.problems[0]
    # two boxes touching along an edge: four triangles on it, counted once (not again as flipped)
    touching = h3.check(joined(box((10, 10, 10)), box((10, 10, 10), at=(10, 10, 0))))
    assert touching.shared_edges == 1 and touching.flipped_edges == 0
    assert "1 edges belong to more than two triangles" in str(touching)


def test_parts_cavities_and_corners_that_meet_in_a_file(h3):
    """An STL keeps no shared corners: they are joined again where they meet (as 32-bit floats, as slicers read
    them). A shell turned in inside another is a sealed cavity; separate parts are a warning, not a fault."""
    v, f = box()
    loose = v[f].reshape(-1, 3)                                  # every triangle its own three corners
    r = h3.check((loose, np.arange(len(loose)).reshape(-1, 3)))
    assert r.ok and r.parts == 1 and r.triangles == 12
    outer = box((20, 20, 20))
    iv, i_f = box((5, 5, 5), at=(5, 5, 5))
    hollow = h3.check(joined(outer, (iv, i_f[:, ::-1])))
    assert hollow.ok and hollow.parts == 1 and hollow.cavities == 1 and abs(hollow.volume - (8000 - 125)) < 1e-6
    assert "1 sealed cavity inside" in str(hollow)
    two = h3.check(joined(box(), box(at=(50, 0, 0))))
    assert two.ok and two.parts == 2 and "2 separate parts" in two.warnings[0]
    apart = h3.check(joined(box(), (iv + 100, i_f[:, ::-1])))   # turned in, but inside nothing
    assert not apart.ok and "1 of its 2 parts face inwards" in apart.problems[0]


def test_the_bed_and_the_units(h3):
    long = box((200, 100, 10))
    assert "fits turned 90 degrees" in h3.check(long, bed=(150, 250, 100)).warnings[0]
    assert "scale it down or split it" in h3.check(long, bed=180).warnings[0]
    assert "fits the 256 x 256 x 256 mm bed" in str(h3.check(long, bed="256x256x256"))
    assert h3._bed("180") == h3._bed(180) == h3._bed([180, 180, 180]) == (180, 180, 180)
    with pytest.raises(ValueError, match="not a size in mm"):
        h3._bed("big")
    v, f = box((0.01, 0.02, 0.03))
    assert "made in metres" in h3.check((v, f)).warnings[0]


def test_what_cannot_be_used_says_why(h3):
    v, f = box()
    with pytest.raises(ValueError, match="vertices must be N x 3"):
        h3.check((v[:, :2], f))
    with pytest.raises(ValueError, match="faces point at vertex 99"):
        h3.check((v, np.vstack([f, [[0, 1, 99]]])))
    with pytest.raises(ValueError, match="whole numbers"):
        h3.check((v, f.astype(float)))
    with pytest.raises(TypeError, match="cannot use a dict"):
        h3.check({"vertices": v})
    with pytest.raises(FileNotFoundError):
        h3.check("no-such.stl")


def test_a_manifold_is_taken_as_it_is_and_an_empty_one_refused(h3, tmp_path):
    """manifold3d is not installed here: stand-ins of its types, named as it names them."""
    v, f = box()

    class Mesh:
        vert_properties = v.astype(np.float32)
        tri_verts = f.astype(np.int32)

    class Manifold:
        def __init__(self, empty=False):
            self.empty = empty

        def status(self):
            return "Error.NoError"

        def is_empty(self):
            return self.empty

        def to_mesh(self):
            return Mesh()

    class CrossSection:
        pass

    for cls in (Mesh, Manifold, CrossSection):
        cls.__module__ = "manifold3d"
    assert h3.check(Manifold()).ok and h3.check(Mesh()).ok
    with pytest.raises(h3.NotPrintable, match="the solid is empty"):
        h3.save(Manifold(empty=True), tmp_path / "e.stl")
    assert not (tmp_path / "e.stl").exists()
    with pytest.raises(TypeError, match="a CrossSection is a flat outline"):
        h3.check(CrossSection())
    assert h3.check([Manifold(), (v + 50, f)]).parts == 2       # a list is joined into one file


# ---------------------------------------------------------------- files

def test_save_writes_an_stl_reads_it_back_and_says_so(h3, tmp_path, capsys):
    report = h3.save(box(), tmp_path / "out" / "box.stl", bed=180)
    data = (tmp_path / "out" / "box.stl").read_bytes()
    assert len(data) == 84 + 50 * 12 and struct.unpack_from("<I", data, 80)[0] == 12
    assert not data.startswith(b"solid")                         # or a reader may take it for ASCII
    rec = np.frombuffer(data, dtype=h3.STL_RECORD, offset=84)
    outward = ((rec["corners"].mean(1) - (5, 10, 15)) * rec["normal"]).sum(1)
    assert (outward > 0).all()                                   # every normal points out
    assert report.ok and report.kind == "binary STL" and "read back from the file" in str(report)
    printed = capsys.readouterr().out
    assert "box.stl (binary STL): 12 triangles" in printed and "READY for the slicer." in printed
    loaded = trimesh.load(tmp_path / "out" / "box.stl")          # another reader agrees
    assert loaded.is_watertight and abs(loaded.volume - 6000) < 1e-3


def test_save_writes_a_3mf_in_millimetres(h3, tmp_path):
    h3.save(box(), tmp_path / "box.3mf", quiet=True)
    with zipfile.ZipFile(tmp_path / "box.3mf") as z:
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= set(z.namelist())
        model = z.read("3D/3dmodel.model").decode()
    assert 'unit="millimeter"' in model and model.count("<vertex ") == 8 and model.count("<triangle ") == 12
    r = h3.check(tmp_path / "box.3mf")
    assert r.ok and r.kind == "3MF" and abs(r.volume - 6000) < 1e-6


def test_a_3mf_with_components_in_other_files_is_placed(h3, tmp_path):
    """As Bambu Studio and PrusaSlicer save them: the build's item is an object of components, each a mesh in
    3D/Objects/, moved (and scaled) by its transform and the item's."""
    v, f = box()
    verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in v.tolist())
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f.tolist())
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    part = (f'<model unit="millimeter" xmlns="{core}"><resources><object id="1" type="model"><mesh><vertices>{verts}'
            f'</vertices><triangles>{tris}</triangles></mesh></object></resources><build/></model>')
    root = (f'<model unit="millimeter" xmlns="{core}" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"><resources>'
            '<object id="2" type="model"><components>'
            '<component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 100 0 0"/>'
            '<component p:path="/3D/Objects/object_1.model" objectid="1" transform="2 0 0 0 1 0 0 0 1 0 50 0"/>'
            '</components></object></resources>'
            '<build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 5"/></build></model>')
    with zipfile.ZipFile(tmp_path / "plate.3mf", "w") as z:
        z.writestr("3D/3dmodel.model", root)
        z.writestr("3D/Objects/object_1.model", part)
    r = h3.check(tmp_path / "plate.3mf")
    assert r.ok and r.parts == 2 and abs(r.volume - 18000) < 1e-6
    assert r.size == (110, 70, 30) and r.lowest == (0, 0, 5)


def test_an_ascii_stl_is_read(h3, tmp_path):
    v, f = box()
    lines = ["solid box"]
    for t in v[f]:
        lines += ["facet normal 0 0 0", "outer loop"] + [f"vertex {x} {y} {z}" for x, y, z in t] + \
                 ["endloop", "endfacet"]
    (tmp_path / "a.stl").write_text("\n".join(lines + ["endsolid box"]))
    r = h3.check(tmp_path / "a.stl")
    assert r.ok and r.kind == "ASCII STL" and r.triangles == 12


def test_save_refuses_what_is_not_a_solid_unless_told(h3, tmp_path):
    v, f = box()
    with pytest.raises(h3.NotPrintable) as err:
        h3.save((v, f[1:]), tmp_path / "holed.stl")
    assert "Nothing was written" in str(err.value) and not (tmp_path / "holed.stl").exists()
    r = h3.save((v, f[1:]), tmp_path / "holed.stl", strict=False, quiet=True)
    assert not r.ok and (tmp_path / "holed.stl").stat().st_size == 84 + 50 * 11
    with pytest.raises(ValueError, match="save as .stl or .3mf"):
        h3.save((v, f), tmp_path / "box.obj")


# ---------------------------------------------------------------- the picture

def test_the_picture_is_the_hubs_own(h3):
    """The same drawing as model3d_generate's views (app/model3d/mesh.py), pixel for pixel."""
    for mesh in (trimesh.creation.icosphere(subdivisions=3, radius=20),
                 trimesh.creation.cylinder(radius=8, height=40, sections=24)):
        v, f = np.asarray(mesh.vertices), np.asarray(mesh.faces)
        assert h3._picture(v, f) == hub_mesh.views(mesh)


def test_views_writes_a_png_to_look_at(h3, tmp_path, capsys):
    h3.save(box(), tmp_path / "box.stl", quiet=True)
    path = h3.views(tmp_path / "box.stl", tmp_path / "views" / "box.png", size=200)
    img = Image.open(path)
    assert img.size == (600, 228) and "look at it with view_image" in capsys.readouterr().out
    with pytest.raises(ValueError, match="views are a PNG"):
        h3.views(box(), tmp_path / "box.jpg")


# ---------------------------------------------------------------- the command line, the image, the assistant

def test_the_command_line(h3, tmp_path, capsys):
    h3.save(box(), tmp_path / "box.stl", quiet=True)
    v, f = box()
    h3.save((v, f[1:]), tmp_path / "holed.stl", strict=False, quiet=True)
    capsys.readouterr()
    assert h3.main(["check", str(tmp_path / "box.stl"), "--bed", "180"]) == 0
    assert "fits the 180 x 180 x 180 mm bed" in capsys.readouterr().out
    assert h3.main(["check", str(tmp_path / "holed.stl")]) == 1
    assert "closed solid: NO" in capsys.readouterr().out
    assert h3.main(["check", str(tmp_path / "none.stl")]) == 2
    assert "hub3d check: no file" in capsys.readouterr().err
    assert h3.main(["views", str(tmp_path / "box.stl"), str(tmp_path / "v.png")]) == 0
    assert (tmp_path / "v.png").stat().st_size > 1000 and "Wrote " in capsys.readouterr().out
    assert h3.main(["recipe"]) == 0
    recipe = capsys.readouterr().out
    assert recipe == h3.RECIPE
    for needed in ("M.cylinder(h, radius_low, radius_high=-1", "m3d.FillRule.EvenOdd", ".warp_batch(f)",
                   "(not 4 x 4)", "Not in manifold3d - do not guess: Cylinder, union(), intersect(), to_stl()",
                   "hub3d.save(cup, \"out/pen-cup.stl\", bed=180)", "view_image"):
        assert needed in recipe, needed


def test_the_recipes_examples_are_whole_programs(h3):
    for code in (h3.EXAMPLE, h3.LETTERS):
        compile(code, "example", "exec")
        assert h3._indent(code) in h3.RECIPE


def test_the_recipe_runs_where_manifold3d_is(h3, monkeypatch, tmp_path):
    """The image's build runs this (hub3d selftest); here only where manifold3d is installed."""
    pytest.importorskip("manifold3d")
    pytest.importorskip("matplotlib")
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path))
    assert h3.selftest() == 0


def test_the_image_installs_it_and_checks_the_recipe():
    docker = (ROOT / "services" / "sandbox" / "Dockerfile").read_text()
    pins = next(r for r in docker.split("\nRUN ") if "manifold3d==" in r)
    for pin in ("manifold3d==3.5.4", "trimesh==5.2.0", "shapely==2.2.0", "networkx==3.6.1"):
        assert pin in pins, pin
    assert "COPY hub3d.py /opt/hub3d/hub3d.py" in docker
    assert "exec /opt/py/bin/python -I -m hub3d" in docker            # isolated: no module from the working folder
    assert "runuser -u sandbox -- /usr/local/bin/hub3d selftest" in docker
    assert docker.index("manifold3d==") < docker.index("COPY hub3d.py") < docker.index("USER sandbox")


def test_what_the_assistant_is_told():
    from app.agent.prompt import print3d_text
    from app.agent.tools.shell import BASH_TEXT
    assert print3d_text(False, False) == ""
    code = print3d_text(True, False)
    assert "the designer's print-ready file is on that page" in code and "its licence" in code
    assert "run `hub3d recipe` in bash first" in code and "do not guess it" in code
    assert "look at hub3d.views with view_image before you say it is done" in code
    assert "model3d_generate" not in code
    photo = print3d_text(False, True)
    assert "model3d_generate" in photo and "hub3d" not in photo
    assert "hub3d for 3D models to print" in BASH_TEXT
    skill = (ROOT / "backend" / "app" / "agent" / "builtin_skills" / "skill-creator" / "SKILL.md").read_text()
    assert "`hub3d recipe`" in skill and "hub3d check model.stl --bed 180" in skill
    assert "A skill makes things of a kind, not one thing" in skill


async def test_the_3d_section_is_in_the_owners_prompt(signed_in, app, fakes):
    from .test_agent import finish, new_chat, say
    fakes.llama["replies"] = [{"content": "Hej."}, {"content": "Hej"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "Hej"))
    system = fakes.llama["requests"][0]["messages"][0]["content"]
    assert "# 3D models to print" in system and "run `hub3d recipe` in bash first" in system
    assert "model3d_generate" not in system                       # TRELLIS.2 is not downloaded here
