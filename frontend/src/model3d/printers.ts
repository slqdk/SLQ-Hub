// The printers a design can be laid out for (0.87.0): the plate the editor draws and the build volume it checks.
// Søren prints on a Bambu Lab P2S - the default - but it is chosen per design, and the last choice is remembered.
// Build volumes from Bambu Lab's specifications (checked 2026-11-25); H2D: the volume one nozzle reaches.
import type { Vec3 } from "./geometry";

export interface Printer { id: string; name: string; bed: Vec3 }

export const PRINTERS: Printer[] = [
  { id: "bambu-p2s", name: "Bambu Lab P2S", bed: [256, 256, 256] },
  { id: "bambu-p1s", name: "Bambu Lab P1S / P1P", bed: [256, 256, 256] },
  { id: "bambu-x1", name: "Bambu Lab X1 Carbon / X1E", bed: [256, 256, 256] },
  { id: "bambu-a1", name: "Bambu Lab A1", bed: [256, 256, 256] },
  { id: "bambu-a1-mini", name: "Bambu Lab A1 mini", bed: [180, 180, 180] },
  { id: "bambu-h2s", name: "Bambu Lab H2S", bed: [340, 320, 340] },
  { id: "bambu-h2d", name: "Bambu Lab H2D (one nozzle)", bed: [325, 320, 325] },
];

export const DEFAULT_PRINTER = "bambu-p2s";
const KEY = "slq.3d.printer";
const BED_KEY = "slq.3d.bed";

export function printerById(id: string | undefined): Printer | undefined {
  return PRINTERS.find((p) => p.id === id);
}

/** The printer a design names, or a custom size it carries, else the last one chosen here, else the P2S. */
export function printerOf(id: string | undefined, bed: Vec3 | undefined): Printer {
  const known = printerById(id);
  if (known) return known;
  if (id === "custom" && bed) return { id: "custom", name: "Custom size", bed };
  const last = lastPrinter();
  return last;
}

export function lastPrinter(): Printer {
  try {
    const id = localStorage.getItem(KEY) ?? DEFAULT_PRINTER;
    if (id === "custom") {
      const bed = JSON.parse(localStorage.getItem(BED_KEY) ?? "null");
      if (Array.isArray(bed) && bed.length === 3 && bed.every((v) => typeof v === "number" && v > 0)) {
        return { id: "custom", name: "Custom size", bed: bed as Vec3 };
      }
    }
    return printerById(id) ?? printerById(DEFAULT_PRINTER)!;
  } catch {
    return printerById(DEFAULT_PRINTER)!;
  }
}

export function rememberPrinter(p: Printer): void {
  try {
    localStorage.setItem(KEY, p.id);
    if (p.id === "custom") localStorage.setItem(BED_KEY, JSON.stringify(p.bed));
  } catch {
    /* private mode: nothing to remember with */
  }
}
