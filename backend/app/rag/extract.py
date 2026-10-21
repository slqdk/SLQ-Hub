"""Documents -> pages (text, and a rendered image where the layout matters)
-> chunks."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import PurePath

IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff")
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".log",
            ".py", ".js", ".ts", ".c", ".h", ".cpp", ".cs", ".java", ".go", ".rs", ".sh", ".sql", ".st", ".rst"}
CHUNK_CHARS = 1800
OVERLAP_CHARS = 250


class Unsupported(ValueError):
    pass


@dataclass
class Page:
    number: int                  # 1-based
    text: str
    image: bytes | None = None   # JPEG, for page-image search and for the model to look at
    width: int | None = None
    height: int | None = None


def kind_of(filename: str, mime: str) -> str:
    ext = PurePath(filename).suffix.lower()
    if mime == "application/pdf" or ext == ".pdf":
        return "pdf"
    if mime in IMAGE_TYPES or ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
        return "image"
    if ext == ".docx":
        return "docx"
    if ext in (".html", ".htm") or mime == "text/html":
        return "html"
    if ext in TEXT_EXT or mime.startswith("text/"):
        return "text"
    raise Unsupported(f"{filename}: this file type is not supported (PDF, images, Word .docx, HTML and text files are)")


def pages(data: bytes, filename: str, mime: str, render: bool = True, max_side: int = 1400) -> list[Page]:
    kind = kind_of(filename, mime)
    if kind == "pdf":
        return _pdf(data, render, max_side)
    if kind == "image":
        return [_image(data, max_side)]
    if kind == "docx":
        return [Page(1, _docx(data))]
    if kind == "html":
        return [Page(1, _html(data))]
    return [Page(1, data.decode("utf-8", "replace"))]


def _pdf(data: bytes, render: bool, max_side: int) -> list[Page]:
    import pymupdf
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - pymupdf raises its own types
        raise Unsupported(f"the PDF cannot be opened: {exc}") from exc
    if doc.needs_pass:
        raise Unsupported("the PDF is password protected")
    out = []
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        img = w = h = None
        if render:
            rect = page.rect
            zoom = min(150 / 72, max_side / max(rect.width, rect.height, 1))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img, w, h = pix.tobytes("jpg", jpg_quality=82), pix.width, pix.height
        out.append(Page(i + 1, text, img, w, h))
    return out


def _image(data: bytes, max_side: int) -> Page:
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)
    except Exception as exc:  # noqa: BLE001
        raise Unsupported(f"the image cannot be opened: {exc}") from exc
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85)
    return Page(1, "", buf.getvalue(), im.width, im.height)


def _docx(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _html(data: bytes) -> str:
    import trafilatura
    html = data.decode("utf-8", "replace")
    text = trafilatura.extract(html, include_tables=True, include_links=False) or ""
    return text or re.sub(r"<[^>]+>", " ", html)


def chunks(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Split on paragraph, then line, then sentence boundaries; pieces of
    about `size` characters with a little overlap."""
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return []
    if len(text) <= size:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "))
            if cut > size // 2:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out
