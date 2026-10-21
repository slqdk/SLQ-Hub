import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { renderMarkdown } from "../lib/markdown";
import { copyText } from "./ui";

/** Rendered markdown. While `live`, re-rendering is throttled to one frame. */
export function Markdown({ text, live, className }: { text: string; live?: boolean; className?: string }) {
  const [html, setHtml] = useState(() => renderMarkdown(text));
  const ref = useRef<HTMLDivElement>(null);
  const pending = useRef<number | null>(null);

  useEffect(() => {
    if (!live) {
      setHtml(renderMarkdown(text));
      return;
    }
    if (pending.current != null) return;
    pending.current = requestAnimationFrame(() => {
      pending.current = null;
      setHtml(renderMarkdown(text));
    });
  }, [text, live]);
  useEffect(() => () => {
    if (pending.current != null) cancelAnimationFrame(pending.current);
  }, []);
  // The last frame of a stream: make sure the final text is shown.
  useEffect(() => {
    if (!live) return;
    const t = window.setTimeout(() => setHtml(renderMarkdown(text)), 120);
    return () => window.clearTimeout(t);
  }, [text, live]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.querySelectorAll("pre").forEach((pre) => {
      if (pre.querySelector(".copy")) return;
      const b = document.createElement("button");
      b.className = "btn small copy";
      b.type = "button";
      b.textContent = "Copy";
      b.addEventListener("click", async () => {
        const ok = await copyText(pre.querySelector("code")?.textContent ?? pre.textContent ?? "");
        b.textContent = ok ? "Copied" : "Copy failed";
        window.setTimeout(() => (b.textContent = "Copy"), 1500);
      });
      pre.appendChild(b);
    });
  }, [html]);

  return <div ref={ref} className={`md ${className ?? ""} ${live ? "cursor-host" : ""}`}
    dangerouslySetInnerHTML={{ __html: html }} />;
}
