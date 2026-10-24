"""The GPU queue: order, slots, priorities, model switches, affinity, leases,
aging, cancellation and load failures - against a fake card."""
import asyncio

import pytest

from app.gpu.scheduler import QueueTimeout, Scheduler, SchedulerError


class FakeCard:
    def __init__(self, slots=None, fail=()):
        self.loaded = None
        self.log = []
        self._slots = slots or {}
        self.fail = set(fail)

    async def load(self, need):
        await asyncio.sleep(0)
        if need in self.fail:
            raise RuntimeError("boom")
        self.loaded = need
        self.log.append(("load", need))

    async def unload(self, need):
        await asyncio.sleep(0)
        self.loaded = None
        self.log.append(("unload", need))

    def slots(self, need):
        return self._slots.get(need, 2 if need.startswith("llm:") else 1)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


def sched(card=None, **kw):
    kw.setdefault("clock", Clock())
    return Scheduler(card or FakeCard(), **kw)


async def test_two_slots_then_wait():
    s = sched()
    t1 = await s.acquire("web", "chat", "llm:a")
    t2 = await s.acquire("fam", "chat", "llm:a")
    third = asyncio.create_task(s.acquire("fam", "chat", "llm:a"))
    await settle()
    assert not third.done()
    assert len(s.running) == 2 and len(s.pending) == 1
    await t1.release()
    t3 = await asyncio.wait_for(third, 1)
    await t2.release()
    await t3.release()
    assert s.backend.log == [("load", "llm:a")]


async def test_priority_beats_arrival_when_card_is_full():
    s = sched(FakeCard(slots={"llm:a": 1}))
    busy = await s.acquire("x", "chat", "llm:a")
    bg = asyncio.create_task(s.acquire("idx", "embed", "llm:a", "background"))
    await settle()
    s.clock.t += 1
    web = asyncio.create_task(s.acquire("web", "agent", "llm:a", "interactive"))
    await settle()
    await busy.release()
    first = await asyncio.wait_for(asyncio.wait({bg, web}, return_when=asyncio.FIRST_COMPLETED), 1)
    done, _ = first
    assert web in done and bg not in done
    await web.result().release()
    await (await asyncio.wait_for(bg, 1)).release()


async def test_switch_waits_for_running_jobs_then_unloads():
    card = FakeCard()
    s = sched(card, affinity_window=0)
    a = await s.acquire("web", "chat", "llm:a")
    b_task = asyncio.create_task(s.acquire("fam", "chat", "llm:b"))
    await settle()
    assert not b_task.done() and card.loaded == "llm:a"
    await a.release()
    b = await asyncio.wait_for(b_task, 1)
    assert card.log == [("load", "llm:a"), ("unload", "llm:a"), ("load", "llm:b")]
    assert s.current == "llm:b"
    await b.release()


async def test_affinity_lets_same_model_work_use_a_free_slot():
    clock = Clock()
    s = sched(clock=clock, affinity_window=20, aging_s=0)
    a1 = await s.acquire("web", "chat", "llm:a")
    b_task = asyncio.create_task(s.acquire("fam", "chat", "llm:b"))
    await settle()
    clock.t += 1
    a2_task = asyncio.create_task(s.acquire("fam", "chat", "llm:a"))
    await settle()
    assert a2_task.done(), "inside the window a same-model job takes the free slot"
    a2 = a2_task.result()
    await a2.release()
    clock.t += 30                      # window over: nothing new for llm:a starts
    a3_task = asyncio.create_task(s.acquire("fam", "chat", "llm:a"))
    await settle()
    assert not a3_task.done()
    await a1.release()
    b = await asyncio.wait_for(b_task, 1)
    assert s.current == "llm:b"
    await b.release()
    a3 = await asyncio.wait_for(a3_task, 1)
    await a3.release()


async def test_lease_lets_same_model_calls_in_and_switches_to_comfy():
    card = FakeCard()
    clock = Clock()
    s = sched(card, clock=clock, affinity_window=0, aging_s=0)
    lease = await s.acquire("web", "agent", "llm:a", "interactive", lease=True)
    # another model waits (window 0) ...
    b_task = asyncio.create_task(s.acquire("fam", "chat", "llm:b"))
    await settle()
    # ... but a call for the loaded model uses slot 2 while the lease holds the card
    fam = await asyncio.wait_for(s.acquire("fam", "chat", "llm:a"), 1)
    # the lease wants an image: waits for slot 2 to finish
    sw = asyncio.create_task(lease.switch("comfy"))
    await settle()
    assert not sw.done() and card.loaded == "llm:a"
    await fam.release()
    await asyncio.wait_for(sw, 1)
    assert card.loaded == "comfy" and lease.need == "comfy"
    assert not b_task.done()
    await lease.switch("llm:a")
    assert card.loaded == "llm:a"
    await lease.release()
    b = await asyncio.wait_for(b_task, 1)
    assert card.loaded == "llm:b"
    await b.release()


async def test_aging_lets_an_old_background_job_through():
    clock = Clock()
    s = sched(FakeCard(slots={"llm:a": 1}), clock=clock, aging_s=60)
    busy = await s.acquire("x", "chat", "llm:a")
    old_bg = asyncio.create_task(s.acquire("idx", "embed", "llm:a", "background"))
    await settle()
    clock.t += 130                     # two levels: background -> interactive
    new_normal = asyncio.create_task(s.acquire("fam", "chat", "llm:a", "normal"))
    await settle()
    await busy.release()
    done, _ = await asyncio.wait({old_bg, new_normal}, return_when=asyncio.FIRST_COMPLETED, timeout=1)
    assert old_bg in done and new_normal not in done
    await old_bg.result().release()
    await (await asyncio.wait_for(new_normal, 1)).release()


async def test_cancel_a_waiting_job():
    s = sched(FakeCard(slots={"llm:a": 1}))
    busy = await s.acquire("x", "chat", "llm:a")
    w = asyncio.create_task(s.acquire("fam", "chat", "llm:a"))
    await settle()
    w.cancel()
    await settle()
    assert w.cancelled() and not s.pending
    await busy.release()
    assert not s.running


async def test_load_failure_fails_that_job_only():
    card = FakeCard(fail={"llm:bad"})
    s = sched(card, affinity_window=0)
    with pytest.raises(SchedulerError):
        await s.acquire("fam", "chat", "llm:bad")
    ok = await asyncio.wait_for(s.acquire("fam", "chat", "llm:a"), 1)
    assert s.current == "llm:a"
    await ok.release()


async def test_max_wait_gives_up():
    s = sched(FakeCard(slots={"llm:a": 1}))
    busy = await s.acquire("x", "chat", "llm:a")
    with pytest.raises(QueueTimeout):
        await s.acquire("fam", "chat", "llm:a", max_wait=0.05)
    assert not s.pending
    await busy.release()


async def test_snapshot_shows_positions_and_owners():
    s = sched(FakeCard(slots={"llm:a": 1}))
    busy = await s.acquire("web", "agent", "llm:a", "interactive", label="Chat 1")
    w = asyncio.create_task(s.acquire("familylog", "chat", "llm:a"))
    await settle()
    snap = s.snapshot()
    assert snap["current"] == "llm:a"
    assert snap["running"][0]["owner"] == "web" and snap["running"][0]["label"] == "Chat 1"
    assert snap["pending"][0]["owner"] == "familylog" and snap["pending"][0]["position"] == 1
    await busy.release()
    await (await asyncio.wait_for(w, 1)).release()


async def test_release_twice_is_harmless_and_context_manager_releases():
    s = sched()
    async with await s.acquire("web", "chat", "llm:a") as t:
        assert t.need == "llm:a"
    await t.release()
    assert not s.running


async def test_two_leases_that_both_want_comfy_do_not_wait_on_each_other():
    card = FakeCard()
    s = sched(card)
    a = await s.acquire("web", "agent", "llm:a", "interactive", lease=True)
    b = await s.acquire("web", "agent", "llm:a", "interactive", lease=True)
    fam = asyncio.create_task(s.acquire("fam", "chat", "llm:a"))
    sa = asyncio.create_task(a.switch("comfy"))
    await settle()
    sb = asyncio.create_task(b.switch("comfy"))
    await asyncio.wait_for(sa, 1)                    # a gets ComfyUI (one slot) ...
    await settle()
    assert s.current == "comfy" and not sb.done()    # ... b waits for that slot
    back = asyncio.create_task(a.switch("llm:a"))    # a is done with its image
    await asyncio.wait_for(sb, 1)                    # now b renders
    await settle()
    assert not back.done()
    await b.switch("llm:a")                          # b is done too: both back on the model
    await asyncio.wait_for(back, 1)
    assert s.current == "llm:a" and a.job.need == b.job.need == "llm:a"
    await a.release()
    await b.release()
    t = await asyncio.wait_for(fam, 1)              # the app's call was never lost
    await t.release()
    assert card.log.count(("load", "comfy")) == 1


async def test_a_lease_switching_to_a_full_residency_waits_for_a_slot():
    s = sched()
    a = await s.acquire("web", "agent", "llm:a", "interactive", lease=True)
    await a.switch("comfy")
    # A second lease on the model wants ComfyUI while a is still rendering.
    c = asyncio.create_task(s.acquire("web", "agent", "llm:a", "interactive", lease=True))
    await settle()
    assert not c.done()                               # the card is on ComfyUI for a's lease
    back = asyncio.create_task(a.switch("llm:a"))
    tc = await asyncio.wait_for(c, 1)
    await asyncio.wait_for(back, 1)
    sw = asyncio.create_task(tc.switch("comfy"))
    sa = asyncio.create_task(a.switch("comfy"))
    await settle(50)
    done = [x for x in (sw, sa) if x.done()]
    assert len(done) == 1 and s.current == "comfy"   # one slot: one of them renders
    await (tc if sw.done() else a).release()
    await asyncio.wait_for(sa if sw.done() else sw, 1)
    await a.release()
    await tc.release()


async def test_a_crashed_model_is_noticed_and_loaded_again():
    card = FakeCard()
    card.dead = set()

    async def alive(need):
        return need not in card.dead
    card.alive = alive
    s = sched(card)
    t = await s.acquire("fam", "chat", "llm:a")
    await t.release()
    assert card.log == [("load", "llm:a")]
    card.dead.add("llm:a")                            # llama-server died (OOM)
    s.suspect()                                       # a client could not reach it
    t = await asyncio.wait_for(s.acquire("fam", "chat", "llm:a"), 1)
    await t.release()
    assert card.log == [("load", "llm:a"), ("load", "llm:a")]      # noticed, loaded again, served


async def test_a_paused_lease_frees_the_card_and_queues_again():
    """A turn waiting for the user's answer gives the card up (Ticket.pause): an image job switches to ComfyUI and
    runs; the turn's resume() waits for it, switches back, and the same ticket works on."""
    card = FakeCard()
    s = sched(card)
    turn = await s.acquire("web", "agent", "llm:a", "interactive", lease=True, meta={"chat_id": 7})
    first = turn.job
    await turn.pause()
    assert turn.paused and not s.running and first.state == "done"
    await turn.pause()                                     # twice: nothing more
    image = await asyncio.wait_for(s.acquire("web", "image", "comfy"), 1)
    back = asyncio.create_task(turn.resume())
    await settle()
    assert not back.done() and card.loaded == "comfy"     # the image job has the card
    await image.release()
    await asyncio.wait_for(back, 1)
    assert not turn.paused and turn.job is not first and turn.job.state == "running"
    assert turn.job.lease and turn.job.meta == {"chat_id": 7, "resumed": True} and turn.job.priority == 0
    assert card.log == [("load", "llm:a"), ("unload", "llm:a"), ("load", "comfy"), ("unload", "comfy"),
                        ("load", "llm:a")]
    await turn.release()
    assert not s.running
    await turn.resume()                                    # released: resume does nothing
    assert not s.running and not s.pending


async def test_a_turn_stopped_while_it_queues_again_leaves_nothing():
    s = sched()
    turn = await s.acquire("web", "agent", "llm:a", lease=True)
    await turn.pause()
    other = await s.acquire("web", "image", "comfy")
    back = asyncio.create_task(turn.resume())
    await settle()
    back.cancel()
    with pytest.raises(asyncio.CancelledError):
        await back
    await turn.release()
    await other.release()
    assert not s.running and not s.pending
