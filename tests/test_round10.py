"""Round-10 tests: first-live-run config (Claude Sonnet 5.5 effort high under test) and the csv-cleaner pressure cases.
Everything is mocked: subprocess is patched to fail for the whole module."""
import os, runpy, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, rjson
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
                              "claude-sonnet-5-5", "--claude-effort", "high", "--grader-model", "claude-haiku-5-5",
                              "--grader-effort", "high")
        self.assertEqual(rc, 0)
        self.assertIn("engines: claude(claude-sonnet-5-5, effort high)", out)
        self.assertIn("--effort high", out)
        self.assertIn("grader: claude(claude-haiku-5-5, effort high)", out)
        spec, _ = se.load_eval_set(d)
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-sonnet-5-5", exec_fn=T.FakeClaude(),
                                                                 effort="high")], runs=1, log=T.quiet)
        plan = rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))
        self.assertEqual((plan["engines"], plan["efforts"]), ({"claude": "claude-sonnet-5-5"}, {"claude": "high"}))

class TestGraderEngine(unittest.TestCase):
    def test_only_claude_is_a_grader_engine(self):
        for engine in ("gemini", "codex"):
            with self.assertRaises(se.EvalError): se.make_grader(engine)
        self.assertEqual(se.make_grader("claude").model, se.DEFAULT_GRADER_MODEL)

class TestEstimateAndGates(Base):
    def dry(self, *extra):
        d = self.approved_skill()
        return self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--dry-run", "--runs", "1", "--budget-usd", "50",
                        "--claude-model", "claude-sonnet-5-5", "--claude-effort", "high", *extra)

    def test_strict_budget_still_refuses_codex_as_an_engine(self):
        rc, out, _ = self.dry("--engines", "claude,codex", "--grader-effort", "high")
        self.assertEqual(rc, 3); self.assertIn("--strict-budget (default): codex", out)

    def test_claude_under_test_with_a_grader_of_another_tier_passes_the_gates(self):
        with mock.patch.object(se, "codex_auth_mode", side_effect=AssertionError("auth.json read in a dry run")):
            rc, out, _ = self.dry("--engines", "claude", "--grader-model", "claude-haiku-5-5", "--grader-effort", "high")
        self.assertEqual(rc, 0, out)
        self.assertIn("live gates that would refuse this command (0)", out)

    def test_claude_grader_of_the_tier_under_test_is_refused(self):
        rc, out, _ = self.dry("--engines", "claude")  # default claude grader = claude-sonnet-5-5
        self.assertEqual(rc, 3); self.assertIn("has the same tier (sonnet)", out)

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
