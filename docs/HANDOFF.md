# Handoff

The state of SLQ Hub for whoever works on it next (a person or Claude).
Read `docs/DESIGN.md` for the decisions; this file says what exists, what
has been checked, and - honestly - what has not.

## State (0.1.0, 2026-10-22)

Everything in the design is built: the install path, the Hub core (scheduler,
llama-server supervisor, the Ollama/OpenAI/Anthropic APIs), the agent harness
with all its tools, projects with SLQ Sync, images through ComfyUI, libraries
(multimodal RAG), MCP, cloud escalation, the web UI (responsive, PWA) and the
command line self-test.

**Nothing has run on the real hardware yet.** The first install on the
Proxmox host is the real test. Expect to fix things there; the self-test
(`python -m app.cli selftest`) is the place to start.

## What has been checked, and how

| Area | Checked by |
|---|---|
| Scheduler | 14 unit tests: priorities, aging, slots, affinity, leases with in-lease switching (two leases that both want ComfyUI, a full residency), cancellation, preload, a crashed model server noticed and loaded again. |
| Model APIs | 23 tests against a fake llama-server: Ollama (chat, generate, show, tags, embed, key in the path), OpenAI (chat, stream, tools, images), Anthropic (messages, stream, tools, thinking), LAN guard, CORS, key priority. |
| Agent | 22 tests: plain turns, tools in chat and project folders, revisions, stopping (in a tool and while writing), retry/edit, SSE replay, attachments, image generation switching the card and back, MCP over HTTP (approval, always-allow, masked secrets) and over stdio in the sandbox, escalation (explicit and automatic), subagents, compaction, skills, memory, artifacts. |
| SLQ Sync | 8 tests (one: writes never follow a symlink planted in the folder), including a round trip with the real `slq_sync.py` client from the SLQ Companion repo against a live server (clone, both-way edits, a conflict, its release). Skipped when that file is not next to this repo. |
| Libraries | 5 tests with keyword-hashing fake embedders: text and page-image indexing, fused search, re-ranking, replacing and deleting documents, GPU indexing through the queue, broken files. |
| Web UI | `tsc` + `vite build`; every page and the main flows clicked through in Chromium (Playwright) against `python -m tests.devserver` (the Hub with fake services): sign-in, chat with markdown/maths/code, tool strip, artifact panel, approvals, subagent, image tool, projects (files, editor, settings, sync), libraries, all settings tabs, dark mode, phone width. |
| Shell scripts | `shellcheck`. `create-vm.sh --dry-run` prints every command. |

## Not verified - check these on the real system

1. **create-vm.sh** on the real Proxmox host: storage names, the hostpci copy
   from VM 104, cloud-init snippets on `local`, the guest agent IP wait.
2. **install.sh** end to end on Ubuntu 24.04: HWE kernel 7.0, amdgpu-dkms 31.60
   building against it, `/dev/kfd`, PCIe atomics, Docker CE, the firewall unit.
3. **llama.cpp on the card** (`server-rocm-b11783`): the exact flags
   (`-kvu`, `--cache-ram`, `-ctk/-ctv q8_0`, `-fa on`), VRAM use (~21 GiB
   expected for Qwen3.8-27B with an 80K pool), real tokens/s.
4. **Qwen3.8 templates**: tool calls parsed by llama-server (`--jinja`),
   `reasoning_content` passed back within a turn, `reasoning_effort`
   (low/medium/xhigh), `enable_thinking`. The Hub also parses text tool calls
   (Qwen3-Coder XML and Hermes JSON) as a fallback.
5. **ComfyUI**: the torch ROCm 7.14 wheels, Qwen-Image 2.1 int8 files and the
   workflow node names (checked against v0.42.3's source, not run), VRAM
   release with `/free` before the LLM loads again.
6. **Embeddings and re-ranking** with the real GGUFs: the Qwen3-VL-Embedding
   chat format written out in `app/rag/library.py`, the re-ranker's score scale
   (log-odds or probability: both handled).
7. **Sandbox isolation** on the VM: the self-test checks that the sandbox cannot
   reach the Hub or the proxy, and can reach the internet.
8. **Nginx Proxy Manager**: SSE streaming (`proxy_buffering off`), uploads
   (`client_max_body_size`), the session cookie behind HTTPS.
9. **PWA** install on a phone through hub.slq.dk.
10. **FamilyLog** against the Hub's Ollama API with its real settings, and
    TwinCAT Coagent (format still unknown; all three formats and CORS exist).

## Where things are

| Path | What |
|---|---|
| `backend/app/gpu/scheduler.py` | The queue. Everything that uses the card goes through `acquire()`. |
| `backend/app/gpu/backend.py`, `runner.py`, `aux.py` | Loading/unloading on the card; the llama-server supervisor client; CPU helper servers. |
| `backend/app/api/` | Ollama, OpenAI, Anthropic formats. |
| `backend/app/agent/loop.py` | The agent loop (`Engine`) and turns (`Agent`). |
| `backend/app/agent/tools/` | The tools; `meta_tools.py` has `task` and `escalate`; `mcp.py` the MCP client. |
| `backend/app/projects/` | Projects, folders, the sync store and the `/api/sync` protocol. |
| `backend/app/images/` | ComfyUI workflows and the image pipeline. |
| `backend/app/rag/` | Libraries: extraction, indexing, search, the `library_search` tool. |
| `backend/app/cli.py` | `selftest`, `set-password`, `create-key`, `keys`. |
| `frontend/src/` | The UI: `views/` (chat, project, libraries, settings), `components/`, `lib/turns.ts` (the turn view model for history and live runs). |
| `services/` | The llm supervisor, the ComfyUI image, the sandbox daemon. |

## Working on it

    # backend tests (Python 3.12+)
    cd backend && pip install -r requirements.txt -r requirements-dev.txt
    python -m pytest -q -p no:cacheprovider

    # the Hub with fake services, for UI work (soren / correct horse battery)
    cd frontend && npm install && npx vite build
    cd ../backend && python -m tests.devserver --port 8080
    #   or run `npm run dev` in frontend/ (port 5173, proxies to :8080)

    # checks before a push
    cd frontend && npx tsc --noEmit && npx vite build
    shellcheck install.sh update.sh proxmox/create-vm.sh deploy/firewall/*.sh

## Known limits (by design, for now)

- One user (the admin). API keys for apps.
- Tool calls in a turn run one after another, not in parallel.
- Approvals wait up to ten minutes; the turn keeps its lease meanwhile, so a
  request for another model waits too.
- The run's live events are kept in memory for an hour; the stored messages
  are permanent.
- No custom ComfyUI workflows yet; no speech or video.
