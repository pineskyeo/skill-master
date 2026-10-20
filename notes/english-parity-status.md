# English-only and multi-tool parity rules: status at the C1 head (2026-10-19)

State: an uncommitted diff on top of c3f733f. It is unit C1 of phase P1 and lands as one commit after pinesky approves
P1. golden/, approvals, registration and iteration folders are untouched.

## What the diff adds
1. English-only rule (notes/english-rule-plan.md)
   - Static checks in eva.py, required, category `language`: english-skill-md, english-bundled-files, english-evals,
     english-agent-md, english-scenarios, english-tool-files, english-wrappers. The scanner flags every non-English
     letter script (Greek allowed), fails closed on letters, marks and numbers it cannot name as English-compatible, and
     flags code points that the Python Unicode database does not assign. evals/files/** is exempt.
   - Run-output matchers (`check.pattern`, `check.input_regex`, `check.rows`, scenario `must_include`) are needs-review,
     not FAIL, because a task may require Korean output. Executor output is never required to be English.
   - Dynamic checks in skill_eval.py: `validate` runs the full folder scan (`eva.english_violations`: SKILL.md, every
     other file a run installs, the evals JSON), so every `--live` gate refuses before the first paid call, and the loop
     runs a code gate after every builder call, whatever its return code (`builder-non-english`).
2. Multi-tool parity rule (notes/multi-tool-parity-plan.md)
   - Skills: parity-skill-claude, parity-skill-codex, parity-skill-opencode, parity-skill-hermes,
     parity-invocation-control, parity-invocation-hermes.
   - Agents: wrappers/claude/<name>.md, wrappers/codex/<name>.toml, wrappers/opencode/<name>.md,
     wrappers/hermes/SOUL.md; rows parity-agent-name, parity-wrapper-*, parity-permissions-*; a strict flat-TOML reader.
   - Dynamic: `validate` and `plan` print per-tool parity, the loop gate is `builder-parity-missing`, and
     `--engines opencode` or `--engines hermes` fails closed. Per-tool coverage is recorded and never reads as a pass.
3. Fail-closed policies
   - Hermes agent permissions: `parity-permissions-hermes` is a required FAIL for every agent until a Hermes permission
     format is verified. Every agent is FAIL today because of it.
   - Review findings (English output matchers, unverified agents/openai.yaml fields, Hermes invocation control) make
     `validate` exit 3 and block every `--live` gate and the loop (`builder-review-required`) until
     `approve --gate rule_review` records a decision. The decision is bound to the finding texts and to the bytes of
     the files the review rows read, per unit kind (`eva.REVIEW_SOURCES`): for a skill evals/evals.json,
     evals/trigger_evals.json and agents/openai.yaml; for an agent AGENT.md, scenarios/, wrappers/codex/ and
     wrappers/opencode/.

## Review rounds
1. Codex review round 1 (read-only): REQUEST_CHANGES with 14 findings (3 P1, 9 P2, 2 P3). The tracked-file diff Codex
   read is line 11 of ~/workspace/pstack-trial/runs/engrule/rev1.events.jsonl.
2. fix-r1 (Claude): a fix for each of the 14 findings, pinned by tests/test_review_round1.py (52 tests).
3. C1 owner review (round 2, 2026-10-19): read the fix-r1 delta against the round-1 diff and every fix site, and ran
   each pinning test. 12 findings hold. Two fixes were incomplete, and the fix-r1 YAML escape decoder held two new
   false PASS paths. Each is fixed with a test in tests/test_review_round2.py that failed on the C0 code and passes
   after the fix:
   - P1-1. Python 3.9 has Unicode 13.0, so letters of later scripts (Kawi, Vithkuqi, Toto, Tangsa) were unassigned and
     passed. `eva.non_english_script` now flags unassigned code points as `Unassigned`. An emoji added after Unicode
     13.0 now fails too.
   - P1-3. The `rule_review` decision hashed only the finding texts, which are short excerpts, five at most per check.
     A changed sixth matcher, a change past the excerpt of a long matcher, or a new value of an unverified
     agents/openai.yaml field kept the old decision valid, and `validate` exited 0. The hash now also covers the bytes
     of the files the findings come from (then `skill_eval.RULE_REVIEW_SOURCES`, since C4 `eva.REVIEW_SOURCES`).
   - YAML escape contexts (new in fix-r1). A frontmatter key with an empty value and a comment ending in `: |` was read
     as a block scalar, so the escaped Hangul on the next nested line was not decoded. The strict reader loads it as
     Hangul and the skill passed. A sequence item `- key: |` hid the sibling keys below it the same way. A key now
     starts a block scalar only when its value, comment removed, is the block indicator, and the block covers only
     lines indented deeper than the key.
   - YAML double-quoted scalars (new in fix-r1). Escapes were decoded only on the line that opens a double-quoted
     scalar, and only when the quote came first. A continuation line, an escaped line break, or a scalar after a tag
     (`!!str`) or an anchor (`&a`) kept escaped Hangul hidden, so `english-wrappers` passed for a YAML file under
     wrappers/. The decoder now carries an open quoted scalar to the next line and reads past a tag or anchor.
     Single-quoted and plain continuation lines stay literal.
4. Codex P1 round 1 (runs/p1/rev1.parsed.txt), fixed in unit C4 with tests in tests/test_review_round2.py,
   tests/test_codex_permissions.py, tests/test_review_round1.py and tests/test_english_rule.py:
   - P1-1. `validate` checked English only in the evals JSON, so a non-English SKILL.md or bundled file reached a paid
     run. `load_eval_set` now uses the full `eva.english_violations` scan.
   - P1-2. The bundled-file walk skipped symlinked folders that a run copies by content. It now follows those that
     resolve inside the unit folder, each real folder once (C6 lists every alias path instead, item 6); links out of the
     folder stay unfollowed.
   - P1-3. An agent's rule-review decision ignored AGENT.md, scenarios/ and the wrappers. The hash now covers the files
     each review row reads, from one table per unit kind.
   - P1-4. The Codex wrapper reader accepted any extra flat key. Keys outside the five recorded ones now fail
     `parity-wrapper-codex` by name.
5. Codex P1 round 2 (runs/p1/rev2.parsed.txt), fixed in unit C5 with tests in tests/test_review_round3.py
   (notes/english-rule-plan.md section 12):
   - G7. A skill run installed files the English scan never read (`templates/`, top-level files, other `agents/` files,
     nested `.git/`). One table, `eva.SKILL_IGNORE`, now names what a run does not install, and the installer, the skill
     hash, the link check and the scan all read it. The scan reads every installed file. `.git` and `FEEDBACK.*` are no
     longer installed or hashed.
   - G9. The YAML escape decoder missed `{"a":"\uD55C"}` and `? "\uD55C"`. Text inside a flow collection that opens at a
     value start and a complex-key entry are now decoded strictly.
   - P2. The registration decision is `skill_eval.registration_record`, tested on a copy of the golden csv-cleaner;
     `register()` writes what it returns.
6. Codex P1 round 3 (runs/p1/rev3.parsed.txt), fixed in unit C6 with tests in tests/test_review_round4.py
   (notes/english-rule-plan.md section 13):
   - P1. The walk skipped a folder whose real path it had already seen, so the installed path of an internal folder
     link (for example a Hangul-named link to `references/`) was never name-checked or scanned, and a wrapper read
     through `wrappers/codex -> ..` left the rule-review hash. One walker, `eva.unit_tree`, now lists every installed
     path, each alias under its own path, and every reader (install, skill hash, link check, English scan, review
     sources) is a view of it. This replaces the "each real folder once" rule of item 4, P1-2.
   - Cycles. A folder link that points back at a folder on its own path fails by name, is never walked, and refuses
     the install. `skill_tree_hash` no longer stalls on two cycle links.
   - P3. The `multi-tool parity rule: [tool] ...` problem format in notes/multi-tool-parity-plan.md section 5 matches
     the code, and the inline comments that narrated adjacent calls in `skill_eval.load_eval_set` and
     `check_live_gates` are gone.

## Tests
`python3 -m unittest discover -s tests`: Ran 817 tests, OK. That is the 618 tests from before this work, plus 53
English, 89 parity, 52 round-1 and 5 round-2 tests.

## Golden impact (`eva.py check`, every unit exits 1)
- csv-cleaner and korean-proofreader fail english-skill-md and english-evals. pdf-summarizer also fails
  english-bundled-files. All three skills pass parity for all four tools.
- code-reviewer and research-assistant fail english-agent-md, english-scenarios, the four parity-wrapper-* rows and
  parity-permissions-hermes, with score 0.68.
- `skill_eval.py validate golden/skills/csv-cleaner` exits 1, so live runs are refused until the eval set is translated
  and re-approved. golden/INDEX.md and registry.json are stale because `eva.py index` was not run.

## Moved to other P1 units
- F1, Codex `sandbox_mode` counted as a permission: a custom agent file's `sandbox_mode` does not override the parent
  session, so a match is a false guarantee. C2 fixes it.
- F2, OpenCode permission keys other than edit, bash and webfetch are not compared, so a read-only agent can keep web
  search. C3 fixes it.

## Known limits (open)
- The scanner detects scripts, not languages: Latin-letter prose in any language passes.
- A known binary signature is trusted: a real binary header followed by hidden UTF-16 text is skipped.
- A known binary extension is trusted for data that does not decode: legacy-encoded text, for example EUC-KR Korean in
  a file named `.png`, is skipped as binary. The round-1 test
  `TestBinaryDetection.test_known_extension_without_signature_or_nul_is_binary_when_it_does_not_decode` pins this
  behavior, so round 2 left it unchanged.
- Codex and OpenCode agent permissions are compared at capability-class level only.
- Engine messages in eva.py and skill_eval.py and the older README text are still Korean. A later unit translates them.
- The OpenCode and Hermes engines do not exist yet.

## Changed files
Modified: README.md, eva.py, skill_eval.py, tests/test_eva.py, tests/test_round6.py, tests/test_round10.py,
tests/test_round16.py, tests/test_round17.py, tests/test_skill_eval.py
New: notes/english-rule-plan.md, notes/multi-tool-parity-plan.md, notes/english-parity-status.md,
tests/test_english_rule.py, tests/test_multi_tool_parity.py, tests/test_review_round1.py, tests/test_review_round2.py

## Cost of the in-flight job (API-equivalent, subscription billing, recorded 2026-10-19): about $12.87
claude impl $3.45, impl-parity $3.55, fix-r1 $4.67; codex review-r1 $1.21. Run logs: ~/workspace/pstack-trial/runs/engrule/.
