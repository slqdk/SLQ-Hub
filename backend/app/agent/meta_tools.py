"""escalate (hand the turn to a stronger model: on this server, or the cloud) and task (a subagent)."""
from __future__ import annotations

from .tools.base import Tool, ToolContext, ToolResult, obj, register

SUBAGENT_STEPS = 30


async def escalate(ctx: ToolContext, args: dict) -> ToolResult:
    reason = " ".join(str(args.get("reason") or "").split())[:300] or "the model asked for help"
    ctx.state["escalate"] = reason
    who = ctx.engine.handover_label() if ctx.engine is not None else ""
    return ToolResult(f"Handing this turn over to {who or 'a stronger model'} (reason: {reason}).")


ESCALATE_TEXT = ("Hand this turn over to {who} when the task is clearly beyond you: you keep failing, or it needs "
                 "knowledge or reasoning you lack. Not for simple things.")


def _escalate_dynamic(ctx: ToolContext) -> tuple[str, dict]:
    who = ctx.engine.handover_label() if ctx.engine is not None else ""
    return ESCALATE_TEXT.format(who=who or "a stronger model"), ESCALATE_PARAMS


async def task(ctx: ToolContext, args: dict) -> ToolResult:
    """Run a focused job in a fresh context and return only its report - the
    main conversation stays small. It shares the turn's lease on the card,
    so subagents run one at a time."""
    from .loop import Engine
    parent = ctx.engine
    agent = ctx.hub.extras.get("agent")
    if parent is None or agent is None:
        return ToolResult("Subagents are not available here.", is_error=True)
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("prompt is required: describe the task completely - the subagent sees nothing else.",
                          is_error=True)
    description = " ".join(str(args.get("description") or prompt).split())[:80]
    sub = Engine(ctx.hub, ctx.run, ctx.chat, ctx.project, ctx.workdir, ctx.model, parent.ticket, depth=1,
                 parent=ctx.call_id, thinking=parent.thinking, effort=parent.effort, escalation=False)
    sub.cloud = parent.cloud
    sub.device = parent.device                       # a person's chat: the same limits for its subagents
    system = agent.system_for(ctx.chat, ctx.project, ctx.workdir, ctx.model, subagent=True,
                              cloud_model=parent.cloud.label if parent.cloud else "", device=parent.device)
    ctx.emit("subagent", id=ctx.call_id, description=description, state="started")
    try:
        report = await sub.loop([{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                                persist=None, max_steps=SUBAGENT_STEPS)
    finally:
        parent.steps += sub.steps
        for k, v in sub.usage.items():
            parent.usage[k] = parent.usage.get(k, 0) + v
        ctx.emit("subagent", id=ctx.call_id, description=description, state="finished")
    steps = sum(1 for s in sub.steps if s.get("type") == "tool")
    return ToolResult(report.strip() or "(The subagent finished without a report.)",
                      display={"subagent": {"description": description, "tool_calls": steps}})


ESCALATE_PARAMS = obj({"reason": {"type": "string", "description": "One sentence: why"}}, ["reason"])
register(Tool("escalate", ESCALATE_TEXT.format(who="a stronger model"), ESCALATE_PARAMS, escalate, in_subagent=False,
              group="meta", dynamic=_escalate_dynamic))
register(Tool("task", "Give a focused job to a subagent that works with the same tools in a fresh context and "
              "returns only its final report. Use it for research or exploration that would fill this "
              "conversation with intermediate output (reading many files or pages). The subagent sees nothing "
              "of this conversation: put everything it needs in the prompt, and say what the report must "
              "contain.",
              obj({"description": {"type": "string", "description": "3-6 words shown to the user"},
                   "prompt": {"type": "string", "description": "The complete task"}}, ["description", "prompt"]),
              task, in_subagent=False, group="meta"))
