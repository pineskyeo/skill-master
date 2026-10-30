# Static checks

`eva.py` grades a skill or an agent folder against the rule bundle and prints PASS, REVIEW or FAIL with one row per check. It also checks a JSON file against a schema and grades one agent run transcript. None of this starts a process or writes a file.

## Sub-features

- `check-unit` grades one folder and prints the verdict and `ruleset_sha256`.
- `check-all` prints one line per unit and a total line.
- `check-rules` runs a non-default bundle, which can never give PASS.
- `schema-check` validates one JSON file against one schema.
- `check-transcript` grades one transcript against an agent and its scenarios.
- `real-skills` checks that the three shipped skills read PASS.

## How to get to it (user POV)

- Run `python3 eva.py check PATH`, with `--json` or `--rules DIR`.
- Run `python3 eva.py check-all PATH`.
- Run `python3 eva.py schema-check SCHEMA INSTANCE`, with `--unit DIR` when the schema uses `x-path-exists`.
- Run `python3 eva.py check AGENT --transcript FILE`.

## Driving it with drive.py

Preconditions:

- `drive.py doctor` prints `doctor: ready`.

- **Bad units fail.** Run `drive.py journey static-checks`. The row `check-all bad` exits 1 and prints `total=6 pass=0 review=0 fail=6`.
- **Golden units fail.** The row `check-all golden` exits 1 and prints `total=5 pass=0 review=0 fail=5`. `tests/test_eva.py` states why. The golden skills are Korean and the golden agents are AGENT.md v1.
- **A good unit passes.** The row `check fixtures/skills/csv-cleaner-en` exits 0 and prints `[PASS]`.
- **A non-default bundle never passes.** The row with `--rules {repo}/rules` exits 3 and prints `bundle-non-default`, even though it names the same rules.
- **A broken bundle stops.** The row with `--rules no-such-bundle` exits 2 and prints `bundle error:`.
- **The schema needs its unit.** The row with `--unit` prints `schema-check: valid`. The row without `--unit` exits 1 and prints `no unit root given`.
- **Real skills.** Run `drive.py journey real-skills`. The first row exits 0 and prints `total=3 pass=3 review=0 fail=0`. Each of the three then validates with `multi-tool parity: claude=pass codex=pass opencode=pass hermes=pass`.
- **Transcript.** Run `drive.py journey agent-lifecycle`. The row `check ... --transcript transcripts/code-reviewer-drill.json` exits 3 and prints `[REVIEW]`, because an `llm` criterion stays `criteria-needs-review`.

## Gotchas

- `golden/` is not a set of good examples for the current rules. Five of five units FAIL, and the tests pin that. Do not read `golden/` as a target.
- `schema-check` of `evals.json` without `--unit` fails closed. The draft of this skill treated that as a product failure. It is a documented refusal.
- `check-all` on a folder with no unit exits 1 and prints `no SKILL.md/AGENT.md units found` to stderr, with no `total=` line.
- `eva.py check` gives PASS to a skill that holds a dangling link inside its folder. `install` and `snapshot` refuse the same folder, so a PASS does not mean the folder can be copied. Observed in `repro_sha256_labeled.py`, sub-claim A2.
