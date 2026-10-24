#!/usr/bin/env python3
"""Update SLQ Hub on its VM, with a backup and a way back.

Ported from SLQ Companion's tools/slq_container.py (2.66.0), for several
services. Run on the VM as root, from the clone (normally through update.sh):

    sudo bash update.sh                  pull, build, back up, restart, check
    sudo bash update.sh --yes            the same without the question (cron)
    sudo bash update.sh --force          rebuild and restart even with nothing new
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
  4. Stops everything and backs up /srv/slq-hub/data (not the models, which
     can be downloaded again) to <backups>/<time>_<version>.tar.gz + .json.
  5. Starts the new version and waits until /hub/api/health answers.
  6. Healthy -> the VM's firewall script is brought up to this version and
     applied, and old backups beyond --keep are deleted. Not healthy within
     --timeout -> the log is shown and everything is put back: old code,
     old images, and the data from step 4.

Settings live in /srv/slq-hub/.env; compose changes in
/srv/slq-hub/docker-compose.override.yml - the clone stays clean, so
updates are fast-forwards. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
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
BUILT = ("hub", "llm", "comfyui", "sandbox", "speech")  # services with their own image
# Folders the services mount, made (owned by uid 1000, the containers' user) before they start: Docker would
# create a missing one as root, and the Hub could not download into it.
FOLDERS = ("data", "models/llm", "models/comfy", "models/speech", "comfy/input", "comfy/output", "comfy/user")
KEEP = 3
TIMEOUT = 300
POLL = float(os.environ.get("HUB_UPDATE_POLL", "3"))
# Left out of the backups: logs, and what is downloaded or built again by itself - the sandbox's NuGet packages
# and caches, and .NET build output (.hub/dotnet) in any working folder (0.15.1).
BACKUP_EXCLUDE = ("./logs", "./sandbox-home/.nuget", "./sandbox-home/.local/share/NuGet")
BACKUP_EXCLUDE_ANYWHERE = ("/.hub/dotnet",)


class Stop(Exception):
    """A refusal with a reason the user can act on."""


class Out:
    def __init__(self) -> None:
        self.tty = sys.stdout.isatty()

    def _c(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.tty else text

    def say(self, text: str = "") -> None:
        print(text, flush=True)

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

def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    env: dict[str, str] = {}
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


def _exclude(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if any(info.name == e or info.name.startswith(e + "/") for e in BACKUP_EXCLUDE):
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
    keep = {"logs"}
    for child in data.iterdir() if data.is_dir() else []:
        if child.name in keep:
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
    if args.resume:                                   # the old updater fast-forwarded and handed over
        out.head("=== SLQ Hub - update, continued by the new updater ===")
        return finish_update(args, take_over(args.resume))
    out.head("=== SLQ Hub - update ===")
    branch = preflight()
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
        out.ok(f"Already up to date ({old_version}). Nothing to do.")
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
        if input("\n  Update now? The Hub is stopped for the backup. [y/N] ").strip().lower() \
                not in ("y", "yes", "j", "ja"):
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
    flags = ["--keep", str(args.keep), "--timeout", str(args.timeout)] + (["--no-backup"] if args.no_backup else [])
    try:
        rc = subprocess.call([sys.executable, str(ROOT / "tools" / "hub_update.py"), "update", "--resume", str(path),
                              *flags])
    except KeyboardInterrupt:
        rc = 130
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
    """Build, back up, start, check - or put the old version back."""
    old, old_version, new_version = state["old"], state["old_version"], state["new_version"]
    old_images = state["old_images"]
    out.head("Building the new images (the Hub keeps running)...")
    compose("pull", "searxng", capture=False)
    if compose("build", capture=False).returncode != 0:
        git("reset", "--hard", old)
        tag_images("rollback", "latest")
        raise Stop("the build failed (see above). The code is back where it was; nothing was stopped.")
    out.ok("Built")

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
    return 0


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
    u.add_argument("--resume", metavar="FILE", help=argparse.SUPPRESS)      # the old updater handing over
    r = sub.add_parser("rollback", help="back to a backup: its code, images and data")
    r.add_argument("backup", nargs="?", help="backup name (default: the newest)")
    r.add_argument("--yes", "-y", action="store_true")
    r.add_argument("--timeout", type=float, default=TIMEOUT)
    sub.add_parser("status", help="version, containers, health, firewall, backups")
    sub.add_parser("backups", help="list the backups")
    sub.add_parser("firewall", help="install this version's firewall script and apply it")
    sub.add_parser("compose", help="docker compose with this install's files")
    args = p.parse_args(argv)
    try:
        return {"update": cmd_update, "rollback": cmd_rollback, "status": cmd_status,
                "backups": cmd_backups, "firewall": cmd_firewall}[args.command](args)
    except Stop as stop:
        out.say()
        out.bad(f"STOPPED: {stop}")
        out.say()
        return 1
    except KeyboardInterrupt:
        out.say()
        out.warn("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
