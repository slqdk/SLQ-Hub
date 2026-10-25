"""The test cases: a hard task in each area the assistant covers, with what a right answer holds.

Each case is a message as a person would write it (most in Danish, as the household writes), sometimes with files,
run as a real chat. What can be checked by machine is: the Hub's checks here (the answer's text, the tools it used)
and, for what the turn made, the sandbox's (check_lib.py). What only a person can judge - how a picture looks, how
a voice sounds - each case says under `review`; `expect` says what a good answer does, for whoever reads the export.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable

from . import fixtures
from .checks import (C, Turn, answer_line, bullets, json_object, language, near, numbers, only_json, plain, urls,
                     used, word_count)

AREAS = (("chat", "Chat"), ("documents", "Documents"), ("images", "Images"), ("sound", "Sound"),
         ("video", "Video"), ("code", "Code"), ("agent", "Research and tasks"))


@dataclass
class Inputs:
    attach: list[tuple[str, bytes, str]] = field(default_factory=list)    # (name, data, mime): with the message
    files: dict[str, bytes] = field(default_factory=dict)                 # in the working folder before the turn
    refs: list[str] = field(default_factory=list)                          # of those: given in the message
    hidden: dict[str, bytes] = field(default_factory=dict)                # .hub/tests/<name>, after the turn
    expected: dict = field(default_factory=dict)                          # what a right answer holds


@dataclass
class Case:
    id: str
    area: str
    title: str
    what: str                       # one line: what it asks for
    prompt: str
    expect: str                     # what a good answer does (for whoever reads the results)
    check: Callable[[Turn, dict], list[dict]]
    inputs: Callable[[], Inputs] = Inputs
    needs: tuple[str, ...] = ()     # as GET /hub/api/help names them: images.generate, sound.music, video.t2v, ...
    sandbox: bool = False           # check_lib.py has checks for it
    timeout: int = 900              # seconds for the turn
    check_timeout: int = 300        # seconds for the sandbox's checks
    internet: bool = False          # its internet question (asking first) is answered Allow once
    review: str = ""                # what a person should look at or listen to

    def info(self) -> dict:
        return {"id": self.id, "area": self.area, "title": self.title, "what": self.what, "prompt": self.prompt,
                "expect": self.expect, "needs": list(self.needs), "internet": self.internet, "review": self.review,
                "timeout": self.timeout}


def _parts(line: str | None) -> list[str]:
    return [p.strip() for p in re.split(r"[;|]", line)] if line else []


_COUNT_WORDS = {"en": 1, "et": 1, "one": 1, "to": 2, "two": 2, "tre": 3, "three": 3, "fire": 4, "four": 4, "fem": 5,
                "five": 5, "seks": 6, "six": 6, "syv": 7, "seven": 7, "otte": 8, "eight": 8}


def _count(text: str) -> int | None:
    m = re.search(r"\b\d+\b", text or "")
    if m:
        return int(m.group(0))
    for w in re.findall(r"[a-zæøå]+", (text or "").lower()):
        if w in _COUNT_WORDS:
            return _COUNT_WORDS[w]
    return None


def _first(line: str) -> list[float]:
    """The values the first number in a line can mean."""
    m = re.search(r"\d[\d.,  ]*\d|\d", line or "")
    return numbers(m.group(0)) if m else []


# ---------------------------------------------------------------- chat

LOGIC = """Løs denne logiske opgave. Fire personer - Anna, Bent, Cecilie og Dennis - bor i hver sit hus. Husene ligger \
på række og er nummereret 1-4 fra venstre mod højre. Husene har hver sin farve: rød, blå, grøn og gul. Hver person har \
ét kæledyr: en hund, en kat, en papegøje eller en fisk.

1. Anna bor lige til højre for den, der har katten.
2. Det blå hus ligger lige til venstre for det røde hus.
3. Fisken bor i det røde hus.
4. Bent og Dennis bor ikke ved siden af hinanden.
5. Papegøjen bor i det grønne hus.
6. Hunden bor i hus 2.
7. Dennis bor ved siden af den, der har papegøjen.

Hvem har fisken, og hvilken farve har huset, hvor katten bor? Forklar kort, hvordan du kom frem til det, og afslut \
med én linje: SVAR: <navn>, <farve>"""


def _logic(t: Turn, e: dict) -> list[dict]:
    line = answer_line(t.answer, "SVAR")
    if line is None:
        return [C("ends with SVAR: <name>, <colour>", False, "no SVAR line")]
    low = line.lower()
    names = {n for n in ("anna", "bent", "cecilie", "dennis") if re.search(rf"\b{n}\b", low)}
    colours = {c for c in ("rød", "blå", "grøn", "gul") if re.search(rf"\b{c}", low)}
    return [C("Dennis has the fish", names == {"dennis"}, line), C("the cat lives in the yellow house",
                                                                  colours == {"gul"}, line)]


MATH = """A tank is filled by two pumps and emptied through a valve. Pump A alone fills the empty tank in 6 hours, \
pump B alone in 4 hours, and valve C empties a full tank in 12 hours. The tank is empty at 08:00, when A and B start. \
At 09:00 C is opened, and at 10:00 B stops; A and C keep running. At what time is the tank full? Show the \
calculation and end with one line: ANSWER: <hh:mm>"""


def _math(t: Turn, e: dict) -> list[dict]:
    line = answer_line(t.answer, "ANSWER", "SVAR")
    ok = bool(line) and (re.search(r"(?<![\d:.])13[:.]00(?!\d)", line) is not None
                         or re.fullmatch(r"\W*(?:at\s*)?13\W*", line) is not None)
    lang, da, en = language(t.answer)
    return [C("13:00", ok, line or "no ANSWER line"),
            C("answers in English", lang == "en", f"English words {en:.0%}, Danish {da:.0%}", "should")]


ORDER = """Udtræk oplysningerne fra e-mailen nedenfor, og svar KUN med ét JSON-objekt (ingen forklaring) med præcis \
disse nøgler: firma, kontakt, varenummer, antal (heltal), stykpris (tal), total_ekskl_moms (tal), total_inkl_moms \
(tal, med 25 % moms), leveringsdato (ÅÅÅÅ-MM-DD), postnummer (tekst) og email.

E-mail:
Hej Mette
Tak for en god snak i går. Jeg bekræfter hermed vores ordre på 14 stk. kugleventiler, type KV-250, til 385,50 kr. \
pr. stk. ekskl. moms. Vi skal have dem leveret senest fredag den 13. november 2026 til Industrivej 7, 8600 Silkeborg. \
Fakturaen sendes til bogholderi@holmhydraulik.dk.
Venlig hilsen
Lene Holm
Indkøbschef, Holm Hydraulik ApS"""
ORDER_WANT = {"firma": "Holm Hydraulik ApS", "kontakt": "Lene Holm", "varenummer": "KV-250", "antal": 14,
              "stykpris": 385.5, "total_ekskl_moms": 5397.0, "total_inkl_moms": 6746.25,
              "leveringsdato": "2026-11-13", "postnummer": "8600", "email": "bogholderi@holmhydraulik.dk"}


def _order(t: Turn, e: dict) -> list[dict]:
    obj, why = json_object(t.answer)
    if obj is None:
        return [C("a JSON object", False, why)]
    out = [C("a JSON object", True, f"{len(obj)} keys")]
    missing = [k for k in ORDER_WANT if k not in obj]
    extra = [k for k in obj if k not in ORDER_WANT]
    out.append(C("all ten keys", not missing, f"missing: {', '.join(missing)}" if missing else "all there"))
    right, wrong = [], []
    for k, want in ORDER_WANT.items():
        got = obj.get(k)
        if isinstance(want, str):
            ok = isinstance(got, str) and got.strip().lower() == want.lower()
        else:
            ok = isinstance(got, (int, float)) and not isinstance(got, bool) and abs(got - want) < 0.011
        (right if ok else wrong).append(k)
    out.append(C("the total with VAT: 6746.25", "total_inkl_moms" in right, f"got {obj.get('total_inkl_moms')!r}"))
    shown = ", ".join(f"{k}={obj.get(k)!r} (want {ORDER_WANT[k]!r})" for k in wrong)
    out.append(C("at least 8 of 10 values right", len(right) >= 8, shown or "all right"))
    out.append(C("all 10 values right", not wrong, shown or "all right", "should"))
    out.append(C("no other keys", not extra, ", ".join(extra), "should"))
    out.append(C("nothing but the JSON", only_json(t.answer), "", "should"))
    return out


INVITE = """Skriv en kort invitation til julefrokosten for kollegerne i et lille ingeniørfirma. Krav:
- præcis 4 punkter i en punktliste, hvert punkt højst 12 ord
- nævn datoen (fredag den 4. december), stedet (kantinen), og at man skal tilmelde sig senest den 20. november
- brug ikke ordet "hyggelig" i nogen form
- afslut med en linje, der begynder med "Venlig hilsen\""""


def _invite(t: Turn, e: dict) -> list[dict]:
    text = t.answer
    lang, da, en = language(text)
    pts = bullets(text)
    long = [p for p in pts if word_count(p) > 12]
    low = plain(text).lower()
    lines = [x.strip() for x in plain(text).splitlines() if x.strip()]
    return [C("in Danish", lang == "da", f"Danish words {da:.0%}, English {en:.0%}"),
            C("exactly 4 points", len(pts) == 4, f"{len(pts)} points"),
            C("each point at most 12 words", bool(pts) and not long,
              "; ".join(f"{word_count(p)} words: {p[:60]}" for p in long) or "all short", "should"),
            C("the date: 4. december", re.search(r"\b4\.\s*december", low) is not None),
            C("sign up by 20. november", re.search(r"\b20\.\s*november", low) is not None),
            C("the place: the canteen", "kantine" in low, "", "should"),
            C('no "hyggelig" in any form', re.search(r"hyggelig", low) is None,
              ", ".join(sorted(set(re.findall(r"\w*hyggelig\w*", low))))),
            C('a line beginning "Venlig hilsen" at the end', any(x.lower().startswith("venlig hilsen")
                                                                 for x in lines[-3:]), lines[-1][:80] if lines else "",
              "should")]


# ---------------------------------------------------------------- documents

SALES = """Vedhæftet er vores salgsdata for 2025 (eksporteret fra Excel med dansk talformat). Svar på:
1. Hvilken region havde den største omsætning i 2. kvartal (april-juni), og hvor stor var den i kr. (antal × \
stykpris)?
2. Hvilken måned havde den største samlede omsætning?
3. Lav et søjlediagram over omsætningen pr. måned, og gem det som omsaetning.png.

Afslut svaret med præcis disse to linjer:
REGION: <region>; <omsætning i kr.>
MÅNED: <måned>"""
_REGION_NAMES = {"Nord": ("nord", "north"), "Syd": ("syd", "south"), "Øst": ("øst", "oest", "east"),
                 "Vest": ("vest", "west")}
_MONTHS_EN = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
              "november", "december")


def _sales_in() -> Inputs:
    data, exp = fixtures.sales_csv()
    return Inputs(attach=[("salg_2025.csv", data, "text/csv")], expected=exp)


def _sales(t: Turn, e: dict) -> list[dict]:
    out = []
    region = answer_line(t.answer, "REGION")
    if region is None:
        out.append(C("ends with REGION: <region>; <revenue>", False, "no REGION line"))
    else:
        low = region.lower()
        hits = sorted((low.find(a), name) for name, names in _REGION_NAMES.items() for a in names
                      if re.search(rf"\b{a}\b", low))
        named = hits[0][1] if hits else None
        out.append(C(f"Q2's biggest region: {e['q2_region']}", named == e["q2_region"], region))
        out.append(C(f"its revenue: {e['q2_revenue']:,.2f} kr.", near(region, e["q2_revenue"]), region))
    month = answer_line(t.answer, "MÅNED", "MAANED", "MANED", "MONTH")
    m = e["best_month"]
    if month is None:
        out.append(C("ends with MÅNED: <month>", False, "no MÅNED line"))
    else:
        low = month.lower()
        ok = fixtures.MONTHS_DA[m - 1] in low or _MONTHS_EN[m - 1] in low or re.search(rf"\b0?{m}\b", low)
        out.append(C(f"the best month: {fixtures.MONTHS_DA[m - 1]}", bool(ok), month))
    out.append(used(t, ("bash",), "worked it out with code", "info"))
    return out


MANUAL = """Vedhæftet er driftsmanualen for pumpestationen. Hvad er det gældende serviceinterval for pumpe P3, og hvor \
i manualen står det? Afslut med én linje: SVAR: <antal> driftstimer"""


def _manual_in() -> Inputs:
    data, exp = fixtures.manual_pdf()
    return Inputs(attach=[("driftsmanual.pdf", data, "application/pdf")], expected=exp)


def _manual(t: Turn, e: dict) -> list[dict]:
    line = answer_line(t.answer, "SVAR")
    text = plain(t.answer)
    return [C("1.900 hours (changed on page 11)", line is not None and e["hours"] in _first(line), line or "no SVAR line"),
            C("says where: page 11", re.search(r"(side|sider|s\.|page)[^\n]{0,30}\b11\b", text, re.I) is not None, "",
              "should"),
            C("mentions the old 2.150 hours (page 9)", near(text, e["old_hours"], rel=0), "", "should")]


LOG = """Vedhæftet er serverloggen fra august til oktober. Hvornår fejlede backuppen på NAS-2 sidst i september, med \
hvilken fejlkode, og hvor mange gange fejlede backuppen på NAS-2 i alt i september? Afslut med én linje: \
SVAR: <dato>; <fejlkode>; <antal>"""


def _log_in() -> Inputs:
    data, exp = fixtures.server_log()
    return Inputs(attach=[("server.log", data, "text/plain")], expected=exp)


def _log(t: Turn, e: dict) -> list[dict]:
    line = answer_line(t.answer, "SVAR")
    parts = _parts(line)
    if len(parts) < 2:
        return [C("ends with SVAR: <date>; <code>; <count>", False, line or "no SVAR line")]
    day = parts[0].lower()
    date_ok = re.search(r"\b27\b", day) is not None and re.search(r"\b0?9\b|sep", day) is not None
    code_ok = re.search(r"e-?5102", line.lower()) is not None
    count = _count(parts[2]) if len(parts) > 2 else None
    return [C("the last failure in September: 27 September", date_ok, parts[0]),
            C("its code: E-5102", code_ok, line),
            C("3 failures in September (not the restore test, not August or October)", count == e["count"],
              f"said {count}", "should")]


DOCX = """Lav et Word-dokument, rapport.docx, med en månedsrapport for et lille produktionsfirma:
- titlen "Månedsrapport oktober 2026"
- tre afsnit med overskrifterne "Produktion", "Kvalitet" og "Plan for november" (rigtige overskrifter, ikke bare fed \
tekst)
- under Produktion en tabel med 4 rækker og 3 kolonner: en overskriftsrække (Linje, Produceret, Kassation %) og tre \
linjer med eksempeltal
- under Plan for november en punktliste med 3 punkter
Fyld afsnittene med et par sætninger realistisk tekst."""

XLSX = """Lav et regneark, budget.xlsx, med et budget for et år:
- række 1: overskrifterne Måned, Indtægter, Udgifter og Resultat
- månederne januar til december i A2:A13 og eksempeltal i Indtægter og Udgifter
- Resultat i kolonne D skal være en formel (Indtægter minus Udgifter) i hver række, ikke et fast tal
- række 14: Total med SUM-formler for Indtægter, Udgifter og Resultat"""


def _made(*suffixes: str, level: str = "info"):
    def fn(t: Turn, e: dict) -> list[dict]:
        files = [f["path"] for f in t.files if f["path"].lower().endswith(suffixes)]
        return [C(f"a {'/'.join(suffixes)} file in the turn's files", bool(files), ", ".join(files) or "none", level)]
    return fn


# ---------------------------------------------------------------- pictures

def _ball_in() -> Inputs:
    data, exp = fixtures.ball_png()
    return Inputs(attach=[("bold.png", data, "image/png")], expected=exp)


def _sign_in() -> Inputs:
    data, exp = fixtures.sign_png()
    return Inputs(attach=[("skilt.png", data, "image/png")], expected=exp)


def _boat_in() -> Inputs:
    data, exp = fixtures.boat_png()
    return Inputs(attach=[("sejlbaad.png", data, "image/png")], expected={**exp, "seconds": [2, 11]})


def _tool(names: tuple[str, ...], what: str):
    return lambda t, e: [used(t, names, what)]


def _vision(t: Turn, e: dict) -> list[dict]:
    line = answer_line(t.answer, "SVAR")
    parts = _parts(line)
    circles = _count(parts[1]) if len(parts) > 1 else None
    triangles = _count(parts[2]) if len(parts) > 2 else None
    return [C("reads ORDRE 4711", bool(line) and "4711" in line, line or "no SVAR line"),
            C("5 blue circles", circles == e["circles"], f"said {circles}"),
            C("3 orange triangles", triangles == e["triangles"], f"said {triangles}", "should")]


# ---------------------------------------------------------------- code

ROMAN = """Skriv et Python-modul, roman.py, med to funktioner: to_roman(n: int) -> str og from_roman(s: str) -> int for \
tallene 1-3999 (almindelige romertal med subtraktiv skrivemåde, fx 1994 = MCMXCIV). Ugyldigt input skal give \
ValueError: tal uden for 1-3999 og strenge, der ikke er korrekte romertal (fx "IIII", "VX", "IC", "MMMM" og den tomme \
streng). Skriv også tests med unittest, og kør dem."""


def _roman_in() -> Inputs:
    return Inputs(hidden={"test_roman_hidden.py": fixtures.ROMAN_HIDDEN.encode()})


DEBUG = """Testene i lager/test_inventory.py fejler. Find og ret fejlene i lager/inventory.py uden at ændre testene, og \
kør testene til sidst. Fortæl kort, hvilke fejl du fandt."""


def _debug_in() -> Inputs:
    files = {k: v.encode() for k, v in fixtures.inventory_files().items()}
    return Inputs(files=files, refs=list(files),
                  hidden={"test_inventory_orig.py": fixtures.INVENTORY_TESTS.encode(),
                          "test_inventory_hidden.py": fixtures.INVENTORY_HIDDEN.encode()},
                  expected={"tests_sha": hashlib.sha256(fixtures.INVENTORY_TESTS.encode()).hexdigest()})


WORDS = """Skriv et C#-konsolprogram (.NET) i mappen ordtaeller, der læser en tekstfil (stien gives som argument) og \
udskriver de 5 hyppigste ord, ét pr. linje i formatet "ord: antal", sorteret efter antal (flest først) og ved lige \
mange alfabetisk. Store og små bogstaver tæller som det samme ord, og tegnsætning tæller ikke med. Byg programmet, og \
kør det på tekst.txt."""


def _words_in() -> Inputs:
    visible, hidden, exp = fixtures.word_texts()
    return Inputs(files={"tekst.txt": visible.encode()}, refs=["tekst.txt"], hidden={"skjult.txt": hidden.encode()},
                  expected={"visible": [list(x) for x in exp["visible"]], "hidden": [list(x) for x in exp["hidden"]]})


def _words(t: Turn, e: dict) -> list[dict]:
    want = "\n".join(f"{w}: {n}" for w, n in e["visible"])
    said = [(m.group(1).lower(), int(m.group(2))) for m in re.finditer(r"^\W*([^\W\d_]+)\W*:\s*(\d+)\s*$",
                                                                         plain(t.answer), re.M)]
    return [used(t, ("bash",), "built and ran it", "should"),
            C("the answer shows the right top 5", [list(x) for x in said[:5]] == e["visible"], f"expected:\n{want}",
              "should")]


PAGE = """Lav en lille interaktiv side som artefakt, der omregner mellem celsius og fahrenheit: to felter, der \
opdaterer hinanden, mens man skriver, og en knap, der nulstiller begge felter."""


def _page(t: Turn, e: dict) -> list[dict]:
    if not t.artifacts:
        return [C("an artifact was made", False, "no artifact in the chat")]
    a = t.artifacts[-1]
    low = (a.get("content") or "").lower()
    inputs = len(re.findall(r"<input\b", low))
    return [C("an interactive artifact was made", a.get("type") in ("html", "react"),
              f"{a.get('title')} ({a.get('type')}, {len(low):,} characters)"),
            C("two input fields and code", inputs >= 2 and ("<script" in low or a.get("type") == "react"),
              f"{inputs} inputs"),
            C("a reset button", re.search(r"<button\b|type=[\"']reset", low) is not None, "", "should"),
            C("the conversion formula", re.search(r"9\s*/\s*5|1\.8\b|5\s*/\s*9|0\.5555", low) is not None, "",
              "should")]


# ---------------------------------------------------------------- research and tasks

WEB = """Find ud af, hvad den nyeste stabile version af Python er, og hvornår den blev udgivet. Brug nettet, og angiv \
kilden med et link."""


def _web(t: Turn, e: dict) -> list[dict]:
    links = urls(t.answer)
    m = re.search(r"\b3\.1\d\.\d+\b", t.answer)
    return [used(t, ("web_search", "web_fetch"), "used the web"),
            C("gives a link to its source", bool(links), ", ".join(links[:3]) or "no link"),
            C("names a Python 3 version", m is not None, m.group(0) if m else "none", "should"),
            C("python.org among the sources", any("python.org" in u for u in links), "", "should")]


FILES = """Lav i mappen maalinger: en fil data.csv med 24 rækker (kolonnerne time og temperatur; temperaturer mellem \
15 og 25 grader med én decimal) og et Python-script analyse.py, der læser data.csv og skriver resultat.md med middel, \
minimum og maksimum med to decimaler. Kør scriptet, så resultat.md kommer i samme mappe, og fortæl mig resultaterne."""


# ---------------------------------------------------------------- the suite

CASES: list[Case] = [
    Case("chat.logic", "chat", "Logic puzzle", "Four houses, seven clues, one answer - in Danish.", LOGIC,
         "Dennis has the fish; the cat lives in the yellow house (1 Bent yellow cat, 2 Anna blue dog, 3 Dennis red "
         "fish, 4 Cecilie green parrot). The reasoning should follow from the clues, not guess.", _logic),
    Case("chat.math", "chat", "Word problem", "A tank, two pumps and a valve that change over time - in English.",
         MATH, "13:00. 08-09 A+B fill 5/12; 09-10 A+B-C fill 1/3, so 3/4 at 10:00; then A-C fill 1/12 an hour, and "
         "the last 1/4 takes 3 hours. Answered in English.", _math),
    Case("chat.json", "chat", "Data from an e-mail as JSON", "Ten fields from a Danish order e-mail, two of them "
         "worked out.", ORDER, "Only a JSON object with the ten keys: Holm Hydraulik ApS, Lene Holm, KV-250, 14, "
         "385.5, 5397.0, 6746.25 (with 25 % VAT), 2026-11-13, \"8600\", bogholderi@holmhydraulik.dk.", _order),
    Case("chat.danish", "chat", "Danish text with rules", "An invitation that keeps five rules about form and words.",
         INVITE, "Danish, exactly four short points with the date, the place and the sign-up date, no \"hyggelig\" "
         "in any form, ending with \"Venlig hilsen\".", _invite, review="Is the Danish natural?"),
    Case("documents.csv", "documents", "Sales figures (CSV)", "A year of sales in Danish number format: the best "
         "region in Q2, the best month and a chart.", SALES, "Reads the semicolons, dd-mm-yyyy dates and 1.199,00 "
         "prices right (with code); Øst had the most in Q2 (408,924.50 kr.), November was the best month; "
         "omsaetning.png is a bar chart of the twelve months.", _sales, _sales_in, sandbox=True, timeout=1500,
         review="Is omsaetning.png a readable bar chart?"),
    Case("documents.pdf", "documents", "A fact in a manual (PDF)", "A 12-page manual where the answer was changed "
         "later in the book.", MANUAL, "1.900 hours: page 9 says 2.150, but the change log on page 11 lowered it "
         "from 1 January 2026. The answer should say both pages.", _manual, _manual_in, timeout=1200),
    Case("documents.log", "documents", "Needle in a server log", "3,600 log lines: when a backup failed, with what "
         "code, how often.", LOG, "27 September 2026, E-5102, 3 times in September (3rd, 14th, 27th) - not the "
         "restore test on the 20th, not 28 August or 2 October. Done with grep or code, not by reading it all.",
         _log, _log_in, timeout=1200),
    Case("documents.word", "documents", "A Word report", "A .docx with a title, real headings, a table and a list.",
         DOCX, "rapport.docx with the title, three Heading-styled headings, a 4x3 table under Produktion and three "
         "list points under Plan for november, with sensible text.", _made(".docx"), sandbox=True, timeout=1500,
         review="Open rapport.docx: does it look like a report?"),
    Case("documents.excel", "documents", "A budget with formulas", "An .xlsx where the results are formulas, not "
         "numbers.", XLSX, "budget.xlsx with the headings, twelve months, =B-C formulas in D2:D13 and SUM formulas "
         "in row 14.", _made(".xlsx"), sandbox=True, timeout=1500),
    Case("images.poster", "images", "A poster with text", "A Christmas concert poster with two lines of text that "
         "must be spelled right.", "Lav en plakat til en julekoncert. Overskriften \"JUL I HALLEN\" skal stå stort "
         "og tydeligt øverst og teksten \"12. DECEMBER\" nederst. Et juletræ med lys i midten og en mørkeblå "
         "baggrund.", "A poster with JUL I HALLEN at the top and 12. DECEMBER at the bottom, spelled right, a lit "
         "tree and a dark blue background.", _tool(("image_generate",), "made a picture"), needs=("images.generate",),
         sandbox=True, timeout=1200, review="Is the text spelled right and readable? Is it a poster?"),
    Case("images.edit", "images", "Change one thing in a picture", "A red ball made blue, nothing else changed.",
         "Gør den røde bold på billedet blå. Resten af billedet skal være uændret.", "The same picture with a blue "
         "ball: the wall, the floor and the shadow as before.", _tool(("image_edit",), "edited the picture"),
         _ball_in, needs=("images.edit",), sandbox=True, timeout=1200, review="Is only the ball changed?"),
    Case("images.cutout", "images", "Remove a background", "The ball alone on a transparent background.",
         "Fjern baggrunden fra billedet, så kun bolden er tilbage med gennemsigtig baggrund.", "A PNG with "
         "transparency where only the ball (perhaps with its shadow) is left.",
         _tool(("image_remove_background",), "removed the background"), _ball_in, needs=("images.cutout",),
         sandbox=True, timeout=1200, review="Are the ball's edges clean?"),
    Case("images.vision", "images", "Read and count in a picture", "The text on a sign, and shapes counted by "
         "colour.", "Hvad står der på skiltet på billedet, og hvor mange blå cirkler og orange trekanter er der? "
         "Afslut med én linje: SVAR: <teksten på skiltet>; <antal cirkler>; <antal trekanter>", "ORDRE 4711, 5 blue "
         "circles, 3 orange triangles - by looking at the picture, or with OCR and code if the model cannot see.",
         _vision, _sign_in, timeout=900),
    Case("sound.speech", "sound", "Read aloud in Danish", "Two Danish sentences with æ, ø and å, read aloud.",
         "Læs denne tekst højt på dansk:\n\n\"Velkommen til SLQ Hub. I dag tester vi, om talesyntesen udtaler æ, ø "
         "og å rigtigt, og om den holder en lille pause ved hvert punktum. Tak, fordi du lyttede.\"",
         "A Danish voice reading the whole text, 6-30 seconds.", _tool(("speech_generate",), "made speech"),
         lambda: Inputs(expected={"seconds": [6, 30]}), needs=("sound.speech",), sandbox=True, timeout=1200,
         review="Listen: are æ, ø and å right, are there pauses at the full stops?"),
    Case("sound.music", "sound", "A short piece of music", "30 seconds, calm piano with strings, no vocals.",
         "Lav et instrumentalt musikstykke på 30 sekunder: roligt klaver med strygere, cirka 80 BPM, melankolsk "
         "stemning, ingen vokal.", "About 30 seconds of calm piano and strings without singing.",
         _tool(("music_generate",), "made music"), lambda: Inputs(expected={"seconds": [25, 40]}),
         needs=("sound.music",), sandbox=True, timeout=1200,
         review="Listen: piano and strings, calm, no voice, one piece rather than parts?"),
    Case("sound.effect", "sound", "A sound effect", "An old door creaking open and slamming, about 6 seconds.",
         "Lav en lydeffekt på cirka 6 sekunder: en gammel trædør, der langsomt knirker op og til sidst smækker i.",
         "A creak that ends in a slam, 4-10 seconds.", _tool(("sound_generate",), "made a sound"),
         lambda: Inputs(expected={"seconds": [4, 10]}), needs=("sound.sound",), sandbox=True, timeout=1200,
         review="Listen: a creak, then a slam?"),
    Case("video.t2v", "video", "Video from a description", "A red kite over a green hill, the camera following.",
         "Lav en kort video: en rød papirdrage flyver over en grøn bakke en blæsende dag med hvide skyer, og kameraet "
         "følger dragen langsomt.", "A few seconds of a red kite in the wind over a green hill, the camera moving "
         "with it.", _tool(("video_generate",), "made a video"), lambda: Inputs(expected={"seconds": [2, 11]}),
         needs=("video.t2v",), sandbox=True, timeout=3600, review="Watch: does it follow the description?"),
    Case("video.i2v", "video", "Make a picture move", "A drawn sailboat: waves and a gentle rocking.",
         "Få billedet til at bevæge sig: bølgerne skvulper, og sejlbåden gynger let. Kameraet står stille.",
         "The picture itself moving: the first frame is the picture, the waves move, the boat rocks, the camera is "
         "still.", _tool(("video_generate",), "made a video"), _boat_in, needs=("video.i2v",), sandbox=True,
         timeout=3600, review="Watch: does it start from the picture, does the camera stay still?"),
    Case("code.python", "code", "A module with tests", "Roman numerals both ways, strict about bad input, with its "
         "own tests.", ROMAN, "roman.py passes hidden tests: all of 1-3999 both ways, and ValueError for 0, 4000, "
         "IIII, VX, IC, MMMM, IXI and the empty string; it wrote and ran unittest tests.",
         lambda t, e: [used(t, ("bash",), "ran code", "should")], _roman_in, needs=("code",), sandbox=True,
         timeout=1800),
    Case("code.debug", "code", "Find the bugs", "Four bugs in a small stock register; the tests must pass without "
         "changing them.", DEBUG, "Finds all four: add overwrites instead of adding, remove keeps items at 0, "
         "low_stock uses < for 'at most', parse_line keeps the spaces around the SKU. The given and hidden tests "
         "pass; the test file is unchanged.", lambda t, e: [used(t, ("bash",), "ran the tests", "should")],
         _debug_in, needs=("code",), sandbox=True, timeout=1800),
    Case("code.csharp", "code", "A C# program", "A word counter in C#, built and run in the sandbox.", WORDS,
         "A console project in ordtaeller that prints \"og: 9, pumpen: 9, systemet: 7, vandet: 7, trykket: 6\" "
         "for tekst.txt (ties alphabetical) and gets a text it has not seen right too.", _words, _words_in,
         needs=("code",), sandbox=True, timeout=1800, check_timeout=900),
    Case("code.web", "code", "An interactive page", "A Celsius-Fahrenheit converter as an artifact.", PAGE,
         "An HTML (or React) artifact with two fields that update each other as you type, the right formula and a "
         "reset button.", _page, timeout=1200, review="Open the artifact: does it convert both ways, does reset "
                                                       "work?"),
    Case("agent.web", "agent", "Research on the web", "The newest Python version and its date, with a source.",
         WEB, "Searches, reads python.org (or another good source) and gives the version, the date and the link. "
         "Check the version against python.org on the day of the run.", _web, timeout=1200, internet=True,
         review="Is the version really the newest one on the day of the run?"),
    Case("agent.files", "agent", "A task in several steps", "Make data, a script and its result in a folder, and "
         "report.", FILES, "maalinger/ with data.csv (24 rows), analyse.py and resultat.md whose mean, minimum and "
         "maximum match data.csv; the answer gives them too.", lambda t, e: [used(t, ("bash",), "ran the script")],
         needs=("code",), sandbox=True, timeout=1500),
]
BY_ID = {c.id: c for c in CASES}
