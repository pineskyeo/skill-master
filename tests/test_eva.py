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
        self.assertEqual({c["id"] for c in r.checks if c["passed"] is None}, {"criteria-unchecked", "expected-unchecked"})
    def test_scenario_success_criteria_checked(self):
        sp = os.path.join(self.agent, "scenarios", "s.json")
        s = rjson(sp); s["success_criteria"] = ["URL 1개 이상"]; wjson(sp, s)
        self.assertEqual(eva.grade_transcript(self.agent, self.t).verdict, "FAIL")
        wjson(self.t, {"scenario": "s", "tool_calls": [{"tool": "t", "result": "found https://x.org"}], "final": "ok https://x.org"})
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
        self.assertTrue({"only-declared-tools", "expected-tools-called", "outcome-keywords", "no-bypass"} <= must)

class TestCodeReviewer(TmpMixin, unittest.TestCase):
    """Task 2: code-reviewer criteria are machine-checkable and graded."""
    def drill(self):
        return rjson(p("transcripts/code-reviewer-drill.json"))
    def test_static_criteria_all_checkable(self):
        r = eva.check(CR)
        self.assertEqual(r.verdict, "PASS", r.failed)
        self.assertNotIn("success-measurable", self.ids(r))
        meta, _ = eva.parse_frontmatter(open(os.path.join(CR, "AGENT.md"), encoding="utf-8").read())
        kinds = {eva.outcome_evidence(c, "")[0] for c in meta["success_criteria"]}
        self.assertEqual(kinds, {"file-lines", "no-writes"})
        for f, s, e in eva.load_scenarios(CR):
            self.assertFalse(e)
            self.assertTrue(all(eva.outcome_evidence(c, "") for c in s["expected_outcome"]), f)
    def test_passing_transcript(self):
        r = eva.grade_transcript(CR, p("transcripts/code-reviewer-drill.json"))
        self.assertEqual(r.verdict, "PASS", r.failed)
        self.assertTrue({"criteria-file-lines", "criteria-no-writes", "expected-file-lines", "expected-no-writes"}
                        <= {c["id"] for c in r.checks if c["passed"]})
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
        run = run_of([{"tool": "read_file", "input": {"path": "src/app.py"}}, {"tool": "grep", "result": "lib/util.go:7: x"}])
        kind, ok, got, _ = eva.outcome_evidence("파일:줄 근거 3개 이상", "src/app.py:1, src/app.py:1, lib/util.go:7", run)
        self.assertEqual(kind, "file-lines"); self.assertFalse(ok, got)  # duplicates counted once
        self.assertTrue(eva.outcome_evidence("at least 2 file:line citations", "src/app.py:1 lib/util.go:7-9", run)[1])
    def test_citations_to_unopened_files_ignored(self):
        run = run_of([{"tool": "read_file", "input": {"path": "src/app.py"}}])
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
    def test_one_unmeasurable_fails(self):
        self.assertFalse(self.m(["출처 링크 3개 이상", "be helpful"]))
    def test_empty_criteria_fails(self):
        self.assertFalse(self.m([]))

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
        self.assertEqual(eva.grade_transcript(RA, self.transcript(t)).verdict, "PASS")
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

class TestNoBypass(TmpMixin, unittest.TestCase):
    """Task 6: searching outside the workspace or mining git history fails the run."""
    def v(self, inp, workspace=None):
        return eva.bypass_violations(run_of([{"tool": "bash", "input": inp}])["calls"], workspace)
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
    def test_bad_bypass_fixture_fails_only_on_no_bypass(self):
        r = eva.grade_transcript(CR, p("transcripts/bad/code-reviewer-bypass.json"))
        self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(self.ids(r, "critical_failed"), {"no-bypass"})
    def test_shell_bypass_transcript_fails(self):
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
            self.assertEqual(r.verdict, "FAIL", cmd); self.assertEqual(self.ids(r, "critical_failed"), {"no-bypass"}, cmd)

class TestDocs(unittest.TestCase):
    def test_no_bypass_principle_documented(self):
        readme = open(p("README.md"), encoding="utf-8").read()
        self.assertIn("No-bypass principle (우회 금지/정석)", readme)
        self.assertIn("no-bypass", readme)
        loop = open(p("LOOP.md"), encoding="utf-8").read()
        self.assertIn("tool_results", loop); self.assertIn("result", loop)

if __name__ == "__main__":
    unittest.main()
