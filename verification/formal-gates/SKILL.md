---
name: formal-gates
description: Bounded formal checks of skill-master's approval hashes (sha256_labeled, skill_tree_hash, install.skill_files, the gate hashes) and of the spend ledger (engine/spend.py and the live start order). Use before you trust a change to those files, when a "VERIFIED CODE CHANGED" line appears, or to ask whether a recorded approval still binds what it approved. It runs the real functions in scratch folders, starts no model and no tool CLI, and records no approval.
---

# Formal gates

A test checks the cases someone wrote. This folder checks every case inside a stated bound. It holds two layers, and they differ in what they prove.

- **What ran.** `check.py` and `ledger_search.py` run the real product functions over every input up to a bound. They prove that bound and nothing above it.
- **What did not run.** `SpendLedger.tla` and `SpendLedger.cfg` state the ledger as a TLA+ model. No Java runtime and no TLC exist on the machine that wrote them, so neither the parser nor the checker ever read them. Treat them as a design document with the same action names as the search. Never call them checked.

## Does your change touch the verified code

It does if it edits any of these:

- `skill_eval.py`: `sha256_labeled`, `skill_tree_hash`, `agent_bound_files`, `gate_hash`, `waiver_hash`, `eval_set_files`, `live_plan_hash`, `live_exec`, `Budget`, `guard_call`, `spend_admit`, `spend_settle`, `run_iteration`, `agent_live_start`, `_live_estimate`, or any line of `_dispatch`. The whole function is pinned, and the live start sits at its end.
- `engine/rule_runner.py`: `sha256_labeled`.
- `engine/install.py`: `skill_files`.
- `engine/primitives/script_denylist.py`: `unit_tree`, `SKILL_IGNORE`.
- `engine/spend.py`: all of it.

You do not need to judge this by eye. The command prints `VERIFIED CODE CHANGED: <file>:<name>` when a pinned source differs, with `engine/spend.py:*` as the name of that whole file, and `VERIFIED CODE MOVED: model action <name>` when a line a model action points at no longer holds its text. A pin line does not change the exit code.

## Run it

From the repository root:

```
python3 verification/formal-gates/check.py
```

It took 41.7s when measured, with Python 3.9.6 and the standard library. Other forms:

| Command | Use |
|---|---|
| `check.py --root PATH` | run against the code under PATH, default the repository |
| `check.py --only ID` | one property |
| `check.py --only ID --real-order` | one `L-` property on the API-key real-order configurations only |
| `check.py mutants` | show every property can fail, on a scratch copy, 113.1s when measured |
| `check.py pins` | print the current pins |

Exit 0 means every property matches `BASELINE` in `check.py`. Exit 1 means one differs. Exit 2 means the checker itself could not run. A run never writes to the repository. Each run compares a digest of the code before and after, and exits 2 if they differ. Evidence goes to `<tempfile.gettempdir()>/formal-gates/evidence/<UTC stamp>-check/report.txt`.

Exit 0 does not mean the code is clean. `BASELINE` holds two open findings as `BROKEN`, and exit 0 says they are the same as before.

## Tell a real bug from a modeling error

Read the counterexample against the code it names.

- **A real bug.** A ledger counterexample prints numbered steps, each with a file and a line. Open each line. If the code does that step in that order, and the promise comes from `references/auth-and-cost.md`, `references/approval-gates.md` or a docstring, the bug is real. A list counterexample prints two different lists with one digest. Run them through the function by hand and the digests will match.
- **A modeling error.** The trace needs a step the code cannot take. Examples are a `Gate` that disagrees with `_live_estimate` at `skill_eval.py:6129`, an action that crosses a call the driver in `ledger_search.py` `World.command` does not make, or a row shape the seed builds and the code never writes. Fix the model. `World.command` restates three pieces by hand, because they sit in code that must not run here. Look there first. They are the gate at `skill_eval.py:6736` and 6129, the settle at 2913-2917, and launch then consume from `Engine.run` at 1255. A reorder in `Engine.run` opens no hole, because the pinned `consume` in `live_exec` at 434 then finds no open intent and refuses before the CLI starts.
- **A checker error.** Exit 2 with a traceback, or `the search and SpendLedger.tla name different actions`, says the check did not run. No property result counts.

Never weaken a property or a bound to make it pass. A finding stays in `BASELINE` as `BROKEN` until the code changes.

## The properties

Status is the result on this repository, measured with the bounds below.

| Id | Status | Cases | Promise | Harm |
|---|---|---|---|---|
| `P-framing` | BROKEN | 471559 | Two different (label, content) lists give two different `sha256_labeled` digests. | An approval stays approved after the bytes it named moved between files or turned into a link. |
| `P-framing-copies` | HOLDS | 2839 | The three framing copies give one digest for one list. | A plan hash and a registration hash stop agreeing for the same files. |
| `P-coverage` | BROKEN | 1097 | On the skill install path, every regular file `install.skill_files` copies is bound by one of the 8 digests a gate reads (`skill_tree_hash`, `skill_md_sha256`, `live_plan_hash`, `eval_set`, `requirements`, `requirements_eval_set`, `rule_review` and `coverage_waiver:hermes`). The agent user-scope path is outside it. | A recorded approval or an eval result says nothing about a file that install ships to the user. |
| `L-exclusive` | HOLDS | 26118 | At most one command is past `Ledger.open` on a workspace. | Two commands spend at once and neither sees the other's rows. |
| `L-preview-matches` | HOLDS | 26118 | A command recovers exactly the USD its budget gate subtracted. | A command starts with a budget gate that never saw a crashed call. |
| `L-counted-once` | HOLDS | 26118 | A crashed call gets one settling row, and none dangles after `Ledger.open`. | A crashed call is charged twice or never. |
| `L-intent-first` | HOLDS | 26118 | On the `run_iteration.one` path, every CLI start has its allowed intent row on disk first. | A crash mid-call leaves a paid call no later command can count. |
| `L-hard-cap` | HOLDS | 26118 | On the `run_iteration.one` path under API-key billing, recovered USD plus admitted bounds stay within `--budget-usd`. | A command passes the hard cap. |
| `L-readable` | HOLDS | 26118 | `spend.jsonl` stays readable by `_scan` at every step, a torn last line included. | Every later live start, plan and report stops on a corrupt ledger. |

The six `L-` rows share one search, so they share one case count.

### Why these targets

- **Framing.** A bug here lets a stale approval read `approved`. Tests miss it because `tests/test_rule_runner.py:733` only pins the two source copies equal, and a test would need a NUL or a sentinel inside a hashed file to see it.
- **Coverage.** A bug here ships bytes no approval or eval result ever named. Tests miss it because no test calls `install.skill_files`, so nothing compares its list with `eva.unit_tree`.
- **Spend ledger.** A bug here breaks the hard cap and the crash accounting, which are among the controls that stayed when N1 dropped signatures. Tests miss it because `tests/test_spend_ledger.py:309` drives one order of two commands and the crash tests run one command after another, so no test interleaves a preview, a crash and a lock.

The other candidates ranked lower. A recorded approval binds the hash computed before it is written, so a file that changes afterward reads stale and a race there fails closed. User-scope install recomputes its manifest hash after recording (`skill_eval.py:5738`) and writes bytes it already holds in memory. The loop's writable set is a table plus a before-and-after fingerprint, and its builder has no Bash tool.

## The counterexamples

**`P-framing`, smallest.** 78948 of 471559 lists share a digest with a smaller list.

1. `skill_tree_hash` passes a folder link up its own path as a marked entry `('a', b'<cycle>')`.
2. `sha256_labeled` hashes `a`, NUL, the marker, NUL.
3. A regular file `a` that holds `<cycle>` hashes the same bytes when it is the last pair of the walk.
4. Both lists give `37e6b495...`. `verification/verify-skill-eval/repro_sha256_labeled.py` A2c shows the same shape for an absent path against a file holding `<missing>`.

**`P-framing`, regular files only.** This one needs no sentinel and no link.

1. One file `a` holds `\0b\0`.
2. Two files `a` and `b` are both empty.
3. `sha256_labeled` writes `a`, NUL, NUL, `b`, NUL, NUL for both.
4. Both lists give `0ffc6787...`. A recorded approval of the first folder reads `approved` on the second, as `verification/verify-skill-eval/repro_sha256_labeled.py` A1c shows through `plan`.

**`P-coverage`.** 1013 of 1097 paths install and move none of 8 gate digests.

1. A file lands at `.git` in the skill folder. `FEEDBACK.md`, `__pycache__`, `x-workspace`, `FEEDBACK.json` and `.git/f` behave the same way.
2. `install.skill_files` lists it, because it walks every regular file.
3. `eva.unit_tree` drops it, because `SKILL_IGNORE` names it. `eval_set_files` does not list it either.
4. Two different contents give equal `skill_tree_hash`, `eval_set`, `requirements_eval_set`, `rule_review`, `coverage_waiver:hermes`, `requirements`, `skill_md_sha256` and `live_plan_hash`.
5. A project-scope install reads no approval, so this bypasses no gate. It means an approval says nothing about those files.

**The `L-` rows** have no counterexample on this repository within the bound. So two concurrent runs on one workspace cannot both pass the cap check. `Ledger.admit` compares the command's own `budget.spent` in memory, and the exclusive flock taken at `engine/spend.py:170` is held from `Ledger.open` until `Ledger.close`. A second command refuses at `TryLock`, or at `Reread` when a crash changed the dangling set after its preview.

## Bounds and assumptions

- `P-framing` covers every list of 0 to 4 entries. An entry is a file, an absent path or a marked link. Labels are `a`, `b` and `a\0b`. Contents are built from 6 tokens, which are NUL, `a`, `b`, `<missing>`, `<outside>` and `<cycle>`. A list holds at most 5, 4, 3 and 1 tokens in all for 1, 2, 3 and 4 entries. The tokens hold no delimiter of a future framing. A change to the framing must add its own delimiter bytes to `TOKENS`, or the check cannot see a broken fix.
- `P-framing-copies` covers every list of 0 to 2 entries with at most 2 tokens. `agent_bound_files` takes no marked entries.
- `P-coverage` covers every path of 1 to 3 segments from `evals`, `__pycache__`, `x-workspace`, `.git`, `FEEDBACK.md`, `FEEDBACK.json`, `scripts`, `files`, `evals.json` and `f`, added to a copy of `tests/fixtures/skills/csv-cleaner-en`. `check.py` leaves out paths the fixture makes impossible. An added `agents/openai.yaml` keeps `rule_review` from being empty. The path `f` is a control and must move a digest, or the run exits 2.
- The `L-` rows cover 8823 states and 26118 transitions over the 10 configurations in `ledger_search.CONFIGS`. That is 2 commands on one workspace (3 in one configuration), 1 to 4 call attempts each with at most 3 admitted, 0 to 3 crashed calls in the seed ledger, and one seed with a torn tail. A crash can land at every step and a torn write at every append. The cap is $0.05 and the estimate $0.01. Under API-key billing the engine is the `capped-stub` of `tests/test_spend_ledger.py`, because no real engine is admitted there today.
- The API-key configurations come in two kinds, and their names say which. A stress configuration replaces the real `guard_call` with a fixed $0.02 bound. The code never takes that order. It exists to load `Ledger.admit`, and the `cap-unchecked` mutant dies there, in `api-key stress, empty ledger, 3 calls`. A real-order configuration calls the real `guard_call` first, as `run_iteration.one` does at `skill_eval.py:2899`. From $0.05 it offers $0.02, $0.01 and $0.01, then refuses at `skill_eval.py:2700`. It offers half of what is left, so the cap compare in `Ledger.admit` never fires there and `cap-unchecked` survives. That is expected, because `Ledger.admit` is a second line behind `guard_call`. `check.py mutants` shows both, and it shows `L-hard-cap` reading `BROKEN` on the real order once `guard_call` overbids too.

The ledger search makes these assumptions:

- `Ledger.open`, `Ledger.admit`, `Ticket.launch`, `Ticket.result`, `dangling`, `_scan`, `Budget`, `spend_admit` and `spend_settle` are the real functions, and so is `guard_call` in every configuration but the stress ones. `check.py` swaps `spend.os`, `spend.fcntl`, `spend.open` and `spend._now` in process, so the search pauses each real read, flock, ftruncate and write. `fsync` does nothing during the search, because the search does not model durability.
- One step is one system call on the ledger. The flock is real, and two commands are two open file descriptions in one Python process. `flock` treats those as separate holders.
- One `os.write` of one row is atomic against one `read`, on a local volume. A crash can tear it, and `CrashTorn` writes half the row.
- A crash runs no handler that charges the call. It closes the process's descriptors, which drops the flock, as the kernel does. The `Killed` exception unwinds through the `finally` and `except BaseException` blocks of `engine/spend.py`, and those only close descriptors.
- Every call costs exactly its bound.

Action names match `SpendLedger.tla` one to one, and `check.py` exits 2 if they drift. `ledger_search.ACTIONS` gives each action a file and a line.

Two invariants differ between the model and the search. `Readable` in the `.tla` marks the ledger corrupt on the first row glued to a torn tail. `_scan` accepts a non-JSON last line as torn, so the search reads that state as readable and fails one row later. `Exclusive` in the `.tla` covers the whole flock span from `reread` on. `holding` in `ledger_search.py` starts only after `Ledger.open` returns, so the `.tla` invariant is the stronger of the two. `PreviewMatches` uses `Opened`, the states after `Ledger.open` returns, as the search does. The second clause of `CountedOnce` names `preview` over `Opened`, because a command's own call dangles between its intent row and its result row. The search calls `spend.dangling` once, when `Ledger.open` returns. By reading, the compare in `Reread` makes the two agree at that point. The `.tla` states the API-key case with the fixed `Bound` of the stress configurations and does not state the real `guard_call` order.

## Out of scope

- Sizes above the bounds. A pass covers the bound.
- `sha256_files`, which frames with a basename and no terminator. It feeds `requirements`, `skill_md_sha256` and the benchmark hash, and it is not enumerated here.
- The caller side of each digest. `verification/verify-skill-eval/features/hash-binding.md` maps which collisions reach a gate.
- The agent user-scope install path and the per-file `source_sha256` of its `install_user` manifest (`engine/install.py:319`, `references/approval-gates.md:33`). `load_policy` allows only `agent.claude` under `user_scope`, `install_user_scope` refuses a skill, and `install_items` calls `skill_files` only for a skill. So `P-coverage` compares the 8 digests of the skill install path and never that manifest.
- The ledger under an overshoot, an unreported cost, a CLI that keeps running after its parent died, a power loss, a network volume, or a `spend.jsonl` a third party edits or removes.
- A cap across commands or across workspaces. `references/auth-and-cost.md` says the cap is per command, so a second `--workspace` or a second command gets its own cap. That is the stated contract, so no property claims more.
- The other paid call paths. `L-intent-first` and `L-hard-cap` cover the `run_iteration.one` path only. The admit and launch of `LLMGrader._call` (`skill_eval.py:1862`, 1868), `CodexGrader._call` (2037, 2053) and `ClaudeBuilder.__call__` (4171, 4180), and the admit of `one` in `agent_run_iteration` (4948), are not modeled and not pinned.
- The gates of `check_live_gates` other than `_live_estimate`, the approval record, the plan hash recheck and `enable_live`. `Gate` is the `_live_estimate` compare inside `check_live_gates`, and the rest sit between `Preview` and `TryLock` in `_dispatch`. They do not touch the ledger.
- `approvals.json`. `store_approval` reads, changes and rewrites it with no lock, and `wjson` truncates in place. Reading the code suggests two concurrent `approve` commands can lose a record. No check here drives that.

## What could not be run, and why

- TLC, Apalache, the TLA+ parser, Alloy and every SMT solver. `drive.py doctor` reports no Java runtime, no TLA+ or Alloy jars, no verification tools on PATH and no z3, cvc5, hypothesis, crosshair or pysat module. Nothing may be installed.
- The real live start. `_dispatch` records an approval and starts a paid run, so `World.command` restates its order from `skill_eval.py:6735-6765` and `run_iteration.one`.
- A real kill of a real process. Crashes are exceptions in threads.

## Update the verification when a change is intended

1. Run `check.py`. Read every status change and every pin line.
2. If a pin line names a function you changed, reread the property and, for the ledger, the actions in `World.command` and `SpendLedger.tla`. Keep the action names the same in both.
3. If a line moved, fix `ledger_search.ACTIONS`.
4. If you fixed a finding, set its row in `BASELINE` to `HOLDS` in the same change. Exit 1 tells you to. The fix in `mutants.py` shows one way, and the fix must cover all three framing copies: `skill_eval.sha256_labeled`, `rule_runner.sha256_labeled` and `skill_eval.agent_bound_files`.
5. Add a `Mutant` for any new property. A `Mutant` is one exact-text edit of a scratch copy, and `base` names a `Fix` to apply first. Run `check.py mutants`. Each `old` text must match exactly once, or the run stops with exit 2.
6. Run `check.py pins` and paste the output into `PINS` in `check.py`.
7. Update the numbers in this file from a real run.

## The mutants

`check.py mutants` copies the code to a scratch folder and works only there.

- For a property that holds, each `Mutant` breaks the real code and the named property must read `BROKEN`. It removes the flock, shares the flock, drops the preview compare, forgets recovered rows, skips recovery, drops the intent row, drops the cap compare, zeroes the recovered amount, keeps a torn line, or changes one framing copy.
- For a finding, a `Fix` first makes the property read `HOLDS`, which proves the check can pass. A broken variant of that fix must then read `BROKEN`.
- Two rows show an `old` text with 0 or many matches stops the run. One row shows the code digest moves when a file appears. Six rows show the plain command exits 1 with the pin line on a broken copy, prints `VERIFIED CODE MOVED: model action TryLock` on that copy, exits 2 when a check writes into the code under test, exits 2 when the search meets a `SpendError` other than the two `Ledger.open` refusals, exits 2 on a scratch copy of this folder with one action renamed in the `.tla`, and exits 2 on a folder with no code.
- One row reads the real `guard_call` offers from $0.05. Two rows use `--real-order`, which searches only the API-key real-order configurations. `cap-unchecked` must read `HOLDS` there, and adding `guard-overbids` must read `BROKEN`.
