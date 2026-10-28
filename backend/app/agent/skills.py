"""Skills: folders with a SKILL.md (front matter `name`, `description`) and any files it refers to - the format of
Claude's skills (agentskills.io; scripts/, references/ and assets/ by convention).

Whose (0.23.0):
- the owner's: <data>/skills/<name>/, ref "<name>" (where they always were);
- a person's: <data>/skills/@<user id>/<name>/, ref "@<id>/<name>";
- the Hub's own (skill-creator): app/agent/builtin_skills/<name>/ in the repo, ref "hub/<name>", read-only.
A skill is its maker's until they share it with the family. The owner's not-shared skills are listed in
<data>/skills/.disabled.json (its name is from when skills were "switched off" for every chat), a person's shared
ones in @<id>/.shared.json. Everyone sees their own skills, the shared ones and the Hub's.

Picked by hand (0.23.0): a chat's skills are the ones chosen for it and for its project; nothing is offered on its
own. Their SKILL.md goes into the system prompt, their other files are read with the skill tool and copied into
the working folder's .hub/skills/ for code.

Made with the assistant (0.23.0): it writes a skill folder in the working folder and calls skill_propose, which
checks it and keeps a copy as a draft (<data>/skills/.drafts/<id>/) for two weeks; the card in the chat adds it."""
from __future__ import annotations

import io
import json
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath

BUILTIN = Path(__file__).parent / "builtin_skills"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")            # folder names (older skills may have . and _)
SPEC_NAME = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)$")  # agentskills.io: what a new skill is named
RESERVED = ("claude", "anthropic")                               # Claude refuses skills named with these
MAX_SKILL_BYTES = 50 * 1024 * 1024
MAX_FILES = 500
DRAFT_DAYS = 14
REF_RE = re.compile(r"^(?:(?P<hub>hub)/|@(?P<user>\d+)/)?(?P<name>[a-z0-9][a-z0-9._-]{0,63})$")
CATEGORY_MAX = 40          # characters of a category's name (0.41.0)


def parse_front_matter(text: str) -> tuple[dict, str]:
    """A small YAML subset: `key: value`, quoted values, folded (>) and literal (|) blocks, and one level of
    `key:` with indented `sub: value` lines (agentskills' metadata map)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head, body = text[3:end].strip("\n"), text[end + 4:].lstrip("\n")
    meta: dict = {}
    key = None
    block: list[str] = []
    style = None

    def flush():
        if key is not None and style:
            meta[key] = (" " if style == ">" else "\n").join(s.strip() for s in block).strip()

    for line in head.splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m and not line.startswith((" ", "\t")):
            flush()
            key, val = m.group(1), m.group(2).strip()
            style, block = None, []
            if val in (">", "|", ">-", "|-", ">+", "|+"):
                style = val[0]
            elif val == "":
                meta[key] = {}                                  # a map may follow (metadata:)
            else:
                meta[key] = _unquote(val)
        elif style and key:
            block.append(line)
        elif key and isinstance(meta.get(key), dict):
            sub = re.match(r"^\s+([A-Za-z0-9_.-]+):\s*(.*)$", line)
            if sub:
                meta[key][sub.group(1)] = _unquote(sub.group(2).strip())
        elif key and isinstance(meta.get(key), str) and line.strip():     # continuation of a plain scalar
            meta[key] = f"{meta[key]} {line.strip()}"
    flush()
    return meta, body


def _unquote(val: str) -> str:
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
        return val[1:-1].replace("''", "'") if val[0] == "'" else val[1:-1].replace('\\"', '"')
    return val


def check_skill_md(text: str) -> tuple[dict, list[str]]:
    """What is wrong with a new skill's SKILL.md by the format Claude and agentskills.io use: (front matter,
    problems)."""
    meta, body = parse_front_matter(text)
    problems = []
    name = meta.get("name") if isinstance(meta.get("name"), str) else ""
    desc = meta.get("description") if isinstance(meta.get("description"), str) else ""
    if not text.startswith("---"):
        problems.append("SKILL.md must start with front matter: a --- line, name: and description:, a --- line")
    if not name:
        problems.append("the front matter has no name")
    elif not SPEC_NAME.match(name):
        problems.append(f"name {name!r} must be 1-64 lowercase letters, digits and single hyphens, not starting or "
                        "ending with a hyphen")
    elif any(w in name for w in RESERVED):
        problems.append(f"name {name!r} contains a word Claude does not accept in skill names (claude, anthropic)")
    if not desc.strip():
        problems.append("the front matter has no description (what the skill does and when to use it)")
    elif len(desc) > 1024:
        problems.append(f"the description is {len(desc)} characters; at most 1024")
    if re.search(r"<[A-Za-z/][^>]*>", f"{name} {desc}"):
        problems.append("name and description must not contain XML tags")
    if not body.strip():
        problems.append("SKILL.md has no instructions after the front matter")
    return meta, problems


def slug(s: str) -> str:
    """A name the format accepts, from anything."""
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")[:64].strip("-")
    return s or "skill"


def ref_of(owner: int | None, name: str, builtin: bool = False) -> str:
    return f"hub/{name}" if builtin else f"@{owner}/{name}" if owner is not None else name


class Skills:
    def __init__(self, root: Path, builtin: Path = BUILTIN):
        self.root = root
        self.builtin = builtin
        root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ whose
    @staticmethod
    def _person(who: dict | None) -> int | None:
        """The person's id, or None for the owner (and for no one in particular: the owner's view)."""
        if who and who.get("role") == "member":
            return int(who["id"])
        return None

    def _home(self, person: int | None) -> Path:
        return self.root if person is None else self.root / f"@{person}"

    def _parse(self, ref: str) -> tuple[str, int | None, str]:
        """(kind, person id, folder name); kind is "hub", "person" or "owner"."""
        m = REF_RE.match(ref or "")
        if not m:
            raise ValueError(f"not a skill: {ref!r}")
        if m.group("hub"):
            return "hub", None, m.group("name")
        if m.group("user"):
            return "person", int(m.group("user")), m.group("name")
        return "owner", None, m.group("name")

    def _folder(self, ref: str) -> tuple[Path, str, int | None, str]:
        kind, person, name = self._parse(ref)
        base = self.builtin if kind == "hub" else self._home(person)
        return base / name, kind, person, name

    def _shared_names(self, person: int | None) -> set[str]:
        """Shared with the family: a person's listed in .shared.json; the owner's all but .disabled.json's."""
        try:
            if person is None:
                off = set(json.loads((self.root / ".disabled.json").read_text()))
                return {d.name for d in self._dirs(self.root)} - off
            return set(json.loads((self._home(person) / ".shared.json").read_text()))
        except (OSError, ValueError):
            return {d.name for d in self._dirs(self.root)} if person is None else set()

    @staticmethod
    def _dirs(base: Path) -> list[Path]:
        if not base.is_dir():
            return []
        return sorted(d for d in base.iterdir() if d.is_dir() and NAME_RE.match(d.name)
                      and (d / "SKILL.md").is_file())

    # ------------------------------------------------------------ categories (0.41.0)
    def _categories(self, person: int | None) -> dict[str, str]:
        """folder name -> category, as the skills' owner set them - .categories.json in their folder of skills. One
        place per owner, so a shared skill shows in its owner's category for everyone who sees it."""
        try:
            data = json.loads((self._home(person) / ".categories.json").read_text())
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v} \
            if isinstance(data, dict) else {}

    def _save_categories(self, person: int | None, cats: dict[str, str]) -> None:
        home = self._home(person)
        home.mkdir(parents=True, exist_ok=True)
        tmp = home / f".categories.json.{secrets.token_hex(4)}.tmp"
        tmp.write_text(json.dumps(dict(sorted(cats.items())), ensure_ascii=False, indent=1))
        tmp.replace(home / ".categories.json")

    @staticmethod
    def clean_category(name: str | None) -> str:
        """A category's name as kept: spaces folded, at most CATEGORY_MAX characters; "" is none."""
        name = " ".join(str(name or "").split())
        if len(name) > CATEGORY_MAX:
            raise ValueError(f"A category's name is at most {CATEGORY_MAX} characters.")
        return name

    def set_category(self, who: dict | None, ref: str, category: str | None) -> str:
        """One of your skills into a category ("" or None: out of any). -> the category as kept."""
        d = self._own(who, ref)
        name = self.clean_category(category)
        person = self._person(who)
        cats = self._categories(person)
        if name:
            cats[d.name] = name
        else:
            cats.pop(d.name, None)
        self._save_categories(person, cats)
        return name

    def rename_category(self, who: dict | None, old: str, new: str | None) -> int:
        """Every one of your skills in category old (any case) into new ("": out of any). -> how many moved."""
        person = self._person(who)
        old_key = " ".join(str(old or "").split()).casefold()
        name = self.clean_category(new)
        if not old_key:
            raise ValueError("Which category?")
        cats = self._categories(person)
        mine = {d.name for d in self._dirs(self._home(person))}
        moved = [k for k, v in cats.items() if v.casefold() == old_key and k in mine]
        for k in moved:
            if name:
                cats[k] = name
            else:
                cats.pop(k)
        if moved:
            self._save_categories(person, cats)
        return len(moved)

    def _entry(self, d: Path, kind: str, person: int | None, shared: bool, mine: bool,
               category: str = "") -> dict:
        meta, _ = parse_front_matter((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
        files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*")
                       if p.is_file() and p.name != "SKILL.md" and not p.name.startswith("."))
        size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
        title = meta.get("name") if isinstance(meta.get("name"), str) and meta.get("name") else d.name
        desc = meta.get("description") if isinstance(meta.get("description"), str) else ""
        return {"ref": ref_of(person, d.name, kind == "hub"), "name": d.name, "title": title,
                "description": desc[:1024], "files": files[:200], "size": size, "kind": kind,
                "owner_id": person, "mine": mine, "shared": shared, "builtin": kind == "hub", "category": category,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((d / "SKILL.md").stat().st_mtime)),
                # older name for the same: shared with the family (the composer once listed "switched off" ones)
                "enabled": shared}

    def _people(self) -> list[int]:
        return sorted(int(d.name[1:]) for d in self.root.iterdir() if d.is_dir() and re.match(r"^@\d+$", d.name)) \
            if self.root.is_dir() else []

    def visible(self, who: dict | None) -> list[dict]:
        """Everything `who` may choose: their own skills, the family's shared ones and the Hub's - theirs first."""
        me = self._person(who)
        out: list[dict] = []
        homes: list[int | None] = [me] + ([None] if me is not None else []) + [p for p in self._people() if p != me]
        for person in homes:
            shared = self._shared_names(person)
            cats = self._categories(person)
            kind = "owner" if person is None else "person"
            for d in self._dirs(self._home(person)):
                mine = person == me
                if mine or d.name in shared:
                    out.append(self._entry(d, kind, person, d.name in shared, mine, cats.get(d.name, "")))
        out += [self._entry(d, "hub", None, True, False) for d in self._dirs(self.builtin)]
        return out

    def mine(self, who: dict | None) -> list[dict]:
        return [s for s in self.visible(who) if s["mine"]]

    def may_see(self, who: dict | None, ref: str) -> bool:
        try:
            d, kind, person, name = self._folder(ref)
        except ValueError:
            return False
        if not (d / "SKILL.md").is_file():
            return False
        return kind == "hub" or person == self._person(who) or name in self._shared_names(person)

    def _own(self, who: dict | None, ref: str) -> Path:
        d, kind, person, _ = self._folder(ref)
        if kind == "hub" or person != self._person(who) or not (d / "SKILL.md").is_file():
            raise PermissionError(f"{ref} is not one of your skills")
        return d

    # ------------------------------------------------------------ reading
    def entry(self, who: dict | None, ref: str) -> dict:
        if not self.may_see(who, ref):
            raise FileNotFoundError(ref)
        return next(s for s in self.visible(who) if s["ref"] == ref)

    def get(self, who: dict | None, ref: str) -> str:
        if not self.may_see(who, ref):
            raise FileNotFoundError(ref)
        return (self._folder(ref)[0] / "SKILL.md").read_text(encoding="utf-8", errors="replace")

    def path(self, who: dict | None, ref: str) -> Path:
        if not self.may_see(who, ref):
            raise FileNotFoundError(ref)
        return self._folder(ref)[0]

    def read_file(self, who: dict | None, ref: str, rel: str) -> bytes:
        d = self.path(who, ref)
        p = (d / rel).resolve()
        if d.resolve() not in p.parents or not p.is_file():
            raise FileNotFoundError(rel)
        return p.read_bytes()

    def chosen(self, refs: list[str], who: dict | None) -> list[dict]:
        """The skills of a turn: the refs chosen for its chat and project that `who` may use, with their text."""
        out, seen = [], set()
        for ref in refs:
            if not isinstance(ref, str) or ref in seen or not self.may_see(who, ref):
                continue
            seen.add(ref)
            s = self.entry(who, ref)
            _, body = parse_front_matter(self.get(who, ref))
            out.append({**s, "body": body.strip()})
        return out

    def zip(self, who: dict | None, ref: str) -> tuple[str, bytes]:
        """A .zip in the shape Claude takes: one folder, named as the skill, with SKILL.md at its top."""
        d = self.path(who, ref)
        return d.name, _zip_folder(d, d.name)

    # ------------------------------------------------------------ changing
    def install(self, who: dict | None, data: bytes, filename: str) -> str:
        """A .zip/.skill bundle (SKILL.md at its root or in one folder) or a bare SKILL.md, into `who`'s skills -
        replacing one of the same name. Not shared until they share it. Returns its ref."""
        person = self._person(who)
        if filename.lower().endswith((".zip", ".skill")) or data[:2] == b"PK":
            src, name = self._unzip(data)
        else:
            meta, _ = parse_front_matter(data.decode("utf-8", "replace"))
            name = meta.get("name") if isinstance(meta.get("name"), str) and meta.get("name") else Path(filename).stem
            src = self.root / f".import-{secrets.token_hex(6)}"
            src.mkdir(parents=True)
            (src / "SKILL.md").write_bytes(data)
        try:
            return self._place(src, slug(name), person)
        finally:
            shutil.rmtree(src, ignore_errors=True)

    def _unzip(self, data: bytes) -> tuple[Path, str]:
        if len(data) > MAX_SKILL_BYTES:
            raise ValueError("the bundle is larger than 50 MB")
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("not a zip file")
        names = [n for n in z.namelist() if not n.endswith("/")]
        if sum(i.file_size for i in z.infolist()) > 4 * MAX_SKILL_BYTES:
            raise ValueError("the bundle unpacks to more than 200 MB")
        if len(names) > MAX_FILES:
            raise ValueError(f"the bundle has more than {MAX_FILES} files")
        skill_md = sorted((n for n in names if PurePosixPath(n).name == "SKILL.md" and "__MACOSX" not in n),
                          key=lambda n: n.count("/"))
        if not skill_md:
            raise ValueError("no SKILL.md in the bundle")
        prefix = str(PurePosixPath(skill_md[0]).parent)
        prefix = "" if prefix == "." else prefix + "/"
        meta, _ = parse_front_matter(z.read(skill_md[0]).decode("utf-8", "replace"))
        name = meta.get("name") if isinstance(meta.get("name"), str) and meta.get("name") else \
            (prefix.strip("/").split("/")[-1] if prefix else "skill")
        tmp = self.root / f".import-{secrets.token_hex(6)}"
        tmp.mkdir(parents=True)
        for n in names:
            if prefix and not n.startswith(prefix):
                continue
            rel = n[len(prefix):]
            parts = PurePosixPath(rel).parts
            if not rel or rel.startswith("/") or ".." in parts or "__MACOSX" in parts:
                continue
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
        return tmp, name

    def _place(self, src: Path, name: str, person: int | None, shared: bool = False) -> str:
        """`src` becomes `person`'s skill `name` (replacing one of that name, keeping whether it was shared)."""
        home = self._home(person)
        home.mkdir(parents=True, exist_ok=True)
        d = home / name
        existed = d.is_dir()
        was_shared = existed and name in self._shared_names(person)
        tmp = home / f".{name}.placing-{secrets.token_hex(4)}"
        shutil.copytree(src, tmp, symlinks=False, ignore=shutil.ignore_patterns(".git", "__MACOSX", ".DS_Store"))
        if existed:
            shutil.rmtree(d)
        tmp.rename(d)
        self._set_shared(person, name, was_shared if existed else shared)
        return ref_of(person, name)

    def _set_shared(self, person: int | None, name: str, on: bool) -> None:
        if person is None:
            path = self.root / ".disabled.json"
            try:
                off = set(json.loads(path.read_text()))
            except (OSError, ValueError):
                off = set()
            off.discard(name) if on else off.add(name)
            path.write_text(json.dumps(sorted(off)))
        else:
            path = self._home(person) / ".shared.json"
            try:
                shared = set(json.loads(path.read_text()))
            except (OSError, ValueError):
                shared = set()
            shared.add(name) if on else shared.discard(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sorted(shared)))

    def set_shared(self, who: dict | None, ref: str, on: bool) -> None:
        d = self._own(who, ref)
        self._set_shared(self._person(who), d.name, on)

    def put_text(self, who: dict | None, ref: str, text: str) -> None:
        (self._own(who, ref) / "SKILL.md").write_text(text, encoding="utf-8")

    def delete(self, who: dict | None, ref: str) -> None:
        d = self._own(who, ref)
        shutil.rmtree(d)
        self._set_shared(self._person(who), d.name, False)
        cats = self._categories(self._person(who))
        if cats.pop(d.name, None) is not None:              # a skill made again by that name starts in none
            self._save_categories(self._person(who), cats)

    def forget_person(self, person_id: int) -> None:
        """A person removed: their skills go too (chats that chose a shared one simply lose it)."""
        shutil.rmtree(self._home(int(person_id)), ignore_errors=True)

    # ------------------------------------------------------------ drafts (made with the assistant)
    @property
    def drafts(self) -> Path:
        return self.root / ".drafts"

    def propose(self, who: dict | None, folder: Path) -> dict:
        """A skill the assistant made in the working folder, checked and kept as a draft until the user adds it.
        ValueError says what is wrong (the model fixes it and calls again)."""
        if not (folder / "SKILL.md").is_file():
            raise ValueError(f"no SKILL.md in {folder.name}/ - the skill's folder holds SKILL.md and its files")
        meta, problems = check_skill_md((folder / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
        files = [p for p in folder.rglob("*") if p.is_file() and not p.is_symlink()
                 and not any(part.startswith(".") for part in p.relative_to(folder).parts)]
        size = sum(p.stat().st_size for p in files)
        if len(files) > MAX_FILES:
            problems.append(f"{len(files)} files; at most {MAX_FILES}")
        if size > MAX_SKILL_BYTES:
            problems.append(f"{size / 1e6:.0f} MB; at most 50 MB")
        if problems:
            raise ValueError("; ".join(problems))
        self.sweep_drafts()
        name = meta["name"]
        did = secrets.token_hex(8)
        d = self.drafts / did
        (d / name).mkdir(parents=True)
        for p in files:
            target = d / name / p.relative_to(folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
        person = self._person(who)
        info = {"id": did, "person": person, "name": name, "created": time.time(), "added": None}
        (d / "draft.json").write_text(json.dumps(info))
        return self.draft(who, did)

    def _draft_info(self, who: dict | None, did: str) -> tuple[Path, dict]:
        if not re.match(r"^[0-9a-f]{16}$", did or ""):
            raise FileNotFoundError(did)
        d = self.drafts / did
        try:
            info = json.loads((d / "draft.json").read_text())
        except (OSError, ValueError):
            raise FileNotFoundError(did)
        if info.get("person") != self._person(who):
            raise FileNotFoundError(did)                     # someone else's draft is no draft of yours
        return d, info

    def draft(self, who: dict | None, did: str) -> dict:
        d, info = self._draft_info(who, did)
        folder = d / info["name"]
        text = (folder / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        meta, _ = parse_front_matter(text)
        files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")
                       if p.is_file() and p.name != "SKILL.md")
        person = info.get("person")
        exists = (self._home(person) / info["name"] / "SKILL.md").is_file()
        return {"draft": did, "name": info["name"], "title": meta.get("name") or info["name"],
                "description": (meta.get("description") or "")[:1024] if isinstance(meta.get("description"), str)
                else "", "files": files[:200], "size": sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()),
                "replaces": exists and not info.get("added"), "added": info.get("added"),
                "ref": ref_of(person, info["name"]), "text": text,
                "expires": info["created"] + DRAFT_DAYS * 86400}

    def add_draft(self, who: dict | None, did: str) -> str:
        """The card's Add skill: the draft becomes `who`'s skill (replacing one of its name)."""
        d, info = self._draft_info(who, did)
        ref = self._place(d / info["name"], info["name"], info.get("person"))
        info["added"] = time.time()
        (d / "draft.json").write_text(json.dumps(info))
        return ref

    def draft_zip(self, who: dict | None, did: str) -> tuple[str, bytes]:
        d, info = self._draft_info(who, did)
        return info["name"], _zip_folder(d / info["name"], info["name"])

    def sweep_drafts(self, now: float | None = None) -> None:
        now = now or time.time()
        if not self.drafts.is_dir():
            return
        for d in self.drafts.iterdir():
            try:
                created = json.loads((d / "draft.json").read_text())["created"]
            except (OSError, ValueError, KeyError):
                created = d.stat().st_mtime
            if now - created > DRAFT_DAYS * 86400:
                shutil.rmtree(d, ignore_errors=True)


def _zip_folder(d: Path, top: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            rel = p.relative_to(d)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts):
                z.write(p, f"{top}/{rel.as_posix()}")
    return buf.getvalue()


# ---------------------------------------------------------------- a turn's skills

def who_of(device) -> dict | None:
    """Whose skills a turn may use: a person's (their device turn), else the owner's."""
    return {"id": device.user_id, "role": "member"} if device is not None else None


def turn_skills(store: Skills, chat: dict | None, project: dict | None, device=None) -> list[dict]:
    """The skills chosen for a chat and its project that its user may use, each with its SKILL.md body and the
    folder its files get in the working folder's .hub/skills/ ("dir"; a second skill of the same name gets -2)."""
    refs = list(((chat or {}).get("settings") or {}).get("skills") or [])
    refs += list(((project or {}).get("settings") or {}).get("skills") or [])
    skills = store.chosen(refs, who_of(device))
    taken: set[str] = set()
    for s in skills:
        d, n = s["name"], 2
        while d in taken:
            d, n = f"{s['name']}-{n}", n + 1
        taken.add(d)
        s["dir"] = d
    return skills


def materialize(store: Skills, skills: list[dict], root: Path, device=None) -> None:
    """The chosen skills into <working folder>/.hub/skills/<dir>/, for code to read and run and for the skill maker
    to improve one (sync and the files card leave .hub alone); skills no longer chosen leave it."""
    want = {s["dir"]: s for s in skills}
    if not want and not (root / ".hub" / "skills").exists() and not (root / ".hub" / "skills").is_symlink():
        return
    # Never through a link: code in the sandbox may plant .hub/skills -> /data, and the cleanup below would empty
    # whatever it points at (0.27.0).
    from ..projects.store import real_dir
    base = real_dir(root, ".hub", "skills")
    for d in list(base.iterdir()):
        if d.name not in want:
            if d.is_symlink() or not d.is_dir():
                d.unlink(missing_ok=True)
            else:
                shutil.rmtree(d, ignore_errors=True)
    for dirname, s in want.items():
        src = store.path(who_of(device), s["ref"])
        dst = base / dirname
        if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
            dst.unlink()
        if _same_tree(src, dst):
            continue
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".*"))


def _same_tree(a: Path, b: Path) -> bool:
    def sig(d: Path) -> set:
        return {(p.relative_to(d).as_posix(), p.stat().st_size, int(p.stat().st_mtime))
                for p in d.rglob("*") if p.is_file() and not p.name.startswith(".")}
    return b.is_dir() and sig(a) == sig(b)
