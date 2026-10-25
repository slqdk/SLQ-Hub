"""Delete files (0.27.0): a downloaded model from the Hub's own list gives its disk space back and can be
downloaded again; files another downloaded model uses stay (Wan's text encoder serves four video models)."""
from __future__ import annotations

UMT5 = "comfy/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE21 = "comfy/vae/wan_2.1_vae.safetensors"


def install(hub, model_id: str, size: int = 1000) -> None:
    entry = next(e for g in ("llm", "aux", "comfy", "audio", "video") for e in hub.catalog.entries(g)
                 if e["id"] == model_id)
    for f in entry["files"]:
        p = hub.catalog.path(f)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(b"x" * size)


async def video_rows(client) -> dict:
    return {m["id"]: m for m in (await client.get("/hub/api/models")).json()["video"]}


async def test_delete_files_keeps_what_other_models_use(signed_in, app):
    hub = app.state.hub
    for mid in ("wan2.2-5b", "wan2.2-i2v-14b", "wan2.2-t2v-14b"):
        install(hub, mid)
    rows = await video_rows(signed_in)
    assert rows["wan2.2-i2v-14b"]["installed"] and rows["wan2.2-i2v-14b"]["freeable"] == 4000   # not umt5, not the VAE
    assert "freeable" not in rows["wan2.2-5b-turbo"]                                            # not downloaded
    r = await signed_in.delete("/hub/api/models/wan2.2-i2v-14b/files")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["deleted"]) == 4 and body["freed"] == 4000 and set(body["kept"]) == {UMT5, VAE21}
    rows = await video_rows(signed_in)
    assert not rows["wan2.2-i2v-14b"]["installed"]
    assert rows["wan2.2-t2v-14b"]["installed"] and rows["wan2.2-5b"]["installed"]
    r = await signed_in.delete("/hub/api/models/wan2.2-t2v-14b/files")                  # the VAE is its alone now
    assert r.status_code == 200 and VAE21 in r.json()["deleted"] and r.json()["kept"] == [UMT5]
    assert (hub.env.models_dir / UMT5).is_file() and (await video_rows(signed_in))["wan2.2-5b"]["installed"]
    # downloaded again, it needs only what is gone
    queued = (await signed_in.post("/hub/api/models/wan2.2-i2v-14b/download")).json()["queued"]
    assert UMT5 not in queued and VAE21 in queued and len(queued) == 5
    hub.extras["downloads"].cancel("wan2.2-i2v-14b")


async def test_what_is_in_use_or_added_by_the_owner_is_refused(signed_in, app):
    hub = app.state.hub
    install(hub, "wan2.2-5b")
    hub.settings.update("video", {"t2v_model": "wan2.2-5b"})
    r = await signed_in.delete("/hub/api/models/wan2.2-5b/files")
    assert r.status_code == 409 and "Assistant, Video" in r.json()["detail"]
    hub.settings.update("video", {"t2v_model": ""})
    r = await signed_in.delete("/hub/api/models/qwen3.8-27b/files")
    assert r.status_code == 409 and "the model for chats" in r.json()["detail"]
    r = await signed_in.delete("/hub/api/models/qwen-image-2.1/files")
    assert r.status_code == 409 and "Assistant, Images" in r.json()["detail"]
    assert (await signed_in.delete("/hub/api/models/nope/files")).status_code == 404
    assert (await signed_in.delete("/hub/api/models/wan2.2-5b/files")).status_code == 200
    hub.settings.update("models", {"default": "qwen3.6-35b-a3b", "fallback_alias": ""})
    hub.settings.update("agent", {"escalate_to": ""})
    t = await hub.scheduler.acquire("system", "preload", "llm:qwen3.8-27b", "background", label="preload")
    await t.release()
    r = await signed_in.delete("/hub/api/models/qwen3.8-27b/files")                    # on the card now
    assert r.status_code == 409 and "Free the GPU first" in r.json()["detail"]


async def test_files_a_required_model_needs_stay(signed_in, app):
    """Qwen-Image 2.1 Turbo requires the base (its download fetches the base's files, and it runs on them): while
    the Turbo is downloaded, deleting the base's files deletes none of them."""
    hub = app.state.hub
    hub.settings.update("images", {"model": "qwen-image-2.1-turbo"})
    base = next(e for e in hub.catalog.entries("comfy") if e["id"] == "qwen-image-2.1")
    turbo = next(e for e in hub.catalog.entries("comfy") if e["id"] == "qwen-image-2.1-turbo")
    assert "qwen-image-2.1" in turbo.get("requires", [])
    rows = {m["id"]: m for m in (await signed_in.get("/hub/api/models")).json()["comfy"]}
    assert rows["qwen-image-2.1"]["freeable"] == 0                                 # nothing would come back
    r = await signed_in.delete("/hub/api/models/qwen-image-2.1/files")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == [] and set(r.json()["kept"]) == {f["dest"] for f in base["files"]}
    assert hub.catalog.installed(base) and hub.catalog.installed(turbo)
