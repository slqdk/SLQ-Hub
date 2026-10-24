// The help page's text (0.20.0): what you can ask the assistant for, section by section, with examples to try.
// Whether each thing works on this Hub right now comes from GET /hub/api/help (backend/app/ui/help_routes.py),
// which uses the same checks that decide which tools a turn is offered. Keep this in step with the tools.

export interface HelpCaps {
  member: boolean;
  internet_asks: boolean;
  code: boolean;
  windows: boolean;
  memory: boolean;
  projects: boolean;
  libraries: boolean;
  mcp: number;
  handover: boolean;
  images: { generate: boolean; edit: boolean; cutout: boolean };
  sound: { speech: boolean; music: boolean; sound: boolean; voice: boolean; separate: boolean };
  video: { t2v: boolean; i2v: boolean; flf2v: boolean };
}

export type Need = "code" | "windows" | "libraries" | "mcp" | "handover"
  | "images.generate" | "images.edit" | "images.cutout"
  | "sound.speech" | "sound.music" | "sound.sound" | "sound.voice" | "sound.separate"
  | "video.t2v" | "video.i2v" | "video.flf2v";

export interface Example {
  text: string;
  attach?: string;            // what to attach before sending ("a picture", "a song and a voice clip")
}

export interface HelpItem {
  id: string;
  title: string;
  what: string;
  examples?: Example[];
  how?: string[];             // for what is done with the buttons rather than asked for
  tips?: string[];
  needs?: Need[];             // all of them must be there
  tools?: string[];           // the assistant's tools it is about (a backend test checks every tool is here)
  owner?: boolean;            // only in the owner's chats
  member?: boolean;           // only in a person's chats
}

export interface HelpSection {
  id: string;
  title: string;
  memberTitle?: string;       // the title in a person's chats, where some of the section is not there
  intro: string;
  items: HelpItem[];
}

/** What is missing and where the owner gets it (a person is told to ask the owner). */
export const NEEDS: Record<Need, { missing: string; get: string; tab?: string }> = {
  "code": { missing: "Code is not allowed in your chats", get: "The owner can allow it under Settings, People." },
  "windows": { missing: "The sandbox cannot run Windows programs",
    get: "Its image needs Wine and the Windows packs; update.sh builds it. Settings, System shows the sandbox.", tab: "system" },
  "libraries": { missing: "Needs the embedding model", get: "Download Qwen3 Embedding under Settings, Models, Helpers for libraries; then make a library.", tab: "models" },
  "mcp": { missing: "No MCP server is connected", get: "Add one under Settings, MCP servers.", tab: "mcp" },
  "handover": { missing: "No model is chosen to take over", get: "Choose one under Settings, Assistant: When the model gets stuck, hand over to.", tab: "agent" },
  "images.generate": { missing: "Needs a picture model", get: "Download one under Settings, Models, Image models (Qwen-Image 2.1, FLUX.2 [klein], ERNIE-Image, Z-Image, Chroma).", tab: "models" },
  "images.edit": { missing: "Needs a model that edits", get: "Download Qwen-Image 2.1 or FLUX.2 [klein] 4B under Settings, Models, Image models.", tab: "models" },
  "images.cutout": { missing: "Needs Qwen-Image 2.1", get: "Download it under Settings, Models, Image models.", tab: "models" },
  "sound.speech": { missing: "Needs a voice model", get: "Download Røst v3 Turbo (Danish) or Chatterbox Multilingual V3 under Settings, Models, Sound, music and speech.", tab: "models" },
  "sound.music": { missing: "Needs a music model", get: "Download ACE-Step 1.5 under Settings, Models, Sound, music and speech.", tab: "models" },
  "sound.sound": { missing: "Needs a sound effects model", get: "Download Stable Audio 3 Medium under Settings, Models, Sound, music and speech.", tab: "models" },
  "sound.voice": { missing: "Needs Seed-VC", get: "Download Seed-VC (and Mel-Band RoFormer vocals, for songs) under Settings, Models, Sound, music and speech.", tab: "models" },
  "sound.separate": { missing: "Needs Mel-Band RoFormer vocals", get: "Download it under Settings, Models, Sound, music and speech.", tab: "models" },
  "video.t2v": { missing: "Needs a video model", get: "Download Wan 2.2 5B or Wan 2.2 14B text to video under Settings, Models, Video models.", tab: "models" },
  "video.i2v": { missing: "Needs a video model for pictures", get: "Download Wan 2.2 5B or Wan 2.2 14B picture to video under Settings, Models, Video models.", tab: "models" },
  "video.flf2v": { missing: "Needs Wan 2.2 14B picture to video", get: "Download it under Settings, Models, Video models.", tab: "models" },
};

export function has(caps: HelpCaps, need: Need): boolean {
  const [group, key] = need.split(".") as [string, string | undefined];
  if (key) return Boolean((caps as unknown as Record<string, Record<string, boolean>>)[group]?.[key]);
  const v = (caps as unknown as Record<string, unknown>)[group];
  return typeof v === "number" ? v > 0 : Boolean(v);
}

export const SECTIONS: HelpSection[] = [
  {
    id: "chat",
    title: "Chat",
    intro: "Write as you would to a person, in Danish or English. The assistant picks its own tools for the job - you "
      + "can also say which (\"search the web for...\", \"make a picture of...\"). The models run on this Hub's "
      + "own graphics card.",
    items: [
      {
        id: "ask",
        title: "Ask, explain, write",
        what: "Questions, explanations, advice, ideas, translations, letters and texts to rewrite. Answers can have "
          + "tables, formulas and code.",
        examples: [
          { text: "Explain how a heat pump works, as if I were 12" },
          { text: "Translate this email to German, friendly but formal: [paste the email]" },
          { text: "Give me ten names for a hockey podcast, each with a one-line pitch" },
          { text: "Rewrite this so it is half as long and easier to read: [paste the text]" },
          { text: "What are the pros and cons of an electric car for someone who drives 25,000 km a year?" },
        ],
        tips: ["A long chat is condensed by itself when it nears what the model can hold; start a new chat for a new "
          + "subject - it answers faster."],
      },
      {
        id: "model",
        title: "Choose the model, and how hard it thinks",
        what: "Under the message box: the model chip chooses the model for this chat, the brain chip how much it "
          + "thinks first (for models that can). Thinking takes longer and helps with hard problems, maths and "
          + "planning.",
        how: [
          "Click the model's name under the message box and pick another. The first answer after a switch takes a "
            + "moment while the card loads it.",
          "Click No thinking and pick Think a little, Think or Think hard.",
          "The owner sets the order of the models, and the model for new chats, under Settings, Models.",
        ],
      },
      {
        id: "web",
        tools: ["web_search", "web_fetch"],
        title: "Search the web and read pages",
        what: "For anything current - news, prices, opening hours, documentation. It searches, reads the pages and "
          + "says where the answer comes from.",
        examples: [
          { text: "Search the web: what are the opening hours of Aalborg Zoo this weekend?" },
          { text: "What is new in the latest version of Python? Use the official release notes" },
          { text: "Read https://www.dr.dk and summarise the three biggest stories" },
          { text: "Find three reviews of the Sony WH-1000XM6 and sum up what they agree on" },
        ],
        tips: ["When the internet is set to Ask first (Settings, Assistant, Web), it asks before every search and "
          + "every new site, and waits for your answer in the chat."],
      },
      {
        id: "attach",
        tools: ["read_file", "view_image"],
        title: "Attach files and pictures",
        what: "Use the + under the message box (Attach files), paste, or drop files on it. Pictures are shown to "
          + "chat models that can see; PDFs and Word files are read as text; other files are read with code.",
        examples: [
          { text: "What is in this picture?", attach: "a photo" },
          { text: "Read the error in this screenshot and tell me how to fix it", attach: "a screenshot" },
          { text: "Summarise this PDF in ten bullet points", attach: "a PDF" },
          { text: "Which of these two offers is cheaper over five years?", attach: "two offers" },
        ],
      },
      {
        id: "while",
        title: "Add, stop, ask again, edit",
        what: "You stay in charge while it works.",
        how: [
          "Write while it works: it reads your message after the step it is on.",
          "The square button stops it.",
          "Under the last answer: copy it, or the circular arrow for a new answer.",
          "The pencil under a message you sent changes it and sends it again; the chat goes on from there.",
          "Search your chats from the sidebar.",
        ],
      },
      {
        id: "plans",
        tools: ["todo_write", "task"],
        title: "Bigger jobs: checklists and helpers",
        what: "For a job with several steps it keeps a checklist you can follow. For research that reads many pages "
          + "or files, it can send helpers that work on one part each and report back.",
        examples: [
          { text: "Plan our move to a new house: make a checklist and work through it with me" },
          { text: "Compare the five most sold heat pumps in Denmark - price, noise and efficiency. Use a helper for each" },
          { text: "Go through all the files in this project and list what each one does" },
        ],
      },
      {
        id: "handover",
        tools: ["escalate"],
        title: "A stronger model when it gets stuck",
        what: "If the model repeats itself, fails three times in a row, or asks for help, the turn goes on with the "
          + "model chosen in Settings, Assistant - the chat says so.",
        examples: [{ text: "This is hard - hand it over to the stronger model if you get stuck" }],
        needs: ["handover"],
      },
      {
        id: "export",
        title: "Export a chat",
        what: "The ⋯ menu at the top of a chat: Export as Markdown, or Export for troubleshooting - a ZIP with "
          + "everything about the chat. Send that ZIP when something goes wrong.",
        owner: true,
      },
      {
        id: "device",
        title: "Your chats stay on this device",
        what: "The Hub answers and keeps nothing. To move your chats to another device, or keep a copy: your name at "
          + "the bottom left, Export all chats - and Import chats on the other device.",
        member: true,
      },
    ],
  },
  {
    id: "documents",
    title: "Documents and files",
    intro: "Each chat has its own folder, and a project has one for all its chats. The assistant reads, writes and "
      + "changes files there. Every file it makes or changes is listed under its answer, to open or download, and "
      + "Files at the top of the chat shows the whole folder.",
    items: [
      {
        id: "read",
        tools: ["read_file"],
        title: "Read and sum up documents",
        what: "PDF, Word, text, Markdown, CSV, Excel and code files - attached, or already in the folder.",
        examples: [
          { text: "Sum up this report: the decisions, who does what, and the deadlines", attach: "a report" },
          { text: "Compare these two contracts and show the differences in a table", attach: "two contracts" },
          { text: "What does the manual say about resetting the device to factory settings?", attach: "a manual" },
          { text: "Check this text for spelling and grammar and list what you changed", attach: "a document" },
        ],
      },
      {
        id: "write",
        tools: ["write_file", "bash"],
        title: "Write documents",
        what: "Notes, letters, reports and Markdown directly; Word (.docx), Excel (.xlsx) and PDF files are made "
          + "with code in the sandbox.",
        examples: [
          { text: "Write a one-page project brief as a Word file: goal, scope, timeline and risks" },
          { text: "Make an Excel sheet for a monthly budget with formulas for the totals" },
          { text: "Turn these notes into a tidy document with headings, and save it as a PDF: [paste the notes]" },
          { text: "Write a polite complaint to the shop about a broken washing machine" },
        ],
        tips: ["Word, Excel and PDF files need code; plain text and Markdown files do not."],
      },
      {
        id: "change",
        tools: ["edit_file", "move_file", "delete_file", "search_files", "list_files"],
        title: "Change, sort and find files",
        what: "It edits files in place and moves, renames and deletes them. In a project the history keeps every "
          + "change.",
        examples: [
          { text: "Fix the spelling in notes.md" },
          { text: "Rename report.docx to 2026-10 status.docx and move it to the reports folder" },
          { text: "Find every file that mentions invoice 2026" },
          { text: "Put the PDFs in a folder of their own, sorted by year" },
        ],
      },
      {
        id: "artifacts",
        tools: ["artifact"],
        title: "Pages, diagrams and documents beside the chat",
        what: "An artifact opens in the panel on the right: a web page, a React component, a drawing (SVG), a "
          + "diagram (Mermaid), a Markdown document or code. Ask for changes and it changes the same one; the ⋯ menu "
          + "opens it again.",
        examples: [
          { text: "Draw a flowchart of how we order and pay for groceries" },
          { text: "Make an interactive calculator for what a heat pump saves per year" },
          { text: "Make a one-page overview of these numbers as a web page with charts", attach: "a CSV or Excel file" },
          { text: "Make a timeline of the Danish monarchs since 1900" },
        ],
      },
      {
        id: "data",
        tools: ["bash"],
        title: "Tables and data",
        what: "CSV and Excel files are worked through with Python in the sandbox: totals, cleaning, charts.",
        examples: [
          { text: "Show the totals per month in sales.xlsx and draw a bar chart" },
          { text: "Clean this CSV: remove duplicates, fix the dates, and save it as clean.csv", attach: "a CSV" },
          { text: "Which ten customers bought the most last year?", attach: "a spreadsheet" },
        ],
        needs: ["code"],
      },
      {
        id: "libraries",
        tools: ["library_search"],
        title: "Search your libraries",
        what: "Manuals, papers and notes in a library (Libraries in the sidebar) are searched by their text and by "
          + "what their pages show. Turn a library on for a chat with the + under the message box, or for a project.",
        examples: [
          { text: "Search the manuals: how do I clear error E42 on the dishwasher?" },
          { text: "What do our notes say about the heating system's service interval?" },
        ],
        needs: ["libraries"],
        owner: true,
      },
      {
        id: "projects",
        title: "Projects",
        what: "A project keeps files, instructions and chats together (the + beside Projects in the sidebar). Its "
          + "instructions are followed in every chat in it, and SLQ Sync keeps a folder on your PC in step with it.",
        examples: [
          { text: "Read the files in this project and write a README that explains them" },
          { text: "Update the price list with the new prices from the attached offer", attach: "an offer" },
        ],
        how: ["The project's page has its chats, its files, its settings (the instructions), its history and Sync "
          + "to a PC."],
        owner: true,
      },
    ],
  },
  {
    id: "images",
    title: "Images",
    intro: "Pictures are made and changed on the graphics card; while it works the chat model steps aside and comes "
      + "back afterwards. A picture takes 15-90 seconds, more when its model has to load first.",
    items: [
      {
        id: "make",
        tools: ["image_generate"],
        title: "Make a picture",
        what: "Describe what you want: the subject, the style, the light, any text that must be in it. With more than "
          + "one picture model downloaded, the picture chip under the message box chooses the one for the chat "
          + "(Settings, Assistant, Images sets the default).",
        examples: [
          { text: "Make a photorealistic picture of a red fox in fresh snow at dawn" },
          { text: "A poster for the club's Christmas party with the text \"Jul i Hallen 2026\"" },
          { text: "A cartoon of a moose playing ice hockey, wide format" },
          { text: "Give me four logo ideas for a coffee bar called Bønne" },
          { text: "A quick draft: a cosy reading corner with a green armchair - fast" },
        ],
        tips: [
          "Say the shape or size: square, portrait, wide, or exact pixels such as 1920x1080.",
          "\"fast\" uses a turbo model for a quick draft; ask for up to four pictures to choose from.",
          "Name a model to use another one for a single picture.",
        ],
        needs: ["images.generate"],
      },
      {
        id: "edit",
        tools: ["image_edit"],
        title: "Change a picture",
        what: "Change, add or remove things, change the style, or combine up to ten pictures.",
        examples: [
          { text: "Change the background to a beach at sunset", attach: "a picture" },
          { text: "Remove the person on the left", attach: "a picture" },
          { text: "Make it look like a watercolour painting", attach: "a picture" },
          { text: "Put the dog from the first picture into the living room in the second", attach: "two pictures" },
        ],
        tips: ["The result keeps the first picture's proportions."],
        needs: ["images.edit"],
      },
      {
        id: "cutout",
        tools: ["image_remove_background"],
        title: "Cut out the subject",
        what: "A PNG of the subject with a transparent background - for a logo, a product photo or a sticker.",
        examples: [{ text: "Remove the background from this photo", attach: "a photo" }],
        needs: ["images.cutout"],
      },
      {
        id: "transform",
        tools: ["image_transform"],
        title: "Crop, resize, rotate, convert",
        what: "Plain picture work without AI: crop, resize, rotate, flip, black and white, brightness, contrast, "
          + "sharpness, blur, a square with padding - and PNG, JPEG or WebP at the quality you want.",
        examples: [
          { text: "Crop it to a square and make it 1080 pixels wide", attach: "a picture" },
          { text: "Make it black and white and a little brighter", attach: "a picture" },
          { text: "Convert it to JPEG at 80 % quality so it gets smaller", attach: "a picture" },
        ],
      },
      {
        id: "look",
        tools: ["view_image"],
        title: "Look at pictures",
        what: "Chat models that can see look at pictures themselves: what is in them, what is wrong, what something "
          + "is.",
        examples: [
          { text: "Write a short caption for this photo for Facebook", attach: "a photo" },
          { text: "What plant is this, and how do I look after it?", attach: "a photo" },
          { text: "What do these warning lights on the dashboard mean?", attach: "a photo" },
        ],
      },
      {
        id: "ocr",
        tools: ["image_ocr"],
        title: "Read the text in a picture",
        what: "OCR for scans and long printed text, in Danish, English or German.",
        examples: [
          { text: "Read the text in this scanned letter and save it as a text file", attach: "a scan" },
          { text: "Type out the recipe in this photo", attach: "a photo" },
        ],
      },
    ],
  },
  {
    id: "sound",
    title: "Sound and music",
    intro: "Speech is made on the processor, so the chat model stays loaded. Music, sound effects and voice "
      + "changes run on the graphics card: seconds for an effect, a few minutes for a song. Every sound is saved as "
      + "a file that plays under the answer.",
    items: [
      {
        id: "speech",
        tools: ["speech_generate"],
        title: "Read text aloud",
        what: "Danish with Røst v3 (the voices Mic and Nic), English and many other languages with Chatterbox "
          + "Multilingual (23 languages) - or in any voice from a clip of 10-20 seconds of someone speaking.",
        examples: [
          { text: "Read this aloud in Danish with the voice Nic: \"Velkommen til SLQ Hub\"" },
          { text: "Make an MP3 of this announcement in English: [paste the text]" },
          { text: "Read the text in my voice", attach: "a 10-20 s recording of you speaking" },
        ],
        tips: ["It takes several seconds of work per second of speech."],
        needs: ["sound.speech"],
      },
      {
        id: "music",
        tools: ["music_generate"],
        title: "Make music",
        what: "A whole song with vocals and lyrics, or instrumental: the style, the mood, the instruments, the "
          + "tempo, the key and the length (up to 5 minutes).",
        examples: [
          { text: "A 30-second upbeat jingle for SLQ Hub, electronic, no vocals" },
          { text: "Write and make a Danish rock song about winter hockey, 2 minutes, with a chorus everyone can sing" },
          { text: "Calm piano music for a slideshow, 3 minutes" },
          { text: "A song in the sound of this clip", attach: "a clip of a song" },
        ],
        tips: ["One request makes the whole piece: put every instrument and any solo in it. Pieces made separately "
          + "do not fit together."],
        needs: ["sound.music"],
      },
      {
        id: "effects",
        tools: ["sound_generate"],
        title: "Sound effects",
        what: "Short sounds from a description: horns, doors, weather, crowds, machines.",
        examples: [
          { text: "An ice hockey arena horn at the end of a period, 3 seconds" },
          { text: "Rain on a tin roof, 20 seconds" },
          { text: "A door creaking slowly open in an old house" },
        ],
        needs: ["sound.sound"],
      },
      {
        id: "remake",
        tools: ["music_remake"],
        title: "Re-make a song",
        what: "The same melody and lyrics in another style - or with the sound of a clip from another song, such as "
          + "a live recording's audience and hall.",
        examples: [
          { text: "Re-make this song as a live concert with a cheering audience and a big hall", attach: "a song" },
          { text: "Make the first song sound like the clip from the second", attach: "a song and a clip" },
          { text: "Make a jazz version and keep the lyrics: [paste the lyrics]", attach: "a song" },
        ],
        tips: ["Give the lyrics to keep the words. Ask to keep more of the original to stay closer to the recording."],
        needs: ["sound.music"],
      },
      {
        id: "stems",
        tools: ["audio_separate"],
        title: "Vocals and music apart",
        what: "Splits a song into the vocals and the accompaniment - for karaoke, a remix or a cappella.",
        examples: [
          { text: "Make a karaoke version of this song", attach: "a song" },
          { text: "Give me only the vocals from this song", attach: "a song" },
        ],
        needs: ["sound.separate"],
      },
      {
        id: "voice",
        tools: ["voice_convert"],
        title: "Another voice",
        what: "Makes a recording sound as if someone else sang or said it: the words, timing and melody stay. In a "
          + "song the vocals are taken out, changed and put back.",
        examples: [
          { text: "Make this song sound as if I sang it", attach: "a song and a 10-30 s clip of your voice" },
          { text: "Change the voice in this recording to the voice in the clip", attach: "a recording and a voice clip" },
        ],
        tips: ["A clip cut from another song works as the voice. Only use a voice you have the right to use."],
        needs: ["sound.voice"],
      },
      {
        id: "mix",
        tools: ["bash"],
        title: "Mix and edit sound",
        what: "With ffmpeg in the sandbox: mixing, cutting, fading, converting.",
        examples: [
          { text: "Put the speech over the music, with the music at 30 % volume" },
          { text: "Cut the first 10 seconds and fade out over the last 5" },
          { text: "Convert all the WAV files in the folder to MP3" },
        ],
        needs: ["code"],
      },
    ],
  },
  {
    id: "video",
    title: "Video",
    intro: "Short video clips (5 seconds by default, up to 10) are made on the graphics card and saved as MP4 files "
      + "that play under the answer. A clip takes minutes, so it makes one at a time. The clips have no sound.",
    items: [
      {
        id: "describe",
        tools: ["video_generate"],
        title: "Video from a description",
        what: "Describe the scene, what moves and how, the camera, the light and the style.",
        examples: [
          { text: "Make a video of a red fox running through fresh snow at dawn, low tracking shot" },
          { text: "A drone flying slowly over the Danish west coast at sunset" },
          { text: "Rain running down a window at night, city lights behind - portrait, for a phone" },
        ],
        tips: ["Describe the motion and the camera (static, slow push-in, pan, orbit, tracking shot).",
          "One shot per clip; for a story, make several and ask it to join them."],
        needs: ["video.t2v"],
      },
      {
        id: "picture",
        tools: ["video_generate"],
        title: "Make a picture move",
        what: "A picture becomes the first frame - one you attach, or one it has just made.",
        examples: [
          { text: "Make this picture move: the camera slowly pushes in and the leaves sway", attach: "a picture" },
          { text: "Make a picture of a lighthouse in a storm, then turn it into a video with crashing waves" },
        ],
        tips: ["Describe the motion, not what is in the picture - the picture already shows that."],
        needs: ["video.i2v"],
      },
      {
        id: "firstlast",
        tools: ["video_generate"],
        title: "From one picture to another",
        what: "A video that starts on one picture and ends on another.",
        examples: [{ text: "Make a video that goes from the first picture to the second, day turning into night",
          attach: "two pictures" }],
        needs: ["video.flf2v"],
      },
      {
        id: "quality",
        tools: ["video_generate"],
        title: "Length, shape and quality",
        what: "Ask for the length (1-10 seconds), landscape, portrait or square, 720p, and - with the Wan 2.2 14B "
          + "models - full quality (several times slower than the default).",
        examples: [
          { text: "The same video again, 8 seconds long" },
          { text: "The same, but in full quality" },
          { text: "Make it in 720p" },
        ],
        needs: ["video.t2v"],
      },
      {
        id: "sound",
        tools: ["bash", "music_generate", "sound_generate"],
        title: "Add sound to a video",
        what: "With a music or sound effects model downloaded, it makes the sound and puts it under the video with "
          + "ffmpeg.",
        examples: [
          { text: "Add calm piano music under the video" },
          { text: "Make the sound of waves and put it under the lighthouse video" },
        ],
        needs: ["code"],
      },
    ],
  },
  {
    id: "code",
    title: "Code and programs",
    intro: "The assistant runs code in a sandbox: Python (with numpy, pandas, matplotlib and more), Node.js, bash, "
      + "git, ffmpeg, ImageMagick and the .NET 10 SDK. The sandbox can reach the internet but not your home "
      + "network, and it works in the chat's or project's folder.",
    items: [
      {
        id: "run",
        tools: ["bash"],
        title: "Write and run code",
        what: "Scripts it writes, runs and fixes until they work - also for sums that need to be exact.",
        examples: [
          { text: "Write a Python script that renames my photos by the date they were taken, and run it on the folder" },
          { text: "What is the monthly payment on a loan of 2.5 million at 4 % over 30 years? Work it out with code" },
          { text: "List the files in this folder over 10 MB, biggest first" },
        ],
        tips: ["It installs what it needs (pip, npm). When the internet asks first, code that goes online asks too.",
          "A command has a time limit - Settings, Assistant, Sandbox."],
        needs: ["code"],
      },
      {
        id: "charts",
        tools: ["bash"],
        title: "Charts and analysis",
        what: "Numbers in, charts and answers out.",
        examples: [
          { text: "Plot the temperatures in this CSV per day, with a 7-day average", attach: "a CSV" },
          { text: "Find the errors in this log file and count them per hour", attach: "a log file" },
        ],
        needs: ["code"],
      },
      {
        id: "apps",
        tools: ["artifact", "write_file"],
        title: "Small apps and web pages",
        what: "Single-page apps open beside the chat as artifacts; bigger ones become files in the folder.",
        examples: [
          { text: "Build a small web app for the kids' chores with points per week" },
          { text: "Make a countdown to Christmas as a React component" },
          { text: "Make a simple game where you catch falling snowflakes" },
        ],
      },
      {
        id: "projects",
        tools: ["bash", "edit_file"],
        title: "Work on a code project",
        what: "In a project it reads the code, changes it, runs the tests and explains what it did; the project's "
          + "history keeps every change.",
        examples: [
          { text: "Add tests for parser.py and run them" },
          { text: "The build fails - find out why and fix it" },
          { text: "Explain how this project is put together, file by file" },
        ],
        needs: ["code"],
        owner: true,
      },
      {
        id: "windows",
        tools: ["windows_app"],
        title: "C# and Windows programs",
        what: "It builds C# with .NET 10 - also Windows Forms and WPF - and runs Windows programs on a virtual "
          + "screen: it sees screenshots, clicks and types, and tells you what happened.",
        examples: [
          { text: "Make a Windows Forms app that converts Celsius to Fahrenheit, run it and show me a screenshot" },
          { text: "Start the program in this project, click Start and tell me what happens" },
        ],
        needs: ["windows"],
        owner: true,
      },
      {
        id: "mcp",
        title: "Tools from MCP servers",
        what: "Servers added under Settings, MCP servers give it more tools, which it uses like its own.",
        examples: [{ text: "Which tools do you have from the MCP servers, and what can they do?" }],
        needs: ["mcp"],
        owner: true,
      },
    ],
  },
  {
    id: "memory",
    title: "Memory and skills",
    memberTitle: "Skills",
    intro: "What the assistant knows about you across chats, and the instructions it follows for particular kinds "
      + "of work.",
    items: [
      {
        id: "remember",
        tools: ["memory"],
        title: "Remember things",
        what: "It keeps short notes about you and your projects and reads them when they help. Settings, Memory shows "
          + "and edits them.",
        examples: [
          { text: "Remember that I prefer short answers in Danish" },
          { text: "What do you remember about the hockey club?" },
          { text: "Forget the note about my old address" },
        ],
        owner: true,
      },
      {
        id: "skills",
        tools: ["skill"],
        title: "Skills",
        what: "A skill is a set of instructions for a kind of task, such as a house style or a way of working. "
          + "Skills switched on under Settings, Skills are used by themselves when a task fits; the + under the "
          + "message box adds others to one chat.",
        examples: [
          { text: "Which skills do you have, and when do you use them?" },
          { text: "Use the [skill name] skill for this" },
        ],
      },
      {
        id: "instructions",
        title: "Instructions for a project",
        what: "A project's instructions (its page, Settings) are followed in every chat in it: who it is for, how "
          + "answers should look, what to keep in mind.",
        owner: true,
      },
    ],
  },
  {
    id: "hub",
    title: "The Hub",
    intro: "Around the chat: the card, the queue, people and apps.",
    items: [
      {
        id: "queue",
        title: "The card and the queue",
        what: "One job uses the graphics card at a time. The plate at the bottom of the sidebar shows what is on it, "
          + "and the queue who waits; a picture, a sound or a video takes the card for a while, then the chat model "
          + "comes back.",
        how: ["Click the plate to see the queue and stop your own jobs."],
      },
      {
        id: "people",
        title: "People in the household",
        what: "Settings, People adds family members: the same chat with their own login, at home or also away. "
          + "Their chats are kept on their own device, and code is off unless you allow it.",
        owner: true,
      },
      {
        id: "apps",
        title: "Phone page and other apps",
        what: "The chat page for phones is at /chat. Other apps (Open WebUI, scripts, editors) use the Hub's models "
          + "with a key from Settings, Apps and keys - in the Ollama, OpenAI and Anthropic formats.",
        owner: true,
      },
      {
        id: "trouble",
        title: "When something goes wrong",
        what: "Export the chat for troubleshooting (the ⋯ menu) and send the ZIP. Settings, System shows the "
          + "services, the card's memory and the logs.",
        owner: true,
      },
    ],
  },
];

/** The items a reader sees: a person's chats have no projects, memory, libraries or Windows programs. */
export function visible(item: HelpItem, member: boolean): boolean {
  return member ? !item.owner : !item.member;
}

/** Words to search for: the title, the text and the examples. */
export function searchText(item: HelpItem): string {
  return [item.title, item.what, ...(item.examples ?? []).map((e) => e.text), ...(item.how ?? []),
    ...(item.tips ?? [])].join(" ").toLowerCase();
}
