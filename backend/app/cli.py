"""Command line inside the hub container.

    python -m app.cli selftest [--images]   check every service, load the model, answer once
    python -m app.cli set-password [USER]   new password (asked, or one line on stdin)
    python -m app.cli create-key NAME [--priority normal|interactive|background]
    python -m app.cli keys                  list API keys

On the VM:  sudo bash /opt/slq-hub/update.sh compose exec hub python -m app.cli selftest

The self-test talks to the running Hub over its own API for anything that
uses the GPU, so it waits in the same queue as everything else.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

import httpx

from . import auth, db
from .catalog import Catalog
from .config import load_env

GREEN, RED, YELLOW, DIM, END = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = END = ""

ISOLATION_PROBE = r"""
import json, socket, sys
out = {}
for host, port in json.loads(sys.argv[1]):
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, port)); out[f"{host}:{port}"] = "open"
    except socket.timeout:
        out[f"{host}:{port}"] = "timeout"
    except ConnectionRefusedError:
        out[f"{host}:{port}"] = "refused"
    except OSError as e:
        out[f"{host}:{port}"] = "unreachable" if e.errno in (101, 113) else f"error {e.errno}"
    finally:
        s.close()
print(json.dumps(out))
"""


class Report:
    def __init__(self):
        self.failed = 0
        self.warned = 0

    def line(self, state: str, name: str, detail: str = "", hint: str = "") -> None:
        colour = {"PASS": GREEN, "FAIL": RED, "WARN": YELLOW, "SKIP": DIM}[state]
        print(f"  {colour}{state:4}{END}  {name:22} {detail}")
        if hint and state in ("FAIL", "WARN"):
            print(f"        {DIM}-> {hint}{END}")
        self.failed += state == "FAIL"
        self.warned += state == "WARN"


def _sandbox_exec(c: httpx.Client, env, cmd: str, timeout: int = 60) -> dict:
    r = c.post(env.sandbox_url + "/exec", headers={"X-Internal-Token": env.internal_token},
               json={"cmd": cmd, "cwd": "/workspace", "timeout": timeout}, timeout=timeout + 15)
    r.raise_for_status()
    return r.json()


def selftest(images: bool = False) -> int:
    env = load_env()
    rep = Report()
    print(f"SLQ Hub {env.version} self-test\n")
    hub_url = f"http://127.0.0.1:{os.environ.get('HUB_LISTEN_PORT', '8080')}"
    c = httpx.Client(timeout=20, trust_env=False)
    internal = {"X-Internal-Token": env.internal_token}

    # ---- the database
    try:
        db.configure(env.db_path)
        with db.connect() as con:
            users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        rep.line("PASS", "database", f"{env.db_path} ({users} user)")
    except Exception as exc:  # noqa: BLE001
        rep.line("FAIL", "database", str(exc))
        return 1

    # ---- the Hub itself
    try:
        h = c.get(hub_url + "/hub/api/health").json()
        rep.line("PASS", "hub", f"version {h.get('version')}")
    except Exception as exc:  # noqa: BLE001
        rep.line("FAIL", "hub", f"no answer on {hub_url} ({exc.__class__.__name__})",
                 "docker compose logs hub")

    # ---- the GPU, through the llm container's supervisor
    try:
        g = c.get(env.llm_url + "/gpu", headers=internal).json()
        total = g.get("vram_total") or 0
        if total > 8 * 1024**3:
            rep.line("PASS", "gpu", f"{total / 1024**3:.1f} GB VRAM, {(g.get('vram_used') or 0) / 1024**3:.1f} GB used")
        else:
            rep.line("FAIL", "gpu", f"the llm container sees no usable GPU ({g})",
                     "check /dev/kfd and /dev/dri in the VM, RENDER_GID in .env, and amdgpu (docs/INSTALL.md)")
    except Exception as exc:  # noqa: BLE001
        rep.line("FAIL", "gpu", f"the llm supervisor does not answer ({exc.__class__.__name__})",
                 "docker compose logs llm")

    # ---- models on disk
    cat = Catalog(env.models_dir, env.data_dir / "models.override.json")
    missing = [e["id"] for g in ("llm", "aux", "comfy") for e in cat.entries(g)
               if e.get("default") and not cat.installed(e)]
    if missing:
        rep.line("WARN", "models", "not downloaded: " + ", ".join(missing),
                 "Settings -> Models in the UI, or: python3 tools/models.py pull --defaults")
    else:
        rep.line("PASS", "models", "all default models are downloaded")

    # ---- the sandbox and its isolation
    try:
        hz = c.get(env.sandbox_url + "/health").json()
        res = _sandbox_exec(c, env, "python3 --version && node --version && tesseract --version 2>&1 | head -1")
        tools = " / ".join(x.strip() for x in res["stdout"].splitlines()[:3])
        if hz.get("uid") == 0:
            rep.line("FAIL", "sandbox", "runs as root", "the sandbox image must run as uid 1000")
        else:
            rep.line("PASS", "sandbox", f"uid {hz.get('uid')}: {tools}")
        targets = [("172.31.250.10", 8080)] + [(ip, 81) for ip in env.trusted_proxies]
        targets += [("1.1.1.1", 443)]
        probe = _sandbox_exec(c, env, "python3 -c " + _sh(ISOLATION_PROBE) + " " + _sh(json.dumps(targets)))
        seen = json.loads(probe["stdout"].strip().splitlines()[-1])
        leaks = [k for k, v in seen.items() if not k.startswith("1.1.1.1") and v in ("open", "refused")]
        if leaks:
            rep.line("FAIL", "sandbox isolation", "the sandbox reaches " + ", ".join(leaks),
                     "systemctl status slq-hub-firewall; sudo bash deploy/firewall/slq-hub-firewall.sh")
        else:
            rep.line("PASS", "sandbox isolation", "no access to the Hub or the LAN")
        if seen.get("1.1.1.1:443") == "open":
            rep.line("PASS", "sandbox internet", "can reach the internet (pip, npm, web)")
        else:
            rep.line("WARN", "sandbox internet", f"1.1.1.1:443 is {seen.get('1.1.1.1:443')}",
                     "the agent cannot install packages; check the VM's outbound network")
    except Exception as exc:  # noqa: BLE001
        rep.line("FAIL", "sandbox", f"{exc.__class__.__name__}: {exc}", "docker compose logs sandbox")

    # ---- web search
    try:
        r = c.get(env.searxng_url + "/search", params={"q": "open source", "format": "json"}, timeout=30)
        n = len(r.json().get("results") or [])
        rep.line("PASS" if n else "WARN", "web search", f"{n} results",
                 "SearXNG answered without results; its engines may be rate-limited - try again later")
    except Exception as exc:  # noqa: BLE001
        rep.line("FAIL", "web search", f"SearXNG does not answer ({exc.__class__.__name__})",
                 "docker compose logs searxng")

    # ---- ComfyUI
    try:
        st = c.get(env.comfy_url + "/system_stats").json()
        dev = (st.get("devices") or [{}])[0]
        rep.line("PASS", "comfyui", f"{dev.get('name', '?')}, torch {st.get('system', {}).get('pytorch_version')}")
    except Exception as exc:  # noqa: BLE001
        rep.line("WARN", "comfyui", f"not answering ({exc.__class__.__name__}) - image tools are off",
                 "docker compose logs comfyui")

    # ---- the model, through the Hub's own API (and its queue)
    key = auth.create_api_key(f"selftest-{os.getpid()}-{int(time.time())}", "interactive", "")
    api = {"Authorization": f"Bearer {key['key']}"}
    try:
        models = cat.llm_models()
        default = json.loads((env.data_dir / "settings.json").read_text()).get("models", {}).get("default") \
            if (env.data_dir / "settings.json").is_file() else None
        default = default or next((m["id"] for m in models if m.get("default")), None)

        def resident() -> str | None:        # the model on the card now (the Hub preloads the default)
            try:
                ms = c.get(hub_url + "/api/ps", headers=api, timeout=30).json().get("models") or []
                return ms[0].get("model") if ms else None
            except Exception:  # noqa: BLE001
                return None

        def vram_used() -> float | None:
            try:
                return (c.get(env.llm_url + "/gpu", headers=internal, timeout=10).json().get("vram_used") or 0) / 1024**3
            except Exception:  # noqa: BLE001
                return None

        was_loaded = default is not None and resident() == default
        t0 = time.monotonic()
        r = c.post(hub_url + "/v1/chat/completions", headers=api, timeout=900, json={
            "model": default, "max_tokens": 64, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}]})
        secs = time.monotonic() - t0
        if r.status_code != 200:
            rep.line("FAIL", "chat model", f"{default}: HTTP {r.status_code} {r.text[:200]}",
                     "docker compose logs llm; Settings -> System -> logs")
        else:
            body = r.json()
            text = (body["choices"][0]["message"].get("content") or "").strip()
            tps = (body.get("timings") or {}).get("predicted_per_second")
            detail = (f"{default} answered {text[:20]!r} in {secs:.1f} s "
                      f"({'already loaded' if was_loaded else 'incl. loading'})")
            if tps:
                detail += f", {tps:.0f} tokens/s"
            used = vram_used()
            if used:
                detail += f"; {used:.1f} GB VRAM in use"
            rep.line("PASS" if "OK" in text.upper() else "WARN", "chat model", detail)
        r = c.post(hub_url + "/v1/embeddings", headers=api, timeout=300, json={"model": "embed-text", "input": "hello"})
        if r.status_code == 200:
            rep.line("PASS", "embeddings", f"{len(r.json()['data'][0]['embedding'])} dimensions")
        else:
            rep.line("WARN", "embeddings", f"HTTP {r.status_code} {r.text[:150]} - libraries will not index")
        if images:
            t0 = time.monotonic()
            r = c.post(hub_url + "/v1/images/generations", headers=api, timeout=900, json={
                "prompt": "a red apple on a wooden table, photo", "size": "512x512", "quality": "low",
                "response_format": "b64_json"})
            if r.status_code == 200 and r.json().get("data"):
                rep.line("PASS", "image generation", f"{time.monotonic() - t0:.1f} s (incl. switching the card)")
            else:
                rep.line("FAIL", "image generation", f"HTTP {r.status_code} {r.text[:200]}",
                         "docker compose logs comfyui")
        else:
            rep.line("SKIP", "image generation", "run with --images to try one")
    except httpx.HTTPError as exc:
        rep.line("FAIL", "chat model", f"{exc.__class__.__name__}: {exc}")
    finally:
        auth.delete_api_key(key["id"])

    print()
    if rep.failed:
        print(f"{RED}{rep.failed} check(s) failed{END}, {rep.warned} warning(s).")
        return 1
    print(f"{GREEN}All checks passed{END}" + (f", {rep.warned} warning(s)." if rep.warned else "."))
    return 0


def _sh(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def set_password(user: str | None) -> int:
    env = load_env()
    db.configure(env.db_path)
    user = user or env.admin_user
    if sys.stdin.isatty():
        pw = getpass.getpass(f"New password for {user}: ")
        if pw != getpass.getpass("Again: "):
            print("The passwords differ.")
            return 1
    else:
        pw = sys.stdin.readline().rstrip("\n")
    if len(pw) < 10:
        print("Use at least 10 characters.")
        return 1
    if not auth.set_password(user, pw):
        print(f"There is no user {user!r}.")
        return 1
    print(f"Password changed for {user}; all sessions are signed out.")
    return 0


def create_key(name: str, priority: str) -> int:
    env = load_env()
    db.configure(env.db_path)
    k = auth.create_api_key(name, priority, "")
    print(k["key"])
    print(f"(an API key for {name}, priority {priority} - it is shown only now)", file=sys.stderr)
    return 0


def list_keys() -> int:
    env = load_env()
    db.configure(env.db_path)
    for k in auth.list_api_keys():
        print(f"{k['id']:4}  {k['name']:24} {k['priority']:12} {k.get('prefix', '')}  last used {k.get('last_used') or '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description="SLQ Hub command line")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("selftest", help="check every service and answer one question")
    st.add_argument("--images", action="store_true", help="also generate one image")
    sp = sub.add_parser("set-password", help="set a user's password")
    sp.add_argument("user", nargs="?")
    ck = sub.add_parser("create-key", help="create an API key for an app")
    ck.add_argument("name")
    ck.add_argument("--priority", default="normal", choices=["interactive", "normal", "background"])
    sub.add_parser("keys", help="list API keys")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        return selftest(a.images)
    if a.cmd == "set-password":
        return set_password(a.user)
    if a.cmd == "create-key":
        return create_key(a.name, a.priority)
    return list_keys()


if __name__ == "__main__":
    sys.exit(main())
