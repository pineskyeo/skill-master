---
name: todo-collector
description: Collects the TODO and FIXME comments of a source tree into one TODO.md file. Use when a person asks for a list of open TODO comments in a project.
goal: Write TODO.md that lists every TODO and FIXME comment of the source tree with its file and line.
capabilities: [read, search, write]
model_policy: judgment
success_criteria:
  - '{"type": "file_exists", "path": "TODO.md"}'
  - '{"type": "llm", "text": "Does TODO.md list each TODO and FIXME comment with its path and line number?"}'
targets: [claude, codex, opencode, hermes]
version: 1.0.0
---
# TODO collector

Builds a single list of open TODO and FIXME comments for a developer who plans cleanup work.

## Steps
1. Search the source tree for TODO and FIXME comments.
2. Read the lines around each hit to copy the comment text.
3. Write TODO.md with one bullet per comment, as path:line and the text.

## Tools
- search finds the TODO and FIXME comments.
- read shows the comment lines with line numbers.
- write creates TODO.md in the project root. It is the only file the agent writes.

## Rules
- Change no source file.
- Skip comments inside vendored folders such as node_modules and vendor.

## Memory & Handoff
Keeps no state between runs. Hands over the path of TODO.md and the number of comments in the final answer.
