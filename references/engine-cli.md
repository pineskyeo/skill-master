# Command reference

This page lists every subcommand of `eva.py` and `skill_eval.py`. The flags come from the argparse parsers: `eva.py` builds its parser in `eva.main`, and `skill_eval.py` builds its parser in `skill_eval.build_parser`. Run `python3 eva.py <subcommand> --help` or `python3 skill_eval.py <subcommand> --help` for the full flag list.

## Subcommands at a glance

`eva.py`, the static checks:

| Subcommand | Purpose | Main flags |
|---|---|---|
| `check PATH` | Check one skill or agent folder against the rule bundle, or grade one agent run transcript | `--json`, `--transcript FILE`, `--rules DIR` |
| `check-all PATH` | Check every folder under PATH that holds a `SKILL.md` or `AGENT.md` | `--json`, `--rules DIR` |
| `feedback PATH` | Write `FEEDBACK.md` and `FEEDBACK.json` into the unit | `--round N`, `--json` |
| `index PATH` | Write `INDEX.md` and `registry.json` into PATH | `--json` |
| `schema-check SCHEMA INSTANCE` | Validate one JSON file against one schema | `--unit DIR` |
| `generate PATH` | Render an agent's four tool files or a skill's invocation metadata | `--check` |

`skill_eval.py`, the eval harness and the unit lifecycle:

| Subcommand | Purpose | Main flags |
|---|---|---|
| `validate SKILL` | Validate the eval set, the English-only rule, multi-tool parity and the rule review record | `--workspace` |
| `estimate SKILL` | Print the cost estimate | the run flags, `--cases`, `--llm-cases`, `--trigger-queries` |
| `plan SKILL` | Print the plan, the gates and the estimate | the run flags, `--cases`, `--llm-cases`, `--trigger-queries` |
| `run SKILL` | Run the eval set per engine, with and without the skill | the run flags, `--live` |
| `grade SKILL` | Grade an iteration | the run flags, `--iteration`, `--grade-cases`, `--grade-runs`, `--live` |
| `calibrate SKILL` | Measure the grader's agreement and Cohen's kappa against the human labels of a golden file | the run flags, `--iteration`, `--golden` (required), `--live` |
| `report SKILL` | Write the benchmark, the gate and `comparison.md` of an iteration | `--workspace`, `--iteration` |
| `loop SKILL` | Run, grade, report and call the metered Claude builder until the gate is clean | the run flags, `--builder-prompt`, `--builder-model`, `--max-iterations`, `--golden`, `--live` |
| `approve SKILL` | Record one gate as an unsigned approval | `--gate` (required), `--workspace`, `--reason` (a `coverage_waiver:<tool>` gate) |
| `approve --batch` | Record every pending approval of the units; one reason line from stdin per coverage waiver item | `--unit` (repeated), `--workspace` (once per `--unit` or never), `--cwd` |
| `pending` | List every missing or stale approval of the units; records nothing | `--unit` (repeated, required), `--workspace`, `--cwd` |
| `approve-ui` | Removed (unit N1): prints one line and exits 2; use `approve --batch` | `--unit` (repeated, required), `--workspace`, `--cwd`, `--tailscale` |
| `approver-session` | Removed (unit N1): prints one line and exits 0 | none |
| `machine-waiver {sign,show,revoke}` | Record, show or revoke this Mac's machine waiver | `sign --tools` (default: `machine_waivable`), `sign --reason` |
| `register SKILL` | Record the registration | `--workspace` |
| `snapshot SKILL` | Copy the skill folder to `<workspace>/skill-snapshot` | `--workspace` |
| `check-builtin-plugins` | Show the enabled state of every `@builtin` Claude plugin | none |
| `builder-check SKILL` | Print the loop builder's writable set and a fingerprint of every entry outside it | `--workspace` (required), `--compare SAVED` |
| `approver-check` | Removed (unit N1): prints `approver-check: OK: ...` and exits 0 | none |
| `hub build [ROOT]` | Write the static registry site | `--out` (required), `--templates` |
| `agent validate AGENT` | Validate an AGENT.md v2 agent and its wrappers | none |
| `agent plan AGENT` | Print the per-tool plan and the estimate | the agent run flags, the agent grader flags |
| `agent run AGENT` | Run the scenarios per tool | the agent run flags, the agent grader flags, `--live` |
| `agent grade AGENT` | Grade the run transcripts of an iteration | `--workspace`, `--iteration`, the agent grader flags, `--live` |
| `agent report AGENT` | Write the benchmark, the gate and `comparison.md` of an iteration | `--workspace`, `--iteration` |
| `agent loop AGENT` | Improve an agent with a builder that may change only AGENT.md | the agent run flags, the agent grader flags, `--builder-prompt`, `--builder-model`, `--max-iterations`, `--live` |
| `install UNIT` | Install an agent's wrappers or a skill folder per tool | `--tools`, `--scope {project,user}`, `--target`, `--dry-run`, `--force`, `--workspace` |
| `version bump UNIT {major,minor,patch}` | Raise the SemVer version after a `CHANGELOG.md` entry | `--dry-run` |
| `deprecate UNIT` | Mark a version deprecated | `--version` (required), `--reason` (required), `--workspace` |
| `drift [ROOT]` | List tool version drift, units recorded under an older ruleset and old rule dates | `--observed TOOL=VERSION`, `--today YYYY-MM-DD` |

`python3 eva.py eval <arguments>` is not an argparse subcommand. `eva.main` passes the arguments after `eval` to `skill_eval.main`, so every `skill_eval.py` subcommand, check and gate applies to it unchanged.

## Approval commands

These commands write an unsigned recorded approval through `engine/approvals.py` (`APPROVER["it"].sign` in `skill_eval.py`). Approvals are unsigned recorded decisions: no approver key, ssh-agent, passphrase, typed challenge, TTY, agent-ancestry check or approver session is needed, and a pipe or a script works. Each record binds the kind, gate, unit, gated-file hash and `ruleset_sha256`, plus the budget of a `--live` start and the reason of a coverage waiver:

- `approve`, for every gate, and `approve --batch`.
- `machine-waiver sign` and `machine-waiver revoke`.
- `register`.
- `run --live`, `grade --live`, `calibrate --live` and `loop --live`.
- `agent run --live`, `agent grade --live` and `agent loop --live`.
- `install --scope user`.

Every refusal exits with status 2. [Approval gates](approval-gates.md) lists the steps, the refusal lines and what a record proves and does not prove.

## eva.py

### check and check-all

`check PATH` prints a header line with the verdict, kind and score, one row per check, and the `ruleset_sha256` line. A row shows its mark, id, category and reason. `--json` prints the report as JSON with a `ruleset_sha256` key. The exit status is 0 for PASS, 1 for FAIL and 3 for REVIEW.

`check-all PATH` prints `[VERDICT] SCORE PATH` per unit, a `total=N pass=N review=N fail=N` line and the `ruleset_sha256` line. It exits 1 when any unit fails or when no unit is found, 3 when a unit gets REVIEW and none fails, and 0 otherwise.

`--rules DIR` loads the rule files of DIR instead of `rules/`. That run adds a `bundle-non-default` needs-review row, so it never gives PASS, and `check-all` prints `bundle non-default DIR`. When the bundle does not load, both commands print `bundle error: ...` to stderr and exit 2 before any unit row. [Rules](rules.md) describes bundles.

`check AGENT --transcript FILE` grades one run transcript against the agent and its scenarios instead of checking the folder. It loads no rule bundle and prints no `ruleset_sha256` line. `--transcript` cannot be combined with `--rules`. [Eval set format](eval-set-format.md) describes the transcript.

### feedback and index

`feedback PATH` checks the unit with the default bundle and writes `FEEDBACK.md` and `FEEDBACK.json` into PATH. `--round N` adds the round to the `FEEDBACK.md` header. It exits like `check`. `--json` is accepted and not read.

`index PATH` checks every unit under PATH and writes `INDEX.md` (one row per unit with its kind, name, version, verdict, score, English state, parity, registration, path and description) and `registry.json` (the registered units only) into PATH. It exits 0. `--json` is accepted and not read.

### schema-check

`schema-check SCHEMA INSTANCE` prints `error ...` per problem, or `schema-check: valid`. `--unit DIR` is the unit root for the `x-path-exists` keyword. It exits 0 when the instance is valid, 1 when it is not, and 2 when the schema is refused or a file cannot be read.

### generate

`generate PATH` writes the generated files of an agent (its four wrappers) or of a skill (its tool metadata) and prints each file it wrote or removed. The wrapper paths come from `wrapper.path` in `targets/<tool>.json`: `wrappers/claude/<name>.md`, `wrappers/codex/<name>.toml`, `wrappers/opencode/<name>.md` and `wrappers/hermes/SOUL.md`. `--check` writes nothing, prints each problem and exits 1 when a generated file differs. Every refusal exits 2.

## skill_eval.py

### The run flags

`estimate`, `plan`, `run`, `grade`, `calibrate` and `loop` take the same run flags:

| Flag | Meaning |
|---|---|
| `--workspace` | The workspace folder. The default is `<skill parent>/<skill name>-workspace`. |
| `--engines` | Comma-separated engines, default `claude`. Only `claude` and `codex` have an engine. `opencode` and `hermes` are refused as not available. |
| `--runs` | Runs per case. |
| `--baseline {none,old}`, `--old-skill` | Compare with no skill (`without_skill`) or with an older skill folder (`old_skill`). |
| `--claude-model`, `--codex-model`, `--claude-arg`, `--codex-arg`, `--claude-effort`, `--codex-effort` | The models, extra CLI arguments and effort of the engines under test. |
| `--grader-engine {claude}`, `--grader-model`, `--grader-effort` | The grader and blind pairwise judge. |
| `--price KEY=IN,OUT`, `--tokens KIND=IN,OUT` | Price and token overrides for the estimate. |
| `--cases`, `--llm-cases`, `--trigger-queries` | Counts for `estimate` and `plan` only. Other commands refuse them. |
| `--live` | Start a real run. Human-only. |
| `--auth-profile` | `personal-mac` or `company` from `policy/auth-profiles.json`. Every `--live` start requires it. |
| `--budget-usd` | The limit in API-equivalent USD. Every `--live` start requires a finite value above 0. |
| `--strict-budget`, `--no-strict-budget` | The default refuses `--live` runs of engines without an enforceable per-call cap (Codex). |
| `--dry-run` | With `--live`, check every live gate for this exact command and print the plan. It executes nothing. Without `--live` it is refused. |
| `--seed` | The seed that `grade --live` and `loop --live` pass to grading. |
| `--run-root` | The folder for the per-run sandboxes. The default is the system temp folder. |

Without `--live`, `plan`, `run`, `calibrate` and `loop` print the plan and the estimate and start no tool CLI, and `grade` grades only the static and human items. The printed plan reads the approval records; only a record signed before unit N1 starts `/usr/bin/ssh-keygen -Y verify`, once per current signed record ([Approval gates](approval-gates.md#statuses)). `--live --dry-run` lists every live gate the command would fail, prints the real command without `--dry-run`, and exits 0 when no gate fails and 3 otherwise. [Auth profiles and cost](auth-and-cost.md) covers `--auth-profile`, `--budget-usd` and the spend ledger.

### The agent flags

The `agent` subcommands have their own, smaller flag sets. They have no `--dry-run`, `--seed`, `--baseline`, `--cases`, `--llm-cases` or `--trigger-queries`.

| Flags | Taken by |
|---|---|
| `--workspace` | every `agent` subcommand except `validate` |
| `--iteration` | `agent grade`, `agent report` |
| The agent run flags: `--engines`, `--runs`, `--old-agent`, `--claude-model`, `--codex-model`, `--claude-arg`, `--codex-arg`, `--claude-effort`, `--codex-effort`, `--strict-budget`, `--no-strict-budget`, `--run-root` | `agent plan`, `agent run`, `agent loop` |
| The agent grader flags: `--grader-engine`, `--grader-model`, `--grader-effort`, `--price`, `--tokens`, `--auth-profile`, `--budget-usd` | `agent plan`, `agent run`, `agent grade`, `agent loop` |
| `--live` | `agent run`, `agent grade`, `agent loop` |
| `--builder-prompt`, `--builder-model`, `--max-iterations` | `agent loop` |

Each flag means what it means for the skill commands.

### Per-command notes

- `validate` prints the eval set problems or `eval set OK: N cases, N trigger queries`, review lines for non-English run-output matchers, a `multi-tool parity:` line and the rule review state. It exits 1 on a problem, 3 when a rule review is not recorded, and 0 otherwise.
- `grade` reads each run's `outputs/transcript.json` and `workdir/`. `--grade-cases` and `--grade-runs` narrow grading to the listed eval ids and run numbers.
- `run --live` exits 0 when the iteration finished. Under the `company` auth profile a stopped iteration exits 3.
- `report` exits 3 when the gate needs approval and 0 when it is clean.
- `loop` takes `--builder-prompt FILE`, the instructions for the metered Claude builder. The file must not be empty. `--max-iterations` must be between 1 and the policy maximum. The removed `--builder` shell command is refused. `loop --live` prints its result as one JSON object, such as `{"status": "clean", "iteration": 2}`, and exits 0 for `clean` and 3 for any other status. [The eval harness](#the-eval-harness-skill_evalpy) lists the statuses.
- `approve --gate` takes `requirements`, `eval_set`, `requirements_eval_set`, `rule_review`, `iteration-N`, `registration` or `coverage_waiver:<tool>`. `approve` takes either `SKILL --gate G [--workspace WS] [--reason TEXT]` or `--batch --unit DIR ...`. Any other mix, `--batch` with `--reason` included, exits 2 with `approve: give SKILL and --gate, or --batch with --unit`. A coverage waiver takes its reason from `--reason`, else from one line of stdin; `--reason` on another gate exits 2.
- `pending` and `approve --batch` take the units as repeated `--unit DIR`. `--workspace WS` is given once per `--unit` in the same order, or not at all, and then each unit uses its default workspace. A workspace does not record its unit folder before a run, so the units are always named. Relative paths resolve under `--cwd`, which defaults to the current folder.
- `pending` prints one block per missing or stale approval with its unit, gate, status, summary, gate hash, `ruleset_sha256`, spend so far from `spend.jsonl` and the estimate `plan` prints for the next live run, then `N pending approval(s); nothing was recorded`. A skill lists `requirements_eval_set` when the live approvals gate is not open, `rule_review` when its findings have no current decision, `registration` when the gate inside the last iteration's `benchmark.json` needs approval, and each `coverage_waiver:<tool>` whose tool did not run and is not cleared. An agent folder lists the same gates; its eval set is its `scenarios/*.json`. It writes nothing and exits 0.
- `approve --batch` reads one reason line from stdin per coverage waiver item, in list order, then prints one `recorded <unit> <gate> sha256 <hash12> by <user>` or `not recorded <unit> <gate>: <why>` line per item and exits 2 when any item was not recorded. A refused reason records nothing.
- `approve-ui` (and `scripts/approve-ui.sh`, which runs `python3 skill_eval.py approve-ui --cwd <caller's folder>` with the given arguments) prints `approve-ui: approvals are unsigned recorded decisions; ...; no page was started, use approve --batch --unit DIR` and exits 2. `approver-session` prints the same note and exits 0.
- `machine-waiver sign` takes the reason from `--reason`, else from one line of stdin, and prints `machine waiver recorded: tools ..., expires ..., sha256 ...`. `machine-waiver revoke` deletes the file and asks nothing.
- `machine-waiver show` prints the waiver's state, sha256, who recorded it (or the principal of a waiver signed before unit N1), reason, expiry, tools, machine id and file. It exits 0 when the waiver counts and 1 otherwise.
- `snapshot` refuses when `<workspace>/skill-snapshot` exists. It also refuses a skill folder that holds a link that resolves outside the folder, dangles, resolves in a loop of links or points back at a folder on its own path, names every such link and copies nothing.
- `check-builtin-plugins` starts `claude --setting-sources project,local --settings <launch settings> plugin list --json` and reports the `enabled` state of every `@builtin` entry. It makes no model call and exits 1 when it cannot confirm that every built-in plugin is off.
- `builder-check` writes nothing and starts no process. With `--compare SAVED` it prints every changed path and exits 1 when any changed.
- `approver-check` prints `approver-check: OK: approvals are unsigned recorded decisions; ...` and exits 0. It reads no key and starts no process.
- `hub build` writes `index.html`, `registry.json`, `style.css` and one page per unit of ROOT (default `.`) into `--out`, which must be absent or empty, after an English-only and link scan of every file. `--templates` defaults to `engine/hub_templates`. It uses no network and pushes nothing.
- `agent validate` prints one line per check and per wrapper and exits 1 when a hard check fails.
- `agent plan` prints the plan and executes nothing. So do `agent run` and `agent loop` without `--live`. `--old-agent` names the previous AGENT.md v2 version of the agent as its baseline. After its earlier lines the plan prints the agent's approval gates: the rule review, the coverage waivers, the machine waiver, the combined `requirements_eval_set` gate, the separate `requirements` and `eval_set` gates, and an `agent live gates (...)` line with the state of the rule-review and approvals checks.
- An agent may pin registered skills with the AGENT.md key `skills` ([Eval set format](eval-set-format.md) has the key). `agent validate` then prints a `skills capability` line, which fails when `capabilities` lacks `skill` (rule `skills-need-skill-capability`). A pin resolves to `<root>/skills/<name>/`. `<root>` is the parent of the folder that holds the agent folder, so `agents/<agent>/` pins `skills/<name>/` of the same repository. No option names another root. The skill's workspace is its default one, `<root>/skills/<name>-workspace/`, the same one `registration_state` and `register` use. A pin verifies only when all of these hold at that moment: the folder's SKILL.md is named `<name>`, `registration_state` reads `registered`, the record is of kind `skill` with exactly that name and version, the SKILL.md frontmatter does not say `user-invocable: false`, and the workspace's `deprecations.json` does not mark that version deprecated. A pin that does not verify reads `pin <pin> does not verify: <reason>`, with one of these reasons:
  - `not <name>@<semver>`
  - `skill <name> is pinned twice`
  - `unregistered: no skill folder <path>`, or `<path> is not a skill folder (no SKILL.md)`
  - `the SKILL.md name is '<found>', not '<name>'`
  - `<state>: <why>` from `registration_state`, such as `stale: skill_sha256 changed` or `unregistered: no registration.json`
  - `the record is of kind '<kind>' for '<found>', not a skill named '<name>'`
  - `registered version is <found>, the pin asks <version>`
  - `user-invocable: false (or an unreadable SKILL.md): ...`, because the init event never lists such a skill and the run could not verify its load
  - `deprecated: <reason>`
- `agent plan` prints one line per pin after the scenarios line: `pin <pin>: registered, registration sha256 <hash> (<skill folder>)`, or `pin <pin>: REFUSED (<reason>)`. For each scenario `fixture` it prints `fixture <scenario> <slug>: sha256 <hash>, <N> file(s)` and one `fixture file <scenario> <slug>/<rel>: sha256 <hash>` line per file. `agent plan`, and `agent run` and `agent loop` without `--live`, then print one `agent plan refused: <reason>` line per refusal and exit 2 without the `DRY RUN` line. They create no workspace. The refusals are `skills-need-skill-capability: skills is set but capabilities lacks skill`, each `pin <pin> does not verify: <reason>`, and each `fixture <slug> of scenario <id>: <problem>` (see [Eval set format](eval-set-format.md) for the fixture problems). The previous version named by `--old-agent` is checked the same way.
- `agent run --live` and `agent loop --live` refuse the same pins and fixtures before the live start is recorded and again after it, and exit 2. The plan hash binds each pin with its registration hash. They also refuse `claude: pinned skill '<name>' is also installed at <path>; ...` when a user-level Claude skill has a pin's name. Each sandbox build checks the pins again. A pin that no longer verifies, or a pinned skill whose registration changed after the iteration started, stops the iteration with `a pinned skill of <agent> changed after the iteration started; nothing was run`. `agent grade --live` refuses a pin that does not verify. `agent grade` without `--live` and `agent report` read recorded files only and check no pin. `register` refuses an agent with `iteration-<n>: pin <pin> does not verify: <reason>; re-run the eval before registering`, or with `iteration-<n>: pin <pin> changed since the iteration recorded it; ...`, when its pins no longer verify at the registration hashes its iteration recorded. `registration_state` reads a registered agent as `stale`, with the same `pin ...` reason, when a pinned skill no longer verifies or its registration file changed.
- `agent run --live` and `agent loop --live` refuse, before the live start is recorded and again after it, without a current `requirements_eval_set` approval (or current `requirements` and `eval_set` approvals) in the agent workspace and without a current `rule_review` decision when the agent has rule-review findings. `agent grade --live` reads no approval. [Approval gates](approval-gates.md#agent-live-gates) has the refusal lines and the loop rule.
- `agent grade` without `--live` grades the transcripts and leaves `llm` criteria as needs-review.
- `agent report` exits 3 when the gate needs approval and 0 when it is clean. The gate problems hold every parity FAIL row of the agent folder as `<row id>: <reason>`, such as `parity-permissions-hermes`, so every agent needs a `registration` approval while the Hermes permission row stays FAIL (gates.md G4). `register` recomputes these rows and refuses an agent whose reported gate does not name each of them. The hub page of a registered agent lists the findings its `registration` approval named. A tool with recorded runs whose pinned skill load is not verified adds `skills-load-unverified:<tool>: <N> pinned run(s) ...` with the word UNVERIFIED. A run counts when its state is `run`, it has pins (`isolation.pinned_skills` in `run.json`, or for the new agent the `pins` of `plan.json`), and `isolation.skills_loaded` is not `verified`. The line rides the gate problems, so `agent report` exits 3 and the `registration` approval names it. `agent loop` counts it, like `agent-load-unverified:codex`, among the problems no builder can fix, and stops with `needs-coverage` when only those remain.
- `install`, `version bump`, `deprecate` and `drift` are described in [Install, versions and drift](install.md).

## The eval harness (skill_eval.py)

`skill_eval.py` runs a skill's eval set with Claude Code, with the skill and without it (`--baseline none`), or against an older copy (`--baseline old` with `--old-skill`, or `<workspace>/skill-snapshot`). It then grades the runs, aggregates them and writes a gate. [Eval set format](eval-set-format.md) describes the input files, the workspace layout and the deviations from skill-creator. The first proposed live target is `golden/skills/csv-cleaner`, because it lives in this repository, its `scripts/clean.py` is deterministic and its input files are in `evals/files/`.

### Live gates

No `claude` or `codex` CLI and no model runs for `run`, `grade`, `calibrate` or `loop` unless the command line holds `--live`, a finite `--budget-usd` above 0 and `--auth-profile`, and the start is recorded as an unsigned approval bound to the plan hash and the budget ([Approval gates](approval-gates.md)). The exception is `check-builtin-plugins`, which starts the `claude` CLI without `--live` and makes no model call. `run`, `grade`, `calibrate` and `loop` then check the live gates in the order that `policy/gates.json` declares under `live`: `eval-set-valid`, `strict-budget`, `subscription-auth`, `grader-tier-distinct`, `rule-review`, `approvals` (`requirements` and `eval_set`), `priced-models`, `preflight` (`run` and `loop` only), `per-call-bound` and `estimate-within-budget`. `loop` checks them again before every iteration, with the reloaded eval set and the remaining budget, and stops with `gates-failed` on a failure.

`grader-tier-distinct` compares tiers, the word opus, sonnet or haiku in a model name (`MODEL_TIERS` in `policy/models.json`). The grader tier must differ from every model under test and from the builder. The gate refuses a name with no tier word or with two, and it refuses a model under test with no recorded name (the Codex CLI default). A Codex model name holds no tier word, so `run --live` and `loop --live` refuse Codex under test. `grade`, `agent run`, `agent loop` and `agent grade` apply the same rule; `grade` and `agent grade` read the engines and `builder_model` of `plan.json`; `calibrate` checks only the grader's own name, because it has no model under test and no builder.

`--strict-budget` is the default. Codex has no per-call budget cap, so its budget is a soft limit: one call can exceed `--budget-usd`, and the Codex `auth.json` copy is readable by the agent under test. So the default `--strict-budget` refuses every `--live` run that selects Codex. `--no-strict-budget` is the risky opt-out of the `strict-budget` gate only. A Codex model name holds no tier word, so `grader-tier-distinct` still refuses Codex under test. A start that passes every gate prints both risks as warnings to stderr, and `plan.json` records `budget_enforcement` (`per_call_cap` for Claude, `soft_limit` for Codex).

### The loop builder

`loop --live --builder-prompt FILE` calls one metered `claude -p` builder between iterations. It runs in the skill folder with `--tools Read,Edit,Write,Glob,Grep` (no Bash, so it cannot start another paid CLI), `--permission-mode acceptEdits`, `--disable-slash-commands`, a `--max-budget-usd` cap from the shared budget, and a `PreToolUse` gate (`claude_bash_gate.py` in edit-allowlist mode) that denies every write outside the writable set of `engine/writable.py`. The prompt holds your instructions, the English-only and multi-tool parity rules and the last `comparison.md`.

A builder call that ends badly is charged `max(reported, cap)`, recorded in `cost.json` and stops the loop with `builder-failed`:

- `builder output is not a JSON object`: the output is empty, noise or a JSON array.
- `builder output is not a result object`: the JSON object's `type` is not `result`.
- `builder result is an error`: the result has `is_error` set, even with exit status 0.

After every builder call, also one that failed or raised, these gates run in the order of `policy/gates.json` `post_builder`, and the first finding ends the loop with its status: `builder-wrote-outside`, `rules-changed`, `builder-non-english`, `builder-parity-missing`, `builder-review-required`, `builder-failed` and `eval-set-invalid`. The other statuses are `clean`, `gates-failed`, `run-stopped`, `needs-human-review` (only human reviews are pending, and no builder runs), `needs-coverage`, `budget-exhausted` and `needs-approval` (problems remain after the last iteration).

`--live --dry-run` prints the live command with `shlex.join`, so an argument that holds `$`, a backtick or a quote can be copied into a shell as it is.

### Eval isolation

Every run gets a fresh sandbox `<run root>/skill-eval-<engine>-XXXX/` under `--run-root` (default: the system temp folder). Its `work/` folder holds the case's input files and, for `with_skill`, only the skill under test. After the run, the outputs except the installed skill copy move to `run-<k>/workdir/`, and the sandbox is removed. A sandbox that cannot be removed after 3 attempts stops the iteration, because it may hold the Codex `auth.json` copy. Isolation that cannot be verified fails closed: the run is recorded as an error and the iteration stops.

Claude Code keeps `HOME` and `CLAUDE_CONFIG_DIR` unchanged, because the macOS Keychain entry of the login is keyed to the config dir (https://code.claude.com/docs/en/authentication). Every Claude launch (executor, grader and builder) gets:

- `--setting-sources project,local`, so the user source (`~/.claude/skills`, user settings) is not loaded.
- `--strict-mcp-config`, so no MCP server loads.
- `--settings` with `disableBundledSkills: true`, `enabledPlugins` set to false for every known `@builtin` plugin, and `skillOverrides` set to `"off"` for every known bundled skill except the skill under test.
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `ENABLE_CLAUDEAI_MCP_SERVERS=false` and `DISABLE_DOCTOR_COMMAND=1` in the child environment.
- `--disable-slash-commands` for `without_skill` runs, for the grader and for an agent run without pinned skills.

After a Claude run, the stream-json `system/init` event must show an explicit empty `plugins` list and a skill set of exactly the skill under test (`with_skill`) or nothing (`without_skill`). Under subscription auth its `apiKeySource` must be `"none"`. A missing field fails closed. The init event lists only user-invocable skills, so a claude.ai synced skill with `user-invocable: false` is refused before launch. Custom command files under `.claude/commands/` in the work folder or an ancestor are refused too.

Codex runs with `HOME=<sandbox>/home` and `CODEX_HOME=<sandbox>/codex_home`, which hides `~/.agents/skills`, `$CODEX_HOME/skills` and the user's `config.toml`. The skill goes to `work/.agents/skills/<name>/` (https://learn.chatgpt.com/docs/build-skills). Skills in `/etc/codex/skills` cannot be hidden and refuse the run. Codex reports no skill-load event, so its trigger detection, which looks for `skills/<name>/SKILL.md` in the run's items, is UNVERIFIED. A Codex stream with no readable JSON event, or with no `turn.completed` or `turn.failed` event, counts as a failed call.

Preflight (`plan` shows it, and `run --live` and `loop --live` refuse on it) also checks:

- skills under `.claude/skills` or `.agents/skills` in the run root or any ancestor;
- a user-level Claude copy of the skill under test with the same name, because whether `--setting-sources` hides synced and plugin skills is UNVERIFIED;
- API-key and provider-routing variables in the environment;
- every settings source a run could load: the user `settings.json` and `settings.local.json` of the config dir, `.claude/settings.json` and `.claude/settings.local.json` of the run root and every ancestor, `managed-settings.json` and `managed-settings.d/*.json`, a macOS configuration profile for `com.anthropic.claudecode`, and the server-managed cache `remote-settings.json`. An `apiKeyHelper`, an API-key or provider variable such as `CLAUDE_CODE_USE_BEDROCK`, or a `CLAUDE_CODE_OAUTH_TOKEN` entry in the `env` of any settings source refuses the run. A settings file that exists but cannot be read also refuses.

An agent run gets a fresh sandbox `<run root>/agent-eval-<engine>-XXXX/`. Its `work/` folder holds exactly the generated wrapper (`.claude/agents/<name>.md` or `.codex/agents/<name>.toml`), the files of the scenario's `fixture`, and each pinned skill copied at project scope (`.claude/skills/<name>/` for Claude, `.agents/skills/<name>/` for Codex). Any other file or skill refuses the run before launch. An agent without pins keeps `--disable-slash-commands`, and the Claude launch drops it only for an agent with pins. The sandbox refuses a pinned skill that shares a bundled skill name, because the launch settings turn every bundled name off. After a Claude run with pins, any init skill outside the pins is a fatal isolation problem. Init skills equal to the pins record `skills_loaded: verified` in the `isolation` object of `run.json`, next to `pinned_skills`. An empty list or a subset records `skills_loaded: unverified`, which is never a pass. The agent gate names it as `skills-load-unverified:<tool>` (see `agent report` above). A pinned skill with `user-invocable: false` is refused before launch, because init never lists it. Whether Codex and Hermes load project-scope skills for a custom agent is UNVERIFIED. A Codex run with pins therefore records `skills_loaded: unverified`, and the engine claims nothing for those tools. Before the sandbox is removed, the run writes the `work_manifest` of the work folder into `transcript.json`. It lists every file left there with its sha256, except the wrapper and the pinned skill files, and every symlink with its target, which is never followed. `plan.json` records each pin with its registration hash (`pins`) and each scenario fixture with its hash and files (`fixtures`). `run.json` records the run's fixture slug and hash (`fixture`). `plan.json` holds `pins` only for an agent with pins and `fixtures` only when a scenario names a fixture, and `run.json` holds `fixture` only for such a scenario.

A skill folder with a symlink that resolves outside it, dangles, resolves in a loop or forms a cycle is refused before install, with every such link named. An input file listed in `evals[].files` is copied only when its real path is inside the skill folder.

`--claude-arg` and `--codex-arg` refuse the flags that would change the priced model, the per-call cap or the isolation, for example `--model`, `--max-budget-usd`, `--settings`, `--setting-sources`, `--plugin-dir`, `--add-dir` and `--mcp-config` for Claude, and `-m`, `--profile`, `-C` and `--add-dir` for Codex (`skill_eval.FORBIDDEN_EXTRA_ARGS`).

### Executor permissions for a gated script

When `evals/evals.json` names `claude_python_script`, both arms run with `--permission-mode default --allowedTools "Edit(/**),Bash(python3 -I .claude/skills/<name>/<script> *)" --disallowedTools Monitor PowerShell`. Their `--settings` add a `PreToolUse`/`*` hook that runs `claude_bash_gate.py` for every tool call. The hook denies any tool input that carries a `command` field, except the one exact `python3 -I` call of the installed script with an existing input file and a new output file inside the work folder. It denies a file edit outside the work folder, under `.claude/skills/`, or on the hook or the installed script. Read, Glob, Grep, Skill, TodoWrite and the other tools without a command or an edit path get no hook decision and keep the normal permission flow. A failing hook blocks (`onFailure: block`).

### Subscription auth

Under the `personal-mac` auth profile, live runs use subscription auth only: the Claude Code claude.ai login and the Codex ChatGPT login. [Auth profiles and cost](auth-and-cost.md) describes the `company` profile, which lists API keys instead.

- `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`, `CODEX_API_KEY`, the other credential variables of `skill_eval.API_KEY_ENV` and the provider-routing variables of `skill_eval.PROVIDER_ENV` refuse a live start when set, even when empty. They are removed from every child environment anyway. Their values are never read or printed, and a run records only the removed names.
- `CLAUDE_CODE_OAUTH_TOKEN` in the shell is not refused but removed from every child, so a run authenticates with the same Keychain login that `claude auth status` checked. Nothing is asked of the user: no `claude setup-token`, no new login and no exported token.
- `SSH_AUTH_SOCK`, `SSH_AGENT_PID`, `SKILL_MASTER_APPROVER_SESSION`, `PS1` and `PROMPT` (`skill_eval.APPROVER_SESSION_ENV`) are removed from every child too, so a run started from a shell with an ssh-agent, or from an approver-session shell left from before unit N1, gives the skill under test no path to that agent. A skill whose eval needs ssh-agent (for example git over ssh) cannot use one in a run.
- The live gate runs `claude auth status --json` (no model call). It passes only with `loggedIn: true` and a method value under a method-like key that is one of the recognized subscription methods. An API-key method, missing JSON or an unknown method refuses. The JSON key names are UNVERIFIED. A session started over SSH or in a tmux server outside the desktop session may not reach the Keychain (UNVERIFIED); `claude auth status` then reports no login and the start refuses.
- Account switching: every `claude` child gets the caller marker `CLAUDE_AS_CALLER=skill_eval` and `CLAUDE_AS_AUTO_SWITCH`, which is `1` only for a call whose `--tools` provably exclude Bash (the grader and the builder) and `0` otherwise. An account shim named `claude` on `PATH` may then retry such a call once on another account (`~/.claude-acctN`) after a usage-limit error. skill_eval itself never passes a token. `SKILL_EVAL_ACCOUNT_SWITCH=0` turns switching off. The config dir of every account the shim may switch to gets the same preflight and launch checks as the default one. Without the shim on `PATH`, the real CLI ignores the marker.
- Codex: only the `auth_mode` field of the file-based `$CODEX_HOME/auth.json` is read, and it must be `chatgpt`. Each run gets a read-only copy (mode 0400, in a 0700 folder) that is removed with the sandbox. The harness never prints or logs it, but the copy is readable by the agent under test, which runs as the same user, so its tokens may end up in a saved answer, transcript or output file.

### Cost

Subscription runs are not billed per token, so the harness records an API-equivalent cost: what the same call would cost at API prices. `--budget-usd` limits that value, and every budget gate uses it. Each call is charged by `skill_eval.api_equivalent`, and the run's `api_equivalent_source` names the basis:

- `tokens_x_price`: usage tokens times the price row of `policy/pricing.json` or `--price`.
- `cli_reported`: the CLI's `total_cost_usd`, when it is larger than the token cost.
- `cap_charged`: the call's cap or worst-case bound, when no usage is known.

A call that fails after launch (`call failed after launch (<exception name>)`) or whose output cannot be parsed is charged `max(reported, cap)` and stops the iteration. Each Claude call gets `--max-budget-usd` at `CLAUDE_CAP_HEADROOM` (0.5) of the remaining budget, floored to cents. A Codex call starts only when its worst-case bound fits the remaining budget.

| Where | What |
|---|---|
| `run-<k>/timing.json` | `cost_usd` (the CLI value), `api_equivalent_usd`, `api_equivalent_source`, `billing` |
| `iteration-N/plan.json` | `api_equivalent_usd` of the run's exec and trigger calls, `billing` |
| `iteration-N/cost.json` | one entry per grade, calibrate and builder call |
| `benchmark.json` `cost` | the totals per engine and kind, `runs_without_usage`, and `actual_spend_usd: null` with a note under subscription auth |
| `comparison.md` | a cost line and a `## Cost` table |

Actual spend under subscription auth is visible only in the account. [Auth profiles and cost](auth-and-cost.md) covers the API-key spend ledger.
