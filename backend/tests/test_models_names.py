"""The owner's names for chat and picture models (Settings -> Models -> Rename): shown everywhere a person reads a
model's name - chats, the model choices, Settings, the phone page - while ids, which apps and settings use, stay."""
from __future__ import annotations

import json

from app.catalog import Catalog

from .test_agent import finish, new_chat, say
from .test_people import add_person, client_from, sign_in


def system(fakes, i: int = 0) -> str:
    return fakes.llama["requests"][i]["messages"][0]["content"]


async def rename(client, model_id: str, name: str):
    return await client.put(f"/hub/api/models/{model_id}/name", json={"name": name})


async def test_a_chat_model_goes_by_the_owners_name(signed_in, app, fakes):
    hub = app.state.hub
    r = await rename(signed_in, "qwen3.8-27b", "  Store   hjerne ")
    assert r.status_code == 200, r.text
    m = r.json()["model"]
    assert (m["id"], m["name"], m["name_card"]) == ("qwen3.8-27b", "Store hjerne", "Qwen3.8 27B")
    listed = {e["id"]: e for e in (await signed_in.get("/hub/api/models")).json()["llm"]}
    assert listed["qwen3.8-27b"]["name"] == "Store hjerne" and "name_card" not in listed["qwen3.6-35b-a3b"]
    # the phone page's list shows it; apps keep the id
    v1 = {e["id"]: e for e in (await signed_in.get("/v1/models")).json()["data"]}
    assert v1["qwen3.8-27b"]["name"] == "Store hjerne"
    tags = [t["name"] for t in (await signed_in.get("/api/tags")).json()["models"]]
    assert "qwen3.8-27b" in tags and "Store hjerne" not in tags
    # the model knows both names; the turn's own record keeps the id
    fakes.llama["replies"] = [{"content": "Hej."}, {"content": "Hilsen"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Hej"))
    assert run.status == "done" and "You run as Store hjerne (Qwen3.8 27B) on the user's own server" in system(fakes)
    assert fakes.llama["requests"][0]["model"] == "qwen3.8-27b"
    # it survives a restart (the overrides file), and the card's name - or nothing - goes back to it
    again = Catalog(hub.env.models_dir, hub.catalog.override_path)
    assert again.get_llm("qwen3.8-27b")["name"] == "Store hjerne"
    m = (await rename(signed_in, "qwen3.8-27b", "")).json()["model"]
    assert m["name"] == "Qwen3.8 27B" and "name_card" not in m
    over = json.loads(hub.catalog.override_path.read_text())
    assert "qwen3.8-27b" not in over.get("llm", {})
    await rename(signed_in, "qwen3.8-27b", "Store hjerne")
    assert (await rename(signed_in, "qwen3.8-27b", "Qwen3.8 27B")).json()["model"].get("name_card") is None
    # other settings of the model keep its name, and its name keeps them
    await rename(signed_in, "qwen3.8-27b", "Store hjerne")
    r = await signed_in.put("/hub/api/models/qwen3.8-27b", json={"thinking_mode": "on"})
    assert r.status_code == 200 and r.json()["model"]["name"] == "Store hjerne"
    assert hub.catalog.get_llm("qwen3.8-27b")["thinking_mode"] == "on"
    # one name per model, a length limit, only chat and picture models, only the owner
    r = await rename(signed_in, "qwen3.6-35b-a3b", "STORE HJERNE")
    assert r.status_code == 400 and "already called" in r.text
    assert len((await rename(signed_in, "qwen3.6-35b-a3b", "x" * 99)).json()["model"]["name"]) == 60
    assert (await rename(signed_in, "embed-text", "Indexer")).status_code == 404
    assert (await rename(signed_in, "no-such-model", "X")).status_code == 404
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        assert (await rename(phone, "qwen3.8-27b", "Mine")).status_code == 403
        names = {e["id"]: e["name"] for e in (await phone.get("/hub/api/models")).json()["llm"]}
        assert names["qwen3.8-27b"] == "Store hjerne"                       # people see the owner's names


async def test_the_hand_over_names_both(signed_in, app, fakes):
    """The small model reads who takes over by both names; the chat's notice by the owner's."""
    hub = app.state.hub
    hub.settings.update("models", {"default": "qwen3.6-35b-a3b"})
    await rename(signed_in, "qwen3.8-27b", "Store hjerne")
    await rename(signed_in, "qwen3.6-35b-a3b", "Lynet")

    def responder(body):
        if "short title" in json.dumps(body["messages"][-1]):
            return {"content": "Biler"}
        if body["model"] == "qwen3.8-27b":
            return {"content": "Her er svaret."}
        return {"content": "Igen.", "tool_calls": [{"name": "web_search", "arguments": {"query": "xc60 co2"}}]}
    fakes.llama["responder"] = responder
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "XC60 mod Tesla?"))
    assert run.status == "done", run.events[-1]
    small = [b for b in fakes.llama["requests"] if b["model"] == "qwen3.6-35b-a3b"]
    big = [b for b in fakes.llama["requests"] if b["model"] == "qwen3.8-27b"]
    first = small[0]["messages"][0]["content"]
    assert "Store hjerne (Qwen3.8 27B), a stronger model on this server takes over" in first
    assert "You run as Lynet (Qwen3.6 35B-A3B)" in first
    assert "handed over to you by Lynet (Qwen3.6 35B-A3B)" in big[0]["messages"][0]["content"]
    from app.chats import store as chats
    notice = chats.messages(c["id"])[-1]["content"][0]
    assert notice["type"] == "notice" and notice["text"].startswith("Handed over from Lynet to Store hjerne:")


async def test_a_picture_model_goes_by_the_owners_name(signed_in, app, fakes):
    r = await rename(signed_in, "qwen-image-2.1", "Billedmaskinen")
    assert r.status_code == 200 and r.json()["model"]["name_card"] == "Qwen-Image 2.1"
    fakes.llama["replies"] = [
        {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a red fox in the snow"}}]},
        {"content": "Her er ræven."}, {"content": "Ræv"}]
    c = await new_chat(signed_in)
    run = await finish(app, await say(signed_in, c["id"], "Tegn en ræv"))
    assert run.status == "done", run.events[-1]
    tools = {t["function"]["name"]: t["function"] for t in fakes.llama["requests"][0]["tools"]}
    assert "with Billedmaskinen (Qwen-Image 2.1), this chat's picture model" in tools["image_generate"]["description"]
    result = next(e["data"] for e in run.events if e["type"] == "tool_result")
    assert "with Billedmaskinen" in result["text"], result["text"]
    comfy = {e["id"]: e["name"] for e in (await signed_in.get("/hub/api/models")).json()["comfy"]}
    assert comfy["qwen-image-2.1"] == "Billedmaskinen"


def test_a_model_the_owner_added_keeps_its_name_in_its_own_entry(tmp_path):
    """Added from Hugging Face or a link: the owner chose its name, so a new one replaces it there (no card name to
    go back to)."""
    models = tmp_path / "models"
    custom = tmp_path / "data" / "models.custom.json"
    custom.parent.mkdir(parents=True)
    custom.write_text(json.dumps({"comfy": [{"id": "noob", "name": "NoobAI", "family": "sdxl",
                                             "files": [{"dest": "comfy/checkpoints/hub/noob.safetensors"}]}]}))
    cat = Catalog(models, tmp_path / "data" / "models.override.json")
    e = cat.rename("noob", "Anime")
    assert e["name"] == "Anime" and "name_card" not in e and e["custom"]
    assert json.loads(custom.read_text())["comfy"][0]["name"] == "Anime"
    assert cat.rename("noob", "")["name"] == "noob"                     # nothing: its id
    assert Catalog.label({"name": "Anime"}) == "Anime"
    assert Catalog.label({"name": "Lynet", "name_card": "Qwen3.6 35B-A3B"}) == "Lynet (Qwen3.6 35B-A3B)"
