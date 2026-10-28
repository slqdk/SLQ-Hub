"""edit_file when the model's copy of the text is not exact (0.46.0). Søren: the red "old_string was not found" came
regularly. A copy with read_file's line numbers in it, tabs that came out as spaces or a lost indentation is now
found; when nothing is, the error shows the most similar lines, exactly, so the next try can copy them."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.agent.tools.files import edit_file, read_file


class Folder:
    def __init__(self, root: Path):
        self.root = root

    def resolve(self, rel: str) -> Path:
        return self.root / rel

    def write(self, rel: str, data, *_):
        (self.root / rel).write_bytes(data.encode() if isinstance(data, str) else data)


def ctx_in(tmp_path: Path):
    return SimpleNamespace(workdir=Folder(tmp_path), state={})


async def edit(tmp_path: Path, old: str, new: str, **more):
    return await edit_file(ctx_in(tmp_path), {"path": "f.st", "old_string": old, "new_string": new, **more})


ST = "PROGRAM MAIN\nVAR\n\tnCount : INT;\nEND_VAR\n\nIF bStart THEN\n\tnCount := nCount + 1;\n\tbDone := TRUE;\nEND_IF\n"


async def test_an_exact_copy_is_edited_as_before(tmp_path):
    (tmp_path / "f.st").write_text(ST)
    res = await edit(tmp_path, "nCount := nCount + 1;", "nCount := nCount + 2;")
    assert not res.is_error and res.text == "Edited f.st: replaced 1 occurrence(s), starting at line 7."
    assert (tmp_path / "f.st").read_text() == ST.replace("+ 1", "+ 2")


async def test_a_file_with_crlf_keeps_its_line_ends(tmp_path):
    """read_text turned \\r\\n into \\n, and the edit wrote every line of a Windows file back changed."""
    (tmp_path / "f.st").write_bytes(ST.replace("\n", "\r\n").encode())
    res = await edit(tmp_path, "IF bStart THEN\n\tnCount := nCount + 1;", "IF bGo THEN\n\tnCount := nCount + 1;")
    assert not res.is_error
    assert (tmp_path / "f.st").read_bytes() == ST.replace("bStart", "bGo").replace("\n", "\r\n").encode()


async def test_a_copy_with_read_files_line_numbers(tmp_path):
    (tmp_path / "f.st").write_text(ST)
    shown = (await read_file(ctx_in(tmp_path), {"path": "f.st"})).text.split("\n")
    old = "\n".join(shown[6:9])                         # lines 6-8, numbers and all: " 6\tIF bStart THEN" ...
    assert old.startswith(" 6\tIF bStart THEN")
    new = old.replace("nCount + 1", "nCount + 5") + "\n\tbBusy := FALSE;"     # an added line without a number
    res = await edit(tmp_path, old, new)
    assert not res.is_error and "line numbers in it - they were left out" in res.text
    assert (tmp_path / "f.st").read_text() == ST.replace("+ 1;\n\tbDone := TRUE;",
                                                         "+ 5;\n\tbDone := TRUE;\n\tbBusy := FALSE;")


async def test_tabs_that_came_out_as_spaces(tmp_path):
    (tmp_path / "f.st").write_text(ST)
    old = "IF bStart THEN\n    nCount := nCount + 1;\n    bDone := TRUE;\nEND_IF"
    new = "IF bStart THEN\n    nCount := nCount + 1;\n    IF nCount > 9 THEN\n        bDone := TRUE;\n    END_IF\nEND_IF"
    res = await edit(tmp_path, old, new)
    assert not res.is_error and "found with whitespace ignored, new_string indented the file's way" in res.text
    assert "starting at line 6" in res.text
    assert (tmp_path / "f.st").read_text() == ST.replace(
        "\tbDone := TRUE;\n", "\tIF nCount > 9 THEN\n\t\tbDone := TRUE;\n\tEND_IF\n")


async def test_a_first_line_copied_without_its_indentation(tmp_path):
    (tmp_path / "f.st").write_text(ST)
    res = await edit(tmp_path, "nCount := nCount + 1;\n\tbDone := TRUE;", "nCount := 0;\n\tbDone := FALSE;")
    assert not res.is_error
    assert (tmp_path / "f.st").read_text() == ST.replace("nCount + 1;\n\tbDone := TRUE;", "0;\n\tbDone := FALSE;")


async def test_one_line_without_indentation_and_more_lines_for_it(tmp_path):
    (tmp_path / "f.st").write_text(ST)
    res = await edit(tmp_path, "bDone := TRUE; ", "bDone := TRUE;\nbBusy := FALSE;")       # and a trailing space
    assert not res.is_error
    assert (tmp_path / "f.st").read_text() == ST.replace("\tbDone := TRUE;\n", "\tbDone := TRUE;\n\tbBusy := FALSE;\n")


async def test_a_loose_match_in_two_places_is_not_guessed(tmp_path):
    (tmp_path / "f.st").write_text("A\n\tx := 1;\nB\n\tx := 1;\n")
    res = await edit(tmp_path, "x  :=  1;", "x := 2;")
    assert res.is_error and "matches 2 places when whitespace is ignored (lines 2, 4)" in res.text
    assert (tmp_path / "f.st").read_text() == "A\n\tx := 1;\nB\n\tx := 1;\n"


async def test_not_found_shows_the_most_similar_lines(tmp_path):
    (tmp_path / "f.st").write_text(ST)
    res = await edit(tmp_path, "IF bStart THEN\n\tnCount := nCount + 10;\n\tbDone := TRUE;", "x")
    assert res.is_error and res.text.startswith("old_string was not found in f.st. The most similar text is at "
                                                "lines 6-8, exactly:\nIF bStart THEN\n\tnCount := nCount + 1;\n")
    res = await edit(tmp_path, "something else entirely", "x")
    assert res.is_error and res.text == ("old_string was not found in f.st. Copy the text exactly (read the file "
                                         "again if it may have changed).")
    assert (tmp_path / "f.st").read_text() == ST


async def test_a_copy_one_level_less_deep_with_spaces(tmp_path):
    (tmp_path / "f.st").write_text("IF a THEN\n\tIF b THEN\n\t\tx := 1;\n\tEND_IF\nEND_IF\n")
    res = await edit(tmp_path, "IF b THEN\n    x := 1;\nEND_IF", "IF b THEN\n    IF c THEN\n        x := 1;\n    END_IF\nEND_IF")
    assert not res.is_error
    assert (tmp_path / "f.st").read_text() == "IF a THEN\n\tIF b THEN\n\t\tIF c THEN\n\t\t\tx := 1;\n\t\tEND_IF\n\tEND_IF\nEND_IF\n"
