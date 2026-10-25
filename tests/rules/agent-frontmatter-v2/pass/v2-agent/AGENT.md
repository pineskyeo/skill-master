---
name: v2-agent
description: Reads a repository and reports its structure. Use when a rule fixture needs an agent unit.
goal: Report the structure of a repository with file and line evidence.
capabilities: [read, search]
model_policy: judgment
success_criteria:
  - '{"type": "file_line_citations", "min": 2}'
  - '{"type": "llm", "text": "Every finding states a concrete risk."}'
targets: [claude, codex, opencode, hermes]
---
# Fixture Agent

## Tools
- read reads one file.
- search finds a line.

## Memory and Handoff
Keeps the list of files it read and hands it to the next turn.
