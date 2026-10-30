"""Round-23 tests: `grade --grade-cases/--grade-runs` narrows grading (static, LLM, pairwise), the cost estimate
counts only the subset, and runs outside the subset keep their files untouched. Everything is mocked."""
import os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, rjson, wjson

_patches = []
def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)
def tearDownModule():
    for pt in _patches: pt.stop()

def quiet(*a, **k): pass

class TestGradeScope(T.Base):
    def setUp(self):
        super().setUp()
        self.sp, problems = self.spec(); self.assertEqual(problems, [])
        self.n = se.run_iteration(self.sp, self.ws, self.engines(), runs=3, log=quiet)
        self.it = os.path.join(self.ws, "iteration-%d" % self.n)

    def grading_files(self):
        out = []
        for root, _, files in os.walk(self.it):
            out += [os.path.relpath(os.path.join(root, f), self.it) for f in files
                    if f == "grading.json" or f.startswith("comparison-")]
        return sorted(out)

    def test_parse(self):
        self.assertIsNone(se.parse_id_list(None, "--x"))
        self.assertEqual(se.parse_id_list("2, 1,2", "--x"), frozenset({1, 2}))
        for bad in ("", "0", "-1", "a", "1;2", "1.5"):
            with self.assertRaises(se.EvalError): se.parse_id_list(bad, "--x")

    def test_counts_narrowed(self):
        full = se.grade_counts(self.sp, self.it)
        one = se.grade_counts(self.sp, self.it, (frozenset({1}), frozenset({1})))
        self.assertLess(one[0], full[0]); self.assertLess(one[1], full[1])
        self.assertEqual(one[1], 1)  # case 1, run 1, one pair for the one engine
        self.assertEqual(se.grade_counts(self.sp, self.it, (None, None)), full)

    def test_unknown_case_refused(self):
        with self.assertRaises(se.EvalError):
            se.grade_iteration(self.sp, self.ws, self.n, None, log=quiet, scope=(frozenset({99}), None))

    def test_scoped_grade_touches_only_subset(self):
        other = os.path.join(self.it, "claude", "eval-2-no-trigger", "with_skill", "run-1", "grading.json")
        wjson(other, {"sentinel": True})
        wjson(os.path.join(self.it, "human_review.json"), {"items": [{"key": "keep#1", "passed": True}]})
        g = self.grader()
        se.grade_iteration(self.sp, self.ws, self.n, g, seed=1, log=quiet, scope=(frozenset({1}), frozenset({1})))
        files = self.grading_files()
        self.assertEqual(rjson(other), {"sentinel": True})
        for f in files:
            if f.endswith("grading.json") and "sentinel" not in rjson(os.path.join(self.it, f)):
                self.assertIn("eval-1-", f); self.assertIn("run-1", f)
            if "comparison-" in f:
                self.assertTrue(f.endswith("comparison-1.json")); self.assertIn("eval-1-", f)
        self.assertTrue(any("comparison-1.json" in f for f in files))
        keys = [x["key"] for x in rjson(os.path.join(self.it, "human_review.json"))["items"]]
        self.assertIn("keep#1", keys)

    def test_estimate_and_gate_use_subset(self):
        a = se.build_parser().parse_args(["grade", "x", "--grade-cases", "1", "--grade-runs", "1"])
        self.assertEqual(se.grade_scope(a), (frozenset({1}), frozenset({1})))
        b = se.build_parser().parse_args(["grade", "x"])
        self.assertEqual(se.grade_scope(b), (None, None))
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            se.build_parser().parse_args(["run", "x", "--grade-cases", "1"])

if __name__ == "__main__": unittest.main()
