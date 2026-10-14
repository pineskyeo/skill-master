import contextlib, io, json, os, shutil, subprocess, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva

def p(*a): return os.path.join(ROOT, *a)
def wjson(path, obj):
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f)
def rjson(path):
    with open(path, encoding="utf-8") as f: return json.load(f)

class TestParser(unittest.TestCase):
    def test_frontmatter(self):
        m, b = eva.parse_frontmatter("---\nname: a-b\nlist: [x, y]\nitems:\n  - one\n  - two\ndesc: >\n  hello\n  world\n---\nbody")
        self.assertEqual(m["name"], "a-b"); self.assertEqual(m["list"], ["x", "y"])
        self.assertEqual(m["items"], ["one", "two"]); self.assertEqual(m["desc"], "hello world"); self.assertEqual(b, "body")
    def test_nested_metadata(self):
        m, _ = eva.parse_frontmatter("---\nname: a\nmetadata:\n  version: 1.2.0\n---\n")
        self.assertEqual(m["metadata"]["version"], "1.2.0")
    def test_no_frontmatter(self):
        self.assertIsNone(eva.parse_frontmatter("# x")[0])

class TestGolden(unittest.TestCase):
    def test_all_golden_pass(self):
        for u in eva.find_units(p("golden")):
            r = eva.check(u)
            self.assertEqual(r.verdict, "PASS", (u, r.failed, r.category_failed))

class TestBad(unittest.TestCase):
    def failed(self, path):
        r = eva.check(p(path)); self.assertEqual(r.verdict, "FAIL")
        return {c["id"] for c in r.failed}
    def test_do_everything(self):
        f = self.failed("bad/skills/DoEverything")
        self.assertTrue({"name-format", "single-purpose", "refs-exist", "description-when"} <= f)
    def test_no_io(self):
        self.assertTrue({"inputs-declared", "outputs-declared", "example-present"} <= self.failed("bad/skills/no-io-skill"))
    def test_no_frontmatter(self):
        self.assertIn("frontmatter", self.failed("bad/skills/wrong-folder-name"))
    def test_vague_agent(self):
        f = self.failed("bad/agents/vague-agent")
        self.assertTrue({"goal-present", "success-measurable", "tools-subset", "memory-section", "multi-turn-scenario"} <= f)
    def test_every_failure_has_fix(self):
        for c in eva.check(p("bad/agents/vague-agent")).checks:
            if c["passed"] is False: self.assertTrue(c["fix"])

class TestCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        shutil.copytree(p("bad"), os.path.join(self.tmp, "bad"))
    def tearDown(self): shutil.rmtree(self.tmp)
    def test_feedback_files(self):
        d = os.path.join(self.tmp, "bad/skills/no-io-skill")
        self.assertEqual(eva.main(["feedback", d, "--round", "1"]), 1)
        self.assertIn("수정:", open(os.path.join(d, "FEEDBACK.md"), encoding="utf-8").read())
        self.assertEqual(json.loads(open(os.path.join(d, "FEEDBACK.json")).read())["verdict"], "FAIL")
    def test_index(self):
        eva.main(["index", self.tmp])
        reg = json.load(open(os.path.join(self.tmp, "registry.json")))
        self.assertEqual(len(reg), 6); self.assertTrue(os.path.exists(os.path.join(self.tmp, "INDEX.md")))
    def test_transcript_without_citations_fails(self):
        r = eva.grade_transcript(p("golden/agents/research-assistant"), p("transcripts/research-followup.json"))
        self.assertEqual(r.verdict, "FAIL")
        self.assertTrue({"criteria-sources", "expected-table"} <= {c["id"] for c in r.failed})
    def test_transcript_with_citations_passes(self):
        r = eva.grade_transcript(p("golden/agents/research-assistant"), p("transcripts/research-followup-cited.json"))
        self.assertEqual(r.verdict, "PASS", r.failed)
    def test_transcript_illegal_tool(self):
        t = os.path.join(self.tmp, "t.json")
        json.dump({"scenario": "basic", "tool_calls": ["web_search", "send_email"], "final": "x"}, open(t, "w"))
        r = eva.grade_transcript(p("golden/agents/research-assistant"), t)
        self.assertIn("only-declared-tools", {c["id"] for c in r.checks if not c["passed"]})

class TestOutcomeEvidence(unittest.TestCase):
    def test_sources_counts_unique_urls(self):
        kind, ok, _, _ = eva.outcome_evidence("출처 링크 3개 이상 인용", "a https://x.org/1 b https://x.org/1. c https://y.org/2")
        self.assertEqual(kind, "sources"); self.assertFalse(ok)
        self.assertTrue(eva.outcome_evidence("at least 2 sources", "https://x.org/1 https://y.org/2")[1])
    def test_length(self):
        self.assertEqual(eva.outcome_evidence("리포트는 10자 이하", "x" * 11)[:2], ("length", False))
        self.assertTrue(eva.outcome_evidence("리포트는 10자 이하", "x" * 10)[1])
    def test_table(self):
        self.assertFalse(eva.outcome_evidence("비교표", "Hermes vs LangChain 비교표 ...")[1])
        self.assertTrue(eva.outcome_evidence("비교표", "| a | b |\n|---|---|\n| 1 | 2 |")[1])
    def test_uncheckable_is_none(self):
        self.assertIsNone(eva.outcome_evidence("모든 주장에 출처를 단다", "x"))

class TestTranscriptOutcome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.agent = os.path.join(self.tmp, "a")
        os.makedirs(os.path.join(self.agent, "scenarios"))
        with open(os.path.join(self.agent, "AGENT.md"), "w", encoding="utf-8") as f: f.write(
            "---\nname: a\ndescription: d. Use when x.\ngoal: do the one thing well\ntools: [t]\nsuccess_criteria:\n  - be nice\n---\n")
        wjson(os.path.join(self.agent, "scenarios", "s.json"), {"id": "s", "turns": [{"user": "q"}], "expected_tools": ["t"],
              "must_include": ["ok"], "expected_outcome": "a good answer"})
        self.t = os.path.join(self.tmp, "t.json")
        wjson(self.t, {"scenario": "s", "tool_calls": ["t"], "final": "ok"})
    def tearDown(self): shutil.rmtree(self.tmp)
    def test_no_checkable_outcome_fails(self):
        r = eva.grade_transcript(self.agent, self.t)
        self.assertEqual(r.verdict, "FAIL")
        self.assertIn("outcome-evidence", {c["id"] for c in r.failed})
        self.assertEqual({c["id"] for c in r.checks if c["passed"] is None}, {"criteria-unchecked", "expected-unchecked"})
    def test_scenario_success_criteria_checked(self):
        sp = os.path.join(self.agent, "scenarios", "s.json")
        s = rjson(sp); s["success_criteria"] = ["URL 1개 이상"]; wjson(sp, s)
        self.assertEqual(eva.grade_transcript(self.agent, self.t).verdict, "FAIL")
        wjson(self.t, {"scenario": "s", "tool_calls": ["t"], "final": "ok https://x.org"})
        self.assertEqual(eva.grade_transcript(self.agent, self.t).verdict, "PASS")

class TestOptionalChecks(unittest.TestCase):
    def test_absent_optional_is_na_and_excluded(self):
        r = eva.check(p("golden/skills/korean-proofreader"))
        na = {c["id"] for c in r.checks if c["passed"] is None}
        self.assertTrue({"version-format", "evals-valid"} & na)
        app = r.applicable
        self.assertEqual(r.score, round(sum(c["passed"] for c in app) / len(app), 3))
        self.assertLess(len(app), len(r.checks))
    def test_present_bad_version_fails_despite_score(self):
        r = eva.check(p("bad/skills/bad-version-skill"))
        self.assertGreaterEqual(r.score, eva.PASS_THRESHOLD)
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual([c["id"] for c in r.failed], ["version-format"])
    def test_present_invalid_evals_fails(self):
        tmp = tempfile.mkdtemp()
        try:
            d = os.path.join(tmp, "pdf-summarizer")
            shutil.copytree(p("golden/skills/pdf-summarizer"), d)
            wjson(os.path.join(d, "evals", "evals.json"), {"evals": [{"prompt": "x"}]})
            r = eva.check(d)
            self.assertGreaterEqual(r.score, eva.PASS_THRESHOLD)
            self.assertEqual(r.verdict, "FAIL")
            self.assertIn("evals-valid", {c["id"] for c in r.critical_failed})
        finally:
            shutil.rmtree(tmp)

class TestCategoryFloor(unittest.TestCase):
    def test_floor_constant(self):
        self.assertTrue(0 < eva.CATEGORY_FLOOR <= 1)
    def test_zero_pass_category_fails_unit(self):
        r = eva.check(p("bad/agents/no-context-agent"))
        self.assertGreaterEqual(r.score, eva.PASS_THRESHOLD)
        self.assertFalse(r.critical_failed)
        self.assertEqual(r.category_failed, ["context"])
        self.assertEqual(r.verdict, "FAIL")
    def test_below_floor_category_fails_unit(self):
        r = eva.Report("x", "skill")
        for i in range(12): r.add("a%d" % i, "big", True, "")
        r.add("b0", "small", True, ""); r.add("b1", "small", False, ""); r.add("b2", "small", False, "")
        self.assertGreaterEqual(r.score, eva.PASS_THRESHOLD)
        self.assertEqual(r.category_failed, ["small"])
        self.assertEqual(r.verdict, "FAIL")
    def test_all_na_category_ignored(self):
        r = eva.Report("x", "skill")
        r.add("a", "big", True, ""); r.na("b", "opt", "absent")
        self.assertEqual(r.category_failed, []); self.assertEqual(r.verdict, "PASS")

class TestExitCodes(unittest.TestCase):
    def run_main(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return eva.main(argv)
    def test_check_all_exit(self):
        self.assertEqual(self.run_main(["check-all", p("golden")]), 0)
        self.assertEqual(self.run_main(["check-all", p("bad")]), 1)

class TestLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.unit = os.path.join(self.tmp, "no-io-skill")
        shutil.copytree(p("bad/skills/no-io-skill"), self.unit)
    def tearDown(self): shutil.rmtree(self.tmp)
    def loop(self, *args):
        return subprocess.run([sys.executable, p("loop.py"), self.unit] + list(args),
                              capture_output=True, text=True)
    def test_builder_nonzero_stops(self):
        res = self.loop("--builder", "exit 3", "--max-rounds", "3")
        self.assertEqual(res.returncode, 2)
        self.assertIn("builder failed: exit 3 in round 1", res.stderr)
        self.assertNotIn("round 2", res.stdout)
    def test_builder_ok_but_still_fail(self):
        res = self.loop("--builder", "true", "--max-rounds", "2")
        self.assertEqual(res.returncode, 1)
        self.assertIn("round 2", res.stdout); self.assertIn("final: FAIL", res.stdout)

if __name__ == "__main__":
    unittest.main()
