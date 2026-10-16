"""Codex grader cannot read auth.json: every tool is disabled on the command line, any tool item in the event stream
fails the call closed, and an answer/stream containing an auth.json value is discarded. Mocked: no model call."""
import json, os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, codex_jsonl
from test_round3_isolation import Base
from test_round10 import FakeCodexGrader, CASE, ITEMS
_p = []
def setUpModule():
    for n in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, n, T._forbidden); pt.start(); _p.append(pt)
def tearDownModule():
    for pt in _p: pt.stop()

class FakeStream(FakeCodexGrader):
    def __init__(self, stream): FakeCodexGrader.__init__(self); self.stream = stream
    def __call__(self, cmd, cwd, timeout, env=None):
        FakeCodexGrader.__call__(self, cmd, cwd, timeout, env=env); return 0, self.stream, ""

OK = json.dumps({"items": [{"id": "1.0", "answer": "yes", "evidence": "fine"}]})

class TestLockdown(Base):
    def test_command_disables_every_tool(self):
        cmd = se.CodexGrader(effort="high").command("x", se.GRADE_SCHEMA)
        off = [cmd[i + 1] for i, x in enumerate(cmd[:-1]) if x == "--disable"]
        for f in ("shell_tool", "unified_exec", "view_image", "browser_use", "computer_use", "apps", "plugins"):
            self.assertIn(f, off)
        self.assertIn('web_search="disabled"', cmd)
        self.assertEqual(se.codex_grader_command_problems(cmd), [])
    def test_launch_shape_refuses_missing_disable_or_enable(self):
        cmd = se.CodexGrader().command("x", se.GRADE_SCHEMA)
        i = cmd.index("shell_tool"); bad = cmd[:i - 1] + cmd[i + 1:]
        self.assertTrue(se.codex_grader_command_problems(bad))
        self.assertTrue(se.codex_grader_command_problems(cmd[:-1] + ["--enable", "shell_tool", cmd[-1]]))
        j = cmd.index('web_search="disabled"'); self.assertTrue(se.codex_grader_command_problems(cmd[:j - 1] + cmd[j + 1:]))
    def test_tool_use_in_stream_fails_closed(self):
        fx = FakeStream(codex_jsonl(OK, cmds=["cat $CODEX_HOME/auth.json"]))
        with self.assertRaises(se.EvalError) as cm: se.CodexGrader(exec_fn=fx, budget=se.Budget(5.0)).grade(CASE, {}, ITEMS)
        self.assertIn("used tools", str(cm.exception))
    def test_auth_value_in_answer_is_discarded(self):
        leak = json.dumps({"items": [{"id": "1.0", "answer": "yes", "evidence": "token FAKE-SECRET-TOKEN"}]})
        with self.assertRaises(se.EvalError) as cm:
            se.CodexGrader(exec_fn=FakeStream(codex_jsonl(leak)), budget=se.Budget(5.0)).grade(CASE, {}, ITEMS)
        self.assertIn("auth.json value", str(cm.exception)); self.assertNotIn("FAKE-SECRET-TOKEN", str(cm.exception))
    def test_reasoning_items_and_clean_answer_still_grade(self):
        s = codex_jsonl(OK).splitlines()
        s.insert(2, json.dumps({"type": "item.completed", "item": {"id": "r", "type": "reasoning", "text": "think"}}))
        out = se.CodexGrader(exec_fn=FakeStream("\n".join(s)), budget=se.Budget(5.0)).grade(CASE, {}, ITEMS)
        self.assertEqual(out["1.0"][0], True)
    def test_auth_secret_strings(self):
        v = se.auth_secret_strings(b'{"a":"short","t":{"id":"eyJhbGciOiJIUzI1NiJ9xxxxxx.eyJzdWIiOiIxMjM0NTY3ODkwIn0yy.sig"}}')
        self.assertNotIn("short", v); self.assertTrue(any(x.startswith("eyJzdWIi") for x in v))
        self.assertEqual(se.auth_secret_strings(b"not json"), set())
if __name__ == "__main__": unittest.main()
