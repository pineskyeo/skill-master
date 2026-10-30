# Approval gates

`approve` (every gate, `rule_review`, `requirements_eval_set` and the three `coverage_waiver:<tool>` gates included), `approve --batch`, `machine-waiver sign` and `machine-waiver revoke`, `register`, a user-scope `install`, and the start of every `--live` run of `run`, `grade`, `calibrate`, `loop` and the `agent` commands go through `engine/approvals.py`. `python3 eva.py eval ...` reaches the same code, because it calls `skill_eval.main`.

Approvals are unsigned recorded decisions (unit N1, pinesky's decision of 2026-10-28). No approver key, signers file, ssh-agent, passphrase, typed challenge, TTY, agent-ancestry check or approver session is needed. A command run from a script, a pipe or an agent session records the approval the same way a person at a Terminal does. Every other rail stays: the gate hashes, `ruleset_sha256`, the staleness checks, every gate problem list, a finite `--budget-usd` and `--auth-profile` for `--live`, the estimate within budget, the spend ledger and the API-key refusals.

## What a record proves

A record that reads `approved` says that `recorded_by` decided this kind (`approve`, `register` or `live`) for this gate, this unit, these exact gated files (`sha256`) and this rule bundle (`ruleset_sha256`), plus the budget cap of a `--live` start and the reason of a coverage waiver. The record counts only while all of them still match. A record copied to another gate, unit or kind, or a live record read for another budget, reads `invalid`. A record whose gated files or rule bundle changed reads `stale`.

## What a record does not prove

- Nothing in a record is a cryptographic signature. Anything that can write `approvals.json`, `registration.json` or `$HOME/.config/skill-master/machine-waiver.json` can write a record that reads `approved`, including an agent of the same user. The hash binding stops a record from counting for content it did not name; it does not stop a new record.
- `recorded_by` is the OS user name of the process (`pwd.getpwuid(os.getuid()).pw_name`), not an identity check. The Python API takes another value; the value `coordinator` is valid when a caller passes it.
- A `--live` record binds the inputs when the run starts. The plan hash is computed again just before live mode turns on, so a change made between `prepare()` and that recheck refuses the run. A file that changes during the run is not caught.

## Records

`approvals.json` maps each gate to its record. `registration.json` keeps its fields and stores the `register` record under `"signed"` (the field name is kept for every reader). Its `"approval"` field holds the registration-gate record, or `"auto"` when the gate was clean.

```json
{"format": 1, "method": "recorded", "kind": "approve", "gate": "eval_set", "unit": "branch-setup",
 "sha256": "<64 hex>", "ruleset_sha256": "<64 hex>", "recorded_by": "pinesky", "at": "2026-10-28T09:41:00Z"}
```

A `live` record adds `"budget_usd": <amount>`. A `coverage_waiver:<tool>` record adds `"reason": "<text>"`. Any other field, a missing field or a malformed value reads `invalid`; the value rules are the ones the signed payload of an older record used (`approvals.payload`). A live record is checked at once and not stored.

| Hash | Covers |
|---|---|
| `approve` | `gate_hash(gate)`, the files of that gate |
| `approve`, `coverage_waiver:<tool>` | the sha256 of labeled lines for the tool, the skill tree hash and the `eval_set` gate hash |
| `approve`, `requirements_eval_set` | the sha256 of `requirements_eval_set v1`, `requirements <requirements gate hash>` and `eval_set <eval_set gate hash>`, so an edit to either file stales it |
| `approve`, `install_user` | the user-scope install manifest hash |
| `register` | the sha256 of the canonical JSON of every field of the registration record but `signed` (`skill_eval.REGISTRATION_SIGNED`, plus `machine_waiver` when present) |
| `live` | the sha256 of the canonical JSON of the exact argv, the workspace, the skill folder hash, the `eval_set` and `requirements` gate hashes, the `--builder-prompt` text, the `--golden` file and the `--old-skill` folder hash |

`schemas/registration.schema.json` is part of the rule bundle hash and still names only the signed record form. `skill_eval.registration_schema_errors` therefore drops schema errors inside a record slot (`signed`, `approval`, `approvals.<gate>`, `machine_waiver.record`) that holds a well-formed recorded record of the slot's kind and gate. The registration format stays 2.

## Records signed before N1

A record with `"method": "ssh-sig"` stays readable and is never written again. It verifies with `/usr/bin/ssh-keygen -Y verify` against `~/.config/skill-master/approvers.allowed_signers` (`~` is the home of the account in the password database, not `$HOME`) and reads `invalid` when that file is missing. Its signature covers the kind, gate, unit, `sha256`, `ruleset_sha256`, `at`, and the budget or reason, so any edit reads `invalid`. Inside a test process the command-line approver refuses to verify against the real file. A record with neither `signature` nor `method` (golden's records, every record from before signed approvals) reads `unsigned` and is not approved.

## Order of an approval

For `approve`, `register`, a user-scope `install` and every real `--live` start:

1. The command's own checks (`prepare()`). `approve` computes the gate hash and refuses when `approvals.json` is not valid JSON or is JSON but not an object. `register` builds the registration record. A `--live` start runs every live gate that starts no process (the `--live --dry-run` set). A failure here refuses before anything is printed or recorded.
2. What the approval covers is printed to stderr: the gated files, the gate hash, `ruleset_sha256`, and for `--live` the full command, the workspace and the budget. Every character that is not printable (ESC, carriage return, other control characters, Unicode format marks) is printed as its Python escape, such as `\x1b` or `\r`.
3. A `coverage_waiver:<tool>` gate takes its reason from `--reason TEXT`, else reads one line from stdin (a pipe is fine).
4. One summary line, `<what>: gate <gate>, unit <unit>, sha256 <hash>[, budget $X][, reason R]; recorded by <user>, unsigned`, then the record is read back with the verdict rules below.
5. The caller writes the record. A `--live` start then runs the live gates that start a process (`claude auth status`), recomputes the plan hash, and turns live mode on with the grant.

`engine/approvals.py` starts no process and reads no file for a recorded record. No `claude` or `codex` CLI starts before step 5.

## Refusals

Every refusal exits with status 2 and prints one line to stderr. `<what>` is `approve`, `register`, or `<subcommand> --live`.

| Step | Line |
|---|---|
| command check | `error: <workspace>/approvals.json is not a JSON object (it holds ...); nothing was signed` (`approve`) |
| reason | `approve: reason refused: it is empty; nothing was recorded`, likewise `it is longer than 200 characters`, `it holds a character that is not printable ASCII` and `it has leading or trailing spaces` (a `coverage_waiver:<tool>` gate) |
| reason | `approve: only a coverage waiver takes a reason; nothing was recorded` (`--reason` on another gate) |
| record | `<what>: cannot record: <field problem>; nothing was recorded` |
| batch | `approve --batch: reason refused for <gate> of <unit>: ...; nothing was recorded` |
| batch | `not recorded <unit> <gate>: <gate> for <unit> changed since the list was shown; nothing was recorded` (one line per item on stdout; the other items are still recorded) |
| batch form | `error: approve: give SKILL and --gate, or --batch with --unit` (also for `--batch` with `--reason`) |
| machine waiver | `machine-waiver sign: tools must be distinct names from machine_waivable (opencode,hermes), got ...; nothing was recorded`; `machine-waiver sign: cannot read this Mac's IOPlatformUUID; nothing was recorded`; `machine-waiver sign: reason refused: ...; nothing was recorded`; `error: no machine waiver at ~/.config/skill-master/machine-waiver.json; nothing to revoke` |

## Removed commands

`approver-check`, `approver-session` and `approve-ui` (and `scripts/approve-ui.sh`) keep their names and options so scripts and runbooks still parse. Each prints one line on stdout that says approvals are unsigned recorded decisions and that no approver key, ssh-agent or approver session is needed, and starts nothing:

| Command | Exit |
|---|---|
| `approver-check` | 0, the line starts `approver-check: OK: ` |
| `approver-session` | 0, no subshell is started |
| `approve-ui` | 2, no page is served; the line names `approve --batch --unit DIR` |

`engine/approve_ui.py` is empty. The child-environment scrub stays: every model child of a run, and so the code under test that it runs, starts without `SSH_AUTH_SOCK`, `SSH_AGENT_PID`, `SKILL_MASTER_APPROVER_SESSION`, `PS1` and `PROMPT` (`skill_eval.APPROVER_SESSION_ENV`), so no child reaches an ssh-agent or an approver-session shell left from before N1.

## Files

| Path | Use |
|---|---|
| `~/.config/skill-master/approvers.allowed_signers` | read only to verify records signed before N1 |
| `$HOME/.config/skill-master/machine-waiver.json` | the machine waiver; `machine-waiver sign` writes it (mode 0600), `revoke` deletes it |
| `$HOME/.config/skill-master/approvals.log` | one JSON line per machine waiver sign or revoke (mode 0600) |

A `HOME` that is not an absolute path makes the machine waiver read `invalid`. Inside a test process the machine waiver is never read, written or deleted under the password-database home.

## Coverage waivers

Registration needs a graded dynamic run on every tool that `coverage_required` in `policy/gate.rules.json` lists, or a recorded waiver for each listed tool that did not run (plan.md:1135, Appendix E Q5). The file lists claude, opencode and hermes. OpenCode and Hermes have no engine, so a skill registers today only with `coverage_waiver:opencode` and `coverage_waiver:hermes` records or a machine waiver that lists them. `coverage_required` is part of the rule bundle, so removing a tool from it moves `ruleset_sha256` and every existing approval reads stale. The loader refuses anything but a non-empty list of unique names from the three tools. `approve --gate coverage_waiver:codex` exits 2. A `coverage_waiver:codex` record left in `approvals.json` never counts and `register` does not copy it. A `registration.json` that holds that key reads `invalid` (`does not match schemas/registration.schema.json`). The texts of `policy/gate.rules.json` still say "signed" waiver; that file feeds the ruleset hash, so N1 left it unchanged.

`approve --gate coverage_waiver:<tool>` records one waiver. `engine/approvals.py` holds the three gate names.

**What a waiver binds.** The record's `sha256` is `gate_hash("coverage_waiver:<tool>")`, a sha256 over labeled lines for the tool, the skill tree hash and the eval set hash (`gate_hash("eval_set")`). The record also names the gate, the unit and `ruleset_sha256`. A waiver stops counting when the skill folder, the eval set or the rule bundle changes, and it reads `invalid` under another tool's key or for another unit.

**The reason.** The gate prints the tool, its dynamic eval status in the last iteration's benchmark, the skill and eval set hashes, and that the waiver clears only this tool for this unit, eval set and ruleset. It then takes the reason from `--reason`, else reads one line from stdin. The reason must hold 1 to 200 printable ASCII characters; a line read from stdin is stripped first, a `--reason` value is not. No environment variable or file supplies it. A waiver record without a valid reason reads `invalid`, and so does any other record that carries a `reason`. `approve --batch` reads one reason line from stdin per waiver item, in list order, before it records anything; a refused reason records nothing.

**Where it counts.** `report` checks every waiver when it runs and writes the verdicts into `benchmark.json` as `coverage_waivers`. `compute_gate` reads only the benchmark, so a waiver recorded after a report counts at the next `report`. Each listed tool that neither ran nor has an approved waiver is a gate problem `coverage-missing:<tool>: dynamic eval <status>, waiver <state>`, where the state is `missing`, `unsigned`, `invalid (<why>)` or `stale (<why>)`. A waived tool is a note that names who recorded it and the reason. `plan` prints a `registration coverage:` line with the rule and the waiver state of each listed tool.

`register` recomputes coverage before its other checks, from the benchmark's `tool_coverage`, the current `coverage_required` and the waiver verdicts at that moment, never from `gate.json`. A `registration` approval does not clear a coverage problem. `loop` stops with `needs-coverage` when coverage problems are the only problems left.

**What a waiver does not prove.** It says nothing about how the skill behaves on that tool. No code checks the reason for truth.

## The machine waiver

`machine-waiver sign [--tools opencode,hermes] [--reason TEXT]` writes one recorded record to `$HOME/.config/skill-master/machine-waiver.json`:

```json
{"format": 1, "method": "recorded", "machine_id": "<IOPlatformUUID>", "tools": ["opencode", "hermes"],
 "expires": "<at + 90 days>", "reason": "<text>", "recorded_by": "pinesky", "at": "<time>"}
```

It binds this Mac's `IOPlatformUUID` (from `/usr/sbin/ioreg -rd1 -c IOPlatformExpertDevice`), the tools it clears, an expiry 90 days after `at` and a reason (`--reason`, else one line of stdin). The tools come only from `machine_waivable` in `policy/gate.rules.json`, which lists `opencode` and `hermes` (gates.md G33). It binds no unit and no `ruleset_sha256`, so a rule change does not stale it. A machine waiver signed before N1 (`method` `ssh-sig`, `principal`, `signature`) still verifies against the signers file.

A tool of `coverage_required` that did not run counts as waived when its own `coverage_waiver:<tool>` record is approved, or else when the machine waiver is valid for this Mac and lists it. The machine waiver reads, in order: `missing` (no file, which is also the state after `revoke`), `unsigned` (neither `signature` nor `method`), `invalid` (a field is malformed, a tool is outside `machine_waivable`, the expiry is not within 90 days after `at`, `at` is more than 5 minutes ahead, or a signed waiver without the signers file), `other-machine` (another id, or the id cannot be read), `invalid` (`ssh-keygen -Y verify` fails, signed waivers only), `expired`, `approved`. Only `approved` clears anything. Whether `IOPlatformUUID` is stable and readable without extra rights is UNVERIFIED.

`report` and `agent report` write `waived by machine policy <sha12>` in the `comparison.md` coverage table and a gate note `coverage waived: <tool> (dynamic eval <status>) by machine policy <sha256> (<who>, expires <time>): <reason>`. `plan` prints one `machine waiver:` line. `machine-waiver show` prints the same fields. A registration that relied on it stores the whole record and its sha256 under `machine_waiver`, which the register record's hash covers. `revoke` deletes the file and logs the revoke; it asks nothing. `registry.classify` does not re-check a stored machine waiver.

## The combined gate

`requirements_eval_set` is one approval of both the requirements and the eval set. The live `approvals` gate opens on one current `requirements_eval_set` record or on current `requirements` and `eval_set` records; one separate record alone does not open it. `plan` prints a `combined approval gate:` line next to the `approval gates:` line.

## Agent live gates

`agent run --live` and `agent loop --live` refuse to start without two things in the agent workspace (`--workspace`, else `<parent>/<name>-workspace`):

- A current `requirements_eval_set` record, or current `requirements` and `eval_set` records. For an agent, `requirements` hashes `<workspace>/requirements.md` and `eval_set` hashes every `scenarios/*.json` of the agent folder.
- A current `rule_review` decision whenever `eva.review_items` lists findings for the agent folder. For a generated agent this is `parity-permissions-opencode`. The decision binds the finding texts and the bytes of AGENT.md, every scenario and the OpenCode wrapper (`eva.review_sources`).

| Gate | Refusal |
|---|---|
| rule review | `rule review not recorded[ (<status>)]: N finding(s) need a human decision (<findings>); read them with `python3 eva.py check <agent>`, then run `approve <agent> --workspace <ws> --gate rule_review` ...` |
| approvals | `live approvals missing: requirements_eval_set is <status>, requirements is <status>, eval_set is <status>; write <ws>/requirements.md, then run `approve <agent> --workspace <ws> --gate requirements_eval_set` ...` |

Both checks run in the problems list of the start, before the live record and again after it, each time with fresh verdicts. A `requirements.md`, scenario, AGENT.md or wrapper edit made in between refuses with `agent <cmd> --live refused after signing: ...`. The plan hash also binds the `requirements` and `rule_review` gate hashes, so an edit after that second check still refuses at the last recheck.

The loop reads the decision before the live record, after it and before iteration 1. From iteration 2 on it checks the findings instead of the bytes, because the builder may change AGENT.md. `rule_review` clears only needs-review rows. A FAIL row such as `parity-permissions-hermes` (always FAIL, gates.md G4) is not a review item, so no decision clears it, and `agent report` adds every parity FAIL row to the gate problems. A `registration` approval therefore names it.

`agent grade --live` is not gated by these approvals. It records a `live` approval but reads no approval.

## Statuses

The rules run in this order, and the first that matches decides a record's status:

| Order | Status | When |
|---|---|---|
| 1 | `missing` | there is no record |
| 2 | `unsigned` | the record has neither `signature` nor `method`; no process starts for it |
| 3 | `invalid` | a field is missing, extra or malformed; a recorded record names another kind, gate, unit or budget than the one read; a signed record and the approvers file is missing |
| 4 | `stale` | the gated files are gone or no longer hash to the record's `sha256` |
| 5 | `stale` | the record names no `ruleset_sha256` or another one than the current bundle hash (`rules changed since approval`) |
| 6 | `invalid` | `ssh-keygen -Y verify` fails (signed records only) |
| 7 | `approved` | `recorded_by` of a recorded record, or the principal of a signed one |

When `approvals.json` is valid JSON but not an object, every gate reads `invalid` with the reason `<workspace>/approvals.json is not a JSON object (it holds ...)`. `plan` shows `requirements=approved by pinesky`, `NOT APPROVED`, `NOT APPROVED (unsigned)`, `NOT APPROVED (stale)` or `NOT APPROVED (invalid: reason)`. A `plan` starts one `ssh-keygen -Y verify` per current signed record it reads and none for recorded or unsigned records.

## Outside the gate

- `--live --dry-run` lists every live gate the command would face and executes nothing, so it records nothing.
- `pending` and `machine-waiver show` record nothing.
- `check-builtin-plugins` runs `claude --settings ... plugin list --json`, which makes no model call.
- `plan`, `estimate`, `validate`, `report`, `snapshot` and a dry `grade` execute no CLI.

## Tests

`tests/test_human_gate.py` runs `approve`, `register` and the removed commands in a child process with a piped stdin, an empty temp `HOME` and a `sitecustomize` audit hook that blocks and records any touch of the real `~/.config/skill-master` and any start of `ssh-add`, `ssh-keygen` or `ssh-agent`. It runs every approval command in process through `cli_approver()` over an empty account home, a non-TTY stdin and a process runner that refuses everything, and checks the hash binding of recorded records. Records signed before N1 are made and verified with throwaway keys under `/tmp`. The other tests record through `tests/test_skill_eval.py`'s test approver: the name `test-approver`, an empty home and a runner that fails the test if any process starts.
