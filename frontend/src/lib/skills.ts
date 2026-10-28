// Skills in categories (0.41.0): the owner of a skill puts it into one; everyone who sees it sees it there. The Skills
// page, the Skills button by the message box and a project's settings all list them this way.
import type { Skill } from "./api";

export const NO_CATEGORY = "No category";
export const HUB_CATEGORY = "SLQ Hub";

/** The heading a skill is listed under: its category, "No category", or "SLQ Hub" for the Hub's own. */
export const skillCategory = (k: Skill): string => (k.builtin ? HUB_CATEGORY : k.category?.trim() || NO_CATEGORY);

/** The skills by category: named ones A-Z, then no category, then the Hub's own; in each, yours first, by title. */
export function byCategory(skills: Skill[]): [string, Skill[]][] {
  const groups = new Map<string, Skill[]>();
  for (const k of skills) {
    const c = skillCategory(k);
    const key = [...groups.keys()].find((g) => g.localeCompare(c, undefined, { sensitivity: "accent" }) === 0) ?? c;
    groups.set(key, [...(groups.get(key) ?? []), k]);
  }
  const rank = (c: string) => (c === HUB_CATEGORY ? 2 : c === NO_CATEGORY ? 1 : 0);
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b, undefined, { sensitivity: "base" }))
    .map(([c, list]) => [c, [...list].sort((x, y) => Number(y.mine) - Number(x.mine)
      || x.title.localeCompare(y.title, undefined, { sensitivity: "base" }))]);
}

/** The categories in use, for suggestions: A-Z, without the two made-up headings. */
export const categoryNames = (skills: Skill[]): string[] =>
  byCategory(skills).map(([c]) => c).filter((c) => c !== NO_CATEGORY && c !== HUB_CATEGORY);

/** Whether a skill matches a search: its title, name, description or category. */
export const skillMatches = (k: Skill, q: string): boolean => {
  const t = q.trim().toLowerCase();
  return !t || [k.title, k.name, k.description, skillCategory(k), k.by].some((v) => (v ?? "").toLowerCase().includes(t));
};
