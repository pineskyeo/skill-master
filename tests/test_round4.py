"""Round-4 regression tests (Codex round-3 review codex-r3.md) for skill_eval.py.
Everything is mocked: subprocess is patched to fail for the whole module; `claude auth status`, the CLIs and the
grader are fakes; the Codex auth.json is a fake file under a temp HOME. No model or real credential is touched."""
import json, os, shutil, stat, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import (se, evals_doc, make_skill, claude_stream, codex_jsonl, FakeClaude, FakeCodex,
                             FakeGraderExec, SKILL_MD, quiet, read, write, wjson, rjson)
from test_round3_isolation import Base

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

SECRET = "sk-ROUND4-MUST-NEVER-APPEAR"

def plan_of(ws, n):
    return rjson(os.path.join(ws, "iteration-%d" % n, "plan.json"))

class TestFrontmatterSkillName(Base):
    """P1: the SKILL.md frontmatter name became the install folder and could contain '../'."""
    def bad_skill(self, name):
        d = make_skill(self.tmp, evals=evals_doc(human=False))
        write(os.path.join(d, "SKILL.md"), SKILL_MD.replace("name: branch-setup", "name: %s" % name))
        return d

    def test_unsafe_name_is_a_validation_problem(self):
        for bad in ("../../escape", "a/b", "..", "Upper", ".hidden"):
            d = self.bad_skill(bad)
            spec, problems = se.load_eval_set(d)
            self.assertTrue(any(p.startswith("SKILL.md name:") for p in problems), bad)
            self.assertEqual(self.cli("validate", d)[0], 1, bad)
            with self.assertRaises(se.EvalError): se.default_workspace(spec)
        rc, _, err = self.cli("plan", self.bad_skill("../../escape"))  # default workspace would be outside: refused
        self.assertEqual(rc, 2); self.assertIn("not a safe path component", err)
        spec, problems = se.load_eval_set(make_skill(self.tmp, evals=evals_doc(human=False)))
        self.assertFalse([p for p in problems if p.startswith("SKILL.md name:")])

    def test_install_never_writes_outside_the_sandbox(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        spec["skill_name"] = "../../../escape"  # bypasses validation on purpose
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        self.assertIn("not a safe path component", plan_of(self.ws, n)["stopped"])
        self.assertEqual(self.fc.calls, []); self.assertEqual(os.listdir(self.runs), [])
        for base in (self.tmp, os.path.dirname(self.tmp), self.runs):
            self.assertFalse(os.path.exists(os.path.join(base, "escape")), base)
        eng, work = se.ClaudeCodeEngine(), os.path.join(self.tmp, "w")
        with self.assertRaises(se.EvalError): eng.install_skill(work, spec["skill_dir"], "../x")
        self.assertTrue(os.path.isfile(os.path.join(eng.install_skill(work, spec["skill_dir"], "ok"), "SKILL.md")))

class TestClaudeSettingsSources(Base):
    """P1: project/local/ancestor/managed settings can supply an API key after env scrubbing."""
    def test_fixture_settings_local_is_refused_before_the_call(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        wjson(os.path.join(spec["skill_dir"], ".claude", "settings.local.json"), {"env": {"ANTHROPIC_API_KEY": SECRET}})
        spec["cases"][0]["files"] = [".claude/settings.local.json"]
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        stopped = plan_of(self.ws, n)["stopped"]
        self.assertIn("isolation (claude)", stopped); self.assertIn("settings.local.json configures API-key auth", stopped)
        self.assertIn("ANTHROPIC_API_KEY", stopped); self.assertNotIn(SECRET, stopped)
        self.assertEqual(self.fc.calls, []); self.assertEqual(os.listdir(self.runs), [])

    def test_preflight_checks_every_settings_source(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        self.assertEqual(se.preflight(spec, ["claude"], self.runs), [])
        cases = ((os.path.join(self.tmp, ".claude", "settings.local.json"), {"env": {"ANTHROPIC_AUTH_TOKEN": SECRET}},
                  "ANTHROPIC_AUTH_TOKEN"),                                                 # ancestor of run_root
                 (os.path.join(self.tmp, ".claude", "settings.json"), {"apiKeyHelper": "/bin/" + SECRET}, "apiKeyHelper"),
                 (os.path.join(self.home, ".claude", "settings.local.json"), {"apiKeyHelper": SECRET}, "apiKeyHelper"),
                 (self.managed, {"env": {"ANTHROPIC_API_KEY": SECRET}}, "ANTHROPIC_API_KEY"))
        for path, doc, key in cases:
            wjson(path, doc)
            pf = se.preflight(spec, ["claude"], self.runs)
            self.assertEqual(len(pf), 1, (path, pf)); self.assertIn(path, pf[0]); self.assertIn(key, pf[0])
            self.assertNotIn(SECRET, "\n".join(pf))
            self.assertEqual(se.preflight(spec, ["codex"], self.runs), [])  # Claude settings do not block Codex runs
            os.remove(path)
        bad = os.path.join(self.tmp, ".claude", "settings.local.json"); write(bad, "{not json")
        self.assertIn("not readable JSON", se.preflight(spec, ["claude"], self.runs)[0])  # fail closed

    def test_ancestor_settings_also_stop_the_run_itself(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        wjson(os.path.join(self.runs, ".claude", "settings.json"), {"apiKeyHelper": SECRET})
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        self.assertIn("configures API-key auth (apiKeyHelper", plan_of(self.ws, n)["stopped"])
        self.assertEqual(self.fc.calls, [])

    def test_live_run_refused(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        wjson(self.managed, {"apiKeyHelper": SECRET})
        calls = []
        with mock.patch.object(se, "live_exec", lambda *a, **k: calls.append(a) or (0, "", "")):
            rc, out, err = self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "50", "--engines", "claude",
                                    "--runs", "1", "--run-root", self.runs)
        self.assertEqual(rc, 2); self.assertIn("preflight", err); self.assertIn("managed-settings.json", err)
        self.assertNotIn(SECRET, out + err); self.assertEqual(calls, [])

class TestExactSkillSet(Base):
    """P1: extra unrecognized skills or a missing plugins field passed the init check."""
    def test_skill_set_must_be_exact_and_plugins_explicit(self):
        e = se.ClaudeCodeEngine()
        ok = lambda skills, plugins=(): {"init": {"skills": list(skills), "plugins": list(plugins), "apiKeySource": "none"}}
        self.assertEqual(e.isolation_problems(ok(["b"]), True, "b", []), [])
        self.assertEqual(e.isolation_problems(ok([{"name": "b"}]), True, "b", []), [])
        self.assertEqual(e.isolation_problems(ok([]), False, "b", []), [])
        p = e.isolation_problems(ok(["b", "mystery"]), True, "b", [])  # not a known user skill: still a leak
        self.assertEqual(len(p), 1); self.assertIn("expected exactly ['b']", p[0]); self.assertIn("mystery", p[0])
        self.assertIn("user-level skills visible: ['u']", e.isolation_problems(ok(["b", "u"]), True, "b", ["u"])[0])
        self.assertTrue(e.isolation_problems(ok(["plugin:b"]), True, "b", []))  # a namespaced copy is not the test copy
        for init in ({"skills": ["b"], "apiKeySource": "none"}, {"skills": ["b"], "plugins": None, "apiKeySource": "none"},
                     {"skills": ["b"], "plugins": {}, "apiKeySource": "none"}):
            p = e.isolation_problems({"init": init}, True, "b", [])
            self.assertEqual(len(p), 1, init); self.assertIn("no plugins list", p[0])

    def test_run_with_extra_skill_or_no_plugins_field_stops(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        no_plugins = "\n".join(json.dumps(x) for x in (
            {"type": "system", "subtype": "init", "skills": ["branch-setup"]},
            {"type": "result", "subtype": "success", "result": "feat/x", "total_cost_usd": 0.01, "usage": {}}))
        for out, frag in ((claude_stream("x", skills=["branch-setup", "unknown-extra"]), "unknown-extra"),
                          (no_plugins, "no plugins list")):
            shutil.rmtree(self.ws, ignore_errors=True)
            calls = []
            fx = lambda cmd, cwd, timeout, env=None, out=out: calls.append(cmd) or (0, out, "")
            n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=fx)], runs=1, log=quiet)
            self.assertIn(frag, plan_of(self.ws, n)["stopped"]); self.assertEqual(len(calls), 1)

class TestSandboxRemoval(Base):
    """P1: rmtree(ignore_errors=True) could silently leave the Codex auth.json copy behind."""
    def test_read_only_folder_is_retried_and_removed(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        sb = se.Sandbox(se.CodexEngine(), spec, {"files": []}, None, self.runs)
        locked = os.path.join(sb.work, "locked"); os.makedirs(locked); write(os.path.join(locked, "f"), "x")
        os.chmod(locked, 0o500)  # rmtree's first attempt fails with PermissionError
        self.addCleanup(lambda: os.path.isdir(locked) and os.chmod(locked, 0o700))
        with mock.patch.object(se.time, "sleep", lambda s: None):
            self.assertEqual(sb.cleanup(), [])
        self.assertFalse(os.path.exists(sb.root)); self.assertEqual(os.listdir(self.runs), [])

    def test_transient_failure_is_retried(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        sb = se.Sandbox(se.CodexEngine(), spec, {"files": []}, None, self.runs)
        real, n = shutil.rmtree, []
        def flaky(path, *a, **k):
            n.append(path)
            if len(n) == 1: raise OSError(16, "Resource busy")
            return real(path, *a, **k)
        with mock.patch.object(se.shutil, "rmtree", flaky), mock.patch.object(se.time, "sleep", lambda s: None):
            self.assertEqual(sb.cleanup(), [])
        self.assertEqual(len(n), 2); self.assertFalse(os.path.exists(sb.root))

    def test_leftover_sandbox_is_surfaced_and_stops_the_iteration(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        fx = FakeCodex()
        with mock.patch.object(se.shutil, "rmtree", lambda path, *a, **k: None), \
                mock.patch.object(se.time, "sleep", lambda s: None):  # deletion silently does nothing
            n = se.run_iteration(spec, self.ws, [se.CodexEngine(exec_fn=fx)], runs=1, log=quiet)
        left = os.listdir(self.runs)  # the sandbox folder is really left over ...
        self.assertEqual(len(left), 1)  # ... but round 5 removed the credential copy first, on its own
        self.assertFalse(os.path.lexists(os.path.join(self.runs, left[0], "codex_home", "auth.json")))
        stopped = plan_of(self.ws, n)["stopped"]
        self.assertIn("could not be removed after 3 attempts", stopped); self.assertIn("auth.json", stopped)
        self.assertEqual(len(fx.calls), 1)  # the iteration stopped after the first run
        self.assertNotIn("FAKE-SECRET-TOKEN", stopped)

    def test_failure_on_an_exception_path_is_not_masked(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        def boom(cmd, cwd, timeout, env=None): raise RuntimeError("crash")
        with mock.patch.object(se.shutil, "rmtree", lambda path, *a, **k: None), \
                mock.patch.object(se.time, "sleep", lambda s: None):
            n = se.run_iteration(spec, self.ws, [se.CodexEngine(exec_fn=boom)], runs=1, log=quiet)
            wjson(self.auth, {"auth_mode": "apikey"})  # sandbox build fails after the folder exists
            with self.assertRaises(se.EvalError) as cm2:
                se.Sandbox(se.CodexEngine(), spec, {"files": []}, None, self.runs)
        stopped = plan_of(self.ws, n)["stopped"]  # the crash and the leftover sandbox are both reported
        self.assertIn("RuntimeError", stopped); self.assertIn("could not be removed", stopped)
        self.assertIn("not ChatGPT", str(cm2.exception)); self.assertIn("could not be removed", str(cm2.exception))

    def test_remove_tree_refuses_paths_outside_root(self):
        with self.assertRaises(se.EvalError): se.remove_tree(self.runs, self.tmp)
        self.assertTrue(os.path.isdir(self.tmp))

class TestCostPrecedence(Base):
    """P1: a reported total_cost_usd of 0 hid nonzero token usage (runs, grader, builder)."""
    def test_api_equivalent(self):
        p = (4.0, 20.0)
        self.assertEqual(se.api_equivalent(1000, 100, 0.0, p), ((1000 * 4 + 100 * 20) / 1e6, "tokens_x_price"))
        self.assertEqual(se.api_equivalent(1000, 100, 0.5, p), (0.5, "cli_reported"))  # larger report wins
        self.assertEqual(se.api_equivalent(1000, 100, 0.001, p)[1], "tokens_x_price")
        self.assertEqual(se.api_equivalent(0, 0, 0.0, p, 0.7), (0.7, "cap_charged"))  # zero report, no usage: the cap
        self.assertEqual(se.api_equivalent(0, 0, None, p), (0.0, "unknown"))
        for junk in (float("nan"), -1.0, True, "0.3"):
            self.assertEqual(se.api_equivalent(0, 0, junk, p, 0.2), (0.2, "cap_charged"), junk)
        self.assertEqual(se.api_equivalent(10, 10, 0.0, None, 0.3), (0.3, "cap_charged"))  # usage but no price

    def test_run_charges_tokens_when_cli_reports_zero(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        fx = lambda cmd, cwd, timeout, env=None: (0, claude_stream("feat/x", usage=(10000, 1000, 0), cost=0.0,
                                                                    skills=[] if "--disable-slash-commands" in cmd
                                                                    else ["branch-setup"]), "")
        b = se.Budget(100.0)
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=fx)], runs=1, budget=b, log=quiet)
        tm = rjson(os.path.join(self.ws, "iteration-%d" % n, "claude", "eval-1-start-the-login-fix-task", "with_skill",
                                "run-1", "timing.json"))
        self.assertEqual(tm["cost_usd"], 0.0); self.assertEqual(tm["api_equivalent_source"], "tokens_x_price")
        self.assertAlmostEqual(tm["api_equivalent_usd"], (10000 * 4 + 1000 * 20) / 1e6)
        self.assertGreater(b.spent, 0.05)

    def test_grader_and_builder_charge_tokens_when_cli_reports_zero(self):
        b = se.Budget(10.0)
        g = se.LLMGrader("claude-sonnet-5-5", exec_fn=FakeGraderExec(cost=0.0), budget=b)
        g.grade({"prompt": "p", "expected_output": "e"}, {"final": "feat/x"}, [{"id": "1.0", "text": "t"}])
        self.assertAlmostEqual(g.usage["api_equivalent_usd"], (100 * 2 + 10 * 10) / 1e6)
        self.assertAlmostEqual(b.spent, (100 * 2 + 10 * 10) / 1e6)
        spec, _ = self.spec(evals=evals_doc(human=False))
        it = os.path.join(self.tmp, "it"); os.makedirs(it)
        out = json.dumps({"type": "result", "total_cost_usd": 0, "usage": {"input_tokens": 1000, "output_tokens": 100}})
        bb = se.Budget(10.0)
        se.ClaudeBuilder("fix", "claude-opus-5-5", bb, exec_fn=lambda cmd, cwd, timeout, env=None: (0, out, ""))(it, spec["skill_dir"])
        self.assertAlmostEqual(bb.spent, (1000 * 4 + 100 * 20) / 1e6)
        bc = se.Budget(10.0)  # zero report and no usage: the cap ($5.00) is charged
        se.ClaudeBuilder("fix", "claude-opus-5-5", bc,
                         exec_fn=lambda cmd, cwd, timeout, env=None: (0, json.dumps({"type": "result", "total_cost_usd": 0}), ""))(it, spec["skill_dir"])
        self.assertEqual(bc.spent, 5.0)

class TestClaudeAuthStatusStrict(Base):
    """P2: loggedIn missing + an OAuth-like string elsewhere passed."""
    def status(self, out):
        return se.claude_auth_problems(lambda cmd, timeout=30: (0, json.dumps(out), ""))

    def test_logged_in_true_and_recognized_method_required(self):
        self.assertEqual(self.status({"loggedIn": True, "authMethod": "claude.ai"}), [])
        self.assertEqual(self.status({"loggedIn": True, "auth": {"method": "oauth_token"}}), [])
        for doc in ({"authMethod": "claude.ai"}, {"loggedIn": "true", "authMethod": "claude.ai"},
                    {"loggedIn": 1, "authMethod": "claude.ai"}, {"authMethod": "bedrock", "provider": "oauth"}):
            p = self.status(doc)
            self.assertEqual(len(p), 1, doc); self.assertIn("no `loggedIn: true`", p[0])
        for doc in ({"loggedIn": True, "authMethod": "bedrock", "provider": "oauth-proxy"},
                    {"loggedIn": True, "note": "oauth"},                       # no method-like key at all
                    {"loggedIn": True, "source": "subscription"},              # not a method key
                    {"loggedIn": True, "authMethod": "claude.ai", "loginMethod": "sso-gateway"},  # one unrecognized
                    {"loggedIn": True, "authMethod": "my-oauth-thing"}):        # substring is not enough
            p = self.status(doc)
            self.assertEqual(len(p), 1, doc); self.assertIn("no recognized claude.ai/OAuth auth method", p[0])
        self.assertIn("API-key auth", self.status({"loggedIn": True, "authMethod": "api_key"})[0])

class TestCodexSoftBudget(Base):
    """P2: Codex's worst-case bound is not a hard cap: labeled soft, and --strict-budget refuses Codex live runs."""
    def test_estimate_and_plan_label_codex_soft(self):
        d = make_skill(self.tmp, evals=evals_doc(human=False))
        rc, out, _ = self.cli("estimate", d, "--engines", "claude,codex", "--price", "codex=2,10")
        self.assertEqual(rc, 0); self.assertIn("budget: SOFT LIMIT: codex has no per-call budget cap", out)
        self.assertNotIn("SOFT LIMIT: claude", out)
        rc, out, _ = self.cli("estimate", d, "--engines", "claude")
        self.assertNotIn("SOFT LIMIT", out)
        rc, out, _ = self.cli("plan", d, "--engines", "codex", "--price", "codex=2,10")
        self.assertIn("SOFT LIMIT: codex", out); self.assertIn("--no-strict-budget", out)
        self.assertIn("budget enforcement: STRICT (default): --live refuses codex", out)  # round 6: strict by default
        self.assertEqual((se.budget_enforcement(se.CodexEngine), se.budget_enforcement(se.ClaudeCodeEngine)),
                         ("soft_limit", "per_call_cap"))

    def test_strict_budget_refuses_codex_live_runs_before_any_call(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        bp = os.path.join(self.tmp, "b.md"); write(bp, "fix")
        calls = []
        with mock.patch.object(se, "live_exec", lambda *a, **k: calls.append(a) or (0, "", "")):
            for argv in (("run",), ("loop", "--builder-prompt", bp)):
                for engines in ("codex", "claude,codex"):
                    rc, out, err = self.cli(*(argv + (d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "50",
                                                      "--engines", engines, "--price", "codex=2,10", "--runs", "1",
                                                      "--run-root", self.runs, "--strict-budget")))
                    self.assertEqual(rc, 2, (argv, engines, out, err))
                    self.assertIn("--strict-budget (default): codex has no enforceable per-call budget cap", err)
        self.assertEqual(calls, []); self.assertEqual(se.iterations(self.ws), []); self.assertFalse(se._LIVE["on"])

    def test_strict_budget_allows_claude_and_default_mode_warns_for_codex(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        fc, fx = FakeClaude(), FakeCodex()
        live = lambda cmd, cwd, timeout, env=None: (fc if cmd[0] == "claude" else fx)(cmd, cwd, timeout, env)
        with mock.patch.object(se, "live_exec", live):
            rc, out, err = self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "50", "--engines", "claude",
                                    "--runs", "1", "--run-root", self.runs, "--strict-budget")
            self.assertEqual(rc, 0, err); self.assertNotIn("SOFT LIMIT", err)
            rc, out, err = self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "50", "--engines", "codex",
                                    "--price", "codex=2,10", "--runs", "1", "--run-root", self.runs)
        self.assertEqual(rc, 2, err); self.assertIn("--strict-budget (default)", err)  # round 6: refused by default
        with mock.patch.object(se, "live_exec", live):
            rc, out, err = self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "50", "--engines", "codex",
                                    "--price", "codex=2,10", "--runs", "1", "--run-root", self.runs, "--no-strict-budget")
        self.assertEqual(rc, 0, err); self.assertIn("WARNING: budget SOFT LIMIT: codex", err)
        self.assertIn("readable by the agent under test", err)
        self.assertEqual(plan_of(self.ws, 1)["budget_enforcement"], {"claude": "per_call_cap"})
        self.assertEqual(plan_of(self.ws, 2)["budget_enforcement"], {"codex": "soft_limit"})
        self.assertTrue(fc.calls); self.assertTrue(fx.calls)

class TestDocs(unittest.TestCase):
    def test_readme_documents_round4(self):
        readme = read(os.path.join(T.ROOT, "README.md"))
        for s in ("--strict-budget", "soft limit", "settings.local.json", "managed-settings.json", "loggedIn: true",
                  "plugins", "tokens_x_price"):
            self.assertIn(s, readme, s)

if __name__ == "__main__":
    unittest.main()
