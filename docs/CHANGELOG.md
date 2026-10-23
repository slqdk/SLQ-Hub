# Changelog

## 0.13.5 - 2026-10-24

- **Music in one piece.** In the first real music chat the assistant made a
  "guitar solo" with the sound-effect model and a band with the music model,
  then mixed them - two pieces with their own key, tempo and beat, which
  cannot fit together. It now knows that one music call makes the whole
  arrangement, with the solo described in its prompt. Speech or an effect
  over music still works, and the assistant now has an ffmpeg command for
  that mix that works (Ornith needed three tries and a hand-over).
- When it mixes, it keeps the parts, so the mix can be changed without
  making them again.
- Pictures and sound files no longer carry ComfyUI's prompt and workflow
  inside (an MP3 had the whole graph as a tag, and ffmpeg copied it into
  the mix).
- ffmpeg and ffprobe in the sandbox leave out their banner: 2 KB of build
  flags on every call, in front of the actual error.
- The troubleshooting export now includes the files the chat's turns made
  or changed - a song, a mix, a script - as they are in the folder now,
  within the 60 MB.

## 0.13.4 - 2026-10-24

- **Full-quality speech only.** In the second listening test Røst v3 Turbo
  said wrong words with 8-bit weights even when only its MLP layers were in
  8 bits. Speed is worth nothing if the words are wrong, so the **Speech
  speed** setting is gone: every voice model runs with its full weights and
  its own decoder. A saved speed setting is simply dropped.
- **Røst v3 Turbo first for Danish.** It sounded best in the listening test
  and is the quicker one (about 4 seconds of work per second of speech on the
  VM, Røst v3 about 6). With both downloaded and no choice made in Settings →
  Assistant → Sound, Danish goes to Røst v3 Turbo; Røst v3 is still there to
  choose.
- Chatterbox Multilingual V3 is for the other languages: its Danish keeps an
  English accent, which is how the model speaks, not a setting.
- `selftest --speech` speaks one sample per voice model again, and Settings
  lists those.

## 0.13.3 - 2026-10-23

- **After the first listening test.** Røst v3 Turbo at Best sounded best (and
  is 1.5 times faster than Røst v3); every Chatterbox sample sounded like an
  English speaker trying Danish; with every layer in 8 bits Røst v3 Turbo
  skipped a word and Røst v3 slurred its first word. So:
  - **Faster** now puts only the MLP layers in 8 bits. The attention layers,
    which keep the speech in step with the text, stay full - to be heard.
  - **Fastest** is every layer in 8 bits (and Turbo's decoder for Røst v3 and
    Chatterbox), and Settings says what the test heard.
  - The texts no longer say Røst v3 sounds best or that Turbo is three times
    faster, and they say Chatterbox's Danish has an English accent (Danish
    goes to Røst).
- Speech that would clip is turned down as a whole before it is encoded.

## 0.13.2 - 2026-10-23

- **Listen to the speech samples in Settings.** Settings → Assistant → Sound
  now plays the last speech self-test's samples - the same sentence and seed
  with each voice model in each speed setting - with what each cost. No more
  copying files off the VM.
- **Speech speed, reordered by what the VM measured.** On the Ryzen 9 5950X
  the token part is two thirds of the time, and 8-bit weights make it 2.3-2.5
  times faster (Røst v3 Turbo then makes its tokens faster than real time);
  Turbo's decoder only shortens the remaining third. So **Faster** is now
  8-bit weights with each model's own decoder, and **Fastest** adds Turbo's
  decoder for Røst v3 and Chatterbox. One sentence on the VM: Røst v3 5.75
  seconds of work per second of speech at Best, 2.28 at Fastest; Røst v3
  Turbo 3.75 and 1.82.
- The 8-bit weights get a scale per output channel: as fast, and closer to the
  full weights.
- Chatterbox stops by itself again with Resemble's repetition penalty (79
  tokens in both full-weight runs); it ran to its limit once with 8-bit
  weights, which the self-test warns about.

## 0.13.1 - 2026-10-23

- **Faster speech, your choice.** The first self-test on the VM measured Røst
  v3 at about six times slower than real time (3.4 s of speech in 20 s).
  Settings → Assistant → Sound → **Speech speed**:
  - **Best**: as before.
  - **Faster**: Røst v3 and Chatterbox use Turbo's two-step decoder (it comes
    with Røst v3 Turbo) - the part that turns speech tokens into sound, a
    quarter of the work.
  - **Fastest**: also 8-bit weights for the token part, every voice model.

  `selftest --speech` now speaks the same sentence in every setting, shows
  where the time goes (the tokens and the decoder), times the token part on
  half the threads too, and saves each sample in `/srv/slq-hub/data/selftest/`
  so you can listen before you choose.
- Chatterbox Multilingual V3 uses Resemble's own repetition penalty (1.2, not
  Røst's 2.0): in the self-test it most likely talked on to its length limit.
  When a piece runs to the limit, the result and the self-test now say so.
- Like upstream, the Røst v3 and Chatterbox output no longer ends in the last
  token's 40 ms of noise.
- Settings → System shows the speech service's CPU.

## 0.13.0 - 2026-10-23

- **Speech, music and sound effects.** The assistant makes sound itself: an
  announcement read aloud, a song with your lyrics, a horn or a crowd. Each is
  an audio file in the chat's folder, played in the card under the answer.
  Download the models under Settings → Models → Sound, music and speech:
  - **Speech** runs on the server's CPU in a new container, so the chat model
    stays on the card and a sentence costs seconds instead of a model swap.
    Røst v3 speaks Danish - the CoRal project's voices Mic and Nic, or any
    voice from a 10-20 s clip you give it; Røst v3 Turbo is about three times
    faster; Chatterbox Multilingual V3 speaks 23 languages and can sound
    livelier.
  - **Songs**: ACE-Step 1.5 (XL Turbo, or the lighter Turbo) sings your
    lyrics, or plays an instrumental, up to five minutes.
  - **Sound effects**: Stable Audio 3 Medium - effects, ambience, loops and
    instrumental pieces.

  Settings → Assistant → Sound chooses the model for each, and `selftest
  --speech` speaks a sentence with each voice model and says how fast. In a
  person's chat ComfyUI keeps no copy of the sound and the speech service
  keeps nothing of her voice. From chat 12, where an announcement had to go
  through a cloud voice (edge-tts) in bash.
- Settings → System and `update.sh status` show the speech service.
- An update makes the folders the services mount when a new version adds
  one.
- Fixed: for the first five minutes after the VM started, people's chats
  could get no code (the Landlock check was skipped while the clock since boot
  read under five minutes).

## 0.12.0 - 2026-10-23

- **The files an answer made, in a card under it.** Whatever the assistant
  made or changed in the working folder - with a tool or a command such as
  ffmpeg - is listed under its answer: name, folder, size, new or changed.
  Sounds and videos play right in the card, and pictures show. Open shows the
  file in the side panel (a PDF opens in the browser's viewer), Download
  saves it, and Add to chat puts it in your next message. People get the same
  card, read from their device. From chat 12, where an MP3 was only "in this
  chat's folder".
- The Files panel plays sound and video and shows PDFs.
- The model is told about the card: it names the file instead of pasting it,
  and removes scratch files it does not mean to hand over.

## 0.11.2 - 2026-10-23

- **Long chats keep their place on the card.** llama-server is started with
  `--no-cache-idle-slots`. By default it moves every idle conversation into
  its RAM cache, and clears it from the card, whenever another request
  starts. A conversation too big for that cache (4 GB for Qwen3.8 27B,
  reached at about 20K tokens) was simply dropped. In chat 11 the new chat's
  title, made on the other slot, threw away a 30K-token conversation, so its
  next message would have read it all again: about half a minute. Idle
  conversations now stay on the card until the room is needed. A model's own
  extra flags can still say `--cache-idle-slots`.

## 0.11.1 - 2026-10-23

- **The assistant knows the time of day.** Each of your messages reaches the
  model with the local time it was sent (`[Fri 23 Oct 2026 06:57]`). It goes
  in the message, not the system prompt, so the cached prompt still works
  from turn to turn. From chat 10: asked about electricity prices "for the
  next 24 hours" at 06:57, the model only knew the date and had to read the
  time off a web page.
- The short notes between tool calls are in your language too (chat 10 had
  one in English).

## 0.11.0 - 2026-10-23

- **People: others in the household get the Hub's chat with their own
  login.** Settings -> People -> Add a person: their name (what the assistant
  calls them), a user name and a password, where the login works - at home
  (the LAN and WireGuard) or at home and away (also through hub.slq.dk) -
  and whether they may run code. New password, Sign out everywhere and Remove
  are in the list.
- **The same chat as yours**: the model, the picture model, thinking levels
  (Think hard with Qwen3.8), web search, pictures made and edited by asking
  for them, attachments and photos, artifacts in the side panel, the files
  the assistant makes, subagents, and the skills you have switched on
  (read-only). No Settings, libraries, projects, memory, MCP servers or
  cloud, and their sign-in does not open the model API (apps use keys).
  They can change their own password.
- **Their chats live on their own device.** Chats, pictures, files and
  artifacts are kept in the browser (IndexedDB). Each message takes the chat
  and its files to the Hub, which answers in a temporary folder; once the
  device has the answer, the Hub deletes the folder, the run and the
  pictures, and ComfyUI keeps no copy either. Chats do not sync between
  devices: Export all chats and Import chats (the account menu) move them in
  one file. Clearing the browser's data deletes them.
- **Code for people, confined.** With "May run code" on, bash runs only in
  the answer's temporary folder (Linux Landlock): it cannot see your files,
  other chats, `/tmp` or `/dev/shm`, connects only to web ports, and nothing
  it starts outlives the command. Without Landlock in the sandbox, people get
  no code; the self-test says which ("sandbox for people").
- **The queue** shows their jobs with their name and no chat title. They see
  yours as "Another job", and can stop only their own.
- ComfyUI's input and output folders are now mounted in the Hub (`/comfy`),
  so the copies of people's pictures can be deleted there; the self-test
  checks them ("comfyui folders"). The update recreates the containers.

## 0.10.1 - 2026-10-23

- **Phone chat page: a clear way back from Picture.** When Picture is on,
  the button becomes a highlighted "Picture ×" chip, labelled even on the
  smallest phones. Tapping it goes back to chat, and the start text says so.
  It was a plain toggle before, marked only by its colour.
- **Phone chat page: thinking levels.** With Qwen3.8 27B the Think button
  becomes a choice: No thinking, Think a little, Think or Think hard (sent as
  `reasoning_effort`). Models that can only think or not, like Qwen3.6
  35B-A3B and the 9B models, keep the on/off button; their chat templates
  have no levels. A "Think on" saved earlier becomes Think.
- `/v1/models` lists each chat model's thinking levels (`efforts`).

## 0.10.0 - 2026-10-22

- **A stuck model hands over to a stronger one.** Settings -> Assistant ->
  "When the model gets stuck, hand over to" (default Qwen3.8 27B). A turn is
  stuck when the model repeats itself, three tool calls in a row fail, or it
  asks for help (the escalate tool). The turn then goes on with the stronger
  model: the card switches within the turn, and the chat says "Handed over
  from Ornith 1.5 9B to Qwen3.8 27B: ...". In projects with cloud escalation
  the cloud comes after that. With nobody to hand over to, the model answers
  with what it has, as in 0.9.1.
- **Each model's own thinking and sampling.** Settings -> Models -> Settings
  (the dialog that had the server settings). "Thinking in chats": follow the
  chat and Settings, always think first, or never. Sampling with and without
  thinking (temperature, top P, top K, min P, presence penalty), filled in
  from the model card. "Use the model's own sampling" goes back to the card.
- **Add from Hugging Face searches by name.** Type "Ornith 1.5 9B" and pick a
  repository from the list (GGUF only, most downloaded first). owner/name or
  a link still works. Q8_0 is preselected where it fits the card easily; a
  16-bit file says that it is twice the size of Q8_0.
- **Keys that work away from home.** Settings -> Apps and keys: each key works
  "At home" or "At home and away". An away key also works through
  hub.slq.dk, from anywhere. The phone link of an away key uses that address,
  which is HTTPS, so the page can be installed as an app. Turning a key that
  was limited to the home network into an away key removes that limit. The
  other keys stay on the LAN. "Allow the model API from outside the LAN" is
  now "Allow every key from outside the LAN"; like away keys, it works only
  through the proxy.
- **The phone page says why it is refused**: only at home, or not through
  this address, plus the address the Hub sees the phone at. A key opened from
  a link is kept even when the Hub refuses it, so "Try again" works once the
  key is allowed.
- **More in the troubleshooting export.** Each model step records what was
  sent (sampling, thinking, effort, max tokens, how many tools and messages,
  the estimated prompt size) and why it stopped. The system prompt and the
  tools are kept when they change: at the start, at a hand-over, after
  condensing. Each distinct prompt is stored once. The model server's log
  leaves out the vision models' "non-consecutive token position" warnings,
  which hid everything else; they are counted instead.
- **From chat 9:**
  - The assistant answers in the language of your latest message.
  - It makes one picture unless you ask for more.
  - A picture made with another model than the chat's says so in the result.

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
