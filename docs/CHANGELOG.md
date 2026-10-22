# Changelog

## 0.9.1 - 2026-10-22

- **A model stuck in a loop is stopped.** Chat 7, exported from the
  Hub, showed why this matters. Ornith 1.5 9B fetched the same two pages
  52 times in a row, with the same words each time, until the 60-step limit.
  That took 15 minutes. Now:
  - A look-up (web search, web fetch, reading or searching files, OCR, a
    library search) repeated with nothing changed in between is not run
    again. The model is told it already has that result. A failed look-up may
    be retried once.
  - After three refused repeats, or the same step three times, the model has
    to answer without tools, with what it has. The chat shows a short note
    saying so.
- **Long chats can always be condensed.** The request that condenses a long
  chat now fits the model's context. Without that, chat 7 (100K tokens on a
  64K model) would have failed on the next message. A long single turn also
  stays inside the context: the tool definitions are counted, and older
  answers and their tool calls are shortened too when results alone are not
  enough. "Condensed" is shown only when it really was.
- **Sampling for models added from Hugging Face.** Qwen3-family models (Qwen3,
  Qwen3.5 and fine-tunes such as Ornith) get Qwen's recommended sampling, as
  the bundled models do, including presence penalty 1.5 against endless
  repetition. Until now they got none, so llama-server's own defaults applied.
  The architecture is read from the model file. Sampling flags under
  Settings -> Models -> extra flags (`--temp`, `--presence-penalty`, ...) now
  win over the model card's values for every model.

## 0.9.0 - 2026-10-22

- **Export a chat** (the chat's ⋯ menu):
  - **Export as Markdown**: the conversation to read or keep, with the thinking
    folded away and tool calls and results shortened.
  - **Export for troubleshooting**: a ZIP with everything the Hub knows about
    the chat, to find out why it behaved as it did. It holds every message
    block (with full tool results) and the runs (steps, timings, tokens,
    errors). It also has the live event log of runs from the last hour and
    what the model would be sent next (system prompt, history after
    condensing, tools). Then the queue around the runs, the model server's
    recent log, and the chat's pictures and attachments. No keys or tokens.
- The phone chat page: **Export this chat** in its menu saves the open chat as
  a file (photos and pictures included).

## 0.8.0 - 2026-10-22

- **Chat page for phones** (`http://<hub>:8080/chat`): a simple chat for the
  rest of the household on the home network. It uses the model API with its
  own key, so it works only on the LAN and cannot see your chats, projects or
  settings. It streams answers, can think first, takes photos from the phone
  (with a model that can see them) and makes pictures (**Picture**, with the
  picture model from Settings, square, portrait or landscape). Its chats are
  kept on the phone, not on the Hub. Add it to the home screen to use it like
  an app.
- **Settings -> Apps and keys**: the new-key dialog gives a phone link with the
  key in it, and a button that limits the key to the home network.
- Ollama apps that check the server with `HEAD /` (Enchanted on the iPhone)
  no longer say "Server unreachable".
- `/v1/models` also tells what each model is (name, chat, embedding or image,
  vision, thinking, default, loaded). Other clients ignore the extra fields.

## 0.7.0 - 2026-10-22

- **Write to the assistant while it works**: a message sent during a turn
  waits (shown as waiting in the chat) and the model reads it after the step
  it is on, so you can correct or add something without stopping it. If it
  had already finished, or you press Stop, the message starts the next turn.
  While it works, the square stops it and the arrow sends.

## 0.6.0 - 2026-10-22

- **Z-Image Turbo** (Alibaba, 6B, Apache 2.0): fast photorealistic pictures in
  8 steps, text in English and Chinese. It shares its text encoder with FLUX.2
  klein. Download it in Settings -> Models.
- **Chroma1-HD** (8.9B, built on FLUX.1 schnell, Apache 2.0): released
  without a safety filter, so any subject and style. Uses a negative prompt.
- **Add image model** (Settings -> Models): paste a Civitai or Hugging Face
  link to an SDXL checkpoint: SDXL itself, Pony, Illustrious, NoobAI and the
  many models built on them. The Hub checks the file before it downloads
  anything (Civitai's base model; on Hugging Face the file's own header). It
  picks settings for the kind of model (quality tags, sampler, clip skip,
  negative prompt), which you can change later under the model's Settings.
  Remove takes the file too. Civitai files that need a signed-in user need
  `CIVITAI_TOKEN` in `.env`.
- The assistant is told which picture model the chat uses and how to write
  its prompt (tags for Pony and Illustrious, sentences for the others). It
  only offers models that are downloaded.
- A chat that chose a picture model that was later removed uses the one from
  Settings.

## 0.5.2 - 2026-10-22

- Phones: the row under the text field stays on one line. The send button
  no longer slides off the screen. The picture and thinking buttons show
  their icon only (blue while thinking is on), and the model name shortens.
- Phones: the menu (sidebar) and the side panel take the visible height, so
  the GPU plate under Libraries and Settings is no longer cut off by
  Chrome's address bar.
- Menus near the edge of the screen move back into view.

## 0.5.1 - 2026-10-22

- Fixed: an icon button's "Delete?" question (files, library documents) was
  cut off; the button now widens for it.

## 0.5.0 - 2026-10-22

- **Put project files in a message**, to have them changed or to ask about
  them: in Files beside the chat, the + on a file (or Add to chat on an open
  file), or + -> Project files under the text field. The assistant gets the
  file's text as it is when you send (as much as fits in about a third of
  the model's context; bigger files, PDFs and Word files it reads with its
  tools) and sees pictures. The files show on the message; a click opens one.
- Files has a search box above the tree.
- The chat column is wider (58rem instead of 46rem).
- The Files button is there in a new project chat before the first message,
  and the panel stays open when that first message creates the chat.
- Fixed: a UTF-8 text file with a non-ASCII character across byte 8000 was
  taken for a binary file (`read_file` refused it).
- Fixed: the sidebar could keep showing a finished new chat as "New chat",
  still running (an older list refresh arriving last).

## 0.4.0 - 2026-10-22

- Choose the picture model per chat (a picture button under the text field,
  shown when more than one picture model is downloaded) and per project
  (project Settings). Settings -> Assistant -> Images stays the default.

## 0.3.0 - 2026-10-22

- **ERNIE-Image and ERNIE-Image Turbo** (Baidu, 8B, Apache 2.0) as optional
  picture models: download them in Settings -> Models, choose them under
  Settings -> Assistant -> Images. Photorealistic; generation only (edits go
  to Qwen-Image or FLUX.2 klein).
- **ERNIE's prompt enhancer**: a 3B model in the same image job turns the
  prompt into a detailed description before the picture is made. The chat
  shows what it wrote. On by default, a switch under Images.
- The image tools only offer what the downloaded models can do (editing
  needs Qwen-Image or FLUX.2 klein, cutting out needs Qwen-Image).

## 0.2.0 - 2026-10-22

- **Add from Hugging Face** (Settings -> Models): paste a GGUF repository or
  its link; the Hub lists its sizes (split files together, the vision
  projector apart), says which fit the card, suggests names, reads from the
  chat template whether tools and thinking work, and downloads the files with
  its own queue. Added models can be loaded, tuned under Server settings, used
  in chats and by apps, and removed with their files. Gated repositories need
  `HF_TOKEN` in `.env`.
- Projects: the New chat button sits with the chats.
- The Think button works for models that switch thinking on and off without
  effort levels.

## 0.1.6 - 2026-10-22

- Settings -> Assistant -> Images: choose the model for pictures (Qwen-Image
  2.1 or FLUX.2 klein, when downloaded).
- The tab rows (projects, settings) no longer show a small vertical scrollbar
  on Windows.
- The welcome text mentions that you can ask for a picture.

## 0.1.5 - 2026-10-22

- Self-test: the chat line says whether the model was already loaded (the Hub
  preloads the default model, so "incl. loading" was wrong) and how much VRAM
  is in use after the answer.
- `install.sh`: no curl error lines while it waits for the Hub to start.
- `docs/HANDOFF.md`: the first install on the real hardware - the machine as
  installed, what went wrong on the way, what is still open.

## 0.1.4 - 2026-10-22

- `install.sh`: the GPU check rounds the VRAM it reports (a 24 GB card showed
  as 23 GB).

## 0.1.3 - 2026-10-22

- `install.sh`: the GPU check reads `/proc/modules` instead of `lsmod | grep
  -q`. Under `set -o pipefail` that pipe can fail when the match is found
  early (SIGPIPE), so it could report amdgpu as not loaded when it was. The
  same fix for the dkms and PCIe-atomics checks, and in `create-vm.sh`. The
  "not loaded" message now says what to check.

## 0.1.2 - 2026-10-22

- `install.sh` keeps the passed-through card awake: it puts
  `amdgpu.runpm=0` on the VM's kernel command line, and the GPU check says so
  if it is not in effect. The VM check shows the card's power state.
- `docs/INSTALL.md`: a VM made by hand gets Ubuntu installed without the card;
  the card goes in after `amdgpu.runpm=0` is set. Troubleshooting for a VM that
  stops by itself (`kvm run failed Bad address`, `stuck in D3`).

## 0.1.1 - 2026-10-22

- `install.sh` checks the VM before it installs anything: the AMD card is
  there, the machine type is q35, Secure Boot is off, and there is room for
  the models and images (on LVM it prints the command that grows `/`). It
  warns about legacy BIOS and a CPU type without AVX2.
- `docs/INSTALL.md`: how to make the VM by hand in the Proxmox UI and install
  Ubuntu from the ISO, as an alternative to `create-vm.sh`.

## 0.1.0 - 2026-10-22

The first version.

- Install path: `proxmox/create-vm.sh` (a q35/OVMF VM with the GPU moved from
  VM 104), `install.sh` (HWE kernel, amdgpu, Docker, firewall, images, models,
  self-test), `update.sh` (backup and automatic rollback).
- One GPU queue for everything: priorities, aging, same-model grouping, two
  slots per model, leases for agent turns.
- Model API for other apps in three formats (Ollama, OpenAI, Anthropic) with
  per-app keys, LAN guard and CORS.
- The assistant: web search and fetch, files, a sandbox for code, images
  (see, generate, edit, cut out, OCR), artifacts, memory, skills, subagents,
  task lists, MCP servers, compaction, cloud escalation where allowed.
- Projects with revision history and SLQ Sync (works with SLQSync.exe).
- Libraries: search documents by text and by what their pages show.
- Web UI: chats, projects, files, libraries, settings, the GPU plate and
  queue; responsive and installable.
