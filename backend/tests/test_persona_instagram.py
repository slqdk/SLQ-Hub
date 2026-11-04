"""A persona's posts on Instagram (0.76.0, Søren: "Are there tools where i automatically can upload to platforms,
like Instagram ... when i approve the post?" - "have the Hub post there directly"): the owner's Meta app, the
persona's account signed in once, a post made ready and handed to Instagram by signed link when it is approved, the
token renewed - against a fake of Instagram's API that fetches the links from the Hub as Meta's servers would."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from PIL import Image

from app.personas import instagram as I

from .mocks import _png, fake_mp4
from .test_loras import ORIGIN
from .test_personas import anna, ok

API = "/hub/api/personas"
APP = {"app_id": "990602627938098", "app_secret": "a1b2c3d4e5f6a7b8c9d0"}
TEXT = "Efterårsløb i Kolding 🍂 Benene protesterede, hovedet jublede. #løb #kolding"


def fetcher(app):
    """Meta's servers fetching a signed link: no sign-in of the Hub's."""
    async def fetch(url: str) -> bytes:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as c:
            r = await c.get(url)
        assert r.status_code == 200, (url, r.status_code, r.text)
        return r.content
    return fetch


async def connected(signed_in, app, fakes) -> tuple[dict, object]:
    """Anna, her account (laura.kolding) signed in through Instagram once."""
    await ok(await signed_in.put(f"{API}/instagram", json=APP, headers=ORIGIN))
    p = await anna(signed_in)
    url = (await ok(await signed_in.post(f"{API}/{p['id']}/instagram/connect", headers=ORIGIN)))["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.test") as outside:
        r = await outside.get(f"{API}/instagram/callback", params={"code": "CODE1", "state": state})
    assert r.status_code == 303 and r.headers["location"].endswith("instagram=connected"), r.text
    fakes.instagram["fetch"] = fetcher(app)
    return p, app.state.hub.extras["personas"]


def make_post(store, pid: str, media: list[tuple[str, bytes]], text: str = TEXT) -> dict:
    sid = "s" + secrets.token_hex(4)
    items = [{"file": store._put(pid, f"posts/{sid}-{i}.{'png' if kind == 'picture' else 'mp4'}", data), "kind": kind}
             for i, (kind, data) in enumerate(media, 1)]
    post = {"id": sid, "task": "t1", "title": "Mandagspost", "text": text, "media": items, "status": "draft",
            "made": int(time.time()), "chat": None, "run": None}
    store._write_posts(pid, [*store.posts_of(pid), post])
    return post


async def settled(store, pid: str, sid: str) -> dict:
    job = store.ig_jobs.get(f"{pid}/{sid}")
    if job is not None:
        await asyncio.wait_for(asyncio.shield(job), 20)
    return next(s for s in store.posts_of(pid) if s["id"] == sid)


def calls(fakes, kind: str) -> list[dict]:
    return [c for k, c in fakes.instagram["calls"] if k == kind]


async def test_the_app_and_the_account_are_set_up_once(signed_in, app, fakes, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    info = await ok(await signed_in.get(f"{API}/instagram"))
    assert info == {"app_id": "", "secret_set": False, "config_id": "", "login": "instagram",
                    "redirect_uri": "https://hub.test/hub/api/personas/instagram/callback",
                    "public_url": "https://hub.test", "ready": False}
    for bad in ({"app_id": "12ab"}, {"app_id": "990602627938098", "app_secret": "not a secret!"}):
        assert (await signed_in.put(f"{API}/instagram", json=bad, headers=ORIGIN)).status_code == 400
    info = await ok(await signed_in.put(f"{API}/instagram", json=APP, headers=ORIGIN))
    assert info["ready"] and info["secret_set"] and "app_secret" not in info
    info = await ok(await signed_in.put(f"{API}/instagram", json={"app_id": APP["app_id"], "app_secret": ""},
                                        headers=ORIGIN))
    assert info["secret_set"]                                                  # empty: the one kept stays
    settings = (await ok(await signed_in.get("/hub/api/settings")))["settings"]
    assert settings["instagram"]["app_secret"] == "********"                   # masked, like the other secrets
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub") as signed_out:
        assert (await signed_out.get(f"{API}/instagram")).status_code == 401    # signed out: nothing

    p = await anna(signed_in)
    pid = p["id"]
    assert p["instagram"] is None
    fakes.instagram["me_wrapped"] = True                                       # /me in a data list: read all the same
    url = (await ok(await signed_in.post(f"{API}/{pid}/instagram/connect", headers=ORIGIN)))["url"]
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert f"{u.scheme}://{u.netloc}{u.path}" == "https://www.instagram.com/oauth/authorize"
    assert q["client_id"] == APP["app_id"] and q["response_type"] == "code" and q["force_reauth"] == "true"
    assert q["redirect_uri"] == "https://hub.test/hub/api/personas/instagram/callback"
    assert set(q["scope"].split(",")) == {"instagram_business_basic", "instagram_business_content_publish"}
    # Instagram sends the browser back to the address on the internet - perhaps without the Hub's sign-in there
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.test") as outside:
        r = await outside.get(f"{API}/instagram/callback", params={"code": "CODE1", "state": q["state"]})
        assert r.status_code == 303 and r.headers["location"] == f"http://hub/personas/{pid}?instagram=connected"
        again = await outside.get(f"{API}/instagram/callback", params={"code": "CODE1", "state": q["state"]})
        assert again.status_code == 400 and "used already" in again.text   # a state counts once
        forged = await outside.get(f"{API}/instagram/callback", params={"code": "CODE1", "state": "made-up"})
        assert forged.status_code == 400
    code = calls(fakes, "code")[0]
    assert code["client_secret"] == APP["app_secret"] and code["redirect_uri"] == q["redirect_uri"]
    assert calls(fakes, "exchange")[0]["grant_type"] == "ig_exchange_token"
    d = await ok(await signed_in.get(f"{API}/{pid}"))
    assert d["instagram"]["username"] == "laura.kolding" and not d["instagram"]["problem"]
    assert d["instagram"]["expires"] > time.time() + 59 * 86400
    store = app.state.hub.extras["personas"]
    kept = store._dir(pid) / "instagram.json"
    assert json.loads(kept.read_text())["token"] == "IGLONG1" and kept.stat().st_mode & 0o077 == 0
    assert "IGLONG1" not in json.dumps(d) and "IGLONG1" not in (store._dir(pid) / "persona.json").read_text()
    # the token and the secret never in the log, though httpx writes each request's address there
    assert "access_token=***" in caplog.text
    assert "IGLONG1" not in caplog.text and "IGSHORT1" not in caplog.text and APP["app_secret"] not in caplog.text

    # turned down at Instagram, or the code no good: back to the persona, with why
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.test") as outside:
        for params, why in (({"error": "access_denied", "error_description": "The user denied your request"},
                             "The%20user%20denied"),
                            ({"code": "WRONG"}, "Error%20validating%20verification%20code")):
            url = (await ok(await signed_in.post(f"{API}/{pid}/instagram/connect", headers=ORIGIN)))["url"]
            state = parse_qs(urlparse(url).query)["state"][0]
            r = await outside.get(f"{API}/instagram/callback", params={**params, "state": state})
            assert r.status_code == 303 and "instagram_error=" in r.headers["location"] and why in r.headers["location"]
    fakes.instagram["permissions"] = "instagram_business_basic"                # posting not allowed
    url = (await ok(await signed_in.post(f"{API}/{pid}/instagram/connect", headers=ORIGIN)))["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.test") as outside:
        r = await outside.get(f"{API}/instagram/callback", params={"code": "CODE1", "state": state})
    assert "instagram_business_content_publish" in r.headers["location"]
    assert (await ok(await signed_in.get(f"{API}/{pid}")))["instagram"]["username"] == "laura.kolding"   # kept

    # disconnected: the Hub forgets the token
    d = await ok(await signed_in.delete(f"{API}/{pid}/instagram", headers=ORIGIN))
    assert d["instagram"] is None and not kept.exists()


async def test_an_approved_post_goes_to_instagram(signed_in, app, fakes, monkeypatch):
    monkeypatch.setattr(I, "POLL", {"picture": 0, "video": 0})
    p, store = await connected(signed_in, app, fakes)
    pid = p["id"]
    tall = _png((832, 1216), (200, 120, 60, 255))                  # what the picture model made: taller than 4:5
    post = make_post(store, pid, [("picture", tall)])
    review = await ok(await signed_in.get(f"{API}/posts", params={"status": "draft"}))
    assert review["posts"][0]["persona"]["instagram"] == "laura.kolding"        # the card: Approve and post
    fakes.instagram["statuses"] = ["IN_PROGRESS", "FINISHED"]
    r = await ok(await signed_in.post(f"{API}/{pid}/posts/{post['id']}/instagram", headers=ORIGIN))
    assert r["status"] == "approved" and r["instagram"]["state"] == "posting"
    assert (await signed_in.post(f"{API}/{pid}/posts/{post['id']}/instagram", headers=ORIGIN)).status_code == 409
    s = await settled(store, pid, post["id"])
    assert s["instagram"]["state"] == "posted", s["instagram"]
    assert s["instagram"]["id"] == "18000000001"
    assert s["instagram"]["permalink"].startswith("https://www.instagram.com/p/")
    made = calls(fakes, "media")
    assert len(made) == 1 and made[0]["caption"] == TEXT and made[0]["ig"] == "17841400001"
    link = made[0]["image_url"]
    assert link.startswith(f"https://hub.test/hub/media/persona/{pid}/{post['id']}-ig-1.jpg?exp=")
    with Image.open(io.BytesIO(fakes.instagram["fetched"][link])) as img:   # what Instagram fetched: a 4:5 JPEG
        assert img.format == "JPEG" and img.mode == "RGB" and img.size == (832, 1040)
    assert calls(fakes, "publish")[0]["creation_id"] == "17900000001"
    assert sum(1 for c in calls(fakes, "get") if c["node"] == "17900000001") == 2     # until it was ready
    # the file made ready is gone again, and its link with it
    assert not (store._dir(pid) / "posts" / f"{post['id']}-ig-1.jpg").exists()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as c:
        assert (await c.get(link)).status_code == 404
    # on Instagram: not back to review, no new text, not again; it can be cleared from the Hub
    base = f"{API}/{pid}/posts/{post['id']}"
    assert (await signed_in.patch(base, json={"status": "draft"}, headers=ORIGIN)).status_code == 409
    assert (await signed_in.patch(base, json={"text": "Ny tekst"}, headers=ORIGIN)).status_code == 409
    assert (await signed_in.post(f"{base}/instagram", headers=ORIGIN)).status_code == 409
    assert (await signed_in.delete(base, headers=ORIGIN)).status_code == 200


async def test_more_pictures_are_a_carousel_and_a_video_a_reel(signed_in, app, fakes, monkeypatch):
    monkeypatch.setattr(I, "POLL", {"picture": 0, "video": 0})
    p, store = await connected(signed_in, app, fakes)
    pid = p["id"]
    two = make_post(store, pid, [("picture", _png((1024, 1024), (10, 10, 200, 255))),
                                 ("picture", _png((2400, 900), (10, 200, 10, 255)))])
    await ok(await signed_in.post(f"{API}/{pid}/posts/{two['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, two["id"])
    assert s["instagram"]["state"] == "posted", s["instagram"]
    items, carousel = calls(fakes, "media")[:2], calls(fakes, "media")[2]
    assert all(c["is_carousel_item"] == "true" and "caption" not in c for c in items)
    assert carousel["media_type"] == "CAROUSEL" and carousel["caption"] == TEXT
    assert carousel["children"] == "17900000001,17900000002"
    with Image.open(io.BytesIO(fakes.instagram["fetched"][items[1]["image_url"]])) as img:
        assert img.size == (1440, 754) and img.size[0] / img.size[1] <= 1.91        # wide: cut to 1.91:1

    def reel(body):                                    # ffmpeg in the sandbox, in the job's own folder
        folder = app.state.hub.env.workspace_dir / Path(body["cwd"]).relative_to("/workspace")
        assert body["cmd"].count("ffmpeg ") == 2 and "fps=30" in body["cmd"] and "+faststart" in body["cmd"]
        assert sorted(f.name for f in folder.iterdir()) == ["in.mp4"]
        (folder / "reel.mp4").write_bytes(fake_mp4(4096))
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "duration_ms": 5, "truncated": False}

    fakes.sandbox["handler"] = reel
    video = make_post(store, pid, [("video", fake_mp4(2048))])
    await ok(await signed_in.post(f"{API}/{pid}/posts/{video['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, video["id"])
    assert s["instagram"]["state"] == "posted", s["instagram"]
    r = calls(fakes, "media")[-1]
    assert r["media_type"] == "REELS" and r["share_to_feed"] == "true" and r["caption"] == TEXT
    assert fakes.instagram["fetched"][r["video_url"]] == fake_mp4(4096)
    assert not any((app.state.hub.env.workspace_dir / I.TMP).glob("*/*"))       # the sandbox's folder cleared


async def test_what_instagram_will_not_take_is_said(signed_in, app, fakes, monkeypatch):
    monkeypatch.setattr(I, "POLL", {"picture": 0, "video": 0})
    store = app.state.hub.extras["personas"]
    lone = await anna(signed_in, name="Grete")
    post = make_post(store, lone["id"], [("picture", _png((800, 800)))])
    r = await signed_in.post(f"{API}/{lone['id']}/posts/{post['id']}/instagram", headers=ORIGIN)
    assert r.status_code == 409 and "Connect" in r.text                        # no account
    p, store = await connected(signed_in, app, fakes)
    pid = p["id"]
    for text, why in (("x" * 2201, "2,200"), (" ".join(f"#t{i}" for i in range(31)), "30 hashtags"),
                      (" ".join(f"@u{i}" for i in range(21)), "20 @names")):
        s = make_post(store, pid, [("picture", _png((800, 800)))], text=text)
        r = await signed_in.post(f"{API}/{pid}/posts/{s['id']}/instagram", headers=ORIGIN)
        assert r.status_code == 400 and why in r.text, (why, r.text)
    emoji = make_post(store, pid, [("picture", _png((800, 800)))], text="🍂" * 1101)   # counted as Instagram does
    assert (await signed_in.post(f"{API}/{pid}/posts/{emoji['id']}/instagram", headers=ORIGIN)).status_code == 400
    text_only = make_post(store, pid, [])
    r = await signed_in.post(f"{API}/{pid}/posts/{text_only['id']}/instagram", headers=ORIGIN)
    assert r.status_code == 400 and "text only" in r.text

    # Instagram could not fetch the picture: the address it fetched from is named
    fakes.instagram["fail"] = {"media": {"code": 9004, "message": "Media download has failed.",
                                         "error_subcode": 2207052}}
    s = make_post(store, pid, [("picture", _png((800, 800)))])
    await ok(await signed_in.post(f"{API}/{pid}/posts/{s['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, s["id"])
    assert s["instagram"]["state"] == "failed" and "Media download has failed" in s["instagram"]["error"]
    assert "https://hub.test" in s["instagram"]["error"] and s["status"] == "approved"
    review = await ok(await signed_in.get(f"{API}/posts", params={"status": "review"}))
    assert s["id"] in [x["id"] for x in review["posts"]]                       # seen where it was approved
    assert s["id"] not in [x["id"] for x in (await ok(await signed_in.get(f"{API}/posts",
                                                                          params={"status": "draft"})))["posts"]]
    # it went wrong at Instagram's end: why, and it can be posted again
    fakes.instagram["statuses"] = ["ERROR"]
    again = await ok(await signed_in.post(f"{API}/{pid}/posts/{s['id']}/instagram", headers=ORIGIN))
    assert again["instagram"]["state"] == "posting"
    s = await settled(store, pid, s["id"])
    assert s["instagram"]["state"] == "failed" and "2207026" in s["instagram"]["error"]
    fakes.instagram["statuses"] = []
    # the sign-in was withdrawn: the post says so, and so does the account - connect again
    fakes.instagram["revoked"] = True
    await ok(await signed_in.post(f"{API}/{pid}/posts/{s['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, s["id"])
    assert s["instagram"]["state"] == "failed" and "connect the account again" in s["instagram"]["error"]
    d = await ok(await signed_in.get(f"{API}/{pid}"))
    assert "connect the account again" in d["instagram"]["problem"]
    r = await signed_in.post(f"{API}/{pid}/posts/{s['id']}/instagram", headers=ORIGIN)
    assert r.status_code == 409 and "connect the account again" in r.text
    posts = await ok(await signed_in.get(f"{API}/{pid}/posts"))
    assert posts["posts"][0]["persona"]["instagram"] == ""                     # the cards: no Approve and post


async def test_the_sign_in_is_renewed_and_a_restart_is_owned_up_to(signed_in, app, fakes):
    p, store = await connected(signed_in, app, fakes)
    pid = p["id"]
    assert await store.ig_tick(force=True) == []                                # a new token: nothing to do yet
    c = store.ig_of(pid)
    c["refreshed"] -= 6 * 86400
    store._ig_write(pid, c)
    assert await store.ig_tick(force=True) == [pid]
    c = store.ig_of(pid)
    assert c["token"] == "IGLONG2" and c["expires"] > time.time() + 59 * 86400 and time.time() - c["refreshed"] < 60
    assert await store.ig_tick() == []                                          # at most hourly
    c["refreshed"] -= 6 * 86400
    store._ig_write(pid, c)
    fakes.instagram["refresh_error"] = {"code": 190, "message": "Error validating access token: Session has expired."}
    assert await store.ig_tick(force=True) == []
    assert "connect the account again" in store.ig_public(pid)["problem"] and store.ig_username(pid) == ""
    c = store.ig_of(pid)
    c.update(problem="", expires=int(time.time()) - 10)                        # ran out while the Hub was off
    store._ig_write(pid, c)
    assert "run out" in store.ig_public(pid)["problem"]

    # posts a restart cut off, settled by asking Instagram about the container that was being published
    c.update(problem="", expires=int(time.time()) + 50 * 86400)
    store._ig_write(pid, c)
    a, b, gone, done_, ready, lost = (make_post(store, pid, [("picture", _png((800, 800)))], text=f"Post {n}")
                                      for n in range(6))
    fakes.instagram["containers"].update({"17900000091": {"caption": "Post 3", "polls": 0, "published": "18000000091"},
                                          "17900000092": {"caption": "Post 4", "polls": 0}})
    fakes.instagram["published"].append({"id": "18000000091", "caption": "Post 3", "container": {}})
    cut = {a["id"]: {"id": "18000000077"},                                     # its id had come back: posted
           b["id"]: {},                                                        # nothing handed to Instagram yet
           gone["id"]: {"publishing": True, "container": "17900000099"},       # Instagram does not know it
           done_["id"]: {"publishing": True, "container": "17900000091"},      # published after all
           ready["id"]: {"publishing": True, "container": "17900000092"}}      # ready, not published
    posts = store.posts_of(pid)
    for s in posts:
        if s["id"] in cut:
            s["status"] = "approved"
            s["instagram"] = {"state": "posting", **cut[s["id"]]}
    store._write_posts(pid, posts)
    (store._dir(pid) / "posts" / f"{b['id']}-ig-1.jpg").write_bytes(b"x")
    await store.ig_after_start()
    await asyncio.wait_for(store.ig_settling, 10)
    got = {s["id"]: s.get("instagram") or {} for s in store.posts_of(pid)}
    assert got[a["id"]]["state"] == "posted"
    assert got[b["id"]]["state"] == "failed" and got[b["id"]]["error"] == "Stopped before it was posted."
    assert got[gone["id"]]["state"] == "failed" and "look on Instagram" in got[gone["id"]]["error"]
    assert got[done_["id"]]["state"] == "posted" and got[done_["id"]]["id"] == "18000000091"
    assert got[done_["id"]]["permalink"].endswith("/C0091/")                  # found by its caption
    assert got[ready["id"]]["state"] == "failed" and "Post again posts it" in got[ready["id"]]["error"]
    assert not (store._dir(pid) / "posts" / f"{b['id']}-ig-1.jpg").exists()
    made = len(calls(fakes, "media"))
    await ok(await signed_in.post(f"{API}/{pid}/posts/{ready['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, ready["id"])
    assert s["instagram"]["state"] == "posted" and len(calls(fakes, "media")) == made     # the same container
    assert calls(fakes, "publish")[-1]["creation_id"] == "17900000092"
    assert not got[lost["id"]]                                                 # untouched

    # not deleted, not disconnected while a post is on its way
    job = asyncio.get_running_loop().create_task(asyncio.sleep(30))
    store.ig_jobs[f"{pid}/{b['id']}"] = job
    try:
        assert (await signed_in.delete(f"{API}/{pid}", headers=ORIGIN)).status_code == 409
        assert (await signed_in.delete(f"{API}/{pid}/instagram", headers=ORIGIN)).status_code == 409
        assert (await signed_in.delete(f"{API}/{pid}/posts/{b['id']}", headers=ORIGIN)).status_code == 409
    finally:
        job.cancel()
        store.ig_jobs.clear()


async def test_a_signed_link_names_one_file_for_a_while(signed_in, app, fakes):
    p, store = await connected(signed_in, app, fakes)
    pid = p["id"]
    store._put(pid, "posts/s0000abcd-ig-1.jpg", b"\xff\xd8\xff jpeg")
    store._put(pid, "posts/s0000abcd-1.png", _png((8, 8)))
    link = urlparse(store.ig_link(pid, "posts/s0000abcd-ig-1.jpg"))
    path, q = link.path, {k: v[0] for k, v in parse_qs(link.query).items()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.test") as outside:
        r = await outside.get(path, params=q)
        assert r.status_code == 200 and r.content == b"\xff\xd8\xff jpeg" and r.headers["content-type"] == "image/jpeg"
        assert (await outside.get(path, params={**q, "sig": "0" * 32})).status_code == 404
        assert (await outside.get(path, params={**q, "sig": "é"})).status_code == 404       # not a 500
        blob = await outside.get(f"/hub/media/blob/{'a' * 64}", params={"exp": q["exp"], "sig": "é"})
        assert blob.status_code == 401                                                 # the older links, the same
        assert (await outside.get(path, params={**q, "exp": str(int(q["exp"]) + 1)})).status_code == 404
        assert (await outside.get(path.replace("-ig-1.jpg", "-1.png"), params=q)).status_code == 404   # not ours
        other = await anna(signed_in, name="Grete")
        assert (await outside.get(path.replace(pid, other["id"]), params=q)).status_code == 404
    old = urlparse(store.ig_link(pid, "posts/s0000abcd-ig-1.jpg", now=time.time() - I.LINK_TTL - 5))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.test") as outside:
        assert (await outside.get(old.path + "?" + old.query)).status_code == 404       # run out


def test_a_picture_is_made_ready_as_instagram_takes_it():
    def ready(size, mode="RGB", color=(30, 60, 90)):
        buf = io.BytesIO()
        Image.new(mode, size, color if mode == "RGB" else (*color, 0)).save(buf, "PNG")
        with Image.open(io.BytesIO(I.prepare_picture(buf.getvalue()))) as img:
            return img.format, img.mode, img.size, img.getpixel((0, 0))
    assert ready((1024, 1280))[:3] == ("JPEG", "RGB", (1024, 1280))             # 4:5 as it is
    assert ready((832, 1216))[2] == (832, 1040)                                 # taller: cut to 4:5
    fmt, mode, size, px = ready((600, 600), "RGBA")                             # see-through: on white
    assert size == (600, 600) and min(px) > 245
    assert ready((3000, 3000))[2] == (1440, 1440)                               # at most 1440 wide
    assert ready((200, 200))[2] == (320, 320)                                   # at least 320
    w, h = ready((4000, 1000))[2]
    assert w == 1440 and 1.9 <= w / h <= 1.91
    gif = io.BytesIO()
    Image.new("P", (400, 500)).save(gif, "GIF")
    with Image.open(io.BytesIO(I.prepare_picture(gif.getvalue()))) as img:
        assert img.format == "JPEG" and img.size == (400, 500)


def test_a_caption_is_counted_as_instagram_counts_it():
    assert I.caption_problem("Hej #løb @laura.kolding") == ""
    assert "2,200" in I.caption_problem("a" * 2201) and I.caption_problem("a" * 2200) == ""
    assert I.caption_problem("🍂" * 1100) == "" and "2,200" in I.caption_problem("🍂" * 1101)   # two units each
    assert I.caption_problem("C# and F# &#35; mail@x.dk") == ""                  # not hashtags or @names


async def test_a_post_is_published_once(signed_in, app, fakes, monkeypatch):
    monkeypatch.setattr(I, "POLL", {"picture": 0, "video": 0})
    p, store = await connected(signed_in, app, fakes)
    pid = p["id"]
    # the answer to the publish got lost on the way: Instagram is asked, and the post counts as posted - once
    fakes.instagram["lost"] = True
    a = make_post(store, pid, [("picture", _png((800, 800)))], text="Første")
    await ok(await signed_in.post(f"{API}/{pid}/posts/{a['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, a["id"])
    assert s["instagram"]["state"] == "posted", s["instagram"]
    assert s["instagram"]["id"] == "18000000001" and s["instagram"]["permalink"].endswith("/C0001/")
    assert len(fakes.instagram["published"]) == 1
    # Instagram turned the publish down (too many today): Post again publishes the container made before - no new
    # upload, no second post
    fakes.instagram["fail"] = {"publish": {"code": 9, "message": "Application request limit reached",
                                           "error_subcode": 2207042}}
    b = make_post(store, pid, [("picture", _png((800, 800)))], text="Anden")
    await ok(await signed_in.post(f"{API}/{pid}/posts/{b['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, b["id"])
    assert s["instagram"]["state"] == "failed" and "limit" in s["instagram"]["error"]
    made = len(calls(fakes, "media"))
    await ok(await signed_in.post(f"{API}/{pid}/posts/{b['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, b["id"])
    assert s["instagram"]["state"] == "posted" and len(calls(fakes, "media")) == made
    assert len(fakes.instagram["published"]) == 2
    # the Hub stopped while the publish was under way: left for the next start to ask Instagram, then posted once
    fakes.instagram["delay"] = {"publish": 3}
    c = make_post(store, pid, [("picture", _png((800, 800)))], text="Tredje")
    await ok(await signed_in.post(f"{API}/{pid}/posts/{c['id']}/instagram", headers=ORIGIN))
    for _ in range(250):
        if store._ig_post(pid, c["id"]).get("publishing"):
            break
        await asyncio.sleep(0.02)
    job = store.ig_jobs[f"{pid}/{c['id']}"]
    job.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await job
    assert store._ig_post(pid, c["id"])["state"] == "posting"                  # not "failed": it may be posted
    fakes.instagram["delay"] = {}
    await store.ig_after_start()
    await asyncio.wait_for(store.ig_settling, 10)
    assert store._ig_post(pid, c["id"])["state"] == "failed"                   # Instagram: ready, not published
    await ok(await signed_in.post(f"{API}/{pid}/posts/{c['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, c["id"])
    assert s["instagram"]["state"] == "posted" and len(fakes.instagram["published"]) == 3


async def test_a_disconnect_is_not_undone_by_a_renewal(signed_in, app, fakes):
    p, store = await connected(signed_in, app, fakes)
    pid = p["id"]
    c = store.ig_of(pid)
    c["refreshed"] -= 6 * 86400
    store._ig_write(pid, c)
    fakes.instagram["delay"] = {"refresh": 0.5}
    tick = asyncio.get_running_loop().create_task(store.ig_tick(force=True))
    await asyncio.sleep(0.15)                                                  # the renewal under way
    await ok(await signed_in.delete(f"{API}/{pid}/instagram", headers=ORIGIN))
    assert await tick == [] and store.ig_of(pid) is None


async def test_post_again_finds_a_post_that_went_through(signed_in, app, fakes, monkeypatch):
    """Instagram could not be asked when it failed (it had gone through): Post again finds it posted."""
    monkeypatch.setattr(I, "POLL", {"picture": 0, "video": 0})
    p, store = await connected(signed_in, app, fakes)
    pid = p["id"]
    d = make_post(store, pid, [("picture", _png((800, 800)))], text="Fjerde")
    fakes.instagram["containers"]["17900000093"] = {"caption": "Fjerde", "polls": 0, "published": "18000000093"}
    fakes.instagram["published"].append({"id": "18000000093", "caption": "Fjerde", "container": {}})
    posts = store.posts_of(pid)
    for x in posts:
        if x["id"] == d["id"]:
            x.update(status="approved", instagram={"state": "failed", "container": "17900000093",
                                                   "error": "look on Instagram before posting it again."})
    store._write_posts(pid, posts)
    await ok(await signed_in.post(f"{API}/{pid}/posts/{d['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, d["id"])
    assert s["instagram"]["state"] == "posted" and s["instagram"]["id"] == "18000000093"
    assert len(fakes.instagram["published"]) == 1 and not calls(fakes, "media") and not calls(fakes, "publish")


async def test_unified_login_for_an_app_made_since_october_2026(signed_in, app, fakes, monkeypatch):
    """0.76.1: Meta offers new apps only Unified login - its dialog with a configuration, a system-user token from
    graph.facebook.com, the account given to the app, and posting on graph.facebook.com."""
    monkeypatch.setattr(I, "POLL", {"picture": 0, "video": 0})
    meta = {"app_id": "1093705243490654", "app_secret": APP["app_secret"], "config_id": "abc"}
    assert (await signed_in.put(f"{API}/instagram", json=meta, headers=ORIGIN)).status_code == 400
    info = await ok(await signed_in.put(f"{API}/instagram", json={**meta, "config_id": "1234567890123"},
                                        headers=ORIGIN))
    assert info["login"] == "unified" and info["config_id"] == "1234567890123" and info["ready"]
    p = await anna(signed_in)
    pid = p["id"]
    url = (await ok(await signed_in.post(f"{API}/{pid}/instagram/connect", headers=ORIGIN)))["url"]
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert f"{u.scheme}://{u.netloc}{u.path}" == "https://www.facebook.com/dialog/oauth"
    assert q["client_id"] == "1093705243490654" and q["config_id"] == "1234567890123" and q["prompt"] == "login"
    assert q["response_type"] == "code" and q["redirect_uri"] == "https://hub.test/hub/api/personas/instagram/callback"
    assert "scope" not in q                                                   # the configuration says what it asks
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.test") as outside:
        r = await outside.get(f"{API}/instagram/callback", params={"code": "CODE1", "state": q["state"]})
    assert r.status_code == 303 and r.headers["location"].endswith("instagram=connected"), r.headers["location"]
    code = calls(fakes, "unified_code")[0]
    assert code["client_id"] == "1093705243490654" and code["client_secret"] == APP["app_secret"]
    assert not calls(fakes, "code") and not calls(fakes, "exchange")          # nothing of Instagram login's
    store = app.state.hub.extras["personas"]
    c = store.ig_of(pid)
    assert c["login"] == "unified" and c["host"] == "https://graph.facebook.com" and c["token"] == "EAAUNI1"
    assert c["expires"] is None and c["user_id"] == "17841400001"
    d = await ok(await signed_in.get(f"{API}/{pid}"))
    assert d["instagram"]["username"] == "laura.kolding" and d["instagram"]["login"] == "unified"
    # its token is not renewed with Instagram login's refresh
    c["refreshed"] -= 30 * 86400
    store._ig_write(pid, c)
    assert await store.ig_tick(force=True) == [] and not calls(fakes, "refresh")
    # posting: the same containers and publishing, on graph.facebook.com
    fakes.instagram["fetch"] = fetcher(app)
    post = make_post(store, pid, [("picture", _png((832, 1216), (200, 120, 60, 255)))])
    await ok(await signed_in.post(f"{API}/{pid}/posts/{post['id']}/instagram", headers=ORIGIN))
    s = await settled(store, pid, post["id"])
    assert s["instagram"]["state"] == "posted", s["instagram"]
    assert calls(fakes, "media")[0]["host"] == "graph.facebook.com" and calls(fakes, "media")[0]["ig"] == "17841400001"
    # no account given: said so
    fakes.instagram["assigned"] = []
    url = (await ok(await signed_in.post(f"{API}/{pid}/instagram/connect", headers=ORIGIN)))["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://hub.test") as outside:
        r = await outside.get(f"{API}/instagram/callback", params={"code": "CODE1", "state": state})
    assert "instagram_error=" in r.headers["location"] and "no%20Instagram%20account" in r.headers["location"]
    # without the configuration ID: Instagram login again
    info = await ok(await signed_in.put(f"{API}/instagram", json={"config_id": ""}, headers=ORIGIN))
    assert info["login"] == "instagram"
