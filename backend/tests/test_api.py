"""The model API for other apps: auth, LAN guard, CORS, and the three
formats translated to and from llama-server - through the queue."""
import base64
import json

import httpx

from .mocks import PNG_1x1


def lines(text: str) -> list[dict]:
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def sse_events(text: str) -> list[tuple[str | None, dict]]:
    out = []
    for block in text.split("\n\n"):
        ev, data = None, None
        for l in block.splitlines():
            if l.startswith("event:"):
                ev = l[6:].strip()
            elif l.startswith("data:"):
                d = l[5:].strip()
                data = d if d == "[DONE]" else json.loads(d)
        if data is not None:
            out.append((ev, data))
    return out


# ---------------------------------------------------------------- the door

async def test_model_api_needs_a_key(client):
    r = await client.get("/api/tags")
    assert r.status_code == 401


async def test_key_in_the_path_works_for_ollama_clients(client, api_key):
    r = await client.get(f"/k/{api_key}/api/tags")
    assert r.status_code == 200
    names = [m["name"] for m in r.json()["models"]]
    assert "qwen3.8-27b" in names and "qwen3.6-35b-a3b" in names and "embed-text" in names


async def test_bearer_and_x_api_key(client, api_key):
    assert (await client.get("/v1/models", headers={"Authorization": f"Bearer {api_key}"})).status_code == 200
    assert (await client.get("/v1/models", headers={"x-api-key": api_key})).status_code == 200
    assert (await client.get("/v1/models", headers={"x-api-key": "slqhub_wrongwrongwrong"})).status_code == 401


async def test_not_from_outside_the_lan(app, api_key):
    t = httpx.ASGITransport(app=app, client=("8.8.8.8", 4000))
    async with httpx.AsyncClient(transport=t, base_url="http://hub") as c:
        r = await c.get("/v1/models", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 403


async def test_through_the_proxy_the_api_is_refused_but_the_ui_works(app, api_key):
    t = httpx.ASGITransport(app=app, client=("10.9.9.9", 4000))
    async with httpx.AsyncClient(transport=t, base_url="http://hub") as c:
        r = await c.get("/v1/models", headers={"Authorization": f"Bearer {api_key}", "X-Forwarded-For": "8.8.8.8"})
        assert r.status_code == 403
        r = await c.get("/hub/api/health", headers={"X-Forwarded-For": "8.8.8.8"})
        assert r.status_code == 200
        # a LAN client coming through the proxy may use it
        r = await c.get("/v1/models", headers={"Authorization": f"Bearer {api_key}", "X-Forwarded-For": "30.11.0.50"})
        assert r.status_code == 200


async def test_a_key_set_to_work_away_from_home(app, signed_in, api_key):
    r = await signed_in.post("/hub/api/keys", json={"name": "Maria", "priority": "interactive", "away": True})
    assert r.status_code == 200 and r.json()["away"] is True
    away = r.json()["key"]
    keys = {k["name"]: k for k in (await signed_in.get("/hub/api/keys")).json()}
    assert keys["Maria"]["away"] == 1 and keys["familylog"]["away"] == 0 and keys["Maria"]["last_used"] is None
    proxy = httpx.ASGITransport(app=app, client=("10.9.9.9", 4000))
    outside = httpx.ASGITransport(app=app, client=("8.8.8.8", 4000))
    async with httpx.AsyncClient(transport=proxy, base_url="http://hub") as c:
        fwd = {"X-Forwarded-For": "203.0.113.7"}
        # through the proxy from outside: the away key works, the home key and no key do not
        assert (await c.get("/v1/models", headers={**fwd, "Authorization": f"Bearer {away}"})).status_code == 200
        r = await c.get("/v1/models", headers={**fwd, "Authorization": f"Bearer {api_key}"})
        assert r.status_code == 403
        body = r.json()
        assert "only on the LAN" in body["error"] and "203.0.113.7" in body["error"]
        assert body["client_ip"] == "203.0.113.7" and body["via_proxy"] is True
        assert (await c.get("/v1/models", headers=fwd)).status_code == 403
        # a wrong key from outside learns nothing more than "LAN only"
        r = await c.get("/v1/models", headers={**fwd, "Authorization": "Bearer slqhub_wrongwrongwrongwrong"})
        assert r.status_code == 403
        # the Ollama path form and a streamed chat work too
        assert (await c.get(f"/k/{away}/api/tags", headers=fwd)).status_code == 200
        r = await c.post("/v1/chat/completions", headers={**fwd, "Authorization": f"Bearer {away}"},
                         json={"model": "qwen3.8-27b", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200 and r.text.rstrip().endswith("data: [DONE]")
    # used, and noted as used only once it was let in
    keys = {k["name"]: k for k in (await signed_in.get("/hub/api/keys")).json()}
    assert keys["Maria"]["last_used"] and keys["familylog"]["last_used"] is None
    # away means through the proxy: a direct peer outside the LAN (or the sandbox) is still refused
    async with httpx.AsyncClient(transport=outside, base_url="http://hub") as c:
        assert (await c.get("/v1/models", headers={"Authorization": f"Bearer {away}"})).status_code == 403
    sandbox = httpx.ASGITransport(app=app, client=("172.31.250.20", 4000))
    async with httpx.AsyncClient(transport=sandbox, base_url="http://hub") as c:
        assert (await c.get("/v1/models", headers={"Authorization": f"Bearer {away}"})).status_code == 403
    # an address limit still applies away from home
    kid = keys["Maria"]["id"]
    assert (await signed_in.patch(f"/hub/api/keys/{kid}", json={"allowed_ips": "30.11.0.0/20"})).status_code == 200
    async with httpx.AsyncClient(transport=proxy, base_url="http://hub") as c:
        r = await c.get("/v1/models", headers={"X-Forwarded-For": "203.0.113.7", "Authorization": f"Bearer {away}"})
        assert r.status_code == 403 and "may not be used from 203.0.113.7" in r.json()["error"]
    # switched back to home only
    assert (await signed_in.patch(f"/hub/api/keys/{kid}", json={"away": False, "allowed_ips": ""})).status_code == 200
    assert {k["name"]: k for k in (await signed_in.get("/hub/api/keys")).json()}["Maria"]["away"] == 0
    async with httpx.AsyncClient(transport=proxy, base_url="http://hub") as c:
        r = await c.get("/v1/models", headers={"X-Forwarded-For": "203.0.113.7", "Authorization": f"Bearer {away}"})
        assert r.status_code == 403
        # ... and at home (a LAN client through the proxy) it works as before
        r = await c.get("/v1/models", headers={"X-Forwarded-For": "30.11.0.50", "Authorization": f"Bearer {away}"})
        assert r.status_code == 200


async def test_allowing_every_key_from_outside_still_means_through_the_proxy(app, signed_in, api_key):
    await signed_in.put("/hub/api/settings", json={"section": "api", "values": {"allow_external": True}},
                        headers={"Origin": "http://hub"})
    proxy = httpx.ASGITransport(app=app, client=("10.9.9.9", 4000))
    async with httpx.AsyncClient(transport=proxy, base_url="http://hub") as c:
        r = await c.get("/v1/models", headers={"X-Forwarded-For": "203.0.113.7", "Authorization": f"Bearer {api_key}"})
        assert r.status_code == 200
    outside = httpx.ASGITransport(app=app, client=("8.8.8.8", 4000))
    async with httpx.AsyncClient(transport=outside, base_url="http://hub") as c:
        assert (await c.get("/v1/models", headers={"Authorization": f"Bearer {api_key}"})).status_code == 403


async def test_spoofed_forwarded_for_is_ignored_from_untrusted_peers(app, api_key):
    t = httpx.ASGITransport(app=app, client=("8.8.8.8", 4000))
    async with httpx.AsyncClient(transport=t, base_url="http://hub") as c:
        r = await c.get("/v1/models", headers={"Authorization": f"Bearer {api_key}", "X-Forwarded-For": "127.0.0.1"})
    assert r.status_code == 403


async def test_cors_only_for_listed_origins(signed_in, api_key):
    r = await signed_in.options("/v1/chat/completions", headers={"Origin": "http://coagent.lan",
                                                                 "Access-Control-Request-Method": "POST"})
    assert r.status_code == 403
    await signed_in.put("/hub/api/settings", json={"section": "api", "values": {"cors_origins": ["http://coagent.lan"]}},
                        headers={"Origin": "http://hub"})
    r = await signed_in.options("/v1/chat/completions", headers={"Origin": "http://coagent.lan",
                                                                 "Access-Control-Request-Method": "POST"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "http://coagent.lan"
    r = await signed_in.get("/v1/models", headers={"Origin": "http://coagent.lan", "Authorization": f"Bearer {api_key}"})
    assert r.headers.get("access-control-allow-origin") == "http://coagent.lan"


# ---------------------------------------------------------------- Ollama

async def test_ollama_show_reports_context_and_capabilities(client, api_key):
    r = await client.post(f"/k/{api_key}/api/show", json={"model": "qwen3.8-27b"})
    body = r.json()
    assert body["model_info"]["qwen35.context_length"] == 65536
    assert set(body["capabilities"]) >= {"completion", "vision", "tools", "thinking"}


async def test_ollama_chat_streams_thinking_text_and_stats(client, api_key, fakes, app):
    img = base64.b64encode(PNG_1x1).decode()
    r = await client.post(f"/k/{api_key}/api/chat", json={
        "model": "qwen3.8-27b", "think": False, "format": {"type": "object", "properties": {"a": {"type": "string"}}},
        "options": {"num_ctx": 16384, "temperature": 0.2, "num_predict": 100},
        "messages": [{"role": "user", "content": "describe", "images": [img]}]})
    assert r.status_code == 200
    out = lines(r.text)
    text = "".join(o["message"]["content"] for o in out if not o["done"])
    thinking = "".join(o["message"].get("thinking", "") for o in out if not o["done"])
    assert text == "Hello from the fake model." and thinking == "thinking..."
    final = out[-1]
    assert final["done"] and final["done_reason"] == "stop"
    assert final["prompt_eval_count"] == 12 and final["eval_count"] == 7
    assert final["eval_duration"] == 100_000_000 and final["prompt_eval_cached_count"] == 3
    req = fakes.llama["requests"][-1]
    assert req["chat_template_kwargs"] == {"enable_thinking": False}
    assert req["temperature"] == 0.2 and req["max_tokens"] == 100
    assert req["response_format"]["type"] == "json_schema"
    parts = req["messages"][0]["content"]
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"]["url"].startswith("data:image/")
    # the queue saw it, under the key's name, and it is done
    snap = app.state.hub.scheduler.snapshot()
    assert not snap["running"] and snap["current"] == "llm:qwen3.8-27b"
    assert fakes.sup["starts"][0]["args"][:2] == ["-m", "/models/llm/Qwen3.8-27B-UD-Q4_K_M.gguf"]


async def test_ollama_chat_non_stream_with_tool_calls(client, api_key, fakes):
    fakes.llama["replies"] = [{"content": "", "tool_calls": [{"name": "lookup", "arguments": {"q": "x"}}]}]
    r = await client.post(f"/k/{api_key}/api/chat", json={
        "model": "qwen3.8-27b:latest", "stream": False,
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
        "messages": [{"role": "user", "content": "find x"}]})
    body = r.json()
    assert body["done"] is True
    assert body["message"]["tool_calls"] == [{"function": {"name": "lookup", "arguments": {"q": "x"}}}]


async def test_ollama_think_effort_maps_to_the_templates_own_names(client, api_key, fakes):
    await client.post(f"/k/{api_key}/api/chat", json={"model": "qwen3.8-27b", "stream": False, "think": "high",
                                                      "messages": [{"role": "user", "content": "hi"}]})
    req = fakes.llama["requests"][-1]
    assert req["chat_template_kwargs"] == {"enable_thinking": True}
    assert req["reasoning_effort"] == "xhigh"


async def test_ollama_generate(client, api_key):
    r = await client.post(f"/k/{api_key}/api/generate", json={"model": "qwen3.6-35b-a3b", "prompt": "hi", "stream": False})
    body = r.json()
    assert body["response"] == "Hello from the fake model." and body["done"] and body["context"] == []


async def test_ollama_unknown_model_without_fallback(signed_in, api_key):
    await signed_in.put("/hub/api/settings", json={"section": "models", "values": {"fallback_alias": ""}},
                        headers={"Origin": "http://hub"})
    r = await signed_in.post(f"/k/{api_key}/api/chat", json={"model": "llama9", "stream": False, "messages": []})
    assert r.status_code == 404


async def test_ollama_embed_uses_the_cpu_helper(client, api_key, fakes):
    r = await client.post(f"/k/{api_key}/api/embed", json={"model": "embed-text", "input": ["a", "bb"]})
    assert len(r.json()["embeddings"]) == 2
    assert any(s["name"] == "embed-text" for s in fakes.sup["starts"])


# ---------------------------------------------------------------- OpenAI

async def test_openai_stream_passes_through_with_the_asked_name(client, api_key, fakes):
    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"},
                          json={"model": "qwen3.8", "stream": True, "reasoning_effort": "low",
                                "messages": [{"role": "user", "content": "hi"}]})
    evs = sse_events(r.text)
    assert evs[-1][1] == "[DONE]"
    assert all(e[1]["model"] == "qwen3.8" for e in evs[:-1] if isinstance(e[1], dict) and "choices" in e[1])
    req = fakes.llama["requests"][-1]
    assert req["model"] == "qwen3.8-27b" and req["reasoning_effort"] == "low"


async def test_openai_non_stream(client, api_key):
    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"},
                          json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]})
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "Hello from the fake model."


async def test_openai_embeddings(client, api_key):
    r = await client.post("/v1/embeddings", headers={"Authorization": f"Bearer {api_key}"},
                          json={"model": "embed-text", "input": "hello"})
    assert r.json()["data"][0]["object"] == "embedding"


async def test_model_list_says_what_each_model_is(client, api_key):
    """The extra fields the phone page (/chat) reads; other clients ignore them."""
    r = await client.get("/v1/models", headers={"Authorization": f"Bearer {api_key}"})
    data = r.json()["data"]
    by_id = {m["id"]: m for m in data}
    big = by_id["qwen3.8-27b"]
    assert big["kind"] == "chat" and big["name"] == "Qwen3.8 27B" and big["vision"] and big["thinking"]
    assert big["default"] is True and by_id["qwen3.6-35b-a3b"]["default"] is False
    assert by_id["embed-text"]["kind"] == "embedding"
    pic = by_id["qwen-image-2.1"]
    assert pic["kind"] == "image" and "generate" in pic["abilities"] and pic["default"] is True
    assert by_id["qwen-image-2.1-turbo"]["default"] is False
    assert all(m["object"] == "model" and m["owned_by"] == "slq-hub" for m in data)


# ---------------------------------------------------------------- phones

async def test_ollama_apps_may_probe_with_head(client, api_key):
    """Enchanted asks HEAD / before every message and says "Server
    unreachable" unless it is answered with 200, as Ollama does."""
    assert (await client.head("/")).status_code == 200
    assert (await client.head(f"/k/{api_key}/")).status_code == 200
    r = await client.get("/", headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200 and r.text.startswith("Ollama is running")


async def test_the_phone_page_is_served_at_chat(tmp_path):
    from fastapi import FastAPI

    from app.main import _static
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>SLQ Hub</title>")
    (dist / "chat.html").write_text("<title>SLQ Chat</title>")
    (dist / "chat.webmanifest").write_text("{}")
    web = FastAPI()
    _static(web, dist)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url="http://hub") as c:
        assert "SLQ Chat" in (await c.get("/chat")).text
        assert "SLQ Chat" in (await c.get("/chat/")).text
        assert (await c.get("/chat.webmanifest")).text == "{}"
        assert "SLQ Hub" in (await c.get("/chats/12")).text           # the web UI's own routes are untouched
        assert "SLQ Hub" in (await c.get("/", headers={"accept": "text/html"})).text


def test_home_network_is_the_lan_range_the_hub_sits_in(hub_env):
    from dataclasses import replace

    from app.config import _networks
    from app.ui.system_routes import _home_network
    nets = tuple(_networks(["30.11.0.0/20", "10.0.0.0/8", "30.0.0.0/8", "127.0.0.0/8"]))
    assert _home_network(replace(hub_env, lan_host="30.11.0.222", lan_networks=nets)) == "30.11.0.0/20"
    assert _home_network(replace(hub_env, lan_host="", lan_networks=nets)) == ""
    assert _home_network(replace(hub_env, lan_host="192.168.1.5", lan_networks=nets)) == ""


# ---------------------------------------------------------------- Anthropic

async def test_anthropic_non_stream_tool_use(client, api_key, fakes):
    fakes.llama["replies"] = [{"content": "Let me check.", "reasoning": "hmm",
                               "tool_calls": [{"name": "get_weather", "arguments": {"city": "Vojens"}}]}]
    r = await client.post("/v1/messages", headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"}, json={
        "model": "claude-sonnet-5-5", "max_tokens": 500, "system": "Be brief.",
        "thinking": {"type": "enabled", "budget_tokens": 2000},
        "tools": [{"name": "get_weather", "description": "w", "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "Weather?"}]})
    body = r.json()
    assert body["stop_reason"] == "tool_use"
    kinds = [b["type"] for b in body["content"]]
    assert kinds == ["thinking", "text", "tool_use"]
    assert body["content"][2]["input"] == {"city": "Vojens"}
    req = fakes.llama["requests"][-1]
    assert req["messages"][0] == {"role": "system", "content": "Be brief."}
    assert req["reasoning_effort"] == "low" and req["tools"][0]["function"]["name"] == "get_weather"


async def test_anthropic_tool_result_round_trip(client, api_key, fakes):
    r = await client.post("/v1/messages", headers={"x-api-key": api_key}, json={
        "model": "qwen3.8-27b", "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "Weather?"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call_9", "name": "get_weather",
                                               "input": {"city": "Vojens"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_9", "content": "12 C"}]}]})
    assert r.status_code == 200
    msgs = fakes.llama["requests"][-1]["messages"]
    assert msgs[1]["tool_calls"][0]["id"] == "call_9"
    assert msgs[2] == {"role": "tool", "tool_call_id": "call_9", "content": "12 C"}


async def test_anthropic_stream_event_order(client, api_key, fakes):
    fakes.llama["replies"] = [{"content": "Hi there", "reasoning": "think think",
                               "tool_calls": [{"name": "t", "arguments": {"a": 1}}]}]
    r = await client.post("/v1/messages", headers={"x-api-key": api_key}, json={
        "model": "qwen3.8-27b", "max_tokens": 100, "stream": True, "thinking": {"type": "enabled", "budget_tokens": 20000},
        "tools": [{"name": "t", "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "go"}]})
    evs = [e for e, _ in sse_events(r.text)]
    assert evs[0] == "message_start" and evs[-1] == "message_stop"
    starts = [d["content_block"]["type"] for e, d in sse_events(r.text) if e == "content_block_start"]
    assert starts == ["thinking", "text", "tool_use"]
    deltas = [d["delta"] for e, d in sse_events(r.text) if e == "content_block_delta"]
    assert "".join(d.get("partial_json", "") for d in deltas) == json.dumps({"a": 1})
    md = next(d for e, d in sse_events(r.text) if e == "message_delta")
    assert md["delta"]["stop_reason"] == "tool_use"


async def test_anthropic_count_tokens(client, api_key):
    r = await client.post("/v1/messages/count_tokens", headers={"x-api-key": api_key},
                          json={"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hello world"}]})
    assert r.json()["input_tokens"] > 0


# ---------------------------------------------------------------- queue integration

async def test_queue_switches_models_for_different_apps(client, api_key, fakes, app):
    await client.post(f"/k/{api_key}/api/chat", json={"model": "qwen3.8-27b", "stream": False,
                                                      "messages": [{"role": "user", "content": "a"}]})
    await client.post(f"/k/{api_key}/api/chat", json={"model": "qwen3.6-35b-a3b", "stream": False,
                                                      "messages": [{"role": "user", "content": "b"}]})
    models = [s["args"][1] for s in fakes.sup["starts"] if s["name"] == "main"]
    assert models == ["/models/llm/Qwen3.8-27B-UD-Q4_K_M.gguf", "/models/llm/Qwen3.6-35B-A3B-UD-IQ4_XS.gguf"]
    assert app.state.hub.scheduler.current == "llm:qwen3.6-35b-a3b"


def test_password_hashes_survive_env_files_and_old_hashes_still_work():
    from app import auth
    h = auth.hash_password("correct horse battery", n=2**10)
    assert h.startswith("scrypt:") and "$" not in h and auth.hash_is_valid(h)
    assert auth.verify_password("correct horse battery", h) and not auth.verify_password("wrong", h)
    old = h.replace(":", "$")                      # the first format
    assert auth.hash_is_valid(old) and auth.verify_password("correct horse battery", old)
    mangled = "scrypt$32768$8$1+BJ$abc"              # what Compose made of an unquoted $-hash
    assert not auth.hash_is_valid(mangled) and not auth.verify_password("x", mangled)
