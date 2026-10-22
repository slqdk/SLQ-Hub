// The phone chat page talks to the Hub only through the model API (/v1), with
// an API key: the same door as any other app, so it answers only on the LAN
// and only for keys that are switched on.

const KEY_STORE = "slq-chat-key";

export function storedKey(): string | null {
  try {
    return localStorage.getItem(KEY_STORE);
  } catch {
    return null;
  }
}

export function saveKey(key: string | null): void {
  try {
    if (key) localStorage.setItem(KEY_STORE, key);
    else localStorage.removeItem(KEY_STORE);
  } catch {
    /* private mode: the key lives for this visit only */
  }
}

/** The key in a pasted text: the bare key, or the whole phone link. */
export function keyFrom(text: string): string | null {
  const m = text.match(/slqhub_[A-Za-z0-9_-]{10,}/);
  return m ? m[0] : null;
}

/** A key handed over in the link (/chat#key=...): kept, then removed from the address. */
export function takeKeyFromLink(): string | null {
  let hash = location.hash;
  try {
    hash = decodeURIComponent(hash);
  } catch {
    /* a broken %-sequence: read it as it is */
  }
  const key = hash ? keyFrom(hash) : null;
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  return key;
}

export type Problem = "key" | "lan" | "address" | "offline" | "busy" | "other";

export class HubError extends Error {
  problem: Problem;
  status: number;
  constructor(problem: Problem, message: string, status = 0) {
    super(message);
    this.problem = problem;
    this.status = status;
  }
}

function headers(key: string | null, json: boolean): Record<string, string> {
  const h: Record<string, string> = {};
  if (key) h.Authorization = `Bearer ${key}`;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

/** The page was opened on the Hub's home address (an IP or a local name), not through the proxy (hub.slq.dk). */
export function servedAtHome(host = location.hostname): boolean {
  return /^(\d{1,3}\.){3}\d{1,3}$/.test(host) || host.startsWith("[") || host === "localhost" ||
    /\.(local|lan|home|internal)$/i.test(host) || !host.includes(".");
}

async function errorFrom(res: Response): Promise<HubError> {
  let text = "";
  let clientIp = "";
  let viaProxy = !servedAtHome();
  try {
    const body = await res.json();
    const e = body?.error ?? body?.detail;
    text = typeof e === "string" ? e : (e?.message ?? "");
    if (typeof body?.client_ip === "string") clientIp = body.client_ip;
    if (typeof body?.via_proxy === "boolean") viaProxy = body.via_proxy;
  } catch {
    /* not JSON */
  }
  if (res.status === 401) return new HubError("key", "The key does not work (it may have been deleted or switched off).", 401);
  const seen = clientIp ? ` The Hub sees this phone at ${clientIp}.` : "";
  if (res.status === 403 && /only on the LAN/i.test(text)) {
    if (viaProxy) {
      return new HubError("lan", `This key works only on the home network, not through ${location.host}. In the Hub's ` +
        `Settings (Apps and keys) it can be set to work away from home too.${seen}`, 403);
    }
    return new HubError("lan", `The Hub answers only at home. Connect the phone to the home Wi-Fi.${seen}`, 403);
  }
  if (res.status === 403 && /may not be used from/i.test(text)) {
    const ip = clientIp || (text.split(" from ").pop() ?? "");
    return new HubError("address", `This key may not be used from this phone's address (${ip}).` +
      (viaProxy ? "" : " Is it on the home Wi-Fi?"), 403);
  }
  if (res.status === 503) return new HubError("busy", text || "The Hub is busy. Try again in a moment.", 503);
  return new HubError("other", text || `The Hub answered ${res.status}.`, res.status);
}

async function call(path: string, key: string | null, init: RequestInit = {}): Promise<Response> {
  let res: Response;
  try {
    // Without a key a signed-in browser (the Hub's owner) still gets in: same origin, so the cookie goes along.
    res = await fetch(path, { credentials: "same-origin", ...init,
      headers: { ...headers(key, init.body !== undefined), ...(init.headers as Record<string, string> | undefined) } });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new HubError("offline", servedAtHome() ? "The Hub does not answer. Is the phone on the home Wi-Fi?"
      : "The Hub does not answer. Is the phone online?");
  }
  if (!res.ok) throw await errorFrom(res);
  return res;
}

export interface HubModel {
  id: string;
  name: string;
  kind: "chat" | "embedding" | "image";
  vision?: boolean;
  thinking?: boolean;
  default?: boolean;
  loaded?: boolean;
  abilities?: string[];
}

export async function listModels(key: string | null): Promise<HubModel[]> {
  const res = await call("/v1/models", key);
  const body = await res.json();
  return ((body?.data ?? []) as HubModel[]).map((m) => ({ ...m, name: m.name || m.id, kind: m.kind || "chat" }));
}

export type Part = { type: "text"; text: string } | { type: "image_url"; image_url: { url: string } };
export interface ApiMessage { role: "system" | "user" | "assistant"; content: string | Part[] }

/** One answer, streamed: onDelta gets the text and the thinking as they come. */
export async function streamChat(key: string | null, body: Record<string, unknown>,
  onDelta: (d: { content?: string; reasoning?: string }) => void, signal: AbortSignal): Promise<void> {
  const res = await call("/v1/chat/completions", key, { method: "POST", body: JSON.stringify({ ...body, stream: true }), signal });
  if (!res.body) throw new HubError("other", "The answer did not arrive (no stream).");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const event = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      for (const line of event.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") return;
        let chunk: any;
        try {
          chunk = JSON.parse(data);
        } catch {
          continue;
        }
        if (chunk.error) throw new HubError(chunk.error.code === "queue" ? "busy" : "other", chunk.error.message || "The model stopped with an error.");
        const delta = chunk.choices?.[0]?.delta ?? {};
        const content = typeof delta.content === "string" ? delta.content : undefined;
        const reasoning = typeof delta.reasoning_content === "string" ? delta.reasoning_content : undefined;
        if (content || reasoning) onDelta({ content, reasoning });
      }
    }
  }
}

export const SIZES = {
  square: { label: "Square", size: "1024x1024" },
  portrait: { label: "Portrait", size: "832x1216" },
  landscape: { label: "Landscape", size: "1216x832" },
} as const;
export type SizeName = keyof typeof SIZES;

/** A new picture from the Hub's picture model, as a data URL. */
export async function makePicture(key: string | null, prompt: string, size: SizeName, signal: AbortSignal): Promise<string> {
  const res = await call("/v1/images/generations", key, { method: "POST", signal,
    body: JSON.stringify({ prompt, n: 1, size: SIZES[size].size, response_format: "b64_json" }) });
  const body = await res.json();
  const b64 = body?.data?.[0]?.b64_json as string | undefined;
  if (!b64) throw new HubError("other", "The Hub sent no picture.");
  return `data:${sniffImage(b64)};base64,${b64}`;
}

function sniffImage(b64: string): string {
  if (b64.startsWith("/9j/")) return "image/jpeg";
  if (b64.startsWith("UklGR")) return "image/webp";
  return "image/png";
}
