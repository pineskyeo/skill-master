---
description: "Reads a repository read-only and explains it. Use when the user asks for an overview."
mode: subagent
permission:
  read: allow
  edit: deny
  glob: allow
  grep: allow
  list: allow
  bash: ask
  task: deny
  external_directory: deny
  webfetch: deny
  websearch: deny
  lsp: allow
  skill: deny
---
You are opencode-ask. Load the skill and use the engine.
