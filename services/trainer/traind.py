"""SLQ Hub's LoRA trainer (0.50.0): a small HTTP service around Ostris' ai-toolkit, for the Hub only.

The Hub decides when it may run - it takes the whole card through its GPU queue first - and sends one job at a
time. This service only runs what it is sent and says how far it got. Python's standard library only, so it adds
nothing to ai-toolkit's own environment.

    GET  /health           {"ok", "busy", "job": the one running, "toolkit": its folder exists, "ref": the ai-toolkit
                            commit it was built at}
    POST /jobs             {"id", "config": {...ai-toolkit's config as JSON...}, "dir": the run's folder} - start
    GET  /jobs/<id>        {"state": running|stopping|done|failed|cancelled, "step", "total", "loss", "elapsed_s",
                            "eta_s", "log": the last lines, "error"}
    POST /jobs/<id>/cancel stop it (Ctrl+C first, as ai-toolkit wants, then harder): "stopping" until ai-toolkit has
                           exited, so the Hub does not hand the card on while it still holds memory

Like the other model containers it cannot reach the internet: the Hub fetches the base models into Hugging Face's
cache (models/hf, HF_HUB_OFFLINE here). The config is written as <dir>/config.json and run with
`python run.py <dir>/config.json` in the ai-toolkit folder; its output (checkpoints every N steps, sample
pictures) lands where the config says, in the run's folder, which the Hub reads itself. Progress comes from ai-toolkit's progress bar: "  15%|...| 300/2000 [10:31<58:12, ...
loss: 3.792e-01]".
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("TRAINER_PORT", "7300"))
TOOLKIT = Path(os.environ.get("AI_TOOLKIT", "/opt/ai-toolkit"))
ROOT = Path(os.environ.get("TRAINER_ROOT", "/training")).resolve()       # runs' folders must lie under it
REF = (Path("/opt/ai-toolkit.ref").read_text().strip() if Path("/opt/ai-toolkit.ref").is_file()
       else os.environ.get("AI_TOOLKIT_REF", ""))           # the commit the image was built at
BAR = re.compile(r"(\d+)/(\d+) \[(\d+(?::\d+){1,2})<(\d+(?::\d+){1,2}|\?)")
LOSS = re.compile(r"loss[:=]\s*([0-9.]+(?:e[-+]?\d+)?)", re.I)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

lock = threading.Lock()
jobs: dict[str, dict] = {}


def _secs(t: str) -> int | None:
    if t == "?":
        return None
    parts = [int(x) for x in t.split(":")]
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def toolkit_env() -> dict:
    """ai-toolkit's environment: without PyTorch's expandable segments, which the image sets. Measured on the RX
    7900 XTX (2026-10-29, the card's VRAM in sysfs against each process's in /sys/class/kfd): with them, about 12 GB
    ai-toolkit freed after its text encoder stayed in use on the card until the process ended, and the first sample
    ran out of memory with PyTorch holding 8.8 of the card's 24 GB. TRAINER_ALLOC_CONF sets the allocator instead."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    for key in ("PYTORCH_HIP_ALLOC_CONF", "PYTORCH_CUDA_ALLOC_CONF", "PYTORCH_ALLOC_CONF"):
        env.pop(key, None)
    if os.environ.get("TRAINER_ALLOC_CONF"):
        env["PYTORCH_HIP_ALLOC_CONF"] = os.environ["TRAINER_ALLOC_CONF"]
    return env


def busy() -> bool:
    return any(j["state"] in ("running", "stopping") for j in jobs.values())


def _follow(job: dict) -> None:
    """Read the process's output (the bar redraws with \\r) into the job's progress and log."""
    proc = job["proc"]
    buf = b""
    while True:
        chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        *pieces, buf = re.split(rb"[\r\n]", buf)
        for raw in pieces:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            m = BAR.search(line)
            with lock:
                if m:
                    job["step"], job["total"] = int(m.group(1)), int(m.group(2))
                    job["elapsed_s"], job["eta_s"] = _secs(m.group(3)), _secs(m.group(4))
                    loss = LOSS.search(line)
                    if loss:
                        job["loss"] = float(loss.group(1))
                    continue                                 # bars are not kept in the log
                job["log"].append(line[:400])
                del job["log"][:-60]
    code = proc.wait()
    with lock:
        if job["state"] in ("running", "stopping"):
            job["state"] = "cancelled" if job.get("cancelled") else ("done" if code == 0 else "failed")
            if job["state"] == "failed":
                job["error"] = next((x for x in reversed(job["log"]) if "error" in x.lower()),
                                    f"ai-toolkit stopped with exit code {code}")[:400]
        job["ended"] = time.time()


def start_job(body: dict) -> tuple[int, dict]:
    jid, cfg, folder = str(body.get("id") or ""), body.get("config"), Path(str(body.get("dir") or "/nonexistent"))
    if not ID_RE.match(jid) or not isinstance(cfg, dict):
        return 400, {"error": "id and config are needed"}
    folder = folder.resolve()
    if ROOT not in folder.parents or not folder.is_dir():
        return 400, {"error": f"the run's folder must be a folder under {ROOT}"}
    if not (TOOLKIT / "run.py").is_file():
        return 503, {"error": f"ai-toolkit is not in {TOOLKIT}"}
    with lock:
        if busy():
            return 409, {"error": "the trainer is busy"}
        conf = folder / "config.json"
        conf.write_text(json.dumps(cfg, indent=1))
        proc = subprocess.Popen(["python", "run.py", str(conf)], cwd=TOOLKIT, env=toolkit_env(), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
        job = {"id": jid, "state": "running", "step": 0, "total": 0, "loss": None, "elapsed_s": 0, "eta_s": None,
               "log": [], "error": "", "proc": proc, "started": time.time()}
        jobs[jid] = job
    threading.Thread(target=_follow, args=(job,), daemon=True).start()
    return 200, {"ok": True}


def cancel_job(jid: str) -> tuple[int, dict]:
    with lock:
        job = jobs.get(jid)
        if job is None:
            return 404, {"error": "no such job"}
        if job["state"] != "running":
            return 200, {"ok": True, "state": job["state"]}
        job["cancelled"] = True
        job["state"] = "stopping"            # "cancelled" once ai-toolkit has exited and let go of the card
        proc = job["proc"]

    def stop() -> None:
        for sig, wait in ((signal.SIGINT, 60), (signal.SIGTERM, 20), (signal.SIGKILL, 5)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue
    threading.Thread(target=stop, daemon=True).start()
    return 200, {"ok": True, "state": "stopping"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if 0 < n < 5_000_000 else {}
        except ValueError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            with lock:
                job = next((j["id"] for j in jobs.values() if j["state"] in ("running", "stopping")), None)
            return self._send(200, {"ok": True, "busy": job is not None, "job": job,
                                    "toolkit": (TOOLKIT / "run.py").is_file(), "ref": REF})
        m = re.fullmatch(r"/jobs/([a-z0-9-]+)", self.path)
        if m:
            with lock:
                job = jobs.get(m.group(1))
                if job is None:
                    return self._send(404, {"error": "no such job"})
                return self._send(200, {k: v for k, v in job.items() if k != "proc"})
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/jobs":
            return self._send(*start_job(self._body()))
        m = re.fullmatch(r"/jobs/([a-z0-9-]+)/cancel", self.path)
        if m:
            return self._send(*cancel_job(m.group(1)))
        self._send(404, {"error": "not found"})

    def log_message(self, fmt: str, *args) -> None:       # quiet: the Hub polls every few seconds
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
