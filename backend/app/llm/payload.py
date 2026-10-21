"""Building llama-server requests: sampling from the model card, thinking
on/off and effort, tools, structured output."""
from __future__ import annotations

SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty",
                 "repeat_penalty", "seed", "stop", "max_tokens", "logit_bias", "n_probs")


def thinking_fields(model: dict, thinking: bool | None, effort: str | None = None) -> dict:
    """chat_template_kwargs / reasoning_effort for the request. The Qwen3.8
    template raises an error for any effort it does not know, so only the
    model's own list is ever sent."""
    caps = model.get("capabilities", [])
    if "thinking" not in caps or thinking is None:
        return {}
    out: dict = {"chat_template_kwargs": {"enable_thinking": bool(thinking)}}
    efforts = (model.get("thinking") or {}).get("efforts") or []
    if thinking and efforts:
        e = normalise_effort(effort, efforts) or (model.get("thinking") or {}).get("default_effort")
        if e in efforts:
            out["reasoning_effort"] = e
    return out


def normalise_effort(effort: str | None, allowed: list[str]) -> str | None:
    if not effort:
        return None
    e = str(effort).lower().strip()
    mapping = {"high": "xhigh", "max": "xhigh", "maximum": "xhigh", "minimal": "low", "min": "low"}
    e = mapping.get(e, e)
    if e in allowed:
        return e
    if e not in ("none", "off") and allowed:
        return allowed[min(len(allowed) - 1, 1)]
    return None


def sampling_defaults(model: dict, thinking: bool | None) -> dict:
    s = model.get("sampling") or {}
    mode = "thinking" if (thinking if thinking is not None else (model.get("thinking") or {}).get("default")) else "instruct"
    return dict(s.get(mode) or {})


def build(model: dict, messages: list[dict], *, tools: list | None = None, tool_choice=None,
          thinking: bool | None = None, effort: str | None = None, sampling: dict | None = None,
          response_format: dict | None = None, stream: bool = True, extra: dict | None = None) -> dict:
    payload: dict = {"model": model["id"], "messages": messages, "stream": stream}
    if stream:
        payload["stream_options"] = {"include_usage": True}
    payload.update(sampling_defaults(model, thinking))
    for k, v in (sampling or {}).items():
        if k in SAMPLING_KEYS and v is not None:
            payload[k] = v
    payload.update(thinking_fields(model, thinking, effort))
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
        payload["parallel_tool_calls"] = True
    if response_format:
        payload["response_format"] = response_format
    if extra:
        payload.update(extra)
    return payload
