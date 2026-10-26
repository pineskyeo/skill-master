# Codex stream fixture: overview.jsonl

One `codex exec --json` event stream of the `overview` scenario of `tests/fixtures/agents/code-reviewer-v2`, used by
`tests/test_agent_eval.py`.

## Recorded source of the event shapes

- `/Users/pinesky/workspace/pstack-trial/runs/p6/rev21.events.jsonl:1-2`: `thread.started` with `thread_id`, and
  `turn.started`.
- `/Users/pinesky/workspace/pstack-trial/runs/p6/rev21.events.jsonl:3-4`: `item.started` and `item.completed` of a
  `command_execution` item with `id`, `command` (wrapped as `/bin/zsh -lc '...'`), `aggregated_output`, `exit_code`
  and `status`.
- `/Users/pinesky/workspace/pstack-trial/runs/p6/rev21.events.jsonl:5`: `item.completed` of an `agent_message` item
  with `id` and `text`.
- `/Users/pinesky/workspace/pstack-trial/runs/p6/rev21.events.jsonl:97`: `turn.completed` with `usage`
  (`input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`).

## What was replaced

- Every text, path, id and number. The thread id is a placeholder, the commands read the scenario's own invented
  `README.md`, and the token counts are invented. No secret, token, account id or user path is in the file.

## UNVERIFIED

- How a top-level `codex exec` session starts as a custom agent. Codex documents custom agents as spawned subagents
  invoked by asking (`inputs/design.md:65`, `:67`); the stream of such a start, and whether a spawned agent's own
  tool calls appear in it, is not recorded. The normalizer refuses the recorded `collab_tool_call` item for that
  reason.
- The `file_change` item shape (no recorded instance). The normalizer reads it as an `apply_patch` call.
- Whether `cached_input_tokens` is part of `input_tokens` (the normalizer reports `input_tokens` as given).
