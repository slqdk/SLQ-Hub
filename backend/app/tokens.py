"""Sign-ins for model downloads (0.64.0, Søren: "Can i somehow put in the token in the ui in stead?"): a Hugging Face
read token (gated models - FLUX.2 klein 9B, its uncensored text encoder) and a Civitai API key (files that want a
signed-in user). Set in Settings -> Models -> Sign-ins for downloads: kept in settings.json on the VM (section
"tokens"), shown masked, the owner's alone, in no export. Or, as before, HF_TOKEN / CIVITAI_TOKEN in
/srv/slq-hub/.env; the one in Settings wins.

The Hub gives its settings at start-up (use, in state.build_hub). Code without the Hub at hand - the download queue,
Hugging Face's API helpers - asks here when it sends a request, so a token saved in Settings works at once, without a
restart. The tokens go only to their own sites: downloads.py and hf.py put them on huggingface.co's and civitai.com's
requests, and httpx drops Authorization when a redirect leaves the host.
"""
from __future__ import annotations

import os

ENV = {"huggingface": "HF_TOKEN", "civitai": "CIVITAI_TOKEN"}
_settings = None                     # the Hub's Settings (app/config.py), or None: .env only


def use(settings) -> None:
    global _settings
    _settings = settings


def _saved(name: str) -> str:
    if _settings is None:
        return ""
    try:
        return str(_settings.get("tokens", name) or "").strip()
    except Exception:  # noqa: BLE001 - a settings file from before 0.64.0 has no such section
        return ""


def get(name: str) -> str:
    """The token: Settings first, then .env ("" when neither has one)."""
    return _saved(name) or os.environ.get(ENV[name], "").strip()


def where(name: str) -> str:
    """"settings", "env" or ""."""
    if _saved(name):
        return "settings"
    return "env" if os.environ.get(ENV[name], "").strip() else ""


def hf() -> str:
    return get("huggingface")


def civitai() -> str:
    return get("civitai")
