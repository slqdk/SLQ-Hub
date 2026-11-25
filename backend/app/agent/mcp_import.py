"""MCP servers from elsewhere (0.86.0).

Søren, 2026-11-25: "Jeg kunne godt tænke mig at have nogle flere mcp servere til brug i chatten.. Jeg har kigget på
https://mcpservers.org/ men jeg ved ikke rigtigt hvad jeg skal gøre". A server's page gives its configuration for
other clients: a JSON block for Claude Desktop, Cursor, VS Code, Windsurf, Gemini CLI, Zed or Opencode, TOML for
Codex, a `claude mcp add` command, a URL, or an npx/uvx command. `read_config` turns any of those into the Hub's own
server settings (agent/mcp.py): what must be filled in (an API key where the page has a placeholder) and what cannot
work here (Docker or Go in the sandbox, the old SSE transport, a file on another computer). Nothing is run or
fetched: it only reads the text. CATALOG: servers checked against the Hub's own client, added with one click in
Settings -> MCP servers.
"""
from __future__ import annotations

import json
import re
import shlex
import tomllib
from urllib.parse import urlparse

MAX_TEXT = 200_000
CHECKED = "2026-11-25"      # when the catalog's servers last answered the Hub's client (initialize, tools/list, a call)

# Hosted servers that need nothing on the Hub's side: they answered initialize, tools/list and a call through
# mcp.HttpConnection on CHECKED. Anyone can use them without an account; a key only raises a limit.
CATALOG: list[dict] = [
    {"id": "mdn", "name": "MDN", "docs": "https://developer.mozilla.org/en-US/mcp",
     "about": "HTML, CSS, JavaScript and Web API documentation, and which browsers support what (Mozilla; "
              "experimental). For web pages and browser games.",
     "config": {"url": "https://mcp.mdn.mozilla.net/"}, "needs": []},
    {"id": "context7", "name": "Context7", "docs": "https://context7.com/docs",
     "about": "Current documentation and code examples for libraries and frameworks - three.js, React, Node.js and "
              "thousands more (Upstash).",
     "config": {"url": "https://mcp.context7.com/mcp"},
     "needs": [{"where": "header", "key": "CONTEXT7_API_KEY", "placeholder": None, "optional": True, "secret": True,
                "label": "API key", "help": "Free at context7.com/dashboard. Without one there is a small monthly "
                                            "quota, which can run out."}]},
    {"id": "microsoft-learn", "name": "Microsoft Learn", "docs": "https://learn.microsoft.com/training/support/mcp",
     "about": "Microsoft's documentation and code samples: C#, .NET, Windows Forms, WPF, PowerShell, Azure.",
     "config": {"url": "https://learn.microsoft.com/api/mcp"}, "needs": []},
    {"id": "deepwiki", "name": "DeepWiki", "docs": "https://docs.devin.ai/work-with-devin/deepwiki-mcp",
     "about": "Questions about a public GitHub repository, answered from its code and its generated wiki "
              "(Cognition).",
     "config": {"url": "https://mcp.deepwiki.com/mcp"}, "needs": []},
    {"id": "huggingface", "name": "Hugging Face", "docs": "https://huggingface.co/settings/mcp",
     "about": "Search models, datasets, Spaces and papers on Hugging Face, and read their cards.",
     "config": {"url": "https://huggingface.co/mcp", "headers": {"Authorization": "Bearer <token>"}},
     "needs": [{"where": "header", "key": "Authorization", "placeholder": "<token>", "optional": True, "secret": True,
                "label": "Access token", "help": "hf_... from huggingface.co/settings/tokens - only for higher "
                                                 "limits."}]},
]

GROUPS = ("mcpServers", "servers", "mcp_servers", "context_servers", "mcp")
SERVER_KEYS = ("url", "serverUrl", "httpUrl", "command")
CLIS = ("claude", "amp", "codex", "gemini", "qwen", "cursor-agent", "opencode")
# What the sandbox starts a server with (services/sandbox/Dockerfile): Node.js 24, Python with uv, .NET 10.
RUNS_HERE = {"npx", "npm", "node", "uvx", "uv", "python", "python3", "pip", "bash", "sh", "dotnet", "pnpm"}
# A line of a pasted page that starts a server (not one that installs something first).
STARTERS = {"npx", "uvx", "uv", "node", "python", "python3", "dotnet", "pnpm"}
INSTALLS = {"install", "i", "add", "pull", "build", "get", "init", "ci"}
NOT_HERE = {
    "docker": "It runs in Docker, which the sandbox does not have",
    "podman": "It runs in a container (podman), which the sandbox does not have",
    "go": "It is started with Go, which the sandbox does not have",
    "bun": "It is started with Bun, which the sandbox does not have",
    "bunx": "It is started with Bun, which the sandbox does not have",
    "deno": "It is started with Deno, which the sandbox does not have",
}
INSTEAD = (". Look on its page for a URL (a remote server) or a way to start it with npx or uvx - or run it on "
           "another machine in HTTP mode and give the Hub its address.")

PLACEHOLDER = re.compile(
    r"\$\{input:(?P<input>[^}\s]+)\}"                         # VS Code asks for the value
    r"|\$\{(?:env:)?(?P<var>[A-Za-z_][A-Za-z0-9_]*)\}"         # the client's environment variable
    r"|<(?P<angle>[^<>\n]{1,80})>"                             # <your personal access token>
    r"|\[(?P<bracket>(?:YOUR|[Yy]our)\b[^\]\n]{0,60})\]"      # [YOUR TOKEN]
    r"|(?P<your>\b(?:YOUR|[Yy]our)[_-][A-Za-z0-9_-]*[A-Za-z0-9])")   # YOUR_API_KEY, your-token, your_key_here


def read_config(text: str, taken: list[dict] | None = None) -> dict:
    """What a pasted configuration describes, as the Hub's server settings: {"servers": [{"config", "needs",
    "notes", "problem", "same_as"}], "error"}. `needs`: values the page leaves to the reader - {"where": "header" |
    "env" | "url", "key", "placeholder" (the text to replace, or None for the whole value), "label", "optional",
    "secret"}. `problem`: why it cannot run here. `taken`: the servers already set up - a name stays unique, and one
    that is there already is named in `same_as`."""
    text = (text or "")[:MAX_TEXT]
    if not text.strip():
        return {"servers": [], "error": "Paste the configuration from the server's page: its JSON, the command, or "
                                        "its URL."}
    for part in _parts(text):
        found = _read(part)
        if found:
            _settle(found, taken or [])
            return {"servers": found, "error": None}
    return {"servers": [], "error": "No MCP server in it. Paste the block the server's page gives for Claude Desktop, "
                                    "Cursor or VS Code (JSON with mcpServers or servers), a `claude mcp add` "
                                    "command, the server's URL, or the npx or uvx command that starts it."}


def catalog(taken: list[dict]) -> list[dict]:
    """CATALOG for Settings, each as read_config gives a server (`found`), with the name it is set up under."""
    out = []
    for entry in CATALOG:
        cfg = _base(entry["name"], "http", **entry["config"])
        found = {"config": cfg, "needs": [dict(n) for n in entry["needs"]], "notes": [], "problem": None,
                 "same_as": _same(cfg, taken)}
        out.append({k: entry[k] for k in ("id", "name", "about", "docs")} | {"checked": CHECKED,
                                                                              "added": found["same_as"],
                                                                              "found": found})
    return out


# ---------------------------------------------------------------- reading

def _parts(text: str) -> list[str]:
    """The fenced code blocks of a pasted page, then the whole text."""
    blocks = [m.group(1) for m in re.finditer(r"```[^\n`]*\n(.*?)```", text, re.S)]
    return [b for b in blocks if b.strip()] + [text]


def _read(text: str) -> list[dict]:
    data = _structured(text)
    if data is not None:
        inputs = _inputs(data)
        return [f for f in (_from_spec(n, s, inputs) for n, s in _servers_in(data)) if f]
    return [f for f in (_from_line(line) for line in _lines(text)) if f]


def _structured(text: str):
    s = text.strip()
    for attempt in (lambda: json.loads(s), lambda: json.loads(_loose(s))):
        try:
            data = attempt()
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    if re.search(r"(?m)^\s*\[[A-Za-z0-9_.\"-]+\]\s*$", s):       # TOML: Codex's [mcp_servers.<name>]
        try:
            return tomllib.loads(s)
        except tomllib.TOMLDecodeError:
            return None
    return None


def _loose(text: str) -> str:
    """JSON as pages print it: // and /* */ comments, trailing commas, a fragment ("mcpServers": {...}) without
    the braces around it."""
    out, i, n, quoted = [], 0, len(text), False
    while i < n:
        c = text[i]
        if quoted:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    s = re.sub(r",(\s*[}\]])", r"\1", "".join(out)).strip().rstrip(",").strip()
    return "{" + s + "}" if s.startswith('"') else s


def _is_server(d) -> bool:
    return isinstance(d, dict) and any(k in d for k in SERVER_KEYS)


def _servers_in(data: dict, depth: int = 0) -> list[tuple[str, dict]]:
    if depth > 5 or not isinstance(data, dict):
        return []
    if _is_server(data):
        return [("", data)]
    for key in GROUPS:
        group = data.get(key)
        if isinstance(group, dict):
            found = _servers_in(group, depth + 1)
            if found:
                return found
    named = [(str(k), v) for k, v in data.items() if _is_server(v)]
    if named:
        return named
    for value in data.values():
        found = _servers_in(value, depth + 1) if isinstance(value, dict) else []
        if found:
            return found
    return []


def _inputs(data: dict) -> dict[str, str]:
    """VS Code's "inputs": what each ${input:<id>} asks for."""
    out: dict[str, str] = {}
    for holder in (data, data.get("mcp") if isinstance(data.get("mcp"), dict) else {}):
        for item in holder.get("inputs") or [] if isinstance(holder, dict) else []:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = str(item.get("description") or item["id"])
    return out


def _from_spec(name: str, spec: dict, inputs: dict[str, str]) -> dict | None:
    url = spec.get("url") or spec.get("serverUrl") or spec.get("httpUrl")
    kind = str(spec.get("type") or spec.get("transport") or "").lower()
    headers = spec.get("headers") or spec.get("http_headers") or (spec.get("requestInit") or {}).get("headers") or {}
    command, args = spec.get("command"), spec.get("args") or []
    env = spec.get("env") or spec.get("environment") or {}
    if isinstance(command, dict):                     # Zed: {"path", "args", "env"}
        args, env = command.get("args") or args, command.get("env") or env
        command = command.get("path") or command.get("command")
    if isinstance(command, list):                     # Opencode: ["npx", "-y", "..."]
        command, args = (command[0] if command else ""), [*command[1:], *args]
    if not isinstance(headers, dict) or not isinstance(env, dict) or not isinstance(args, list):
        return None
    headers = {str(k): str(v) for k, v in headers.items() if str(k).lower() not in ("accept", "content-type")}
    env = {str(k): str(v) for k, v in env.items()}
    if isinstance(url, str) and url.strip():
        return _http(name, url.strip(), headers, kind, inputs)
    if isinstance(command, str) and command.strip():
        return _stdio(name, command.strip(), [str(a) for a in args], env, inputs)
    return None


def _lines(text: str) -> list[str]:
    joined = re.sub(r"\\\r?\n", " ", text)            # a command over several lines
    return [ln.strip().removeprefix("$ ").strip() for ln in joined.splitlines() if ln.strip()]


def _from_line(line: str) -> dict | None:
    if re.fullmatch(r"https?://\S+", line):
        return _http("", line, {}, "", {})
    try:
        words = shlex.split(line)
    except ValueError:
        return None
    if not words:
        return None
    if words[0] in CLIS and words[1:3] == ["mcp", "add"]:
        return _cli_add(words[3:])
    if words[0] in CLIS and words[1:3] == ["mcp", "add-json"]:        # claude mcp add-json <name> '<json>'
        rest = [w for w in words[3:] if not w.startswith("-")]
        try:
            spec = json.loads(rest[1]) if len(rest) > 1 else None
        except ValueError:
            return None
        return _from_spec(rest[0], spec, {}) if _is_server(spec) else None
    if (_program(words[0]) in STARTERS | set(NOT_HERE) and len(words) > 1
            and not INSTALLS & {w.lower() for w in words[1:3]}):
        return _stdio("", words[0], words[1:], {}, {})
    return None


def _cli_add(words: list[str]) -> dict | None:
    """`claude mcp add [options] <name> <url | -- command...>` - and amp's, codex's, gemini's, which are alike."""
    kind, headers, env, rest, command = "", {}, {}, [], []
    i = 0
    while i < len(words):
        w = words[i]
        if w == "--":
            command = words[i + 1:]
            break
        key, _, inline = w.partition("=") if w.startswith("--") else (w, "", "")
        value = inline or (words[i + 1] if i + 1 < len(words) else "")
        step = 1 if inline else 2
        if key in ("--transport", "-t"):
            kind = value.lower()
        elif key in ("--header", "-H"):
            k, sep, v = value.partition(":") if ":" in value else value.partition("=")
            if sep:
                headers[k.strip()] = v.strip()
        elif key in ("--env", "-e"):
            k, sep, v = value.partition("=")
            if sep:
                env[k.strip()] = v
        elif key in ("--scope", "-s", "--timeout", "--client-id", "--client-secret", "--callback-port"):
            pass
        elif w.startswith("-"):
            step = 1
        else:
            rest.append(w)
            step = 1
        i += step
    name = rest[0] if rest else ""
    target = next((r for r in rest[1:] if r.startswith(("http://", "https://"))), None)
    if target and not command:
        return _http(name, target, headers, kind, {})
    if command:
        return _stdio(name, command[0], command[1:], env, {})
    if len(rest) > 1:                                  # `claude mcp add name npx -y pkg`, no --
        return _stdio(name, rest[1], rest[2:], env, {})
    return None


# ---------------------------------------------------------------- one server

def _base(name: str, transport: str, **more) -> dict:
    cfg = {"name": name, "transport": transport, "url": "", "headers": {}, "command": "", "env": {},
           "enabled": True, "trusted": False}
    cfg.update(more)
    return cfg


def _http(name: str, url: str, headers: dict[str, str], kind: str, inputs: dict[str, str]) -> dict:
    notes: list[str] = []
    parts = urlparse(url)
    if parts.path.rstrip("/").endswith("/sse"):
        url = url.replace(parts.path, parts.path.rstrip("/")[:-4] + "/mcp", 1)
        notes.append("Its page gives /sse, the old SSE transport, which the Hub does not speak: changed to /mcp, which "
                     "most such servers have too. If it does not connect, the server offers only the old one.")
    elif kind == "sse":
        notes.append("Its page says SSE, the old transport, which the Hub does not speak. If it does not connect, the "
                     "server offers only that one.")
    if (parts.hostname or "") in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        notes.append("localhost here is the Hub's own container: give the address of the machine the server runs on.")
    needs: list[dict] = []
    host = parts.hostname or ""
    for m in PLACEHOLDER.finditer(url):
        need = _need("url", "", m, inputs)
        if host and m.group(0).lower() in host:        # https://your-forgejo-instance.org: the whole host
            start = url.lower().find(host)
            need |= {"placeholder": url[start:start + len(host)], "label": need["label"] + " (its host name)"}
        needs.append(need)
    for key, value in headers.items():
        needs += [_need("header", key, m, inputs) for m in PLACEHOLDER.finditer(value)]
    cfg = _base(name or _name_from_url(url), "http", url=url, headers=headers)
    return {"config": cfg, "needs": _dedupe(needs), "notes": notes, "problem": None, "same_as": None}


def _stdio(name: str, command: str, args: list[str], env: dict[str, str], inputs: dict[str, str]) -> dict:
    program = _program(command)
    words = [command, *args]
    bridge = next((i for i, w in enumerate(words[:4]) if _package(w) == "mcp-remote"), None)
    if bridge is not None:                             # a bridge for clients that cannot reach a URL: the Hub can
        return _bridged(name, words[bridge + 1:], env, inputs)
    notes: list[str] = []
    problem = NOT_HERE[program] + INSTEAD if program in NOT_HERE else None
    if problem is None and program not in RUNS_HERE:
        notes.append(f"The sandbox must have {program}: it has Node.js 24 (npx), Python with uv (uvx) and .NET 10.")
    for w in args:
        if re.match(r"^(?:/Users/|/home/|~/|[A-Za-z]:\\|/path/to/|/mnt/|/Volumes/)", w):
            notes.append(f"{w} is a path on another computer: the server runs in the sandbox, which sees the "
                         "chat's working folder (/workspace).")
            break
    needs: list[dict] = []
    for key, value in list(env.items()):
        needs += [_need("env", key, m, inputs) for m in PLACEHOLDER.finditer(value)]
    quoted = []
    for i, w in enumerate(words):
        refs = list(PLACEHOLDER.finditer(w))
        if not refs or i == 0:
            quoted.append(shlex.quote(w))
            continue
        # A key in the command line would show in Settings: it goes into the environment, and the line names it.
        out, last = [], 0
        for m in refs:
            var = m.group("var")
            if not (var and var in env):              # ${VAR} the config sets itself stays as it is
                var = _env_name(m, words[i - 1] if i else "", inputs)
                if var not in env:
                    needs.append({**_need("env", var, m, inputs), "placeholder": None})
            out.append(_dq(w[last:m.start()]) + "${" + var + "}")
            last = m.end()
        quoted.append('"' + "".join(out) + _dq(w[last:]) + '"')
    cfg = _base(name or _name_from_command(words), "stdio", command=" ".join(quoted), env=env)
    return {"config": cfg, "needs": _dedupe(needs), "notes": notes, "problem": problem, "same_as": None}


def _bridged(name: str, words: list[str], env: dict[str, str], inputs: dict[str, str]) -> dict | None:
    """npx mcp-remote <url> [--header "K: V"]...: the URL itself, the headers with the config's env filled in."""
    url = next((w for w in words if w.startswith(("http://", "https://"))), None)
    if url is None:
        return None
    headers: dict[str, str] = {}
    for i, w in enumerate(words):
        if w == "--header" and i + 1 < len(words):
            k, sep, v = words[i + 1].partition(":")
            if sep:
                v = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: env.get(m.group(1), m.group(0)), v.strip())
                headers[k.strip()] = v
    found = _http(name, url, headers, "", inputs)
    found["notes"].insert(0, "mcp-remote is a bridge for clients that cannot reach a remote server themselves: the "
                             "Hub connects to the URL directly.")
    return found


# ---------------------------------------------------------------- names and values

def _need(where: str, key: str, m: re.Match, inputs: dict[str, str]) -> dict:
    label = _label(m, inputs, key)
    secret = where == "header" or bool(re.search(r"token|key|secret|pass|auth|pat\b|cred", f"{key} {label}", re.I))
    return {"where": where, "key": key, "placeholder": m.group(0), "label": label, "optional": False,
            "secret": secret and where != "url"}


def _label(m: re.Match, inputs: dict[str, str], key: str = "") -> str:
    if m.group("input"):
        return inputs.get(m.group("input")) or _words(m.group("input"))
    if m.group("var"):
        return m.group("var")
    raw = m.group("angle") or m.group("bracket") or m.group("your") or key
    text = _words(re.sub(r"^(?:YOUR|your|Your)[\s_-]*", "", raw)) or _words(raw)
    return text[:1].upper() + text[1:] if text else key


ACRONYMS = {"api", "pat", "url", "id", "ssh", "mcp", "hf", "ai", "oauth", "jwt", "ip", "dsn"}


def _words(s: str) -> str:
    """YOUR_API_KEY's rest as words: API_KEY -> "API key"."""
    words = [w for w in re.split(r"[\s_-]+", s.strip()) if w]
    if words and all(w.isupper() or w.isdigit() for w in words):
        words = [w.upper() if w.lower() in ACRONYMS else w.lower() for w in words]
    return " ".join(words)


def _env_name(m: re.Match, before: str, inputs: dict[str, str]) -> str:
    """An environment variable for a value the command line needs: --api-key YOUR_API_KEY -> API_KEY."""
    base = m.group("var") or m.group("input") or ""
    if not base and before.startswith("--") and "=" not in before:
        base = before[2:]
    base = base or re.sub(r"^(?:YOUR|your|Your)[\s_-]*", "", m.group("angle") or m.group("bracket")
                                                              or m.group("your") or "value")
    name = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper() or "VALUE"
    return name if not name[0].isdigit() else "MCP_" + name


def _dq(s: str) -> str:
    return re.sub(r'([\\"$`])', r"\\\1", s)


def _dedupe(needs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for n in needs:
        k = (n["where"], n["key"], n["placeholder"])
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _program(word: str) -> str:
    return re.split(r"[\\/]", word)[-1].lower().removesuffix(".exe").removesuffix(".cmd")


def _package(word: str) -> str:
    """npm's name without scope and version: @upstash/context7-mcp@latest -> context7-mcp."""
    w = word.split("/")[-1] if word.startswith("@") else word
    return w.split("@")[0] if not w.startswith("@") else w


def _name_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "server").lower()
    labels = [x for x in host.split(".") if x not in ("www", "mcp", "api", "server")]
    if len(labels) > 1:
        labels = labels[:-1]                           # the top-level domain
    return "-".join(labels) or host


def _name_from_command(words: list[str]) -> str:
    program = _program(words[0])
    pkg = next((w for w in words[1:] if not w.startswith("-") and w not in ("run", "x", "dlx", "exec", "tool")),
               program) if program in ("npx", "uvx", "uv", "pnpm", "bunx", "docker", "go") else program
    name = _package(pkg).split("/")[-1].split(":")[0]
    name = re.sub(r"^(?:mcp-server-|server-|mcp-)|(?:-mcp-server|-mcp|-server)$", "", name) or name
    return name[:40] or "server"


def _key(cfg: dict) -> str:
    if cfg.get("transport") == "stdio":
        return " ".join(str(cfg.get("command") or "").split())
    return str(cfg.get("url") or "").rstrip("/").lower()


def _same(cfg: dict, taken: list[dict]) -> str | None:
    k = _key(cfg)
    return next((str(t.get("name")) for t in taken if isinstance(t, dict) and _key(t) == k and k), None)


def _settle(found: list[dict], taken: list[dict]) -> None:
    """Names unique among the servers set up and those found; one already set up is named."""
    names = {str(t.get("name") or "").lower() for t in taken if isinstance(t, dict)}
    for f in found:
        cfg = f["config"]
        f["same_as"] = _same(cfg, taken)
        base = re.sub(r"\s+", " ", str(cfg["name"] or "server")).strip()[:40] or "server"
        name, n = base, 2
        while name.lower() in names:
            name, n = f"{base} {n}", n + 1
        cfg["name"] = name
        names.add(name.lower())
