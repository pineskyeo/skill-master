"""Round-9 regression tests (Codex round-8 review codex-r8.md) for skill_eval.py.
Everything is mocked: subprocess is patched to fail for the whole module; the builder CLI is a fake.
No model or real credential is touched."""
import json, os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, evals_doc, quiet, read, rjson
from test_round3_isolation import Base

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

ERR = {"type": "result", "subtype": "error_max_turns", "is_error": True,
       "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.2}

class TestBuilderIsError(Base):
    """P2: a builder result with is_error true is charged max(reported, cap) and stops, even with exit 0."""
    def build(self, out, rc=0):
        it = os.path.join(self.tmp, "it"); os.makedirs(it, exist_ok=True)
        b = se.Budget(10.0)  # cap = 0.5 x 10 = $5.00
        bld = se.ClaudeBuilder("fix", "claude-opus-5-5", b, exec_fn=lambda *a, **k: (rc, out, ""))
        with self.assertRaises(se.EvalError) as cm: bld(it, self.tmp)
        return b, str(cm.exception), rjson(os.path.join(it, "cost.json"))["entries"][-1]

    def test_is_error_with_exit_0_charges_the_cap_and_stops(self):
        b, msg, entry = self.build(json.dumps(ERR))
        self.assertIn("builder result is an error (subtype 'error_max_turns', exit 0)", msg)
        self.assertIn("charged $5.00 and stopped", msg)
        self.assertEqual(b.spent, 5.0)
        self.assertEqual((entry["command"], entry["calls"], entry["api_equivalent_usd"]), ("builder", 1, 5.0))

    def test_is_error_with_nonzero_exit_also_charges_the_cap(self):
        b, msg, _ = self.build(json.dumps(ERR), rc=1)
        self.assertIn("exit 1", msg); self.assertEqual(b.spent, 5.0)

    def test_reported_cost_above_the_cap_is_kept(self):
        b, msg, entry = self.build(json.dumps(dict(ERR, total_cost_usd=7.25)))
        self.assertIn("charged $7.25 and stopped", msg)
        self.assertAlmostEqual(b.spent, 7.25); self.assertAlmostEqual(entry["api_equivalent_usd"], 7.25)

    def test_is_error_false_still_succeeds(self):
        it = os.path.join(self.tmp, "it"); os.makedirs(it, exist_ok=True)
        b = se.Budget(10.0)
        out = json.dumps(dict(ERR, is_error=False, subtype="success"))
        rc = se.ClaudeBuilder("fix", "claude-opus-5-5", b, exec_fn=lambda *a, **k: (0, out, ""))(it, self.tmp)
        self.assertEqual(rc, 0); self.assertAlmostEqual(b.spent, 0.2)

    def test_loop_stops_with_builder_failed(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        b = se.Budget(10.0)
        bld = se.ClaudeBuilder("fix", "claude-opus-5-5", b, exec_fn=lambda *a, **k: (0, json.dumps(ERR), ""))
        os.makedirs(os.path.join(self.ws, "iteration-1"), exist_ok=True)
        with mock.patch.object(se, "run_iteration", return_value=1), \
             mock.patch.object(se, "grade_iteration"), \
             mock.patch.object(se, "report_iteration", return_value=(None, {"problems": ["fail"]})):
            res = se.loop(spec, self.ws, [], None, bld, max_iterations=2, budget=b, log=quiet)
        self.assertEqual(res["status"], "builder-failed")
        self.assertIn("builder result is an error", res["error"])
        self.assertEqual(b.spent, 5.0)

class TestDocs(unittest.TestCase):
    def test_docs_document_builder_error_result(self):
        docs = T.docs_text()
        for s in ("builder result is an error", "is_error"):
            self.assertIn(s, docs, s)

if __name__ == "__main__":
    unittest.main()
