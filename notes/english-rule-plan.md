# English-only rule: plan

Owner decision (pinesky, 2026-10-19 18:38 KST): all agent and skill content is English only, to save tokens.
This document was written before the implementation. Section 8 holds measured output. Section 11 records the Codex review round 1 changes (2026-10-19); where it differs from sections 2 to 7, section 11 wins and the earlier sections were edited to match.

## 1. What the rule is

One shared scanner in `eva.py` decides what counts as non-English text. Two front ends use it.

- Static rule: `python3 eva.py check <unit>` (skills and agents). Required checks, a failure gives verdict FAIL.
- Dynamic rule: `python3 skill_eval.py validate` (and therefore every `--live` gate) plus a code gate in `loop` after every builder call.

The scanner detects scripts, not languages. German or French prose written in Latin letters passes, because the owner
said accented Latin is fine and a language detector would need a dictionary. This is a known limit, not a feature.

## 2. What counts as violating text

A character violates the rule when all of these hold:

1. It is not ASCII.
2. Its Unicode category is not punctuation (P*), separator (Z*) or other/format/control (C*).
3. One of two name tests (`unicodedata.name`; the stdlib has no script property) says it belongs to a non-English writing
   system:
   - a word of the name is in `eva.NON_ENGLISH_SCRIPT_WORDS` (HANGUL, CJK, KANGXI, HIRAGANA, KATAKANA, BOPOMOFO,
     IDEOGRAPH(IC), CYRILLIC, ARABIC, HEBREW, THAI, DEVANAGARI, ... plus CHEROKEE, YI, CANADIAN, OGHAM, RUNIC, COPTIC,
     TIFINAGH, CUNEIFORM, EGYPTIAN and about 150 more, see the constant), or the code point is one of the squared
     katakana words / era names U+3300-3357, U+337B-337F, U+32FF; or
   - fail closed, for letters, marks and numbers only (categories L*, M*, N*): the first word of the name is not in
     `eva.ENGLISH_COMPATIBLE_FIRST_WORDS` (LATIN, GREEK, COMBINING, MODIFIER, the letterlike math symbols, digit and
     numeral forms). A script that is on neither list is therefore flagged; or
   - the code point is unassigned in this Python's Unicode database (category Cn; Unicode 13.0 on Python 3.9). A script
     that a newer Unicode version adds is flagged this way, as `Unassigned` (C1 owner review, round 2).

This covers Hangul syllables, Hangul Jamo (all blocks), compatibility Jamo, halfwidth Hangul, circled and parenthesized
Hangul, the invisible Hangul filler, CJK ideographs (all extensions and compatibility forms), kana, Cherokee, Yi and every
other script of Unicode 13 (the version of the Python 3.9 database). `tests/test_review_round1.py` walks all code points to
prove that every assigned L*/M*/N* character is either flagged or has an English-compatible first word.

Decision: CJK, kana and the other listed scripts also FAIL. Reason: the owner's rule is "English only" and its purpose
is token cost. A Hangul-only rule would let Chinese or Japanese text in at the same cost and would contradict the
rule's own wording. A Latin-letter test would be simpler but would flag Greek letters used as math and unit notation
(`mu`, `Delta`, `pi`), so Greek is deliberately English-compatible.

Allowed (never flagged): accented Latin, Greek, plain Unicode punctuation (curly quotes, dashes, ellipsis, CJK-style
punctuation marks such as the ideographic comma, which are category P*), arrows, math and other symbols that are not
named after a listed script, and emoji. Emoji are not natural-language text and are not scanned for.

Escapes: `\uXXXX` / `\UXXXXXXXX` escapes are decoded before scanning only where the file format interprets them, so
`json.dumps(..., ensure_ascii=True)` output cannot hide Hangul, and ASCII text that only looks like an escape is not
flagged. JSON strings: always. YAML (SKILL.md and AGENT.md frontmatter, `agents/openai.yaml`, every other installed
`.yaml`/`.yml` skill file, `wrappers/**/*.yaml`): in block style, inside double-quoted scalars only, including their
continuation lines and a scalar after a tag or anchor (plain and single-quoted scalars, `|`/`>` block content and
comments keep the backslash; C1 owner review, round 2). A flow collection that opens at a value start (`{` or `[`,
followed across lines until it closes) and a complex-key entry (`?` and the lines indented deeper) are decoded strictly,
everywhere in their text (C5, section 12). TOML files: inside basic strings only (`'\uAC00'` literal strings and comments are not decoded). Markdown
bodies, scripts and plain text: never. `\\uAC00` (an escaped backslash) is never decoded. JSON that does not parse is
scanned strictly (decoded everywhere). See section 11.

## 3. Rule ids, severity, category

All ids use the new category `language`. Every one except the review item is `must=True`, so a failure gives the
unit verdict FAIL regardless of score.

| id | applies to | scans | severity |
|---|---|---|---|
| `english-skill-md` | skill | `SKILL.md`: every frontmatter line (all field values, `description`, `metadata`) and every body line | required, FAIL |
| `english-bundled-files` | skill | every other file a run installs: the skill folder minus `eva.SKILL_IGNORE`, `SKILL.md` and `agents/openai.yaml` (content and relative path; section 12) | required, FAIL |
| `english-evals` | skill | `evals/evals.json`, `evals/trigger_evals.json`: every string except output matchers (section 5, section 11) | required, FAIL |
| `english-agent-md` | agent | `AGENT.md`: frontmatter and body | required, FAIL |
| `english-scenarios` | agent | `scenarios/*.json`: every string except `must_include`, plus the relative path of every file under `scenarios/` | required, FAIL |
| `english-output-matchers` | skill and agent | the exempted matcher strings of section 5 | needs-review when they contain non-English text, otherwise n/a. A recorded decision is required before any live run (section 11) |

`english-skill-md` etc. report `n/a` when the file or folder does not exist (for example no `references/`). Missing
required files are already reported by `frontmatter` / `evals-valid`.

Each failure reason names file and line and shows a short excerpt, for example
`SKILL.md:3 (frontmatter) [Hangul]: "...text around the first offending character..."`. At most 5 lines are shown,
then `(+N more)`.

## 4. Scripts: do string literals count?

Decision: yes. For files under `scripts/` the whole file is scanned: comments, docstrings, string literals and
identifiers. Reasons:

- Text a script prints reaches the model's context, so a Korean `print()` message costs the same tokens as Korean prose.
- Splitting "prose" from "data" literals statically is unreliable, and a gate with a loophole is exactly what the
  builder loop would learn to exploit.
- Real non-English matching logic does not need a literal: write the pattern with escapes. In Python source the
  backslash-escaped form `"[\uac00-\ud7a3]"` is ASCII, so it passes (the source text contains a backslash, a `u` and four
  hex digits, not a Hangul character; Python turns the escape into the Hangul range only when it runs). Task input data
  belongs in `evals/files/`.

Binary files are skipped and counted in the check reason, but only when they are identifiable (section 11): text that
decodes is always scanned; data that does not decode is binary only with a known format signature or a known binary
extension. A file that is not valid UTF-8 and is not an identified binary fails with "not valid UTF-8 text and not a supported binary format ..., cannot verify English-only", so a
legacy-encoded text file (EUC-KR) cannot slip through as "binary". In a skill, everything `eva.SKILL_IGNORE` names is
skipped, because a run does not install it (section 12); in an agent, `__pycache__` and `.git` directories are skipped.

## 5. Exceptions

Only two, both narrow and documented.

1. `evals/files/**` (task input data) is never scanned. File names listed in `evals[].files` are scanned, because they
   appear in prompts and fixtures can be renamed.
2. Output matchers: exactly the strings under `check.pattern`, `check.input_regex` and `check.rows` (static assertion
   objects in `evals.json`) and under `must_include` (agent scenarios). These match the run's output, and the run's
   output may legitimately be Korean (for example a proofreading task). They are not FAIL material, but they are never
   silent: when one contains non-English text the unit gets a `needs-review` item (`english-output-matchers`, verdict
   REVIEW, exit code 3) naming file, line and excerpt; `skill_eval.py validate` prints the same lines as `review:` notes
   and exits 3 until a decision is recorded, and live runs refuse until then (section 11). The rest of the `check`
   object (`path`, `fixture`, `tool`, `type`, `flags`, dict keys) is configuration, not output matching, and fails.

Not scanned because a run does not install them or they are not skill or agent content: `evals/files/**`,
`evals/*.bak*`, other files in `evals/` besides the two eval JSON files, everything else `eva.SKILL_IGNORE` names
(`__pycache__`, `*-workspace`, `.git`, `FEEDBACK.md`, `FEEDBACK.json`), and in an agent every file outside `AGENT.md`,
`scenarios/` and `wrappers/`. Every other file of a skill is scanned (section 12).

Considered and left out: an opt-in waiver block inside SKILL.md/references (`<!-- allow-non-english -->`). JSON cannot carry
it, and an LLM builder could insert waivers to pass its own gate. The owner can ask for it later; the default stays FAIL.

## 6. Dynamic rule (skill_eval.py)

- `validate`: `load_eval_set` calls `eva.english_violations(skill_dir)`, the same scan as `eva.py check`. It covers the
  whole skill folder: `SKILL.md`, every other file a run installs (`agents/openai.yaml` included), `evals/evals.json`
  and `evals/trigger_evals.json`. `evals/files/**` is exempt. Each finding is a validation problem,
  so `validate` exits 1 and every `--live` gate refuses ("eval set has N validation problem(s)"). Matcher review notes
  are printed after the OK line; `validate` then exits 3 until `approve --gate rule_review` records a decision
  (section 11).
- `loop`: right after every builder call (any return code, also when the builder raised; section 11), `loop` runs
  `eva.english_violations(skill_dir)` (the full static English check of the rewritten folder, FAIL-level groups only). Any finding stops the loop with
  `{"status": "builder-non-english", "iteration": n, "problems": [...]}` before the skill is reloaded or another
  iteration is run, so nothing more is spent. This is code, not a prompt line. The gate is also what protects
  against a builder that edits `evals/` (the builder is told not to, but the gate covers it).
- The builder instruction that `skill_eval` sends now contains the sentence "Write all skill content in English only
  (every file a run installs, plus evals/evals.json and evals/trigger_evals.json); the loop stops if any non-English
  text appears."
- Executor run output is NOT checked. A task may require Korean output. The rule covers skill and agent content and
  eval definitions, never run outputs (transcripts, final answers, grader notes).
- The starting skill folder is gated too: `load_eval_set` adds every English finding as a validation problem, so every
  `--live` gate refuses it before iteration 1, and the loop's per-iteration precheck refuses it again.
- `--builder-prompt FILE` (the operator's own instructions) is not scanned. It is not skill content.

## 7. Impact on registration, index and approvals (no semantic change)

Code read, not guessed:

- `skill_eval.register` (skill_eval.py `register`): compares `skill_tree_hash`, `eval_set_sha256`, benchmark hash and
  approvals. It never calls `eva.check`. `_dispatch` loads the eval set first but ignores its problems for `register`.
  The registered csv-cleaner stays registered; the file hashes do not change because golden/ is not edited.
- `approve` / `approval_ok`: hash-bound to files, no eva check. Unchanged.
- `eva.py index <dir>`: calls `check()` on every unit and OVERWRITES `<dir>/INDEX.md` and `<dir>/registry.json` with the
  new verdicts. Running it on `golden/` would now record FAIL for every unit. It was not run. The committed
  `golden/registry.json` and `golden/INDEX.md` are stale (they say PASS) until the owner decides to re-index.
- `eva.py check-all golden` now exits 1. `eva.py feedback` writes into the unit folder and was not run.
- `skill_eval.py validate golden/skills/csv-cleaner` now exits 1 (section 8), so `run --live` / `loop --live` on it is
  refused by the validation gate until the content is translated. Translating changes file hashes, which invalidates
  approvals by design; that is the owner's decision and not done here.

## 8. Golden impact

Measured after round 1 with `python3 eva.py check <unit>` (exit code 1 = FAIL for all five). `golden/` was not edited and
is not exempted. This is the combined diff of the English-only rule and the multi-tool parity rule
(`notes/multi-tool-parity-plan.md`), so the table lists both kinds of failing checks. The golden skills fail only the new
`english-*` checks (every parity row passes). The golden agents fail the `english-*` checks, the four `parity-wrapper-*`
rows (they have no `wrappers/`), and `parity-permissions-hermes`, which always fails (fail closed, no verified Hermes
permission format). No other row fails or needs review (asserted in
`tests/test_english_rule.py::TestIndexAndGolden::test_golden_units_fail_exactly_on_these_checks`), so with the
`language` and `parity` rows removed every unit still scores PASS. The counts are findings, one per offending line
(Markdown) or per offending JSON string.

| unit | verdict | failing checks | where |
|---|---|---|---|
| `golden/skills/csv-cleaner` | FAIL (score 0.94) | `english-skill-md`, `english-evals` | SKILL.md lines 17, 19, 21 (body: Inputs/Outputs/Example); evals/evals.json 32 strings, evals/trigger_evals.json 12 strings (prompts, expected_output, assertions, queries) |
| `golden/skills/korean-proofreader` | FAIL (0.93) | `english-skill-md`, `english-evals` | SKILL.md 5 lines (description on line 3 plus body); evals/evals.json 12 strings |
| `golden/skills/pdf-summarizer` | FAIL (0.90) | `english-skill-md`, `english-bundled-files`, `english-evals` | SKILL.md 5 lines; references/format.md line 1; evals/evals.json 11 strings |
| `golden/agents/code-reviewer` | FAIL (0.68) | `english-agent-md`, `english-scenarios`, `parity-wrapper-claude`, `parity-wrapper-codex`, `parity-wrapper-opencode`, `parity-wrapper-hermes`, `parity-permissions-hermes` | AGENT.md 6 lines (goal, success_criteria, body); scenarios 7 strings (01-overview.json, 02-drill.json); no wrappers/ |
| `golden/agents/research-assistant` | FAIL (0.68) | `english-agent-md`, `english-scenarios`, the same five `parity-*` rows | AGENT.md 6 lines; scenarios 5 strings (01-basic.json, 02-followup.json); no wrappers/ |

(`golden/skills/csv-cleaner-workspace` is an eval workspace, not a unit; `eva.py check` on it reports "no SKILL.md or
AGENT.md".)

csv-cleaner details: its frontmatter and `scripts/clean.py` are already English-only (`english-bundled-files` passes),
and none of its static `check` patterns contain non-English text (`english-output-matchers` is N/A, so no rule review
is needed and its registration and approvals are unaffected). Its `evals/files/**` fixtures and the two
`evals.json.bak-*` files are not scanned.

Consequences for the registered csv-cleaner (no semantics changed):

- Registration, `approvals.json` and every recorded hash stay valid: nothing under `golden/` changed.
- `python3 skill_eval.py validate golden/skills/csv-cleaner` now exits 1 with 47 `english-only rule:` problems
  (measured 2026-10-20). `validate` runs `eva.english_violations` over the whole skill folder, so the count is 3
  `SKILL.md` body lines (17, 19, 21), 32 `evals/evals.json` strings and 12 `evals/trigger_evals.json` strings.
  `evals/files/**` is exempt. `run --live` / `loop --live` on it would be refused by the validation gate until the
  `SKILL.md` body and the eval set are translated. Translating the eval set changes `evals/evals.json` and therefore
  the `eval_set` approval hash: a new approval (owner decision).
- `golden/registry.json` and `golden/INDEX.md` still say PASS. They are only rewritten by `eva.py index golden`,
  which was not run.

## 9. Existing tests changed (no golden exemption anywhere)

- `tests/test_eva.py`: `test_all_golden_pass` became `test_golden_units_fail_only_the_english_and_parity_rules` (expects
  FAIL; skills fail only `english-*` ids; agents fail only `english-*`, `parity-wrapper-*` and `parity-permissions-hermes`;
  PASS without the `language` and `parity` rows). C4 later removed this test, and
  `tests/test_english_rule.py::TestIndexAndGolden::test_golden_units_fail_exactly_on_these_checks` now pins the exact
  failing rows of each golden unit. `test_check_all_exit` expects exit 1 for `golden`.
  `test_static_criteria` expects exactly the two agent english ids plus the four wrapper rows and the Hermes row. Tests that use Korean fixture text on purpose
  (`bad/` units, Korean descriptions in the description tests) read the other checks through `minus_language()`.
  The example line in the `GOOD_FM` template is English.
- `tests/test_skill_eval.py`: `SKILL_MD` example line is English (the loop tests build the skill from it).
- `tests/test_round6.py`, `test_round10.py`, `test_round16.py`, `test_round17.py`: use the golden csv-cleaner eval set
  for unrelated behaviour. They now assert that the only validation problems are `english-only rule:` ones, and
  `test_round6` asserts the two failing english checks.
- New: `tests/test_english_rule.py` (53 tests) and `tests/test_review_round1.py` (round 1 review fixes, section 11).

## 10. Deliberately left out

- No opt-in waiver block (section 5).
- `LOOP.md`, `loop.py`, `golden/`, `bad/` untouched (outside the allowed edit list); `loop.py` inherits the rule through `eva.check`.
- No language detection for Latin-script prose.
- eva.py's own messages and fix hints stay Korean; the owner rule is about agent and skill content, not tooling.

## 11. Codex review round 1 (2026-10-19): changes and policy choices

Policy rule used throughout: where a fix needed a choice, the stricter option won (a false FAIL or REVIEW is better than a
false PASS) and no rule was weakened. Tests: `tests/test_review_round1.py` (one class per finding).

| finding | change | where |
|---|---|---|
| P1-1 scripts | two-test classification (script words, fail closed on L*/M*/N* with a first-word allowlist), Greek exception kept, katakana/era-name ranges | `eva.non_english_script`, `NON_ENGLISH_SCRIPT_WORDS`, `ENGLISH_COMPATIBLE_FIRST_WORDS` |
| P1-3 reviews | `eva.review_items`; gate `rule_review` in `approvals.json`, bound to the finding texts and the bytes of `evals/evals.json`, `evals/trigger_evals.json` and `agents/openai.yaml` (round 2); `validate` exits 3, live gates refuse, `loop` stops with `builder-review-required` | `skill_eval.rule_review_*`, `check_live_gates`, `loop`, `_dispatch` |
| P2-1 matcher fields | matcher exemption by key path: `check.pattern`, `check.input_regex`, `check.rows`, `must_include` | `eva.OUTPUT_MATCHER_KEYS`, `_json_strings` |
| P2-3 builder exit | English, parity and review gates run after every builder call, return code and error kept in the result | `skill_eval.loop` |
| P2-4 binary | decodable text is always scanned; undecodable data is binary only with a known signature or extension; unknown undecodable fails | `eva.read_scannable`, `BINARY_SIGNATURES`, `BINARY_EXTENSIONS` |
| P2-5 scenario names | every file path under `scenarios/` is scanned | `eva.english_scan` (agent branch) |
| parity-2 escapes | decode per format and quoting style (YAML double-quoted, TOML basic) | `eva._yaml_decode_lines`, `_toml_decode`, `english_text_findings(decode=...)` |
| reporting-4 index | `english=review` | `eva.english_state`, `build_index` |

Choices worth knowing:

- Stricter binary rule: an unidentified file with a NUL byte used to be skipped as binary; now it is scanned if it decodes,
  and otherwise must match a signature or have a known binary extension, or it fails. A binary asset in an unlisted format
  fails until its signature or extension is added to the constants on purpose.
- A `.png` (or any binary extension, or a file starting with a binary signature) that decodes as text is scanned as text.
  A real binary header followed by hidden UTF-16 text is trusted and skipped (known limit of any signature rule).
- Review decisions reuse the approvals mechanism with one new key (`rule_review`). The existing keys (`requirements`,
  `eval_set`, `iteration-N`, `registration`), `registration.json` and the iteration folders are untouched and keep their
  meaning; `register` and the other approvals never read the new key. Not changed: csv-cleaner has no review finding, so
  nothing new is required of it.
- A parity review (`parity-invocation-hermes`, unverified Codex fields) uses the same decision gate as the English matcher
  review, so no review result can start a paid run silently.
- Remaining known limit: the scanner detects scripts, not languages (Latin-letter prose passes).
- Changed by the C1 owner review (round 2): Python 3.9's Unicode 13.0 database does not know scripts added later. Those
  characters are unassigned there (category Cn) and used to be skipped. They now fail as `Unassigned`, and so does an
  emoji added after Unicode 13.0.

## 12. Codex P1 review round 2 (C5): scan scope, YAML strict forms, registration decision

Tests: `tests/test_review_round3.py`.

| finding | change | where |
|---|---|---|
| G7 scan scope | one table, `eva.SKILL_IGNORE` (`evals`, `__pycache__`, `*-workspace`, `.git`, `FEEDBACK.md`, `FEEDBACK.json`), names what a skill run does not install; `install_skill`, `skill_tree_hash`, `escaping_links` and the English scan of a skill read it; the scan reads every file the walk finds (since C6 the walk is `eva.unit_tree`, section 13) | `eva.SKILL_IGNORE`, `eva.english_scan` (skill branch); `skill_eval.skill_tree_hash`, `install_skill`, `escaping_links` |
| G9 YAML forms | a flow collection opened at a value start and a complex-key entry are decoded strictly (fail closed); block style keeps the precise decoder | `eva._flow_scan`, `_yaml_decode_value`, `_yaml_decode_lines` |
| P2 registration | the decision half of `register()` is `registration_record(spec, ws)`, which returns the record or raises and writes nothing; `register()` writes what it returns | `skill_eval.registration_record`, `register` |

Choices worth knowing:

- The table lives in `eva.py`, because `skill_eval.py` already imports `eva` and `eva.py` imports `skill_eval` only inside
  its `eval` command dispatch.
- `.git` folders and the `FEEDBACK.*` files that `eva.py feedback` and `loop.py` write into the unit are not installed and
  not hashed, instead of scanned. A `.git` folder holds undecodable object files, so scanning it would fail every skill
  kept in its own repository, and `FEEDBACK.*` holds the engine's Korean messages. No golden skill holds such a file, so
  the golden hashes and rows did not move.
- `SKILL.md` keeps `english-skill-md` and `agents/openai.yaml` keeps `english-tool-files`; every other installed file
  goes to `english-bundled-files`, scanned with the escape mode of its extension (`.yaml`/`.yml`, `.toml`) and its file
  name.
- The agent scan is unchanged: no command installs an agent folder (`skill_eval.py` copies only skill folders, in
  `install_skill`).
- The flow and complex-key rule may flag ASCII text that a YAML reader keeps literal, such as a single-quoted
  `'[\uAC00-\uD7A3]'` inside a flow collection. That is the fail-closed choice: the line decoder already missed escapes
  in four YAML forms, so the forms it does not model now only add findings. In block style the same value passes.
- The round-1 limit (an internal folder link scanned once by its real folder, so a file reached through a link was
  reported under the first path the walk met) is closed in C6 (section 13).

## 13. Codex P1 review round 3 (C6): one walker for install, hash, review sources and scan

Tests: `tests/test_review_round4.py`.

| finding | change | where |
|---|---|---|
| P1 alias and wrapper bytes | one walker, `eva.unit_tree(path, ignore)`, lists what `shutil.copytree(path, dst, ignore=...)` with `symlinks=False` reaches: `files` (every installed path, each alias of an internal folder link under its own path), `outside` (links whose real path leaves the unit) and `cycles` (folder links whose real path is a folder on their own path from the root, the root included). The English scan name-checks and scans every alias path; `review_sources` of an agent adds each Codex and OpenCode wrapper file a review row reads whenever it is a file inside the agent folder | `eva.unit_tree`, `eva._tree_view`, `eva.english_scan`, `eva.review_sources` |
| P1 cycles | a cycle is never walked; it fails the `english-*` row of its folder by name (`<path> (link) points back at a folder on its own path (a cycle), so a copy of the unit would never end`), and `install_skill` refuses it | `eva.LINK_CYCLE`, `skill_eval.install_skill` |
| tree-hash stall | `skill_tree_hash` hashes the walker's files; an outside link or a cycle enters as its path plus a marker, never as content and never walked. Before C6 the walk re-entered a cycle until the OS stopped path resolution (32 link levels on macOS), so two cycle links made it walk about `2^32` paths | `skill_eval.skill_tree_hash`, `sha256_labeled` |

Choices worth knowing:

- Every reader is a view of one walk: `install_skill` copies exactly the walker's files with `shutil.copy2`, so install
  equals scan by construction. An empty folder is no longer created in the run, because the walker lists files only.
- The walk order is the order `skill_tree_hash` used before C6 (top-down, a folder's files sorted, then its subfolders
  sorted), and that function already followed internal links without dedupe. So a skill folder with no outside link
  and no cycle hashes exactly as before, and the golden skills keep their recorded hashes. A folder with an outside
  link or a cycle hashes differently than before; `install_skill` refused outside and dangling links before C6, and a
  cycle made its copy fail with `shutil.Error`, so no such folder could finish a run.
- `escaping_links` is derived from the walker (outside links plus dangling files). Inside an aliased folder it now also
  names the link under the alias path, because the copy would follow it there too.
- The agent scan walks the agent folder once with its own ignore (`eva.SKIP_DIRS` folders only) and takes the
  `scenarios/` and `wrappers/` views; `SKILL_IGNORE` stays the skill table.

