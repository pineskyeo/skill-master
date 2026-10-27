"""Round-8 regression tests (Codex round-7 review codex-r7.md) for skill_eval.py.
Everything is mocked: subprocess is patched to fail for the whole module; the CLIs and the builder are fakes.
No model or real credential is touched."""
import json, os, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, evals_doc, codex_jsonl, quiet, read, rjson
from test_round3_isolation import Base

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def plan_of(ws, n):
    return rjson(os.path.join(ws, "iteration-%d" % n, "plan.json"))

def jl(*events):
    return "\n".join(json.dumps(e) for e in events)

TRUNCATED = (
    jl({"type": "thread.started", "thread_id": "t"}),
    jl({"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
       {"type": "item.completed", "item": {"id": "j", "type": "agent_message", "text": "partial"}}),
    jl({"type": "thread.started", "thread_id": "t"}, {"type": "error", "message": "stream closed"}),
)

class TestCodexTruncatedStream(Base):
    """P2: a Codex stream without turn.completed / turn.failed is a failed call: bound charged, run stopped."""
    PRICES = {"codex": (2.0, 10.0)}

    def run_with(self, out):
        spec, _ = self.spec(evals=evals_doc(human=False))
        shutil.rmtree(self.ws, ignore_errors=True)
        calls = []
        b = se.Budget(100.0)
        eng = se.CodexEngine(exec_fn=lambda *a, **k: calls.append(a) or (0, out, ""))
        n = se.run_iteration(spec, self.ws, [eng], runs=1, budget=b, prices=self.PRICES, log=quiet)
        return b, plan_of(self.ws, n)["stopped"], calls

    def bound(self):
        return se.call_bound("codex", None, "exec", self.PRICES, se.DEFAULT_TOKENS)

    def test_parse_raises_without_terminal_event(self):
        for out in TRUNCATED:
            with self.assertRaises(ValueError, msg=out) as cm: se.CodexEngine().parse(out)
            self.assertIn("no turn.completed or turn.failed", str(cm.exception))

    def test_terminal_events_still_parse(self):
        self.assertEqual(se.CodexEngine().parse(codex_jsonl("ok"))["final"], "ok")
        r = se.CodexEngine().parse(jl({"type": "thread.started"}, {"type": "turn.failed", "error": {"message": "boom"}}))
        self.assertIn("boom", r["error"])

    def test_truncated_stream_charges_the_bound_and_stops(self):
        for out in TRUNCATED:
            b, stopped, calls = self.run_with(out)
            self.assertEqual(len(calls), 1, out)  # stopped after the first paid call
            self.assertAlmostEqual(b.spent, self.bound())
            self.assertIn("output could not be parsed after the call (ValueError)", stopped)
            self.assertIn("cap_charged", stopped)
            self.assertEqual(os.listdir(self.runs), [])

class TestBuilderNonResultObject(Base):
    """P2: builder output that is a JSON object but not a result ({} etc.) is charged max(reported, cap) and stops."""
    def build(self, out):
        it = os.path.join(self.tmp, "it"); os.makedirs(it, exist_ok=True)
        b = se.Budget(10.0)  # cap = 0.5 x 10 = $5.00
        bld = se.ClaudeBuilder("fix", "claude-opus-5-5", b, exec_fn=lambda *a, **k: (0, out, ""))
        with self.assertRaises(se.EvalError) as cm: bld(it, self.tmp)
        return b, str(cm.exception), rjson(os.path.join(it, "cost.json"))["entries"][-1]

    def test_empty_object_charges_the_cap_and_stops(self):
        b, msg, entry = self.build("{}")
        self.assertIn("builder output is not a result object", msg)
        self.assertIn("charged $5.00 and stopped", msg)
        self.assertEqual(b.spent, 5.0)
        self.assertEqual((entry["command"], entry["calls"], entry["api_equivalent_usd"]), ("builder", 1, 5.0))

    def test_other_non_result_objects(self):
        for out in ('{"type": "system"}', '{"usage": {"input_tokens": 1, "output_tokens": 1}}', '{"type": null}'):
            b, msg, _ = self.build(out)
            self.assertIn("builder output is not a result object", msg, out)
            self.assertEqual(b.spent, 5.0, out)

    def test_reported_cost_above_the_cap_is_kept(self):
        b, msg, entry = self.build(json.dumps({"type": "assistant", "total_cost_usd": 7.25}))
        self.assertIn("charged $7.25 and stopped", msg)
        self.assertAlmostEqual(b.spent, 7.25); self.assertAlmostEqual(entry["api_equivalent_usd"], 7.25)

class TestDocs(unittest.TestCase):
    def test_docs_document_unfinished_streams(self):
        docs = T.docs_text()
        for s in ("builder output is not a result object", "turn.completed", "turn.failed"):
            self.assertIn(s, docs, s)

if __name__ == "__main__":
    unittest.main()
