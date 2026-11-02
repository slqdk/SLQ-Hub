"""ComfyUI workflows as the editor saves them - nodes, links, widget values, subgraphs - turned into the API format
that ComfyUI's /prompt takes, as ComfyUI's own editor does when it queues one (its frontend's graphToPrompt and
ExecutableNodeDTO, 1.55): subgraphs flattened into "outer:inner" ids, bypassed nodes passed through, muted ones
dropped, reroutes followed, primitive nodes' values put into the widgets they feed, notes left out. A node's widget
values are matched to its inputs with ComfyUI's node list (/object_info) - the editor's file stores only the values,
in the order of the node's widgets. (0.67.0, the Workflows page.)

Checked against ComfyUI 0.42.3's own editor on its 303 local templates (tests/test_workflows.py has the cases)."""
from __future__ import annotations

import json
from typing import Any

MODE_NEVER, MODE_BYPASS = 2, 4                 # muted: left out; bypassed: its inputs pass straight through
SUBGRAPH_IN, SUBGRAPH_OUT = -10, -20           # the subgraph's own input and output nodes, in its links
NOTES = {"Note", "MarkdownNote", "Label"}
VIRTUAL = NOTES | {"Reroute", "PrimitiveNode"}
# Input types the editor shows as a widget (a value in the node), unless forceInput; the rest are links.
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO", "COLOR", "COLORS", "BOUNDING_BOX", "BOUNDING_BOXES",
                "LOAD_3D", "LOAD_3D_ANIMATION", "WEBCAM", "CURVE", "COMFY_DYNAMICCOMBO_V3", "IMAGECOMPARE",
                "MARKDOWN"}
# Widgets the editor shows but does not send (a camera picker that only draws).
UNSENT = {"CAMERA_ANGLE_VIEW"}
CONTROL_VALUES = {"fixed", "increment", "decrement", "randomize"}
MAX_NODES = 5000                # once subgraphs are opened: a few KB of nested subgraphs can make millions
# A file list with an upload button keeps the button's value after its own (two for sound: a player too)
UPLOAD_EXTRAS = {"image_upload": 1, "video_upload": 1, "audio_upload": 2, "file_upload": 1}
EMPTY = {"IMAGECOMPARE": {"__value__": ["", ""]}}


class WorkflowError(ValueError):
    """The workflow cannot be read: what is wrong, in a sentence."""


def is_api(data: Any) -> bool:
    """API format: {node id: {"class_type", "inputs"}}."""
    return (isinstance(data, dict) and bool(data)
            and all(isinstance(v, dict) and isinstance(v.get("class_type"), str)
                    and isinstance(v.get("inputs", {}), dict) for v in data.values()))


def is_editor(data: Any) -> bool:
    """The editor's format: {"nodes": [...], "links": [...]}."""
    return isinstance(data, dict) and isinstance(data.get("nodes"), list) and isinstance(data.get("links", []), list)


def _spec(raw) -> tuple[Any, dict]:
    if isinstance(raw, (list, tuple)) and raw:
        opts = raw[1] if len(raw) > 1 and isinstance(raw[1], dict) else {}
        return raw[0], opts
    return raw, {}


def _widget_kind(kind, opts: dict):
    """The kind of widget the editor makes for an input (None: a link only). An input of several types or of a
    type of its own names it in widgetType (frame_rate "FLOAT,INT" is a FLOAT widget, Painter's mask a PAINTER)."""
    if opts.get("forceInput"):
        return None
    if isinstance(kind, list):                           # a COMBO written as its list of choices
        return "COMBO"
    if isinstance(opts.get("widgetType"), str):
        return opts["widgetType"]
    return kind if isinstance(kind, str) and kind in WIDGET_TYPES else None


def _default(kind, opts: dict):
    """A widget's value when the file keeps none: its default, a list's first choice."""
    if "default" in opts:
        return opts["default"]
    if isinstance(kind, list) and kind:
        return kind[0]
    choices = opts.get("options")
    if isinstance(choices, list) and choices:
        first = choices[0]
        return first.get("key") if isinstance(first, dict) else first
    return EMPTY.get(kind, _MISSING)


def ordered_inputs(entry: dict) -> list[tuple[str, Any, dict]]:
    """A node's inputs as (name, type, options), required then optional, in ComfyUI's order."""
    inp = entry.get("input") or {}
    order = entry.get("input_order") or {}
    out = []
    for sec in ("required", "optional"):
        specs = inp.get(sec) or {}
        names = [n for n in (order.get(sec) or []) if n in specs] + [n for n in specs if n not in (order.get(sec) or [])]
        for name in names:
            kind, opts = _spec(specs[name])
            out.append((name, kind, opts))
    return out


def _has_control(name: str, kind, opts: dict) -> bool:
    return kind in ("INT", "FLOAT") and bool(opts.get("control_after_generate") or name in ("seed", "noise_seed"))


def assign_widgets(entry: dict, values: list, controls: dict | None = None) -> dict[str, Any]:
    """The node's widget values by input name, from the editor's list in widget order. A seed's "control after
    generate" and an upload button keep a value of their own in that list (the control goes into `controls`: input
    -> "fixed", "randomize" ...); a dynamic combo's chosen option brings its own widgets after it ("format.codec")."""
    out: dict[str, Any] = {}
    pos = 0

    def take(name: str, kind, opts: dict, prefix: str) -> None:
        nonlocal pos
        widget = _widget_kind(kind, opts)
        if widget is None:
            return
        key = prefix + name
        if pos < len(values):
            value = values[pos]
            pos += 1
            if _has_control(name, widget, opts) and pos < len(values) and values[pos] in CONTROL_VALUES:
                if controls is not None:
                    controls[key] = values[pos]
                pos += 1
            if widget == "COMBO":
                pos += sum(extra for flag, extra in UPLOAD_EXTRAS.items() if opts.get(flag))
        else:
            value = _default(kind, opts)
            if value is _MISSING:
                return
        if widget in EMPTY:
            value = EMPTY[widget]                         # a viewer's state, which the editor sends empty
        if widget not in UNSENT:
            out[key] = value
        if kind == "COMFY_DYNAMICCOMBO_V3":
            chosen = next((o for o in opts.get("options") or [] if isinstance(o, dict) and o.get("key") == value), None)
            for sec in ("required", "optional"):
                for sub, raw in ((chosen or {}).get("inputs", {}).get(sec) or {}).items():
                    k2, o2 = _spec(raw)
                    take(sub, k2, o2, f"{key}.")

    for name, kind, opts in ordered_inputs(entry):
        take(name, kind, opts, "")
    return out


def _links(level: dict) -> dict[int, dict]:
    """A graph's links by id, whichever way they are written ([id, from, slot, to, slot, type] or objects)."""
    out = {}
    for link in level.get("links") or []:
        if isinstance(link, (list, tuple)) and len(link) >= 5:
            out[int(link[0])] = {"origin_id": link[1], "origin_slot": link[2], "target_id": link[3],
                                 "target_slot": link[4], "type": link[5] if len(link) > 5 else "*"}
        elif isinstance(link, dict) and "id" in link:
            out[int(link["id"])] = link
    return out


def _types_fit(a: str, b: str) -> bool:
    if a in ("*", "") or b in ("*", ""):
        return True
    return bool(set(str(a).split(",")) & set(str(b).split(",")))


class _Level:
    """One graph: the workflow itself or a subgraph's definition."""

    def __init__(self, data: dict):
        self.data = data
        self.nodes = {n["id"]: n for n in data.get("nodes") or [] if isinstance(n, dict) and "id" in n}
        self.links = _links(data)


class _Node:
    """A node where it runs: its id ("outer:inner" inside subgraphs), its graph and the subgraph node holding it."""

    def __init__(self, conv: "_Converter", node: dict, level: _Level, path: tuple, holder: "_Node | None"):
        self.conv, self.node, self.level, self.path, self.holder = conv, node, level, path, holder
        self.id = ":".join(str(p) for p in (*path, node["id"]))
        self.type = node.get("type")
        self.mode = node.get("mode") or 0
        self.inputs = node.get("inputs") or []
        self.outputs = node.get("outputs") or []
        self._promoted: dict | None = None

    @property
    def is_subgraph(self) -> bool:
        return self.type in self.conv.subgraphs

    def link_of(self, slot: int) -> int | None:
        if 0 <= slot < len(self.inputs):
            link = self.inputs[slot].get("link")
            return int(link) if link is not None else None
        return None

    def slot_named(self, name: str | None) -> int | None:
        """The slot of this node's input called `name` (a subgraph node keeps only some of its inputs in the
        file, so they are found by name, not by place)."""
        for i, inp in enumerate(self.inputs):
            if name is not None and inp.get("name") == name:
                return i
        return None

    # ------------------------------------------------------------- values a subgraph node shows for its inputs
    def promoted(self) -> dict[int, Any]:
        """A subgraph node's own values for the subgraph's inputs that feed widgets inside (definition input index
        -> value), in the older files' way: its widget values in the order of those inputs. A file with
        "proxyWidgets" keeps them by inner widget instead (proxied())."""
        if self._promoted is not None:
            return self._promoted
        out: dict[int, Any] = {}
        values = self.node.get("widgets_values")
        values = values if isinstance(values, list) else []
        if (self.node.get("properties") or {}).get("proxyWidgets") is None:
            definition = self.conv.subgraphs[self.type]
            k = 0
            for index in self.conv.widget_inputs(definition):
                if k < len(values) and values[k] is not None:
                    out[index] = values[k]
                k += 1
        self._promoted = out
        return out

    def proxied(self) -> dict[tuple, Any]:
        """(inner node id, widget name) -> value: the values a subgraph node shows for widgets inside it
        ("proxyWidgets", in its properties, with the values in the same order; a missing one is the inner
        widget's own)."""
        proxies = (self.node.get("properties") or {}).get("proxyWidgets")
        values = self.node.get("widgets_values")
        values = values if isinstance(values, list) else []
        out: dict[tuple, Any] = {}
        if not isinstance(proxies, list):
            return out
        for i, entry in enumerate(proxies):
            if not (isinstance(entry, (list, tuple)) and len(entry) >= 2) or i >= len(values):
                continue
            if str(entry[1]).startswith("$$") or values[i] is None:
                continue
            out[(str(entry[0]), str(entry[1]))] = values[i]
        return out

    # ------------------------------------------------------------- following links back to what feeds an input
    def resolve_input(self, slot: int, seen: set, want: str | None = None):
        """What feeds input `slot`: ("node", id, slot), ("value", v), or None (nothing - a widget keeps its own
        value)."""
        key = f"{self.id}[I]{slot}"
        if key in seen:
            raise WorkflowError(f"the links go round in a circle at node {self.id}")
        seen.add(key)
        link_id = self.link_of(slot)
        if link_id is None:
            return None
        link = self.level.links.get(link_id)
        if link is None:
            return None
        want = want or (self.inputs[slot].get("type") if slot < len(self.inputs) else None)
        if link["origin_id"] == SUBGRAPH_IN and self.holder is not None:
            holder = self.holder
            definition = self.conv.subgraphs[holder.type]
            index = int(link["origin_slot"])
            def_inputs = definition.get("inputs") or []
            name = def_inputs[index].get("name") if index < len(def_inputs) else None
            outer = holder.slot_named(name)
            if outer is not None and holder.link_of(outer) is not None:
                return holder.resolve_input(outer, seen, want)
            values = holder.promoted()
            if index in values:
                return ("value", values[index])
            if slot < len(self.inputs) and self.inputs[slot].get("widget"):
                return None                                  # the widget's own value stands
            inner = self.conv.inner_widget_value(definition, index, holder)
            return None if inner is _MISSING else ("value", inner)
        origin = self.level.nodes.get(link["origin_id"])
        if origin is None:
            return None
        return self.conv.node(origin, self.level, self.path, self.holder).resolve_output(int(link["origin_slot"]),
                                                                                         want, seen)

    def resolve_output(self, slot: int, want: str | None, seen: set):
        key = f"{self.id}[O]{slot}"
        if key in seen:
            raise WorkflowError(f"the links go round in a circle at node {self.id}")
        seen.add(key)
        if self.mode == MODE_NEVER:
            return None
        if self.mode == MODE_BYPASS:
            through = self._bypass_slot(slot, want)
            return None if through < 0 else self.resolve_input(through, seen, want)
        if self.is_subgraph:
            definition = self.conv.subgraphs[self.type]
            outs = definition.get("outputs") or []
            if slot >= len(outs):
                return None
            level = self.conv.level(definition)
            for link_id in outs[slot].get("linkIds") or []:
                link = level.links.get(int(link_id))
                if link is None:
                    continue
                inner = level.nodes.get(link["origin_id"])
                if inner is None:
                    continue
                return self.conv.node(inner, level, (*self.path, self.node["id"]), self).resolve_output(
                    int(link["origin_slot"]), want, seen)
            return None
        if self.type == "Reroute":
            return self.resolve_input(0, seen, want)
        if self.type == "PrimitiveNode":
            values = self.node.get("widgets_values")
            return ("value", values[0]) if isinstance(values, list) and values else None
        if self.type in NOTES:
            return None
        return ("node", self.id, slot)

    def _bypass_slot(self, slot: int, want: str | None) -> int:
        """Which input a bypassed node passes on to output `slot`: the same slot when its type fits, else the
        first input of the wanted type, else the first that fits (the editor's rule)."""
        if not self.inputs:
            return -1
        out_type = self.outputs[slot].get("type") if slot < len(self.outputs) else "*"
        if want in (None, "*", ""):
            return slot if len(self.inputs) > slot else 0
        if slot < len(self.inputs):
            t = self.inputs[slot].get("type") or "*"
            if _types_fit(t, out_type) and _types_fit(t, want):
                return slot
        for i, inp in enumerate(self.inputs):
            if inp.get("type") == want:
                return i
        for i, inp in enumerate(self.inputs):
            t = inp.get("type") or "*"
            if _types_fit(t, out_type) and _types_fit(t, want):
                return i
        return -1


_MISSING = object()


def _is_link(value) -> bool:
    return (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
            and isinstance(value[1], int) and not isinstance(value[1], bool))


def _viewport(node: dict) -> dict:
    """A 3D node's viewer state as the editor sends it: no pictures, and the camera saved with the node (or the
    viewer's default)."""
    cam = (node.get("properties") or {}).get("Camera Info")
    if not isinstance(cam, dict):
        cam = {"position": {"x": 10, "y": 10, "z": 10}, "target": {"x": 0, "y": 0, "z": 0}, "zoom": 1,
               "cameraType": "perspective"}
    return {"image": "", "mask": "", "normal": "", "camera_info": cam}


class _Converter:
    def __init__(self, workflow: dict, info: dict):
        self.workflow = workflow
        self.info = info
        defs = (workflow.get("definitions") or {}).get("subgraphs") or []
        self.subgraphs = {d["id"]: d for d in defs if isinstance(d, dict) and d.get("id")}
        self._levels: dict[int, _Level] = {}
        self._nodes: dict[tuple, _Node] = {}
        self._cache: dict = {}
        self.controls: dict[str, dict] = {}            # node id -> input -> the editor's seed control

    def level(self, data: dict) -> _Level:
        key = id(data)
        if key not in self._levels:
            self._levels[key] = _Level(data)
        return self._levels[key]

    def node(self, node: dict, level: _Level, path: tuple, holder: _Node | None) -> _Node:
        key = (id(level), path, node["id"])
        if key not in self._nodes:
            self._nodes[key] = _Node(self, node, level, path, holder)
        return self._nodes[key]

    def widget_inputs(self, definition: dict) -> list[int]:
        """The subgraph's inputs that feed a widget inside (their index in its definition), in order: the ones a
        subgraph node shows as widgets of its own."""
        key = ("widgets", id(definition))
        if key in self._cache:
            return self._cache[key]
        level = self.level(definition)
        out = []
        for index, inp in enumerate(definition.get("inputs") or []):
            for link_id in inp.get("linkIds") or []:
                link = level.links.get(int(link_id))
                target = level.nodes.get(link["target_id"]) if link else None
                t_inputs = (target or {}).get("inputs") or []
                t_slot = int(link["target_slot"]) if link else -1
                if 0 <= t_slot < len(t_inputs) and t_inputs[t_slot].get("widget"):
                    out.append(index)
                    break
        self._cache[key] = out
        return out

    def own_widgets(self, n: "_Node", controls: dict | None = None) -> dict[str, Any]:
        """A node's widget values from its own list - with what a subgraph node holding it shows for them."""
        entry = self.info[n.type]
        values = n.node.get("widgets_values")
        if isinstance(values, dict):                        # some nodes keep their values by name
            inputs = dict(values)
        else:
            inputs = assign_widgets(entry, values if isinstance(values, list) else [], controls)
        if n.holder is not None:
            for (node_id, widget), value in n.holder.proxied().items():
                if node_id == str(n.node["id"]) and widget in inputs:
                    inputs[widget] = value
        return inputs

    def inner_widget_value(self, definition: dict, index: int, holder: "_Node"):
        """The value of the inner widget a subgraph input feeds (the first one): what a subgraph node shows for an
        input that feeds no widget of its own but a widget inside."""
        ins = definition.get("inputs") or []
        if index >= len(ins):
            return _MISSING
        level = self.level(definition)
        for link_id in ins[index].get("linkIds") or []:
            link = level.links.get(int(link_id))
            target = level.nodes.get(link["target_id"]) if link else None
            if target is None:
                continue
            t_inputs = target.get("inputs") or []
            t_slot = int(link["target_slot"])
            if t_slot >= len(t_inputs):
                continue
            widget = (t_inputs[t_slot].get("widget") or {}).get("name")
            if not widget or target.get("type") in self.subgraphs or target.get("type") not in self.info:
                continue
            values = self.own_widgets(self.node(target, level, (*holder.path, holder.node["id"]), holder))
            if widget in values:
                return values[widget]
        return _MISSING

    def flatten(self, level: _Level, path: tuple, holder: _Node | None, out: list) -> None:
        for raw in level.nodes.values():
            if len(out) >= MAX_NODES:
                raise WorkflowError(f"it has more than {MAX_NODES} nodes once its subgraphs are opened")
            n = self.node(raw, level, path, holder)
            if n.is_subgraph:
                if n.mode in (MODE_NEVER, MODE_BYPASS):
                    continue
                definition = self.subgraphs[n.type]
                if len(path) > 16:
                    raise WorkflowError("subgraphs are nested too deep")
                self.flatten(self.level(definition), (*path, raw["id"]), n, out)
            else:
                out.append(n)

    def run(self) -> dict:
        nodes: list[_Node] = []
        self.flatten(self.level(self.workflow), (), None, nodes)
        unknown = sorted({n.type for n in nodes if n.type not in VIRTUAL and n.type not in self.info
                          and n.mode not in (MODE_NEVER, MODE_BYPASS)})
        if unknown:
            raise UnknownNodes(unknown)
        prompt: dict[str, dict] = {}
        for n in nodes:
            if n.type in VIRTUAL or n.mode in (MODE_NEVER, MODE_BYPASS):
                continue
            entry = self.info[n.type]
            mine: dict = {}
            inputs = self.own_widgets(n, mine)
            if mine:
                self.controls[n.id] = mine
            for slot, inp in enumerate(n.inputs):
                name = inp.get("name")
                if not name or inp.get("link") is None:
                    continue
                got = n.resolve_input(slot, set())
                if got is None:
                    continue
                if got[0] == "value":
                    inputs[name] = got[1]
                else:
                    inputs[name] = [got[1], got[2]]
            for name, value in list(inputs.items()):
                if isinstance(value, list) and not _is_link(value):
                    inputs[name] = {"__value__": value}       # a list value, which would read as a link
            for name, kind, opts in ordered_inputs(entry):
                if kind == "LOAD_3D" and not isinstance(inputs.get(name), dict):
                    inputs[name] = _viewport(n.node)          # the editor's 3D viewer state, which it makes on queue
            title = n.node.get("title") or entry.get("display_name") or n.type
            prompt[n.id] = {"inputs": inputs, "class_type": n.type, "_meta": {"title": title}}
        # links to nodes that are not in the prompt (muted, a note) go, as in the editor
        for item in prompt.values():
            for name, value in list(item["inputs"].items()):
                if _is_link(value) and value[0] not in prompt:
                    del item["inputs"][name]
        if not prompt:
            raise WorkflowError("the workflow has no nodes that run")
        return prompt


class UnknownNodes(WorkflowError):
    """Nodes this ComfyUI does not have: custom nodes, or newer than its version."""

    def __init__(self, types: list[str]):
        super().__init__(f"{len(types)} kind{'s' if len(types) != 1 else ''} of node this ComfyUI does not have: "
                         + ", ".join(types[:20]) + (" ..." if len(types) > 20 else ""))
        self.types = types


def _shape(workflow: dict) -> None:
    """The editor's file as the converter reads it - lists and objects where they belong - so a damaged or hostile
    file is refused here rather than half-read."""
    def nodes_ok(nodes) -> bool:
        return isinstance(nodes, list) and all(
            isinstance(n, dict) and isinstance(n.get("id"), (int, str)) and isinstance(n.get("type"), str)
            and isinstance(n.get("inputs") or [], list) and isinstance(n.get("outputs") or [], list)
            and isinstance(n.get("properties") or {}, dict)
            and all(isinstance(i, dict) for i in (n.get("inputs") or []) + (n.get("outputs") or []))
            for n in nodes)
    if not nodes_ok(workflow.get("nodes")) or not isinstance(workflow.get("links") or [], list):
        raise WorkflowError("its nodes or links are not as ComfyUI writes them")
    definitions = workflow.get("definitions") or {}
    subs = definitions.get("subgraphs") or [] if isinstance(definitions, dict) else None
    if not isinstance(subs, list) or not all(
            isinstance(d, dict) and nodes_ok(d.get("nodes") or []) and isinstance(d.get("links") or [], list)
            and isinstance(d.get("inputs") or [], list) and isinstance(d.get("outputs") or [], list)
            and all(isinstance(x, dict) for x in (d.get("inputs") or []) + (d.get("outputs") or []))
            for d in subs):
        raise WorkflowError("its subgraphs are not as ComfyUI writes them")


def to_api(workflow: dict, info: dict, controls: dict | None = None) -> dict:
    """The editor's workflow in API format. WorkflowError says what stops it; UnknownNodes lists the node kinds
    this ComfyUI does not have. `controls` gets each seed's "control after generate" (node id -> input -> mode)."""
    if not is_editor(workflow):
        raise WorkflowError("this is not a ComfyUI workflow (no nodes and links)")
    _shape(workflow)
    conv = _Converter(workflow, info)
    try:
        prompt = conv.run()
    except WorkflowError:
        raise
    except RecursionError:
        raise WorkflowError("its links run through too many nodes to follow")
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise WorkflowError(f"a part of it could not be read ({exc.__class__.__name__}: {str(exc)[:120]})")
    if controls is not None:
        controls.update(conv.controls)
    return prompt


def parse(data: bytes) -> tuple[str, dict]:
    """A workflow file: JSON in either format, or a picture ComfyUI saved (its "prompt" in API format, else its
    "workflow"). Returns ("api" | "editor", the data)."""
    head = data[:16]
    if head.startswith(b"\x89PNG") or head[:4] == b"RIFF" or head[:3] == b"\xff\xd8\xff":
        return _from_picture(data)
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise WorkflowError("the file is not JSON and not a picture with a workflow in it")
    if isinstance(obj, dict) and isinstance(obj.get("prompt"), dict) and is_api(obj["prompt"]):
        return "api", obj["prompt"]                           # {"prompt": {...}} as /prompt takes it
    if is_api(obj):
        return "api", obj
    if is_editor(obj):
        return "editor", obj
    raise WorkflowError("this JSON is not a ComfyUI workflow (neither the editor's nodes and links nor the API format)")


def _from_picture(data: bytes) -> tuple[str, dict]:
    import io

    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            meta = dict(getattr(img, "text", {}) or {})
            meta.update({k: v for k, v in img.info.items() if isinstance(v, str)})
            exif = img.getexif() if hasattr(img, "getexif") else {}
    except Exception as exc:  # noqa: BLE001 - PIL raises many kinds
        raise WorkflowError(f"the picture could not be read ({exc.__class__.__name__})")
    for key, kind in (("prompt", "api"), ("workflow", "editor")):
        raw = meta.get(key)
        if raw:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if (kind == "api" and is_api(obj)) or (kind == "editor" and is_editor(obj)):
                return kind, obj
    # WebP from ComfyUI keeps them in EXIF, as "prompt:{...}" and "workflow:{...}"
    for value in (exif or {}).values():
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if isinstance(value, str):
            for key, kind in (("prompt:", "api"), ("workflow:", "editor")):
                if value.startswith(key):
                    try:
                        obj = json.loads(value[len(key):])
                    except ValueError:
                        continue
                    if (kind == "api" and is_api(obj)) or (kind == "editor" and is_editor(obj)):
                        return kind, obj
    raise WorkflowError("the picture has no ComfyUI workflow in it (ComfyUI saves one unless told not to)")
