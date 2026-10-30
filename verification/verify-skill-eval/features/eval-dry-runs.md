# Eval commands without a paid run

`skill_eval.py` validates a skill's eval set, prints what a run would do and cost, and reads recorded iterations. Without `--live` it starts no model and no tool CLI. A user runs these commands to check a skill before spending anything.

## Sub-features

- `validate` checks the eval set, the English-only rule and multi-tool parity.
- `estimate` prints the cost table.
- `plan` prints the plan, the gates and the estimate.
- `run-dry` prints the plan for `run`, `loop` and `calibrate`.
- `refusals` covers `--dry-run` without `--live`, count flags on the wrong command, and an engine with no adapter.
- `grade-report` reads a recorded iteration.

## How to get to it (user POV)

- Run `python3 skill_eval.py validate SKILL`, `estimate SKILL` or `plan SKILL`.
- Run `python3 skill_eval.py run SKILL`, `loop SKILL` or `calibrate SKILL --golden FILE`.
- Run `python3 skill_eval.py grade SKILL` or `report SKILL`.
- Run `python3 eva.py eval <arguments>`, which passes the arguments to `skill_eval.py`.

## Driving it with drive.py

Preconditions:

- `drive.py doctor` prints `doctor: ready`.
- For `skill-recorded-iteration`, the journey deletes `comparison.md` and `gate.json` of the last iteration first, so the `has` row after `report` proves a real write.

- **Validate.** Run `drive.py journey eval-dry-runs`. The first row exits 0 and prints `eval set OK: 6 cases, 20 trigger queries`.
- **Plan.** The `plan` row prints `DRY RUN: nothing was executed` and the line `workspace: .../fixtures/skills/csv-cleaner-en-workspace`.
- **Run without `--live`.** The `run`, `eva.py eval run`, `loop` and `calibrate` rows print the same `DRY RUN` line.
- **Refusals.** `run --dry-run` exits 2 and prints `--dry-run checks the gates`. `run --cases 3` exits 2. `run --engines opencode` exits 2 and prints `is not available`.
- **No iteration yet.** `grade` and `report` exit 2 and print `no iterations in`.
- **Nothing was left.** The `lacks` row proves the workspace folder is absent. `silent()` proves no process, no socket and no write but the tempfile probe.
- **A recorded iteration.** Run `drive.py journey skill-recorded-iteration`. `report golden/skills/csv-cleaner` exits 3 and prints `iteration-4: NEEDS APPROVAL` and `stale: rules changed since this iteration ran`. The `has` row shows it rewrote `comparison.md` and `gate.json`. `grade --iteration 2` prints `static + human queue only (dry run); LLM items not graded`.

## Gotchas

- A dry run is not free of writes. `plan`, `run`, `loop`, `calibrate` and `eva.py eval run` each create and remove one file in `$TMPDIR`, the probe of `tempfile.gettempdir()`. The hook logged 12 write events across 6 children.
- `grade` without `--live` writes into the iteration. The hook logged 26 write events in `skill-recorded-iteration`. It is a dry run for spending only.
- `calibrate --golden no-such.json` exits 0 and prints the plan. Without `--live` nothing reads the golden file, so this row proves nothing about that file.
- `plan` and the `live gates` block read the scratch `$HOME`. The child opened `machine-waiver.json` and `spend.jsonl` read-only and the hook logged the opens, so a run with the real `HOME` would read the real files.
- The `plan` output holds the full launch command, including settings JSON, on one line of several kilobytes. Match a narrow regex.
