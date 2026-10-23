# Handoff

The state of SLQ Hub for whoever works on it next (a person or Claude).
Read `docs/DESIGN.md` for the decisions; this file says what exists, what
has been checked, and - honestly - what has not.

## State (0.13.3, 2026-10-23)

Everything in the design is built: the install path, the Hub core (scheduler,
llama-server supervisor, the Ollama/OpenAI/Anthropic APIs), the agent harness
with all its tools, projects with SLQ Sync, images through ComfyUI, libraries
(multimodal RAG), MCP, cloud escalation, the web UI (responsive, PWA) and the
command line self-test. Since 0.8.0 there is also a chat page for phones
(`/chat`) on the model API, for the rest of the household. A chat that behaves
strangely can be exported from its ⋯ menu ("Export for troubleshooting": a ZIP
with messages, runs, live events, the next turn's context, the queue and the
model server's log, and since 0.10.0 what each model step was sent and the
system prompts used) - ask for that first. A small model that gets stuck hands
the turn over to a stronger one on the server (Settings → Assistant). A key
can be set to work away from home, so the phone page works at
`https://hub.slq.dk/chat`. Since 0.11.0 the owner can add **people**
(Settings → People): the same chat with their own login, at home or also
away, with their chats kept in their own browser - the Hub answers each
message in a temporary folder and keeps nothing of it (DESIGN §6 "People's
chats", §11). Code for people runs only where the sandbox has Landlock.
Since 0.13.0 the assistant makes **sound** (DESIGN §8 "Sound"): speech on the
CPU in a new `speech` container (Røst v3 for Danish, Chatterbox Multilingual
V3 for 23 languages), so the chat model stays on the card; music (ACE-Step
1.5) and sound effects (Stable Audio 3) on the card through ComfyUI and the
queue. No sound model is downloaded by default (Settings → Models → Sound,
music and speech). On VM 222 the speech container runs Røst v3, Røst v3
Turbo and Chatterbox. Søren's first listening test (below): Røst v3 Turbo at
full weights sounds best - about 4x slower than real time - while 8-bit
weights everywhere made mistakes, so 0.13.3's "Faster" puts only the MLP
layers in 8 bits (not heard yet). Music and sound effects have not run on
the card yet.

**It runs on the real hardware since 2026-10-22**: VM 222 on Søren's Proxmox
host, installed with `install.sh` 0.1.3. The self-test passed: database, GPU
(24.0 GB VRAM), all default models on disk, sandbox (Python 3.12.3, Node
18.19.1, tesseract 5.3.4) with its isolation (no Hub, no LAN) and internet,
web search (38 results), ComfyUI (`cuda:0 AMD Radeon RX 7900 XTX : native`,
torch 2.14.1+rocm7.14), Qwen3.8-27B answering at 33 tokens/s (a short reply),
text embeddings (1024 dimensions). Web UI on the LAN:
`http://30.11.0.222:8080`, admin user `adminsorenlyder`. Since then:
hub.slq.dk serves the UI through Nginx Proxy Manager, and pictures are made
and edited on the card (chat 9, below). Not done yet: FamilyLog, and days of
uptime (see "The D3 problem").

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
| Model APIs | 29 tests against a fake llama-server: Ollama (chat, generate, show, tags, embed, key in the path, `HEAD /`), OpenAI (chat, stream, tools, images, the extra fields of `/v1/models`), Anthropic (messages, stream, tools, thinking), LAN guard, CORS, key priority, the `/chat` route, the home network for keys, keys that work away from home (through the proxy only - not from a direct outside peer or the sandbox; a wrong key from outside gets the same 403; an address list still applies; last used only once let in; switched back), every key from outside still only through the proxy. |
| Agent | 43 tests: plain turns, the files a turn made or changed (by a tool and by a command; scratch files removed again, `.hub` and untouched files left out; newest first) as a run step, served for the card (a range for the player, a download, PDFs without the sandbox CSP), the folder snapshot's ignores (node_modules, `.pyc`, links) and its limit, the local time each user message was sent (in the message, the same text in later turns; summer and winter time, `TZ`), tools in chat and project folders, revisions, stopping (in a tool and while writing), retry/edit, SSE replay, messages sent while a turn works (read after a tool step, while the last answer is written, after the end, with Stop), attachments, project files put in a message (text inline and fenced, too big or binary as pointers, pictures as images, refused paths, the pointer once the message is old), image generation switching the card and back, MCP over HTTP (approval, always-allow, masked secrets) and over stdio in the sandbox, escalation (explicit and automatic), subagents, compaction (and a summary request that fits the context: 40 big results before the new question, 8K context), a long single turn that stays inside a 14K context (12 results of 9,000 characters, tool definitions counted), the loop guard (the same search refused, a failing read retried once then refused, the forced answer without tools and its notice, repeats with new words, the same bash step three times, a read after an edit running again), hand-over to the stronger local model (a loop, three failed calls, the escalate tool; the card switches within the lease; its own system prompt and note, no reasoning of the other model; nobody to hand over to; local first, then the cloud), what each step was sent and why it stopped, a model's own thinking and sampling, system prompts stored once and removed with the chat, the note when a picture call names another model than the chat's, skills, memory, artifacts. |
| SLQ Sync | 8 tests (one: writes never follow a symlink planted in the folder), including a round trip with the real `slq_sync.py` client from the SLQ Companion repo against a live server (clone, both-way edits, a conflict, its release). Skipped when that file is not next to this repo. |
| Libraries | 5 tests with keyword-hashing fake embedders: text and page-image indexing, fused search, re-ranking, replacing and deleting documents, GPU indexing through the queue, broken files. |
| Web UI | `tsc` + `vite build`; every page and the main flows clicked through in Chromium (Playwright) against `python -m tests.devserver` (the Hub with fake services): sign-in, chat with markdown/maths/code, tool strip, artifact panel, approvals, subagent, image tool, projects (files, editor, settings, sync), libraries, all settings tabs, dark mode, phone width. |
| Files card (0.12.0) | In Chromium against the dev server ("jingle": its fake sandbox writes a real 1.2 s WAV and a PDF, then notes are written) (14 checks): three files listed newest first with folder and "new", the sound playing in the card (duration 1.2 s), Download saving `jingle.wav`, the PDF's Open aiming at the raw file served as a PDF without the sandbox CSP (headless Chromium has no PDF viewer), Open showing a file in the side panel, Add to chat putting it in the composer, the panel's player, the card after a reload, no sideways scroll at phone width, the buttons moving under the name when the chat is narrow beside the panel; a person's card (her `fib.py`): listed, downloaded from the device's store, Open showing the chat's files, no Add to chat. |
| Shell scripts | `shellcheck`. `create-vm.sh --dry-run` prints every command. `install.sh`'s VM check: 12 cases against fake `/sys` and `/proc` trees (card present or not, its audio function alone, a card asleep in D3hot, i440fx, bare metal, Secure Boot on, legacy BIOS, no AVX2, low space with and without LVM). The `amdgpu.runpm=0` GRUB drop-in, sourced the way `grub-mkconfig` does, appends to the existing command line. The `qm config ... hostpci` one-liner in INSTALL.md against a fake `qm`. |
| Self-test | Against `tests.devserver`: the chat line says "incl. loading" on a cold card and "already loaded" after a preload. |
| Add from Hugging Face | 6 tests (`tests/test_models_hf.py`), one of them the search by name (`/api/models` faked: words matched in the repo id, GGUF only, by downloads), against a fake huggingface.co built from the real tree of `unsloth/Qwen3.8-27B-GGUF` (fetched 2026-10-22: directories, a split BF16, an MTP draft, imatrix, two mmproj, LFS sizes) and the model-info `gguf` block: repo names and links, quant labels, grouping, the API (lookup, add with the context capped to the model's, download through the queue, the llama-server command line for a single file and a split, survives a restart, listed by `/api/tags`, removal of files and folders, refusals: gated, unknown, no GGUF, bad names, taken names, disk space). The dialog clicked through in Chromium against the dev server's fake repository. |
| Files in a message | Clicked through in Chromium at 1920, 1280 and 390 px against the dev server: + -> Project files opens the panel, the search finds a file, its + and the file bar's Add to chat put files in the message (chips in the composer, a count in the panel), sending creates the chat with the panel still open, the message shows the files and a click opens one; the project page's Files tab has no + buttons. |
| Image models from links | 5 tests (`tests/test_images_models.py`) against a fake Hugging Face (real safetensors headers, Range requests) and a fake Civitai (model and version API, a login-only download, the redirect to its storage): link parsing, the checkpoint check (SDXL, v-prediction, no text encoders, no VAE, refiner, SD 1.5, FLUX, LoRA), presets and their limits, lookup and add from both sites, the 401 that names `CIVITAI_TOKEN`, the token kept from the storage host, settings changes, removal (not while it is the Settings model), a chat with its own SDXL model (the graph, clip skip, tags, the SDXL size) and the tool description it gets. The dialog clicked through in Chromium at 1440 and 390 px. |
| Phone chat page | 0.10.1: 20 more checks on iPhone SE and iPhone 13 (the Picture chip with its way back and the start text, four thinking levels for Qwen3.8 with an old "Think on" becoming Think, Think hard sent as `reasoning_effort: xhigh`, the on/off button for Qwen3.6, the level remembered per choice when switching back). Before: 34 checks in Chromium (Playwright) with iPhone 13 and iPhone SE (dark) emulation against the dev server: Settings (the page's address, Only the home network, Copy the phone link); connecting (a wrong paste, an unknown key, the link - the key is kept and leaves the address); a streamed answer (Bearer key, thinking off by default, the system prompt, the default model, the title); Think on; the history sent; a photo (shrunk to JPEG, sent as `image_url`); a picture (portrait size, kept as data); the viewer's Save link; Stop and Try again; two chats kept over a reload, with their photo and picture; delete; copy; away from home (a 403 from the LAN guard); a key switched off (said in the chat, then the key screen after a reload); the composer at 320 px. The requests Enchanted (`HEAD /`, Bearer, `/api/tags`, `/api/chat`) and Reins (key in the path, `GET` of the base address) make, replayed with curl; read from their sources (Enchanted 2026-07 and its OllamaKit fork, Reins 2026-10). |
| Sampling for added models | 2 tests (`tests/test_models_sampling.py`) and one more check in the Hugging Face test: the architecture read from a GGUF header (skipping a number and a string list before it; not a GGUF; missing), Qwen3-family values for a model added before 0.9.1 (from its file), with and without thinking; none for a llama model; an entry's own values kept; `--temp` and `--presence-penalty=` among the extra flags leave those values out. |
| Chat export | 7 tests (`tests/test_export.py`), one of them the model server's log without the vision models' position warnings: Markdown (thinking, tool calls and results, one section per turn, the turn line), the ZIP (every part; full tool arguments; the model card and safe settings - a cloud API key set in Settings appears nowhere in it; the picture as a file and linked in the transcript; context.json with the system prompt, history, tools and request, the picture left out; the event log with streamed pieces joined; the queue job of the run), 404/400/401, joining events, file names. Both menu items and the phone page's Export this chat clicked through in Chromium (downloads named after the chat, unzipped, the phone file has no whole key). |
| Settings 0.10.0 | In Chromium against the dev server (12 checks): the hand-over setting (Qwen3.8 27B by default), a model's Settings dialog (sampling prefilled from the card, thinking "Always think first" and a new temperature saved, "Use the model's own sampling" back to the card with thinking kept, the composer of a new chat following the model's thinking), Add from Hugging Face searching "Ornith 1.5 9B" (two repositories listed, a click looks one up, Q8_0 preselected for 9B, BF16 "twice the size of Q8_0"), and a live hand-over (Qwen3.6 35B-A3B repeats a web search, refused twice, Qwen3.8 27B answers; the notice live and after a reload; the footer names both models). |
| Keys away from home | In Chromium against the dev server started as if behind a proxy (`--proxy 127.0.0.1 --public-url https://hub.example.test --lan-host 192.168.1.10`; uvicorn without its own proxy handling, as in production) (16 checks): the Apps page and dialog text, "Where it works" hiding "Only the home network" when away, the phone link through the public address and the home-only link, the list's per-key choice, switching a home-network key to away removing that limit (and back), the phone page from outside (`X-Forwarded-For: 203.0.113.9`) with a home key ("not through ...", "The Hub sees this phone at 203.0.113.9"), Try again after the key was switched (the key from the link was kept), an answer from outside, an away key connecting straight away. |
| Listening test, 0.13.2 (2026-10-23) | Søren played the self-test's samples in Settings → Assistant → Sound (the same Danish sentence, seed 1). Røst v3 Turbo at Best (factor 3.92) "clearly best", Røst v3 at Best (5.82) next. Every Chatterbox sample sounds like an English speaker trying to speak Danish. Røst v3 Turbo with every layer in 8 bits (2.10) skipped a word; Røst v3 with every layer in 8 bits, with its own decoder (3.11) and with Turbo's (1.95), pronounced the first word oddly and sounded clipped, otherwise correct. Hence 0.13.3: 8-bit MLP layers only as "Faster" (the attention layers keep the speech aligned with the text), every layer as "Fastest" with the warning, and the advice to use Røst v3 Turbo at Best for Danish. |
| Real hardware, 0.13.1 self-test (2026-10-23) | `selftest --speech` on VM 222 (AMD Ryzen 9 5950X, AVX2 only, 10 threads), the same Danish sentence with seed 1, one line per setting (best / Turbo's decoder / Turbo's decoder + 8-bit, as 0.13.1 ordered them). Røst v3: 3.4 s of speech in 19.6 s, factor 5.75, T3 91 tokens at 6.4/s (14.2 s) and decoder 5.3 s; Turbo's decoder: T3 15.4 s, decoder 1.4 s (factor 6.16 - but only 2.7 s of sound came out of the same 91 tokens: listen); + 8-bit: 3.2 s in 7.4 s, factor 2.28, T3 85 tokens at 15.7/s, decoder 1.9 s. Røst v3 Turbo: factor 3.75, T3 11.0/s; 8-bit: 1.82, T3 26.4/s (faster than real time). Chatterbox Multilingual V3 with Resemble's repetition penalty: stopped by itself at 79 tokens (2.7 s) in both full-weight runs - factor 7.46, decoder 6.2 s, and 5.62 with Turbo's decoder (1.2 s); with 8-bit weights it ran to the ceiling (228 tokens, 9.0 s, the self-test's WARN). T3 on 5 threads as fast as on 10 (15.8 vs 15.7, 25.5 vs 26.4, 14.3 vs 12.9 tokens/s). So T3 is two thirds of the time on this CPU and 8-bit weights the big lever: 0.13.2 made 8-bit "faster" and the decoder "fastest". Samples not heard yet (`scp` without a destination, then the samples moved into Settings). |
| Real hardware, 0.13.0 self-test (2026-10-23) | `selftest --speech` on VM 222, every line passed: GPU 24.0 GB (11.1 GB in use with Ornith 1.5 9B loaded); the sandbox, its isolation and internet; **Landlock ABI 8** for people's code ("people's code stays in its folder"); web search; ComfyUI (torch 2.14.1+rocm7.14) and its folders; the speech service - the image built, torch 2.11.0+cpu on 10 threads; Røst v3: 3.4 s of speech in 20.4 s, a real-time factor of 5.95 (the first sentence 27 s with loading), with its Mic voice file (a float WAV, read by the new reader); Chatterbox Multilingual V3: 9.0 s of speech in 46.6 s (factor 5.18) for a 51-character Danish sentence - 9.0 s is exactly that sentence's token ceiling, so it most likely did not stop by itself (0.13.1: Resemble's repetition penalty 1.2 instead of Røst's 2.0, and the self-test says when a piece ran to the ceiling); Ornith answering at 60 tokens/s; embeddings. |
| Real hardware, chat 9 (2026-10-22) | Søren's troubleshooting export: FLUX.2 klein 4B made 3 pictures at 1024² in ~17 s (about 10 s to swap Ornith out and klein in, then ~0.45 s per step, 4 steps), Qwen-Image 2.1 edited one in 76 s (including the swap from klein), the LLM was back in 5-7 s each time. Ornith 1.5 9B (`ornith-1.5-9b-2`, probably Q8_0) generated 63-65 tokens/s, Qwen3.5 9B BF16 41 tokens/s; prompt processing ~2,000-3,250 tokens/s; a 1024² picture costs ~1,026 tokens for these vision models; the prompt cache reused 7,550 of 7,578 tokens between turns (none after a picture: the LLM was unloaded). |
| Tab rows | In Chromium: the old CSS gave `.tabs` 1px of vertical overflow (scrollHeight 42, clientHeight 41 - a scrollbar with Windows' classic scrollbars, reported by Søren); the inset-shadow rule gives none. |
| Compose | `docker compose config` (v2.40.3) with a `.env` written the way `install.sh` writes it: renders, and the admin password hash arrives intact. |
| llama.cpp | b11783 built from source (CPU): every command line the Hub builds parses; `--no-cache-idle-slots` (0.11.2) measured with that binary and a tiny model: a 3,997-token conversation whose state (1.02 MiB) was bigger than a 1 MiB RAM cache was dropped when a short request ran on the other slot (its next message reused 0 tokens and read 4,005), while with the flag - or with a cache big enough - all 3,997 were reused; the server's trace shows the idle slot saved to the RAM cache and cleared; the real supervisor started the real llama-server through the Hub's scheduler, runner and client with a tiny random model - load, streaming (the real chunk format), a killed server noticed and reloaded, unload. Embeddings (strings and `content` arrays) and `/v1/rerank` answer in the shapes the Hub expects. |
| ComfyUI | v0.42.3 on CPU with placeholder model files: all workflow graphs (Qwen-Image 2.1 generate, turbo, edit with 1 and 3 images, background removal, FLUX.2 klein generate and edit, ERNIE-Image and Turbo each with and without the prompt enhancer, Z-Image Turbo, Chroma1-HD with the default and a given negative, SDXL with clip skip 2 and tags and plain, and SDXL with every sampler and scheduler the settings offer: 41 in all) pass ComfyUI's own validation. Z-Image, Chroma and SDXL follow `image_z_image_turbo.json`, `image_chroma_text_to_image.json` and `image_sdxl_simple.json` (fetched 2026-10-22), including `TextGenerate`'s dynamic `sampling_mode.*` inputs. The ERNIE graphs follow the official templates (`Comfy-Org/workflow_templates`: `image_ernie_image.json`, `image_ernie_image_turbo.json`, fetched 2026-10-22). Tests: the graph shapes, and an agent turn with only Turbo downloaded (the enhancer's text comes back through `PreviewAny` into the tool result), the enhancer switched off, an edit with ERNIE chosen going to Qwen-Image. |
| MCP over stdio | A real stdio MCP server through the real sandbox daemon (`execd.py`): initialize, a server-to-client ping, tools, an 18 KB result. |
| Sound (0.13.0) | 18 tests (`tests/test_audio.py`) against a fake speech service and a fake ComfyUI: the catalog and downloads; no sound tools until a model is downloaded, then the voices with their labels read from float WAV samples (as the Røst files are); a Danish sentence spoken and saved (the request, the result, the card, the next free name); English going to Chatterbox with expressiveness and a voice memo converted by the sandbox's ffmpeg (the command, the copy removed, the voice kept for the owner); Røst Turbo's accent note and tokenizer folder; every error (unknown voice, missing or unreadable clip, bad extension, model not downloaded, the service's error, a 400, the service down); a song's graph; a song in a project under `audio/`, the Settings model, `save_as` as a sandbox path and as a folder; Stable Audio 3's prompt tags and FLAC; missing nodes; a person's sound and speech (the clip converted confined to her turn, ComfyUI's copy and history deleted, her voice not kept, a 300 s FLAC refused); a file too big for her device said so; both WAV readers (PCM of 8-32 bits, float 32/64, extensible, stereo, a LIST chunk, a stream's data size, refusals); the service's text pieces, limits, paths and voice cache; the compose service (no GPU, `models/` read-only, no CPU cap). The speech service itself (`server.py` in a venv with the pinned packages; torch 2.11.0 from PyPI on the CPU where the image has the CPU build of the same version) against random-weight models of both real architectures on 2 threads, 23 checks: MP3, WAV, FLAC and Opus that decode at the length it reports; the built-in voice; voice files as 16-bit WAV and as float WAV written by libsndfile (80-byte header) and by ffmpeg; a 48 kHz stereo clip resampled; a clip's voice kept only with `cache_voice`; a non-WAV clip refused; two paragraphs as pieces with progress; every refusal (empty, too long, format, a path outside the models, a model file as a voice, unknown family, a language it does not speak, a 0.5 s clip, Turbo's five seconds); a client that leaves (stopped after the piece in hand, free again); `/free`; a dropped model's memory handed back (4.4 GB loaded, 1.9 GB after `gc`, 0.84 GB after `malloc_trim`). ComfyUI v0.42.3 (CPU, placeholder files): all 26 sound graphs pass its validation (both ACE-Step models in each format, an instrumental waltz in an unknown language, Stable Audio 3 for every kind and format, 380 s); the Hub's client on real audio jobs (`EmptyAudio` → `SaveAudioAdvanced`) gets MP3, FLAC and Opus that decode at the right length, and `keep=False` deletes the file and the history entry. ffmpeg 6.1.1 with the clip command: an AAC voice memo (its first 30 s), an MP3 and a video's sound become 24 kHz mono WAV; a text file named `.mp3` and a playlist pointing to the web are refused. The updater's `ensure_folders` in a temporary root. In Chromium (19 checks): the models page's sound group with CPU/GPU tags, Settings → Assistant → Sound, the speech service in Settings → System, speech, a song and a horn in a chat with the card playing each, the tool rows, phone width. An independent review (a separate agent, the whole diff) found seven issues, all fixed: the Røst voice samples are 32-bit float WAVs (their sizes are exactly an 80-byte libsndfile header plus float samples), which Python's `wave` cannot read - every Danish request would have failed; a person's voice kept in the service's cache; `save_as` with a sandbox path landing in a nested folder; a long FLAC never reaching a person's device; a voice path read before its size was checked; the speech service missing from the status views; the docs. |
| People (0.11.0) | 19 tests (`tests/test_people.py`): adding, changing, signing out and removing a person (user names, case, passwords); home only (refused from outside before the password; a session taken outside is signed out) and away; every owner route 403 for a person, the sync API and the model API (`/v1`, `/api`) 401; settings reduced to three defaults with no secrets; a device turn (her name and no memory in the system prompt, no bash/memory/library tools, the photo seen, no chat/run/prompt rows left, folder and photo gone after `/done`, the queue log with her name and "Chat"); pictures, files and artifacts back to the device, an artifact changed in the next turn; bash only when allowed and Landlock present, sent confined; turns private between people and bad requests refused (paths out of the folder, `.hub/home`); the queue as a person sees it; swept turns and ledgers (a list written into the folder by her code is ignored); the run dropped and deleted files reported; ComfyUI's history entries and output/input pictures deleted for her and kept for the owner; a picture the owner or another turn stores meanwhile kept; only the skills switched on (list, prompt, the skill tool); at most four turns waiting for a device; the time each message was sent, from the device. 5 tests (`tests/test_sandbox_daemon.py`, real Landlock, skipped without it): a confined command reads and writes its folder only (not a sibling file, another turn's folder, `/root`; `TMPDIR` inside), `/dev/null`, `/dev/urandom`, process substitution and child processes work, `/dev/shm` and `/dev/pts` do not; background, `setsid` and `nohup` processes are killed when it ends, the owner's are not; bad confine settings refused. By hand on this build machine (kernel 6.18, ABI 7) as uid 1000: Landlock denies a confined process another process's `environ`, `mem` and `maps`, while `cmdline` stays readable (documented). The Hub's ComfyUI client against real ComfyUI v0.42.3 (CPU, a LoadImage → SaveImage graph): with `keep=False` the output file, the input file and the history entry are gone; with `keep=True` they stay. In Chromium (Playwright, 19 checks): Settings → People (add, list, the user name from the name), her chat at phone size (her sidebar, the thinking levels, no libraries), a title, a picture shown from the device's store, an artifact, an attachment and a file the assistant wrote in the Files panel, both chats and the picture after a reload with the Hub's copies gone, the owner seeing none of it, Export all chats → Import on a second browser with the picture, her session refused by the owner API, and not one refused request from her pages. An independent review (a separate agent, the whole diff) found five issues - `/dev/shm` writable, disabled skills readable, abandoned turns unbounded, the model API open to her session, `/proc/<pid>/cmdline` visible - the first four are fixed, the last is documented. |

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
4. **ComfyUI on the card**: FLUX.2 klein 4B generates and Qwen-Image 2.1
   edits on the card (chat 9, above), and the LLM loads again after it. Not
   seen yet: Qwen-Image 2.1 generating (and whether its int8 kernels or the
   bf16 fallback ran), the self-test's image line (`sudo bash
   /opt/slq-hub/update.sh compose exec hub python -m app.cli selftest
   --images`).
   ERNIE-Image is untried too: whether the prompt enhancer answers in English
   for English requests, how long enhancer plus the 16 GB model take on
   gfx1100, and RAM with three large files in ComfyUI's cache. New in 0.6.0
   and untried on the card: Z-Image Turbo (bf16), Chroma1-HD with the
   template's fp8 files (RDNA3 has no fp8 maths; ComfyUI should dequantize to
   bf16 - if it fails, the same repository has the bf16 `Chroma1-HD.safetensors`,
   17.8 GB, and `comfyanonymous/flux_text_encoders` has `t5xxl_fp16`), and any
   SDXL checkpoint.
5. **Embeddings and re-ranking**: text embeddings answer (1024 dimensions);
   the Qwen3-VL-Embedding chat format in `app/rag/library.py` and the
   re-ranker's score scale (log-odds or probability: both handled) wait for
   the first library upload.
6. **Nginx Proxy Manager**: hub.slq.dk works from Søren's phone (2026-10-22:
   signed in, the start page with his models). Not confirmed through it yet:
   SSE streaming of a long answer (`proxy_buffering off`), uploads
   (`client_max_body_size`).
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
13. **Civitai against the real site**: the API shapes come from Civitai's
    own source (github.com/civitai/civitai: `src/pages/api/v1/models/[id].ts`,
    `src/pages/api/download/models/[modelVersionId].ts`, read 2026-10-22);
    the real API could not be reached from where this was built. Not tried:
    a real lookup, which models need `CIVITAI_TOKEN`, a resumed download on
    its storage, models hidden by region.

14. **The phone chat page on real phones**: built and checked only in
    Chromium with phone emulation. On an iPhone: whether a link opened in
    Safari carries the key into the home-screen app (home-screen web apps
    keep their own storage; if not, the page asks and the whole link can be
    pasted), whether Save or a long press saves a picture, the keyboard over
    the composer. On Android: Chrome with `interactive-widget=resizes-content`.
    Real phone photos (HEIC on the iPhone, 48 MP) through the shrink step.
15. **Phone apps against the real Hub**: Enchanted's requests were replayed,
    not run from the app. Chatbox on phones fetches from its WebView unless
    its provider setting "improve network compatibility" is on (its own
    `docs/technical/provider-networking.md`), so it probably needs that
    switch for a plain-http LAN address - not tried.
16. **The loop guard and the new sampling with Ornith 1.5 9B on the card**:
    chat 7 (its export: the same two web_fetch calls and the same 354-token
    text 52 times, then a second loop, 60 steps, 15.5 minutes, prompts from
    4K to 62K tokens, thinking off, no sampling values sent) is the case. Not
    yet seen: whether presence penalty 1.5 alone stops it repeating, how the
    9B model answers when it is made to answer, and whether chat 7 can now
    be continued (its next message condenses 100K tokens on a 64K context).
    Ornith is a reasoning model (its card: it opens with a think block); with
    "Think" off in plain chats it runs without one.
17. **Hand-over on the card**: how long the switch from a small model
    (Ornith, Qwen3.6) to Qwen3.8 27B takes within a turn, and whether Qwen3.8
    makes good use of a smaller model's steps. With a 9B model as the chat
    default, a hand-over also means the next turn starts on the small model
    again (the hand-over is per turn).
18. **Search on Add from Hugging Face against the real site**: built from the
    documented `/api/models` parameters (`search`, `filter=gguf`,
    `sort=downloads`, `direction=-1`, `limit`) and checked against a fake;
    this build environment could not reach huggingface.co.
19. **Keys away from home through the real proxy**: what client address
    Nginx Proxy Manager passes on. From outside it should be the phone's
    public address. A phone at home that uses hub.slq.dk goes out and back
    in through the router (hairpin NAT), so the Hub may see the router's LAN
    address (then even a home-only key works there) or the public address
    (then it needs "At home and away") - the refusal now says which address
    the Hub saw. Not tried: the page installed from hub.slq.dk on Android,
    and a picture request through the proxy (it is not streamed, so
    `proxy_read_timeout` in the Advanced block must be long).
20. **The size of the step records**: each model step now stores a small
    `sent` record, and every turn a `run_prompts` row (the prompt text itself
    once per distinct text). Expected to stay small; worth a look at
    `data/hub.db` after some weeks.
21. **Landlock in the real sandbox container**: seen on VM 222 (the 0.13.0
    self-test: ABI 8, and the probe's confined command could not read the
    file next to its folder). Not yet seen: a person's real chat with code.
22. **ComfyUI's folders in the Hub**: the update adds two mounts
    (`HUB_ROOT/comfy/input` and `output` at `/comfy`); the self-test's
    "comfyui folders" line checks they are there and writable by uid 1000.
    Not seen: the deletion after a real picture on the card.
23. **People on real phones and through the proxy**: built and checked in
    Chromium only. Not tried: IndexedDB on an iPhone (Safari and the
    home-screen app; storage kept or asked to persist), a chat with big
    attachments sent with every message over mobile data and through Nginx
    Proxy Manager (`client_max_body_size`; the Hub takes 120 MB per message),
    the address NPM passes on for a home-only person at home through
    hub.slq.dk (hairpin NAT, as in 19), a reload or a lost connection in the
    middle of an answer on a phone (the answer should be picked up again).
24. **People with the real models**: whether Qwen3.8 uses the temporary
    folder sensibly (attachments in `.hub/attachments/`), and how a long
    chat behaves when its oldest messages are left out (no summary for
    people's chats).
25. **The time stamp on user messages with the real models** (0.11.1):
    chat 11 (Qwen3.8 27B) got `[Fri 23 Oct 2026 09:55]` and wrote no stamp
    of its own; not yet seen with the 9B models or a question that needs the
    time.
26. **The RAM prompt cache for Qwen3.8 27B**: a 30K-token conversation's
    state was 5,635 MiB (chat 11), above the 4,096 MiB `cache_ram_mib`. With
    0.11.2 an idle conversation stays on the card, but switching to another
    chat and back still needs the RAM cache. A bigger one (`--cache-ram
    12288` in the model's extra flags) only after `free -h` with the model
    loaded shows the room.
27. **The files card on the real machine**: the snapshot before and after a
    turn in a big project folder (time, and the 20,000-file limit), a PDF in
    Safari and Chrome, an MP3 from the sandbox playing in Safari on an iPhone
    (the player asks for byte ranges), and whether Qwen3.8 now names the
    file and cleans up scratch files as the system prompt asks.
28. **The speech image**: built on VM 222 by the 0.13.0 update and running
    (torch 2.11.0+cpu). Not seen: Røst v3 Turbo's files downloaded and loaded.
29. **The real voice models**: Røst v3 and Chatterbox Multilingual V3
    downloaded and spoke in the 0.13.0 self-test (Røst with its Mic file, a
    float WAV). Not yet heard: how they sound through this service's
    sentence splitting and pauses, and whether Mic and Nic come out as "male
    voice" and "female voice" from their pitch (the tool description shows
    it).
30. **The 0.13.3 "Faster" (8-bit MLP layers, attention full)**: not measured
    on the VM or heard. Here (random weights, 2 threads) it made T3 1.4x
    quicker for Turbo and 1.5x for the 500M models, against 1.9x and 1.8x with
    every layer in 8 bits; whether it avoids the skipped word and slurred
    first words is for the ear (the idea: those are alignment errors, and
    alignment lives in the attention layers). Turbo's decoder on Røst v3's
    tokens was only heard together with 8-bit weights. T3 is as fast on 5
    threads as on 10, so overlapping the next sentence's T3 with this
    sentence's decoder could save 15-20% on longer texts (estimated; not
    built - the two would share torch's global random generator, which
    breaks the seed's repeatability). Faster than that with full quality
    means the card: a speech residency would swap the chat model out per
    request (or sit beside a small one, which the scheduler cannot do yet).
31. **Memory with sound**: two voice models in the speech container (about
    4 GB each, a 12 GB limit) while ComfyUI reads ACE-Step XL's 20 GB of files
    and the LLM's file is mapped, in a 40 GB VM. Watch `free -h` and the
    kernel log for OOM kills the first time both run.
32. **ACE-Step 1.5 and Stable Audio 3 on the card**: VRAM (XL Turbo with its
    4B planner about 20 GB), the time per song and per effect, whether the
    planner runs well under ROCm, how Danish lyrics sound. ACE-Step 1.5 Turbo
    is the lighter choice if XL does not fit or is slow.
33. **A person's voice clip in the real sandbox**: ffmpeg confined by
    Landlock to her turn folder - tried with a fake sandbox and with the real
    ffmpeg separately, not together.
34. **Chinese, Japanese, Hebrew and Russian in Chatterbox Multilingual**:
    their optional text helpers are not in the image (the Chinese one would
    download a table from Hugging Face; the service runs offline), so these
    languages are spoken from the plain text and probably worse - Chinese
    likely badly. Danish, English and the other European languages need none.
35. **Chatterbox stopping by itself**: with Resemble's repetition penalty
    (0.13.1) it stopped at 79 tokens in both full-weight runs, but ran to the
    ceiling with 8-bit weights (one run; 0.13.2's 8-bit weights have a scale
    per channel, closer to the full ones). Watch the self-test's WARN lines
    and the "did not stop by itself" note in results.

## Where things are

| Path | What |
|---|---|
| `backend/app/gpu/scheduler.py` | The queue. Everything that uses the card goes through `acquire()`. |
| `backend/app/gpu/backend.py`, `runner.py`, `aux.py` | Loading/unloading on the card; the llama-server supervisor client; CPU helper servers. |
| `backend/app/api/` | Ollama, OpenAI, Anthropic formats. |
| `backend/app/catalog.py`, `hf.py`, `downloads.py` | The model catalog (bundled + `data/models.custom.json` + overrides), Hugging Face lookups for Add from Hugging Face, the in-app download queue. |
| `backend/app/agent/loop.py` | The agent loop (`Engine`) and turns (`Agent`). |
| `backend/app/chats/export.py` | A chat as Markdown, or as the troubleshooting ZIP (`GET /hub/api/chats/{id}/export?format=md|zip`). |
| `backend/app/agent/tools/` | The tools; `meta_tools.py` has `task` and `escalate`; `mcp.py` the MCP client. |
| `backend/app/projects/` | Projects, folders, the sync store and the `/api/sync` protocol. |
| `backend/app/images/` | ComfyUI workflows, the image pipeline (by family), `sources.py` (SDXL checkpoints from Civitai and Hugging Face links). |
| `backend/app/audio/`, `backend/app/agent/tools/audio.py` | Sound: the ACE-Step and Stable Audio 3 graphs (`workflows.py`), the pipeline (models, routing, ComfyUI through the queue, speech), the speech service's client and voice labels (`speech.py`), the WAV reader (`wavfile.py`); the three tools. |
| `services/speech/server.py` | The speech service (Chatterbox on the CPU): text pieces, voices, the two model families, NDJSON over aiohttp. |
| `backend/app/rag/` | Libraries: extraction, indexing, search, the `library_search` tool. |
| `backend/app/cli.py` | `selftest`, `set-password`, `create-key`, `keys`. |
| `frontend/src/` | The UI: `views/` (chat, project, libraries, settings), `components/`, `lib/turns.ts` (the turn view model for history and live runs). |
| `frontend/src/chat/`, `frontend/chat.html` | The phone chat page (`/chat`): its own entry, on the model API with a key (`hub.ts`), chats in IndexedDB on the device (`store.ts`). |
| `backend/app/agent/device.py`, `backend/app/ui/people_routes.py` | People's chats (device turns: the temporary folder, the result, what is deleted) and the People settings API. `backend/app/gate.py` has `MEMBER_ROUTES`. |
| `frontend/src/person/` | The chat a person gets (`PersonApp.tsx`), their IndexedDB store (`store.ts`), the device-turn flow (`turns.ts`). |
| `services/sandbox/confine.py` | Landlock confinement for people's commands (used by `execd.py`). |
| `services/` | The llm supervisor, the ComfyUI image, the sandbox daemon, the speech service. |

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

- One owner. People the owner adds use the chat with their chats on their
  own device: no sync between devices (Export/Import moves them), every
  message carries the chat's files, no projects, libraries or memory. API
  keys for apps.
- A person's code shares the sandbox container with the owner's commands:
  it cannot read their files, environment or memory, but it can see their
  command lines while they run. No shared memory (`/dev/shm`) for it, so
  Python multiprocessing does not work there.
- Tool calls in a turn run one after another, not in parallel.
- Approvals wait up to ten minutes; the turn keeps its lease meanwhile, so a
  request for another model waits too.
- The run's live events are kept in memory for an hour; the stored messages
  are permanent.
- No custom ComfyUI workflows yet; no video. Speech, music and sound effects
  are one file per call (no voice conversion, no editing of a song; ffmpeg in
  bash mixes and cuts).
