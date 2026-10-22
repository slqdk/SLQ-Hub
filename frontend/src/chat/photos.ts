// Photos from the phone, made small enough to send and keep: the longest
// side at most 1536 px, as JPEG. (A phone photo is 12-48 megapixels; the
// model sees about a megapixel anyway.)

const MAX_SIDE = 1536;

function load(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("This file is not a picture the browser can open."));
    img.src = url;
  });
}

export async function photoFromFile(file: File): Promise<string> {
  const url = URL.createObjectURL(file);
  try {
    const img = await load(url);
    const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
    const w = Math.max(1, Math.round(img.naturalWidth * scale));
    const h = Math.max(1, Math.round(img.naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const g = canvas.getContext("2d");
    if (!g) throw new Error("The photo could not be read.");
    g.fillStyle = "#fff";                       // transparent PNGs become white, not black, as JPEG
    g.fillRect(0, 0, w, h);
    g.drawImage(img, 0, 0, w, h);
    return canvas.toDataURL("image/jpeg", 0.86);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** A file name for saving a picture. */
export function pictureName(dataUrl: string, at: number): string {
  const ext = dataUrl.startsWith("data:image/jpeg") ? "jpg" : dataUrl.startsWith("data:image/webp") ? "webp" : "png";
  const d = new Date(at);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `picture-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.${ext}`;
}
