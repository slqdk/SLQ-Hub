"""Building llama-server requests: sampling from the model card, thinking
on/off and effort, tools, structured output."""
from __future__ import annotations

SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty",
                 "repeat_penalty", "seed", "stop", "max_tokens", "logit_bias", "n_probs")


# What the model "thinks" last when its thinking budget runs out, or when the user presses Stop thinking (0.30.0):
# llama-server forces these words and the end of thinking, and the model goes on with a tool call or its answer.
BUDGET_MESSAGE = ("\n\n(That is enough thinking for this step - I act on what I have now: a tool call to check or "
                  "measure what is still open, or my answer.)\n")


def thinking_fields(model: dict, thinking: bool | None, effort: str | None = None, control: bool = False) -> dict:
    """chat_template_kwargs / reasoning_effort for the request. The Qwen3.8
    template raises an error for any effort it does not know, so only the
    model's own list is ever sent. control (the Hub's own turns, not the model
    API's callers): reasoning control on, so the Hub can end the thinking -
    at its budget, or when the user presses Stop thinking - through
    llama-server's POST /v1/chat/completions/control, which forces
    BUDGET_MESSAGE and the end tag (b11783). The budget is counted by the Hub,
    not sent as reasoning_budget_tokens: once the thinking has ended, b11783's
    budget sampler starts counting again at a <think> in the answer and would
    force its message into the answer or a tool call's arguments - code that
    mentions the tag, say (measured: docs/HANDOFF.md)."""
    caps = model.get("capabilities", [])
    if "thinking" not in caps or thinking is None:
        return {}
    out: dict = {"chat_template_kwargs": {"enable_thinking": bool(thinking)}}
    efforts = (model.get("thinking") or {}).get("efforts") or []
    if thinking and efforts:
        e = normalise_effort(effort, efforts) or (model.get("thinking") or {}).get("default_effort")
        if e in efforts:
            out["reasoning_effort"] = e
    if thinking and control:
        out["reasoning_control"] = True
        out["reasoning_budget_message"] = BUDGET_MESSAGE
    return out


def thinking_end(reasoning: str) -> bool:
    """Whether a step's thinking was ended by the Hub's budget message (the budget, or Stop thinking)."""
    return bool(reasoning) and reasoning.rstrip().endswith(BUDGET_MESSAGE.strip())


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


# llama-server flags (Settings -> Models -> extra flags) that set a sampling value: when one is given, the
# request leaves that value out, so the flag is what counts.
FLAG_KEYS = {"--temp": "temperature", "--temperature": "temperature", "--top-p": "top_p", "--top-k": "top_k",
             "--min-p": "min_p", "--presence-penalty": "presence_penalty", "--frequency-penalty": "frequency_penalty",
             "--repeat-penalty": "repeat_penalty"}


def sampling_defaults(model: dict, thinking: bool | None) -> dict:
    s = model.get("sampling") or {}
    mode = "thinking" if (thinking if thinking is not None else (model.get("thinking") or {}).get("default")) else "instruct"
    out = dict(s.get(mode) or {})
    for flag in (model.get("server") or {}).get("extra") or []:
        key = FLAG_KEYS.get(str(flag).split("=", 1)[0])
        if key:
            out.pop(key, None)
    return out


def build(model: dict, messages: list[dict], *, tools: list | None = None, tool_choice=None,
          thinking: bool | None = None, effort: str | None = None, sampling: dict | None = None,
          response_format: dict | None = None, stream: bool = True, extra: dict | None = None,
          control: bool = False) -> dict:
    payload: dict = {"model": model["id"], "messages": messages, "stream": stream}
    if stream:
        payload["stream_options"] = {"include_usage": True}
    payload.update(sampling_defaults(model, thinking))
    for k, v in (sampling or {}).items():
        if k in SAMPLING_KEYS and v is not None:
            payload[k] = v
    payload.update(thinking_fields(model, thinking, effort, control))
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
        payload["parallel_tool_calls"] = True
    if response_format:
        payload["response_format"] = response_format
    if extra:
        payload.update(extra)
    return payload
