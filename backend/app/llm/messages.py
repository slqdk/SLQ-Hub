"""Messages in the shape llama-server wants (OpenAI chat format), from the
shapes other clients send (Ollama, Anthropic), and images made ready for
the vision encoder."""
from __future__ import annotations

import base64
import io
import json

from PIL import Image, ImageOps

from .. import netfetch
from .stream import new_call_id

MAX_SIDE = 1568


# ---------------------------------------------------------------- images

def prepare_image(data: bytes, max_side: int = MAX_SIDE) -> tuple[bytes, str]:
    """Upright, at most max_side on the long edge; PNG when it has
    transparency, otherwise JPEG. Returns (bytes, mime)."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"not an image the Hub can read ({exc.__class__.__name__})") from exc
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    out = io.BytesIO()
    if has_alpha:
        img.convert("RGBA").save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png"
    img.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue(), "image/jpeg"


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def image_part(data: bytes) -> dict:
    prepared, mime = prepare_image(data)
    return {"type": "image_url", "image_url": {"url": data_url(prepared, mime)}}


def decode_data_url(url: str) -> bytes:
    head, _, b64 = url.partition(",")
    if not head.startswith("data:") or ";base64" not in head:
        raise ValueError("not a base64 data URL")
    return base64.b64decode(b64)


async def inline_image_url(url: str, extra_blocked=()) -> dict:
    """An image_url part with the image inlined (and downscaled). http(s)
    URLs are fetched through the SSRF guard (the private ranges and
    `extra_blocked`: the LAN's, Settings'); llama-server never fetches."""
    if url.startswith("data:"):
        return image_part(decode_data_url(url))
    data, _ctype, _final = await netfetch.fetch(url, max_bytes=20_000_000, accept="image/*",
                                                extra_blocked=extra_blocked)
    return image_part(data)


async def normalise_openai(messages: list[dict], extra_blocked=()) -> list[dict]:
    """OpenAI-format messages from an app, with every image inlined."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            parts = []
            for p in c:
                t = p.get("type")
                if t == "image_url":
                    url = p["image_url"]["url"] if isinstance(p.get("image_url"), dict) else p.get("image_url")
                    parts.append(await inline_image_url(url, extra_blocked))
                elif t in ("text", "input_text"):
                    parts.append({"type": "text", "text": p.get("text", "")})
                else:
                    parts.append(p)
            m = {**m, "content": parts}
        out.append(m)
    return out


# ---------------------------------------------------------------- Ollama

def _args_json(args) -> str:
    return args if isinstance(args, str) else json.dumps(args or {})


def from_ollama(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    open_calls: list[tuple[str, str]] = []          # (name, id) waiting for a tool result
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content") or ""
        if role == "assistant":
            msg: dict = {"role": "assistant", "content": content}
            calls = []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                cid = tc.get("id") or new_call_id()
                calls.append({"id": cid, "type": "function",
                              "function": {"name": fn.get("name", ""), "arguments": _args_json(fn.get("arguments"))}})
                open_calls.append((fn.get("name", ""), cid))
            if calls:
                msg["tool_calls"] = calls
            if m.get("thinking"):
                msg["reasoning_content"] = m["thinking"]
            out.append(msg)
        elif role == "tool":
            name = m.get("tool_name") or m.get("name") or ""
            idx = next((i for i, (n, _) in enumerate(open_calls) if n == name), 0 if open_calls else None)
            cid = open_calls.pop(idx)[1] if idx is not None else new_call_id()
            out.append({"role": "tool", "tool_call_id": cid, "content": content})
        else:
            images = m.get("images") or []
            if images and role == "user":
                parts: list[dict] = [image_part(base64.b64decode(i.split(",", 1)[-1])) for i in images]
                if content:
                    parts.append({"type": "text", "text": content})
                out.append({"role": "user", "content": parts})
            else:
                out.append({"role": role, "content": content})
    return out


def ollama_format(fmt) -> dict | None:
    if not fmt:
        return None
    if fmt == "json":
        return {"type": "json_object"}
    if isinstance(fmt, dict):
        return {"type": "json_schema", "json_schema": {"name": "response", "schema": fmt}}
    return None


# ---------------------------------------------------------------- Anthropic

def _anthropic_image(source: dict) -> dict:
    if source.get("type") == "base64":
        return image_part(base64.b64decode(source.get("data", "")))
    if source.get("type") == "url":
        return {"type": "image_url", "image_url": {"url": source.get("url", "")}}   # inlined later
    raise ValueError("unsupported image source")


def _text_of(content) -> tuple[str, list[dict]]:
    """Text and images from a tool_result's content."""
    if isinstance(content, str):
        return content, []
    texts, images = [], []
    for b in content or []:
        if b.get("type") == "text":
            texts.append(b.get("text", ""))
        elif b.get("type") == "image":
            images.append(_anthropic_image(b.get("source") or {}))
    return "\n".join(texts), images


def from_anthropic(system, messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    if system:
        text = system if isinstance(system, str) else "\n\n".join(
            b.get("text", "") for b in system if b.get("type") == "text")
        if text:
            out.append({"role": "system", "content": text})
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text, reasoning, calls = "", "", []
            for b in content or []:
                t = b.get("type")
                if t == "text":
                    text += b.get("text", "")
                elif t == "thinking":
                    reasoning += b.get("thinking", "")
                elif t in ("tool_use", "server_tool_use"):
                    calls.append({"id": b.get("id") or new_call_id(), "type": "function",
                                  "function": {"name": b.get("name", ""), "arguments": json.dumps(b.get("input") or {})}})
            msg: dict = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = calls
            if reasoning:
                msg["reasoning_content"] = reasoning
            out.append(msg)
            continue
        parts: list[dict] = []
        tool_msgs: list[dict] = []
        tool_images: list[dict] = []
        for b in content or []:
            t = b.get("type")
            if t == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif t == "image":
                parts.append(_anthropic_image(b.get("source") or {}))
            elif t == "document":
                src = b.get("source") or {}
                if src.get("type") == "text":
                    parts.append({"type": "text", "text": src.get("data", "")})
            elif t == "tool_result":
                text, imgs = _text_of(b.get("content"))
                if b.get("is_error"):
                    text = "Error: " + text
                tool_msgs.append({"role": "tool", "tool_call_id": b.get("tool_use_id", ""), "content": text})
                tool_images.extend(imgs)
        out.extend(tool_msgs)
        if tool_images:
            parts = [{"type": "text", "text": "(Images returned by the tool above.)"}, *tool_images, *parts]
        if parts:
            if all(p["type"] == "text" for p in parts):
                out.append({"role": "user", "content": "\n\n".join(p["text"] for p in parts)})
            else:
                out.append({"role": "user", "content": parts})
    return out


def anthropic_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    out = []
    for t in tools:
        if "input_schema" not in t:          # server tools (web_search_2025...) - not ours to run
            continue
        out.append({"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                     "parameters": t.get("input_schema") or {"type": "object"}}})
    return out or None


def anthropic_tool_choice(choice: dict | None):
    if not choice:
        return None
    kind = choice.get("type")
    if kind == "any":
        return "required"
    if kind == "tool":
        return {"type": "function", "function": {"name": choice.get("name", "")}}
    if kind == "none":
        return "none"
    return "auto"
