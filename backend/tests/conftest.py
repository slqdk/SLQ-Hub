import json
from pathlib import Path

import httpx
import pytest

from app import auth
from app.config import Env, _networks
from app.main import create_app
from app.state import Transports

from .mocks import (HostRouter, make_civitai, make_civitai_delivery, make_comfy, make_hf, make_llama, make_mcp_http,
                    make_sandbox, make_searxng, make_speech, make_supervisor)

CATALOG = Path(__file__).resolve().parents[1] / "app" / "catalog" / "models.json"


def _install_fake_models(models_dir: Path, ids: set[str]) -> None:
    cat = json.loads(CATALOG.read_text())
    for group in ("llm", "aux", "comfy", "audio", "video"):
        for e in cat[group]:
            if e["id"] in ids:
                for f in e["files"]:
                    p = models_dir / f["dest"]
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b"fake")


class Fakes:
    def __init__(self):
        self.sup = {}
        self.llama = {}
        self.comfy = {}
        self.sandbox = {}
        self.cloud = {}
        self.mcp = {}
        self.hf = {}
        self.civitai = {}
        self.speech = {}


@pytest.fixture
def fakes():
    return Fakes()


@pytest.fixture
def hub_env(tmp_path):
    data = tmp_path / "data"
    models = tmp_path / "models"
    _install_fake_models(models, {"qwen3.8-27b", "qwen3.6-35b-a3b", "embed-text", "rerank", "qwen-image-2.1",
                                  "qwen-image-2.1-turbo"})
    return Env(data_dir=data, models_dir=models, llm_url="http://supervisor", llm_host="llm",
               comfy_url="http://comfy", searxng_url="http://searxng", sandbox_url="http://sandbox:7000",
               sandbox_ip="172.31.250.20", internal_token="tok", secret_key="secret", public_url="https://hub.test",
               trusted_proxies=("10.9.9.9",),
               lan_networks=tuple(_networks(["127.0.0.0/8", "30.11.0.0/20"])),
               admin_user="soren", admin_password_hash=auth.hash_password("correct horse battery", n=2**10),
               speech_url="http://speech:7100", speech_models="/models")


@pytest.fixture
def app(hub_env, fakes):
    router = HostRouter({
        "supervisor": make_supervisor(fakes.sup),
        "llm": make_llama(fakes.llama),
        "comfy": make_comfy(fakes.comfy),
        "sandbox:7000": make_sandbox(fakes.sandbox),
        "searxng": make_searxng(),
        "cloud.test": make_llama(fakes.cloud),
        "mcp.test": make_mcp_http(fakes.mcp),
        "huggingface.co": make_hf(fakes.hf),
        "civitai.com": make_civitai(fakes.civitai),
        "delivery.civitai.test": make_civitai_delivery(fakes.civitai),
        "speech:7100": make_speech(fakes.speech),
    })
    t = Transports(llm=router, runner=router, comfy=router, sandbox=router, web=router, speech=router)
    application = create_app(hub_env, t, startup=False)
    application.state.router = router
    return application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub") as c:
        yield c


@pytest.fixture
async def signed_in(client):
    r = await client.post("/hub/api/auth/login", json={"username": "soren", "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
async def api_key(signed_in):
    r = await signed_in.post("/hub/api/keys", json={"name": "familylog", "priority": "normal"})
    assert r.status_code == 200, r.text
    return r.json()["key"]
