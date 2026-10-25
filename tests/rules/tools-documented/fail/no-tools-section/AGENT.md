---
name: no-tools-section
description: Reads a repository and reports its structure. Use when a rule fixture needs an agent unit.
goal: Report the structure of a repository with file and line evidence.
tools: [read_file, grep]
success_criteria:
  - at least 2 file:line citations
---
# Fixture Agent

## Usage
- read_file reads one file.
- grep finds a line.

## Memory and Handoff
Keeps the list of files it read and hands it to the next turn.
