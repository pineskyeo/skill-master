# Skill Master

Skill Master checks, evaluates and installs agent skills and agents built for three tools: Claude Code, OpenCode and Hermes Agent. A skill is a folder with a `SKILL.md`. An agent is a folder with an AGENT.md v2 file, scenario files under `scenarios/` and one generated wrapper per tool under `wrappers/`. `eva.py` and `skill_eval.py` run on Python 3.9 with the standard library only.

## Two command-line tools

- `eva.py` runs the static checks. `check` grades one unit against the rule bundle in `rules/`, prints one row per check and the verdict, and ends with the `ruleset_sha256` line. `check-all` prints one verdict line per unit under a folder. `check --transcript` grades one agent run transcript. `feedback` writes `FEEDBACK.md` and `FEEDBACK.json` into a unit, `index` writes `INDEX.md` and `registry.json`, `generate` renders an agent's four tool files or a skill's invocation metadata, and `schema-check` validates a JSON file against a schema.
- `skill_eval.py` is the eval harness and the unit lifecycle. It validates an eval set, plans and estimates a run, runs a skill with and without itself (or against an older version), grades, reports and loops. The `agent` subcommands do the same for AGENT.md v2 agents per tool. `install`, `version bump`, `deprecate` and `drift` manage releases, and `hub build` writes the static registry site. Without `--live`, the eval commands `plan`, `run`, `grade`, `calibrate` and `loop` start no model and no tool CLI: `plan`, `run`, `calibrate` and `loop` print the plan and the estimated cost, and `grade` grades only the static and human items. The exception is `check-builtin-plugins`, which starts the `claude` CLI without `--live` to list its plugins and makes no model call.

`approve`, `register`, every `--live` start and `install --scope user` write an unsigned recorded approval bound to the gated files, the unit and the rule bundle hash. No key, passphrase, terminal or session is needed. [Approval gates](references/approval-gates.md) explains the records.

## Folder layout

- `bad/` holds skills and agents that fail the checks. `check-all bad` reports FAIL for each one.
- `engine/` holds the library code, including the rule runner, the schema validator, the rule primitives, the transcript normalizers, approvals, spend records, install, the registry and the hub templates.
- `golden/` holds the reference skills and agents, a skill workspace with recorded iterations, `INDEX.md` and `registry.json`.
- `notes/` holds notes on the English-only rule and multi-tool parity.
- `policy/` holds the eval policy, the live and post-builder gate lists, the gate rules, the auth profiles, the install policy, the models and the prices.
- `references/` holds the reference pages linked below.
- `rules/` is the default rule bundle: the `*.rules.json` files and their `data/` files.
- `schemas/` holds the JSON schemas for rule files, eval sets, scenarios, transcripts, frontmatter, tool files, targets and records.
- `targets/` holds one file per tool with its skill, invocation, wrapper, permission, model and render facts.
- `tests/` holds the unit tests, and `tests/rules/` holds the outcome fixtures of every rule.
- `transcripts/` holds sample agent run transcripts and the fixture repository `transcripts/fixtures/drill-repo/`.

The top level also holds `loop.py`, the builder and check loop of [LOOP.md](LOOP.md), `label_blind.py`, which builds blind human labels for `calibrate --golden`, and `claude_bash_gate.py`, the PreToolUse gate for the loop builder's writable set.

## Verdicts

`eva.py` gives each unit PASS, REVIEW or FAIL. The score is the share of passing checks among the checks that apply, so `n/a` and needs-review rows do not count. A unit fails when no check applies, when the score is below 0.8, when a must check fails, or when a category has no pass or a pass rate below 0.5. A unit that does not fail gets REVIEW while any needs-review row remains, and PASS otherwise. `check` exits 0 for PASS, 1 for FAIL and 3 for REVIEW. A run with `--rules DIR` never gives PASS, and a broken rule bundle prints `bundle error:` and exits 2.

## Read more

- [Command reference](references/engine-cli.md): every `eva.py` and `skill_eval.py` subcommand with its flags.
- [Eval set format](references/eval-set-format.md): `evals/evals.json`, `evals/trigger_evals.json`, assertion checks, agent scenarios and transcripts.
- [Rules](references/rules.md): rule files, the bundle hash, the English-only rule, multi-tool parity, scoring and fixtures.
- [Approval gates](references/approval-gates.md): the recorded approvals behind `approve`, `register` and `--live`.
- [Auth profiles and cost](references/auth-and-cost.md): the two auth profiles, the hard cap and the spend ledger.
- [Install, versions and drift](references/install.md): `install`, `version bump`, `deprecate` and `drift`.
- [Feedback loop protocol](LOOP.md): the builder and check loop of `loop.py` and the citation rules for transcripts.
