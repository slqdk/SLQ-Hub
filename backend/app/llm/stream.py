"""Putting a streamed answer back together, and tool calls the server did
not parse.

llama-server parses Qwen3.x tool calls (Qwen3-Coder XML) into OpenAI
`tool_calls` itself. When it does not - an older template, a model that
wrote the call in its text - parse_text_tool_calls() recovers them from the
content, in either the XML form or Hermes-style JSON.
"""
from __future__ import annotations

import json
import logging
import re
import secrets

log = logging.getLogger("hub.llm")

# Gemma 4 writes a call as <|tool_call>call:name{key:<|"|>text<|"|>,...}<tool_call|>. Its slip, seen twice in a row on
# 2026-11-04 (0.79.1, chat 292, Gemma 4 31B): a string closed with a backtick instead of <|"|>, so llama-server read on
# into the next call - the picture's save_as became 'x.jpg`}<tool_call|><|tool_call>call:post_draft{media:[', the
# picture was saved under that name, and post_draft found no x.jpg three times. Kept in the chat, the broken call was
# copied in the next run: the history the model is sent leaves it out too.
LEAK = re.compile(r"<tool_call\|>|<\|tool_call>")
LEAK_IN_TEXT = re.compile(r"[`'\"]*\}*\s*(?:<tool_call\|>\s*)?<\|tool_call>call:\w+\{\S*")


def cut_leak(value: str) -> str:
    """A string argument without the start of another call that leaked into it, nor the junk the slip left."""
    m = LEAK.search(value)
    if m is None:
        return value
    return value[:m.start()].rstrip().rstrip("`'\"}]),;").rstrip()


def clean_arguments(args):
    """(the arguments with cut_leak on every string in them, whether any was cut)."""
    if isinstance(args, str):
        out = cut_leak(args)
        return out, out != args
    if isinstance(args, list):
        pairs = [clean_arguments(v) for v in args]
        return [v for v, _ in pairs], any(c for _, c in pairs)
    if isinstance(args, dict):
        pairs = {k: clean_arguments(v) for k, v in args.items()}
        return {k: v for k, (v, _) in pairs.items()}, any(c for _, c in pairs.values())
    return args, False


def clean_result_text(text: str) -> str:
    """A tool's result as the model sees it again, without a leaked call (a file name the slip had spoilt)."""
    return LEAK_IN_TEXT.sub("", text) if "<|tool_call>" in text else text


def new_call_id() -> str:
    return "call_" + secrets.token_hex(8)


class Accumulator:
    """Feed it llama-server chunks; it keeps the whole answer and returns
    small events for the caller to forward (text, thinking, tool calls)."""

    def __init__(self) -> None:
        self.content = ""
        self.reasoning = ""
        self.tool_calls: dict[int, dict] = {}
        self.finish_reason: str | None = None
        self.usage: dict = {}
        self.timings: dict = {}

    def feed(self, chunk: dict) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        if chunk.get("timings"):
            self.timings = chunk["timings"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or choice.get("message") or {}
            r = delta.get("reasoning_content")
            if r:
                self.reasoning += r
                events.append(("thinking", {"text": r}))
            t = delta.get("content")
            if t:
                self.content += t
                events.append(("text", {"text": t}))
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index", len(self.tool_calls)))
                cur = self.tool_calls.get(idx)
                fn = tc.get("function") or {}
                if cur is None:
                    cur = self.tool_calls[idx] = {"id": tc.get("id") or new_call_id(),
                                                  "name": fn.get("name") or "", "arguments": ""}
                    events.append(("tool_start", {"id": cur["id"], "name": cur["name"]}))
                elif fn.get("name") and not cur["name"]:
                    cur["name"] = fn["name"]
                if fn.get("arguments"):
                    cur["arguments"] += fn["arguments"]
                    events.append(("tool_args", {"id": cur["id"], "delta": fn["arguments"]}))
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
        return events

    def result(self) -> dict:
        calls = [self.tool_calls[i] for i in sorted(self.tool_calls)]
        content = self.content
        if not calls and "<tool_call>" in content:
            content, calls = parse_text_tool_calls(content)
            if calls:
                self.finish_reason = "tool_calls"
        for c in calls:
            if "tool_call" not in (c.get("arguments") or ""):
                continue
            try:
                args = json.loads(c["arguments"])
            except (TypeError, ValueError):
                continue
            args, cut = clean_arguments(args)
            if cut:                                  # the start of another call leaked into an argument
                log.warning("%s: a call's start leaked into its arguments - cut: %s", c.get("name"),
                            c["arguments"][-160:])
                c["arguments"] = json.dumps(args, ensure_ascii=False)
        return {"content": content, "reasoning": self.reasoning, "tool_calls": calls,
                "finish_reason": self.finish_reason or ("tool_calls" if calls else "stop"),
                "usage": self.usage, "timings": self.timings}


_TOOL_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
_FUNCTION = re.compile(r"<function=([^>\s]+)>\s*(.*?)\s*(?:</function>|$)", re.S)
_PARAM = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", re.S)


def _value(raw: str):
    s = raw.strip()
    if s and (s[0] in "[{\"" or s in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", s)):
        try:
            return json.loads(s)
        except ValueError:
            pass
    return raw.strip("\n")


def parse_text_tool_calls(text: str) -> tuple[str, list[dict]]:
    """(content without the calls, [{id, name, arguments(json str)}])."""
    calls: list[dict] = []
    for block in _TOOL_BLOCK.findall(text):
        fm = _FUNCTION.search(block)
        if fm:
            args = {k: _value(v) for k, v in _PARAM.findall(fm.group(2))}
            calls.append({"id": new_call_id(), "name": fm.group(1), "arguments": json.dumps(args)})
            continue
        try:
            obj = json.loads(block)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("name"):
            args = obj.get("arguments", obj.get("parameters", {}))
            calls.append({"id": new_call_id(), "name": obj["name"],
                          "arguments": args if isinstance(args, str) else json.dumps(args)})
    if not calls:
        return text, []
    cleaned = _TOOL_BLOCK.sub("", text).strip()
    return cleaned, calls


def parse_arguments(raw: str | dict | None) -> dict:
    """Tool-call arguments as a dict; tolerant of a model's sloppy JSON."""
    if isinstance(raw, dict):
        return raw
    if not raw or not str(raw).strip():
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"value": val}
    except ValueError:
        pass
    s = str(raw).strip()
    # common slips: trailing commas, single quotes, a missing closing brace
    fixed = re.sub(r",\s*([}\]])", r"\1", s)
    if fixed.count("{") > fixed.count("}"):
        fixed += "}" * (fixed.count("{") - fixed.count("}"))
    try:
        val = json.loads(fixed)
        return val if isinstance(val, dict) else {"value": val}
    except ValueError:
        raise ValueError(f"the tool arguments are not valid JSON: {s[:200]}")


def valid_arguments(raw: str | dict | None) -> str:
    """Tool-call arguments as JSON object text a chat template can render, for the history sent to a model: as
    they are when they parse, repaired when parse_arguments can, else a small object saying they were not valid
    JSON. llama-server parses every earlier call of the conversation and refuses the whole request over one it
    cannot (chat 21: a call the context's end had cut off, sent back at the next step)."""
    if isinstance(raw, dict):
        return json.dumps(clean_arguments(raw)[0], ensure_ascii=False)
    try:
        val = json.loads(raw or "{}")
        if isinstance(val, dict):
            val, cut = clean_arguments(val)          # a call stored before 0.79.1 with another's start in it
            return json.dumps(val, ensure_ascii=False) if cut else (raw or "{}")
    except (TypeError, ValueError):
        pass
    try:
        return json.dumps(parse_arguments(raw), ensure_ascii=False)
    except ValueError:
        s = str(raw)
        more = f"... [{len(s) - 300:,} more characters]" if len(s) > 300 else ""
        return json.dumps({"_not_valid_json": s[:300] + more}, ensure_ascii=False)


def salvage_cut_write(raw: str) -> dict | None:
    """write_file arguments the end of the context cut off inside their content: {"path", "content"} (and
    "append") with what the model had written. None unless the path is whole and the cut is in the content."""
    for drop in range(7):                            # the cut may split an escape: \ or \u00e
        if drop > len(raw):
            break
        try:
            val = json.loads(raw[:len(raw) - drop] + '"}')
        except ValueError:
            continue
        if not (isinstance(val, dict) and isinstance(val.get("path"), str) and val["path"].strip()
                and isinstance(val.get("content"), str) and val["content"]
                and set(val) <= {"path", "content", "append"} and list(val)[-1] == "content"):
            return None
        content = val["content"]
        if "\ud800" <= content[-1] <= "\udbff":    # half of a pair the cut split
            content = content[:-1]
        out = {"path": val["path"], "content": content}
        if val.get("append") is True:
            out["append"] = True
        return out if content else None
    return None
