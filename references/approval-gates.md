# Approval gates

`approve` (every gate, `rule_review`, `requirements_eval_set` and the four `coverage_waiver:<tool>` gates included), `approve --batch`, `approve-ui`, `approver-session`, `machine-waiver sign` and `machine-waiver revoke`, `register`, and the start of every `--live` run of `run`, `grade`, `calibrate` and `loop` go through one human gate in `engine/approvals.py`. `python3 eva.py eval ...` reaches the same code, because it calls `skill_eval.main`. The gate needs a person at a real terminal and an ssh signature from a key listed in `~/.config/skill-master/approvers.allowed_signers`. `approval_ok` counts a record as approved only when its signature verifies against that file.

## What the gate proves

A record that reads `approved` was signed by the private key of a principal in the approvers file. The signature covers the kind (`approve`, `register` or `live`), the gate, the unit, the hash of the gated files and the signing time. A `--live` signature also covers the budget cap, and a coverage waiver signature covers the reason the person typed. Before signing, the command checked that stdin, stdout and stderr were terminals, that no agent environment variable was set, and that no agent process was among its ancestors. The person then typed back a six-character challenge and the key passphrase.

## What the gate does not prove

The passphrase is the secret. Everything else on this list is a check that a same-user program can defeat (Appendix C, R1 of the plan):

- A program running as the same user can edit `engine/approvals.py` or `skill_eval.py`, replace the approvers file, or call the engine's Python functions with its own approver. The gate binds the command line, not the process.
- If the approver key is loaded in ssh-agent, anything that can reach the agent socket can sign without the passphrase. `approver-check` refuses the key in every agent except a live `approver-session` agent (see "The approver session"), and it asks the launchd agent too. Inside that session any process of the subshell inherits the socket, including `ssh -A` or a `ForwardAgent` host and any agent pinesky starts there (Appendix C, R20). Do not run ssh, git or an agent in the `[approver]` shell. The engine's own children never get the session: every model child of a run, and so the code under test that it runs, starts without `SSH_AUTH_SOCK`, `SSH_AGENT_PID`, `SKILL_MASTER_APPROVER_SESSION`, `PS1` and `PROMPT` (`skill_eval.APPROVER_SESSION_ENV`).
- A same-user process can connect to the approval page on `127.0.0.1`. Without the one-time token or the cookie it gets 403, and it cannot see the challenge, which only the launching terminal shows (Appendix C, R14b).
- An SSH session that an agent opens to this Mac (for example `ssh -t localhost`) has `sshd` and `launchd` above it, not the agent, so the ancestry walk cannot see the agent. This is the same class as a detached process. The session also needs SSH credentials for this Mac.
- The machine waiver is read from `$HOME`. A process that sets `HOME` can only point the reader at another file, which still has to verify against the fixed signers file and this Mac's id. Revoke deletes the file, so a copy made before the revoke verifies again until its expiry if someone restores it.
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

`~` is the home folder of the account in the password database (`pwd.getpwuid(os.getuid())`), not `$HOME`. No flag, environment variable or config file changes either path. A missing signers file fails every gate. Only the Python API, which tests call, takes another signers file or key.

Three more files live under `$HOME/.config/skill-master/`. They resolve through `$HOME`, so a test or a lane with a temp `HOME` never touches the real folder:

| Path | Use |
|---|---|
| `$HOME/.config/skill-master/machine-waiver.json` | the machine waiver; `machine-waiver sign` writes it (mode 0600), `revoke` deletes it |
| `$HOME/.config/skill-master/approvals.log` | one JSON line per page approval or rejection and per machine waiver sign or revoke (mode 0600) |
| `$HOME/.config/skill-master/approve-ui.json` | `tailnet_owner_login` and the optional `magicdns_name` that `approve-ui --tailscale` reads; pinesky writes it |

A `HOME` that is not an absolute path makes the machine waiver read `invalid`. Inside a test process the reader never opens a path under the password-database home.

## The signers line pinesky writes

One line per approver, in the `allowed_signers` format of `ssh-keygen(1)`:

```
pinesky namespaces="skill-master-approval" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...
```

The first field is the principal, which `plan` shows as `approved by pinesky`. The `namespaces` option limits the key to this program's namespace, `skill-master-approval`. The last two fields are the key type and the base64 key from the `.pub` file. A line that does not parse makes `approver-check` fail. The key needs a passphrase, and it is never added to ssh-agent or stored with `--apple-use-keychain`. `ed25519-sk` (FIDO) keys are not supported: `approver-check` probes the key with `ssh-keygen -y -P ""`, which probably opens an `-sk` key handle without a passphrase (UNVERIFIED), so the check would refuse it.

## Gate order

For `approve`, `register` and every real `--live` start:

1. Presence. stdin, stdout and stderr are TTYs, no agent marker is set, and no ancestor is an agent. All failing checks are reported together, and nothing else runs.
2. `approver-check`. The signers file exists, parses and lists a key. The key file exists and does not open with an empty passphrase, which this step tests by starting `/usr/bin/ssh-keygen -y -P ""`. `/usr/bin/ssh-add -L`, which this step starts with `SSH_AUTH_SOCK` set to one socket at a time, answers "The agent has no identities." or lists no signers key for every agent it asks: the agent `SSH_AUTH_SOCK` names, unless it is a live approver-session socket, and the launchd agent whenever `SSH_AUTH_SOCK` does not name it. Only a live approver-session agent may list the key. `SSH_AUTH_SOCK` must be set, except in an SSH login (see "SSH sessions").
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
| approver-check | `<what>: approver-check: the approver key is loaded in ssh-agent (...); remove it with ssh-add -d ~/.config/skill-master/approver_ed25519`, with ` at <socket>` after `ssh-agent` for an agent other than the one `SSH_AUTH_SOCK` names |
| approver-check | `<what>: approver-check: cannot resolve the launchd ssh-agent socket (N sockets of this user match /private/tmp/com.apple.launchd.*/Listeners); cannot confirm the approver key is not loaded there` |
| approver-check | `<what>: approver-check: ssh-add -L could not ask the approver-session agent at <socket> (exit N: ...)` |
| approver-check | `<what>: approver-check: approver key opens without a passphrase: ...` |
| approver-check | `<what>: approver-check: SSH_AUTH_SOCK is not set, so ssh-agent cannot be asked whether the approver key is loaded; run this from a Terminal where ssh-add -L works` |
| approver-check | `<what>: approver-check: ssh-add -L could not ask ssh-agent (exit N: ...); cannot confirm the approver key is not loaded` |
| command check | `error: <workspace>/approvals.json is not a JSON object (it holds ...); nothing was signed` (`approve`) |
| reason | `approve: reason refused: it is empty; nothing was signed`, likewise `it is longer than 200 characters` and `it holds a character that is not printable ASCII` (a `coverage_waiver:<tool>` gate) |
| challenge | `<what>: challenge mismatch: nothing was signed` |
| signing | `<what>: signing failed: ...` |
| signing | `<what>: the new signature does not verify: ...` |
| batch | `approve --batch: challenge mismatch: nothing was signed`; `approve --batch: reason refused for <gate> of <unit>: ...; nothing was signed` |
| batch, page | `not signed <unit> <gate>: approve --batch: <gate> for <unit> was not confirmed in this sitting or changed since the list was shown; nothing was signed` (one line per item; the other items are still signed) |
| batch form | `error: approve: give SKILL and --gate, or --batch with --unit` |
| session | `approver-session: already inside an approver session (<folder>)` |
| session | `approver-session: the private ssh-agent did not start on <socket>`; `approver-session: ssh-add could not load the approver key (exit N: ...)`; `approver-session: the session agent does not hold a key of the approvers file`; `approver-session: signing failed: ...` (the probe signature) |
| page | `approve-ui needs a human at an interactive terminal (...)` and the other presence and approver-check lines, before any socket opens |
| page | `error: --tailscale needs tailnet_owner_login in ~/.config/skill-master/approve-ui.json; nothing was started`; `error: --tailscale: tailscale ip -4 failed (...); is Tailscale up? nothing was started`; `error: --tailscale: <address> is not a Tailscale address (100.64.0.0/10); nothing was started` |
| page request | HTTP 403 for a wrong tailnet peer, a wrong `Host`, no cookie and no valid unused token, a POST without the page's `Origin`, a challenge mismatch or a lost launching terminal; 413 for a body over 64 KiB; 400 for a form that does not parse; 410 after the page closed |
| machine waiver | `machine-waiver sign: tools must be distinct names from machine_waivable (opencode,hermes), got ...; nothing was signed`; `machine-waiver sign: cannot read this Mac's IOPlatformUUID; nothing was signed`; `machine-waiver sign: reason refused: ...; nothing was signed`; `machine-waiver sign: challenge mismatch: nothing was signed`; `machine-waiver revoke: challenge mismatch: nothing was signed`; `error: no machine waiver at ~/.config/skill-master/machine-waiver.json; nothing to revoke` |

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
{"format": 1, "method": "ssh-sig", "sha256": "<64 hex>", "ruleset_sha256": "<64 hex>", "principal": "pinesky",
 "at": "2026-10-20T09:41:00Z", "signature": "-----BEGIN SSH SIGNATURE-----\n...\n-----END SSH SIGNATURE-----\n"}
```

The signed bytes are ASCII lines with one `key value` pair each and a final newline:

```
skill-master-approval v1
kind approve
gate eval_set
unit branch-setup
sha256 <64 hex>
ruleset_sha256 <64 hex>
at 2026-10-20T09:41:00Z
```

A `live` payload has a `budget_usd <amount>` line after `ruleset_sha256`. A coverage waiver payload has a `reason <text>` line before `at`, rebuilt from the record's `reason`. The reader rebuilds these bytes from the record's `sha256` and `at` plus what it already knows. The kind comes from where the record is stored, the gate from the `approvals.json` key, and the unit from the eval set being checked. The record never supplies them. A record copied to another gate or unit does not verify. Golden's `iteration-4` and `registration` records share one sha256, and they still cannot stand in for each other.

| Hash | Covers |
|---|---|
| `approve` | `gate_hash(gate)`, the files of that gate |
| `approve`, `coverage_waiver:<tool>` | the sha256 of labeled lines for the tool, the skill tree hash and the `eval_set` gate hash |
| `approve`, `requirements_eval_set` | the sha256 of `requirements_eval_set v1`, `requirements <requirements gate hash>` and `eval_set <eval_set gate hash>`, so an edit to either file stales it |
| `register` | the sha256 of the canonical JSON of `skill_name`, `iteration`, `benchmark_sha256`, `skill_sha256`, `eval_set_sha256` and `skill_md_sha256` from the registration record |
| `live` | the sha256 of the canonical JSON of the exact argv, the workspace, the skill folder hash, the `eval_set` and `requirements` gate hashes, the `--builder-prompt` text, the `--golden` file and the `--old-skill` folder hash |

A live signature is verified, and its principal and plan hash are printed to stderr. It is not stored.

## Coverage waivers

Registration needs a graded dynamic run on every tool that `coverage_required` in `policy/gate.rules.json` lists, or a signed waiver for each listed tool that did not run (plan.md:1135, Appendix E Q5). The file lists claude, codex, opencode and hermes. OpenCode and Hermes have no engine, so a skill registers today only with signed `coverage_waiver:opencode` and `coverage_waiver:hermes` records or a machine waiver that lists them, plus `coverage_waiver:codex` when Codex was not selected. `coverage_required` is part of the rule bundle, so removing a tool from it moves `ruleset_sha256` and every existing approval reads stale. The loader refuses anything but a non-empty list of unique names from the four tools.

`approve --gate coverage_waiver:<tool>` signs one waiver. `engine/approvals.py` holds the four gate names. Every other gate name with a colon fails the payload check, and every other payload keeps its bytes.

**What a waiver binds.** The record's `sha256` is `gate_hash("coverage_waiver:<tool>")`, a sha256 over labeled lines for the tool, the skill tree hash and the eval set hash (`gate_hash("eval_set")`). The payload adds the unit name and `ruleset_sha256`, as for every gate. A waiver stops counting when the skill folder, the eval set or the rule bundle changes. Copied to another tool's key it reads stale, and checked for another unit it reads invalid.

**The typed reason.** After the presence checks, approver-check and the gate's own checks, the gate prints the tool, its dynamic eval status in the last iteration's benchmark, the skill and eval set hashes, and that the waiver clears only this tool for this unit, eval set and ruleset. It then reads the reason from the terminal. The reason must hold 1 to 200 printable ASCII characters once the surrounding spaces are stripped. Anything else refuses before the challenge, and nothing is signed. No flag, environment variable or file supplies the reason. The record stores it as `reason`, and the payload covers it with a `reason <text>` line before the `at` line. A waiver record without a valid reason reads `invalid`, and so does any other record that carries a `reason`.

**Where it counts.** `report` checks every waiver when it runs and writes the verdicts into `benchmark.json` as `coverage_waivers`. `compute_gate` reads only the benchmark, so a waiver signed after a report counts at the next `report`. Each listed tool that neither ran nor has an approved waiver is a gate problem `coverage-missing:<tool>: dynamic eval <status>, waiver <state>`, where the state is `missing`, `unsigned`, `invalid (<why>)` or `stale (<why>)`. A benchmark without a valid `tool_coverage` entry for a tool counts that tool as not run. A waived tool is a note that names the principal and the reason. The `comparison.md` coverage table shows `run`, `waived by <principal>` or `coverage-missing` per tool in its registration column. `plan` prints a `registration coverage:` line with the rule and the waiver state of each listed tool.

`register` recomputes coverage before its other checks, from the benchmark's `tool_coverage`, the current `coverage_required` and the waiver verdicts at that moment, never from `gate.json`. It refuses with one error that names each missing tool as `coverage-missing:<tool>`. A signed `registration` approval does not clear a coverage problem. `loop` stops with `needs-coverage`, exits like `needs-approval` and calls no builder when coverage problems are the only problems left, because no builder can make a missing engine run.

**What a waiver proves.** A principal in the approvers file signed, at a terminal that passed the presence checks, that this tool may stay without a dynamic run for this unit, eval set and rule bundle, and typed the stored reason.

**What a waiver does not prove.** It says nothing about how the skill behaves on that tool. No code checks the reason for truth. The limits in "What the gate does not prove" apply unchanged, so a same-user program can edit the code or call the Python API with its own approver.

## The machine waiver

`machine-waiver sign` writes one signed record to `$HOME/.config/skill-master/machine-waiver.json` (schemas/machine-waiver.schema.json). It binds this Mac's `IOPlatformUUID` (from `/usr/sbin/ioreg -rd1 -c IOPlatformExpertDevice`), the tools it clears, an expiry 90 days after signing and a typed reason. The tools come only from `machine_waivable` in `policy/gate.rules.json`, which lists `opencode` and `hermes` (gates.md G33). Claude and Codex are never machine-waivable. It binds no unit and no `ruleset_sha256`, so a rule change does not stale it. The signed bytes are:

```
skill-master-approval v1
kind machine_waiver
machine_id <IOPlatformUUID>
tools opencode,hermes
expires <at + 90 days>
reason <text>
at <time>
```

A tool of `coverage_required` that did not run counts as waived when its own `coverage_waiver:<tool>` record is approved, or else when the machine waiver verifies for this Mac and lists it. The machine waiver reads, in order: `missing` (no file, which is also the state after `revoke`), `unsigned`, `invalid` (a field is malformed, a tool is outside `machine_waivable`, the expiry is not within 90 days after signing, the signing time is more than 5 minutes ahead, the signers file is missing), `other-machine` (another id, or the id cannot be read), `invalid` (`ssh-keygen -Y verify` fails), `expired`, `approved`. Only `approved` clears anything. Whether `IOPlatformUUID` is stable and readable without extra rights is UNVERIFIED.

Where it shows: `report` and `agent report` write `waived by machine policy <sha12>` in the `comparison.md` coverage table and a gate note `coverage waived: <tool> (dynamic eval <status>) by machine policy <sha256> (<principal>, expires <time>): <reason>`. `plan` prints one `machine waiver:` line with its state, principal, sha256, tools, expiry and reason. `machine-waiver show` prints the same fields and never the signature. A registration that relied on it stores the whole signed record and its sha256 under `machine_waiver`, which the register signature covers, and the hub unit page lists it with the approvals. `revoke` asks a challenge, deletes the file and logs the revoke. `registry.classify` does not re-check a stored machine waiver, so a later revoke or expiry does not unregister a unit.

## The combined gate

`requirements_eval_set` is one approval of both the requirements and the eval set. The live `approvals` gate opens on one current `requirements_eval_set` record or on current `requirements` and `eval_set` records; one separate record alone does not open it. The separate gates stay, so older records still read. `plan` prints a `combined approval gate:` line next to the `approval gates:` line.

## Sittings: approve --batch and the page

A sitting is one presence check and one approver-check for every signature of a command. `approve --batch` opens it, lists every pending item, asks the reason of each waiver item, one six-character challenge and then the passphrase once, and signs each item as its own record through `_approve_with` and `store_approval`, the same code and format as a single `approve`. The challenge confirms the exact payload fields of each listed item (kind, gate, unit, gate hash, `ruleset_sha256`). If a gated file changes after the list was shown, that item is refused and the others are still signed. A challenge mismatch signs nothing. `approve --batch` passes the passphrase on stdin to `ssh-keygen` with no controlling terminal, the non-interactive form of the premise test.

## The approver session

`approver-session` runs presence and approver-check, makes a new folder `/tmp/skill-master-approver-XXXXXXXX` (mode 0700), starts `/usr/bin/ssh-agent -D -t 28800 -a <folder>/agent.sock`, loads the key with `/usr/bin/ssh-add -t 28800` at the terminal, and signs and verifies one probe payload through that agent. The probe bytes start with `skill-master-probe v1` and hold a random nonce, so they never read as an approval. The start refuses unless every step passed. It then starts a subshell with `SSH_AUTH_SOCK=<folder>/agent.sock`, `SKILL_MASTER_APPROVER_SESSION=<folder>` and a prompt starting `[approver]`. On exit, hangup or terminate it kills the agent and deletes the folder. Whether the prompt survives the user's shell startup files is UNVERIFIED.

Inside the subshell every signing command still runs presence, refuses an agent ancestor and asks its challenge. Only the passphrase prompt goes away, because `ssh-keygen -Y sign` gets the public key of the key the session agent holds and that agent's socket. Whether this Mac's `ssh-keygen` signs through an agent is UNVERIFIED: premise box 2 was refused by the sandbox (prototypes/ssh-agent/result.txt), so the probe signature is what makes the start fail closed.

The session rule of `approver-check`: an agent may list the key only when `SSH_AUTH_SOCK` is `<folder>/agent.sock` of the folder `SKILL_MASTER_APPROVER_SESSION` names, that folder is `skill-master-approver-*` directly under `/tmp`, a directory (not a link) owned by this user with mode 0700, the socket is a socket owned by this user, and `ssh-add -L` through it answers. Any other agent that lists the key refuses, a socket in another folder included. The launchd agent is asked too whenever `SSH_AUTH_SOCK` does not name it. Its socket is resolved with the glob `/private/tmp/com.apple.launchd.*/Listeners`, keeping only sockets of this user in folders of this user; anything but exactly one match fails closed. That the real launchd folder and socket are owned by the user is UNVERIFIED.

## SSH sessions

pinesky may sign from an SSH session he opened (Appendix E, Q3). The presence walk still reads every process up to pid 0 and refuses any agent name, so an interactive TTY session whose chain is `sshd-session`, `sshd`, `launchd` passes, and an agent anywhere in the chain refuses, above `sshd` included (an agent can start its own `sshd`). A process the walk cannot read, such as a root `sshd` that libproc refuses, fails closed. That libproc's short info reads the root `sshd` the way it read pid 1 is inferred, not probed. The walk does not stop at a process named `sshd`. In an SSH login (`SSH_CONNECTION`, `SSH_CLIENT` or `SSH_TTY` set) `SSH_AUTH_SOCK` may be unset; approver-check then asks the launchd agent, and a forwarded agent that `SSH_AUTH_SOCK` names is asked like any other agent.

## The approval page

`approve-ui` (or `zsh scripts/approve-ui.sh`, which changes to the repository folder and passes `--cwd` and every argument) runs presence and approver-check before any socket opens, then asks the passphrase once (or uses the approver-session agent) and checks it with one probe signature. It binds `127.0.0.1` on a port the OS picks and prints `http://127.0.0.1:<port>/?t=<token>` with a one-time `secrets.token_urlsafe(32)` token and the batch challenge to the launching terminal only. The first request with the token gets a cookie `sm_<port>` set `HttpOnly; SameSite=Strict; Path=/` and a redirect to `/`, and the token stops working. Every request without the cookie, every `Host` other than `127.0.0.1:<port>`, and every POST whose `Origin` is not the page's own get 403. Every page escapes each value it shows and is sent with `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`, `X-Frame-Options: DENY`, `Cache-Control: no-store`, `Referrer-Policy: same-origin` and `X-Content-Type-Options: nosniff`. `same-origin` sends no `Referer` to another origin and keeps the page's own `Origin` on its form POST; under `no-referrer` a browser sends `Origin: null` there (Fetch standard, "append a request `Origin` header"), and the server refuses that. The server prints no request log.

The page lists each pending item with its unit, gate, status, summary, gate hash, `ruleset_sha256`, spend and estimate, and the text under review: the requirements file and every eval-set file for `requirements_eval_set`, read once, with the gate hash computed from those same bytes. The text keeps its line breaks and tabs; every other character that is not printable is shown as its Python escape, as on the terminal. pinesky picks approve, reject or leave per item, types reasons, types the terminal challenge and submits once. Before signing, the server checks that the launching terminal is still a TTY. Approved items are signed in the server process through `_approve_with`; a rejection writes no record, the item stays pending, and its reason goes to `approvals.log` with the time, unit, gate, hash, decision, reason and principal, never the token. A challenge mismatch signs nothing and prints a new challenge; the third mismatch closes the page. The server exits after the batch, or after 10 minutes without an authenticated request.

`--tailscale` works for one launch and says so loudly in the terminal. It reads `tailnet_owner_login` from `$HOME/.config/skill-master/approve-ui.json` and refuses to start without it. It runs `tailscale ip -4` (the binary is the first of `/opt/homebrew/bin/tailscale`, `/usr/local/bin/tailscale` and `/Applications/Tailscale.app/Contents/MacOS/Tailscale` that exists; the App bundle binary comes last because under the stripped environment of the call it prints `The Tailscale GUI failed to start` instead of answering, seen 2026-10-27 over SSH) and refuses to start unless it prints exactly one IPv4 address in 100.64.0.0/10, which it binds. It never binds `0.0.0.0` and never uses `tailscale serve` or funnel. On every request it runs `tailscale whois --json <peer>` and answers 403 unless the login equals the owner login. The login is read from `UserProfile.LoginName`, verified on a real whois output captured 2026-10-27 (Homebrew tailscale 1.96.3); a failed command, unparsable JSON or a missing field answers 403. The `Host` check accepts the Tailscale address and the optional `magicdns_name` of the config file. The token, cookie, challenge, idle exit and server-side signing work as in the local mode. The owner check authenticates a device of the owner's login, not the person at it.

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

The S1 cases are in `tests/test_approval_batch.py`, `tests/test_approve_ui.py`, `tests/test_machine_waiver.py`, `tests/test_combined_gate.py` and the `SessionAgent`, `SessionStart` and `SshSessionRoot` classes of `tests/test_human_gate.py`. They drive the page core with fake requests, a fake clock, a fake server and a fake `tailscale`, the session with a fake agent, `ssh-add` and shell, and the session rule with a fake glob, fake sockets and a fake `ssh-add`. The real-socket and pty cases skip only when the bind or `openpty` fails, as it does inside the session sandbox.

The older tests sign through `tests/test_skill_eval.py`'s test approver: a throwaway passphrase key, the principal `test-approver`, a fake `ssh-add`, and a runner that starts only `/usr/bin/ssh-keygen` on files in the test-key folder.
