// Model files from this computer to the Hub (0.62.0), in pieces: one request stays under the limit of the proxy in
// front of the Hub, and a lost connection - or a Hub that restarts for an update - costs one piece. Choosing the same
// file again (same name, size and date) goes on where the Hub's copy ends.
import { api, ApiError } from "./api";

export const PIECE = 32 * 1024 * 1024;

export interface UploadInfo { id: string; name: string; size: number; received: number; done: boolean }

/** GET /hub/api/model-uploads/{id}/inspect: what the file is, from its own header ({pending: true} until it has
 *  arrived). */
export interface UploadFacts {
  pending?: boolean;
  kind?: "gguf" | "mmproj" | "sdxl" | "lora" | "safetensors" | "unknown" | "other";
  reason?: string;
  /** a .gguf */
  architecture?: string | null; name?: string | null; parameters?: number | null; context_length?: number | null;
  quantization?: string; tools?: boolean | null; thinking?: boolean | null;
  /** a .safetensors */
  supported?: boolean; vpred?: boolean; preset?: string;
  suggested?: { id: string; name: string };
}

const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms));

/** Send a file (or go on with it). onProgress gets the bytes the Hub has; onPiece runs after each piece. */
export async function uploadFile(file: File, opts: { signal?: AbortSignal;
  onProgress?: (received: number, total: number) => void; onPiece?: (up: UploadInfo) => Promise<void> | void }
): Promise<UploadInfo> {
  let up = await api<UploadInfo>("/hub/api/model-uploads",
    { body: { name: file.name, size: file.size, modified: file.lastModified }, signal: opts.signal });
  opts.onProgress?.(up.received, file.size);
  let failures = 0;
  while (up.received < file.size) {
    const at = up.received;
    let sent = false;
    try {
      up = await api<UploadInfo>(`/hub/api/model-uploads/${up.id}`, { method: "PUT", query: { offset: at },
        raw: file.slice(at, Math.min(at + PIECE, file.size)), signal: opts.signal });
      failures = 0;
      sent = true;
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
      const status = e instanceof ApiError ? e.status : 0;
      // 409: the Hub's copy ends elsewhere; 0 or 5xx: no answer, or the proxy's while the Hub restarts
      if (status !== 409 && status !== 0 && status < 500) throw e;
      failures += 1;
      if (failures > 30) throw e;
      await sleep(Math.min(15000, 1000 * failures));
      try {
        up = await api<UploadInfo>(`/hub/api/model-uploads/${up.id}`, { signal: opts.signal });
      } catch (again) {
        if ((again as Error).name === "AbortError") throw again;
      }
    }
    opts.onProgress?.(up.received, file.size);
    if (sent) await opts.onPiece?.(up);         // outside the retries: what it throws ends the upload
  }
  return up;
}

export const inspectUpload = (id: string) => api<UploadFacts>(`/hub/api/model-uploads/${id}/inspect`);
