# Claude stream fixture: overview.jsonl

One `claude -p --output-format stream-json --verbose` stream of the `overview` scenario of
`tests/fixtures/agents/code-reviewer-v2`, used by `tests/test_agent_eval.py`.

## Recorded source of the event shapes

- `/Users/pinesky/workspace/pstack-trial/runs/p1/clj21.jsonl:1-4`: `system/hook_started` and `system/hook_response`.
- `/Users/pinesky/workspace/pstack-trial/runs/p1/clj21.jsonl:5`: `system/init` with `cwd`, `session_id`, `tools`,
  `model`, `apiKeySource`, `agents`, `skills` and `plugins`.
- `/Users/pinesky/workspace/pstack-trial/runs/p1/clj21.jsonl:6-9`: `system/thinking_tokens` and an `assistant` event
  with a `thinking` block.
- `/Users/pinesky/workspace/pstack-trial/runs/p1/clj21.jsonl:14-15`: an `assistant` `tool_use` block of `Read` and the
  `user` `tool_result` block with the line-numbered `N<tab>line` content.
- `/Users/pinesky/workspace/pstack-trial/runs/p1/clj21.jsonl:17`: `rate_limit_event`.
- `/Users/pinesky/workspace/pstack-trial/runs/p1/c2.jsonl:1`: the `result` event fields `subtype`, `is_error`,
  `num_turns`, `session_id`, `total_cost_usd` and `usage`. The `success` subtype and its `result` string follow the
  same event of the program runner's later sessions (61 `result/success` events in `runs/p1/c*.jsonl`, counted by
  `/tmp/b3-owner/scripts/types.py`, capture `/tmp/b3-owner/stream-types.txt`).

## What was replaced

- Every text, path, id and number. The session id, message ids, tool use ids, uuids and hook ids are placeholders.
  The cwd is `/sandbox/work`. The file contents, the final answer, the token counts and the cost are invented for
  the scenario.
- The `init` lists were cut to what the scenario needs: `tools` holds three names, `slash_commands`, `skills`,
  `plugins` and `mcp_servers` are empty, and `agents` holds three built-in names plus `code-reviewer-v2`.
- Fields the normalizer does not read were dropped (`wire_tool_inputs`, `tool_use_result`, `tool_result_meta`,
  `memory_paths`, `capabilities`, `request_id`, `timestamp` and the like). No secret, token, account id or user
  path is in the file.

## UNVERIFIED

- That `claude -p --agent code-reviewer-v2` lists the project agent in `system/init` `agents` (the recorded init of
  `clj21.jsonl:5` lists the agents of an interactive session, not of a `--agent` start). Operator lane 7 settles it.
- That a `--agent` session reports its tool calls with the same `assistant` and `user` blocks as the recorded
  session.
- The `result/success` event of a `--agent` start.
