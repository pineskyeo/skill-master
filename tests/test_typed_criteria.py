"""Typed agent success criteria (unit B1, plan.md:772-819): AGENT.md v2 and scenario v2 schemas, the typed dispatcher in
engine/primitives/transcript.py, transcript grading against typed criteria, the capability data, and the removal of the
prose recognition. Each criterion type has a passing and a failing transcript graded through eva.grade_transcript."""
import ast, contextlib, copy, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva
from engine import rule_runner as RR, schema as S
from engine.primitives import parity as PR, transcript as T

CR2 = os.path.join(ROOT, "tests", "fixtures", "agents", "code-reviewer-v2")
RA2 = os.path.join(ROOT, "tests", "fixtures", "agents", "research-assistant-v2")
VOCABULARY = ["read", "search", "write", "shell", "web_fetch", "web_search", "delegate", "skill"]
TYPES = ["min_verified_sources", "file_line_citations", "no_writes", "max_chars", "markdown_table", "llm"]
CRITERION_DEFS = ("criterion", "count", "nonblank") + tuple(TYPES)
# sha256 of each A4 eva.py source segment moved to engine/primitives/transcript.py, computed from the A4 export by
# /tmp/b1-owner/pins.py (/tmp/b1-owner/a4-pins.txt). The moved file writes U+2192 as an escape, undone before hashing.
MOVED_PINS = {
    "PATH_KEYS": "cdfe834b7aaefb875e6e64c8e71b60847a48ee7e3bb820a371e65ad02f76b498",
    "CMD_KEYS": "c4f87b06da3c8771f70c9593427cc745005ee33bccc7df3d8600c8fd5f18e1e0",
    "GIT_HISTORY_RE": "4c222095ef34ebb0dc24549ec23c4d8531fe18465c5c88477a6df96dded1d436",
    "_strings": "cb6185334e7b40c71f2192e2759caf513d89d20164793605ba1335500c5e30f5",
    "_call_inputs": "1aaf835fc546b3afd0ca2ce7290cc4e75539c909f36b153ab2fbbec021210f05",
    "_outside": "2402d8ed6303fb023e71e615e3a94fe8e8aa84860eeaad0ba6f97118aaa936ac",
    "answer_lookup_violations": "665b7f46375fb2dd0bd26c5c574690d2d33391058fca318b49949a8ac639d30f",
    "URL_RE": "576352b97737d2335d5f5c4a75f54b81ff611c2f40bb443990e49d0b5ca49889",
    "TABLE_RE": "5144d3be607fdf59d8f0400428de878ad5c1ea14e418d853a983723b192e3a22",
    "FILELINE_RE": "871a83b00305fcde5867bde12d2f0e48380e55b45e7028992dce3d120ec48d99",
    "NUMBERED_LINE_RE": "7373739ecb46a53c9acb9261baca6669cc5af952eb91eda6c3843959f1efb79c",
    "GREP_LINE_RE": "c39dd4a693527e1b0b499de9d391eb3d44d5db04e98b2be02db2ce7f0da9174e",
    "WRITE_WORDS": "6683711013d01c9e2394051382e3263670f648a587abff232f6dd5133d3271c6",
    "WRITE_CMD_RE": "cc28fafbc3ffc1bd43cd8cd577eef7d3f3b5b86d1541225b2ecbf078c5a2d956",
    "_norm_url": "636e26f98a1fcade45072b8f666aafcd553f3ef928436746cb72de33c1cd85b4",
    "_tool_words": "c1a1e08095c3af15b88724577b0d4c910427396a4d6824ba3fb8e68e8e62061f",
    "write_calls": "7f4ba4ec26a19cb87a3924ac59d5852d0e7a7752651a4bd2e6a4874c68cadb09",
    "_norm_path": "bf8383b114e5f1352ca70079d29b61183859dfabfcba9fc75feb6258dddb8b21",
    "SHELL_META_RE": "416d90cee61fc80f5bd0b0b10a94afc0c2a00a0769dccb40681e8ccf1d2157d8",
    "READ_TOOL_WORDS": "b7b1f55f9de0938b653f065c3dc31c34e62de686888af07cbeace44639efbaa5",
    "NOT_READ_TOOL_WORDS": "8c471c491d302795c3441a338748bd031ac17797b43de1cb4c273300bcfc5268",
    "SHELL_TOOL_WORDS": "13033e29ec02f5a966568766c385e97e0dbc49ac5126f64813cde7c1edaae0af",
    "CWD_KEYS": "9ac0159642cbcebff879c12061e2e81a6a2c8a67246b10928235b95c05b4cd70",
    "_GREP_FLAG_LETTERS": "51c7e81dc216658f32cedfeca86dfe096dfaea56fbbf090dd68a4609a4860871",
    "_numbered_read_cmd": "3e79a7d32030025b1a001a3e7f66be21c010af2d69a2484818428af723ee619a",
    "_read_call": "1ff332bcd22bd4b59e49dd7581f15607f9c58fecaa26dc0007fafcc6158a639f",
    "lines_seen": "7eaca033d0cae557ab571c28b8d1424578f86848528e292900b955433e920ac6",
    "_result_lines": "fac4332038ec865887ec93f8c9c0487d69a95e248f2035304f86d0748250bdcc",
    "_numbered_lines": "8af75583bc260bda35c549a5408ea0047ad1fed4751813d64ea9cc04ffdc9455",
    "_grep_lines": "5f8302b35acbd12b36393c90280cbb7af0773e27926bee0c9006d1ea5aeb7fd0",
}
DELETED = ("FILELINE_CRIT_RE", "NO_WRITE_CRIT_RE", "outcome_evidence", "_min_count", "_max_chars", "CAPABILITY_BY_TOOL",
           "TOOL_ALIASES", "NEUTRAL_TOOLS", "tool_keys", "OPENCODE_PERMISSION_GRANTS")
HANGUL_RE = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def crit_line(c):
    return "  - '%s'\n" % json.dumps(c).replace("'", "''")


def agent_md(criteria, capabilities="[read, search, write, web_fetch]", extra="", targets="[claude, codex, opencode, hermes]",
             model_policy="judgment", name="typed-agent"):
    return ("---\nname: %s\ndescription: Reads files and reports on them. Use when a typed criterion test needs an agent.\n"
            "goal: Report on the files of a repository with evidence.\ncapabilities: %s\nmodel_policy: %s\n"
            "success_criteria:\n%stargets: %s\n%s---\n# Typed Agent\n## Tools\n- read reads one file.\n"
            "## Memory & Handoff\nKeeps the files it read.\n" % (name, capabilities, model_policy,
                                                                 "".join(crit_line(c) for c in criteria), targets, extra))


SCENARIO = {"id": "s", "turns": [{"user": "q"}, {"user": "more"}], "expected_tools": ["read"], "must_include": ["ok"],
            "expected_outcome": [{"type": "no_writes"}]}


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def agent(self, criteria, scenario=None, name="typed-agent", **kw):
        d = os.path.join(self.tmp, name)
        write(os.path.join(d, "AGENT.md"), agent_md(criteria, name=name, **kw))
        for i, s in enumerate(scenario if isinstance(scenario, list) else [scenario or SCENARIO], 1):
            write(os.path.join(d, "scenarios", "%02d.json" % i), json.dumps(s))
        return d

    def transcript(self, final, calls=(), scenario="s", name="t.json", workspace=None):
        t = {"scenario": scenario, "final": final, "tool_calls": list(calls)}
        if workspace:
            t["workspace"] = workspace
        p = os.path.join(self.tmp, name)
        write(p, json.dumps(t))
        return p

    def grade(self, criteria, final, calls=(), scenario=None):
        return eva.grade_transcript(self.agent(criteria, scenario), self.transcript(final, calls))

    def row(self, r, cid):
        rows = [c for c in r.checks if c["id"] == cid]
        self.assertEqual(len(rows), 1, [c["id"] for c in r.checks])
        return rows[0]

    def check_row(self, unit, rid):
        rows = [c for c in eva.check(unit).checks if c["id"] == rid]
        self.assertEqual(len(rows), 1, rid)
        return rows[0]


READ = {"tool": "read_file", "input": {"path": "src/app.py"}, "result": "     1\timport os\n     2\tx = 1\n     3\ty = 2"}
FETCH = {"tool": "web_fetch", "input": {"url": "https://a.org/1"}, "result": "https://a.org/1 https://b.org/2"}


class TestEachTypeHasAPassingAndAFailingTranscript(Tmp):
    """One passing and one failing transcript per criterion type, graded by eva.grade_transcript (plan.md:798)."""

    def outcome(self, crit, final, calls):
        r = self.grade([crit], final, calls)
        return r, self.row(r, "criteria-" + T.KINDS[crit["type"]])

    def test_min_verified_sources(self):
        crit = {"type": "min_verified_sources", "min": 2}
        _, row = self.outcome(crit, "See https://a.org/1 and https://b.org/2.", [FETCH])
        self.assertEqual(row["status"], "pass")
        r, row = self.outcome(crit, "See https://a.org/1 and https://made-up.example/3.", [FETCH])
        self.assertEqual(row["status"], "fail")
        self.assertIn("made-up.example/3", row["reason"])
        self.assertEqual(r.verdict, "FAIL")

    def test_a_url_only_in_the_final_answer_never_counts(self):
        crit = {"type": "min_verified_sources", "min": 1}
        _, row = self.outcome(crit, "https://only-here.org/x", [{"tool": "web_search", "input": {"q": "x"}, "result": "none"}])
        self.assertEqual(row["status"], "fail")

    def test_a_url_only_in_a_tool_input_never_counts(self):
        crit = {"type": "min_verified_sources", "min": 1}
        calls = [{"tool": "web_fetch", "input": {"url": "https://a.org/1"}, "result": "fetch failed: timeout"}]
        r, row = self.outcome(crit, "See https://a.org/1.", calls)
        self.assertEqual(row["status"], "fail")
        self.assertIn("not found in tool results: ['https://a.org/1']", row["reason"])
        self.assertEqual(r.verdict, "FAIL")
        _, row = self.outcome(crit, "See https://a.org/1.", [dict(calls[0], result="https://a.org/1 page text")])
        self.assertEqual(row["status"], "pass")

    def test_file_line_citations(self):
        crit = {"type": "file_line_citations", "min": 2}
        _, row = self.outcome(crit, "src/app.py:1 and src/app.py:3", [READ])
        self.assertEqual(row["status"], "pass")
        _, row = self.outcome(crit, "src/app.py:1 and src/app.py:9", [READ])
        self.assertEqual(row["status"], "fail")
        self.assertIn("src/app.py:9", row["reason"])

    def test_a_line_shown_only_by_a_multi_file_grep_never_counts(self):
        grep = {"tool": "bash", "input": {"command": "grep -rn x src"}, "result": "src/app.py:2:x = 1"}
        _, row = self.outcome({"type": "file_line_citations", "min": 1}, "src/app.py:2", [grep])
        self.assertEqual(row["status"], "fail")

    def test_no_writes(self):
        crit = {"type": "no_writes"}
        _, row = self.outcome(crit, "done", [READ])
        self.assertEqual(row["status"], "pass")
        for call in ({"tool": "Edit", "input": {"file_path": "x"}}, {"tool": "write_file", "input": {"path": "x"}},
                     {"tool": "bash", "input": {"command": "echo hi > notes.txt"}},
                     {"tool": "bash", "input": {"command": "sed -i 's/a/b/' f.py"}},
                     {"tool": "bash", "input": {"command": "git commit -m x"}}):
            _, row = self.outcome(crit, "done", [READ, call])
            self.assertEqual(row["status"], "fail", call)

    def test_max_chars(self):
        crit = {"type": "max_chars", "max": 10}
        _, row = self.outcome(crit, "x" * 10, [READ])
        self.assertEqual(row["status"], "pass")
        _, row = self.outcome(crit, "x" * 11, [READ])
        self.assertEqual(row["status"], "fail")
        self.assertIn("11 chars (max 10)", row["reason"])

    def test_max_chars_counts_characters_not_bytes(self):
        crit = {"type": "max_chars", "max": 10}
        _, row = self.outcome(crit, "\u00e9" * 10, [READ])
        self.assertEqual(row["status"], "pass", row["reason"])
        self.assertIn("10 chars (max 10)", row["reason"])
        _, row = self.outcome(crit, "\u00e9" * 11, [READ])
        self.assertEqual(row["status"], "fail")
        self.assertIn("11 chars (max 10)", row["reason"])

    def test_markdown_table(self):
        crit = {"type": "markdown_table"}
        _, row = self.outcome(crit, "| a | b |\n|---|---|\n| 1 | 2 |", [READ])
        self.assertEqual(row["status"], "pass")
        _, row = self.outcome(crit, "| a | b |\n| 1 | 2 |", [READ])
        self.assertEqual(row["status"], "fail")

    def test_llm(self):
        crit = {"type": "llm", "text": "Every claim has a source."}
        r = self.grade([crit, {"type": "no_writes"}], "ok", [READ])
        row = self.row(r, "criteria-needs-review")
        self.assertEqual(row["status"], "needs-review")
        self.assertIn("Every claim has a source.", row["reason"])
        self.assertEqual(r.verdict, "REVIEW")
        r = self.grade([crit, {"type": "no_writes"}], "ok", [READ, {"tool": "Edit", "input": {"file_path": "x"}}])
        self.assertEqual(self.row(r, "criteria-needs-review")["status"], "needs-review")
        self.assertEqual(r.verdict, "FAIL")


class TestLlmIsNeverPass(Tmp):
    def test_the_dispatcher_never_passes_llm(self):
        for final in ("", "Every claim has a source.", "x" * 5000):
            kind, ok, _, fix = T.criterion_evidence({"type": "llm", "text": "Every claim has a source."}, final, None)
            self.assertEqual((kind, ok), ("needs-review", None))
            self.assertTrue(fix)

    def test_only_llm_criteria_fail_outcome_evidence(self):
        llm = {"type": "llm", "text": "Be helpful."}
        r = self.grade([llm], "ok", [READ], scenario=dict(SCENARIO, expected_outcome=[llm]))
        self.assertEqual(self.row(r, "outcome-evidence")["status"], "fail")
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual({c["status"] for c in r.checks if c["id"].endswith("needs-review")}, {"needs-review"})


class TestGradingRows(Tmp):
    def test_rows_in_order_with_prefixes_and_numbering(self):
        crits = [{"type": "no_writes"}, {"type": "llm", "text": "Clear."}, {"type": "no_writes"}]
        scen = dict(SCENARIO, success_criteria=[{"type": "max_chars", "max": 100}],
                    expected_outcome=[{"type": "no_writes"}, {"type": "markdown_table"}])
        r = self.grade(crits, "ok | a |\n|---|\n", [READ], scenario=scen)
        self.assertEqual([c["id"] for c in r.checks], [
            "transcript-valid", "agent-valid", "scenario-known", "only-declared-tools", "expected-tools-called",
            "no-answer-lookup", "outcome-keywords", "criteria-no-writes", "criteria-needs-review", "criteria-no-writes-2",
            "criteria-length", "expected-no-writes", "expected-table", "outcome-evidence"])
        self.assertTrue(all(c["must"] for c in r.checks))

    def test_only_declared_tools_maps_names_through_the_data_and_names_the_bad_ones(self):
        r = self.grade([{"type": "no_writes"}], "ok", [READ, {"tool": "Grep"}, {"tool": "TodoWrite"}])
        self.assertEqual(self.row(r, "only-declared-tools")["status"], "pass")
        r = self.grade([{"type": "no_writes"}], "ok", [READ, {"tool": "send_email"}, {"tool": "bash"}])
        row = self.row(r, "only-declared-tools")
        self.assertEqual(row["status"], "fail")
        self.assertIn("send_email", row["reason"])
        self.assertIn("bash", row["reason"])

    def test_expected_tools_are_capabilities_covered_by_a_call(self):
        scen = dict(SCENARIO, expected_tools=["read", "search"])
        r = self.grade([{"type": "no_writes"}], "ok", [READ], scenario=scen)
        row = self.row(r, "expected-tools-called")
        self.assertEqual(row["status"], "fail")
        self.assertIn("search", row["reason"])
        r = self.grade([{"type": "no_writes"}], "ok", [READ, {"tool": "list_dir"}], scenario=scen)
        self.assertEqual(self.row(r, "expected-tools-called")["status"], "pass")

    def test_a_v1_agent_fails_agent_valid_with_the_migration_message_and_grading_stops(self):
        a = os.path.join(self.tmp, "old")
        write(os.path.join(a, "AGENT.md"), "---\nname: old\ndescription: d. Use when x.\ngoal: do the one thing well\n"
              "tools: [read_file]\nsuccess_criteria:\n  - at least 2 file:line citations\n---\n")
        r = eva.grade_transcript(a, self.transcript("ok", [READ]))
        self.assertEqual([c["id"] for c in r.checks], ["transcript-valid", "agent-valid"])
        row = r.checks[1]
        self.assertEqual((row["status"], row["must"]), ("fail", True))
        self.assertTrue(row["reason"].startswith("AGENT.md v1"), row["reason"])
        self.assertEqual(row["fix"], T.MIGRATION)
        self.assertEqual(r.verdict, "FAIL")

    def test_every_reason_and_fix_grade_transcript_prints_is_ascii_english(self):
        cases = [(CR2, os.path.join(ROOT, "transcripts", "code-reviewer-drill.json")),
                 (RA2, os.path.join(ROOT, "transcripts", "research-followup.json")),
                 (RA2, os.path.join(ROOT, "transcripts", "bad", "research-fabricated-urls.json")),
                 (CR2, os.path.join(ROOT, "transcripts", "bad", "code-reviewer-answer-lookup.json")),
                 (os.path.join(ROOT, "golden", "agents", "code-reviewer"), os.path.join(ROOT, "transcripts", "code-reviewer-drill.json"))]
        bad = self.transcript("x" * 900 + " src/a.py:99", [{"tool": "send_email"}, {"tool": "Edit", "input": {"command": "rm -rf ../x"}}],
                              scenario="drill", name="bad.json")
        cases += [(CR2, bad), (RA2, self.transcript("x", scenario="nope", name="nope.json")),
                  (CR2, self.transcript("x", name="noscen.json", scenario="")), (os.path.join(self.tmp, "none"), bad)]
        for agent, t in cases:
            for c in eva.grade_transcript(agent, t).checks:
                for text in (c["reason"], c["fix"]):
                    self.assertTrue(text.isascii(), (agent, t, c["id"], text))
                    self.assertIsNone(HANGUL_RE.search(text))

    def test_the_scenario_known_reason_prints_a_non_ascii_scenario_id_as_ascii(self):
        sid = "\uc2dc\ub098\ub9ac\uc624-\u00e9"
        r = eva.grade_transcript(self.agent([{"type": "no_writes"}]), self.transcript("ok", [READ], scenario=sid))
        row = self.row(r, "scenario-known")
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["reason"], "scenario '\\uc2dc\\ub098\\ub9ac\\uc624-\\xe9' not found")
        self.assertEqual(r.verdict, "FAIL")


class TestYouSee(unittest.TestCase):
    """plan.md:794: the v2 fixture gives the per-criterion outcomes the A4 head printed for the golden agent
    (/tmp/b1-owner/base-tr-research-followup-cited.txt)."""

    def test_research_followup_cited_against_the_v2_fixture(self):
        r = eva.grade_transcript(RA2, os.path.join(ROOT, "transcripts", "research-followup-cited.json"))
        self.assertEqual([(c["id"], c["status"]) for c in r.checks], [
            ("transcript-valid", "pass"), ("agent-valid", "pass"), ("scenario-known", "pass"),
            ("only-declared-tools", "pass"), ("expected-tools-called", "pass"), ("no-answer-lookup", "pass"),
            ("outcome-keywords", "pass"), ("criteria-sources", "pass"), ("criteria-needs-review", "needs-review"),
            ("criteria-length", "pass"), ("expected-table", "pass"), ("outcome-evidence", "pass")])
        self.assertEqual((r.verdict, r.score), ("REVIEW", 1.0))


class TestAgentFrontmatterV2(Tmp):
    def v2row(self, criteria=None, **kw):
        return self.check_row(self.agent(criteria or [{"type": "no_writes"}], **kw), "agent-frontmatter-v2")

    def test_a_v2_agent_and_the_fixtures_pass(self):
        self.assertEqual(self.v2row()["status"], "pass")
        for unit in (CR2, RA2):
            self.assertEqual(self.check_row(unit, "agent-frontmatter-v2")["status"], "pass", unit)

    def test_a_v1_agent_fails_with_the_migration_message(self):
        for md in ("---\nname: typed-agent\ndescription: d. Use when x.\ngoal: do the one thing well\ntools: [read_file]\n"
                   "success_criteria:\n  - at least 2 file:line citations\n---\n",
                   agent_md([{"type": "no_writes"}]).replace("success_criteria:\n", "success_criteria:\n  - read-only\n"),
                   agent_md([{"type": "no_writes"}]).replace("capabilities:", "tools: [read]\ncapabilities:")):
            d = os.path.join(self.tmp, "typed-agent")
            write(os.path.join(d, "AGENT.md"), md)
            row = self.check_row(d, "agent-frontmatter-v2")
            self.assertEqual((row["status"], row["must"]), ("fail", True), md)
            self.assertTrue(row["reason"].startswith("AGENT.md v1"), row["reason"])
            self.assertEqual(row["fix"], T.MIGRATION)
            self.assertIn("rename tools to capabilities", row["fix"])
            self.assertIn("model_policy", row["fix"])
            self.assertIn("targets", row["fix"])
            self.assertEqual(eva.check(d).verdict, "FAIL")

    def test_the_golden_agents_fail_the_v2_row(self):
        for name in ("code-reviewer", "research-assistant"):
            row = self.check_row(os.path.join(ROOT, "golden", "agents", name), "agent-frontmatter-v2")
            self.assertEqual(row["status"], "fail")
            self.assertTrue(row["reason"].startswith("AGENT.md v1"))

    def test_schema_refusals_name_the_path_and_the_enum(self):
        cases = {
            "unknown capability": (dict(capabilities="[read, browse]"), "/capabilities/1", "browse"),
            "mcp tool": (dict(capabilities="[read, mcp__crm__lookup]"), "/capabilities/1", "enum"),
            "no hermes": (dict(targets="[claude, codex, opencode]"), "/targets", "minItems"),
            "extra target": (dict(targets="[claude, codex, opencode, hermes, cursor]"), "/targets", "maxItems"),
            "repeated target": (dict(targets="[claude, codex, opencode, claude]"), "/targets", "uniqueItems"),
            "model id": (dict(model_policy="claude-opus-4-1"), "/model_policy", "pattern"),
            "dotted model id": (dict(model_policy="gpt-5.1"), "/model_policy", "pattern"),
            "slash model id": (dict(model_policy="openai/gpt"), "/model_policy", "pattern"),
            "unknown key": (dict(extra="color: blue\n"), "color", "additionalProperties"),
            "no capabilities": (dict(capabilities="[]"), "/capabilities", "minItems"),
        }
        for name, (kw, where, word) in cases.items():
            row = self.v2row(**kw)
            self.assertEqual(row["status"], "fail", name)
            self.assertIn(where, row["reason"], name)
            self.assertIn(word, row["reason"], name)
            self.assertFalse(row["reason"].startswith("AGENT.md v1"), name)

    def test_each_required_key_is_refused_when_missing(self):
        keys = ("name", "description", "goal", "capabilities", "model_policy", "success_criteria", "targets")
        for key in keys:
            lines = agent_md([{"type": "no_writes"}]).split("\n")
            i = next(n for n, ln in enumerate(lines) if ln.startswith(key + ":"))
            j = i + 1
            while lines[j].startswith("  - "):
                j += 1
            d = os.path.join(self.tmp, "typed-agent")
            write(os.path.join(d, "AGENT.md"), "\n".join(lines[:i] + lines[j:]))
            row = self.check_row(d, "agent-frontmatter-v2")
            self.assertEqual((row["status"], row["must"]), ("fail", True), key)
            self.assertIn("required property '%s' is missing" % key, row["reason"], key)
            self.assertFalse(row["reason"].startswith("AGENT.md v1"), key)
            self.assertEqual(eva.check(d).verdict, "FAIL", key)

    def test_a_repeated_capability_is_refused(self):
        row = self.v2row(capabilities="[read, search, read]")
        self.assertEqual(row["status"], "fail")
        self.assertIn("/capabilities", row["reason"])
        self.assertIn("uniqueItems", row["reason"])
        self.assertEqual(self.v2row(capabilities="[read, search]")["status"], "pass")

    def test_the_strict_loader_refuses_duplicate_keys_nan_and_infinity_in_a_criterion(self):
        for raw in ('{"type": "max_chars", "max": 5, "max": 800}', '{"type": "no_writes", "type": "no_writes"}',
                    '{"type": "max_chars", "max": NaN}', '{"type": "max_chars", "max": Infinity}',
                    '{"type": "max_chars", "max": -Infinity}'):
            d = self.agent([{"type": "no_writes"}])
            fp = os.path.join(d, "AGENT.md")
            with open(fp, encoding="utf-8") as f:
                text = f.read()
            write(fp, text.replace('\'{"type": "no_writes"}\'', "'%s'" % raw))
            row = self.check_row(d, "agent-frontmatter-v2")
            self.assertEqual(row["status"], "fail", raw)
            self.assertIn("/success_criteria/0: not a JSON object", row["reason"], raw)
            self.assertFalse(T.read_agent(d).valid, raw)
            self.assertEqual(eva.check(d).verdict, "FAIL", raw)

    def test_an_unreadable_or_out_of_unit_agent_md_fails_the_v2_row(self):
        bad = os.path.join(self.tmp, "typed-agent")
        os.makedirs(bad)
        with open(os.path.join(bad, "AGENT.md"), "wb") as f:
            f.write(agent_md([{"type": "no_writes"}]).encode("utf-8") + b"\xff\xfe\n")
        outside = os.path.join(self.tmp, "elsewhere", "AGENT.md")
        write(outside, agent_md([{"type": "no_writes"}]))
        linked = os.path.join(self.tmp, "linked", "typed-agent")
        os.makedirs(linked)
        os.symlink(outside, os.path.join(linked, "AGENT.md"))
        for d in (bad, linked):
            row = self.check_row(d, "agent-frontmatter-v2")
            self.assertEqual((row["status"], row["must"]), ("fail", True), d)
            self.assertTrue(row["reason"].startswith("AGENT.md unreadable: "), row["reason"])
            self.assertTrue(row["reason"].isascii(), row["reason"])
            self.assertEqual(eva.check(d).verdict, "FAIL", d)

    def test_a_v2_problem_from_non_ascii_input_prints_as_ascii(self):
        row = self.v2row(capabilities="[read, \ube0c\ub77c\uc6b0\uc988]")
        self.assertEqual(row["status"], "fail")
        self.assertIn("/capabilities/1", row["reason"])
        self.assertIn("\\ube0c\\ub77c\\uc6b0\\uc988", row["reason"])
        self.assertTrue(row["reason"].isascii(), row["reason"])
        row = self.v2row([{"type": "no_writes", "\uc0c9\uc0c1": 1}])
        self.assertEqual(row["status"], "fail")
        self.assertIn("additionalProperties: property '\\uc0c9\\uc0c1' is not allowed", row["reason"])
        self.assertTrue(row["reason"].isascii(), row["reason"])

    def test_the_op_refuses_an_extra_key_and_a_schema_name_outside_the_pattern_at_load_time(self):
        with open(os.path.join(ROOT, "rules", "agent-structure.rules.json"), encoding="utf-8") as f:
            doc = json.load(f)
        rule = next(r for r in doc["rules"] if r["id"] == "agent-frontmatter-v2")

        def bundle(name, check):
            folder = os.path.join(self.tmp, name)
            write(os.path.join(folder, "rules.json"), json.dumps(dict(doc, rules=[dict(rule, check=check)])))
            return RR.load_bundle(folder)

        good = bundle("good", {"op": "agent_frontmatter", "schema": "agent-frontmatter.schema.json"})
        self.assertEqual([r.id for r in good.rules], ["agent-frontmatter-v2"])
        for name, check in (
                ("extra-key", {"op": "agent_frontmatter", "schema": "agent-frontmatter.schema.json", "strict": False}),
                ("traversal", {"op": "agent_frontmatter", "schema": "../schemas/agent-frontmatter.schema.json"}),
                ("upper-case", {"op": "agent_frontmatter", "schema": "Agent-Frontmatter.schema.json"}),
                ("no-suffix", {"op": "agent_frontmatter", "schema": "agent-frontmatter.json"}),
                ("no-schema", {"op": "agent_frontmatter"})):
            with self.assertRaises(RR.BundleError, msg=name) as cm:
                bundle(name, check)
            self.assertIn("rules.json", str(cm.exception), name)

    def test_criterion_objects_are_closed_and_counts_are_integers_of_at_least_one(self):
        bad = [{"type": "min_verified_sources"}, {"type": "min_verified_sources", "min": 0},
               {"type": "min_verified_sources", "min": True}, {"type": "file_line_citations", "min": 2.0},
               {"type": "file_line_citations", "min": "2"}, {"type": "max_chars", "max": 0}, {"type": "max_chars"},
               {"type": "no_writes", "min": 1}, {"type": "markdown_table", "x": 1}, {"type": "llm"},
               {"type": "llm", "text": " "}, {"type": "llm", "text": "a", "min": 1}, {"type": "tone"}, {"min": 1}]
        for c in bad:
            self.assertEqual(self.v2row([c])["status"], "fail", c)
        good = [{"type": "min_verified_sources", "min": 1}, {"type": "file_line_citations", "min": 3},
                {"type": "no_writes"}, {"type": "max_chars", "max": 800}, {"type": "markdown_table"},
                {"type": "llm", "text": "Every claim has a source."}]
        self.assertEqual(self.v2row(good)["status"], "pass")

    def test_a_broken_json_criterion_is_a_v2_problem_not_v1(self):
        d = self.agent([{"type": "no_writes"}])
        fp = os.path.join(d, "AGENT.md")
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        write(fp, text.replace('\'{"type": "no_writes"}\'', '\'{"type": "no_writes"\''))
        row = self.check_row(d, "agent-frontmatter-v2")
        self.assertEqual(row["status"], "fail")
        self.assertIn("/success_criteria/0", row["reason"])
        self.assertFalse(row["reason"].startswith("AGENT.md v1"))

    def test_the_row_is_critical(self):
        self.assertIn("agent-frontmatter-v2", eva.CRITICAL)
        rule = next(r for r in RR.default_bundle().rules if r.id == "agent-frontmatter-v2")
        self.assertEqual((rule.category, rule.severity, rule.applies_to), ("structure", "fail", ("agent",)))
        self.assertEqual(rule.fix, T.MIGRATION)


class TestScenarioV2(unittest.TestCase):
    def test_v2_accepts_typed_outcomes_and_capabilities(self):
        self.assertEqual(eva.scenario_errors(SCENARIO), [])
        self.assertEqual(eva.scenario_errors(dict(SCENARIO, success_criteria=[{"type": "markdown_table"}])), [])
        for unit in (CR2, RA2):
            for f, s, e in eva.load_scenarios(unit):
                self.assertEqual(e, [], (unit, f))

    def test_v2_rejects_v1_scenarios(self):
        for name in ("code-reviewer", "research-assistant"):
            for f, s, e in eva.load_scenarios(os.path.join(ROOT, "golden", "agents", name)):
                self.assertTrue(e, (name, f))
        for bad in (dict(SCENARIO, expected_outcome="a good answer"), dict(SCENARIO, expected_outcome=["at least 3 sources"]),
                    dict(SCENARIO, expected_outcome=[]), dict(SCENARIO, expected_tools=["read_file"]),
                    dict(SCENARIO, expected_tools=[]), dict(SCENARIO, expected_tools=["browse"]),
                    dict(SCENARIO, success_criteria=["be nice"]), dict(SCENARIO, success_criteria=[]),
                    dict(SCENARIO, expected_outcome=[{"type": "max_chars", "max": True}])):
            self.assertTrue(eva.scenario_errors(bad), bad)

    def test_a_bad_criterion_message_names_its_index(self):
        errs = eva.scenario_errors(dict(SCENARIO, expected_outcome=[{"type": "no_writes"}, {"type": "max_chars"}]))
        self.assertTrue(any(m.startswith("expected_outcome[1]: ") for m in errs), errs)


class TestDefinitionsAgree(unittest.TestCase):
    def doc(self, name):
        with open(os.path.join(ROOT, "schemas", name), encoding="utf-8") as f:
            return json.load(f)

    def test_the_criterion_definitions_are_equal_in_both_schemas(self):
        a, s = self.doc("agent-frontmatter.schema.json")["$defs"], self.doc("scenario.schema.json")["$defs"]
        for k in CRITERION_DEFS:
            self.assertEqual(a[k], s[k], k)
        self.assertEqual(a["criterion"]["properties"]["type"]["enum"], TYPES)
        self.assertEqual(sorted(T.KINDS), sorted(TYPES))

    def test_the_vocabulary_is_one_enum_everywhere(self):
        a, s = self.doc("agent-frontmatter.schema.json")["$defs"], self.doc("scenario.schema.json")["$defs"]
        self.assertEqual(a["capability"]["enum"], VOCABULARY)
        self.assertEqual(s["capability"]["enum"], VOCABULARY)
        self.assertEqual(self.doc("targets.schema.json")["$defs"]["class"]["enum"], VOCABULARY)
        self.assertEqual(list(PR.vocabulary()), VOCABULARY)
        self.assertEqual(self.doc("agent-frontmatter.schema.json")["properties"]["targets"]["items"]["enum"], list(PR.TOOLS))

    def test_every_mapped_tool_name_lands_in_the_vocabulary(self):
        with open(os.path.join(ROOT, "rules", "data", "capabilities.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertLessEqual(set(data["tools"].values()), set(VOCABULARY))
        self.assertTrue(all(k == k.lower() and k.strip() == k for k in data["tools"]))
        self.assertEqual(data["neutral"], ["todowrite", "todo_write"])
        self.assertFalse(set(data["neutral"]) & set(data["tools"]))
        for name, cap in (("read", "read"), ("read_file", "read"), ("view", "read"), ("cat", "read"), ("grep", "search"),
                          ("glob", "search"), ("find", "search"), ("search", "search"), ("list_dir", "search"),
                          ("ls", "search"), ("list", "search"), ("edit", "write"), ("bash", "shell"),
                          ("webfetch", "web_fetch"), ("websearch", "web_search"), ("task", "delegate"), ("skill", "skill")):
            self.assertEqual(data["tools"][name], cap, name)
        self.assertNotIn("browse", data["tools"])
        self.assertEqual(PR.capabilities(["Read_File", "Grep", "TodoWrite", "mcp__x"]), ({"read", "search"}, ["mcp__x"]))

    def test_the_capability_data_is_the_a4_table_with_only_the_d6_moves_to_search(self):
        a4 = {}
        for cap, names in (("read", "read_file read grep glob list_dir ls find search view cat"),
                           ("write", "write_file edit_file write edit multiedit create_file delete_file notebookedit "
                                     "apply_patch"),
                           ("shell", "bash shell terminal exec run_command"),
                           ("web_fetch", "web_fetch webfetch"), ("web_search", "web_search websearch"),
                           ("delegate", "task"), ("skill", "skill")):
            for n in names.split():
                a4[n] = cap
        expected = dict(a4, **{n: "search" for n in ("grep", "glob", "find", "search", "list_dir", "ls", "list")})
        with open(os.path.join(ROOT, "rules", "data", "capabilities.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["tools"], expected)
        self.assertEqual(data["neutral"], ["todowrite", "todo_write"])
        table, neutral = PR.capability_table()
        self.assertEqual((dict(table), sorted(neutral)), (expected, ["todo_write", "todowrite"]))

    def test_a_read_only_agent_calling_a_write_or_shell_tool_fails_only_declared_tools(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        d = os.path.join(tmp, "typed-agent")
        write(os.path.join(d, "AGENT.md"), agent_md([{"type": "max_chars", "max": 800}], capabilities="[read, search]"))
        write(os.path.join(d, "scenarios", "01.json"), json.dumps(SCENARIO))
        names = ("write_file", "edit_file", "write", "edit", "multiedit", "create_file", "delete_file", "notebookedit",
                 "apply_patch", "bash", "shell", "terminal", "exec", "run_command")
        for name in names:
            t = os.path.join(tmp, "t-%s.json" % name)
            write(t, json.dumps({"scenario": "s", "final": "ok", "tool_calls": [READ, {"tool": name}]}))
            r = eva.grade_transcript(d, t)
            row = next(c for c in r.checks if c["id"] == "only-declared-tools")
            self.assertEqual(row["status"], "fail", name)
            self.assertIn("capability not declared: ['%s']" % name, row["reason"])
            self.assertEqual(r.verdict, "FAIL", name)

    def test_opencode_grants_search_for_its_finding_keys(self):
        grants = PR.target("opencode")["permissions"]["grants"]
        self.assertEqual({k: grants[k] for k in ("glob", "grep", "list", "read", "lsp")},
                         {"glob": "search", "grep": "search", "list": "search", "read": "read", "lsp": "read"})
        self.assertEqual(PR.target("claude")["permissions"]["compare"], "tool-classes")


class TestProseRecognitionIsGone(unittest.TestCase):
    def source(self, rel):
        with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
            return f.read()

    def engine_files(self):
        out = []
        for dp, dn, fn in os.walk(os.path.join(ROOT, "engine")):
            dn[:] = [d for d in dn if d != "__pycache__"]
            out += [os.path.relpath(os.path.join(dp, n), ROOT).replace(os.sep, "/") for n in fn if n.endswith(".py")]
        return out

    def test_no_deleted_name_is_defined_in_eva_or_the_engine(self):
        for rel in ["eva.py"] + self.engine_files():
            tree = ast.parse(self.source(rel))
            defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} | \
                      {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
            self.assertEqual(defined & set(DELETED), set(), rel)
        for name in DELETED + ("capabilities",):
            self.assertFalse(hasattr(eva, name), name)

    def test_the_transcript_module_and_the_grader_hold_no_hangul(self):
        self.assertIsNone(HANGUL_RE.search(self.source("engine/primitives/transcript.py")))
        src = self.source("eva.py")
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in ("grade_transcript", "_host_success_measurable",
                                                                    "_host_tools_declared", "_host_scenarios"):
                self.assertIsNone(HANGUL_RE.search(ast.get_source_segment(src, node)), node.name)

    def test_every_moved_verifier_is_the_a4_segment(self):
        src = self.source("engine/primitives/transcript.py")
        segs = {}
        for node in ast.parse(src).body:
            name = node.name if isinstance(node, ast.FunctionDef) else \
                node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None
            if name:
                segs[name] = ast.get_source_segment(src, node)
        unescape = lambda text: re.sub(r"\\u([0-9a-f]{4})", lambda m: chr(int(m.group(1), 16)), text)
        for name, pin in MOVED_PINS.items():
            self.assertEqual(hashlib.sha256(unescape(segs[name]).encode("utf-8")).hexdigest(), pin, name)
        self.assertTrue(src.isascii())

    def test_eva_keeps_the_public_verifier_names(self):
        for name in ("answer_lookup_violations", "write_calls", "lines_seen", "URL_RE", "FILELINE_RE"):
            self.assertIs(getattr(eva, name), getattr(T, name), name)
        for name in ("load_transcript", "grade_transcript"):
            self.assertTrue(callable(getattr(eva, name)))

    def test_engine_modules_never_import_eva(self):
        for rel in self.engine_files():
            tree = ast.parse(self.source(rel))
            names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
                    {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            self.assertFalse(names & {"eva", "skill_eval"}, rel)

    def test_no_code_constant_maps_tool_names(self):
        self.assertFalse(hasattr(PR, "CAPABILITY_BY_TOOL"))
        self.assertFalse(hasattr(PR, "TOOL_ALIASES"))
        self.assertFalse(hasattr(PR, "tool_keys"))


class TestAgentRows(Tmp):
    def test_success_measurable_counts_typed_non_llm_criteria_only(self):
        llm = {"type": "llm", "text": "Be helpful."}
        self.assertEqual(self.check_row(self.agent([llm, llm]), "success-measurable")["status"], "fail")
        row = self.check_row(self.agent([llm, {"type": "max_chars", "max": 800}]), "success-measurable")
        self.assertEqual(row["status"], "pass")
        self.assertIn("1/2", row["reason"])
        self.assertIn("Be helpful.", row["reason"])

    def test_tools_declared_reads_capabilities(self):
        self.assertEqual(self.check_row(self.agent([{"type": "no_writes"}]), "tools-declared")["status"], "pass")
        d = self.agent([{"type": "no_writes"}])
        with open(os.path.join(d, "AGENT.md"), encoding="utf-8") as f:
            text = f.read()
        write(os.path.join(d, "AGENT.md"), text.replace("capabilities: [read, search, write, web_fetch]\n", ""))
        self.assertEqual(self.check_row(d, "tools-declared")["status"], "fail")

    def test_tools_subset_checks_every_expected_capability(self):
        self.assertEqual(self.check_row(self.agent([{"type": "no_writes"}]), "tools-subset")["status"], "pass")
        row = self.check_row(self.agent([{"type": "no_writes"}], dict(SCENARIO, expected_tools=["read", "shell"])),
                             "tools-subset")
        self.assertEqual(row["status"], "fail")
        self.assertIn("shell", row["reason"])

    def test_the_v2_fixtures_pass_every_structure_row(self):
        for unit in (CR2, RA2):
            r = eva.check(unit)
            bad = {c["id"] for c in r.failed if c["category"] != "parity"}
            self.assertEqual(bad, set(), unit)
            self.assertEqual({c["id"] for c in r.failed}, {"parity-wrapper-claude", "parity-wrapper-codex",
                                                           "parity-wrapper-opencode", "parity-wrapper-hermes",
                                                           "parity-permissions-hermes", "wrappers-generated"})


class TestNoCriterionShapeRaises(Tmp):
    """B1-r1-1: a criterion whose fields have any JSON shape gives every agent row, a failing v2 row and a FAIL verdict,
    in check, check-all and grade_transcript."""
    SHAPES = ('{"type": ["max_chars"], "max": 800}', '{"type": {"name": "max_chars"}, "max": 800}',
              '{"type": ["llm"], "text": "Clear."}', '{"type": "llm", "text": ["Clear."]}',
              '{"type": "llm", "text": {"t": "Clear."}}', '{"type": "max_chars", "max": [800]}',
              '{"type": "min_verified_sources", "min": {"n": 1}}', '{"type": null}', '{"type": 3}', '{"type": true}',
              '{"type": [], "min": []}')

    def unit(self, i, raw):
        name = "shape-%02d" % i
        d = self.agent([{"type": "no_writes"}, {"type": "max_chars", "max": 800}], name=name)
        fp = os.path.join(d, "AGENT.md")
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        write(fp, text.replace('\'{"type": "max_chars", "max": 800}\'', "'%s'" % raw))
        return d

    def test_check_gives_every_row_and_fails_the_v2_row(self):
        ids = [c["id"] for c in eva.check(self.agent([{"type": "no_writes"}], name="good")).checks]
        for i, raw in enumerate(self.SHAPES):
            r = eva.check(self.unit(i, raw))
            self.assertEqual([c["id"] for c in r.checks], ids, raw)
            row = self.row(r, "agent-frontmatter-v2")
            self.assertEqual(row["status"], "fail", raw)
            self.assertIn("/success_criteria/1", row["reason"], raw)
            self.assertTrue(row["reason"].isascii(), raw)
            self.assertEqual(r.verdict, "FAIL", raw)
            self.assertEqual(self.row(r, "success-measurable")["status"], "pass", raw)

    def test_check_all_reports_every_unit_of_the_folder(self):
        for i, raw in enumerate(self.SHAPES):
            self.unit(i, raw)
        self.agent([{"type": "no_writes"}], name="zz-good")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = eva.main(["check-all", self.tmp, "--json"])
        reps = json.loads(out.getvalue())
        self.assertEqual(code, eva.EXIT_CODES["FAIL"])
        self.assertEqual(len(reps), len(self.SHAPES) + 1)
        for rep in reps[:-1]:
            row = next(c for c in rep["checks"] if c["id"] == "agent-frontmatter-v2")
            self.assertEqual((row["status"], rep["verdict"]), ("fail", "FAIL"), rep["path"])

    def test_grade_transcript_stops_at_agent_valid(self):
        for i, raw in enumerate(self.SHAPES):
            r = eva.grade_transcript(self.unit(i, raw), self.transcript("ok", [READ], name="t-%02d.json" % i))
            self.assertEqual([c["id"] for c in r.checks], ["transcript-valid", "agent-valid"], raw)
            self.assertEqual((r.checks[1]["status"], r.verdict), ("fail", "FAIL"), raw)
            self.assertTrue(r.checks[1]["reason"].isascii(), raw)


class TestCriterionTextIsEnglish(Tmp):
    """B1-r1-2 and B1-r1-16: a criterion string that decodes to non-ASCII text (a JSON escape of Hangul, a lone
    surrogate, any other non-ASCII character) fails agent-frontmatter-v2 with a reason that names the criterion, so the
    agent is FAIL and never graded; literal Hangul keeps failing english-agent-md."""
    TEXTS = ("\uac01 claim has a source.", "bad \ud800 text", "bad \udc80 text", "caf\u00e9 au lait", "\u2192 arrow")

    def assert_refused(self, d, index=0):
        r = eva.check(d)
        row = self.row(r, "agent-frontmatter-v2")
        self.assertEqual((row["status"], row["must"]), ("fail", True), d)
        self.assertIn("/success_criteria/%d: non-ASCII text" % index, row["reason"])
        self.assertTrue(row["reason"].isascii(), row["reason"])
        self.assertFalse(row["reason"].startswith("AGENT.md v1"), row["reason"])
        self.assertEqual(r.verdict, "FAIL")
        g = eva.grade_transcript(d, self.transcript("ok", [READ], name="t.json"))
        self.assertEqual([c["id"] for c in g.checks], ["transcript-valid", "agent-valid"])
        self.assertEqual((g.checks[1]["status"], g.verdict), ("fail", "FAIL"))
        for c in g.checks:
            self.assertTrue(c["reason"].isascii() and c["fix"].isascii(), c)
        return r

    def test_a_json_escape_in_a_single_quoted_criterion_is_refused(self):
        for text in self.TEXTS:
            d = self.agent([{"type": "llm", "text": text}, {"type": "no_writes"}])
            with open(os.path.join(d, "AGENT.md"), "rb") as f:
                self.assertTrue(f.read().isascii())
            r = self.assert_refused(d)
            self.assertEqual(self.row(r, "english-agent-md")["status"], "pass", text)

    def test_a_double_quoted_yaml_criterion_holding_a_json_escape_is_refused(self):
        d = self.agent([{"type": "no_writes"}])
        fp = os.path.join(d, "AGENT.md")
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        write(fp, text.replace("success_criteria:\n", 'success_criteria:\n  - "{\\"type\\": \\"llm\\", \\"text\\": '
                                                     '\\"\\\\uac01 claim\\"}"\n'))
        r = self.assert_refused(d)
        self.assertEqual(self.row(r, "english-agent-md")["status"], "pass")

    def test_a_non_ascii_key_or_nested_value_is_refused(self):
        d = self.agent([{"type": "no_writes"}])
        fp = os.path.join(d, "AGENT.md")
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        write(fp, text.replace('\'{"type": "no_writes"}\'', '\'{"type": "no_writes", "\\uac01": 1}\''))
        self.assert_refused(d)

    def test_literal_hangul_keeps_failing_english_agent_md(self):
        d = self.agent([{"type": "no_writes"}])
        fp = os.path.join(d, "AGENT.md")
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        write(fp, text.replace('\'{"type": "no_writes"}\'', '\'{"type": "llm", "text": "\uac01 claim"}\'\n  - '
                                                            '\'{"type": "no_writes"}\''))
        r = self.assert_refused(d)
        self.assertEqual(self.row(r, "english-agent-md")["status"], "fail")

    def test_an_ascii_criterion_still_passes(self):
        d = self.agent([{"type": "llm", "text": "Every claim has a source."}, {"type": "no_writes"}])
        self.assertEqual(self.check_row(d, "agent-frontmatter-v2")["status"], "pass")

    def test_check_transcript_never_crashes_on_a_lone_surrogate(self):
        d = self.agent([{"type": "llm", "text": "bad \ud800 text"}, {"type": "no_writes"}])
        t = self.transcript("ok", [READ], name="t.json")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        for extra in ([], ["--json"]):
            p = subprocess.run([sys.executable, os.path.join(ROOT, "eva.py"), "check", "--transcript", t, d] + extra,
                               cwd=ROOT, env=env, capture_output=True)
            self.assertEqual(p.returncode, eva.EXIT_CODES["FAIL"], p.stderr)
            self.assertNotIn(b"Traceback", p.stderr)
            self.assertIn(b"/success_criteria/0: non-ASCII text", p.stdout)
            self.assertIn(b"bad \\\\ud800 text" if extra else b"bad \\ud800 text", p.stdout)
            out = p.stdout.decode("utf-8")
            if extra:
                self.assertTrue(all(c["reason"].isascii() and c["fix"].isascii() for c in json.loads(out)["checks"]))
            else:
                self.assertTrue(out.replace("\u2713", "").replace("\u2717", "").isascii(), out)


class TestAddedFilesAreAscii(unittest.TestCase):
    def test_every_file_b1_adds_is_ascii_english(self):
        files = ["schemas/agent-frontmatter.schema.json", "schemas/scenario.schema.json", "engine/primitives/transcript.py",
                 "rules/data/capabilities.json", "tests/test_typed_criteria.py", "rules/agent-structure.rules.json",
                 "rules/parity-agent.rules.json"]
        for top in ("tests/fixtures/agents/code-reviewer-v2", "tests/fixtures/agents/research-assistant-v2",
                    "tests/rules/agent-frontmatter-v2"):
            for dp, dn, fn in os.walk(os.path.join(ROOT, *top.split("/"))):
                files += [os.path.relpath(os.path.join(dp, n), ROOT) for n in fn]
        self.assertGreater(len(files), 15)
        for rel in files:
            with open(os.path.join(ROOT, rel), "rb") as f:
                self.assertTrue(f.read().isascii(), rel)


if __name__ == "__main__":
    unittest.main()
