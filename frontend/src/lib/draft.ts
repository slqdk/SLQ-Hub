// A message to start a new chat with - the help page's examples. The next message box that opens takes it.
let pending: string | null = null;

export function setDraft(text: string): void {
  pending = text;
}

/** The waiting text, without taking it (a component's first render may run twice). */
export function peekDraft(): string {
  return pending ?? "";
}

export function clearDraft(): void {
  pending = null;
}
