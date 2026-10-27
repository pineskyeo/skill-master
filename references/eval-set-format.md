# Eval set format

This page describes the files a skill or an agent carries for evaluation: a skill's `evals/evals.json` and `evals/trigger_evals.json`, an agent's `scenarios/*.json`, and the agent run transcript. The schemas in `schemas/` are the source of truth. Where a schema's `$defs` entry has a `description`, that description is the message template that a failing check prints. `schemas/agent-frontmatter.schema.json` has none, so its messages come from the validator's own error format.

## evals/evals.json

`schemas/evals.schema.json` defines the document. The `evals-valid` rule (`rules/skill-evals.rules.json`) validates it and compares `skill_name` with the frontmatter `name` after `strip` and NFKC normalization of the frontmatter value. The `evals-count` rule (`rules/skill-structure.rules.json`) needs at least 3 cases.

```json
{
	"skill_name": "csv-cleaner",
	"evals": [
		{
			"id": 1,
			"prompt": "Clean data.csv and write cleaned.csv.",
			"expected_output": "cleaned.csv without duplicate rows.",
			"files": ["evals/files/data.csv"],
			"assertions": [
				"The answer names the number of removed rows.",
				{"text": "cleaned.csv exists", "layer": "static", "check": {"type": "file_exists", "path": "cleaned.csv"}}
			]
		}
	]
}
```

| Key | Rule |
|---|---|
| `skill_name` | Required. Equals the SKILL.md frontmatter `name`. |
| `evals` | Required. A non-empty list of case objects with unique `id` values. |
| `claude_python_script` | Optional. An existing relative `scripts/*.py` path inside the skill (checked by `skill_eval.load_eval_set`). |
| `evals[].id` | Required integer. |
| `evals[].prompt` | Required non-blank string. |
| `evals[].expected_output` | Required non-blank string. |
| `evals[].files` | Optional list of paths. Each one is a regular file inside the skill folder. |
| `evals[].assertions` | Optional non-empty list of assertions. `expectations` is accepted with the same shape. When both are present and `assertions` is not empty, `skill_eval.py` reads `assertions`. |
| `evals[].name` | Optional. Matches `^[a-z0-9][a-z0-9._-]{0,63}$` without `..`, because it becomes a folder name. |
| `evals[].layer` | Optional default layer of the case's string assertions: `static`, `llm` or `human`. Without it, a string assertion is `llm`. |
| `evals[].pressure` | Optional `{"kinds": [str, ...], "context": str}`. `kinds` is a non-empty list of non-empty strings and `context` is optional (checked by `skill_eval.load_eval_set`). |

An assertion is a non-blank string or an object `{"text": str, "layer": "static" | "llm" | "human", "check": {...}}`. In the object form `text` and `layer` are required. The layer decides who grades it:

- `static`: the check object below decides. A static assertion without a `check` is a `validate` problem.
- `llm`: the grader model decides during `grade --live`.
- `human`: `grade` queues it in `human_review.json` of the iteration until a person decides it.

## Assertion checks

A static check object has a `type` from `skill_eval.STATIC_TYPES`. `skill_eval.load_eval_set` (so `validate` and every live gate) validates each one against `schemas/evals.schema.json#/$defs/check`. The `evals-valid` rule of `eva.py check` accepts any `check` object and does not apply `$defs/check`. `skill_eval.static_check` grades it against the run's `outputs/transcript.json` and its `workdir/`. Every `path` and `fixture` is a relative path without a `..` segment, and a `fixture` must be one of the case's `files`.

| `type` | Keys | Passes when |
|---|---|---|
| `regex` | `pattern` (required, compiles in Python `re`), `target` (`final` by default, or `transcript`) | `re.search` finds the pattern in the final answer, or in the JSON text of the whole run transcript for `target: transcript`. A `flags` string that holds `i` makes the search case-insensitive. |
| `not_regex` | the same keys as `regex` | The pattern is not found. |
| `tool_call` | `tool` (required), `min` (default 1), `max`, `input_regex` | The number of transcript tool calls named `tool` is between `min` and `max`. With `input_regex`, only calls whose JSON-encoded input matches it count. |
| `skill_triggered` | `expected` (required, `true` or `false`) | The run transcript's `triggered` value equals `expected`. |
| `file_exists` | `path` (required) | `workdir/<path>` exists. |
| `file_equals_fixture` | `path`, `fixture` (both required) | The output file has the same bytes as the fixture file in the skill folder. |
| `csv_cleaned` | `path`, `fixture`, `data_rows` (all required, `data_rows` an integer of 0 or more) | The output CSV equals the fixture's records with every cell stripped and repeated records dropped, and it holds exactly `data_rows` + 1 records. |
| `csv_rows` | `path` (required), `rows`, `min`, `max`, `normalize`, `unique` | Every listed row appears between `min` and `max` times, and with `unique: true` the file has no duplicate record. |

A `csv_rows` check takes only the keys of `skill_eval.CSV_ROWS_KEYS`: `type`, `path`, `rows`, `min`, `max`, `normalize` and `unique`. The schema adds these limits:

- `rows` is a list of lists of strings. An empty inner list matches a blank line. An empty outer list is allowed only with `unique: true`.
- `min` defaults to 1, and `max` has no default. Both are integers of 0 or more, and `max` is at least `min`.
- `min` or `max` needs a non-empty `rows` list.
- `max` below 1 needs an explicit `min: 0`.
- `min: 0` without `max` asserts nothing and is refused.
- `normalize` is `none` (the default) or `strip`, which strips every cell before the compare.
- The check needs a non-empty `rows` list or `unique: true`.
- No path or cell holds a lone surrogate (U+D800 to U+DFFF).

The English-only rule treats `check.pattern`, `check.input_regex` and `check.rows` as run-output matchers: non-English text there gives a needs-review row, not a FAIL. [Rules](rules.md) describes the English-only rule.

## Calibration golden file

`calibrate --golden FILE` measures the grader against human labels. The file is `{"examples": [...]}`, a non-empty list where each example holds `prompt`, `output` and `item` as strings and `label` as a boolean, plus an optional `id` and `expected_output`. `label_blind.py` builds blind labels for it without showing the grader's verdict.

## evals/trigger_evals.json

`schemas/trigger-evals.schema.json` defines the trigger set, and `skill_eval.load_triggers` reads it. The top level is a list of `{"query": str, "should_trigger": bool}` objects with distinct non-blank queries. It holds exactly 10 queries with `should_trigger: true` and exactly 10 with `false`. A missing file is a `validate` problem. No bundle rule checks the shape of this file, and the `english-evals` rule scans its text.

```json
[
	{"query": "Remove the duplicate rows from this CSV export.", "should_trigger": true},
	{"query": "Write a haiku about autumn.", "should_trigger": false}
]
```

## Agent scenarios v2

An agent holds one scenario per JSON file under `scenarios/`. `schemas/scenario.schema.json` defines a scenario, and the `scenarios-valid` rule checks every file. Four more rules of `rules/agent-structure.rules.json` look at the scenarios: `scenarios-present` counts at least 2 valid scenarios, `scenarios-expected` finds an `expected_outcome` in each one, `tools-subset` keeps `expected_tools` within the AGENT.md `capabilities`, and `multi-turn-scenario` finds one scenario with 2 or more turns. These four rules are `soft`, so a failure lowers the score but is not a must failure.

```json
{
	"id": "review-small-diff",
	"turns": [{"user": "Review the change in src/app.py."}],
	"expected_tools": ["read", "search"],
	"must_include": ["src/app.py"],
	"expected_outcome": [{"type": "no_writes"}, {"type": "file_line_citations", "min": 1}]
}
```

| Key | Rule |
|---|---|
| `id` | Required non-blank string. A transcript names its scenario by this id. Two files with the same id are not refused: transcript grading uses the first file in sorted order, even when that file is invalid. |
| `turns` | Required non-empty list of `{"user": str}` objects. |
| `expected_tools` | Required non-empty list of capabilities: `read`, `search`, `write`, `shell`, `web_fetch`, `web_search`, `delegate` or `skill`. |
| `must_include` | Required non-empty list of strings that the final answer must contain. |
| `expected_outcome` | Required non-empty list of typed criterion objects. |
| `success_criteria` | Optional non-empty list of typed criterion objects. |

A v1 scenario, with tool names in `expected_tools` or string outcomes, fails the schema. The AGENT.md v2 frontmatter (`schemas/agent-frontmatter.schema.json`) uses the same criterion objects in its `success_criteria`, each written as one single-quoted JSON object per list item. A criterion holds ASCII text only, so a JSON escape of a non-ASCII character is refused too. A `tools` key, a string `success_criteria` or a criterion written as plain text marks the file as AGENT.md v1, which fails `agent-frontmatter-v2`.

`engine/primitives/transcript.py` (`criterion_evidence`) checks each typed criterion on the final answer and the run's tool calls and results:

| `type` | Keys | Passes when |
|---|---|---|
| `min_verified_sources` | `min` (1 or more) | At least `min` distinct URLs in the final answer also appear in a tool result. |
| `file_line_citations` | `min` (1 or more) | At least `min` `path:line` or `path:start-end` citations in the final answer point at lines that a tool call reading that one file showed with line numbers. |
| `no_writes` | none | No call writes. A call writes when its tool name holds a word of `transcript.WRITE_WORDS` (for example write, edit, create, delete, move, patch, apply, save, commit, push or update, so `TodoWrite` counts), or when a shell command matches `transcript.WRITE_CMD_RE` (for example `rm`, `mv`, `cp`, `touch`, `mkdir`, `tee`, `sed -i`, a `>` or `>>` redirect, or `git commit`, `git push` or `git add`). |
| `max_chars` | `max` (1 or more) | The final answer has at most `max` characters. |
| `markdown_table` | none | The final answer holds a Markdown table with a header row and a separator row. |
| `llm` | `text` (non-blank) | Never decided by code. The row is needs-review, so a human or a model grader decides, and the verdict is at best REVIEW. |

## Transcript format

`eva.py check AGENT --transcript FILE` and `skill_eval.py agent grade` read one run transcript. `eva.load_transcript` validates it against `schemas/transcript.schema.json` and returns the scenario, the final answer, the workspace, the tool calls and the tool results.

```json
{
	"scenario": "review-small-diff",
	"workspace": "/abs/path/to/repo",
	"tool_calls": [
		"Glob",
		{"tool": "Read", "input": {"file_path": "/abs/path/to/repo/src/app.py"}, "result": "1\timport os"}
	],
	"tool_results": ["text a tool returned"],
	"final": "src/app.py:1 imports os."
}
```

| Key | Rule |
|---|---|
| `scenario` | Required non-blank string: the `id` of a valid scenario of the agent. |
| `final` | Required string: the final answer. |
| `tool_calls` | Required list. Each entry is a non-blank tool name, or an object with `tool` (required), `input` (any JSON), `result` (string), `id` (non-blank string) and `is_error` (boolean). Any other key is an error. |
| `tool_results` | Optional list of strings. They count as tool results next to each call's `result`. |
| `workspace` | Optional non-blank string. Access to an absolute path outside it counts as answer lookup. |
| `tool`, `format`, `model`, `session_id`, `turns`, `usage`, `cost_usd`, `is_error`, `agent_loaded` | Optional fields that the schema allows. `tool` is one of `claude`, `codex`, `opencode` or `hermes`. `turns` is a list of `{"user": str, "final": str}`. `usage` is `{"input_tokens": int, "output_tokens": int}`. `cost_usd` is a number of 0 or more, or null. `agent_loaded` is `verified`, `unverified` or `not-loaded`. |

The normalizers in `engine/normalize/` turn a CLI stream into this format. Only the `claude` and `codex` normalizers produce a transcript. The `opencode` and `hermes` normalizers refuse with `engine not available (gates.md G33)`. No normalizer writes `turns`. The `claude` normalizer takes `workspace` from the `cwd` of the `system/init` event.

`eva.grade_transcript` adds these rows, all of them must rows:

- `transcript-valid`: the transcript matches the schema. A malformed transcript gives a FAIL report, not an exception.
- `agent-valid`: the AGENT.md v2 frontmatter is valid.
- `scenario-known`: the scenario id names a valid scenario file.
- `only-declared-tools`: every tool name maps through `rules/data/capabilities.json` to a capability the AGENT.md declares. Names the data file lists as neutral are skipped.
- `expected-tools-called`: a tool of every capability in `expected_tools` was called.
- `no-answer-lookup`: no tool call reaches outside the workspace or digs through git history. A path-key value (`path`, `file_path`, `dir`, `cwd`, `target` and the like) or a word of a command-key value (`command`, `cmd`, `script`, `args`) fails it when it is `~`, `/`, starts with `..`, or is an absolute path outside `workspace`. Without `workspace`, other absolute paths pass. A command that mines git history (`git reflog`, `git fsck`, `git cat-file`, `git rev-list --all`, `git log --all`, `--reflog` or `-g`, `--lost-found`, `--unreachable`) fails it too.
- `outcome-keywords`: the final answer contains every `must_include` string, compared without case.
- `criteria-<kind>` and `expected-<kind>`: one row per criterion. `criteria-` rows come from the AGENT.md `success_criteria` and the scenario `success_criteria`, and `expected-` rows from the scenario `expected_outcome`. `<kind>` is `sources`, `file-lines`, `no-writes`, `length`, `table` or `needs-review` (`transcript.KINDS`). A repeated row id gets the suffix `-2`, then `-3`.
- `outcome-evidence`: at least one criterion is machine-checkable.

[LOOP.md](../LOOP.md) gives the citation rules of `file_line_citations` in detail.

## Workspace layout

A skill's workspace defaults to `<skill parent>/<skill name>-workspace/`, the skill-creator convention, and `--workspace` changes it.

```
<name>-workspace/
  requirements.md, approvals.json, registration.json, spend.jsonl, skill-snapshot/
  iteration-N/
    plan.json, benchmark.json, comparison.md, gate.json, human_review.json, calibration.json, cost.json
    <engine>/                                  claude or codex
      benchmark.json                           this engine only, in the skill-creator schema
      eval-<id>-<name>/
        eval_metadata.json, comparison-<k>.json
        with_skill/run-<k>/, without_skill/run-<k>/ or old_skill/run-<k>/
          workdir/                             the run's output files, without the installed skill copy
          outputs/transcript.json, outputs/final.md
          timing.json, grading.json
      trigger/<config>/results.json
```

Grading runs the static checks first, then the `llm` items (`grade --live`), and queues the `human` items in `human_review.json`. A person sets `passed` to `true` or `false` there and runs `grade` again. Until then the item is `pending` and stays out of the pass rate. The LLM grader must be another model than the one under test. The blind pairwise comparison shows each `with_skill` run next to the same-numbered baseline run in an order drawn from `random.Random(seed)` and records `mapping` and `seed` in `comparison-<k>.json`.

## Deviations from skill-creator

- An engine level: skill-creator writes `iteration-N/eval-*/<config>/run-*`, the harness writes `iteration-N/<engine>/eval-*/...`. Inside the engine folder the layout follows skill-creator, and the per-engine `benchmark.json` follows its schema. The top-level `iteration-N/benchmark.json` adds the per-engine, trigger, pairwise, pressure, calibration and gate fields.
- Eval folders are named `eval-<id>-<name>`, or `eval-<id>` when the case has no name of its own. The id is in `eval_metadata.json`.
- `grading.json` keeps `expectations[]` with `text`, `passed` and `evidence`, and adds `id`, `layer`, `status` and `grader`. `passed` is null while an item waits for a human or was not graded. Such an item is left out of `summary.pass_rate` and counted in `summary.pending`.
- `benchmark.json` writes `delta` as a string such as `"+0.50"`, as skill-creator does. A run with no graded item is left out of the pass-rate statistics.
- `comparison-<k>.json` keeps `winner` and `reasoning`, and adds `mapping`, `winner_configuration`, `seed` and `grader_model`. A winner other than `A`, `B` or `TIE` is not turned into a tie: the file records `status: "failed"` and the grading stops.
- Human review is per item in `human_review.json`, not the free-form `feedback.json` of agentskills.io.
- The trigger set holds exactly 10 positive and 10 negative queries. Claude trigger runs install the skill folder under `.claude/skills/` instead of writing a command file.
- The approval gates, the budget, the regression rules and the grader agreement threshold are house rules that skill-creator does not have.
