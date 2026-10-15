"""Subscription-auth-only eval runs and API-equivalent cost accounting (skill_eval.py).
Everything is mocked: subprocess is patched to fail for the whole module; `claude auth status` is a fake; the Codex
auth.json is a fake file under a temp HOME. No claude/codex CLI or model is called, no real credential is touched."""
import json, os, stat, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, evals_doc, codex_jsonl, FakeGraderExec, quiet, read, write, wjson, rjson
from test_round3_isolation import Base

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

SECRET = "sk-THIS-MUST-NEVER-APPEAR"

def tree_text(root):
    """Every file under root as text (to prove a secret was never written)."""
    out = []
    for dp, _, fn in os.walk(root):
        for f in fn:
            try: out.append(read(os.path.join(dp, f)))
            except (UnicodeDecodeError, OSError): pass
    return "\n".join(out)

class TestApiKeysRefusedAndScrubbed(Base):
    def test_preflight_refuses_each_api_key_variable_without_showing_it(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        self.assertEqual(se.preflight(spec, ["claude", "codex"], self.runs), [])
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
            with mock.patch.dict(os.environ, {k: SECRET}):
                pf = se.preflight(spec, ["claude"], self.runs)
            self.assertEqual(len(pf), 1, k); self.assertIn("%s is set (value not shown)" % k, pf[0])
            self.assertNotIn(SECRET, "\n".join(pf))
        with mock.patch.dict(os.environ, {"CODEX_API_KEY": ""}):  # set but empty still counts as set
            self.assertTrue(se.preflight(spec, ["codex"], self.runs))

    def test_live_run_refused_before_any_call(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        calls = []
        with mock.patch.object(se, "live_exec", lambda *a, **k: calls.append(a) or (0, "", "")), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": SECRET}):
            rc, out, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "50", "--engines", "claude",
                                    "--runs", "1", "--run-root", self.runs)
            rc2, _, err2 = self.cli("grade", d, "--workspace", self.ws, "--live", "--budget-usd", "5")
        self.assertEqual(rc, 2); self.assertIn("ANTHROPIC_API_KEY is set", err); self.assertNotIn(SECRET, out + err)
        self.assertEqual(rc2, 2)  # grade: no iteration yet; the gate order does not matter, nothing ran
        self.assertEqual(calls, []); self.assertEqual(se.iterations(self.ws), [])
        os.makedirs(os.path.join(self.ws, "iteration-1"))
        wjson(os.path.join(self.ws, "iteration-1", "plan.json"), {"engines": {"claude": "claude-opus-5-5"}})
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}):
            rc, _, err = self.cli("grade", d, "--workspace", self.ws, "--live", "--budget-usd", "5")
        self.assertEqual(rc, 2); self.assertIn("subscription auth: OPENAI_API_KEY is set", err); self.assertNotIn(SECRET, err)
        self.assertFalse(se._LIVE["on"])

    def test_child_env_never_carries_api_keys(self):
        keys = {k: SECRET for k in se.API_KEY_ENV}
        with mock.patch.dict(os.environ, dict(keys, CLAUDE_CODE_OAUTH_TOKEN="oat-fake")):
            spec, _ = self.spec(evals=evals_doc(human=False))
            claude_env = se.ClaudeCodeEngine().sandbox_env(self.runs)
            sb = se.Sandbox(se.CodexEngine(), spec, {"files": []}, None, self.runs)
            codex_env = dict(sb.env); rec = sb.record(); sb.cleanup()
            seen = []
            def fake_run(cmd, **kw):
                seen.append(kw["env"]); return mock.Mock(returncode=0, stdout="", stderr="")
            se._LIVE["on"] = True
            try:
                with mock.patch.object(subprocess, "run", fake_run):
                    se.live_exec(["claude", "-p", "x"], self.tmp, 1)                 # grader/builder path (env=None)
                    se.live_exec(["claude", "-p", "x"], self.tmp, 1, env=dict(os.environ))
                    se.run_auth_status(se.CLAUDE_AUTH_STATUS_CMD)
            finally:
                se._LIVE["on"] = False
        for env in [claude_env, codex_env] + seen:
            self.assertFalse(set(se.API_KEY_ENV) & set(env), sorted(set(se.API_KEY_ENV) & set(env)))
        for env in [claude_env, codex_env] + seen:  # round 6: Keychain login only, an exported token is removed too
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertEqual(rec["env_removed"], sorted(se.API_KEY_ENV + ("CLAUDE_CODE_OAUTH_TOKEN",)))
        self.assertNotIn(SECRET, json.dumps(rec)); self.assertNotIn("oat-fake", json.dumps(rec))

class TestClaudeAuthStatus(Base):
    def status(self, rc=0, out=None, raise_=None):
        def fn(cmd, timeout=30):
            self.assertEqual(cmd, ["claude", "auth", "status", "--json"])
            if raise_: raise raise_
            return rc, out if isinstance(out, str) else json.dumps(out), ""
        return se.claude_auth_problems(fn)

    def test_classification(self):
        self.assertEqual(self.status(out={"loggedIn": True, "authMethod": "claude.ai"}), [])
        self.assertEqual(self.status(out={"loggedIn": True, "auth": {"method": "oauth_token"}}), [])  # nested, setup-token
        for bad in ({"loggedIn": True, "authMethod": "api_key"}, {"authMethod": "apiKeyHelper"},
                    {"apiKeySource": "ANTHROPIC_API_KEY", "authMethod": "claude.ai"}, {"authMethod": "console"}):
            p = self.status(out=bad)
            self.assertEqual(len(p), 1, bad); self.assertIn("API-key auth", p[0])
        p = self.status(out={"loggedIn": False, "authMethod": "claude.ai"})[0]
        self.assertIn("reports no login", p); self.assertNotIn("setup-token", p); self.assertNotIn("/login", p)
        self.assertIn("gave no JSON", self.status(out="Logged in with claude.ai")[0])  # text output: fail closed
        self.assertIn("gave no JSON (exit 1)", self.status(rc=1, out={"authMethod": "claude.ai"})[0])
        self.assertIn("could not run (FileNotFoundError)", self.status(raise_=FileNotFoundError())[0])
        p = self.status(out={"loggedIn": True, "authMethod": "bedrock", "authEmail": "me@example.com"})
        self.assertIn("no recognized claude.ai/OAuth auth method", p[0]); self.assertIn("bedrock", p[0])
        self.assertNotIn("me@example.com", p[0])  # an email is never echoed

    def test_live_gate_refuses_api_key_login_before_any_call(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        calls = []
        with mock.patch.dict(se.AUTH_STATUS, {"fn": lambda cmd, timeout=30: (0, json.dumps({"authMethod": "api_key"}), "")}), \
                mock.patch.object(se, "live_exec", lambda *a, **k: calls.append(a) or (0, "", "")):
            rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "50", "--engines", "claude",
                                  "--runs", "1", "--run-root", self.runs)
        self.assertEqual(rc, 2); self.assertIn("subscription auth: claude: auth status reports API-key auth", err)
        self.assertEqual(calls, [])

    def test_status_is_never_run_in_a_dry_run(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        rc, out, _ = self.cli("plan", d, "--workspace", self.ws, "--engines", "claude")
        self.assertEqual(rc, 0); self.assertIn("checked only by a real --live", out)
        self.assertEqual(self.status_calls, [])

    def test_codex_only_run_does_not_need_claude_status(self):
        a = mock.Mock(cmd="run")
        self.assertEqual(se.subscription_problems(a, [se.CodexEngine()]), []); self.assertEqual(self.status_calls, [])
        se.subscription_problems(mock.Mock(cmd="grade"), [])  # the grader is claude
        self.assertEqual(len(self.status_calls), 1)

class TestCodexChatGPTAuth(Base):
    def test_auth_mode_must_be_chatgpt(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        self.assertEqual(se.codex_auth_mode(self.auth), "chatgpt")
        for doc, frag in (({"auth_mode": "apikey", "OPENAI_API_KEY": SECRET}, "auth_mode is 'apikey'"),
                          ({"tokens": {"id_token": SECRET}}, "auth_mode is missing/unreadable")):
            wjson(self.auth, doc)
            pf = se.preflight(spec, ["codex"], self.runs)
            self.assertEqual(len(pf), 1); self.assertIn(frag, pf[0]); self.assertNotIn(SECRET, pf[0])
        write(self.auth, "{not json"); self.assertIsNone(se.codex_auth_mode(self.auth))
        self.assertEqual(se.preflight(spec, ["claude"], self.runs), [])  # claude-only runs do not need Codex auth

    def test_sandbox_gets_a_read_only_copy_removed_after_the_run(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        real_mode, real_text = stat.S_IMODE(os.stat(self.auth).st_mode), read(self.auth)
        seen = []
        def fx(cmd, cwd, timeout, env=None):
            ch = env["CODEX_HOME"]; p = os.path.join(ch, "auth.json")
            seen.append({"home": env["HOME"], "ch": ch, "link": os.path.islink(p), "mode": stat.S_IMODE(os.stat(p).st_mode),
                         "dir_mode": stat.S_IMODE(os.stat(ch).st_mode), "same": read(p) == real_text, "cwd": cwd,
                         "keys": set(se.API_KEY_ENV) & set(env)})
            return 0, codex_jsonl("plain answer"), ""
        n = se.run_iteration(spec, self.ws, [se.CodexEngine(exec_fn=fx)], runs=1, log=quiet)
        s, root = seen[0], os.path.dirname(seen[0]["cwd"])
        self.assertEqual((s["home"], s["ch"]), (os.path.join(root, "home"), os.path.join(root, "codex_home")))
        self.assertFalse(s["link"]); self.assertTrue(s["same"])
        self.assertEqual((s["mode"], s["dir_mode"]), (0o400, 0o700)); self.assertEqual(s["keys"], set())
        self.assertEqual(os.listdir(self.runs), [])                           # sandbox (and the copy) removed
        self.assertEqual((stat.S_IMODE(os.stat(self.auth).st_mode), read(self.auth)), (real_mode, real_text))  # untouched
        self.assertIsNone(rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"])
        self.assertNotIn("FAKE-SECRET-TOKEN", tree_text(self.ws))            # no token in any harness output
        t = rjson(os.path.join(self.ws, "iteration-1", "codex", "eval-1-start-the-login-fix-task", "with_skill", "run-1",
                               "outputs", "transcript.json"))
        self.assertIsNone(t["error"])

    def test_changed_copy_stops_and_is_still_removed(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        before = read(self.auth)
        def fx(cmd, cwd, timeout, env=None):  # simulated token refresh: rename-replace inside the 0700 folder
            p = os.path.join(env["CODEX_HOME"], "auth.json"); os.remove(p); write(p, '{"auth_mode": "chatgpt"}')
            return 0, codex_jsonl("plain"), ""
        n = se.run_iteration(spec, self.ws, [se.CodexEngine(exec_fn=fx)], runs=1, log=quiet)
        self.assertIn("changed or removed the read-only auth.json copy", rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"])
        self.assertEqual(os.listdir(self.runs), []); self.assertEqual(read(self.auth), before)

    def test_sandbox_build_failure_leaves_nothing_behind(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        wjson(self.auth, {"auth_mode": "apikey"})  # login changed after preflight
        fx = mock.Mock()
        n = se.run_iteration(spec, self.ws, [se.CodexEngine(exec_fn=fx)], runs=1, log=quiet)
        self.assertIn("not ChatGPT", rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"])
        fx.assert_not_called(); self.assertEqual(os.listdir(self.runs), [])

    def test_claude_oauth_token_is_removed_and_never_written(self):
        """Round 6: Claude runs use only the existing Keychain login; an exported setup-token is not passed on."""
        tok = "sk-ant-oat01-FAKE-OAUTH-TOKEN"
        spec, _ = self.spec(evals=evals_doc(human=False))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": tok}):
            self.assertEqual(se.preflight(spec, ["claude"], self.runs), [])  # not refused: just not used
            n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        for env in self.fc.envs:
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env); self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertNotIn(tok, tree_text(self.ws))
        iso = rjson(os.path.join(self.ws, "iteration-%d" % n, "claude", "eval-1-start-the-login-fix-task", "with_skill",
                                 "run-1", "outputs", "transcript.json"))
        self.assertIsNone(iso["error"])

class TestApiEquivalentCost(Base):
    def test_cost_recorded_apart_from_spend(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        engs = self.engines()
        b = se.Budget(1000.0)
        n = se.run_iteration(spec, self.ws, engs, runs=1, budget=b, prices={"codex": (2.0, 10.0)}, log=quiet)
        it = os.path.join(self.ws, "iteration-%d" % n)
        tc = rjson(os.path.join(it, "claude", "eval-1-start-the-login-fix-task", "with_skill", "run-1", "timing.json"))
        self.assertEqual((tc["cost_usd"], tc["api_equivalent_usd"], tc["api_equivalent_source"], tc["billing"]),
                         (0.05, 0.05, "cli_reported", "subscription"))
        tx = rjson(os.path.join(it, "codex", "eval-1-start-the-login-fix-task", "with_skill", "run-1", "timing.json"))
        self.assertIsNone(tx["cost_usd"]); self.assertEqual(tx["api_equivalent_source"], "tokens_x_price")
        self.assertAlmostEqual(tx["api_equivalent_usd"], (500 * 2.0 + 60 * 10.0) / 1e6)
        plan = rjson(os.path.join(it, "plan.json"))
        self.assertAlmostEqual(plan["api_equivalent_usd"], round(b.spent, 4)); self.assertNotIn("spent_usd", plan)
        g = self.grader(cost=0.01)
        se.grade_iteration(spec, self.ws, n, g, seed=1, log=quiet)
        entries = rjson(os.path.join(it, "cost.json"))["entries"]
        self.assertEqual([e["command"] for e in entries], ["grade"])
        self.assertAlmostEqual(entries[0]["api_equivalent_usd"], 0.01 * entries[0]["calls"])
        bench, _ = se.report_iteration(spec, self.ws, n)
        c = bench["cost"]
        self.assertEqual((c["billing"], c["budget_basis"], c["actual_spend_usd"]), ("subscription", "api_equivalent", None))
        ae = c["api_equivalent_usd"]
        self.assertAlmostEqual(ae["by_engine"]["claude"]["exec"] + ae["by_engine"]["claude"]["trigger"]
                               + ae["by_engine"]["codex"]["exec"] + ae["by_engine"]["codex"]["trigger"], b.spent, places=4)
        self.assertAlmostEqual(ae["total"], round(b.spent + entries[0]["api_equivalent_usd"], 4), places=4)
        md = read(os.path.join(it, "comparison.md"))
        self.assertIn("## Cost", md); self.assertIn("| actual spend | not metered |", md)
        self.assertIn("API-equivalent cost: $", md); self.assertNotIn("spent:", md)

    def test_calibration_and_builder_costs_are_entries(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        it = os.path.join(self.ws, "iteration-%d" % n)
        gp = os.path.join(self.tmp, "golden.json")
        wjson(gp, {"examples": [{"prompt": "p", "output": "feat/x", "item": "names a branch", "label": True}]})
        se.calibrate_metered(gp, self.grader(cost=0.02), it)
        se.ClaudeBuilder("fix", "claude-opus-5-5", se.Budget(10.0),
                         exec_fn=lambda cmd, cwd, timeout, env=None: (0, json.dumps({"total_cost_usd": 0.3, "usage": {
                             "input_tokens": 50000, "output_tokens": 5000}}), ""))(it, spec["skill_dir"])  # = $0.30
        other = se.cost_summary(it)["api_equivalent_usd"]["other"]
        self.assertEqual(other, {"calibrate": 0.02, "builder": 0.3})

    def test_unknown_usage_is_flagged_and_charged_cap_counts(self):
        it = os.path.join(self.tmp, "it")
        wjson(os.path.join(it, "codex", "eval-1", "with_skill", "run-1", "timing.json"),
              {"api_equivalent_usd": 0.0, "api_equivalent_source": "unknown"})
        wjson(os.path.join(it, "claude", "eval-1", "with_skill", "run-1", "timing.json"),
              {"api_equivalent_usd": 0.06, "api_equivalent_source": "cap_charged"})
        wjson(os.path.join(it, "claude", "eval-1", "with_skill", "run-1", "workdir", "timing.json"), {"api_equivalent_usd": 99})
        c = se.cost_summary(it)
        self.assertEqual(c["runs_without_usage"], 1)
        self.assertEqual(c["api_equivalent_usd"]["total"], 0.06)  # a run's own output files are not counted

    def test_budget_limits_api_equivalent_cost(self):
        spec, _ = self.spec()
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=3, budget=se.Budget(0.12), log=quiet)
        plan = rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))
        self.assertIn("budget exhausted", plan["stopped"])  # $0.05 API-equivalent per run, though nothing is billed
        self.assertAlmostEqual(plan["api_equivalent_usd"], 0.15)
        rc, out, _ = self.cli("estimate", spec["skill_dir"], "--engines", "claude")
        self.assertIn("API-equivalent total $", out)

class TestDocs(unittest.TestCase):
    def test_readme_documents_subscription_auth_and_cost(self):
        readme = read(os.path.join(T.ROOT, "README.md"))
        for s in ("Subscription auth", "CLAUDE_CODE_OAUTH_TOKEN", "claude setup-token", "tmux", "auth_mode",
                  "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_AUTH_TOKEN", "API-equivalent",
                  "### Cost", "cost.json", "0400"):
            self.assertIn(s, readme, s)

if __name__ == "__main__":
    unittest.main()
