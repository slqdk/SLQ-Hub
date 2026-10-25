"""0.24.0 - what the card's work costs: its own power reading split between the jobs on it, each answer's energy
and cost, a chat's and a project's totals, and the usage of each one (the owner, each person, each app, tests,
loading, idle) per day for Settings -> Usage."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app import db, usage
from app.gpu.energy import MAX_GAP_S, EnergyMeter
from app.gpu.scheduler import Job

from .test_agent import finish, new_chat, say
from .test_people import add_person, client_from, sign_in, turn, user_msg

ORIGIN = {"Origin": "http://hub"}
ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- the reading

def supervisor():
    spec = importlib.util.spec_from_file_location("slq_supervisor", ROOT / "services" / "llm" / "supervisor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def card(root: Path, name: str, files: dict[str, str]) -> None:
    dev = root / name / "device"
    for rel, text in {"mem_info_vram_total": str(24 * 1024**3), "mem_info_vram_used": str(2 * 1024**3),
                      **files}.items():
        (dev / rel).parent.mkdir(parents=True, exist_ok=True)
        (dev / rel).write_text(text + "\n")


def test_the_supervisor_reads_the_cards_own_power(tmp_path):
    sup = supervisor()
    # as Søren's RX 7900 XTX on VM 222 (2026-10-25, while it built code): power1_average, no power1_input
    card(tmp_path / "a", "card1", {"hwmon/hwmon3/power1_average": "337000000", "hwmon/hwmon3/power1_cap": "339000000",
                                   "hwmon/hwmon3/power1_cap_default": "339000000",
                                   "hwmon/hwmon3/power1_cap_max": "350000000",
                                   "hwmon/hwmon3/power1_cap_min": "305000000", "hwmon/hwmon3/power1_label": "PPT"})
    g = sup.gpu(str(tmp_path / "a"))
    assert g["vram_total"] == 24 * 1024**3
    assert (g["power_w"], g["power_cap_w"], g["power_source"]) == (337.0, 339.0, "power1_average")
    card(tmp_path / "b", "card0", {"hwmon/hwmon1/power1_input": "45500000"})          # a card with only _input
    g = sup.gpu(str(tmp_path / "b"))
    assert (g["power_w"], g["power_source"]) == (45.5, "power1_input") and "power_cap_w" not in g
    card(tmp_path / "c", "card0", {})                                                  # no reading at all
    assert "power_w" not in sup.gpu(str(tmp_path / "c"))


# ---------------------------------------------------------------- the meter

class Sched:
    def __init__(self):
        self.running: dict[int, Job] = {}
        self.pending: list[Job] = []
        self.switching: str | None = None


def job(i: int, need: str = "llm:a", run_id: str | None = None) -> Job:
    return Job(id=i, owner="web", kind="agent", need=need, priority=0, label="", lease=True, enqueued_at=0.0,
               meta={"run_id": run_id} if run_id else {})


async def test_the_meter_splits_the_cards_energy_between_the_jobs_on_it():
    now = [0.0]
    readings = [{"power_w": 200.0, "power_cap_w": 339.0, "power_source": "power1_average"}]
    s = Sched()

    async def read():
        return readings[0]

    m = EnergyMeter(s, read, clock=lambda: now[0])
    idle: list[float] = []
    m.on_idle = idle.append
    m.power = 300.0
    a, b = job(1, run_id="r1"), job(2, run_id="r2")
    a.started_at = 0.0
    s.running[1] = a
    m.changed()
    now[0] = 10.0                                    # A alone for 10 s: 3000 J
    s.running[2] = b
    m.changed()
    now[0] = 14.0                                    # both for 4 s: 600 J each
    a.ended_at = 14.0
    del s.running[1]
    m.finished(a)
    m.changed()
    assert (a.energy_j, b.energy_j) == (3600.0, 600.0)
    assert m.take_run("r1") == (1.0, 14.0) and m.take_run("r1") == (0.0, 0.0)     # Wh and seconds, once
    m.other_w = 50.0                                 # the rest of the PC while the card works
    now[0] = 16.0
    await m.sample()                                 # the fresh reading (200 W) for the 2 s just ended
    assert b.energy_j == 600.0 + (200 + 50) * 2 and m.cap == 339.0
    del s.running[2]
    m.finished(b)
    m.changed()                                      # idle now: counted apart, without the rest of the PC
    readings[0] = {"power_w": 30.0}
    now[0] = 76.0
    await m.sample()
    assert idle == [30.0 * 60] and m.idle_j == 0
    s.switching = "llm:b"                            # the card loads for a waiting job: that job's energy
    c = job(3, need="llm:b")
    s.pending.append(c)
    m.changed()
    now[0] = 81.0
    m.changed()
    assert c.energy_j == (30 + 50) * 5
    readings[0] = {}                                 # no reading: nothing is counted
    now[0] = 90.0
    await m.sample()
    assert m.power is None and c.energy_j == 400.0
    m.power = 300.0                                  # a reading that hung: at most MAX_GAP_S
    now[0] = 500.0
    m.close()
    assert c.energy_j == 400.0 + 350 * MAX_GAP_S


# ---------------------------------------------------------------- answers, chats, projects

def ticking(app):
    """The meter at 300 W, its clock a second further at each look: every interval a job has counts."""
    m = app.state.hub.extras["energy"]
    m.power = 300.0
    t = [m.last_t]

    def tick() -> float:
        t[0] += 1.0
        return t[0]

    m.clock = tick
    return m


async def test_an_answer_shows_its_energy_and_cost_and_the_day_counts_it(signed_in, app, fakes):
    ticking(app)
    fakes.llama["replies"] = [{"content": "Hej."}, {"content": "Greeting"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "hej"))
    run = (await signed_in.get(f"/hub/api/chats/{c['id']}")).json()["runs"][0]
    u = run["usage"]
    assert u["energy_wh"] > 0 and u["gpu_seconds"] >= 0
    assert abs(u["cost"] - u["energy_wh"] * 3.0 / 1000) < 1e-6                   # 3 kr per kWh
    assert (u["prompt_tokens"], u["completion_tokens"], u["cached_tokens"]) == (12, 7, 3)
    r = (await signed_in.get("/hub/api/usage", params={"period": "today"})).json()
    me = next(x for x in r["rows"] if x["who"] == "owner")
    assert (me["label"], me["kind"], me["turns"]) == ("Soren", "owner", 1)
    assert (me["tokens_in"], me["tokens_out"], me["tokens_cached"]) == (9, 7, 3)  # read, written, from the cache
    assert abs(me["energy_wh"] - u["energy_wh"]) < 1e-3 and r["price_per_kwh"] == 3.0
    assert r["power"] == {"now_w": 280.0, "cap_w": 339.0, "source": "power1_average"}   # read for the page
    with db.connect() as con:                                                     # and the queue log has it
        logged = con.execute("SELECT energy_wh FROM jobs_log WHERE kind='agent'").fetchone()["energy_wh"]
    assert logged > 0


async def test_a_projects_total_stays_when_its_chats_go(signed_in, app, fakes):
    ticking(app)
    p = (await signed_in.post("/hub/api/projects", json={"name": "Garage"})).json()
    fakes.llama["replies"] = [{"content": "Done."}, {"content": "T"}]
    c = await new_chat(signed_in, project_id=p["id"])
    await finish(app, await say(signed_in, c["id"], "go"))
    await finish(app, await say(signed_in, c["id"], "again"))
    chats = (await signed_in.get("/hub/api/chats", params={"project_id": p["id"]})).json()
    assert chats[0]["energy_wh"] > 0 and abs(chats[0]["cost"] - chats[0]["energy_wh"] * 3 / 1000) < 1e-5
    total = (await signed_in.get(f"/hub/api/projects/{p['id']}")).json()["usage"]
    assert total["turns"] == 2 and abs(total["energy_wh"] - chats[0]["energy_wh"]) < 1e-3
    assert total["since"] == usage.today()
    plain = await new_chat(signed_in)                                             # the sidebar's list stays plain
    assert "energy_wh" not in next(x for x in (await signed_in.get("/hub/api/chats")).json() if x["id"] == plain["id"])
    await signed_in.delete(f"/hub/api/chats/{c['id']}")
    after = (await signed_in.get(f"/hub/api/projects/{p['id']}")).json()["usage"]
    assert after["turns"] == 2 and after["energy_wh"] == total["energy_wh"]


async def test_the_price_and_the_rest_of_the_pc_are_settings(signed_in, app, fakes):
    m = ticking(app)
    r = await signed_in.put("/hub/api/settings", json={"section": "costs", "values": {"price_per_kwh": "2.5",
                                                                                     "other_watts": 60}})
    assert r.status_code == 200 and r.json()["settings"]["costs"] == {"price_per_kwh": 2.5, "other_watts": 60.0}
    assert m.other_w == 60.0
    assert (await signed_in.put("/hub/api/settings", json={"section": "costs", "values": {
        "price_per_kwh": "lots"}})).status_code == 400
    await signed_in.put("/hub/api/settings", json={"section": "costs", "values": {"other_watts": -5}})
    assert m.other_w == 0.0                                                       # not below zero
    fakes.llama["replies"] = [{"content": "Ok."}, {"content": "T"}]
    c = await new_chat(signed_in)
    await finish(app, await say(signed_in, c["id"], "hi"))
    u = (await signed_in.get(f"/hub/api/chats/{c['id']}")).json()["runs"][0]["usage"]
    assert abs(u["cost"] - u["energy_wh"] * 2.5 / 1000) < 1e-6


# ---------------------------------------------------------------- apps, people, idle

async def test_an_apps_calls_count_for_its_key(client, api_key, app, fakes):
    ticking(app)
    h = {"Authorization": f"Bearer {api_key}"}
    fakes.llama["replies"] = [{"content": "Hi from the model."}]
    r = await client.post("/v1/chat/completions", headers=h, json={"model": "qwen3.8:27b", "messages": [
        {"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    r = await client.post("/v1/chat/completions", headers=h, json={"model": "qwen3.8:27b", "stream": True,
                                                                   "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and "[DONE]" in r.text
    r = await client.post("/api/chat", headers=h, json={"model": "qwen3.8:27b", "stream": False,
                                                        "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    r = await client.post("/v1/messages", headers={"x-api-key": api_key}, json={
        "model": "qwen3.8:27b", "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    await app.state.hub.scheduler.drain()
    rows = {x["who"]: x for x in usage.totals("today")}
    key = rows["key:familylog"]
    assert (key["jobs"], key["turns"]) == (4, 0)
    assert (key["tokens_in"], key["tokens_out"], key["tokens_cached"]) == (36, 28, 12)
    assert key["energy_wh"] > 0 and key["seconds"] >= 0
    assert usage.label(app.state.hub, "key:familylog") == ("familylog", "app")


async def test_a_persons_answers_count_for_her_and_only_the_owner_sees_the_page(signed_in, app, fakes):
    ticking(app)
    maria = await add_person(signed_in)
    fakes.llama["replies"] = [{"content": "Hej Maria."}, {"content": "Hilsen"}]
    async with client_from(app) as phone:
        await sign_in(phone)
        res = await turn(app, phone, {"title": "New chat", "messages": [user_msg("hej")]})
        u = res["usage"]
        assert u["energy_wh"] > 0 and abs(u["cost"] - u["energy_wh"] * 3 / 1000) < 1e-6   # kept on her device
        assert (await phone.get("/hub/api/usage")).status_code == 403
    r = (await signed_in.get("/hub/api/usage", params={"period": "month"})).json()
    rows = {x["who"]: x for x in r["rows"]}
    her = rows[f"user:{maria['id']}"]
    assert (her["label"], her["kind"], her["turns"], her["tokens_out"]) == ("Maria", "person", 1, 7)
    assert rows["owner"]["turns"] == 0                                            # the owner listed, unused
    assert usage.who_total(f"user:{maria['id']}", "today")["turns"] == 1          # what a limit would read
    await signed_in.delete(f"/hub/api/people/{maria['id']}", headers=ORIGIN)
    gone = {x["who"]: x for x in (await signed_in.get("/hub/api/usage")).json()["rows"]}
    assert gone[f"user:{maria['id']}"]["label"] == "A removed person"             # her numbers stay
    assert (await signed_in.get("/hub/api/usage", params={"period": "year"})).status_code == 400


async def test_the_card_idle_and_loading_have_rows_of_their_own(signed_in, app, fakes):
    hub = app.state.hub
    hub.extras["energy"].on_idle(7200.0)                                          # 2 Wh while nobody used it
    t = await hub.scheduler.acquire("system", "preload", "llm:qwen3.8-27b", "background", label="preload")
    await t.release()
    rows = {x["who"]: x for x in (await signed_in.get("/hub/api/usage", params={"period": "all"})).json()["rows"]}
    idle = rows["idle"]                              # (and the moment since the last look, read for the page)
    assert idle["label"] == "The card, idle" and 2.0 <= idle["energy_wh"] < 2.01 and 0.006 <= idle["cost"] < 0.00603
    assert rows["system"]["label"] == "Loading models" and rows["system"]["jobs"] == 1


@pytest.mark.parametrize("who,owner,kind,meta", [
    ("owner", "web", "image", {}), ("key:FamilyLog", "FamilyLog", "api", {}), ("user:4", "Maria", "agent",
                                                                                {"user_id": 4}),
    ("library", "library", "index", {}), ("system", "web", "load", {}), ("tests", "tests", "agent", {})])
def test_who_a_job_counts_for(who, owner, kind, meta):
    j = Job(id=1, owner=owner, kind=kind, need="comfy", priority=0, label="", lease=False, enqueued_at=0.0, meta=meta)
    assert usage.who_of_job(j) == who
