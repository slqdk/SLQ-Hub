# Handoff

The state of SLQ Hub for whoever works on it next (a person or Claude).
Read `docs/DESIGN.md` for the decisions; this file says what exists, what
has been checked, and - honestly - what has not.

## State (0.1.1, 2026-10-22)

Everything in the design is built: the install path, the Hub core (scheduler,
llama-server supervisor, the Ollama/OpenAI/Anthropic APIs), the agent harness
with all its tools, projects with SLQ Sync, images through ComfyUI, libraries
(multimodal RAG), MCP, cloud escalation, the web UI (responsive, PWA) and the
command line self-test.

**Nothing has run on the real hardware yet.** The first install on the
Proxmox host is the real test. Expect to fix things there; the self-test
(`python -m app.cli selftest`) is the place to start.

The first install does not use `create-vm.sh`: Søren made VM 222 by hand in
the Proxmox UI (at first SeaBIOS, i440fx, CPU `x86-64-v2-AES`, 34 GiB, no
GPU) and installs Ubuntu 24.04.2 from the ISO. He was given the changes to
make before installing (`docs/INSTALL.md`, "Or: make the VM by hand"); his
`qm config 222` afterwards has not been seen yet. `install.sh` now checks the
VM before installing anything (stage `vm`).

First real-hardware observation: starting VM 222 right after VM 104 released
the card (GPU at host address `0000:0a:00.0`) failed with
`error writing '1' to '.../0000:0a:00.0/reset': Inappropriate ioctl for device`
and then `kvm: ../hw/pci/pci.c:1654: pci_irq_handler: Assertion '0 <= irq_num
&& irq_num < PCI_NUM_PINS' failed` - the card no longer answered config reads
(the RDNA3 reset problem the design expected). Advised: reboot the Proxmox
host. Whether that fixed it, and whether a plain reboot of VM 222 (which
`install.sh` does once) hits the same problem, is not known yet.

## What has been checked, and how

| Area | Checked by |
|---|---|
| Scheduler | 14 unit tests: priorities, aging, slots, affinity, leases with in-lease switching (two leases that both want ComfyUI, a full residency), cancellation, preload, a crashed model server noticed and loaded again. |
| Model APIs | 23 tests against a fake llama-server: Ollama (chat, generate, show, tags, embed, key in the path), OpenAI (chat, stream, tools, images), Anthropic (messages, stream, tools, thinking), LAN guard, CORS, key priority. |
| Agent | 22 tests: plain turns, tools in chat and project folders, revisions, stopping (in a tool and while writing), retry/edit, SSE replay, attachments, image generation switching the card and back, MCP over HTTP (approval, always-allow, masked secrets) and over stdio in the sandbox, escalation (explicit and automatic), subagents, compaction, skills, memory, artifacts. |
| SLQ Sync | 8 tests (one: writes never follow a symlink planted in the folder), including a round trip with the real `slq_sync.py` client from the SLQ Companion repo against a live server (clone, both-way edits, a conflict, its release). Skipped when that file is not next to this repo. |
| Libraries | 5 tests with keyword-hashing fake embedders: text and page-image indexing, fused search, re-ranking, replacing and deleting documents, GPU indexing through the queue, broken files. |
| Web UI | `tsc` + `vite build`; every page and the main flows clicked through in Chromium (Playwright) against `python -m tests.devserver` (the Hub with fake services): sign-in, chat with markdown/maths/code, tool strip, artifact panel, approvals, subagent, image tool, projects (files, editor, settings, sync), libraries, all settings tabs, dark mode, phone width. |
| Shell scripts | `shellcheck`. `create-vm.sh --dry-run` prints every command. `install.sh`'s VM check: 11 cases against fake `/sys` and `/proc` trees (card present or not, its audio function alone, i440fx, bare metal, Secure Boot on, legacy BIOS, no AVX2, low space with and without LVM), and once against a real sysfs (not a Proxmox VM). The `qm config ... hostpci` one-liner in INSTALL.md against a fake `qm`. |
| Compose | `docker compose config` (v2.40.3) with a `.env` written the way `install.sh` writes it: renders, and the admin password hash arrives intact. |
| llama.cpp | b11783 built from source (CPU): every command line the Hub builds parses; the real supervisor started the real llama-server through the Hub's scheduler, runner and client with a tiny random model - load, streaming (the real chunk format), a killed server noticed and reloaded, unload. Embeddings (strings and `content` arrays) and `/v1/rerank` answer in the shapes the Hub expects. |
| ComfyUI | v0.42.3 on CPU with placeholder model files: all seven workflow graphs (Qwen-Image 2.1 generate, turbo, edit with 1 and 3 images, background removal, FLUX.2 klein generate and edit) pass ComfyUI's own validation. Not rendered (no real weights). |
| MCP over stdio | A real stdio MCP server through the real sandbox daemon (`execd.py`): initialize, a server-to-client ping, tools, an 18 KB result. |

## Not verified - check these on the real system

1. **create-vm.sh** on the real Proxmox host: storage names, the hostpci copy
   from VM 104, cloud-init snippets on `local`, the guest agent IP wait. (The
   first VM was made by hand, so this is still untested.) Likewise the VM
   check in `install.sh` on a real q35/OVMF VM with the card: the sysfs paths
   it reads (PCI vendor/class, host bridge id, the `SecureBoot` EFI variable).
2. **install.sh** end to end on Ubuntu 24.04: HWE kernel 7.0, amdgpu-dkms 31.60
   building against it, `/dev/kfd`, PCIe atomics, Docker CE, the firewall unit.
3. **llama.cpp on the card** (`server-rocm-b11783`): the flags parse (checked
   on a CPU build), but VRAM use (~21 GiB expected for Qwen3.8-27B with an 80K
   pool), ROCm itself and real tokens/s are unknown until the first load.
4. **Qwen3.8 templates**: tool calls parsed by llama-server (`--jinja`),
   `reasoning_content` passed back within a turn, `reasoning_effort`
   (low/medium/xhigh), `enable_thinking`. The Hub also parses text tool calls
   (Qwen3-Coder XML and Hermes JSON) as a fallback.
5. **ComfyUI on the card**: the torch ROCm 7.14 wheels, the Qwen-Image 2.1 int8
   kernels on gfx1100, a real render, VRAM release with `/free` before the LLM
   loads again. (The graphs themselves pass ComfyUI's validation.)
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
