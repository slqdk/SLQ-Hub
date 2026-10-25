"""What update.sh asks before it stops the Hub (0.26.0): is anybody using the card or the Hub right now?"""
from __future__ import annotations

import asyncio
import time
import types

import httpx

from app.ui.update_routes import DEVICE_FETCH_S, busy_reasons

TOKEN = {"X-Internal-Token": "tok"}
NEED = "llm:qwen3.8-27b"


async def test_only_the_updater_on_the_vm_may_ask(client, app):
    assert (await client.get("/hub/api/internal/busy")).status_code == 403
    assert (await client.get("/hub/api/internal/busy", headers={"X-Internal-Token": "wrong"})).status_code == 403
    r = await client.get("/hub/api/internal/busy", headers=TOKEN)
    assert r.status_code == 200, r.text
    assert r.json() == {"busy": False, "why": [], "version": app.state.hub.env.version}
    proxy = httpx.ASGITransport(app=app, client=("10.9.9.9", 4000))          # through the reverse proxy
    async with httpx.AsyncClient(transport=proxy, base_url="http://hub") as away:
        r = await away.get("/hub/api/internal/busy", headers={**TOKEN, "X-Forwarded-For": "30.11.0.7"})
        assert r.status_code == 403


async def test_work_on_the_card_and_work_waiting_for_it(client, app):
    hub = app.state.hub
    first = await hub.scheduler.acquire("web", "agent", NEED, "interactive", label="Kan du lave et spil?",
                                        meta={"chat_id": 5})
    second = asyncio.get_running_loop().create_task(
        hub.scheduler.acquire("library", "index", "llm:qwen3.6-35b-a3b", "background", label="indexing"))
    await asyncio.sleep(0.05)
    r = (await client.get("/hub/api/internal/busy", headers=TOKEN)).json()
    assert r["busy"] and r["why"] == ["a chat is working on the card (Kan du lave et spil?)", "1 job waits for the card"]
    await first.release()
    t = await asyncio.wait_for(second, 5)
    assert busy_reasons(hub) == ["library indexing is working on the card (indexing)"]
    await t.release()
    assert busy_reasons(hub) == []


async def test_a_chat_between_two_steps_or_asking_the_user(app):
    """A turn that is not on the card right now - between its steps, or waiting for an approval - is still going."""
    hub = app.state.hub
    run = hub.extras["agent"].runs.create(7)
    run.status = "running"
    assert busy_reasons(hub) == ["a chat is answering"]
    run.approvals["call_1"] = asyncio.get_running_loop().create_future()
    assert busy_reasons(hub) == ["a chat is answering and waits for an answer to its question"]
    run.approvals.clear()
    t = await hub.scheduler.acquire("web", "agent", NEED, "interactive", label="Chat", meta={"chat_id": 7})
    assert busy_reasons(hub) == ["a chat is working on the card (Chat)"]          # said once, not twice
    await t.release()
    run.status = "done"
    assert busy_reasons(hub) == []


async def test_a_turn_whose_task_died_is_not_waited_for(app):
    """Its status never became final, but nothing can finish it now: the update would otherwise wait forever."""
    hub = app.state.hub
    run = hub.extras["agent"].runs.create(8)
    run.status = "running"
    run.task = asyncio.get_running_loop().create_task(asyncio.sleep(0))
    assert busy_reasons(hub) == ["a chat is answering"]
    await run.task
    assert busy_reasons(hub) == []
    person = hub.extras["agent"].runs.create(None)
    person.task = run.task
    hub.extras["device"].turns["run_b"] = types.SimpleNamespace(user={"id": 4, "display_name": "Sanne"}, ended=None,
                                                                run=person)
    assert busy_reasons(hub) == []


async def test_a_persons_chat_and_their_device_fetching_the_answer(app):
    hub = app.state.hub
    turns = hub.extras["device"].turns
    turns["run_a"] = types.SimpleNamespace(user={"id": 3, "display_name": "Maria"}, ended=None)
    assert busy_reasons(hub) == ["Maria's chat is answering"]
    turns["run_a"].ended = time.time() - 5
    assert busy_reasons(hub) == ["Maria's device is fetching an answer"]
    turns["run_a"].ended = time.time() - DEVICE_FETCH_S - 1
    assert busy_reasons(hub) == []


async def test_a_test_run_a_library_and_downloads(app, monkeypatch):
    hub = app.state.hub
    monkeypatch.setattr(hub.extras["tests"], "running", lambda: "run_tests")
    assert busy_reasons(hub) == ["a test run is going (Settings -> Tests)"]
    monkeypatch.setattr(hub.extras["tests"], "running", lambda: None)
    hub.extras["libraries"].current = {"id": 3, "filename": "manual.pdf"}       # indexing on the CPU: no job
    assert busy_reasons(hub) == ["a library is reading manual.pdf"]
    hub.extras["libraries"].current = None
    downloads = hub.extras["downloads"]
    downloads.state["llm/a.gguf"] = {"model": "qwen3.8-27b", "status": "downloading"}
    downloads.state["llm/b.gguf"] = {"model": "qwen3.6-35b-a3b", "status": "queued"}
    downloads.state["llm/c.gguf"] = {"model": "embed-text", "status": "done"}
    assert busy_reasons(hub) == []                  # nothing is downloading them: no reason to wait (for ever)
    downloads._task = asyncio.get_running_loop().create_task(asyncio.sleep(5))
    try:
        assert busy_reasons(hub) == ["downloading qwen3.6-35b-a3b, qwen3.8-27b"]
        for st in downloads.state.values():
            st["status"] = "done"
        assert busy_reasons(hub) == []
    finally:
        downloads._task.cancel()
