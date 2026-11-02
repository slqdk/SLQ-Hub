"""The Hub's converter against ComfyUI's own editor (convert.js): python3 compare.py <editor folder> <editor's API
folder> <object_info.json>. Prints what differs; the editor's leftovers (values of inputs a node does not have, a 3D
viewer's camera) are not differences. 0.42.3: 246 of 304 identical, 7 differing harmlessly, 50 refused."""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.workflows import convert as C  # noqa: E402


def main(editor_dir: str, api_dir: str, info_path: str) -> None:
    info = json.loads(Path(info_path).read_text(encoding="utf-8"))
    tally, notes = collections.Counter(), []
    for ref_path in sorted(Path(api_dir).glob("*.json")):
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
        try:
            mine = C.to_api(json.loads((Path(editor_dir) / ref_path.name).read_text(encoding="utf-8")), info)
        except C.WorkflowError as exc:
            tally["refused"] += 1
            notes.append((ref_path.name, str(exc)[:160]))
            continue
        diff = []
        if set(ref) != set(mine):
            diff.append(("nodes", sorted(set(ref) ^ set(mine))[:8]))
        for nid in set(ref) & set(mine):
            spec = info.get(ref[nid]["class_type"], {}).get("input") or {}
            known = set(spec.get("required") or {}) | set(spec.get("optional") or {})
            for k in set(ref[nid]["inputs"]) | set(mine[nid]["inputs"]):
                a, b = mine[nid]["inputs"].get(k, "<absent>"), ref[nid]["inputs"].get(k, "<absent>")
                if a != b and k != "viewport_state" and not (k.split(".")[0] not in known and a == "<absent>"):
                    diff.append((nid, ref[nid]["class_type"], k, str(a)[:60], str(b)[:60]))
        tally["differs" if diff else "same"] += 1
        notes += [(ref_path.name, d) for d in diff]
    print(dict(tally))
    for n in notes:
        print(n)


if __name__ == "__main__":
    main(*sys.argv[1:4])
