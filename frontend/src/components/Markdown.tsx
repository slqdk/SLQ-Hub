import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { renderMarkdown } from "../lib/markdown";
import { copyText } from "../lib/clipboard";

/** Rendered markdown. While `live`, re-rendering is throttled to one frame. remoteImages=false: pictures from
 * the web become links (the internet asks first). */
export function Markdown({ text, live, className, remoteImages = true }: { text: string; live?: boolean;
  className?: string; remoteImages?: boolean }) {
  const render = (t: string) => renderMarkdown(t, { remoteImages });
  const [html, setHtml] = useState(() => render(text));
  const ref = useRef<HTMLDivElement>(null);
  const pending = useRef<number | null>(null);

  useEffect(() => {
    if (!live) {
      setHtml(render(text));
      return;
    }
    if (pending.current != null) return;
    pending.current = requestAnimationFrame(() => {
      pending.current = null;
      setHtml(render(text));
    });
  }, [text, live, remoteImages]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => {
    if (pending.current != null) cancelAnimationFrame(pending.current);
  }, []);
  // The last frame of a stream: make sure the final text is shown.
  useEffect(() => {
    if (!live) return;
    const t = window.setTimeout(() => setHtml(render(text)), 120);
    return () => window.clearTimeout(t);
  }, [text, live, remoteImages]);   // eslint-disable-line react-hooks/exhaustive-deps

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
