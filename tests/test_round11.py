"""Round-11 tests: a pairwise winner outside A/B/TIE is charged, recorded as failed and stops (never a silent TIE);
preflight runs the Codex executor's ancestor-folder and /etc/codex/skills checks. Everything is mocked (subprocess
patched to fail)."""
import json, os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, rjson, wjson
from test_round3_isolation import Base
from test_round10 import CASE

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
    def test_claude_judge_bad_winner_is_charged_and_raises(self):
        for bad in ("BOTH", "C", "a", None, "", 1):
            b = se.Budget(10.0)
            out = json.dumps({"type": "result", "structured_output": {"winner": bad, "reasoning": "r"},
                              "usage": {"input_tokens": 100, "output_tokens": 10}, "total_cost_usd": 0.02})
            g = se.LLMGrader("claude-sonnet-5-5", exec_fn=lambda *a, **k: (0, out, ""), budget=b)
            with mock.patch.object(se, "launch_claude", lambda fn, cmd, cwd, t: (0, out, "")):
                with self.assertRaises(se.JudgeAnswerError) as cm:
                    g.compare(CASE, {"final": "a"}, {"final": "b"})
            self.assertIn("not A, B or TIE", str(cm.exception))
            self.assertEqual(g.usage["calls"], 1); self.assertGreater(b.spent, 0)

    def test_grade_iteration_records_failure_and_cost(self):
        spec, _ = self.spec(evals=T.evals_doc(human=False))
        engs = [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=T.FakeClaude())]
        n = se.run_iteration(spec, self.ws, engs, runs=1, log=T.quiet)
        g = self.grader(compare_pick="X")
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

class TestCodexSkillChecks(Base):
    def preflight(self):
        return se.preflight(self.spec()[0], ["codex"], self.runs)

    def test_ancestor_skill_is_a_preflight_problem(self):
        skill(os.path.join(self.tmp, ".agents", "skills"), "intruder")  # self.tmp is an ancestor of the sandbox
        self.assertTrue(any("intruder" in p for p in self.preflight()))

    def test_admin_skill_is_a_preflight_problem(self):
        admin = os.path.join(self.tmp, "etc-codex-skills"); skill(admin, "admin-one")
        with mock.patch.object(se, "CODEX_ADMIN_SKILLS", admin):
            self.assertTrue(any("admin-one" in p for p in self.preflight()))

    def test_direct_link_to_deep_skill_is_a_preflight_problem(self):
        # round 12: depth is measured on the visible path under the scanned root, not on the resolved link target
        deep = os.path.join(self.tmp, "deep", "a", "b", "c", "d", "e", "f"); skill(deep, "linked-deep")
        ag = os.path.join(self.tmp, ".agents", "skills"); os.makedirs(ag)
        os.symlink(os.path.join(deep, "linked-deep"), os.path.join(ag, "linked-deep"))
        self.assertTrue(any("linked-deep" in p for p in self.preflight()))

    def test_admin_direct_link_to_deep_skill_is_a_preflight_problem(self):
        deep = os.path.join(self.tmp, "deep", "a", "b", "c", "d", "e", "f"); skill(deep, "admin-deep")
        admin = os.path.join(self.tmp, "etc-codex-skills"); os.makedirs(admin)
        os.symlink(os.path.join(deep, "admin-deep"), os.path.join(admin, "admin-deep"))
        with mock.patch.object(se, "CODEX_ADMIN_SKILLS", admin):
            self.assertTrue(any("admin-deep" in p for p in self.preflight()))

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

if __name__ == "__main__":
    unittest.main()
