---
name: skill-creator
description: Makes a new skill from the user's material and instructions, or improves one of their skills - in the format Claude uses (a folder with SKILL.md and its files). Use when the user wants to make, change or improve a skill.
---

# Making a skill

A skill is a folder with a `SKILL.md` and the files it points to. When someone picks the skill in a chat, the
assistant gets `SKILL.md` as instructions and reads the other files when it needs them. You are making that
folder for the user from their material (PDFs, Word, Excel, PowerPoint, notes, pictures) and what they say the
skill should do. The format is the one Claude uses, so the skill also works in Claude.

## 1. Know what the skill is for

From the user's message, be clear about three things: what the skill should make the assistant do, for which
requests, and what a good result looks like (the answer, a file, a format). If one of them is missing and you
cannot tell from the material, ask one short question before you start. Otherwise start.

An example the user gives (a link, a picture) shows what the skill should handle - it is not the skill. Unless the
user wants a skill for that one thing, make it work for any such request, and put the example in `## Example`, not
into the defaults and rules. When the result needs facts the source may not give (a picture has no scale), the
skill says what to ask for or what to assume.

## 2. Read all the material

Read every file the user gave, completely: `read_file` reads PDF (pages with offset/limit - go through long ones in
parts), Word, Excel (every sheet), PowerPoint (every slide), text and code; `view_image` shows a picture. Attached
files are in `.hub/attachments/`. Note what the skill needs: facts, rules, numbers, steps, names, examples, and
where each came from (file and page or sheet).

## 3. Name it and describe it

- `name`: 1-64 characters, lowercase letters, digits and single hyphens, in English, saying what it does
  (`invoice-checker`, `training-course-plan`). Not the words "claude" or "anthropic".
- `description`: one to three sentences, at most 1024 characters: what the skill does and when to use it, with the
  words a person would use when they need it. It is what people read when they pick the skill.

## 4. Write the folder

Write everything under `skills/<name>/` in the working folder:

```
skills/<name>/
  SKILL.md          instructions (required)
  references/       the material's knowledge, as Markdown the assistant can read
  assets/           files the skill uses as they are: templates, a spreadsheet to fill in
  scripts/          code the skill runs - only if this Hub runs code for the user
```

`SKILL.md` starts with the front matter and then the instructions:

```
---
name: <name>
description: <description>
---

# <A title>

<What the skill is for, in a sentence or two.>

## How to do it
1. ...

## Rules
- ...

## Output
<What the result looks like.>

## Example
<A short request and what a good answer does.>
```

Write the instructions for an assistant that knows nothing of this conversation: plain steps and rules, the
reasons where they matter, the format of the result. Keep `SKILL.md` under about 300 lines. Put longer knowledge in
`references/` - one file per topic (`references/prices.md`, `references/safety-rules.md`), tables as Markdown
tables, with where each part came from - and say in `SKILL.md` which file to read for what: "For the error codes,
read references/error-codes.md." Keep the facts exact: numbers, names and units as in the material.

Write in the language the user writes in, unless they ask for another. The name stays in English.

To keep a file the user gave as it is (an Excel template, a logo in `.hub/attachments/`), do not rewrite it: name
it in `include` when you call `skill_propose` (step 6), with its path from the working folder's top; it goes into
`assets/`. Files you wrote in the skill's folder are in the skill already - no `include` for them.

**Fixed questions.** When the skill always needs the same few choices from the user (how many, which style, which
colour), put them in a `questions` block in `SKILL.md`. When the skill is chosen, the chat shows them as a card
with buttons, and the answers come with the user's message - the user need not write anything else. One question
a line: the question, then its options, all split by `|`; `*` before an option picks it from the start; `+` before
the question lets the user choose several:

````
```questions
How many elves should be in the picture? | 0 | 1 | *2 | 3
Should there be snow? | Yes | No
+ Which colours? | *Red | Green | Gold
```
````

Only real options (2-12 each, at most 10 questions); what is open stays a question for the conversation. Say in
the instructions how each answer is used ("put that many elves in the picture").

## 5. Try it

Follow your own `SKILL.md` once on a realistic request (one the user gave, or one you make up) and show the user
the result in a few lines. If it went wrong because the instructions were unclear or something was missing, fix
the files first.

## Skills that make a file with code

When the skill's result is a file made by code - a 3D model to print, a spreadsheet, a picture, a document - the
code is the part that breaks. Make it work the way the user will use it:

- **A library for the format, never the format by hand.** Excel: openpyxl. Word: python-docx. Pictures and
  charts: Pillow, matplotlib. 3D models (STL, 3MF): manifold3d builds the solid (extrude a 2D outline, revolve a
  profile, add and cut parts - what it makes is always closed) and hub3d saves, checks and draws it; run
  `hub3d recipe` for manifold3d's API with worked examples before writing the script, and put the calls the
  script needs in the skill rather than the recipe. The sandbox has them all. A hand-written STL is a list of
  triangles that slicers reject when one is wrong.
- **The script is the skill's, and the skill runs it.** Put it in `scripts/` with arguments for what changes
  (sizes, counts, the output file - and it writes where it is told) and `--help`; it prints what it wrote. In
  `SKILL.md`, say exactly how to run it: a chosen skill's files are in `.hub/skills/<name>/` of the working folder
  (the Skills part of the instructions names the folder), so `python .hub/skills/<name>/scripts/make.py --out
  result.stl ...` - and that the assistant runs it rather than writing a new one. Without that line the next chat
  writes its own, untested.
- **Check the result with something other than the script.** Open the file again with the library and check what
  matters: a 3D model is a closed solid, its size in mm is the size asked for and it fits the printer
  (`hub3d check model.stl --bed 180` says all three); a spreadsheet has the sheets, columns and numbers asked for.
  When the result is something to look at, draw a picture of it (a 3D model: `hub3d views model.stl views.png`),
  look at it with `view_image` and compare it with the user's picture. A test that only finds the file, or counts
  what is in it, proves nothing - say in `SKILL.md` how to check, so each use checks too.
- **A skill makes things of a kind, not one thing.** Its examples and its script work for any thing of that kind
  (any box with a lid, any vase from a profile), with the sizes as arguments - not the object of the chat it was
  made in, which the next use does not want.
- **Test output stays out of the skill.** Write it to `out/` in the working folder, not into `skills/<name>/`. The
  skill's folder holds only what the skill needs: no test files, no notes of your work, no caches.

## 6. Hand it over

Call `skill_propose` with `path: "skills/<name>"` (and `include` for files from elsewhere kept as they are). It
checks the folder and lists what it packed; if it says something is wrong, or test output is in the list, fix it
and call it again. Only when it said the skill is ready: tell the user, in a sentence or two, what the skill does
and that **Add skill** on the card adds it to their skills - after that they pick it in a chat with the Skills
button or by typing `/` and its name, and can share it with the family on the Skills page. If it is not ready,
there is no card: say what is wrong instead.

## Improving a skill the user has

When the user picks one of their skills in this chat and asks to change it, the whole skill is in
`.hub/skills/<folder>/` (the Skills section of your instructions names the folder). Write the new version to
`skills/<name>/` with the same name: read each file there and write it, changed or not, under `skills/<name>/` -
files kept as they are (an Excel template, a picture) go in `include` instead. Make the changes, try it, and call
`skill_propose` as above. Adding it replaces the old one.
