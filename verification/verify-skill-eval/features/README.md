# skill-eval verification map

This directory is the maintained source for verifying the user-facing behavior of `eva.py` and `skill_eval.py`. Read this index before you drive a command, then use the matching feature file as the recipe.

## Baseline preconditions

- Work from the repository root.
- `python3 verification/verify-skill-eval/drive.py doctor` prints `doctor: ready`.
- No `--live` flag, no approval command and no tool CLI appears anywhere in a recipe.
- Every journey makes its own scratch folder, so no recipe needs a prior state.

## Driving conventions

- Start every recipe from a fresh journey. Do not reuse a scratch folder.
- Treat every command as literal. Paths are relative to the scratch `work/` folder, which holds copies of `bad`, `golden`, `skills`, `tests/fixtures` and `transcripts`.
- A folder's workspace is `<parent>/<frontmatter name>-workspace`. The name comes from the frontmatter, not the folder. An install folder uses the same name.
- Add a row to `JOURNEYS` in `drive.py` rather than running a tool by hand, so the evidence is kept.

## Proof and skip reporting

- Proof is the command, its exit status, its stdout and stderr, and a probe of the resulting file.
- A file claim needs a `has` or `same` row. An absence claim needs a `lacks` row on the exact path the tool writes.
- A mutation claim needs the state before and the state after, through the same path. `unit-lifecycle` shows the pattern with `version: 1.0.0` and `version: 1.0.1`.
- Read `verdict.json` for `repo_unchanged` and the side effects before you report a pass.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with drive.py` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run, and every product behavior a journey reports.

## Features

- [Static checks](./static-checks.md) covers `check`, `check-all`, `schema-check` and transcript grading over the bad, golden and real units.
- [Eval commands without a paid run](./eval-dry-runs.md) covers `validate`, `estimate`, `plan`, `run`, `loop`, `calibrate`, `grade` and `report`.
- [Agent evaluation](./agent-evaluation.md) covers every `agent` subcommand, wrappers, scenarios, fixtures, pins and the golden agents.
- [Install and releases](./install-and-releases.md) covers `install`, `version bump`, `deprecate`, `drift`, `hub build`, `snapshot` and `builder-check`.
- [Hash binding](./hash-binding.md) covers what the digests bind and the two open candidates.
