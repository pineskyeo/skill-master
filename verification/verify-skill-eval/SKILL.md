---
name: verify-skill-eval
description: Drive the skill-master command-line tools (eva.py and skill_eval.py) the way a user does and keep the evidence. Use to prove that the static checks, the no-`--live` eval and agent commands, install and the hash gates work, before you trust a change to engine/, rules/, schemas/, skills/ or golden/. It starts no model and no tool CLI and records no approval.
---

# Verify skill-eval

This skill drives two short-lived command-line tools at the repository root. `eva.py` runs the static checks. `skill_eval.py` is the eval harness and the unit lifecycle, and its `agent` subcommands handle AGENT.md v2 agents. There is no server. The driver is `drive.py` in this folder. Run every command below from the repository root.

## Launch

There is nothing to build. The tools need Python 3.9 and its standard library. To start, run a journey:

```
python3 verification/verify-skill-eval/drive.py journey unit-lifecycle
```

Each journey builds its own scratch folder at `<tempfile.gettempdir()>/verify-skill-eval/scratch/<UTC stamp>-<journey>/`. The folder holds copies of `bad`, `golden`, `skills`, `tests/fixtures` and `transcripts` under `work/`, plus an empty `home/`, `tmp/`, `target/` and the audit hook under `hook/`. Each child runs with `cwd` at `work/`, `HOME` at `home/`, `TMPDIR` at `tmp/`, `PATH=/usr/bin:/bin` and `PYTHONDONTWRITEBYTECODE=1`.

The code under test is the checkout. The driver copies only the data, so `repo_unchanged=True` in every verdict means no child touched the repository. Two different journeys can run side by side. The same journey twice in one second fails on the evidence folder name.

## Doctor

Run this first, and again when anything looks off.

```
python3 verification/verify-skill-eval/drive.py doctor
```

It exits 0 and prints `doctor: ready` when Python is 3.9 or later and a real `eva.py check` of the fixture skill gives PASS with the audit hook loaded and no process, socket or write. That child runs under the same hook and scratch isolation as a journey, because a doctor that claims to be read-only has to show it. The doctor removes its own scratch folder.

The lines that follow are the tool inventory for anyone who needs a verifier. They are true on the machine that ran them:

- `java runtime answered` starts `java -version` on every candidate: PATH, `JAVA_HOME` and the usual JDK folders. On macOS `/usr/bin/java` is a stub that exits 1 without a JDK, so a file on PATH proves nothing.
- `TLA+ jars or apps` and `Alloy jars or apps` list what a bounded search of the home folder, `/Applications`, `/opt`, Homebrew, `/usr/local` and the editor extension folders found. `jar search` names any unreadable folder, because unreadable is not the same as empty.
- `verification tools on PATH` starts each one that is present and shows its exit and first line.
- `tool CLIs on PATH (never started)` only looks. Never start those.

## Drive

```
python3 verification/verify-skill-eval/drive.py all
python3 verification/verify-skill-eval/drive.py journey <name>
```

| Journey | What it proves |
|---|---|
| `static-checks` | `check-all` over `bad` and `golden`, `check` verdicts and exit codes, `--rules`, `schema-check` with and without `--unit` |
| `eval-dry-runs` | `validate`, `estimate`, `plan`, `run`, `loop`, `calibrate`, `grade` and `report` without `--live`, and their refusals |
| `skill-recorded-iteration` | `report`, `grade` and `pending` on the recorded iterations of `golden/skills/csv-cleaner` |
| `agent-lifecycle` | every `agent` subcommand with no iteration, `generate`, the wrapper refusals, `check --transcript`, `pending` and the agent `install` |
| `agent-recorded-iteration` | `agent grade` and `agent report` on a hand-built iteration, and a grading that goes stale |
| `golden-agents` | what the tools do with the two AGENT.md v1 agents under `golden/agents/` |
| `agent-scenarios` | scenario fixtures and pins through `agent plan`, `agent run` and `agent loop`, and each documented fixture refusal |
| `real-skills` | `check-all skills`, `validate`, `plan` and `pending` over the three real skills |
| `unit-lifecycle` | `feedback`, `index`, `install`, `version bump`, `deprecate`, `drift`, `hub build`, `snapshot` and `builder-check` |

A journey is a list of rows in the `JOURNEYS` table of `drive.py`. Add a row, not a helper.

- `S(tool, args, exits, *patterns)` runs `eva.py` (tool `eva`) or `skill_eval.py` (tool `eval`). `{repo}` and `{scratch}` expand in `args`. The row passes when the exit status is listed, every regex matches stdout plus stderr, and the hook loaded in that child. `save="name"` keeps stdout at `<scratch>/name`.
- `put`, `cp`, `rm` and `link` seed the scratch `work/` folder.
- `has`, `lacks`, `same`, `holds` and `silent` probe the result. Paths are relative to `work/`, so `../target/...` reaches the install target. `silent()` asserts that the journey so far started nothing, opened no socket and wrote nothing but tempfile's writability probe in `$TMPDIR`.

An expected value comes from `README.md`, `references/*.md` or a test's stated contract. It never comes from copying what a run printed. A row with the wrong expectation is a driver error, so fix the row. A row that catches the product breaking its docs stays failing, and the reason goes in the feature's Gotchas.

`drive.py` refuses to run any row that names `--live`, `--scope`, `approve`, `approver-session`, `approver-check`, `approve-ui`, `register`, `machine-waiver` or `check-builtin-plugins`. The refusal covers every spelling argparse accepts: `--live=1` and the prefixes `--liv`, `--li` and `--l`.

### What is left out, and why

- Every `--live` start, `--live --dry-run` included. A real start spends money and records an approval.
- `approve`, `approve --batch`, `register` and `install --scope user`. Each records an approval. `install --scope user --dry-run` records nothing, but the driver refuses the flag in any form.
- `machine-waiver sign`, `show` and `revoke`, and `approver-session`, `approver-check` and `approve-ui`. The run rules forbid the first group, and the removed commands prove nothing.
- `check-builtin-plugins`. It starts the `claude` CLI.
- `agent run`, `agent grade` and `agent loop` with a real tool. Without `--live`, `agent run` and `agent loop` read and plan, and `agent grade` writes `grading.json` into the iteration. The journeys drive `agent grade` and `agent report` on a hand-built iteration.

## Evidence

Evidence lives at `<tempfile.gettempdir()>/verify-skill-eval/evidence/<UTC stamp>-<journey>/` and survives cleanup:

- `verdict.json` holds every row, the side effects and the digest of the repository.
- `audit.jsonl` holds one line per event the hook saw, across all children.
- `NN-<command>.txt` holds the command, its exit, its audit counts, stdout and stderr.

A journey passes only when every row passes, `repo_unchanged` is true, and the audit shows no process, socket, write outside scratch or refused tool CLI. A row also fails if the hook did not load in its child, so zero events cannot mean a hook that never ran.

Proof standards:

- Exercise the real command line. In a journey, `drive.py` starts the real scripts, never a function. The repro scripts differ. They also call product functions in process, outside the hook, with the real `HOME`, and hand-write `approvals.json` in their scratch folder. By reading the code, the digest calls of `repro_install_unhashed.py` only read, and `install_skill` in B2 writes only into its scratch sandbox.
- Capture the action and the result. A `has` row after an `S` row proves the file exists, and a `same` row proves its bytes.
- Check side effects. `lacks` rows name the exact path the tool would write, and the `workspace:` line of `plan` pins that path in the same journey.
- Mock only at a production boundary. The one input only a paid run can write is the iteration in `agent-recorded-iteration`, so that journey hand-builds it. The other `put` rows write files a person authors or a test tampers with, such as `pinned/AGENT.md` in `agent-scenarios` and `CHANGELOG.md` in `unit-lifecycle`.
- A check never seen to fail proves nothing. `drive.py selftest` shows that each guard can fail, and that the real `drive()` fails a journey on a process, a socket, an outside write and a repository change when every step passes. `drive.py flip` shows that both repro verdicts flip.

The hook observed what the no-`--live` path does in `eval-dry-runs` and the first block of `agent-lifecycle`:

- It starts no process and opens no socket. The count is 0 across 14 children in `eval-dry-runs` and the 8 children before the first `generate` in `agent-lifecycle`.
- It writes nothing to the workspace, the unit or `home/`. No workspace folder appears.
- It does write. `plan`, `run`, `loop`, `calibrate` and `eva.py eval run` each create and remove one file in `$TMPDIR`, the writability probe of `tempfile.gettempdir()`. The name "dry run" does not mean zero writes. `silent()` allows only that create and remove pair and fails on a file left behind.
- It opens gate files read-only: `$HOME/.config/skill-master/machine-waiver.json` and the workspace `spend.jsonl`. In this scratch both are absent. `$HOME` here is the scratch home, so the hook says nothing about the real home.
- `grade` without `--live` is not a read-only command when an iteration exists. In `skill-recorded-iteration` the hook logged 26 write events under the iteration.

## Cleanup

```
python3 verification/verify-skill-eval/drive.py cleanup
```

It removes the scratch folders only, prints how many, and exits 1 if the scratch root survives. The evidence folders stay. No journey leaves a process behind, because every child runs to completion under a timeout. The driver kills nothing by name.

## Helpers

Run each with `python3` from the repository root.

| Command | Use |
|---|---|
| `verification/verify-skill-eval/drive.py doctor` | health and the tool inventory |
| `verification/verify-skill-eval/drive.py all` | every journey, exit 1 if any fails |
| `verification/verify-skill-eval/drive.py journey <name>` | one journey |
| `verification/verify-skill-eval/drive.py selftest` | shows each guard failing: `refused`, every `lacks` path, the probes, the step checks, the hook, the repository digest and the journey rule of the real `drive()` on a scratch copy |
| `verification/verify-skill-eval/drive.py flip` | builds a fixed scratch copy of the code and shows both repro verdicts flip, and that a crash prints neither word |
| `verification/verify-skill-eval/drive.py cleanup` | removes scratch, keeps evidence |
| `verification/verify-skill-eval/repro_sha256_labeled.py [--root PATH]` | candidate A, collisions in `sha256_labeled` |
| `verification/verify-skill-eval/repro_install_unhashed.py [--root PATH]` | candidate B, files that install outside every digest |

Each repro prints one REPRODUCED or REFUTED line per sub-claim, an overall line, and exits 0 either way. A crash exits 2 and prints neither word. `--root` names the code under test, so a scratch copy can carry a fix. Each writes `report.txt` under the evidence folder.

The features under `features/` are the maintained map. Read `features/README.md` before you drive one. `drive.py all` passing says nothing about candidates A and B. They live in the repro scripts, and `features/hash-binding.md` holds their state.
