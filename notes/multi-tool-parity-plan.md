# Multi-tool parity rule: plan

Owner decision (pinesky, 2026-10-19 18:43 KST): every time a skill or an agent is created, it is created for all four
tools together: Claude Code, Codex CLI, OpenCode, Hermes Agent. This is a second change set on top of the English-only
rule (`notes/english-rule-plan.md`). Baseline before this change set: `python3 -m unittest discover -s tests` printed
`Ran 671 tests ... OK`. Section 11 is filled in after the implementation with real output.

Source of the per-tool formats: `~/workspace/pstack-trial/runs/engrule/design.md`, sections 2 and 3 (local versions in
that file: Claude Code 2.1.302, Codex CLI 0.162.1, OpenCode 1.17.13, Hermes: not installed). Nothing here was re-verified
against a primary source in this session. Every format fact below is "per design.md"; the items design.md marks
UNVERIFIED are listed in section 8 and stay unverified.

## 1. What the rule is

Two front ends, one set of functions (`eva.py`):

- Static rule: `python3 eva.py check <unit>`. New rows in the category `parity`, all required (`must=True`), so a failure
  gives verdict FAIL regardless of score. Skills and agents.
- Dynamic rule (`skill_eval.py`, skills only): `validate` and `plan` print the per-tool parity status; a parity failure is
  a validation problem (so every `--live` gate refuses); `loop` tells the builder to keep all four tools loadable and a code
  gate re-checks the rewritten skill after every builder call; `benchmark`/`report` record per tool whether it was run.

`skill_eval.py` calls the `eva.py` functions (`eva.parity_report`, `eva.parity_status`, `eva.parity_violations`). No
parity logic is duplicated.

## 2. Package layout

### 2.1 Skill package

The shared agentskills.io `SKILL.md` is the single source for all four tools. There is no per-tool copy of the skill.

```
<skill-name>/
  SKILL.md                    required, shared by Claude Code, Codex CLI, OpenCode, Hermes Agent
  agents/openai.yaml          optional, Codex only (invocation policy); validated if present
  references/ assets/ scripts/ evals/   unchanged
```

"Loadable by all four" means the SKILL.md frontmatter satisfies the intersection of the four tools' rules:

| Constraint | Protects | Source |
|---|---|---|
| frontmatter parses; `name` and `description` are non-empty strings | all four | agentskills.io, design 2.1 to 2.4 |
| `name` matches `^[a-z0-9]+(-[a-z0-9]+)*$` | OpenCode | design 2.3 |
| `name` equals the folder name exactly (no NFKC folding) | OpenCode | design 2.3 |
| every `metadata` value is a string (quoted if YAML would type it) | OpenCode | design 2.3 |
| no reserved words in `name`, `description` at most 1024 characters, no `<` `>` | Claude Code | Claude skill best practices, already enforced by `name-reserved`, `description-length`, `description-no-xml` |
| `agents/openai.yaml`, if present, parses and `policy.allow_implicit_invocation` (if present) is an unquoted boolean | Codex CLI | design 2.2 |
| `metadata.hermes` is not a mapping or list (decision, 2.2 below) | Hermes, OpenCode | design 2.3 and 2.4 |
| `disable-model-invocation: true` is only allowed with the equivalent controls (2.3 below) | Claude Code, Codex, OpenCode | design 2.2 and 2.3 |

### 2.2 Decision: Hermes-specific keys

design.md 2.4 says Hermes-specific keys go under `metadata.hermes` (tags, `requires_toolsets: [terminal]`). design.md
2.3 says OpenCode requires `metadata` values to be strings. A `metadata.hermes` mapping would break OpenCode, and a
string form (for example JSON in a string) is not documented for Hermes (UNVERIFIED).

Decision: the portable package uses only agentskills.io fields. `metadata.hermes` as a mapping or list is a required FAIL
under `parity-skill-hermes` (it also cannot be parsed by this repo's frontmatter reader). A skill is loadable by Hermes
with the plain fields alone. A skill that needs a shell says so in the `compatibility` string. Hermes-specific tuning
(tags, `requires_toolsets`) is out of scope until someone verifies a string-safe form on a real Hermes install.

### 2.3 Decision: `disable-model-invocation`

OpenCode 1.17.13 ignores `disable-model-invocation` (task text, confirmed by design.md 2.3: "No
`disable-model-invocation`"). A skill that relies on it would be auto-selectable on OpenCode and (no equivalent is known)
on Hermes.

Decision, `parity-invocation-control` (required):

- Field absent, or `disable-model-invocation: false`: n/a. `false` is the default behaviour, nothing relies on it.
- `disable-model-invocation: true`: FAIL unless both equivalents are declared in the same package:
  - OpenCode: `metadata` key `opencode/autoinvoke` with the string value `"false"` (design 2.3).
  - Codex: `agents/openai.yaml` with `policy: allow_implicit_invocation: false` (design 2.2).
- Hermes has no verified equivalent (design 2.4 lists none). When `true` is declared, `parity-invocation-hermes` adds a
  `needs-review` item (verdict REVIEW, exit 3): a human confirms that auto-selection by Hermes is acceptable.

To make `opencode/autoinvoke` expressible, the nested-key pattern of the frontmatter reader now allows `/` and `.` in
second-level keys (`metadata.opencode/autoinvoke`). Top-level keys are unchanged.

### 2.4 Agent package

`AGENT.md` stays the source (name, description, tools). New `wrappers/` layout, one thin file per tool:

```
<agent-name>/
  AGENT.md                            source (unchanged format)
  scenarios/*.json                    unchanged
  wrappers/claude/<name>.md           YAML frontmatter + prompt body (Claude Code subagent)
  wrappers/codex/<name>.toml          flat TOML (Codex custom agent)
  wrappers/opencode/<name>.md         YAML frontmatter + prompt body (OpenCode agent); file name = agent name
  wrappers/hermes/SOUL.md             Hermes profile identity
  wrappers/hermes/config.yaml         optional; existence and English-only only, schema UNVERIFIED
```

`<name>` is the `name` of `AGENT.md`, which must match `^[a-z0-9]+(-[a-z0-9]+)*$` (house rule: it is a file stem on
three tools, `parity-agent-name`).

Per wrapper:

| Tool | Required | Compared with AGENT.md |
|---|---|---|
| Claude Code | file exists, frontmatter parses, `name`, `description`, `tools` (list or comma string), non-empty body; optional `model` (non-empty string) | `name` equal, `description` equal (whitespace-normalized), `tools` capabilities equal |
| Codex CLI | file exists, flat TOML parses, `name`, `description`, `developer_instructions` non-empty strings, no `sandbox_mode` key (no effect in a custom agent file); optional `model`, `model_reasoning_effort` (strings); no other key | `name` equal, `description` equal; permissions are not compared (2.5) |
| OpenCode | file exists, frontmatter parses, `description`, `permission` with every recorded key (below) set to `allow`/`ask`/`deny` and no other key, `external_directory` set to `deny`, non-empty body; optional `mode` in `primary`/`subagent`/`all`, `model`, `name` | file stem equals name, `description` equal, `name` (if present) equal, permission vs capabilities |
| Hermes Agent | `SOUL.md` non-empty; optional `config.yaml` readable | `SOUL.md` contains the agent name and the AGENT.md description verbatim (whitespace-normalized) |

Deviations from the task wording, all deliberate:

- Claude `tools` is optional in the Claude format but required by this package: a permission comparison needs a declared
  list, and what an omitted list grants is not established by design.md. OpenCode `permission` is required for the same
  reason.
- A Codex wrapper must not set `sandbox_mode` (plan C2, 2026-10-19). The key has no effect in a custom agent file: the
  Codex doc says a custom agent's `sandbox_mode` does not override the parent session, so the agent runs with the parent
  session's sandbox (source: plan inputs/agent-master-skills.md section 3.4, finding F1). A wrapper that sets it fails
  `parity-wrapper-codex` with `sandbox_mode in a Codex custom agent file does not override the parent session; remove
  it`. Premise UNVERIFIED as of 2026-10-19: the operator probe on codex-cli 0.162.1 (plan C2, first Build box) has not
  run. The rule fails closed either way, because it can only turn a PASS into FAIL or REVIEW.
- A Codex wrapper may set only five of the eight custom-agent keys that the plan inputs record (C4, Codex P1 round 1
  finding P1-4; plan inputs/design.md section 2.2). The five are required `name`, `description`,
  `developer_instructions` and optional `model`, `model_reasoning_effort` (`eva.CODEX_AGENT_REQUIRED_KEYS`,
  `eva.CODEX_AGENT_OPTIONAL_KEYS`). The recorded Codex doc says `sandbox_mode` and `mcp_servers` have no effect in a
  custom agent file (inputs/agent-master-skills.md section 3.4, finding F1), so they stay out. `skills.config` is a
  dotted key, and the strict flat-TOML reader rejects it until plan unit A3 adds tables. Every other flat key fails
  `parity-wrapper-codex` and the reason names it (`keys ['foo'] are not in the recorded Codex custom-agent key list
  (...)`). That includes `approval_policy`, `model_provider` and `mcp_servers`, which the recorded Codex doc says have no
  effect in a custom agent file (agent-master-skills.md finding F1). It is FAIL, not REVIEW, because the plan names no
  REVIEW for this row and an unverified key must not pass (standing order 10). `sandbox_mode` keeps its own remove
  message. No doc was re-read for this; the list is as recorded on 2026-10-19.
- The OpenCode wrapper must set every recorded permission key explicitly (plan C3, 2026-10-19). The recorded keys are
  `read`, `edit`, `glob`, `grep`, `list`, `bash`, `task`, `external_directory`, `webfetch`, `websearch`, `lsp` and
  `skill`, twelve in all. Source: plan inputs/agent-master-skills.md section 3.4, which took them from the OpenCode agents
  doc (opencode.ai/docs/agents) as read on 2026-10-19. That list ends in "...", so it is known to be incomplete, and
  this package records it as `complete: false` (`eva.OPENCODE_PERMISSION_KEYS_COMPLETE = False`). No doc re-read or
  operator capture has run since, because this run had no network (plan Appendix E, Q24). Why every key must be set:
  the default of an unset key is not recorded anywhere this package can cite, so it is UNVERIFIED. A missing key fails
  `parity-wrapper-opencode` with `permission.<key> must be set explicitly`. A key outside the list fails the same row
  and is named. Why a match is REVIEW: OpenCode may have keys outside the list, and each of those keeps its UNVERIFIED
  default. So `parity-permissions-opencode` is REVIEW, never PASS, while `complete` is false (2.5).
- OpenCode `mode` is validated when present, not required (design 2.3 lists only `description` as required).
- Hermes has no frontmatter in SOUL.md (design 2.4), so name and description consistency is textual: SOUL.md must state
  both.

### 2.5 Permission comparison

AGENT.md `tools` are free-form names. They are mapped to capabilities by one table (`CAPABILITY_BY_TOOL` in `eva.py`):

| capability | tool names (lowercase) |
|---|---|
| read | read_file, read, grep, glob, list_dir, ls, find, search, view, cat |
| write | write_file, edit_file, write, edit, multiedit, create_file, delete_file, notebookedit, apply_patch |
| shell | bash, shell, terminal, exec, run_command |
| web_fetch | web_fetch, webfetch |
| web_search | web_search, websearch |
| delegate | task |
| skill | skill |

`todowrite` and `todo_write` are neutral (ignored). Any other name is "unmapped": the row fails with the name, so the
table is extended on purpose rather than guessed. Plan C3 split `web` into `web_fetch` and `web_search` and added
`delegate` and `skill`, so `skill` is no longer neutral. The delegate name is `task`, the recorded OpenCode permission
key that plan C3 maps to delegate (2.4). No other delegate tool name existed in this package's code, so no other name was
added.

- Claude: the wrapper `tools` list must name the same tools as AGENT.md `tools`, compared by name (lower case) through the
  explicit alias table `eva.TOOL_ALIASES` (`Read`=`read_file`, `Write`=`write_file`, `Edit`=`edit_file`, `Bash`=`shell`,
  `WebFetch`=`web_fetch`, `WebSearch`=`web_search`); any other name only equals itself, so `Grep`, `Glob` and `list_dir`
  are different tools. Round 1 (section 12): comparing capability sets let `read_file` be replaced by `grep`.
- Codex: nothing to compare. `sandbox_mode` has no effect in a custom agent file and the wrapper must not set it (2.4),
  and the flat reader cannot express any other Codex per-agent restriction, so the wrapper adds no capability set. The
  classes Codex cannot restrict per agent are `eva.CODEX_UNRESTRICTABLE` (write, shell, web_fetch, web_search, delegate,
  skill). When AGENT.md lacks any of them, `parity-permissions-codex` is REVIEW, never PASS, with `Codex cannot restrict
  <list> per agent; the agent runs with the parent session's sandbox`, the list in that order. This applies plan
  Appendix E, Q4 (default REVIEW, cleared per unit by a recorded `rule_review`). The row passes only when AGENT.md needs
  all six, is N/A while the Codex wrapper has problems, and fails on an unmapped AGENT.md tool.
- OpenCode: one table, `eva.OPENCODE_PERMISSION_GRANTS`, maps each recorded key (2.4) to the class it grants. Every
  check reads it: missing key, unknown key, value, and the derived set.

  | key | grants |
  |---|---|
  | read, glob, grep, list, lsp | read |
  | edit | write |
  | bash | shell |
  | webfetch | web_fetch |
  | websearch | web_search |
  | task | delegate |
  | skill | skill |
  | external_directory | no class, so it must be `deny` |

  A key set to `allow` or `ask` grants its class. The derived set must equal the AGENT.md set over every class, or
  `parity-permissions-opencode` fails and states both sets. A match is REVIEW while the key list is incomplete, with
  `the OpenCode permission key list is not verified complete; an unlisted key keeps its UNVERIFIED default`, and PASS
  only once `eva.OPENCODE_PERMISSION_KEYS_COMPLETE` is true (plan risk R17, Appendix E Q24). Pattern maps are not
  supported. The strict reader allows one level of nesting, so `bash:` with nested `"*": deny` lines, and the flow form
  `bash: {"*": deny, "python3 *": allow}`, both make the frontmatter unusable. The wrapper row fails, never PASS.
- Hermes: cannot be compared, because no verified per-agent permission surface exists (design 2.4: per-command allow/deny
  UNVERIFIED). Round 1 decision (section 12): the row `parity-permissions-hermes` is a required **FAIL** for every agent
  (fail closed) until a Hermes format is verified, instead of `n/a`, so a complete agent is never `hermes=pass`.

## 3. Strict flat-TOML reader (stdlib only)

`eva.parse_flat_toml(text)` raises `TomlError` (message with line number) on anything it cannot parse. Supported:
comments, bare keys, basic strings with all TOML escapes (`\b \t \n \f \r \" \\ \uXXXX \UXXXXXXXX`), literal strings,
multi-line basic and literal strings (leading newline trimmed, line-ending backslash), integers, floats, booleans, and
arrays of those (one line or several). Rejected with an error: tables and arrays of tables (`[x]`, `[[x]]`), dotted and
quoted keys, inline tables, dates and times, duplicate keys, raw control characters in strings, invalid escapes,
surrogate escapes, unterminated values, trailing text after a value, a BOM, a lone CR. "Flat" is the package rule: a Codex
wrapper that needs `[mcp_servers.x]` tables is a FAIL until the reader grows (follow-up).

## 4. Rule ids, severity, tools

All use the category `parity`, all `must=True` (FAIL) except the Hermes review item. `n/a` when nothing applies.

Skill rows:

| id | protects | FAIL when |
|---|---|---|
| `parity-skill-claude` | Claude Code | frontmatter unparsable, name/description missing, reserved word, description over 1024 chars, `<`/`>` |
| `parity-skill-codex` | Codex CLI | frontmatter unparsable, name/description missing, `agents/openai.yaml` present but unparsable or `policy` malformed. needs-review (never pass) when the file holds any field other than `policy.allow_implicit_invocation` |
| `parity-skill-opencode` | OpenCode | name not `^[a-z0-9]+(-[a-z0-9]+)*$`, name differs from folder, non-string metadata value, bad `opencode/autoinvoke` value |
| `parity-skill-hermes` | Hermes Agent | frontmatter unparsable, name/description missing, `metadata.hermes` mapping or list |
| `parity-invocation-control` | Claude Code, Codex, OpenCode | `disable-model-invocation: true` without both equivalents; n/a otherwise |
| `parity-invocation-hermes` | Hermes Agent | needs-review when `disable-model-invocation: true`; n/a otherwise |
| `english-tool-files` | all (language) | non-English text in `agents/openai.yaml` (n/a when the file is absent) |

Agent rows:

| id | protects | FAIL when |
|---|---|---|
| `parity-agent-name` | all four | AGENT.md name is not `^[a-z0-9]+(-[a-z0-9]+)*$` |
| `parity-wrapper-claude` / `-codex` / `-opencode` / `-hermes` | that tool | wrapper missing, unparsable, a required key missing, name or description differs from AGENT.md, the Codex wrapper sets `sandbox_mode` (no effect in a custom agent file, so the wrapper must not set it) or a key outside the recorded Codex key list (named in the reason), the OpenCode wrapper leaves a recorded permission key unset, sets an unknown key, sets a value other than allow/ask/deny, or sets `external_directory` to anything but deny |
| `parity-permissions-claude` / `-opencode` | that tool | Claude: wrapper tool names differ from the AGENT.md tool names (by name, aliases only from `TOOL_ALIASES`). OpenCode: the classes the permission keys grant differ from the AGENT.md capabilities; needs-review (never pass) on a match while the key list is not verified complete (2.5); n/a while the OpenCode wrapper has problems. Any: AGENT.md has an unmapped tool name |
| `parity-permissions-codex` | Codex CLI | AGENT.md has an unmapped tool name. needs-review (never pass) when AGENT.md lacks write, shell, web_fetch, web_search, delegate or skill (2.5); n/a while the Codex wrapper has problems |
| `parity-permissions-hermes` | Hermes Agent | always (fail closed: no verified Hermes permission format), reason printed |
| `english-wrappers` | all (language) | non-English text in any file under `wrappers/` (names and contents; `\u` escapes decoded only in TOML basic strings and YAML double-quoted scalars) |

Existing `english-*` rows are unchanged. The per-tool status used by `skill_eval` and the index is derived from these rows
(`eva.PARITY_ROW_TOOLS`): a tool is `fail` if any row that protects it failed, `review` if a review item remains, else
`pass`.

## 5. Dynamic rule (skill_eval.py)

- `load_eval_set` appends `multi-tool parity rule: [tool] ...` problems (from `eva.parity_violations`). `validate` therefore
  exits 1 and every `--live` gate refuses, like the English rule.
- `validate` prints one status line `multi-tool parity: claude=pass codex=pass opencode=fail hermes=pass` plus a
  `review:` line per needs-review finding. With review findings and no recorded decision it ends with `RULE REVIEW NOT
  RECORDED` and exits 3 (section 12).
- `plan` prints the same parity line and a `tool coverage` block: for each of the four tools, `claude`/`codex` are
  `planned (engine claude)` when selected and `not-run: engine not selected` otherwise; `opencode` and `hermes` are
  `not-run: engine not available`.
- `loop`: the builder instruction gets `eva.PARITY_BUILDER_RULE` (all four tools stay loadable, listed constraints).
  After every builder call, whatever its return code (and also when it raised; round 1, section 12), the code first runs the
  English gate (`builder-non-english`), then `eva.parity_violations(skill_dir)`, then the review gate
  (`builder-review-required`); any finding stops the loop with
  `{"status": "builder-parity-missing", "iteration": n, "problems": [...], "returncode": rc}` before the skill is
  reloaded or another iteration is run. Code, not prompt text. English findings and FAIL-level parity findings in the
  starting skill also block a run. `load_eval_set` adds each one as a validation problem, so `validate` exits 1 and
  every `--live` gate refuses before any spend, `loop --live` and its per-iteration precheck included.
- Engines: `ENGINES` stays claude and codex. `make_engines` refuses `opencode` and `hermes` explicitly:
  `engine 'opencode' is not available: no OpenCode engine exists yet ...` (exit 2). Nothing fakes a run.
- `benchmark.json` gets `tool_coverage`: per tool `{"status": "run"|"not-run: engine not selected"|"not-run: engine not
  available"|"not-run: no graded runs"|"pending: runs collected, none graded yet", "graded_runs": n, "pending_runs": m}` and
  `parity` (static status at report time). `run` means at least one run is graded (the `summarize` criterion: `pass_rate` is
  not null), not "passed"; collected `grading.json` files with a null `pass_rate` are `pending`. `comparison.md` gets a `Tool coverage` table. `plan.json` records
  `tool_coverage` at run start.
- Gate and registration: `compute_gate` adds a NOTE (not a problem) `per-tool dynamic coverage: run on ...; NOT run on
  ...`. `gate["problems"]`, `needs_approval`, `register`, `approve`, hash inputs and the registration record are
  unchanged. Reason: OpenCode and Hermes engines do not exist, so a blocking rule would force a manual approval on every
  registration, which tells the owner nothing. When the engines exist, the follow-up turns the note into a problem.
  The registered csv-cleaner (iteration-4, `registration.json`) is untouched; its files are not rewritten (nothing re-ran
  `report`). If someone re-runs `report` on it later, the new `benchmark.json` carries `tool_coverage` and the note; the
  benchmark hash changes then, exactly as it already does on any re-run (the timestamp is part of the file).

## 6. Index and hub

`eva.py index` registry entries gain `english` (`pass`/`fail`) and `parity` (per-tool status) fields, and INDEX.md gains
two columns, so a static hub page can draw badges without re-running checks. The hub itself is design only (README
section); nothing is published, pushed or fetched.

## 7. What is checked per tool (summary)

- Claude Code: SKILL.md fields and limits; agent wrapper frontmatter, name, description, tools capabilities.
- Codex CLI: SKILL.md fields; `agents/openai.yaml` policy shape; agent TOML keys, name, description, no `sandbox_mode`
  (it has no effect in a custom agent file); a REVIEW for each of write, shell, web_fetch, web_search, delegate, skill
  that AGENT.md lacks.
- OpenCode: name regex and folder name, string metadata, autoinvoke value; agent frontmatter, file stem, description,
  every recorded permission key set, no unknown key, `external_directory` denied, derived classes vs capabilities; a
  REVIEW on a match while the key list is not verified complete.
- Hermes Agent: SKILL.md fields, no `metadata.hermes` mapping; SOUL.md existence, name and description text.

## 8. What is NOT verifiable offline

Nothing below was run; no tool binary was called.

- That any of the four tools actually loads the skill or the wrapper. Static checks only prove the file shapes in
  design.md. The OpenCode and Hermes engines do not exist, so no dynamic run exists for them.
- Codex: the full `agents/openai.yaml` schema (only `policy.allow_implicit_invocation` is known); a top-level Codex
  session as a custom agent (design: UNVERIFIED); the Codex plugin manifest; that `sandbox_mode` in a custom agent file
  has no effect on codex-cli 0.162.1 (the Codex doc says so; the plan C2 operator probe has not run as of 2026-10-19);
  whether `features.shell_tool = false` in a custom agent file stops its shell (plan A3 reads that probe).
- Claude Code: reading `.agents/skills` (UNVERIFIED); what an omitted `tools` list grants.
- OpenCode: per-command bash permission patterns are supported by the tool but not by this strict reader; the npm plugin
  route (UNVERIFIED); the full permission key list and the default of a key a wrapper leaves unset (the recorded list of
  twelve ends in "...", 2.4).
- Hermes: everything runtime. Not installed on this machine. Per-agent profile layout, `config.yaml` schema, a
  string-safe form of `metadata.hermes`, per-command shell permissions, a `disable-model-invocation` equivalent: all
  UNVERIFIED. Version pins for the company install are unknown.
- Tool versions drift: Codex says its agent format "may evolve" (design section 5).

## 9. Golden impact

See section 11 (measured after implementation). `golden/` is not edited and not exempted.

## 10. Follow-ups

1. OpenCode engine and Hermes engine (design stages 7 and 8). Then flip the dynamic-coverage note into a gate problem.
2. Verify the UNVERIFIED items of section 8 on real installs (Hermes at the company, Codex top-level agent, Codex
   `openai.yaml` schema), then widen the readers or the permission mapping.
3. TOML tables in the reader if a Codex wrapper needs `mcp_servers` or `skills.config`.
4. A generator that writes the four wrappers from AGENT.md, so wrappers cannot drift (the checks only detect drift).
5. Per-command bash permissions for OpenCode (nested mapping) in the strict frontmatter reader. Until then a pattern map
   fails the wrapper (2.5).
6. Decide what to do with the golden units: add wrappers and translate content (owner decision, changes hashes and
   approvals), re-run `eva.py index golden`.
7. Extend `CAPABILITY_BY_TOOL` when an agent uses a tool name that is not mapped (the check fails by design).
8. Verify the full OpenCode permission key list (plan Appendix E, Q24), add any missing key to
   `eva.OPENCODE_PERMISSION_GRANTS`, and only then set `eva.OPENCODE_PERMISSION_KEYS_COMPLETE` to true.

## 11. Results

Measured after implementation, nothing committed, no tool binary run, `golden/` not edited (`git status golden` shows only
the untracked files that existed before this change set).

Test suite (as first implemented): `python3 -m unittest discover -s tests` printed `Ran 760 tests` and `OK` (baseline 671, so
+89: all in the new `tests/test_multi_tool_parity.py`; the existing tests changed in count by 0). After Codex review round 1
the count is in section 12.

Golden impact, `python3 eva.py check <unit>` (exit code 1 = FAIL for all five; english failures are the previous change
set, the `parity-*` failures are new):

| unit | verdict | failing checks | parity rows |
|---|---|---|---|
| `golden/skills/csv-cleaner` | FAIL (0.94) | `english-skill-md`, `english-evals` | all pass (invocation rows n/a) |
| `golden/skills/korean-proofreader` | FAIL (0.93) | `english-skill-md`, `english-evals` | all pass |
| `golden/skills/pdf-summarizer` | FAIL (0.90) | `english-skill-md`, `english-bundled-files`, `english-evals` | all pass |
| `golden/agents/code-reviewer` | FAIL (0.68) | `english-agent-md`, `english-scenarios`, `parity-wrapper-claude`, `parity-wrapper-codex`, `parity-wrapper-opencode`, `parity-wrapper-hermes`, `parity-permissions-hermes` | name pass, 4 wrapper rows fail, Claude/Codex/OpenCode permissions n/a (no wrapper to read), Hermes permissions fail (fail closed, round 1) |
| `golden/agents/research-assistant` | FAIL (0.68) | same seven ids | same |

Per-tool status of every golden skill: `claude=pass codex=pass opencode=pass hermes=pass`. Every golden agent:
`fail` for all four tools (no `wrappers/`). Every other row of every golden unit passes and none is needs-review
(asserted in `tests/test_english_rule.py::TestIndexAndGolden::test_golden_units_fail_exactly_on_these_checks`;
C4 removed the weaker `tests/test_eva.py::TestGolden`). The agent scores dropped from 0.88 to 0.71 because the four
failed wrapper rows count in the pass rate, and to 0.68 after round 1 because the fail-closed Hermes row counts too;
that does not change the verdict (already FAIL).

`python3 skill_eval.py validate golden/skills/csv-cleaner` still exits 1 with only `english-only rule:` problems (47 since
C4: the 44 eval-set findings plus 3 in `SKILL.md`, because `validate` now runs the full folder scan) and
now prints `multi-tool parity: claude=pass codex=pass opencode=pass hermes=pass`; it has zero `multi-tool parity rule:`
problems. So the registered csv-cleaner is affected by the English rule only, not by this one. The registration,
`approvals.json` and iteration files were not touched or re-hashed. `python3 skill_eval.py plan golden/skills/csv-cleaner
--engines opencode` exits 2 with `engine 'opencode' (OpenCode) is not available: ... (never faked)`.

Existing tests changed (no golden exemption anywhere):

- `tests/test_eva.py`: new helper `minus_parity`. `TestGolden`, which C4 later removed, expected english failures for
  every unit plus `parity-wrapper-*` for the agents only. The exact-set test
  `tests/test_english_rule.py::TestIndexAndGolden::test_golden_units_fail_exactly_on_these_checks` now pins the exact
  failing rows of each golden unit. `test_zero_pass_category_fails_unit` and `test_static_criteria` read the other
  checks through `minus_parity` (the `bad/` and golden agents have no wrappers). `test_reserved_name_words` and
  `test_description_no_xml` expect the Claude parity row next to the base row (one cause, two rows, by design).
  `test_host_fields_allowed` now expects `parity-invocation-control` to fail for `disable-model-invocation: true` alone
  and a PASS for `argument-hint` alone.
- `tests/test_english_rule.py`: the fixture agent gets generated wrappers (`write_wrappers`); the golden list gains the four
  `parity-wrapper-*` ids for the two agents.
- New: `tests/test_multi_tool_parity.py` (89 tests): TOML reader edge cases, per-tool skill positive and negative cases,
  invocation control, agent wrappers missing, unparsable and inconsistent, permission mapping, English on wrappers and
  `openai.yaml`, index fields, golden, validate and plan output, engines fail closed, builder gate with a mock builder,
  benchmark and report coverage records, gate note without a gate problem, registration code untouched.

Not touched: `loop.py` and `LOOP.md` (outside the allowed edit list). `loop.py` calls `eva.check`, so its builder now has
to reach a skill that passes the parity rows too; it needed no change.

Side effects worth knowing:

- A metadata value that YAML types as a non-string (`version: 1.0`) now fails `parity-skill-opencode`, although
  `field-types` still accepts it (skills-ref reads every scalar as a string). The `version-format` rule already requires
  `x.y.z`, which is a string, so repo skills are not affected.
- `eva.py index` registry entries and `INDEX.md` gained the `english` and `parity` fields/columns. Nothing re-indexed
  `golden/`.

## 12. Codex review round 1 (2026-10-19): changes and policy choices

Rule used: where a fix needed a choice, the stricter option won (false FAIL or REVIEW over false PASS) and nothing was
weakened. Tests: `tests/test_review_round1.py`. The English-only side of the same review is in
`notes/english-rule-plan.md` section 11.

- **Hermes permissions fail closed (P1-2).** `parity-permissions-hermes` is a required FAIL for every agent, with the
  reason "no verified per-agent tool-permission format exists (UNVERIFIED, design 2.4)". It replaces the `n/a` row, so
  `hermes=pass` can no longer be reported for an agent, and every agent has verdict FAIL until someone verifies a Hermes
  format and implements the comparison in `add_parity_checks_agent`. The owner-visible consequence is deliberate: no agent
  reaches PASS yet. The other three tools are judged on their own rows.
- **Tools compared by name (P2-2).** The Claude wrapper's `tools` names must equal the AGENT.md tool names through
  `TOOL_ALIASES`. OpenCode wrappers cannot name tools, only permission keys, so they stay at class level and their
  reasons say so; this is a known limit, not an equivalence claim. Plan C3 widened that comparison from `edit`, `bash`
  and `webfetch` to every recorded key (section 2.5). Round 1 also compared the Codex wrapper's
  `sandbox_mode` at class level. Plan C2 removed that comparison: the key has no effect in a custom agent file, and the
  wrapper must not set it (section 2.5).
- **Unverified Codex metadata is a review (parity-1).** `agents/openai.yaml` with any field other than
  `policy.allow_implicit_invocation` (for example `interface.display_name`) makes `parity-skill-codex` needs-review
  (REVIEW, `codex=review`), and the reason no longer says the Codex rules are satisfied. A real problem (unparsable file,
  malformed policy) is still a FAIL.
- **Review decisions (P1-3).** Every needs-review finding (English output matchers, unverified Codex fields, Hermes
  invocation control, and since plan C2 the Codex agent permission row) is listed by `eva.review_items`.
  `skill_eval.py validate` exits 3 until `approve --gate rule_review` records a decision; `check_live_gates` (so `run`, `grade`, `calibrate`, `loop` and the loop precheck) refuses with
  `rule review not recorded`; `loop` stops with `builder-review-required` after a builder call. The decision is the new
  `rule_review` key in `approvals.json`, bound to a SHA-256 of the exact finding texts and of the bytes of the files the
  review rows read (C1 owner review, round 2; per unit kind since C4). The files come from one table,
  `eva.REVIEW_SOURCES`, derived from the row producers: for a skill `evals/evals.json`, `evals/trigger_evals.json` and
  `agents/openai.yaml`; for an agent `AGENT.md` and every file under `scenarios/`, `wrappers/codex/` and
  `wrappers/opencode/`. The Claude and Hermes wrappers feed no review row. `SKILL.md` stays out: it only turns
  `parity-invocation-hermes` (`disable-model-invocation`) and `parity-skill-codex` (name and description present) on or
  off, which changes the findings themselves, so a builder edit keeps the decision. Existing approval keys,
  `registration.json` and iteration folders keep their meaning.
- **Builder exit code (P2-3).** The English, parity and review gates run after every builder call; the result keeps
  `returncode` and `error`. `builder-failed` is returned only when the failed builder left no finding.
- **Coverage (reporting-3).** `tool_coverage` counts a run as graded only when its `pass_rate` is not null (the `summarize`
  criterion); collected-but-ungraded runs give `pending: runs collected, none graded yet` with a `pending_runs` count.
  `completeness.graded_runs` in `build_benchmark` (an older field that counts collected `grading.json` files for the
  "incomplete" gate problem) was not part of this change set and is unchanged.
- **Index (reporting-4).** `english=review` for a unit whose only language finding is a matcher review.

Results after round 1 (measured): `python3 -m unittest discover -s tests` printed `Ran 812 tests` and `OK` (760 before; +52
in the new `tests/test_review_round1.py`; existing tests were adjusted, not removed). `python3 eva.py check <unit>` exits 1
(FAIL) for all five golden units. The golden agents now fail seven checks each (`english-agent-md`, `english-scenarios`,
four `parity-wrapper-*`, `parity-permissions-hermes`) at score 0.68; the golden skills fail only `english-*` checks.
