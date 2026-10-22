# Handoff

The state of SLQ Hub for whoever works on it next (a person or Claude).
Read `docs/DESIGN.md` for the decisions; this file says what exists, what
has been checked, and - honestly - what has not.

## State (0.5.1, 2026-10-22)

Everything in the design is built: the install path, the Hub core (scheduler,
llama-server supervisor, the Ollama/OpenAI/Anthropic APIs), the agent harness
with all its tools, projects with SLQ Sync, images through ComfyUI, libraries
(multimodal RAG), MCP, cloud escalation, the web UI (responsive, PWA) and the
command line self-test.

**It runs on the real hardware since 2026-10-22**: VM 222 on Søren's Proxmox
host, installed with `install.sh` 0.1.3. The self-test passed: database, GPU
(24.0 GB VRAM), all default models on disk, sandbox (Python 3.12.3, Node
18.19.1, tesseract 5.3.4) with its isolation (no Hub, no LAN) and internet,
web search (38 results), ComfyUI (`cuda:0 AMD Radeon RX 7900 XTX : native`,
torch 2.14.1+rocm7.14), Qwen3.8-27B answering at 33 tokens/s (a short reply),
text embeddings (1024 dimensions). Web UI on the LAN:
`http://30.11.0.222:8080`, admin user `adminsorenlyder`. Not done yet: Nginx
Proxy Manager / hub.slq.dk, an image render, FamilyLog, and days of uptime
(see "The D3 problem").

## The machine, as installed

- **Host** TX82PVE: Proxmox VE 8.4.19 (its support ended 2026-08-31, the UI
  says), kernel 6.8.12-37-pve, AMD CPU, GRUB, `quiet iommu=pt`.
- **Host passthrough config** (changed 2026-10-22): `/etc/modprobe.d/vfio.conf`
  = `options vfio-pci ids=1002:744c,1002:ab30,1002:73bf,1002:ab28
  disable_idle_d3=1` and `softdep radeon|amdgpu|snd_hda_intel pre: vfio-pci`.
  Before, the IDs were only those of a Navi 21 card (73bf/ab28, kept in case
  that card is still in the machine), so nothing bound the 7900 XTX at boot:
  the host's snd_hda_intel took its audio function and the card was the
  host's boot console. amdgpu and radeon are blacklisted. `vreset.service`
  (it only ran `echo device_specific > .../0000:0a:00.0/reset_method`, which
  the kernel rejects for this card) is disabled. `/etc/modules` still lists
  `vfio_virqfd` (gone since kernel 6.2; a harmless warning).
- **The card**: Sapphire NITRO+ RX 7900 XTX Vapor-X, host `0000:0a:00.0`
  `[1002:744c]` (rev c8) and `.1` HDMI audio `[1002:ab30]`, IOMMU groups 18
  and 19. No reset method (`Inappropriate ioctl for device` on reset).
- **VM 222** `Ubuntu-SLQHub`, made by hand (not `create-vm.sh`): q35, OVMF
  with `pre-enrolled-keys=0`, `cpu: host`, 12 cores, 40960 MB, `balloon: 0`,
  530G on `VMDisc01`, `hostpci0: 0000:0a:00,pcie=1` (all functions, no
  `x-vga`), default display, agent on, `onboot: 1`. Ubuntu 24.04 from the
  24.04.2 ISO, hostname `slq-hub`, Linux user `adminsorenlyder`, IP
  30.11.0.222, `/` on LVM with the whole disk (526.95g). HWE kernel
  7.0.0-38-generic with `amdgpu.runpm=0`, amdgpu-dkms `7.1.9-2407772.24.04`
  (31.60), Docker 29.9.2. In the guest the card is `01:00.0`: `CP RS64
  enable`, `PCIE atomic ops is not supported` (KFD accepts it anyway - GFX11
  RS64 firmware), `BAR=256M`, 24560M VRAM.
- **VM 104** `OllamaWebui` (the old GPU VM): `onboot: 0`, kept as a fallback.
  35000 MB, 470G. The top of its config (bios, machine, hostpci) not seen.

## What happened on the first install

Kept for diagnosis - the error strings are what to search for next time.

1. **Card moved from VM 104 without a host reboot**: VM 222 would not start -
   `error writing '1' to '/sys/bus/pci/devices/0000:0a:00.0/reset':
   Inappropriate ioctl for device`, then `kvm: ../hw/pci/pci.c:1654:
   pci_irq_handler: Assertion '0 <= irq_num && irq_num < PCI_NUM_PINS'
   failed` (the card answered config reads with 0xFF). A host reboot fixed it.
2. **The Ubuntu installer with the card, idle ~34 minutes**: `QEMU[4519]:
   error: kvm run failed Bad address` - amdgpu reading register 0x5466
   (`mov r12d,[r12]`, R12 = rmmio + 0x15198) and the host unable to satisfy
   it. The console froze. A reset from the UI then logged `kvm: vfio: Unable
   to power on device, stuck in D3` and `vfio_bar_restore: reset recovery -
   restoring BARs`, leaving `qmpstatus: prelaunch` (no "running" overlay in
   the tree).
3. **A false stop in `install.sh`**: "the amdgpu module is not loaded" while
   `/proc/modules` listed it - `lsmod | grep -q` under pipefail (SIGPIPE).
   Fixed in 0.1.3.

### The D3 problem

Working explanation, not proven: amdgpu's runtime power management (BACO, on
by default for Navi 31) put the idle card into D3, and it did not come back;
the read at step 2 was the next access. Step 1 fits too (the card left in a
low-power state after VM 104). The alternative is QEMU issue #2574 ("kvm run
failed Bad address" with Navi 31 and guest kernels 6.7+, no cause found
there). In place now: `amdgpu.runpm=0` in the guest (`install.sh` writes it,
the GPU check reports it), `disable_idle_d3=1` and early vfio-pci binding on
the host, and Ubuntu installed without the card (`docs/INSTALL.md`). Since
then the VM rebooted once with the card (`install.sh`'s reboot) and came
back. Watch for the two error strings above in `journalctl -b | grep QEMU` on
the host if the VM ever stops by itself.

## What has been checked, and how

| Area | Checked by |
|---|---|
| Real hardware | VM 222 (above): `install.sh` 0.1.3 end to end - stages `vm`, `config`, `system`, `gpu`, `build`, `models`, `start`, `selftest` - and every self-test line passed (image generation skipped). So the VM check's sysfs reads work on a real q35/OVMF VM with the card, amdgpu-dkms 31.60 builds and loads on 7.0.0-38, ROCm sees the card (`/dev/kfd`), the llama.cpp ROCm image and the ComfyUI torch build use it, and the sandbox firewall holds. |
| Scheduler | 14 unit tests: priorities, aging, slots, affinity, leases with in-lease switching (two leases that both want ComfyUI, a full residency), cancellation, preload, a crashed model server noticed and loaded again. |
| Model APIs | 23 tests against a fake llama-server: Ollama (chat, generate, show, tags, embed, key in the path), OpenAI (chat, stream, tools, images), Anthropic (messages, stream, tools, thinking), LAN guard, CORS, key priority. |
| Agent | 23 tests: plain turns, tools in chat and project folders, revisions, stopping (in a tool and while writing), retry/edit, SSE replay, attachments, project files put in a message (text inline and fenced, too big or binary as pointers, pictures as images, refused paths, the pointer once the message is old), image generation switching the card and back, MCP over HTTP (approval, always-allow, masked secrets) and over stdio in the sandbox, escalation (explicit and automatic), subagents, compaction, skills, memory, artifacts. |
| SLQ Sync | 8 tests (one: writes never follow a symlink planted in the folder), including a round trip with the real `slq_sync.py` client from the SLQ Companion repo against a live server (clone, both-way edits, a conflict, its release). Skipped when that file is not next to this repo. |
| Libraries | 5 tests with keyword-hashing fake embedders: text and page-image indexing, fused search, re-ranking, replacing and deleting documents, GPU indexing through the queue, broken files. |
| Web UI | `tsc` + `vite build`; every page and the main flows clicked through in Chromium (Playwright) against `python -m tests.devserver` (the Hub with fake services): sign-in, chat with markdown/maths/code, tool strip, artifact panel, approvals, subagent, image tool, projects (files, editor, settings, sync), libraries, all settings tabs, dark mode, phone width. |
| Shell scripts | `shellcheck`. `create-vm.sh --dry-run` prints every command. `install.sh`'s VM check: 12 cases against fake `/sys` and `/proc` trees (card present or not, its audio function alone, a card asleep in D3hot, i440fx, bare metal, Secure Boot on, legacy BIOS, no AVX2, low space with and without LVM). The `amdgpu.runpm=0` GRUB drop-in, sourced the way `grub-mkconfig` does, appends to the existing command line. The `qm config ... hostpci` one-liner in INSTALL.md against a fake `qm`. |
| Self-test | Against `tests.devserver`: the chat line says "incl. loading" on a cold card and "already loaded" after a preload. |
| Add from Hugging Face | 5 tests (`tests/test_models_hf.py`) against a fake huggingface.co built from the real tree of `unsloth/Qwen3.8-27B-GGUF` (fetched 2026-10-22: directories, a split BF16, an MTP draft, imatrix, two mmproj, LFS sizes) and the model-info `gguf` block: repo names and links, quant labels, grouping, the API (lookup, add with the context capped to the model's, download through the queue, the llama-server command line for a single file and a split, survives a restart, listed by `/api/tags`, removal of files and folders, refusals: gated, unknown, no GGUF, bad names, taken names, disk space). The dialog clicked through in Chromium against the dev server's fake repository. |
| Files in a message | Clicked through in Chromium at 1920, 1280 and 390 px against the dev server: + -> Project files opens the panel, the search finds a file, its + and the file bar's Add to chat put files in the message (chips in the composer, a count in the panel), sending creates the chat with the panel still open, the message shows the files and a click opens one; the project page's Files tab has no + buttons. |
| Tab rows | In Chromium: the old CSS gave `.tabs` 1px of vertical overflow (scrollHeight 42, clientHeight 41 - a scrollbar with Windows' classic scrollbars, reported by Søren); the inset-shadow rule gives none. |
| Compose | `docker compose config` (v2.40.3) with a `.env` written the way `install.sh` writes it: renders, and the admin password hash arrives intact. |
| llama.cpp | b11783 built from source (CPU): every command line the Hub builds parses; the real supervisor started the real llama-server through the Hub's scheduler, runner and client with a tiny random model - load, streaming (the real chunk format), a killed server noticed and reloaded, unload. Embeddings (strings and `content` arrays) and `/v1/rerank` answer in the shapes the Hub expects. |
| ComfyUI | v0.42.3 on CPU with placeholder model files: all eleven workflow graphs (Qwen-Image 2.1 generate, turbo, edit with 1 and 3 images, background removal, FLUX.2 klein generate and edit, ERNIE-Image and Turbo each with and without the prompt enhancer) pass ComfyUI's own validation, including `TextGenerate`'s dynamic `sampling_mode.*` inputs. The ERNIE graphs follow the official templates (`Comfy-Org/workflow_templates`: `image_ernie_image.json`, `image_ernie_image_turbo.json`, fetched 2026-10-22). Tests: the graph shapes, and an agent turn with only Turbo downloaded (the enhancer's text comes back through `PreviewAny` into the tool result), the enhancer switched off, an edit with ERNIE chosen going to Qwen-Image. |
| MCP over stdio | A real stdio MCP server through the real sandbox daemon (`execd.py`): initialize, a server-to-client ping, tools, an 18 KB result. |

## Not verified - check these on the real system

1. **create-vm.sh** on the real Proxmox host: storage names, the hostpci copy
   from VM 104, cloud-init snippets on `local`, the guest agent IP wait. (The
   first VM was made by hand.)
2. **llama.cpp on the card beyond the self-test**: VRAM with Qwen3.8-27B and
   its 80K pool (~21 GiB expected; the Hub had preloaded the model, so the
   self-test's 0.3 GB was before the load - from 0.1.5 it prints the VRAM in
   use after its chat), tokens/s on long answers, two parallel requests,
   Qwen3.6-35B-A3B, switching between them.
3. **Qwen3.8 templates**: tool calls parsed by llama-server (`--jinja`),
   `reasoning_content` passed back within a turn, `reasoning_effort`
   (low/medium/xhigh), `enable_thinking`. The Hub also parses text tool calls
   (Qwen3-Coder XML and Hermes JSON) as a fallback.
4. **ComfyUI on the card**: torch sees it, but no render yet (`sudo bash
   /opt/slq-hub/update.sh compose exec hub python -m app.cli selftest
   --images`): the Qwen-Image 2.1 int8 kernels on gfx1100, FLUX.2 klein (it
   is installed), VRAM release with `/free` before the LLM loads again.
   ERNIE-Image is untried too: whether the prompt enhancer answers in English
   for English requests, how long enhancer plus the 16 GB model take on
   gfx1100, and RAM with three large files in ComfyUI's cache.
5. **Embeddings and re-ranking**: text embeddings answer (1024 dimensions);
   the Qwen3-VL-Embedding chat format in `app/rag/library.py` and the
   re-ranker's score scale (log-odds or probability: both handled) wait for
   the first library upload.
6. **Nginx Proxy Manager**: SSE streaming (`proxy_buffering off`), uploads
   (`client_max_body_size`), the session cookie behind HTTPS.
7. **PWA** install on a phone through hub.slq.dk.
8. **FamilyLog** against the Hub's Ollama API with its real settings, and
   TwinCAT Coagent (format still unknown; all three formats and CORS exist).
9. **Days of uptime with the card**: no `kvm run failed Bad address` or
   `stuck in D3` (see "The D3 problem"); whether stopping and starting the VM
   (not a reboot inside it) needs a host reboot.
10. **PCIe atomics**: the guest has none, likely because the card is passed
    with all its functions (QEMU 8.2+ enables AtomicOps on the root port only
    for a single-function device). KFD, llama.cpp and torch work without.
    Passing only `0000:0a:00.0` should give them - not tried, only worth it if
    something needs them.
11. **Add from Hugging Face against the real site**: it works there - Søren added `ornith-ai/Ornith-1.5-9B-GGUF` and `HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive` (both the BF16 file, 18.0 and 17.5 GB): looked up, downloaded by the Hub's own queue (so `app/downloads.py` has moved real multi-GB files), and Ornith loaded on the card (qwen35 architecture, llama.cpp b11783). Not tried: the tree API's pagination, a gated repository with and without `HF_TOKEN`. The VRAM rating is a rule of thumb (weights vs. card), not a KV-cache calculation.
12. **Files in a message with the real model**: whether Qwen3.8 edits a file
    it was given in the message with `edit_file` straight away (the text is
    exact, without `read_file`'s line numbers), and whether 30% of the
    context for file text is the right share in long project chats.

## Where things are

| Path | What |
|---|---|
| `backend/app/gpu/scheduler.py` | The queue. Everything that uses the card goes through `acquire()`. |
| `backend/app/gpu/backend.py`, `runner.py`, `aux.py` | Loading/unloading on the card; the llama-server supervisor client; CPU helper servers. |
| `backend/app/api/` | Ollama, OpenAI, Anthropic formats. |
| `backend/app/catalog.py`, `hf.py`, `downloads.py` | The model catalog (bundled + `data/models.custom.json` + overrides), Hugging Face lookups for Add from Hugging Face, the in-app download queue. |
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
