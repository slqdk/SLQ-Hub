# SLQ Hub — design

SLQ Hub is a self-hosted assistant that tries to get as close as possible to
working with Claude, on one AMD Radeon RX 7900 XTX (24 GB) in a Proxmox VM.
It is also the **only owner of that GPU**: every other app on the network
(FamilyLog, TwinCAT Coagent, …) calls models through the Hub, and the Hub's
queue decides what runs when.

Nothing in it is TwinCAT-specific. Domain knowledge arrives as skills
(SKILL.md bundles, the format Claude uses): imported, or made in the Hub with
the assistant from the user's own material (§6 "Skills").

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
| Queue granularity | A whole agent task (one user turn with all its tool calls) holds the GPU until it is done - except while it waits for the user's answer to a question (0.16.0). |
| Parallelism | 2 slots per model: another request for the **same** model may run beside an agent task. Model swaps and image jobs wait. |
| Stack | Python 3.12 + FastAPI backend, React + TypeScript + Vite frontend (the same as SLQ and FamilyLog, so the queue, sync and Ollama code carries over). |
| Agent tools | Web search + fetch (own SearXNG), code execution in a sandbox, project files, MCP servers. |
| Harness features | Skills, memory, artifacts (live HTML/React), subagents + context compaction. |
| Approval | Runs freely inside the sandbox. Asks only for actions outside it (MCP tools; with the internet set to ask first, also going online): Allow once, Allow in this chat, Always, Deny. |
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
    │              llama-server per model when the Hub says so. Internal only,
    │              no way out (the inner network, below).
    ├── comfyui    ComfyUI on PyTorch ROCm 7.14. Internal only, no way out
    │              (127.0.0.1:8188 on the VM for maintenance through an SSH tunnel).
    ├── speech     Text to speech on the CPU (Chatterbox, Røst; CPU PyTorch).
    │              No GPU. Internal only, no way out.
    ├── searxng    Web search for the agent. Internal only.
    └── sandbox    Ubuntu with Python, Node, .NET 10 (C#), ImageMagick, Tesseract, ffmpeg …
                   runs the agent's commands as uid 1000 in /workspace.
                   Own network, blocked from the LAN by the VM's firewall.
```

Networks:

- `core` — hub and searxng: the two that use the internet (web pages, model
  downloads, search engines).
- `inner` (0.15.0) — hub, llm (172.31.251.11), comfyui (.12) and speech
  (.13); the Hub is .10. The model containers need nothing outside it, so
  `DOCKER-USER` and `INPUT` rules on the VM drop new connections from their
  addresses to anything outside `172.31.251.0/24`: the internet, the LAN and
  the VM itself. A model file, a ComfyUI node or a Python library in them
  cannot phone home. New connections inside the network (to the Hub, to each
  other) pass, and so do replies to the Hub's connections and to ComfyUI's
  maintenance port. Firewall rules rather than Docker's `internal` networks,
  which publish no ports - ComfyUI's maintenance port is published on the
  VM's loopback.
- `sbx` — hub and sandbox only. The sandbox is not on `core`, has no Docker
  socket, and `DOCKER-USER` rules on the VM drop its traffic to private
  ranges (internet stays open for `pip install`, `git clone`, …).

All of this was tested with Docker 29.9.1, the compose file's networks and
the real firewall script outside the VM (0.15.0): before the rules a model
container reached the internet, the LAN and the VM; after them none of the
three, while the Hub reached the models by name, the internet and the
sandbox, the sandbox kept its internet and lost the LAN and the models, and
both published ports answered. The Hub's default route went through `core`.
Moving running 0.14.0 containers from `core` to `inner` with `compose up`
worked too.

Volumes (host paths, all under `/srv/slq-hub`):

| Path | Contents |
|---|---|
| `data/` | `hub.db`, settings, chats, uploads, generated images, project files, sync revisions, skills, memory, libraries, the sandbox's home. Backed up by `update.sh` (not the logs, the sandbox's NuGet caches or .NET build output). |
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
  While the turn waits for the user's answer to a question (an MCP tool, the
  internet asking first), it gives the card up (`Ticket.pause`) and queues
  again with the answer (`Ticket.resume`: the same owner, residency, priority
  and lease; the same ticket, so its subagents keep working with it) - a
  person away from the screen holds nothing up (0.16.0). The chat shows the
  place in the queue if it has to wait then.

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
   - **The card must be empty for a chat model** (0.22.1). The scheduler only
     knows what it loaded, and ComfyUI can hold the card after a job the Hub
     gave up on: in the first test run on the card (2026-10-25) ComfyUI
     stopped answering during a video, kept its memory, and the next seven
     chat model loads failed (out of memory). Before an LLM loads, and when
     ComfyUI's `/free` leaves the card in use, the backend tells ComfyUI to
     interrupt what it runs, clear its queue and free the card, and watches
     the VRAM until it is under 2.5 GB (two minutes at most); a model that
     still did not fit (llama-server's "out of memory") is tried once more
     after that. A job the Hub stops waiting for is cancelled in ComfyUI
     (`/interrupt` for its prompt, and out of the queue); a ComfyUI that is
     slow to answer while a job runs (loading a large model) is waited for,
     not failed, and its last node list stands in when it is too busy to list
     them.
   - **Following a job** (0.22.2). The Hub hears a job's progress over
     ComfyUI's websocket, without keepalive pings: in the second test run the
     socket closed during a 14B video job, 162 s after its last message, and
     the Hub gave up - and cancelled - a job that ComfyUI's log shows going
     on for almost five minutes more (busy loading its second expert, it had
     not answered the client's pings).
     A closed socket now only means asking instead: `/history/<id>` until the
     job is there, every second; a job in neither the history nor `/queue` is
     gone - ComfyUI keeps both in memory, so it restarted (or the job was
     deleted) - and that ends the call at once, said as such; five minutes
     with no answer at all ends it too.
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

### Energy and cost (0.24.0)

Søren asked for the total cost per chat and per project at a fixed 3 kr per
kWh, and a token counter per person and for himself, with a usage limit for
the children's profiles to come.

**The reading.** The supervisor's `/gpu` adds the card's own power reading
from amdgpu's hwmon (microwatts): `power1_average`, else `power1_input`, and
the limit `power1_cap`. On VM 222 (RX 7900 XTX, read by Søren on 2026-10-25)
there is `power1_average` - 337 W while it built code, against a 339 W cap
(default 339, 305-350 W), and 65 W idle (after the 0.24.0 update, so with
the default chat model loaded) - and no `power1_input`; the label is PPT. It is
the card's figure, not the PC at the socket. At 65 W the idle card alone is
about 1.56 kWh a day, 4.68 kr at 3 kr per kWh.

**Splitting it** (`app/gpu/energy.py`, `EnergyMeter`). Time is cut at every
change of what runs or loads (the scheduler's new `on_change`, called on each
broadcast) and at every reading - every 2 s while something runs, every
60 s while the card is idle (a job starting after idle reads at once). An
interval's energy - the latest reading times its length (at most 30 s) - goes
in equal shares to the jobs running in it (`Job.energy_j`); while an idle card
loads for the head of the queue, to that job. Settings → Usage adds a fixed
number of watts for the rest of the PC while the card works (`costs.other_watts`,
0). What the card draws while nobody uses it is kept apart ("The card, idle").
A finished job's energy goes to its run (`meta["run_id"]`): a turn can be
several jobs (it gives the card up while it waits for an answer), and the turn
takes the sum when it ends.

**Counting it** (`app/usage.py`, table `usage_daily`: local day, who, project).
An agent turn is counted once, when it ends (`finish_turn`): its usage gets
`energy_wh`, `cost` (kr at the price of the day, `costs.price_per_kwh`, 3.00)
and `gpu_seconds` - shown under the answer, and kept on the device for a
person's - and the day's row for who ran it ("owner", "tests" or
"user:<id>", with the project) gets the answer, its tokens (read = the prompt
less what came from llama-server's cache, written, from the cache), energy,
cost and seconds. Any other job is counted when it finishes (`job_finished`):
an app's call on the model API under "key:<name>" with the tokens its handler
noted on the job, library indexing under "library", loading models under
"system". The queue log keeps each job's energy too. Rows stay when chats go.

**Where it shows.** Under each answer: time, tokens, energy and cost. A chat's
⋯ menu: its total (answers from before 0.24.0 carry no energy and are said to
be unmeasured). A project's page: its total since counting began (chats
deleted since included) and each chat's cost. Settings → Usage: the card's
draw now and its limit, then per who for today, this month or all time -
answers and jobs, tokens read and written, time on the card, energy, cost - with
a total; the price and the rest of the PC's watts. `usage.who_total(who,
period)` is what a limit for a person can read.

---

## 4. Models

Defaults (`backend/app/catalog/models.json`; Settings → Models changes the
server parameters per model, stored in `data/models.override.json`). More
language models come from Hugging Face (Settings → Models → Add from Hugging
Face, `backend/app/hf.py`): any GGUF repository - the Hub groups its files into
quantisations (split files together, the vision projector apart - also one named
`...-vision-f16.gguf` rather than `mmproj...`; MTP heads and draft models for
speculative decoding left out; two files of one quantisation, like `Model-Q4_K_M`
and `Model-noMTP-Q4_K_M`, by what their names do not share), rates each
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

**Video models** (0.19.0; Settings → Models → Video models; none is
downloaded by default). Details in section 8.

| Id | Files (`Comfy-Org/Wan_2.2_ComfyUI_Repackaged`) | Abilities | Licence |
|---|---|---|---|
| `wan2.2-5b` | TI2V 5B fp16 (10 GB), Wan 2.2 VAE (1.4 GB), umt5-xxl fp8 (6.7 GB, from the Wan 2.1 repository) | description, picture | Apache 2.0 |
| `wan2.2-i2v-14b` | i2v high and low noise experts fp8_scaled (14.3 GB each), lightx2v 4-step LoRAs v1 (1.2 GB each), Wan 2.1 VAE, umt5-xxl | picture, first and last picture | Apache 2.0 |
| `wan2.2-t2v-14b` | t2v experts fp8_scaled, lightx2v 4-step LoRAs v1.1, Wan 2.1 VAE, umt5-xxl | description | Apache 2.0 |

Searching for a model that is in the Hub's own list (Seed-VC, Wan) in the
Hugging Face dialog points to its section instead: that dialog adds GGUF chat
models only.

VRAM budget for Qwen3.8-27B (from its config: 16 full-attention layers × 4
KV heads × 256 head dim): weights 15.3 GiB + mmproj 0.9 + recurrent state
0.3 per slot + KV q8_0 for an 80K pool ≈ 2.7 GiB + compute buffers ≈ 1.5 GiB
→ about 21 GiB. One conversation can use up to 64K tokens (the agent compacts
before that), and both slots share the pool.

**Context per chat** (0.26.0). The server's context (`-c`, the shared pool)
and what one chat's request may use (`--kv-unified-per-slot`, the agent's
budget, what `/api/show` and `/api/ps` report) are two settings: Settings →
Models → Settings has both (`server.ctx`, `server.per_request`). Empty, the
second is the card's `context_per_request`; it is never more than a slot has
(the pool with a unified cache, else an equal share) and must be at least
4,096 and at most the context. Before, only the pool could be changed, and a
chat kept the card's 65,536 whatever the pool (chat 74 ran with 128,000). The
dialog says what one chat may use now and what the model was trained for
(`{arch}.context_length` from the file's header, `app/gguf.py`). The KV cache
is reserved when the model loads, so a pool too big for the card stops
llama-server from starting; per token the 27B's q8_0 cache takes 16 × 4 × 256
× 2 values ≈ 34 KiB by the numbers above (not measured). A change reaches
llama-server when the model is loaded again; a turn in between learns the
running server's size from its refusal (§6 "The real token count"). Null for
the context, the slots or the RAM cache means the card's value again; a slot
count below one is refused.

**Delete files** (0.27.0). A downloaded model from the Hub's own list (any
group) can give its disk space back: `DELETE /hub/api/models/{id}/files`
deletes the files that no other model uses - downloaded or being downloaded,
its own files and those of the models it requires (Wan's text encoder serves
the 5B, the Turbo and the 14B pair; Qwen-Image 2.1 Turbo requires the base) -
and Download fetches them again. Refused while it is in use: on the card,
waited for by a queued job, the chat model, the apps' fallback, the hand-over
model, chosen under Assistant (Images, Sound, Video), ComfyUI working, or a
library reading with the helpers. The list says what it would give back
(`freeable`); with nothing to give, the button is off. A model the owner
added goes with Remove, as before.

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

**Names (0.14.0).** Settings → Models → Rename gives a chat or picture model
the owner's name: `name` becomes it everywhere a person reads a model's name -
the composer, the turn's footer, the hand-over notice, the picture menu, tool
results, Settings, the queue plate, people's chats and the phone page (its
list comes from `/v1/models`' `name`). A bundled model keeps it in
`data/models.override.json` (`llm` or `comfy`, `name`) and its own name as
`name_card`, with "Use Qwen3.8 27B" (its own name) to go back; one the owner
added changes its entry in `models.custom.json`. The id - what apps, keys,
aliases and settings use - never changes, and `/api/tags` lists ids. Two
models in a group cannot share a name. The model's own texts (its system
prompt, who takes over, the image tools' model lists) say both,
`Catalog.label`: "You run as Store hjerne (Qwen3.8 27B)", so what it knows
about the model still applies. Sound and helper models keep their names (texts
and descriptions refer to Røst and Chatterbox by name).

**Order (0.17.0).** Settings → Models has arrows on each chat and picture model;
the owner's order is stored in `data/models.override.json` (`order`: `llm`,
`comfy`, lists of ids) and applied when the catalog loads, so everything that
lists models follows it - the composer's model and picture menus, Settings,
people's chats, and `/v1/models` and `/api/tags` (the phone page's list).
Models the order does not name come after the ones it does, in the catalog's
order (bundled, then added), so one added later goes last; a removed one
leaves the order. `PUT /hub/api/models/order` (`group`, `ids`), the owner's
only.

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

**Not finished, and Continue** (0.22.0). A turn that reaches the step limit
(Settings → Assistant → "Most steps in one turn", 60 by default) ends with a
note in the answer, a `limit` step in the run (`{"type": "limit", "steps":
60, "depth": 0}`) and a `step_limit` event; one the user stops ends
`cancelled`. Either may have stopped in the middle of the work - chat 21's
card showed the game's file as if it were done, with the game freezing at
Start. The answer then says so: "Not finished: the turn stopped after 60
steps" (or "it was stopped before it was done"), in the files card above the
files ("files so far") or on its own when there are none, with **Continue**
under the last answer. The files stay listed: the Hub cannot tell a finished
file from a half-written one, and they are in the folder anyway. Continue
(`POST /hub/api/chats/{id}/continue`, 409 unless the last turn is one of
those) adds a user message with only `{"type": "continue", "reason":
"limit"|"stopped", "steps": n}` - shown as a "Continued" mark, not as the
user's words - and starts a turn; the model reads it as "(SLQ Hub: the user
pressed Continue. Your last turn ended at the step limit (60 steps …) before
the work was done. Go on from where you stopped: check the state of what you
were making before you change it, and do not start over.)". A person's chat
does the same on the device (the block passes the device turn's check with
only its reason and a sane number). A turn stopped before it wrote anything
has no answer to mark; the user's own message can be edited and sent again.

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

**Cut-off calls** (0.16.2). An answer that ends at the end of the context, or
at the limit for one answer (finish `length`), may stop inside a tool call.
Chat 21: the model put a whole game into one `write_file` with 48,596 of
65,536 tokens already in use, and after 16,940 tokens the context was full,
31 KB into the file's content. Such a call does not run. Its result says it
was cut off and where - the context's end with the tokens on each side, or the
limit for one answer - and to write a long file in parts (`write_file`, then
`write_file` with `append: true`, as the system prompt says too). A cut-off
`write_file` whose path is whole and whose cut is in its content keeps what it
had: the content so far is written (appended, if the call said so), and the
result shows its last 300 characters for the model to continue from, with
`append`. Arguments that are not valid JSON - cut off or not - never go back to
the model server as they are: llama-server parses every earlier call to
render the chat template, and refused the whole next request (a 500), so the
turn failed and every later message in that chat would have too. The history
carries them as they were when they parse, repaired when the tolerant parser
can, else `{"_not_valid_json": "<their first 300 characters>..."}` - within a
turn and from a stored chat alike. The stored blocks keep what the model sent,
for the UI and the export.

**A part that would replace the file** (0.21.0). Chat 21's next turn wrote the
game in parts, and twice sent the next part without `append: true`: the part
replaced the 18 KB written so far, and the model spent 12 of its 60 steps
building the file again (the turn then ended at the step limit with the game
unfinished). A file the turn has added to with `append` is no longer replaced
by a `write_file` without it: the call is refused, its result giving the
sizes and the two ways on - `append: true` for the next part, `replace: true`
to start over. A file written whole may still be written whole again (the
turn's own record, `ToolContext.state["written_in_parts"]`).

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
3. The skills chosen for the chat and its project (0.23.0): their SKILL.md
   text, up to 40,000 characters together; longer ones are named with their
   description and loaded with `skill` (§6 "Skills").
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
| `web_search`, `web_fetch` | SearXNG; fetch with an SSRF guard (no private addresses, the connection made to the checked address, every redirect checked), readable text extraction, truncation. |
| `list_files`, `read_file`, `write_file`, `edit_file`, `search_files`, `move_file`, `delete_file` | The project's folder (or the chat's scratch folder). Every write is checked in as a sync revision, so everything can be restored. `read_file` on an image puts the image in front of the model. `write_file` with `append: true` adds to a file's end (0.16.2): a long file goes in parts. |
| `bash` | Runs in the sandbox in the project folder, with a timeout and output limits. Long output goes to a file, and the model gets the head, the tail and the path. C# with the .NET 10 SDK (0.15.1, below). |
| `windows_app` | A Windows desktop program (Windows Forms or WPF on .NET 10) run under Wine on a virtual screen in the sandbox, built first if it is a project: screenshots when its first window is up, at each `shot` step and at the end; steps click (by position or by the text on the screen), type, press keys, drag, scroll and wait (0.16.0, "Windows programs on a virtual screen" below). The owner's chats, where the sandbox has Wine. |
| `web_app` | A web page, browser app or game from the working folder - or an HTML, React, SVG or Mermaid artifact, as the side panel runs it - opened in Chromium in the sandbox: screenshots, the console, uncaught errors with file and line, failed requests, alert texts; steps click (by position, CSS selector or text), type, press and hold keys, move, scroll, wait and evaluate JavaScript (0.29.0, "Web pages in a browser" below). The owner's chats, where the sandbox has the browser. |
| `view_image`, `image_transform`, `image_ocr` | Look at an image; crop, resize, rotate, convert and compress (Pillow); Tesseract OCR (dan+eng). |
| `image_generate`, `image_edit`, `image_remove_background` | ComfyUI through the queue (the lease switches residency). |
| `speech_generate`, `music_generate`, `sound_generate` | Audio files in the working folder (0.13.0, section 8): speech on the CPU (the speech service), music and sound effects in ComfyUI through the queue. Offered when a model for them is downloaded. |
| `skill` | Reads one of the chosen skills: its SKILL.md, or one of its other files (`skill(name, file)`; PDF, Word, Excel and PowerPoint as text, pictures shown). Offered when the chat has skills. |
| `skill_propose` | The skill the assistant made in the working folder: checked against the format, kept as a draft and shown as a card with Add skill (0.23.0, §6 "Skills"). Not in subagents. |
| `memory` | One tool with commands: view, create, str_replace, insert, delete, rename. Global memory (`/memories`) plus per-project memory (`/memories/project`). |
| `library_search` | Present when the chat or project has libraries: text and page-image search, with page images for the vision model. |
| `artifact` | Create or update an HTML, React, SVG, Mermaid, Markdown or code artifact, with versions. Shown in the side panel in a sandboxed iframe. React and Babel are served locally, so it works offline. |
| `todo_write` | A visible checklist for long tasks. It helps a 27B model keep track of them. |
| `task` | A subagent with a fresh context and the tools except artifact, todo_write, image and sound generation, task and escalate, inside the same lease. Its steps show nested under the call; only its report goes back. |
| MCP tools | From configured servers: streamable HTTP, or stdio servers that run inside the sandbox through a pipe in the sandbox daemon. These ask first, with "Always allow" per tool. |
| `escalate` | Present when someone can take over: the stronger local model (Settings → Assistant), or the cloud where escalation is on. Its description names who. |

**Approval.** Everything inside the sandbox runs without asking: files,
bash, image tools, web - unless the internet asks first (below). MCP tools
ask the first time. Every question offers Allow once, **Allow in this chat**
(0.16.0: the owner's chats), Always (the owner's) and Deny. "This chat" is
kept per chat (`chat_permissions`: the tool, or the `internet:` key the
question was about) and goes with the chat; its subagents share it; the
chat's ⋯ menu lists what was allowed there ("Allowed in this chat"), each
with Take back (`GET /hub/api/chats/{id}` → `allowed`, `DELETE
/hub/api/chats/{id}/allowed[?tool=]`). While a question waits, the turn's
lease is given up (section 3).

**Internet for the assistant** (0.15.0, `app/agent/internet.py`). Settings →
Assistant → Web → "Internet for the assistant": **Allowed** (the default, as
before) or **Ask first** (`web.access = "ask"`). Asking first, a call that
would go online stops the turn and asks in the chat, at the call, like an MCP
tool: Allow once, Allow in this chat, Always, or Deny.

| Call | Asks | "Always" remembers |
|---|---|---|
| `web_search` | every search, showing the query | "Always allow web search" (`internet:search`) |
| `web_fetch` | per site, showing the URL | "Always allow www.dr.dk" (`internet:site:www.dr.dk`; the exact host as the connection names it - IDNA, so a look-alike name shows as `xn--...` - and `dr.dk` is another) |
| `bash` | only with `internet: true`; otherwise the command runs offline | "Always allow internet for code" (`internet:code`) |
| `windows_app` | never where code can be kept offline: the program and its build run offline (online where internet for code is always or in this chat allowed); where it cannot, every run asks | as `bash` (`internet:code`) |
| `web_app` | as `windows_app`: offline the page gets only its own files (anything from the web is listed as blocked); where code cannot be kept offline every run asks, showing the JavaScript its steps run | "Always allow internet for code" (`internet:code`) |

- **Offline code.** The sandbox daemon runs the command under `confine.py
  --offline`: Landlock (ABI 4+) refuses every TCP connection, and a seccomp
  filter the ways around Landlock's rule, which sees only `connect()` on a
  plain TCP socket - MPTCP sockets (which reach any TCP server), TCP Fast
  Open (`sendto`/`sendmsg` with `MSG_FASTOPEN` opens a connection without
  `connect()`), every other socket family and protocol, io_uring, and the
  32-bit and x32 system call tables. Offline, UDP sockets go too, so no DNS
  lookup or QUIC; Unix sockets and netlink stay, and a local server can still
  listen (nothing can connect to it over TCP). In its Landlock domain the
  command cannot ptrace or read the memory of a process outside it either
  (tried: a uid-1000 process unconfined can attach to another, offline it
  gets "Operation not permitted"), so it cannot drive an online process that
  way. A person's online command keeps UDP (DNS needs it; the firewall still
  keeps the sandbox off the LAN) and its ports 53, 80 and 443. Files as usual: the owner's
  commands keep their access, a person's confined command its usual limits.
  All of it was tried for real (kernel 6.18, ABI 7), also inside a Docker
  29.9.1 container with the default seccomp profile, as the sandbox runs: each
  way out open without the filter, refused with it. The daemon tries an
  offline command when it starts and says on `/health` whether it works
  (`offline`); where it does not (no Landlock ABI 4, or the filter cannot be
  installed), code cannot be kept offline, so every command asks, and the
  question says why. The self-test's "offline code" line probes it on the VM.
  A failed offline command's result tells the model to run it again with
  `internet: true`, which asks.
- **Once is once.** A command allowed online with "Allow once" has what it
  leaves running ended with it (the daemon's run mark, as for people's
  commands), so no background process keeps the internet for later offline
  commands. A process that clears its environment and leaves its session can
  outlive it - the mark's limit.
- **Redirects.** Asking first, `web_fetch` follows a redirect only within the
  site the user said yes to (with or without `www.`) or to a site the owner
  always allows; to another it stops, and the result gives the model the new
  address to fetch - which asks.
- **What the model knows.** The system prompt and the tool descriptions say
  that the user has it ask first (and no longer that the sandbox has internet
  access), and `bash` gets its `internet` parameter only then. A denied call
  returns "The user declined this tool call"; no answer in ten minutes is a
  deny. A declined call does not count as a failed one, so three of them do
  not hand the turn over to a stronger model or the cloud.
- **Where it asks.** `approval_needed` carries `ask` (what, the detail - whole,
  or cut with a note saying so - the "always" text, `can_always`); a
  subagent's question carries `depth` and `parent`, so it shows inside its row,
  and the turn says "Waiting for your answer" then too. The answer goes to
  `POST /hub/api/runs/{id}/approve` (`allow`, `chat`, `always`, `deny`), and
  `approval_answered` takes the buttons away at once (a reload replays it) -
  before the turn queues for the card again. While the turn waits, the card
  is free for other work (section 3).
- **Always.** The owner's answers are kept with the tools they always allow
  (`tool_permissions`, `internet:` keys) and listed under "Always allowed
  online" in the Web section, each with "Ask again"; the MCP tab leaves them
  out.
- **People.** A person is asked in their own chat, once each time (Allow
  once, Deny; `POST /hub/api/device/turns/{id}/approve` refuses "always"):
  their chats leave nothing on the Hub, and the owner's answers are the
  owner's.
- **What the browser loads.** Asking first, a picture from the web in an
  answer or a Markdown artifact shows as a link ("Picture from example.com -
  not loaded"), and other things that load by themselves (video, audio, SVG
  images, backgrounds in styles) lose their web address, so what the model
  writes cannot make the browser fetch from the web - a picture's address can
  carry data out. `/hub/api/auth/status` says `internetAsks`. HTML, React, SVG
  and Mermaid artifacts (0.16.0) get a Content-Security-Policy as the first
  thing in their page: inline code, `data:` and `blob:`, and the Hub's own
  artifact runtime (React, Babel, Tailwind, Mermaid) - no scripts, styles,
  fonts, pictures, connections, frames or form posts from anywhere else; their
  frame loses `allow-popups`. Two things the policy alone does not close, a
  small guard script at the top of the page does: Chromium does not gate WebRTC
  by `connect-src`, so the guard removes `RTCPeerConnection` and its kin (a data
  channel could otherwise carry data out past the policy); and `frame-src 'none'`
  blocks a frame's content but a frame of its own would get fresh WebRTC, so the
  guard strips any frame a script adds (frames are blocked here anyway). A frame
  navigating itself away (a link, `location`, a `meta` refresh) is not something
  its own policy can stop, so the UI's page carries `frame-src 'self'` while
  asking (sent with the page, which is never cached: after switching to "Ask
  first", a page loaded before keeps the old rule until it is reloaded). Tried
  in Chromium: nothing reached the web, the fetch rejected, a link left the frame
  on Chrome's error page; with a separate harness, neither a direct
  `RTCPeerConnection` nor one in a nested `srcdoc` frame reached a STUN server.
  This is defence in depth - the per-call approval is the real gate.
- **Not covered.** Offline code can still talk, over a Unix socket or through
  files, to a process that is online already (one a command allowed "always"
  left running, say). SearXNG talks to the search engines
  (that is the search); image URLs that apps send to the model API are
  fetched as before (the app's request, not the assistant's choice - with the
  same guard, LAN ranges included); MCP servers ask by their own rule; the
  Hub's model downloads are not the assistant's.

**C# in the sandbox** (0.15.1). The sandbox has the .NET 10 SDK from Ubuntu's
archive (`dotnet-sdk-10.0`, about 600 MB without the AOT compiler it
recommends): console programs, libraries, ASP.NET and tests build and run
there.

- **Build output.** Each command gets `ArtifactsPath=<working folder>/.hub/dotnet`
  from the Hub, so .NET's artifacts layout puts `bin/` and `obj/` there - out
  of sync, the files card and the Files panel - instead of beside the
  project, where every build would be checked in and synced to the user's PC
  (Linux paths in `obj/` against Visual Studio's). A project that sets its own
  output keeps it (an environment property is the weakest kind).
- **No build servers.** `MSBUILDDISABLENODEREUSE`, `DOTNET_CLI_USE_MSBUILD_SERVER=0`
  and `UseSharedCompilation=false`: nothing of a build keeps running and
  holding memory after the command. No telemetry, banner, developer
  certificate or workload update checks.
- **Windows programs** (0.16.0). The Windows Forms/WPF reference pack and
  the win-x64 app host are in the image, beside the SDK's own packs
  (`services/sandbox/dotnet_windows.py` at the image build: Microsoft's files
  as conda-forge repackages them for win-64, pinned by SHA-256, put under the
  version the SDK's `BundledVersions.props` asks for - a 10.0.12 pack serves
  an SDK that asks for 10.0.13; then a Windows Forms project is built with no
  package source at all, or the build fails). So `EnableWindowsTargeting=true`
  is set for every project (in 0.15.1 it made every restore fetch the pack)
  and Windows projects build offline. The system prompt says to target
  `net10.0`/`net10.0-windows` (other versions need packages) and to hand over
  the program with `dotnet publish -c Release
  -p:DefaultAppHostRuntimeIdentifier=win-x64 -o publish`: the .dll with a
  Windows .exe beside it, which runs on the user's PC with the .NET 10 Desktop
  Runtime. A RID-specific publish (`-r win-x64`) wants the Windows runtime
  packs from NuGet even framework-dependent - only for `--self-contained`
  (no runtime needed, about 150 MB). Where the sandbox has Wine, the model
  sees the program with `windows_app` (below); elsewhere (a person's chat)
  the prompt says Windows programs build but cannot run here.
- **Offline and people.** Builds work offline once their NuGet packages are
  in the cache (`~/.nuget/packages` in the sandbox's home); a restore that
  needs the internet asks while it asks first. A person's build goes to
  `.hub/dotnet` in her turn's folder, with her packages in her turn's home -
  fetched again for each answer, never sent to her device.

**Windows programs on a virtual screen** (0.16.0, `windows_app` in
`app/agent/tools/windows.py`, `services/sandbox/winshot.py`). The sandbox
image has 64-bit Wine 9 from Ubuntu's archive (no i386 packages, so no 32-bit
programs), Xvfb, xdotool, the X tools and the Windows build of the .NET 10
runtime with Windows Desktop in `/opt/dotnet-win` (about 1 GB more image).
The Hub runs `winshot --json {...}` in the sandbox like a bash command - so as
the owner, offline while the internet asks first - and reads the screenshots
from `.hub/windows/<call>/` in the working folder (only those paths, checked
as pictures, then deleted), and shows them to the model with the steps' and
windows' description; for a model without vision it adds the text Tesseract
reads in each window.

- **One run.** The target is a project (`.csproj`, or the folder with one:
  `dotnet build` first, its errors are the result), a .NET `.dll` (run by
  the Windows `dotnet.exe`) or a 64-bit `.exe` (the app host .NET builds finds
  the runtime through `DOTNET_ROOT`; a .NET Framework or 32-bit program is
  refused with the reason). Xvfb on a free display (no TCP), the program
  started, the first window awaited (a program that ends first, its exit code
  and console output, an unhandled .NET exception included), the steps, then
  everything Wine started ended (`wineserver -k`) and the screen stopped. One
  program at a time (a lock in the prefix; Wine's server is shared). Each
  call starts the program afresh. Default screen 1024x768, timeout 60 s (300
  at most) after the build.
- **Coordinates.** Every screenshot starts at the top-left corner of the
  program's first window and reaches to the right and bottom edges of all its
  windows (dialogs, menus, drop-down lists), so a click at (x, y) is that
  pixel of a screenshot. A click may name a text instead: OCR (Tesseract,
  `eng+dan`) on the screenshot made three times larger, then each window alone
  (a drop-down list's highlighted row otherwise hides the rows under it), then
  inverted - the first place the words are read, as a whole word sequence,
  else as the start of words.
- **Typing** (0.16.1) goes through HubKeys, a small Windows program
  (`HUBKEYS_PROGRAM` in winshot.py) built into the prefix with it -
  `winshot --setup` builds it with no package source and starts it once under
  Wine to see it answer. At a run's first type step it starts beside the
  program (0.1-0.2 s) and sends each text with `SendInput`, as Windows'
  on-screen keyboard does: a character on the keyboard as its key, with Shift
  when it needs one, anything else as a Unicode packet (`KEYEVENTF_UNICODE`),
  which arrives as exactly that character - Æ Ø Å, other scripts and emoji
  included. Wine's `VkKeyScanW` answers a character that is not on the
  keyboard with the key of a look-alike (č: c, Ж: ?), so each key is checked
  with `ToUnicode` first. `\n` is Return, `\t` Tab.
  - **The barrier.** The text goes straight into Wine's input queue, but
    clicks and keys go through the X server, which Wine reads only when the
    program takes its next input: a busy program got a text before the click
    sent ahead of it (measured: the second box's text landed in the first).
    So before each text winshot holds Shift down through X and HubKeys waits
    until Wine reports it down (`GetAsyncKeyState`, 5 s at most), then the
    same for up - every click and key before it is in the queue ahead of the
    text. If the program does not get there in 5 s, it types anyway and the
    step says the text may have gone ahead.
  - **Why not through X** (0.16.0 did; measured under Wine 9.0 with the
    image's packages, uid 1000, `C.UTF-8`). 0.16.0 remapped a spare key code
    to each character's Unicode keysym and pressed it with XTEST. Wine lost
    the first key (its `+key` trace: the key map change reached it only after
    that key, so the key had no virtual key, `VK_NONAME`; after any real key
    the change came in time), turned every Latin-1 letter into U+FFFD (such a keysym
    comes back from Xlib as one Latin-1 byte, which Wine decodes as UTF-8;
    the legacy keysym passed, and Unicode keysyms beyond Latin-1 did too),
    put U+FFFD for every letter into WPF, and gave a busy program other
    letters (43 typed at 120 ms of work each came out as `br5.`): Wine looks
    up a key's character when the program translates it, from the key map as
    it is by then. Legacy keysyms fixed the letters only; 250 ms between keys
    did not bring the first one back. `xdotool type` remaps the same way, and
    pressed no Shift for capitals.
  - **What a program sees.** Typed characters arrive as `WM_CHAR`. A key on
    the keyboard also gives its `WM_KEYDOWN`/`WM_KEYUP`; a Unicode packet
    gives `VK_PACKET` there (`Keys.Packet` in a KeyDown handler), as on
    Windows. Keys (`key` steps), clicks and drags still go through X.
- **How Wine is set up** (once, in the image: `winshot --setup` as the
  sandbox user, the prefix `/opt/hub-wine`, fresh with every new container).
  Measured, each against the alternative: no virtual desktop (in Wine 9's, a
  window that moved itself, as WinForms' CenterScreen does, was drawn at twice
  its position and its menus opened at the real one); Wine draws Windows'
  frames itself (`Decorated=N`, there is no window manager) and manages its
  windows itself (`Managed=N`: as managed windows without a window manager,
  each dialog's appearing and closing waited two seconds for one, and keys
  pressed meanwhile were late); programs are told it is Windows 8.1 (as
  Windows 10, opening a WinForms menu ended the program: "Failed to get
  thread's DpiAwareness context"); no crash debugger (`winedbg` off: the .NET
  exception stays readable); no menu entries, Mono or Gecko. The copies of
  Wine's own DLLs that `wineboot` puts in the prefix are links to the
  originals: 19 MB instead of 688.
- **An init in the sandbox** (`init: true` in compose): Wine's server and
  services outlive each run's process and became zombies under the sandbox
  daemon, which does not reap them.
- **Who.** The owner's chats, where the sandbox's `/health` says `windows`
  (`winshot --check`: files only), asked every few minutes before a turn
  writes its prompt. Not in a person's chat: her code may write only her
  turn's folder.
- **Fidelity.** Wine is close to Windows, not the same: fonts (Wine's Tahoma,
  Liberation for Arial and friends) and some controls look a little
  different, and the OS version a program reads says 8.1. The prompt says so;
  the user tries the program on a real PC.

**Web pages in a browser** (0.29.0, `web_app` in `app/agent/tools/webapp.py`,
`services/sandbox/pageshot.py`). The assistant made web pages and games it
never saw: chat 79's Championship Manager page "tested" itself by starting
`python3 -m http.server &` in the sandbox and listing its files, and was handed
over with a data file that did not parse (an apostrophe in "Japhet N'Doram")
and so a team list and a player list that threw. The sandbox image has
Playwright (pinned, in its own Python, `/opt/pageshot`) and only Chromium's
headless shell with its libraries (`/opt/ms-playwright`); the Hub runs
`pageshot --json {...}` like a bash command - as the owner, offline while the
internet asks first - and reads the screenshots from `.hub/web/<call>/` (only
those paths, checked as pictures, then the folder removed; also when the turn
is stopped, and runs older than ten minutes are swept).

- **Served, not opened as a file.** pageshot answers every request to
  `http://page.local/` from the page's folder through Playwright's request
  routing - no server, no port, nothing that needs the network - so a page
  that `fetch()`es its own level data works as on a web server, which a
  `file://` page does not. The site's root is the page's folder (or `root`);
  nothing outside it is served, after a folder's `index.html` is added and
  every link followed. Names are escaped into the address ("spil #1.html").
  Other hosts load only online and are listed as blocked offline. The
  browser's own blocking does not cover everything (a WebRTC or prefetch
  connection) - offline, the whole run is under `confine.py --offline`.
- **One run.** The page loaded (and its network quiet for up to 3 s), a
  screenshot, the steps, a screenshot at the end (or at each `shot`). What
  comes back: each step's result (a click by CSS selector or by the words on
  the page, with where it landed; an `eval`'s value as JSON), the console
  with where each line came from, uncaught errors with their stack - a
  script that does not parse has none, so an error listener installed before
  the page's scripts gives its file, line and column (chat 79: "Unexpected
  identifier 'Doram' at data.js:78:33") - requests that failed (a missing
  file is a 404), `alert()` and `confirm()` texts (dismissed), and the page's
  text for a model without vision. A held key repeats as a keyboard does (a
  quarter second, then 30 a second), so a game that moves on keydown moves.
  Waits and holds are cut to the run's time (default 30 s, at most 120); a
  page that stops answering - a script that never ends, an eval that never
  settles - still gives what came before, printed when the time and a grace
  are up, with the step it hung at.
- **Artifacts as the panel runs them.** `artifact: "<id>"` writes the
  artifact's newest version into the run's folder as `ArtifactPanel.tsx`
  builds it (`artifact_doc` is a copy of `artifactDoc`; a test runs the
  TypeScript with node and compares) with the Hub's artifact runtime beside
  it, and runs it like the panel's frame: a Content-Security-Policy `sandbox`
  with the frame's flags (no storage, its own origin), and while the internet
  asks first the panel's offline policy and guard. 760x820 by default, the
  panel's size on a big screen. The first artifact opened this way showed
  that every React artifact failed in the panel: the bundled Babel 8 has
  removed preset-typescript's `isTSX`/`allExtensions`, which the panel
  passed; it now takes TSX from the `.tsx` file name.
- **Who.** The owner's chats, where the sandbox's `/health` says `browser`
  (`pageshot --check`: Chromium starts, tried when the daemon starts - not at
  the image build, so a browser that will not start cannot stop the image
  from building), asked every few minutes before a turn writes its prompt.
  The self-test opens a small page offline, types Danish letters into it and
  clicks its button by its text.

**How it works** (0.29.0). Four rules in the base prompt bring it closer to
how Claude works: for a known file format, protocol, game or program, look
for its documentation and existing tools first (chats 74-79 spent hours on
Championship Manager's data files byte by byte; a community package with
them turned up late); in long work, keep what is found in a notes file in the working folder
as it goes, what was checked apart from what is only guessed (it outlasts the
chat); before calling a task done, check it the way the user will use it -
run it, open or play it (`web_app`, `windows_app` where there), compare with
a reference - and measure with code rather than reason at length; and end
with what was done, what was checked and what could not be checked.

**Thinking.** The default is on for agent turns in projects and off for
quick chats. A toggle in the composer overrides it. Qwen3.8 tends to
overthink, so the default effort is `medium`.

**Compaction.** At 75% of the model's context (Settings → Assistant) -
counting the tool definitions, which are not in the messages - old tool
outputs are shortened first; if that is not enough, everything before the
latest user message is condensed by the model into the chat's notes, which
go into the system prompt from then on. The UI shows a notice. Since 0.27.0
the notes are structured (Goal, Decisions and facts, Files, Done, Open
questions, Next) and each condensing replaces them - given the old notes, the
model carries over what still matters; appended, the summary grew with every
condensing until it left room for nothing else. The user reads and corrects
them in the chat's ⋯ menu (Notes; `PUT /hub/api/chats/{id}/notes`, refused
while the chat works, at most 20,000 characters). The summary
request itself must fit: old tool outputs are cut to their start, then long
answers, then the oldest messages are left out (with a note). Within one long
turn there is nothing before the user's message to fold, so the turn's own
older results, then older answers and tool-call arguments, are shortened -
in a small context down to only the newest result and answer whole.

**The real token count** (0.26.0). The sizes above are estimates from the
characters (3.2 a token), and some text costs far more tokens a character:
chat 74's hex dumps made its last request 62,286 tokens where the estimate
said 45,004, so it was never condensed, its answer was cut at the end of the
64K context and the next request was refused. Each local model step now
measures real tokens per estimated token (llama-server's `prompt_tokens`
over the estimate it sent, between 1 and 3) and every later size - fitting,
condensing, the room left for the answer, the summary request - is scaled by
it. A turn starts from its chat's last measured step (subagents from their
parent). When new text is denser still and llama-server refuses a request as
longer than its context ("request (N tokens) exceeds the available context
size (M tokens)"), N sets the ratio, M caps the turn's context (a server keeps
the size it was started with until it is loaded again, whatever Settings say
now), and the request is fitted again, once (a `refit` step; a forced answer
keeps its instruction). A step's `sent` keeps `token_ratio` when it was above
1. A summary so long that the request to condense more could take in fewer
than 1,000 estimated tokens of old messages is not extended: folding would
move `summary_upto` past messages it never read, so the turn is shortened
instead.

**A lean context** (0.27.0). A model answers worse in a long context well
before it runs out, so the context is kept small all along, not only at 75%:

- Tool output from turns before the previous one keeps its first 400
  characters (when longer than 1,500), with how much is left out and where
  to get it (`.hub/outputs/…` when the result was stored there, else
  chat_history or calling the tool again). The previous turn keeps its
  output whole - a follow-up often asks about it. The same rows are
  shortened the same way every turn, so llama-server's prompt cache holds.
- Within a turn, once a request passes half the context, all tool results
  but the newest four are cleared to their start in one go - only when that
  frees at least 15% of the context, since every change to an older message
  makes llama-server read the request again from there. A shortened result
  shortened again stays exactly the same unless it is cut deeper, and keeps
  its count and pointer. The results of the last step stay whole in every
  stage (the model has not read them) except the very last resort. After a
  cut the loop guard's epoch moves on, so a cleared look-up may be repeated.
- The main agent's prompt says what a subagent is for: big or many files,
  long pages or logs, hex dumps go to `task` with a precise question, and
  only the answer comes back.
- **0.28.4 (chat 78, measured):** a model with linear-attention layers
  (Qwen3.5 and later, as Søren's Qwen3.8 27B) can resume llama-server's
  cache only from a checkpoint - in chat 78 from the end of the system
  prompt and tools, 12,432 tokens. A change to any older message therefore
  means reading almost the whole request again: four steps in a row trimmed
  a little each (the request hovered at the 75% budget) and read about
  36,000 tokens again each, about a minute a step. Three causes, three
  changes. Thinking: the 27B wrote 134,000 characters of it in 33 steps,
  more than all results and calls together, and the current turn's thinking
  goes back with every step; the clearing at half the context now also
  leaves out the thinking of all but the newest two steps
  (`trim_reasoning`), so it frees its 15% in one go. Headroom: over the
  budget, the lighter cuts (older thinking, results beyond the newest six to
  1,500 characters, beyond two to 400) go down to 60% of the context, so the
  next steps only add to the request. Stability: an older answer or call cut
  again changed its count ("shortened by 33 characters" became "32") - an
  older message changed at every step; `cut_text` now leaves a cut text as
  it is unless it cuts deeper (the count adds up), like `shorten_result`.
  Replayed on chat 78's steps 16-33 with the real token ratios (the replay
  matched the real request sizes within 3%): older messages changed at
  steps 17, 30, 31, 32 and 33, about 177,000 tokens read; with 0.28.4 at 17,
  20, 30 and 32, about 134,000 - and from step 30 on no longer at every
  step. Condensing waits until what it can fold is at least 10% of the
  context: the continued chat had condensed its only earlier messages (the
  notes, 1,200 tokens) into notes as long, and the changed system prompt
  made the server read 48,000 tokens again.

- **0.29.0 (chat 79):** a 9B model wrote "<p>Brow... [shortened by 24
  characters]" into its game's `index.html` - the Hub's own mark for text left
  out of what the model sees, copied into a file. `write_file`, `edit_file`
  and `artifact` now refuse text with one of the Hub's marks in it (`MARK` in
  `context.py`: `cut_text`'s, `shorten_result`'s with the reasons the code
  gives, and `clip`'s "... [N characters omitted] ...") unless the file has it
  already, and say what the mark is. Text written with bash is not checked.

**chat_history** (0.27.0, `app/agent/tools/history.py`). Search (BM25 over
each message's text, tool calls and results; Danish letters are word
letters; the whole query as a phrase counts extra) and read, over the whole
chat - the condensed part, older turns' full output, what was cleared in
this turn - and the chats it continues from (`settings.continued_from`, five
back). A person's turn searches the conversation her device sent, the
messages left out of the context included; nothing of it is stored. The
search leaves out its own calls and the message that started the turn, and
runs off the event loop. Offered only when there is more than the model sees:
condensed, continued, cleared in this turn, two earlier turns, or 20
messages; a person's turn when messages were left out or are shortened. A
cut or a condensing in the middle of a turn offers it from the next step.

**Continue in a new chat** (0.27.0). `POST /hub/api/chats/{id}/handover`
(the ⋯ menu, or the hint under the last answer of a chat that was condensed
or whose last request was 60% full - `context` in `GET /chats/{id}`, the
notes' own request not counted): a new chat in the same project with the
same settings, titled "… (2)", and `continued_from`. A plain chat's folder is
copied (not `.hub/skills`, `dotnet`, `tmp`, `home`; at most 2 GB) into a fresh
folder that is then renamed into place - never into whatever sits at the new
chat's path. Its first message is a `handover` block, and a run with
`handover_from` writes the notes from the old chat's notes and messages
(`HANDOVER_PROMPT`, the same headings, no thinking) as its first answer,
streamed like any answer. Since 0.28.4 the notes give files with their paths
(attachments are in `.hub/attachments/` - chat 78's first command looked for
them at the top of the folder) and say which findings were checked and which
are still guesses (chat 78's notes carried the old chat's guesses about the
record layout as facts). The model later reads "(SLQ Hub: this chat
continues the chat "…"…your notes from it follow…)" - without "notes follow"
when writing them was stopped. Retry under the notes writes them again; a
message sent meanwhile is answered after them; options from the browser
cannot start a handover. The old chat stays as it was; the new one links
back to it and searches it with chat_history. Not in people's chats (their
chats live on their devices).

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
in six, or when the model calls `escalate`. A failure of the card or ComfyUI
(a picture, sound or video tool's `hub_fault`: ComfyUI's or the scheduler's
error, running out of memory) does not count (0.22.1): the first test run
handed a video turn to Qwen3.8 27B after three out-of-memory failures, which
the stronger model then met too. Running out of memory also says, in the
result, not to repeat the call but to ask for less. Who takes over, in this
order:

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
   model is fine; it gets the person's prompt too - before 0.15.0 it got the
   owner's, memory index and all), bash only when allowed, artifacts kept in the turn
   (`DeviceInfo`), the skills she chose for the chat (hers, the family's
   shared ones, the Hub's; 0.23.0). Without a stored
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
the model, the times), and her skills - with the drafts the assistant made for
her, for two weeks - which live on the Hub (0.23.0, below).

### Skills (0.23.0)

Søren asked to make skills from his own material - PDFs, Excel sheets and the
like - with instructions, to keep and use them, for the family too, in the
format Claude uses, chosen "in a clever place". Decided with him: each
person's skills are private on the Hub and can be shared with the whole
family; a chat uses only the skills chosen for it (nothing is offered on its
own); a skill is made with the assistant; the library is a Skills page in the
sidebar, by Projects.

**Format.** A skill is a folder with `SKILL.md` - YAML front matter with
`name` (1-64 characters, a-z, 0-9 and single hyphens, the folder's name) and
`description` (1-1024 characters: what it does and when to use it), optional
`license`, `compatibility`, `metadata` and `allowed-tools` - then the
instructions, with `references/`, `assets/` and `scripts/` as needed (the
Agent Skills format, agentskills.io). `check_skill_md` applies Claude's rules
as well (no "claude" or "anthropic" in the name, no XML tags in the name or
description), so a skill made here imports in Claude. Import takes a .zip or
.skill (one top folder, or SKILL.md at the root) or a single SKILL.md;
Download gives the .zip Claude takes. At most 50 MB and 500 files.

**Where they live** (`app/agent/skills.py`, `Skills`). The owner's in
`data/skills/<name>/`, a person's in `data/skills/@<user id>/<name>/`, the
Hub's own in `app/agent/builtin_skills/` (today `skill-creator`). A skill is
named by its ref: `<name>`, `@<id>/<name>` or `hub/<name>`. The owner's skills
that are not shared are listed in `data/skills/.disabled.json` - the file of
the old "switched off" list, so a skill that was switched on before 0.23.0 is
shared - and a person's shared ones in `@<id>/.shared.json`. New and imported
skills are private; replacing one keeps its sharing. Everyone sees their own
skills, the others' shared ones (with who made them) and the Hub's; only the
maker changes, shares or deletes a skill. A removed person's skills go with
them.

**Choosing.** A chat's `settings.skills` and its project's hold refs (a
person's device chat sends hers with each turn). `turn_skills` resolves them
for whoever runs the turn, drops refs that are gone or not theirs to see,
and gives each skill a folder name. Their SKILL.md text goes into the system
prompt (`skills_text`: "The user chose these skills for this chat"), up to
40,000 characters together; a longer one is named with its description, to
load with `skill(name)`. Each chosen skill is copied to `.hub/skills/<dir>/`
in the working folder before the turn (`materialize`, which also removes the
ones no longer chosen), for code to read and run and for the skill maker to
improve it; the prompt names the folder. Sync and the files card leave `.hub`
alone. The `skill` tool reads only the chosen skills. In the
composer: a Skills button (yours, shared by the family, from SLQ Hub; Make a
skill; All skills), a chip for each chosen skill, and "/" with a name in the
message for a list to pick from (arrows, Enter or Tab, Escape). A project's
Settings lists every skill to use in all its chats.

**Making one.** Make a skill (the Skills button, the Skills page's New
skill, the help's examples) chooses the built-in `skill-creator` for the chat
and starts the message with "Make a skill that ". Its instructions: know what
the skill is for (one question when it is unclear), read all the material
(`read_file` gives PDF, Word, Excel - every sheet, values and formulas, 2,000
rows a sheet - and PowerPoint - slides, tables, notes - as text), write
`skills/<name>/` in the working folder (SKILL.md, references for long
material, assets kept as they are, scripts only where code helps), in the
user's language, try it once, then call `skill_propose`. That checks the
folder (the format's rules and the limits; the model gets the problems and
fixes them), copies the files named in `include` (attachments) into
`assets/`, and keeps the skill as a draft for two weeks
(`data/skills/.drafts/<id>/`). The chat shows a card: name, description,
files, Show SKILL.md, .zip, and **Add skill** ("Add skill (replaces the old
one)" when one of that name exists), which makes it one of the user's skills;
then the card says so and offers Use in a new chat. A draft belongs to whoever
made it. Improve (the Skills page) opens a chat with the skill maker and the
skill chosen; the model copies it from `.hub/skills/` to `skills/<name>/`,
changes it and proposes it under the same name. In a project the working
folder is the project's, so the skill's files become project files (synced,
with revisions) until deleted.

**The Skills page** (`/skills`; a person's app has it in its sidebar too):
Yours (a Shared switch, Use - a new chat with the skill chosen -, Improve with
the assistant, Edit SKILL.md, Download .zip, Delete), Shared by the family
(who made it; Read SKILL.md, Download), From SLQ Hub; New skill and Import.
The routes (`/hub/api/skills`, `/skills/one?ref=`, `/skills/zip`,
`/skills/drafts/<id>` with `/add` and `/zip`) are open to a person for what
is theirs to see. Settings → Skills is gone; its address opens this page.

---

## 7. Projects and files (SLQ Sync)

A project is a set of instructions, files, chats, skills used in all its chats,
memory and settings (default model, cloud escalation). Its files live in
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

**Download as ZIP** (0.29.0; Søren, chat 79: a browser game is four files and
a data folder, downloaded one by one). `GET /hub/api/folders/{kind}/{id}/zip`
gives the folder - or the files and folders named (`path=`, repeated) - as one
ZIP, named after the project or the chat. The files card's **Download all**
sends the answer's files, named from the folder that holds them all (a game in
`game/` comes out as `index.html`, `game.js`, `data/...`); the Files panel's
ZIP button sends the whole folder, without what sync ignores (`.hub`, `.git`,
`node_modules`, ...). A file named that is gone since is left out. At most
2 GB; the page asks first (`check=true`: how many files, how big, or why not)
so a refusal is a message rather than a broken download. Built in a temporary
file in `data/tmp` off the event loop and deleted once sent. Code in the
sandbox has the same uid and the same folder and may swap a listed file or
folder for a link (to the Hub's database, to `/dev/urandom`) or a FIFO before
it is read: each file is opened from the workspace down with `O_NOFOLLOW` at
every step (`Workdir.open_inside`), only a regular file is read, and no more
of it than was listed.

---

## 8. Images, sound and video

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
- **Edits and cut-outs at the original's size** (0.22.2). Qwen-Image and
  FLUX.2 klein edit at about one megapixel and return that size: both test
  runs' 768×512 ball came back at 1248×832, and one model told the user it was
  the original's size. Up to 2048×2048 the result is scaled to the original's
  size (Lanczos; a cut-out premultiplied, so its edges keep their colour);
  a bigger original keeps the model's size rather than being blown up from one
  megapixel. The tool's result says the size, and the original's when they
  differ.

### Sound (0.13.0)

The sound tools make audio files in the working folder, and the card under the
answer plays them (`backend/app/audio/`, `backend/app/agent/tools/audio.py`):
speech, music and sound effects below, and since 0.18.0 remakes, stems and
other voices ("Voices and remakes").
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

### Voices and remakes (0.18.0)

Søren asked for a voice from a short clip of someone talking or singing - in
a song or a clip - and for a song made to sound like a clip of another song
(its audience if live, feel, reverb), re-made in its style. Three tools and one
option, all on the card through ComfyUI and the queue, with four nodes of the
Hub's own (`services/comfyui/nodes/slq_audio`, installed by the ComfyUI image):

- **A song with a clip's sound** (`music_generate` with `reference`).
  ACE-Step 1.5 has a timbre encoder for a reference recording, apart from the
  source it covers. ComfyUI v0.42.3's `ReferenceTimbreAudio` gives the model
  one latent for both: the reference is heard and also becomes the structure
  (cover mode) - for a short clip under a new song, the song would follow the
  clip for its length and silence after it. `SLQAceStepReference` patches the
  model (a `model_function_wrapper`, per graph): the clip, VAE-encoded and cut
  to 750 frames (30 s), reaches only the timbre encoder (`refer_audio`), while
  the planner's audio codes stay the structure. Honest limit: ACE-Step learnt
  the reference from other parts of the same song, so a clip steers the sound
  - instruments, production, a singer's timbre - and does not clone a
  person's voice; the tool says so and points to `voice_convert`.
- **A remake** (`music_remake`). The song (at most 5 minutes) is VAE-encoded
  once and used twice: `SLQAceStepReference` turns it into ACE-Step's own
  semantic codes (5 a second, the model's tokenizer) - the melody, rhythm and
  structure - with `is_covers`, and a style clip as the timbre; the sampler
  starts from noise (`keep` 0) or from the song itself (`keep` up to 0.9,
  denoise 1 − keep: more of the recording survives, less of the new style).
  `SLQAceStepEncode` is the text encoder without the planner (the codes come
  from the song) and leaves an unknown bpm, key or time signature as `N/A`
  instead of guessing. The model hears the clip, the assistant does not: the
  prompt describes what the user says it sounds like. Without lyrics the
  words are not kept, and the result says so. The codes: the tokenizer's FSQ
  index is a sum of digits times up to 12,800, and in bfloat16 (8 significant
  bits) it lands on the wrong code or past the codebook's end - measured with
  the real tokenizer code: 0 % of bf16 indices matched float32, the largest
  past 64,000. ComfyUI's own cover mode uses the quantized values, never the
  index, so it does not meet this; the node runs the FSQ step in float32
  (100 % equal to the tokenizer's float32 output).
  **0.20.2 (chat 24, measured):** the first remake on the card - keep 0, no
  style clip, "a live concert with a cheering audience and a big hall" - came
  back in another key (D major → E major) and tempo (about 112 → 152 BPM),
  its chroma no closer to the song's at the same moment than at a random
  offset of 20-60 s, its loudness flat where the song has a quiet intro and a
  drop: a new song of the same length and lyrics. With no clip the timbre
  encoder had heard silence. Since then, without a clip the song's own sound
  is the timbre - 10 s of its start, middle and end, joined (ComfyUI's
  `TrimAudioDuration` and `AudioConcat`), as ACE-Step's own pipeline prepares
  a reference; keep defaults to 0.5; and `SLQAceStepReference` works out the
  song's codes when it runs (the model loaded for it) and checks them against
  ACE-Step's own tokenizer path - the share of 5-a-second steps whose decoded
  vectors agree (cosine > 0.99) - which the tool's result reports, with a
  warning the user hears under 90 % (stand-ins: 100 % in float32 and in
  bfloat16). For the same recording only live, the tool's description gives
  effects that keep it exactly: a hall (convolution with a decaying pink-noise
  impulse response; `afir` normalises it to its peak, so it needs `wet=10` and
  `volume=2` to sit about 5 dB under the song) and a crowd from
  `sound_generate`, mixed under with ffmpeg.
  **0.27.1 (chat 76, measured):** "the same song, rock instead of pop, the
  voices as they are" - four remakes of a 214-s pop song (ACE-Step 1.5 XL
  Turbo, its own sound, codes 83-84 % in agreement). Keep 0.75 and 0.85: the
  mel spectra 0.97-0.976 alike (cosine, frame by frame) and the onsets locked
  to the song's (correlation 0.66-0.68 at a 0-s lag, in every 30-s window) -
  the user heard the original. Keep 0.45 and 0.5: onsets uncorrelated
  (0.02-0.05), another tempo - "the rhythm was way off". Between them (0.6,
  0.7) nothing was tried. A remake plays the whole recording again, voices
  too, so "keep the voices" cannot be had from it; the tool's description now
  says so, with what was measured, and gives the way that keeps the singing
  exactly: `audio_separate`, a remake of the accompaniment alone, and the
  original vocals laid over it with ffmpeg (`MIX_RECIPE`: `amix` without
  normalising, a limiter; stems and remakes keep the song's length). The
  model had also described guitars it could not hear: every sound tool's
  result now says it cannot hear the file. A sound input given by an
  attachment's name is found in `.hub/attachments/`, and keep is written as
  given (0.75 was shown as 0.8).
  **0.28.0 (chat 76 again, measured):** Søren tried the stems way, keep 0.7
  and 0.6, still on 0.26.0. The model mixed the vocals at 0.3 and let `amix`
  halve both: 11 dB under the new band where they sing (in the song they are
  level with it, -0.4 dB). The new band was in time - 98-100 % of its beats
  within 70 ms of the accompaniment's, 97 BPM and F# minor like the song -
  but its long-term spectrum (1/3-octave bands) moved from the accompaniment's
  only 1.1-1.4 dB, as far as the 0.75 and 0.85 remakes ("a lot like" and
  "almost exactly like the original") moved from the song (0.45-0.5: 2.0-2.4
  dB). Over all six remakes of the song, 0.6-0.85 kept its tempo, key and
  beat and 0.4-0.5 lost all three (about 150 BPM against 97): keep alone does
  not give a new sound on the old beat. So `music_remake` takes `vocals: "original"`
  (offered when the separation model is downloaded): one graph separates the
  song, remakes only its accompaniment (lyrics `[Instrumental]`; the
  accompaniment's own sound unless a clip is given), adds the original vocals
  back with `AudioMerge` (unity gain; it divides by the peak only when the sum
  passes full scale) and saves the mix and the band alone - no ffmpeg written
  by the model (`MIX_RECIPE`, now with a level for the vocals, stays for
  changing the balance afterwards). Every remake graph ends in
  `SLQBeatCheck`: librosa's beat tracker on the song (its accompaniment, for
  a band) and on the result, the tempo from a line through the beats, the key
  from the CQT chroma (Krumhansl-Kessler profiles), and the share of the
  result's beats within 70 ms of the song's and the reverse (a double or half
  tempo on the same beat counts); 80 % or more follows, under chance plus 20
  points (at least 45 %) lost, between partly. The tool's result gives it as
  measured, not heard, with a warning to tell the user when the beat is lost.
  On chat 76's files: 91-100 % for the six in time, 24-25 % for the two that
  were not (chance 23 %). Keep defaults to 0.6. Two ways to move the sound
  further while the beat stays are described but not yet tried on the card:
  below 0.6 with the song's bpm and key as the beat check measured them
  (without them ACE-Step chose about 150 BPM, as in chat 24), and a reference
  clip in the new style.
- **Stems** (`audio_separate`): vocals and accompaniment, for karaoke,
  remixes, or one part alone. `SLQSeparateVocals` runs Kimberley Jensen's
  Mel-Band RoFormer vocal model (SDR 10.98 on MVSEP's multisong set, the one
  UVR and ComfyUI users use) with ZFTurbo's MSST code at a pinned commit:
  8-s chunks at 44.1 kHz stereo, overlap 2, linear fades, summed and divided
  by the windows (MSST's `demix`; with an identity model it gives back its
  input exactly at every length); the accompaniment is the mix minus the
  vocals. MSST's class has the same 684 tensors as Kim's original code, and
  gives the same output within 2·10⁻⁴ (MSST zeroes the DC bin). Up to 10
  minutes. MSST imports the model as `models.bs_roformer`, and ComfyUI's
  models folder would answer to `models`, so the node loads the two files under
  private module names (the one import line rewritten when loaded, checked to
  be there).
- **Another voice** (`voice_convert`): Seed-VC v1 - zero-shot, no training -
  in `SLQSeedVC`, a port of its `inference.py`. Singing uses the F0 model
  (44.1 kHz, `seed-uvit-whisper-base`, RMVPE for the melody), speech the
  22.05 kHz `whisper-small-wavenet` model; whisper-small hears the content,
  CAMPPlus (the weights in Seed-VC's repository) the speaker's style, BigVGAN
  makes the sound; chunks of 30 s of context with the reference as prompt,
  crossfaded over 16 frames, as upstream. Changes: no torchaudio (ComfyUI's
  image has none and no torchaudio exists for torch 2.14): ComfyUI's
  resampler and a port of TorchAudio's Kaldi filterbank (identical output on
  three lengths); the reference trimmed to its voiced parts, then 25 s (a
  clip of a song's separated vocals is half silence); an automatic pitch
  shift by whole octaves only (`auto_octave`: a man's song in a woman's voice
  goes up an octave and stays in the key of the music it goes back into),
  plus `pitch` in semitones; the output as loud as the source's vocals.
  With music (default for singing) the song and the voice clip are both
  separated first, the converted vocals added back onto the accompaniment
  (`AudioMerge`, normalised only if it would clip), and `vocals_only` saves
  them alone too. The node reports the shift and the voiced seconds it heard
  (JSON text in the job's history) and the result says them. A song in
  someone's voice is two steps: `music_generate`, then `voice_convert` -
  the system prompt says so. Only a voice the user has the right to use.
- **Inputs.** ffmpeg in the sandbox (confined in a person's chat; local files
  only) converts the user's file to 44.1 kHz stereo FLAC - the first 30 s of a
  clip, 5 minutes of a song to remake, 10 minutes to separate or convert -
  and ffprobe's length lets the result say "only the first 5:00 of the song
  (6:40) were used". The Hub reads only the FLAC's STREAMINFO block (its
  length); ComfyUI decodes it. The copies go to ComfyUI's input folder and
  are deleted after the job in every chat, the owner's too (a song is tens of
  MB); in a person's chat the outputs and history go as before.
- **Models.** `mel-band-roformer-vocals` (ability `separate`) and `seed-vc`
  (ability `voice`: both Seed-VC models, whisper-small's folder, the two
  BigVGAN folders, RMVPE) in the catalog, under ComfyUI's models folder; the
  configs come from the checkouts in the image (catalog `settings`). Their
  paths are the ones the projects' own code downloads; this session could not
  reach Hugging Face, so the sizes are estimates (progress only). The nodes
  take paths inside the models folder itself (`separation/...`, never outside
  it), not a loader's folder type.
- **The image.** `services/comfyui/Dockerfile` fetches Seed-VC
  (`SEEDVC_REF`) and MSST (`MSST_REF`) by commit, without history, installs
  librosa, munch, matplotlib, beartype and rotary-embedding-torch at the
  versions tested, and copies the node package. The heavy imports happen when
  a node runs, so a problem there fails that job with its reason instead of
  keeping ComfyUI from starting; a ComfyUI image older than the Hub is named
  as such ("update.sh rebuilds it"). The nodes load their models per job and
  hand the card back at the end (ComfyUI's own models are unloaded first).
- **Licences.** Seed-VC and ComfyUI are GPL-3.0, so the node package is
  GPL-3.0; it runs only inside the ComfyUI container, and the Hub talks to
  ComfyUI over HTTP. MSST is MIT; Kim's weights state no licence; ACE-Step is
  MIT; whisper-small and BigVGAN v2 are MIT; RMVPE is the file RVC's project distributes.
- **Checked here** (DESIGN §13, HANDOFF): ComfyUI v0.42.3 on CPU with torch
  2.14.1 (conda-forge) and random-weight stand-ins built from the real
  architectures (a small ACE-Step 1.5 DiT, a full-size ACE 1.5 VAE, small
  RoFormer and Seed-VC parts, a random RMVPE): every new graph ran end to end
  with the files and durations it should, a 75-s conversion in four chunks
  included; and what reaches ACE-Step's forward in each mode (timbre only:
  the clip and the planner's codes; cover: the song's codes, `is_covers`, the
  clip). Not checked: anything with the real weights - the sound, the time
  and memory on the card.

### Video (0.19.0)

Søren asked for video: from a picture, and from a description. One tool,
`video_generate`, on the card through ComfyUI and the queue like pictures and
sound, with ComfyUI's own nodes only (the image is unchanged):

- **Models.** Wan 2.2 (Alibaba, Apache 2.0), Comfy-Org's repackaged files, in
  a new catalog group `video` (Settings → Models → Video models):
  - `wan2.2-5b`, TI2V 5B: one model for a description and for a picture
    (`t2v`, `i2v`). fp16 weights (10 GB), the Wan 2.2 VAE (16× spatial, 48
    channels), umt5-xxl fp8 as text encoder; 1280×704 and 121 frames at 24 fps
    for 5 s; 20 steps, cfg 5, uni_pc, shift 8 - the official template
    (`video_wan2_2_5B_ti2v.json`). 18.2 GB to download (16.9 GB as
    Settings counts, in binary units).
  - `wan2.2-i2v-14b` (a picture, `i2v`; a first and a last picture, `flf2v`)
    and `wan2.2-t2v-14b` (a description): two 14B experts - high noise for
    the first steps, low noise for the rest, each its own `KSamplerAdvanced` -
    in fp8_scaled weights (14.3 GB each), the Wan 2.1 VAE, 16 fps, 81 frames
    for 5 s, shift 5. `fast` (the default) adds the lightx2v 4-step LoRAs: 4
    steps split at 2, cfg 1; `full` is 20 steps split at 10, cfg 3.5 - the
    templates' own switch. The first-to-last template runs full quality at
    shift 8 and cfg 4, which the catalog carries as `flf2v_full`. 480p
    (832×480) by default, 720p (1280×720) when asked. 38.0 GB each (35.4 in
    Settings); the pair shares the text encoder and the VAE (7.0 GB,
    downloaded once).
  - `wan2.2-5b-turbo` (0.25.0), first in the list, so it is the default
    once downloaded: the 5B with the Turbo distillation - quanhaol's
    Wan2.2-TI2V-5B-Turbo, step- and CFG-distilled to 4 steps with
    Self-Forcing/DMD, for a description and for a picture, CC BY-NC-SA 4.0 -
    as Kijai's rank-64 LoRA (`Kijai/WanVideo_comfy`,
    `LoRAs/Wan22-Turbo/Wan22_TI2V_5B_Turbo_lora_rank_64_fp16.safetensors`, 332
    MB, sha256 0ace5244...). Its other files are the 5B's, so beside the 5B
    only the LoRA downloads. 4 steps at cfg 1 - ComfyUI then leaves out the
    negative pass, so 4 model passes instead of the 5B's 40 - with UniPC (bh2)
    at shift 5, as the Turbo's own examples; decoding stays as it was. Søren
    asked for video to be faster after the test runs (15.5 minutes for a 5-s
    clip; "they turn out good"): the faster choice is the default, and
    `quality: "full"` (the user asks for the best quality) hands the clip to
    the 5B's 20 steps (`settings.full_model`), as `full` drops the 14B's
    LoRAs. FastWan's 5B LoRA (3 steps) was the other candidate; it was
    trained for text to video only.
  - Why Wan: fp16 and fp8 weights, which ComfyUI computes in 16 bits on
    RDNA3 - the plain ROCm path. LTX-2.x and the newer MiniMax and Hunyuan
    templates ship nvfp4 or int8-convrot weights and 32B-class text encoders
    aimed at NVIDIA. AMD's own 2026 ComfyUI material names Wan 2.2 14B fp8 for
    RDNA3. The paths and sizes were checked on Hugging Face's pages.
- **Graphs** (`app/video/workflows.py`), built in code from the official
  templates (workflow_templates 0.11.93 for ComfyUI v0.42.3). 5B:
  `UNETLoader → ModelSamplingSD3`, `CLIPLoader` (type `wan`), `VAELoader`,
  `Wan22ImageToVideoLatent` (the picture as its first frame), `KSampler`,
  `VAEDecodeTiled` (0.22.1, below), `CreateVideo`, `SaveVideo` (MP4, H.264);
  the Turbo puts its LoRA in a `LoraLoaderModelOnly` between the
  `UNETLoader` and `ModelSamplingSD3`. 14B: two `UNETLoader`
  (each through `LoraLoaderModelOnly` when fast) → `ModelSamplingSD3`;
  `EmptyHunyuanLatentVideo` for a description, `WanImageToVideo` for a
  picture, `WanFirstLastFrameToVideo` for two; the high-noise sampler runs
  steps 0 to the split and hands on its leftover noise, the low-noise one the
  rest. Wan's own Chinese negative prompt comes first, the user's after it.
  Frames are 4n + 1 (the VAE packs four frames per latent frame, plus the
  first).
- **Decoded in tiles** (0.22.1). The templates decode the clip whole
  (`VAEDecode`). In the first test run on the card the 5B's 5-s 720p clip
  then asked for 18.56 GiB in one piece with 9.3 GiB free and ran out of
  memory - four tries, each after the whole 10 minutes of sampling, since the
  decode comes last. ComfyUI's own fall-back sizes its tiles from its
  estimate of the whole decode (13.4 GiB for that clip in bf16), which lets a
  tile be 1024 px - most of the frame - and is probably why it did not save
  it. Both graphs now end in `VAEDecodeTiled` with 512-px tiles overlapping
  by 64, under a third of that. Time is not tiled (`temporal_size` 4096, the
  node's largest): both Wan VAEs decode a latent frame or two at a time with
  a cache, so a shorter time tile saves nothing and would leave a seam.
  Measured on the card: 28-41 s a step at 1280×704 and 121 frames, the
  first video of a test run the slowest (40.6 s in both runs, then 28-35 s;
  why is not known), so 10-14 minutes of sampling for a 5-s clip; the tiled
  decode and saving took 1.5 minutes more (second run). Its kite clip shows
  no seam: the sky's colour runs on smoothly across the tile edges.
- **Sizes** (`size_for`): the model's 480p or 720p pixel count in the
  picture's shape (held between 1:2.5 and 2.5:1), or landscape, portrait or
  square, rounded to 32 (5B: the 16× VAE and 2×2 patches) or 16 (14B).
  1-10 s; 5 s is what the models learnt.
- **The tool**: `video_generate(prompt, image, last_image, seconds,
  orientation, hd, quality, negative, model, seed, save_as)`. A picture is a
  path in the working folder (an attachment too), read with PIL as
  `image_edit` does, sent to ComfyUI as PNG and deleted from its input folder
  after the job in every chat. The MP4 goes to the working folder (`video/`
  in a project), where the files card plays it. The result gives the length,
  frames, size, model, quality, seed and the seconds on the card, and says
  there is no sound; the description shows how to put `music_generate` or
  `sound_generate` under it with ffmpeg. In a person's chat ComfyUI keeps
  nothing (history and output deleted), and a file over 25 MB is said not to
  reach her device. Not in subagents. Settings → Assistant → Video chooses
  the model for a description and for a picture (first-to-last uses the
  picture model when it can, else one that can); the tool's description says
  to name another only when the user asks for it (0.22.3 - the second test
  run's model picked the 14B pair for a picture on its own). The two experts' steps show
  as one progress bar. The system prompt's short Video section: minutes per
  clip, so one at a time, and ask before making more.
- **Checked here** (HANDOFF): ComfyUI v0.42.3 on CPU with stand-ins - one-block
  Wan DiTs of the three kinds (48→48 channels for the 5B, 36→16 and 16→16 for
  the 14B pairs), rank-4 LoRAs, a random Wan 2.1 VAE and a full-size random
  Wan 2.2 VAE, and a test node for the umt5 conditioning. Every graph as the
  Hub builds it was accepted at `/prompt`; with the stand-ins all seven (5B
  text and picture; 14B picture fast and full, first-to-last, text fast and
  full) ran to an MP4 - H.264 High, yuv420p, the frame count, size and frame
  rate the Hub reports, no prompt in the file. The Hub's own client ran one
  with websocket progress and left nothing behind in ComfyUI. Not checked:
  anything with the real weights.

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
  by date, libraries, settings, help, and the GPU plate (what is loaded, slot
  lamps, queue; it opens the queue).
- **Chat.** Streaming markdown with code, tables and KaTeX maths, collapsible
  thinking, tool calls as a terminal strip (one row per call, expandable to
  input and output), approvals inline, artifact cards, task lists, a summary
  line per answer, retry and edit-and-resend.
- **Not finished** (0.22.0). An answer stopped at the step limit or by the
  user says so in an amber bar - in its files card, or alone - with Continue
  under the last answer; a pressed Continue shows as a "Continued" mark
  (section 6, "Not finished, and Continue").
- **Files from an answer** (0.12.0). A card under the answer lists the files
  it made or changed in the working folder, newest first, whatever made them
  (a tool or a command such as ffmpeg). Each file gets its kind, folder, size
  and "new" or "changed"; sounds and videos play in the card and pictures
  show. Open puts it in the side panel (a PDF opens in a new tab), Download
  saves it, and Add to chat puts it in the next message. Over six files, the
  rest folds away. A person's card reads the files from the device.
- **Composer.** Attach, paste or drop files, files of the project (or the
  chat's folder) to put in the message, the model, the picture model,
  thinking (off, a little, normal, hard), libraries; the Skills button and
  "/" in the message for the chat's skills (0.23.0, §6 "Skills").
- **Side panel.** Artifacts (sandboxed iframe, versions, source, save to the
  folder) or the folder's files, with a search and a + on each file that
  puts it in the next message. It shows pictures, plays sound and video, and
  shows PDFs (served without the `sandbox` CSP the other files get, which
  the browser's PDF viewer cannot run under).
- **Project page.** Chats, files (tree, CodeMirror editor, upload, history and
  restore per file), settings, revisions, sync devices.
- **Libraries.** Documents with indexing progress and a search to try.

Settings: models (download with progress, load, free the GPU, rename, order,
server parameters; the sound models in their own group, each marked CPU or GPU),
queue, people, usage (0.24.0, §3 "Energy and cost"), apps and keys (with
connection examples and CORS), assistant (agent, libraries, web, sandbox,
images, sound: the model for music, for sound effects, the voice models for
Danish and for other languages, and the last speech self-test's samples with
players), memory, MCP servers (and the tools you always allow), cloud, tests (0.21.0, §12: the
model and the cases to run, a live panel while a run goes - which case, what
it does, Open the chat, Stop - and the runs with each case's result, checks,
time breakdown and answer, Export and Delete; a case's chat says "Test" by its
title), system (services - the speech service with its threads and loaded
models - VRAM, disk, logs, theme, password).

On mobile it shows one column, and the panels become overlays. It is an
installable PWA (manifest, icons, a service worker that caches only the app
shell).

**An open page across an update (0.13.6).** A page runs the code it loaded
until it is reloaded, and a phone's tab can stay open for days. On 2026-10-24
a jingle had no player under the answer on Søren's phone - most likely a tab
still on a build from before the files card (0.12.0): the current build shows
the card at phone width. Every answer to the UI (`ui` and
`public` paths) carries `X-Hub-Version`; `api()` remembers the first one it
sees, and a different one later puts "SLQ Hub was updated to … - Reload" in
the toast corner until the page is reloaded (or the notice put away). The page
asks again when it comes back into view and when its queue stream reconnects
after a restart.

**A turn's stream on a phone (0.13.6).** A phone that sleeps or loses its
network breaks the turn's event stream. The browser reconnects on its own,
resuming after the last event; when it gives up (an error answer from the
proxy while the Hub restarts, or no network), the page fetches the stored
turn - and without a connection it keeps what streamed in and tries again when
the network is back, when the page is in view again, or after 2-30 s, instead
of showing the chat as it was before the turn. If the turn is still at work,
it follows it again from the start of its events.

**Help (0.20.0).** Søren asked for a help section with everything one can ask
for, with examples, in sections. `/help/<section>` (Help in the sidebar, and
"What you can ask for" on a new chat): Chat, Documents and files, Images, Sound
and music, Video, Code and programs, Memory and skills, The Hub. Each item says
what it is, has example requests - Try puts one in a new chat's message box
(not sent: the user changes it, attaches what it says, and sends), Copy copies
it - and the steps or tips that go with it; a search runs over all of it. The
text is in `frontend/src/lib/help.ts`, each item naming the tools it is about
(a test fails when a tool is in no item, or an item names a tool that does not
exist). Whether an item works on this Hub now comes from `GET /hub/api/help`
(`backend/app/ui/help_routes.py`), with the same checks that decide which tools
a turn gets: the downloaded picture, sound and video models, the sandbox's
Windows support, the hand-over model, libraries, MCP servers, the internet
setting, and for a person her code permission. An item that cannot work says
what is missing - to the owner where to get it (Open Settings), to a person to
ask the owner - and its Try is off. A person gets the same page from her
sidebar, without the owner's items (projects, memory, libraries, Windows
programs, export) and with hers (chats kept on the device). The sections
are chips that wrap - all of them in view on a phone, the chosen one filled
(ink on paper inverted), its name and icon heading the section (0.20.1: on
Søren's phone the tab strip's scrollbar lay over the chosen tab's thin
underline, so it was hard to tell which was chosen). Tab strips elsewhere
(Settings, a project page) hide their scrollbar, tint the chosen tab with a
3 px line, and scroll it into view when it starts off-screen on a phone.

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
artifact panel; the queue plate; her Skills page) over IndexedDB (`slq-hub-<user name>`:
chats, per-chat data, files, pictures). Pictures are made by asking; there is
no Picture button. The account menu has Export all chats / Import chats (one
JSON file with pictures and files), Change password and Sign out. A Files
panel lists the chat's files (attachments in `.hub/attachments/`, what the
assistant made), each sent with every message.

---

## 11. Security

- **Links planted in the workspace** (0.27.0). Code in the owner's sandbox
  sees the whole workspace, and the Hub sees more of the disk than the
  sandbox: a symlink planted where a folder belongs (`chats/7 -> /data`,
  `.hub/skills -> /data`) would have the Hub read or delete its own files -
  the skills cleanup would empty the link's target, and read_file in a chat
  whose folder is a link would read settings and keys. Chat and project
  folders are made with `real_dir` (a link in the way is removed, never
  followed, and the folder must resolve inside the workspace), a working
  folder replaced by a link during a turn is refused on every path the
  tools resolve, the skills cleanup removes links instead of following
  them, and Continue in a new chat copies into a fresh folder.

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
  ABI 4+ it connects only to ports 53, 80 and 443 - and (0.15.0) a seccomp
  filter refuses MPTCP, TCP Fast Open and other socket kinds, which
  Landlock's rule does not see; with ABI 6+ it cannot signal processes or
  reach abstract sockets outside. Landlock also denies
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
- **Web fetch.** `web_fetch` and image URLs from apps refuse private and
  loopback addresses, the LAN's ranges (`HUB_LAN_CIDRS`, which may be public
  addresses) and Settings' blocked ranges (`app/netfetch.py`; before 0.15.0
  an app's image URL was checked against the private ranges only). It looks
  the name up once - as the connection names it (IDNA 2008, as httpx), so
  `straße.de` is not looked up as `strasse.de` - checks every address, and
  connects to a checked address (0.15.0: a pinned connection pool; TLS is
  still verified against the name), so a name server that answers
  differently the second time - DNS rebinding - cannot steer it into the LAN.
  Each of up to five redirects is checked and pinned the same way. No proxy
  from the environment (a proxy would look the name up itself).
- **Model containers** (0.15.0). llm, comfyui and speech are on `inner` only,
  and the VM's firewall drops new connections from them to anything outside
  it (section 2). They need nothing outside: the Hub downloads the models,
  and Hugging Face is offline in speech.
- **Internet for the assistant.** "Ask first" (section 6) makes web search,
  each site and code that goes online wait for the user's answer, and runs
  other code without TCP connections. HTML and React artifacts load nothing
  from the web then (their own policy; the page's `frame-src 'self'`).
- **Test runs** (0.21.0) answer their turns' questions themselves, as the
  owner who started them: Allow once (never Always or the chat) only for the
  web case's searches and pages, and for code the sandbox cannot keep offline
  when the call does not ask for the internet; anything else Deny. Each answer
  is in the case's result. The checks that open the turn's files or run its
  code run in the sandbox, offline where it can be; the Hub writes the check
  files into the case's folder without following links (`Workdir.write`) and
  reads them back for the export only from inside it.
- **Windows programs** (0.16.0). They run in the sandbox under Wine as the
  sandbox user, like any command (offline while asking first, the same
  firewall); Wine needs no privileges, and its X server listens on no TCP
  port. HubKeys, the typing helper (0.16.1), runs beside them the same way
  and talks only to winshot, through a pipe.
- **Speech service.** On `inner` only: not published, and the sandbox's
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
3. Wait until nobody uses the Hub (0.26.0, below).
4. Stop the app and back up `data/` (not the logs, the sandbox's NuGet caches
   or .NET build output: they come back by themselves).
5. Start the new version and wait for health.
6. On failure, roll back code, images and data automatically.
7. Install the firewall script and unit as the new version has them, and
   apply the rules (0.15.0; they are added only when missing).

**Waiting for a free Hub** (0.26.0). An update may be started at any time:
after the build it asks the running Hub, `GET /hub/api/internal/busy` (from
the VM with `HUB_INTERNAL_TOKEN` from `.env`; refused through the proxy,
`app/ui/update_routes.py`), what is going on - a job on the card or waiting
for it, a model being loaded, a chat turn between two steps or waiting for an
answer to its question, a person's turn and their device fetching its answer
(two minutes after it ended), a test run, a library reading a document, a
model download - and goes on only once the answer has been "nothing" for
`--quiet` seconds (60, asked every 5). A turn whose task has ended without
finishing, and queued downloads nothing is downloading, do not count: they
would keep it waiting for ever.

Started in a terminal, the waiting and installing run in a session of their
own (`start_new_session`; the resume file below, marked `detached`; output in
`/srv/slq-hub/update.log`, the previous one kept as `update.log.1`), and the
terminal shows that log until they end and returns their exit code: Ctrl+C or
closing the window stops only the showing, never a backup or a start halfway.
The update's own updater builds first, in the window (Ctrl+C there undoes it);
an updater that was handed over goes to its own session before the build,
because the old updater kills it a quarter of a second after a Ctrl+C - in
0.25.0's `subprocess.call`; from 0.26.0 on, `hand_over` waits and leaves the
Ctrl+C to the new one. Without a terminal (cron, `--yes`) or with `--here` it
all runs where it is.

`/srv/slq-hub/update.lock` names the update's process, written whole
(`os.replace`) with the boot id, the phase (building, waiting for the Hub to
be free, being installed), the old commit and version; a lock counts only
while its pid, in the same boot, is a `tools/hub_update.py` process.
`update.sh status` shows it, and another update is refused while it runs.
`update.sh cancel` stops an update that builds or waits - SIGTERM, which
SIGHUP and Ctrl+C equal there: the code and the images' tags go back as after
a failed build, nothing was stopped. So does any error before the install.
From the install on, SIGTERM, SIGHUP and SIGINT are ignored, as they are while
putting the code back. A lock whose process is gone (killed, the VM reset) is
dealt with by the next `update.sh`: cut off before the install, it puts the
code and the tags back first and carries on; cut off during it, it stops and
says what to do (`status`, `--force` to start this version again, or
`rollback`). `--now` skips the wait. A Hub older than 0.26.0 cannot answer
(its gate says 401), and then the card's own load decides
(`gpu_busy_percent` of 10 or more is busy), which does not see a turn between
its steps - so the update to 0.26.0 itself waits for the card only; a 403 (a
token that is not the Hub's) or a 5xx is said once and also falls back to the
card.

When the fast-forward changed the updater itself (`tools/hub_update.py` or
`update.sh`), the new updater does steps 2-7 (0.16.0): the old one writes what
it knows (`HUB_ROOT/update-resume.json`: the commits, versions and the images
tagged `:rollback`), starts `hub_update.py update --resume <file>` and waits.
The new one deletes the file as it takes over; a file still there afterwards
means it never started, and the old one puts the code back as after a failed
build. So a step new in a version runs in the update that brings it - from the
update after 0.16.0 on (0.16.0 itself arrives through 0.15.1's updater).

`update.sh status|backups|rollback|firewall` exist too (`status` says whether
the installed firewall is the clone's, `firewall` installs and applies it).
`status`, `firewall` and an update also try each model container's way out -
a connection to 1.1.1.1:443 from inside it, which the rules must stop - and
say what they found.
An update makes the folders the services mount when a new version adds one
(owned by uid 1000).

**Self-test.** `python -m app.cli selftest` checks every service, the
sandbox's isolation (since 0.15.0 also that a command run offline cannot
connect: "offline code"; since 0.16.0 "windows programs": a small Windows
Forms program built offline and run under Wine, its button found by its text
and clicked, which closes it - since 0.16.1 after "Søren: ÆØÅ æøå" is typed
into it, which must arrive exactly) and one answer with its tokens/s; `--images` makes a
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

**Tests** (0.21.0, Settings → Tests, `app/evals/`). The self-test says the
services answer; this says how well the assistant does the work. 24 cases, a
hard task in each area (`suite.py`): chat (a logic puzzle checked to have one
solution, a word problem in English, ten fields from an e-mail as JSON, Danish
under five rules), documents (a year of sales in Danish Excel format, a fact
a 12-page manual changes on a later page, a needle in a 3,600-line log, a Word
report, a budget with formulas), images (a poster with text, an edit, a
cut-out, reading and counting in a picture), sound (Danish speech, music, an
effect), video (from a description, from a picture), code (a Python module
against hidden tests, four bugs to find, a C# program, an interactive page)
and research and tasks (the web, a task in several steps). Their input files
are made the same way every time (`fixtures.py`, fixed seeds), and what a
right answer holds is worked out from them, not written by hand.

The cases run in the suite's order (0.22.1): the chat model's own work
first (chat, documents, code, research and tasks), then pictures and sound,
which take the card from it, and video last - so a failure of the heaviest
job cannot cost the others their run, as it did in the first run on the card.

What the second run taught the checks (0.22.2): a result is judged, not the
way to it - the edit and the cut-out judge the newest picture the turn left
in its folder, made by a tool or by its own code (Qwen3.8 turned the ball blue
with a hue shift in Python, pixel for pixel right, and failed a check that
wanted `image_edit`); the invitation is judged up to its "Venlig hilsen" and
signature, and anything after it (a ticked list of the rules, quoting
"hyggelig") is a check of its own; the poster's lines are read whole, one
letter in five off and the numbers exact ("ULI HALLEN" and "JUL ALLEN" were
right posters); the page's formula may be written `(c * 9) / 5`; and a video
must change smoothly from frame to frame (a good clip changes by a point or
two of 255, the broken one by 107).

Each case is a real chat (`chats.test_run` = the run: not in the chat list,
deleted with the run) and a real turn: the agent, the model the run chose
(thinking as in a chat, or on or off), the tools and the sandbox - nothing is
faked or shortened. Its turns queue as `tests` at background priority, so a
person's chats go first. A question the turn asks is answered as a careful
user would: Allow once for the web case's searches, and for code the sandbox
cannot keep offline unless the call asks for the internet; everything else is
answered Deny (recorded in the result). A case has a time limit (15 minutes to
an hour), and Stop ends the run after cancelling the turn at work.

Then the checks. The Hub checks the answer (`checks.py`: a line it was asked to
end with, numbers in Danish or English writing, JSON, Danish, bullet points,
the tools used). What the turn made is checked in the sandbox, offline where
it can be, in the case's folder (`check_lib.py`, copied to `.hub/tests/` with
the hidden tests and the pictures the tools made): the chart, the .docx and
.xlsx (python-docx, openpyxl), the pictures' colours and transparency, the
poster's text (Tesseract), the sound's length and loudness and the video's
length, size and motion (ffprobe, ffmpeg), hidden unittest tests on the
Python, `dotnet run` on a text the C# program has not seen. The Hub never opens
the model's files or runs its code itself. A check is a must (the case fails
without it), a should (it passes in part) or a measurement; each case also
says what only a person can judge. A result keeps the answer, the checks, the
tools, what was asked and answered, and where the time went (queue, loading,
the model's steps and tokens/s, the tools).

**Export** (`export.py`): `summary.md` (the run, a table, then each case: the
question, what a good answer does, the checks, the answer), `results.json`,
and per case its result, answer, the turn's events, what the sandbox's checks
got and the chat's troubleshooting export - for a closer look (by Claude) at
whether the work lives up to what is expected.

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
- **The inner network and its firewall rules** (0.15.0) were tested with
  Docker 29.9.1 outside the VM, not yet on it. `update.sh status` shows
  whether the VM's firewall is current and tries each model container's way
  out; a model container that cannot load or answer after the update would be
  the sign of a rule too many.
- **Windows programs from the sandbox** (0.15.1, 0.16.0): Windows Forms and
  WPF build offline with the packs in the image and run under Wine - tried in
  the real image here (Docker 29.9.1, the default seccomp profile, offline),
  not yet on the VM. Typing through HubKeys (0.16.1) was tried under Wine 9.0
  with the image's packages, not in the built image: the self-test's
  "windows programs" line types Danish letters there. The packs and the Windows runtime come from conda-forge
  at the image build: if a pinned file is ever withdrawn there, the sandbox
  image does not build (and the update rolls back) until the pins are
  changed. Not known: whether an icon and version information get into an
  .exe built on Linux; whether `dotnet test` works offline and in people's
  chats (the classic test runner talks to its test host over TCP on
  localhost, which offline code and people's port rules refuse); how well the
  local models click by coordinates in screenshots (by text is the fallback),
  and programs with more than standard controls under Wine (DataGridView,
  owner-drawn controls, charts, DirectX).
- **Offline code** (internet asking first) needs Landlock ABI 4 and a nested
  seccomp filter in the sandbox; tested with ABI 7 here, also inside a Docker
  29.9.1 container with the default seccomp profile, not on the VM. The
  self-test's "offline code" line probes it there.
- **People's chats in the browser** depend on the browser keeping IndexedDB.
  The app asks for persistent storage, but Safari deletes a website's storage
  after seven days of use without a visit to it - except for a page added to
  the home screen, which is what to do on an iPhone. Export all chats is the
  backup.
- **Voices and remakes** (0.18.0, §8) ran only on a CPU ComfyUI with
  random-weight stand-ins. With the real weights nobody has heard them: how
  much of a voice a reference clip gives a song (ACE-Step learnt references
  from other parts of the same song - a speech clip is outside that), how
  well a remake keeps the song with a style clip, how clean Mel-Band RoFormer
  vocals are on the household's music, whether Seed-VC keeps the words and the
  tune, and the time and VRAM of each on the 7900 XTX. The Seed-VC and Kim
  checkpoints load with `weights_only` (tensors only): if one needs more, it
  fails with its name and the reason. The catalog's paths for them were not
  checked against Hugging Face from here. Seed-VC (April 2025) and its
  dependencies are pinned; MSST is pinned to the commit the node was written
  against (its import line is checked when loaded). The first remake heard
  (chat 24) did not follow its song; whether 0.20.2's own-sound timbre and
  keep 0.5 make remakes follow, and what the codes check says on the real
  weights, is not known yet.
- **Video** (0.19.0, §8): the test runs on the card (2026-10-25) measured
  the 5B at 720p and 121 frames - 28-41 s a step, 10-14 minutes a clip - and
  found its whole decode out of memory; in tiles (0.22.1) the second run's
  clip from a description came out right. Its clip from a picture did not:
  after a 14B job the Hub had given up on, the 5B's clip kept the picture only
  in the top-left corner of its first frame (448 px square, where the first
  decode tile's overlap begins), and the frames after it were flat colours -
  not explained yet (ComfyUI's log). Unknown on the 7900 XTX:
  the 14B pair's time, fast and full, and whether it fits the VM's RAM; the fp8 experts on gfx1100; and the 14B pair's memory -
  ComfyUI loads the high-noise expert, then the low-noise one (14.3 GB each),
  which with the 6.7 GB text encoder is about 35 GB of weights for the VM's
  40 GB of RAM if ComfyUI keeps them all cached. If it swaps or runs out,
  `COMFY_ARGS` in `.env` is the knob (`--cache-none`, `--reserve-vram`). Also
  how the models do with Danish prompts (umt5 is multilingual; Wan learnt
  from English and Chinese captions).

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
