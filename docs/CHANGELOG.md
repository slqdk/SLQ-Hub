# Changelog

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
