// Effort (0.38.0): one choice for how hard a turn works - Quick, Think, Extra.
export type Level = "quick" | "think" | "extra";
export const LEVELS: Level[] = ["quick", "think", "extra"];
export const LEVEL_LABEL: Record<Level, string> = { quick: "Quick", think: "Think", extra: "Extra" };

type Holder = { level?: string | null; thinking?: boolean | null } | null | undefined;

/** The effort a chat works at: its own, else its project's, else Settings' default. A thinking on/off chosen before
 * 0.38.0 still counts (Quick or Think), as the Hub reads it. */
export function levelOf(chat: Holder, project: Holder, fallback: unknown): Level {
  for (const s of [chat, project]) {
    if (!s) continue;
    if (s.level && (LEVELS as string[]).includes(s.level)) return s.level as Level;
    if (typeof s.thinking === "boolean") return s.thinking ? "think" : "quick";
  }
  return (LEVELS as string[]).includes(String(fallback)) ? (fallback as Level) : "quick";
}

/** What a level does, for the menus. */
export function levelText(level: Level, canThink: boolean, review: boolean): string {
  if (level === "quick") return "Answers straight away";
  if (level === "think") return canThink ? "Thinks first" : "This model does not think";
  return (canThink ? "Thinks hardest" : "No thinking") + (review ? ", and another model checks the work" : "");
}

export const LEVEL_OPTIONS = [
  { value: "quick", label: "Quick - answers straight away" },
  { value: "think", label: "Think - thinks first (half the thinking budget a step)" },
  { value: "extra", label: "Extra - thinks hardest, and another model checks the work" },
];
