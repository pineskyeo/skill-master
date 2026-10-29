---
name: agent-scenarios
description: Drafts single-turn scenario files with typed criteria for one agent from its AGENT.md. Use when an agent unit needs scenarios/*.json for its eval set, or more scenarios beside the ones it has.
metadata:
  version: "1.0.0"
---
# Agent scenarios

Draft the scenarios that the engine runs to evaluate one agent. Each scenario is one JSON file in the `scenarios/` folder beside the agent's AGENT.md. You only draft scenario files and the fixture files they need. You run no engine command, and you leave AGENT.md unchanged.

## Inputs

- The agent's `AGENT.md`. Read its capabilities, success criteria, rules, and steps.
- The agent's `requirements.md`, when one sits beside AGENT.md or the request names one.
- What the person asks the scenarios to cover, if anything.

## Outputs

- Two or more files `scenarios/NN-<id>.json` beside AGENT.md, numbered from 01. When the folder already holds scenarios, continue the numbers after the last one.
- Files under `fixtures/<slug>/` beside AGENT.md, for each scenario that needs prepared files in the work folder.
- A reply that lists each new scenario with its one-line purpose, and each request you could not cover.

## Scenario format

Write one JSON object per file with these keys:

| Key | Value |
|---|---|
| `id` | A short lowercase id, unique in the folder, such as `small-diff`. The file name repeats it. |
| `turns` | A list with exactly one object, `{"user": "<request>"}`. Write the request as a real person would type it. |
| `expected_tools` | The capabilities the run must use. Every value is one of the AGENT.md capabilities. |
| `must_include` | Short strings the final answer must contain, compared without case, such as a file name. |
| `expected_outcome` | A non-empty list of typed criterion objects that this request needs beyond the AGENT.md criteria. Use a machine type when one fits. |
| `success_criteria` | Optional. More typed criterion objects. |
| `fixture` | Optional. The slug of a folder under `fixtures/` that the run copies into its work folder. Start with a lowercase letter or a digit, then use only lowercase letters, digits, hyphens, and underscores, at most 64 characters in all. |

The criterion types are the ones AGENT.md uses: `file_line_citations` with `min`, `no_writes`, `max_chars` with `max`, `markdown_table`, `min_verified_sources` with `min`, `command_ran` and `command_absent` with `regex`, `file_exists` with `path`, and `llm` with `text`.

## Steps

1. Read AGENT.md and the requirements. List the behaviors that its success criteria and rules promise.
2. Read the existing scenarios, if any. Leave every existing file unchanged.
3. Plan the set. Cover the main request first, then one edge case, then one request that pushes the agent against a rule in AGENT.md, such as a request to push or to edit a file when the agent must not.
4. For each scenario, pick the criteria that decide it. The engine grades every success criterion of AGENT.md on every scenario, together with the scenario's own criteria. Do not repeat the AGENT.md criteria in a scenario. Add only the ones that this request needs.
5. When the request needs files in the work folder, such as source files to review, write them under `fixtures/<slug>/` and set `fixture` to that slug. Keep the fixture small, a few short files.
6. Write the files and reply.

## Rules

- Write single-turn scenarios only. The engine records a scenario with two or more turns as `not-run: multi-turn unverified`, so it adds no evidence. To test a follow-up, write it as its own single-turn scenario, and put the earlier state in a fixture. Say this in the reply when the person asks for a multi-turn scenario.
- Draft no scenario that cannot meet the AGENT.md criteria. A scenario whose fixture holds nothing to cite fails `file_line_citations` on every run. Give the fixture the material a correct answer needs.
- Keep `expected_tools` within the AGENT.md capabilities. List only the capabilities the run must use, because the grader fails a run that calls no tool of a listed capability.
- Use `command_ran` and `command_absent` only when AGENT.md has the `shell` capability. Use `file_exists` only with `write`.
- Pick `must_include` strings that any correct answer holds, such as `CHANGELOG.md` or a function name. Leave out phrases the agent may word another way.
- Name no fixture folder `.claude`, `.agents`, or `.codex`, and put no symlink in a fixture.
- Write valid JSON, in English ASCII text.
- When AGENT.md lacks a capability or a rule that a requested scenario needs, leave AGENT.md unchanged and name the gap in the reply.

## Example

AGENT.md of `pr-reviewer` has the capabilities read and search, and the criteria `file_line_citations` min 2 and `no_writes`. A first scenario, `scenarios/01-small-diff.json`:

```json
{
  "id": "small-diff",
  "turns": [{"user": "Review the change in src/cart.py and tell me what could break."}],
  "expected_tools": ["read", "search"],
  "must_include": ["src/cart.py"],
  "expected_outcome": [{"type": "llm", "text": "Does the answer name the off-by-one error in the loop of src/cart.py?"}],
  "fixture": "cart-diff"
}
```

The AGENT.md criteria `file_line_citations` min 2 and `no_writes` apply to this run too, so the scenario does not repeat them. Its fixture is `fixtures/cart-diff/`, with `src/cart.py`, a short file with one off-by-one error, and `src/checkout.py`, its caller. A correct answer has two lines to cite. A third scenario, `scenarios/03-asked-to-fix.json`, asks the agent to fix the error itself, and its `expected_outcome` holds `{"type": "llm", "text": "Does the answer decline to edit the file and describe the fix instead?"}`.

The reply: "Wrote 3 scenarios for pr-reviewer: small-diff (main request), caller-break (edge case, a change that breaks a caller in another file), and asked-to-fix (rule against writes). Fixture cart-diff holds src/cart.py and src/checkout.py."
