# Agent evaluation

An agent is a folder with an AGENT.md v2 file, scenario files and one generated wrapper per tool. `skill_eval.py agent` validates it, plans a per-tool eval, grades recorded transcripts and reports a gate. `eva.py generate` renders the wrappers. Every step below works without a paid run.

## Sub-features

- `agent-validate` prints one line per check and per wrapper.
- `agent-plan` prints the cells, the gates and the inputs. `agent run` and `agent loop` without `--live` print the same plan.
- `generate` renders four wrappers and refuses to overwrite a hand-edited one.
- `agent-grade` grades the transcripts of an iteration and leaves `llm` criteria open.
- `agent-report` writes the benchmark, the gate and `comparison.md`.
- `fixtures` checks the `fixture` folder of a scenario.
- `pins` checks the `skills` key of an agent.
- `golden-v1` shows what the tools do with an AGENT.md v1 agent.

## How to get to it (user POV)

- Run `python3 skill_eval.py agent validate AGENT`.
- Run `python3 skill_eval.py agent plan AGENT`, `agent run AGENT` or `agent loop AGENT`.
- Run `python3 skill_eval.py agent grade AGENT` or `agent report AGENT`.
- Run `python3 eva.py generate AGENT`, with `--check` to compare only.
- Run `python3 skill_eval.py pending --unit AGENT`.

## Driving it with drive.py

Preconditions:

- `drive.py doctor` prints `doctor: ready`.
- `tests/fixtures/agents/code-reviewer-v2` ships no wrappers, so the first rows see them as missing.
- The journey builds the iteration in `agent-recorded-iteration` by hand, because only a paid run writes one. It holds `plan.json`, `run.json` and a copy of `transcripts/code-reviewer-drill.json`.

- **Before generate.** Run `drive.py journey agent-lifecycle`. `agent validate` exits 0 and prints `wrapper claude: missing (eva.py generate)`. `agent plan` prints `cell overview claude: not-run: wrapper missing`. `install` exits 2 and names `python3 eva.py generate`.
- **Generate.** The next rows print `4 written`, then `0 written, 0 removed, 4 unchanged`. `agent validate` prints `wrapper claude: up to date`. `agent plan` prints `cell overview claude: planned`.
- **A tampered wrapper.** The `put` row overwrites the Claude wrapper. `generate --check` exits 1. `install` exits 2. `generate` exits 2 and prints `move it away and rerun`. After the `rm` row, `generate` prints `1 written`.
- **Nothing was left.** Before the first `generate`, `lacks` proves `fixtures/agents/code-reviewer-v2-workspace` and `../target/.claude` are absent, and `silent()` holds.
- **Grade and report.** Run `drive.py journey agent-recorded-iteration`. `agent grade` prints `1 run(s) graded; llm criteria stay needs-review`. `agent report` exits 3 and prints `NEEDS APPROVAL`, `coverage-missing:hermes`, `parity-permissions-hermes` and `criteria-ungraded:drill/claude/new_agent/run-1`. After the transcript changes, `agent report` prints `run-ungraded:drill/claude/new_agent/run-1`.
- **Scenario fixtures.** Run `drive.py journey agent-scenarios`. `agent plan skills/agent-scenarios/evals/files/doc-linker` prints `fixture one-broken-link docs-basic: sha256 <hash>, 2 file(s)`. A `.claude` folder, a link and a missing fixture folder each exit 2 with `agent plan refused: fixture docs-basic of scenario one-broken-link: ...`.
- **Pins.** The same journey adds an agent with `skills: [ghost@1.0.0]`. `agent plan` exits 2 and prints `pin ghost@1.0.0: REFUSED (unregistered: no skill folder`.
- **Golden agents.** Run `drive.py journey golden-agents`. `check` exits 1 on `agent-frontmatter-v2`. `agent validate` exits 1. `generate` exits 2 and prints `Migrate AGENT.md to v2`.

## Gotchas

- The agent workspace is `<name>-workspace`, not `<folder>-workspace`. The draft of this skill probed `fixtures/agents/code-reviewer-workspace`, which no tool writes, so that `lacks` row could never fail. The agent's name is `code-reviewer-v2`.
- `generate` refuses a wrapper with no generator header. Move the wrapper away, then rerun. A rerun alone refuses again.
- `agent validate` prints `scenarios present: fail (... 1 valid ...)` and then `validate: OK` for doc-linker. The row is not a hard check, which matches `references/engine-cli.md`.
- `agent plan`, `agent run` and `agent loop` exit 0 on a golden v1 agent and print `scenarios: 0 valid`, although `agent validate` exits 1. A zero exit here does not mean the agent is valid.
- `agent plan` reads through a link inside a scenario fixture. With `fixtures/docs-basic/out` linked to `/etc/hosts`, the hook logged 6 reads of `/private/etc/hosts` in that one `agent plan`, which then refused. By the code, `agent_fixture_files` feeds the eval_set hash and `sha256_labeled` follows the link, while `fixture_files` refuses it.
- `agent grade` with no `--live` writes `grading.json`. It is a dry run for spending only.
- The hand-built iteration proves the grading and report code. It proves nothing about a real run's transcript.
