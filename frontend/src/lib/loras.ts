// LoRA categories (0.70.0): the headings a chat's picture menu lists LoRAs under, as skills are - none first, then
// A-Z. Trainings give their LoRAs a category (by what they learn unless set: People, Things, Styles).
import type { Lora } from "./api";

export const NO_LORA_CATEGORY = "No category";
export const KIND_CATEGORY: Record<string, string> = { person: "People", thing: "Things", style: "Styles" };

export const loraCategory = (l: Lora): string => l.category?.trim() || NO_LORA_CATEGORY;

/** The LoRAs by category: no category first, the rest A-Z (one heading for names that differ only in case); in each,
 *  by name. */
export function byLoraCategory(loras: Lora[]): [string, Lora[]][] {
  const groups = new Map<string, Lora[]>();
  for (const l of loras) {
    const c = loraCategory(l);
    const key = [...groups.keys()].find((g) => g.localeCompare(c, undefined, { sensitivity: "accent" }) === 0) ?? c;
    groups.set(key, [...(groups.get(key) ?? []), l]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => Number(b === NO_LORA_CATEGORY) - Number(a === NO_LORA_CATEGORY)
      || a.localeCompare(b, undefined, { sensitivity: "base" }))
    .map(([c, list]) => [c, [...list].sort((x, y) => x.name.localeCompare(y.name, undefined, { numeric: true }))]);
}

/** The categories in use, for suggestions (with the kinds' own). */
export function loraCategoryNames(loras: Lora[]): string[] {
  const names = new Set<string>(Object.values(KIND_CATEGORY));
  for (const l of loras) if (l.category?.trim()) names.add(l.category.trim());
  return [...names].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}
