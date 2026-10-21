"""Memory: plain Markdown files the agent keeps across chats.

    /memories/...            global (facts about the user, preferences)
    /memories/project/...    the current project's own memory

On disk: <data>/memory/global/ and <data>/memory/projects/<id>/. The prompt
lists the files with their first line; the model reads and edits them with
the `memory` tool, and the UI shows them under Settings -> Memory."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

MAX_FILE = 64 * 1024
MAX_FILES = 200


class Memory:
    def __init__(self, root: Path):
        self.root = root
        (root / "global").mkdir(parents=True, exist_ok=True)

    def roots(self, project_id: int | None) -> dict[str, Path]:
        r = {"/memories": self.root / "global"}
        if project_id is not None:
            p = self.root / "projects" / str(project_id)
            p.mkdir(parents=True, exist_ok=True)
            r["/memories/project"] = p
        return r

    def resolve(self, path: str, project_id: int | None) -> Path:
        path = "/" + (path or "/memories").strip().lstrip("/")
        roots = self.roots(project_id)
        for prefix in sorted(roots, key=len, reverse=True):
            if path == prefix or path.startswith(prefix + "/"):
                rel = path[len(prefix):].lstrip("/")
                if ".." in PurePosixPath(rel).parts:
                    raise ValueError("'..' is not allowed in memory paths")
                return roots[prefix] / rel if rel else roots[prefix]
        if path.startswith("/memories/project"):
            raise ValueError("project memory exists only in a project chat")
        raise ValueError("memory paths start with /memories")

    def index(self, project_id: int | None, limit: int = 60) -> list[tuple[str, str]]:
        out = []
        for prefix, root in self.roots(project_id).items():
            for f in sorted(root.rglob("*.md")):
                if prefix == "/memories" and (self.root / "projects") in f.parents:
                    continue
                first = ""
                try:
                    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.strip():
                            first = line.strip().lstrip("#").strip()[:120]
                            break
                except OSError:
                    pass
                out.append((f"{prefix}/{f.relative_to(root).as_posix()}", first))
                if len(out) >= limit:
                    return out
        return out

    def view(self, path: str, project_id: int | None) -> str:
        p = self.resolve(path, project_id)
        if p.is_dir():
            files = [f for f in sorted(p.rglob("*")) if f.is_file()]
            if not files:
                return f"{path} is empty."
            return f"Files in {path}:\n" + "\n".join(f"- {path.rstrip('/')}/{f.relative_to(p).as_posix()} "
                                                       f"({f.stat().st_size} bytes)" for f in files)
        if not p.is_file():
            raise FileNotFoundError(path)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(f"{i:4d}\t{l}" for i, l in enumerate(lines, 1)) or "(empty file)"

    def create(self, path: str, text: str, project_id: int | None) -> str:
        p = self.resolve(path, project_id)
        if p.suffix != ".md":
            raise ValueError("memory files are Markdown (.md)")
        if len(text.encode()) > MAX_FILE:
            raise ValueError("a memory file may hold at most 64 KB - split or condense it")
        if not p.exists() and sum(1 for _ in self.root.rglob("*.md")) >= MAX_FILES:
            raise ValueError("there are already 200 memory files - condense before adding more")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return f"Saved {path}."

    def str_replace(self, path: str, old: str, new: str, project_id: int | None) -> str:
        p = self.resolve(path, project_id)
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            raise ValueError(f"old_str occurs {n} times in {path} (must be exactly once)")
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Updated {path}."

    def insert(self, path: str, line: int, text: str, project_id: int | None) -> str:
        p = self.resolve(path, project_id)
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        line = max(0, min(int(line), len(lines)))
        lines[line:line] = text.splitlines()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return f"Inserted into {path} after line {line}."

    def delete(self, path: str, project_id: int | None) -> str:
        p = self.resolve(path, project_id)
        if p.is_dir():
            raise ValueError("delete files one at a time")
        p.unlink()
        return f"Deleted {path}."

    def rename(self, old: str, new: str, project_id: int | None) -> str:
        a, b = self.resolve(old, project_id), self.resolve(new, project_id)
        b.parent.mkdir(parents=True, exist_ok=True)
        a.rename(b)
        return f"Renamed {old} to {new}."
