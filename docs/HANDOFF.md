# Handoff

The state of SLQ Hub for whoever works on it next (a person or Claude).
Read `docs/DESIGN.md` for the decisions; this file says what exists, what
has been checked, and - honestly - what has not.

## State (0.20.0, 2026-10-25)

Everything in the design is built: the install path, the Hub core (scheduler,
llama-server supervisor, the Ollama/OpenAI/Anthropic APIs), the agent harness
with all its tools, projects with SLQ Sync, images through ComfyUI, libraries
(multimodal RAG), MCP, cloud escalation, the web UI (responsive, PWA) and the
command line self-test. Since 0.8.0 there is also a chat page for phones
(`/chat`) on the model API, for the rest of the household. A chat that behaves
strangely can be exported from its ⋯ menu ("Export for troubleshooting": a ZIP
with messages, runs, live events, the next turn's context, the queue and the
model server's log, since 0.10.0 what each model step was sent and the system
prompts used, and since 0.13.5 the files its turns made) - ask for that first.
A small model that gets stuck hands the turn over to a stronger one on the
server (Settings → Assistant). A key can be set to work away from home, so the
phone page works at `https://hub.slq.dk/chat`. Since 0.11.0 the owner can add
**people** (Settings → People): the same chat with their own login, at home or
also away, with their chats kept in their own browser - the Hub answers each
message in a temporary folder and keeps nothing of it (DESIGN §6 "People's
chats", §11). Code for people runs only where the sandbox has Landlock. Since
0.13.0 the assistant makes **sound** (DESIGN §8 "Sound"): speech on the CPU in
a new `speech` container (Røst v3 for Danish, Chatterbox Multilingual V3 for
23 languages), so the chat model stays on the card; music (ACE-Step 1.5) and
sound effects (Stable Audio 3) on the card through ComfyUI and the queue. No
sound model is downloaded by default (Settings → Models → Sound, music and
speech). On VM 222 the speech container runs Røst v3, Røst v3 Turbo and
Chatterbox. Søren's two listening tests (below): Røst v3 Turbo at full weights
sounds best - about 4x slower than real time - and 8-bit weights said wrong
words even in the MLP layers only, so 0.13.4 removed the speed setting (full
weights only) and puts Røst v3 Turbo first for Danish. Chatterbox speaks
Danish with an English accent: other languages only. Music and sound effects
first ran on the card in chat 15 (10 s each, below); the assistant mixed a
separately made "solo" over a band, so 0.13.5 tells it that one music call
makes the whole piece. Since 0.15.0 the **internet can ask first** (Settings
→ Assistant → Web → Internet for the assistant; DESIGN §6): web search, each
site and code that goes online wait for Allow once / Always / Deny in the
chat, and other code runs without TCP connections (Landlock). `web_fetch`
connects to the address it checked (no DNS rebinding), and the model
containers (llm, comfyui, speech) sit on their own `inner` network that the
VM's firewall keeps from reaching anything outside it (DESIGN §2). After the
0.15.0 update, run `sudo bash /opt/slq-hub/update.sh firewall` once (the old
updater does the update and does not know the new rules). 0.15.0 also fixes an
older bug found by its review: a hand-over in a person's chat gave the
stronger model the owner's system prompt, memory index included. Since
0.15.1 the sandbox has the **.NET 10 SDK** (C#; DESIGN §6 "C# in the
sandbox"): build output goes to `.hub/dotnet`. Since 0.16.0 Windows Forms/WPF
build offline (the Windows packs are in the image) and the assistant **sees
Windows programs** with `windows_app`: Wine on a virtual screen in the
sandbox, screenshots, clicks by position or by text, typing (DESIGN §6
"Windows programs on a virtual screen"). 0.16.0 also brings "Allow in this
chat" for every question, the card given up while a question waits (DESIGN
§3), HTML/React artifacts that load nothing from the web while the internet
asks first, an updater that lets its new version finish the update (DESIGN
§12), and fixes for Add from Hugging Face (MTP/noMTP twins with the same
name, a vision file not named mmproj, a parameter count from an MTP head).
0.16.1 fixes typing into Windows programs, found while checking 0.16.0's
typing under real Wine 9.0 before it reached the VM: typing through X lost
the first key, turned Æ Ø Å into U+FFFD (WPF: every letter) and scrambled
text for a busy program. HubKeys, a small Windows helper built into the Wine
prefix, now types with `SendInput`, behind a barrier that keeps a text from
overtaking an earlier click (DESIGN §6 "Typing"); the self-test's "windows
programs" line types "Søren: ÆØÅ æøå" and passes only if it arrives exactly.
0.16.2 fixes what broke chat 21 (below): a `write_file` the end of the context
cut off made llama-server refuse the next request, and so the chat. Such a
call is now explained to the model, a cut-off `write_file` keeps what it had
written, arguments that are not valid JSON never go back to the model server
(chat 21 can go on), `write_file` can append, and the system prompt says to
write long files in parts (DESIGN §6 "Cut-off calls"). 0.17.0 lets the owner
order the chat and picture models (Settings → Models, arrows); the chat's
menus and the phone page follow it (DESIGN §4 "Order"). 0.18.0 brings
**voices and remakes** (DESIGN §8 "Voices and remakes"): `music_generate`
takes a reference clip whose sound the song takes on, `music_remake`
re-performs a song with the sound of a prompt and/or a clip (a live recording's
audience and room), `audio_separate` gives vocals and accompaniment (Mel-Band
RoFormer), and `voice_convert` puts the voice of a 5-30 s clip on speech or
singing (Seed-VC; with music the vocals are separated, converted and mixed
back) - through four nodes of the Hub's own in the ComfyUI image
(`services/comfyui/nodes/slq_audio`). Two new catalog entries, not downloaded
by default. All of it ran here only on a CPU ComfyUI with random weights:
nobody has heard it yet (items 45-48). 0.19.0 brings **video** (DESIGN §8
"Video"): `video_generate` makes an MP4 from a description, from a picture,
or from a first to a last picture, with Wan 2.2 through ComfyUI's own nodes
and the queue - the 5B (both, 720p, 24 fps) or the 14B pair (picture or
description, 480p, 16 fps, 4-step LoRAs or full quality). Three catalog
entries under Settings → Models → Video models, none downloaded by default,
and Settings → Assistant → Video to choose. The graphs ran here only on a CPU
ComfyUI with stand-ins: nobody has seen a real video from it yet (items
49-51). The Hugging Face dialog now says when a search matches a model in the
Hub's own list (Søren searched "Seed-VC" there). 0.20.0 adds **Help**
(DESIGN §10 "Help"): every kind of request, by section, with examples that
Try puts in a new chat, and for each whether it works on this Hub now and
what is missing; 0.20.1 makes the chosen section (and the chosen tab in
Settings) easy to see on a phone. 0.16.0 runs on VM 222 (chat 21's export
came from it); 0.16.1, 0.16.2, 0.17.0, 0.18.0, 0.19.0, 0.20.0 and 0.20.1 are
not there yet.

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
| Image models from links | 5 tests (`tests/test_images_models.py`) against a fake Hugging Face (real safetensors headers, Range requests) and a fake Civitai (model and version API, a login-only download, the redirect to its storage): link parsing, the checkpoint check (SDXL, v-prediction, no text encoders, no VAE, refiner, SD 1.5, FLUX, LoRA), presets and their limits, lookup and add from both sites, the 401 that names `CIVITAI_TOKEN`, the token kept from the storage host, settings changes, removal (not while it is the Settings model), a chat with its own SDXL model (the graph, clip skip, tags, the SDXL size) and the tool description it gets. The dialog clicked through in Chromium at 1440 and 390 px. The three download tests, deselected in the 0.16.0 session for lack of disk (the guard needs ~12 GB), passed again on 2026-10-24 with 31 GB free, with the other two. |
| Phone chat page | 0.10.1: 20 more checks on iPhone SE and iPhone 13 (the Picture chip with its way back and the start text, four thinking levels for Qwen3.8 with an old "Think on" becoming Think, Think hard sent as `reasoning_effort: xhigh`, the on/off button for Qwen3.6, the level remembered per choice when switching back). Before: 34 checks in Chromium (Playwright) with iPhone 13 and iPhone SE (dark) emulation against the dev server: Settings (the page's address, Only the home network, Copy the phone link); connecting (a wrong paste, an unknown key, the link - the key is kept and leaves the address); a streamed answer (Bearer key, thinking off by default, the system prompt, the default model, the title); Think on; the history sent; a photo (shrunk to JPEG, sent as `image_url`); a picture (portrait size, kept as data); the viewer's Save link; Stop and Try again; two chats kept over a reload, with their photo and picture; delete; copy; away from home (a 403 from the LAN guard); a key switched off (said in the chat, then the key screen after a reload); the composer at 320 px. The requests Enchanted (`HEAD /`, Bearer, `/api/tags`, `/api/chat`) and Reins (key in the path, `GET` of the base address) make, replayed with curl; read from their sources (Enchanted 2026-07 and its OllamaKit fork, Reins 2026-10). |
| Sampling for added models | 2 tests (`tests/test_models_sampling.py`) and one more check in the Hugging Face test: the architecture read from a GGUF header (skipping a number and a string list before it; not a GGUF; missing), Qwen3-family values for a model added before 0.9.1 (from its file), with and without thinking; none for a llama model; an entry's own values kept; `--temp` and `--presence-penalty=` among the extra flags leave those values out. |
| Chat export | 7 tests (`tests/test_export.py`), one of them the model server's log without the vision models' position warnings: Markdown (thinking, tool calls and results, one section per turn, the turn line), the ZIP (every part; full tool arguments; the model card and safe settings - a cloud API key set in Settings appears nowhere in it; the picture as a file and linked in the transcript; context.json with the system prompt, history, tools and request, the picture left out; the event log with streamed pieces joined; the queue job of the run), 404/400/401, joining events, file names. Both menu items and the phone page's Export this chat clicked through in Chromium (downloads named after the chat, unzipped, the phone file has no whole key). |
| Settings 0.10.0 | In Chromium against the dev server (12 checks): the hand-over setting (Qwen3.8 27B by default), a model's Settings dialog (sampling prefilled from the card, thinking "Always think first" and a new temperature saved, "Use the model's own sampling" back to the card with thinking kept, the composer of a new chat following the model's thinking), Add from Hugging Face searching "Ornith 1.5 9B" (two repositories listed, a click looks one up, Q8_0 preselected for 9B, BF16 "twice the size of Q8_0"), and a live hand-over (Qwen3.6 35B-A3B repeats a web search, refused twice, Qwen3.8 27B answers; the notice live and after a reload; the footer names both models). |
| Keys away from home | In Chromium against the dev server started as if behind a proxy (`--proxy 127.0.0.1 --public-url https://hub.example.test --lan-host 192.168.1.10`; uvicorn without its own proxy handling, as in production) (16 checks): the Apps page and dialog text, "Where it works" hiding "Only the home network" when away, the phone link through the public address and the home-only link, the list's per-key choice, switching a home-network key to away removing that limit (and back), the phone page from outside (`X-Forwarded-For: 203.0.113.9`) with a home key ("not through ...", "The Hub sees this phone at 203.0.113.9"), Try again after the key was switched (the key from the link was kept), an answer from outside, an away key connecting straight away. |
| Real hardware, chat 15 (2026-10-24) | Søren's export, 0.13.4: "a 10-second intro to SLQ-HUB with a wild guitar solo of high quality". Ornith 1.5 9B called `sound_generate` (Stable Audio 3 Medium, kind instrument, a "wild electric guitar solo", 10 s) and `music_generate` (ACE-Step 1.5 XL Turbo, a band at 128 BPM in E minor, instrumental, 10 s) and mixed them with ffmpeg - two pieces with their own key, tempo and beat. The first music and sound on the card: Stable Audio 10 s in 8.5 s (6.5 s to the first step: the LLM out, the model in, the encoder; 8 steps 0.4 s; decode and save 1.6 s; 44.1 kHz); ACE-Step 10 s in 18.8 s (9.8 s to load after Stable Audio, the planner about 8 s, 8 steps 0.6 s; 48 kHz). Ornith back in about 9 s (6.7K tokens, no cache). Three ffmpeg calls failed on `-map "[0]"` (an unlabelled filter output; the error sat under 2 KB of banner and the graph JSON that ComfyUI writes into each MP3); the hand-over to Qwen3.8 27B worked - its first step 24 s (7.4 s to load, 10.8K tokens at about 1,000/s) - and it labelled the output. Then it deleted both parts, right before offering to change the mix. VRAM is not in the export, and the MP3 neither (the export had only pictures and attachments - 0.13.5 adds the turns' files). How it sounded: not reported. Hence 0.13.5. |
| Listening test, 0.13.3 (2026-10-23/24) | Søren played the 0.13.3 samples in Settings: Røst v3 Turbo with only its MLP layers in 8 bits ("Faster") and with every layer in 8 bits ("Fastest") both said wrong words; every Chatterbox sample still sounds English. Hence 0.13.4: no 8-bit weights and no borrowed decoder - every model at full weights with its own decoder - and Røst v3 Turbo first for Danish. Røst v3's 0.13.3 Faster/Fastest samples got no comment. |
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
| Real hardware, chat 21 (2026-10-24) | Søren's export, 0.16.0: recreate an old game (Atomic Bomberman) from its .exe, with Qwen3.8 27B (Adv. NL) - JonathanColetti's Uncensored noMTP-Q5_K_M with its F16 vision file, a 65,536-token pool on two slots (unified KV). Step 14 of the turn: 48,596 tokens in (7,281 cached, after the turn's own trimming), about 2,800 words of thinking, then the whole game as one `write_file`: 16,940 tokens at 22.7 t/s (797 s) until the context was full (llama-server: `n_tokens = 65535, truncated = 1`), 31,400 characters into the file's content. The Hub answered that step ("not valid JSON") and compacted - its 1,239-token summary ran on slot 1, which first found the shared KV pool full and purged slot 0 - and at step 15 llama-server refused the request with a 500, `Failed to parse tool call arguments as JSON ... column 31400`: the cut-off call was still in the history. The run failed, and the next message in the chat would have failed the same way (the export's context.json still carries the call). The Hub's prompt estimate was 7% low (45,159 for 48,596), so it asked for 20,377 tokens where 16,940 fitted - not the cause: llama-server stops at the context's end either way. Hence 0.16.2. |
| Cut-off calls (0.16.2) | 18 tests (`tests/test_cut_off.py`) against the fake llama-server, which now refuses a history with a tool call whose arguments are not a JSON object, as the real one did in chat 21: chat 21's flow (a `write_file` cut off with 48,596 + 16,940 tokens of the context used keeps its first characters, the model appends the rest and the file is whole; the next request carries a readable stand-in; the result names the context's end and its numbers, shows the last characters in a fence and says to append; the chat's next message goes through, with the stored call readable); a bash call cut off at the limit for one answer does not run; arguments that are not JSON without a cut; `write_file` with append (to a missing file, to a folder); the prompt and the tool's text; `valid_arguments`; what a cut-off write keeps (an escape or half a `\u` at the cut, a split surrogate pair, append, a cut path, other keys); a stored 0.16.0 chat sent back readable. With the old history building, four of them fail on the fake's 500. Not tried against the real llama-server (no model file here) or on the VM. |
| Model order (0.17.0) | 4 tests (`tests/test_models_order.py`): the order through the API (the catalog's first: bundled, then added), `/hub/api/models`, `/v1/models` and `/api/tags` among the downloaded models, after a restart (the overrides file), a model added later last, a partial order (the rest after, as before), a removed model leaving the order, renaming and model settings keeping it; picture models too, apart from the language models; refusals (an unknown id, one twice, the aux group, another group's model, a bad body - nothing stored); a person sees the owner's order and gets 403 changing it. In Chromium 153 (`@sparticuz/chromium` from npm, with playwright-core: Playwright's own download is blocked here) against the dev server (9 checks): the arrows and the note, the first's up and the last's down disabled, Move up putting the second first, the Hub's stored order, a new chat's model menu in that order, Move down at 390 px, no sideways scroll, no page errors. Not on the VM. |
| Voices and remakes (0.18.0), the Hub's side | 11 tests (`tests/test_voices.py`) against the fake ComfyUI (now with several saving nodes named as ComfyUI names them, the new nodes in `/object_info`, SLQSeedVC's text output and a failing node) and a fake sandbox ffmpeg/ffprobe: a song with a reference clip (the FLAC conversion command, the upload, `LoadAudio` → `VAEEncodeAudio` → `SLQAceStepReference` with the planner still on, the clip's copy gone from ComfyUI's input folder in the owner's chat, the "only the first 0:30" note, a missing file); a remake (song and clip uploaded, the cover source and timbre, `SLQAceStepEncode` with unknown metadata left out and known metadata in, denoise 1 − keep, no empty latent, the 5-minute cut, "no lyrics given"); stems (the RoFormer's model and config paths inside the models folder, both saves, one number for both names when taken, vocals only as FLAC under a given name, a wrong `keep`); singing in another voice (two separations, every SLQSeedVC input - the catalog's files as paths inside the models folder, steps 40 - `AudioMerge` onto the accompaniment, the mix and the vocals saved, the octave the node reports in the result); speech (the 22 kHz model, no RMVPE, no separation); refusals (music without the separation model before anything is queued, a ComfyUI image without the Hub's nodes named as such, a node's own error reaching the model); a person's conversion confined, with nothing left in ComfyUI; the FLAC length reader (and a FLAC from ffmpeg); the graph builders' clamps; the nodes' names against the graphs; the catalog, the nodes' defaults and the Dockerfile's pins agreeing. One real bug found on the way: the first draft passed a loader-style name (`MelBandRoformer.ckpt`) where the Hub's nodes take a path inside the models folder (`separation/MelBandRoformer.ckpt`). In Chromium against the dev server: Settings → Models lists both new models with the new note, at 1440 and 390 px, no sideways scroll, no page errors. |
| Voices and remakes (0.18.0), the nodes | In this build container (no GPU, no Docker, no Hugging Face): ComfyUI v0.42.3 on CPU with torch 2.14.1 and torchvision 0.29.1 from conda-forge (the VM's versions), ComfyUI's requirements and the image's new pins from PyPI (transformers 5.20.2, librosa 1.0.0), Seed-VC at `51383ef`, MSST at `6fdad13`. Random-weight stand-ins built from the real code: a small ACE-Step 1.5 DiT (the detection reads its sizes; the encoder must stay 2048 wide), a full-size ACE 1.5 VAE (337.4 MB in fp16, the real file's size), a small RoFormer, small Seed-VC models for both configs, small whisper and BigVGAN, a full RMVPE. Queued through the HTTP API exactly as `app.audio.workflows` builds them (the text encoder replaced by a test node of the same output shapes): a song with a reference clip (6.02 s out), the timbre without planner codes refused with its reason, remakes with keep 0 and a clip and with keep 0.5, stems (both 6.0 s), singing in another voice with separation, mix and vocals (6.03 s; the node's JSON in the history), speech (22.05 kHz Opus, 4.0 s), a 75-s song (four chunks, crossfaded, 75.05 s with `pitch` 3). What reaches ACE-Step's forward, through real sampling: timbre only - the clip (750 of 900 frames) and the planner's codes, `is_covers` unset; cover with a clip - the song's codes (equal to the tokenizer's own in float32), `is_covers`, the clip; cover alone - ComfyUI's silence reference. `SLQAceStepEncode` with the real ACE15 tokenizer: N/A for an unknown bpm, key and time signature, the values when known, the planner off. The Kaldi filterbank port equal to TorchAudio 2.11's own (0 difference, three lengths); the overlap-add giving back its input for an identity model (five lengths, overlap 2-8); the octave choice, the F0 of 130 s in 60-s pieces (exact frame count), the voiced-parts trimming (10 s half silence → 5.9 s). MSST's RoFormer against Kim's own code: the same 684 tensors and shapes, outputs within 2·10⁻⁴. The real configs build: Kim's RoFormer 228.2M parameters (his 913 MB file), Seed-VC singing 196.4M, speech 103.0M. Found and fixed on the way: ComfyUI's bf16 FSQ index (wrong codes, out of range), a random test VAE with uninitialised parameters (NaN latents - the node now refuses a non-finite latent with its reason), chunks without overlap losing their faded edges (overlap is now at least 2). Not with real weights, not on ROCm. |
| Video (0.19.0), the Hub's side | 11 tests (`tests/test_video.py`) against the fake ComfyUI (now with the Wan and video nodes in `/object_info`, SaveVideo saving and reporting as ComfyUI v0.42.3 does - its file among "images", marked animated, under `video/` with ComfyUI's `_00001_.mp4` names - and a failing node of any name): the three catalog entries (abilities, every file under `comfy/` with its repository path, the templates' totals, the 14B pair's shared files queued once); no tool and no system prompt section until a model is downloaded, then the models with what they make, their default size and frame rate, and which one is used; the 5B from a description (every node input against the template, Wan's negative prompt first, the MP4 saved and named from the prompt, a free name the next time, 30 s capped at 10 and said) and from a picture (uploaded as PNG, its portrait shape kept at 768x1152, deleted from ComfyUI's input folder; not a picture, a missing one); the 14B picture pair fast (the LoRAs on both experts, shift 5, high noise 0-2 with leftover noise, low noise 2-end), full (no LoRAs, 20 steps split at 10, cfg 3.5) and first to last (both pictures, shift 8 and cfg 4); the 14B text pair chosen in Settings, portrait at 480x832, in a project under `video/`, `hd`, a named model, `save_as`; what the downloaded models cannot do; a person's video with her picture: the file to her device, ComfyUI's history, output and input gone, and a file too big for her device said so; errors (a missing node, no video saved, a node's own error, a bad quality); frames and sizes; the two experts as one progress bar; the Hugging Face dialog naming the Hub's own models (also when Hugging Face cannot be reached). In Chromium against the dev server ("video": its fake ComfyUI hands out ffmpeg's test pattern as a real MP4), at 1440 and 390 px: the video plays in the files card (5 s, its metadata loaded), named from the prompt, with the tool row; Settings → Models lists the three with the note, the GPU tag and Settings' sizes; Assistant → Video has both choices; the Hugging Face dialog's note for "Seed-VC" without the "No GGUF model" error; no sideways scroll, no page errors. |
| Video (0.19.0), the graphs | ComfyUI v0.42.3 on CPU (as for 0.18.0) with stand-ins built from ComfyUI's own classes: one-block Wan DiTs (dim 128; 48→48 channels for the 5B, 36→16 and 16→16 for the 14B pairs - detected as WAN22 and WAN21 as the real files are), rank-4 LoRAs on their attention (applied, no unmatched keys), a random Wan 2.1 VAE (dim 32) and a full-size random Wan 2.2 VAE (1,409.4 MB in bf16 - the real file's size), a test node for umt5-xxl's conditioning ([1, 12, 4096]). Each graph exactly as `app.video.workflows` builds it, with the real text encoder name: accepted at `/prompt`, stopping at `CLIPLoader` on the placeholder file. With the stand-ins: 5B text and picture (64x64, 25 frames at 24 fps), 14B picture fast and full, first to last, text fast and full (64x48, 17 frames at 16 fps) - each an MP4 with exactly those frames, size and rate, H.264 High, yuv420p, no prompt or workflow in its metadata. The Hub's own client (`Comfy.run_job`, kind video) on a 14B job: websocket progress per expert (1/2, 2/2 on node 14, then on node 15), the MP4's bytes, and nothing left in ComfyUI's output folder after `keep=False`. The catalog's twelve files checked on Hugging Face's pages (names and sizes). Not with real weights, not on ROCm. |
| Help (0.20.0) | 4 tests (`tests/test_help.py`): what `/hub/api/help` tells the owner (code, memory, projects; Qwen-Image's three abilities; no sound or video until downloaded, then the 5B's two abilities, speech and music; Windows when the sandbox says so, asked again; the internet asking; the hand-over model, and none when unset) and a person (no code unless allowed - and then only with Landlock in the sandbox; no memory, projects, libraries, Windows or MCP); every tool named in some help item and no item naming a tool that does not exist; every Need in the help a field of the endpoint with its text. In Chromium against the dev server (41 checks), at 1440 and 390 px: the welcome link and the sidebar's Help (marked as the page); the eight sections; Make a picture and video from a description Ready, first-to-last "Needs Wan 2.2 14B picture to video" with where to get it and its Try off, Another voice "Needs Seed-VC"; the search ("karaoke" finds Vocals and music apart; nothing found said so); Open Settings to Settings, Models; Try putting the example in a new chat's message box, and an example with a file saying what to attach; a person's help (her eight sections with Skills for Memory and skills, her chats on the device and no export, no libraries or projects, code not allowed - ask the owner - without Open Settings, Try into a new chat); no sideways scroll, no page errors. |
| The chosen section and tab (0.20.1) | Søren's phone screenshot (Chrome on Android, `hub.slq.dk/help/documents`): the tab strip's grey scrollbar thumb lay over the chosen tab's 2 px underline, and the chosen tab's text was only slightly darker. In Chromium against the dev server: the help's sections as chips - one chosen, filled (background and text both differ from the others), every chip inside the 390 px screen, the section's name heading it, and the rest of the help checks as before (47 at 1440 and 390 px, a person's included); dark mode looked at (the chosen chip inverted). Settings at `/settings/system`, `cloud` and `models`, at 1440 and 390 px: the chosen tab in view (System and Cloud start off-screen on a phone and are scrolled to), tinted, a 3 px line, the strip's `scrollbar-width: none`. Not tried on an Android phone: whether Chrome there hides the strip's scrollbar as the CSS asks (`scrollbar-width: none` and `::-webkit-scrollbar { display: none }`). |
| Real hardware, chat 17 (2026-10-24) | Søren's phone screenshot (Chrome on Android, `hub.slq.dk/c/17`): "a radio tech jingle for SLQ HUB, about 10 seconds" - one `music_generate` call (no mixing), Qwen3.8 27B, 1 min 9 s; the answer and its stats line, but no files card. The same flow against the dev server with the current build in Chromium as a Pixel 7 and an iPhone 13 shows the card, live and after a reload, also with the page offline for 4 s, frozen (Android's background tab) until the turn ended, reloaded mid-turn, or another page opened meanwhile. The screenshot matches a build from before 0.12.0 exactly (the header's Files button since 0.5.0, the card since 0.12.0; an unknown tool's row shows its first argument), so most likely a tab loaded before yesterday's updates and never reloaded - not confirmed (ask whether a pull-to-refresh shows it). Found on the way: offline as the turn ended, the page never showed the answer (fixed in 0.13.6). |
| Internet for the assistant, pinned fetch, the inner network (0.15.0) | 12 tests (`tests/test_internet.py`): Allowed is as before (no question, the old prompt text); asking first, a search asks with its query - allow once, deny (the model hears it was declined), always (kept; the next search goes ahead) - and the prompt no longer says the sandbox has internet access; three declined calls hand nothing over (they counted as three failures: a stronger model or the cloud would have taken the turn); a page asks per site (the same host in capitals does not ask again, another site does; once leaves nothing); a redirect within the site (dr.dk → www.dr.dk) is followed, one to another site stops with the new address for the model, unless the owner always allows that site, and every redirect is followed when the internet is allowed; code runs offline (`offline` sent to the sandbox, the result's note) unless it says `internet: true` (also "true" and 1), which asks - allowed once with `end_leftovers`, and `approval_answered` in the events; always for code, taken back in Settings; without offline code (no Landlock ABI 4, or the daemon's `offline: false`) every command asks; a person is asked without "always" (refused by her approve route) even where the owner always allows; the setting saved through the API and `internetAsks` in the auth status (signed in only); a file name with `$(...)` stays a name in the OCR command (it ran in the shell before: `!r` quoting); the compose file's networks and the firewall's addresses agree. 7 tests (`tests/test_netfetch.py`): only public addresses (IPv4 first), the connection to the checked address with no second lookup (a name that resolves only for the check still reaches the server), HTTPS on the pinned address with SNI and the certificate verified against the name (a certificate for another name refused), every redirect checked and pinned, an app's picture URL on the LAN's public range (30.11.0.0/20) refused (before, the Hub fetched it), IDN names looked up as the connection names them (`xn--strae-oqa.de`, not `strasse.de`). 6 tests (`tests/test_sandbox_daemon.py`, real Landlock): the owner's offline command cannot connect (not even to localhost: "Permission denied") while it writes its files as usual, a person's confined command with `offline` cannot either; MPTCP, TCP Fast Open by `sendto` and `sendmsg`, and UDP all open without limits, all refused offline (Unix sockets stay), a person's online command keeps UDP but not MPTCP or Fast Open - without the seccomp filter the same test finds all of them open offline; the daemon reports `offline: true`; the socket filter builds for x86_64 and arm64; the self-test's "offline code" line (PASS through the real daemon, FAIL when the daemon ignores `offline`, WARN without ABI 4); a command allowed online once leaves nothing running (background, `setsid`, `nohup`); where Landlock cannot limit connections (ABI 3) offline is refused, not run unlimited. 1 test (`tests/test_people.py`): a hand-over in her chat gives the stronger model her prompt (it gave it the owner's: his name and memory index - an older bug found by the review). By hand with ABI 7: every way out under `confine.py --offline` and a person's limits, and inside a Docker 29.9.1 container with the default seccomp profile as uid 1000 (as the sandbox runs): TCP, MPTCP, Fast Open, UDP and ping sockets open without the filter, refused with it; listening, Unix sockets, netlink and `localhost` from /etc/hosts still work. Docker 29.9.1 here with the compose file's networks and the real firewall script (30 checks): before the rules a model container reaches the internet, the LAN and the VM; after them none of the three, the Hub reaches the models by name, the internet, the LAN and the sandbox, SearXNG and the sandbox keep the internet, the sandbox loses the LAN, the models and the VM, both published ports answer, a second run adds no rule; the Hub's default route through `core`; 0.14.0's containers moved to `inner` by `compose up`; the updater's way-out probe (`update.sh status`/`firewall`) through real `compose exec`: "open" before the rules, "no way out" after, "not running" for a stopped one. The firewall sync (copy, daemon-reload, restart; nothing copied when unchanged) with a fake systemctl. In Chromium against the dev server (21 checks): the setting, the empty always-allowed list, a search asking with its query and "Waiting for your answer", allow once, a subagent's search asking inside its row with the turn waiting for you, a picture from the web shown as a link and not loaded, always (the next search does not ask), code with internet denied (the call says so), a site asking with its host, Settings listing Web search and www.dr.dk, Ask again, the MCP tab without them, a person on a Pixel 7 asked without Always, her answer going through, no page errors, and the picture loading again once the internet is allowed. An independent review (a separate agent, the whole diff) found: offline code could still connect by MPTCP (and, found while checking, TCP Fast Open); "Allow once" left background processes online; redirects reached other sites unasked; three declines handed the turn over; IDN names resolved to another domain; long commands cut without a sign; the buttons staying after an answer and a subagent's question not shown as waiting; docs promising more; app picture URLs not checked against the LAN's ranges; and the older hand-over bug in people's chats - all fixed, and HTML artifacts loading from the web in the browser documented as not covered. A second pass of the same review confirmed every fix by running it; of its remaining low findings, a remote `srcset` candidate after the first is now dropped (every `srcset` goes while asking), the seccomp filter is required wherever Landlock limits connections (a person's online command no longer runs without it), and ptrace was tried: a uid-1000 process can attach to another unconfined, but offline code gets "Operation not permitted" (Landlock's domain rule), so it cannot drive an online process that way. The remote-load filter in Chromium against crafted HTML (13 checks): https, protocol-relative, backslash and tab-split addresses become links; the Hub's own and `data:` pictures stay; `srcset`, picture sources, video, SVG images and style backgrounds lose their web addresses; links stay; no request left the page. Not on the VM. |
| Windows programs (0.16.0) | 16 tests (`tests/test_windows.py`): `windows_app` only where the sandbox's `/health` says `windows` (asked again after the cache) and not in a person's chat, the system prompt's two texts; a run's request (the target as a sandbox path, the steps as given, the default screen and timeout, `ocr` for a model without vision, `ArtifactsPath`, the build's own time), its screenshots read only from the folder it was given and deleted after, the steps, windows and console output in the result, a failed step as news not a failed call; steps as a JSON string, a screen size, the timeout capped, bad steps and targets refused before the sandbox; no answer from the sandbox; asking first it runs offline without a question, online where internet for code is always allowed, asks where code cannot be kept offline. winshot's parts without Wine: PE kinds (x64, x86, .NET Framework, arm64, not a program) and the refusals, the build's program or its errors (paths and the project suffix stripped), the program's windows parsed from `xwininfo` (Wine's own, 1x1 and unmapped ones left out, a title with quotes), coordinates relative to the first window and off-screen or non-finite ones refused, finding text (whole words, a word's start, inside one word, case), the prefix settings, `--check` and its refusal. dotnet_windows: the versions read from `BundledVersions.props`, pinned files, a `.conda` unpacked by member and one that climbs out refused. `test_a_real_run_under_wine` (opt-in, `SLQ_TEST_WINE=1`) passed here: a WinForms program built, "Søren" typed, Greet found by its text, the dialog in the shot, closed by Return. By hand, the real image (`docker build` of `services/sandbox` on a clean Ubuntu 24.04 base, Docker 29.9.1 with the default seccomp profile, as uid 1000): the packs placed as 10.0.13 and the offline build proved during the build; `/health` with `windows: true`; through the real daemon, offline as the owner: a WinForms project built and run (11 s), "Søren" typed, Greet and OK found by their text, a drop-down list's "Pump 3" and File → Exit clicked by text (the program ended, exit code 0), OCR text per window for a model without vision; a slider dragged (980 → 357 rpm), WPF shown and clicked, a startup crash reported with its .NET exception; the self-test's "windows programs" line PASS (10 s); without an init, ten zombies per run, with `init: true` none. The dev Hub wired to that container in Chromium (4 checks): the fake model wrote a project and called `windows_app`; the row "Tester (6 steps)", its steps and three real screenshots. Measured on the way (winshot's notes): Wine 9's virtual desktop drew a self-moved window at twice its position; managed windows without a window manager waited 2 s per dialog; as Windows 10 a WinForms menu ended the program; Tesseract on the whole screen missed a drop-down list's rows. **Typing**: 0.16.0's path (libX11/libXtst by code point: a spare key code remapped to each character, then XTEST) was checked only against an X client that records what it receives (python-xlib); under real Wine it failed - see the 0.16.1 row. Not on the VM. |
| Typing into Windows programs (0.16.1) | In this build container, not in the built image (no Docker daemon or Docker Hub here): Ubuntu 24.04.5, kernel 6.18, the image's packages from Ubuntu's archive (wine64 9.0~repack-4build3, xvfb, xdotool, x11-utils, zstd, tesseract with dan, dotnet-sdk-10.0 10.0.113), the Windows packs and runtime 10.0.12 placed by `dotnet_windows.py` (the offline WinForms build proved), a prefix from `winshot --setup` as uid 1000, `LANG=C.UTF-8`, as the sandbox runs. A WinForms program that writes what its text box holds, and each KeyPress, on Return. **0.16.0's typing, measured:** "Hello ÆØÅ æøå" gave "ello �� ���" (the first key lost; every Latin-1 letter U+FFFD); letters, digits and spaces ("Hello World ABC xyz 123") arrived after the lost first key; in WPF "Søren ÆØÅ æøå — 😀" gave U+FFFD for every letter, "—" and "😀" right; a program busy 120 ms per change got "br5." of 43 characters. Wine's `+key,+event` trace: the first key reached Wine as `VK_NONAME` (its MappingNotify was processed only after that KeyPress) and `ToUnicodeEx` found no key for it; later keys got a virtual key. Tried and dropped: legacy (Latin-1) keysyms fixed the letters only; 250 ms between keys, a remap 0.3 s before the first key and 50 ms between remap and press did not bring the first key back; a real key (Shift, space) before it did; a busy program scrambled regardless (one remapped key code, read late). **HubKeys** (SendInput, checked with `ToUnicode`; Wine's `VkKeyScanW` gave c for č and ? for Ж before the check), each exact: Danish idle; 43 characters with the program busy 120 ms per change; WPF; "a—b €c čd Łe Жf 中g ✓h 😀i"; every printable ASCII character; no click before typing; a multiline box with Return and Tab over two type steps; a click on a second box between two texts with the first box busy 150 ms per change - without the barrier the second text landed in the first box, with it each in its own (the second barrier waited 741 ms; idle 0 ms). The helper starts in 0.1-0.2 s. `winshot --setup` builds it with no package source and starts it under Wine (the whole setup 19.5 s; the prefix stays 19 MB) - started first with setup's Wine environment it failed (`mscoree` disabled: Wine could not load `System.Runtime.dll`), so it runs with the programs' environment. The self-test's probe through the real winshot: PASS, 10.5 s; through 0.16.0's winshot: FAIL, "arrived as "�ren: ��� ���"". Tests: 11 new cases in `tests/test_windows.py` (the protocol against a fake helper - barrier order, the note when the program is busy, five failures; `check` needs the helper; the source; an offline build with the real SDK; the self-test's verdict), and the two opt-in real ones (0.16.0's, now checking that Danish letters and an emoji arrive exactly through a WinForms program; new: a click between two texts on a busy program) pass here as uid 1000 and fail against 0.16.0's winshot ("a:sss|b:second ���"). Not on the VM, not in the built image. |
| Allow in this chat, the card while asking (0.16.0) | Tests: a search answered "Allow in this chat" (no second question in the chat or its subagent, the next chat asks, listed in the chat's `allowed`, taken back with `DELETE .../allowed?tool=`, gone with the chat; a bad decision 400); an MCP tool the same way (not kept as "always"); a person is not offered it (`can_chat` false, her route refuses `chat`). The scheduler: a paused lease frees the card (an image job switches to ComfyUI and runs, `resume()` waits, switches back, the same ticket; job meta `resumed`), a turn stopped while queuing again leaves nothing. The agent: while a question waits nothing holds the card, background jobs take every slot, the answer's `approval_answered` comes at once, then `queued` until a slot frees, then `started` and the tool; and Stop while the question waits leaves the card with the other jobs and never re-queues (no `approval_answered`, nothing pending) - before, `_ask` resumed in its `finally` even on cancel, so a stopped turn took the card back only to let it go. In Chromium against the dev server (6 checks): the four buttons, no second question, the chat menu's "Allowed in this chat - Web search", the dialog, Take back, asked again. |
| Artifacts while asking, the updater's hand-over, Hugging Face (0.16.0) | In Chromium against the dev server (9 checks, "remote page": an artifact with a picture, a stylesheet, a script and a fetch from the web, and a link): asking first, the page has `frame-src 'self'`, the fetch is rejected, nothing reaches the web (each blocked by the policy, the browser says why), no `allow-popups`, the link leaves the frame on Chrome's error page without a request; allowed, the page has no `frame-src`, the artifact loads all four as before. A guard script in the page also removes WebRTC (which Chromium does not gate by connect-src) and strips any frame a script adds, so neither a direct `RTCPeerConnection` nor a nested `srcdoc` frame reaches a STUN server - checked with a separate Playwright harness built for it (0 packets by either route across direct/dynamic/static/document.write, normal scripts still run), not through the dev server's "remote page". The page's `Cache-Control: no-cache` matters: with the first page cached, the link's request did go out. The updater: 7 tests (`tests/test_hub_update.py`) - a changed updater hands over after the fast-forward (the state written), the new one takes over (the file gone, its flags), one that does not start puts the code back, its own failure is the result, a resumed update continues from the build without starting over, a resume file it cannot use is left in place (so the old updater rolls back as "did not start"), `--resume` hidden from `--help`; `update --resume` of the real script against a scratch `HUB_ROOT` (it reports and stops at the build). Add from Hugging Face: the trees of `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF` (MTP and noMTP twins, a draft model, `vision-f16`) and `HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF` (a FastMTP sidecar; Hugging Face's 1.86B parameters are the sidecar's) as Søren saw them (2026-10-24): twins labelled `Q4_K_M` / `noMTP-Q4_K_M`, the draft and the sidecar left out, `vision-f16` as the projector, a big "Vision" model kept as a model, the parameter count dropped when the smallest file has more than 40 bits per weight (F32 is 32, so a real F32 repo keeps its count), and two projector files that would read the same label told apart by their folder. |
| C# in the sandbox (0.15.1) | 3 tests (`tests/test_dotnet.py`): the image installs `dotnet-sdk-10.0` without recommends and sets no telemetry, banner or build servers - and not `EnableWindowsTargeting` (with it set for every project, even a plain console app's restore fetched `Microsoft.WindowsDesktop.App.Ref` 10.0.13 from NuGet, so nothing built offline); bash sends `ArtifactsPath=<working folder>/.hub/dotnet`, a person's inside her turn; the system prompt and the bash description tell the model about C#, Windows programs and the publish command, and a person without code hears nothing of it. The real SDK (Ubuntu's 10.0.113, runtime 10.0.13) installed here: a console app made, built and run (8 s, then 2.7 s), with `ArtifactsPath` the project folder keeps only its sources (bin/obj under `.hub/dotnet`, several projects side by side), `dotnet publish -o publish` puts the program in the folder; no MSBuild or compiler server left running; built and run through the real daemon as the owner offline, as a person online and as a person offline (Landlock and the seccomp filter: 7 s each). The WinForms template's build got past the Windows check and stopped only at NuGet, which this build environment cannot reach. The updater's backup leaves out NuGet caches and `.hub/dotnet` (a tar of a fake data folder). The image's new layer, copied from the Dockerfile, built with Docker 29.9.1 on a clean Ubuntu 24.04 base with the official image's package sources: SDK 10.0.113 without the AOT packs (546 MB); in a container with no network at all, as uid 1000 with a fresh home, a console app made, built and run in 7.6 s, its output in `.hub/dotnet`, no build server left. The full sandbox image not built here (Docker Hub is not reachable). Not on the VM. |
| Model names (0.14.0) | 4 tests (`tests/test_models_names.py`): a chat model renamed (spaces tidied) shows in `/hub/api/models` with `name_card`, in `/v1/models`' `name`, not in `/api/tags` (ids), and in the system prompt as "Store hjerne (Qwen3.8 27B)", while the request goes to the id; it survives a new catalog (restart); "" or its own name goes back and leaves no override; its thinking setting keeps the name; a name another model has (any case) is refused, 60 characters at most, helpers and unknown ids 404, a person may not rename (403) but sees the name; the hand-over: the small model reads "Store hjerne (Qwen3.8 27B), a stronger model", the big one "handed over to you by Lynet (Qwen3.6 35B-A3B)", the notice "Handed over from Lynet to Store hjerne"; a picture model: the image tool lists "qwen-image-2.1: Billedmaskinen (Qwen-Image 2.1)" and the result says "with Billedmaskinen"; an added model's name changes in its own entry ("" = its id). In Chromium against the dev server (12 checks): Rename in Settings → Models for a chat and a picture model, the list with the model's own name beside, the Defaults choice, the composer, the answer's footer, the picture menu, "Use Qwen3.8 27B", a taken name refused in a toast; on a Pixel 7 the dialog and no sideways scroll. Not on the VM. |
| Open page across an update, lost stream (0.13.6) | A test: `X-Hub-Version` on the UI's answers (auth status, chats, health), not on the model API's. In Chromium as a Pixel 7 against the dev server (9 checks): no notice while the version stays; a newer version (the answers rewritten in the browser) after the page comes back into view gives "SLQ Hub was updated to 9.9.9" with Reload, and Reload loads the page without it; offline for 12 s while the turn ends - what streamed stays, and back online the answer, the files card and the stats come; the first stream cut without an end and the reconnect answered 502 - the page follows the turn again (3 streams) to the answer and the card. The same two connection checks fail with 0.13.5's chat view. Not tried: a real phone, a real proxy. |
| Music in one piece, metadata, export (0.13.5) | Tests: the system prompt's sound section (one music call makes the whole piece only when a music model is there; the ffmpeg mix command; "makes" for one tool), the music and sound tool texts (a solo in the music prompt; with no music model the sound tool says separately made music does not fit), the export's `made/` (the newest turn's version of a file, a file gone from the folder and one too big said so, subfolders kept). ComfyUI v0.42.3 on the CPU with `--disable-metadata`: an MP3 from `SaveAudioAdvanced` and a PNG from `SaveImage` without the prompt (without the flag both carry it). The mix command run with ffmpeg 6.1.1 (the sandbox's version) on a 48 kHz stereo and a 24 kHz mono MP3; the wrapper: no banner, `-version` still answers, ffprobe works. The sandbox and ComfyUI images themselves not rebuilt here. |
| Sound, full weights only (0.13.4) | 22 tests in `tests/test_audio.py`: the request carries only key, family and files (no speed knobs) and a capped piece is named in the result; a `settings.json` from 0.13.3 with `sound.speech_speed` loads without it and the whole Sound section saves (the key is still refused when sent); the samples list skips 0.13.2-0.13.3 speed runs, missing files and bad names; with both Røst models downloaded Danish goes to Røst v3 Turbo and the tool says so, the Sound setting still picks Røst v3. The real speech service (random weights, 2 threads) with the self-test's speech lines: one sample per model, speech.json without settings, the WARN for a piece that ran to the ceiling. In Chromium against the dev server started with a 0.13.3 settings file (27 checks): Turbo first on the models page, no speed choice, the Danish choice and its help, Save over the old file (the stale key gone from the file), one sample per model playing, speech in a chat by Røst v3 Turbo, phone width. Not on the VM yet. |
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
    (torch 2.11.0+cpu); Røst v3 Turbo's files downloaded and loaded in the
    0.13.1 self-test. Not seen: the 0.13.4 image (only code removed).
29. **The real voice models**: all three spoke on the VM, one sentence each
    with seed 1, and were heard in Settings (Røst v3 Turbo best, Røst v3 next,
    Chatterbox English-accented). Not yet heard: a longer text through this
    service's sentence splitting and pauses, a cloned voice, and whether Mic
    and Nic come out as "male voice" and "female voice" from their pitch (the
    tool description shows it).
30. **Faster speech with full quality**: 8-bit weights are out (0.13.4: they
    said wrong words, even with the attention layers full), as are bf16 and
    weight-only int8 (both slower on this CPU). T3 is as fast on 5 threads as
    on 10, so overlapping the next sentence's T3 with this sentence's decoder
    could save 15-20% on longer texts (estimated; not built - the two would
    share torch's global random generator, which breaks the seed's
    repeatability). Beyond that, full quality faster means the card: a speech
    residency would swap the chat model out per request (or sit beside a
    small one, which the scheduler cannot do yet). Offered to Søren, not
    asked for.
31. **Memory with sound**: two voice models in the speech container (about
    4 GB each, a 12 GB limit) while ComfyUI reads ACE-Step XL's 20 GB of files
    and the LLM's file is mapped, in a 40 GB VM. Watch `free -h` and the
    kernel log for OOM kills the first time both run.
32. **ACE-Step 1.5 and Stable Audio 3 on the card**: both ran in chat 15,
    10 s each (8.5 s and 18.8 s, mostly loading and ACE-Step's planner).
    Not seen: VRAM (XL Turbo with its 4B planner about 20 GB), a 2-3 minute
    song's time (the planner's share grows with the length), how they sound,
    how Danish lyrics sound. ACE-Step 1.5 Turbo is the lighter choice if XL
    does not fit or is slow. Whether the models now make one piece instead of
    mixing parts (0.13.5's texts) is for the next music chat.
33. **A person's voice clip in the real sandbox**: ffmpeg confined by
    Landlock to her turn folder - tried with a fake sandbox and with the real
    ffmpeg separately, not together.
34. **Chinese, Japanese, Hebrew and Russian in Chatterbox Multilingual**:
    their optional text helpers are not in the image (the Chinese one would
    download a table from Hugging Face; the service runs offline), so these
    languages are spoken from the plain text and probably worse - Chinese
    likely badly. Danish, English and the other European languages need none.
35. **Chatterbox stopping by itself**: with Resemble's repetition penalty
    (0.13.1) it stopped at 79 tokens in both full-weight runs (it ran to the
    ceiling once with 8-bit weights, which are gone). Watch the self-test's
    WARN lines and the "did not stop by itself" note in results.
36. **The inner network and its firewall on the VM** (0.15.0): tested with
    Docker 29.9.1 and the real script outside the VM, not on VM 222 (Docker
    29.9.2, kernel 7.0). After the update and the one-time `sudo bash
    /opt/slq-hub/update.sh firewall`, its output (and `update.sh status`)
    should say "no way out" for llm, comfyui and speech. Then a chat answer, a
    picture and a spoken sentence prove the Hub still reaches all three. If the
    `inner` subnet (172.31.251.0/24) collides with another network on the VM,
    `compose up` fails and the update rolls back - its log would say "Pool
    overlaps".
37. **Offline code on the VM** (0.15.0): the VM's sandbox has Landlock ABI 8
    (the 0.13.0 self-test), so asking first should run code offline; the
    self-test's new "offline code" line probes it (Landlock and the seccomp
    filter inside the real sandbox container). Not seen: a real `pip install`
    asking and running with "Allow once", whether Qwen3.8 and Ornith set
    `internet: true` when a command needs it (or retry after the "ran without
    the network" note), whether a tool the agent uses breaks without UDP when
    offline, and how often asking first interrupts a normal research chat.
38. **`web_fetch` on the pinned connection against real sites**: tested with
    local HTTP and HTTPS servers (SNI and the certificate by name); this build
    environment intercepts outbound TLS, so no real site was fetched through
    it. A site whose IPv4 address does not answer is tried on its next
    address (IPv6 last).
39. **C# on the VM** (0.15.1): the sandbox image with `dotnet-sdk-10.0` built by
    the update (about 600 MB more); the self-test's sandbox line shows the
    .NET version. Not seen: whether the published .exe gets its icon and
    version information when built on Linux, whether it runs on Søren's PC
    with the .NET 10 Desktop Runtime, whether `dotnet test` works offline and
    in people's chats (the classic test runner uses TCP on localhost), and how
    Qwen3.8 and Ornith write forms without a designer.
40. **Windows programs on the VM** (0.16.0): the update builds the new sandbox
    layers there (Wine from the archive, about 120 MB to download and 735 MB
    on disk; 200 MB from conda-forge, unpacked to 256 MB; the build proves
    the offline WinForms build and stops if it fails). Then the self-test's
    "windows programs" line should PASS. Not seen: Wine on kernel 7.0 and
    Docker 29.9.2 (here: kernel 6.18, Docker 29.9.1), how long a run takes on
    the 5950X (11 s here: build 6 s, Wine 2 s), whether Qwen3.8 and Ornith
    give good click coordinates from the screenshots (their vision encoders
    see the picture at their own resolution) or need clicks by text, and
    programs beyond standard controls. Typing (0.16.1, HubKeys) passed under
    Wine 9.0 with the image's packages in this build container, not in the
    built image: the update builds HubKeys with the prefix (offline, seconds;
    if it fails the sandbox image does not build and the update rolls back),
    and the self-test's "windows programs" line types "Søren: ÆØÅ æøå" - its
    PASS on the VM settles it, a FAIL shows what arrived. Also not seen: the
    barrier on a slow VM (5 s at most per text before it types anyway).
41. **The updater's hand-over** (0.16.0): tested with fakes and the script's
    resume path only. It runs only when an update's fast-forward changes the
    updater itself (`tools/hub_update.py` or `update.sh`) - 0.16.1 and 0.16.2
    do not; its output then starts a second header "update, continued by the
    new updater".
42. **Allow in this chat and the card while asking with real models**: a
    question left waiting while a picture or another chat uses the card, then
    answered: the turn queues and goes on (the model may have to load again -
    its prompt cache is gone then).
43. **Artifacts while asking on real phones**: the policy and `frame-src`
    tried in Chromium only (Safari and Firefox honour both; not tried). The
    WebRTC/frame guard was checked in Chromium with a separate harness; its
    worth is Chromium-specific (that is the engine the household uses), and it
    is untried on a phone.
44. **Cut-off calls on the card** (0.16.2): whether Qwen3.8 continues a kept
    file cleanly from its last characters with `append`, and writes long files
    in parts when the prompt and the result say so; that chat 21 goes on after
    the update. A larger context helps too (Settings → Models → Settings): by
    the model shapes, Qwen3.8 27B Q4_K_M should take a 128K pool (34 MB of
    q8_0 KV per 1K tokens) and the Q5_K_M stays at 64K - estimates, check the
    VRAM in Settings → System after a change.
45. **The ComfyUI image with the Hub's nodes** (0.18.0): the update rebuilds it -
    Seed-VC and MSST fetched by commit (GitHub must allow fetching a commit
    that is not a branch head; it did here), librosa, munch, matplotlib,
    beartype and rotary-embedding-torch at the tested versions on top of
    ComfyUI's requirements. Then ComfyUI's log should list `slq_audio` under
    "Import times for custom nodes" and `/object_info` the four SLQ nodes;
    a tool says "its image is older than this Hub" when they are missing.
46. **The new downloads** (0.18.0): Settings → Models → Sound, music and
    speech → Mel-Band RoFormer vocals and Seed-VC. Their Hugging Face paths
    are the ones the projects' code uses (`KimberleyJSN/melbandroformer`,
    `Plachta/Seed-VC` with the v2 singing checkpoint, `openai/whisper-small`'s
    `model.safetensors`, NVIDIA's two BigVGAN v2 repos,
    `lj1995/VoiceConversionWebUI`'s `rmvpe.pt`); a wrong one shows as
    "Hugging Face answered 404" on that file. The sizes are estimates.
47. **The real weights loading** (0.18.0): Kim's checkpoint must fit the
    RoFormer exactly (it is loaded strictly; it did in shape here); the
    Seed-VC, BigVGAN, CAMPPlus and RMVPE files load with `weights_only`
    (tensors and plain data only - an error names the file); whisper-small
    through transformers 5. If a Seed-VC checkpoint is refused, the error
    says which; the fix is an allow-listed type, not unsafe loading.
48. **How it sounds, how long it takes** (0.18.0) - a test plan: (1)
    `audio_separate` on a song you know (are the vocals clean, does the
    accompaniment keep the backing vocals?); (2) `voice_convert` speech with
    a 10-s clip of yourself (`singing: false`); (3) `voice_convert` on a song
    with a sung clip, music on (the octave it reports, the words, the mix);
    (4) `music_remake` with a live clip ("live concert recording, cheering
    audience, arena reverb", lyrics given), then with `keep` 0.4; (5)
    `music_generate` with a voice clip as reference, then the same song
    through `voice_convert`. Note the seconds each took (the result says)
    and the VRAM in Settings → System; ask for the export when something is
    off. Expected limits: a reference clip steers a song's sound, it does not
    copy a voice; separated vocals carry some bleed, and Seed-VC hears it.
49. **The video downloads** (0.19.0): Settings → Models → Video models. The
    paths are the official templates' and were checked on Hugging Face's
    pages (2026-10-24). Settings shows 35.4 GB for each 14B entry (the second
    one is 28.9 GB more: the text encoder and VAE are shared) and 16.9 GB for
    the 5B - binary units; Hugging Face's pages say 38.0 and 18.2 GB. No
    ComfyUI image change: the Wan nodes are ComfyUI's own (v0.42.3).
50. **Video on the card** (0.19.0) - a test plan, one at a time, noting the
    seconds the result gives and the VRAM in Settings → System: (1) the 5B
    from a description at its default 720p, 5 s; if it runs out of memory,
    `hd: false` (480p) and tell me; (2) the 5B from a picture (attach one:
    "make this picture move - the camera slowly pushes in"); (3) the 14B
    picture model fast, then `quality: full` (several times longer); (4) a
    first and a last picture; (5) the 14B text model. Then: does it look
    right, does the motion follow the prompt, Danish prompt vs English.
51. **The 14B pair and the VM's memory** (0.19.0): ComfyUI holds one 14.3 GB
    expert on the card at a time and swaps the other in from RAM; with the
    6.7 GB text encoder that is about 35 GB of weights for the VM's 40 GB.
    Watch `free -g` and ComfyUI's log during (3). If the VM swaps hard or
    ComfyUI is killed, try `COMFY_ARGS=--cache-none` in `.env` (ComfyUI
    then reloads from disk each time) and `update.sh`; ask for the log
    before changing more.

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
| `frontend/src/lib/help.ts`, `frontend/src/components/Help.tsx`, `backend/app/ui/help_routes.py` | Help (0.20.0): the text, by section, with the tools each item is about; the page (owner: `views/HelpView.tsx`, a person: in `PersonApp`); what works on this Hub now. |
| `backend/app/video/`, `backend/app/agent/tools/video.py` | Video (0.19.0): the Wan 2.2 graphs (`workflows.py`), the pipeline (models, sizes, ComfyUI through the queue), the `video_generate` tool. |
| `services/speech/server.py` | The speech service (Chatterbox on the CPU): text pieces, voices, the two model families, NDJSON over aiohttp. |
| `services/comfyui/nodes/slq_audio/` | The Hub's own ComfyUI nodes (0.18.0): ACE-Step timbre and cover source, the planner-free text encoder, Mel-Band RoFormer stems, Seed-VC voice conversion (README.md there; the image installs them with Seed-VC and MSST at pinned commits). |
| `backend/app/rag/` | Libraries: extraction, indexing, search, the `library_search` tool. |
| `backend/app/cli.py` | `selftest`, `set-password`, `create-key`, `keys`. |
| `frontend/src/` | The UI: `views/` (chat, project, libraries, settings), `components/`, `lib/turns.ts` (the turn view model for history and live runs). |
| `frontend/src/chat/`, `frontend/chat.html` | The phone chat page (`/chat`): its own entry, on the model API with a key (`hub.ts`), chats in IndexedDB on the device (`store.ts`). |
| `backend/app/agent/device.py`, `backend/app/ui/people_routes.py` | People's chats (device turns: the temporary folder, the result, what is deleted) and the People settings API. `backend/app/gate.py` has `MEMBER_ROUTES`. |
| `frontend/src/person/` | The chat a person gets (`PersonApp.tsx`), their IndexedDB store (`store.ts`), the device-turn flow (`turns.ts`). |
| `services/sandbox/confine.py` | Landlock confinement for people's commands, and `--offline` (no TCP connections) for code while the internet asks first (used by `execd.py`). |
| `services/sandbox/winshot.py`, `dotnet_windows.py`, `backend/app/agent/tools/windows.py` | Windows programs on a virtual screen: the runner in the sandbox (Wine, Xvfb, xdotool, OCR; HubKeys, the typing helper, is its `HUBKEYS_PROGRAM`; `--setup` makes the prefix and builds HubKeys into it at the image build), the Windows .NET parts for the image (packs, runtime), the `windows_app` tool. |
| `backend/app/agent/internet.py`, `backend/app/netfetch.py` | Internet for the assistant (what asks, what "always" keeps); the fetch guard with its pinned connections. |
| `deploy/firewall/`, `tools/hub_update.py` | The VM's firewall (the sandbox off the LAN, the model containers off everything outside `inner`); the updater, which installs and applies it and tries the model containers' way out. |
| `services/` | The llm supervisor, the ComfyUI image, the sandbox daemon, the speech service. |

## Working on it

    # backend tests (Python 3.12+)
    cd backend && pip install -r requirements.txt -r requirements-dev.txt
    python -m pytest -q -p no:cacheprovider

    # the Hub with fake services, for UI work (soren / correct horse battery)
    cd frontend && npm install && npx vite build
    cd ../backend && python -m tests.devserver --port 8080
    #   or run `npm run dev` in frontend/ (port 5173, proxies to :8080)

    # windows_app for real: Wine, Xvfb, xdotool, ImageMagick, Tesseract and dotnet on
    # this machine, the Windows runtime from `dotnet_windows.py runtime DIR`, then a
    # prefix from `winshot.py --setup DIR` (it builds HubKeys into it; both scripts in
    # services/sandbox). Run as the prefix's owner (Wine refuses another user's)
    SLQ_TEST_WINE=1 HUB_WINE_PREFIX=DIR HUB_DOTNET_WINDOWS=DIR python -m pytest tests/test_windows.py -k real

    # the dev Hub against a real sandbox container that mounts its data/workspace
    # as /workspace (token "tok"); the fake model's "window" uses windows_app
    python -m tests.devserver --port 8080 --data DIR --sandbox http://IP:7000

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
- Approvals wait up to ten minutes (then: deny). Meanwhile the turn gives the
  card up; after the answer it queues again, and if the card was switched to
  other work, its model loads again (without its prompt cache).
- Offline code (internet asking first) has no network at all - no TCP
  connections, not even to a server it starts on localhost, and no UDP, so no
  DNS. Unix sockets remain, so it can still talk to a process that is online
  already (one a command allowed "always" left running).
- While the internet asks first, artifacts load nothing from the web; a page
  of the Hub loaded before switching to "Ask first" keeps letting an
  artifact's frame navigate away until it is reloaded.
- Windows programs run under Wine: 64-bit only, close to Windows but not
  identical, one at a time, each call from the start; not in people's chats.
  A typed character that is not on the (US) keyboard reaches the program as
  a Unicode packet: its KeyDown sees `VK_PACKET`, as on Windows.
- The run's live events are kept in memory for an hour; the stored messages
  are permanent.
- No custom ComfyUI workflows yet; no video. Speech, music and sound effects
  are one file per call (no voice conversion, no editing of a song; ffmpeg in
  bash mixes and cuts).
