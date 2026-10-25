---
description: "Reads a repository read-only and explains it. Use when the user asks for an overview."
mode: subagent
permission:
  read: allow
  edit: deny
  glob: allow
  grep: allow
  list: allow
  bash: deny
  task: deny
  external_directory: deny
  webfetch: deny
  websearch: deny
  lsp: allow
  skill: deny
---
You are named-agent. Load the skill and use the engine.
