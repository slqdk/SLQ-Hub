"""The test cases' input files, made the same way every time (fixed seeds): a sales export, a manual, a server log,
pictures, a small program with bugs - and the answers they hold, worked out here, so a case knows what a right
answer says without anyone writing it down by hand."""
from __future__ import annotations

import io
import random
import re
from collections import Counter
from datetime import date, datetime, timedelta

MONTHS_DA = ("januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober",
             "november", "december")


def _da_number(x: float, decimals: int = 2) -> str:
    """1199.0 -> "1.199,00" (Danish: a dot between thousands, a decimal comma)."""
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


# ---------------------------------------------------------------- a sales export (Danish Excel)

REGIONS = ("Nord", "Syd", "Øst", "Vest")
PRODUCTS = (("A-100", 249.00), ("B-200", 1199.00), ("C-300", 89.50))


def sales_rows() -> list[tuple[date, str, str, int, float]]:
    rnd = random.Random(2025)
    rows = []
    day = date(2025, 1, 1)
    while day.year == 2025:
        n = rnd.choice((1, 2, 2, 3)) + (3 if day.month == 11 else 0)        # November: a campaign
        for _ in range(n):
            q2 = day.month in (4, 5, 6)
            region = rnd.choices(REGIONS, (1.0, 1.0, 1.7, 1.0) if q2 else (1.0, 1.1, 0.9, 1.0))[0]
            sku, price = rnd.choices(PRODUCTS, (5, 2, 8))[0]
            rows.append((day, region, sku, rnd.randint(1, 40), price))
        day += timedelta(days=1)
    return rows


def sales_csv() -> tuple[bytes, dict]:
    """salg_2025.csv as Excel writes it with Danish settings (semicolons, dd-mm-yyyy, 1.199,00) and the answers."""
    rows = sales_rows()
    lines = ["dato;region;produkt;antal;stykpris"]
    for d, region, sku, qty, price in rows:
        lines.append(f"{d:%d-%m-%Y};{region};{sku};{qty};{_da_number(price)}")
    q2: Counter = Counter()
    months: Counter = Counter()
    for d, region, _, qty, price in rows:
        if d.month in (4, 5, 6):
            q2[region] += qty * price
        months[d.month] += qty * price
    (region, revenue), (second, revenue2) = q2.most_common(2)
    (month, best), (_, best2) = months.most_common(2)
    return ("\n".join(lines) + "\n").encode("utf-8"), {
        "q2_region": region, "q2_revenue": round(revenue, 2), "q2_second": second, "q2_margin": revenue / revenue2,
        "best_month": month, "best_month_name": MONTHS_DA[month - 1], "month_margin": best / best2,
        "months": {m: round(v, 2) for m, v in sorted(months.items())}, "rows": len(rows)}


# ---------------------------------------------------------------- a long manual (PDF)

_PARTS = ("Kontrollér", "Efterse", "Rengør", "Notér", "Udskift", "Mål")
_THINGS = ("tætningerne", "lejerne", "trykføleren", "kontraventilen", "sugekurven", "motorens ventilation",
           "niveauvippen", "styretavlens filter", "rørophænget", "afspærringsventilen")
_WHEN = ("ved hvert eftersyn", "en gang om måneden", "hver tredje måned", "efter kraftig regn",
         "når alarmen har været aktiv", "før vinteren")
_WHY = ("for at undgå utætheder", "så pumpen ikke løber tør", "så slitage opdages i tide",
        "fordi aflejringer nedsætter ydelsen", "så alarmen virker, når den skal", "for at holde strømforbruget nede")
_TOPICS = ("Generelt", "Sikkerhed", "Opstart", "Pumpe P2", "Styring og alarmer", "Pumpe P1", "Rørføring",
           "Rengøring", "Pumpe P3", "Fejlfinding", "Ændringslog", "Kontakter")
MANUAL_FACTS = {
    4: "Serviceinterval for pumpe P2: hver 1.800 driftstimer eller mindst én gang årligt.",
    6: "Serviceinterval for pumpe P1: hver 2.400 driftstimer eller mindst én gang årligt.",
    9: "Serviceinterval for pumpe P3: hver 2.150 driftstimer eller mindst én gang årligt.",
    11: "1. januar 2026: Serviceintervallet for pumpe P3 er nedsat fra 2.150 til 1.900 driftstimer efter "
        "leverandørens anbefaling. Øvrige intervaller er uændrede.",
}


def manual_pdf() -> tuple[bytes, dict]:
    """driftsmanual.pdf: 12 pages of maintenance text; P3's interval on page 9, changed on page 11."""
    import pymupdf
    rnd = random.Random(12)
    doc = pymupdf.open()
    for page_no, topic in enumerate(_TOPICS, start=1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((56, 50), "Driftsmanual - Pumpestation Nord", fontsize=9, fontname="helv")
        page.insert_text((56, 800), f"Side {page_no} af {len(_TOPICS)}", fontsize=9, fontname="helv")
        page.insert_text((56, 90), f"{page_no}. {topic}", fontsize=16, fontname="hebo")
        paragraphs = []
        for _ in range(5):
            sentences = [f"{rnd.choice(_PARTS)} {rnd.choice(_THINGS)} {rnd.choice(_WHEN)}, {rnd.choice(_WHY)}."
                         for _ in range(rnd.randint(3, 5))]
            paragraphs.append(" ".join(sentences))
        if page_no in MANUAL_FACTS:
            paragraphs.insert(2, MANUAL_FACTS[page_no])
        page.insert_textbox(pymupdf.Rect(56, 110, 539, 780), "\n\n".join(paragraphs), fontsize=10.5, fontname="helv")
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data, {"hours": 1900, "old_hours": 2150, "page": 11, "old_page": 9}


# ---------------------------------------------------------------- a server log

def server_log() -> tuple[bytes, dict]:
    """server.log from 20 August to 5 October 2026: noise from four servers, the NAS backups each night."""
    rnd = random.Random(4471)
    nas2_failed = {date(2026, 8, 28): "E-4471", date(2026, 9, 3): "E-4471", date(2026, 9, 14): "E-4471",
                   date(2026, 9, 27): "E-5102", date(2026, 10, 2): "E-5102"}
    nas1_failed = {date(2026, 9, 29): "E-6001", date(2026, 8, 24): "E-6001"}
    noise = (("WEB-1", "INFO", "GET /status 200 {ms} ms"), ("WEB-1", "INFO", "GET /api/orders 200 {ms} ms"),
             ("WEB-1", "WARNING", "slow response on /api/report ({ms} ms)"), ("DB-1", "INFO", "checkpoint complete"),
             ("DB-1", "INFO", "vacuum finished in {s} s"), ("DB-1", "WARNING", "replication lag {s} s"),
             ("NAS-1", "INFO", "disk check OK"), ("NAS-2", "INFO", "disk check OK"),
             ("NAS-2", "INFO", "share /data/projekter mounted by 10.0.4.{n}"))
    lines: list[tuple[datetime, str]] = []
    t = datetime(2026, 8, 20, 0, 0, 0)
    end = datetime(2026, 10, 5, 23, 59, 59)
    while t < end:
        host, level, text = rnd.choice(noise)
        lines.append((t, f"{host} {level} " + text.format(ms=rnd.randint(40, 4000), s=rnd.randint(1, 90),
                                                          n=rnd.randint(10, 60))))
        t += timedelta(seconds=rnd.randint(600, 1800))
    d = date(2026, 8, 20)
    while d <= end.date():
        for host, failed, start_h in (("NAS-1", nas1_failed, 1), ("NAS-2", nas2_failed, 2)):
            started = datetime(d.year, d.month, d.day, start_h, rnd.randint(0, 9), rnd.randint(0, 59))
            lines.append((started, f"{host} INFO backup started (volume /data/projekter)"))
            took = timedelta(minutes=rnd.randint(14, 70), seconds=rnd.randint(0, 59))
            if d in failed:
                lines.append((started + took, f"{host} ERROR backup FAILED code={failed[d]} (volume /data/projekter)"))
            else:
                if rnd.random() < 0.15:
                    lines.append((started + took / 2, f"{host} WARNING backup slow: {rnd.randint(8, 30)} MB/s"))
                lines.append((started + took, f"{host} INFO backup finished OK ({rnd.randint(40, 160)} GB, "
                                              f"{took.seconds // 60} min)"))
        d += timedelta(days=1)
    lines.append((datetime(2026, 9, 20, 3, 41, 12), "NAS-2 ERROR restore-test FAILED (checksum mismatch) code=R-12"))
    lines.sort()
    text = "\n".join(f"{when:%Y-%m-%d %H:%M:%S} {what}" for when, what in lines) + "\n"
    in_sept = sorted(k for k in nas2_failed if k.month == 9)
    return text.encode("utf-8"), {"date": in_sept[-1].isoformat(), "code": nas2_failed[in_sept[-1]],
                                  "count": len(in_sept), "lines": len(lines)}


# ---------------------------------------------------------------- pictures

def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


BALL = {"size": (768, 512), "center": (384, 300), "radius": 80}


def ball_png() -> tuple[bytes, dict]:
    """bold.png: a red ball on a wooden floor in front of a grey wall."""
    import numpy as np
    from PIL import Image
    w, h = BALL["size"]
    cx, cy = BALL["center"]
    r = BALL["radius"]
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32)
    wall = 232 - 30 * (y / 330)
    img[...] = wall[..., None]
    floor = y >= 330
    plank = ((y - 330) // 26) % 2
    img[floor] = np.stack([150 - 14 * plank, 100 - 10 * plank, 62 - 6 * plank], -1)[floor]
    img[floor] *= (0.92 + 0.08 * np.sin(x / 37.0 + plank * 2))[floor][:, None]
    shadow = ((x - cx) / (r * 1.1)) ** 2 + ((y - (cy + r - 6)) / (r * 0.28)) ** 2 < 1
    img[shadow] *= 0.62
    d = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    ball = d <= r
    light = np.clip(1.15 - np.sqrt((x - (cx - r * 0.35)) ** 2 + (y - (cy - r * 0.35)) ** 2) / (r * 1.6), 0.35, 1.15)
    img[ball] = np.stack([205 * light, 28 * light, 30 * light], -1)[ball]
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    return _png(out), dict(BALL)


SIGN = {"size": (1024, 768), "text": "ORDRE 4711", "circles": 5, "triangles": 3}


def sign_png() -> tuple[bytes, dict]:
    """skilt.png: a green sign with ORDRE 4711, five blue circles and three orange triangles."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", SIGN["size"], (246, 245, 240))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((212, 70, 812, 240), radius=18, fill=(22, 92, 52))
    font = ImageFont.load_default(size=96)
    box = draw.textbbox((0, 0), SIGN["text"], font=font)
    draw.text((512 - (box[2] - box[0]) / 2 - box[0], 155 - (box[3] - box[1]) / 2 - box[1]), SIGN["text"],
              font=font, fill=(255, 255, 255))
    for cx, cy in ((130, 380), (330, 600), (520, 420), (760, 650), (900, 380)):
        draw.ellipse((cx - 40, cy - 40, cx + 40, cy + 40), fill=(40, 90, 220))
    for cx, cy in ((180, 640), (640, 560), (860, 520)):
        draw.polygon(((cx, cy - 48), (cx - 46, cy + 34), (cx + 46, cy + 34)), fill=(240, 140, 20))
    return _png(img), dict(SIGN)


def boat_png() -> tuple[bytes, dict]:
    """sejlbaad.png (16:9): a sailboat on a calm sea, sky and sun."""
    import numpy as np
    from PIL import Image, ImageDraw
    w, h = 1024, 576
    y = np.mgrid[0:h, 0:w][0].astype(np.float32)
    sky = np.stack([120 + 110 * (y / 330), 170 + 70 * (y / 330), 235 + 15 * (y / 330)], -1)
    sea = np.stack([20 + 30 * ((y - 330) / 246), 70 + 40 * ((y - 330) / 246), 130 + 30 * ((y - 330) / 246)], -1)
    arr = np.where((y < 330)[..., None], sky, sea)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)
    draw.ellipse((790, 60, 880, 150), fill=(255, 236, 160))
    for row in range(345, 576, 22):
        for x0 in range(-40, w, 90):
            off = (row * 7) % 45
            draw.arc((x0 + off, row - 6, x0 + off + 60, row + 8), 200, 340, fill=(200, 220, 240), width=2)
    draw.polygon(((420, 360), (640, 360), (605, 405), (455, 405)), fill=(110, 60, 30))
    draw.line((530, 360, 530, 170), fill=(70, 50, 30), width=6)
    draw.polygon(((536, 180), (536, 350), (630, 350)), fill=(250, 250, 245))
    draw.polygon(((524, 200), (524, 350), (450, 350)), fill=(235, 235, 228))
    return _png(img), {"size": (w, h)}


# ---------------------------------------------------------------- a small program with bugs (Python)

INVENTORY = '''"""A small stock register for a workshop: quantities per item number (SKU)."""


class Inventory:
    def __init__(self):
        self.items: dict[str, int] = {}

    def add(self, sku: str, qty: int) -> None:
        """Put qty more of sku in stock. qty must be positive."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        self.items[sku] = qty

    def remove(self, sku: str, qty: int) -> None:
        """Take qty of sku out of stock. An item that reaches 0 is no longer listed."""
        have = self.items.get(sku, 0)
        if qty <= 0 or qty > have:
            raise ValueError(f"cannot take {qty} of {sku}: {have} in stock")
        self.items[sku] = have - qty

    def listing(self) -> list[tuple[str, int]]:
        """(sku, qty) for every item in stock, sorted by sku."""
        return sorted(self.items.items())

    def total_value(self, prices: dict[str, float]) -> float:
        """The stock's value at the given unit prices, rounded to 2 decimals."""
        return round(sum(qty * prices[sku] for sku, qty in self.items.items()), 2)

    def low_stock(self, threshold: int) -> list[str]:
        """The skus with at most `threshold` in stock, sorted."""
        return sorted(sku for sku, qty in self.items.items() if qty < threshold)


def parse_line(line: str) -> tuple[str, int] | None:
    """One line of a stock file: "SKU;qty". Blank lines and lines starting with # give None.
    Spaces around the parts are ignored."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    sku, qty = line.split(";")
    return sku, int(qty)


def load(text: str) -> Inventory:
    """An inventory from a stock file's text; the same sku on two lines adds up."""
    inv = Inventory()
    for line in text.splitlines():
        parsed = parse_line(line)
        if parsed:
            inv.add(*parsed)
    return inv
'''

INVENTORY_TESTS = '''import unittest

from inventory import Inventory, load, parse_line


class InventoryTests(unittest.TestCase):
    def test_add_twice_adds_up(self):
        inv = Inventory()
        inv.add("A-1", 3)
        inv.add("A-1", 4)
        self.assertEqual(inv.listing(), [("A-1", 7)])

    def test_removed_to_zero_is_not_listed(self):
        inv = Inventory()
        inv.add("A-1", 2)
        inv.add("B-2", 5)
        inv.remove("A-1", 2)
        self.assertEqual(inv.listing(), [("B-2", 5)])

    def test_low_stock_includes_the_threshold(self):
        inv = Inventory()
        inv.add("A-1", 2)
        inv.add("B-2", 3)
        inv.add("C-3", 4)
        self.assertEqual(inv.low_stock(3), ["A-1", "B-2"])

    def test_parse_line_ignores_spaces(self):
        self.assertEqual(parse_line("  K-9 ; 12 "), ("K-9", 12))
        self.assertIsNone(parse_line("# comment"))

    def test_load_adds_up_the_same_sku(self):
        inv = load("A-1;2\\nB-2;1\\nA-1;5\\n")
        self.assertEqual(inv.listing(), [("A-1", 7), ("B-2", 1)])


if __name__ == "__main__":
    unittest.main()
'''

INVENTORY_HIDDEN = '''import unittest

from inventory import Inventory, load, parse_line


class HiddenInventoryTests(unittest.TestCase):
    def test_value_after_changes(self):
        inv = Inventory()
        inv.add("A-1", 10)
        inv.add("A-1", 5)
        inv.remove("A-1", 3)
        inv.add("B-2", 2)
        self.assertEqual(inv.total_value({"A-1": 2.5, "B-2": 10.0}), 50.0)

    def test_too_many_out_is_refused(self):
        inv = Inventory()
        inv.add("A-1", 2)
        with self.assertRaises(ValueError):
            inv.remove("A-1", 3)
        with self.assertRaises(ValueError):
            inv.remove("X-0", 1)
        self.assertEqual(inv.listing(), [("A-1", 2)])

    def test_gone_is_not_low(self):
        inv = Inventory()
        inv.add("A-1", 1)
        inv.add("B-2", 1)
        inv.remove("A-1", 1)
        self.assertEqual(inv.low_stock(1), ["B-2"])

    def test_load_with_comments_and_spaces(self):
        inv = load("# stock\\n\\n A-1 ; 2\\nA-1;3\\n  # end\\n")
        self.assertEqual(inv.listing(), [("A-1", 5)])

    def test_add_refuses_nothing(self):
        with self.assertRaises(ValueError):
            Inventory().add("A-1", 0)
'''


def inventory_files() -> dict[str, str]:
    return {"lager/inventory.py": INVENTORY, "lager/test_inventory.py": INVENTORY_TESTS}


ROMAN_HIDDEN = '''import unittest

from roman import from_roman, to_roman

KNOWN = {1: "I", 4: "IV", 9: "IX", 14: "XIV", 40: "XL", 90: "XC", 400: "CD", 444: "CDXLIV", 1994: "MCMXCIV",
         2026: "MMXXVI", 3888: "MMMDCCCLXXXVIII", 3999: "MMMCMXCIX"}


class HiddenRomanTests(unittest.TestCase):
    def test_known(self):
        for n, s in KNOWN.items():
            self.assertEqual(to_roman(n), s, n)
            self.assertEqual(from_roman(s), n, s)

    def test_round_trip(self):
        for n in range(1, 4000):
            self.assertEqual(from_roman(to_roman(n)), n, n)

    def test_out_of_range(self):
        for n in (0, -1, 4000, 10000):
            with self.assertRaises(ValueError, msg=n):
                to_roman(n)

    def test_not_roman(self):
        for s in ("", "IIII", "VX", "IC", "MMMM", "IIV", "XM", "VV", "LL", "DD", "IXI", "ABC", "XIIII"):
            with self.assertRaises(ValueError, msg=s):
                from_roman(s)
'''


# ---------------------------------------------------------------- texts for a word counter (C#)

def _text(spec: dict[str, int], rnd: random.Random) -> str:
    """Sentences made of exactly these words, each `count` times, with capitals and punctuation."""
    bag = [w for w, n in spec.items() for _ in range(n)]
    rnd.shuffle(bag)
    out, i = [], 0
    while i < len(bag):
        n = rnd.randint(6, 11)
        words = bag[i:i + n]
        i += n
        words = [w.upper() if rnd.random() < 0.05 else w for w in words]
        words[0] = words[0][0].upper() + words[0][1:]
        if len(words) > 4 and rnd.random() < 0.5:
            words[2] += ","
        if len(words) > 6 and rnd.random() < 0.2:
            words[5] = f"({words[5]})"
        out.append(" ".join(words) + rnd.choice((".", ".", ".", "!", "?")))
    lines, line = [], []
    for s in out:
        line.append(s)
        if len(" ".join(line)) > 70:
            lines.append(" ".join(line))
            line = []
    if line:
        lines.append(" ".join(line))
    return "\n".join(lines) + "\n"


WORDS_VISIBLE = {"og": 9, "pumpen": 9, "systemet": 7, "vandet": 7, "trykket": 6, "en": 4, "er": 4, "at": 3,
                 "skal": 3, "ventilen": 3, "kontrolleres": 3, "når": 2, "føleren": 2, "tanken": 2, "fyldes": 2,
                 "før": 2, "start": 2, "hver": 2, "uge": 2, "måles": 1, "efter": 1, "fejl": 1, "straks": 1,
                 "rapporteres": 1, "højt": 1, "lavt": 1, "niveau": 1}
WORDS_HIDDEN = {"data": 8, "måling": 6, "sensor": 5, "alarm": 4, "værdi": 4, "gemmes": 3, "hvert": 3, "minut": 3,
                "logges": 3, "sendes": 2, "til": 2, "serveren": 2, "kontrol": 2, "dagligt": 2, "nulstilles": 1,
                "når": 1, "grænsen": 1, "overskrides": 1, "øges": 1, "intervallet": 1}


def top_words(text: str, n: int = 5) -> list[tuple[str, int]]:
    """The rule the case asks for: letters only, any case alike; by count, then alphabetically."""
    counts = Counter(w.lower() for w in re.findall(r"[^\W\d_]+", text))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def word_texts() -> tuple[str, str, dict]:
    visible = _text(WORDS_VISIBLE, random.Random(7))
    hidden = _text(WORDS_HIDDEN, random.Random(8))
    return visible, hidden, {"visible": top_words(visible), "hidden": top_words(hidden)}
