"""escalate (hand the turn to a cloud model) and task (a subagent)."""
from __future__ import annotations

from .tools.base import Tool, ToolContext, ToolResult, obj, register

SUBAGENT_STEPS = 30


async def escalate(ctx: ToolContext, args: dict) -> ToolResult:
    reason = " ".join(str(args.get("reason") or "").split())[:300] or "the local model asked for help"
    ctx.state["escalate"] = reason
    return ToolResult(f"Handing this turn over to the cloud model (reason: {reason}).")


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
    system = agent.system_for(ctx.chat, ctx.project, ctx.workdir, ctx.model, escalation=False, subagent=True,
                              cloud_model=parent.cloud.label if parent.cloud else "")
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


register(Tool("escalate", "Hand this turn over to a stronger cloud model when the task is clearly beyond you: you "
              "keep failing, or it needs knowledge or reasoning you lack. Not for simple things.",
              obj({"reason": {"type": "string", "description": "One sentence: why"}}, ["reason"]),
              escalate, in_subagent=False, group="meta"))
register(Tool("task", "Give a focused job to a subagent that works with the same tools in a fresh context and "
              "returns only its final report. Use it for research or exploration that would fill this "
              "conversation with intermediate output (reading many files or pages). The subagent sees nothing "
              "of this conversation: put everything it needs in the prompt, and say what the report must "
              "contain.",
              obj({"description": {"type": "string", "description": "3-6 words shown to the user"},
                   "prompt": {"type": "string", "description": "The complete task"}}, ["description", "prompt"]),
              task, in_subagent=False, group="meta"))
