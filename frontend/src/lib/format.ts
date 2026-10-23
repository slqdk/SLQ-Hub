export function bytes(n: number | null | undefined): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

export function seconds(s: number | null | undefined): string {
  if (s == null) return "";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${Math.round(s % 60)} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function ago(iso: string | number | null | undefined): string {
  if (!iso) return "";
  const t = typeof iso === "number" ? iso * 1000 : Date.parse(iso);
  const d = (Date.now() - t) / 1000;
  if (d < 45) return "just now";
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 86400 * 7) return `${Math.round(d / 86400)} days ago`;
  return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function dateGroup(iso: string): string {
  const t = new Date(iso);
  const now = new Date();
  const days = Math.floor((new Date(now.toDateString()).getTime() - new Date(t.toDateString()).getTime()) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "This week";
  if (days < 31) return "This month";
  return t.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

export function tokens(n: number | null | undefined): string {
  if (!n) return "0";
  return n >= 10000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : String(n);
}

/** "llm:qwen3.8-27b" -> a name for people. */
export function residencyName(need: string | null | undefined, names: Record<string, string>): string {
  if (!need) return "Nothing loaded";
  const [kind, id] = need.split(":", 2);
  if (kind === "llm") return names[id] ?? id;
  if (kind === "comfy") return "ComfyUI (pictures, sound)";
  if (kind === "embedgpu") return "Indexing pages";
  if (kind === "idle") return "Nothing loaded";
  return need;
}
