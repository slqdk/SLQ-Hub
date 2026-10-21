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
| Access | Web UI on a subdomain through Nginx Proxy Manager, with login. The model API answers only from LAN/WireGuard. |
| Users | One admin. Other apps get API keys. |
| Inference engine | llama.cpp (`llama-server`), started and stopped by the Hub. No Ollama: one queue, not two. |
| Language models | Qwen3.8-27B (main, vision, tools, thinking) and Qwen3.6-35B-A3B (fast MoE). |
| Main model settings | Q4_K_M-class quant, 64K context per conversation, KV cache q8_0. |
| Cloud | Automatic escalation for hard tasks, but **only in web chats inside projects where it is switched on** (off by default). API calls from apps never leave the house. |
| Images | Understand (vision), generate, edit, and classical processing (crop, scale, convert, OCR). |
| Image engine | ComfyUI, headless behind the Hub. |
| Other media in v1 | None (no speech, no video). |
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
    ├── searxng    Web search for the agent. Internal only.
    └── sandbox    Ubuntu with Python, Node, ImageMagick, Tesseract, ffmpeg …
                   runs the agent's commands as uid 1000 in /workspace.
                   Own network, blocked from the LAN by the VM's firewall.
```

Networks:

- `core` — hub, llm, comfyui, searxng.
- `sbx` — hub and sandbox only. The sandbox is not on `core`, has no Docker
  socket, and `DOCKER-USER` rules on the VM drop its traffic to private
  ranges (internet stays open for `pip install`, `git clone`, …).

Volumes (host paths, all under `/srv/slq-hub`):

| Path | Contents |
|---|---|
| `data/` | `hub.db`, settings, chats, uploads, generated images, project files, sync revisions, skills, memory, libraries. Backed up by `update.sh`. |
| `models/llm/` | GGUF files. Not backed up (re-downloadable). |
| `models/comfy/` | ComfyUI model folders (`diffusion_models/`, `text_encoders/`, `vae/`, …). |
| `comfy/` | ComfyUI input/output/user folders. |

---

## 3. The GPU scheduler (the queue)

There is one card. Everything that touches it goes through
`backend/app/gpu/scheduler.py`: web chat turns, API calls from apps, image
jobs and library indexing. The queue lives in memory, as in SLQ: a request
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

- The web UI shows "Waiting — 2 ahead" in the composer, and a queue drawer
  with the running and waiting jobs, owner, model, wait time, and a cancel
  button.
- An API caller just waits. Its HTTP request stays open until the job starts,
  and streaming then begins as normal. `max_queue_wait` (default 30 min)
  answers 503 after that.

### Example

An agent turn in the web UI runs on Qwen3.8 in slot 1. FamilyLog asks
Qwen3.8 to categorise 40 transactions, and runs at once in slot 2. Then FamilyLog
asks for Qwen3.6-35B-A3B. That job waits until the agent turn is done, the
card switches models, and it runs. If the agent turn asks for an image
meanwhile, it waits for FamilyLog's slot-2 call to finish, the card switches to
ComfyUI, renders, and switches back on the agent's next model call.

---

## 4. Models

Defaults (`backend/app/llm/catalog.py`, editable in Settings → Models):

| Id | File | Context | Notes |
|---|---|---|---|
| `qwen3.8-27b` | `unsloth/Qwen3.8-27B-GGUF` `Qwen3.8-27B-UD-Q4_K_M.gguf` (16.5 GB) + `mmproj-F16.gguf` (0.9 GB) | 80K shared pool (`-c 81920 -np 2 -kvu`) | KV q8_0, flash attention on. Vision, tools, thinking. |
| `qwen3.6-35b-a3b` | `unsloth/Qwen3.6-35B-A3B-GGUF` `Qwen3.6-35B-A3B-UD-IQ4_XS.gguf` (17.7 GB) + `mmproj-F16.gguf` | 64K | Fast MoE (3B active). Q4_K_M plus mmproj is too tight on 24 GB. |
| `embed-text` | `Qwen/Qwen3-Embedding-0.6B-GGUF` `Qwen3-Embedding-0.6B-Q8_0.gguf` | — | CPU (`--device none`), always running. |
| `rerank` | `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` | — | CPU, always running. |
| `embed-vl` | `mradermacher/Qwen3-VL-Embedding-2B-GGUF` Q8_0 + mmproj | — | Page images for libraries. CPU for queries; GPU (queued, background) for bulk indexing. |

VRAM budget for Qwen3.8-27B (from its config: 16 full-attention layers × 4
KV heads × 256 head dim): weights 15.3 GiB + mmproj 0.9 + recurrent state
0.3 per slot + KV q8_0 for an 80K pool ≈ 2.7 GiB + compute buffers ≈ 1.5 GiB
→ about 21 GiB. One conversation can use up to 64K tokens (the agent compacts
before that), and both slots share the pool.

Sampling comes from the model cards. Qwen3.8 with thinking: temperature 1.0,
top_p 0.95, top_k 20. Without thinking: 0.7 / 0.8 / 20, presence penalty 1.5.
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
Settings → API keys with a name, a priority and an optional IP restriction.
It is accepted three ways:

- `Authorization: Bearer slqhub_…`, which the OpenAI SDKs send.
- `x-api-key: slqhub_…`, which the Anthropic SDKs send.
- A key in the path: `http://hub:11434/k/slqhub_…/api/chat`. Ollama clients
  send no auth header, so FamilyLog only needs its "Ollama address" changed to
  `http://<hub>:11434/k/<key>`, with no code change.

**LAN guard.** API routes answer only to clients in `api_allowed_cidrs`
(LAN, WireGuard, VPN). Behind Nginx Proxy Manager the real client address
comes from `X-Forwarded-For`, trusted only from the proxy's IP, so the
subdomain serves the UI but not the API.

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

FamilyLog's own setting "free ComfyUI first" should be switched off:
the Hub does that.

---

## 6. The harness (the agent)

Every web chat turn is an agent task: one queue job holding a lease.

**Loop.** The loop runs like this:

1. Build the prompt.
2. Call the model (streamed).
3. Run the tool calls it makes, in parallel where they are independent.
4. Append the results and repeat until the model answers without tools,
   or a step limit is reached.

The UI receives every event as it happens over SSE (`text`, `thinking`,
`tool_call`, `tool_result`, `image`, `artifact`, `todo`, `step`, `queued`,
`escalated`, `done`). A reload replays from the last event it saw.

**Prompt assembly**, in this order:

1. Base instructions.
2. Environment: date, project, sandbox paths.
3. The project's instructions.
4. The index of enabled skills (name + description only).
5. The memory index.
6. Tool schemas.
7. History, compacted if needed.

**Tools**

| Tool | What it does |
|---|---|
| `web_search`, `web_fetch` | SearXNG; fetch with an SSRF guard (no private addresses), readable text extraction, truncation. |
| `list_files`, `read_file`, `write_file`, `edit_file`, `search_files`, `move_file`, `delete_file` | The project's folder (or the chat's scratch folder). Every write is checked in as a sync revision, so everything can be restored. `read_file` on an image puts the image in front of the model. |
| `bash` | Runs in the sandbox in the project folder, with a timeout and output limits. Long output goes to a file, and the model gets the head, the tail and the path. |
| `view_image`, `image_transform`, `image_ocr` | Look at an image; crop, resize, rotate, convert and compress (Pillow); Tesseract OCR (dan+eng). |
| `image_generate`, `image_edit`, `image_remove_background` | ComfyUI through the queue (the lease switches residency). |
| `skill` | Loads a skill's SKILL.md. Its other files are read with `read_file`. |
| `memory_*` | View, create, replace text in, and delete memory files. Global memory plus per-project memory. |
| `artifact` | Create or update an HTML, React, SVG, Mermaid, Markdown or code artifact, with versions. Shown in the side panel in a sandboxed iframe. React and Babel are served locally, so it works offline. |
| `todo_write` | A visible checklist for long tasks. It helps a 27B model keep track of them. |
| `task` | A subagent with a fresh context and a subset of tools, inside the same lease. Returns a summary. |
| MCP tools | From configured servers: streamable HTTP, or stdio servers that run inside the sandbox through a pipe in the sandbox daemon. These ask first, with "Always allow" per tool. |
| `escalate` | Present only where cloud escalation is on. |

**Approval.** Everything inside the sandbox runs without asking: files,
bash, image tools, web. MCP tools ask the first time.

**Thinking.** The default is on for agent turns in projects and off for
quick chats. A toggle in the composer overrides it. Qwen3.8 tends to
overthink, so the default effort is `medium`.

**Compaction.** At 75% of the model's context, older turns are summarised by
the model into a summary message that is stored with the chat. Old tool
outputs are cut to stubs first. A "Context compacted" step is shown.

**Steps.** Above each answer there is a collapsible tree:

- queue wait and model load;
- for each model call, prompt tokens (and how many came from cache),
  generated tokens and tok/s, from llama-server's `timings`;
- for each tool call, its duration.

**Cloud escalation** (web chats in projects with "Cloud escalation" on):

- **Triggers.** It starts when the model calls `escalate`, when three tool
  calls in a row fail, when the same call is repeated three times, or when
  the conversation outgrows the local context.
- **What happens.** The rest of the turn runs on the configured provider:
  Anthropic (Claude) or an OpenAI-compatible endpoint (DeepSeek, OpenRouter).
  Tools still run locally.
- **The card.** The lease is released, so the card is free while the cloud
  works.
- **Visibility.** Every escalation shows a badge with the reason and cost.

---

## 7. Projects and files (SLQ Sync)

A project is a set of instructions, files, chats, enabled skills, memory and
settings (default model, cloud escalation). Its files live in
`data/projects/<id>/` and the sandbox sees the same folder.

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

## 8. Images

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
- **Workflows** are built in code from the confirmed API-format graphs
  (`backend/app/images/workflows.py`), so the number of reference images can
  vary. Before each job they are checked against ComfyUI's `/object_info`, so
  a renamed node gives a clear error instead of a broken render. A workflow
  exported from ComfyUI ("Export (API)") can replace a built-in one: the binder
  finds the prompt, seed, size and image inputs.
- **Classical processing** (crop, scale, rotate, convert, compress, OCR) runs
  without the GPU: Pillow in the Hub, Tesseract in the sandbox.
- **Outputs.** Generated images appear in the chat and are saved in the
  project's `images/` folder when the chat is in a project.

---

## 9. Libraries (multimodal RAG)

- **Ingest** is a background job. PDF, images, DOCX, Markdown and text are
  read with PyMuPDF and python-docx. Each page is rendered to an image, and
  its text is extracted and chunked.
- **Index.** Text chunks are embedded with `embed-text` on the CPU. Page images
  are embedded with `embed-vl`, a GPU job at background priority, or the CPU
  when the card is busy for long. Vectors are stored in SQLite (numpy
  brute-force search is enough at this scale).
- **Query.** The question is embedded for both indexes. The results are merged
  (reciprocal rank fusion) and re-ranked by `rerank` on chunk text.
- **Answer.** The answer comes from the top pages, given to Qwen3.8 as text plus
  page images, with `[document p.N]` citations that link to the page.
- **Use.** A library can be attached to a project or a chat. The agent gets a
  `library_search` tool.

---

## 10. Web UI

The layout follows Claude:

- **Sidebar.** New chat, projects, recent chats, libraries, settings.
- **Chat.** Streaming markdown, code, images, collapsible thinking and tool
  cards, and the steps tree.
- **Composer.** Attachments, paste screenshots, model picker, think toggle,
  skills picker.
- **Side panel.** Artifacts or project files.
- **Header.** Queue indicator and drawer.

Settings are grouped by topic: models (download with progress, per-model
parameters), API keys, CORS, cloud provider, MCP servers, skills, memory,
workflows, sandbox, system (VRAM, disk, versions, logs).

On mobile it shows one column, and the panels become overlays. It is an
installable PWA (manifest, icons, a service worker that caches only the app
shell).

---

## 11. Security

- **Login.** The single admin account uses a scrypt password hash. Sessions
  are HttpOnly cookies that last 30 days, and are `Secure` behind HTTPS.
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
- **Ports.** ComfyUI and llama-server are never published on the LAN.

---

## 12. Operations

**Install** (`docs/INSTALL.md`):

1. `proxmox/create-vm.sh` on the host.
2. `install.sh` in the VM. It installs the HWE kernel, amdgpu-dkms, Docker,
   the firewall unit, the secrets, the images, the models and the admin
   password, then runs smoke tests.
3. Nginx Proxy Manager.
4. Point FamilyLog at the Hub.

**Update** (`update.sh`):

1. Fast-forward the clone.
2. Build the new images while the old ones serve.
3. Stop the app and back up `data/`.
4. Start the new version and wait for health.
5. On failure, roll back code, images and data automatically.

`update.sh status|backups|rollback` exist too.

**Logs.** `update.sh compose logs -f <service>`. The Hub keeps an error log
and per-model llama-server logs, viewable in Settings → System.

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
  this card (community numbers). `install.sh` runs `llama-bench` once so the
  real numbers are known.

---

## 14. Build order

1. Install path: VM script, `install.sh`, compose, images, `update.sh`, guide.
2. Hub core: settings, auth, scheduler, llm supervisor, the three API formats.
   **FamilyLog and Coagent can use the Hub from here.**
3. Web UI: login, chat, queue, settings, models.
4. Harness: tools, projects + sync, skills, memory, artifacts, subagents,
   compaction, MCP, escalation.
5. Images (ComfyUI) and libraries (RAG).
