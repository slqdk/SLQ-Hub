"""web_search (SearXNG) and web_fetch (SSRF-guarded, readable text)."""
from __future__ import annotations

import io
import ipaddress
import re

import httpx

from ... import netfetch
from ...chats import store as chats
from .base import Tool, ToolContext, ToolResult, clip, obj, register


async def web_search(ctx: ToolContext, args: dict) -> ToolResult:
    q = (args.get("query") or "").strip()
    if not q:
        return ToolResult("query is required", is_error=True)
    n = max(1, min(int(args.get("max_results") or ctx.settings.get("web", "search_results") or 8), 20))
    params = {"q": q, "format": "json"}
    if args.get("time_range") in ("day", "week", "month", "year"):
        params["time_range"] = args["time_range"]
    try:
        async with httpx.AsyncClient(timeout=20, transport=ctx.hub.transports.web) as c:
            r = await c.get(ctx.hub.env.searxng_url + "/search", params=params)
        r.raise_for_status()
        results = r.json().get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        return ToolResult(f"Search is not available ({exc.__class__.__name__}).", is_error=True)
    if not results:
        return ToolResult(f"No results for {q!r}.")
    lines = []
    for i, res in enumerate(results[:n], 1):
        snippet = re.sub(r"\s+", " ", res.get("content") or "").strip()[:300]
        date = f" ({res['publishedDate'][:10]})" if res.get("publishedDate") else ""
        lines.append(f"{i}. {res.get('title', '').strip()}{date}\n   {res.get('url')}\n   {snippet}")
    ctx.emit("sources", items=[{"title": r.get("title"), "url": r.get("url")} for r in results[:n]])
    return ToolResult(f"Results for {q!r}:\n" + "\n".join(lines) +
                      "\n\nFetch the pages that matter with web_fetch before relying on them.")


def html_to_text(html: str, url: str) -> str:
    try:
        import trafilatura
        text = trafilatura.extract(html, url=url, include_links=False, include_tables=True,
                                   output_format="markdown", favor_recall=True)
        if text and len(text) > 200:
            return text
    except Exception:  # noqa: BLE001 - fall back to plain stripping
        pass
    html = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


async def web_fetch(ctx: ToolContext, args: dict) -> ToolResult:
    url = (args.get("url") or "").strip()
    max_chars = max(1000, min(int(args.get("max_chars") or ctx.settings.get("web", "fetch_max_chars") or 30000),
                              150_000))
    extra = []
    for cidr in (ctx.settings.get("web", "blocked_cidrs") or []) + [str(n) for n in ctx.hub.env.lan_networks]:
        try:
            extra.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            pass
    try:
        data, ctype, final = await netfetch.fetch(url, extra_blocked=extra, transport=ctx.hub.transports.web,
                                                  skip_check=ctx.hub.transports.web is not None)
    except netfetch.FetchError as exc:
        return ToolResult(str(exc), is_error=True)
    except httpx.HTTPError as exc:
        return ToolResult(f"Could not fetch {url} ({exc.__class__.__name__}).", is_error=True)
    ctype = ctype.lower()
    if ctype.startswith("image/"):
        up = chats.save_upload(data, final.rsplit("/", 1)[-1][:80] or "image", ctype.split(";")[0])
        return ToolResult(f"The image at {final} is shown below.", images=[{"sha": up["sha"], "mime": up["mime"]}])
    if "pdf" in ctype or final.lower().endswith(".pdf"):
        import fitz
        doc = fitz.open(stream=io.BytesIO(data).read(), filetype="pdf")
        text = "\n".join(f"--- page {i + 1} ---\n{p.get_text()}" for i, p in enumerate(doc))
    elif "html" in ctype or data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        text = html_to_text(data.decode("utf-8", "replace"), final)
    else:
        text = data.decode("utf-8", "replace")
    text, cut = clip(text, max_chars)
    return ToolResult(f"Content of {final}:\n\n{text}" + ("\n\n(cut to fit; ask for more with max_chars)" if cut else ""))


register(Tool("web_search", "Search the web (SearXNG). Returns titles, URLs and snippets. Use it for anything "
              "current or that you are not sure of; then read the best pages with web_fetch. Cite the URLs "
              "you used in your answer.",
              obj({"query": {"type": "string"}, "max_results": {"type": "integer"},
                   "time_range": {"type": "string", "enum": ["day", "week", "month", "year"]}}, ["query"]),
              web_search, group="web"))
register(Tool("web_fetch", "Fetch a public web page, PDF or image and return its readable text (or show the "
              "image). Local network addresses are refused.",
              obj({"url": {"type": "string"}, "max_chars": {"type": "integer"}}, ["url"]),
              web_fetch, group="web"))
