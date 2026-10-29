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

/** How long something worked, roughly: "45 s", "12 min", "2 h 22 min". */
export function worked(s: number | null | undefined): string {
  const v = Math.max(0, Math.round(s ?? 0));
  if (v < 60) return `${v} s`;
  const m = Math.round(v / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
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

/** The card's energy (0.24.0): "0.4 Wh", "12 Wh", "1.24 kWh". */
export function energy(wh: number | null | undefined): string {
  const v = wh ?? 0;
  if (v >= 1000) return `${(v / 1000).toFixed(v >= 100000 ? 0 : 2)} kWh`;
  if (v >= 10) return `${Math.round(v)} Wh`;
  if (v >= 0.05) return `${v.toFixed(1)} Wh`;
  return v > 0 ? "<0.1 Wh" : "0 Wh";
}

/** What it cost, in kroner: "1.25 kr", or øre below a krone ("8.4 øre", "0.3 øre"). */
export function money(kr: number | null | undefined): string {
  const v = kr ?? 0;
  if (v >= 1) return `${v.toFixed(2)} kr`;
  const ore = v * 100;
  if (ore >= 10) return `${Math.round(ore)} øre`;
  if (ore >= 0.05) return `${ore.toFixed(1)} øre`;
  return v > 0 ? "<0.1 øre" : "0 øre";
}

/** "llm:qwen3.8-27b" -> a name for people. */
export function residencyName(need: string | null | undefined, names: Record<string, string>): string {
  if (!need) return "Nothing loaded";
  const [kind, id] = need.split(":", 2);
  if (kind === "llm") return names[id] ?? id;
  if (kind === "comfy") return "ComfyUI (pictures, sound)";
  if (kind === "embedgpu") return "Indexing pages";
  if (kind === "train") return "LoRA training";
  if (kind === "idle") return "Nothing loaded";
  return need;
}
