"""Round-10 tests: first-live-run config (Claude Sonnet 5.5 effort high under test; Codex grader / pairwise judge,
read-only, conservative per-call charge) and the csv-cleaner pressure cases. Everything is mocked: subprocess is
patched to fail for the whole module, the Codex grader CLI is a fake and its auth.json is a fake file in a temp home."""
import json, os, runpy, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, codex_jsonl, rjson, wjson, FAKE_CODEX_AUTH
from test_round3_isolation import Base

CSV_CLEANER = os.path.join(T.ROOT, "golden", "skills", "csv-cleaner")
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

CASE = {"prompt": "clean it", "expected_output": "clean csv"}
ITEMS = [{"id": "1.0", "text": "no duplicate rows"}]

class FakeCodexGrader:
    """Fake `codex exec --json --sandbox read-only`: records what the call could see, answers with a JSON object."""
    def __init__(self, answer=None, usage=(1000, 100, 50), raise_=None, rc=0):
        self.calls, self.answer, self.usage, self.raise_, self.rc = [], answer, usage, raise_, rc
    def __call__(self, cmd, cwd, timeout, env=None):
        ch = env["CODEX_HOME"]
        self.calls.append({"cmd": cmd, "cwd": cwd, "env": env, "cwd_files": os.listdir(cwd),
                           "auth_during": os.path.isfile(os.path.join(ch, "auth.json")), "codex_home": ch})
        if self.raise_: raise self.raise_
        ans = self.answer
        if ans is None:
            ans = json.dumps({"winner": "A", "reasoning": "r"}) if '"winner"' in cmd[-1] else \
                json.dumps({"items": [{"id": "1.0", "answer": "yes", "evidence": "only unique rows"}]})
        return self.rc, codex_jsonl(ans, usage=self.usage), ""

class TestClaudeEffort(Base):
    def test_effort_flag_on_the_engine_command(self):
        e = se.ClaudeCodeEngine("claude-sonnet-5-5", effort="high")
        cmd = e.command("p", True)
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "high")
        self.assertNotIn("--effort", se.ClaudeCodeEngine("claude-sonnet-5-5").command("p", True))
        self.assertEqual(e.label(), "claude(claude-sonnet-5-5, effort high)")

    def test_bad_effort_and_effort_via_extra_args_are_refused(self):
        with self.assertRaises(se.EvalError): se.ClaudeCodeEngine("claude-sonnet-5-5", effort="huge")
        for bad in (["--effort", "low"], ["--effort=low"]):
            with self.assertRaises(se.EvalError) as cm: se.ClaudeCodeEngine("claude-sonnet-5-5", extra_args=bad)
            self.assertIn("effort", str(cm.exception))

    def test_codex_engine_effort_uses_the_config_override(self):
        cmd = se.CodexEngine(effort="high").command("p", True)
        self.assertIn("model_reasoning_effort=high", [cmd[i + 1] for i, x in enumerate(cmd[:-1]) if x == "-c"])

    def test_plan_and_plan_json_record_model_and_effort(self):
        d = self.approved_skill()
        rc, out, _ = self.cli("plan", d, "--workspace", self.ws, "--engines", "claude", "--claude-model",
                              "claude-sonnet-5-5", "--claude-effort", "high", "--grader-engine", "codex",
                              "--grader-effort", "high")
        self.assertEqual(rc, 0)
        self.assertIn("engines: claude(claude-sonnet-5-5, effort high)", out)
        self.assertIn("--effort high", out)
        self.assertIn("grader: codex(CLI default model, effort high)", out)
        spec, _ = se.load_eval_set(d)
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-sonnet-5-5", exec_fn=T.FakeClaude(),
                                                                 effort="high")], runs=1, log=T.quiet)
        plan = rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))
        self.assertEqual((plan["engines"], plan["efforts"]), ({"claude": "claude-sonnet-5-5"}, {"claude": "high"}))

class TestCodexGraderCommand(unittest.TestCase):
    def test_read_only_default_model_and_effort(self):
        cmd = se.CodexGrader(effort="high").command("grade this", se.GRADE_SCHEMA)
        self.assertEqual(cmd[:3], ["codex", "exec", "--json"])
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
        self.assertNotIn("workspace-write", cmd)
        self.assertNotIn("-m", cmd)  # Codex default model
        self.assertIn("model_reasoning_effort=high", [cmd[i + 1] for i, x in enumerate(cmd[:-1]) if x == "-c"])
        self.assertIn('"enum": ["yes", "no"]', cmd[-1])  # the schema is in the prompt
        self.assertEqual(se.codex_grader_command_problems(cmd), [])

    def test_launch_shape_refuses_anything_but_read_only(self):
        base = ["codex", "exec", "--json", "--skip-git-repo-check"]
        for bad in (base + ["--sandbox", "workspace-write", "p"], base + ["p"],
                    base + ["--sandbox", "read-only", "--sandbox", "danger-full-access", "p"],
                    base + ["--sandbox", "read-only", "--full-auto", "p"],
                    base + ["--sandbox", "read-only", "--dangerously-bypass-approvals-and-sandbox", "p"],
                    base + ["--sandbox", "read-only", "--add-dir", "/", "p"]):
            self.assertTrue(se.codex_grader_command_problems(bad), bad)

    def test_bad_grader_effort_and_engine(self):
        with self.assertRaises(se.EvalError): se.CodexGrader(effort="max")  # not a Codex level
        with self.assertRaises(se.EvalError): se.make_grader("gemini")
        self.assertIsInstance(se.make_grader("codex", effort="high"), se.CodexGrader)
        self.assertEqual(se.make_grader("claude").model, se.DEFAULT_GRADER_MODEL)

class TestCodexGraderCalls(Base):
    """Mocked Codex grader calls: isolation, auth copy lifecycle, conservative charge, fail-closed stops."""
    def bound(self, kind="grader"):
        return se.call_cost(se.DEFAULT_TOKENS[kind], se.CODEX_GRADER_ASSUMED_PRICE) * se.WORST_CASE_FACTOR

    def test_grade_runs_isolated_and_removes_the_auth_copy(self):
        fx, b = FakeCodexGrader(), se.Budget(10.0)
        g = se.CodexGrader(exec_fn=fx, budget=b, effort="high")
        out = g.grade(CASE, {"final": "done", "tool_calls": []}, ITEMS)
        self.assertEqual(out, {"1.0": (True, "only unique rows")})
        c = fx.calls[0]
        self.assertEqual(c["cwd_files"], [])  # fresh empty folder: no project files, skills or AGENTS.md
        self.assertTrue(c["auth_during"])
        self.assertNotEqual(c["codex_home"], os.environ["CODEX_HOME"])  # isolated copy, never the real CODEX_HOME
        self.assertFalse(os.path.lexists(c["codex_home"]))  # copy and sandbox removed after the call
        self.assertEqual(os.listdir(self.runs), [])
        for k in se.NON_SUBSCRIPTION_ENV: self.assertNotIn(k, c["env"])
        self.assertEqual(c["cmd"][c["cmd"].index("--sandbox") + 1], "read-only")
        self.assertAlmostEqual(b.spent, self.bound())  # tiny usage: the worst-case bound is charged
        self.assertEqual(g.usage["calls"], 1)
        self.assertEqual(g.record_name, "codex(CLI default model, effort high)")

    def test_usage_above_the_bound_is_charged(self):
        b = se.Budget(100.0)
        se.CodexGrader(exec_fn=FakeCodexGrader(usage=(400000, 20000, 20000)), budget=b).grade(CASE, {}, ITEMS)
        want = se.call_cost((400000, 40000), se.CODEX_GRADER_ASSUMED_PRICE)
        self.assertGreater(want, self.bound())
        self.assertAlmostEqual(b.spent, want)

    def test_pairwise_judge(self):
        b = se.Budget(10.0)
        w, why = se.CodexGrader(exec_fn=FakeCodexGrader(), budget=b).compare(CASE, {"final": "a"}, {"final": "b"})
        self.assertEqual((w, why), ("A", "r"))
        self.assertAlmostEqual(b.spent, self.bound("pairwise"))

    def test_fenced_json_answer_is_accepted(self):
        ans = "```json\n" + json.dumps({"items": [{"id": "1.0", "answer": "no", "evidence": "dup"}]}) + "\n```"
        out = se.CodexGrader(exec_fn=FakeCodexGrader(answer=ans)).grade(CASE, {}, ITEMS)
        self.assertEqual(out, {"1.0": (False, "dup")})

    def test_non_json_answer_is_charged_and_stops(self):
        b = se.Budget(10.0)
        with self.assertRaises(se.EvalError) as cm:
            se.CodexGrader(exec_fn=FakeCodexGrader(answer="all good!"), budget=b).grade(CASE, {}, ITEMS)
        self.assertIn("not one JSON object", str(cm.exception)); self.assertIn("charged $", str(cm.exception))
        self.assertAlmostEqual(b.spent, self.bound())
        self.assertEqual(os.listdir(self.runs), [])

    def test_failure_after_launch_is_charged_and_stops(self):
        b = se.Budget(10.0)
        with self.assertRaises(se.EvalError) as cm:
            se.CodexGrader(exec_fn=FakeCodexGrader(raise_=TimeoutError()), budget=b).grade(CASE, {}, ITEMS)
        self.assertIn("call failed after launch (TimeoutError)", str(cm.exception))
        self.assertAlmostEqual(b.spent, self.bound())
        self.assertEqual(os.listdir(self.runs), [])

    def test_nonzero_exit_is_charged_and_stops(self):
        b = se.Budget(10.0)
        with self.assertRaises(se.EvalError):
            se.CodexGrader(exec_fn=FakeCodexGrader(rc=1), budget=b).grade(CASE, {}, ITEMS)
        self.assertAlmostEqual(b.spent, self.bound())

    def test_truncated_stream_is_charged_and_stops(self):
        b = se.Budget(10.0)
        fx = lambda cmd, cwd, timeout, env=None: (0, json.dumps({"type": "turn.started"}), "")
        with self.assertRaises(se.EvalError) as cm: se.CodexGrader(exec_fn=fx, budget=b).grade(CASE, {}, ITEMS)
        self.assertIn("could not be parsed", str(cm.exception))
        self.assertAlmostEqual(b.spent, self.bound())

    def test_bound_above_remaining_budget_refuses_before_launch(self):
        fx, b = FakeCodexGrader(), se.Budget(self.bound() - 0.01)
        with self.assertRaises(se.EvalError) as cm: se.CodexGrader(exec_fn=fx, budget=b).grade(CASE, {}, ITEMS)
        self.assertIn("before the call", str(cm.exception))
        self.assertEqual((fx.calls, b.spent), ([], 0.0))

    def test_no_chatgpt_login_refuses_before_launch(self):
        os.remove(self.auth)
        fx, b = FakeCodexGrader(), se.Budget(10.0)
        with self.assertRaises(se.EvalError): se.CodexGrader(exec_fn=fx, budget=b).grade(CASE, {}, ITEMS)
        self.assertEqual((fx.calls, b.spent), ([], 0.0))
        self.assertEqual(os.listdir(self.runs), [])

    def test_auth_copy_changed_by_the_grader_stops(self):
        def fx(cmd, cwd, timeout, env=None):
            p = os.path.join(env["CODEX_HOME"], "auth.json"); os.chmod(p, 0o600)
            with open(p, "w") as f: f.write("{}")
            return 0, codex_jsonl(json.dumps({"items": []})), ""
        b = se.Budget(10.0)
        with self.assertRaises(se.EvalError) as cm: se.CodexGrader(exec_fn=fx, budget=b).grade(CASE, {}, ITEMS)
        self.assertIn("changed or removed the read-only auth.json", str(cm.exception))
        self.assertAlmostEqual(b.spent, self.bound())
        self.assertEqual(os.listdir(self.runs), [])

    def test_live_disabled_by_default(self):
        with self.assertRaises(se.LiveDisabled): se.CodexGrader().grade(CASE, {}, ITEMS)
        self.assertEqual(os.listdir(self.runs), [])

    def test_codex_grader_cannot_grade_codex_runs(self):
        it = os.path.join(self.tmp, "it"); wjson(os.path.join(it, "plan.json"), {"engines": {"codex": None}})
        with self.assertRaises(se.EvalError): se.check_grader_model(it, None, "codex")
        wjson(os.path.join(it, "plan.json"), {"engines": {"claude": "claude-sonnet-5-5"}})
        se.check_grader_model(it, None, "codex")  # Claude under test: fine
        with self.assertRaises(se.EvalError): se.check_grader_model(it, "claude-sonnet-5-5", "claude")

    def test_grade_iteration_records_the_codex_grader(self):
        spec, _ = self.spec(evals=T.evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-sonnet-5-5", exec_fn=T.FakeClaude())],
                             runs=1, log=T.quiet)
        g = se.CodexGrader(exec_fn=FakeCodexGrader(), budget=se.Budget(20.0), effort="high")
        se.grade_iteration(spec, self.ws, n, g, seed=1, log=T.quiet)
        it = os.path.join(self.ws, "iteration-%d" % n)
        cost = rjson(os.path.join(it, "cost.json"))["entries"][-1]
        self.assertEqual(cost["command"], "grade"); self.assertGreater(cost["api_equivalent_usd"], 0)
        comps = [os.path.join(dp, f) for dp, _, fn in os.walk(it) for f in fn if f.startswith("comparison-")]
        self.assertTrue(comps)
        self.assertEqual(rjson(comps[0])["grader_model"], "codex(CLI default model, effort high)")

class TestEstimateAndGates(Base):
    def test_codex_grader_rows_are_priced_at_the_bound(self):
        est = se.estimate(6, 6, 20, 1, [("claude", "claude-sonnet-5-5")], "none", None, grader_engine="codex")
        rows = {r["kind"]: r for r in est["rows"]}
        per_g = se.call_cost(se.DEFAULT_TOKENS["grader"], se.CODEX_GRADER_ASSUMED_PRICE) * se.WORST_CASE_FACTOR
        self.assertEqual(rows["grader"]["calls"], 12); self.assertAlmostEqual(rows["grader"]["usd"], round(12 * per_g, 4))
        self.assertEqual(rows["grader"]["model"], "codex:CLI default")
        self.assertEqual(est["unpriced_models"], [])
        over = se.estimate(6, 6, 20, 1, [("claude", "claude-sonnet-5-5")], "none", None, grader_engine="codex",
                           prices={"grader": (1.0, 1.0)})
        g = {r["kind"]: r for r in over["rows"]}["grader"]
        self.assertAlmostEqual(g["usd"], round(12 * (12000 + 800) / 1e6 * se.WORST_CASE_FACTOR, 4))

    def dry(self, *extra):
        d = self.approved_skill()
        return self.cli("run", d, "--workspace", self.ws, "--live", "--dry-run", "--runs", "1", "--budget-usd", "50",
                        "--claude-model", "claude-sonnet-5-5", "--claude-effort", "high", *extra)

    def test_strict_budget_still_refuses_codex_as_an_engine(self):
        rc, out, _ = self.dry("--engines", "claude,codex", "--grader-engine", "codex", "--grader-effort", "high")
        self.assertEqual(rc, 3); self.assertIn("--strict-budget (default): codex", out)

    def test_claude_under_test_with_codex_grader_passes_the_gates(self):
        with mock.patch.object(se, "codex_auth_mode", side_effect=AssertionError("auth.json read in a dry run")):
            rc, out, _ = self.dry("--engines", "claude", "--grader-engine", "codex", "--grader-effort", "high")
        self.assertEqual(rc, 0, out)
        self.assertIn("live gates that would refuse this command (0)", out)
        self.assertIn("grader budget: SOFT LIMIT", out); self.assertIn("with its tools switched off", out)
        self.assertNotIn("codex:CLI default", out)  # run budget excludes the later grade command

    def test_claude_grader_equal_to_the_model_under_test_is_refused(self):
        rc, out, _ = self.dry("--engines", "claude")  # default claude grader = claude-sonnet-5-5
        self.assertEqual(rc, 3); self.assertIn("is the model under test", out)

    def test_grade_live_with_codex_grader_checks_codex_login_not_claude(self):
        a = mock.Mock(cmd="grade", grader_engine="codex", builder_prompt=None)
        with mock.patch.object(se, "codex_auth_problems", return_value=["codex: no login"]) as cp:
            self.assertEqual(se.subscription_problems(a, []), ["codex: no login"])
        cp.assert_called_once(); self.assertEqual(self.status_calls, [])
        with mock.patch.object(se, "codex_auth_problems", return_value=[]):  # grade runs no engine: no claude login
            self.assertEqual(se.subscription_problems(a, [se.ClaudeCodeEngine("claude-sonnet-5-5")]), [])
        self.assertEqual(self.status_calls, [])
        se.subscription_problems(mock.Mock(cmd="run", grader_engine="codex"), [se.ClaudeCodeEngine("x")])
        self.assertEqual(len(self.status_calls), 1); self.status_calls.clear()  # run executes the Claude engine
        a = mock.Mock(cmd="loop", grader_engine="codex", builder_prompt="b.txt")  # the builder is claude
        with mock.patch.object(se, "codex_auth_problems", return_value=[]):
            se.subscription_problems(a, [])
        self.assertEqual(len(self.status_calls), 1)

class TestCsvCleanerPressureCases(unittest.TestCase):
    def setUp(self):
        self.spec, self.problems = se.load_eval_set(CSV_CLEANER)
        self.cases = {c["id"]: c for c in self.spec["cases"]}

    def test_counts_and_mixed_layers(self):
        self.assertEqual([x for x in self.problems if not x.startswith("english-only rule:")], [])  # golden csv-cleaner is Korean: only english-only problems remain (golden/ is neither exempted nor edited)
        self.assertEqual(len(self.spec["cases"]), 6)
        self.assertEqual(sorted(c["id"] for c in self.spec["cases"] if c["pressure"]), [4, 5, 6])
        layers = [it["layer"] for c in self.spec["cases"] for it in c["items"]]
        self.assertEqual((layers.count("static"), layers.count("llm"), layers.count("human")), (16, 1, 0))
        self.assertEqual({cid: sorted(it["layer"] for it in c["items"]) for cid, c in self.cases.items()},
                         {1: ["static"] * 2, 2: ["static"] * 3, 3: ["static"] * 3,
                          4: ["llm", "static", "static", "static"], 5: ["static"] * 2, 6: ["static"] * 3})

    def item(self, cid, text_part):
        return next(it for it in self.cases[cid]["items"] if text_part in it["text"])

    def test_overwrite_checks_catch_in_place_writes(self):
        sc = lambda it, calls: se.static_check(it["check"], {"tool_calls": calls}, "/nonexistent")[0]
        wr = self.item(4, "Write 도구")
        self.assertFalse(sc(wr, [{"tool": "Write", "input": {"file_path": "/w/evals/files/orders_2025.csv", "content": "x"}}]))
        self.assertTrue(sc(wr, [{"tool": "Write", "input": {"file_path": "/w/orders_2025_clean.csv", "content": "x"}}]))

    def test_output_paths_are_static_file_checks(self):
        want = {1: "orders_clean.csv", 5: "events_clean.csv", 6: "customers_clean.csv"}
        for cid, path in want.items():
            it = self.item(cid, path)
            self.assertEqual((it["layer"], it["check"]), ("static", {"type": "file_exists", "path": path}))
            self.assertIn("현재 폴더에 " + path, self.cases[cid]["prompt"])

    def clean(self, rel):
        out = os.path.join(tempfile.mkdtemp(), "out.csv"); self.addCleanup(shutil.rmtree, os.path.dirname(out))
        with mock.patch.object(sys, "argv", ["clean.py", os.path.join(CSV_CLEANER, rel), out]):
            runpy.run_path(os.path.join(CSV_CLEANER, "scripts", "clean.py"))
        with open(out, newline="") as f: return f.read().splitlines()

    def test_fixtures_match_the_expected_outputs(self):
        raw = open(os.path.join(CSV_CLEANER, "evals/files/events_large.csv")).read().splitlines()
        self.assertEqual(len(raw), 601)
        ev = self.clean("evals/files/events_large.csv")
        self.assertEqual((len(ev), len(set(ev))), (481, 481))  # header + 480 (case 5 expected_output)
        norm = self.clean_lines(raw[:300])
        late_dups = [l for l in raw[300:] if ",".join(c.strip() for c in l.split(",")) in norm]
        self.assertTrue(late_dups)  # duplicates also sit in the second half: cleaning only the head misses them
        cu = self.clean("evals/files/customers_case.csv")
        self.assertEqual(cu, ["email,name,city", "kim@example.com,Kim,Seoul", "KIM@example.com,Kim,Seoul",
                              "lee@example.com,Lee,busan", "lee@example.com,Lee,Busan", "park@example.com,Park,Incheon"])

    @staticmethod
    def clean_lines(lines):
        seen, out = set(), []
        for l in lines:
            k = ",".join(c.strip() for c in l.split(","))
            if k not in seen: seen.add(k); out.append(k)
        return out

if __name__ == "__main__":
    unittest.main()
