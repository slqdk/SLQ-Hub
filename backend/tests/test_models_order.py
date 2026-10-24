"""The owner's order for chat and picture models (Settings -> Models, 0.17.0): the chat's model choices, people's
chats and the phone page list them so. Models the order does not name come after the ones it does, in the
catalog's order - a model added later goes last."""
from __future__ import annotations

import json

from app.catalog import Catalog

from .test_people import add_person, client_from, sign_in


def _add_llm(hub, model_id: str, name: str) -> None:
    dest = f"llm/hf/test/{model_id}.gguf"
    (hub.env.models_dir / dest).parent.mkdir(parents=True, exist_ok=True)
    (hub.env.models_dir / dest).write_bytes(b"GGUF")
    hub.catalog.add_custom({"id": model_id, "name": name, "description": "Added from Hugging Face: test.",
                            "files": [{"dest": dest, "size": 4}], "server": {"ctx": 8192, "parallel": 1},
                            "capabilities": ["chat"]})


def _ids(listing: list[dict]) -> list[str]:
    return [e["id"] for e in listing]


async def _order(client, ids: list, group: str = "llm"):
    return await client.put("/hub/api/models/order", json={"group": group, "ids": ids})


async def test_the_owner_orders_the_chat_models(signed_in, app):
    hub = app.state.hub
    _add_llm(hub, "ornith-1.5-9b", "Ornith 1.5 9B")
    first = ["qwen3.8-27b", "qwen3.6-35b-a3b", "ornith-1.5-9b"]
    assert _ids((await signed_in.get("/hub/api/models")).json()["llm"]) == first      # the catalog's: bundled, added
    mine = ["ornith-1.5-9b", "qwen3.8-27b", "qwen3.6-35b-a3b"]
    r = await _order(signed_in, mine)
    assert r.status_code == 200, r.text
    assert _ids(r.json()["llm"]) == mine
    assert _ids((await signed_in.get("/hub/api/models")).json()["llm"]) == mine
    # what apps and the phone page list follows it, among the downloaded models
    v1 = [e["id"] for e in (await signed_in.get("/v1/models")).json()["data"] if e["id"] in mine]
    tags = [t["name"] for t in (await signed_in.get("/api/tags")).json()["models"] if t["name"] in mine]
    assert v1 and v1 == [i for i in mine if i in v1] and tags == [i for i in mine if i in tags]
    # it survives a restart, in the overrides file beside the names
    assert _ids(Catalog(hub.env.models_dir, hub.catalog.override_path).llm_models()) == mine
    assert json.loads(hub.catalog.override_path.read_text())["order"]["llm"] == mine
    # a model added later goes last; one not named keeps its place after the named ones
    _add_llm(hub, "qwen3.5-9b", "Qwen3.5 9B")
    assert _ids(hub.catalog.llm_models()) == mine + ["qwen3.5-9b"]
    await _order(signed_in, ["qwen3.6-35b-a3b"])
    assert _ids(hub.catalog.llm_models()) == ["qwen3.6-35b-a3b", "qwen3.8-27b", "ornith-1.5-9b", "qwen3.5-9b"]
    # a removed model leaves the order too
    await _order(signed_in, ["qwen3.5-9b", "ornith-1.5-9b", "qwen3.8-27b", "qwen3.6-35b-a3b"])
    hub.catalog.remove_custom("ornith-1.5-9b")
    assert json.loads(hub.catalog.override_path.read_text())["order"]["llm"] == ["qwen3.5-9b", "qwen3.8-27b",
                                                                                 "qwen3.6-35b-a3b"]
    assert _ids(hub.catalog.llm_models()) == ["qwen3.5-9b", "qwen3.8-27b", "qwen3.6-35b-a3b"]
    # renaming and other settings keep the order, and the order keeps them
    await signed_in.put("/hub/api/models/qwen3.8-27b/name", json={"name": "Store hjerne"})
    await signed_in.put("/hub/api/models/qwen3.8-27b", json={"thinking_mode": "on"})
    listed = (await signed_in.get("/hub/api/models")).json()["llm"]
    assert _ids(listed) == ["qwen3.5-9b", "qwen3.8-27b", "qwen3.6-35b-a3b"] and listed[1]["name"] == "Store hjerne"


async def test_picture_models_have_an_order_too(signed_in, app):
    comfy = _ids((await signed_in.get("/hub/api/models")).json()["comfy"])
    mine = [comfy[-1], *comfy[:-1]]
    r = await _order(signed_in, mine, "comfy")
    assert r.status_code == 200, r.text
    assert _ids((await signed_in.get("/hub/api/models")).json()["comfy"]) == mine
    assert _ids((await signed_in.get("/hub/api/models")).json()["llm"]) == ["qwen3.8-27b", "qwen3.6-35b-a3b"]


async def test_what_an_order_may_be(signed_in, app):
    assert (await _order(signed_in, ["qwen3.8-27b", "no-such-model"])).status_code == 404
    r = await _order(signed_in, ["qwen3.8-27b", "qwen3.8-27b"])
    assert r.status_code == 400 and "twice" in r.text
    r = await _order(signed_in, ["embed-text"], "aux")
    assert r.status_code == 400 and "llm and comfy" in r.text
    assert (await _order(signed_in, ["qwen-image-2.1"], "llm")).status_code == 404           # another group's model
    assert (await signed_in.put("/hub/api/models/order", json={"ids": "qwen3.8-27b"})).status_code == 422
    path = app.state.hub.catalog.override_path
    assert not path.exists() or "order" not in json.loads(path.read_text())                 # nothing was stored


async def test_people_see_the_owners_order_and_cannot_change_it(signed_in, app):
    await _order(signed_in, ["qwen3.6-35b-a3b", "qwen3.8-27b"])
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert _ids((await phone.get("/hub/api/models")).json()["llm"]) == ["qwen3.6-35b-a3b", "qwen3.8-27b"]
        assert (await _order(phone, ["qwen3.8-27b", "qwen3.6-35b-a3b"])).status_code == 403
    assert _ids(app.state.hub.catalog.llm_models()) == ["qwen3.6-35b-a3b", "qwen3.8-27b"]
