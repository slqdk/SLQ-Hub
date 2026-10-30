# Changelog

## 0.59.0 - 2026-10-30

- **Every picture says what made it.** Under each picture: the picture model,
  the LoRAs it used with their strength, and any LoRA that was left out and
  why (a LoRA works only with picture models of its family).
- **The picture menu decides.** The assistant can no longer pick another
  picture model or add LoRAs in its own call. It used to copy them from
  earlier pictures, so a chat set to RealVisXL could still draw with
  Qwen-Image and leave an SDXL LoRA out. Switch the model or the LoRAs in the
  menu at any time, also in the middle of a chat.
- The picture menu lists all your LoRAs. Those for another family are greyed
  out with the model they need. A LoRA that is on but does not fit the chat's
  model is struck through above the message box.

## 0.58.0 - 2026-10-30

- **Pause a LoRA training and go on later.** Stop is now a pause: the
  training keeps its checkpoints, and "Continue from step N" trains on from
  the last one (one is saved every 250 steps, so at most 249 steps are done
  again). A finished training can go on too: raise Steps and press Continue.
  "Train again" is now called "Start over" and still deletes the checkpoints.
- On a phone, the buttons at the bottom of a dialog wrap to a second line
  instead of being cut off.

## 0.57.1 - 2026-10-30

- **A clear message when the proxy refuses an upload.** Through hub.slq.dk a
  big file, such as a LoRA, could come back as Nginx Proxy Manager's raw
  "413 Request Entity Too Large" page. The Hub now says what happened and what
  to do. The proxy setup in docs/INSTALL.md (step 4) now allows 3200 MB, as a
  LoRA can be up to 3 GB; it said 600 MB.

## 0.57.0 - 2026-10-30

- **A chosen LoRA shows above the message box, with its trigger word.** A
  click on the word writes it into your message; the cross turns the LoRA off
  for the chat. The Hub still puts the trigger word in the picture's prompt by
  itself. The picture menu now shows a LoRA's trigger word next to its note.

## 0.56.0 - 2026-10-30

- **Train a LoRA on an SDXL model.** New training can now be based on one of
  your SDXL models (RealVisXL 5.0 and others added under Settings -> Models)
  instead of FLUX.2 klein 4B. SDXL trains quicker, its LoRAs are small, and
  they work with every SDXL model - best with the one they were trained on.
  The first SDXL training fetches a few small configuration files.
- Adding an image model from Hugging Face no longer repeats the repository's
  name in the suggested name ("RealVisXL V5.0", not "RealVisXL V5.0 RealVisXL
  V5.0 fp16").

## 0.55.0 - 2026-10-30

- **Slider LoRAs.** A slider LoRA moves one quality along a line - plus for
  more, minus for the opposite - and takes strengths like 4 or -3. Mark it
  in the LoRA's settings ("Slider LoRA"; a file with "slider" in its name is
  marked for you): its strength goes from -10 to 10, and the chat's slider
  follows. Plain LoRAs stay at 0-2.

## 0.54.0 - 2026-10-30

- **A strength slider for each LoRA in a chat.** Tick a LoRA in the picture
  menu and a slider shows under it (0-2); the chat keeps it, and Reset goes
  back to the LoRA's own strength (its settings). A strength named in a
  request still wins.
- **A LoRA for the wrong FLUX is caught.** A LoRA made for FLUX.2 klein 9B is
  refused with the reason - the Hub's klein is the 4B, and a LoRA works only
  with the size it was trained on. A FLUX.1 LoRA is no longer taken for a
  klein one; you say which models it is for.

## 0.53.1 - 2026-10-30

- **Trigger words: a warning when one has a real word or digits in it.**
  "waterx55" turned the watercolour sample into a lake at some steps - the
  model read "water". New training and Duplicate now say so as you type,
  and suggest letters only.
- "Which step?" explains a sample that shows something else at some steps
  (a landscape instead of the person): choose a step where all three show
  them, and the scribbles in a corner are the trigger word as a signature.

## 0.53.0 - 2026-10-30

- **Backups without the trainings' checkpoints.** A training's checkpoints,
  sample pictures and ai-toolkit's cache files are no longer in the update's
  backup - with three trainings it was 10.3 GB and kept the Hub stopped for
  ten minutes. Pictures, captions and the training settings are still backed
  up, and a rollback no longer deletes checkpoints.
- **Free space on a training.** Once you have added the LoRA you want,
  "Free ..." at the bottom of the training deletes its checkpoints (a few GB
  each); pictures, captions, sample pictures and the added LoRAs stay. The
  list shows how much each training takes.
- ai-toolkit's optimizer state (only for resuming a training, which the Hub
  does not do) is deleted when a training ends.

## 0.52.1 - 2026-10-29

- **Which pictures, by what it learns.** For a person, the training page
  now says the mix that works - about half close-ups of the face, a third
  head and shoulders, the rest full body - and what to leave out (other
  people, beauty filters, the same outfit again and again). A thing and a
  style get their own advice, and the captions advice follows the kind.
  "More tips" under the pictures opens the guide.

## 0.52.0 - 2026-10-29

- **The advice is on the training page.** "Which step?" explains how to
  choose a checkpoint by its sample pictures (too early, too late, what to do
  when it looks weaker in a chat); "Make it better" what to change for the
  next round - steps up or down, pictures, captions, rank, strength - and to
  keep the best step before training again. A "?" at Steps and Rank says
  what they do.
- **The first LoRA training runs on the card.** Without expandable
  segments (0.51.1) the memory ai-toolkit frees goes back to the card: it
  made its sample pictures and trains (about 4.4 s a step for FLUX.2 klein
  4B, a first estimate).
- The training page counted ai-toolkit's other progress bars as steps -
  "Step 35 of 35" while it cached 35 pictures, and the sample pictures'
  bars. Only the training's own steps count now.

## 0.51.1 - 2026-10-29

- **LoRA training ran out of memory at its first sample pictures** on the
  RX 7900 XTX. Measured: memory ai-toolkit gave back after its text encoder
  stayed in use on the card until the training ended. ai-toolkit now runs
  without PyTorch's "expandable segments" (set in the trainer's image), the
  likeliest cause - the next run shows whether that was it.
  `TRAINER_ALLOC_CONF` in `.env` can set PyTorch's allocator for the trainer.

## 0.51.0 - 2026-10-29

- **Duplicate a LoRA training** to try a v2: the copy gets the pictures,
  captions, steps and rank (the name counts up - "Rikke v2"), with a new
  trigger word or kind if you like - a new trigger word replaces the old one
  in every caption. The original keeps its checkpoints and sample pictures.
- Fixed: a picture removed and another added under the same name was trained
  from the old picture's cached data (ai-toolkit keys its cache by the file
  name). A removed picture's cache now goes with it.
- Fixed: a trigger word is matched as a word of its own - "lora" was found
  inside "explorative", so a caption or a picture prompt could be left
  without it.

## 0.50.1 - 2026-10-29

- **Turning the trainer on works with update.sh alone.** With
  `COMPOSE_PROFILES=training` added to `.env` and no new version to fetch,
  `update.sh` said "Already up to date" and did nothing; now it builds and
  starts a service whose profile was turned on since the last update (the
  trainer, Windows). Directly: `sudo bash update.sh compose up -d --build
  trainer`.
- An update waits for a LoRA training, also while it downloads its base
  model.

## 0.50.0 - 2026-10-29

- **Train your own LoRA - only for the admin.** Settings -> Models -> LoRAs
  -> Train a LoRA teaches FLUX.2 klein 4B a thing, a person or a style from
  your own pictures: add 15-40 pictures, let the chat model write the
  captions (or type them), choose a trigger word and start. The picture page
  shows how far it is, with the time left, and can stop it.
- **Choose by the sample pictures.** Every 250 steps it saves a checkpoint
  with three sample pictures made with the trigger word; Add as LoRA puts
  the step that looks right among your LoRAs.
- A training takes the whole card for a few hours, through the GPU queue:
  chats and pictures wait, and the GPU plate says "LoRA training". The first
  one downloads about 16 GB of model files.
- The trainer is optional: `COMPOSE_PROFILES=training` in `/srv/slq-hub/.env`
  and `update.sh` build and start it (Ostris' ai-toolkit on PyTorch for
  ROCm). Not yet run on the real card.

## 0.49.0 - 2026-10-29

- **LoRAs for the picture models - only for the admin.** Settings -> Models
  -> LoRAs adds a LoRA (a .safetensors file): which picture models it fits
  and its trigger word are read from the file when they can be, and can be
  changed, with a default strength and a note on what it is for.
- **Using one:** the picture chip under the message box turns LoRAs on for
  every picture in the chat, or ask for one by name. Its trigger word goes
  into the prompt by itself; a LoRA for another family of models is left out
  with a note.
- People's chats, their devices and apps on the model API never see or use
  a LoRA.

## 0.48.1 - 2026-10-29

- **SLQ Sync says why it was refused.** Instead of "sign in with a sync device
  token", the PC's sync app now shows which it is: no token arrived (a proxy
  may drop the Authorization header), it is not a device token, or the token
  is unknown to the Hub (removed - add the device again). The Hub's log notes
  each refusal, without the token.

## 0.48.0 - 2026-10-29

- **Import a whole set of skills at once.** A .zip with several skills side
  by side - one folder each, with its SKILL.md - brings them all in one go
  from Skills -> Import; the message names them. A single skill imports as
  before, also one with an example SKILL.md somewhere inside it.

## 0.47.0 - 2026-10-29

- **"internal" is the admin's alone.** Skills the admin puts in the category
  internal are never shown to, read by or used for anyone else - not in the
  family's skill lists, not in their chats, not even if they were shared
  before. Moving a skill into internal stops sharing it, and it stays
  private if it is moved out again. The family cannot make an internal
  category of their own.

## 0.46.0 - 2026-10-28

- **Working steps say what they did.** While the assistant works, the steps
  it has finished fold into one line that says what they did - "Wrote
  SKILL.md, read it, edited it 2x, ran 1 command (1 failed on the way)" -
  with the steps themselves one click away. The step under way stays open
  below it: the thinking as it comes, or the call that runs, headed by what
  the thought before it said it would do. The line turns red only when the
  last step failed.
- **A thought is headed by what it decided** - "Let me read the file to see
  the exact questions block." instead of "Thought for 74 words". Click it for
  the whole thought.
- **No more "0 ms".** Reading and editing a file takes no time worth showing;
  a step's time shows from one second.
- **Fewer red "old_string was not found".** An edit now finds its text when
  the model's copy has read_file's line numbers in it, tabs that came out as
  spaces, a lost indentation or a stray space - in one place only - and the
  new text gets the file's indentation. When the text really is not there,
  the model is shown the most similar lines exactly, to copy from on its next
  try. The row says "The text to change was not found in SKILL.md".
- **Windows line ends are kept.** Editing a file with CRLF line ends (as
  TwinCAT's files have) no longer turns every line end into LF.

## 0.45.1 - 2026-10-28

- **The Skills menu closes when you pick a skill.** Choosing (or taking off)
  a skill in the Skills menu by the message box closes the menu; the skill
  shows above the message box as before.

## 0.45.0 - 2026-10-28

- **Categories are folders.** In the Skills menu by the message box, on the
  Skills page and in a project's settings, a category is closed until you
  click its name; skills in no category are listed straight away, first.
  The folders you open are remembered in the browser. With a search on the
  Skills page they open by themselves.
- **"internal" comes last and opens on three quick clicks.** A category named
  internal is listed at the very bottom and opens only when its name is
  clicked three times in a row; it is closed again the next time.

## 0.44.0 - 2026-10-28

- **A skill can bring its own category.** A skill whose SKILL.md says
  `metadata:` / `category: Billeder` lands in that category when you import
  it - handy for a set of skills. Once you move it, your choice stands, also
  when you import a new version.
- **A long question card scrolls.** A skill with many questions no longer
  pushes the message box off a phone's screen: the card scrolls instead.

## 0.43.0 - 2026-10-28

- **Question cards.** When a choice is yours and there are real options, the
  assistant now asks on a card with buttons - one choice or several - and
  waits for your answer (the new `ask_user` tool, like Claude's own cards).
  You can also write your own answer on the card, or just write a message
  instead. The row then shows what you chose. It works in people's chats too.
- **Skills with fixed questions.** A skill can carry questions with their
  options - "How many elves? 0, 1, 2, 3" - in a `questions` block in its
  SKILL.md. When you choose the skill, they show as a card above the message
  box; pick the answers, add a picture, and send - no text needed. The
  answers go with the message (and show in it), stay for the next messages,
  and Change gives the card back. The format, one question a line:
  `How many elves? | 0 | 1 | *2 | 3` (`*` picks an option from the start,
  `+` in front of the question allows several). The skill maker knows it, so
  "Make a skill" can make such skills.

## 0.42.0 - 2026-10-28

- **A step says what came of it.** A finished step in an answer no longer
  shows only what it was given (`bash curl -s "https://www.linkedin.co...`).
  It shows what came of it: the last line a command printed, "Failed
  (exit 6): curl: (6) Could not resolve host ...", "Timed out", "An HTML page,
  45 KB", "Created fib.py (124 bytes, 8 lines)", "3 results - first: ...",
  the page a fetch read and its size. A failure is red. While a step runs it
  still shows its command, and the full input and result are one click
  away as before.
- **Commands say what they are for.** The assistant now gives every bash
  command a few words for you - "Run the tests", "Henter LinkedIn-profilen" -
  in the chat's language. They head the step's row, with the outcome under
  them. Pages and Windows programs it tries (web_app, windows_app) can have
  them too.

## 0.41.0 - 2026-10-28

- **Skills in categories.** As you make more skills, put each into a category
  from its ⋯ menu → Category: pick one you have or type a new one. Private and
  shared skills alike; the family sees your shared ones under your categories.
  The Skills page lists them by category - each can be folded away, and the
  pencil by a category renames it for all your skills in it at once - with a
  search over names, descriptions and categories once there are more than a
  few. The Skills button by the message box and a project's settings list
  them by category too. The Hub's own skills are under "SLQ Hub", the rest
  under "No category" until you place them.

## 0.40.0 - 2026-10-28

- **The PC gets one folder of a project, not all of it.** Before, the
  assistant's own files sat beside your TwinCAT files and SLQ Sync copied them
  to your PC (in chat 157: `src/`, `NOTES.md`, `FB_MC2_AxisControl/`). A new
  project now has two folders. The **synced folder** is yours: you name it
  when you make the project (the field is filled with the project's name, so
  you can just type e.g. `twincat`), and SLQ Sync keeps your PC's folder the
  same as it. The PC client is unchanged. **hub/** is the assistant's: its
  notes (`hub/NOTES.md`), working files and drafts, the pictures, sound and
  video it makes, your chat attachments (`hub/attachments/`) and what you
  upload in Files without picking a folder. None of it reaches the PC, and its
  history is kept like the rest. The assistant is told which folder is yours
  and where its own files go. Neither folder can be deleted or renamed in
  Files, and the synced folder keeps its name. Projects made before 0.40.0
  still sync as a whole. A chat moved into a project that has the two folders
  brings its files to `hub/`.

## 0.39.0 - 2026-10-27

- **Windows in a container on the Hub's VM (optional).** For Windows-only
  tools the Hub should use - such as a TwinCAT build server for checking PLC
  code with the real compiler. Turn it on with `COMPOSE_PROFILES=windows` in
  `.env`, then `sudo bash update.sh compose up -d windows`. It installs Windows
  10 LTSC by itself (or the version you choose) and logs its user on. You
  reach it with Remote Desktop on the VM's address. It gets the sandbox's
  firewall rules: internet yes, your LAN and the Hub's other containers no.
  The Hub reaches it at `172.31.250.30`. If `.env` has no
  `WINDOWS_PASSWORD`, update.sh writes a random one there and shows it. See
  INSTALL.md, section 12.

## 0.38.0 - 2026-10-27

- **Effort: Quick, Think or Extra.** One chip under the message box replaces
  the thinking chip, as you suggested. Quick answers straight away. Think thinks
  first, each step at most half the thinking budget. Extra thinks hardest and
  also gets the independent review. A model with thinking levels shows them in
  the same menu for Think. Settings → Assistant sets the default for plain
  chats (Quick) and for projects (Extra), instead of the two thinking switches
  and the review switch. A project can have its own in its settings. In chat
  145 the review took 12-14 minutes of each turn; now it runs only at Extra.
- **A video made in a person's chat is not lost when it fails to arrive.**
  Sanne's video disappeared because the phone did not get the file, and the Hub
  still deleted its copy. Now the phone tries three times, and the Hub keeps the
  answer's files for an hour until the phone has them. Opening the chat again
  fetches what is missing. A file the phone has but cannot play says "this
  browser cannot play it - download it" instead of "not there any more".
- **Files of up to 100 MB reach a person's device** (was 25 MB), 250 MB per
  answer. Files too big to send with every message stay on the phone, and the
  assistant is told which ones they are.

## 0.37.0 - 2026-10-27

You asked for these when the local model cannot finish on its own. Both work
in chats of projects with "Let hard tasks go to the cloud model" on, with a
cloud model set up in Settings → Cloud.

- **Continue in the cloud.** The last answer has a "Continue in the cloud"
  button. The cloud model takes the work over from there and finishes it - also
  when the local model gave an answer you are not happy with. The chat shows
  "Continued in the cloud".
- **The cloud model in the model menu.** The chat's model menu lists the cloud
  model, e.g. "claude-opus-5-5 (cloud)". Pick it, and the chat's turns run in
  the cloud from the first step: your card stays free. A turn in the cloud is
  not checked by the independent review (the review runs on your card).

## 0.36.0 - 2026-10-27

For checking TwinCAT code with a real compiler, as you asked in chat 139. Both
changes work for any MCP server, not only TwinCAT.

- **An MCP tool can get files from the chat's folder.** A server can mark an
  argument as "files in the working folder". The assistant then gives the
  paths, and the Hub sends the files along with the call. A build server on
  your TwinCAT PC gets the code without the assistant copying it into the call.
  Only files inside the chat's folder go, up to 20 MB a file and 50 MB a call.
  When the server asks first, the question lists the files.
- **The independent review can use your servers' checking tools.** A new switch
  on a server, "The independent review may use it", gives the review that
  server's tools that say they only look - a TwinCAT build, say - so it can
  compile what was made. Only for a trusted server, because the review should
  not stop and wait for you. Settings → MCP marks those tools "only looks", and
  tools that take files "gets files".
- A server whose tool list has a tool without a proper name or description no
  longer breaks Settings → MCP or the turn.

## 0.35.2 - 2026-10-27

- **The comparison comes first in the browser check's result.** In long
  turns the Hub shortens older tool results to their first lines. The
  comparison with the original came last, so it was cut away. In chat 138
  the assistant ended by saying it couldn't tell you its own figures. Now
  the figures are the first thing in the result.
- **A new attachment with a taken name is told apart.** You attached the
  new CM98 kit, but an older `cm98-reference.zip` was already in the
  project, so the new one was saved as `8ff2ac8d-cm98-reference.zip`. The
  message said to unpack `cm98-reference.zip`, and the assistant unpacked
  the old kit. Now the assistant is told that the older file has that name,
  and that a request naming it means the one just attached.

## 0.35.1 - 2026-10-26

- **A reference in the folder is used, not redone.** In chat 136 the CM98
  folder held a finished stylesheet and a page that already matched the
  original. The assistant borrowed the class names but wrote its own
  stylesheet with its own measures. Its squad screen matched 65%, against
  77% for the page it had been given. The assistant is now told to use such
  a reference as it is: link it, and copy its classes, places and values. It
  should compare its result with the reference's own result and not stop
  below it.

## 0.35.0 - 2026-10-26

- **The assistant can set its page beside the original.** In chat 135 you
  gave it three screenshots of Championship Manager 98 to copy. It made its
  own "reference" picture with image_generate, sampled single pixels, and
  rewrote game.js twice. It never compared its page with your screenshots.
  Now `web_app` takes a picture to compare with. The page opens at the
  picture's size, and the assistant gets:
  - how much of the page matches,
  - where it differs most, with the colour on each side,
  - a picture of the original and the page side by side, with the
    differences marked in red.

  It can compare one part only, like the title bar. That is useful when the
  original has a photo behind it that the page doesn't have.
- **A picture you give it is the reference.** The assistant is told:
  - never to make a reference with image_generate,
  - to measure your picture in one script (size, colours, where things are,
    text sizes),
  - to build at its size and compare until they match,
  - when the task is how it looks, to change the look and leave how it
    works alone.

## 0.34.0 - 2026-10-26

- **A project's total now includes chats moved into it.** CM98 said 2.04 kr
  while one of its chats alone said 2.72 kr. That chat had done its work
  before you moved it in, and its use stayed outside the project. A moved
  chat now takes its use along. Chats moved before this update are settled
  once when the Hub starts. Settings → Usage keeps the same totals.
- **More about each chat on a project's page.** Under each title you see:
  - its answers,
  - the models that worked, in order (e.g. "Qwen3.8 Fable (U) → Qwen3.8
    Standard" after a hand-over),
  - how long its turns took,
  - the card's energy,
  - tokens read and written,
  - whether the last turn didn't finish or failed, and what the last review
    found.
- **Docs get a folder of their own.** The assistant puts documentation and
  write-ups, such as a file format described or a report, in `docs/`.
  NOTES.md stays at the top.

## 0.33.0 - 2026-10-26

- **Files show when they changed.** Every file in the Files panel shows its
  time: the clock today, the date before that, and the full time on hover. A
  folder shows its newest change. The new clock button beside the ZIP button
  sorts the newest first. Click it again for by name. The choice is
  remembered.
- **The assistant keeps the folder in order.** A program, site or game of
  several files goes in a folder of its own, extracted data in `data/`, and
  the scripts that make it in `scripts/`. It follows the structure that is
  already there and doesn't move your own files unless you ask. Scratch and
  test files go in a hidden folder (`.hub/tmp/`) instead of between your
  files, like CM98's `test_data.js`. To tidy an existing folder, ask it to.
- **After a hand-over, the chat stays with the model that took over.** When
  Fable hands a turn over to Standard, the chat now uses Standard from then
  on, and the model chip says so at once. Before, your next message switched
  the card back to Fable: a model load first, and the work back with the
  model that got stuck. Pick another model in the chip to go back.

## 0.32.1 - 2026-10-26

- **"Change it again" now changes the picture it made, not your original.**
  In a chat outside a project, a picture the assistant made or edited was
  shown but never saved, and nothing named it. So the next "change it" went
  back to the picture you attached. Made and edited pictures are now saved
  in the chat's folder (`images/…`), shown under the answer with Open,
  Download and Add to chat, and the assistant is told to change that newest
  file.
- **Combining pictures puts the scene first.** The result keeps the
  proportions of the first picture, so the assistant is told to pass the
  picture whose scene should stay as the first one. For example, the sofa
  comes first in "put the dog from picture 2 on the sofa in picture 1".

## 0.32.0 - 2026-10-26

- **The check of the work always ends with a report.** In chat 126 the
  reviewer used all its 24 steps in 33 minutes and gave no verdict. It now
  has a time limit as well - Settings → Assistant → "Time for the check",
  15 minutes by default. Near the end (3 steps before its 24, or after 80 %
  of its time) it is told to stop checking and write its report, and its
  last step can only answer. You always get its report and verdict, with
  what it did not get to check. The review shows when it was asked to
  finish.
- **The reviewer checks the work, not the task all over again.** It is told
  not to redo the analysis: it checks the files and artifacts the turn made
  against your request, and checks claims about data with a few samples.
  NOTES.md is no longer one of the files it checks. It holds the notes, not
  the work.
- **The export shows what the reviewer did.** It lists every call and a bit
  of what each returned, under the review in the transcript.
- **A script copied with the Hub's "[shortened by …]" mark in it is not
  run.** In chat 128 such a script failed with a SyntaxError, and two file
  writes of the same copy were refused. Together that was three failures in
  a row, so the turn was handed over to Standard. The assistant is now told
  what is wrong, as it already was for files.
- **Half an emoji in a call no longer breaks the export or the live view.**

## 0.31.0 - 2026-10-26

- **The work's notes as a file: NOTES.md.** The chat's notes are written into
  NOTES.md in its folder when the chat is condensed, continued in a new chat
  or its notes corrected - so they are under Files, can be edited, and go
  with the folder. Once you or the assistant change the file, the Hub leaves
  it to you. In long work the assistant is reminded to write what it has
  found there (the answer shows it), and when the file is there it is told
  to read it first. Chat 126 went 54 steps without notes. Settings →
  Assistant turns it off.
- **Move to a project.** A chat outside a project can be moved into a new
  project or one you have, from its ⋯ menu. Its files go into the project's
  folder - nothing there is overwritten - and from its next answer on it
  works with the project's instructions, files and sync.

## 0.30.2 - 2026-10-26

- **"Pause" is respected by the review.** When you write while the assistant
  works ("kan du holde en pause?") and it answers that, its work is not
  checked and sent back to go on - the review runs when it ends the work
  itself.
- **Notes for Continue in a new chat give paths inside the folder.** Chat
  126's notes pointed at the old chat's folder and at two files that did not
  exist, and the new chat spent its first minutes looking for them and
  analysing the database files again.

## 0.30.1 - 2026-10-26

- **Settings → Tests judges the right cut-out.** "Remove a background" looked
  only at the newest picture the turn left. Qwen3.8 27B made its cut-out
  correctly and then a preview of it on a chessboard (opaque on purpose), so
  the case failed although the cut-out was right (94.8 % transparent). It now
  judges the newest picture with a transparent background at the original
  size.

## 0.30.0 - 2026-10-26

- **A limit on thinking, and Stop thinking.** One step now thinks at most
  8,000 tokens (Settings → Assistant → "Most thinking in one step"; 2,000 to
  16,000, or no limit); then it stops thinking and acts on what it has - a
  tool call to check something, or its answer. In chat 78 single steps
  thought for nine minutes. While it thinks, **Stop thinking** beside the
  thinking does the same at once, in your chats and in people's.
- **The work is checked by another model before the answer.** When a turn
  has made or changed code, pages, documents or an artifact, another model -
  by default the one a stuck turn is handed over to - checks it in a fresh
  context the way you would use it: it runs the program, opens the page or
  plays the game, and reports. With problems, the model that did the work
  goes on from the report and fixes what holds up. It shows in the answer as
  **Independent review**; Settings → Assistant turns it off or chooses who
  checks ("the model that did the work" avoids switching models). It takes
  a few minutes more, once a turn; test runs are not checked.

## 0.29.0 - 2026-10-26

- **The assistant can see and play what it makes for the browser.** A new
  tool opens a web page, a browser game or an HTML/React artifact in a
  browser in the sandbox, clicks, types and holds keys as a player would,
  and gets screenshots, what the page wrote to its console, errors with the
  file and line, and files that failed to load. In chat 79 the game was
  never opened; opened now, its data file has a broken line (an apostrophe
  in "Japhet N'Doram") that stops the team and player lists from working.
  Needs update.sh: it adds the browser to the sandbox. The self-test checks it.
- **New working rules for the assistant:** look for existing documentation
  and tools first, keep notes in a file during long work, check a result the
  way you will use it before calling it done, and say what it checked and
  what it could not check.
- **Download as ZIP.** "Download all" on the files under an answer gives
  them as one ZIP (a game's files and its data folder together), and Files
  has a button for the whole folder.
- **React artifacts work again.** Every React artifact showed a Babel error
  in the side panel instead of the app.
- The assistant can no longer write the Hub's "[shortened by N characters]"
  marks into a file - chat 79's game got one in its heading.

## 0.28.4 - 2026-10-25

- **Long thinking turns waste less time re-reading.** In chat 78 the stronger
  model read its whole conversation again at four steps in a row, about a
  minute each: the Hub trimmed a little at every step, and models like
  Qwen3.8 can only pick up from near the start after any change. Now the
  older steps' thinking - most of such a turn - is cleared in one go with
  old tool output, room is made well below the limit, and something cut once
  stays exactly as it was.
- No pointless condensing: a chat with almost nothing before the current
  turn (like a fresh continued chat) is no longer condensed.
- **Notes for Continue in a new chat** give the files with their paths and say
  which findings were checked and which are still guesses.

## 0.28.3 - 2026-10-25

- **A tidier "Working with ..." line on a phone.** The model's name and the
  figures (time, steps, tokens/s) each wrapped into two lines side by side;
  now the figures sit on their own line under the name. On a desktop they
  stay on one line. Also in people's chats.
- Less empty room between the last line of a chat and the message box on a
  phone.

## 0.28.2 - 2026-10-25

- **Live thinking reads like a terminal.** While a model thinks, its box
  follows the end: new lines appear at the bottom and nothing already on
  screen moves. It used to show the last 1500 characters from their start,
  so on a phone the words crept backwards while the newest ones were out of
  sight below the box. Scroll up in the box to read, and it stays where you
  are until you scroll back to the end. In the phone chat (/chat) too.

## 0.28.1 - 2026-10-25

- **Settings → Assistant → Video tells the truth about first-to-last videos.**
  The help under "Model for a video from a picture" named Wan 2.2 14B picture
  to video even when it was not downloaded. It now says which downloaded
  model makes a video from a first to a last picture - or that none does
  (the 5B models start from one picture only). The assistant is told the
  same, so it says so instead of trying.

## 0.28.0 - 2026-10-25

- **The same vocals, a new band - in one go.** Chat 76's second try split the
  song, remade the music and mixed the vocals back - but the assistant's own
  mix put the vocals 11 dB under the band. Now a remake can keep the song's
  own vocals itself: it splits the song, remakes only the band, lays the
  original vocals back over it at their own level, and saves the new band
  alone as well. Needs Mel-Band RoFormer vocals (Settings → Models).
- **Every remake says whether it kept the beat.** The assistant cannot hear,
  so the Hub measures it: the song's and the new version's tempo and key, and
  how many beats line up - and the assistant tells you before you listen
  when the beat was lost. On chat 76's six remakes: keep 0.6-0.85 kept the
  beat, 0.4-0.5 did not.
- **Keep is 0.6 by default** (0.5 lost the song's tempo and key in chat 76).
- update.sh rebuilds the ComfyUI image: it has a new node for the beat check.

## 0.27.1 - 2026-10-25

- **Remakes, after chat 76.** A remake plays the whole recording again, the
  voices too, and its one setting (keep) turned out to have a cliff: 0.75 and
  0.85 sounded like the original, 0.45 and 0.5 lost the rhythm. The assistant
  is now told what was measured, to try 0.6-0.7, and how to keep the voices
  exactly while only the band changes: split the song, remake the music
  alone, and lay the original vocals back on top.
- The sound tools find an attached file by its name, and the assistant is
  reminded that it cannot hear what it made: no more descriptions of guitars
  that are not there.

## 0.27.0 - 2026-10-25

- **Long chats stay sharp.** Older answers' tool output is kept short (the
  previous answer's stays whole), and in a long turn the old results are
  cleared in one go at half of the model's context, long before it runs out.
  When a chat is condensed, it now becomes structured notes - the goal,
  decisions and facts, files, what is done, open questions, what is next -
  that replace the old ones instead of piling up.
- **Notes** in a chat's ⋯ menu: read what the earlier part of the chat was
  condensed into, and correct it.
- **The assistant can look anything up in the whole chat**, also what was
  condensed or cleared, and in the chat it continues from - in people's chats
  too, where it searches the conversation their phone sent.
- **Continue in a new chat** (⋯ menu, or the hint under a long chat's last
  answer): the assistant writes notes from the chat, and a new chat starts
  from them with the same files and settings. The old chat stays as it was,
  and the new one links back to it.
- Big reads - many files, long logs, hex dumps - go to a helper that reads
  in its own context and reports back.
- **Delete files** for downloaded built-in models under Settings → Models:
  the disk space back, and Download fetches them again. Files another
  downloaded model uses stay.
- Security: links that code in the sandbox could plant in the workspace are
  no longer followed by the Hub (they could have let a cleanup delete the
  Hub's own data, or a chat read its settings).

## 0.26.0 - 2026-10-25

- **Updates wait until nobody uses the Hub.** `sudo bash update.sh` can be
  started at any time: it builds the new version while the Hub runs, then
  waits until the Hub has had nothing to do for a minute - no chat answering
  or waiting for an answer, no person's chat, picture, video, test run,
  library or download - before it stops it for the backup. The waiting and
  installing run on their own, and the window shows them: Ctrl+C or closing
  it does not stop the update, and never in the middle of a backup or a
  start. `sudo bash update.sh cancel` stops an update that still builds or
  waits (nothing has been stopped then), `--now` skips the wait, and an
  update that was cut off (the VM reset, say) is put right by the next
  `update.sh`. The update to 0.26.0 itself can only watch the card's load:
  the running 0.25.0 cannot say what it does.
- **Long chats no longer run out of room unnoticed.** The Hub estimated a
  request's size from its characters, and text such as hex dumps costs far
  more tokens than that: chat 74 was at 62,286 tokens where the Hub counted
  45,004, so it was never condensed and stopped with "request exceeds the
  available context size". Every step now measures the real count and
  condenses or shortens in time, and a request the model server still finds
  too long is shortened and sent again.
- **Context per chat** in Settings → Models → Settings, beside the context.
  Raising the context gave a chat no more room before: it kept the model
  card's 65,536. The dialog says what one chat may use now and what the model
  was trained for; whether a bigger context fits on the card shows when the
  model loads.
- Cancelling a download no longer leaves the other models' downloads waiting
  for ever, and a cancelled model can be downloaded again right away.
- The troubleshooting export's `context.json` has the measured token ratio.

## 0.25.0 - 2026-10-25

- **Faster video: Wan 2.2 5B Turbo.** A new video model under Settings →
  Models → Video models: the 5B in 4 steps instead of 20, without the second
  pass each step needed - a tenth of the work for the same 5-second 720p clip,
  which took about a quarter of an hour. Beside Wan 2.2 5B it is a 332 MB
  download (it uses the 5B's files). Once downloaded it is the default for
  video from a description and from a picture; ask for "the best quality" to
  have one clip made with the 5B's 20 steps, or choose Wan 2.2 5B under
  Settings → Assistant → Video. The Turbo is licensed for non-commercial use.

## 0.24.1 - 2026-10-25

- **The queue's times count up.** The GPU queue showed each job's running or
  waiting time as it was at the queue's last change - a turn that had worked
  for minutes said "running 0 ms". The times now count on while it is open.

## 0.24.0 - 2026-10-25

- **What it costs.** Under each answer: the energy the card used for it and
  what that cost, at 3.00 kr per kWh. A chat's ⋯ menu adds its answers up, and
  a project's page shows its total and each chat's cost. The energy is the
  graphics card's own power reading while it works for you, shared between the
  jobs on it at the time.
- **Settings → Usage**: for today, this month or all time, what you, each
  person, each app key and the tests used - answers, tokens read and written,
  time on the card, energy and cost - plus what the card drew while idle, and
  what the card draws right now. The electricity price is set here, and so is
  what the rest of the PC draws while the card works, if you know it (0
  counts the card only). Usage is kept per day, so a limit for a person can be
  added later.
- The help's "The Hub" section tells where to find the costs.

## 0.23.0 - 2026-10-25

- **Make your own skills.** Attach your material - PDF, Word, Excel,
  PowerPoint - and say what the skill should do: the assistant reads it all,
  writes the skill in the format Claude uses, tries it and shows a card with
  **Add skill**. Start one from the Skills button by the message box (Make a
  skill), the Skills page (New skill) or the help's examples. Excel files are
  read sheet by sheet, values and formulas, and PowerPoint files slide by
  slide, also in chats that run no code.
- **A Skills page** in the sidebar, by Projects: your skills, the ones the
  family shares and the Hub's own. Share a skill with the family or keep it
  to yourself, improve it with the assistant, edit its SKILL.md, download it
  as a .zip for Claude, import one from Claude (.zip, .skill or SKILL.md), or
  delete it. Everyone the owner added has their own skills too. Settings →
  Skills is gone; its address opens the new page.
- **You choose the skills for each chat.** The Skills button lists them, or
  type / and a skill's name in the message. The chosen ones show above the
  message and the assistant follows them; a project's settings can choose
  skills for all its chats. Before, the skills switched on in Settings were
  offered in every chat for the assistant to pick from.
- The help's "Memory and skills" section tells how to make, use and share
  skills.

## 0.22.3 - 2026-10-25

- **The video model you set is the one used.** The assistant now keeps to
  Settings → Assistant → Video unless you ask for a model by name, as it
  already did for pictures. In the second test run it chose the 14B pair for
  a picture on its own, and that job stalled for minutes.

## 0.22.2 - 2026-10-25

- **Edits and cut-outs at the original's size.** A picture you edit or cut
  out came back at the model's working size, about one megapixel - the test
  runs' 768×512 ball came back at 1248×832, and the assistant said it was the
  same size. It now comes back at the original's size (up to 2048×2048;
  a cut-out keeps clean edges), and the assistant is told the size.
- **A video job is followed to the end.** In the second test run the
  connection to ComfyUI closed during a 14B video, and the Hub gave the job
  up and stopped it. The Hub now keeps asking ComfyUI about the job instead,
  and if ComfyUI restarted and lost it, that is said at once rather than
  after five minutes.
- **Tests judge the result, not the way to it.** The second run's model
  turned the ball blue with its own code - exactly right - and failed because
  it did not use the edit tool; the edit and the cut-out now judge the picture
  the answer left, however it was made. Also: the invitation is checked up to
  its "Venlig hilsen" and signature, with anything after it as a check of its
  own; the poster's two lines are read whole (two right posters failed on a
  letter Tesseract dropped); the converter's formula is found when written
  `(c * 9) / 5`; and a video that jumps between flat colours fails a new
  check, "it moves smoothly".

## 0.22.1 - 2026-10-25

- **Video that ran out of memory at the very end.** In the first test run on
  the card, Wan 2.2 5B's 5-second 720p clip sampled for about 11 minutes and
  then ran out of memory turning the result into frames - four times. Video
  is now decoded in tiles (512 px, the whole clip at once in time), which
  needs a fraction of the memory.
- **The card after a failed job.** ComfyUI then stopped answering while it
  held the card, and no chat model could load after it: the last seven test
  cases failed in seconds. Before a chat model loads, the Hub now makes sure
  the card is empty - ComfyUI is told to stop its work, drop its queue and
  free the card - and a model that did not fit is loaded once more after
  that. A job the Hub gives up on is cancelled in ComfyUI, and a ComfyUI that
  is slow to answer while a job runs is waited for instead of failed.
- **No hand-over for a failure of the card.** Three failed picture, sound or
  video calls in a row no longer hand the turn to the stronger model, which
  would meet the same card; running out of memory tells the assistant not to
  repeat the call but to ask for less, or say so.
- **Tests.** The video cases run last, so a failure there cannot cost the
  others their run. The Danish invitation is checked for a wrong weekday
  (Qwen3.6 called Friday 20 November a Thursday). A passed "was made" check
  names the file instead of what a failure would say, and a word Tesseract
  read on the poster says how it was read.

## 0.22.0 - 2026-10-25

- **Not finished, and Continue.** An answer that reached the most steps one
  turn may take (Settings → Assistant, 60 by default), or that you stopped,
  now says so: "Not finished" in an amber bar in its files card - the files
  stay listed as "files so far", since they may be whole or half-written -
  or on its own when it made no files. **Continue** under the last answer
  goes on from there: the assistant is told why it stopped and to check what
  it has made instead of starting over, and the chat shows a small
  "Continued" mark rather than a message from you. The same in people's
  chats. In chat 21 the card showed the game's file as if it were done.

## 0.21.0 - 2026-10-25

- **Tests.** Settings → Tests gives the assistant 24 hard tasks in every
  area, each run as a real chat on your Hub - the same models, queue, tools
  and sandbox as your own chats, at a lower priority so your chats go first:
  a logic puzzle, a word problem, JSON from an e-mail, Danish under strict
  rules, a year of sales figures in Danish number format, a fact a manual
  changes on a later page, a needle in a server log, a Word report, a budget
  with formulas, a poster with text, a picture to edit, one to cut out, a
  sign to read, Danish speech, music, a sound effect, two videos, a Python
  module, four bugs to find, a C# program, an interactive page, research on
  the web and a task in several steps. Choose the chat model, thinking and
  the tests; tests whose model is not downloaded say what they need.
- Each answer is checked where a machine can: the answer's numbers and
  wording, the tools it used, and - in the sandbox - what it made: the chart,
  the Word and Excel files, the pictures' colours and transparency, the
  poster's text, the sound's length and loudness, the video's motion, and
  hidden tests run on its code. A result shows passed, in part or failed,
  with every check, where the time went and what to look at yourself.
  **Export** makes a ZIP with an overview, every answer and file, and each
  test chat's troubleshooting export - send it to Claude for a closer look.
  The test chats are not in the chat list; open them from the results. A run
  is kept until you delete it, with its chats and files.
- **A file written in parts is not replaced by mistake.** In chat 21 the
  model twice sent the next part of the game without adding it to the end,
  replaced the whole file with it, and spent 12 of its 60 steps building it
  again. That call is now refused with an explanation, unless the model says
  it wants to start over.

## 0.20.2 - 2026-10-25

- **Remakes stay closer to the original.** Chat 24's live remake of "Breakfast
  at Tiffany's" came back in another key and tempo - a new song. Now a remake
  without a style clip keeps the song's own sound (the model hears 10 s of
  its start, middle and end) and starts from the recording itself by default
  (keep 0.5; ask for more or less of the original). The result says how well
  the song's melody codes were read, and warns when they were not.
- **The same recording, only live.** Ask to keep the exact recording but make
  it sound live, and the assistant adds a big hall and a cheering crowd with
  effects - the recording itself stays as it is. In Help under Sound and
  music.

## 0.20.1 - 2026-10-25

- **Easier to see which help section you are in.** The sections are now
  buttons that wrap onto several lines - all of them show on a phone - and
  the chosen one is filled; its name heads the section below.
- Tabs in Settings and on a project page show the chosen tab more clearly (a
  tinted tab with a thicker line), no longer have a scrollbar lying over it on
  a phone, and scroll to it when it starts off the edge of the screen.

## 0.20.0 - 2026-10-25

- **Help.** A new page - Help in the sidebar, or "What you can ask for" on a
  new chat - with everything you can ask the assistant for, in sections:
  Chat, Documents and files, Images, Sound and music, Video, Code and
  programs, Memory and skills, and The Hub. Each part has examples: **Try**
  puts one in a new chat for you to change and send, and Copy copies it.
  Search finds anything on the page ("karaoke", "PDF", "video").
- It shows what works on your Hub right now: a part whose model is not
  downloaded says which one it needs and where to get it (Open Settings), and
  its examples wait until it is there. People you have added get the same
  help in their chats, with what applies to them.

## 0.19.0 - 2026-10-24

- **Video.** Ask for a video in any chat: from a description ("a red fox runs
  through fresh snow at dawn, low tracking shot"), from a picture you attach
  or one the assistant just made ("make this picture move - the camera slowly
  pushes in"), or from a first to a last picture. It is saved as an MP4 in the
  chat's folder (in a project under `video/`) and plays under the answer.
  Clips are 5 s by default (up to 10), without sound - the assistant can put
  music or sound effects under them with ffmpeg. It takes minutes on the card.
- Three models under Settings → Models → Video models, none downloaded by
  default: **Wan 2.2 5B** (description and picture, 720p, 24 fps; 16.9 GB)
  and the larger **Wan 2.2 14B** pair - one for pictures (and first-to-last
  pictures), one for descriptions (480p by default, 16 fps; 35.4 GB each,
  6.5 GB of it shared). The 14B models run fast with 4-step LoRAs, or at full
  quality when asked. Settings → Assistant → Video chooses which model is
  used for a description and for a picture.
- **Add from Hugging Face** now says when a search matches a model in the
  Hub's own list, such as Seed-VC or Wan, and where to download it - that
  dialog adds chat models (GGUF files) only.

## 0.18.0 - 2026-10-24

- **Another voice on a recording.** Attach a song or a recording and a 5-30 s
  clip of a voice, and the assistant makes it sound as if that person sang or
  spoke it (`voice_convert`, Seed-VC): the words, timing and melody stay. In
  a song the vocals are separated from the music, converted and mixed back;
  a clip cut from another song works as the voice. The melody moves by whole
  octaves to fit the voice (a man's song in a woman's voice goes up an octave
  and stays in key), or by the semitones you ask for.
- **Re-make a song in another sound.** `music_remake` re-performs a song with
  its melody, rhythm and structure - in a style you describe and/or the sound
  of a clip from another song: a live recording's audience, room, reverb and
  feel. Give the lyrics to keep the words; `keep` stays closer to the
  original recording.
- **Songs that take on a clip's sound.** `music_generate` takes a reference
  clip - a singer, another song's sound. It steers the sound; for a song in a
  particular person's voice, make the song and then convert its singing.
- **Stems.** `audio_separate` gives a song's vocals and its accompaniment
  (karaoke, remixes, an a cappella).
- Two new models under Settings → Models → Sound, music and speech: Mel-Band
  RoFormer vocals and Seed-VC (not downloaded by default). The ComfyUI image
  gets the Hub's own nodes for this, so the update rebuilds it (a few minutes
  longer than usual).

## 0.17.0 - 2026-10-24

- **Models in your own order.** Settings → Models has arrows on each language
  and picture model: move them up or down, and the chat's model menu (and
  the picture menu, people's chats and the phone page) lists them in that
  order. A model added later goes last.

## 0.16.2 - 2026-10-24

- **A tool call cut off at the end of the context no longer breaks the chat.**
  In chat 21 the assistant wrote a whole game into one file; after 13
  minutes the context was full in the middle of it, and the next step failed
  with "Failed to parse tool call arguments as JSON" - as every later message
  in that chat would have. Now the assistant is told the call was cut off,
  where and why, and a file it was writing keeps what was written, so it adds
  the rest instead of starting over. A broken tool call never goes back to the
  model server, so chat 21 itself can go on.
- `write_file` can append, and the assistant is told to write long files in
  parts.

## 0.16.1 - 2026-10-24

- **Typing into Windows programs is exact.** In 0.16.0 `windows_app` typed
  through the X server, and under Wine that lost the first key, turned Æ Ø Å
  (every Latin-1 letter) into "�", put "�" for every letter into WPF, and gave
  a busy program the wrong letters. Now a small Windows helper in the sandbox
  (HubKeys) types each text with SendInput, as Windows' on-screen keyboard
  does: Danish letters, accents, other scripts and emoji arrive as typed,
  however busy the program is, and a text typed after a click waits until the
  program has taken the click. `\n` in a text is Return, `\t` Tab.
- The self-test's "windows programs" line also types "Søren: ÆØÅ æøå" into its
  program, and passes only if exactly that arrives.
- The sandbox image builds HubKeys with its Wine prefix (seconds, nothing to
  download).

## 0.16.0 - 2026-10-24

- **The assistant sees Windows programs.** A Windows Forms or WPF program it
  builds runs under Wine on a virtual screen in the sandbox (`windows_app`):
  it gets screenshots when the window is up and after the steps it gives -
  clicks (by position, or by the text on the screen: "OK", "Start"), typing,
  keys, dragging a slider, scrolling - so it can check what a form looks like
  and that its buttons, lists, menus and dialogs work before it hands the
  program over. The screenshots show in the tool's row. Wine is close to
  Windows, not the same: try the program on a PC too. Owner's chats only.
- **Windows projects build offline.** The Windows packs are in the sandbox
  image now, so Windows Forms and WPF projects build without NuGet, and the
  program is handed over as a folder with its .exe (`dotnet publish -p:DefaultAppHostRuntimeIdentifier=win-x64`)
  for a PC with the .NET 10 Desktop Runtime.
- **Allow in this chat.** In your own chats, a question - a search, a site,
  code online, an MCP tool - also offers **Allow in this chat**: the same kind
  of call goes ahead without asking for the rest of the chat (its subagents
  too), and the chat's ⋯ menu lists what was allowed there, each with Take
  back. It ends with the chat.
- **A question no longer holds the card.** While the chat waits for your
  answer, pictures, music and other chats can use the GPU; after the answer
  the chat waits its turn if it must (it says so), then goes on.
- **Artifacts load nothing from the web while asking first.** Web pages and
  apps the assistant makes (HTML, React, SVG, diagrams) may use only their own
  code and the Hub's own libraries then - no pictures, scripts, fonts or
  connections from other sites, no pop-ups, and their frame cannot navigate
  away (reload the page after switching to Ask first). Allowed, as before.
- **Add from Hugging Face**: a repository with each quantisation twice (with
  and without MTP, like JonathanColetti's Qwen3.8 27B Uncensored) showed both
  with the same name - now `Q5_K_M` and `noMTP-Q5_K_M`; a vision file not
  named mmproj (`...-vision-f16.gguf`) is offered as vision instead of as a
  0.9 GB model; MTP heads and draft models are left out; and a parameter
  count that describes such a small file (HauhauCS's "1.9B") is not shown.
- **The updater finishes with its new version.** When an update changes the
  updater itself, the new one does the rest of the update (build, backup,
  start, firewall), so a new step works in the update that brings it - from
  the next update on (this one still runs 0.15.1's updater).
- The self-test has a new line, "windows programs": a small Windows Forms
  program built offline and run under Wine, its button found and clicked.
- The sandbox gets an init process, which collects what commands leave
  behind (Wine's background processes after each run).
- The UI's page is never served from the browser's cache (a new version, and
  the internet setting, apply at the next load).
- The sandbox image grows by about 1 GB (Wine, the Windows .NET runtime and
  packs); the update downloads about 320 MB more while building it.

## 0.15.1 - 2026-10-24

- **C# in the sandbox.** The assistant can write, build and run C# with the
  .NET 10 SDK: console programs, libraries, ASP.NET, tests.
- **Windows Forms and WPF** build in the sandbox but cannot run there
  (Windows only, no screen). The assistant builds them to catch errors and
  hands you the program as one .exe (`publish/` in the chat's or project's
  folder, in the files card): small, for a PC with the .NET 10 Desktop
  Runtime - or, if you ask, self-contained (about 150 MB). It writes the
  forms in code; there is no designer here.
- Build output (`bin/`, `obj/`) stays out of the folder - in `.hub/dotnet` -
  so a build is not synced to your PC and does not mix with Visual Studio's.
  Nothing of a build keeps running in the sandbox afterwards.
- With the internet asking first, a build that needs NuGet packages asks the
  first time (a Windows program always does); after that it builds offline.
- The self-test's sandbox line shows the .NET version.
- Update backups leave out the sandbox's NuGet packages and .NET build
  output, which come back by themselves.
- The sandbox image grows by about 600 MB.

## 0.15.0 - 2026-10-24

- **The internet can ask first.** Settings → Assistant → Web → **Internet
  for the assistant**: Allowed (as before) or **Ask first**. Asking first,
  the chat stops at the step and asks - like Claude does - with what it
  wants: the search, the page or the command. **Allow once**, **Always**
  (per kind: "Always allow web search", "Always allow www.dr.dk" per site,
  "Always allow internet for code"), or **Deny**, which the assistant hears
  and works around (a no is not a reason to hand the turn to a stronger
  model). A subagent's question shows inside its row.
- Asking first, code runs **without the network** (no connections, no DNS)
  unless the assistant says a command needs it (`pip install`, `git clone`),
  which asks. Allowed once, whatever the command leaves running is stopped
  with it. Where the sandbox cannot keep code offline, every command asks.
  The self-test has a new line for it: "offline code".
- A page that redirects to another site is not followed without asking, and
  pictures from the web in answers show as links instead of loading by
  themselves (a picture's address can carry data out).
- What you always allow is listed under **Always allowed online** in the Web
  section; **Ask again** takes it back.
- People are asked in their own chats, once each time (no "always": their
  chats leave nothing on the Hub, and your answers are yours).
- **web_fetch cannot be steered into the LAN.** It already refused local
  addresses, but looked the name up twice: a name server could answer with a
  public address for the check and a local one for the connection (DNS
  rebinding). Now it connects to the address it checked, every redirect too.
  Pictures that apps send by address are refused on the home network's
  addresses too, not only the private ranges (30.11.0.x is public address
  space).
- **The model containers have no way out.** llm, comfyui and speech now sit
  on their own network, and the VM's firewall stops them from connecting to
  the internet, the LAN or the VM itself. They need nothing outside - the Hub
  downloads the models - so a model file, a ComfyUI node or a library in them
  cannot phone home. **Once, after this update:** `sudo bash
  /opt/slq-hub/update.sh firewall` (the update runs the old updater, which
  does not know the new rules). From the next update on, the updater keeps
  the firewall current by itself.
- `update.sh status` and `update.sh firewall` (and every update from now on)
  try each model container's way out and say "no way out" - or which one
  still reaches the internet.
- Fixed: when a person's chat was handed over to the stronger model, that
  model got your system prompt - your name and the list of your memory files
  with their first lines - instead of hers.
- Fixed: the picture text tool (OCR) put file names into its command with
  the wrong quoting; a name with `$(...)` in it would have run as a command.

## 0.14.0 - 2026-10-24

- **Rename models.** Settings → Models → Rename gives a chat model or a
  picture model your own name. It shows everywhere a name is read: the
  composer, under each answer, the picture menu, hand-over notices, tool
  results, Settings, the queue, people's chats and the phone page. "Use
  Qwen3.8 27B" (its own name) goes back. Apps are not affected: they keep
  using the model's id (`qwen3.8-27b`). Two models cannot share a name.
- The model itself is told both names ("You run as Store hjerne (Qwen3.8
  27B)"), so it still knows what it is.
- A picture's result names the model by its shown name instead of its id.
- An added picture model is renamed from the list now, like the others,
  instead of in its Settings.

## 0.13.6 - 2026-10-24

- **An open page notices an update.** A page keeps running the version it
  loaded until it is reloaded, and a phone's tab can stay open for days -
  the missing player under the jingle on Søren's phone was most likely a tab
  from before the files card (0.12.0). Every answer from the Hub to the page
  now carries its version; when it changes, the page says "SLQ Hub was
  updated to … - Reload". It also checks when the page comes back into view.
- **A lost connection no longer loses the answer.** When a phone lost its
  network or slept as a turn ended, the chat stayed as it was before the
  turn until a reload. Now the page keeps what came in and fetches the
  finished turn when the connection is back. If the stream breaks while the
  turn still works, the page picks it up again.
- The design text no longer lists the speech speed under Settings.

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
