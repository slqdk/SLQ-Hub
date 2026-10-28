// Skills in categories (0.41.0): the owner of a skill puts it into one; everyone who sees it sees it there. The Skills
// page, the Skills button by the message box and a project's settings all list them this way. Since 0.45.0 a
// category is a folder, closed until its name is clicked; skills in none are listed straight away, first. A category
// named "internal" comes last and opens only on three quick clicks (Søren's).
import { useRef, useState } from "react";
import type { Skill } from "./api";

export const NO_CATEGORY = "No category";
export const HUB_CATEGORY = "SLQ Hub";

/** The heading a skill is listed under: its category, "No category", or "SLQ Hub" for the Hub's own. */
export const skillCategory = (k: Skill): string => (k.builtin ? HUB_CATEGORY : k.category?.trim() || NO_CATEGORY);

/** The folder that opens only on three quick clicks, and is listed last. */
export const isInternal = (category: string): boolean => category.trim().toLowerCase() === "internal";

/** The skills by category: no category first (listed straight away), named ones A-Z, the Hub's own, "internal"
 *  last; in each, yours first, by title. */
export function byCategory(skills: Skill[]): [string, Skill[]][] {
  const groups = new Map<string, Skill[]>();
  for (const k of skills) {
    const c = skillCategory(k);
    const key = [...groups.keys()].find((g) => g.localeCompare(c, undefined, { sensitivity: "accent" }) === 0) ?? c;
    groups.set(key, [...(groups.get(key) ?? []), k]);
  }
  const rank = (c: string) => (isInternal(c) ? 3 : c === HUB_CATEGORY ? 2 : c === NO_CATEGORY ? -1 : 0);
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

/** Which folders are open: remembered in this browser under `key` - but "internal" opens only on three clicks, each
 *  within 1.5 s of the one before, and is closed again next time (it is not remembered). */
export function useFolders(key: string) {
  const [open, setOpen] = useState<string[]>(() => {
    try {
      const v = JSON.parse(localStorage.getItem(key) ?? "[]");
      return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
    } catch {
      return [];
    }
  });
  const [unlocked, setUnlocked] = useState(false);
  const clicks = useRef<number[]>([]);
  const isOpen = (category: string) => (isInternal(category) ? unlocked : open.includes(category));
  const click = (category: string) => {
    if (isInternal(category)) {
      if (unlocked) {
        setUnlocked(false);
        return;
      }
      const now = Date.now();
      const last = clicks.current[clicks.current.length - 1];
      clicks.current = last !== undefined && now - last < 1500 ? [...clicks.current, now] : [now];
      if (clicks.current.length >= 3) {
        clicks.current = [];
        setUnlocked(true);
      }
      return;
    }
    const next = open.includes(category) ? open.filter((c) => c !== category) : [...open, category];
    setOpen(next);
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch { /* a private window: it is forgotten, that is all */ }
  };
  return { isOpen, click };
}
