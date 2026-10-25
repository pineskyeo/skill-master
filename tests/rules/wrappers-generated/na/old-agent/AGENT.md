---
name: old-agent
description: Reads a repository and reports its structure. Use when a test needs a v1 agent.
goal: Report the structure of a repository with file and line evidence.
tools: [read_file, grep]
success_criteria:
  - at least 2 file:line citations
---
# Old Agent
## Tools
- read_file reads one file.
## Memory and Handoff
Keeps the list of files it read.
