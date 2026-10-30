# Rules

`eva.py check` grades a skill or an agent by running a rule bundle. This page describes the rule files and every rule, how a bundle loads and hashes, the English-only rule, multi-tool parity, scoring, and the fixtures that test every rule. The code lives in `engine/rule_runner.py`, `engine/primitives/` and `eva.py`.

## Principles

- A published specification decides the format. Fields, lengths, names and the folder layout follow the agentskills.io specification, its reference validator `skills-ref` and skill-creator's `quick_validate.py`. A rule stricter than its source is marked as a house rule (H) in the table below.
- Evaluation is eval-driven. A skill carries its eval set (`evals/evals.json`) first, then runs with and without the skill (or against its older version), and the results drive the next change.
- A failing test is never fixed by loosening a check. The code or the fixture changes to meet the standard. A check may grow stricter to match a specification.
- Nothing unverified is guessed. A fact that could not be checked is marked UNVERIFIED, and a feature that does not exist is listed under [Not implemented](#not-implemented).

## Rule files

A bundle is a folder. Its top-level `rules.json` and `*.rules.json` files are rule files. Every other file in it, such as the `data/` files, is hashed but not loaded as rules. Some primitives read their data file from the bundle in use (`data/scripts.json`, `data/description.json`), so a bundle given with `--rules` needs its own copy. `rules/data/capabilities.json` is always read from the repository. The default bundle is `rules/`:

| File | Rules | Applies to | What it checks |
|---|---|---|---|
| `skill-structure.rules.json` | 14 | skill | frontmatter, fields, compatibility length, size, sections, file references, version format, number of eval cases |
| `skill-description.rules.json` | 7 | skill (`description-present` also agent) | the description: presence, length, `<` and `>`, when-to-use clause, point of view, single purpose |
| `naming.rules.json` | 3 | skill, agent (`name-reserved` skill only) | name format, name equals the folder name, reserved words |
| `skill-frontmatter.rules.json` | 1 | skill | frontmatter field types |
| `skill-evals.rules.json` | 1 | skill | `evals/evals.json` shape and `skill_name` |
| `agent-structure.rules.json` | 13 | agent | AGENT.md v2 frontmatter, goal, success criteria, scenarios, capabilities, skill pins, sections |
| `english-only.rules.json` | 9 | skill, agent, repository | English-only text |
| `parity-skill.rules.json` | 7 | skill | multi-tool parity of a skill |
| `parity-agent.rules.json` | 10 | agent | multi-tool parity of an agent and its wrappers |

A skill eval case can run one rule file of the default bundle over one output file with the static check `rule_check` ([Eval set format](eval-set-format.md)).

`schemas/rules.schema.json` validates every rule file. A file holds `ruleset` (a lowercase name), `version` (`MAJOR.MINOR.PATCH`) and a non-empty `rules` list. Each rule has these keys:

- `id`: a lowercase name. Two rules may share an id only when their `applies_to` kinds are disjoint and neither is a `repository` rule.
- `applies_to`: `skill`, `agent` or both, or exactly `["repository"]`.
- `category`: `structure`, `single-task`, `io`, `evaluation`, `language`, `parity`, `goal`, `tool-use` or `context`.
- `severity`: `fail`, `soft` or `review`.
- `protects`: the tools the rule protects, from `claude`, `codex`, `opencode` and `hermes`.
- `source`: `{"url", "section", "verified"}` for a documented rule, or `{"house"}` with an optional `verified` date for a house rule.
- `check`: one op node.
- `message`: one string, or an object keyed by outcome (`pass`, `fail`, `review`, `na`). `fix`: one string, or an object keyed by `fail` and `review`. Both render op facts written as `{key}`.
- `na_must` (optional): `false` gives an `n/a` row that is not must.

A check node names one op from the closed op table of `rule_runner.OPS`. The generic ops are `schema`, `equals`, `subset`, `set_equals`, `contains_text`, `regex`, `not_regex`, `file_exists`, `file_absent`, `count`, `max_bytes`, `max_lines`, `all`, `any`, `not` and `when`. The primitives are `script_denylist`, `parity_skill`, `parity_invocation`, `parity_agent_name`, `parity_wrapper`, `parity_permissions`, `heading_present`, `description_heuristics`, `local_refs`, `agent_frontmatter` and `generated_up_to_date`. The host ops (`host_frontmatter`, `host_frontmatter_fields`, `host_field_length`, `host_main_file_size`, `host_metadata_version`, `host_evals_count`, `host_tools_declared`, `host_success_measurable` and `host_scenarios`) run callables that `eva.py` installs. Operands read the unit through selectors: `frontmatter:<pointer>`, `json:<relpath>#<pointer>`, `toml:<relpath>#<pointer>`, `yaml:<relpath>#<pointer>`, `markdown:<relpath>`, `text:<relpath>`, `glob:<pattern>` and `path:basename`. A `{key}` in a relpath is filled from a top-level frontmatter string. The `normalize` list takes `strip`, `nfkc`, `lower` and `string-or-empty`, and `string-or-empty` must come first.

## All rules

`[required]` marks a rule of severity `fail`: its row is must, so a failure makes the verdict FAIL whatever the score. `[needs-review]` marks a rule of severity `review`: a failing outcome becomes a needs-review row. A rule without a marker has severity `soft`: it counts in the score only. A must rule that does not apply gives an `n/a` row.

The source codes name what the rule follows. S is the agentskills.io specification, V the `skills-ref` and `quick_validate` validators, B Anthropic's skill authoring best practices, C the skill-creator and agentskills.io eval documents, T the tool's own documentation that the rule's `source.url` names, and H a house rule: stricter than its source or absent from it.

| Rule | Applies to | Category | Checks | Source |
|---|---|---|---|---|
| `frontmatter` [required] | skill, agent | structure | the main file opens with YAML frontmatter between `---` lines | S (skill), H (agent) |
| `frontmatter-fields` [required] | skill | structure | only the six spec fields `name`, `description`, `license`, `compatibility`, `metadata` and `allowed-tools`, plus the Claude Code host fields `argument-hint` and `disable-model-invocation`, which skills-ref and quick_validate reject | S, V, H |
| `field-types` [required] | skill | structure | field types per `schemas/skill-frontmatter.schema.json`, including YAML values that are not strings | S, V, H |
| `compatibility-length` [required] | skill | structure | `compatibility`, when present, has 1 to 500 characters | S, V |
| `name-format` [required] | skill, agent | single-task | `name` after NFKC: lowercase letters, digits and `-`, no leading, trailing or double `-`, at most 64 characters | S, V |
| `name-matches-folder` [required] | skill, agent | structure | `name` equals the folder name after NFKC | S, V |
| `name-reserved` [required] | skill | single-task | `name` holds neither `anthropic` nor `claude` | B |
| `description-present` [required] | skill, agent | single-task | a non-empty `description` | S |
| `description-length` [required] | skill | single-task | `description` has 1 to 1024 characters | S, B |
| `description-min-length` | skill | single-task | `description` has at least 20 characters | H |
| `description-no-xml` [required] | skill | single-task | no `<` or `>` in `description` | B, V |
| `description-when` [required] | skill | single-task | an English trigger clause outside quotes, such as "Use when ...", with a concrete condition | S, H |
| `description-pov` [required] | skill | single-task | no first- or second-person words in `description` | B |
| `single-purpose` | skill | single-task | at most `SINGLE_PURPOSE_MAX_VERBS` distinct task verbs and no add-on phrase in `description` | H |
| `inputs-declared` | skill | io | an Inputs heading in the body | H |
| `outputs-declared` | skill | io | an Outputs heading in the body | H |
| `example-present` | skill | io | an Example heading in the body | H |
| `size-limit` | skill | structure | `SKILL.md` has fewer than 500 lines and at most 20000 bytes, a byte proxy for the 5000-token body budget | S, B, H |
| `refs-relative` | skill | structure | file references are forward-slash paths relative to the skill root | S |
| `refs-exist` [required] | skill | structure | every referenced file exists inside the skill folder; a reference found only from the enclosing git repository root gives needs-review | H |
| `refs-one-level` | skill | structure | a file linked from `SKILL.md` links nothing that `SKILL.md` does not | S |
| `refs-path-depth` | skill | structure | references sit at most one folder deep, as in `references/x.md` | H |
| `reference-toc` | skill | structure | a referenced `.md` file over 100 lines starts with a table of contents | B |
| `version-format` [required] | skill | structure | `metadata.version`, when present, is `x.y.z` | H |
| `evals-valid` [required] | skill | evaluation | `evals/evals.json` follows `schemas/evals.schema.json` and its `skill_name` equals the frontmatter `name` | C, H |
| `evals-count` [required] | skill | evaluation | at least 3 eval cases | B, C, H |
| `agent-frontmatter-v2` [required] | agent | structure | AGENT.md v2 frontmatter per `schemas/agent-frontmatter.schema.json` | H |
| `goal-present` [required] | agent | goal | a `goal` of at least 15 characters | H |
| `success-measurable` [required] | agent | goal | at least one typed success criterion that is not `llm` | H |
| `scenarios-present` | agent | goal | at least 2 valid scenarios in `scenarios/*.json` | H |
| `scenarios-valid` [required] | agent | goal | every scenario follows `schemas/scenario.schema.json` | H |
| `scenarios-expected` | agent | goal | every scenario has an `expected_outcome` | H |
| `tools-declared` [required] | agent | tool-use | the frontmatter declares its `capabilities` | H |
| `skills-need-skill-capability` [required] | agent | tool-use | an AGENT.md `skills` key (pinned registered skills) needs `skill` in the capabilities; n/a without the key | H |
| `tools-subset` | agent | tool-use | every scenario's `expected_tools` are among the capabilities | H |
| `tools-documented` | agent | tool-use | a Tools section explains the tools | H |
| `memory-section` | agent | context | a memory, handoff or context section | H |
| `multi-turn-scenario` | agent | context | one scenario with two or more turns | H |
| `english-skill-md` [required] | skill | language | `SKILL.md`, frontmatter and body | H |
| `english-bundled-files` [required] | skill | language | every installed file other than `SKILL.md` and `agents/openai.yaml`, contents and names; `evals/`, `__pycache__`, `*-workspace`, `.git`, `FEEDBACK.md` and `FEEDBACK.json` are ignored | H |
| `english-evals` [required] | skill | language | `evals/evals.json` and `evals/trigger_evals.json`, except the run-output matchers | H |
| `english-tool-files` [required] | skill | language | `agents/openai.yaml` | H |
| `english-agent-md` [required] | agent | language | `AGENT.md`, frontmatter and body | H |
| `english-scenarios` [required] | agent | language | `scenarios/`, JSON contents and file names, except `must_include` | H |
| `english-wrappers` [required] | agent | language | every file under `wrappers/` | H |
| `english-output-matchers` [needs-review] | skill, agent | language | the run-output matchers that `english-evals` and `english-scenarios` skip | H |
| `english-ruleset` [required] | repository | language | `rules/`, `schemas/`, `targets/` and `policy/`, at bundle load | H |
| `parity-skill-claude` [required] | skill | parity | the frontmatter parses with a `name` and a `description`; no reserved word, no name or description over the limits of `targets/claude.json`, no `<` or `>` | B, H |
| `parity-skill-opencode` [required] | skill | parity | `name` matches `^[a-z0-9]+(-[a-z0-9]+)*$` and equals the folder name exactly; every `metadata` value is a string; `metadata.opencode/autoinvoke`, when present, is `"true"` or `"false"` | H |
| `parity-skill-hermes` [required] | skill | parity | the frontmatter parses with a `name` and a `description`; no `metadata.hermes` mapping or list | H |
| `parity-invocation-control` [required] | skill | parity | `disable-model-invocation: true` comes with the Codex and OpenCode controls; `n/a` when the key is absent or false | H |
| `parity-invocation-hermes` [needs-review] | skill | parity | `disable-model-invocation: true` has no verified Hermes equivalent; `n/a` when the key is absent or false | H |
| `tool-metadata-generated` [required] | skill | parity | `agents/openai.yaml` and the `metadata.opencode/autoinvoke` line equal what `eva.py generate` renders | H |
| `parity-agent-name` [required] | agent | parity | the agent name matches `^[a-z0-9]+(-[a-z0-9]+)*$` | H |
| `parity-wrapper-claude` [required] | agent | parity | `wrappers/claude/<name>.md` is present, parseable and consistent with AGENT.md | T |
| `parity-wrapper-opencode` [required] | agent | parity | `wrappers/opencode/<name>.md` is present, parseable and consistent with AGENT.md | T |
| `parity-wrapper-hermes` [required] | agent | parity | `wrappers/hermes/SOUL.md` is present, parseable and consistent with AGENT.md | T |
| `parity-permissions-claude` [required] | agent | parity | the Claude wrapper grants exactly the AGENT.md capabilities | T |
| `parity-permissions-opencode` [required] | agent | parity | the OpenCode wrapper grants exactly the AGENT.md capabilities; a match still gives needs-review (G3) | T |
| `parity-permissions-hermes` [required] | agent | parity | fails for every agent (G4) | T |
| `wrappers-generated` [required] | agent | parity | the four wrappers equal what `eva.py generate` renders | H |

## Families

`eva.FAMILY_FILES` lists the nine rule files of the default bundle. `eva.check_skill` and `eva.check_agent` add their rows through `rule_runner.apply_families`. Each family file comes from the bundle in use when that bundle holds the file, and from the default bundle otherwise, so a bundle given with `--rules` never removes family rows. `eva.check` then applies the other rule files of the bundle in use.

Five rows keep a reason that `eva.py` computes: `evals-valid`, `field-types`, `name-format`, `name-matches-folder` and `name-reserved` (`eva.SHAPE_ROWS`). The rule still decides the status, and the row is must. When the rule and `eva.py` disagree on pass, the row fails with `rule and eva.py disagree`.

## Loading a bundle

`rule_runner.load_bundle` loads the default bundle, or the folder that `--rules DIR` names. Any of these stops the whole load with a `BundleError`, so `check`, `check-all`, `feedback` and `index` print `bundle error: ...` and exit 2 before any unit row:

- a `--rules` path that is not a folder;
- a rule file that is not JSON or fails `schemas/rules.schema.json`;
- an unknown op or an unknown selector, a `string-or-empty` that is not first in `normalize`, or a `schema` op that names a file that is not in `schemas/`;
- a literal path that leaves the unit;
- a host op that `eva.py` did not install;
- a data file that a rule names and that is missing or malformed, or a broken `targets/*.json`;
- a duplicate rule id, or the reserved id `bundle-non-default`;
- a `repository` rule that is not a `script_denylist` group check, a `review_of` that names no group rule, or a `repository` rule that fails, such as `english-ruleset`;
- a link or a special file in a hashed tree, or a file that cannot be read.

`rule_runner.RESERVED_OPS` is empty, so no op is reserved today.

A bundle given with `--rules` adds the needs-review row `bundle-non-default` to every report, so the verdict is never PASS. `check-all` without `--json` then prints `bundle non-default DIR`. `--rules` cannot be combined with `--transcript`.

## Check one file against a schema

`python3 eva.py schema-check SCHEMA INSTANCE` validates a JSON file with the validator of `engine/schema.py`, the one the rule runner uses. For example, `python3 eva.py schema-check schemas/rules.schema.json rules/naming.rules.json` prints `schema-check: valid` and exits 0. It exits 1 for an invalid instance and 2 for a refused schema or an unreadable file.

The validator supports a subset of JSON Schema 2020-12 and fails closed. It refuses a schema with an unsupported keyword, a malformed keyword value, a non-local or dangling `$ref`, `then` or `else` without `if`, or an empty `enum`. It adds these keywords:

- `x-python-regex`: the string compiles with Python `re`.
- `x-unique-by`: object items with that key have distinct values.
- `x-count-where`: the number of items valid under `where` is between `min` and `max`.
- `x-le`: `[a, b]` holds `a <= b` when both are numbers.
- `x-path-exists`: a relative path to a regular file inside the unit that `--unit DIR` names. Without `--unit` it fails closed.
- `x-yaml-string`: the frontmatter scalar is not typed by YAML 1.1. It needs the typed-key annotation that the rule runner passes, so `schema-check` reports an error on every string it reaches (fail closed).

## ruleset_sha256

`check` and `check-all` print `ruleset_sha256 <hex>` after the rows, and `--json` adds a `ruleset_sha256` key. The hash is `rule_runner.sha256_labeled` over the sorted `(label, path)` pairs of `rule_runner.ruleset_pairs`:

- every file of the bundle folder, labeled `rules/...`;
- every file of `schemas/` and `targets/` (`rule_runner.HASHED_TREES`);
- `policy/eval-policy.json`, `policy/gates.json` and `policy/gate.rules.json` (`rule_runner.POLICY_FILES`), hashed as `<missing>` when absent.

Each label is hashed with the file's bytes. Labels are relative, so one bundle hashes the same in any folder. The hash moves when any byte of a hashed file changes, and when a file in a hashed tree is added, removed or renamed. It does not move for `eva.py`, `skill_eval.py`, `engine/`, `tests/` or the other `policy/` files, such as `policy/auth-profiles.json` and `policy/install.json`. With `--rules DIR`, the files of DIR take the place of `rules/` in the hash.

`skill_eval.ruleset_sha256` computes the same hash on every call. Every approval record binds it, so a change of the hash makes existing approvals read stale ([Approval gates](approval-gates.md)). The loop stops with `rules-changed` when a builder call moves it, and `drift` lists units recorded under an older hash ([Install, versions and drift](install.md)).

## The English-only rule

All skill and agent content is English only (house rule of 2026-10-19 in `rules/english-only.rules.json`). The nine `english-*` rows in [All rules](#all-rules) name what each one scans. The `script_denylist` primitive (`engine/primitives/script_denylist.py`) classifies each character by its Unicode category and name with the word lists and code-point ranges of `rules/data/scripts.json`:

- ASCII, punctuation (category P), separators (Z) and control and format characters (C) are never non-English. The exception is a code point that this Python's Unicode database does not assign (Cn), which is non-English.
- Any other character is non-English when its code point is in `non_english_ranges` or its name holds a word of `non_english_script_words` (for example `HANGUL`, `CJK`, `CYRILLIC`).
- A letter, mark or number whose first name word is not in `english_compatible_first_words` is non-English as well. This fails closed. Greek stays allowed for math and unit notation.
- The scanner detects scripts, not languages. German prose in Latin letters passes.

The scanner decodes escapes before it classifies: JSON escapes always, YAML double-quoted scalars and TOML basic strings. Text that decodes as UTF-8, or as UTF-16 with a byte order mark, is always scanned. Only bytes that do not decode are skipped as binary, and only when they start with a signature from the data file (or a RIFF or `ftyp` container) or carry a binary extension from it. Anything else fails, because it cannot be verified.

Run-output matchers are `check.pattern`, `check.input_regex` and `check.rows` in the eval set, and `must_include` in scenarios. Non-English text there gives the needs-review row `english-output-matchers`, not a FAIL, because a run may have to produce non-English output. Task input data that must stay non-English belongs in `evals/files/`, which the English-only rules do not scan. A failing `english-ruleset` is a bundle error.

## Multi-tool parity

A unit is built for Claude Code, OpenCode and Hermes Agent together. `engine/primitives/parity.py` checks it against one target file per tool, `targets/<tool>.json`, which holds the tool's skill limits, invocation control, wrapper path and format, permission model, model mapping and render template. The `parity-*`, `tool-metadata-generated` and `wrappers-generated` rows in [All rules](#all-rules) apply it.

Each tool's permission row has its own limit:

- Claude Code compares the wrapper `tools` by capability class.
- OpenCode (gates.md G3): the recorded permission key list stays `complete: false` in `targets/opencode.json`. A wrapper that grants exactly the needed capabilities still gets needs-review, never PASS.
- Hermes Agent (gates.md G4): no verified per-agent permission format exists, so `targets/hermes.json` sets `fixed: fail`. `parity-permissions-hermes` fails for every agent. The rule has severity `fail`, so the row is must and every agent's verdict is FAIL under the default bundle. `eva.py generate` writes only `SOUL.md` for Hermes, because the `config.yaml` keys are UNVERIFIED.

Only `claude` and `codex` have an engine in `skill_eval.py`. Registration therefore needs recorded coverage waivers for OpenCode and Hermes ([Approval gates](approval-gates.md)).

## Scoring and the category floor

`eva.Report` collects one row per check. A row's status is `pass`, `fail`, `n/a` or `needs-review`.

- A rule row is must unless the rule's severity is `soft`. The ids in `eva.CRITICAL` are must in every case.
- A `review` severity turns a `fail` outcome into a needs-review row.
- `n/a` and needs-review rows are left out of the score and the categories.
- The score is the passing rows divided by the rows that apply, rounded to 3 places.
- A category fails when it has no passing row or a pass rate below `CATEGORY_FLOOR` (0.5).

The verdict is FAIL when no row applies, the score is below `PASS_THRESHOLD` (0.8), a must row fails, or a category fails. Otherwise it is REVIEW while a needs-review row remains, and PASS when none does. `EXIT_CODES` maps PASS to 0, FAIL to 1 and REVIEW to 3. The JSON report holds `path`, `kind`, `score`, `threshold`, `category_floor`, `categories`, `category_failed`, `needs_review`, `verdict` and `checks`.

## Rule fixtures and the selftest

`tests/test_rule_fixtures.py` checks that every rule of the default bundle has fixtures and that every fixture gives the expected status. The layout is `tests/rules/<rule id>/<side>/<unit>/`, with one `expected.json` per side that maps each unit folder name to its status:

| Side | Expected status |
|---|---|
| `pass/` | `pass` |
| `fail/` | `fail`, or `needs-review` for a rule of severity `review` |
| `review/` | `needs-review` |
| `na/` | `n/a` |

Every rule needs `pass/` and `fail/`, plus any extra side listed in the test's `EXTRA_SIDES`. The test's `OUTCOMES` table names the rules that cannot reach both pass and fail:

- `english-output-matchers` and `parity-invocation-hermes` need `review/` and `na/`.
- `parity-permissions-opencode` needs `fail/`, `review/` and `na/` (G3).
- `parity-permissions-hermes` needs only `fail/` (G4).

The `NO_SIDE` table names reachable outcomes that other tests build in a temp folder instead. A fixture of a `repository` rule is a small repository root with `rules/`, `schemas/`, `targets/` and `policy/`. A fixture folder with no rule of that id, a missing side, a side without a unit, a unit missing from `expected.json`, and a wrong status each fail the test with a line that names the rule id or the folder.

`tests/rules/_selftest/` is a bundle of one rule, `selftest-name-matches-folder`, with one pass and one fail fixture. The test loads it with `load_bundle`, checks that it holds exactly its five files, and plants gaps in a temp copy to prove that the fixture check finds each one. The default bundle check skips folders whose names start with `_`.

Run the fixture test with `python3 -m unittest discover -s tests -p test_rule_fixtures.py`.

## Sources

The checks were designed from these documents:

- agentskills.io specification: https://agentskills.io/specification
- agentskills.io, evaluating skill output: https://agentskills.io/skill-creation/evaluating-skills.md
- agentskills.io, optimizing descriptions (trigger evals): https://agentskills.io/skill-creation/optimizing-descriptions.md
- the skills-ref validator: https://raw.githubusercontent.com/agentskills/agentskills/main/skills-ref/src/skills_ref/validator.py
- Anthropic skill-creator: https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/SKILL.md
- skill-creator `quick_validate.py`: https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/scripts/quick_validate.py
- Anthropic skill authoring best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- OpenAI evaluation best practices: https://developers.openai.com/api/docs/guides/evaluation-best-practices

A rule's own `source` field names the URL or the house decision it follows, with the date it was verified.

## Not implemented

- Description optimization: skill-creator splits the trigger queries 60/40 into train and validation sets and lets a model propose descriptions. `skill_eval.py` measures trigger rates only and never edits a description.
- A review viewer like skill-creator's `eval-viewer/generate_review.py`. The harness writes `comparison.md` and `human_review.json` only.
- A token budget. No tokenizer is used, so `size-limit` stands in with 20000 bytes for the 5000-token body budget.
