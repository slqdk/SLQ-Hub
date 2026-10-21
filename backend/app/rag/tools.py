"""library_search: the agent's way into the libraries of a chat or project."""
from __future__ import annotations

from ..agent.tools.base import Tool, ToolContext, ToolResult, obj, register
from .library import LibraryError


def chosen(ctx: ToolContext) -> list[int]:
    ids = list((ctx.chat.get("settings") or {}).get("libraries") or [])
    if ctx.project:
        ids += [i for i in (ctx.project.get("settings") or {}).get("libraries") or [] if i not in ids]
    return [int(i) for i in ids if str(i).isdigit()]


def _available(ctx: ToolContext) -> bool:
    libs = ctx.hub.extras.get("libraries")
    return bool(libs and libs.available() and chosen(ctx))


async def library_search(ctx: ToolContext, args: dict) -> ToolResult:
    libs = ctx.hub.extras["libraries"]
    ids = chosen(ctx)
    names = {lib["id"]: lib["name"] for lib in libs.list()}
    if args.get("library"):
        want = str(args["library"]).strip().lower()
        ids = [i for i in ids if names.get(i, "").lower() == want or str(i) == want] or ids
    ids = [i for i in ids if i in names]
    if not ids:
        return ToolResult("No library is connected to this chat.", is_error=True)
    try:
        hits = await libs.search(ids, args.get("query") or "", args.get("top_k"))
    except LibraryError as exc:
        return ToolResult(str(exc), is_error=True)
    if not hits:
        return ToolResult(f"Nothing found in {', '.join(names[i] for i in ids)} for that query. Try other words.")
    lines = []
    for n, h in enumerate(hits, 1):
        body = h.text.strip() if h.text else "(a page without text - see its image)"
        lines.append(f"[{n}] {h.ref()} (score {h.score})\n{body}")
    show = int(libs.cfg("images_in_results") or 0)
    vision = "vision" in (ctx.model.get("capabilities") or []) or (ctx.engine is not None and ctx.engine.cloud)
    images, seen = [], set()
    for h in hits:
        if len(images) >= show or not vision:
            break
        if h.image and h.image not in seen and ("page" in h.via or not h.text):
            seen.add(h.image)
            images.append({"sha": h.image, "mime": "image/jpeg", "caption": h.ref()})
    if images:
        lines.append("Page images of the best visual matches follow: " + "; ".join(i["caption"] for i in images) + ".")
    lines.append("Cite what you use as (file, page).")
    return ToolResult("\n\n".join(lines), images=images,
                      display={"library_hits": [{"doc_id": h.doc_id, "filename": h.filename, "page": h.page,
                                                 "score": h.score, "image": h.image, "via": h.via,
                                                 "text": h.text[:300]} for h in hits]})


register(Tool("library_search", "Search the document libraries connected to this chat (manuals, papers, notes; "
              "text and page images). Returns the best passages with file and page, and images of the pages that "
              "match visually. Search before answering questions the libraries may cover; ask several focused "
              "queries rather than one vague one.",
              obj({"query": {"type": "string", "description": "What to look for, in words likely to appear in the "
                                                             "documents"},
                   "library": {"type": "string", "description": "Only this library (name); default: all connected"},
                   "top_k": {"type": "integer", "description": "Number of hits (default 6)"}}, ["query"]),
              library_search, available=_available, group="knowledge"))
