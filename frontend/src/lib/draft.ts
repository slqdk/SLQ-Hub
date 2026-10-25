// A message to start a new chat with - the help page's examples, the Skills page's New skill and Use in a new chat.
// The next message box that opens takes the text; the next new chat takes the skills (chosen for it).
let pending: string | null = null;
let pendingSkills: string[] | null = null;

export function setDraft(text: string, skills?: string[]): void {
  pending = text;
  pendingSkills = skills && skills.length ? skills : null;
}

/** The waiting text, without taking it (a component's first render may run twice). */
export function peekDraft(): string {
  return pending ?? "";
}

export function clearDraft(): void {
  pending = null;
}

/** The skills a new chat starts with (0.23.0), without taking them. */
export function peekDraftSkills(): string[] | null {
  return pendingSkills;
}

export function clearDraftSkills(): void {
  pendingSkills = null;
}

/** The text that starts a skill with the skill maker; the skill maker is chosen with it. */
export const NEW_SKILL_TEXT = "Make a skill that ";
export const SKILL_MAKER = "hub/skill-creator";
