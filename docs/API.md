# Connecting other apps

Other apps use the Hub's models through the same queue as the web UI, so
they never fight over the card. The model API speaks three formats on the
same port; pick whichever the app already speaks.

Everything here answers only on the LAN and WireGuard (the networks in
`HUB_LAN_CIDRS`), unless Settings → Apps and keys allows the API through the
proxy.

## 1. Make a key

Settings → **Apps and keys** → **New key**. One key per app:

| Field | Meaning |
|---|---|
| App | A name; it shows in the queue as the job's owner. |
| Priority | `interactive` (like a web chat), `normal` (most apps), `background` (batch work). Waiting requests gain one level every two minutes, so nothing waits forever. |
| Only from these addresses | Optional: IPs or networks (`30.11.0.20, 10.8.0.0/24`). |

The key (`slqhub_...`) is shown once. A key can be switched off or deleted
at any time; requests with it then get 401.

The page also shows the exact addresses for this Hub. Below, `HUB` is the
VM's LAN address (for example `30.11.0.140`).

## 2. Model names

| Name | Model |
|---|---|
| `qwen3.8-27b` | Qwen3.8 27B: the main model. Vision, tools, thinking. |
| `qwen3.6-35b-a3b` | Qwen3.6 35B-A3B: fast MoE. Vision, tools, thinking. |
| `embed-text` | Qwen3 Embedding 0.6B (embeddings only). |

Ollama-style names (`qwen3.8:27b`, `qwen3.8:latest`, ...) work too. Any other
name goes to the fallback model (Settings → Models), so an app with an old
model name in its settings still gets an answer. Asking for the model that is
already loaded never waits for a model swap; asking for the other one waits
until the card can switch.

## 3. Ollama format (FamilyLog and other Ollama apps)

Ollama clients send no key, so the key goes in the address:

    http://HUB:11434/k/slqhub_.../

Set that as the app's "Ollama address" or base URL. Supported:

| Endpoint | Notes |
|---|---|
| `GET /api/tags`, `GET /api/ps`, `GET /api/version` | The Hub's models; `ps` shows what is loaded. |
| `POST /api/show` | Includes `model_info["<family>.context_length"]` and capabilities. |
| `POST /api/chat` | Streaming or not; `images` (base64) on messages; `tools`; `format` (`"json"` or a JSON schema); `think` true/false; `options` (`num_ctx`, `temperature`, `top_p`, `top_k`, `seed`, `num_predict`, `stop`, `repeat_penalty`). The stream carries `message.thinking`; the last line has `prompt_eval_count`, `eval_count` and the durations. |
| `POST /api/generate` | The same options; an empty prompt only loads the model (`done_reason: "load"`). |
| `POST /api/embed`, `POST /api/embeddings` | Embeddings. |

`keep_alive` is accepted and ignored: the Hub's scheduler decides what stays
loaded. Pull, push, create, copy and delete answer with an error: models are
managed in the Hub's settings.

**FamilyLog**: set the Ollama address as above, choose `qwen3.8-27b` for chat
and vision and `qwen3.6-35b-a3b` for categorising, and switch off "free
ComfyUI first" (leave the ComfyUI address empty). The Hub does that now.

Test from a shell on the LAN:

    curl http://HUB:11434/k/slqhub_.../api/tags
    curl http://HUB:11434/k/slqhub_.../api/chat -d '{"model": "qwen3.8-27b", "stream": false,
      "messages": [{"role": "user", "content": "Say hello in Danish"}]}'

## 4. OpenAI format

    base URL   http://HUB:8080/v1
    API key    slqhub_...          (Authorization: Bearer ...)

| Endpoint | Notes |
|---|---|
| `GET /v1/models` | |
| `POST /v1/chat/completions` | Streaming, tools, images (`image_url` with a data URL or an http URL the Hub can fetch), `response_format`, `reasoning_effort` (`low`, `medium`, `high`), `chat_template_kwargs: {"enable_thinking": false}` to answer without thinking. Responses include llama-server's `timings`. |
| `POST /v1/embeddings` | `model: "embed-text"`. |
| `POST /v1/images/generations` | Qwen-Image 2.1 through ComfyUI: `prompt`, `size` (`1024x1024`, ...), `n`, `quality` (`low` = the 8-step turbo model), `response_format` (`b64_json` or `url`). |
| `POST /v1/images/edits` | Multipart: `image` (one or more), `prompt`. |

With the OpenAI Python SDK:

```python
from openai import OpenAI
client = OpenAI(base_url="http://HUB:8080/v1", api_key="slqhub_...")
r = client.chat.completions.create(model="qwen3.8-27b",
                                   messages=[{"role": "user", "content": "Hello"}])
print(r.choices[0].message.content)
```

## 5. Anthropic format

    base URL   http://HUB:8080
    API key    slqhub_...          (x-api-key: ...)

`POST /v1/messages` (streaming, tools, images, `thinking`) and
`POST /v1/messages/count_tokens`. The Anthropic SDKs work with `base_url`
set; the model name is one of the Hub's.

```python
import anthropic
client = anthropic.Anthropic(base_url="http://HUB:8080", api_key="slqhub_...")
m = client.messages.create(model="qwen3.8-27b", max_tokens=1024,
                           messages=[{"role": "user", "content": "Hello"}])
print(m.content[0].text)
```

## 6. Browser apps (TwinCAT Coagent and other static pages)

A web page served from another address that calls the Hub from the browser
needs two things:

1. Its origin in Settings → Apps and keys → **Browser apps**, one per line,
   exactly as the browser sends it (`https://coagent.example.dk`, or
   `http://30.11.0.20:5500` for a page on the LAN). The Hub then answers the
   browser's CORS preflight for `/v1` and `/api`.
2. A key, sent as `Authorization: Bearer` (OpenAI format) or `x-api-key`
   (Anthropic format; the Hub also accepts
   `anthropic-dangerous-direct-browser-access`).

The page must be opened from the LAN or WireGuard, like any other client.

## 7. Errors and waiting

- A request waits in the queue while the card is busy with something else; the
  HTTP request stays open, and streaming starts when its turn comes. After
  `queue.max_wait_s` (Settings → Queue, default 30 minutes) it gets 503.
- `401`: missing, wrong or switched-off key. `403`: not from the LAN, or not
  from an address the key allows. `404`: unknown endpoint (or a model name
  with no fallback).
- Cancelling a request (closing the connection) frees the slot; llama-server
  stops generating.
