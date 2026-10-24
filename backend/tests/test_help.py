"""0.20.0 - the help page: what works on this Hub now (GET /hub/api/help), for the owner and for a person, and the
help text itself (frontend/src/lib/help.ts) kept in step with the tools and with what the endpoint says."""
from __future__ import annotations

import re
from pathlib import Path

from app.agent.tools import REGISTRY

from .test_audio import install
from .test_people import add_person, client_from, sign_in

HELP = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "help.ts"


async def test_the_owner_learns_what_works_here(signed_in, app, fakes):
    r = await signed_in.get("/hub/api/help")
    assert r.status_code == 200, r.text
    h = r.json()
    assert (h["member"], h["code"], h["memory"], h["projects"], h["windows"], h["internet_asks"]) == \
        (False, True, True, True, False, False)
    assert h["images"] == {"generate": True, "edit": True, "cutout": True}         # Qwen-Image 2.1 (conftest)
    assert h["sound"] == dict.fromkeys(("speech", "music", "sound", "voice", "separate"), False)
    assert h["video"] == {"t2v": False, "i2v": False, "flf2v": False}
    assert h["handover"] is True                                                   # Qwen3.8 27B by default
    assert h["libraries"] is True and h["mcp"] == 0
    install(app, "wan2.2-5b", "roest-v3", "ace-step-1.5-turbo")
    fakes.sandbox["windows"] = True
    app.state.hub.extras.pop("windows_app", None)                                  # asked again, not cached
    await signed_in.put("/hub/api/settings", json={"section": "web", "values": {"access": "ask"}})
    await signed_in.put("/hub/api/settings", json={"section": "agent", "values": {"escalate_to": ""}})
    h = (await signed_in.get("/hub/api/help")).json()
    assert h["video"] == {"t2v": True, "i2v": True, "flf2v": False}
    assert (h["sound"]["speech"], h["sound"]["music"], h["sound"]["voice"]) == (True, True, False)
    assert (h["windows"], h["internet_asks"], h["handover"]) == (True, True, False)


async def test_a_person_learns_what_works_in_her_chats(signed_in, app, fakes):
    await add_person(signed_in)                                   # no code
    async with client_from(app) as phone:
        await sign_in(phone)
        h = (await phone.get("/hub/api/help")).json()
    assert h["member"] is True
    assert (h["code"], h["memory"], h["projects"], h["libraries"], h["windows"], h["mcp"]) == \
        (False, False, False, False, False, 0)
    assert h["images"]["generate"] is True
    await add_person(signed_in, username="per", may_run_code=True)
    async with client_from(app) as phone:
        await sign_in(phone, "per")
        assert (await phone.get("/hub/api/help")).json()["code"] is True      # the sandbox has Landlock
    fakes.sandbox["landlock"] = 0
    app.state.hub.extras["device"]._confine = (float("-inf"), False)
    async with client_from(app) as phone:
        await sign_in(phone, "per")
        assert (await phone.get("/hub/api/help")).json()["code"] is False     # as her turns would decide


def _help_text() -> str:
    return HELP.read_text(encoding="utf-8")


def test_every_tool_is_in_the_help():
    """A tool added without a word in the help is caught here: each tool is named in some item's tools."""
    from app.agent.loop import _ensure_tools_loaded
    from app.rag import tools as _rag  # noqa: F401 - registers library_search
    _ensure_tools_loaded()
    named = {n for group in re.findall(r"tools: \[([^\]]*)\]", _help_text()) for n in re.findall(r'"([a-z_]+)"', group)}
    assert set(REGISTRY) - named == set(), "tools the help does not mention"
    assert named - set(REGISTRY) == set(), "the help names tools that do not exist"


async def test_every_need_in_the_help_is_answered(signed_in):
    """The help's Need names (help.ts) are fields of /hub/api/help, and each has its text in NEEDS."""
    text = _help_text()
    union = re.search(r"export type Need = ([^;]+);", text).group(1)
    needs = re.findall(r'"([a-z.0-9]+)"', union)
    assert len(needs) == 16
    h = (await signed_in.get("/hub/api/help")).json()
    for n in needs:
        group, _, key = n.partition(".")
        assert group in h and (not key or key in h[group]), n
        assert re.search(rf'^\s+"{re.escape(n)}": \{{ missing: ', text, re.M), f"no NEEDS text for {n}"
    used = set(re.findall(r'needs: \[([^\]]*)\]', text))
    for group in used:
        for n in re.findall(r'"([a-z.0-9]+)"', group):
            assert n in needs, n
