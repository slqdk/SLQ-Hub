// Downloads started from the page.
import { api } from "./api";

/** Save what a URL answers (the Hub names the file when `name` is empty). */
export function download(url: string, name = ""): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Save text made in the page as a file. */
export function saveText(text: string, name: string, type = "application/json"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  download(url, name);
  window.setTimeout(() => URL.revokeObjectURL(url), 30000);
}

/** A ZIP the Hub makes (0.29.0): asked first with check=true, so a refusal (too big, nothing there) is a message
 * rather than a broken download. Throws the Hub's message. */
export async function downloadZip(url: string): Promise<void> {
  await api(url + (url.includes("?") ? "&" : "?") + "check=true");
  download(url);
}
