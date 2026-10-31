"""0.64.0 - sign-ins for downloads in Settings -> Models (Søren: "Can i somehow put in the token in the ui in stead?",
after klein 9B uncensored's download said to put HF_TOKEN in .env): the Hugging Face token and the Civitai API key are
settings, kept on the Hub, shown masked, the owner's alone and in no export; a token saved there is used at once - by
the download queue, the training's fetch, the Hugging Face lookups - before .env's; Hugging Face can be asked whether
it takes it."""
from __future__ import annotations

from app import tokens
from app.chats import export

from .test_images_models import _drain
from .test_people import add_person, client_from, sign_in

ORIGIN = {"Origin": "http://hub"}
GATED = "black-forest-labs/FLUX.2-klein-9b-fp8"


def _klein_9b_repos(token: str) -> dict:
    """klein 9B's three files, small; its model file gated for one token."""
    return {GATED: {"gated": "auto", "token": token, "tree": [
                {"type": "file", "path": "flux-2-klein-9b-fp8.safetensors", "size": 300}]},
            "Comfy-Org/Flux-2-Klein-9B": {"tree": [
                {"type": "file", "path": "split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors", "size": 200}]},
            "Comfy-Org/flux2-dev": {"tree": [
                {"type": "file", "path": "split_files/vae/flux2-vae.safetensors", "size": 100}]}}


async def test_a_token_saved_in_settings_downloads_a_gated_model(signed_in, app, fakes, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("CIVITAI_TOKEN", raising=False)
    hub = app.state.hub
    fakes.hf["repos"] = _klein_9b_repos("hf_mine")
    assert (await signed_in.get("/hub/api/tokens")).json() == {"huggingface": {"from": ""}, "civitai": {"from": ""}}
    assert (await signed_in.post("/hub/api/models/flux2-klein-9b/download")).status_code == 200
    await _drain(hub)
    st = next(d for d in hub.extras["downloads"].snapshot()["files"] if "klein-9b-fp8" in d["dest"])
    assert st["status"] == "failed" and "Sign-ins for downloads" in st["error"] and len(st["error"]) <= 300

    r = await signed_in.put("/hub/api/settings", json={"section": "tokens", "values": {"huggingface": " hf_mine "}},
                            headers=ORIGIN)
    assert r.status_code == 200 and r.json()["settings"]["tokens"] == {"huggingface": "********", "civitai": ""}
    assert (await signed_in.get("/hub/api/settings")).json()["settings"]["tokens"]["huggingface"] == "********"
    status = await signed_in.get("/hub/api/tokens")
    assert status.json()["huggingface"] == {"from": "settings"} and "hf_mine" not in status.text
    assert tokens.hf() == "hf_mine"                                  # without its spaces
    fakes.hf["downloaded"] = []
    assert (await signed_in.post("/hub/api/models/flux2-klein-9b/download")).status_code == 200
    await _drain(hub)                                                # no restart: the next download has it
    assert hub.catalog.installed(hub.catalog.get("comfy", "flux2-klein-9b"))
    assert f"{GATED}/flux-2-klein-9b-fp8.safetensors" in fakes.hf["downloaded"]
    assert (await signed_in.get("/hub/api/training")).json()["hf_token"] is True

    # the form sends the mask back for a token it did not change: it stays
    r = await signed_in.put("/hub/api/settings", json={"section": "tokens",
                                                       "values": {"huggingface": "********", "civitai": "civ-key"}},
                            headers=ORIGIN)
    assert r.status_code == 200 and tokens.hf() == "hf_mine" and tokens.civitai() == "civ-key"
    # Settings win over .env; emptied, .env's is used again
    monkeypatch.setenv("HF_TOKEN", "hf_env")
    assert tokens.hf() == "hf_mine" and tokens.where("huggingface") == "settings"
    await signed_in.put("/hub/api/settings", json={"section": "tokens", "values": {"huggingface": ""}}, headers=ORIGIN)
    assert tokens.hf() == "hf_env" and (await signed_in.get("/hub/api/tokens")).json()["huggingface"] == {"from": "env"}


async def test_hugging_face_is_asked_whether_it_takes_the_token(signed_in, app, fakes, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    fakes.hf["whoami"] = {
        "hf_read": {"type": "user", "name": "sorenlyder",
                    "auth": {"type": "access_token", "accessToken": {"displayName": "hub", "role": "read"}}},
        "hf_fine": {"type": "user", "name": "sorenlyder",
                    "auth": {"type": "access_token", "accessToken": {
                        "displayName": "hub", "role": "fineGrained",
                        "fineGrained": {"canReadGatedRepos": False, "global": [], "scoped": []}}}}}

    async def check(token: str) -> dict:
        await signed_in.put("/hub/api/settings", json={"section": "tokens", "values": {"huggingface": token}},
                            headers=ORIGIN)
        return (await signed_in.get("/hub/api/tokens", params={"check": "true"})).json()["huggingface"]
    assert await check("hf_read") == {"from": "settings", "check": {"ok": True, "name": "sorenlyder", "role": "read"}}
    fine = (await check("hf_fine"))["check"]
    assert fine["ok"] is True and fine["role"] == "fineGrained" and fine["gated"] is False      # cannot read klein 9B
    wrong = (await check("hf_typo"))["check"]
    assert wrong["ok"] is False and "does not take this token" in wrong["error"]
    assert "check" not in (await check(""))                      # nothing to ask about


async def test_the_tokens_are_the_owners_alone(signed_in, app, fakes):
    await signed_in.put("/hub/api/settings", json={"section": "tokens", "values": {"huggingface": "hf_mine"}},
                        headers=ORIGIN)
    await add_person(signed_in)
    async with client_from(app) as phone:
        await sign_in(phone)
        r = await phone.get("/hub/api/settings")
        assert r.status_code == 200 and "tokens" not in r.json()["settings"] and "hf_mine" not in r.text
        assert (await phone.get("/hub/api/tokens")).status_code in (401, 403)
        r = await phone.put("/hub/api/settings", json={"section": "tokens", "values": {"huggingface": "hf_theirs"}},
                            headers=ORIGIN)
        assert r.status_code in (401, 403) and tokens.hf() == "hf_mine"
    assert "tokens" not in export.SAFE_SECTIONS                   # a chat's troubleshooting export leaves them out
