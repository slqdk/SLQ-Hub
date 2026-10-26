"""Configuration: the environment (fixed per machine) and runtime settings
(edited in the UI, stored in <data>/settings.json).

Environment variables come from /srv/slq-hub/.env through compose. In
development everything has a default that points at localhost and ./data.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path


def _list(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _networks(values: list[str]) -> list[ipaddress._BaseNetwork]:
    out = []
    for v in values:
        try:
            out.append(ipaddress.ip_network(v, strict=False))
        except ValueError:
            pass
    return out


@dataclass(frozen=True)
class Env:
    data_dir: Path
    models_dir: Path
    llm_url: str                 # the llama-server supervisor
    llm_host: str                # where the llama-servers listen
    comfy_url: str
    searxng_url: str
    sandbox_url: str
    sandbox_ip: str
    internal_token: str
    secret_key: str
    public_url: str
    trusted_proxies: tuple[str, ...]
    lan_networks: tuple = field(default_factory=tuple)
    admin_user: str = "admin"
    admin_password_hash: str = ""
    version: str = "dev"
    static_dir: Path | None = None
    lan_host: str = ""                 # the VM's LAN address, for "how to connect" in the UI
    ui_port: int = 8080                # published ports on the VM
    ollama_port: int = 11434
    comfy_files: Path | None = None    # ComfyUI's input/ and output/ (mounted), to delete people's pictures there
    speech_url: str = "http://127.0.0.1:7100"   # the speech service (text to speech on the CPU)
    speech_models: str = "/models"     # the models folder as the speech service sees it

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hub.db"

    @property
    def workspace_dir(self) -> Path:
        """The Hub's view of the shared workspace (projects and chat folders)."""
        return self.data_dir / "workspace"

    sandbox_workspace: str = "/workspace"   # the sandbox's view of the same folder


def _read_version() -> str:
    for p in (Path(__file__).resolve().parents[1] / "VERSION",
              Path(__file__).resolve().parents[2] / "VERSION"):
        try:
            return p.read_text().strip()
        except OSError:
            continue
    return "dev"


def load_env() -> Env:
    e = os.environ
    data = Path(e.get("HUB_DATA_DIR") or (Path.cwd() / "data")).resolve()
    static = None
    for p in (Path(e.get("HUB_STATIC_DIR", "")) if e.get("HUB_STATIC_DIR") else None,
              Path(__file__).resolve().parents[1] / "static",
              Path(__file__).resolve().parents[2] / "frontend" / "dist"):
        if p and (p / "index.html").is_file():
            static = p
            break
    return Env(
        data_dir=data,
        models_dir=Path(e.get("HUB_MODELS_DIR") or (data.parent / "models")).resolve(),
        llm_url=e.get("HUB_LLM_URL", "http://127.0.0.1:9000").rstrip("/"),
        llm_host=e.get("HUB_LLM_HOST", "127.0.0.1"),
        comfy_url=e.get("HUB_COMFY_URL", "http://127.0.0.1:8188").rstrip("/"),
        searxng_url=e.get("HUB_SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/"),
        sandbox_url=e.get("HUB_SANDBOX_URL", "http://127.0.0.1:7000").rstrip("/"),
        sandbox_ip=e.get("HUB_SANDBOX_IP", "172.31.250.20"),
        internal_token=e.get("HUB_INTERNAL_TOKEN", ""),
        secret_key=e.get("HUB_SECRET_KEY", "dev-secret"),
        public_url=e.get("HUB_PUBLIC_URL", ""),
        trusted_proxies=tuple(_list(e.get("HUB_TRUSTED_PROXIES"))),
        lan_networks=tuple(_networks(_list(e.get(
            "HUB_LAN_CIDRS", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8")))),
        admin_user=e.get("HUB_ADMIN_USER", "admin"),
        admin_password_hash=e.get("HUB_ADMIN_PASSWORD_HASH", ""),
        version=_read_version(),
        static_dir=static,
        lan_host=e.get("HUB_LAN_HOST", ""),
        ui_port=int(e.get("HUB_PORT") or 8080),
        ollama_port=int(e.get("HUB_OLLAMA_PORT") or 11434),
        comfy_files=Path(e["HUB_COMFY_FILES"]) if e.get("HUB_COMFY_FILES") else None,
        speech_url=e.get("HUB_SPEECH_URL", "http://127.0.0.1:7100").rstrip("/"),
        speech_models=e.get("HUB_SPEECH_MODELS", "/models").rstrip("/") or "/models",
    )


# ---------------------------------------------------------------- runtime settings

DEFAULTS: dict = {
    "models": {
        "default": "qwen3.8-27b",          # web chats and the agent
        "fallback_alias": "qwen3.8-27b",   # unknown model names from apps map here ("" = refuse)
        "preload": True,                   # load the default model when the Hub starts
    },
    "queue": {
        "affinity_window_s": 20,           # how long a waiting job lets same-model work go first
        "aging_s": 120,                    # every N seconds of waiting raises a job one priority level
        "max_wait_s": 1800,                # an API request waits at most this long for its turn
    },
    "api": {
        "cors_origins": [],                # browser apps allowed to call the model API
        "allow_external": False,           # model API through the proxy (from outside the LAN)
    },
    "agent": {
        "max_steps": 60,
        "compact_at": 0.75,                # share of the model's context
        "thinking_in_projects": True,
        "thinking_in_chats": False,
        "effort": "medium",
        "thinking_budget": 8000,           # most thinking tokens in one model step (0 = no limit): then it acts
        "tool_output_chars": 12000,
        "user_name": "",                   # how the assistant calls the user ("" = the admin user name)
        "escalate_to": "qwen3.8-27b",      # a stuck turn goes on with this model ("" = it answers with what it has)
        "review": True,                    # a turn that made code, pages or documents is checked by another model
        "review_model": "",                # "" = the hand-over model above (else the same), "same", or a model id
    },
    "cloud": {
        "provider": "anthropic",           # anthropic | openai (any OpenAI-compatible endpoint)
        "api_key": "",
        "model": "claude-sonnet-5-5",
        "base_url": "",                    # OpenAI-compatible base URL (DeepSeek, OpenRouter, ...)
        "max_tokens": 16000,
    },
    "web": {
        "access": "allow",                 # allow | ask: internet for the assistant - ask first: web search, pages
                                           # and code that goes online wait for the user's answer
        "search_results": 8,
        "fetch_max_chars": 30000,
        "blocked_cidrs": [],               # extra ranges web_fetch refuses (private ranges always are)
    },
    "sandbox": {
        "timeout_s": 120,
        "max_timeout_s": 1800,
    },
    "images": {
        "model": "qwen-image-2.1",
        "steps": 25,
        "turbo_steps": 8,
        "enhance_prompts": True,           # ERNIE-Image: let its own prompt enhancer expand the prompt
        "cfg": 1.0,
        "default_size": 1024,
    },
    "sound": {                             # "" = the first downloaded one (Settings -> Models order)
        "music_model": "",
        "sound_model": "",
        "speech_model_da": "",             # Danish speech
        "speech_model": "",                # every other language
    },
    "video": {                             # "" = the first downloaded one that can (Settings -> Models order)
        "t2v_model": "",                   # from a description
        "i2v_model": "",                   # from a picture (and from a first and a last picture, when it can)
    },
    "libraries": {
        "page_images": "cpu",              # cpu | gpu | off: embed page images for visual search (gpu = fast,
                                           # but it unloads the chat model while indexing)
        "results": 6,                      # hits per library_search
        "images_in_results": 2,            # page images shown to the model with the hits
        "rerank": True,
    },
    "mcp": {
        "servers": [],                     # [{name, transport: http|stdio, url|command, headers, enabled}]
    },
    "costs": {                             # 0.24.0: what the card's energy costs (Settings -> Usage)
        "price_per_kwh": 3.0,              # kr per kWh, a fixed price
        "other_watts": 0,                  # the rest of the PC while the card works (the card's reading is its own)
    },
}


class Settings:
    """settings.json with defaults merged in. Writes are whole-file and
    atomic; callers change one section at a time through update()."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        merged = copy.deepcopy(DEFAULTS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        for section, values in stored.items():
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                # a setting a newer version removed is dropped: the UI sends back what it was given, and
                # update() refuses keys it does not know (sound.speech_speed, gone in 0.13.4)
                merged[section].update({k: v for k, v in values.items() if k in DEFAULTS[section]})
            else:
                merged[section] = values
        return merged

    def get(self, section: str, key: str | None = None, default=None):
        with self._lock:
            sec = self._data.get(section, {})
            if key is None:
                return copy.deepcopy(sec)
            return copy.deepcopy(sec.get(key, default)) if isinstance(sec, dict) else default

    def all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, section: str, values: dict) -> dict:
        with self._lock:
            cur = self._data.setdefault(section, {})
            if not isinstance(cur, dict):
                cur = self._data[section] = {}
            for k, v in values.items():
                if section in DEFAULTS and k not in DEFAULTS[section]:
                    raise KeyError(f"unknown setting {section}.{k}")
                cur[k] = v
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            return copy.deepcopy(cur)
