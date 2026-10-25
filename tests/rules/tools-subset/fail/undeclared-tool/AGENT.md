---
name: undeclared-tool
description: Reads a repository and reports its structure. Use when a rule fixture needs an agent unit.
goal: Report the structure of a repository with file and line evidence.
capabilities: [read, search]
model_policy: judgment
success_criteria:
  - '{"type": "file_line_citations", "min": 2}'
targets: [claude, codex, opencode, hermes]
---
# Fixture Agent

## Tools
- read_file reads one file.
- grep finds a line.

## Memory and Handoff
Keeps the list of files it read and hands it to the next turn.
