"""Round-7 regression tests (Codex round-6 review codex-r6.md) for skill_eval.py.
Everything is mocked: subprocess is patched to fail for the whole module; `claude auth status`, the CLIs and the
builder are fakes. No model or real credential is touched."""
import json, os, shlex, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, evals_doc, codex_jsonl, FakeClaude, quiet, read, write, wjson, rjson
from test_round3_isolation import Base

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

SECRET = "oat-ROUND7-MUST-NEVER-APPEAR"

def plan_of(ws, n):
    return rjson(os.path.join(ws, "iteration-%d" % n, "plan.json"))

class TestBuilderUnreadableOutput(Base):
    """P2: builder output that is not a JSON object is charged max(reported, cap) and stops (no normal return)."""
    def build(self, out):
        it = os.path.join(self.tmp, "it"); os.makedirs(it, exist_ok=True)
        b = se.Budget(10.0)  # cap = 0.5 x 10 = $5.00
        bld = se.ClaudeBuilder("fix", "claude-opus-5-5", b, exec_fn=lambda *a, **k: (0, out, ""))
        with self.assertRaises(se.EvalError) as cm: bld(it, self.tmp)
        return b, str(cm.exception), rjson(os.path.join(it, "cost.json"))["entries"][-1]

    def test_unreadable_output_charges_the_cap_and_stops(self):
        for out in ("", "not json at all", "[1, 2]", "null", '"text"'):
            b, msg, entry = self.build(out)
            self.assertIn("builder output is not a JSON object", msg, out)
            self.assertIn("charged $5.00 and stopped", msg, out)
            self.assertEqual(b.spent, 5.0, out)
            self.assertEqual((entry["command"], entry["calls"], entry["api_equivalent_usd"]), ("builder", 1, 5.0))

    def test_reported_cost_above_the_cap_is_kept(self):
        # multi-line output: the whole is not JSON, one line reports more than the cap
        out = "warning: something\n" + json.dumps({"type": "result", "total_cost_usd": 7.25, "usage": {}})
        b, msg, entry = self.build(out)
        self.assertIn("charged $7.25 and stopped", msg)
        self.assertAlmostEqual(b.spent, 7.25); self.assertAlmostEqual(entry["api_equivalent_usd"], 7.25)

    def test_small_reported_cost_never_undercuts_the_cap(self):
        b, msg, _ = self.build("junk\n" + json.dumps({"total_cost_usd": 0.01}))
        self.assertEqual(b.spent, 5.0); self.assertIn("charged $5.00", msg)

    def test_valid_output_still_returns(self):
        it = os.path.join(self.tmp, "it"); os.makedirs(it)
        b = se.Budget(10.0)
        out = json.dumps({"type": "result", "total_cost_usd": 0.2, "usage": {"input_tokens": 10, "output_tokens": 5}})
        bld = se.ClaudeBuilder("fix", "claude-opus-5-5", b, exec_fn=lambda *a, **k: (0, out, ""))
        self.assertEqual(bld(it, self.tmp), 0)
        self.assertLess(b.spent, 5.0)

class TestCodexUnreadableStream(Base):
    """P2: a Codex stream with no readable JSON event is a failed call: max(reported, bound) charged, run stopped."""
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

    def test_parse_raises_on_unreadable_stream(self):
        for out in ("", "garbage\nmore garbage", "[1]\n2\nnull", json.dumps({"no_type": 1})):
            with self.assertRaises(ValueError, msg=out): se.CodexEngine().parse(out)
        r = se.CodexEngine().parse("noise\n" + codex_jsonl("ok"))  # readable events among noise: parsed normally
        self.assertEqual(r["final"], "ok")

    def test_unreadable_stream_charges_the_bound_and_stops(self):
        for out in ("", "Segmentation fault\n<html>oops</html>"):
            b, stopped, calls = self.run_with(out)
            self.assertEqual(len(calls), 1, out)  # stopped after the first paid call
            self.assertAlmostEqual(b.spent, self.bound())
            self.assertIn("output could not be parsed after the call (ValueError)", stopped)
            self.assertIn("cap_charged", stopped)
            self.assertEqual(os.listdir(self.runs), [])

    def test_reported_cost_above_the_bound_is_kept(self):
        big = self.bound() + 3.0
        b, stopped, _ = self.run_with("garbage\n" + json.dumps({"total_cost_usd": big}))
        self.assertAlmostEqual(b.spent, big)
        self.assertIn("charged $%.2f (cli_reported)" % big, stopped)

class TestSettingsOauthToken(Base):
    """P2: CLAUDE_CODE_OAUTH_TOKEN (and API keys) in any loadable settings "env" is refused before the call."""
    def test_preflight_refuses_token_in_every_settings_source(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        self.assertEqual(se.preflight(spec, ["claude"], self.runs), [])
        paths = (os.path.join(self.tmp, ".claude", "settings.local.json"),   # ancestor of run_root
                 os.path.join(self.tmp, ".claude", "settings.json"),
                 os.path.join(self.runs, ".claude", "settings.json"),
                 os.path.join(self.home, ".claude", "settings.json"),          # user settings of the config dir
                 os.path.join(self.home, ".claude", se.CLAUDE_REMOTE_SETTINGS),  # server-managed cache
                 self.managed)
        for path in paths:
            wjson(path, {"env": {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}})
            pf = se.preflight(spec, ["claude"], self.runs)
            self.assertEqual(len(pf), 1, (path, pf)); self.assertIn(path, pf[0])
            self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", pf[0]); self.assertIn("Keychain login", pf[0])
            self.assertNotIn(SECRET, "\n".join(pf))
            self.assertEqual(se.preflight(spec, ["codex"], self.runs), [])
            os.remove(path)

    def test_token_and_api_key_together_both_reported(self):
        p = os.path.join(self.tmp, "s.json")
        wjson(p, {"env": {"CLAUDE_CODE_OAUTH_TOKEN": SECRET, "ANTHROPIC_API_KEY": SECRET}, "apiKeyHelper": SECRET})
        probs = se.claude_settings_auth_problems([p])
        self.assertEqual(len(probs), 2, probs)
        self.assertIn("configures API-key auth (apiKeyHelper, ANTHROPIC_API_KEY", probs[0])
        self.assertIn("sets CLAUDE_CODE_OAUTH_TOKEN", probs[1])
        self.assertNotIn(SECRET, "\n".join(probs))

    def test_fixture_settings_token_stops_the_run_before_the_call(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        wjson(os.path.join(spec["skill_dir"], ".claude", "settings.local.json"), {"env": {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}})
        spec["cases"][0]["files"] = [".claude/settings.local.json"]
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        stopped = plan_of(self.ws, n)["stopped"]
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", stopped); self.assertNotIn(SECRET, stopped)
        self.assertEqual(self.fc.calls, []); self.assertEqual(os.listdir(self.runs), [])

    def test_grader_and_builder_launch_refused(self):
        cwd = os.path.join(self.tmp, "proj"); os.makedirs(cwd)
        wjson(os.path.join(cwd, ".claude", "settings.json"), {"env": {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}})
        calls = []
        cmd = ["claude", "-p", "x"] + se.claude_isolation_args()
        with self.assertRaises(se.EvalError) as cm:
            se.launch_claude(lambda *a, **k: calls.append(a) or (0, "", ""), cmd, cwd, 10)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", str(cm.exception)); self.assertNotIn(SECRET, str(cm.exception))
        self.assertEqual(calls, [])

class TestLiveCommandQuoting(Base):
    """P3: the printed live command is shlex-quoted, so $ and backticks survive copy-paste."""
    def test_live_command_round_trips_through_shlex(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        rr = os.path.join(self.tmp, "runs $HOME `id` it's")
        os.makedirs(rr)
        argv = ("run", d, "--workspace", self.ws, "--live", "--dry-run", "--budget-usd", "4.5", "--engines", "claude",
                "--runs", "1", "--run-root", rr)
        rc, out, err = self.cli(*argv)
        live = next(l for l in out.splitlines() if l.startswith("live command: "))[len("live command: "):]
        want = ["python3", "skill_eval.py"] + [x for x in argv if x != "--dry-run"]
        self.assertEqual(shlex.split(live), want)
        self.assertIn(shlex.quote(rr), live)
        self.assertNotIn('"%s"' % rr, live)  # not JSON quoting

class TestDocs(unittest.TestCase):
    def test_readme_documents_round7(self):
        readme = read(os.path.join(T.ROOT, "README.md"))
        for s in ("Round 7", "builder output is not a JSON object", "shlex.join", "Round 7부터 `env`의 `CLAUDE_CODE_OAUTH_TOKEN`도 거부"):
            self.assertIn(s, readme, s)
        self.assertNotIn("`CLAUDE_CODE_OAUTH_TOKEN`은 검사하지 않음", readme)

if __name__ == "__main__":
    unittest.main()
