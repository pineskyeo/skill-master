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
def run_of(calls=(), results=()):
    """Normalized run as produced by eva.load_transcript."""
    calls = [{"tool": c, "input": None, "result": None} if isinstance(c, str) else
             {"tool": c["tool"], "input": c.get("input"), "result": c.get("result")} for c in calls]
    return {"calls": calls, "results": [c["result"] for c in calls if c["result"]] + list(results)}

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
                      {"tool": "grep", "result": "lib/util.go:7:x\nlib/util.go-8-y\nlib/util.go:9:z"}])
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
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
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
    def test_fixture_cites_real_lines(self):
        run, err = eva.load_transcript(self.DRILL)
        self.assertFalse(err)
        cites = eva.FILELINE_RE.findall(run["final"])
        self.assertGreaterEqual(len(cites), 3)
        for f, ln, _ in cites:
            with open(p(f), encoding="utf-8") as fh: real = fh.read().splitlines()
            ln = int(ln); self.assertLessEqual(ln, len(real), (f, ln))
            shown = [res for res in run["results"] if ("%s:%d:" % (f, ln)) in res or ("%6d\t" % ln) in res]
            self.assertTrue(shown, (f, ln))
            self.assertIn(real[ln - 1].strip(), "\n".join(shown), (f, ln))  # tool output is the real file content
    def drill_with_final(self, final):
        t = rjson(self.DRILL); t["final"] = final
        return eva.grade_transcript(CR, self.transcript(t))
    def test_nonexistent_lines_fail(self):
        r = self.drill_with_final("tests/test_eva.py:99999, tests/test_eva.py:99998, tests/test_eva.py:99997 test 파일 변경 없음")
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(self.ids(r, "critical_failed"), {"criteria-file-lines", "expected-file-lines"})
        self.assertIn("tests/test_eva.py:99999", next(c["reason"] for c in r.failed if c["id"] == "expected-file-lines"))
    def test_wrong_line_numbers_fail(self):
        run, _ = eva.load_transcript(self.DRILL)
        good = sorted({int(ln) for _, ln, _ in eva.FILELINE_RE.findall(run["final"])})
        wrong = [ln + 1000 for ln in good]  # same file, lines the tools never showed
        r = self.drill_with_final("test: " + ", ".join("tests/test_eva.py:%d" % ln for ln in wrong))
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
        for res in ("     3\tx = 1", "3→x = 1", "3: x = 1", "3 x = 1"):
            run = run_of([{"tool": "read_file", "input": {"file_path": "/work/repo/src/app.py"}, "result": res}])
            self.assertTrue(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/app.py:3", run)[1], res)
            self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/app.py:4", run)[1], res)
            self.assertFalse(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/other.py:3", run)[1], res)
    def test_grep_lines_and_ranges(self):
        run = run_of([{"tool": "grep", "input": {"command": "grep -rn -A1 foo src"}, "result": "src/a.py:10:foo\nsrc/a.py-11-bar"}])
        ev = lambda final: eva.outcome_evidence("파일:줄 근거 1개 이상", final, run)[1]
        self.assertTrue(ev("src/a.py:10")); self.assertTrue(ev("src/a.py:10-11"))
        self.assertFalse(ev("src/a.py:10-12")); self.assertFalse(ev("src/b.py:10"))
        # top-level tool_results: grep lines carry their path and count; bare numbered lines cannot be attributed
        run = run_of(["grep"], results=["src/a.py:10:foo", "7: bar"])
        self.assertTrue(eva.outcome_evidence("파일:줄 근거 1개 이상", "src/a.py:10", run)[1])
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
        for body in ("```\nRun scripts/missing.py\n```\n", "~~~sh\npython references/missing.md\n~~~\n",
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

if __name__ == "__main__":
    unittest.main()
