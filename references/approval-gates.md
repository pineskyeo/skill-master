# Approval gates

`approve` (every gate, `rule_review` included), `register`, and the start of every `--live` run of `run`, `grade`, `calibrate` and `loop` go through one human gate in `engine/approvals.py`. `python3 eva.py eval ...` reaches the same code, because it calls `skill_eval.main`. The gate needs a person at a real terminal and an ssh signature from a key listed in `~/.config/skill-master/approvers.allowed_signers`. `approval_ok` counts a record as approved only when its signature verifies against that file.

## What the gate proves

A record that reads `approved` was signed by the private key of a principal in the approvers file. The signature covers the kind (`approve`, `register` or `live`), the gate, the unit, the hash of the gated files and the signing time. A `--live` signature also covers the budget cap. Before signing, the command checked that stdin, stdout and stderr were terminals, that no agent environment variable was set, and that no agent process was among its ancestors. The person then typed back a six-character challenge and the key passphrase.

## What the gate does not prove

The passphrase is the secret. Everything else on this list is a check that a same-user program can defeat (Appendix C, R1 of the plan):

- A program running as the same user can edit `engine/approvals.py` or `skill_eval.py`, replace the approvers file, or call the engine's Python functions with its own approver. The gate binds the command line, not the process.
- If the approver key is loaded in ssh-agent, anything that can reach the agent socket can sign without the passphrase. `approver-check` refuses while `ssh-add -L` lists the key.
- Anything that knows the passphrase can sign without a terminal. ssh-keygen reads the passphrase from stdin when no controlling terminal exists (premise test, `prototypes-ssh-sig/result.txt` section 4).
- The ancestry walk sees only the parent chain. A process that an agent detaches (double fork, `setsid`, adopted by launchd) has no agent ancestor. Neither does a command typed into pinesky's terminal through `tmux send-keys`, a `.command` file opened in Terminal, or TIOCSTI from a process on the same terminal. The environment markers catch a detached process only if it kept the agent's environment; `env -i` removes them.
- An agent that runs as `node .../cli.js` shows the process name `node`, which the name table does not match. This is UNVERIFIED for this install, where Claude Code runs as `claude.exe`.
- An agent hosted by an interpreter, such as a `python3 agent.py` process, shows the interpreter's name and path, `python3` or `Python`. The name table does not match those either. Only the environment markers can catch such an agent.
- A `--live` signature binds the inputs when the run starts. The plan hash is computed again just before live mode turns on, so a change made while the challenge was on screen refuses the run. A file that changes during the run is not caught.

`guard.py` fingerprints the approvers file, and Codex and pinesky review every change to `engine/approvals.py`.

## Fixed paths

| Path | Use |
|---|---|
| `~/.config/skill-master/approvers.allowed_signers` | the signers file every gate verifies against |
| `~/.config/skill-master/approver_ed25519` | the private key the command line signs with |

`~` is the home folder of the account in the password database (`pwd.getpwuid(os.getuid())`), not `$HOME`. No flag, environment variable or config file changes either path. The program never creates or writes `~/.config/skill-master`. A missing signers file fails every gate. Only the Python API, which tests call, takes another signers file or key.

## The signers line pinesky writes

One line per approver, in the `allowed_signers` format of `ssh-keygen(1)`:

```
pinesky namespaces="skill-master-approval" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...
```

The first field is the principal, which `plan` shows as `approved by pinesky`. The `namespaces` option limits the key to this program's namespace, `skill-master-approval`. The last two fields are the key type and the base64 key from the `.pub` file. A line that does not parse makes `approver-check` fail. The key needs a passphrase, and it is never added to ssh-agent or stored with `--apple-use-keychain`. `ed25519-sk` (FIDO) keys are not supported: `approver-check` probes the key with `ssh-keygen -y -P ""`, which probably opens an `-sk` key handle without a passphrase (UNVERIFIED), so the check would refuse it.

## Gate order

For `approve`, `register` and every real `--live` start:

1. Presence. stdin, stdout and stderr are TTYs, no agent marker is set, and no ancestor is an agent. All failing checks are reported together, and nothing else runs.
2. `approver-check`. The signers file exists, parses and lists a key. The key file exists and does not open with an empty passphrase, which this step tests by starting `/usr/bin/ssh-keygen -y -P ""`. `SSH_AUTH_SOCK` is set, and `/usr/bin/ssh-add -L`, which this step starts, either answers "The agent has no identities." or lists no signers key.
3. The command's own checks. `approve` computes the gate hash and refuses when `approvals.json` is not valid JSON or is JSON but not an object. `register` builds the registration record. A `--live` start runs every live gate that starts no process (the `--live --dry-run` set). A failure here refuses before anything is signed.
4. The gate, the unit, the hash, what the hash covers, and for `--live` the full command, the workspace and the budget are printed to stderr. Every character that is not printable (ESC, carriage return, other control characters, Unicode format marks) is printed as its Python escape, such as `\x1b` or `\r`. A fixture name or a path therefore cannot clear or redraw the terminal before the challenge.
5. The person types the six-character challenge.
6. `/usr/bin/ssh-keygen -Y sign -n skill-master-approval` signs. The command line pipes the payload on stdin and reads the signature from stdout, so no payload file exists while the passphrase is typed. ssh-keygen should then ask for the passphrase on the controlling terminal. That is UNVERIFIED with a passphrase key, and it is pinesky's operator step. `-Y find-principals` names the principal, and `-Y verify` checks the new signature at once.
7. The caller writes the record. A `--live` start then runs the live gates that start a process (`claude auth status`), recomputes the plan hash, and turns live mode on with the verified grant.

Nothing is written unless steps 1 to 6 pass. The gate itself starts only `/usr/bin/ssh-keygen` and `/usr/bin/ssh-add`, in steps 2 and 6. No `claude` or `codex` CLI starts before step 7.

PLAN line 443 says "every live gate and register run approver-check first". For `approve` and `register`, this unit reads that as step 2: before any other work of the command, after the presence checks. A `--live` start does not hold to that reading. Before the gate, `_dispatch` parses and checks its arguments and does static work: `load_eval_set`, the `--cases`, `--runs`, `--max-iterations` and `--builder` checks, `read_builder_prompt`, `make_engines`, the `--price` and `--tokens` parsing, `make_grader`, `estimate`, the `--dry-run` check, the `--budget-usd` check and the `--iteration` check. None of them starts a process. In `skill_eval.py`, only `live_exec`, `run_auth_status` and `builtin_plugin_check` start one, and `eva.py` starts none.

## Refusals

Every refusal exits with status 2 and prints one line per failing check to stderr. `<what>` is `approve`, `register`, or `<subcommand> --live`.

| Step | Line |
|---|---|
| presence | `<what> needs a human at an interactive terminal (stdin is not a TTY)`, likewise `stdout` and `stderr` |
| presence | `<what> needs a human at an interactive terminal (agent environment variables are set: NAME, NAME)` |
| presence | `<what> needs a human at an interactive terminal (agent process in the parent chain: claude.exe (pid N))` |
| presence | `<what> needs a human at an interactive terminal (ancestry check failed closed: WHY)` |
| approver-check | `<what>: approver-check: approvers file missing: ~/.config/skill-master/approvers.allowed_signers` |
| approver-check | `<what>: approver-check: the approver key is loaded in ssh-agent (...); remove it with ssh-add -d ~/.config/skill-master/approver_ed25519` |
| approver-check | `<what>: approver-check: approver key opens without a passphrase: ...` |
| approver-check | `<what>: approver-check: SSH_AUTH_SOCK is not set, so ssh-agent cannot be asked whether the approver key is loaded; run this from a Terminal where ssh-add -L works` |
| approver-check | `<what>: approver-check: ssh-add -L could not ask ssh-agent (exit N: ...); cannot confirm the approver key is not loaded` |
| command check | `error: <workspace>/approvals.json is not a JSON object (it holds ...); nothing was signed` (`approve`) |
| challenge | `<what>: challenge mismatch: nothing was signed` |
| signing | `<what>: signing failed: ...` |
| signing | `<what>: the new signature does not verify: ...` |

`python3 skill_eval.py approver-check` prints the same approver-check reasons, each prefixed with `approver-check: `, and exits 1. When every check passes it prints `approver-check: OK` and exits 0. It signs nothing and runs no presence check.

An unset `SSH_AUTH_SOCK` fails closed on purpose. An unset variable in one shell does not show that no agent holds the key. Other processes of the same user can still reach the login agent (inferred; the premise test saw `SSH_AUTH_SOCK` set inside agent sessions).

## Agent markers

A marker is a variable that an agent host sets for its children. It counts when it is set, whatever its value. The refusal names every marker found.

| Rule | Names |
|---|---|
| exact | `CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_EFFORT`, `CLAUDE_AS_ACTIVE`, `CLAUDE_AS_CALLER`, `CLAUDE_AS_AUTO_SWITCH`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PROJECT_DIR`, `CLAUDE_TMPDIR`, `AI_AGENT`, `AGENT_TRANSCRIPTS`, `SAND_AGENT`, `SANDBOX_RUNTIME`, `OPENCODE` |
| prefix | `CLAUDE_CODE_`, `CODEX_`, `CURSOR_`, `__CURSOR_`, `OPENCODE_`, `HERMES_` |
| never a marker | `CLAUDE_CONFIG_DIR`, `CLAUDE_AS_HOME`, `CLAUDE_ACCOUNTS`, `CLAUDE_AS_REAL_CLAUDE`, `CODEX_HOME`, `SKILL_EVAL_ACCOUNT_SWITCH` |

The last row lists user configuration that `skill_eval.py` reads as input for a `--live` run. Unsetting one of them would change what the run does, so they never refuse. If pinesky's own Terminal sets a marker, the refusal names it, and he can run the command with `env -u NAME`. Whether his Terminal sets any is UNVERIFIED. The marker set differs between Claude Code sessions (premise test, section 8). The markers of Codex CLI, OpenCode and Hermes processes are UNVERIFIED, so those prefixes only tighten the check.

## Agent processes

The walk starts at the command's own process and reads every parent up to launchd (pid 1). A process is an agent when the lower-cased name of its executable (`comm`) or the base name of its path starts with `claude`, `codex`, `opencode`, `hermes` or `cursor`. A prefix rule fails closed: it matches `claude.exe`, the name this Claude Code install runs under, and names like `codex-aarch64-apple-darwin`.

The plan asked for a walk over `ps -o ppid=,comm=`. `/bin/ps` is setuid root and cannot run inside the session sandbox (`PermissionError`, premise test section 5). The walk therefore reads libproc through `ctypes` from the absolute path `/usr/lib/libproc.dylib`, so `PATH` and the environment cannot steer it. It uses `proc_pidinfo(PROC_PIDT_SHORTBSDINFO)` and `proc_pidpath`. Both read processes of any user. In the H1 owner's probe of 2026-10-20, `PROC_PIDTBSDINFO` refused pid 1 (`launchd`, owned by root) with errno 1, while the short form read it. Only pid 1 was probed. A Terminal chain usually includes a root `login` process, and that `PROC_PIDTBSDINFO` would refuse it too is inferred from the pid 1 result. That the short form reads it is UNVERIFIED until pinesky runs the gate in a real Terminal.

Any process that cannot be read, a parent loop, a chain longer than 64, or a platform other than macOS refuses with `ancestry check failed closed: ...`. So does a libproc answer for another pid than the one asked, a short `proc_pidinfo` answer, or a failed `proc_pidpath`.

## Records

`approvals.json` maps each gate to its record. `registration.json` keeps its fields and gains `"signed"`, the `register` record. Its `"approval"` field holds the verified registration-gate record, or `"auto"` when the gate was clean.

```json
{"format": 1, "method": "ssh-sig", "sha256": "<64 hex>", "principal": "pinesky",
 "at": "2026-10-20T09:41:00Z", "signature": "-----BEGIN SSH SIGNATURE-----\n...\n-----END SSH SIGNATURE-----\n"}
```

The signed bytes are ASCII lines with one `key value` pair each and a final newline:

```
skill-master-approval v1
kind approve
gate eval_set
unit branch-setup
sha256 <64 hex>
at 2026-10-20T09:41:00Z
```

A `live` payload has a `budget_usd <amount>` line after `sha256`. The reader rebuilds these bytes from the record's `sha256` and `at` plus what it already knows. The kind comes from where the record is stored, the gate from the `approvals.json` key, and the unit from the eval set being checked. The record never supplies them. A record copied to another gate or unit does not verify. Golden's `iteration-4` and `registration` records share one sha256, and they still cannot stand in for each other.

| Hash | Covers |
|---|---|
| `approve` | `gate_hash(gate)`, the files of that gate |
| `register` | the sha256 of the canonical JSON of `skill_name`, `iteration`, `benchmark_sha256`, `skill_sha256`, `eval_set_sha256` and `skill_md_sha256` from the registration record |
| `live` | the sha256 of the canonical JSON of the exact argv, the workspace, the skill folder hash, the `eval_set` and `requirements` gate hashes, the `--builder-prompt` text, the `--golden` file and the `--old-skill` folder hash |

A live signature is verified, and its principal and plan hash are printed to stderr. It is not stored.

## Statuses

The rules run in this order, and the first that matches decides a record's status:

| Order | Status | When |
|---|---|---|
| 1 | `missing` | there is no record |
| 2 | `unsigned` | the record has neither `signature` nor `method`. Every record written before this unit reads this way, golden's included, and no process starts for it. |
| 3 | `invalid` | a field is missing or malformed, or the approvers file is missing (`approvers file missing: ~/.config/skill-master/approvers.allowed_signers`) |
| 4 | `stale` | the gated files are gone or no longer hash to the record's `sha256` |
| 5 | `invalid` | `ssh-keygen -Y verify` fails |
| 6 | `approved` | the signature verifies for the record's principal |

Stale comes before the signature check, so a record with a bad signature over files that changed reads `stale`, not `invalid`. Neither is approved. The signature covers the `sha256` line and the kind, so a record whose `sha256` was edited to match changed files reaches rule 5 and reads `invalid`. When `approvals.json` is valid JSON but not an object, every gate reads `invalid` with the reason `<workspace>/approvals.json is not a JSON object (it holds ...)`.

`plan` shows `requirements=approved by pinesky`, `NOT APPROVED`, `NOT APPROVED (unsigned)`, `NOT APPROVED (stale)` or `NOT APPROVED (invalid: reason)`. A `plan` starts one `ssh-keygen -Y verify` per current signed record it reads, which means `requirements`, `eval_set`, and `rule_review` when the skill has rule-review findings. Golden's four unsigned records cost no call.

## Outside the gate

- `--live --dry-run` lists every live gate the command would face and executes nothing, so it needs no signature.
- `check-builtin-plugins` runs `claude --settings ... plugin list --json`, which makes no model call. It is a separate subcommand, not a live run.
- `plan`, `estimate`, `validate`, `report`, `snapshot` and a dry `grade` execute no CLI.

## Tests

`tests/test_human_gate.py` holds the unit cases, which use throwaway keys under `/tmp` and patch only the TTY probe. It also holds the command cases, which run the real CLI in a subprocess and check that it refuses without a signature. No test reaches the real approvers file, the real key or the real `ssh-add`:

- The approver that `cli_approver()` builds refuses approver-check and verification while `unittest` or `pytest` is loaded.
- Subprocess children get a `sitecustomize` audit hook that blocks and records any touch of the real `~/.config/skill-master` and any start of `ssh-add` or `ssh-keygen`.
- The pty and in-process cases run only where the real ancestry probe sees an agent. In a human Terminal they skip. The pty subprocess cases also skip with `no pty device` where `pty.openpty()` fails, which it does inside the session sandbox.

The older tests sign through `tests/test_skill_eval.py`'s test approver: a throwaway passphrase key, the principal `test-approver`, a fake `ssh-add`, and a runner that starts only `/usr/bin/ssh-keygen` on files in the test-key folder.
