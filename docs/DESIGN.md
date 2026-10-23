# SLQ Hub — design

SLQ Hub is a self-hosted assistant that tries to get as close as possible to
working with Claude, on one AMD Radeon RX 7900 XTX (24 GB) in a Proxmox VM.
It is also the **only owner of that GPU**: every other app on the network
(FamilyLog, TwinCAT Coagent, …) calls models through the Hub, and the Hub's
queue decides what runs when.

Nothing in it is TwinCAT-specific. Domain knowledge arrives as skills
(SKILL.md bundles) that are added later.

Decided 2026-10-21 in eight rounds of questions. This file records what was
decided and how the pieces fit; `docs/INSTALL.md` is the step-by-step guide.

---

## 1. Decisions

| Topic | Decision |
|---|---|
| Old GPU VM 104 (Ollama/ComfyUI) | Shut down and kept as a fallback, `onboot 0`. The Hub VM takes the 7900 XTX. |
| SLQ Companion, Hermes Agent | Retired. Nothing in the Hub exists to keep them alive. |
| Creating the VM | One script on the Proxmox host (`proxmox/create-vm.sh`): q35 + OVMF + cloud-init + the GPU's `hostpciN` line copied from VM 104. |
| OS | Ubuntu 24.04 LTS with the HWE kernel (7.0). AMD supports Radeon on 24.04.5 only with HWE. |
| Running services | Docker Compose. `update.sh` with backup and automatic rollback, as in SLQ and FamilyLog. |
| VM size | 40 GB RAM, 500 GB disk. |
| Access | Web UI on a subdomain through Nginx Proxy Manager, with login. The model API answers only from LAN/WireGuard, except for keys set to work away from home (a person's phone), which also work through the proxy. |
| Users | One owner (the admin). The owner can add people from the household (0.11.0): the same chat with their own login, at home or also away, their chats kept on their own device. Other apps get API keys. |
| Inference engine | llama.cpp (`llama-server`), started and stopped by the Hub. No Ollama: one queue, not two. |
| Language models | Qwen3.8-27B (main, vision, tools, thinking) and Qwen3.6-35B-A3B (fast MoE). |
| Main model settings | Q4_K_M-class quant, 64K context per conversation, KV cache q8_0. |
| Stuck turns | A small model that gets stuck hands the turn over to a stronger model on the server (Settings → Assistant, default Qwen3.8 27B), in the same lease. |
| Cloud | Automatic escalation for hard tasks, but **only in web chats inside projects where it is switched on** (off by default), and after the stronger local model. API calls from apps never leave the house. |
| Images | Understand (vision), generate, edit, and classical processing (crop, scale, convert, OCR). |
| Image engine | ComfyUI, headless behind the Hub. |
| Sound (0.13.0) | The agent makes speech, music and sound effects as audio files. Speech runs on the CPU in its own container (Chatterbox: Røst v3 for Danish, Chatterbox Multilingual V3 for 23 languages), so the chat model stays on the card; music (ACE-Step 1.5) and sound effects (Stable Audio 3) run in ComfyUI through the queue. No video. |
| Where images are made | In the chat only. No separate studio section. |
| API clients | FamilyLog (Ollama API) and TwinCAT Coagent (format unknown, so all three: OpenAI, Ollama, Anthropic, with a CORS allowlist). |
| Queue policy | Priority + FIFO. Interactive web chat before apps, apps before background work. FIFO within a priority. Jobs for the loaded model are grouped to avoid model swaps. |
| Queue granularity | A whole agent task (one user turn with all its tool calls) holds the GPU until it is done. |
| Parallelism | 2 slots per model: another request for the **same** model may run beside an agent task. Model swaps and image jobs wait. |
| Stack | Python 3.12 + FastAPI backend, React + TypeScript + Vite frontend (the same as SLQ and FamilyLog, so the queue, sync and Ollama code carries over). |
| Agent tools | Web search + fetch (own SearXNG), code execution in a sandbox, project files, MCP servers. |
| Harness features | Skills, memory, artifacts (live HTML/React), subagents + context compaction. |
| Approval | Runs freely inside the sandbox. Asks only for actions outside it (MCP tools), with "Always allow" per tool. |
| File sync | SLQ Sync ported with the **same protocol**, so the existing SLQSync.exe works against the Hub. |
| UI language | English. The model answers in the language it is spoken to. |
| Mobile | Responsive layout + installable PWA from the start. |
| Document libraries | Yes, multimodal RAG with citations. |

---

## 2. Topology

```
Proxmox host
└── VM "slq-hub"  Ubuntu 24.04 HWE · amdgpu-dkms 31.60 · Docker · 40 GB RAM · 500 GB
    │  RX 7900 XTX passed through (hostpci from VM 104)
    │
    ├── hub        FastAPI + built React UI. Owns auth, chats, projects, the GPU
    │              scheduler, the agent harness and every public API.
    │              Published: 8080 (UI + API) and 11434 (same app, Ollama-style URL).
    ├── llm        llama.cpp ROCm image + a small supervisor. Starts/stops one
    │              llama-server per model when the Hub says so. Internal only.
    ├── comfyui    ComfyUI on PyTorch ROCm 7.14. Internal only
    │              (127.0.0.1:8188 on the VM for maintenance through an SSH tunnel).
    ├── speech     Text to speech on the CPU (Chatterbox, Røst; CPU PyTorch).
    │              No GPU. Internal only.
    ├── searxng    Web search for the agent. Internal only.
    └── sandbox    Ubuntu with Python, Node, ImageMagick, Tesseract, ffmpeg …
                   runs the agent's commands as uid 1000 in /workspace.
                   Own network, blocked from the LAN by the VM's firewall.
```

Networks:

- `core` — hub, llm, comfyui, speech, searxng.
- `sbx` — hub and sandbox only. The sandbox is not on `core`, has no Docker
  socket, and `DOCKER-USER` rules on the VM drop its traffic to private
  ranges (internet stays open for `pip install`, `git clone`, …).

Volumes (host paths, all under `/srv/slq-hub`):

| Path | Contents |
|---|---|
| `data/` | `hub.db`, settings, chats, uploads, generated images, project files, sync revisions, skills, memory, libraries. Backed up by `update.sh`. |
| `models/llm/` | GGUF files. Not backed up (re-downloadable). |
| `models/comfy/` | ComfyUI model folders (`diffusion_models/`, `text_encoders/`, `vae/`, …). |
| `models/speech/` | The speech models (made by the Hub on the first download; the speech container mounts `models/` read-only). |
| `comfy/` | ComfyUI input/output/user folders. |

---

## 3. The GPU scheduler (the queue)

There is one card. Everything that touches it goes through
`backend/app/gpu/scheduler.py`: web chat turns, API calls from apps, image,
music and sound-effect jobs, and library indexing. CPU work does not ask the
queue: the CPU embedding models, and speech, which runs in its own container
without a GPU. The queue lives in memory, as in SLQ: a request
that was waiting when the backend restarted cannot be resumed anyway,
because its connection is gone. Finished jobs are written to the database
for statistics.

### Concepts

- **Residency**: what is loaded on the card. Examples: `llm:qwen3.8-27b`,
  `llm:qwen3.6-35b-a3b`, `comfy`, `embed-gpu`. One at a time.
- **Slots**: how many jobs a residency serves at once. LLMs have 2
  (`llama-server -np 2`), ComfyUI and GPU indexing have 1. The Hub never sends
  llama-server more requests than it has slots, so the queue the UI shows
  is the real one.
- **Job**: one unit of work with an owner (`web`, or an API key's name), a
  priority, the residency it needs, and its enqueue time.
- **Priority**: `interactive` (the web UI) → `normal` (apps, by default) →
  `background` (indexing, batch work). Each API key carries its own
  priority.
- **Lease**: an agent task holds its slot for the whole turn, including the
  pauses while tools run. Inside its lease it may switch residency, for
  example to ComfyUI for `image_generate` and back to the LLM afterwards.

### Admission (re-run on every state change)

1. Sort waiting jobs by (priority, enqueue time). The first one is the *head*.
2. If the head needs what is loaded and a slot is free, it starts. Then other
   waiting jobs that need the same residency fill any free slots.
3. If the head needs something else:
   - **Affinity**: while the head has waited less than `affinity_window`
     (default 20 s), waiting jobs for the loaded model with the same or better
     priority may still use free slots. This groups same-model work and saves
     swaps.
   - After that a **switch is pending**: nothing new is admitted. When the
     last running job releases, the scheduler unloads (llama-server stop or
     ComfyUI `/free`), loads what the head needs, and starts it.
4. A lease that asks to switch takes precedence over waiting jobs. It
   waits only for the other running jobs to finish; the lease keeps its slot.
5. Cancelling a waiting job removes it. Cancelling a running job aborts its
   HTTP stream: llama-server stops generating when the client disconnects, and
   ComfyUI gets `/interrupt`.

### What clients see

- The web UI shows the turn's state under the answer ("Waiting for the card,
  2 jobs ahead", "Getting the card ready", "Working"), and the GPU plate
  at the bottom of the sidebar shows what is loaded, the slots in use and how
  many wait. It opens the queue: running and waiting jobs with owner,
  priority, model, wait time and a cancel button.
- An API caller just waits. Its HTTP request stays open until the job starts,
  and streaming then begins as normal. After `queue.max_wait_s` (default
  30 min) it gets 503.

### Example

An agent turn in the web UI runs on Qwen3.8 in slot 1. FamilyLog asks
Qwen3.8 to categorise 40 transactions, and runs at once in slot 2. Then FamilyLog
asks for Qwen3.6-35B-A3B. That job waits until the agent turn is done, the
card switches models, and it runs. If the agent turn asks for an image
meanwhile, it waits for FamilyLog's slot-2 call to finish, the card switches to
ComfyUI, renders, and switches back on the agent's next model call.

---

## 4. Models

Defaults (`backend/app/catalog/models.json`; Settings → Models changes the
server parameters per model, stored in `data/models.override.json`). More
language models come from Hugging Face (Settings → Models → Add from Hugging
Face, `backend/app/hf.py`): any GGUF repository - the Hub groups its files into
quantisations (split files together, the vision projector apart), rates each
against the card's VRAM, and stores the entry in `data/models.custom.json`
(files under `models/llm/hf/<owner>--<name>/`, downloaded by the Hub's own
queue; `HF_TOKEN` in `.env` for gated repositories). They start with two slots
in a unified KV pool, q8_0 cache and the context chosen when adding them:

| Id | File | Context | Notes |
|---|---|---|---|
| `qwen3.8-27b` | `unsloth/Qwen3.8-27B-GGUF` `Qwen3.8-27B-UD-Q4_K_M.gguf` (16.5 GB) + `mmproj-F16.gguf` (0.9 GB) | 80K shared pool (`-c 81920 -np 2 -kvu`) | KV q8_0, flash attention on. Vision, tools, thinking. |
| `qwen3.6-35b-a3b` | `unsloth/Qwen3.6-35B-A3B-GGUF` `Qwen3.6-35B-A3B-UD-IQ4_XS.gguf` (17.7 GB) + `mmproj-F16.gguf` | 64K | Fast MoE (3B active). Q4_K_M plus mmproj is too tight on 24 GB. |
| `embed-text` | `Qwen/Qwen3-Embedding-0.6B-GGUF` `Qwen3-Embedding-0.6B-Q8_0.gguf` | — | CPU (`--device none`); started when needed, stopped after 15 idle minutes. |
| `rerank` | `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` | — | CPU, the same way. |
| `embed-vl` | `mradermacher/Qwen3-VL-Embedding-2B-GGUF` Q8_0 + mmproj | — | Page images for libraries. Always CPU for queries; indexing on the CPU by default, or on the GPU as a background queue job (Settings → Assistant → Libraries). |

**Sound models** (0.13.0; Settings → Models → Sound, music and speech; none
is downloaded by default). Details in section 8.

| Id | Files | Runs on | Licence |
|---|---|---|---|
| `roest-v3` | `CoRal-project/roest-v3-chatterbox-500m`: T3 (2.1 GB), Resemble's S3Gen (1.1 GB) and voice encoder, the voices Mic and Nic | CPU | OpenRAIL |
| `roest-v3-turbo` | `CoRal-project/roest-v3-chatterbox-350m`: T3 (1.9 GB), the two-step S3Gen (1.1 GB), its GPT-2 tokenizer, the same voices | CPU | OpenRAIL |
| `chatterbox-multilingual-v3` | `ResembleAI/chatterbox`: `t3_mtl23ls_v3` (2.1 GB), the built-in voice `conds.pt`; S3Gen, voice encoder and voices shared with Røst v3 | CPU | MIT |
| `ace-step-1.5-xl-turbo` | `Comfy-Org/ace_step_1.5_ComfyUI_files`: XL turbo bf16 (10 GB), Qwen3 0.6B encoder, 4B planner (8.4 GB), VAE | GPU | MIT |
| `ace-step-1.5-turbo` | the same repository: turbo (4.8 GB), 1.7B planner (3.7 GB), shared encoder and VAE | GPU | MIT |
| `stable-audio-3-medium` | `Comfy-Org/stable-audio-3`: checkpoint (9.2 GB), T5Gemma encoder (1.2 GB) | GPU | Stability AI Community (free under USD 1M revenue) |

Not chosen: YuE and OmniVoice (non-commercial licences), MiniMax Music
(unclear licence, slow), Stable Audio Open (superseded by Stable Audio 3).

VRAM budget for Qwen3.8-27B (from its config: 16 full-attention layers × 4
KV heads × 256 head dim): weights 15.3 GiB + mmproj 0.9 + recurrent state
0.3 per slot + KV q8_0 for an 80K pool ≈ 2.7 GiB + compute buffers ≈ 1.5 GiB
→ about 21 GiB. One conversation can use up to 64K tokens (the agent compacts
before that), and both slots share the pool.

Searching by name (Add from Hugging Face): the Hub asks
`/api/models?search=<longest word>&filter=gguf&sort=downloads` and keeps the
repositories whose id contains every word of the query; owner/name or a link
looks the repository up directly. Q8_0 is preselected where it fits the card
comfortably (small models), and 16-bit files say they are twice its size.

**Per model** (Settings → Models → Settings, stored in
`data/models.override.json` beside the server parameters): thinking in chats -
follow the chat's and Settings' choice, always think first, or never - and
sampling for answers with and without thinking (temperature, top_p, top_k,
min_p, presence penalty; checked against limits), which replaces the card's
values until reset. The order for thinking: the turn's toggle, the chat, the
project, the model's own choice, then Settings.

Sampling comes from the model cards. Qwen3.8 with thinking: temperature 1.0,
top_p 0.95, top_k 20. Without thinking: 0.7 / 0.8 / 20, presence penalty 1.5.
Models added from Hugging Face have no card values; those of the Qwen3 family
(the GGUF architecture starts with `qwen3`: stored when added, or read from
the file's header for models added before 0.9.1) get the same Qwen values.
Others leave sampling to llama-server. A model's own sampling (above) replaces
both. A sampling flag among a model's extra
llama-server flags (`--temp`, `--top-p`, `--top-k`, `--min-p`,
`--presence-penalty`, ...) wins: the request then leaves that value out.
The Qwen3.8 template accepts only `reasoning_effort` = `low`, `medium` or
`xhigh` and fails on anything else, so the Hub never sends any other value.

**Aliases.** Ollama-style names (`qwen3.8:27b`, `qwen3.8:latest`, …) and a
configurable fallback map onto catalog ids, so an app with an old model name
in its settings still gets an answer.

---

## 5. Public API (for FamilyLog, Coagent and anything else)

All of it goes through the queue.

| Format | Endpoints |
|---|---|
| Ollama | `GET /api/tags`, `POST /api/show`, `POST /api/chat`, `POST /api/generate`, `POST /api/embed`, `POST /api/embeddings`, `GET /api/ps`, `GET /api/version` |
| OpenAI | `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/embeddings`, `POST /v1/images/generations`, `POST /v1/images/edits` |
| Anthropic | `POST /v1/messages` (streaming, tools, images, thinking), `POST /v1/messages/count_tokens` |

**Authentication.** Every app gets a key (`slqhub_…`), created in
Settings → Apps and keys with a name, a priority and an optional IP restriction.
It is accepted three ways:

- `Authorization: Bearer slqhub_…`, which the OpenAI SDKs send.
- `x-api-key: slqhub_…`, which the Anthropic SDKs send.
- A key in the path: `http://hub:11434/k/slqhub_…/api/chat`. Ollama clients
  send no auth header, so FamilyLog only needs its "Ollama address" changed to
  `http://<hub>:11434/k/<key>`, with no code change.

`/v1/models` items carry extra fields - `name`, `kind` (chat, embedding,
image), `vision`, `thinking`, `efforts` (the thinking levels the model's
template takes, as `reasoning_effort`; empty when it only thinks or not),
`default`, `loaded` - for the phone chat page (section 10); other clients
ignore them.

**LAN guard.** API routes answer only to clients in `HUB_LAN_CIDRS` (LAN,
WireGuard, VPN; set in `/srv/slq-hub/.env`). Behind Nginx Proxy Manager the
real client address comes from `X-Forwarded-For`, trusted only from the
proxy's IP, so the subdomain serves the UI but not the API. Two exceptions,
both only through the proxy (a direct peer outside the LAN, like the sandbox,
is always refused): a key set to **At home and away** (per key, for a
person's phone: the chat page then works at `https://hub.slq.dk/chat`), and
every key when Settings → Apps and keys allows that. A key's address list
still applies. The 403 names the address the Hub saw (`client_ip`,
`via_proxy`), which is how "it says I am not at home" gets diagnosed.

**CORS.** Coagent may be a static page calling from the browser. Allowed
origins are a list in Settings.

**Ollama specifics** that FamilyLog relies on:

- `images` (base64) on messages.
- `format` (`"json"` or a JSON schema), mapped to llama-server
  `response_format`.
- `think` true/false, mapped to `chat_template_kwargs.enable_thinking`.
- `options.num_ctx`, `temperature`, `top_p`, `top_k`, `seed`, `num_predict`
  and `stop`.
- `message.thinking` in the stream.
- `prompt_eval_count`, `eval_count` and the durations on the final line.
- `/api/show` returning `model_info["<arch>.context_length"]`.
- `keep_alive` is accepted and ignored, because the scheduler decides.
- `GET /` and `HEAD /` answer 200 "Ollama is running". Enchanted (iPhone)
  sends `HEAD /` before every message and refuses to send when it fails.

FamilyLog's own setting "free ComfyUI first" should be switched off:
the Hub does that.

---

## 6. The harness (the agent)

Every web chat turn is an agent task: one queue job holding a lease.

**Loop.** The loop runs like this:

1. Build the prompt.
2. Call the model (streamed).
3. Run the tool calls it makes, one after another (MCP tools ask first).
4. Append the results and repeat until the model answers without tools,
   or a step limit is reached.

**Loop guard.** Small models can get stuck repeating a step (chat 7: Ornith
1.5 9B fetched the same two pages 52 times). Tools that only look things up
are marked `read_only` (web_search, web_fetch, read_file, list_files,
search_files, view_image, image_ocr, skill, library_search). Each turn keeps
an epoch that every other tool call advances. A read-only call repeated with
the same arguments in the same epoch is refused with a result that says so; a
failed one may be retried once. After three refusals, or the same step (text
and calls) three times, the next request goes without tools and with a note
to answer from what it has; any tool calls in that answer are dropped. The
event `loop` and a `loop` step record it, and the answer carries a `notice`
block (shown in the chat, never sent to a model). When someone can take
over (below), the turn is handed over instead of forced to answer; a
hand-over resets the guard.

The UI receives every event as it happens over SSE
(`GET /hub/api/runs/{id}/events`): `queued` (with the position), `started`,
`step`, `thinking`, `text`, `tool_start`, `tool_args`, `tool_call`,
`approval_needed`, `tool_result`, `artifact`, `todo`, `subagent`, `usage`,
`compacted`, `escalated`, `title`, `message_saved`, and finally `done`,
`error` or `cancelled`. A reload replays the run's events (kept for an hour
after it ends), and the stored messages are written step by step, so a
stopped or crashed turn keeps what it did.

**Prompt assembly** (`backend/app/agent/prompt.py`), in this order:

1. Base instructions with the date, the working folder and its sandbox path,
   images and artifacts guidance.
2. The memory index (file names and first lines).
3. The skills on offer (name + description only).
4. Who takes over when the model is stuck (a stronger local model, or the
   cloud where escalation is on) - or, after a hand-over, that the turn came
   from a smaller model whose steps should be checked rather than trusted.
5. The project's instructions.
6. The summary of the condensed part of the chat, if any.
7. Then the history; tool schemas go in the request's `tools`.

**What a turn leaves behind.** The working folder is listed before and
after each turn (`projects.snapshot`: size and modification time of every
file the sync rules do not ignore; folders over 20,000 files are skipped).
The new and changed files become a `files` step of the run (at most 50), the
card under the answer; a person's turn records the files it sends to the
device. The system prompt tells the model about the card, so it names the
file instead of pasting it and deletes scratch files.

The system prompt holds no clock beyond the date, so llama-server reuses its
cached prefix from step to step. The time of day goes with each user message
instead: the history gives every user message the local time it was sent
(`[Fri 23 Oct 2026 06:57]`, from its `created_at`, in `TZ` or Copenhagen
time), and an earlier message reads the same in every later turn, so the
cache stays valid from turn to turn too. People's devices send each
message's time with the conversation.

**Tools**

| Tool | What it does |
|---|---|
| `web_search`, `web_fetch` | SearXNG; fetch with an SSRF guard (no private addresses), readable text extraction, truncation. |
| `list_files`, `read_file`, `write_file`, `edit_file`, `search_files`, `move_file`, `delete_file` | The project's folder (or the chat's scratch folder). Every write is checked in as a sync revision, so everything can be restored. `read_file` on an image puts the image in front of the model. |
| `bash` | Runs in the sandbox in the project folder, with a timeout and output limits. Long output goes to a file, and the model gets the head, the tail and the path. |
| `view_image`, `image_transform`, `image_ocr` | Look at an image; crop, resize, rotate, convert and compress (Pillow); Tesseract OCR (dan+eng). |
| `image_generate`, `image_edit`, `image_remove_background` | ComfyUI through the queue (the lease switches residency). |
| `speech_generate`, `music_generate`, `sound_generate` | Audio files in the working folder (0.13.0, section 8): speech on the CPU (the speech service), music and sound effects in ComfyUI through the queue. Offered when a model for them is downloaded. |
| `skill` | Loads a skill's SKILL.md, or one of its other files (`skill(name, file)`). |
| `memory` | One tool with commands: view, create, str_replace, insert, delete, rename. Global memory (`/memories`) plus per-project memory (`/memories/project`). |
| `library_search` | Present when the chat or project has libraries: text and page-image search, with page images for the vision model. |
| `artifact` | Create or update an HTML, React, SVG, Mermaid, Markdown or code artifact, with versions. Shown in the side panel in a sandboxed iframe. React and Babel are served locally, so it works offline. |
| `todo_write` | A visible checklist for long tasks. It helps a 27B model keep track of them. |
| `task` | A subagent with a fresh context and the tools except artifact, todo_write, image and sound generation, task and escalate, inside the same lease. Its steps show nested under the call; only its report goes back. |
| MCP tools | From configured servers: streamable HTTP, or stdio servers that run inside the sandbox through a pipe in the sandbox daemon. These ask first, with "Always allow" per tool. |
| `escalate` | Present when someone can take over: the stronger local model (Settings → Assistant), or the cloud where escalation is on. Its description names who. |

**Approval.** Everything inside the sandbox runs without asking: files,
bash, image tools, web. MCP tools ask the first time.

**Thinking.** The default is on for agent turns in projects and off for
quick chats. A toggle in the composer overrides it. Qwen3.8 tends to
overthink, so the default effort is `medium`.

**Compaction.** At 75% of the model's context (Settings → Assistant) -
counting the tool definitions, which are not in the messages - old tool
outputs are shortened first; if that is not enough, everything before the
latest user message is summarised by the model into the chat's summary, which
goes into the system prompt from then on. The UI shows a notice. The summary
request itself must fit: old tool outputs are cut to their start, then long
answers, then the oldest messages are left out (with a note). Within one long
turn there is nothing before the user's message to fold, so the turn's own
older results, then older answers and tool-call arguments, are shortened -
in a small context down to only the newest result and answer whole.

**Messages while it works.** A message sent while a turn runs does not wait
for the end: it goes into the run's inbox (event `user_queued`), and the
loop hands it to the model at the next step boundary - after the tool
results of the step it is on, or instead of ending when the model had just
given its last answer. It is stored there, in the order the model read it
(with the run's id and `while_working` on its text, which the model sees as
"The user wrote this while you were working"), so tool calls and results
stay in pairs. A message left in the inbox when the turn ends - it came after
the last step, or the user pressed Stop - is stored as a normal message and
starts the next turn (not after an error). At most ten wait at a time.

**Steps.** Every run stores its steps: queue wait, model loads, each model
call (prompt tokens, cached tokens, generated tokens, tokens/s from
llama-server's `timings`) and each tool call with its duration. Each model
call also records what was sent (`sent`: sampling, thinking, effort,
max_tokens, number of tools and messages, the estimated prompt size) and why
it stopped (`finish`). The system prompt and the tools' names are kept in
`run_prompts` when they change (start, subagent, hand-over, compaction); a
text is stored once in `prompt_texts` however many turns use it, and goes
with the last run that used it. The UI shows a summary line under each answer
(model, time, tokens, tokens/s); the full list is in the chat's `runs` from
the API and in the troubleshooting export.

**Hand-over** (`Engine._stuck`). A turn is stuck when the loop guard trips,
when three tool calls in a row fail, when the same call is made three times
in six, or when the model calls `escalate`. Who takes over, in this order:

- **The stronger model on this server** (Settings → Assistant → "When the
  model gets stuck, hand over to", default Qwen3.8 27B), when it is
  downloaded and is not the one already working. The turn goes on in the same
  lease: the card switches before the next step (`ticket.switch`), thinking
  and effort are resolved again for the new model, the other model's
  reasoning is dropped (and images, if the new model cannot see), the system
  prompt says the turn came from a smaller model, and a note in the history
  says why. Once per turn; subagents never hand over.
- **The cloud**, in projects with cloud escalation, when the local model
  cannot (or already took over).
- **Nobody**: a loop ends in an answer without tools (loop guard, above).

Every hand-over shows a notice ("Handed over from Qwen3.6 35B-A3B to Qwen3.8
27B: the same step (web_search) 3 times."), emits `escalated` and records an
`escalated` step with `from`, `local` and the reason. The answer's footer
names both models.

**Cloud escalation** (web chats in projects with "Cloud escalation" on):

- **Triggers.** The same as a hand-over (above), when no stronger local
  model can take the turn.
- **What happens.** The rest of the turn runs on the configured provider:
  Anthropic (Claude) or an OpenAI-compatible endpoint (DeepSeek, OpenRouter).
  Tools still run locally.
- **The card.** The lease is released, so the card is free while the cloud
  works.
- **Visibility.** Every escalation shows a notice with the model and the
  reason, and the run's steps record it.

### People's chats: device turns (0.11.0)

A person (an account the owner added, `users.role = 'member'`) keeps their
chats in the browser. Each answer is a **device turn**
(`app/agent/device.py`):

1. The browser posts the conversation, the chat's files, the pictures of the
   last 12 messages and the latest version of each artifact to
   `POST /hub/api/device/turns` (at most 120 MB decoded; two running turns
   per person).
2. The Hub writes the files into a temporary folder
   (`workspace/device/<turn>`), stores the pictures, and runs the normal
   `Engine` with `engine.device` set: the person's name, no memory, no
   libraries, no MCP, no cloud escalation (a hand-over to the stronger local
   model is fine), bash only when allowed, artifacts kept in the turn
   (`DeviceInfo`), only the skills the owner switched on. Without a stored
   summary to fold old messages into, the oldest are left out until the
   context fits (the system prompt says how many).
3. Events stream from `/events`. The result carries the new messages, the
   files the assistant made or changed (25 MB each, 100 MB in all), the
   files it deleted (`removed`), the artifacts, the steps and the title.
4. The browser fetches the pictures (`/blob/<sha>`) and files (`/file`),
   saves the chat, then calls `/done`. The Hub deletes the folder, drops the
   run from memory and deletes every stored file the turn created. A reload
   in between picks the turn up again (the pending turn is in localStorage).

What the Hub keeps: nothing of the content. No chat, run, prompt or artifact
rows are written. Stored files are content-addressed and shared, so a turn
deletes only files that were new when it stored them, a file that the owner
(or a project, a library) stores meanwhile is released, and one another
running turn shows too is handed on to that turn (`blobs.on_store`, the
upload listener). The list of files a turn will delete is kept outside its
folder (`data/device-turns/<turn>`), where a person's code cannot edit it; a
startup sweep uses it after a restart. Turns whose device never comes back
are deleted after an hour, and a person has at most four such turns waiting.
ComfyUI keeps nothing either (`images.pipeline.EPHEMERAL`): after each job
the Hub deletes its history entry and, through the mounted folders
(`HUB_COMFY_FILES`), the output and input pictures and sounds. The speech
service keeps no voice prompt from a person's clip (`cache_voice` false). What
does stay: the queue log's row for each answer (the person's name, "Chat",
the model, the times).

---

## 7. Projects and files (SLQ Sync)

A project is a set of instructions, files, chats, enabled skills, memory and
settings (default model, cloud escalation). Its files live in
`data/workspace/projects/<id>/`, which the sandbox sees as
`/workspace/projects/<id>`. A chat outside a project has its own scratch folder,
`data/workspace/chats/<id>/` (deleted with the chat). Attachments and long tool
outputs go to `.hub/` inside the folder, which sync ignores.

**Files in a message.** In a chat, files of its folder can be put in the next
message (the + in the Files panel, or + -> Project files in the composer).
The message stores a `file_ref` block per file. A text file's content goes
into the blob store as it is at that moment and reaches the model before
what the user wrote, in a fence, marked "as it was when this message was
sent" - so a model that has since changed the file knows to read it again.
Files are taken in order while their text fits in 30% of the answering
model's context per request (`REF_SHARE`); the rest, binaries, PDFs and Word
files are named with a pointer to `read_file`. Pictures (PNG, JPEG, WebP,
GIF, BMP) become image blocks, like attachments. Like pictures, a file's text
gives way to a pointer once the message is older than the last twelve
stored messages; compaction treats it the same way. Paths are checked
against the folder (`Workdir.resolve`); at most 20 files per message.

`backend/app/projects/syncstore.py` is SLQ Companion's `syncstore.py`:

- **History.** Each project has a linear revision history. Contents live in a
  content-addressed blob store (`data/blobs/<sha256>`). A revision lists only
  the files that changed.
- **Snapshots.** The server folder is the head: it is snapshotted after every
  Hub write and before every client request.
- **Conflicts** are decided per file by the client's base sha. Nothing is
  overwritten; the client gets the server's version beside its own as
  `*.slqconflict-r<rev>.*`.

The routes are identical: `/api/sync/whoami`, `/api/sync/projects`, and
`/api/sync/projects/{id}/head|changes|blob/{sha}|checkin|revisions`, with
`Authorization: Bearer slqsync_…` device tokens. The existing SLQSync.exe and
`slq_sync.py` therefore work against the Hub. The server-side ignore list is
generic (`.git`, `node_modules`, `__pycache__`, `.venv`, `*.tmp`, `*.bak`,
`~$*`, `thumbs.db`, …). The TwinCAT patterns are not included.

The UI gets a file tree with preview and editor, the revision list with
restore, and the sync devices.

---

## 8. Images and sound

- **Vision input.** Pasted or attached images, and `read_file` / `view_image`
  on project images, go to Qwen3.8 as images. They are downscaled to at most
  1568 px on the long side.
- **Generate / edit / remove background.** Qwen-Image 2.1 in ComfyUI is
  one checkpoint for both generating and editing:
  - **Files.** `qwen_image_2.1_int8_convrot.safetensors` (7.3 GB) and
    `qwen3vl_8b_int8_convrot.safetensors` (9.4 GB) as text encoder, plus
    `qwen_image_2.1_vae_bf16.safetensors`. All three fit together on the card.
  - **Quality and speed.** The standard checkpoint takes 25 steps. A turbo
    checkpoint takes 8 steps when speed matters.
  - **Editing** takes up to 10 reference images through `TextEncodeQwenImage21`.
  - **Background removal** is the official template: an edit with the prompt
    "Remove the background, and output a PNG image". It saves PNG with alpha.
  - **FLUX.2 [klein] 4B** (Apache 2.0, 4 steps) is in the catalog as an
    optional fast model.
  - **ERNIE-Image and ERNIE-Image Turbo** (Baidu, 8B, Apache 2.0) are
    optional too: photorealistic, generation only (an edit with ERNIE chosen
    goes to Qwen-Image or FLUX.2 klein). Files from `Comfy-Org/ERNIE-Image`:
    the 16.1 GB diffusion model (base or Turbo), `ministral-3-3b` (7.7 GB) as
    text encoder, the 3B prompt enhancer (6.9 GB) and the FLUX.2 VAE it shares
    with klein. Base: 25 steps, cfg 4, empty negative; Turbo: 8 steps, cfg 1,
    zeroed negative; euler/simple - as in the official templates.
  - **Z-Image Turbo** (Alibaba, 6B, Apache 2.0) and **Chroma1-HD** (8.9B,
    FLUX.1 schnell based, Apache 2.0, no safety alignment) are optional
    catalog models, after the official templates (`image_z_image_turbo.json`,
    `image_chroma_text_to_image.json`). Z-Image: bf16 model (12.3 GB), Qwen3-4B
    as text encoder (the same file as FLUX.2 klein's, shared), the FLUX.1 VAE
    `ae.safetensors`; AuraFlow sampling with shift 3, 8 steps of res_multistep
    at cfg 1, a zeroed negative. Chroma: the fp8mixed model (9.2 GB) and fp8
    T5-XXL from the template, the same `ae.safetensors`; shift 1, CFGGuider
    3.5, euler over a beta schedule, the template's negative prompt unless the
    assistant gives one.
  - **SDXL checkpoints from a link** (Settings -> Models -> Add image model,
    `backend/app/images/sources.py`): a Civitai model or version page, or a
    Hugging Face repository or file. Civitai's API names each version's base
    model (SDXL 1.0, Pony, Illustrious, NoobAI ... count as SDXL); on Hugging
    Face the chosen file's header is read with two Range requests and must
    hold SDXL's UNet (the 2816-wide class embedding), both text encoders and
    the VAE. SD 1.x, refiners, FLUX, LoRAs and diffusers folders are refused
    with the reason. The entry (family `sdxl`, in `models.custom.json`) keeps
    its own settings, from a preset per kind: Pony (score tags, clip skip 2,
    Euler a, 25 steps, cfg 7, from its model card), Illustrious/NoobAI
    (quality tags and negative from NoobAI's card, Euler a, 28 steps, cfg 5),
    SDXL (ComfyUI's SDXL template: dpmpp_2m karras, 25 steps, cfg 7). One
    graph serves them all: CheckpointLoaderSimple, CLIPSetLastLayer for clip
    skip 2, the quality tags before the prompt, the model's negative plus the
    assistant's, the nearest SDXL training size. V-prediction checkpoints
    (NoobAI V-pred) work: ComfyUI reads the `v_pred` key. The file goes to
    `models/comfy/checkpoints/hub/<id>.safetensors`; Civitai downloads follow
    its redirect to storage with `CIVITAI_TOKEN` (sent to Civitai only).
  - **Families and abilities.** Each picture model in the catalog has a
    `family` (which graph) and `abilities` (generate, edit, cutout). Models
    that only generate hand edits to one that edits. `image_generate`'s
    description and model list are built per turn from the downloaded
    models, with the chat's own model named and a prompt hint per family.
  - **Which model makes a picture**: the one the assistant names in the call
    (when you ask for it by name), else the chat's choice (the picture button
    under the text field), else the project's (project Settings), else
    Settings → Assistant → Images. Turbo checkpoints that need their base
    (Qwen-Image Turbo) are not offered there; they serve "fast" requests.
  - **ERNIE's prompt enhancer** is the "prompt writer" step: in the same
    ComfyUI graph, `TextGenerate` on the enhancer expands the prompt (its own
    Chinese instruction plus `{"prompt", "width", "height"}` as JSON, exactly
    as the templates send it) before `ministral-3-3b` encodes it. A
    `PreviewAny` node puts the text in the job's history, and the image tool
    shows it in the chat. On by default; Settings → Assistant → Images turns
    it off. It needs no extra model switch on the card - it is part of the
    image job.
- **Workflows** are built in code from the confirmed API-format graphs
  (`backend/app/images/workflows.py`), so the number of reference images can
  vary. Before each job they are checked against ComfyUI's `/object_info`, so
  a renamed node gives a clear error instead of a broken render. (Custom
  workflows exported from ComfyUI are a possible later addition.)
- **Classical processing** (crop, scale, rotate, convert, compress, OCR) runs
  without the GPU: Pillow in the Hub, Tesseract in the sandbox.
- **Outputs.** Generated images appear in the chat and are saved in the
  project's `images/` folder when the chat is in a project.

### Sound (0.13.0)

Three tools make one audio file each in the working folder, and the card under
the answer plays it (`backend/app/audio/`, `backend/app/agent/tools/audio.py`).
A tool is offered only when a model for it is downloaded, never in subagents;
its description is built per turn from the downloaded models (voices, languages,
routing), and the system prompt says what each costs.

- **Speech (`speech_generate`)** runs on the CPU in the `speech` container
  (`services/speech/server.py`): Resemble AI's Chatterbox at a pinned commit on
  CPU PyTorch 2.11, an aiohttp server on the `core` network. It never asks the
  queue, like the CPU embedding models: the chat model stays loaded with its
  prompt cache, and a sentence costs seconds instead of two model swaps.
  - **Models.** Røst v3 Turbo (CoRal, 350M, Danish; reads English with a
    heavy accent, and the result says so; voice clips over five seconds),
    Røst v3 (0.5B, rated more natural by CoRal's listeners, about 1.5 times
    slower on the VM's CPU), Chatterbox Multilingual V3 (23 languages, a
    built-in voice, expressiveness; its Danish has an English accent in every
    sample heard - a limit of the model, not a setting: its tokenizer has
    Danish and æ/ø/å). Røst v3 and Chatterbox Multilingual V3 share S3Gen and
    the voice encoder (only T3 differs); Turbo has its own two-step S3Gen. In
    the listening test on the VM (0.13.2) Røst v3 Turbo sounded best, so it
    comes first in the catalog.
  - **Which model.** The one named in the call; otherwise Settings → Assistant
    → Sound: the voice model for Danish, or the one for other languages -
    unless that one does not speak the language and another downloaded model
    does. Empty settings mean the first downloaded in catalog order - for
    Danish, Røst v3 Turbo when it is there.
  - **Voices.** A model's own (Mic and Nic, described as male and female from
    the samples' median pitch; Chatterbox's `default`), or a clip in the
    working folder (5-20 s of one person). ffmpeg converts a clip in the
    sandbox - confined to the turn in a person's chat - to a 24 kHz mono WAV
    (its first 30 s, local files only), so no media library parses users'
    files in the Hub or the speech service. Both read WAVs with the same small
    reader (`wavfile.py`, `parse_wav`: PCM of 8-32 bits and 32/64-bit float -
    the Røst samples are float).
  - **How it speaks.** The text is cut into pieces of at most 260 characters at
    sentence ends (then commas, then words); each is spoken with the same voice
    and seed + its number, trimmed at -45 dB, and the pieces are joined with
    0.15 s pauses (0.45 s between paragraphs) and encoded once (MP3 96 kb/s,
    Opus 48 kb/s, FLAC or WAV). A ceiling of three speech tokens per character
    (at most 1000) stops a model that loses its place. As upstream does, the
    500M models' last token is dropped (emitted just before the end with weak
    attention, it decodes to about 40 ms of noise). Sampling as each model's
    makers set it: CoRal's for Røst (repetition penalty 2.0), Resemble's for
    Chatterbox (1.2). Expressiveness sets Chatterbox's exaggeration, with
    guidance 0.3 from 0.65 up (its own advice). Chatterbox's Perth watermark
    stays in.
  - **The service.** One request at a time; the progress per piece streams
    back as NDJSON (the tool row shows it), and a Hub that goes away stops the
    work after the piece in hand. At most two models in memory, the least
    recently used dropped first and its memory handed back (`malloc_trim`); all
    dropped after 15 idle minutes. A clip's voice prompt waits for the next
    request only when the Hub asks (`cache_voice`): for the owner, never in a
    person's chat. 6,000 characters per call. The `done` event says where the
    time went - T3's tokens and seconds, the decoder's seconds - and how many
    pieces ran to the token ceiling instead of stopping by themselves; the
    result then says the end may be noise and another seed usually helps.
  - **Speed: full weights only** (0.13.4). On VM 222 (Ryzen 9 5950X, AVX2,
    10 threads) Røst v3 takes about 20 s for 3.4 s of speech, a real-time
    factor of 6: T3 (one token at a time, 6.4 a second) 14 s, the ten-step
    decoder 5 s; Røst v3 Turbo about 3.9. 0.13.1-0.13.3 offered a Speech speed
    setting: 8-bit weights for T3 (dynamic quantisation, a scale per output
    channel) made it 2.3-2.5x quicker, and Turbo's distilled two-step decoder
    could serve the 500M models. The listening tests rejected it: with every
    layer in 8 bits Turbo skipped a word and Røst v3 slurred its first word,
    and with only the MLP layers in 8 bits (attention full) Turbo still said
    wrong words. Wrong words make speech useless, so the setting is gone and
    every model runs with its full weights and its own decoder. Also tried and
    not worth it on this CPU: bf16 (slower without native bf16), torch's int8
    weight-only matmul (4x slower than float), fewer threads for T3 (as fast
    on 5 as on 10 - memory, not cores, limits it - but no quicker). Running
    one piece's decoder while T3 makes the next could save perhaps 15-20 % on
    longer texts; not built, because both would draw from torch's global
    random generator and a seed would no longer repeat. Faster with full
    quality means the card - a model swap per request, or a speech model
    beside a small chat model, which the scheduler cannot do yet.
    `selftest --speech` speaks the same sentence and seed with each voice
    model, and Settings → Assistant → Sound plays the samples with what each
    cost, to choose by ear. Speech that would clip is turned down as a whole.
- **Music (`music_generate`)**: ACE-Step 1.5 in ComfyUI's native nodes, as in
  the official template: `UNETLoader`, `DualCLIPLoader` (type `ace`: the 0.6B
  encoder and the planner), `TextEncodeAceStepAudio1.5` (style, lyrics with
  section tags, BPM, key, time signature, language, length, seed),
  `EmptyAceStep1.5LatentAudio`, AuraFlow sampling with shift 3, 8 euler steps
  at cfg 1, `VAEDecodeAudio`, `SaveAudioAdvanced` (MP3 V0, FLAC or Opus
  128 kb/s). 10-300 s; no lyrics means an instrumental; English is strongest.
- **Sound effects (`sound_generate`)**: Stable Audio 3 Medium with its
  T5Gemma encoder: the prompt gets the tag the model was trained with
  (`TrackType: SFX`, `Instrument` or `Music, VocalType: Instrumental`, by
  `kind`), a BPM for loops and music, and the length; 8 lcm steps at cfg 1;
  0.5-380 s.
- **On the card**, music and sound effects go the way pictures do: the turn's
  lease switches to `comfy` (or the pipeline takes its own `audio` job), the
  graph is checked against `/object_info`, `run_job(kind="audio")` takes the
  file from the job's history, and in a person's chat ComfyUI's copy and
  history entry are deleted after the job. The sampler's steps and the
  planner's tokens show as progress in the tool row (at most twice a second).
  ComfyUI runs with `--disable-metadata` (0.13.5): otherwise every picture
  and sound file carries the prompt and the whole graph inside (an MP3 as a
  tag, which ffmpeg copies into anything mixed from it).
- **One piece of music per call** (0.13.5). In the first real music chat
  (chat 15) a small model made a "guitar solo" with `sound_generate` and a
  band with `music_generate` and mixed them: two pieces with their own key,
  tempo and beat. The system prompt and both tool texts now say that one
  `music_generate` call makes the whole arrangement (a solo goes in its
  prompt) and that music made separately never fits together; mixing speech
  or an effect over music does, and the system prompt carries an ffmpeg
  command that works (labelled filter output - the small model failed three
  times on an unlabelled one and handed over). Keep the parts of a mix, so
  it can be changed. In the sandbox, `ffmpeg` and `ffprobe` are wrappers
  that add `-hide_banner`: the banner is 2 KB of build flags on every call,
  in front of what went wrong.
- **Files.** Named from the text (`tredive-sekunder-tilbage.mp3`, then
  `-2`, ...), in a project under `audio/`. `save_as` may name a file (a path
  in the folder or a sandbox path) or a folder. In a person's chat a file must
  fit the 25 MB a device turn sends back: a FLAC over 120 s is refused before
  the card works on it, and a result too big says so.

---

## 9. Libraries (multimodal RAG)

- **Ingest** is a background job. PDF, images, DOCX, Markdown and text are
  read with PyMuPDF and python-docx. Each page is rendered to an image, and
  its text is extracted and chunked.
- **Index.** Text chunks are embedded with `embed-text` on the CPU. Page images
  are embedded with `embed-vl`, on the CPU by default (slow, but it never
  disturbs a chat), or on the GPU as a background queue job that gives the card
  back every 90 seconds. Vectors are stored in SQLite as float16 (numpy
  brute-force search is enough at this scale).
- **Query.** The question is embedded for both indexes. The results are merged
  (reciprocal rank fusion) and re-ranked by `rerank` on chunk text.
- **Answer.** `library_search` returns the best passages with file and page,
  plus up to two page images of the best visual matches for the vision model,
  and asks for citations as (file, page). What the re-ranker calls irrelevant
  goes below page-only hits (scanned pages, pictures), which it cannot judge.
- **Use.** A library can be attached to a project or a chat. The agent gets a
  `library_search` tool.

---

## 10. Web UI

The layout follows Claude; the look is a control cabinet: RAL 7035 grey
chrome, white paper for the work, engraved label plates (Barlow Semi
Condensed), and indicator lamps with their IEC 60073 meanings - green
running, amber waiting, blue your action is needed (a tool asks), red fault.

- **Sidebar.** New chat, search (titles and messages), projects, recent chats
  by date, libraries, settings, and the GPU plate (what is loaded, slot lamps,
  queue; it opens the queue).
- **Chat.** Streaming markdown with code, tables and KaTeX maths, collapsible
  thinking, tool calls as a terminal strip (one row per call, expandable to
  input and output), approvals inline, artifact cards, task lists, a summary
  line per answer, retry and edit-and-resend.
- **Files from an answer** (0.12.0). A card under the answer lists the files
  it made or changed in the working folder, newest first, whatever made them
  (a tool or a command such as ffmpeg). Each file gets its kind, folder, size
  and "new" or "changed"; sounds and videos play in the card and pictures
  show. Open puts it in the side panel (a PDF opens in a new tab), Download
  saves it, and Add to chat puts it in the next message. Over six files, the
  rest folds away. A person's card reads the files from the device.
- **Composer.** Attach, paste or drop files, files of the project (or the
  chat's folder) to put in the message, the model, the picture model,
  thinking (off, a little, normal, hard), libraries and extra skills.
- **Side panel.** Artifacts (sandboxed iframe, versions, source, save to the
  folder) or the folder's files, with a search and a + on each file that
  puts it in the next message. It shows pictures, plays sound and video, and
  shows PDFs (served without the `sandbox` CSP the other files get, which
  the browser's PDF viewer cannot run under).
- **Project page.** Chats, files (tree, CodeMirror editor, upload, history and
  restore per file), settings, revisions, sync devices.
- **Libraries.** Documents with indexing progress and a search to try.

Settings: models (download with progress, load, free the GPU, server
parameters; the sound models in their own group, each marked CPU or GPU),
queue, apps and keys (with connection examples and CORS), assistant (agent,
libraries, web, sandbox, images, sound: the model for music, for sound effects,
the voice models for Danish and for other languages, the speech speed, and
the last speech self-test's samples with players), skills, memory, MCP
servers (and the tools you always allow), cloud, system (services - the speech
service with its threads and loaded models - VRAM, disk, logs, theme,
password).

On mobile it shows one column, and the panels become overlays. It is an
installable PWA (manifest, icons, a service worker that caches only the app
shell).

**The phone chat page (`/chat`)** is a second, small page for the rest of the
household: one column, a drawer of chats, and a composer with Picture (the
Settings picture model; square, portrait, landscape), Think, a photo button
when the model can see, and the model. It is one more app on the model API
with its own key (people with a login use the Hub's own chat, below): it
answers only on the LAN, follows
the key's address limit and priority (its requests show in the queue under
the key's name), and cannot reach chats, projects, files, memory or settings.
Its conversations live in the phone's IndexedDB, never on the Hub. The key
comes in the link (`/chat#key=…`: read, kept in the browser, removed from the
address; a fragment is never sent to a server) or is pasted. It has no agent:
no web search, tools or files - plain chat, photos and pictures. The owner's
signed-in browser needs no key (the gate accepts the owner's session on the
model API; a person's session is refused there).

**People (0.11.0).** Settings → People adds a person: a name (what the
assistant calls them), a user name, a password (10+ characters), where the
login works (at home: `HUB_LAN_CIDRS`; or at home and away: also through the
proxy) and "May run code". The list has New password (it signs them out
everywhere), Sign out everywhere and Remove. A person who signs in gets
`PersonApp` (`frontend/src/person/`): the owner's chat components (composer
with model, picture model, thinking levels and skills; the turn view; the
artifact panel; the queue plate) over IndexedDB (`slq-hub-<user name>`:
chats, per-chat data, files, pictures). Pictures are made by asking; there is
no Picture button. The account menu has Export all chats / Import chats (one
JSON file with pictures and files), Change password and Sign out. A Files
panel lists the chat's files (attachments in `.hub/attachments/`, what the
assistant made), each sent with every message.

---

## 11. Security

- **Login.** The owner and the people the owner adds use scrypt password
  hashes. Sessions are HttpOnly cookies that last 30 days, and are `Secure`
  behind HTTPS. Failed sign-ins are throttled per address.
- **People.** The gate lets a person's session reach only `MEMBER_ROUTES`
  (models, a few settings, skills, the queue and its events, their own
  password, their device turns); everything else is 403, the sync API and
  the model API refuse it. A person limited to the home network is signed
  out anywhere else, and refused at sign-in from outside before the password
  is checked. Device turns are bound to their user; pictures are served to a
  person only from their own turns. Others' queue jobs show as "Another job".
- **People's code** (only with "May run code", and only where the sandbox
  reports Landlock) runs through `confine.py`: read and write beneath the
  turn folder (its home and `TMPDIR` are in it), read the system (`/usr`,
  `/etc`, `/opt`, `/proc`, ...), `/dev/null`-style devices - nothing else:
  not `/workspace`, `/home/sandbox`, `/tmp`, `/dev/shm` or terminals. With
  ABI 4+ it connects only to ports 53, 80 and 443; with ABI 6+ it cannot
  signal processes or reach abstract sockets outside. Landlock also denies
  reading other processes' memory and environment, but `/proc/<pid>/cmdline`
  stays readable: the command lines of the owner's commands and MCP servers
  running at the same moment are visible to a person's code (so keep secrets
  out of MCP arguments; they belong in its environment). The daemon is not
  dumpable (its token), and kills whatever a confined command leaves running
  (its process group and anything carrying the command's mark).
- **Keys and tokens.** API keys and sync tokens are stored only as hashes and
  shown once.
- **Sandbox.** The sandbox has:
  - no Docker socket;
  - uid 1000;
  - CPU, memory and pids limits;
  - its own network, dropped from private ranges by `DOCKER-USER` rules (a
    systemd unit re-applies them at boot);
  - public DNS servers (Docker's embedded DNS would otherwise forward to the
    LAN resolver).
- **Web fetch.** `web_fetch` refuses private and loopback addresses.
- **Speech service.** On `core` only: not published, and the sandbox's
  network cannot reach it. It reads only files under `/models/speech`
  (resolved paths; the models folder is mounted read-only), a voice file only
  as `.wav` and at most 25 MB, a clip only as WAV bytes (users' audio is
  converted by ffmpeg in the sandbox first), at most 6,000 characters. Model
  files load with `torch.load(weights_only=True)` or safetensors; Hugging Face
  is offline; it writes nothing but its tmpfs `/tmp`.
- **Phone chat page.** A client of the model API like any app (LAN guard,
  key, address limit). It renders answers with the UI's sanitising Markdown
  renderer and runs on the Hub's origin, so a signed-in browser opening it
  acts as the Hub's owner.
- **Ports.** ComfyUI and llama-server are never published on the LAN.

---

## 12. Operations

**Install** (`docs/INSTALL.md`):

1. `proxmox/create-vm.sh` on the host.
2. `install.sh` in the VM. It installs the HWE kernel, amdgpu-dkms, Docker,
   the firewall unit, the secrets, the images, the models and the admin
   password, then runs the self-test (`python -m app.cli selftest`: every
   service, the sandbox's isolation, one answer from the model with its
   tokens/s).
3. Nginx Proxy Manager.
4. Point FamilyLog at the Hub.

**Update** (`update.sh`):

1. Fast-forward the clone.
2. Build the new images while the old ones serve.
3. Stop the app and back up `data/`.
4. Start the new version and wait for health.
5. On failure, roll back code, images and data automatically.

`update.sh status|backups|rollback` exist too. An update makes the folders
the services mount when a new version adds one (owned by uid 1000).

**Self-test.** `python -m app.cli selftest` checks every service, the
sandbox's isolation and one answer with its tokens/s; `--images` makes a
picture, and `--speech` speaks the same sentence with each downloaded voice
model, reports the real-time factor with T3's and the decoder's share, and
saves each in `data/selftest/` (with `speech.json`), where Settings →
Assistant → Sound plays them.

**Logs.** `update.sh compose logs -f <service>`. The Hub keeps an error log
and per-model llama-server logs, viewable in Settings → System.

**A chat that behaves strangely.** Its ⋯ menu exports a troubleshooting ZIP
(`app/chats/export.py`): every stored message block and run (steps, timings,
tokens, errors), the live event log of runs still in memory (streamed pieces
joined), what the model would be sent next (system prompt, history after
condensing, tools, the request body), the settings that shape a turn (never a
key or token: cloud gives only whether a key is set, MCP only names and
status), the queue's jobs during the runs (its monotonic times turned into
clock times), the tail of llama-server's log, the chat's pictures and
attachments, and (0.13.5) the files its turns made or changed, as the folder
has them now - a song, a mix, a script that failed - newest turn first, up to
60 MB in all. The phone page exports its chats as JSON from the device.

---

## 13. Risks and things not yet verified on the hardware

- **GPU hand-over.** The 7900 XTX has a known reset problem: handing it from
  VM 104 to the new VM, or a VM reboot, may need a Proxmox host reboot.
  `create-vm.sh` sets VM 104 to `onboot 0`, so a host reboot is a clean way
  out.
- **PCIe atomics.** gfx11 in a VM needs PCIe atomics, or recent MEC firmware.
  VM 104 already ran ROCm, so this is expected to be fine. `install.sh` checks
  `dmesg` and stops with a clear message if the GPU is rejected.
- **The llama.cpp ROCm image** is built upstream but "not tested by CI beyond
  being built". The image tag is pinned, and `.env` can switch it.
- **Qwen3-VL-Embedding through llama.cpp** may not match transformers exactly.
  The text index carries retrieval if the image index is weak, and the image
  index can be switched off.
- **ComfyUI int8 kernels on gfx1100** (`comfy-kitchen`) are new. The bf16
  checkpoints are the fallback; the DiT is 14 GB, which still fits without the
  LLM loaded.
- **Throughput.** Expect roughly 30–38 tok/s generation for Qwen3.8-27B Q4 on
  this card (community numbers). The self-test and every answer's summary
  line report the real numbers.
- **MCP stdio servers** run through the sandbox daemon's pipe API; tested
  against a fake server only.
- **Streaming through Nginx Proxy Manager** needs `proxy_buffering off` (in
  docs/INSTALL.md); the Hub also sends `X-Accel-Buffering: no`.
- **Landlock in the sandbox container** needs the kernel's LSM list to include
  it and Docker's seccomp profile to allow its three system calls. Without
  it people simply get no code (the self-test's "sandbox for people" line).
- **Speech on the CPU** runs on the VM at full weights: a real-time factor
  of about 4 for Røst v3 Turbo and 6 for Røst v3 (0.13.1-0.13.3 self-tests).
  8-bit weights were about twice as quick but said wrong words, so they are
  gone (0.13.4). Real time with full quality would need the card, at the
  price of a model swap per request. Heard so far: one sentence per model;
  longer texts (the sentence splitting and pauses) not yet.
- **ACE-Step 1.5 and Stable Audio 3 on gfx1100** first ran on the card in
  chat 15 (2026-10-24), 10 s each: Stable Audio 3 Medium in 8 s, ACE-Step 1.5
  XL Turbo in 19 s (about 10 s of it loading, 8 s the planner), the LLM back
  in about 9 s. Still unknown: a 2-3 minute song's time and VRAM (XL is about
  20 GB with its planner), how they sound, and how well ACE-Step sings Danish.
- **People's chats in the browser** depend on the browser keeping IndexedDB.
  The app asks for persistent storage, but Safari deletes a website's storage
  after seven days of use without a visit to it - except for a page added to
  the home screen, which is what to do on an iPhone. Export all chats is the
  backup.

---

## 14. Build order

All five parts are built (0.1.0); see docs/HANDOFF.md for what has and has not
run on the real hardware.

1. Install path: VM script, `install.sh`, compose, images, `update.sh`, guide.
2. Hub core: settings, auth, scheduler, llm supervisor, the three API formats.
3. Web UI: login, chat, queue, settings, models.
4. Harness: tools, projects + sync, skills, memory, artifacts, subagents,
   compaction, MCP, escalation.
5. Images (ComfyUI) and libraries (RAG).
