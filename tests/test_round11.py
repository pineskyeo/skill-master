"""Round-11 tests: a pairwise winner outside A/B/TIE is charged, recorded as failed and stops (never a silent TIE);
the Codex grader runs the executor's ancestor-folder and /etc/codex/skills checks before launch; the live gate refuses
a Codex executor paired with a Codex grader before any call. Everything is mocked (subprocess patched to fail)."""
import json, os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, rjson, wjson
from test_round3_isolation import Base
from test_round10 import FakeCodexGrader, CASE, ITEMS

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def skill(root, name):
    wjson(os.path.join(root, name, "x.json"), {})
    with open(os.path.join(root, name, "SKILL.md"), "w") as f: f.write("---\nname: %s\n---\n" % name)

class TestInvalidPairwiseWinner(Base):
    def bound(self):
        return se.call_cost(se.DEFAULT_TOKENS["pairwise"], se.CODEX_GRADER_ASSUMED_PRICE) * se.WORST_CASE_FACTOR

    def test_codex_judge_bad_winner_is_charged_and_raises(self):
        for bad in ("C", "a", None, "", 1):
            b = se.Budget(10.0)
            fx = FakeCodexGrader(answer=json.dumps({"winner": bad, "reasoning": "r"}))
            with self.assertRaises(se.JudgeAnswerError) as cm:
                se.CodexGrader(exec_fn=fx, budget=b).compare(CASE, {"final": "a"}, {"final": "b"})
            self.assertIn("not A, B or TIE", str(cm.exception))
            self.assertAlmostEqual(b.spent, self.bound())

    def test_claude_judge_bad_winner_is_charged_and_raises(self):
        b = se.Budget(10.0)
        out = json.dumps({"type": "result", "structured_output": {"winner": "BOTH", "reasoning": "r"},
                          "usage": {"input_tokens": 100, "output_tokens": 10}, "total_cost_usd": 0.02})
        g = se.LLMGrader("claude-sonnet-5-5", exec_fn=lambda *a, **k: (0, out, ""), budget=b)
        with mock.patch.object(se, "launch_claude", lambda fn, cmd, cwd, t: (0, out, "")):
            with self.assertRaises(se.JudgeAnswerError):
                g.compare(CASE, {"final": "a"}, {"final": "b"})
        self.assertEqual(g.usage["calls"], 1); self.assertGreater(b.spent, 0)

    def test_grade_iteration_records_failure_and_cost(self):
        spec, _ = self.spec(evals=T.evals_doc(human=False))
        engs = [se.ClaudeCodeEngine("claude-sonnet-5-5", exec_fn=T.FakeClaude())]
        n = se.run_iteration(spec, self.ws, engs, runs=1, log=T.quiet)
        def fx(cmd, cwd, timeout, env=None):
            ans = json.dumps({"winner": "X", "reasoning": "r"}) if '"winner"' in cmd[-1] else \
                json.dumps({"items": [{"id": "1.0", "answer": "yes", "evidence": "e"}]})
            return 0, T.codex_jsonl(ans, usage=(1000, 100, 50)), ""
        g = se.CodexGrader(exec_fn=fx, budget=se.Budget(50.0))
        with self.assertRaises(se.JudgeAnswerError):
            se.grade_iteration(spec, self.ws, n, g, seed=1, log=T.quiet)
        it = os.path.join(self.ws, "iteration-%d" % n)
        comps = [os.path.join(dp, f) for dp, _, fn in os.walk(it) for f in fn if f.startswith("comparison-")]
        self.assertEqual(len(comps), 1)
        c = rjson(comps[0])
        self.assertEqual((c["status"], c["winner"], c["invalid_winner"], c["winner_configuration"]),
                         ("failed", None, "X", None))
        self.assertIn("not A, B or TIE", c["error"])
        cost = rjson(os.path.join(it, "cost.json"))["entries"][-1]
        self.assertEqual(cost["command"], "grade"); self.assertGreater(cost["api_equivalent_usd"], 0)

class TestCodexGraderSkillChecks(Base):
    def test_ancestor_skill_refuses_before_launch(self):
        skill(os.path.join(self.tmp, ".agents", "skills"), "intruder")  # self.tmp is an ancestor of the sandbox
        fx, b = FakeCodexGrader(), se.Budget(10.0)
        with self.assertRaises(se.EvalError) as cm: se.CodexGrader(exec_fn=fx, budget=b).grade(CASE, {}, ITEMS)
        self.assertIn("codex grader launch refused", str(cm.exception)); self.assertIn("intruder", str(cm.exception))
        self.assertEqual((fx.calls, b.spent), ([], 0.0))
        self.assertEqual(os.listdir(self.runs), [])  # sandbox (and auth copy) removed

    def test_admin_skill_refuses_before_launch(self):
        admin = os.path.join(self.tmp, "etc-codex-skills"); skill(admin, "admin-one")
        fx, b = FakeCodexGrader(), se.Budget(10.0)
        with mock.patch.object(se, "CODEX_ADMIN_SKILLS", admin):
            with self.assertRaises(se.EvalError) as cm: se.CodexGrader(exec_fn=fx, budget=b).grade(CASE, {}, ITEMS)
            self.assertIn("admin-one", str(cm.exception))
            self.assertTrue(any("admin-one" in p for p in se.preflight(self.spec()[0], ["codex"], self.runs)))
        self.assertEqual((fx.calls, b.spent), ([], 0.0))
        self.assertEqual(os.listdir(self.runs), [])

    def test_direct_link_to_deep_skill_refuses_before_launch(self):
        # round 12: depth is measured on the visible path under the scanned root, not on the resolved link target
        deep = os.path.join(self.tmp, "deep", "a", "b", "c", "d", "e", "f"); skill(deep, "linked-deep")
        ag = os.path.join(self.tmp, ".agents", "skills"); os.makedirs(ag)
        os.symlink(os.path.join(deep, "linked-deep"), os.path.join(ag, "linked-deep"))
        fx, b = FakeCodexGrader(), se.Budget(10.0)
        with self.assertRaises(se.EvalError) as cm: se.CodexGrader(exec_fn=fx, budget=b).grade(CASE, {}, ITEMS)
        self.assertIn("linked-deep", str(cm.exception))
        self.assertEqual((fx.calls, b.spent), ([], 0.0))
        self.assertEqual(os.listdir(self.runs), [])

    def test_admin_direct_link_to_deep_skill_refuses_before_launch(self):
        deep = os.path.join(self.tmp, "deep", "a", "b", "c", "d", "e", "f"); skill(deep, "admin-deep")
        admin = os.path.join(self.tmp, "etc-codex-skills"); os.makedirs(admin)
        os.symlink(os.path.join(deep, "admin-deep"), os.path.join(admin, "admin-deep"))
        fx, b = FakeCodexGrader(), se.Budget(10.0)
        with mock.patch.object(se, "CODEX_ADMIN_SKILLS", admin):
            with self.assertRaises(se.EvalError) as cm: se.CodexGrader(exec_fn=fx, budget=b).grade(CASE, {}, ITEMS)
            self.assertIn("admin-deep", str(cm.exception))
        self.assertEqual((fx.calls, b.spent), ([], 0.0))

    def test_skills_under_depth_is_visible_path_depth(self):
        deep = os.path.join(self.tmp, "deep", "a", "b", "c", "d", "e", "f"); skill(deep, "far")
        root = os.path.join(self.tmp, "scan"); os.makedirs(os.path.join(root, "x", "y"))
        os.symlink(os.path.join(deep, "far"), os.path.join(root, "far"))
        self.assertEqual([n for n, _ in se.skills_under(root, 1)], ["far"])
        # a link to a shallow folder does not let a visibly deep skill through
        skill(os.path.join(self.tmp, "shallow", "p", "q"), "too-deep")
        os.symlink(os.path.join(self.tmp, "shallow"), os.path.join(root, "x", "y", "s"))
        self.assertNotIn("too-deep", [n for n, _ in se.skills_under(root, 3)])
        self.assertIn("too-deep", [n for n, _ in se.skills_under(root, 6)])  # scan/x/y/s/p/q/too-deep

    def test_clean_tree_still_launches(self):
        fx = FakeCodexGrader()
        se.CodexGrader(exec_fn=fx, budget=se.Budget(10.0)).grade(CASE, {}, ITEMS)
        self.assertEqual(len(fx.calls), 1)

class TestCodexExecutorWithCodexGraderGate(Base):
    def dry(self, *extra):
        d = self.approved_skill()
        return self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--dry-run", "--runs", "1", "--budget-usd", "50",
                        "--claude-model", "claude-sonnet-5-5", *extra)

    def test_refused_even_with_no_strict_budget(self):
        rc, out, _ = self.dry("--engines", "claude,codex", "--grader-engine", "codex", "--no-strict-budget")
        self.assertEqual(rc, 3); self.assertIn("a codex grader cannot grade codex runs", out)

    def test_live_gate_raises_before_any_call(self):
        a = mock.Mock(cmd="run", grader_engine="codex", strict_budget=False)
        fx = T.FakeCodex()
        with self.assertRaises(se.EvalError) as cm:
            se.check_live_gates(a, {}, [], self.ws, 1, [se.CodexEngine(exec_fn=fx)], se.DEFAULT_TOKENS, {}, 50.0)
        self.assertIn("a codex grader cannot grade codex runs", str(cm.exception))
        self.assertEqual(fx.calls, []); self.assertEqual(self.status_calls, [])

    def test_codex_executor_with_claude_grader_not_refused_by_this_gate(self):
        rc, out, _ = self.dry("--engines", "codex", "--grader-engine", "claude", "--no-strict-budget")
        self.assertNotIn("a codex grader cannot grade codex runs", out)

if __name__ == "__main__":
    unittest.main()
