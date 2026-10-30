#!/usr/bin/env python3
"""Update SLQ Hub on its VM, with a backup and a way back.

Ported from SLQ Companion's tools/slq_container.py (2.66.0), for several
services. Run on the VM as root, from the clone (normally through update.sh):

    sudo bash update.sh                  pull, build, wait until nobody uses the Hub, back up, restart, check
    sudo bash update.sh --yes            the same without the question (cron)
    sudo bash update.sh --now            do not wait: stop the Hub right after the build
    sudo bash update.sh --force          rebuild and restart even with nothing new
    sudo bash update.sh --here           wait and install in this window, not in the background
    sudo bash update.sh cancel           stop an update that builds or waits
    sudo bash update.sh status           version, containers, health, firewall, backups
    sudo bash update.sh backups          list the backups
    sudo bash update.sh rollback [name]  back to a backup (code + images + data)
    sudo bash update.sh firewall         install this version's firewall and apply it

`status`, `firewall` and an update try each model container's way out (a
connection to 1.1.1.1:443 from inside it): with the firewall's rules, none.
    sudo bash update.sh compose <args>   docker compose with the right files

What `update` does, in an order that keeps the running Hub safe as long as
possible:

  1. Checks the clone is clean and docker compose works. Fetches; nothing
     new -> nothing happens.
  2. Shows the new commits and version, and asks.
  3. Tags the running images `:rollback`, fast-forwards, and BUILDS the new
     images while the old ones keep serving. A failed build puts the code
     back - nothing was stopped. When the fast-forward changed the updater
     itself, the new updater does the rest (from the build on): what it
     changed applies to this update, not only the next one.
  4. Waits until nobody uses the Hub (0.26.0): it asks the running Hub
     (/hub/api/internal/busy, with the internal token) whether a chat, a
     person's chat, a picture, a video, a test run, a library or a download is
     going, and goes on once the Hub has had nothing to do for --quiet seconds
     (60). Started in a terminal, the waiting and installing run in a session
     of their own - right after the build, or before it when the new updater
     took over - and the terminal shows their log (/srv/slq-hub/update.log):
     Ctrl+C or closing the window does not stop them. `update.sh cancel`
     stops an update that still builds or waits, and so does anything that
     goes wrong before the install: the code and the images' tags go back. A
     Hub older than 0.26.0 cannot say: the card's own load (gpu_busy_percent)
     decides. --now skips the wait; --here keeps it all in the window.
  5. Stops everything and backs up /srv/slq-hub/data (not the models, which
     can be downloaded again) to <backups>/<time>_<version>.tar.gz + .json.
  6. Starts the new version and waits until /hub/api/health answers.
  7. Healthy -> the VM's firewall script is brought up to this version and
     applied, and old backups beyond --keep are deleted. Not healthy within
     --timeout -> the log is shown and everything is put back: old code,
     old images, and the data from step 5.

Settings live in /srv/slq-hub/.env; compose changes in
/srv/slq-hub/docker-compose.override.yml - the clone stays clean, so
updates are fast-forwards. Standard library only.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB_ROOT = Path(os.environ.get("HUB_ROOT", "/srv/slq-hub"))
ENV_FILE = HUB_ROOT / ".env"
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
OVERRIDE = HUB_ROOT / "docker-compose.override.yml"
PROJECT = "slq-hub"
BUILT = ("hub", "llm", "comfyui", "sandbox", "speech", "trainer")  # services with their own image (trainer: optional)
# Folders the services mount, made (owned by uid 1000, the containers' user) before they start: Docker would
# create a missing one as root, and the Hub could not download into it.
FOLDERS = ("data", "models/llm", "models/comfy", "models/speech", "comfy/input", "comfy/output", "comfy/user",
           "windows/storage", "windows/oem", "windows/shared", "data/training", "models/hf")
KEEP = 3
TIMEOUT = 300
POLL = float(os.environ.get("HUB_UPDATE_POLL", "3"))
QUIET = 60                # seconds the Hub must have had nothing to do before an update stops it (0.26.0)
CHECK = float(os.environ.get("HUB_UPDATE_CHECK", "5"))
BUSY_PERCENT = 10         # a Hub older than 0.26.0 cannot say what it does: the card's own load decides
# Left out of the backups: logs, and what is downloaded or built again by itself - the sandbox's NuGet packages
# and caches, and .NET build output (.hub/dotnet) in any working folder (0.15.1).
BACKUP_EXCLUDE = ("./logs", "./sandbox-home/.nuget", "./sandbox-home/.local/share/NuGet")
BACKUP_EXCLUDE_ANYWHERE = ("/.hub/dotnet",)
# LoRA trainings (0.53.0): a run's checkpoints and samples (output/) and ai-toolkit's caches beside its pictures
# (images/_latent_cache, _t_e_cache) - gigabytes ai-toolkit makes again; with them a backup was 10.3 GB and took the
# Hub away for ten minutes (2026-10-30). The pictures, captions and run.json are backed up; a restore leaves the
# trainings' folders as they are and puts those back over them, so it never deletes checkpoints.
BACKUP_EXCLUDE_RE = re.compile(r"^\./training/[^/]+/(output|images/_[^/]+)(/|$)")
RESTORE_KEEP = {"logs", "training"}


class Stop(Exception):
    """A refusal with a reason the user can act on."""


class Out:
    def __init__(self) -> None:
        self.tty = sys.stdout.isatty()

    def _c(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.tty else text

    def say(self, text: str = "") -> None:
        try:
            print(text, flush=True)
        except OSError:              # the window was closed (--here): the update goes on without it
            pass

    def head(self, text: str) -> None:
        self.say(self._c(text, "36;1"))

    def ok(self, text: str) -> None:
        self.say(self._c("  [OK]   ", "32") + text)

    def warn(self, text: str) -> None:
        self.say(self._c("  [WARN] ", "33") + text)

    def bad(self, text: str) -> None:
        self.say(self._c("  [FAIL] ", "31") + text)

    def info(self, text: str) -> None:
        self.say("         " + text)


out = Out()


# ---------------------------------------------------------------- helpers

def read_env(path: Path | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    path = path or ENV_FILE
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def stamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")


def backup_name(version: str, now: datetime | None = None) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in version)
    return f"{stamp(now)}_{safe}"


def backups_to_delete(names: list[str], keep: int = KEEP) -> list[str]:
    """Oldest first. Names start with a timestamp, so lexical = chronological."""
    return sorted(sorted(names, reverse=True)[keep:])


def health_verdict(state: str, answered: bool, restarts_seen: int) -> str:
    if answered and state == "running":
        return "ok"
    if state in ("exited", "dead"):
        return "failed"
    if restarts_seen >= 3:
        return "failed"
    return "wait"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def run(cmd: list[str], capture: bool = True, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=capture, errors="replace")
    if check and r.returncode != 0:
        raise Stop(f"{' '.join(cmd[:4])} ... failed:\n         "
                   + ((r.stderr or r.stdout or "").strip()[-800:] if capture else "see above"))
    return r


def git(*args: str) -> subprocess.CompletedProcess:
    return run(["git", *args])


def compose_args() -> list[str]:
    args = ["docker", "compose", "-p", PROJECT, "--env-file", str(ENV_FILE), "-f", str(COMPOSE)]
    if OVERRIDE.is_file():
        args += ["-f", str(OVERRIDE)]
    return args


def compose(*args: str, capture: bool = True, check: bool = False) -> subprocess.CompletedProcess:
    return run([*compose_args(), *args], capture=capture, check=check)


def backup_dir() -> Path:
    env = read_env()
    return Path(os.environ.get("HUB_BACKUP_DIR") or env.get("HUB_BACKUP_DIR")
                or (HUB_ROOT / "backups")).resolve()


def read_version_at(ref: str = "HEAD") -> str:
    r = git("show", f"{ref}:VERSION")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "unknown"


def failed_file() -> Path:
    return backup_dir() / "last-failed.json"


def last_failed() -> dict:
    try:
        return json.loads(failed_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_backups(where: Path) -> list[dict]:
    items = []
    for meta in sorted(where.glob("*.json"), reverse=True):
        tar = where / (meta.stem + ".tar.gz")
        if not tar.is_file():
            continue
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        info.update(name=meta.stem, size=tar.stat().st_size)
        items.append(info)
    return items


# ---------------------------------------------------------------- steps

def preflight(need_clean: bool = True) -> str:
    if os.geteuid() != 0:
        raise Stop("run it as root:  sudo bash update.sh")
    for tool in ("git", "docker"):
        if shutil.which(tool) is None:
            raise Stop(f"{tool} is not installed. Run install.sh first.")
    if run(["docker", "compose", "version"]).returncode != 0:
        raise Stop("docker compose is not available - run install.sh first.")
    if not ENV_FILE.is_file():
        raise Stop(f"{ENV_FILE} does not exist - run install.sh first.")
    if not (ROOT / ".git").is_dir():
        raise Stop(f"{ROOT} is not a git clone.")
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if need_clean:
        dirty = [l for l in git("status", "--porcelain", "--untracked-files=no").stdout.splitlines()
                 if l.strip()]
        if dirty:
            out.warn("Files in the clone have been changed by hand:")
            for l in dirty:
                out.info(l)
            raise Stop(f"put machine settings in {ENV_FILE} and compose changes in {OVERRIDE}, "
                       "undo the edits (git checkout -- <file>), then update again.")
    return branch


def image_ref(service: str, tag: str) -> str:
    return f"slq-hub-{service}:{tag}"


def image_id(service: str, tag: str) -> str | None:
    r = run(["docker", "image", "inspect", image_ref(service, tag), "--format", "{{.Id}}"])
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def tag_images(src: str, dst: str) -> dict[str, str | None]:
    """Tag every built image src -> dst; returns service -> image id (None = none)."""
    ids = {}
    for svc in BUILT:
        iid = image_id(svc, src)
        ids[svc] = iid
        if iid:
            run(["docker", "tag", image_ref(svc, src), image_ref(svc, dst)], check=True)
    return ids


def container_id(service: str = "hub") -> str | None:
    r = compose("ps", "-a", "-q", service)
    return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else None


def container_state(service: str = "hub") -> str:
    cid = container_id(service)
    if not cid:
        return "missing"
    r = run(["docker", "inspect", "--format", "{{.State.Status}}", cid])
    return (r.stdout.strip() or "missing") if r.returncode == 0 else "missing"


def health_url() -> str:
    port = read_env().get("HUB_PORT") or "8080"
    return f"http://127.0.0.1:{port}/hub/api/health"


def answers() -> bool:
    try:
        with urllib.request.urlopen(health_url(), timeout=5) as r:
            return r.status == 200
    except OSError:
        return False


def wait_healthy(timeout: float) -> bool:
    start, restarting = time.monotonic(), 0
    out.info(f"Waiting for the Hub to answer (up to {int(timeout)} s)...")
    while time.monotonic() - start < timeout:
        state = container_state()
        restarting = restarting + 1 if state == "restarting" else 0
        verdict = health_verdict(state, state == "running" and answers(), restarting)
        if verdict == "ok":
            out.ok(f"The Hub answers ({int(time.monotonic() - start)} s)")
            return True
        if verdict == "failed":
            out.bad(f"The hub container is {state}.")
            return False
        time.sleep(POLL)
    out.bad(f"No answer within {int(timeout)} s.")
    return False


def show_log(lines: int = 40) -> None:
    r = compose("logs", "--no-color", "--tail", str(lines), "hub")
    text = (r.stdout or "") + (r.stderr or "")
    if text.strip():
        out.say("  ---- hub log (last lines) ----")
        for l in text.rstrip().splitlines()[-lines:]:
            out.say("  | " + l)
        out.say("  ------------------------------")


# ---------------------------------------------------------------- waiting until nobody uses the Hub (0.26.0)

class Cancelled(Exception):
    """`update.sh cancel`, a closed terminal or Ctrl+C while the update waited for the card."""


def busy_url() -> str:
    port = read_env().get("HUB_PORT") or "8080"
    return f"http://127.0.0.1:{port}/hub/api/internal/busy"


def card_load(drm: str = "/sys/class/drm") -> int | None:
    """The card's own load in percent (amdgpu's gpu_busy_percent; the busiest card), None without one."""
    best = None
    for p in Path(drm).glob("card*/device/gpu_busy_percent"):
        try:
            v = int(p.read_text().strip())
        except (OSError, ValueError):
            continue
        best = v if best is None else max(best, v)
    return best


_noted: set[str] = set()


def note_once(text: str) -> None:
    if text not in _noted:
        _noted.add(text)
        out.warn(text)


def hub_busy() -> tuple[bool, list[str]]:
    """(busy, what is going on), as the running Hub says (0.26.0 on). A Hub that does not say - older, refusing,
    not answering or cut off while it restarts - leaves it to the card's own load."""
    req = urllib.request.Request(busy_url(), headers={"X-Internal-Token": read_env().get("HUB_INTERNAL_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        why = [str(w) for w in data.get("why") or []]
        return bool(data.get("busy")) or bool(why), why
    except urllib.error.HTTPError as exc:            # 401 or 404 from a Hub before 0.26.0: it has no such question
        if exc.code == 403:
            note_once("The Hub refused the updater's question (403): is HUB_INTERNAL_TOKEN in .env the Hub's? "
                      "Watching the card's load instead.")
        elif exc.code >= 500:
            note_once(f"The Hub could not answer ({exc.code}). Watching the card's load instead.")
    except Cancelled:
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, AttributeError, TypeError):
        pass
    load = card_load()
    if load is not None and load >= BUSY_PERCENT:
        return True, [f"the card is {load} % busy (the running Hub cannot say what for)"]
    return False, []


def wait_until_free(quiet: float) -> None:
    """Back once the Hub has had nothing to do for `quiet` seconds; says what it waits for when that changes, and
    every ten minutes. Raises Cancelled from the signal handlers."""
    free_since, said, said_at = None, None, 0.0
    while True:
        busy, why = hub_busy()
        now = time.monotonic()
        if busy:
            free_since = None
            if why != said or now - said_at > 600:
                out.info(f"{datetime.now():%H:%M} waiting: " + "; ".join(why))
                said, said_at = why, now
        else:
            if free_since is None:
                free_since = now
                if said is not None:
                    out.info(f"{datetime.now():%H:%M} free - going on after {int(quiet)} s without work")
                said = None
            if now - free_since >= quiet:
                return
        time.sleep(CHECK)


def _cancel(signum, frame):
    raise Cancelled(signal.Signals(signum).name)


def attached() -> bool:
    """Run from a terminal that may close (not cron, not the background updater)."""
    return sys.stdout.isatty()


def lock_file() -> Path:
    return HUB_ROOT / "update.lock"


def log_file() -> Path:
    return HUB_ROOT / "update.log"


LOCK_MARK = b"tools/hub_update.py"   # in the command line of the process a lock names (not a pid used again)
CANCELLABLE = ("building", "waiting")


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def lock_data() -> dict:
    try:
        lock = json.loads(lock_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        lock_file().unlink(missing_ok=True)          # written whole (os.replace), so this one is broken
        return {}
    return lock if isinstance(lock, dict) else {}


def lock_alive(lock: dict) -> bool:
    """Its process still runs: the same boot, and that pid is an updater (not a process that got the number since)."""
    try:
        if lock.get("boot") and lock["boot"] != boot_id():
            return False
        return LOCK_MARK in Path(f"/proc/{int(lock['pid'])}/cmdline").read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        return False


def read_lock() -> dict:
    """The update that builds, waits or installs now ({} when none). A lock whose process is gone stays for
    recover_cut_off() to deal with."""
    lock = lock_data()
    return lock if lock and lock_alive(lock) else {}


def write_lock(state: dict, phase: str) -> None:
    lock = lock_data()
    since = lock.get("since") if lock.get("pid") == os.getpid() else None
    data = {"pid": os.getpid(), "boot": boot_id(), "version": state["new_version"], "phase": phase,
            "since": since or datetime.now().isoformat(timespec="seconds"), "log": str(log_file()),
            "old": state["old"], "old_version": state["old_version"]}
    tmp = lock_file().with_name(lock_file().name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, lock_file())


def drop_own_lock() -> None:
    if lock_data().get("pid") == os.getpid():
        lock_file().unlink(missing_ok=True)


def recover_cut_off(args) -> None:
    """An update whose process is gone (killed, the VM reset) left its lock. Before the install nothing was
    stopped: its code and its images' tags go back, so this update can offer the version again. Cut off during the
    install, the Hub may be stopped or half-updated: say what to do (--force restarts this version)."""
    lock = lock_data()
    if not lock or lock_alive(lock):
        return
    phase, version = str(lock.get("phase", "")), lock.get("version")
    if phase.startswith(CANCELLABLE) and lock.get("old"):
        out.warn(f"An update to {version} stopped while it was {phase} (since {lock.get('since')}), and its "
                 f"process is gone. Putting the code back at {lock.get('old_version')} first - nothing was stopped.")
        undo_build(lock)
    elif not args.force:
        raise Stop(f"an update to {version} was cut off while it was {phase} (since {lock.get('since')}; its log: "
                   f"{lock.get('log')}). Check the Hub: sudo bash update.sh status. To start this version again: "
                   "sudo bash update.sh --force; to go back to the last backup: sudo bash update.sh rollback.")
    else:
        out.warn(f"An update to {version} was cut off while it was {phase}: --force builds and starts again.")
    lock_file().unlink(missing_ok=True)


def quiet_signals() -> None:
    """From here on nothing may cut it off: neither `cancel` nor a closed window or Ctrl+C."""
    for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(s, signal.SIG_IGN)


def undo_build(state: dict) -> None:
    """Back to before the fast-forward, as after a failed build: the old code, the old images as :latest."""
    git("reset", "--hard", state["old"])
    tag_images("rollback", "latest")


def go_background(args, state: dict) -> int:
    """The rest of the update by an updater in a session of its own (0.26.0), so that neither Ctrl+C nor a closed
    window cuts it off - least of all while it stops, backs up and starts the Hub. This window shows its log."""
    path, log = resume_file(), log_file()
    child, start, why = None, 0, ""
    try:
        path.write_text(json.dumps({**state, "detached": True}), encoding="utf-8")
        if log.exists():
            log.replace(log.with_name(log.name + ".1"))
        flags = ["--keep", str(args.keep), "--timeout", str(args.timeout), "--quiet", str(args.quiet)] + \
            (["--no-backup"] if args.no_backup else []) + (["--now"] if args.now else [])
        with open(log, "w", encoding="utf-8") as f:
            f.write(f"SLQ Hub update {state['old_version']} -> {state['new_version']}, from "
                    f"{datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.flush()
            start = f.tell()
            child = subprocess.Popen([sys.executable, str(ROOT / "tools" / "hub_update.py"), "update", "--resume",
                                      str(path), "--yes", *flags], cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=f,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(60):
            if not path.exists() or child.poll() is not None:
                break
            time.sleep(0.5)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - `cancel`, Ctrl+C, a disk error: see below
        why = f" ({type(exc).__name__}: {exc})" if str(exc) else f" ({type(exc).__name__})"
    if child is None or path.exists():                  # it never took over: nothing was stopped, the code goes back
        quiet_signals()
        path.unlink(missing_ok=True)
        try:
            if child is not None:
                child.kill()
        except OSError:
            pass
        undo_build(state)
        raise Stop(f"the update could not go on in the background{why} (see {log}). The code is back where it "
                   "was; nothing was stopped.")
    out.info("The rest runs in the background: Ctrl+C or closing this window does not stop it.")
    out.info(f"Its log      : sudo tail -f {log}")
    out.info("Stop it while it builds or waits: sudo bash update.sh cancel")
    out.say()
    return follow(child, log, start)


def follow(child, log: Path, start: int = 0) -> int:
    """Shows the background updater's log until it ends (its exit code). The update is the other process's now:
    nothing here may undo it - Ctrl+C, `cancel` aimed at this one or a closed window only stop the showing."""
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, signal.SIG_DFL)
    pos = start
    try:
        while True:
            done = child.poll()
            pos = show_from(log, pos)
            if done is not None:
                return done
            time.sleep(0.5)
    except KeyboardInterrupt:
        out.say()
        out.info(f"No longer shown here - the update goes on. Follow it: sudo tail -f {log}")
    except Exception:  # noqa: BLE001 - the window went away
        pass
    return 0


def show_from(log: Path, pos: int) -> int:
    try:
        with open(log, "rb") as f:
            f.seek(pos)
            new = f.read()
    except OSError:
        return pos
    if new:
        sys.stdout.write(new.decode("utf-8", "replace"))
        sys.stdout.flush()
    return pos + len(new)


def _exclude(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if any(info.name == e or info.name.startswith(e + "/") for e in BACKUP_EXCLUDE):
        return None
    if BACKUP_EXCLUDE_RE.match(info.name):
        return None
    return None if any(e + "/" in info.name + "/" for e in BACKUP_EXCLUDE_ANYWHERE) else info


def make_backup(name: str, info: dict) -> Path:
    where = backup_dir()
    where.mkdir(parents=True, exist_ok=True)
    data = HUB_ROOT / "data"
    tar_path = where / f"{name}.tar.gz"
    try:
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(str(data), arcname=".", filter=_exclude)
    except OSError as exc:
        tar_path.unlink(missing_ok=True)
        raise Stop(f"the backup of {data} failed: {exc}")
    (where / f"{name}.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return tar_path


def restore_data(name: str) -> None:
    where = backup_dir()
    tar_path = where / f"{name}.tar.gz"
    if not tar_path.is_file():
        raise Stop(f"no backup {name}.tar.gz in {where}")
    data = HUB_ROOT / "data"
    for child in data.iterdir() if data.is_dir() else []:
        if child.name in RESTORE_KEEP:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    data.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(str(data), filter="tar")


def prune_backups(keep: int) -> list[str]:
    where = backup_dir()
    gone = backups_to_delete([b["name"] for b in list_backups(where)
                              if not b["name"].endswith("-before-rollback")], keep)
    for n in gone:
        for ext in (".tar.gz", ".json"):
            (where / f"{n}{ext}").unlink(missing_ok=True)
    return gone


def ensure_folders(root: Path = HUB_ROOT) -> list[str]:
    """Make the mounted folders that are missing (an update can add one); returns those it made."""
    made = []
    for rel in FOLDERS:
        p = root / rel
        if p.is_dir():
            continue
        new = [q for q in (p, *p.parents) if root in q.parents and not q.exists()]   # it and missing parents
        p.mkdir(parents=True, exist_ok=True)
        for q in new:
            try:
                os.chown(q, 1000, 1000)
            except OSError:
                pass
        made.append(rel)
    return made


def ensure_windows_password(path: Path | None = None) -> str | None:
    """With the Windows container's profile on (COMPOSE_PROFILES=windows, 0.39.0) and no WINDOWS_PASSWORD in .env,
    write a random one there - Windows would otherwise get dockur's default password, with RDP open to the LAN.
    Returns the new password, or None when nothing was needed."""
    path = path or ENV_FILE
    env = read_env(path)
    profiles = {p.strip() for p in env.get("COMPOSE_PROFILES", "").split(",")}
    if "windows" not in profiles or env.get("WINDOWS_PASSWORD"):
        return None
    import secrets
    password = secrets.token_urlsafe(12)
    text = path.read_text(encoding="utf-8")
    path.write_text(text + ("" if text.endswith("\n") or not text else "\n") + f"WINDOWS_PASSWORD={password}\n",
                    encoding="utf-8")
    return password


def start_new_services() -> list[str]:
    """Services of a compose profile turned on since the last update (COMPOSE_PROFILES in .env - the LoRA trainer,
    Windows) that have never had a container: built and started, so "add the profile, run update.sh" works when
    there is no new version to fetch (0.50.1). A service that exists, running or stopped, is left as it is."""
    wanted = compose("config", "--services")
    have = compose("ps", "-a", "--services")
    if wanted.returncode != 0 or have.returncode != 0:
        return []
    new = sorted(set(wanted.stdout.split()) - set(have.stdout.split()))
    if not new:
        return []
    ensure_folders(HUB_ROOT)
    say_windows_password()
    out.info(f"Turned on in .env since the last update: {', '.join(new)} - building and starting it (a first build "
             "can take a long while)")
    if compose("up", "-d", "--build", *new, capture=False).returncode != 0:
        raise Stop(f"could not build or start {', '.join(new)} - see above. Try again: "
                   f"sudo bash update.sh compose up -d --build {' '.join(new)}")
    return new


def say_windows_password() -> None:
    new = ensure_windows_password()
    if new:
        user = read_env().get("WINDOWS_USER") or "hub"
        out.ok(f"Windows: a password for RDP was written to {ENV_FILE} (WINDOWS_PASSWORD) - user {user}, "
               f"password {new}")


FIREWALL = ((ROOT / "deploy" / "firewall" / "slq-hub-firewall.sh", Path("/usr/local/sbin/slq-hub-firewall.sh"), 0o755),
            (ROOT / "deploy" / "firewall" / "slq-hub-firewall.service",
             Path("/etc/systemd/system/slq-hub-firewall.service"), 0o644))


def sync_firewall(files=FIREWALL, systemctl: str = "systemctl") -> str:
    """The VM's firewall as this version has it: install.sh put the script in place once, and a new rule (the
    model containers' network, 0.15.0) comes with an update. Copies what changed and applies the rules again -
    they are added only when missing, so this is safe every time. Returns what it did."""
    changed = []
    for src, dst, mode in files:
        if not src.is_file():
            continue
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            os.chmod(dst, mode)
            changed.append(dst.name)
        except OSError as exc:
            return f"could not install {dst} ({exc})"
    if shutil.which(systemctl) is None:
        return "no systemd here: " + (", ".join(changed) + " copied" if changed else "nothing to do")
    if changed:
        run([systemctl, "daemon-reload"])
    r = run([systemctl, "restart", "slq-hub-firewall.service"])
    if r.returncode != 0:
        return "the firewall did not apply - see: journalctl -u slq-hub-firewall"
    return ("updated " + ", ".join(changed) + " and applied" if changed else "applied")


MODELS = ("llm", "comfyui", "speech")
WAY_OUT = ("import socket\n"
           "s = socket.socket()\n"
           "s.settimeout(4)\n"
           "try:\n"
           "    s.connect(('1.1.1.1', 443))\n"
           "    print('open')\n"
           "except OSError:\n"
           "    print('closed')\n")


def ways_out(services=MODELS) -> dict[str, str]:
    """Whether each model container can open a connection to the internet (1.1.1.1:443) - with the inner
    network's firewall rules it cannot. "open", "closed", or why it was not tried."""
    def probe(svc: str) -> str:
        if container_state(svc) != "running":
            return "not running"
        try:
            r = subprocess.run([*compose_args(), "exec", "-T", svc, "python3", "-c", WAY_OUT], cwd=str(ROOT),
                               text=True, capture_output=True, errors="replace", timeout=60, check=False)
        except subprocess.TimeoutExpired:
            return "no answer"
        last = (r.stdout.strip().splitlines() or [""])[-1]
        return last if r.returncode == 0 and last in ("open", "closed") else "could not try"
    with ThreadPoolExecutor(len(services)) as pool:
        return dict(zip(services, pool.map(probe, services)))


def report_ways_out() -> bool:
    """Tries each model container's way out and says what it found; False when one reaches the internet."""
    found = ways_out()
    for svc, state in found.items():
        if state == "closed":
            out.ok(f"{svc:<8}: no way out (the internet does not answer it)")
        elif state == "open":
            out.bad(f"{svc:<8}: reaches the internet - its firewall rule is missing (sudo bash update.sh firewall)")
        else:
            out.info(f"{svc:<8}: {state}")
    return "open" not in found.values()


def put_back(commit: str, images: dict[str, str | None], backup: str | None, timeout: float) -> bool:
    out.head("Putting the previous version back...")
    compose("stop")
    git("reset", "--hard", commit)
    out.ok(f"Code   : back at {commit[:10]} ({read_version_at()})")
    rebuild = False
    for svc in BUILT:
        want = images.get(svc)
        if want is None:                       # the service is new in the version being put back: nothing to restore
            continue
        if image_id(svc, "rollback") == want:
            run(["docker", "tag", image_ref(svc, "rollback"), image_ref(svc, "latest")], check=True)
        else:
            rebuild = True
    if rebuild:
        out.info("Some previous images are gone - building them from the old code...")
        if compose("build", capture=False).returncode != 0:
            raise Stop("rebuilding the previous version failed. The data is in the backup.")
    out.ok("Images : the previous ones")
    if backup:
        restore_data(backup)
        out.ok(f"Data   : restored from {backup}")
    compose("up", "-d", "--remove-orphans", capture=False, check=True)    # a service the old version lacks goes
    return wait_healthy(timeout)


# ---------------------------------------------------------------- commands

def cmd_update(args) -> int:
    if args.resume:                                   # the old updater handed over, or this one went to the background
        state = take_over(args.resume)
        out.head("=== SLQ Hub - update, " + ("in the background ===" if state.get("detached")
                                             else "continued by the new updater ==="))
        return finish_update(args, state)
    out.head("=== SLQ Hub - update ===")
    branch = preflight()
    recover_cut_off(args)
    other = read_lock()
    if other:
        raise Stop(f"an update to {other.get('version')} is already {other.get('phase')} (since {other.get('since')}, "
                   f"process {other.get('pid')}). Follow it: sudo tail -f {other.get('log')}; stop it while it "
                   "waits: sudo bash update.sh cancel")
    old = git("rev-parse", "HEAD").stdout.strip()
    old_version = read_version_at()
    out.info(f"Clone  : {ROOT}  ({branch} at {old[:10]}, version {old_version})")
    out.info(f"Backups: {backup_dir()}")
    if git("fetch", "origin").returncode != 0:
        raise Stop("could not fetch from GitHub (network?).")
    if git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}").returncode != 0:
        raise Stop(f"the clone is on '{branch}', which the remote does not have.")
    incoming = [l for l in git("log", "--oneline", f"HEAD..origin/{branch}").stdout.splitlines() if l.strip()]
    if not incoming and not args.force:
        started = start_new_services()
        out.ok(f"Already up to date ({old_version}). " + (f"Started {', '.join(started)}." if started
                                                         else "Nothing to do."))
        return 0
    new_version = read_version_at(f"origin/{branch}")
    target = git("rev-parse", f"origin/{branch}").stdout.strip()
    failed = last_failed()
    if failed.get("commit") == target and not args.force:
        raise Stop(f"{new_version} ({target[:10]}) did not start when it was tried at "
                   f"{failed.get('at')}, and nothing newer has been pushed. Try again with --force.")
    if incoming:
        out.head(f"New: {old_version} -> {new_version}")
        for l in incoming[:30]:
            out.info(l)
        if len(incoming) > 30:
            out.info(f"... and {len(incoming) - 30} more")
    else:
        out.warn("No new commits - rebuilding and restarting anyway (--force).")
    if not args.yes:
        if not sys.stdin.isatty():
            raise Stop("not asking without a terminal - add --yes to update unattended.")
        when = "right after the build" if args.now else "once nobody has used it for a minute"
        if input(f"\n  Update? The new version is built while the Hub runs; it is stopped for the backup {when}. "
                 "[y/N] ").strip().lower() not in ("y", "yes", "j", "ja"):
            out.info("Nothing changed.")
            return 0

    old_images = tag_images("latest", "rollback")
    if incoming and run(["git", "merge", "--ff-only", f"origin/{branch}"], capture=False).returncode != 0:
        raise Stop("the update is not a fast-forward - the clone has commits of its own.")
    state = {"v": RESUME_VERSION, "old": old, "old_version": old_version, "new_version": new_version,
             "target": target, "old_images": old_images}
    if incoming and updater_changed(old):
        return hand_over(args, state)
    return finish_update(args, state)


# The rest of an update, done by the updater of the version being installed when the fast-forward changed this
# file or update.sh: the old one writes what the new one needs (RESUME_VERSION 1 - every later updater reads it)
# and waits for it. The new one deletes the file when it takes over; a file still there afterwards means it never
# did (it did not start), and the old one puts the code back as a failed build would.
RESUME_VERSION = 1
UPDATER_FILES = ("tools/hub_update.py", "update.sh")


def updater_changed(old: str) -> bool:
    return git("diff", "--quiet", old, "HEAD", "--", *UPDATER_FILES).returncode == 1


def resume_file() -> Path:
    return HUB_ROOT / "update-resume.json"


def hand_over(args, state: dict) -> int:
    path = resume_file()
    path.write_text(json.dumps(state), encoding="utf-8")
    out.info("The updater itself changed - the new one does the rest of this update.")
    flags = ["--keep", str(args.keep), "--timeout", str(args.timeout), "--quiet", str(args.quiet)] + \
        (["--no-backup"] if args.no_backup else []) + (["--now"] if args.now else []) + (["--here"] if args.here else [])
    child = subprocess.Popen([sys.executable, str(ROOT / "tools" / "hub_update.py"), "update", "--resume", str(path),
                              *flags])
    while True:
        try:
            rc = child.wait()
            break
        except KeyboardInterrupt:     # the new updater got the Ctrl+C too: killing it could cut off its undo
            continue
    if path.exists():                                   # it never took over: as a failed build
        path.unlink(missing_ok=True)
        git("reset", "--hard", state["old"])
        tag_images("rollback", "latest")
        raise Stop(f"the new updater did not start (exit code {rc}). The code is back where it was; nothing was "
                   "stopped.")
    return rc


def take_over(path: str) -> dict:
    """The state the old updater left (and the file gone, so it knows this one took over)."""
    p = Path(path)
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Stop(f"cannot read {p} ({exc}).") from exc
    if not isinstance(state, dict) or state.get("v") != RESUME_VERSION or not state.get("old"):
        raise Stop(f"{p} is not an update this updater can continue.")   # leave the file: the old one rolls back
    p.unlink(missing_ok=True)                        # now the old updater sees it is gone and knows this one took over
    state["old_images"] = state.get("old_images") or {}
    return state


def finish_update(args, state: dict) -> int:
    """Build, wait until nobody uses the Hub, back up, start, check - or put the old version back."""
    try:
        return _finish_update(args, state)
    finally:
        drop_own_lock()


def in_terminal(args, state: dict) -> bool:
    """Started in a window that may close (not cron, not the background updater, not --here)."""
    return attached() and not state.get("detached") and not args.here


def _finish_update(args, state: dict) -> int:
    old, old_version, new_version = state["old"], state["old_version"], state["new_version"]
    old_images = state["old_images"]
    if args.resume and in_terminal(args, state):
        # Handed over by the old updater, which kills this one a quarter of a second after a Ctrl+C - even in the
        # middle of putting the code back: the build goes to the background too.
        return go_background(args, state)
    for s in (signal.SIGTERM, signal.SIGHUP):          # `update.sh cancel`, a closed window: undo, stop nothing
        signal.signal(s, _cancel)
    phase, detach = "building", False
    try:
        if not state.get("built"):
            write_lock(state, phase)
            out.head("Building the new images (the Hub keeps running)...")
            compose("pull", "searxng", capture=False)
            if compose("build", capture=False).returncode != 0:
                quiet_signals()
                undo_build(state)
                raise Stop("the build failed (see above). The code is back where it was; nothing was stopped.")
            out.ok("Built")
            state["built"] = True
        if in_terminal(args, state):                   # stopping, the backup and the start: in a session of its own
            detach = True
        elif container_state() == "running" and not args.now:   # 0.26.0: not while somebody uses the Hub
            phase = "waiting for the Hub to be free"
            write_lock(state, phase)
            out.head(f"Waiting until nobody has used the Hub for {int(args.quiet)} s...")
            wait_until_free(args.quiet)
            out.ok("Nobody uses the Hub")
    except Stop:
        raise
    except (Cancelled, KeyboardInterrupt) as exc:
        quiet_signals()
        undo_build(state)
        raise Stop(f"cancelled while {phase} ({str(exc) or 'Ctrl+C'}). The code is back where it was; nothing was "
                   "stopped, and the next update.sh offers this version again.")
    except Exception as exc:  # noqa: BLE001 - anything before the install: the Hub runs on as it was
        quiet_signals()
        undo_build(state)
        raise Stop(f"{type(exc).__name__} while {phase}: {exc}. The code is back where it was; nothing was "
                   "stopped.") from exc
    if detach:
        return go_background(args, state)
    quiet_signals()                                     # from here on it finishes, or puts the old version back
    write_lock(state, "being installed")

    name = None
    first = container_id() is None
    if args.no_backup:
        out.warn("Backup : skipped (--no-backup)")
    elif first:
        out.info("Backup : skipped - first start, there is no data yet")
    else:
        out.head("Backing up the data...")
        compose("stop", check=True)
        name = backup_name(old_version)
        try:
            tar = make_backup(name, {"commit": old, "version": old_version, "images": old_images,
                                     "created": datetime.now().isoformat(timespec="seconds"),
                                     "reason": "before update to " + new_version})
        except Stop:
            out.bad("Starting the old version again.")
            git("reset", "--hard", old)
            tag_images("rollback", "latest")
            compose("up", "-d")
            raise
        out.ok(f"Backup : {tar.name} ({human(tar.stat().st_size)})")

    out.head("Starting the new version...")
    say_windows_password()
    made = ensure_folders()
    if made:
        out.ok("Folders: made " + ", ".join(made))
    compose("up", "-d", "--remove-orphans", capture=False, check=True)
    if wait_healthy(args.timeout):
        fw = sync_firewall()
        (out.ok if fw.startswith(("applied", "updated")) else out.warn)(f"Firewall: {fw}")
        report_ways_out()
        gone = prune_backups(args.keep)
        if gone:
            out.info(f"Deleted {len(gone)} old backup(s), keeping {args.keep}.")
        run(["docker", "image", "prune", "-f"])
        failed_file().unlink(missing_ok=True)
        out.head("=== Update complete ===")
        out.info(f"Version: {old_version} -> {read_version_at()}")
        return 0

    show_log()
    if not any(old_images.values()):
        raise Stop("the new version does not start, and there are no earlier images to go back to.")
    backup_dir().mkdir(parents=True, exist_ok=True)
    failed_file().write_text(json.dumps({"commit": state["target"], "version": new_version,
                                         "at": datetime.now().isoformat(timespec="seconds")}),
                             encoding="utf-8")
    if put_back(old, old_images, name, args.timeout):
        out.head("=== Update undone ===")
        out.warn(f"{new_version} did not start; {old_version} runs again with the data from before. "
                 f"update.sh skips {new_version} until something newer is pushed (or --force).")
        return 1
    show_log()
    raise Stop(f"the previous version did not come back either. The backup is {name or '(none)'} "
               f"in {backup_dir()}.")


def cmd_rollback(args) -> int:
    out.head("=== SLQ Hub - roll back ===")
    preflight()
    backups = list_backups(backup_dir())
    if not backups:
        raise Stop(f"there are no backups in {backup_dir()}.")
    pick = next((b for b in backups if b["name"] == args.backup), None) if args.backup else backups[0]
    if pick is None:
        raise Stop(f"no backup called {args.backup}. `update.sh backups` lists them.")
    commit = pick.get("commit")
    if not commit or git("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        raise Stop(f"the backup does not name a commit this clone has ({commit}).")
    out.info(f"Back to : {pick['name']} - version {pick.get('version')}, made {pick.get('created')}")
    out.warn("Everything changed since then (chats, settings, files) is replaced by the backup. "
             "A backup of the current state is made first.")
    if not args.yes:
        if not sys.stdin.isatty():
            raise Stop("not asking without a terminal - add --yes.")
        if input("  Roll back? [y/N] ").strip().lower() not in ("y", "yes", "j", "ja"):
            out.info("Nothing changed.")
            return 0
    compose("stop")
    now_version = read_version_at()
    safety = backup_name(now_version) + "-before-rollback"
    make_backup(safety, {"commit": git("rev-parse", "HEAD").stdout.strip(), "version": now_version,
                         "images": {s: image_id(s, "latest") for s in BUILT},
                         "created": datetime.now().isoformat(timespec="seconds"),
                         "reason": "before rolling back to " + pick["name"]})
    out.ok(f"Current state saved as {safety}")
    if put_back(commit, pick.get("images") or {}, pick["name"], args.timeout):
        out.head("=== Rolled back ===")
        out.info(f"Version: {read_version_at()}. `update.sh` brings the newest version back.")
        return 0
    show_log()
    raise Stop(f"the restored version does not answer. {safety} holds the state from before.")


def cmd_status(args) -> int:
    out.head("=== SLQ Hub - status ===")
    preflight(need_clean=False)
    out.info(f"Clone  : {ROOT} at {git('rev-parse', '--short', 'HEAD').stdout.strip()}, "
             f"version {read_version_at()}")
    for svc in ("hub", "llm", "comfyui", "speech", "searxng", "sandbox"):
        state = container_state(svc)
        (out.ok if state == "running" else out.warn)(f"{svc:<8}: {state}")
    (out.ok if answers() else out.bad)("Hub answers" if answers() else "Hub does not answer")
    fw = run(["systemctl", "is-active", "slq-hub-firewall.service"]) if shutil.which("systemctl") else None
    if fw is not None:
        active = fw.stdout.strip() == "active"
        current = all(dst.is_file() and dst.read_bytes() == src.read_bytes() for src, dst, _ in FIREWALL
                      if src.is_file())
        (out.ok if active and current else out.warn)(
            "Firewall: " + ("applied" if active else fw.stdout.strip() or "not active")
            + ("" if current else " - an older script; run: sudo bash update.sh firewall"))
    report_ways_out()
    b = list_backups(backup_dir())
    out.info(f"Backups: {len(b)} in {backup_dir()}" + (f", newest {b[0]['name']}" if b else ""))
    lock = lock_data()
    if lock and lock_alive(lock):
        out.warn(f"Update : to {lock.get('version')} {lock.get('phase')} since {lock.get('since')} "
                 f"(process {lock.get('pid')}, log {lock.get('log')})")
    elif lock:
        out.bad(f"Update : to {lock.get('version')} was cut off while it was {lock.get('phase')} (since "
                f"{lock.get('since')}; log {lock.get('log')}) - the next update.sh deals with it")
    return 0


def cmd_cancel(args) -> int:
    """Stop an update that builds or waits for the Hub to be free (the code and images go back; nothing was
    stopped)."""
    if os.geteuid() != 0:
        raise Stop("run it as root: sudo bash update.sh cancel")
    lock = read_lock()
    if not lock:
        out.info("No update is building or waiting.")
        return 0
    if not str(lock.get("phase", "")).startswith(CANCELLABLE):
        raise Stop(f"the update to {lock.get('version')} is {lock.get('phase')} now - too late to cancel; it "
                   "finishes, or puts the old version back by itself.")
    os.kill(int(lock["pid"]), signal.SIGTERM)
    for _ in range(120):
        if not read_lock():
            out.ok(f"The update to {lock.get('version')} is cancelled; the Hub runs on as it was.")
            return 0
        time.sleep(0.5)
    raise Stop(f"the waiting update (process {lock['pid']}) did not stop - see {lock.get('log')}.")


def cmd_firewall(args) -> int:
    """Install this version's firewall script and apply it (an update does this by itself)."""
    if os.geteuid() != 0:
        raise Stop("run it as root: sudo bash update.sh firewall")
    fw = sync_firewall()
    ok = fw.startswith(("applied", "updated"))
    (out.ok if ok else out.bad)(f"Firewall: {fw}")
    return 0 if report_ways_out() and ok else 1


def cmd_backups(args) -> int:
    b = list_backups(backup_dir())
    if not b:
        out.info(f"No backups in {backup_dir()}.")
        return 0
    for x in b:
        out.say(f"  {x['name']:<40} {human(x['size']):>9}  version {x.get('version', '?'):<9} "
                f"{x.get('reason', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "compose":          # passthrough, flags and all
        if not ENV_FILE.is_file():
            out.bad(f"{ENV_FILE} does not exist - run install.sh first.")
            return 1
        say_windows_password()
        ensure_folders()
        return run([*compose_args(), *argv[1:]], capture=False).returncode
    if not argv or argv[0].startswith("-"):
        argv = ["update", *argv]
    p = argparse.ArgumentParser(prog="update.sh", description="SLQ Hub updates")
    sub = p.add_subparsers(dest="command", required=True)
    u = sub.add_parser("update", help="pull, build, back up, restart, check (the default)")
    u.add_argument("--yes", "-y", action="store_true", help="do not ask")
    u.add_argument("--force", action="store_true", help="rebuild and restart even with nothing new")
    u.add_argument("--no-backup", action="store_true", help="skip the data backup")
    u.add_argument("--keep", type=int, default=KEEP, help=f"backups to keep (default {KEEP})")
    u.add_argument("--timeout", type=float, default=TIMEOUT, help="seconds to wait for the Hub")
    u.add_argument("--now", action="store_true", help="do not wait until nobody uses the Hub")
    u.add_argument("--here", action="store_true", help="wait and install in this window, not in the background")
    u.add_argument("--quiet", type=float, default=QUIET,
                   help=f"seconds without work before the Hub is stopped (default {QUIET})")
    u.add_argument("--resume", metavar="FILE", help=argparse.SUPPRESS)      # the old updater handing over
    r = sub.add_parser("rollback", help="back to a backup: its code, images and data")
    r.add_argument("backup", nargs="?", help="backup name (default: the newest)")
    r.add_argument("--yes", "-y", action="store_true")
    r.add_argument("--timeout", type=float, default=TIMEOUT)
    sub.add_parser("status", help="version, containers, health, firewall, backups")
    sub.add_parser("backups", help="list the backups")
    sub.add_parser("firewall", help="install this version's firewall script and apply it")
    sub.add_parser("cancel", help="stop an update that builds or waits for the Hub to be free")
    sub.add_parser("compose", help="docker compose with this install's files")
    args = p.parse_args(argv)
    try:
        return {"update": cmd_update, "rollback": cmd_rollback, "status": cmd_status,
                "backups": cmd_backups, "firewall": cmd_firewall, "cancel": cmd_cancel}[args.command](args)
    except Stop as stop:
        out.say()
        out.bad(f"STOPPED: {stop}")
        out.say()
        return 1
    except (KeyboardInterrupt, Cancelled):
        out.say()
        out.warn("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
