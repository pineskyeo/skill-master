---
name: doc-linker
description: Finds broken relative links in a Markdown docs folder and reports each one with its file and line. Use when a person asks to check the links of a docs folder.
goal: Report every broken relative link of the docs folder with its file and line, without changing any file.
capabilities: [read, search]
model_policy: judgment
success_criteria:
  - '{"type": "file_line_citations", "min": 1}'
  - '{"type": "no_writes"}'
targets: [claude, codex, opencode, hermes]
version: 1.0.0
---
# Doc linker

Checks the relative links of a Markdown docs folder for a technical writer before a release.

## Steps
1. Search the docs folder for Markdown links.
2. Read each file with line numbers and check that each relative link target exists.
3. Report each broken link as path:line and the missing target.

## Tools
- search lists the Markdown files and their links.
- read shows each file with line numbers, which the report needs.

## Rules
- Change no file. Suggest the right target instead of editing.
- Ignore links that start with http or https.

## Memory & Handoff
Keeps no state between runs. Hands over the list of broken links as the final answer.
