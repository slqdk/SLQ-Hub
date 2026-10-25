"""The Hub's long-lived objects, built once per app (and per test)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from fastapi import Request

from . import db
from .catalog import Catalog
from .config import Env, Settings
from .gpu.aux import AuxManager
from .gpu.backend import GpuBackend
from .gpu.comfy import Comfy
from .gpu.energy import EnergyMeter
from .gpu.runner import MAIN, Runner
from .gpu.scheduler import PRIORITY_NAME, Job, Scheduler
from .llm.client import LlmClient

log = logging.getLogger("hub")


@dataclass
class Transports:
    """Injected in tests: fake llama-server / supervisor / ComfyUI / sandbox / speech service."""
    llm: httpx.AsyncBaseTransport | None = None
    runner: httpx.AsyncBaseTransport | None = None
    comfy: httpx.AsyncBaseTransport | None = None
    sandbox: httpx.AsyncBaseTransport | None = None
    web: httpx.AsyncBaseTransport | None = None
    speech: httpx.AsyncBaseTransport | None = None


@dataclass
class Hub:
    env: Env
    settings: Settings
    catalog: Catalog
    runner: Runner
    comfy: Comfy
    backend: GpuBackend
    scheduler: Scheduler
    llm: LlmClient
    aux: AuxManager
    transports: Transports
    extras: dict = field(default_factory=dict)

    @property
    def main_url(self) -> str:
        return self.runner.base_url(MAIN[1])

    def model_need(self, model_id: str) -> str:
        return f"llm:{model_id}"

    def loaded_model(self) -> str | None:
        cur = self.scheduler.current or ""
        return cur.split(":", 1)[1] if cur.startswith("llm:") else None


def _log_job(job: Job) -> None:
    try:
        with db.connect() as con:
            con.execute("INSERT INTO jobs_log (owner, kind, need, priority, label, enqueued_at, started_at,"
                        " ended_at, state, error, energy_wh) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (job.owner, job.kind, job.need, PRIORITY_NAME.get(job.priority, "?"), job.label[:200],
                         job.enqueued_at, job.started_at, job.ended_at, job.state, (job.error or "")[:500] or None,
                         round(job.energy_j / 3600, 4) if job.started_at is not None else None))
    except Exception:  # never let statistics break the queue
        log.exception("could not log job")


def build_hub(env: Env, transports: Transports | None = None) -> Hub:
    t = transports or Transports()
    env.data_dir.mkdir(parents=True, exist_ok=True)
    db.configure(env.db_path)
    settings = Settings(env.data_dir / "settings.json")
    catalog = Catalog(env.models_dir, env.data_dir / "models.override.json")
    runner = Runner(env.llm_url, env.internal_token, env.llm_host, transport=t.runner)
    comfy = Comfy(env.comfy_url, transport=t.comfy, files=env.comfy_files)
    backend = GpuBackend(runner, comfy, catalog)
    q = settings.get("queue")
    scheduler = Scheduler(backend, affinity_window=float(q.get("affinity_window_s", 20)),
                          aging_s=float(q.get("aging_s", 120)), on_finish=_log_job)
    llm = LlmClient(transport=t.llm)
    llm.on_unreachable = scheduler.suspect
    aux = AuxManager(runner, catalog)
    hub = Hub(env=env, settings=settings, catalog=catalog, runner=runner, comfy=comfy, backend=backend,
              scheduler=scheduler, llm=llm, aux=aux, transports=t)
    _meter(hub)
    return hub


def _meter(hub: Hub) -> None:
    """The card's energy (0.24.0, app/gpu/energy.py): measured along every job, counted into the usage when the job
    or its turn ends. Its readings start with the app (main.py)."""
    from . import usage
    meter = EnergyMeter(hub.scheduler, hub.runner.gpu)
    try:
        meter.other_w = max(0.0, float(hub.settings.get("costs", "other_watts") or 0))
    except (TypeError, ValueError):
        meter.other_w = 0.0
    meter.on_idle = lambda joules: usage.idle(hub, joules)
    hub.extras["energy"] = meter
    hub.scheduler.on_change = meter.changed

    def finished(job: Job) -> None:
        meter.finished(job)                    # its last interval first: then its energy is whole
        usage.job_finished(hub, job)
        _log_job(job)

    hub.scheduler.on_finish = finished


def get_hub(request: Request) -> Hub:
    return request.app.state.hub
