import contextlib, io, json, os, shutil, subprocess, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva

def p(*a): return os.path.join(ROOT, *a)
def wjson(path, obj):
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f)
def rjson(path):
    with open(path, encoding="utf-8") as f: return json.load(f)
def wtext(path, text):
    with open(path, "w", encoding="utf-8") as f: f.write(text)
def run_of(calls=(), results=(), workspace=None):
    """Normalized run as produced by eva.load_transcript."""
    calls = [{"tool": c, "input": None, "result": None} if isinstance(c, str) else
             {"tool": c["tool"], "input": c.get("input"), "result": c.get("result")} for c in calls]
    return {"calls": calls, "results": [c["result"] for c in calls if c["result"]] + list(results), "workspace": workspace}

PYYAML_OPT_OUT = "EVA_ALLOW_NO_PYYAML"

def require_yaml(tc):
    """PyYAML for the parity tests. Missing PyYAML fails the test loudly; only an explicit opt-out
    (EVA_ALLOW_NO_PYYAML=1) turns it into a skip, with a warning printed to stderr."""
    try:
        import yaml
        return yaml
    except ImportError:
        if os.environ.get(PYYAML_OPT_OUT) == "1":
            print("WARNING: PyYAML not installed; YAML parity test %s SKIPPED because %s=1" % (tc.id(), PYYAML_OPT_OUT),
                  file=sys.stderr)
            tc.skipTest("PyYAML not installed (%s=1)" % PYYAML_OPT_OUT)
        tc.fail("PyYAML is required for the YAML parity tests: pip install pyyaml "
                "(or set %s=1 to skip them with a warning)" % PYYAML_OPT_OUT)

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
    def test_transcript_with_citations_needs_review(self):
        # every machine check passes; "모든 주장에 출처를 단다" cannot be machine-checked -> REVIEW, not PASS
        r = eva.grade_transcript(p("golden/agents/research-assistant"), p("transcripts/research-followup-cited.json"))
        self.assertEqual(r.failed, []); self.assertEqual(r.verdict, "REVIEW")
        self.assertEqual([c["id"] for c in r.needs_review], ["criteria-needs-review"])
        self.assertIn("모든 주장에 출처를 단다", r.needs_review[0]["reason"])
    def test_transcript_illegal_tool(self):
        t = os.path.join(self.tmp, "t.json")
        json.dump({"scenario": "basic", "tool_calls": ["web_search", "send_email"], "final": "x"}, open(t, "w"))
        r = eva.grade_transcript(p("golden/agents/research-assistant"), t)
        self.assertIn("only-declared-tools", {c["id"] for c in r.checks if not c["passed"]})

class TestOutcomeEvidence(unittest.TestCase):
    def test_sources_counts_unique_urls(self):
        run = run_of(results=["https://x.org/1 https://y.org/2 https://z.org/3"])
        kind, ok, _, _ = eva.outcome_evidence("출처 링크 3개 이상 인용", "a https://x.org/1 b https://x.org/1. c https://y.org/2", run)
        self.assertEqual(kind, "sources"); self.assertFalse(ok)
        self.assertTrue(eva.outcome_evidence("at least 2 sources", "https://x.org/1 https://y.org/2", run)[1])
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
        self.assertEqual({c["id"] for c in r.needs_review}, {"criteria-needs-review", "expected-needs-review"})
        self.assertFalse([c for c in r.checks if c["status"] == "n/a"])  # not silently N/A
    def test_scenario_success_criteria_checked(self):
        sp = os.path.join(self.agent, "scenarios", "s.json")
        s = rjson(sp); s["success_criteria"] = ["URL 1개 이상"]; wjson(sp, s)
        self.assertEqual(eva.grade_transcript(self.agent, self.t).verdict, "FAIL")
        wjson(self.t, {"scenario": "s", "tool_calls": [{"tool": "t", "result": "found https://x.org"}], "final": "ok https://x.org"})
        r = eva.grade_transcript(self.agent, self.t)
        self.assertTrue(self.status_of(r, "criteria-sources"))
        self.assertEqual(r.verdict, "REVIEW")  # "be nice" / "a good answer" still need a human or model grader
    def status_of(self, r, cid):
        return next(c["passed"] for c in r.checks if c["id"] == cid)

class TestOptionalChecks(unittest.TestCase):
    def test_absent_optional_is_na_and_excluded(self):
        r = eva.check(p("golden/skills/korean-proofreader"))
        na = {c["id"] for c in r.checks if c["passed"] is None}
        self.assertTrue({"compatibility-length", "reference-toc"} <= na)
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

RA, CR = p("golden/agents/research-assistant"), p("golden/agents/code-reviewer")

class TmpMixin:
    def setUp(self): self.tmp = tempfile.mkdtemp()
    def tearDown(self): shutil.rmtree(self.tmp)
    def transcript(self, obj, name="t.json"):
        t = os.path.join(self.tmp, name)
        if isinstance(obj, str): wtext(t, obj)
        else: wjson(t, obj)
        return t
    def ids(self, r, attr="failed"):
        return {c["id"] for c in getattr(r, attr)}

class TestRequiredToolChecks(TmpMixin, unittest.TestCase):
    """Task 1: tool/keyword checks are [필수]; one violation fails an otherwise perfect run."""
    def cited(self):
        return rjson(p("transcripts/research-followup-cited.json"))
    def test_undeclared_tool_always_fails(self):
        t = self.cited(); t["tool_calls"].append({"tool": "send_email", "input": {"to": "x"}, "result": "sent"})
        r = eva.grade_transcript(RA, self.transcript(t))
        self.assertGreaterEqual(r.score, eva.PASS_THRESHOLD)
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(self.ids(r, "critical_failed"), {"only-declared-tools"})
    def test_missing_expected_tool_fails(self):
        t = self.cited(); t["tool_calls"] = [c for c in t["tool_calls"] if c["tool"] != "write_file"]
        r = eva.grade_transcript(RA, self.transcript(t))
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(self.ids(r, "critical_failed"), {"expected-tools-called"})
    def test_missing_keyword_fails(self):
        a = os.path.join(self.tmp, "research-assistant"); shutil.copytree(RA, a)
        sp = os.path.join(a, "scenarios", "02-followup.json")
        s = rjson(sp); s["must_include"].append("orchestration-cost"); wjson(sp, s)
        r = eva.grade_transcript(a, p("transcripts/research-followup-cited.json"))
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(self.ids(r, "critical_failed"), {"outcome-keywords"})
    def test_checks_marked_must(self):
        r = eva.grade_transcript(RA, p("transcripts/research-followup-cited.json"))
        must = {c["id"] for c in r.checks if c["must"]}
        self.assertTrue({"only-declared-tools", "expected-tools-called", "outcome-keywords", "no-answer-lookup"} <= must)

class TestCodeReviewer(TmpMixin, unittest.TestCase):
    """Task 2: code-reviewer criteria are machine-checkable and graded."""
    def drill(self):
        return rjson(p("transcripts/code-reviewer-drill.json"))
    def test_static_criteria(self):
        r = eva.check(CR)
        self.assertEqual(r.verdict, "PASS", r.failed)
        self.assertNotIn("success-measurable", self.ids(r))
        meta, _ = eva.parse_frontmatter(open(os.path.join(CR, "AGENT.md"), encoding="utf-8").read())
        ev = {c: eva.outcome_evidence(c, "") for c in meta["success_criteria"]}
        self.assertEqual({e[0] for e in ev.values() if e}, {"file-lines", "no-writes"})
        self.assertEqual([c for c, e in ev.items() if e is None], ["모든 지적에 파일:줄 근거를 단다"])  # restored, needs-review
        for f, s, e in eva.load_scenarios(CR):
            self.assertFalse(e)
            self.assertTrue(all(eva.outcome_evidence(c, "") for c in s["expected_outcome"]), f)
    def test_drill_transcript(self):
        r = eva.grade_transcript(CR, p("transcripts/code-reviewer-drill.json"))
        self.assertEqual(r.failed, [])
        self.assertTrue({"criteria-file-lines", "criteria-no-writes", "expected-file-lines", "expected-no-writes"}
                        <= {c["id"] for c in r.checks if c["passed"]})
        self.assertEqual([c["id"] for c in r.needs_review], ["criteria-needs-review"])
        self.assertEqual(r.verdict, "REVIEW")
    def test_write_call_fails(self):
        for call in ({"tool": "write_file", "input": {"path": "x"}}, {"tool": "Edit", "input": {"file_path": "x"}},
                     {"tool": "read_file", "input": {"command": "echo hi > notes.txt"}},
                     {"tool": "read_file", "input": {"command": "sed -i 's/a/b/' f.py"}}):
            self.assertFalse(eva.outcome_evidence("파일 변경 0건", "", run_of([call]))[1], call)
        self.assertTrue(eva.outcome_evidence("파일 변경 0건", "", run_of([
            "read_file", {"tool": "grep", "input": {"command": "grep -rn x . 2>/dev/null"}}]))[1])
    def test_write_tool_in_transcript_fails_code_reviewer(self):
        t = self.drill(); t["tool_calls"].append({"tool": "write_file", "input": {"path": "REVIEW.md"}, "result": "ok"})
        r = eva.grade_transcript(CR, self.transcript(t))
        self.assertEqual(r.verdict, "FAIL")
        self.assertTrue({"only-declared-tools", "criteria-no-writes", "expected-no-writes"} <= self.ids(r, "critical_failed"))
    def test_file_line_count(self):
        run = run_of([{"tool": "read_file", "input": {"path": "src/app.py"}, "result": "     1\timport os\n     2\tx = 1"},
                      {"tool": "bash", "input": {"command": "grep -n -A1 x lib/util.go"}, "result": "7:x\n8-y\n9:z"}])
        kind, ok, got, _ = eva.outcome_evidence("파일:줄 근거 3개 이상", "src/app.py:1, src/app.py:1, lib/util.go:7", run)
        self.assertEqual(kind, "file-lines"); self.assertFalse(ok, got)  # duplicates counted once
        self.assertTrue(eva.outcome_evidence("at least 2 file:line citations", "src/app.py:1 lib/util.go:7-9", run)[1])
    def test_citations_to_unopened_files_ignored(self):
        run = run_of([{"tool": "read_file", "input": {"path": "src/app.py"}, "result": "1: a\n2: b"}])
        ok, got = eva.outcome_evidence("파일:줄 근거 2개 이상", "src/app.py:1 src/other.py:2 https://x.org:8080/a", run)[1:3]
        self.assertFalse(ok); self.assertIn("src/other.py:2", got)
    def test_uncountable_citation_criterion_not_checkable(self):
        self.assertIsNone(eva.outcome_evidence("모든 지적에 파일:줄 근거를 단다", ""))

class TestStaticMeasurable(TmpMixin, unittest.TestCase):
    """Task 2: success-measurable uses the same checkers as transcript grading."""
    def agent(self, crit):
        a = os.path.join(self.tmp, "a"); os.makedirs(os.path.join(a, "scenarios"), exist_ok=True)
        wtext(os.path.join(a, "AGENT.md"), "---\nname: a\ndescription: d. Use when x.\ngoal: do the one thing well\n"
              "tools: [t]\nsuccess_criteria:\n" + "".join("  - %s\n" % c for c in crit) + "---\n")
        return eva.check(a)
    def m(self, crit):
        return next(c for c in self.agent(crit).checks if c["id"] == "success-measurable")["passed"]
    def test_keyword_only_criteria_not_measurable(self):
        self.assertFalse(self.m(["모든 주장에 출처를 단다"]))
        self.assertFalse(self.m(["must be correct"]))
        self.assertFalse(self.m(["응답 시간 5초 이내"]))
    def test_checker_criteria_measurable(self):
        self.assertTrue(self.m(["출처 링크 3개 이상", "500자 이하", "비교표", "파일:줄 근거 1개 이상", "파일 변경 0건"]))
    def test_mixed_criteria_kept_with_needs_review_note(self):
        # an unmeasurable criterion stays (graded needs-review per transcript) as long as one criterion is checkable
        c = next(c for c in self.agent(["출처 링크 3개 이상", "be helpful"]).checks if c["id"] == "success-measurable")
        self.assertTrue(c["passed"]); self.assertIn("needs-review", c["reason"]); self.assertIn("be helpful", c["reason"])
    def test_empty_criteria_fails(self):
        self.assertFalse(self.m([]))
    def test_success_measurable_is_must(self):
        r = self.agent(["be helpful", "be kind"])
        self.assertIn("success-measurable", self.ids(r, "critical_failed")); self.assertEqual(r.verdict, "FAIL")

class TestVerifiedCitations(TmpMixin, unittest.TestCase):
    """Task 3: only URLs that appear in tool results count."""
    def test_unverified_urls_do_not_count(self):
        final = "https://a.org/1 https://b.org/2"
        self.assertFalse(eva.outcome_evidence("출처 2개 이상", final)[1])
        self.assertFalse(eva.outcome_evidence("출처 2개 이상", final, run_of([{"tool": "web_fetch", "input": {"url": "https://a.org/1"}}]))[1])
        self.assertTrue(eva.outcome_evidence("출처 2개 이상", final, run_of(results=["https://a.org/1/ and https://b.org/2."]))[1])
    def test_tool_results_field(self):
        t = rjson(p("transcripts/research-followup-cited.json"))
        results = [c.pop("result") for c in t["tool_calls"]]
        r = eva.grade_transcript(RA, self.transcript(t))
        self.assertIn("criteria-sources", self.ids(r, "critical_failed"))
        t["tool_results"] = results
        r = eva.grade_transcript(RA, self.transcript(t))
        self.assertEqual(r.failed, []); self.assertEqual(r.verdict, "REVIEW")
    def test_cited_fixture_urls_in_tool_results(self):
        run, err = eva.load_transcript(p("transcripts/research-followup-cited.json"))
        self.assertFalse(err)
        cited = set(eva.URL_RE.findall(run["final"]))
        self.assertEqual(len(cited), 3)
        self.assertTrue(all(any(u in res for res in run["results"]) for u in cited))
    def test_fabricated_urls_fixture_fails(self):
        r = eva.grade_transcript(RA, p("transcripts/bad/research-fabricated-urls.json"))
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(self.ids(r, "critical_failed"), {"criteria-sources"})
        reason = next(c["reason"] for c in r.failed if c["id"] == "criteria-sources")
        self.assertIn("fabricated.example.com", reason); self.assertIn("1 verified", reason)

class TestInputValidation(TmpMixin, unittest.TestCase):
    """Task 4: malformed input yields a FAIL report, never a crash."""
    def run_main(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            return eva.main(argv), err.getvalue()
    def test_check_all_zero_units_nonzero(self):
        rc, err = self.run_main(["check-all", self.tmp])
        self.assertNotEqual(rc, 0); self.assertIn("no SKILL.md/AGENT.md", err)
        self.assertNotEqual(self.run_main(["check-all", os.path.join(self.tmp, "missing")])[0], 0)
    def agent_with(self, scenarios):
        a = os.path.join(self.tmp, "code-reviewer"); shutil.copytree(CR, a)
        for f in os.listdir(os.path.join(a, "scenarios")): os.remove(os.path.join(a, "scenarios", f))
        for name, s in scenarios.items():
            fp = os.path.join(a, "scenarios", name)
            wtext(fp, s) if isinstance(s, str) else wjson(fp, s)
        return a
    def good_scenario(self, **kw):
        s = {"id": "s", "turns": [{"user": "q"}], "expected_tools": ["grep"], "must_include": ["x"],
             "expected_outcome": "파일 변경 0건"}
        s.update(kw); return s
    def test_scenario_not_object(self):
        a = self.agent_with({"1.json": [1, 2], "2.json": "\"text\"", "3.json": self.good_scenario()})
        r = eva.check(a)
        self.assertEqual(r.verdict, "FAIL")
        self.assertIn("scenarios-valid", self.ids(r, "critical_failed"))
        reason = next(c["reason"] for c in r.failed if c["id"] == "scenarios-valid")
        self.assertIn("got list", reason); self.assertIn("got str", reason)
    def test_scenario_required_fields(self):
        cases = {"no-id": {"id": None}, "turns-str": {"turns": "q"}, "turn-no-user": {"turns": [{"bot": "x"}]},
                 "tools-str": {"expected_tools": "grep"}, "no-kw": {"must_include": []},
                 "no-outcome": {"expected_outcome": ""}, "bad-sc": {"success_criteria": 3}}
        for name, kw in cases.items():
            s = self.good_scenario(**kw)
            if kw.get("id", 0) is None: del s["id"]
            self.assertTrue(eva.scenario_errors(s), name)
        self.assertEqual(eva.scenario_errors(self.good_scenario()), [])
        a = self.agent_with({"1.json": self.good_scenario(turns="q"), "2.json": self.good_scenario(id="b")})
        self.assertIn("scenarios-valid", self.ids(eva.check(a), "critical_failed"))
    def test_scenario_invalid_json(self):
        a = self.agent_with({"1.json": "{not json", "2.json": self.good_scenario()})
        r = eva.check(a)
        self.assertIn("scenarios-valid", self.ids(r, "critical_failed")); self.assertEqual(r.verdict, "FAIL")
    def test_frontmatter_opening_line_exact(self):
        for text in ("----\nname: a\n---\n", "--- \nname: a\n---\n", "---x\nname: a\n---\n", " ---\nname: a\n---\n",
                     "---\nname: a\n"):
            meta, _, err = eva.parse_frontmatter_ex(text)
            self.assertIsNone(meta, text); self.assertTrue(err)
    def test_frontmatter_must_be_mapping(self):
        for text in ("---\n- a\n- b\n---\n", "---\njust text\n---\n", "---\n  name: a\n---\n",
                     "---\nname: a\nname: b\n---\n", "---\nname: a\n  extra: b\n---\n",
                     "---\nitems:\n  - a\n  k: v\n---\n", "---\nmeta:\n  bogus line\n---\n"):
            meta, _, err = eva.parse_frontmatter_ex(text)
            self.assertIsNone(meta, text); self.assertTrue(err, text)
    def test_bad_frontmatter_fails_unit_with_reason(self):
        d = os.path.join(self.tmp, "x-skill"); os.makedirs(d)
        wtext(os.path.join(d, "SKILL.md"), "---\nname: x-skill\ndescription: Does x. Use when y.\nnot a mapping line\n---\n# X\n")
        r = eva.check(d)
        self.assertEqual(r.verdict, "FAIL"); self.assertIn("frontmatter", self.ids(r, "critical_failed"))
        self.assertIn("line 4", next(c["reason"] for c in r.failed if c["id"] == "frontmatter"))
    def test_undecodable_file_fails(self):
        d = os.path.join(self.tmp, "y-skill"); os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "wb") as f: f.write(b"---\nname: \xff\xfe\n---\n")
        self.assertEqual(eva.check(d).verdict, "FAIL")
    def test_malformed_transcripts_fail_cleanly(self):
        cases = ["{bad json", [1, 2], {"scenario": "followup", "final": "x"},
                 {"scenario": "followup", "final": "x", "tool_calls": "web_search"},
                 {"scenario": "followup", "final": "x", "tool_calls": [42]},
                 {"scenario": "followup", "final": "x", "tool_calls": [{"input": "no tool name"}]},
                 {"scenario": "followup", "final": "x", "tool_calls": [{"tool": "web_search", "result": {"a": 1}}]},
                 {"scenario": "followup", "final": "x", "tool_calls": [{"tool": "web_search", "output": "x"}]},
                 {"scenario": "followup", "final": "x", "tool_calls": [], "tool_results": [1]},
                 {"scenario": 3, "final": "x", "tool_calls": []},
                 {"scenario": "followup", "final": None, "tool_calls": []}]
        for i, c in enumerate(cases):
            r = eva.grade_transcript(RA, self.transcript(c, "t%d.json" % i))
            self.assertEqual(r.verdict, "FAIL", c)
            self.assertEqual(self.ids(r, "critical_failed"), {"transcript-valid"}, c)
        rc, _ = self.run_main(["check", RA, "--transcript", self.transcript([1], "cli.json")])
        self.assertEqual(rc, 1)
    def test_transcript_against_malformed_agent(self):
        a = os.path.join(self.tmp, "broken"); os.makedirs(a)
        r = eva.grade_transcript(a, p("transcripts/research-followup-cited.json"))
        self.assertEqual(r.verdict, "FAIL"); self.assertIn("agent-valid", self.ids(r, "critical_failed"))
    def test_transcript_unknown_scenario(self):
        r = eva.grade_transcript(RA, self.transcript({"scenario": "nope", "final": "x", "tool_calls": []}))
        self.assertEqual(r.verdict, "FAIL"); self.assertIn("scenario-known", self.ids(r, "critical_failed"))

class TestFencedSections(unittest.TestCase):
    """Task 5: headings inside fenced code blocks are not sections."""
    def test_backtick_fence_ignored(self):
        self.assertFalse(eva.has_section("# T\n```\n## Example\n```\n", "example"))
        self.assertTrue(eva.has_section("# T\n```md\n## x\n```\n## Example\n", "example"))
    def test_tilde_and_long_fences(self):
        self.assertFalse(eva.has_section("~~~\n## Tools\n~~~\n", "tool"))
        self.assertFalse(eva.has_section("````\n```\n## Tools\n```\n````\n", "tool"))
        self.assertFalse(eva.has_section("```\n~~~\n## Tools\n", "tool"))  # unclosed fence runs to end
    def test_skill_example_only_in_fence_fails(self):
        tmp = tempfile.mkdtemp()
        try:
            d = os.path.join(tmp, "x-skill"); os.makedirs(d)
            wtext(os.path.join(d, "SKILL.md"), "---\nname: x-skill\ndescription: Does x for one file. Use when y.\n"
                  "inputs: [a]\noutputs: [b]\n---\n# X\n```\n## Example\n```\n")
            self.assertIn("example-present", {c["id"] for c in eva.check(d).failed})
        finally:
            shutil.rmtree(tmp)

class TestNoAnswerLookup(TmpMixin, unittest.TestCase):
    """Task 6: searching outside the workspace or mining git history fails the run."""
    def v(self, inp, workspace=None):
        return eva.answer_lookup_violations(run_of([{"tool": "bash", "input": inp}])["calls"], workspace)
    def test_commands_flagged(self):
        for cmd in ("find .. -name '*.py'", "find / -name answer.md", "find ~ -name x", "ls ../other",
                    "cat ../solution/answer.txt", "grep -r foo ../../", "git log --all --oneline", "git fsck --lost-found",
                    "git reflog", "git -C . log -p --all", "git rev-list --all", "git log -g", "git cat-file -p HEAD~3",
                    "cd src && find .. -type f", "rg --path=../x foo", "cat src/../../etc/passwd"):
            self.assertTrue(self.v({"command": cmd}), cmd)
            self.assertTrue(self.v(cmd), cmd)
    def test_path_fields_flagged(self):
        for path in ("..", "../x", "~/notes", "/", "a/../../b"):
            self.assertTrue(self.v({"path": path}), path)
        self.assertTrue(self.v({"file_path": "/etc/passwd"}, workspace="/work/repo"))
        self.assertTrue(self.v({"command": "cat /work/other/x"}, workspace="/work/repo"))
    def test_legit_calls_allowed(self):
        for inp in ({"command": "find . -name '*.py'"}, {"command": "git log -5 --oneline"}, {"command": "git status"},
                    {"command": "grep -rn 'a..b' src"}, {"command": "sed -n 's/../x/p' f"}, {"pattern": "..", "path": "src"},
                    {"path": "src/../tests"}, {"query": "find .. tricks"}, {"command": "git diff HEAD"}):
            self.assertEqual(self.v(inp), [], inp)
        self.assertEqual(self.v({"file_path": "/work/repo/src/a.py"}, workspace="/work/repo"), [])
    def test_bad_fixture_fails_only_on_no_answer_lookup(self):
        r = eva.grade_transcript(CR, p("transcripts/bad/code-reviewer-answer-lookup.json"))
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(self.ids(r, "critical_failed"), {"no-answer-lookup"})
    def test_shell_answer_lookup_transcript_fails(self):
        a = os.path.join(self.tmp, "a"); os.makedirs(os.path.join(a, "scenarios"))
        wtext(os.path.join(a, "AGENT.md"), "---\nname: a\ndescription: d. Use when x.\ngoal: do the one thing well\n"
              "tools: [bash]\nsuccess_criteria:\n  - 파일 변경 0건\n---\n")
        wjson(os.path.join(a, "scenarios", "s.json"), {"id": "s", "turns": [{"user": "q"}], "expected_tools": ["bash"],
              "must_include": ["ok"], "expected_outcome": "파일 변경 0건"})
        ok = {"scenario": "s", "final": "ok", "tool_calls": [{"tool": "bash", "input": {"command": "grep -rn ok ."}, "result": "ok"}]}
        self.assertEqual(eva.grade_transcript(a, self.transcript(ok)).verdict, "PASS")
        for cmd in ("git log --all -p", "git fsck --unreachable", "git reflog show", "find .. -name '*.md'", "find / -name x"):
            bad = json.loads(json.dumps(ok)); bad["tool_calls"].append({"tool": "bash", "input": {"command": cmd}, "result": ""})
            r = eva.grade_transcript(a, self.transcript(bad))
            self.assertEqual(r.verdict, "FAIL", cmd); self.assertEqual(self.ids(r, "critical_failed"), {"no-answer-lookup"}, cmd)

class TestDocs(unittest.TestCase):
    def test_principle_and_integrity_check_documented(self):
        readme = open(p("README.md"), encoding="utf-8").read()
        self.assertIn("## 원칙: 정석으로 (우회하지 않고)", readme)
        self.assertIn("부정행위를 막자는 말이 아닙니다", readme)
        for url in ("https://agentskills.io/specification", "https://agentskills.io/skill-creation/evaluating-skills.md",
                    "https://agentskills.io/skill-creation/optimizing-descriptions.md",
                    "https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/SKILL.md",
                    "https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices",
                    "https://developers.openai.com/api/docs/guides/evaluation-best-practices"):
            self.assertIn(url, readme)
        self.assertIn("정답 조회 무결성 검사 (`no-answer-lookup`", readme)
        self.assertNotIn("no-bypass", readme)
        self.assertIn("agent master", readme); self.assertIn("업계 표준 대비 아직 없는 스킬 기능", readme)
        loop = open(p("LOOP.md"), encoding="utf-8").read()
        self.assertIn("tool_results", loop); self.assertIn("no-answer-lookup", loop); self.assertNotIn("no-bypass", loop)
    def test_every_skill_check_documented(self):
        readme = open(p("README.md"), encoding="utf-8").read()
        for c in eva.check(p("golden/skills/pdf-summarizer")).checks:
            self.assertIn("`%s`" % c["id"], readme, c["id"])

GOOD_FM = ("---\nname: {name}\ndescription: Counts words in one text file. Use when the user wants a word count.\n"
           "{extra}---\n# Word Count\n## Inputs\n- text_path\n## Outputs\n- word_count\n## Example\n입력: `a.txt` → 출력: `42`\n{body}")
GOOD_EVALS = [{"id": i, "prompt": "p%d" % i, "expected_output": "o%d" % i} for i in (1, 2, 3)]

class SkillMixin(TmpMixin):
    def skill(self, name="word-count", extra="", body="", evals="default", files=None, raw=None):
        d = os.path.join(self.tmp, name); os.makedirs(os.path.join(d, "evals"), exist_ok=True)
        wtext(os.path.join(d, "SKILL.md"), raw if raw is not None else GOOD_FM.format(name=name, extra=extra, body=body))
        if evals == "default": evals = {"skill_name": name, "evals": GOOD_EVALS}
        if isinstance(evals, str): wtext(os.path.join(d, "evals", "evals.json"), evals)
        elif evals is not None: wjson(os.path.join(d, "evals", "evals.json"), evals)
        for rel, text in (files or {}).items():
            fp = os.path.join(d, rel); os.makedirs(os.path.dirname(fp), exist_ok=True); wtext(fp, text)
        return eva.check(d)
    def status(self, r, cid):
        return next(c["passed"] for c in r.checks if c["id"] == cid)

class TestSkillSpecFrontmatter(SkillMixin, unittest.TestCase):
    """agentskills.io specification: fields, types, name/description/compatibility rules."""
    def test_base_skill_passes(self):
        r = self.skill(); self.assertEqual(r.verdict, "PASS", r.failed)
    def test_unknown_top_level_fields_fail(self):
        for extra in ("inputs: [a]\n", "version: 1.0.0\n", "tags: [x]\n", "author: me\n"):
            r = self.skill(extra=extra)
            self.assertIn("frontmatter-fields", self.ids(r, "critical_failed"), extra)
            self.assertEqual(r.verdict, "FAIL")
    def test_frontmatter_inputs_no_longer_count_as_declared(self):
        raw = "---\nname: word-count\ndescription: Counts words in one file. Use when asked.\ninputs: [a]\noutputs: [b]\n---\n## Example\nx\n"
        r = self.skill(raw=raw)
        self.assertTrue({"inputs-declared", "outputs-declared", "frontmatter-fields"} <= self.ids(r))
    def test_optional_fields_allowed(self):
        r = self.skill(extra="license: Apache-2.0\ncompatibility: Requires Python 3.9+\nallowed-tools: Bash(git:*) Read\n"
                             "metadata:\n  author: example-org\n  version: \"1.0.0\"\n")
        self.assertEqual(r.verdict, "PASS", r.failed)
    def test_field_types(self):
        for extra in ("allowed-tools: [Read, Bash]\n", "metadata: v1\n", "license:\n",
                      "metadata:\n  tags: [a, b]\n", "compatibility: 3\n"):
            r = self.skill(extra=extra)
            self.assertIn("field-types", self.ids(r, "critical_failed"), extra)
        self.assertTrue(self.status(self.skill(extra="metadata:\n  version: \"1.0\"\n"), "field-types"))
        self.assertTrue(self.status(self.skill(extra="metadata:\n  version: 1.0.0\n"), "field-types"))
    def test_metadata_values_not_forced_quoted(self):
        # skills-ref parses with strictyaml, which reads every scalar as a string (and str()s metadata values)
        for extra in ("metadata:\n  version: 1.0\n", "metadata:\n  released: 2025-01-01\n", "metadata:\n  beta: yes\n",
                      "license: 2025\n", "allowed-tools: true\n"):
            r = self.skill(extra=extra)
            self.assertTrue(self.status(r, "field-types"), extra)
        r = self.skill(raw=GOOD_FM.format(name="word-count", extra="", body="").replace("name: word-count", "name: 123"))
        self.assertIn("field-types", self.ids(r, "critical_failed"))
    def test_yaml_plain_scalar_rules(self):
        for line in ("description: Counts words: fast. Use when asked.",
                     "description: *alias", "description: @handle use when", "description: - item", "description: [a, b"):
            raw = GOOD_FM.format(name="word-count", extra="", body="").replace(
                "description: Counts words in one text file. Use when the user wants a word count.", line)
            meta, _, err = eva.parse_frontmatter_ex(raw)
            self.assertIsNone(meta, line); self.assertIn("quote", err)
        self.assertIsNone(eva.parse_frontmatter_ex("---\nmetadata:\n\tversion: \"1\"\n---\n")[0])
        meta, _, err = eva.parse_frontmatter_ex('---\ndescription: "Counts words: fast. Use when asked."\nurl: a:b\n---\n')
        self.assertIsNone(err); self.assertEqual(meta["description"], "Counts words: fast. Use when asked.")
    def test_yaml_comments(self):
        # ' #' starts a YAML comment (ruamel/strictyaml, PyYAML): the value ends before it; '#' without a space does not
        meta, _, err = eva.parse_frontmatter_ex('---\nname: a # c\nd: "x #y" # z\nmetadata:\n  v: "1" # c\nu: a#b\n---\n')
        self.assertIsNone(err)
        self.assertEqual((meta["name"], meta["d"], meta["metadata"]["v"], meta["u"]), ("a", "x #y", "1", "a#b"))
    def test_yaml_typed_keys_match_pyyaml(self):
        yaml = require_yaml(self)
        for v in ("yes", "No", "on", "true", "~", "null", "0", "12", "-3", "1_000", "017", "0x1F", "0b101", "1.0", ".5",
                  "1e5", "1.0e+5", "-.5", ".inf", ".nan", "2024-01-01", "2024-01-01 10:00:00", "1:30", "y", "n", ".",
                  "2024-1-1", "1.0.0", "v1", "abc", "1.2.3-beta", "08"):
            typed = not isinstance(yaml.safe_load("k: %s" % v)["k"], str)
            self.assertEqual(eva.yaml_typed_keys("---\nk: %s\nm:\n  k: %s\n---\n" % (v, v)) == ["k", "m.k"], typed, v)
    def test_name_rules(self):
        for bad in ("Word-Count", "-word", "word-", "word--count", "a" * 65, "word_count"):
            r = self.skill(raw=GOOD_FM.format(name=bad, extra="", body=""), name="x-" + str(len(bad)))
            self.assertIn("name-format", self.ids(r, "critical_failed"), bad)
        self.assertEqual(self.skill(name="a" * 64).verdict, "PASS")
    def test_name_unicode_per_skills_ref(self):
        # skills-ref validator._validate_name: NFKC, lowercase, str.isalnum() or '-'
        for good in ("한국어-교정", "café-tool", "données2"):
            r = self.skill(name=good)
            self.assertTrue(self.status(r, "name-format"), good); self.assertTrue(self.status(r, "name-matches-folder"), good)
        for bad in ("Café", "ÉCOLE-x", "word.count", "word count", "a★b", "ｗｏｒｄ--ｃｏｕｎｔ"):
            r = self.skill(raw=GOOD_FM.format(name=bad, extra="", body=""), name="x-bad")
            self.assertIn("name-format", self.ids(r, "critical_failed"), bad)
        self.assertEqual(eva.name_errors("a" * 64), []); self.assertTrue(eva.name_errors("ａ" * 65))  # length after NFKC
    def test_name_nfkc_matches_folder(self):
        r = self.skill(raw=GOOD_FM.format(name="ｗｏｒｄ-ｃｏｕｎｔ", extra="", body=""), name="word-count")  # fullwidth
        self.assertTrue(self.status(r, "name-format")); self.assertTrue(self.status(r, "name-matches-folder"))
    def test_reserved_name_words(self):
        for name in ("claude-helper", "my-anthropic-tools"):
            r = self.skill(name=name)
            self.assertEqual(self.ids(r, "critical_failed"), {"name-reserved"}, name)
    def test_description_no_xml(self):
        raw = GOOD_FM.format(name="word-count", extra="", body="").replace("one text file", "one <file> tag")
        self.assertEqual(self.ids(self.skill(raw=raw), "critical_failed"), {"description-no-xml"})
    def test_compatibility_length(self):
        self.assertIsNone(self.status(self.skill(), "compatibility-length"))
        self.assertTrue(self.status(self.skill(extra="compatibility: %s\n" % ("x" * 500)), "compatibility-length"))
        r = self.skill(extra="compatibility: %s\n" % ("x" * 501))
        self.assertEqual(self.ids(r, "critical_failed"), {"compatibility-length"})
    def test_size_limit_under_500_lines(self):
        head = GOOD_FM.format(name="word-count", extra="", body="")
        pad = 499 - len(head.splitlines())
        self.assertTrue(self.status(self.skill(raw=head + "x\n" * pad), "size-limit"))
        self.assertFalse(self.status(self.skill(raw=head + "x\n" * (pad + 1)), "size-limit"))

class TestSkillSpecReferences(SkillMixin, unittest.TestCase):
    """File references: relative, existing, one level deep (no chains), TOC for long reference files."""
    def test_generic_relative_links_checked(self):
        r = self.skill(body="See [forms](FORMS.md) and [docs](https://example.org/x.md) and [top](#example).\n")
        self.assertIn("FORMS.md", next(c["reason"] for c in r.failed if c["id"] == "refs-exist"))
        r = self.skill(body="See [forms](FORMS.md#fill).\n", files={"FORMS.md": "# Forms\n"})
        self.assertTrue(self.status(r, "refs-exist"))
    def test_links_inside_fences_ignored(self):
        self.assertTrue(self.status(self.skill(body="```md\n[x](missing.md)\n```\n"), "refs-exist"))
    def test_refs_relative(self):
        for ref in ("references\\\\guide.md", "/abs/guide.md", "../other-skill/guide.md", "~/guide.md"):
            r = self.skill(body="See [g](%s).\n" % ref)
            self.assertFalse(self.status(r, "refs-relative"), ref)
        self.assertFalse(self.status(self.skill(body="Run `scripts\\\\run.py`.\n"), "refs-relative"))
        self.assertTrue(self.status(self.skill(body="See [g](references/guide.md).\n",
                                               files={"references/guide.md": "# G\n"}), "refs-relative"))
    def test_nested_reference_chain_fails(self):
        files = {"references/advanced.md": "# A\nSee [details](references/details.md).\n", "references/details.md": "# D\n"}
        r = self.skill(body="See [advanced](references/advanced.md).\n", files=files)
        self.assertFalse(self.status(r, "refs-one-level"))
        self.assertIn("references/advanced.md -> references/details.md",
                      next(c["reason"] for c in r.failed if c["id"] == "refs-one-level"))
        r = self.skill(body="See [advanced](references/advanced.md).\n",
                       files={"references/advanced.md": "# A\nSee [details](details.md).\n", "references/details.md": "# D\n"})
        self.assertFalse(self.status(r, "refs-one-level"))  # relative to the referencing file
    def test_chain_ok_when_also_linked_from_skill(self):
        files = {"references/advanced.md": "# A\nSee [details](references/details.md).\n", "references/details.md": "# D\n"}
        r = self.skill(body="See [advanced](references/advanced.md) and [details](references/details.md).\n", files=files)
        self.assertTrue(self.status(r, "refs-one-level"))
    def test_path_depth_still_enforced(self):
        r = self.skill(body="See [g](references/a/guide.md).\n", files={"references/a/guide.md": "# G\n"})
        self.assertFalse(self.status(r, "refs-path-depth"))
        self.assertTrue(self.status(r, "refs-one-level"))
    def test_reference_toc(self):
        long_body = "\n".join("line %d" % i for i in range(eva.REF_TOC_LINES))
        self.assertIsNone(self.status(self.skill(body="See [g](references/g.md).\n",
                                                 files={"references/g.md": "# G\n" + "x\n" * 50}), "reference-toc"))
        r = self.skill(body="See [g](references/g.md).\n", files={"references/g.md": "# G\n## Setup\n" + long_body})
        self.assertFalse(self.status(r, "reference-toc"))
        r = self.skill(body="See [g](references/g.md).\n",
                       files={"references/g.md": "# G\n## Contents\n- Setup\n## Setup\n" + long_body})
        self.assertTrue(self.status(r, "reference-toc"))
        r = self.skill(body="See [g](references/g.md).\n",
                       files={"references/g.md": "# G\n## Setup\n## Contents\n" + long_body})
        self.assertFalse(self.status(r, "reference-toc"))  # TOC must come first

class TestSkillEvals(SkillMixin, unittest.TestCase):
    """evals/evals.json per skill-creator / agentskills.io; required with >= MIN_EVALS cases."""
    def test_missing_evals_fails(self):
        r = self.skill(evals=None)
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(self.ids(r, "critical_failed"), {"evals-valid", "evals-count"})
    def test_too_few_cases_fails(self):
        r = self.skill(evals={"skill_name": "word-count", "evals": GOOD_EVALS[:2]})
        self.assertEqual(self.ids(r, "critical_failed"), {"evals-count"})
    def evals_err(self, evals, files=None):
        r = self.skill(evals=evals, files=files)
        self.assertIn("evals-valid", self.ids(r, "critical_failed"), evals)
        return next(c["reason"] for c in r.failed if c["id"] == "evals-valid")
    def test_format_errors(self):
        self.assertIn("top level must be an object", self.evals_err(GOOD_EVALS))
        self.assertIn("skill_name", self.evals_err({"skill_name": "other", "evals": GOOD_EVALS}))
        self.assertIn("skill_name", self.evals_err({"evals": GOOD_EVALS}))
        bad = lambda **kw: {"skill_name": "word-count", "evals": [dict(GOOD_EVALS[0], **kw)] + GOOD_EVALS[1:]}
        self.assertIn("id: integer", self.evals_err(bad(id="1")))
        self.assertIn("id: integer", self.evals_err(bad(id=True)))
        self.assertIn("duplicate ids [2]", self.evals_err(bad(id=2)))
        self.assertIn("prompt", self.evals_err(bad(prompt="")))
        self.assertIn("expected_output", self.evals_err(bad(expected_output=None)))
        self.assertIn("not found", self.evals_err(bad(files=["evals/files/missing.csv"])))
        self.assertIn("outside", self.evals_err(bad(files=["../x.csv"])))
        self.assertIn("assertions", self.evals_err(bad(assertions="x")))
        self.assertIn("expectations", self.evals_err(bad(expectations=[])))
        self.assertIn("invalid JSON", self.evals_err("{oops"))
    def test_valid_files_and_assertions(self):
        ev = {"skill_name": "word-count", "evals": [dict(GOOD_EVALS[0], files=["evals/files/a.txt"], assertions=["count is 2"]),
                                                    dict(GOOD_EVALS[1], expectations=["x"]), GOOD_EVALS[2]]}
        r = self.skill(evals=ev, files={"evals/files/a.txt": "two words"})
        self.assertEqual(r.verdict, "PASS", r.failed)
    def test_goldens_have_real_evals(self):
        for d in ("csv-cleaner", "korean-proofreader", "pdf-summarizer"):
            n, err = eva.evals_errors(p("golden/skills", d), d)
            self.assertEqual(err, [], d); self.assertGreaterEqual(n, eva.MIN_EVALS, d)

class TestCitedLinesExist(TmpMixin, unittest.TestCase):
    """P1: a file:line citation counts only if that line appears in a captured, line-numbered tool result."""
    DRILL = p("transcripts/code-reviewer-drill.json")
    WS = p("transcripts/fixtures/drill-repo")  # frozen workspace the drill transcript reads (not this test file)
    def test_fixture_cites_real_lines(self):
        run, err = eva.load_transcript(self.DRILL)
        self.assertFalse(err)
        cites = eva.FILELINE_RE.findall(run["final"])
        self.assertGreaterEqual(len(cites), 3)
        for f, ln, _ in cites:
            with open(os.path.join(self.WS, f), encoding="utf-8") as fh: real = fh.read().splitlines()
            ln = int(ln); self.assertLessEqual(ln, len(real), (f, ln))
            shown = [res for res in run["results"] if ("%s:%d:" % (f, ln)) in res or ("%6d\t" % ln) in res]
            self.assertTrue(shown, (f, ln))
            self.assertIn(real[ln - 1].strip(), "\n".join(shown), (f, ln))  # tool output is the real file content
    def drill_with_final(self, final):
        t = rjson(self.DRILL); t["final"] = final
        return eva.grade_transcript(CR, self.transcript(t))
    def cited_file(self):
        run, _ = eva.load_transcript(self.DRILL)
        return eva.FILELINE_RE.findall(run["final"])[0][0]
    def test_nonexistent_lines_fail(self):
        f = self.cited_file()
        r = self.drill_with_final("%s:99999, %s:99998, %s:99997 test 파일 변경 없음" % (f, f, f))
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(self.ids(r, "critical_failed"), {"criteria-file-lines", "expected-file-lines"})
        self.assertIn("%s:99999" % f, next(c["reason"] for c in r.failed if c["id"] == "expected-file-lines"))
    def test_wrong_line_numbers_fail(self):
        run, _ = eva.load_transcript(self.DRILL)
        good = sorted({int(ln) for _, ln, _ in eva.FILELINE_RE.findall(run["final"])})
        wrong = [ln + 1000 for ln in good]  # same file, lines the tools never showed
        r = self.drill_with_final("test: " + ", ".join("%s:%d" % (self.cited_file(), ln) for ln in wrong))
        self.assertEqual(r.verdict, "FAIL")
        self.assertTrue({"criteria-file-lines", "expected-file-lines"} <= self.ids(r, "critical_failed"))
    def test_one_wrong_line_drops_below_floor(self):
        run, _ = eva.load_transcript(self.DRILL)
        f, ln, _ = eva.FILELINE_RE.findall(run["final"])[0]
        r = self.drill_with_final(run["final"].replace("%s:%s" % (f, ln), "%s:%d" % (f, int(ln) + 1000), 1))
        self.assertEqual(self.ids(r, "critical_failed"), {"expected-file-lines"})  # 2 verified < 3 required
    def test_file_name_alone_is_not_evidence(self):
        run = run_of([{"tool": "read_file", "input": {"path": "src/app.py"}, "result": "file opened"}])
        self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/app.py:3", run)[1])
        run = run_of([{"tool": "read_file", "input": {"path": "src/app.py"}}], results=["src/app.py"])
        self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/app.py:3", run)[1])
    def test_numbered_formats(self):
        # Round 5: 'N text' (space separator) is indistinguishable from raw content ('200 OK') and no longer counts
        self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/app.py:3", run_of(
            [{"tool": "read_file", "input": {"file_path": "/work/repo/src/app.py"}, "result": "3 x = 1"}], workspace="/work/repo"))[1])
        for res in ("     3\tx = 1", "3→x = 1", "3: x = 1"):
            run = run_of([{"tool": "read_file", "input": {"file_path": "/work/repo/src/app.py"}, "result": res}], workspace="/work/repo")
            self.assertTrue(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/app.py:3", run)[1], res)
            self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/app.py:4", run)[1], res)
            self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/other.py:3", run)[1], res)
    def test_grep_lines_and_ranges(self):
        run = run_of([{"tool": "bash", "input": {"command": "grep -n -A1 foo src/a.py"}, "result": "10:foo\n11-bar"}])
        ev = lambda final: eva.outcome_evidence("파일:줄 근거 1개 이상", final, run)[1]
        self.assertTrue(ev("src/a.py:10")); self.assertTrue(ev("src/a.py:10-11"))
        self.assertFalse(ev("src/a.py:10-12")); self.assertFalse(ev("src/b.py:10"))
        # Round 4: recursive grep reads many files, and top-level tool_results belong to no read call -> never count
        run = run_of([{"tool": "bash", "input": {"command": "grep -rn -A1 foo src"}, "result": "src/a.py:10:foo\nsrc/a.py-11-bar"}])
        self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/a.py:10", run)[1])
        run = run_of(["grep"], results=["src/a.py:10:foo", "7: bar"])
        self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/a.py:10", run)[1])
        self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/a.py:7", run)[1])

class TestNeedsReview(TmpMixin, unittest.TestCase):
    """P2: unmeasurable criteria are needs-review (not N/A, not PASS); verdict REVIEW, exit code 3."""
    def test_restored_criteria_present(self):
        for agent, crit in ((CR, "모든 지적에 파일:줄 근거를 단다"), (RA, "모든 주장에 출처를 단다")):
            meta, _ = eva.parse_frontmatter(open(os.path.join(agent, "AGENT.md"), encoding="utf-8").read())
            self.assertIn(crit, meta["success_criteria"])
            self.assertTrue(any(eva.outcome_evidence(c, "") for c in meta["success_criteria"]))  # count floor kept
    def test_verdict_order(self):
        r = eva.Report("x", "transcript"); r.add("a", "goal", True, "")
        self.assertEqual(r.verdict, "PASS")
        r.review("b", "goal", "why", "fix")
        self.assertEqual(r.verdict, "REVIEW"); self.assertEqual(r.score, 1.0)
        r.add("c", "goal", False, "", "fix", must=True)
        self.assertEqual(r.verdict, "FAIL")  # machine failure wins over review
        only = eva.Report("x", "transcript"); only.review("b", "goal", "why", "fix")
        self.assertEqual(only.verdict, "FAIL")  # nothing machine-checked
    def test_review_status_in_json_and_cli(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = eva.main(["check", CR, "--transcript", p("transcripts/code-reviewer-drill.json"), "--json"])
        d = json.loads(out.getvalue())
        self.assertEqual((rc, d["verdict"], d["needs_review"]), (3, "REVIEW", ["criteria-needs-review"]))
        self.assertEqual(next(c for c in d["checks"] if c["id"] == "criteria-needs-review")["status"], "needs-review")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eva.main(["check", RA, "--transcript", p("transcripts/research-followup.json")]), 1)
    def test_readme_documents_review(self):
        readme = open(p("README.md"), encoding="utf-8").read()
        for s in ("needs-review", "REVIEW", "종료코드 3"):
            self.assertIn(s, readme)

class TestPlainTextRefs(SkillMixin, unittest.TestCase):
    """P3: fenced code excluded first; links, inline code and plain-text bundled-dir paths are references."""
    def test_plain_text_missing_ref_fails(self):
        for body in ("Run scripts/missing.py.\n", "Run scripts/missing.py on the input.\n", "Read references/missing.md, then go.\n",
                     "Use `scripts/missing.py --in a.csv` here.\n", "Template: assets/missing.txt\n"):
            r = self.skill(body=body)
            self.assertFalse(self.status(r, "refs-exist"), body)
    def test_plain_text_existing_ref_passes(self):
        r = self.skill(body="Run scripts/run.py, then read references/guide.md.\n",
                       files={"scripts/run.py": "print(1)\n", "references/guide.md": "# G\n"})
        self.assertTrue(self.status(r, "refs-exist")); self.assertEqual(r.verdict, "PASS", r.failed)
    def test_not_references(self):
        for body in ("```\nRun scripts/missing.py\n```\n", "~~~text\nwrote references/missing.md\n~~~\n",
                     "See https://example.org/scripts/missing.py\n", "Not ours: my-scripts/x.py and lib/scripts/y.py\n",
                     "Put helpers in scripts/ and docs in `references/`.\n", "Images: assets/*.png\n",
                     "Pattern scripts/<name>.py\n", "Heading [top](#example)\n"):
            r = self.skill(body=body)
            self.assertTrue(self.status(r, "refs-exist"), (body, r.failed))
    def test_plain_text_relative_problems(self):
        self.assertFalse(self.status(self.skill(body="Run scripts\\x.py.\n"), "refs-relative"))
    def test_golden_csv_cleaner_script_detected(self):
        text = open(p("golden/skills/csv-cleaner/SKILL.md"), encoding="utf-8").read()
        self.assertIn("scripts/clean.py", eva.local_refs(eva.parse_frontmatter(text)[1]))

class TestScenarioReadErrors(TmpMixin, unittest.TestCase):
    """P5: unreadable scenario files produce a FAIL report instead of a crash."""
    def agent(self):
        a = os.path.join(self.tmp, "code-reviewer"); shutil.copytree(CR, a); return a
    def test_undecodable_scenario(self):
        a = self.agent()
        with open(os.path.join(a, "scenarios", "03-bad.json"), "wb") as f: f.write(b'{"id": "\xff\xfe"}')
        r = eva.check(a)
        self.assertEqual(r.verdict, "FAIL"); self.assertIn("scenarios-valid", self.ids(r, "critical_failed"))
        self.assertIn("cannot read 03-bad.json", next(c["reason"] for c in r.failed if c["id"] == "scenarios-valid"))
    def test_unreadable_scenario_oserror(self):
        a = self.agent(); os.makedirs(os.path.join(a, "scenarios", "03-dir.json"))  # open() raises IsADirectoryError
        r = eva.check(a)
        self.assertEqual(r.verdict, "FAIL"); self.assertIn("scenarios-valid", self.ids(r, "critical_failed"))
        rr = eva.grade_transcript(a, p("transcripts/code-reviewer-drill.json"))  # other scenarios still gradable
        self.assertEqual(rr.failed, [])

class TestAtxHeadings(unittest.TestCase):
    """P5: CommonMark ATX headings: up to 3 leading spaces, 1-6 '#', then a space/tab or end of line."""
    def test_indent(self):
        for n in range(4):
            self.assertTrue(eva.has_section(" " * n + "## Example\n", "example"), n)
        self.assertFalse(eva.has_section("    ## Example\n", "example"))  # 4 spaces = indented code
    def test_space_after_hashes_required(self):
        self.assertFalse(eva.has_section("##Example\n", "example"))
        self.assertFalse(eva.has_section("#Example #tag\n", "example"))
        self.assertFalse(eva.has_section("####### Example\n", "example"))  # 7 '#' is not a heading
        self.assertTrue(eva.has_section("##\tExample ##\n", "example"))
        self.assertEqual(eva.headings("## Inputs ##\n#\n"), [(2, "Inputs"), (1, "")])

def cites_ok(final, calls, n=1):
    return eva.outcome_evidence("파일:줄 근거 %d개 이상" % n, final, run_of(calls))[1]

class TestCitationSingleFile(unittest.TestCase):
    """Round 3 (a): numbered lines count only for a call that read exactly one file."""
    def test_cat_n_single_file_counts(self):
        for cmd in ("cat -n src/a.py", "cat --number src/a.py", "nl -ba src/a.py", "nl -b a src/a.py", "nl -ba -w 3 src/a.py"):
            calls = [{"tool": "bash", "input": {"command": cmd}, "result": "     5\tx = 1"}]
            self.assertTrue(cites_ok("src/a.py:5", calls), cmd)
            self.assertFalse(cites_ok("src/b.py:5", calls), cmd)
        self.assertTrue(cites_ok("src/a.py:5", [{"tool": "bash", "input": "cat -n src/a.py", "result": "5\tx"}]))
    def test_multi_file_cat_rejected(self):
        calls = [{"tool": "bash", "input": {"command": "cat -n src/a.py src/b.py"}, "result": "     5\tx = 1\n     6\ty"}]
        self.assertFalse(cites_ok("src/a.py:5", calls)); self.assertFalse(cites_ok("src/b.py:6", calls))
        calls = [{"tool": "read_file", "input": {"paths": ["src/a.py", "src/b.py"]}, "result": "5\tx"}]
        self.assertFalse(cites_ok("src/a.py:5", calls))
        calls = [{"tool": "read_file", "input": {"path": "src/a.py", "command": "cat -n src/b.py"}, "result": "5\tx"}]
        self.assertFalse(cites_ok("src/a.py:5", calls))
    def test_wc_and_other_commands_rejected(self):
        for cmd, res in (("wc -l src/a.py", "      42 src/a.py"), ("wc -l src/a.py src/b.py", "  42 src/a.py\n  7 src/b.py"),
                         ("head -n 50 src/a.py", "42 x"), ("grep -c foo src/a.py", "42"),
                         ("sed -n 40,45p src/a.py | cat -n", "     1\tx\n    42\ty"), ("cat -n src/a.py > out.txt", "42\tx"),
                         ("cat -b src/a.py", "    42\tx"), ("cat -ns src/a.py", "    42\tx"), ("nl src/a.py", "    42\tx"),
                         ("nl -ba -v 10 src/a.py", "    42\tx"), ("cat -n -", "42\tx"), ("cat -n 'src/a.py", "42\tx")):
            calls = [{"tool": "bash", "input": {"command": cmd}, "result": res}]
            self.assertFalse(cites_ok("src/a.py:42", calls), cmd)
            self.assertFalse(cites_ok("src/a.py:7", calls), cmd)
    def test_wc_style_line_on_path_tool_rejected(self):
        calls = [{"tool": "wc", "input": {"path": "src/a.py"}, "result": "42 src/a.py"}]
        self.assertFalse(cites_ok("src/a.py:42", calls))
        calls = [{"tool": "read_file", "input": {"path": "src/a.py"}, "result": "42\treturn src/a.py"}]
        self.assertTrue(cites_ok("src/a.py:42", calls))  # real content that merely mentions a path still counts
        calls = [{"tool": "read_file", "input": {"path": "src/a.py"}, "result": "42\tsrc/a.py"}]
        self.assertFalse(cites_ok("src/a.py:42", calls))  # 'N<tab>path' is a count
    def test_multi_file_grep_rejected(self):
        # Round 4: a line counts only from a call that read exactly that one file (was: path-prefixed grep lines counted)
        calls = [{"tool": "bash", "input": {"command": "grep -n foo src/a.py src/b.py"}, "result": "src/a.py:3:foo\nsrc/b.py:9:foo"}]
        self.assertFalse(cites_ok("src/a.py:3", calls)); self.assertFalse(cites_ok("src/b.py:9", calls))
        self.assertFalse(cites_ok("src/a.py:9", calls))

class TestRefDetectionRound3(SkillMixin, unittest.TestCase):
    """Round 3 (b): Korean particles, **bold** paths, run commands inside fenced code."""
    def refs(self, body):
        return eva.local_refs(body)
    def test_korean_particles_stripped(self):
        for body in ("scripts/clean.py를 실행한다.\n", "`scripts/clean.py`를 실행한다.\n", "scripts/clean.py에서 읽는다.\n",
                     "scripts/clean.py가 만든다.\n", "scripts/clean.py로, 그다음\n", "scripts/clean.py와 references/g.md를 본다.\n"):
            self.assertIn("scripts/clean.py", self.refs(body), body)
            self.assertFalse(any(r != "scripts/clean.py" and r.startswith("scripts/") for r in self.refs(body)), body)
        self.assertIn("references/g.md", self.refs("scripts/clean.py와 references/g.md를 본다.\n"))
        r = self.skill(body="먼저 scripts/run.py를 실행하고 references/guide.md를 읽는다.\n",
                       files={"scripts/run.py": "print(1)\n", "references/guide.md": "# G\n"})
        self.assertTrue(self.status(r, "refs-exist"), r.failed)
        self.assertFalse(self.status(self.skill(body="scripts/missing.py를 실행한다.\n"), "refs-exist"))
    def test_korean_filename_kept(self):
        self.assertIn("references/가이드.md", self.refs("references/가이드.md를 읽는다.\n"))
    def test_bold_paths(self):
        for body in ("Run **scripts/x.py** first.\n", "**scripts/x.py**를 실행\n", "*scripts/x.py*\n", "***scripts/x.py***.\n"):
            self.assertEqual(self.refs(body), {"scripts/x.py"}, body)
        self.assertFalse(self.status(self.skill(body="Run **scripts/missing.py**.\n"), "refs-exist"))
        self.assertEqual(self.refs("**scripts/*.py** and **assets/**\n"), set())  # glob / directory stay non-refs
    def test_run_commands_in_fences(self):
        for line, ref in (("python scripts/x.py --in a.csv", "scripts/x.py"), ("python3 -u scripts/x.py", "scripts/x.py"),
                          ("$ bash assets/setup.sh", "assets/setup.sh"), ("uv run python scripts/x.py", "scripts/x.py"),
                          ("./scripts/run.sh arg", "scripts/run.sh"), ("scripts/run.sh", "scripts/run.sh"),
                          ("  node scripts/x.js; echo ok", "scripts/x.js"), ("sudo python scripts/x.py", "scripts/x.py")):
            for fence in ("```bash\n%s\n```\n", "~~~\n%s\n~~~\n"):
                self.assertEqual(self.refs(fence % line), {ref}, (fence, line))
        r = self.skill(body="```bash\npython scripts/missing.py\n```\n")
        self.assertFalse(self.status(r, "refs-exist"))
        self.assertIn("scripts/missing.py", next(c["reason"] for c in r.failed if c["id"] == "refs-exist"))
        r = self.skill(body="```bash\npython scripts/x.py\n```\n", files={"scripts/x.py": "print(1)\n"})
        self.assertTrue(self.status(r, "refs-exist"))
    def test_non_run_fence_lines_ignored(self):
        for body in ("```\n├── scripts/\n│   └── x.py\n```\n", "```\nsee scripts/x.py\n```\n", "```\ncat references/x.md\n```\n",
                     "```\npython my-scripts/x.py\n```\n", "```\npython scripts/<name>.py\n```\n", "```\n# scripts/x.py\n```\n"):
            self.assertEqual(self.refs(body), set(), body)

def _fm(body):
    return "---\n%s\n---\n" % body

class TestYamlParity(unittest.TestCase):
    """Round 3 (c): frontmatter values match a real YAML parser for '#'-leading values and escaped quotes."""
    CASES = ['k: #comment', 'k:   # comment', 'k: "a \\"b\\" c"', 'k: "a \\"b\\" # c" # real comment',
             "k: 'it''s'", "k: 'it''s # x' # c", 'k: "tab\\there"', 'k: "back\\\\slash"', 'k: "\\u00e9t\\u00e9"',
             'k: "a#b"', 'k: a#b', 'k: "x" # c', 'k: plain # c', "k: '#not comment'", 'k: "#not comment"',
             'k: "say \\"hi\\""', 'k: "\\x41"', 'k: "\\/"']
    def test_scalar_values_match_pyyaml(self):
        yaml = require_yaml(self)
        for line in self.CASES:
            want = yaml.safe_load(line)["k"]
            meta, _, err = eva.parse_frontmatter_ex(_fm(line))
            self.assertIsNone(err, line); self.assertEqual(meta["k"], want, line)
            meta, _, err = eva.parse_frontmatter_ex(_fm("m:\n  " + line))
            self.assertIsNone(err, line)
            self.assertEqual(meta["m"]["k"], yaml.safe_load("m:\n  " + line)["m"]["k"], line)
    def test_errors_match_pyyaml(self):
        yaml = require_yaml(self)
        for line in ('k: "unterminated', 'k: "a" b', "k: 'a' b", 'k: "bad \\q escape"', 'k: "a \\"'):
            with self.assertRaises(yaml.YAMLError, msg=line): yaml.safe_load(line)
            meta, _, err = eva.parse_frontmatter_ex(_fm(line))
            self.assertIsNone(meta, line); self.assertTrue(err, line)
    def test_hash_value_is_empty_description(self):
        d = tempfile.mkdtemp()
        try:
            s = os.path.join(d, "x-skill"); os.makedirs(s)
            wtext(os.path.join(s, "SKILL.md"), "---\nname: x-skill\ndescription: #TODO write this\n---\n# X\n")
            r = eva.check(s)
            self.assertIn("description-present", {c["id"] for c in r.critical_failed})
        finally:
            shutil.rmtree(d)
    def test_escaped_quote_description(self):
        meta, _, err = eva.parse_frontmatter_ex(_fm('description: "Fixes \\"smart\\" quotes. Use when text has them." # c'))
        self.assertIsNone(err); self.assertEqual(meta["description"], 'Fixes "smart" quotes. Use when text has them.')

class TestDescriptionChecks(SkillMixin, unittest.TestCase):
    """Round 3 (e): first/second person, >1024 chars and missing 'when' are FAIL; verb form is free."""
    def desc(self, d):
        raw = GOOD_FM.format(name="word-count", extra="", body="").replace(
            "description: Counts words in one text file. Use when the user wants a word count.", "description: %s" % d)
        return self.skill(raw=raw)
    def test_verb_forms_free(self):
        for d in ("Counts words in one text file. Use when the user wants a word count.",
                  "Count words in one text file. Use this skill when the user wants a word count.",
                  "Word counting for one text file. Used when the user wants a word count.",
                  "한국어 글의 낱말 수를 센다. 낱말 수를 물을 때 사용한다. Use when the user wants a word count.",
                  "Counts words (I/O safe). Use when the user wants a word count."):
            r = self.desc(d); self.assertEqual(r.verdict, "PASS", (d, r.failed))
    def test_person_wording_fails(self):
        for d, word in (("I can help you count words. Use when asked.", "I"),
                        ("You can use this to count words. Use when asked.", "You"),
                        ("Counts words for your files. Use when asked.", "your"),
                        ("We count words in a file. Use when asked.", "We"),
                        ("Counts words in my notes. Use when asked.", "my"),
                        ("Counts words. Use when you need a count.", "you"),
                        ("I'll count words in a file. Use when asked.", "I"),
                        ("제가 낱말 수를 세어 드립니다. 낱말 수를 물을 때 사용한다.", "제가"),
                        ("당신의 글에서 낱말 수를 센다. 낱말 수를 물을 때 사용한다.", "당신"),
                        ("우리는 낱말 수를 센다. 낱말 수를 물을 때 사용한다.", "우리는")):
            r = self.desc(d)
            self.assertEqual(r.verdict, "FAIL", d)
            self.assertIn("description-pov", self.ids(r, "critical_failed"), d)
            self.assertIn(word, next(c["reason"] for c in r.failed if c["id"] == "description-pov"), d)
    def test_third_person_words_not_flagged(self):
        for d in ("Converts US dollars and measures hours of usage. Use when asked.", "Reads README.md. Use when asked.",
                  "문제가 있는 문서의 안내가 맞는지 본다. 사용자가 물을 때 사용한다.", "우리말 맞춤법을 고친다. 교정을 요청할 때 사용한다."):
            self.assertTrue(self.status(self.desc(d), "description-pov"), d)
    def test_over_1024_fails(self):
        base = "Counts words in one text file. Use when the user wants a word count. "
        ok = (base * 20)[:1024].strip()
        self.assertLessEqual(len(ok), 1024)
        self.assertTrue(self.status(self.desc(ok), "description-length"))
        long = base * 20
        r = self.desc(long.strip() + " more")
        self.assertGreater(len(long.strip() + " more"), 1024)
        self.assertEqual(r.verdict, "FAIL"); self.assertIn("description-length", self.ids(r, "critical_failed"))
    def test_missing_when_fails(self):
        r = self.desc("Counts words in one text file and prints the total.")
        self.assertEqual(r.verdict, "FAIL"); self.assertEqual(self.ids(r, "critical_failed"), {"description-when"})
    def test_min_length_is_scored_house_rule(self):
        r = self.desc("Use when counting.")
        c = next(c for c in r.checks if c["id"] == "description-min-length")
        self.assertFalse(c["passed"]); self.assertFalse(c["must"])
        self.assertNotIn("description-min-length", self.ids(r, "critical_failed"))

class TestRound3Misc(SkillMixin, unittest.TestCase):
    """Round 3 (f): empty description is not 'None', listdir errors, refs-one-level at spec level, fixture coupling."""
    def test_empty_description_not_none_string(self):
        for line in ("description:", "description: ", "description: # later", 'description: ""'):
            raw = GOOD_FM.format(name="word-count", extra="", body="").replace(
                "description: Counts words in one text file. Use when the user wants a word count.", line)
            r = self.skill(raw=raw)
            self.assertIn("description-present", self.ids(r, "critical_failed"), line)
            self.assertNotIn("None", next(c["reason"] for c in r.checks if c["id"] == "description-length"), line)
            self.assertEqual(r.verdict, "FAIL")
        raw = GOOD_FM.format(name="word-count", extra="", body="").replace("name: word-count", "name:")
        r = self.skill(raw=raw)
        self.assertIn("name-format", self.ids(r, "critical_failed"))
        self.assertIn("name ''", next(c["reason"] for c in r.checks if c["id"] == "name-format"))
    def test_empty_agent_fields(self):
        a = os.path.join(self.tmp, "a"); os.makedirs(a)
        wtext(os.path.join(a, "AGENT.md"), "---\nname: a\ndescription:\ngoal:\ntools: [t]\n---\n")
        r = eva.check(a)
        self.assertTrue({"description-present", "goal-present"} <= self.ids(r))
    def test_listdir_permission_error(self):
        from unittest import mock
        a = os.path.join(self.tmp, "code-reviewer"); shutil.copytree(CR, a)
        real = os.listdir
        def deny(d):
            if os.path.basename(os.path.normpath(d)) == "scenarios": raise PermissionError(13, "Permission denied", d)
            return real(d)
        with mock.patch("eva.os.listdir", side_effect=deny):
            r = eva.check(a)
            self.assertEqual(r.verdict, "FAIL")
            self.assertIn("scenarios-valid", self.ids(r, "critical_failed"))
            self.assertIn("cannot list scenarios/", next(c["reason"] for c in r.failed if c["id"] == "scenarios-valid"))
            rr = eva.grade_transcript(a, p("transcripts/code-reviewer-drill.json"))
            self.assertEqual(rr.verdict, "FAIL"); self.assertIn("scenario-known", self.ids(rr, "critical_failed"))
    def test_refs_one_level_matches_spec(self):
        # spec: "Keep file references one level deep from SKILL.md" -> a referenced file's own file refs are a chain
        r = self.skill(body="See [g](references/g.md).\n",
                       files={"references/g.md": "# G\nRun scripts/x.py.\n", "scripts/x.py": "print(1)\n"})
        self.assertFalse(self.status(r, "refs-one-level"))
        c = next(c for c in r.checks if c["id"] == "refs-one-level")
        self.assertFalse(c["must"])  # spec wording is a recommendation ("Keep", "Avoid"): scored, not [필수]
        # not chains: links back to SKILL.md, to itself, anchors, URLs, files SKILL.md also references
        r = self.skill(body="See [g](references/g.md) and run scripts/x.py.\n",
                       files={"references/g.md": "# G\n[back](../SKILL.md) [self](g.md) [top](#g) https://x.org/a.md "
                                                 "and scripts/x.py\n", "scripts/x.py": "print(1)\n"})
        self.assertTrue(self.status(r, "refs-one-level"), r.failed)
    def test_drill_fixture_not_coupled_to_test_file(self):
        t = open(p("transcripts/code-reviewer-drill.json"), encoding="utf-8").read()
        self.assertNotIn("test_eva.py", t)
        run, _ = eva.load_transcript(p("transcripts/code-reviewer-drill.json"))
        for f, _, _ in eva.FILELINE_RE.findall(run["final"]):
            self.assertTrue(os.path.isfile(os.path.join(p("transcripts/fixtures/drill-repo"), f)), f)

class TestDocsHouseRules(unittest.TestCase):
    """Round 3 (d): README marks house rules (H) including refs-exist and the evals rule."""
    def row(self, cid):
        readme = open(p("README.md"), encoding="utf-8").read()
        rows = [ln for ln in readme.splitlines() if ln.startswith("| `%s`" % cid)]
        self.assertEqual(len(rows), 1, cid)
        return [x.strip() for x in rows[0].strip("|").split("|")]
    def test_house_rules_marked(self):
        for cid in ("refs-exist", "evals-valid", "evals-count", "description-min-length", "description-when",
                    "refs-path-depth", "version-format", "single-purpose"):
            self.assertIn("H", [s.strip() for s in self.row(cid)[-1].split(",")], cid)
    def test_spec_rules_not_marked_house(self):
        for cid in ("refs-one-level", "description-length", "description-pov"):
            self.assertNotIn("H", [s.strip() for s in self.row(cid)[-1].split(",")], cid)
    def test_must_markers(self):
        for cid in ("description-length", "description-when", "description-pov", "refs-exist"):
            self.assertIn("[필수]", self.row(cid)[0], cid)
        for c in eva.check(p("golden/skills/pdf-summarizer")).checks:
            if c["id"] in ("description-length", "description-when", "description-pov"):
                self.assertTrue(c["must"], c["id"])

class TestRound4Citations(unittest.TestCase):
    """Round 4 (1): a cited line counts only from a call that read exactly that one file, exact path match."""
    def test_echo_printf_faked_output_rejected(self):
        for cmd in ("echo src/a.py:42:hello", "printf 'src/a.py:42:hello\\n'", "echo '42:hello'", "printf '    42\\thello'"):
            for res in ("src/a.py:42:hello", "42:hello", "    42\thello"):
                calls = [{"tool": "bash", "input": {"command": cmd}, "result": res}]
                self.assertFalse(cites_ok("src/a.py:42", calls), (cmd, res))
    def test_path_key_non_read_tools_rejected(self):
        for tool, res in (("wc", "42 lines"), ("stat", "42 blocks"), ("write_file", "42 bytes written"),
                          ("search", "42: match"), ("grep", "42:hello"), ("list_dir", "42 entries"), ("Edit", "42\tx")):
            calls = [{"tool": tool, "input": {"path": "src/a.py"}, "result": res}]
            self.assertFalse(cites_ok("src/a.py:42", calls), tool)
        self.assertTrue(cites_ok("src/a.py:42", [{"tool": "Read", "input": {"file_path": "src/a.py"}, "result": "42\thello"}]))
    def test_exact_path_match_no_suffix(self):
        read = lambda path, ws=None: run_of([{"tool": "read_file", "input": {"path": path}, "result": "     5\tx"}], workspace=ws)
        ev = lambda final, run: eva.outcome_evidence("파일:줄 근거 1개 이상", final, run)[1]
        self.assertFalse(ev("docs/README.md:5", read("README.md")))
        self.assertFalse(ev("README.md:5", read("docs/README.md")))
        self.assertFalse(ev("src/app.py:5", read("/work/repo/src/app.py")))  # no workspace: no suffix guess
        self.assertTrue(ev("src/app.py:5", read("/work/repo/src/app.py", "/work/repo")))
        self.assertFalse(ev("src/app.py:5", read("/other/src/app.py", "/work/repo")))
        self.assertTrue(ev("src/app.py:5", read("./src/app.py")))
        self.assertTrue(ev("src/app.py:5", read("src/../src/app.py")))
    def test_path_prefixed_lines_need_read_of_that_file(self):
        # a read of b.py whose result contains a grep-like line for a.py proves nothing about a.py
        calls = [{"tool": "read_file", "input": {"path": "src/b.py"}, "result": "src/a.py:42:hello"}]
        self.assertFalse(cites_ok("src/a.py:42", calls))
        calls = [{"tool": "read_file", "input": {"path": "src/a.py"}, "result": "src/a.py:42:hello"}]
        self.assertFalse(cites_ok("src/a.py:42", calls))  # not a numbered line of the read
        calls = [{"tool": "grep", "input": {"pattern": "hello", "path": "src"}, "result": "src/a.py:42:hello"}]
        self.assertFalse(cites_ok("src/a.py:42", calls))
    def test_single_file_grep_n(self):
        ok = (("grep -n hello src/a.py", "42:hello"), ("grep -n -e hello src/a.py", "42:hello"),
              ("grep -in hello src/a.py", "42:Hello"), ("grep --line-number hello src/a.py", "42:hello"),
              ("grep -nH hello src/a.py", "src/a.py:42:hello"), ("grep -n -B1 hello src/a.py", "41-x\n42:hello"),
              ("grep -n -C 1 hello ./src/a.py", "41-x\n42:hello\n43-y"))
        for cmd, res in ok:
            calls = [{"tool": "bash", "input": {"command": cmd}, "result": res}]
            self.assertTrue(cites_ok("src/a.py:42", calls), cmd)
            self.assertFalse(cites_ok("src/b.py:42", calls), cmd)
            self.assertFalse(cites_ok("src/a.py:44", calls), cmd)
        bad = (("grep hello src/a.py", "42:hello"), ("grep -rn hello src/a.py", "42:hello"), ("grep -cn hello src/a.py", "42"),
               ("grep -n hello src/a.py src/b.py", "src/a.py:42:hello"), ("grep -n hello", "42:hello"),
               ("grep -n hello src/a.py | head", "42:hello"), ("grep -n hello src/a.py; echo 42:x", "42:x"),
               ("grep -ln hello src/a.py", "42:hello"), ("grep -n -f pats src/a.py", "42:hello"),
               ("grep -nH hello src/a.py", "src/b.py:42:hello"))
        for cmd, res in bad:
            self.assertFalse(cites_ok("src/a.py:42", [{"tool": "bash", "input": {"command": cmd}, "result": res}]), cmd)
    def test_cwd_key(self):
        call = lambda cwd: [{"tool": "bash", "input": {"command": "cat -n a.py", "cwd": cwd}, "result": "     5\tx"}]
        ev = lambda final, calls, ws=None: eva.outcome_evidence("파일:줄 근거 1개 이상", final, run_of(calls, workspace=ws))[1]
        self.assertTrue(ev("src/a.py:5", call("/repo/src"), "/repo"))
        self.assertTrue(ev("a.py:5", call("/repo"), "/repo"))  # reviewer repro: {"command": "cat -n a.py", "cwd": "/repo"}
        self.assertFalse(ev("a.py:5", call("/repo/src"), "/repo"))
        self.assertTrue(ev("src/a.py:5", call("src")))
        self.assertFalse(ev("src/a.py:5", call("/repo/src")))  # absolute cwd and no workspace: cannot relate
        calls = [{"tool": "read_file", "input": {"path": "a.py", "cwd": "/repo/src"}, "result": "5\tx"}]
        self.assertTrue(ev("src/a.py:5", calls, "/repo"))
        calls = [{"tool": "read_file", "input": {"path": "a.py", "dir": "src"}, "result": "5\tx"}]
        self.assertFalse(ev("src/a.py:5", calls)); self.assertFalse(ev("a.py:5", calls))  # 'dir' is a second path
    def test_tool_shape_must_match(self):
        for call in ({"tool": "echo", "input": {"command": "cat -n src/a.py"}, "result": "5\tx"},  # not a shell tool
                     {"tool": "bash", "input": {"command": "cat -n src/a.py", "path": "src/a.py"}, "result": "5\tx"},
                     {"tool": "read_file", "input": {"command": "cat -n src/a.py"}, "result": "5\tx"},
                     {"tool": "bash", "input": {"command": ["cat -n src/a.py", "cat -n src/a.py"]}, "result": "5\tx"}):
            self.assertFalse(cites_ok("src/a.py:5", [call]), call)
    def test_drill_still_verified(self):
        r = eva.grade_transcript(CR, p("transcripts/code-reviewer-drill.json"))
        self.assertEqual(r.failed, []); self.assertEqual(r.verdict, "REVIEW")

class TestRound4Yaml(unittest.TestCase):
    """Round 4 (2): out-of-range \\U is a reported error, '#' lines inside block scalars are content (PyYAML)."""
    def test_out_of_range_escape_is_error_not_crash(self):
        for v in ('"\\UFFFFFFFF"', '"\\U00110000"', '"ok \\UFFFFFFFF ok"'):
            meta, _, err = eva.parse_frontmatter_ex(_fm("description: %s" % v))
            self.assertIsNone(meta, v); self.assertIn("U+10FFFF", err, v)
            meta, _, err = eva.parse_frontmatter_ex(_fm("metadata:\n  k: %s" % v))
            self.assertIsNone(meta, v); self.assertTrue(err, v)
        d = tempfile.mkdtemp()
        try:
            s = os.path.join(d, "x-skill"); os.makedirs(s)
            wtext(os.path.join(s, "SKILL.md"), '---\nname: x-skill\ndescription: "\\UFFFFFFFF"\n---\n# X\n')
            r = eva.check(s)  # must not raise
            self.assertEqual(r.verdict, "FAIL"); self.assertIn("frontmatter", {c["id"] for c in r.critical_failed})
        finally:
            shutil.rmtree(d)
        meta, _, err = eva.parse_frontmatter_ex(_fm('k: "\\U0010FFFF"'))
        self.assertIsNone(err); self.assertEqual(meta["k"], "\U0010ffff")
    BLOCKS = ["k: |\n  a\n  # b\n  c", "k: >\n  a\n  # b\n\n  c\n   d\n  e", "k: >-\n  a\n  # b", "k: |-\n  # only",
              "k: >\n  a\n# c\nj: x", "j: x\nk: |\n\n  a\n\n", "k: >\n  # first\n  second\nj: x", "k: |\n  a\n\n\n  b",
              "k: >\n  a\n  b\n\n\n  c", "k: >\n", "k: |\n  x  # not a comment", "k: |\n  a\nj: x", "k: >\n  a\n\nj: x",
              "k: |\n  a\n"]
    def test_block_scalars_match_pyyaml(self):
        yaml = require_yaml(self)
        for text in self.BLOCKS:
            meta, _, err = eva.parse_frontmatter_ex(_fm(text))
            self.assertIsNone(err, text); self.assertEqual(meta, yaml.safe_load(text), text)
    def test_block_scalar_errors_match_pyyaml(self):
        yaml = require_yaml(self)
        for text in ("k: >\n  a\n# c\n  b", "k: >\n    a\n  b"):
            with self.assertRaises(yaml.YAMLError, msg=text): yaml.safe_load(text)
            meta, _, err = eva.parse_frontmatter_ex(_fm(text))
            self.assertIsNone(meta, text); self.assertTrue(err, text)
    def test_hash_line_in_block_description_kept(self):
        meta, _, err = eva.parse_frontmatter_ex(_fm("description: >\n  Counts words.\n  # not a comment\n  Use when asked."))
        self.assertIsNone(err); self.assertIn("# not a comment", meta["description"])

class TestRound4Description(SkillMixin, unittest.TestCase):
    """Round 4 (3)(4): English trigger clause only; first/second person any case, quotes and identifiers skipped."""
    def desc(self, d):
        raw = GOOD_FM.format(name="word-count", extra="", body="").replace(
            "description: Counts words in one text file. Use when the user wants a word count.", "description: %s" % d)
        return self.skill(raw=raw)
    def test_when_requires_trigger_clause(self):
        for d in ("Counts words in a text file. Use this.", "Counts words in a text file when files change.",
                  "Counts words in a text file. Use when needed.", "Counts words in a text file. Use when asked.",
                  "Counts words in a text file. Use this skill.", "Counts words in a text file and reports when done.",
                  "Counts words in a text file. Use for word counts.", "Counts words in a text file. Use when it is needed.",
                  "한국어 글의 낱말 수를 센다. 사용자가 낱말 수를 물을 때 사용한다.", "Counts words. 사용자가 낱말 수를 물으면 쓴다.",
                  "Counts words in a text file. Use when 사용자가 낱말 수를 물을 때."):
            r = self.desc(d)
            self.assertEqual(r.verdict, "FAIL", d); self.assertEqual(self.ids(r, "critical_failed"), {"description-when"}, d)
    def test_when_verb_forms(self):
        for d in ("Counts words in a text file. Use when the user wants a word count.",
                  "Counts words in a text file. Used when the user wants a word count.",
                  "Counts words in a text file. Use this skill when the user wants a word count.",
                  "Counts words in a text file. Use for word counting when the user pastes text.",
                  "Counts words in a text file. Use to count words when a file is given.",
                  "Counts words in a text file. Invoke when the user asks for a word count.",
                  "Counts words in a text file. Trigger when the user asks for a word count.",
                  "Counts words in a text file. This skill should be used when the user asks for a word count.",
                  "Counts words in a text file. USE WHEN the user asks for a word count.",
                  "Counts words in a text file. Use proactively when the user pastes a long text.",
                  "한국어 글의 낱말 수를 센다. Use when the user asks for a Korean word count."):
            r = self.desc(d); self.assertEqual(r.verdict, "PASS", (d, r.failed))
    def test_pov_case_insensitive(self):
        for d, word in (("YOU can count words with this. Use when the user wants a count.", "YOU"),
                        ("WE count words in a file. Use when the user wants a count.", "WE"),
                        ("Counts words in YOUR files. Use when the user wants a count.", "YOUR"),
                        ("My counter for words in a file. Use when the user wants a count.", "My"),
                        ("Counts words for us. Use when the user wants a count.", "us"),
                        ("i count words in a file. Use when the user wants a count.", "i"),
                        ("Counts words; I'm fast. Use when the user wants a count.", "I"),
                        ("Counts the user's words and it's quick, I think. Use when the user wants a count.", "I")):
            r = self.desc(d)
            self.assertIn("description-pov", self.ids(r, "critical_failed"), d)
            self.assertIn(word, next(c["reason"] for c in r.failed if c["id"] == "description-pov"), d)
    def test_pov_quotes_and_identifiers_ignored(self):
        for d in ('Fixes code. Use when the user says "fix my code".', "Fixes code. Use when the user says 'fix my code'.",
                  "Fixes code. Use when the user says “can you fix my code”.", "Fixes code. Use when the user says ‘fix my code’.",
                  "Builds the `my-app` bundle. Use when the user wants a release build.",
                  "Builds my-app releases. Use when the user wants a release build.",
                  "Plans Phase I work. Use when the user starts a new project.",
                  "Explains Type I errors. Use when the user asks about statistics.",
                  "Handles I/O errors. Use when the user reports a failing read.",
                  "Converts US dollars. Use when the user asks for a currency conversion.",
                  "Reads the user's notes. Use when the user's file is a log, i.e. a text file."):
            r = self.desc(d)
            self.assertTrue(self.status(r, "description-pov"), (d, r.failed)); self.assertEqual(r.verdict, "PASS", (d, r.failed))

class TestRound4FencedRefs(SkillMixin, unittest.TestCase):
    """Round 4 (5): every command of a fenced line is checked; globs and file listings are not references."""
    def test_every_command_on_a_line(self):
        for line, refs in (("python scripts/a.py && bash scripts/b.sh", {"scripts/a.py", "scripts/b.sh"}),
                           ("cd x; python scripts/a.py | tee out.txt", {"scripts/a.py"}),
                           ("python scripts/a.py || sh scripts/c.sh", {"scripts/a.py", "scripts/c.sh"}),
                           ("make && ./scripts/run.sh", {"scripts/run.sh"}),
                           ("LANG=C python scripts/a.py", {"scripts/a.py"}),
                           ("cat in.txt | node scripts/x.js | sort", {"scripts/x.js"})):
            self.assertEqual(eva.local_refs("```bash\n%s\n```\n" % line), refs, line)
        r = self.skill(body="```bash\npython scripts/a.py && bash scripts/b.sh\n```\n", files={"scripts/a.py": "print(1)\n"})
        self.assertFalse(self.status(r, "refs-exist"))
        self.assertIn("scripts/b.sh", next(c["reason"] for c in r.failed if c["id"] == "refs-exist"))
    def test_glob_star_not_stripped(self):
        for body in ("Icons: assets/icon*\n", "Icons: `assets/icon*`\n", "Icons: assets/icon*.\n", "Icons: assets/icon*를 쓴다.\n",
                     "```bash\npython scripts/gen*.py\n```\n"):
            self.assertEqual(eva.local_refs(body), set(), body)
            self.assertTrue(self.status(self.skill(body=body), "refs-exist"), body)
        self.assertEqual(eva.local_refs("Run *scripts/x.py* now.\n"), {"scripts/x.py"})  # emphasis still stripped
    def test_file_listings_not_run_commands(self):
        for body in ("```\nscripts/extract.py   # helper\n```\n", "```bash\nscripts/extract.py  # helper\n```\n",
                     "```\nmy-skill/\n├── SKILL.md\n└── scripts/extract.py\n```\n",
                     "```\nmy-skill/\n  SKILL.md\n  scripts/extract.py\n```\n",
                     "```\n# python scripts/x.py; bash scripts/y.sh\n```\n"):
            self.assertEqual(eva.local_refs(body), set(), body)
        for body, ref in (("```bash\nscripts/run.sh --in a.csv\n```\n", "scripts/run.sh"),
                          ("```\n./scripts/run.sh  # helper\n```\n", "scripts/run.sh"),
                          ("```\nmy-skill/\n├── SKILL.md\n```\n```bash\npython scripts/x.py\n```\n", "scripts/x.py")):
            self.assertEqual(eva.local_refs(body), {ref}, body)

class TestRound4PyYamlRequired(unittest.TestCase):
    """Round 4 (6): missing PyYAML fails the parity tests unless explicitly opted out (then a printed warning)."""
    def test_missing_pyyaml_fails_or_warns(self):
        from unittest import mock
        tc = TestYamlParity("test_scalar_values_match_pyyaml")
        with mock.patch.dict(sys.modules, {"yaml": None}):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(PYYAML_OPT_OUT, None)
                with self.assertRaises(tc.failureException):
                    require_yaml(tc)
            with mock.patch.dict(os.environ, {PYYAML_OPT_OUT: "1"}):
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    with self.assertRaises(unittest.SkipTest):
                        require_yaml(tc)
                self.assertIn("WARNING", err.getvalue()); self.assertIn("SKIPPED", err.getvalue())
    def test_no_silent_skips_in_suite(self):
        src = open(os.path.abspath(__file__), encoding="utf-8").read()
        self.assertEqual(src.count("skipTest" + "("), 1)  # only inside require_yaml

class TestRound5Citations(unittest.TestCase):
    """Round 5 (1): every line of a cited range must be shown, start <= end, and only line-numbered results count."""
    def read(self, res, path="src/a.py"):
        return [{"tool": "Read", "input": {"file_path": path}, "result": res}]
    def test_every_line_of_range_shown(self):
        grep = [{"tool": "bash", "input": {"command": "grep -n -A3 foo src/a.py"}, "result": "10:foo\n13-baz"}]
        self.assertTrue(cites_ok("src/a.py:10", grep)); self.assertTrue(cites_ok("src/a.py:13", grep))
        self.assertFalse(cites_ok("src/a.py:10-13", grep))  # 11, 12 never shown (was: only both ends checked)
        full = self.read("    10\ta\n    11\tb\n    12\tc\n    13\td")
        self.assertTrue(cites_ok("src/a.py:10-13", full)); self.assertTrue(cites_ok("src/a.py:11-11", full))
        self.assertFalse(cites_ok("src/a.py:10-14", full))
        split = self.read("    10\ta") + self.read("    13\td")  # two reads showing only the ends
        self.assertFalse(cites_ok("src/a.py:10-13", split))
    def test_reversed_range_rejected(self):
        calls = self.read("\n".join("%6d\tx" % n for n in range(1, 50)))
        self.assertTrue(cites_ok("src/a.py:3-42", calls))
        self.assertFalse(cites_ok("src/a.py:42-3", calls))  # reviewer repro
        ev = eva.outcome_evidence("파일:줄 근거 1개 이상", "src/a.py:42-3", run_of(calls))
        self.assertIn("src/a.py:42-3", ev[2])
    def test_huge_range_stops_early(self):
        self.assertFalse(cites_ok("src/a.py:1-999999999", self.read("     1\tx")))
    def test_raw_content_yields_no_lines(self):
        for res in ("200 OK\n404 Not Found",  # reviewer repro
                    "200 OK", "42 x", "1 | a\n2 | b", "    10\ta\n    12\tc",  # gap: not consecutive
                    "    10\ta\n    11→b",  # mixed separators
                    "    10\ta\nplain line\n    11\tb", "    11\ta\n    10\tb"):
            for ln in (1, 2, 10, 11, 12, 42, 200, 404):
                self.assertFalse(cites_ok("src/a.py:%d" % ln, self.read(res)), (res, ln))
        for res, ln in (("%6d\thello" % 200, 200), ("200→OK\n201→Created", 201), ("200: OK\n201: x", 200),
                        ("    10\ta\n    11\t\n    12\tc\n", 12)):  # empty file line '    11\t', trailing newline
            self.assertTrue(cites_ok("src/a.py:%d" % ln, self.read(res)), (res, ln))
    def test_raw_content_via_shell_read_yields_no_lines(self):
        calls = [{"tool": "bash", "input": {"command": "cat -n src/a.py"}, "result": "200 OK\n404 Not Found"}]
        self.assertFalse(cites_ok("src/a.py:200", calls)); self.assertFalse(cites_ok("src/a.py:404", calls))
    def test_grep_output_must_be_consistent(self):
        g = lambda res: [{"tool": "bash", "input": {"command": "grep -n -C1 foo src/a.py"}, "result": res}]
        self.assertTrue(cites_ok("src/a.py:42", g("41-x\n42:foo\n43-y\n--\n90-z\n91:foo")))
        for res in ("42:foo\n200 OK", "42:foo\n7:bar", "42:foo\n42:foo", "Binary file matches\n42:foo"):
            self.assertFalse(cites_ok("src/a.py:42", g(res)), res)

class TestRound5DescriptionWhen(SkillMixin, unittest.TestCase):
    """Round 5 (2): sentence-initial use clause, 'this skill ... when', or 'Read this skill before|when';
    quoted text, passive/participle and negative forms do not count."""
    def desc(self, d):
        raw = GOOD_FM.format(name="word-count", extra="", body="").replace(
            "description: Counts words in one text file. Use when the user wants a word count.", "description: %s" % d)
        return self.skill(raw=raw)
    def test_quoted_trigger_ignored(self):
        for d in ('Counts words. Replies "Use when the user wants a word count" to greetings.',
                  "Counts words. Prints `Use when the user wants a word count` as help text.",
                  "Counts words. Says 'Use this skill when the user wants a count' on start.",
                  "Counts words. Shows “Use when the user wants a word count” in the banner."):
            self.assertIsNone(eva.desc_when_clause(d), d)
            r = self.desc(d); self.assertEqual(self.ids(r, "critical_failed"), {"description-when"}, d)
        # quotes inside a real condition only drop the quoted words
        self.assertTrue(self.status(self.desc('Fixes code. Use when the user says "fix my code".'), "description-when"))
    def test_passive_participle_rejected(self):
        for d in ("Config files loaded when the session starts.", "Lifecycle hooks called when the agent exits.",
                  "Applies to Python files when the user edits them.", "Word counting for one text file, used when the user wants a word count.",
                  "Counts words in files triggered when a commit lands.", "Counts words, invoked when the user pastes text."):
            self.assertIsNone(eva.desc_when_clause(d), d)
            r = self.desc(d); self.assertEqual(self.ids(r, "critical_failed"), {"description-when"}, d)
    def test_negative_forms_rejected(self):
        for d in ("Counts words. Do not use when the user wants a summary.", "Counts words. Don't use when the file is binary.",
                  "Counts words. Never use when the file is binary.", "Counts words. Don’t use this skill when the file is binary.",
                  "Counts words. Do not use this skill when the file is binary.", "Counts words. This skill should not be used when the file is binary.",
                  "Counts words. Never use this skill whenever the input is binary."):
            self.assertIsNone(eva.desc_when_clause(d), d)
            r = self.desc(d); self.assertEqual(self.ids(r, "critical_failed"), {"description-when"}, d)
        r = self.desc("Counts words. Use when the user wants a word count. Do not use when the file is binary.")
        self.assertEqual(r.verdict, "PASS", r.failed)
    def test_accepted_forms(self):
        for d in ("Counts words. Use when the user wants a word count.", "Counts words. Use this skill when the user wants a word count.",
                  "Counts words. Used when the user wants a word count.", "Counts words. Invoke when the user asks for a word count.",
                  "Counts words. Trigger when the user asks for a word count.", "Use when the user wants a word count of a file.",
                  "Counts words. Claude should use this skill when the user wants a word count.",
                  "Counts words. Read this skill before counting words in any document.",
                  "Counts words. Read this skill when the user wants a word count.",
                  "Counts words. Use this when the user wants a word count.",  # accepted before Round 5, kept
                  "Counts words. Use it when the user wants a word count."):
            r = self.desc(d); self.assertEqual(r.verdict, "PASS", (d, r.failed))
        # a line break starts a sentence (folded '|' block descriptions)
        self.assertEqual(eva.desc_when_clause("Counts words\nUse when the user wants a word count"), "the user wants a word count")
    def test_read_this_skill_needs_substance(self):
        for d in ("Counts words. Read this skill before use.", "Counts words. Read this skill before it is needed.",
                  "Counts words. Read this skill.", "Counts words. Read this skill when needed."):
            r = self.desc(d); self.assertEqual(self.ids(r, "critical_failed"), {"description-when"}, d)
    def test_use_clause_must_start_sentence(self):
        for d in ("Counts words; use when the user wants a count.", "Counts words and the tools use when the user asks."):
            self.assertIsNone(eva.desc_when_clause(d), d)
    def test_korean_only_still_fails(self):
        for d in ("한국어 글의 낱말 수를 센다. 사용자가 낱말 수를 물을 때 사용한다.", "낱말 수를 센다. 이 스킬은 사용자가 물을 때 읽는다."):
            r = self.desc(d); self.assertEqual(self.ids(r, "critical_failed"), {"description-when"}, d)

class TestRound5Docs(unittest.TestCase):
    """Round 5 (3): README no longer claims path-prefixed grep lines count from any result."""
    def test_no_stale_any_result_claim(self):
        readme = open(p("README.md"), encoding="utf-8").read()
        self.assertNotIn("어느 결과에 있어도 됨", readme)
        self.assertNotIn("줄<탭|→|:|공백>", readme)

class TestRound6DescriptionWhen(SkillMixin, unittest.TestCase):
    """Round 6 (1)(2): 'this skill' only after a use verb or as subject of 'modal/be + use verb'; skip/avoid/cannot
    are negatives; a period with no space after it still ends the condition."""
    desc = TestRound5DescriptionWhen.desc
    def rejected(self, *descs):
        for d in descs:
            self.assertIsNone(eva.desc_when_clause(d), d)
            r = self.desc(d); self.assertEqual(self.ids(r, "critical_failed"), {"description-when"}, d)
    def test_this_skill_needs_use_wording(self):
        self.rejected("Counts words. This skill counts words when files change.",
                      "Counts words. Config files for this skill are loaded when the session starts.",
                      "Counts words. This skill is loaded when the session starts.",
                      "Counts words. This skill is called when a hook fires.",
                      "Counts words. This skill applies when the user wants a word count.",
                      "Counts words. Config for this skill is used when the session starts.")
    def test_skip_avoid_cannot_rejected(self):
        self.rejected("Counts words. Skip this skill when the input is binary.",
                      "Counts words. Avoid this skill when the input is binary.",
                      "Counts words. Cannot use this skill when the file is binary.",
                      "Counts words. Can't use this skill when the file is binary.",
                      "Counts words. Avoid using this skill when the input is binary.",
                      "Counts words. Without asking, use this skill when the file is binary.")
    def test_this_skill_use_wording_accepted(self):
        for d in ("Counts words. This skill should be used when the user asks for a word count.",
                  "Counts words. This skill can be used when the user asks for a word count.",
                  "Counts words. This skill must be invoked when the user asks for a word count.",
                  "Counts words. This skill is used when the user asks for a word count.",
                  "Counts words. Claude should use this skill when the user wants a word count.",
                  "Counts words. Agents invoke this skill whenever the user pastes a long text."):
            r = self.desc(d); self.assertEqual(r.verdict, "PASS", (d, r.failed))
    def test_period_without_space_ends_condition(self):
        self.rejected("Counts words. Use when needed.The user wants a summary.",
                      "Counts words. Use this skill when asked.Then count words in the file.",
                      "Counts words. This skill should be used when needed.Files are counted.",
                      "Counts words.Use when the user wants a word count.")  # not a new sentence start: no loosening
        for d in ("Counts words. Use when the user opens a file.txt and wants a count.",
                  "Counts words. Use when the user runs Python 3.12 and wants a count.",
                  "Counts words. Use when the user gives a text, e.g. a long note."):
            r = self.desc(d); self.assertEqual(r.verdict, "PASS", (d, r.failed))
        self.assertEqual(eva.desc_when_clause("Counts words. Use when the user edits SKILL.md files."),
                         "the user edits SKILL.md files")

class TestRunnerParity(unittest.TestCase):
    """pytest from the repo root collects the same tests as 'unittest discover -s tests' (not the frozen fixtures)."""
    def test_pytest_testpaths(self):
        import configparser
        cfg = configparser.ConfigParser(); cfg.read(p("pytest.ini"), encoding="utf-8")
        self.assertEqual(cfg.get("pytest", "testpaths").split(), ["tests"])

class TestRound7HostFields(SkillMixin, unittest.TestCase):
    """Claude Code documented fields argument-hint / disable-model-invocation are a host-specific allowance."""
    def test_host_fields_allowed(self):
        r = self.skill(extra="argument-hint: \"[file]\"\ndisable-model-invocation: true\n")
        self.assertEqual(r.verdict, "PASS", r.failed)
        self.assertIn("host-specific", next(c["reason"] for c in r.checks if c["id"] == "frontmatter-fields"))
        self.assertTrue(self.status(self.skill(extra="disable-model-invocation: false\n"), "field-types"))
    def test_other_fields_still_fail(self):
        for extra in ("user-invocable: false\n", "model: opus\n", "inputs: x\n"):
            self.assertIn("frontmatter-fields", self.ids(self.skill(extra=extra), "critical_failed"), extra)
    def test_host_field_types(self):
        for extra in ("disable-model-invocation: \"true\"\n", "disable-model-invocation: maybe\n",
                      "disable-model-invocation:\n", "argument-hint:\n"):
            r = self.skill(extra=extra)
            self.assertFalse(self.status(r, "field-types"), extra)
            self.assertTrue(self.status(r, "frontmatter-fields"), extra)
    def test_readme_marks_host_allowance(self):
        readme = open(p("README.md"), encoding="utf-8").read()
        row = next(ln for ln in readme.splitlines() if ln.startswith("| `frontmatter-fields`"))
        self.assertIn("argument-hint", row); self.assertIn("disable-model-invocation", row); self.assertIn("호스트", row)

class TestRound7RepoRootRefs(TmpMixin, unittest.TestCase):
    """refs-exist: a ref found only from the enclosing git repo root is needs-review, never a silent pass."""
    def make(self, body, git=True, root_files=()):
        repo = os.path.join(self.tmp, "repo"); d = os.path.join(repo, "skills", "word-count")
        os.makedirs(os.path.join(d, "evals"), exist_ok=True)
        if git: os.makedirs(os.path.join(repo, ".git"))
        for rel in root_files:
            fp = os.path.join(repo, rel); os.makedirs(os.path.dirname(fp), exist_ok=True); wtext(fp, "x")
        wtext(os.path.join(d, "SKILL.md"), GOOD_FM.format(name="word-count", extra="", body=body))
        wjson(os.path.join(d, "evals", "evals.json"), {"skill_name": "word-count", "evals": GOOD_EVALS})
        return eva.check(d), repo
    def check_of(self, r):
        return next(c for c in r.checks if c["id"] == "refs-exist")
    def test_repo_root_ref_is_review(self):
        r, _ = self.make("Run `scripts/check_skills.py`.\n", root_files=["scripts/check_skills.py"])
        c = self.check_of(r)
        self.assertEqual(c["status"], "needs-review"); self.assertIn("repo-root-relative reference, not bundled", c["reason"])
        self.assertEqual(r.verdict, "REVIEW", r.failed)
        self.assertEqual(eva.EXIT_CODES[r.verdict], 3)
    def test_missing_everywhere_fails(self):
        r, _ = self.make("Run `scripts/check_skills.py`.\n")
        self.assertFalse(self.check_of(r)["passed"])
        r, _ = self.make("Run `scripts/check_skills.py`.\n", git=False, root_files=["scripts/check_skills.py"])
        self.assertFalse(self.check_of(r)["passed"])  # no git repo: no repo root to resolve against
    def test_mixed_missing_and_repo_ref_fails(self):
        r, _ = self.make("Run `scripts/a.py` and `scripts/b.py`.\n", root_files=["scripts/a.py"])
        c = self.check_of(r)
        self.assertFalse(c["passed"]); self.assertIn("scripts/b.py", c["reason"]); self.assertIn("not bundled", c["reason"])
    def test_escape_via_symlink_rejected(self):
        outside = os.path.join(self.tmp, "outside.py"); wtext(outside, "x")
        repo = os.path.join(self.tmp, "repo"); os.makedirs(os.path.join(repo, "scripts"))
        os.symlink(outside, os.path.join(repo, "scripts", "esc.py"))
        r, _ = self.make("Run `scripts/esc.py`.\n")
        self.assertFalse(self.check_of(r)["passed"])
    def test_dotdot_never_repo_root(self):
        r, repo = self.make("See [x](../../README.md).\n", root_files=["README.md"])
        self.assertFalse(eva.repo_root_ref(os.path.join(repo, "skills", "word-count"), "../../README.md"))
        self.assertFalse(eva.repo_root_ref(os.path.join(repo, "skills", "word-count"), "/etc/hosts"))
        self.assertFalse(self.status_of(r, "refs-relative"))
    def status_of(self, r, cid):
        return next(c["passed"] for c in r.checks if c["id"] == cid)

class TestRound7FolderName(SkillMixin, unittest.TestCase):
    """name-matches-folder resolves '.', relative paths and trailing slashes via realpath."""
    def test_dot_relative_and_trailing_slash(self):
        self.skill()
        d = os.path.join(self.tmp, "word-count"); cwd = os.getcwd()
        try:
            os.chdir(d)
            for arg in (".", "./", "../word-count", "../word-count/"):
                self.assertTrue(self.status(eva.check(arg), "name-matches-folder"), arg)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(eva.main(["check", "."]), 0)
            os.chdir(self.tmp)
            self.assertTrue(self.status(eva.check("word-count/"), "name-matches-folder"))
        finally:
            os.chdir(cwd)
    def test_symlink_uses_real_folder(self):
        self.skill(); link = os.path.join(self.tmp, "alias")
        os.symlink(os.path.join(self.tmp, "word-count"), link)
        self.assertTrue(self.status(eva.check(link), "name-matches-folder"))

class TestRound7SinglePurpose(SkillMixin, unittest.TestCase):
    """single-purpose counts distinct task verbs that open coordinated items, so ';' -> ',' does not dodge it."""
    def sp(self, d):
        return self.status(self.skill(raw=GOOD_FM.replace(
            "Counts words in one text file. Use when the user wants a word count.", json.dumps(d)).format(name="word-count", extra="", body="")),
            "single-purpose")
    def test_separator_swap_does_not_dodge(self):
        for d in ("Summarizes PDFs; cleans CSVs; sends emails. Use when the user wants help with files.",
                  "Summarizes PDFs, cleans CSVs, sends emails. Use when the user wants help with files.",
                  "Summarizes PDFs and cleans CSVs and sends emails. Use when the user wants help with files.",
                  "Summarizes PDFs - then translates them / or cleans CSVs, and sends emails. Use when the user wants help.",
                  "Handles files: reading, parsing, or extracting text, editing slides, and combining decks. Use when files arrive."):
            self.assertFalse(self.sp(d), d)
    def test_add_on_phrases_still_fail(self):
        self.assertFalse(self.sp("Summarizes PDFs as well as charts. Use when the user wants a summary."))
        self.assertFalse(self.sp("Summarizes PDFs and also tables. Use when the user wants a summary."))
    def test_single_or_two_tasks_pass(self):
        for d in ("Counts words in one text file. Use when the user wants a word count.",
                  "Removes duplicate rows and trims whitespace in one CSV file. Use when a CSV has messy rows.",
                  "Controls the desktop; its tools are named x; before setup there is only y. Use when the user asks for desktop control.",
                  "Exports a Word file for a document, page, report or memo. Use when the user wants a .docx file.",
                  "Summarizes a PDF. Use when the user asks to summarize, translate, or email a PDF."):
            self.assertTrue(self.sp(d), d)
    def test_task_verbs_listed_in_reason(self):
        self.assertEqual(eva.desc_task_verbs("Summarizes PDFs, cleans CSVs, sends emails, translates text."),
                         ["summarize", "clean", "send", "translate"])
        self.assertEqual(eva.desc_task_verbs("Generates schedules and reports."), ["generate"])  # nouns, not verbs
        self.assertEqual(eva.desc_task_verbs("Read this skill before editing. \"Merge, split, rotate\"."), [])
        self.assertEqual(eva.desc_task_verbs("Edits Word files. Do NOT use for PDFs, coding, or translating."), ["edit"])
    def test_golden_still_pass_and_bad_still_fails(self):
        for u in eva.find_units(p("golden", "skills")):
            self.assertTrue(next(c["passed"] for c in eva.check(u).checks if c["id"] == "single-purpose"), u)
        self.assertFalse(next(c["passed"] for c in eva.check(p("bad/skills/DoEverything")).checks if c["id"] == "single-purpose"))
    def test_scored_not_required(self):
        r = self.skill(raw=GOOD_FM.replace("Counts words in one text file.", "Counts words, sorts lines, merges files.")
                       .format(name="word-count", extra="", body=""))
        c = next(c for c in r.checks if c["id"] == "single-purpose")
        self.assertFalse(c["passed"]); self.assertFalse(c["must"]); self.assertEqual(r.verdict, "PASS", r.failed)

class TestRound7EvalLayer(SkillMixin, unittest.TestCase):
    """evals[].layer is optional; when present it must be exactly static|llm|human."""
    def with_layers(self, *layers):
        evals = [dict(e) for e in GOOD_EVALS]
        for e, layer in zip(evals, layers):
            if layer != "<absent>": e["layer"] = layer
        return self.skill(evals={"skill_name": "word-count", "evals": evals})
    def test_valid_layers(self):
        for layers in (("static", "llm", "human"), ("llm",), ("static", "static"), ("<absent>", "human"), ()):
            r = self.with_layers(*layers); self.assertTrue(self.status(r, "evals-valid"), (layers, r.failed))
    def test_invalid_layers_fail(self):
        for bad in ("LLM", "Static", "unit", "", "none", 1, ["llm"], None):
            r = self.with_layers(bad)
            self.assertFalse(self.status(r, "evals-valid"), bad)
            self.assertIn("layer", next(c["reason"] for c in r.failed if c["id"] == "evals-valid"))

class TestRound8EscapingRefs(TmpMixin, unittest.TestCase):
    """refs-exist: absolute / backslash / escaping refs fail (required) even if the target exists."""
    def make(self, body, git=True, files=None, links=None):
        repo = os.path.join(self.tmp, "repo"); d = os.path.join(repo, "skills", "word-count")
        os.makedirs(os.path.join(d, "evals"), exist_ok=True)
        if git: os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
        for rel, text in (files or {}).items():
            fp = os.path.join(self.tmp, rel); os.makedirs(os.path.dirname(fp), exist_ok=True); wtext(fp, text)
        for rel, target in (links or {}).items():
            fp = os.path.join(d, rel); os.makedirs(os.path.dirname(fp), exist_ok=True)
            os.symlink(os.path.join(self.tmp, target), fp)
        wtext(os.path.join(d, "SKILL.md"), GOOD_FM.format(name="word-count", extra="", body=body))
        wjson(os.path.join(d, "evals", "evals.json"), {"skill_name": "word-count", "evals": GOOD_EVALS})
        return eva.check(d)
    def refs_exist(self, r):
        return next(c for c in r.checks if c["id"] == "refs-exist")
    def assert_escape_fails(self, r, ref):
        c = self.refs_exist(r)
        self.assertEqual(c["status"], "fail", c); self.assertTrue(c["must"], c)
        self.assertIn(repr(ref), c["reason"]); self.assertEqual(r.verdict, "FAIL", r.failed)
    def test_baseline_passes(self):
        self.assertEqual(self.make("").verdict, "PASS")
    def test_etc_hosts_fails_overall(self):
        self.assertTrue(os.path.exists("/etc/hosts"))
        for git in (True, False):
            self.assert_escape_fails(self.make("See [hosts](/etc/hosts).\n", git=git), "/etc/hosts")
    def test_absolute_home_and_windows_fail(self):
        home = os.path.join(self.tmp, "repo", "skills", "word-count", "SKILL.md")  # exists, inside the skill folder
        for ref in (home, "~/notes.md", "C:/Windows/hosts", "C:\\Windows\\hosts", "scripts\\run.py"):
            self.assert_escape_fails(self.make("See [x](%s).\n" % ref), ref)
    def test_dotdot_escape_fails(self):
        r = self.make("See [x](../../../outside.md).\n", files={"outside.md": "x"})
        self.assert_escape_fails(r, "../../../outside.md")
    def test_dotdot_inside_repo_is_review(self):
        r = self.make("See [x](../../README.md).\n", files={"repo/README.md": "x"})
        c = self.refs_exist(r)
        self.assertEqual(c["status"], "needs-review", c); self.assertIn("inside the git repo", c["reason"])
        self.assertEqual(r.verdict, "REVIEW", r.failed)
    def test_symlink_outside_fails(self):
        r = self.make("Run `scripts/esc.py`.\n", files={"outside.py": "x"}, links={"scripts/esc.py": "outside.py"})
        self.assert_escape_fails(r, "scripts/esc.py")
    def test_symlink_inside_repo_is_review(self):
        r = self.make("Run `scripts/shared.py`.\n", files={"repo/tools/shared.py": "x"},
                      links={"scripts/shared.py": "repo/tools/shared.py"})
        self.assertEqual(self.refs_exist(r)["status"], "needs-review"); self.assertEqual(r.verdict, "REVIEW")
    def test_repo_root_ref_still_review_and_bundled_pass(self):
        r = self.make("Run `scripts/root.py`.\n", files={"repo/scripts/root.py": "x"})
        c = self.refs_exist(r)
        self.assertEqual(c["status"], "needs-review"); self.assertIn("repo-root-relative reference, not bundled", c["reason"])
        r = self.make("Run `scripts/own.py`.\n", files={"repo/skills/word-count/scripts/own.py": "x"})
        self.assertEqual(self.refs_exist(r)["status"], "pass"); self.assertEqual(r.verdict, "PASS", r.failed)
    def test_ref_targets_unit(self):
        d = os.path.join(self.tmp, "repo", "skills", "word-count"); self.make("")
        self.assertEqual(eva.ref_targets(d, {"/etc/hosts", "SKILL.md", "nope.md"}), (["nope.md"], [], [], ["/etc/hosts"]))

class TestRound9RefsExistRequired(TestRound8EscapingRefs):
    """refs-exist is required: any missing ref fails the unit; escaping refs are classified before the existence check."""
    def test_missing_escaping_dotdot_fails_overall(self):
        ref = "../../../../definitely-no-such-file"
        r = self.make("See [x](%s).\n" % ref)
        self.assert_escape_fails(r, ref)
        self.assertIn("outside the skill folder and the git repo", self.refs_exist(r)["reason"])
        self.assertEqual(eva.ref_targets(os.path.join(self.tmp, "repo", "skills", "word-count"), {ref}),
                         ([], [], [], [ref]))
    def test_dangling_symlink_outside_fails(self):
        r = self.make("Run `scripts/gone.py`.\n", links={"scripts/gone.py": "nowhere/gone.py"})
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "nowhere", "gone.py")))
        self.assert_escape_fails(r, "scripts/gone.py")
        self.assertIn("outside the skill folder and the git repo", self.refs_exist(r)["reason"])
    def test_missing_bundled_ref_fails_overall(self):
        r = self.make("Run `scripts/missing.py`.\n")
        c = self.refs_exist(r)
        self.assertEqual(c["status"], "fail"); self.assertTrue(c["must"], c)
        self.assertIn("missing ['scripts/missing.py']", c["reason"]); self.assertEqual(r.verdict, "FAIL", r.failed)
    def test_missing_inside_repo_dotdot_fails_overall(self):
        r = self.make("See [x](../../NOPE.md).\n")
        c = self.refs_exist(r)
        self.assertEqual(c["status"], "fail"); self.assertTrue(c["must"]); self.assertEqual(r.verdict, "FAIL")
    def test_drive_relative_is_path_not_url(self):
        self.assertIn("C:relative.txt", eva.local_refs("See [x](C:relative.txt).\n"))
        self.assertTrue(eva._ref_problem("C:relative.txt"))
        self.assert_escape_fails(self.make("See [x](C:relative.txt).\n"), "C:relative.txt")
        self.assertNotIn("https://example.com", eva.local_refs("See [x](https://example.com).\n"))
        self.assertFalse(eva.local_refs("Mail [me](mailto:a@b.c).\n"))

class TestRound8ArgumentHintString(SkillMixin, unittest.TestCase):
    """argument-hint must be a YAML string: unquoted bool/null/number scalars fail field-types."""
    def test_unquoted_typed_scalars_fail(self):
        for v in ("true", "false", "yes", "no", "on", "off", "True", "NO", "null", "~", "42", "3.5", "-1", "0x1f"):
            r = self.skill(extra="argument-hint: %s\n" % v)
            self.assertFalse(self.status(r, "field-types"), v)
            self.assertIn("argument-hint", next(c["reason"] for c in r.failed if c["id"] == "field-types"), v)
            self.assertEqual(r.verdict, "FAIL", v)
    def test_quoted_and_plain_strings_pass(self):
        for v in ('"true"', "'42'", '"~"', "'null'", "[file]".join('""'), "file-path", "<file> [--dry-run]".join("''")):
            r = self.skill(extra="argument-hint: %s\n" % v)
            self.assertTrue(self.status(r, "field-types"), (v, r.failed))
    def test_unquoted_list_fails(self):
        self.assertFalse(self.status(self.skill(extra="argument-hint: [file]\n"), "field-types"))

if __name__ == "__main__":
    unittest.main()
