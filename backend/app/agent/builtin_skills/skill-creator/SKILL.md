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

To keep a file as it is (an Excel template, a logo), do not rewrite it: name it in `include` when you call
`skill_propose` (step 6); it goes into `assets/`.

## 5. Try it

Follow your own `SKILL.md` once on a realistic request (one the user gave, or one you make up) and show the user
the result in a few lines. If it went wrong because the instructions were unclear or something was missing, fix
the files first.

## 6. Hand it over

Call `skill_propose` with `path: "skills/<name>"` (and `include` for files kept as they are). It checks the folder;
if it says something is wrong, fix it and call it again. Then tell the user, in a sentence or two, what the skill
does and that **Add skill** on the card adds it to their skills - after that they pick it in a chat with the Skills
button or by typing `/` and its name, and can share it with the family on the Skills page.

## Improving a skill the user has

When the user picks one of their skills in this chat and asks to change it, the whole skill is in
`.hub/skills/<folder>/` (the Skills section of your instructions names the folder). Write the new version to
`skills/<name>/` with the same name: read each file there and write it, changed or not, under `skills/<name>/` -
files kept as they are (an Excel template, a picture) go in `include` instead. Make the changes, try it, and call
`skill_propose` as above. Adding it replaces the old one.
