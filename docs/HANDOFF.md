# Handoff

The state of SLQ Hub for whoever works on it next (a person or Claude).
Read `docs/DESIGN.md` for the decisions; this file says what exists, what
has been checked, and - honestly - what has not.

## State (0.54.0, 2026-10-30)

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
Settings) easy to see on a phone. Chat 24 (below) was the first remake heard:
it did not sound like the song at all, and measured it had another key and
tempo - so 0.20.2 gives a remake without a clip the song's own sound, starts
it from the recording (keep 0.5), checks the song's codes on the card and
says what it found, and tells the assistant how to make the same recording
sound live with effects (item 52). The VM runs 0.20.2 (chat 21's export of
2026-10-25 says so). 0.21.0 adds **Tests** (Settings → Tests, DESIGN §12
"Tests"): 24 hard cases in every area, each run as a real chat through the
agent, the queue (background priority) and the sandbox, checked by the Hub
(the answer) and in the sandbox (what the turn made: files, pictures, sound,
video, hidden tests on its code), with an export for a closer look - the way
to measure the assistant on the card instead of asking chat by chat (items
53-54). It also stops a `write_file` without `append` from replacing a file
the turn is writing in parts: chat 21's last turn did that twice, spent 12 of
its 60 steps rebuilding the game and stopped at the step limit unfinished
(below). 0.22.0 marks such an answer "Not finished" (step limit or Stop) in
its files card or on its own, with Continue, which tells the model why it
stopped and goes on (DESIGN §6 "Not finished, and Continue"). The first test
run on the card (0.21.0, below) passed every case before video; the video ran
out of memory while decoding, four times after 10-13 minutes each, then ComfyUI
held the card and every chat model load after it failed - the last seven
cases with it. 0.22.1 decodes video in tiles, clears the card before a chat
model loads (and loads it once more when it did not fit), cancels in ComfyUI
a job the Hub gives up on, waits for a busy ComfyUI instead of failing, does
not hand a turn over for a failure of the card, and runs the video cases last
(DESIGN §3, §6, §8, §12; item 56). The second run (0.22.1, Qwen3.8 27B,
below) had no errors: 19 passed, 3 in part, 2 failed - the video from a
description came out right in tiles, but the one from a picture came out as
flat colours after a 14B job the Hub had given up on (item 57). 0.22.2 follows
a job by asking when ComfyUI's websocket closes (no more keepalive pings; a
job ComfyUI no longer has ends at once), gives edits and cut-outs back at the
original's size, and makes the checks judge results rather than the way to
them (DESIGN §3, §7, §12). ComfyUI's log of that run shows the 14B job going
on for almost five minutes after the Hub gave it up, loading its second
expert with both staged in RAM (item 51); 0.22.3 keeps the video model to
Settings → Assistant → Video unless the user names one. 0.23.0 brings
**skills** (DESIGN §6 "Skills"): everyone makes skills with the assistant from
their own files (PDF, Word, Excel, PowerPoint) - the built-in skill maker
writes the skill in Claude's format and shows a card with **Add skill** -,
imports them from Claude and downloads them for Claude, keeps them private or
shares them with the family, and chooses them per chat (the Skills button by
the message box, or "/" and a name). A Skills page sits in the sidebar by
Projects; Settings → Skills is gone. A chat now uses only the skills chosen
for it and its project - before, the owner's switched-on skills were offered
in every chat (item 58). 0.24.0 shows **what the card's work costs** (DESIGN
§3 "Energy and cost"): the card's own power reading (`power1_average`, read
by the supervisor; 337 W on VM 222 while it built code), split between the
jobs on the card, at a fixed price (3.00 kr per kWh, Settings → Usage) -
under each answer, in a chat's ⋯ menu, on a project's page and per chat
there, and in Settings → Usage per person, for the owner, per app key, for
the tests and for the card while idle, with tokens read and written and the
time on the card, per day (a limit for the children can build on it). Not
the PC at the socket: a "rest of the PC" setting in watts adds that if known
(item 59). 0.24.1 makes the queue dialog's running and waiting times count up
(they stayed at the queue's last change: "running 0 ms" in Søren's screenshot
of a working turn). 0.25.0 adds **Wan 2.2 5B Turbo** (DESIGN §8 "Video"): the
5B in 4 steps at cfg 1 through a 332 MB LoRA on its own files - 4 model
passes instead of 40 for a clip that took 15.5 minutes - as the default video
model once downloaded, with "the best quality" handing a clip to the 5B's 20
steps (item 60). 0.26.0 makes **`update.sh` wait until nobody uses the Hub**
(DESIGN §12 "Waiting for a free Hub"): it builds while the Hub serves, asks
the Hub what is going on and stops it only after a quiet minute; the waiting
and installing run in a session of their own while the terminal shows them,
so Ctrl+C or a closed window cannot cut an update off (`update.sh cancel`
stops one that still builds or waits).
It also **counts tokens as the model server does** (DESIGN §6 "The real
token count"): chat 74's hex dumps cost 1.38 tokens for each one the
estimate counted, so it was never condensed, its answer was cut at the end
of the context and the next request was refused. And the **context per
chat** is a setting beside the context (Settings → Models → Settings; DESIGN
§4): chat 74 ran with Søren's 128,000 and still had the card's 65,536 per
chat (items 61-63). The VM runs 0.26.0 (Søren's screenshots, 2026-10-25
18:17: the new Context per chat field, and "trained for up to 262,144" read
from the real GGUF header). 0.27.0 is about **long chats** (DESIGN §6 "A lean
context", "chat_history", "Continue in a new chat"): condensing writes
structured notes that replace the old ones and that the user can read and
correct (the ⋯ menu, Notes); older turns' tool output is kept short and a
long turn's old results are cleared at half the context; the assistant can
search the whole chat (`chat_history`), also in people's chats; Continue in a
new chat starts a fresh chat from notes, with the same files and settings,
offered under a long chat's last answer; and the main agent is told to give
big reads to a subagent. It also adds **Delete files** for downloaded
built-in models (DESIGN §4; Søren's disk had 28 GB free of 518, and the two
14B video models take 62 GB) and closes two older holes an independent review
found: links planted in the workspace by code in the sandbox were followed by
the Hub (the skills cleanup could empty `/data`; a chat folder turned into a
link let read_file reach the Hub's settings) - DESIGN §11 (items 64-65). 0.27.1 follows chat 76 (below): a remake
"rock instead of pop, the voices as they are" sounded like the original at
keep 0.75-0.85 and lost its rhythm at 0.45-0.5 (measured); the remake tool
now says so, and how to keep the vocals and change only the band (stems, a
remake of the accompaniment, the vocals mixed back) - item 66. 0.28.0 follows
Søren's try of that (chat 76 again, below): the new band was exactly in time,
but the model's own mix put the vocals 11 dB under it and the band had
changed little. `music_remake` now keeps the song's own vocals in one call
(`vocals: "original"`: separation, a remake of the band only, the vocals
added back at their level, the band saved too), every remake measures
whether it kept the song's tempo, key and beat (`SLQBeatCheck`, a new node in
the ComfyUI image), and keep defaults to 0.6 - item 67. 0.28.1: Settings → Assistant →
Video named Wan 2.2 14B picture to video for first-to-last videos after Søren
had deleted it; the help text and the video tool's description now follow
what is downloaded (no first-to-last video without the 14B picture to video). 0.28.2: live thinking (Søren's phone, chat 78: "the text is spooling backwards
when the screen is full") showed the last 1500 characters from their start in
a 340-px box, so the front was cut off token by token and the newest text was
below the box; it now follows the end like a terminal (`ThinkingText`, whole
lines, the box pinned to its end until the reader scrolls up). The dev server's
fake model now streams for real (`StreamingASGITransport`), so this can be
watched there ("long thinking"). 0.28.3: on a phone the "Working with ..." line's name
and figures wrapped side by side, and 28 px of room sat above the composer;
the figures now go under the name below 860 px, and the room is 10 px
(Chromium, 11 checks at 360, 412 and 1440 px). 0.28.4 follows chat 78 (the first
Continue in a new chat on the VM, below): the request hovered at the 75%
budget and the Hub trimmed a little at every step, which a hybrid model
(Qwen3.8 27B) pays for by reading almost everything again; older steps'
thinking is now cleared with old tool output, cuts go down to 60%, a cut
stays as it is, tiny condensing is skipped, and the notes give paths and
mark guesses - item 68. 0.29.0 answers Søren's "what is missing compared with
your own way of working?" and his chat 79 (below). The assistant **sees web
pages and games** with `web_app` (DESIGN §6 "Web pages in a browser"):
Chromium's headless shell with Playwright in the sandbox image, the page's
folder served at `http://page.local/` (its own `fetch()`es work, offline
too), clicks, keys held as in a game, JavaScript evaluated, and back come
screenshots, the console, errors with file and line, failed requests - for
artifacts too, run as the side panel runs them. The base prompt gets four
working rules (documentation and existing tools first, a notes file in long
work, check it the way the user will use it, say what was not checked). The
files card has **Download all** (one ZIP) and Files a ZIP button for the whole
folder (DESIGN §7). Opening the first artifact this way showed that **every
React artifact failed to compile** in the panel (the bundled Babel 8 dropped
two options the panel passed) - fixed. And the file tools refuse text with the
Hub's own "[shortened by N characters]" marks in it, which a 9B model had
copied into chat 79's index.html. An independent review (two passes) found
ten problems in the first cut - among them a race that could have put the
Hub's database in a ZIP, and pageshot hanging on a page that never finishes -
all fixed and tested. Item 69 lists what only the VM can show.
0.30.0 does items 5 and 6 of that list. A **thinking budget**: one step
thinks at most Settings → Assistant's "Most thinking in one step" (8,000
tokens to begin with), then the Hub ends the thinking through llama-server's
control route and the model acts on what it has; **Stop thinking** beside the
live thinking does the same at once (DESIGN §6 "A thinking budget, and Stop
thinking"). The Hub counts the budget itself: the server's own budget,
measured, forces its message into an answer that contains `<think>`. And an
**independent review**: a turn that made or changed code, pages or documents
(files or artifacts) is checked before it ends by another model - the
hand-over model by default - in a fresh context, with tools that only look,
run and open; with problems the model that did the work gets the report,
fenced as the reviewer's words, and goes on - once a turn, with at least 12
more steps (DESIGN §6 "An independent review"). Two code reviews by a
separate agent found ten problems each - among them condensing that took the
review for the user's latest message, test runs reviewed by another model,
the server's budget re-arming inside answers, and the loop guard refusing
the fix's re-reading of what it had read before - all fixed and tested.
Item 70 lists what only the VM can show.
0.30.1 comes from Søren's two Settings → Tests runs (below): the cut-out case
judged the newest picture, and Qwen3.8 27B's newest was a preview of its
correct cut-out on a chessboard; it now judges the newest picture with
transparency at the original size.
0.30.2 comes from Søren's chat 126 (the TURBO model on the game, below): no
review after an answer to what the user wrote while the turn worked (his
"kan du holde en pause?"), and the notes of Continue in a new chat give paths
relative to the folder, only files that exist.
0.31.0 does what Søren asked after chat 126 went 54 steps without a notes
file: the Hub writes the chat's notes into **NOTES.md** in the folder
(condensing, Continue in a new chat, corrected notes) unless the user or the
assistant keeps that file, reminds the model to write it after 15 steps of
work, and points the system prompt at it; and a chat outside a project can be
**moved into a project** from its ⋯ menu - its files merged into the
project's folder without overwriting anything (DESIGN §6 "NOTES.md", §7 "Move
to a project"). Item 71 lists what only the VM can show.
0.32.0 makes the independent review end with a report every time: chat
126's reviewer used all 24 steps in 33 minutes and gave none. It has a time
limit now too (Settings → Assistant → "Time for the check", 15 minutes),
is told near its end - 3 steps before its 24, or at 80 % of its time - to
stop checking and report, and its last step has no tools; its prompt keeps
it on what the turn made and the request rather than redoing the work, and
NOTES.md is no longer reviewed (DESIGN §6 "A review always ends with a
report"). The export lists what the reviewer did. From Søren's chat 128:
`bash` no longer runs a command with the Hub's "[shortened by ...]" mark in
it (DESIGN §6, 0.29.0's note), and his question whether three failed calls
in a row is too few before a hand-over is answered in DESIGN §6
"Hand-over" (it stays three). A code review by a separate agent found four
defects, fixed. Item 72 lists what only the VM can show.
0.32.1 comes from Søren: after an edit, "change it" again without the
picture attached edited his original once more. In a chat outside a project
an edit was shown but not saved, and its result named no file - the
attachment was the only picture the model could name. Made and edited
pictures are now saved in plain chats too (`images/...`, under the answer)
and the result says a further change starts from that file; `image_edit`'s
text says so, and to put the scene first when combining (DESIGN §8
"Outputs"). Item 73.
0.33.0 does three things Søren asked for on the same day: the Files panel
shows when each file changed and can sort the newest first (DESIGN §7); the
base prompt has a fifth rule, keeping the folder in order (folders by
purpose, the user's files left where they are, scratch files in `.hub/tmp/`,
DESIGN §6 "How it works"); and a local hand-over becomes the chat's model, so
the next message does not switch the card back to the model that got stuck
(DESIGN §6 "Hand-over"). Item 74.
0.34.0, from Søren's look at CM98's page: the project's total left out a chat
moved into it (2.04 kr for the project, 2.72 kr for that one chat) - a moved
chat now takes its usage rows along, and chats moved before are settled once
at start (DESIGN §3 "Counting it"); each chat on the project's page shows its
answers, models, time, energy, tokens and how its last turn ended (§3 "Where
it shows"); and the folder rule names `docs/` for documentation. Item 75.
0.35.0, from chat 135 (CM98 "exactly like the original", three screenshots):
`web_app` compares the page with a picture of the original - how much matches,
where it differs most with the colours on each side, and the two side by side
with the differences in red - whole or one part (`compare_area`), and the base
prompt has a sixth rule: the user's picture is the reference (never an
image_generate one), measure it in one script, build at its size, compare, and
leave the logic alone when the task is the look (DESIGN §6 "Web pages in a
browser", "How it works"). Søren got a measured kit for CM98 outside the repo.
Item 76.
0.35.1, from chat 136: the sixth rule goes on - a reference already in the
folder is used as it is and the result is not left below it. Item 76.
0.35.2, from chat 138: `web_app`'s comparison opens its result (clearing kept
only the start), and an attachment saved under another name because an older
file had its name says so to the model (`context.renamed`). Item 76.
0.36.0, for TwinCAT (Søren, chat 139: skills for TwinCAT 3 and Motion, and a way
to have the work verified): two generic MCP additions. A tool argument with
`"format": "slq-hub-file"` names files in the working folder; the Hub reads
them (inside the folder - a link only when it stays inside; each file once,
at most 1,000 files, 20 MB a file, 50 MB a call) and sends them in
`params._meta["slq-hub/files"]`, so a server on another machine gets the code
without the model copying it into the call; a server that asks first lists the
files on its approval card. And the independent review may use the tools that
say they only look (`readOnlyHint`) of trusted servers the owner lets it use
(the server's new switch, off by default): a build on the user's PC is how PLC
code gets checked (DESIGN §6 "MCP tools and the working folder", "An
independent review"). An independent review of the first version found that
such tools were refused when repeated (a build after a file changed on the PC
would have been refused), that every trusted read-only tool reached the review
(a memory server's too) and that the docs promised "no links": fixed before
the release. Nothing TwinCAT-specific is in the repo: the skills (twincat3-plc
with its checker and file tools, twincat3-motion) and the TwinCAT build server
(an MCP server for the Windows PC with XAE 4026) were delivered to Søren outside
it. Item 77.
0.37.0, from Søren the same morning ("how can I ask the assistant to give the
job to the cloud?" - only the model could, by `escalate`, and from Fable that
reached Qwen3.8 Standard first): **Continue in the cloud** under the last answer
and the **cloud model in the chat's model menu**, both where a hand-over to the
cloud is allowed. Such a turn never takes the card (DESIGN §6 "The cloud from a
turn's start"). Item 78.
0.38.0, from Søren's evening messages: **Effort - Quick / Think / Extra**
replaces the thinking chip, the two thinking switches and the review switch
(Extra is the only level with the review; Think has half the thinking
budget); chat 145 (79 minutes over two turns, 26 of them in reviews, two
single steps of 6 minutes thinking at the budget) was the measurement. A
person's video that failed to reach her phone is no longer deleted on the
Hub before it arrives, and files up to 100 MB reach a person's device. Item
79.
0.39.0: the optional **Windows container** (dockur/windows, profile
`windows`) on the sandbox's network and firewall rules, for the TwinCAT build
server on the Hub's own VM - Søren wants everything on the Ubuntu VM. VM 222
got +100 GB (617 GB, `ubuntu-lv` grown) and has `/dev/kvm` (CPU type host, 12
`svm`). Item 80. On 2026-10-28 Søren decided not to go on with the Windows VM:
the container stays behind its profile, off, and nothing on VM 222 uses it.
0.40.0, from Søren's look at project 4's Files (the assistant's `src/`,
`NOTES.md` and `FB_MC2_AxisControl/` beside his TwinCAT solution, and all of it
synced to his PC): a new project has a **synced folder** - named when it is
made, the name filled from the project's - that is the PC's folder, and
**hub/** beside it for the assistant's notes, working files, made media, the
attachments (`hub/attachments/`) and uploads to no folder; it never reaches the
PC. The mapping is Hub-side (`sync_routes.py`), so SLQSync.exe is unchanged;
older projects (no `sync_folder`, or ".") sync as a whole as before. Søren's
answers: uploads and attachments both to hub/, the name chosen at creation,
always on for new projects; no migration of project 4 - he makes a new
project. DESIGN §7 "The synced folder". Item 81.
0.41.0, Søren: "inddele skills i kategorier.. både private og delte" - he
will make many. A skill's owner puts it into a category (`.categories.json`
in the owner's folder of skills, beside `.shared.json`/`.disabled.json`), so a
shared skill shows in its owner's category for everyone; the Skills page, the
composer's Skills menu and a project's settings list by category (the Hub's
own under "SLQ Hub", the rest under "No category"), the page with a search,
folding and renaming a category for all one's skills in it. Seen in a browser
here (dev server); not yet on the VM.
0.42.0, from Søren's phone screenshot of chat 158 (rows of "bash curl -s
https://www.linkedin.co..."): a finished step's row shows what came of it
(`toolOutcome` in `lib/turns.ts`, from the stored result: bash's last line of
output or why it failed, a fetch's page and size, a search's count, the file
tools' own first line), red when it failed; bash, web_app and windows_app take
an `about` the chat shows at the head of the row (`tools/base.py` ABOUT; bash
requires it, so llama-server's grammar should make Qwen write it - not seen on
the card yet). Old chats get the outcome line too; the about only where the
model gave one.
0.43.0, Søren: question cards "ligesom du har, men kun hvis der er
deciderede valgmuligheder", and fixed skills ("Billede med Juletema": a fixed
instruction plus questions with options, the user uploads a picture without
text). `ask_user` (tools/ask.py) waits on `Run.ask_questions` (30 minutes; a
message written meanwhile answers it: `Run.queue`); `/runs/{id}/answer` and
the device turn's `/answer`. A skill's ```questions block (skills.py
`parse_questions`, not inside another fence) is shown by the composer as a
card; answers travel as an `answers` block (context.py renders it for the
model; people's devices may send it). Seen in a browser on the dev server;
not on the VM - whether Qwen uses ask_user only for real options is the thing
to watch (export a chat where it asks too much or not at all).
0.44.0: a skill's `metadata.category` places it on import (`Skills._category_from`, only when its owner has not
placed it), and the composer's question card scrolls past 46% of the screen. Made with it, outside the repo: four
picture skills for Søren, in English as the app is (picture-animals/-christmas/-halloween/-easter, category
Pictures; the Danish billede-med-* drafts replaced): seven questions each,
image_edit with qwen-image-2.1 on an attached picture, else image_generate with the chat's model (not Chroma1-HD).
0.45.0, Søren: categories are folders, closed until their name is clicked (composer menu, Skills page, project
settings; `useFolders` in `lib/skills.ts`, remembered per browser), skills in none first; his category "internal" is
listed last and opens on three clicks within 1.5 s of each other, never remembered. Seen on the dev server.
0.45.1, Søren: the Skills menu closes as soon as a skill is picked.
0.46.0, Søren on a working chat ("Det giver ikke super meget mening.. og den røde kommer jævnligt. Og der står
altid 0ms"): runs of thinking and calls fold into one closed block (Turn.tsx `units()`/`WorkBlock`, `workSummary` in
turns.ts) that names what the steps did, red only when the last step failed; while the turn works, the step under
way stays outside it - the thinking (with Stop thinking), or the running call headed by `thoughtGist` of the thought
before it (its last sentence of intent, "Let me ...", else its first). A finished thought is headed by its gist.
Times under 1 s are hidden: file tools take ~1 ms and `round(..., 2)` made it 0.0 - the "0 ms". edit_file
(tools/files.py): read_file's line numbers are taken out of old_string (and new_string); else a line-wise match
with whitespace ignored, used only where it is the one place, with new_string re-indented through the table of
old's indentations to the file's (`_fit_indent`; `_step` converts deeper levels, e.g. four spaces to a tab); else
the error shows the most similar lines (`_closest`, difflib, 0.6 at least). A CRLF file stays CRLF (`read_text`
had translated it, and every edit wrote it back with LF). Seen on the dev server (scenario "work steps", phone
width). NOT seen: why Søren's edits failed - the screenshot did not show the calls (item 82).
0.47.0, Søren: "internal mappen skal ALTID kun være tilgænglig for admin". In skills.py: the owner's skills in a
category named internal (`is_internal`, any case) are left out of a person's `visible()` and refused by `may_see()`
- so the list, the skill's text, files and .zip, the skill tool and a device turn's chosen skills all lose them,
shared before or not. Into internal unshares (`set_category`, `rename_category`, a skill's own metadata category);
`set_shared` refuses while it is there; the entry says `admin_only` and `shared: false`, and the Skills page shows
"Only you - internal is never shared" without the switch. A person cannot name a category internal (400), and one
named so before 0.47.0 reads as no category. Tested here (unit and API, the phone side included); the admin's
Skills page seen on the dev server.
0.48.0: `Skills.install_all` - a .zip whose SKILL.md files lie in folders side by side (none inside another's
folder; `_skill_folders`) installs each of them; POST /hub/api/skills answers `refs`/`names` as well, and the Skills
page's toast names them. Made for the set of 18 skills Søren asked for ("Lav alle"), delivered as one zip. Tested
here (unit and API).
0.48.1, Søren's screenshot: SLQ Sync 2.65.0 against https://hub.slq.dk answered "401 sign in with a sync device
token" on whoami (Test and save) and on projects (Link) - while the Link dialog's project list (loaded with the saved
token when the settings window opened) showed project #7. The Hub refuses only when no bearer arrives or its token
is not in `sync_tokens` (removed from a project's Sync page; nothing else deletes one), so the cause was NOT seen.
`gate._sync_refusal` now names which (no token / not slqsync_ / unknown token, each under 64 characters for the
tray's window) and logs it with the address. Asked Søren to add the device again, and if that fails, to read the new
message after the update.
0.49.0, Søren: LoRAs for pictures, "LoRa is Admin only! also for using". `app/images/loras.py` keeps the list
(`data/loras.json`; files in `models/comfy/loras/hub/<id>.safetensors`, ComfyUI's `hub/<id>.safetensors`), reads a
file's header (must be a LoRA; `guess_base` from tensor names and metadata - img_mlp → Qwen-Image, lora_te1_ → SDXL,
noise_refiner → Z-Image, double/single blocks → FLUX.2 klein, which FLUX.1 LoRAs also look like) and resolves a
call's LoRAs for the model family actually used. `workflows.with_loras` puts LoraLoaderModelOnly (LoraLoader for SDXL
checkpoints, text encoders too) after the model's loader and rewires its users. Routes `/hub/api/loras` (owner only;
not in MEMBER_ROUTES). The chat setting `image_loras` (not a device setting) and the tools' `loras` parameter exist
only in the owner's turns. Tested here against the fake ComfyUI and on the dev server (Settings, the picture menu).
NOT seen on the card (item 83).
0.50.0, Søren: "Og så skal træningen også bygges ind" - LoRA training, admin only like the LoRAs. `app/training/`:
a run is `data/training/<id>/` (run.json, images/ with a caption .txt beside each JPEG - ai-toolkit's layout -
output/). Pictures are turned upright, at most 2048 px, refused under 256 px. Captions: the chat model when it can see
(through the queue, owner "training", one picture per call, thinking off; empty answers are counted and named) or
typed; the trigger word is put first. Training: the Hub fetches the base into Hugging Face's cache layout in
`models/hf` (the trainer is on `inner`, offline like the other model containers) - only what ai-toolkit reads,
checked in its source at b69b983 (2026-10-27): `flux-2-klein-base-4b.safetensors` from black-forest-labs/
FLUX.2-klein-base-4B (not its diffusers folders, which would double it), `ae.safetensors` from ai-toolkit/flux2_vae,
all of Qwen/Qwen3-4B (the text encoder, a class attribute in `flux2_klein_model.py`, not a config key). Then the
residency "train" (GpuBackend clears the card; nothing of ours loads) as a background job with `meta.cancel`, so
the queue's Stop works too. `services/trainer/traind.py` (stdlib HTTP on 7300) runs `python run.py
<dir>/config.json` and parses tqdm's bar for step, ETA and loss; a cancel is SIGINT, SIGTERM, SIGKILL, and the job
says "stopping" until ai-toolkit has exited - the Hub keeps the card until then. A job the trainer no longer knows
(it restarted) or a minute without an answer fails the run with the reason. After a Hub restart a leftover job is
told to stop first, then the Hub holds the card until the trainer is idle.
0.50.1, Søren's screenshot: 12 pictures with captions, "The trainer is not running". My 0.50.0 note said "add the
profile, run update.sh" - but with nothing new to fetch the updater stopped at "Already up to date". Now
`start_new_services` (in that branch) builds and starts services in `compose config --services` that have no
container in `compose ps -a --services` (made folders first). Whether Compose takes COMPOSE_PROFILES from
`--env-file` for `config`/`build`/`up` without the service named is NOT verified on the VM (naming it -
`update.sh compose up -d --build trainer` - starts it either way). The updater's busy check names a training and
counts a base-model download.
0.51.0, Søren ("ja gør det" to a Duplicate button, after his first real run started: "Rikke", 35 pictures, the base
download showing 7.2 GiB - the klein-base single file is the same size as the distilled one's): `Training.duplicate`
copies a run's JPEGs and captions (not output/, not ai-toolkit's caches) into a new run with the same steps and rank,
named "<name> v2/v3...", with a new trigger word replacing the old one in the captions as a word (`loras.has_word`,
now also used by `set_captions` and `with_triggers`: the substring test found "lora" in "explorative"). Read in
ai-toolkit's dataloader_mixins.py: `images/_latent_cache/<stem>_<md5>.safetensors` is keyed by file name and crop,
not content (`_t_e_cache` hashes the caption), so `remove_image` and `add_images` drop a name's cache files - before,
a new picture under a removed one's name trained on the old latents. The editor closes while the Duplicate dialog
is open (one modal at a time; Escape closed both). Tested here and on the dev server.
0.51.1, the first real training ("Rikke", 35 pictures, rank 16) failed twice at the first sample: torch OOM in the
FLUX.2 VAE decoder (`toolkit/models/v2/vae/flux2_kl.py`), "Tried to allocate 4.50 GiB ... 23.98 GiB of which 4.01 GiB
is free ... 8.82 GiB allocated by PyTorch" - after "Free the GPU". Measured with a loop every 5 s on the VM (sysfs
`mem_info_vram_used` against each process's `/sys/class/kfd/kfd/proc/<pid>/vram_*`): only ComfyUI (0.1 GB) and
`run.py` on the card; while the text encoder loaded the card matched run.py (15.4 / 15.2 GB); then run.py fell to
3.4 GB but the card stayed at 15.5, and with the transformer back (8.8 GB) the card held 15.6 - about 6.7 GB in use
that no process accounted for, until run.py died (card 0.2 GB). So nothing else held the card (the Hub's clearing
logged nothing: the card was empty at the start); memory freed inside ai-toolkit was not given back. The image sets
`PYTORCH_HIP_ALLOC_CONF=expandable_segments:True` (my addition, not ai-toolkit's); with expandable segments PyTorch
maps and unmaps physical memory (HIP's VMM) - the likeliest place for it to stay mapped. `traind.toolkit_env` now
leaves it out (the Dockerfile line stays, so the built layers are reused; `TRAINER_ALLOC_CONF` sets one). NOT yet
known whether that was it: the next run with the same loop tells (the card should fall with run.py). If not: keep
the text encoder on the card (no `cache_text_embeddings`), or sample at 768 px.
0.52.0, measured on the card with 0.51.1 (the same loop): the card now follows run.py - 19.0 → 11.5 → 9.5 GB for
run.py with the card at 19.2 → 11.8 → 9.8 (ComfyUI 0.2) - so expandable segments WERE the cause. The baseline samples
were made and training started: "Step 1 of 1500 · 1 h 49 min left" (about 4.4 s a step; tqdm's first estimate),
loss 0.776, the card at about 12 GB while it trains. Also seen: the page showed "Step 35 of 35" while ai-toolkit
cached 35 pictures - traind took every tqdm bar for the training's. Now only the bar with the job's name counts
(ai-toolkit: `ToolkitProgressBar(desc=self.job.name)` in BaseSDTrainProcess); the caching and sampling bars are
dropped. Søren asked for the advice I had given in the chat to be in the interface ("ellers ka jeg ikke huske det"):
the training editor has "Which step?" and "Make it better" (a guide panel under the info line, `GuideText` in
`Training.tsx`) and a "?" at Steps and Rank - click, not hover, so they work on a phone. 0.52.1 (asked for after
"is it only the face?"): the picture mix and caption advice per kind (`PICTURES`, `CAPTIONS`, `PICTURE_HINT`), for a
person with a check against the café sample; the links further down scroll to the guide.
0.53.0, Søren's update on 2026-10-30 "hung" ten minutes at "Backing up the data..." - the backup was 10.3 GB
(normally under a minute). Measured on the VM: disk 617 GB, 478 used, 109 free; `/srv/slq-hub`: models 281 GB,
backups 23 GB, data 13 GB of which training 11 GB - three trainings overnight (rikke, rosanna, stine), output/
4.9 + 2.8 + 2.8 GB, pictures 100-206 MB each; Docker: images 132 GB (36 reclaimable), build cache 35 GB (all
reclaimable). Now `hub_update.BACKUP_EXCLUDE_RE` leaves out `training/<id>/output` and `training/<id>/images/_*`
(ai-toolkit's caches), and `restore_data` keeps `training/` (RESTORE_KEEP), extracting the backed-up pictures,
captions and run.json over it - a rollback never deletes checkpoints. `Training.free_space` (route `/free`, the
"Free <size>" button) deletes a run's *.safetensors, optimizer.pt and caches; the samples stay (without Add as
LoRA). optimizer.pt is deleted when a run ends. NOT measured: how much of each run's output was optimizer.pt and
how much checkpoints - `ls -lh` of one output folder would say.
0.53.1, Søren's screenshot of a 3000-step training (trigger "waterx55"): the portrait and café samples looked like
the person at 2250-3000, the watercolour showed her at 2250 and 2750 but a lake or field at 2500 and 3000, with
scribbled "signatures". Read as: the prompt "waterx55 as a watercolour painting" tips between two pictures (same
seed at every step), "water" in the trigger pulls towards water, the digits get written as text. `triggerWarning`
in Training.tsx (a short list of known words, and digits) warns in New training and Duplicate; "Which step?" has
a bullet on it. NOT tested: whether a letters-only trigger fixes the watercolour (a v2 would show it).
0.54.0, Søren: "can I get a slider when I choose it" - the chat setting `image_lora_strength` (id -> 0..2; at the
LoRA's own strength nothing is saved) and `LoraStrength` in Composer.tsx, saved on release; `tools.images._loras`
passes it on, a strength the call names wins. The slider row is at most the menu's narrowest width - the menu is
placed on the screen when it opens, and a wider row pushed it off a phone's edge. Also asked: downloading others'
LoRAs. `loras.flux_size` tells klein 4B (hidden 3072, 5 double / 20 single blocks), klein 9B (4096, 8 / 24) and
FLUX.1 (3072, 19 / 38) apart by block numbers and lora_A widths - sizes read in ai-toolkit's flux2/src/model.py;
a 9B LoRA is refused at upload, a FLUX.1-shaped one gets no family (the admin says - Chroma perhaps). NOT checked
against real downloaded LoRAs: their key names may differ (BFL's double_blocks, diffusers' transformer_blocks and
kohya's underscores are all matched). The config keys were checked against the same commit (`diffusion_trainer`,
arch `flux2_klein_4b`, `cache_text_embeddings` so the 8 GB text encoder leaves the card, `timestep_type`
"weighted" as ai-toolkit's UI default for klein; no quantisation, AdamW - quanto and bitsandbytes are the least
sure on AMD). The image follows ai-toolkit's own AMD install (`manager/spec.py`: torch 2.13.0 / torchvision 0.28.0
/ torchaudio 2.11.0 from the rocm7.1 index, held by a constraint while its requirements go in) - not ComfyUI's
torch 2.14.1+rocm7.14. Checkpoints `<id>_000000250.safetensors` (the last `<id>.safetensors`) and samples
`samples/<time>__<step>_<n>.jpg`, as in BaseSDTrainProcess. Tested here against a fake trainer, traind against a
fake run.py, and the UI on the dev server (captions, start, progress, stop, Add as LoRA). NOT built or run on the
card (item 84).

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
| Energy and cost (0.24.0) | 14 tests (`tests/test_usage.py`): the supervisor's reading from a fake sysfs (Søren's values: `power1_average` 337 W, cap 339 W, no `power1_input`; a card with only `power1_input`; none); the meter's split (one job, two jobs sharing, a finished job's run and seconds taken once, the rest of the PC added, a fresh reading for the interval just ended, idle apart and handed on without the rest of the PC, a waiting job's model load, no reading counts nothing, a hung reading at most 30 s); an answer's energy and cost (3 kr per kWh) in its run, the day's row (answer, tokens read 9 = 12 - 3 from the cache, written 7), Settings → Usage's rows and the card's reading now, the queue log's energy; a project's total and each chat's (not in the sidebar's list), kept when the chat is deleted; the price and the rest of the PC as settings (a string number, a refusal, not below zero; the next answer at 2.50 kr); an app's four calls (OpenAI plain and streamed, Ollama, Anthropic) under its key with their tokens; a person's answer (its usage on her device, her row, a removed person's row kept, her 403 on the page, `who_total` for a limit); idle and loading rows; who each kind of job counts for. In Chromium against the dev server (24 checks; its fake card draws 280 W, "slow" answers take 5 s): under an answer "0.4 Wh (0.1 øre)", the chat's ⋯ menu total, a project's line and its chat's cost, Settings → Usage after People (the card's draw and limit, you with two answers, the total row, Today, the price saved and shown), at 390 px each one a block with every figure named and nothing off the side, a person's answer and chat total (after a reload, from her device), her 403, her row on the owner's page, the help's two items (hers without Who used how much). |
| Wan 2.2 5B Turbo (0.25.0) | 2 tests (`tests/test_video.py`) and the catalog test: the entry first in the list, its files the 5B's and the LoRA (with the 5B there only the LoRA queues), its licences; it is the default, from a description and from a picture - `LoraLoaderModelOnly` (the LoRA at strength 1) between the `UNETLoader` and `ModelSamplingSD3` (shift 5), `KSampler` 4 steps, cfg 1, uni_pc_bh2, simple - and says its name; full quality makes the 5B's own graph (20 steps, cfg 5, uni_pc, shift 8, no LoRA); the owner's choice of the 5B stands; the tool's texts. ComfyUI v0.42.3 on CPU with the tiny 5B stand-in and a stand-in LoRA in the formats a LoRA extracted from a full fine-tune carries (lora_down/lora_up with alpha on every 2-D weight, `.diff` on the 1-D, modulation and 5-D weights, `.diff_b` on the biases, all under `diffusion_model.`): both graphs accepted and run to an MP4 (25 frames, 64×64, 24 fps), no "lora key not loaded" in ComfyUI's log, and the frames differ from the same graph without the LoRA (applied, not only accepted). Not seen: Kijai's real file (the shell here cannot reach Hugging Face). |
| Waiting for a free Hub (0.26.0) | 14 tests (`tests/test_hub_update.py`): the Hub's answer; a Hub before 0.26.0 (401 or 404) and one cut off mid-answer left to the card's own load (`gpu_busy_percent`, the busiest card, 10 % or more), a 403 said once, `cancel` while it asks still cancels; the quiet minute (a fake clock: busy, free, busy again starts it over, what it waits for said when it changes); with nobody using the Hub it waits its quiet time and updates with SIGTERM, SIGHUP and SIGINT ignored, `--now` does not wait; in a terminal it builds, then hands the rest to an updater in its own session (resume file, flags, `--now` passed on, the log) and shows its log and exit code, one that never takes over puts the code back; handed over in a terminal it goes to its own session before the build; Ctrl+C only stops showing the log; the background updater waits where it is (the lock: phase, pid, boot, old commit, written whole); cancelled while building or waiting, or any error before the install, puts code and tags back with the signals ignored during the undo; one update at a time, `cancel` while building or waiting, not while installing; a lock counts only for a running `tools/hub_update.py` in the same boot; a cut-off update put back by the next one before the install, or stopped with what to do after it (`--force` goes on); the old updater's hand-over waits through a Ctrl+C. 6 tests (`tests/test_update_wait.py`): only the internal token from the VM gets an answer (through the proxy 403); a job on the card and one waiting for it, a model being loaded, a chat between steps or waiting for an approval (said once when its job is on the card), a turn whose task died (not waited for), a person's chat and her device fetching the answer for two minutes, a test run, a library reading a document, downloads only while something downloads them. 1 test (`tests/test_downloads.py`, fails on 0.25.0): Cancel on the file being downloaded lets the next model's download go on, and the cancelled model downloads again when asked. An independent review (a separate agent, the whole diff, with its own rehearsals) found ten problems - Ctrl+C during a handed-over wait killing the new updater halfway through its undo (0.25.0's `subprocess.call`), an updater killed while waiting leaving the clone ahead of the running Hub ("Already up to date"), a stranded queued download keeping the wait going for ever, a raised context per chat refused by the still-running server and refitted to the same size, Ctrl+C live during the install, lock gaps (no handler yet, the undo cut short, a pid used again, a lock written in parts), a forced answer losing its instruction in a refit, the summary request's room dropping to nothing, an emptied Slots field saved as 0, CPU library indexing not seen - all fixed and tested. A rehearsal with real processes (a git origin with 0.25.0 and this updater as 0.26.0, fake `docker` and `systemctl`, a stand-in Hub answering busy, `script` for a terminal): 0.25.0's updater fast-forwarded and handed over; the new one went to its own session before the build and the terminal showed its log; Ctrl+C to the terminal's processes left the old updater with 130 and the new one saying "No longer shown here", while the background updater (parent init, no terminal) kept waiting; killed with SIGKILL, `status` said "cut off", and the next `update.sh` put the code back at 0.25.0, handed over again and finished at 0.26.0 after a quiet minute (backup, start, health, firewall), all shown in the terminal; `cancel` while waiting put back the clone and the five images' tags, shown in the terminal with exit 1; a plain update to 0.26.1 built in the window, Ctrl+C there put the code back, and run again it finished. |
| The real token count (0.26.0) | 5 tests (`tests/test_agent.py`) against the fake llama-server, which now counts like the real one (`usage.prompt_tokens` from a given ratio, hex dumps costlier) and refuses a request longer than its context in llama-server's words: ten dense tool results at 1.6 real tokens per estimated - every request fits, from the second step on each with `token_ratio` 1.6; three hex dumps in one step - the next request refused, a `refit` step with the real size, sent again shortened with the newest dump whole; a chat whose last step measured 2.0 - condensed before its first request is sent, not refused first; Settings saying 30,000 while the server has 14,000 - one refusal, then every request fits the server's size; a summary that leaves no room - not extended, the history left alone. Each test fails with its part of the fix taken out. Chat 74's export: ratios 0.89-0.91 on its first steps (prose), up to 1.38 on its last (hex dumps); its last step's 45,004 estimated ×1.342 measured one step before is 60,395 - over the 49,152 at which it condenses. 3 tests (`tests/test_models_context.py`): the context per chat set, kept in the overrides, sent to llama-server as `--kv-unified-per-slot` (none when it is the whole context), told to apps (`/api/show`), refused above the context or below 4,096, back to the card's when emptied, capped by a smaller context and by a slot's share without a unified cache; zero slots refused, null back to the card's; `context_length` read from GGUF headers (uint32 and uint64, missing, not a GGUF). In Chromium (8 checks): the field with the card's value as placeholder, what one chat may use, saved and shown again, too much refused with the reason, emptied, the phone width. |
| Real hardware, chat 76 (2026-10-25) | Søren's export, 0.26.0: "Re-make this song with a rock theme instead of the pop theme. Don't change the voices" with Backstreet Boys' "I Want It That Way" (214 s), then "a more heavy version", "the original rhythm but heavy metal". Four remakes (ACE-Step 1.5 XL Turbo, its own sound, codes 82.7-83.9 % in agreement - each with the warning), two models (Qwen3.8 27B, then Qwen3.5 9B uncensored). Measured here against the song (mono 22 kHz, 64-band mel spectra, onset strength): keep 0.75 "Rock" and 0.85 "Heavy Metal" - spectra 0.970 and 0.976 alike, onsets correlated 0.66 and 0.68 at lag 0 and locked in every 30-s window, the same tempo; keep 0.5 "Hard Rock" and 0.45 "Heavy Metal 2" - onsets 0.055 and 0.024, unlocked, another tempo (about 152 against 65 by the onset autocorrelation). Søren heard exactly that: the original twice, the rhythm way off once (the fourth not yet commented). Also: the 27B thought itself into a loop recalling the lyrics before it searched them; the tool refused the attachment's bare name, so each turn copied it out and deleted it again (one turn failed on the deleted copy); and every answer described guitars and drums it could not hear. The mix recipe ran here on the remake and the song with ffmpeg 6.1.1 (the sandbox's): 213.7 s in, 213.7 s out. Hence 0.27.1. |
| Chat 76's fixes (0.27.1) | 1 test (`tests/test_voices.py`): an attachment found by its name in `.hub/attachments/`, keep written as given (0.75), the result saying the model cannot hear it, the description's measured keep and the vocals-and-band recipe, a missing file still refused. |
| Real hardware, chat 76 again (2026-10-25) | Søren's second export, still 0.26.0, with the message he was given: "split the song with audio_separate, remake only the music as heavy metal without lyrics at keep 0.7 and 0.6, lay the original vocals over each with ffmpeg". Qwen3.5 9B uncensored did it in 15 steps (the attachment's bare name refused once, so it copied the file out; one ffmpeg command refused for `normalize=0.9`), mixed with `[0:a]volume=1.2[music];[1:a]volume=0.3[vocal];[music][vocal]amix=inputs=2:duration=first:normalize=true` and deleted the two backings. "It did not work." Measured here, the vocal stem least-squares taken out of each mix: the vocals in it at a gain of 0.143-0.144 (-16.9 dB), the new band 6.3-6.5 dB under the original accompaniment - the vocals 10.8-11.1 dB under the band where they sing, against -0.4 dB in the song. The new band in time: 97 BPM and F# minor like the song (librosa), 98-100 % of its beats within 70 ms of the accompaniment's, the onsets best aligned at lag 0 (one 23-ms frame) in 13 of 14 15-s windows (the 14th barely better elsewhere). Its long-term spectrum 1.1-1.4 dB (rms over 1/3-octave bands) from the accompaniment's - as far as the 0.75 and 0.85 remakes - "a lot like the original", "almost exactly like the original", Søren said - moved from the song (1.1-1.4 dB; 0.45-0.5: 2.0-2.4 dB) - and no more sustained 1-5 kHz energy (distorted guitars would add it); what that sounds like was not heard here. The first export's four, measured with the new beat check's code: 0.75 and 0.85 97 BPM, F# minor, 91 % on the song's beats; 0.5 and 0.45 about 150 BPM, F# major and C# major, 24-25 % (chance 23 %). The cliff lies between 0.5 and 0.6. Hence 0.28.0. |
| Remakes (0.28.0) | 3 new tests and 3 changed (`tests/test_voices.py`): the band-only graph (the song separated, its accompaniment encoded and its own sound the timbre, `[Instrumental]` for lyrics given anyway, the new band and the original vocals through `AudioMerge` at unity, the mix and the band saved), both files in the working folder and shown, the result (the separation named, keep 0.6, the beat check that follows, is lost - "the vocals will not sit on this band" - or holds only in part); the option offered, and the system prompt's line given, only with the separation model, refused without it before anything is queued, a wrong value refused; `SLQBeatCheck` in every remake graph (the song, or its accompaniment, against the result); keep 0.6 by default; the graph builder (a clip for the band's sound, Opus for both files); `MIX_RECIPE` run with ffmpeg 6.1.1 - 44.1-kHz vocals over a 48-kHz band: as long as the band, nothing clipping, the vocals within 1 dB of their own level (chat 76's command: 17 dB down). The beat check's analysis (`rhythm.py`) run here with librosa 1.0.0, the image's pin, on chat 76's files: 91-100 % on the beat for the six in time, 24-25 % for the two that were not, the song against itself 100 %, about 5 s a pair on this CPU. On the CPU ComfyUI v0.42.3 with the 0.18.0 stand-ins (text encoder replaced as before): the band-only remake of the 8-s song ran to its two files (separation, remake, AudioMerge; 48 kHz stereo, 8.04 s each) with the beat check's JSON in the job's history, and a whole remake with its beat check. Not on the card: the real models in the band-only remake (it is chat 76's stems way in one graph, so the beat should hold at 0.6), how the band sounds, the beat check on ROCm's ComfyUI, and whether bpm and key or a style clip let the sound move further. |
| Real hardware, chat 78 (2026-10-25) | Søren's export, 0.28.0, the first Continue in a new chat on the VM (from chat 74, Championship Manager 97/98's DOS database to a browser game): the notes were written in 54 s from 40,690 tokens (1,053 tokens of notes, the headings as designed), the new chat started at 12,619 tokens. Then "Du bestemmer" - one turn, still running at the export after 61 minutes and 34 steps. Qwen3.5 9B: 12 steps in 3.5 min, the first failing because the notes named the files without their folder (`.hub/attachments/`), three failing on the same hex-dump bug, so the turn went to Qwen3.8 27B (Adv): 21 steps in 52.7 min on the card - 45.9 min writing (69,360 tokens, nearly all thinking; one step 13,354 tokens, 10 minutes) and 6.4 min reading, 5.3 of them re-reading after changes to older messages: 48,503 tokens from scratch after a condensing that folded only the new chat's notes (59,457 tokens estimated before, 60,522 after), then 33-36,000 tokens at each of steps 30-33 (cached 12,432 each time: the end of the system prompt and tools). Replaying steps 16-33 here with the real token ratios reproduced the request sizes within 3% and showed the cause: the budget trims ran at every step from 30 on, and re-cutting changed an older answer's count (33 to 32). The work itself moved on - header layout, a reference database found on GitHub (baskeyx/cm9798), 215-byte player records, the field types - no step repeated another, except the 9B's three tries of one command and one dump of record 1 labelled record 2. Hence 0.28.4. |
| Thinking turns and condensing (0.28.4) | 3 tests: cutting again changes nothing (answers, call arguments with quotes and Danish letters, unreadable arguments) unless it cuts deeper, the newest two steps keep their thinking (`tests/test_long_chats.py`); a 16-step thinking turn on a 40,000-token context - older messages change only at the clearings, each freeing at least 15%, at least three steps apart, the newest two steps' thinking kept; a turn with almost nothing before it is trimmed, never condensed (`tests/test_agent.py`). Both turn tests fail with their part taken out (all thinking kept: changes at two steps in a row and no clearing; no threshold: a condensing request). The replay of chat 78 (scratch tooling, the Hub's own `to_model_messages` and trims): older messages changed at 4 of steps 17-33 instead of 5, and no longer at every step from 30; about 134,000 tokens read instead of 177,000. Not on the card: the real model's answers with fewer steps' thinking kept. |
| Real hardware, chat 79 (2026-10-25) | Søren's export, 0.28.4: "Kan du gøre det til et browserspil? (3)" - Qwen3.5 9B (Uncensored, aggressive) with Qwen3.8 27B, 50 steps in 21 min, four files (`index.html`, `style.css`, `data.js`, `game.js`). Its only test: `python3 -m http.server 8080 &` in the sandbox and `ls`. Played here with pageshot (offline under `confine.py`, 412x860): the home screen loads; `data.js` line 78 has `'Japhet N'Doram'` (a name with an apostrophe in a single-quoted string, from the model's own generator script), so the whole data file fails ("Unexpected identifier 'Doram' at data.js:78:33") and "Vælg Hold" and "Se Spillere" throw `TEAMS is not defined` / `TOP_PLAYERS is not defined`; the heading reads "Brow... [shortened by 24 characters]" - in the model's own `write_file` call (message 1806, at character 367, not at any cut the Hub makes), so copied, not cut by the Hub. Only one such call in seven exports (chats 21-79). Søren asked for the project as a ZIP. |
| Web pages in a browser (0.29.0) | 26 tests (`tests/test_web_app.py`). The Hub's side against the fake sandbox: offered only where `/health` says `browser`, not in a person's chat; the prompt's pointer; the request (the page's folder as root, or `root`; steps, screen, timeout, out, online) and the report (steps, failed steps, screenshots, errors with stacks, console with counts, failed requests, dialogs, the page's text for a model without vision); every refusal before the sandbox (not a page, missing, outside, a root that does not hold it, both or neither target, bad steps, a bad timeout); an artifact written as the panel builds it with its runtime, `allow-popups` only while the internet is allowed, the offline policy while asking; asking first runs offline, and where code cannot be kept offline the question shows the JavaScript; nothing left in `.hub/web`, old runs swept. pageshot itself: what it serves (links out of the folder, a folder's index.html that leads out, `%2e%2e`, a NUL), names escaped, key names, bad requests answered before the browser starts. In a real Chromium (Playwright 1.63, headless shell 1243): a game that fetches its level, a held key that repeats, an uncaught error with its stack, a script that does not parse with file, line and column, a 404, the console with lines, an alert; offline the web is blocked and named; a sandboxed page has no storage (as the panel); a page that never answers and an eval that never settles give what came before with the step; a long wait cut to the time left; "spil #1.html". `artifact_doc` equals `artifactDoc` run with node for six pages, with and without the offline policy. The self-test's web line (pass, typing, fetch, errors, misses). |
| ZIP downloads (0.29.0) | 4 tests (`tests/test_zip.py`): a chat folder (subfolders kept; `.hub`, `node_modules`, `*.bak` and a link out left out; named after the chat), an answer's files (their common folder taken off), a file gone since left out, none there a 404, outside a 400, a project's folder named after the project (also from its chat), the `check=true` preflight; and the race: after the listing a file swapped for a link to a stand-in database, a folder for a link, a file for a FIFO, the chat folder itself for another chat's, a file that grew - none followed, nothing hangs, no more than listed is read. |
| Trim marks in files (0.29.0) | 2 tests (`tests/test_long_chats.py`): `write_file`, `edit_file` and `artifact` refuse chat 79's mark and a cleared-result mark, a file that has the mark already may keep it; every mark the Hub makes (`cut_text`, `shorten_result` with every reason found in the code, `clip`) is caught, and look-alikes ("[12 characters - letters; digits]") are not. |
| React artifacts (0.29.0) | Found by opening one with pageshot: "[BABEL] /artifact.tsx: @babel/preset-typescript: The .allExtensions and .isTSX options have been removed" with the bundled @babel/standalone 8.0.7 - the same in node. With the options gone, TSX compiles (from the `.tsx` name) in node and in Chromium. |
| Web pages, artifacts, ZIPs in the UI (0.29.0) | In Chromium against the dev server with a real sandbox daemon (`execd.py` here, its `/health` saying `browser: true`) and pageshot laid out as in the image (`/opt/pageshot`, `/opt/ms-playwright`, `/usr/local/bin/pageshot`), 24 checks at 1440 and 412 px: a React artifact compiles in the panel and counts clicks; `web_app` opens it through the daemon ('value: "Snow 2"'); "web game" writes a game and plays it (the level fetched, the paddle moved by a held key, no errors, two screenshots, "game (3 steps)" on the row); Download all on the card's head line gives the answer's three files without their folder; Files' ZIP button gives the whole folder without `.hub`. The self-test's probe page through the real pageshot, online and offline: PASS. |
| Independent review (0.29.0) | A separate agent read the diff and ran its own scripts, twice. First pass, ten findings: the ZIP followed a link swapped in after the listing (the stand-in database ended up in the ZIP; `/dev/urandom` grew the file without end), pageshot hung on an endless page, artifacts lacked the panel's offline policy, the guard missed `clip`'s mark and refused look-alikes, a folder's index.html could lead out, a NUL in an address ended the run, the listing ran on the event loop, `"30s"` left files behind, `#` and `%` in names, the question hid the JavaScript. Second pass: all held; three new - a long wait taken for a hang, odd `steps` could end a turn in the question, the chat folder itself swapped for a link - fixed. |
| NOTES.md, Move to a project (0.31.0) | 13 tests (`tests/test_notes_file.py`): the Hub's copy written, written again, left alone once changed (by hand, as `notes.md`) and by another chat of a project, written over by the chat that continues it, a link in its place replaced and its target untouched, a FIFO not read, a working folder that is a link left alone, cleared notes taking the chat's own copy away; Continue in a new chat writes NOTES.md into the new folder and the next turn's prompt points at it, a NOTES.md the model keeps is copied and left alone (and corrected notes do not touch it); condensing and corrected notes write it, the setting turns it off; a 17-step turn gets one reminder at step 15 (the message, the event, the notice), none when it wrote the file first, none in a test run or with the setting off; condensing at the turn's start does not stop the step-15 reminder; a chat moved into a new project (files, attachments and outputs, a link as a link, an executable's mode, an empty folder; not skills or a FIFO; the old folder in the trash; checked in; the next prompt names the project and the old folder); nothing overwritten (409 before anything is copied, the same content counted as there, a name alike but for case refused, a non-UTF-8 name refused); an attachment by the same name renamed and the message pointed at it; refused: no project named, unknown, archived, a link in the way, working, a test case's chat; while a chat moves no message, retry, edit, Continue, notes or Files panel; a failed copy puts the folder back and removes the new project; a name taken between plan and copy is not replaced and what the move made goes again. Mutations of the guards caught. In Chromium against the dev server (23 checks, 1440 and 412 px; "long work"): the reminder live and after a reload, NOTES.md among the answer's files, Move to a project in the menu, the dialog (a new project named after the chat) within the width, the chat in the project with the project's files shown, no Move afterwards, the setting, the help. |
| Code review (0.31.0) | A separate agent read the diff and ran its own scripts: the first cut's move could lose files written into the chat's folder while it moved (a Files-panel save, verified) or by a turn that started while the size was measured (verified), could follow a link swapped in after its checks to write outside the workspace (verified by simulation), and two moves into one project at once kept only one `index.html` (verified); a condensing at a turn's start kept the step-15 reminder away (verified), a non-UTF-8 name half-moved and broke the project's history (verified), links, modes and empty folders were lost (verified), another project chat wrote over the Hub's notes, notes were written through a working folder swapped for a link (verified), a hand-over dropped the reminder's notice (verified), and retry, edit, Continue and notes did not wait for a move. The move was rewritten (`app/projects/move.py`: renamed away first, folder handles with O_NOFOLLOW, hard links that never replace, the sync lock, the trash) and the rest fixed, each with a test. Left: the reminder is not stored, so with thinking off the next turn's prompt cache ends there. |
| Review wrap-up (0.32.0) | 9 new tests in `tests/test_review.py` (24 in all) and one in `tests/test_long_chats.py`: a reviewer that keeps reading is told at step 3 of 5 "you have 3 steps left" (once), its fifth request has no tools and the Hub's last-step message, and its answer counts (problems, `wrapped: steps`, `limit: 5 steps`, the work's model told it may not have checked everything); the prompt states "5 steps and about 15 minutes" and "Do not do the work again"; the run's steps keep `wrap_up` records and the reviewer's calls with `call`, `result` and the review's id, the main turn's tool steps none; the events nest under the review; the export lists its steps and the wrap-up; the chat view's runs leave `call`/`result` out. Nothing given when asked: `failed`, "it gave no report when the Hub asked for it". Time on a turned clock (Settings value "10" as the form saves it): told at 8.4 minutes, last step at 10.5 (`wrapped: time`); half an hour waiting for the user's answer to a question does not count; time up and the report cut at the length limit: one last step, its cut report counted, nothing asked for after it. A looping reviewer gets the report message, and a report without VERDICT counts as problems. NOTES.md alone is not reviewed and is left out of the list. Half an emoji in its call: the chat opens, the Markdown and ZIP export and the event stream work ("?"); a page's `<style>` stays in code in the transcript. `bash` refuses a command with the Hub's mark and does not send it to the sandbox. `WrapUp` and the setting's parsing ("inf", "nan", a day at most; 576 s of 600 reads "9 minutes"). Mutations of each part and each fix caught (21). In Chromium against the dev server (15 checks, 1440 and 412 px; "slow review"): the two notices live among the reviewer's 23 reads, problems found and fixed, after a reload "It was asked to finish near its limit of 24 steps", the setting at 15, the help; the 0.30.0 review check again (24 checks). |
| Code review (0.32.0) | A separate agent read the diff and ran its own scripts: a lone surrogate in a reviewer's call, now stored in the run's steps, made the chat fail to open for good (verified: `UnicodeEncodeError` on every `GET /chats/{id}` and the export); the transcript put the reviewer's raw results - a page's `<style>`, `</details>` - into the Markdown (verified with marked); after the time was up a report cut at the length limit got "continue where you stopped" and the last-step message together, a second last step, and only its last part stored (verified); a looping reviewer's report without VERDICT was thrown away while the same at the limits counted (verified); also the steps' size in every chat view, "inf" minutes ending the turn in an error, "10 of your 10 minutes" from rounding, the help naming 15 minutes whatever the setting. All fixed and tested; the ZIP export and the event stream also failed on such a surrogate in the live events (found while testing the first fix; older than 0.32.0), now "?". Left: the model switch counts toward the review's time (8-11 s on VM 222). |
| Pictures in plain chats (0.32.1) | 2 tests (`tests/test_images_chain.py`): an attachment edited in a plain chat is saved as `images/<time>-<prompt>.png` (blue, as the edit gave it), on the card under the answer, the result saying "edit that file"; `image_edit`'s description names the newest file and the scene first; in the next turn the history holds the path, and an edit of it sends ComfyUI the blue picture, not the red original, saved as a second file with the first kept; a picture made in a plain chat is a file too. A person's chat still saves none (the device test's card is unchanged). Mutations caught (4, the person's chat among them). In Chromium against the dev server (6 checks, 1440 and 412 px; "picture", then "darker"): the picture under the answer with its thumbnail, the edit's call naming `images/...-a-red-fox-in-the-snow.png` and its result "edit that file". |
| Files' times, the folder rule, the chat after a hand-over (0.33.0) | Tests: a hand-over's event carries `chat_model`, the chat's settings name the model that took over, the notice says "The chat goes on with Qwen3.8 27B.", and the next message (the default still the fast model) runs on the 27B with no switch; the chip's PATCH takes it back and a later hand-over works again; a Settings → Tests run hands over without keeping the model; a person's hand-over test unchanged; the base prompt's folder rule. In Chromium against the dev server (14 checks, 1440 and 412 px): a project with `alpha.txt`, `game/index.html` and `zeta.txt` written a second apart - each row's time, by name with the folder first, newest first after the button (`game`, `zeta.txt`, `alpha.txt`) with `aria-pressed`, the folder's title "Newest change inside", the order kept after a reload, no sideways scroll on the phone. And (10 checks; "hand it over"): a chat on Qwen3.6 35B-A3B hands over, the chip shows Qwen3.8 27B while the turn still works, the notice says the chat goes on with it, the next message runs on the 27B with no second hand-over, and the chip's menu takes it back (saved). |
| Usage of moved chats, the project's overview (0.34.0) | 3 tests (`tests/test_usage.py`): a chat with two turns moved into a new project - the project's total has both and agrees with the chat's own (cost and energy), the owner's rows without a project are empty, Settings → Usage's owner total unchanged, a turn after the move counts once more, settling finds nothing more; a chat moved as 0.31.0 did (no usage moved, the move's revision between its turns) - settled at start: the two turns before the revision go to the project, the one after stays, once only; a project's chats with a hand-over (models in order), the step limit (`unfinished: limit`), a review (`review: ok`, two answers) and none at all, and the sidebar's list without the new fields. The folder rule's `docs/` in the base-prompt test. In Chromium against the dev server (10 checks, 1440 and 412 px): a project with a handed-over chat ("Qwen3.6 35B-A3B → Qwen3.8 27B", 1 answer, the time, tokens), a reviewed game ("review: problems found") and an empty chat ("No answers yet"), no sideways scroll on the phone. Not seen: the settling of Søren's chat 126 on the VM (its revision's title must match the chat's title). |
| Windows container (0.39.0) | `docker compose config` (Docker 29 here) with a test `.env`: without the profile the services are the six; with `COMPOSE_PROFILES=windows` in the env file the `windows` service appears, on `sbx` at 172.31.250.30, with `/dev/kvm`, `/dev/net/tun`, `NET_ADMIN`, the public resolvers, RDP published and the web viewer on loopback. The firewall script against a fake `iptables`: eight DROP rules for .30 (seven ranges with the LAN, and INPUT) next to the sandbox's. Tests: `ensure_windows_password` writes a random password only with the profile on and none set, and keeps it; the compose and firewall text (`tests/test_hub_update.py`). Not run: the container itself (no KVM here) - item 80. |
| Effort and people's files (0.38.0) | Tests: `Agent.level_of` and `resolve` (`tests/test_thinking.py`): Settings' defaults for plain chats and projects, the chat's and the project's own level, a pre-0.38.0 `thinking` true/false read as think/quick, a message's level; Quick no thinking, Think the model's default level or the one chosen, Extra its highest whatever was chosen; a model that never thinks by default thinks when the chat says Think. Think halves the budget a step (8,000 → 4,000 sent), Extra keeps it. A turn that wrote a file is reviewed at Extra and not at Think, the chat's choice over Settings'. `left_on_device` and the prompt line for files kept on the device, the 100 MB limit (`tests/test_people.py`). The suite moved from `agent.review` to the levels: 494 passed. In Chromium against the dev server (16 checks, 1440 and 412 px): the Effort chip starts at Settings' Quick, its menu has Quick/Think/Extra with what they do and, for a model with levels, how hard Think thinks; Extra chosen and saved as the chat's `level`; Settings → Assistant has the two defaults and no old switches; a project's Effort select; no sideways scroll. Not seen: the person app's fetch-again path and the "cannot play" label on a real iPhone (item 79). |
| The cloud from a turn's start (0.37.0) | 2 tests (`tests/test_agent.py`): Continue in the cloud refused in a plain chat and before anything was said; under a finished turn it stores `{continue, reason: done, cloud: true}`, the turn runs on the fake cloud with no request to the card and no `queued` or `escalated` event, `started` names `openai:big-model`, the cloud gets "# Escalated" and "pressed Continue in the cloud … They do not think the local model will get the work done", no `escalate` tool, a `cloud` step. The cloud as the chat's model: the turn runs in the cloud, no request to the card (no title), "# Cloud model" in the prompt; with the project's switch off the local default answers and the answer's notice says it cannot use the cloud now. Plain Continue's wording unchanged (`tests/test_continue.py`). In Chromium against the dev server (16 checks, 1440 and 412 px; the dev server's fake cloud at cloud.test): no button in a plain chat; in a cloud project the button under the answer, pressed - "Continued in the cloud", the fake cloud's answer, its footer naming `openai:big-model`; the model menu lists "big-model (cloud)" with "your card stays free, no review", chosen and saved as `cloud`, the button gone, the next message answered in the cloud; no sideways scroll. Not seen: a real Anthropic call from the VM (item 78). |
| MCP files and the review's tools (0.36.0) | Tests: the fake MCP server got a `build` tool (`readOnlyHint`, a `files` array with `format: slq-hub-file`); Settings' status shows it as only looking and taking files, and the server's `trusted` and `review`; a call with a file named twice (`src/Axis.st`, `./src/Axis.st`) arrives once in `_meta["slq-hub/files"]` (param, arg, path, name, size, base64 content) with the arguments unchanged; the same call again reaches the server again (not refused as a repeat); a missing file and one outside the folder are the model's errors and never reach the server; the tool's description says the Hub sends the files. `read_files` alone: a file for two arguments goes for each, a link inside the folder is sent as its file (`arg` the link, `path` the file), a link out of the folder is refused, `MAX_COUNT` holds. A server that asks first: the approval event lists the files (`sends`), an add call has none. A tool list with a number for a name, a dict for a description, a blank name and a string: the one usable tool, its description dropped (`tests/test_agent.py`). The reviewer gets the build tool only from a server that is trusted and lets the review use it (not trusted, or the switch off: none), the one working gets all, and the reviewer's prompt mentions such tools only when it has them (`tests/test_review.py`). Mutations caught (11: the repeat refusal back, no dedupe, no count cap, no `arg`, the switch ignored, trust ignored, tools that change things let in, the prompt line always, no `sends`, the tool list unchecked, `review` missing from the status). The whole suite: 488 passed. The TwinCAT build server (outside the repo) in its fake mode against the Hub's own `HttpConnection`: initialize, tools/list (`file_params` and `_only_looks` read its schema), a build with files read by `read_files`, a call without files, a bad file name, a wrong token (401). Not run: the server against a real XAE (item 77); the new switch and the approval card's file list not seen in a browser. |
| Compared with the original (0.35.0) | Tests (`tests/test_web_app.py`): the picture against itself (100%, "No area differs much"); a blue title bar where the original's is red (85-95%, the six worst cells named with "original #aa020f, page #0120da", the cells that match left out; the picture 1936x480 with red only where it differs); a page twice the size (scaled, the note with the screen to use); a page 12 off everywhere (100%, and no red in the picture); `compare_area` parsed from "0,0,640,60", a list, "[0, 0, 640, 60]" and spaces, clipped at the picture's edge, refused for 3 numbers, words, outside the picture and 1x1; a page with the right bar but no photo (under 15% whole, 100% in the bar's area, its strip picture 640x166 one above the other); places in an area at x 100 reported as the original's pixels (20x20 cells); a small area shown 4x. Through the tool with the sandbox faked: the page opened at 640x480, the report under "The last screenshot against .hub/attachments/original.png", three pictures; an area's report; no such picture, `compare_area` without `compare` and "the top" refused before the browser runs. The base prompt's sixth rule. By hand in the real Chromium and fonts here: CM98's squad and player search screens rebuilt part for part from Søren's screenshots and compared with the module (the whole squad screen 75% without the game's photo, its rows of buttons 89-94%, its title bar 82%; the search screen 58%, its photo larger). The first version's difference picture was red almost everywhere - JPEG noise under the 24 limit tinted it - now red only where a pixel counts. Mutations caught (6: the area's offset, the tint, the area dropped, the screen not the picture's, the strip side by side, no zoom). |
| Real hardware, chat 138 (2026-10-27) | Søren's export, 0.35.0 (not yet updated), CM98, 45 minutes, 136 tool calls, the Fable model. The prompt (mine) said to unpack `.hub/attachments/cm98-reference.zip`; the new kit had been saved as `8ff2ac8d-cm98-reference.zip`, because chat 136's first kit had that name, so it unpacked the old kit. It saw that cm98.js, the photos and `.cm-dim` were missing, wrote its own cm98.js and decided the photos could not be had, and never looked at the attached file. It linked cm98.css, put the parts at the document's places and called `compare` (whole 47-52%, title bar 50%, bottom buttons 52% - the kit's own page gets 84% and 94%), and then explained the gap away by the different players. Three clearings cut its results to 400 characters, and its last answer said it could not state its figures. 0.35.2: the comparison first in the result, and the renamed attachment told apart. |
| Real hardware, chat 136 (2026-10-26) | Søren's export, 0.35.0, CM98 with the first kit (cm98.css, squad.html, search.html, docs/cm98-ui.md, the screenshots). 56 minutes, 150 tool calls, Qwen3.8 27B and the Fable model. It unpacked the kit, tidied the folder as asked (the generated picture and debug_test.html deleted, the test scripts in `.hub/tmp/`), called `web_app` with `compare` five times (with `compare_area` twice) and passed a review. But it did not link cm98.css: it wrote its own style.css with the kit's class names and its own measures (Verdana, a 48-pixel title bar at y 4-52, the bottom buttons every 128 pixels), and stopped at 65% for the squad screen and 54% for its title bar, where the kit's own page reaches 77% and 84%. Its condensed summary kept those measures as "decisions". 0.35.1: the rule to use a reference in the folder as it is. Søren's next chat: a new one, with a prompt that links the kit and names the reference page's figures as the bar. |
| Real hardware, chat 135 (2026-10-26) | Søren's export and the CM98 folder: "make the graphics exactly like the original" with three screenshots (squad 640x479 - its top row missing, the player search 640x480, a player's profile scaled to 488x366). The assistant made a picture with image_generate ("Championship Manager 1998 video game screenshot style reference: dark green background...") and kept it in `images/`, sampled single pixels with ImageMagick (18 calls), wrote a NOTES.md table of guessed colours and a Windows-95 grey frame the game does not have, rewrote index.html and style.css, game.js twice and then six edits, left a `debug_test.html`, and spent its last steps on the data; it never set its page beside a screenshot. 0.35.0 is the answer: `compare`, `compare_area` and the sixth working rule. |
| Real hardware, chat 126 moved, chat 128 (2026-10-26) | Søren moved chat 126 into the new project "CM98" on 0.31.0; his export after the move: project 3, `moved_from` `/workspace/chats/126`, the next prompt names the project, the old folder and NOTES.md. Chat 128 in CM98 ("Ret PL-liste fra NOTES", Fable, 42 minutes, 83 calls): its first call was `read_file NOTES.md` (item 71); the review by Standard took 11 steps and 8.4 minutes and found real faults (empty squads shown as "undefined", the positions coarser than claimed), and Fable checked each finding, opened the game with `web_app` and kept NOTES.md; then three failures in a row - a Python script copied from its own shortened call (SyntaxError) and two `write_file`s of the same copy refused - handed the turn to Standard, which wrote the file at once. Fable left `test_data.js` in the project folder (a scratch file). |
| Real hardware, chat 126 (2026-10-26) | Søren's export, 0.29.0: "Kan du gøre det til et browserspil? (5)", continued from chat 80, DavidAU's TURBO 735-882 with thinking. Its notes from chat 80 gave chat 80's folder (`/workspace/chats/80/...`) and two files that were in neither folder (a parser script, a trainers JSON - the Hub copies the whole folder). Then 42 model steps and 41 tool calls (19 bash, 6 read_file, 5 write_file, 5 web_app, 5 edit_file, one failed) in 21.8 min, 26,827 tokens out, 1.24 M of 1.38 M prompt tokens from the cache, 24-29 tokens/s, prompts read at 600-950 tokens/s, the model loaded in 10.3 s. The first 14 steps (3.4 min) analysed the DB1 files again although players_ref.json and teams_ref.json held them parsed. It opened the game with `web_app` on its own, found chat 79's `N'Doram` line and fixed it; it rewrote game.js three times. Søren asked for a pause; its status said tested, no JavaScript errors, Manchester United selected - for a game.js it had not opened since the last rewrite. Opened here (Chromium, offline): it stays on the loading screen (TypeError: no `#matchload` element), data.js holds 10 teams and 102 players, "Manchester United" against the players' "Man Utd", and the League Table and Fixtures stay empty. 0.30.2: no review after an answer to the user's words (it would have sent the work back during his pause); the notes' paths. |
| Real hardware, two test runs (2026-10-26) | Søren's exports of Settings → Tests on 0.29.0, the same 22 cases, thinking on: Qwen3.8 27B (unsloth UD-Q4_K_M, effort low, presence penalty 0) 19 pass / 3 fail in 38 min 38 s; DavidAU's Qwen3.8-27B TURBO Fable Cold Fusion 735-882 (Q4_K_M; no effort sent - its card has no effort list - and presence penalty 1.5, the Qwen3 values for an added model) 18 pass / 3 partial / 1 fail in 27 min 18 s. The TURBO model wrote 26,784 tokens against 52,122 and 31,706 characters of thinking against 99,324, at 29.5 against 31.8 tokens/s, 135 against 203 Wh; the 27B's longest cases were code (6 min for the module with tests, 23,000 characters of thinking). The model's own misses, two each: the 27B a key typo in the JSON case ("stykprik") and the Danish invitation without a bullet list and with an explanation after it; the TURBO model a wrong weekday ("torsdag den 20. november", a Friday) and temperatures down to 14.7 where 15-25 were asked. The TURBO model's poster text and music clipping are the picture and music models' (its prompts were shorter); it left seven attempts in the cut-out folder. The 27B's cut-out "fail" was the check's (0.30.1, measured on its files: all five checks hold). One run each at temperature 1.0: a difference of one or two cases is within chance. |
| Thinking budget, Stop thinking (0.30.0) | 5 tests (`tests/test_thinking.py`) against the fake llama-server, whose control route now answers as b11783's does (yes for any streaming completion with reasoning control, thinking or not; the reasoning ends with the request's message): the request carries `reasoning_control` and the message, not `reasoning_budget_tokens`; a turn at a budget of 20 pieces sends one control call and its thinking ends with the message (`sent.thinking_budget` 20, `thinking_ended: budget`), a thinking that ends sooner sends none however long the answer, no limit sends none, thinking off has no control; Stop thinking ends it (`thinking_ended: stopped`), 404 for a finished run, `ok: false` and nothing sent once the answer is being written (the fake now writes the answer slowly too, and the forced message a piece at a time); a stopped thinking that the message takes past the budget is not ended a second time. Mutations caught: no clearing of `run.thinking` when the answer starts, no check of the user's stop before the budget's call. On the real llama-server b11783 (CPU, a tiny random model with Qwen3's special tokens and the Qwen3.5 chat template, `Engine._model_step` itself with a tool): budgets of 40 and 200 ended there - the budget's pieces, then the forced message (42 pieces in one run, 36-37 in the code review's) - Stop at about 30 pieces at 31 + 42, a stop after the step `false`, 150 tokens without a budget as 146-150 pieces; with a `<think>` pushed into the answer by a logit bias, the server's own `reasoning_budget_tokens` (25 and 8) put its message into the answer 4 and 6 times, the Hub's counting none. |
| Independent review of the work (0.30.0) | 15 tests (`tests/test_review.py`): a turn that wrote a game is reviewed by the hand-over model with only looking tools in a fresh context of two messages (the request, the files with sizes, the answer as claims), the queue names the reviewer while it works and the work's model after; with problems the report goes back fenced and the turn fixes it (one review a turn), stored as a user-role `review` row of the run with nested events and a run step, read by later turns as history, in the export, not taken for the user's message by condensing, and the next turn's token ratio is not the reviewer's; nothing to fix ends the turn; not reviewed: a plain answer, a picture, a file that came while the turn only answered, the setting off, a Settings → Tests run; an artifact reviewed from its source in `.hub/review/` (the prompt names `web_app`); a reviewer that only repeats itself "did not finish" and sends nothing back; files changed or deleted during the review named without blame; a review at the step limit (2 steps) still gets its fix; the fix may read again what was read before the answer; a reviewer whose last answer is empty, or that reaches its step limit, did not finish; only the turn's own files go to the reviewer (all changed files when it ran bash); an artifact's source is not written through a link at the file (replaced) or at `.hub/review` (no source); Retry and Edit skip the review; the parts (verdicts, finished or not, a report that cannot close the fence or speak as the Hub in any case, the request after a Continue, the report still the task when its own turn is rebuilt). Mutations of each fix caught. |
| Thinking and review in the UI (0.30.0) | In Chromium against the dev server (24 checks at 1440 and 412 px): Stop thinking beside the live thinking, the head within the width, it stops at once and says "then stopped"; a budget of 25 pieces ends a long thinking by itself (70 words instead of about 600); the review row live with its nested steps, "found problems", the fix; after a reload its report in the turn, not as the user's bubble, the reviewer's "Checked: ..." line not run into its list; an artifact's review naming it, its reviewer reading `.hub/review/pump-status.html` (on the phone the artifact panel is closed first); the settings, 8,000 by default. |
| Code review (0.30.0) | A separate agent read the diff and ran scripts against b11783 and the tests: ten findings - condensing after a review lost the fix instruction (the review taken for the latest user message), Settings → Tests runs reviewed by another model (its steps in the tested model's figures), a reviewer that looped counted as "problems", any change in the folder blamed on the reviewer and a sync starting a review, the report spliced unquoted into a Hub note, the next turn's token ratio from the reviewer, the server's budget forcing its message into answers that contain `<think>`, Stop races and a fake that answered unlike b11783, the queue naming the reviewer's model after a question, the live report shown twice, `chat_history` missing reviews, a review near the step limit leaving the fix one step - all fixed and tested. A third pass on the fixes, ten more: the loop guard refused the fix's re-reading of what it had read before the answer (three refusals forced an answer, verified), the stored report loaded remote images while the internet asks first, a reviewer ending with an empty answer counted as "problems" with an earlier line as its report, a condensing during the fix rebuilt the report without its instruction, files synced in meanwhile handed to the reviewer as the turn's work, the request after a Continue taken from a message written while the earlier turn worked, a link at `.hub/review/<id>` turning the source's write onto a file of the folder, case-sensitive fence escaping, untested guards (the answer phase, a stop then the budget) - fixed and tested; the new chat's title made by the reviewer's model when the review came last is left as it is (DESIGN). |
| Live thinking (0.28.2), video help (0.28.1) | In Chromium against the dev server, whose fake model now streams for real (the "long thinking" message: 45 lines in about 11 s), at 390 and 1440 px, 16 checks, passed in four runs in a row: the box filled and stayed at its end, every line the same from one look to the next except the last, which only grew; scrolled up with the wheel it stayed there while the text went on, back at the end it followed again; afterwards "Thought for N words", opened from the start; no sideways scroll. The 0.28.1 code failed the following check (the newest text up to 356 px below the box on the phone). An earlier version of the check that scrolled by setting scrollTop failed once in five: a token rendered before the scroll event pulled the box back - scrolling up now stops the following at once (wheel, a finger moving down). The video help in Chromium (8 checks, desktop and phone, the dev server with only Wan 2.2 5B). Not on the real hardware: a touch swipe on Søren's phone. |
| Long chats (0.27.0) | 13 tests (`tests/test_long_chats.py`): a condensed chat looks up what its notes leave out (search ranked, read whole, nothing found, a bad id, no query); the tool offered only when there is more than the model sees; BM25 ranking (both words first, Danish letters, a snippet around the match); a person's turn finds a message her device sent that was left out of the model's context; notes that replace the old ones (given to the condensing request) and that the user corrects (400 above 20,000 characters; read from the next answer on); Continue in a new chat (title "(2)", settings, `continued_from`, the folder copied without `.hub/skills` but with `.hub/outputs`, no copy left over, the handover block, the notes written from the old chat without tools or thinking, the old chat as it was, the next turn's handover text and notes, the old chat searchable); in a project ("New chat (2)", "(3)"), refused when empty or working; the chat's context figures; a message sent while the notes are written is answered after them, retry writes the notes again, options from the browser cannot start a handover; planted links (the next chat's `.hub`, a chat folder, `.hub/skills`) removed and never followed; shortening that stays the same when repeated and keeps its pointer; the last step's results kept whole except as the last resort; subagent guidance for the main agent only. 4 more in `tests/test_agent.py`: older turns' output shortened the same way every turn (the previous turn whole); old results cleared once at half the context and only when it frees 15% (the newest four whole); a server with less context than Settings say believed from its refusal; a summary that leaves no room not extended. 3 tests (`tests/test_models_delete.py`): Delete files keeps the files other downloaded models use (Wan's text encoder, the 2.1 VAE while the other 14B is there), refuses what is in use, and keeps all of Qwen-Image 2.1's files while its Turbo (which requires it) is downloaded. 1 test (`tests/test_downloads.py`). Each new test fails with its part of the fix taken out. An independent review (a separate agent, the whole diff, with probe tests) found 13 problems in the first version - among them a message sent during the notes answered with the notes again, clearing that cut results the model had not read (and the loop guard then refusing the re-read), Delete files ignoring `requires`, retry under the notes losing them, the hint showing in a fresh chat, shortening that changed a shortened result again (losing its pointer and the prompt cache), the copy's destination following a planted link - and, outside the diff, the skills cleanup following `.hub/skills -> /data`; all fixed and tested. In Chromium (17 checks, the dev server's "long chat" makes a 70 %-full answer): the hint under a long chat's last answer, the menu (Continue in a new chat; Notes once there are notes), Notes shown with headings, corrected, saved and stored, Continue from the hint (the "Continued from" mark, the notes streaming in, the title "(2)", in the sidebar, no hint in the fresh chat, the link back to the old chat), the phone width (the hint, Notes), Delete files (asks with the size, Download again, the chat model's files refused). |
| Skills (0.23.0) | 7 tests (`tests/test_skills.py`): the store - who sees what (one's own, the others' shared ones, the Hub's; a person's private skill hidden from the owner and from another person), sharing, only the maker changes or deletes, the old switched-off list read as not shared, a removed person's skills gone; Claude's format - a .zip and a .skill with one top folder, one with SKILL.md at its root, a bare SKILL.md, export and import back, a replace that keeps the sharing, the name and description rules (length, characters, "claude", XML tags, the folder's name); `read_file` on an Excel workbook (every sheet, values and formulas) and a PowerPoint file (slides, a table, notes); only the chosen skills reach the model (the system prompt, the `skill` tool, `.hub/skills/` and its removal, a project's skills); a long skill named instead of put in the prompt; the skill maker's flow (a bad name refused with the reason, then fixed; `include` into `assets/`; the draft's card, text and .zip; Add skill; a replace); a person's skills (made in a device turn, her draft not the owner's to add, shared "by Maria", used in her chat, gone with her). In Chromium against the dev server (58 checks; "make a skill" makes the fake model write a skill and propose it): Skills in the sidebar by Projects; the page's sections; a SKILL.md imported (private), shared, edited and saved, downloaded as a .zip; Use opens a new chat with it chosen; "/" lists the skills that match, Enter chooses one and takes the /word out, a chip's x and the Skills menu take it out again; Make a skill chooses the skill maker and starts the message; the card (name, files, description, Show SKILL.md, its other file), nothing added before Add skill, then among yours, in the composer's list at once, the card after a reload, Use in a new chat; Improve (the skill maker and the skill, the message); Delete; `/settings/skills` opens the page and Settings has no Skills tab; a project's skills saved by ref; at 390 px the page and the composer fit, the / list in view; a person's page (Yours, the family's "By Soren" without switch or Delete), Use, Make a skill in her device chat, Add skill, private until she shares it, then "By Maria" on the owner's page; no page errors, no refused requests. The help checks (47) and the Tests checks (39) passed again. |
| Help (0.20.0) | 4 tests (`tests/test_help.py`): what `/hub/api/help` tells the owner (code, memory, projects; Qwen-Image's three abilities; no sound or video until downloaded, then the 5B's two abilities, speech and music; Windows when the sandbox says so, asked again; the internet asking; the hand-over model, and none when unset) and a person (no code unless allowed - and then only with Landlock in the sandbox; no memory, projects, libraries, Windows or MCP); every tool named in some help item and no item naming a tool that does not exist; every Need in the help a field of the endpoint with its text. In Chromium against the dev server (41 checks), at 1440 and 390 px: the welcome link and the sidebar's Help (marked as the page); the eight sections; Make a picture and video from a description Ready, first-to-last "Needs Wan 2.2 14B picture to video" with where to get it and its Try off, Another voice "Needs Seed-VC"; the search ("karaoke" finds Vocals and music apart; nothing found said so); Open Settings to Settings, Models; Try putting the example in a new chat's message box, and an example with a file saying what to attach; a person's help (her eight sections with Skills for Memory and skills, her chats on the device and no export, no libraries or projects, code not allowed - ask the owner - without Open Settings, Try into a new chat); no sideways scroll, no page errors. |
| The chosen section and tab (0.20.1) | Søren's phone screenshot (Chrome on Android, `hub.slq.dk/help/documents`): the tab strip's grey scrollbar thumb lay over the chosen tab's 2 px underline, and the chosen tab's text was only slightly darker. In Chromium against the dev server: the help's sections as chips - one chosen, filled (background and text both differ from the others), every chip inside the 390 px screen, the section's name heading it, and the rest of the help checks as before (47 at 1440 and 390 px, a person's included); dark mode looked at (the chosen chip inverted). Settings at `/settings/system`, `cloud` and `models`, at 1440 and 390 px: the chosen tab in view (System and Cloud start off-screen on a phone and are scrolled to), tinted, a 3 px line, the strip's `scrollbar-width: none`. Not tried on an Android phone: whether Chrome there hides the strip's scrollbar as the CSS asks (`scrollbar-width: none` and `::-webkit-scrollbar { display: none }`). |
| Real hardware, chat 24 (2026-10-25) | Søren's export, 0.20.0: "Re-make this song as a live concert with a cheering audience and a big hall" with Deep Blue Something's "Breakfast at Tiffany's" (MP3, 4:18) attached; Qwen3.8 27B (Uncensored) fetched the lyrics from Genius and called `music_remake` (keep 0 by default, no clip, a rich 1990s alt-rock live prompt): ACE-Step 1.5 XL Turbo made 259 s in 40 s on the card (8 steps, about 0.6 s each). "It does not sound like the original at all - not even the same genre." Measured here on both files (librosa, 22.05 kHz): key D major → E major, tempo about 112 → 152 BPM, chroma similarity at the same moment 0.779 against 0.766-0.776 for the two songs 20-60 s apart (no relation in time), loudness flat where the song has a quiet 45-s intro and a drop at 2:10, spectral centroid 2,409 → 1,587 Hz. Its timbre encoder had heard silence (no clip). Hence 0.20.2. |
| Remakes (0.20.2) | Tests: without a clip the song's own sound (three 10-s `TrimAudioDuration` parts of its start, middle and end, two `AudioConcat`, `VAEEncodeAudio`, as the timbre - a song under 30 s whole), keep 0.5 by default, "with its own sound" and the codes check in the result, a warning that tells the user when the codes disagree, the live-effects recipe in the description and the system prompt, the recipe run with this ffmpeg (6.1.1, as in the sandbox image) on generated sound - as long as the song, nothing clipping. On the CPU ComfyUI v0.42.3 with the 0.18.0 stand-ins: the remake with a clip, without (the 6-s song whole) and with three parts of an 8-s song (2-s parts - 10-s parts of a 40-s song did not fit this container's 8 GB with the full-size VAE on the CPU): each ran to its file, and the node's check reached the job's history ("agree": 1.0); the check in bfloat16 too (100 %, random and structured latents). The hall's level measured by hand on chat 24's song: about 5 dB under it with `wet=10,volume=2` (inaudible, 31 dB under, with afir's defaults). Not on the card: whether remakes now follow their song, and the check on the real weights. |
| Tests (0.21.0) | 15 tests (`tests/test_evals.py`): the puzzle has exactly one solution (every arrangement tried against the clues as the prompt words them), the fixtures hold clear answers (Q2 region 13.5 % and the best month 32 % ahead of the next, the manual's facts on their pages, the log's three September failures, no tie at the fifth word in either text, ties that sort alike in any culture), every case complete and every sandbox case in `check_lib.py`, reading answers (Danish and English numbers, the SVAR line, language, bullets, JSON), every Hub-side check on a good and a bad answer, the sandbox's checks on real files with this machine's python3, ffmpeg and Tesseract (a .docx and an .xlsx made with python-docx and openpyxl, a cut-out, an "edit" that changed nothing, a tone, a zooming MP4 made from the boat picture), a run through the agent (chats hidden from the list but there by id, `tests` jobs at background priority, a skipped case, thinking off as asked), questions answered as the case allows (web search allowed for the web case, denied for another), the sandbox's checks through the fake sandbox running the real command (code.debug: the fixed module passes 5 + 5 tests; unfixed it fails; run offline in the case's folder), Stop and the time limit, the export (README, summary, results, each case's files and its chat's bundle; the temporary ZIP deleted once sent), deleting a run (chats and folders gone), a run cut short by a restart, and a person refused. End to end against the dev server with the real sandbox daemon (`execd.py` here: Landlock ABI 7, offline commands): a fake model answering each case as a good model would (`tests/dev_evals.py`, real tools) - all 24 cases ran in 31 s; the 17 whose work is real passed (the chart from pandas, rapport.docx and budget.xlsx, roman.py against the hidden tests, the four bugs, the C# word counter built and run with `dotnet run` offline on the hidden text, the artifact, the folder task), and the 7 that got the fake services' pictures, silent sound and test-pattern video failed or passed in part on exactly the checks that should catch that (not blue, no transparency, -91 dB, 3.0 s instead of 25-40, the video's first frame not the picture). The export read through: summary, every case's chat bundle. In Chromium (desktop and phone, 39 checks): the areas and cases, choosing, the live panel, the counts, the results opened with a failed case's crosses, the details with the time breakdown, Export downloading the ZIP, the case's chat marked Test and not in the sidebar, the tag back to Tests, Delete; no sideways scroll, no page errors. |
| Not finished, and Continue (0.22.0) | 3 tests (`tests/test_continue.py`): a turn at the step limit (4 here) keeps a `limit` step, emits `step_limit` and ends with the new note; Continue adds only the continue block, the model is told the step limit and not to start over, a finished turn is not continued (409), the transcript says both; a turn still working is not continued, a stopped one is ("They stopped your last turn"); a person's device turn with a continue block - extra keys dropped, an odd reason and steps made safe. In Chromium against the dev server ("endless": a file each step; 19 checks, desktop and phone): the card's amber bar with "4 files so far" and Continue, the Continued mark (not a message), the turn going on, the old card keeping its note without the button; a turn stopped in its second step marked the same and continued; no sideways scroll, no page errors. |
| Real hardware, first test run (2026-10-25) | Søren's export of Settings → Tests, 0.21.0: Qwen3.6 35B-A3B (Normal), every case, 1 h 0 min. The 16 cases before video passed every check in 10 min (6-94 s each; the chat model at 71-81 tokens/s; 11 s waiting for the card in all): the puzzle, the word problem, JSON, Danish under rules, the CSV in Danish number format, the manual's later page, the log's needle, the Word report, the budget with formulas, the poster (Qwen-Image 2.1, 800×1408: "JUL I HALLEN" and "12. DECEMBER" spelled right - Tesseract read them only from its enlarged and inverted copies), the edit and the cut-out (1 min 13-14 s in the tool), the sign, Danish speech (Røst v3 Turbo: 11.4 s in 47 s on the CPU), music (ACE-Step 1.5 XL Turbo: 30 s in 23 s) and a sound effect (Stable Audio 3 Medium: 6 s in 7 s). Read by hand: the invitation called Friday 20 November "torsdag" (now a check), and two Danish slips ("echte", "indtasting"). Video from a description: the 5B at 1280×704 and 121 frames sampled at 28-41 s a step (20 steps; 10-13.5 minutes a try, the first the slowest), then ran out of memory in `VAEDecode` ("Tried to allocate 18.56 GiB ... 9.30 GiB is free", 4.94 GiB allocated and 8.90 GiB reserved by PyTorch) - four times, the model asking again each time; then the 14B, whose node list ComfyUI did not send in time (ReadTimeout). After three failures in a row the turn was handed to Qwen3.8 27B, whose load ran out of memory (ComfyUI still held the card), as did every chat model load after it: the seven remaining cases ended in 2-22 s with that error. Hence 0.22.1. Not known: why ComfyUI stopped answering and kept the card (its log, item 56). |
| The card after a failed job (0.22.1) | 8 tests (`tests/test_card_recovery.py`): with stubs, a chat model that did not fit (llama-server's "out of memory") loads after ComfyUI is told to interrupt, clear its queue and free the card, and other load failures are not retried; a card in use is cleared before a chat model loads, an empty one left alone; a ComfyUI whose `/free` leaves the card in use is interrupted and cleared. Through the scheduler against the fakes (the fake supervisor now reports the VRAM in use and fails a start that would not fit; the fake ComfyUI records interrupts and queue edits and can be too busy to list its nodes): a chat turn after ComfyUI held 22 GB loads its model once, after the clearing; a card that stays full ends the turn with the out-of-memory error after two starts; a video out of memory three times (different prompts) is not handed over, says not to repeat it, and each job is interrupted and deleted from ComfyUI's queue; a busy ComfyUI lends its last node list. Both graphs end in `VAEDecodeTiled`. ComfyUI v0.42.3 on CPU with the stand-ins: every video graph as the Hub builds it runs to the end (MP4s with the frames, size and rate it reports); the 5B's decode in small tiles (64 px, to make it tile at 192×128) with one time tile differs from the whole decode by 1.1 of 255 on every frame, with time tiles of 8 frames by 1.5 and up to 2.0 after the first tile - random weights, so plumbing, not looks. ComfyUI's own fall-back read in its code (`comfy/sd.py`): it sizes the tiles from its estimate of the whole decode (`8000·h·w·256` bytes in bf16 for this VAE: 13.4 GiB at 720p), which allows 1024-px tiles. |
| Real hardware, second test run (2026-10-25) | Søren's export, 0.22.1: Qwen3.8 27B ("Qwen3.8 (Adv)", thinking effort low), every case, 1 h 7 min, no errors - 19 passed, 3 in part, 2 failed; the chat model at 30-33 tokens/s. Video from a description: the 5B, 40.5 s a step, then the tiled decode and saving in 86 s - 15.5 minutes, a right clip (`VAEDecodeTiled` works on the card; no seam across the tile edges in the sky), but of a red origami *dragon*: the model wrote the prompt in English and took "papirdrage" (a paper kite) for a paper dragon. Video from a picture: the model chose the 14B picture pair; its first step came after 65 s, then nothing for 162 s, then the websocket closed (`ConnectionClosedError`) and the Hub gave the job up and cancelled it; loading the chat model again took 300 s (about what the clearing's two waits take if the card stays in use); the 5B then made a clip in 801 s (35 s a step) that keeps the picture only in the top-left corner of its first frame and is flat colours after (white, yellow, black, a new one every four frames) - the check "it starts from the picture" caught it (45.9 of 255). The edit "failed": the model turned the ball blue with a hue shift in Python, pixel for pixel right, and the check wanted `image_edit` (fixed: results are judged). The cut-out passed, after 3.5 minutes of its own checking and taking the shadow away - and both runs' cut-out came back at 1248×832 for a 768×512 ball, which the model called the original's size (fixed). The invitation was right, followed by a ticked list of the rules quoting "hyggelig" (now its own check); the page's formula was written `(c * 9) / 5` (now read); the poster was right ("ULI HALLEN" to Tesseract; now read whole). Danish slips throughout: "invite jer", "effulgt", "nest størst", "Seksktion", "nulstøtning", "Klaret", "tømrer", "Tempoforfaget", "så den er medtaget" for one it left out - more than Qwen3.6 35B-A3B's in the first run, at the same temperature 1.0. |
| Following a job, sizes, checks (0.22.2) | Tests: a job ComfyUI no longer has (neither in `/history` nor in `/queue`) ends at once, one still in the queue is waited for (the fake ComfyUI now has `GET /queue`, running and lost jobs and a chosen picture); a cut-out scaled to the original keeps its transparency and its edge colour, an edit keeps the original's size, a 6 MP original keeps the model's, the tools say the size; the invitation with a list after it fails only "nothing after the invitation"; the page with `(c * 9) / 5`; a picture the turn's own code made is judged; a clip jumping between flat colours fails "it moves smoothly". The poster check on both runs' posters: both read whole ("JUL ALLEN", "ULI HALLEN"; "13 DECEMBER" refused). Frame jumps measured on the second run's clips: the kite 1.3 of 255 at most, the broken clip 107, ffmpeg's test pattern 2.4. ComfyUI v0.42.3 on CPU (a slow graph of its own nodes): the websocket made to close after two messages - the Hub's client asked instead and got the job's six pictures; ComfyUI killed and started under a job - the socket closed ("no close frame received or sent"), the client waited through the refused connections and said the job was lost 13 s after it began, as soon as ComfyUI answered. |
| Real hardware, chat 21 again (2026-10-25) | Søren's export, 0.20.2: "can you try again to make the game?" - Qwen3.8 27B (Uncensored), thinking on, effort low; handed over to Qwen3.8 27B after three failed calls in a row. 60 model steps in 47 minutes (2,843 s of model time, median 25.9 tokens/s, prompts growing from 23K to 45K tokens of the 64K; the tools 3 s in all), then "Stopped after 60 steps". Steps 9 and 17 sent the next part without `append` and replaced the file (18 KB and 22 KB written), and steps 10-21 rebuilt it. The game it left, run here headless (a DOM stub, frames by hand): the file is whole and its script parses, but Start freezes it - level 1 has no empty cell (`:` read as a brick everywhere), so no enemies, the level counts as cleared, and `spawnPortal()` throws on null in the first frame; levels 2, 9 and 12 alike, the other eight start with two enemies. The model had found the cause and was one of four fixes in when the limit came. Hence 0.21.0's guard against replacing a file written in parts; the step limit is Settings → Assistant → "Most steps in one turn" (60; up to 300). |
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
| People (0.11.0) | 19 tests (`tests/test_people.py`): adding, changing, signing out and removing a person (user names, case, passwords); home only (refused from outside before the password; a session taken outside is signed out) and away; every owner route 403 for a person, the sync API and the model API (`/v1`, `/api`) 401; settings reduced to three defaults with no secrets; a device turn (her name and no memory in the system prompt, no bash/memory/library tools, the photo seen, no chat/run/prompt rows left, folder and photo gone after `/done`, the queue log with her name and "Chat"); pictures, files and artifacts back to the device, an artifact changed in the next turn; bash only when allowed and Landlock present, sent confined; turns private between people and bad requests refused (paths out of the folder, `.hub/home`); the queue as a person sees it; swept turns and ledgers (a list written into the folder by her code is ignored); the run dropped and deleted files reported; ComfyUI's history entries and output/input pictures deleted for her and kept for the owner; a picture the owner or another turn stores meanwhile kept; her skills (since 0.23.0 in `tests/test_skills.py`); at most four turns waiting for a device; the time each message was sent, from the device. 5 tests (`tests/test_sandbox_daemon.py`, real Landlock, skipped without it): a confined command reads and writes its folder only (not a sibling file, another turn's folder, `/root`; `TMPDIR` inside), `/dev/null`, `/dev/urandom`, process substitution and child processes work, `/dev/shm` and `/dev/pts` do not; background, `setsid` and `nohup` processes are killed when it ends, the owner's are not; bad confine settings refused. By hand on this build machine (kernel 6.18, ABI 7) as uid 1000: Landlock denies a confined process another process's `environ`, `mem` and `maps`, while `cmdline` stays readable (documented). The Hub's ComfyUI client against real ComfyUI v0.42.3 (CPU, a LoadImage → SaveImage graph): with `keep=False` the output file, the input file and the history entry are gone; with `keep=True` they stay. In Chromium (Playwright, 19 checks): Settings → People (add, list, the user name from the name), her chat at phone size (her sidebar, the thinking levels, no libraries), a title, a picture shown from the device's store, an artifact, an attachment and a file the assistant wrote in the Files panel, both chats and the picture after a reload with the Hub's copies gone, the owner seeing none of it, Export all chats → Import on a second browser with the picture, her session refused by the owner API, and not one refused request from her pages. An independent review (a separate agent, the whole diff) found five issues - `/dev/shm` writable, disabled skills readable, abandoned turns unbounded, the model API open to her session, `/proc/<pid>/cmdline` visible - the first four are fixed, the last is documented. |

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
   edits on the card (chat 9, above), and the LLM loads again after it.
   Qwen-Image 2.1 generated in the first test run (an 800×1408 poster with
   its text right, 39 s in the tool with the swap; an edit and a cut-out
   1 min 13-14 s each). Not seen yet: whether its int8 kernels or the bf16
   fallback ran (ComfyUI's log says), the self-test's image line (`sudo bash
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
    10 s each (8.5 s and 18.8 s, mostly loading and ACE-Step's planner); in
    the first test run XL Turbo made 30 s of music in 23 s and Stable Audio 3
    Medium 6 s of a sound effect in 7 s (after 11 and 6 s of loading).
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
50. **Video on the card** (0.19.0) - the test runs measured (1) below:
    28-41 s a step, 10-14 minutes, and since 0.22.1 it finishes (second run:
    15.5 minutes with the tiled decode, a right clip). A test plan, one at a
    time, noting the seconds the result gives and the VRAM in Settings →
    System: (1) the 5B from a description at its default 720p, 5 s - done;
    (2) the 5B from a picture (item 57 first; attach one:
    "make this picture move - the camera slowly pushes in"); (3) the 14B
    picture model fast, then `quality: full` (several times longer); (4) a
    first and a last picture; (5) the 14B text model. Then: does it look
    right, does the motion follow the prompt, Danish prompt vs English.
51. **The 14B pair and the VM's memory** (0.19.0) - the second test run's
    ComfyUI log (12:05-12:30 UTC) and kernel log: ComfyUI's dynamic VRAM
    staged both experts ("13630MB Staged" each; "0 models unloaded" before
    the second) beside the text encoder (6419 MB). The first expert's first
    step took 51 s with its loading; the second expert's loading had run
    3 min 16 s without a step when the job stopped, and the job ended 510 s
    after it began - 4 min 43 s after the Hub's interrupt (ComfyUI honours an
    interrupt between steps), with no restart, no out-of-memory kill and no
    GPU reset. The kernel's only line: `amdgpu_amdkfd_restore_userptr_worker
    hogged CPU` at 12:14:36 - the driver restoring host memory registered for
    the card, which ComfyUI's "pinned memory" is on AMD (`hipHostRegister`;
    up to 40-90 % of RAM by its own rule). Likely the VM's RAM - about 34 GB
    of staged weights in 40 GB - but not measured. To measure: `vmstat 5` in
    a second SSH window during a 14B job (si/so, free). To try, one at a
    time: `COMFY_ARGS=--disable-pinned-memory`, then `--cache-none`, in
    `.env` and `update.sh`; ComfyUI's start-up log says its pinned limit
    ("Enabled pinned memory").
52. **Remakes after 0.20.2** - a test plan with chat 24's song: (1) the same
    request ("Re-make this song as a live concert with a cheering audience
    and a big hall"): the result should say "with its own sound", "keep 0.5"
    and "Check: the song's codes agree with ACE-Step's own tokenizer for
    ...%" - note that number; does it sound like the song now? (2) the same
    with "keep 0.7" and with "keep 0.3"; (3) "Keep this exact recording, but
    make it sound live" (the effects version: a hall and a crowd). Send the
    export after (1): I measure key, tempo and harmony against the original
    again. If the check says under 90 %, the codes are the problem.
53. **Tests on the card** (0.21.0) - two runs (2026-10-25; rows above):
    Qwen3.6 35B-A3B (16 passed before video took the card) and Qwen3.8 27B (no
    errors). Seen: every case's checks on the real sandbox - `dotnet run`
    offline, the hidden tests, web research on python.org - Tesseract on real
    posters, the time limits holding (the longest case 23 minutes of the
    video's 60). Next, after 0.22.2: every case again with Qwen3.6, to compare
    the two models with the corrected checks. Runs are kept until deleted,
    with their chats and files.
54. **The guard against replacing a file written in parts** (0.21.0) with
    the real models: whether Qwen3.8 reads the refusal and sends the part
    again with `append: true` (expected), and whether a model that really
    means to start over uses `replace: true`. Chat 21 can go on: "fortsæt",
    best with the diagnosis (its row above) - since 0.22.0 Continue under its
    last answer does the same, without the diagnosis.
55. **Continue with the real models** (0.22.0): whether Qwen3.8 checks the
    files before it changes them and goes on rather than starting over, and
    how a continued chat behaves when its history passes the context's
    condensing point (chat 21's last prompts were 45K of 64K tokens).
56. **The card after a failed job** (0.22.1). (a) The tiled decode on the
    card: done in the second run - 86 s for the decode and saving, no seam in
    the kite clip's sky; not seen: its peak VRAM (Settings → System during
    it). (b) Why ComfyUI stopped
    answering and kept the card in the first run - not explained yet; ask for
    its log of 2026-10-25 09:05-10:00 UTC (`sudo bash /opt/slq-hub/update.sh
    compose logs --since 2026-10-25T09:05:00Z --until 2026-10-25T10:00:00Z
    comfyui > comfyui.log`) and the kernel's (`sudo journalctl -k --since
    "2026-10-25 09:00 UTC" --until "2026-10-25 10:05 UTC" > kernel.log`): an
    out-of-memory kill, a GPU reset or a hang. (c) The clearing itself: a
    chat after a video should load at once; the Hub's log (`compose logs
    hub`, logger `hub.gpu`) says "... GB in use on the card before a model
    loads: stopping ComfyUI's work and freeing it", "the card still has ...
    GB in use after ComfyUI was asked to free it" or "... did not fit on the
    card (...); clearing ComfyUI and trying once more" when it had to. If a
    chat model still fails to load after a failed video, send both logs
    before changing more. Seen in the second run: after the 14B job the chat
    model loaded after 300 s, as long as the card staying in use through both
    of the clearing's two-minute waits would take; the Hub's log says whether
    it did.
57. **The clip from a picture that came out broken** (second test run): the
    5B, right after a 14B job the Hub had cancelled, made a clip that keeps
    the picture only in the top-left corner of its first frame and is flat
    colours after. Not explained. ComfyUI's log shows nothing amiss for it:
    13 min 20 s, no warning, no tiled-encode fallback - but it began 39 s
    after the 14B job ended (item 51), under whatever that job left in RAM.
    To tell which: a fresh ComfyUI (`sudo bash /opt/slq-hub/update.sh compose
    restart comfyui`), then Settings → Tests, only "Make a picture move",
    with the 5B (Settings → Assistant → Video).
58. **Skills with the real models** (0.23.0): whether Qwen3.8 follows the
    skill maker - reads all of a long PDF or a big workbook (`read_file`
    gives 2,000 rows a sheet), writes a SKILL.md that passes the checks the
    first or second time, keeps the user's language, puts long material in
    `references/` and tries the skill before `skill_propose` - and how a
    chosen skill changes its answers. A test: attach a manual (PDF) and an
    Excel sheet, "Make a skill that ...", Add skill, then a new chat with the
    skill and a question the manual answers. Not seen either: a skill
    downloaded here imported in Claude, one from Claude imported here, and
    the composer's "/" list with a phone's keyboard.
59. **Energy on the card** (0.24.0): (a) seen: the supervisor reads hwmon
    inside the `llm` container - Settings → Usage on Søren's phone said "The
    card draws 5 W now (its limit: 339 W)" (2026-10-25 16:09). (b) The idle
    draw, two readings that do not agree yet: 65 W on the VM at 16:00, right
    after the 0.24.0 update (the default chat model loading or just loaded),
    which Søren called idle - about 4.68 kr a day - and 5 W at 16:09, a minute
    before his queue showed a web chat holding the card (perhaps between two
    model steps, with the model loaded). Next: Settings → Usage after a minute
    with nothing running, with the model loaded (the sidebar's plate names
    it), then after Settings → Models, Free the GPU. The "The card, idle" row
    gives the day's real figure either way. (c) How quickly
    `power1_average` follows the load (the SMU's averaging): a short answer
    may be counted at the draw from just before it; long turns, pictures and
    videos are what the numbers are for. (d) Two jobs on the card at once
    share its draw equally - an estimate. (e) The rest of the PC: 0 W until
    someone measures it at the socket.
60. **Wan 2.2 5B Turbo on the card** (0.25.0): (a) that Kijai's LoRA loads
    in ComfyUI's own `LoraLoaderModelOnly` without "lora key not loaded" in
    ComfyUI's log (`compose logs comfyui`) - its keys were not seen here;
    (b) the time for a 5-s 720p clip: 4 steps at cfg 1 should take about a
    tenth of the 5B's 10-14 minutes of sampling, plus the 1.5 minutes of
    decoding and saving - say 3 minutes in all, to be measured (Settings →
    Tests, "Video from a description" and "Make a picture move", with Settings
    → Assistant → Video on the Turbo); (c) the look next to the 5B's 20 steps
    - Civitai's Turbo merges mention oversaturation (their fix: the latent
    times 0.8); if it shows, `lora_strength` below 1 in the catalog's settings
    is the first thing to try.
61. **Waiting for a free Hub on the VM** (0.26.0): seen - the VM runs 0.26.0
    (Søren's screenshots), so that update went through; not reported -
    whether it waited, and how. (a) the update to 0.26.0
    itself is finished by its own updater, but the running 0.25.0 Hub
    cannot say what it does (401), so the card's `gpu_busy_percent` decides -
    not seen on the VM: what it reads with the model loaded and idle, and
    while a turn runs a tool between two steps (a tool that runs for over a
    minute with the card under 10 % would let this one update go on during
    the turn); (b) from 0.26.0 on the Hub answers - run `sudo bash update.sh`
    while a chat answers: the terminal shows "waiting: a chat is working on
    the card (…)", and the update goes on by itself a minute after the
    answer; (c) the background updater after the SSH window closes on the
    VM (here it survived a closed terminal and Ctrl+C in its own session;
    systemd-logind's `KillUserProcesses` is off on Ubuntu by default), and
    `status`, `cancel` there; (d) a chat started during the backup and
    restart is cut off, as before - the wait makes that unlikely, not
    impossible.
62. **The real token count with the real models** (0.26.0): a long turn
    should show `token_ratio` in its steps' `sent` (the export) and be
    condensed or shortened before the end of the context - chat 74 can go
    on ("fortsæt"). The refit depends on llama-server's words "request (N
    tokens) exceeds the available context size" (b11783, chat 74); a later
    llama.cpp that words it otherwise leaves only the ratio.
63. **A bigger context on the card** (0.26.0): measured (Søren's
    screenshots, 18:17) - "Qwen3.8 (Adv. NL)", a 27B he added from Hugging
    Face, at 65,536 (two slots, q8_0) while it worked: GPU memory 22.1 GB of
    24.0, so about 1.9 GB free; by DESIGN §4's numbers 128,000 with q8_0
    would just not fit (about 24.2), with a q4_0 cache about where it is
    now, and 262,144 not at all. Also measured, chat 74's log -
    the 27B started and served with Søren's 128,000 (q8_0, two slots) and
    read 57K tokens at about 820 a second; not measured: the VRAM in use with
    it (Settings → System), and whether 262,144 fits - by DESIGN §4's numbers
    about 4.4 GiB more cache than 128,000, so likely not with q8_0 and the
    vision file loaded; a q4_0 cache halves it. Also not seen: answers at
    200K+ (slower to read and to write, and models follow very long contexts
    less well).
64. **Long chats with the real models** (0.27.0): whether Qwen3.8 writes
    useful structured notes - and carries over the old ones, now that they
    are replaced rather than appended; whether it reaches for chat_history
    when a detail is gone, and for a subagent on big reads; whether the
    cleared results make it re-read files more often; the handover notes'
    quality in a real long chat (chat 74 is a good one to try: ⋯ menu,
    Continue in a new chat); and whether the clearing at half the context
    keeps llama-server's cache useful (`cached_tokens` per step in the
    export). The 0.6 threshold of the hint is a guess.
66. **Chat 76: the band changed, the voices kept** (0.27.1): whether keep
    0.6-0.7 changes a pop song's sound and keeps its rhythm, and whether the
    stems way (a remake of the accompaniment, the original vocals mixed back)
    gives "the same voices, a heavier band" - Søren was given a message to
    try. Whether the models follow the new description, and stop describing
    sound they cannot hear. Tried on 0.26.0 (chat 76 again, in the table):
    the rhythm held at 0.6-0.7, the band changed little, the model's own mix
    put the vocals 11 dB under - followed by 67.
68. **Long thinking turns** (0.28.4): in a long turn of Qwen3.8 27B with
    thinking (chat 78's kind), the steps' `cached_tokens` should stay close
    to the prompt except at a clearing every several steps, and the model
    should keep its thread with only the newest two steps' thinking. Also:
    whether the notes of the next Continue in a new chat give paths and mark
    guesses. Left as it is: the 27B's thinking itself (up to 13,000 tokens,
    10 minutes, for one step) - thinking off in the chat makes it much faster.
69. **Web pages in a browser on the VM** (0.29.0): update.sh rebuilds the
    sandbox image with a new layer - Playwright 1.63.0 in `/opt/pageshot` and
    `playwright install --with-deps --only-shell chromium` (Ubuntu 24.04's
    libraries from the archive, the headless shell from Playwright's CDN: the
    build needs both) - not built here (no Docker daemon). Measured here: the
    headless shell 261 MB, Playwright with its driver 140 MB; its libraries
    from the archive not measured. Then: the daemon's log line "web pages in a browser
    possible" and the self-test's "web pages" line PASS (offline). Not seen on
    the VM: Chromium as the `sandbox` user under the real `confine.py --offline`
    (only here, as root), its memory beside Wine and the models (measured
    here: about 370 MB PSS at the peak of a run on chat 79's game, Python,
    Playwright's driver and Chromium together; 590 MB summed RSS), how long a
    run takes there (2-5 s here), two runs at once.
    Whether the models use it - and the four new working rules: does Qwen3.8
    look for existing tools, keep NOTES.md, play its game before it says done,
    and say what it did not check? A chat 79 again ("play the game and fix what
    is broken") would show it: the export's `web_app` calls and their reports.
    Also: Download all on Søren's phone (Chrome on Android saves the ZIP), a
    whole-folder ZIP of chat 79's folder (some 30 MB of DB1 files and JSON).
81. **0.40.0's synced folder with the real SLQSync.exe**: tested here with
    `slq_sync.py` (the Python client) in a round trip against both layouts,
    and the API by hand - not with the Windows tray app. Check on the PC: a
    new project (say `TwinCAT HUB Project 2`, synced folder `twincat`), link an
    empty PC folder, copy the TwinCAT solution in, sync - the files land in
    `twincat/` on the Hub, nothing in `hub/` reaches the PC. Then a chat in
    the project: the assistant's notes and work files in `hub/`, its change
    to a POU in `twincat/` (and on the PC after a sync), attachments in
    `hub/attachments/`. Not seen anywhere: the New project dialog with its
    synced-folder field in a browser (built and type-checked only).
82. **edit_file's tolerance on the VM** (0.46.0): which case made Søren's red
    "old_string was not found" - line numbers copied, tabs as spaces, a lost
    indentation, or text that is not in the file (an older version, a guess,
    a shortened read). An export of a chat with a failed edit shows the call
    and the file. Also whether a re-indented new_string is what the model
    meant (in ST the result is only cosmetic; in Python it would matter), and
    the work block and thought gists on a real turn with Qwen3.8's thinking.
83. **LoRAs on the card** (0.49.0): a real LoRA per family through ComfyUI - FLUX.2 klein 4B (bf16 weights, the
    likeliest to work; a LoRA trained on klein-base-4B used on the 4-step model), Qwen-Image 2.1 (its weights are
    int8 "convrot": whether ComfyUI patches a LoRA into them at all is the open question), Z-Image, SDXL. ComfyUI's
    log says "lora key not loaded" when a LoRA does not fit; the Hub does not read that yet, so a wrong family shows
    only as a picture without the LoRA's effect. Also: an upload of a few hundred MB through hub.slq.dk (Nginx Proxy
    Manager's body size limit).
84. **LoRA training on the card** (0.50.0) - none of it has run on the real hardware:
    - **The image**: `COMPOSE_PROFILES=training` in `.env`, then `sudo bash update.sh compose up -d --build
      trainer` (or plain `update.sh` from 0.50.1). With the profile on, a full update builds the trainer with the
      rest, and a failed trainer build undoes the update like any failed build - turn the profile off to update
      without it. Does ai-toolkit's
      requirements install go through with the rocm7.1 torch held by the constraint (a package wanting another
      torch fails the build - read which), and does `import torch` see the card in the container
      (`sudo bash update.sh compose exec trainer python -c "import torch; print(torch.cuda.is_available())"`)?
      ai-toolkit calls its AMD support "experimental and largely untested".
    - **Memory (0.51.1, solved in 0.51.2)**: the first two runs ran out at the first sample (expandable
      segments kept freed memory on the card); without them it trains at about 12 GB. Still to see: the whole
      1500 steps, the time it really takes, and the samples at 250, 500, ... looking like the person.
    - **The first run**: a thing with 15-25 pictures, 1000 steps. The download (about 16 GB - an estimate: 7.75
      GB is the distilled klein-4B's single file on Hugging Face, the base repo's was not looked at, and Qwen3-4B's
      bf16 weights are about 8 GB) lands in `models/hf/hub`; ai-toolkit then
      loads offline (HF_HUB_OFFLINE) - if it asks for a file the Hub did not fetch, the run fails with ai-toolkit's
      own message in the trainer's last lines. Watch: the memory (bf16 + cached text embeddings is meant to fit
      in 24 GB; `rocm-smi` while it trains), the speed (the time left after the first steps), and whether the
      progress bar is parsed (step and time left on the page).
    - **The result**: does a checkpoint load in ComfyUI on the 4-step klein (item 83: "lora key not loaded" in
      ComfyUI's log means the keys do not fit), and does the trigger word bring the thing?
    - **Stop and restart**: Stop during training (the card is free within about 90 s, after ai-toolkit has
      exited); a Hub restart while it trains (the leftover job is stopped before anything loads).
    - Not offered: klein 9B (gated, under BFL's own licence; ai-toolkit quantises it by default).
80. **Not pursued (Søren, 2026-10-28)** - kept here in case it is taken up again; if it stays unused, remove
    the `windows` service, `.30` from `SANDBOX_IPS`, `windows/*` from `FOLDERS` and INSTALL.md section 12.
    **0.39.0's Windows container on VM 222**: `COMPOSE_PROFILES=windows` in
    `.env`, `sudo bash update.sh compose up -d windows`; the password is shown
    once. Check: Windows 10 LTSC installs by itself (logs; web viewer through
    the SSH tunnel), RDP from the LAN works, Windows reaches the internet but
    not the LAN (`ping` a LAN address fails), and - the unverified part -
    the Hub reaches a port of Windows other than RDP: with the TwinCAT build
    server running in Windows, `sudo bash update.sh compose exec hub curl -s
    -X POST http://172.31.250.30:8765/mcp -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'`
    answers (dockur forwards the container's ports to the Windows VM; if this
    port is not forwarded, that is the thing to fix).
79. **0.38.0 on the VM**: Sanne makes a short video in her chat on her iPhone.
    Check: the file card plays it - or says "this browser cannot play it"
    (then the codec, not the transfer, is the problem: look at the file's
    `ffprobe`). Turn the phone's network off while a video is made, on again
    after the answer: the toast says it is not on the device yet, and opening
    the chat again fetches it (within the hour). In Søren's chats: the Effort
    chip; a CM98 turn at Extra still gets the review, at Think it does not;
    Settings → Assistant shows Quick for plain chats and Extra for projects.
78. **0.37.0 on the VM**: in a CM98 chat (the project's cloud switch on, Settings
    → Cloud with Søren's Anthropic key, claude-opus-5-5): press Continue in the
    cloud under an answer - the turn should start at once (no wait for the
    card), the answer's footer name `anthropic:claude-opus-5-5`, the work go on
    from where the local model stood. Then pick the cloud model in the menu and
    send a message. Look at what Settings → Usage shows for such a turn (not
    checked here: the card's energy should be 0, the cloud's tokens counted).
77. **0.36.0 with the TwinCAT build server**: not run anywhere real yet. On
    the TwinCAT PC: `python tc_build_server.py --selftest` (both builds should
    fail with only the deliberate C0046 error - the first time the XAE's
    import of the skill's PLCopen XML and TwinCAT files is seen); then the
    server with `--token`, added in Settings -> MCP (HTTP, the header,
    trusted, "The independent review may use it"). Check in a chat with the
    twincat3-plc skill: the tool shows as `mcp__<name>__twincat_build` with
    "(only looks)" and "(gets files)" in Settings, the model passes paths (not
    the XML text), the build's errors come back, a second build after a fix
    runs, and the review calls the build too. A server that does not know
    `_meta` sees only the paths - harmless.
76. **0.35.0 and 0.35.1 on the VM**: chat 136 showed `compare` used and the kit's stylesheet not (above). Now: a new chat in CM98 with Søren's kit
    (`docs/cm98-ui.md`, `reference/` with cm98.css, squad.html, search.html,
    the title font and the screenshots) and the prompt he got. Check: is
    `web_app` called with `compare` (and `compare_area` for the parts the
    photo leaves free), does the page open at 640x480, are the reports and the
    picture readable for Qwen3.8 (the vision models should see the red), does
    it leave game.js's logic alone and make no image_generate "reference"?
    Open `reference/squad.html` with `compare: reference/screenshots/
    squad-640x480.png` as a first call: about 75% whole, 89-94% in the rows of
    buttons, as measured here - fonts in the sandbox are Liberation Sans and
    the bundled DejaVu Sans Bold, as here, so the figures should agree.
75. **0.34.0 on the VM**: after the update, CM98's page should say about
    4.77 kr (2.04 + 2.72) with chat 126's answers included -
    if it still says 2.04 kr, the move's revision did not match the chat's
    title (the Hub's log says "was moved into project ... but not when"). Each
    chat's line: the models (Fable, then Standard where it handed over), the
    time, and "not finished" where a turn stopped at the step limit.
74. **0.33.0 on the VM**: the next chat in CM98 - does the assistant put new
    files in folders, and test scripts in `.hub/tmp/`? Ask it once to tidy
    CM98 ("organise the project folder: the game in game/, the DB1 files and
    JSON in data/, the scripts in scripts/; fix the paths; delete
    test_data.js") and check that SLQ Sync moves the files on the PC too and
    that the game still opens. A hand-over: the model chip should turn to
    Standard during the turn, and the next message should start without a
    model load (the export's `load` steps).
73. **Picture changes in a row on the VM** (0.32.1): attach a photo in a
    plain chat, "make it brighter", then "now add a hat" without the photo:
    the second `image_edit` should name `images/...` (the export's call), not
    `.hub/attachments/...`. Also: two pictures combined with the scene first,
    and the 5 dog photos as references (how close the dog comes out) - before
    Subjects (saved reference pictures) is built.
72. **The review's wrap-up on the VM** (0.32.0): whether Qwen3.8 Standard
    reports when told "this review is nearly over" or only at its last step
    without tools; how long reviews take now on the card (the export's
    review `seconds`, its `wrap_up` steps and their `minutes`) - 15 minutes
    plus the step under way and the last one, which with the 27B's long
    steps may be several minutes; whether reports made at the limit still
    find real faults, and say what they did not check; whether the prompt's
    "do not do the work again" keeps it off re-analysing DB1 (the
    transcript now lists its calls). If 15 minutes is too short for a
    browser game, Settings → Assistant → "Time for the check". Also whether
    `bash` refusing a copied "[shortened by ...]" mark leads Fable to write
    the part in full rather than fail again (chat 128 needed the hand-over).
71. **NOTES.md and Move to a project on the VM** (0.31.0): whether Fable
    and Standard write NOTES.md when reminded at step 15 (the export's
    notices and the file under Files), whether they read it at the start of
    the next chat - its first call should be `read_file NOTES.md` - and
    whether what they write keeps checked facts apart from guesses. Move
    chat 126 into a new project "CM98" (its 22 MB of DB1 and JSON): the
    project's Files and History, SLQ Sync on the PC picking it up, the next
    answer working in `/workspace/projects/<id>` - and the old paths
    (`/workspace/chats/126/...`) in its history not confusing it. Seen
    since: the move itself (Søren's export after it), and chat 128 in CM98
    began with `read_file NOTES.md` and kept the file through its turn.
    Still open: the reminder at step 15 on the card, SLQ Sync on the PC,
    and whether the notes keep checked facts apart (chat 126's NOTES.md had
    the PL 97/98 list wrong - chat 128 was about fixing it).
70. **Thinking budget and the review on the VM** (0.30.0): measured only on
    llama-server b11783 with a tiny random model on the CPU. With Qwen3.8
    27B: does a step stop thinking at about 8,000 tokens (the export's
    `sent.thinking_ended: budget`, the thinking ending with "(That is enough
    thinking for this step ...") - is a piece of its stream still about a
    token there (`completion_tokens` against the budget) - and does the
    model then act rather than think on in its answer? Stop thinking on
    Søren's phone and PC. The review: how long it takes on the card (a 35B
    turn reviewed by the 27B costs two model switches, plus up to 24 steps -
    since 0.32.0 about 15 minutes, a few more with long steps, item 72);
    whether the 27B's reports find real faults and the fixes hold (chat 79's
    kind: a browser game in a project); whether the working model fixes or
    argues. If it is too slow: Settings → Assistant → "Who checks the work"
    → the model that did the work (no switch), or off.
67. **The same vocals, a heavier band, in one call** (0.28.0): after
    update.sh (it rebuilds the ComfyUI image for `SLQBeatCheck`), in a new chat
    with the song attached, e.g. "Lav en heavy metal-version af sangen med den
    originale vokal (vocals original), keep 0.6". Expected: the mix and the
    band saved, keep 0.6, a beat check that follows (about 97 BPM, F# minor,
    90 % or more). Søren listens: the vocals at their level? How much did the
    band change? Then the two untried ways, each with its beat check: keep 0.5
    with bpm 97 and key F# minor (does the tempo hold now?), and keep 0.6 with
    a 30-s heavy metal instrumental from music_generate as the reference (is
    the band heavier?). The export tells which held. Also: the beat check's
    time on the VM's CPU (about 5 s a pair here) and that its verdicts match
    what Søren hears.
65. **Delete files on the VM** (0.27.0): with the real catalog and files
    (the two 14B video models, 62 GB) - which Søren may have deleted by hand
    before this update with the commands he was given; Download should then
    offer them again. Not seen: a model whose files another model shares
    being deleted while that one downloads (counted as a user - tested only
    against the fakes).

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
| `backend/app/evals/`, `frontend/src/components/TestsTab.tsx` | Tests (0.21.0): the cases (`suite.py`), their input files and answers (`fixtures.py`), the Hub's checks (`checks.py`), the sandbox's checks (`check_lib.py`, run in the sandbox only), the runner and its storage (`runner.py`, table `test_runs`), the export (`export.py`), the API (`routes.py`); the Settings tab. `backend/tests/dev_evals.py` answers each case for the dev server. |
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

    # Settings -> Tests end to end without Docker: the sandbox daemon on this
    # machine (its python3 needs numpy, Pillow, python-docx, openpyxl, pandas,
    # matplotlib; ffmpeg, Tesseract and dotnet for those cases), /workspace a
    # link to the dev Hub's workspace; the fake model answers each case well
    ln -sfn DIR/data/workspace /workspace
    EXECD_ROOT=/workspace EXECD_TOKEN=tok python3 ../services/sandbox/execd.py &
    python -m tests.devserver --port 8080 --data DIR --sandbox http://127.0.0.1:7000

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
- Skills are chosen by hand for each chat or project; nothing is picked on
  its own. A skill's scripts run only where code may run (the owner's chats,
  a person's with "May run code"). The chosen skills' SKILL.md texts share
  40,000 characters of the prompt; a longer one is read when needed.
- Costs count the card's own power reading (plus a fixed number of watts for
  the rest of the PC, if set) at one fixed price - not the PC at the socket,
  not hourly prices. Answers from before 0.24.0 have no energy.
- The independent review sees the working folder's files (not `.git`,
  `node_modules` and the like, nothing outside the folder) and the turn's
  artifacts; it names files that changed while it ran but undoes nothing.
  Once a turn; not in people's chats, on the cloud or in test runs.
- Move to a project: only a chat outside a project, into one project; a file
  the project has by the same name with other content (or a name alike but
  for case) stops the move until one of the two is renamed or deleted
  (attachments excepted: renamed); FIFOs and sockets stay behind; the old
  folder is kept in `data/trash`, which nothing empties yet. A background
  job a turn left running and that writes by the old absolute path makes
  `chats/<id>` again - an orphan folder.
- NOTES.md: the reminder is not stored with the turn, so with thinking off
  the next turn's prompt cache ends where it was; the turn after the file
  first appears reads its prompt again once (the line in the system prompt).
- The run's live events are kept in memory for an hour; the stored messages
  are permanent.
- No custom ComfyUI workflows yet: pictures, sound and video come from the
  Hub's own graphs. Speech, music, sound effects and video are one file per
  call (a video 10 s at most); a remake re-performs a song from its start
  (5 minutes at most), not a chosen part of it; ffmpeg in bash mixes and
  cuts.
