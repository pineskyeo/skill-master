---
name: unit-requirements
description: Drafts requirements.md for a new skill or agent unit from a person's request, with the headings that the unit kind needs. Use when a person asks to build a skill or an agent and its requirements file is not written yet.
metadata:
  version: "1.0.0"
---
# Unit requirements

Draft one `requirements.md` for a new skill or agent from a person's request. A person approves this file before any eval of the unit runs, so each line states something that a check or a grader can decide. You only draft this file. You run no engine command, and you write no SKILL.md, AGENT.md, or eval file.

## Inputs

- The request. It is chat text, a file of notes, or a thread that the person points at.
- The output path. If the request names none, write `requirements.md` in the current folder.
- The unit kind and the unit name, when the person gives them.

## Outputs

- One `requirements.md` at the output path, in English.
- A reply of a few lines that names the file, the unit kind, the unit name, and each open question.

## Steps

1. Read the request and every file it points at.
2. Pick the unit kind. A skill is one job that an agent loads by its description, such as cleaning a CSV file. An agent is a role with its own capabilities that a person starts, such as a pull request reviewer. If the person names the kind, use it.
3. Pick the unit name. Use lowercase letters, digits, and single hyphens, at most 64 characters, and leave out the words anthropic and claude. Keep a name the person gives when it fits this rule. If you change it, say so under Open questions.
4. Write the title line `# <name> requirements`. Then write the headings for the unit kind in the order below. Copy each heading exactly, with no number, colon, or extra word.
5. Fill each heading with short bullets. State what the request says. When the request leaves a decision open, write the decision under Open questions instead of making it for the person.
6. Reply.

## Skill headings

- `## What it does`. The input the skill takes, the output it writes, and what it leaves unchanged.
- `## When to use`. The requests that should load the skill.
- `## When not to use`. At least two nearby requests that the skill must not take.
- `## First live run`. The engines under test, which are `claude` and `codex` unless the person names others, and 3 runs per case, the engine default. Name a model only when the person names one.
- `## Pass criteria`. Checks that decide whether the skill works. Write each one so that a script or a grader can answer yes or no. Write "the output CSV has no duplicate row", not "the output is clean".
- `## Open questions`.

## Agent headings

- `## Goal`. One sentence that says what the agent finishes.
- `## Users`. Who starts the agent, and in what setting.
- `## Capabilities`. Only words from this list: read, search, write, shell, web_fetch, web_search, delegate, skill. Give one reason for each capability. List each registered skill the agent needs as `<name>@<version>`, and add skill to the capabilities when you do.
- `## Success criteria`. Checks that decide whether a run succeeded. Name the criterion type when one fits: file_line_citations, no_writes, max_chars, markdown_table, min_verified_sources, command_ran, command_absent, or file_exists. Write the rest as yes or no questions for a grader. Also write each Non-goal that a check can decide as a criterion, such as command_absent for a command the agent must never run.
- `## Non-goals`. What the agent must not do, including the steps that a person keeps.
- `## Target tools`. Always claude, codex, opencode, and hermes, because the engine requires all four. If the person names fewer, list all four and record the difference under Open questions.
- `## Open questions`.

## Rules

- Keep every heading, also when the person asks for a shorter file. Put what is unknown under Open questions. When nothing is open, write `- None.` there.
- Keep each number that the request gives. When the request says "fast" or "small" without a number, ask for the number under Open questions.
- Write in English. Translate a request written in another language, and keep file names, paths, and commands as given.
- Approval is a person's step outside this skill. Never write that the file is approved. Say in the reply that the file waits for that step.

## Example

The request: "I rename scanned invoice PDFs by hand to vendor_date.pdf. Make me a skill for that, and write the requirements first."

The file:

```markdown
# invoice-renamer requirements

## What it does
- Takes one folder of scanned invoice PDFs and renames each file to <vendor>_<YYYY-MM-DD>.pdf.
- Reads the vendor and the invoice date from the PDF text. Leaves the file content unchanged.

## When to use
- A person asks to rename invoice PDFs by vendor and date.

## When not to use
- Renaming photos or other files that are not invoices.
- Copying invoice totals into a spreadsheet.

## First live run
- Engines claude and codex, 3 runs per case.

## Pass criteria
- Every renamed file matches <vendor>_<YYYY-MM-DD>.pdf.
- Every PDF keeps its bytes. Only the file name changes.
- A PDF without a readable date keeps its name, and the reply lists it.

## Open questions
- Which date to use when an invoice shows both an issue date and a due date.
```

The reply: "Wrote requirements.md for the skill invoice-renamer. One question is open: which date to use when an invoice shows two dates. The file waits for a person's approval."
