"""Round-5 regression tests (Codex round-4 review codex-r4.md) for skill_eval.py.
Everything is mocked: subprocess is patched to fail for the whole module; `claude auth status`, the CLIs, the grader
and the builder are fakes; the Codex auth.json, settings, managed and MDM files are fake files under a temp folder."""
import json, os, plistlib, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import (se, evals_doc, make_skill, claude_stream, FakeClaude, FakeCodex, FakeGraderExec, SKILL_MD,
                             quiet, read, write, wjson, rjson)
from test_round3_isolation import Base

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

SECRET = "sk-ROUND5-MUST-NEVER-APPEAR"
EVAL1 = ("claude", "eval-1-start-the-login-fix-task", "with_skill", "run-1")

def plan_of(ws, n):
    return rjson(os.path.join(ws, "iteration-%d" % n, "plan.json"))

def has_iso_args(cmd, **form):
    iso = se.claude_isolation_args(**form)
    return any(cmd[i:i + len(iso)] == iso for i in range(len(cmd)))

def grade_once(g):
    return g.grade({"prompt": "p", "expected_output": "e"}, {"final": "feat/x"}, [{"id": "1.0", "text": "t"}])

class TestOneClaudeLaunchPath(Base):
    """P1: grader and builder used ordinary settings and inherited provider variables."""
    def test_executor_grader_and_builder_share_isolation_args_and_env(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        seen = []
        rec = lambda out: (lambda cmd, cwd, timeout, env=None: seen.append((cmd, cwd, env)) or (0, out, ""))
        g = se.LLMGrader("claude-sonnet-5-5", exec_fn=lambda cmd, cwd, timeout, env=None:
                         seen.append((cmd, cwd, env)) or FakeGraderExec()(cmd, cwd, timeout, env))
        grade_once(g)
        it = os.path.join(self.tmp, "it"); os.makedirs(it)
        se.ClaudeBuilder("fix", "claude-opus-5-5", se.Budget(10.0), exec_fn=rec(json.dumps({"type": "result", "usage": {}})))(it, spec["skill_dir"])
        calls = [(c, w, e) for c, w, e in [(c, w, self.fc.envs[i]) for i, (c, w) in enumerate(self.fc.calls)]] + seen
        self.assertEqual(len(calls), 4 + 20 + 2)  # 2 cases x (with_skill, without_skill), 20 trigger runs, grader, builder
        root = os.path.realpath(spec["skill_dir"])
        for cmd, cwd, env in calls:
            form = {"edit_root": root} if cmd is seen[1][0] else {}
            self.assertTrue(has_iso_args(cmd, **form), cmd[:3])
            self.assertEqual({k: env.get(k) for k in se.CLAUDE_CHILD_ENV}, se.CLAUDE_CHILD_ENV)
            self.assertFalse(set(se.NON_SUBSCRIPTION_ENV) & set(env))
        gcwd = seen[0][1]  # the grader ran in a fresh empty folder (not the caller's cwd), removed afterwards
        self.assertNotEqual(gcwd, os.getcwd()); self.assertFalse(os.path.exists(gcwd))
        self.assertIn("--disable-slash-commands", seen[0][0])
        self.assertEqual(seen[1][1], spec["skill_dir"])
        bcmd = seen[1][0]
        hook = json.loads(bcmd[bcmd.index("--settings") + 1])["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual((os.path.basename(hook["args"][0]), hook["args"][1:], hook["onFailure"]),
                         ("claude_bash_gate.py", ["edit-allowlist", root], "block"))

    def test_launch_without_isolation_args_is_refused(self):
        calls = []
        fx = lambda *a, **k: calls.append(a) or (0, "", "")
        for cmd in (["claude", "-p", "x"], ["sh", "-c", "claude -p x"] + se.claude_isolation_args()):
            with self.assertRaises(se.EvalError): se.launch_claude(fx, cmd, self.tmp, 1)
        self.assertEqual(calls, [])

    def test_provider_routing_is_scrubbed_and_refused(self):
        env = {"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_BASE_URL": "https://proxy", "AWS_BEARER_TOKEN_BEDROCK": SECRET,
               "ANTHROPIC_FOUNDRY_API_KEY": SECRET, "PATH": "/bin"}
        self.assertEqual(se.scrub_env(env), {"PATH": "/bin"})
        with mock.patch.dict(os.environ, env):
            probs = se.api_key_env_problems()
            self.assertEqual(len(probs), 4); self.assertNotIn(SECRET, "\n".join(probs))
            self.assertTrue(any("CLAUDE_CODE_USE_BEDROCK is set" in p and "provider routing" in p for p in probs))
            self.assertTrue(any("AWS_BEARER_TOKEN_BEDROCK is set" in p and "API-key auth" in p for p in probs))
            self.assertFalse(set(env) - {"PATH"} & set(se.claude_child_env()))
            calls, b = [], se.Budget(10.0)
            fx = lambda cmd, cwd, timeout, env=None: calls.append(cmd) or (0, "{}", "")
            g = se.LLMGrader("claude-sonnet-5-5", exec_fn=fx, budget=b)
            with self.assertRaises(se.EvalError) as cm: grade_once(g)
            self.assertIn("claude launch refused", str(cm.exception))
            it = os.path.join(self.tmp, "it"); os.makedirs(it)
            with self.assertRaises(se.EvalError): se.ClaudeBuilder("fix", "claude-opus-5-5", b, exec_fn=fx)(it, self.tmp)
            self.assertEqual(calls, []); self.assertEqual(b.spent, 0.0)  # refused before launch: nothing charged
            d = self.approved_skill(evals=evals_doc(human=False))
            live = []
            with mock.patch.object(se, "live_exec", lambda *a, **k: live.append(a) or (0, "", "")):
                rc, out, err = self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "50", "--engines",
                                        "claude", "--runs", "1", "--run-root", self.runs)
            self.assertEqual(rc, 2); self.assertIn("ANTHROPIC_BASE_URL is set", err); self.assertEqual(live, [])
            self.assertNotIn(SECRET, out + err)

    def test_settings_routing_and_builder_project_settings_are_refused(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        wjson(self.managed, {"env": {"CLAUDE_CODE_USE_VERTEX": "1"}})
        self.assertIn("CLAUDE_CODE_USE_VERTEX", se.preflight(spec, ["claude"], self.runs)[0])
        os.remove(self.managed)
        wjson(os.path.join(spec["skill_dir"], ".claude", "settings.local.json"), {"apiKeyHelper": SECRET})
        calls, b = [], se.Budget(10.0)
        it = os.path.join(self.tmp, "it"); os.makedirs(it)
        with self.assertRaises(se.EvalError) as cm:
            se.ClaudeBuilder("fix", "claude-opus-5-5", b, exec_fn=lambda *a, **k: calls.append(a))(it, spec["skill_dir"])
        self.assertIn("apiKeyHelper", str(cm.exception)); self.assertNotIn(SECRET, str(cm.exception))
        self.assertEqual(calls, []); self.assertEqual(b.spent, 0.0)

class TestEffectiveSettingsSources(Base):
    """P1: preflight checked fixed paths only (no CLAUDE_CONFIG_DIR, managed-settings.d, MDM, server cache)."""
    def setUp(self):
        Base.setUp(self)
        self.spec_, _ = self.spec(evals=evals_doc(human=False))
    def pf(self):
        return se.preflight(self.spec_, ["claude"], self.runs)

    def test_claude_config_dir_is_the_user_settings_source(self):
        cfg = os.path.join(self.tmp, "alt-config")
        wjson(os.path.join(cfg, "settings.json"), {"apiKeyHelper": SECRET})
        self.assertEqual(self.pf(), [])  # not read without CLAUDE_CONFIG_DIR
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            self.assertEqual(se.claude_config_dir(), cfg)
            p = self.pf(); self.assertEqual(len(p), 1); self.assertIn(cfg, p[0]); self.assertNotIn(SECRET, p[0])

    def test_server_managed_cache_is_checked(self):
        cache = os.path.join(self.home, ".claude", "remote-settings.json")
        wjson(cache, {"env": {"ANTHROPIC_API_KEY": SECRET}})
        p = self.pf(); self.assertEqual(len(p), 1); self.assertIn("remote-settings.json", p[0])
        n = se.run_iteration(self.spec_, self.ws, self.engines()[:1], runs=1, log=quiet)  # the run checks it too
        self.assertIn("remote-settings.json", plan_of(self.ws, n)["stopped"]); self.assertEqual(self.fc.calls, [])

    def test_managed_settings_d_drop_ins(self):
        d = os.path.join(os.path.dirname(self.managed), "managed-settings.d"); os.makedirs(d)
        wjson(os.path.join(d, ".hidden.json"), {"apiKeyHelper": "x"}); write(os.path.join(d, "notes.txt"), "{bad")
        self.assertEqual(self.pf(), [])  # hidden and non-.json files are ignored, as Claude Code does
        wjson(os.path.join(d, "20-auth.json"), {"env": {"ANTHROPIC_AUTH_TOKEN": SECRET}})
        p = self.pf(); self.assertEqual(len(p), 1); self.assertIn("20-auth.json", p[0]); self.assertNotIn(SECRET, p[0])
        os.remove(os.path.join(d, "20-auth.json")); write(os.path.join(d, "10-broken.json"), "{not json")
        self.assertIn("not readable JSON", self.pf()[0])
        os.remove(os.path.join(d, "10-broken.json")); os.chmod(d, 0o000)
        try:
            self.assertIn("cannot be listed", self.pf()[0])  # fail closed
        finally:
            os.chmod(d, 0o700)

    def test_mdm_configuration_profile(self):
        os.makedirs(os.path.join(self.mdm, "someone"))
        with open(os.path.join(self.mdm, "someone", se.CLAUDE_MDM_DOMAIN + ".plist"), "wb") as f:
            plistlib.dump({"apiKeyHelper": SECRET}, f)
        p = self.pf(); self.assertEqual(len(p), 1); self.assertIn("com.anthropic.claudecode.plist", p[0])
        self.assertNotIn(SECRET, p[0])
        shutil.rmtree(os.path.join(self.mdm, "someone"))
        with open(os.path.join(self.mdm, se.CLAUDE_MDM_DOMAIN + ".plist"), "wb") as f:
            plistlib.dump({"env": {"ANTHROPIC_BASE_URL": "https://gw"}}, f)
        self.assertIn("ANTHROPIC_BASE_URL", self.pf()[0])
        write(os.path.join(self.mdm, se.CLAUDE_MDM_DOMAIN + ".plist"), "garbage")
        self.assertIn("not readable", self.pf()[0])
        os.remove(os.path.join(self.mdm, se.CLAUDE_MDM_DOMAIN + ".plist"))
        locked = os.path.join(self.mdm, "locked"); os.makedirs(locked); os.chmod(locked, 0o000)
        try:
            self.assertIn("cannot be read", self.pf()[0])
        finally:
            os.chmod(locked, 0o700)

    def test_windows_registry_cannot_be_checked(self):
        with mock.patch.object(se.sys, "platform", "win32"):
            self.assertIn("registry policies", self.pf()[0])
        self.assertEqual(se.preflight(self.spec_, ["codex"], self.runs), [])

class TestSymlinkEscape(Base):
    """P1: copytree / copy2 followed symlinks out of the skill folder into the run."""
    def outside_secret(self):
        p = os.path.join(self.tmp, "outside", "credentials.json"); os.makedirs(os.path.dirname(p))
        write(p, SECRET); return p

    def assert_refused(self, spec, frag):
        n = se.run_iteration(spec, self.ws, self.engines(), runs=1, log=quiet)
        stopped = plan_of(self.ws, n)["stopped"]
        self.assertIn(frag, stopped); self.assertEqual(self.fc.calls, [])
        self.assertEqual(os.listdir(self.runs), [])
        self.assertNotIn(SECRET, json.dumps(plan_of(self.ws, n)))

    def test_skill_symlink_to_outside_file_or_dir(self):
        sec = self.outside_secret()
        for link, target in (("notes.md", sec), ("ref", os.path.dirname(sec)), ("dangling", os.path.join(self.tmp, "gone"))):
            shutil.rmtree(self.ws, ignore_errors=True)
            spec, _ = self.spec(evals=evals_doc(human=False))
            os.symlink(target, os.path.join(spec["skill_dir"], link))
            self.assertEqual(se.escaping_links(spec["skill_dir"]), [link])
            self.assert_refused(spec, "symlinks that leave it")
            os.remove(os.path.join(spec["skill_dir"], link))

    def test_internal_symlinks_are_fine(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        write(os.path.join(spec["skill_dir"], "a.md"), "a"); os.symlink("a.md", os.path.join(spec["skill_dir"], "b.md"))
        os.symlink(os.path.join(self.tmp, "x"), os.path.join(spec["skill_dir"], "evals", "ignored"))  # not copied
        self.assertEqual(se.escaping_links(spec["skill_dir"]), [])
        dst = se.ClaudeCodeEngine().install_skill(os.path.join(self.tmp, "w"), spec["skill_dir"], "branch-setup")
        self.assertEqual(read(os.path.join(dst, "b.md")), "a")

    def test_fixture_symlink_to_outside(self):
        sec = self.outside_secret()
        spec, _ = self.spec(evals=evals_doc(human=False))
        os.symlink(sec, os.path.join(spec["skill_dir"], "data.json"))
        spec["cases"][0]["files"] = ["data.json"]
        self.assert_refused(spec, "fixture 'data.json' resolves outside the skill folder")

    def test_old_skill_is_checked_too(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        old = os.path.join(self.tmp, "old"); shutil.copytree(spec["skill_dir"], old)
        os.symlink(self.outside_secret(), os.path.join(old, "leak.md"))
        with self.assertRaises(se.EvalError): se.CodexEngine().install_skill(os.path.join(self.tmp, "w"), old, "branch-setup")

class TestCredentialCopyRemovedFirst(Base):
    """P1: a failed sandbox deletion left the Codex auth.json copy behind."""
    def sandbox(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        return se.Sandbox(se.CodexEngine(), spec, {"files": []}, None, self.runs)

    def test_auth_copy_removed_even_when_the_tree_is_not(self):
        sb = self.sandbox(); auth = os.path.join(sb.root, "codex_home", "auth.json")
        self.assertTrue(os.path.isfile(auth))
        with mock.patch.object(se.shutil, "rmtree", lambda path, *a, **k: None), \
                mock.patch.object(se.time, "sleep", lambda s: None):
            probs = sb.cleanup()
        self.assertFalse(os.path.lexists(auth)); self.assertTrue(os.path.isdir(sb.root))
        self.assertEqual(len(probs), 1); self.assertIn("could not be removed", probs[0])

    def test_undeletable_copy_is_erased_and_reported(self):
        sb = self.sandbox(); auth = os.path.join(sb.root, "codex_home", "auth.json")
        def no_unlink(p, *a, **k): raise PermissionError(1, "Operation not permitted")
        with mock.patch.object(se.os, "unlink", no_unlink):
            err = se.CodexEngine().remove_credentials(sb.root)
        self.assertIn("could not be deleted", err); self.assertIn("content was erased", err)
        self.assertEqual(os.path.getsize(auth), 0)  # no token left on disk
        self.assertNotIn("FAKE-SECRET-TOKEN", err)
        probs = sb.cleanup()  # the erased copy no longer matches: reported, and the sandbox is gone
        self.assertEqual(len(probs), 1); self.assertIn("changed or removed", probs[0]); self.assertFalse(os.path.exists(sb.root))

    def test_modified_copy_is_still_detected_before_removal(self):
        sb = self.sandbox(); auth = os.path.join(sb.root, "codex_home", "auth.json")
        os.chmod(auth, 0o600); write(auth, "{}")
        probs = sb.cleanup()
        self.assertTrue(any("changed or removed the read-only auth.json" in p for p in probs))
        self.assertFalse(os.path.exists(sb.root))

    def test_failed_sandbox_build_removes_the_copy(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        eng = se.CodexEngine()
        real = eng.sandbox_env
        def env_then_fail(root): real(root); raise RuntimeError("late failure")
        eng.sandbox_env = env_then_fail
        removed = []
        orig = se.CodexEngine.remove_credentials
        with mock.patch.object(se.CodexEngine, "remove_credentials",
                               lambda self_, root: removed.append(os.path.lexists(os.path.join(root, "codex_home", "auth.json")))
                               or orig(self_, root)):
            with self.assertRaises(RuntimeError): se.Sandbox(eng, spec, {"files": []}, None, self.runs)
        self.assertEqual(removed, [True]); self.assertEqual(os.listdir(self.runs), [])

class TestChargeFailedCalls(Base):
    """P1: a call that times out or raises after launch was not charged (runs, grader, builder)."""
    def test_executor_timeout_is_charged_recorded_and_stops(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        calls = []
        def timeout(cmd, cwd, t, env=None): calls.append(cmd); raise subprocess.TimeoutExpired(cmd, t)
        b = se.Budget(10.0)
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=timeout)], runs=1, budget=b, log=quiet)
        self.assertEqual(len(calls), 1); self.assertEqual(b.spent, 5.0)  # the --max-budget-usd cap
        stopped = plan_of(self.ws, n)["stopped"]
        self.assertIn("call failed after launch (TimeoutExpired)", stopped); self.assertIn("charged $5.00 (cap_charged)", stopped)
        rd = os.path.join(self.ws, "iteration-%d" % n, *EVAL1)
        tm = rjson(os.path.join(rd, "timing.json"))
        self.assertEqual((tm["api_equivalent_usd"], tm["api_equivalent_source"]), (5.0, "cap_charged"))
        self.assertIn("TimeoutExpired", rjson(os.path.join(rd, "outputs", "transcript.json"))["error"])
        self.assertEqual(plan_of(self.ws, n)["api_equivalent_usd"], 5.0)
        self.assertEqual(os.listdir(self.runs), [])

    def test_codex_crash_charges_the_worst_case_bound(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        def boom(cmd, cwd, t, env=None): raise OSError("pipe broke")
        b, prices = se.Budget(100.0), {"codex": (2.0, 10.0)}
        n = se.run_iteration(spec, self.ws, [se.CodexEngine(exec_fn=boom)], runs=1, budget=b, prices=prices, log=quiet)
        bound = se.call_bound("codex", None, "exec", prices, se.DEFAULT_TOKENS)
        self.assertAlmostEqual(b.spent, bound); self.assertIn("OSError", plan_of(self.ws, n)["stopped"])

    def test_live_disabled_is_not_charged(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        b = se.Budget(10.0)
        with self.assertRaises(se.LiveDisabled):
            se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5")], runs=1, budget=b, log=quiet)
        self.assertEqual(b.spent, 0.0)

    def test_grader_crash_is_charged_and_recorded(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        def boom(cmd, cwd, t, env=None): raise subprocess.TimeoutExpired(cmd, t)
        b = se.Budget(10.0)
        g = se.LLMGrader("claude-sonnet-5-5", exec_fn=boom, budget=b)
        with self.assertRaises(se.EvalError) as cm: se.grade_iteration(spec, self.ws, n, g, log=quiet)
        self.assertIn("grader call failed after launch (TimeoutExpired)", str(cm.exception))
        self.assertEqual(b.spent, 5.0); self.assertEqual(g.usage["calls"], 1)
        e = rjson(os.path.join(self.ws, "iteration-%d" % n, "cost.json"))["entries"]
        self.assertEqual((e[-1]["command"], e[-1]["calls"], e[-1]["api_equivalent_usd"]), ("grade", 1, 5.0))
        self.assertEqual(os.listdir(self.runs), [])  # grader temp folder removed
        g2 = se.LLMGrader("claude-sonnet-5-5", exec_fn=boom)  # no budget: the worst-case bound is recorded
        with self.assertRaises(se.EvalError): grade_once(g2)
        self.assertAlmostEqual(g2.usage["api_equivalent_usd"],
                               se.call_bound("grader", "claude-sonnet-5-5", "grader", {}, se.DEFAULT_TOKENS))

    def test_builder_crash_is_charged_and_stops_the_loop(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        bp = os.path.join(self.tmp, "b.md"); write(bp, "fix")
        fake, fg = FakeClaude(regress=True), FakeGraderExec()
        def live(cmd, cwd, timeout, env=None):
            if "--json-schema" in cmd: return fg(cmd, cwd, timeout)
            if "--tools" in cmd: raise subprocess.TimeoutExpired(cmd, timeout)
            return fake(cmd, cwd, timeout, env)
        with mock.patch.object(se, "live_exec", live):
            rc, out, err = self.cli("loop", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "200", "--engines",
                                    "claude", "--runs", "1", "--builder-prompt", bp, "--run-root", self.runs)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(rc, 3); self.assertEqual(r["status"], "builder-failed")
        self.assertIn("builder call failed after launch (TimeoutExpired)", r["error"])
        e = [x for x in rjson(os.path.join(self.ws, "iteration-1", "cost.json"))["entries"] if x["command"] == "builder"]
        self.assertEqual(len(e), 1); self.assertGreater(e[0]["api_equivalent_usd"], 0)
        self.assertEqual(se.iterations(self.ws), [1])

    def test_loop_stops_after_a_stopped_run(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        def boom(cmd, cwd, t, env=None): raise RuntimeError("x")
        fg, built = FakeGraderExec(), []
        g = se.LLMGrader("claude-sonnet-5-5", exec_fn=fg)
        r = se.loop(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=boom)], g,
                    lambda it, sd: built.append(it) or 0, max_iterations=3, runs=1, log=quiet)
        self.assertEqual(r["status"], "run-stopped"); self.assertIn("RuntimeError", r["error"])
        self.assertEqual(fg.calls, []); self.assertEqual(built, []); self.assertEqual(se.iterations(self.ws), [1])

class TestUsageUnavailableCharge(unittest.TestCase):
    """P2: with no token usage, a small CLI-reported cost overrode the larger cap/bound."""
    def test_max_of_reported_and_fallback(self):
        p = (4.0, 20.0)
        self.assertEqual(se.api_equivalent(0, 0, 0.5, p, 2.0), (2.0, "cap_charged"))
        self.assertEqual(se.api_equivalent(0, 0, 3.0, p, 2.0), (3.0, "cli_reported"))
        self.assertEqual(se.api_equivalent(0, 0, 0.5, p, 0.0), (0.5, "cli_reported"))
        self.assertEqual(se.api_equivalent(1000, 100, 0.5, p, 9.0), (0.5, "cli_reported"))  # usage known: no fallback
        self.assertEqual(se.api_equivalent(1000, 100, 0.0, p, 9.0)[1], "tokens_x_price")

    def test_codex_run_without_usage_charges_bound(self):
        eng = se.CodexEngine()
        res = dict(se.new_result(), cost_usd=0.01)
        self.assertEqual(se.run_cost(res, eng, {"codex": (2.0, 10.0)}, 0.9), (0.9, "cap_charged"))

class TestSkillSourcesBeforeLaunch(Base):
    """P2: the exact-skill check ran after the call and init omits `user-invocable: false` skills."""
    def user_skill(self, rel, invocable=True):
        d = os.path.join(self.home, ".claude", rel); os.makedirs(d)
        write(os.path.join(d, "SKILL.md"), SKILL_MD.replace("name: branch-setup", "name: %s%s" % (
            os.path.basename(rel), "" if invocable else "\nuser-invocable: false")))
        return d

    def test_hidden_from_init_skill_blocks_before_launch(self):
        """Round 6 (Keychain login only, shared config dir): a synced `user-invocable: false` skill, which init would
        not list, is refused before the call; plugin skills are left to the post-run plugins == [] check."""
        spec, _ = self.spec(evals=evals_doc(human=False))
        self.user_skill("skills/synced/abc/quiet-helper", invocable=False)  # would never appear in system/init skills
        pf = se.preflight(spec, ["claude"], self.runs)
        self.assertEqual(len(pf), 1, pf); self.assertIn("quiet-helper", pf[0]); self.assertIn("user-invocable: false", pf[0])
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", pf[0]); self.assertNotIn("setup-token", pf[0])
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        self.assertIn("user-invocable: false", plan_of(self.ws, n)["stopped"])
        self.assertEqual(self.fc.calls, [])  # refused before the paid call
        shutil.rmtree(os.path.join(self.home, ".claude"))
        self.user_skill("skills/synced/abc/loud-helper")  # listed by init if it loads: the post-run check stops it
        self.user_skill("plugins/cache/mp/pl/1.0/skills/quiet-plugin", invocable=False)  # needs an enabled plugin
        self.user_skill("skills/plain-user-skill")  # the documented "user" source: hidden by --setting-sources
        self.assertEqual(se.preflight(spec, ["claude"], self.runs), [])
        notes = se.claude_shared_skill_notes(se.claude_config_dir())
        self.assertEqual(len(notes), 1); self.assertIn("1 synced and 1 plugin skill(s)", notes[0])
        shutil.rmtree(self.ws, ignore_errors=True)
        fx = lambda cmd, cwd, t, env=None: (0, claude_stream("x", skills=["branch-setup"], plugins=[{"name": "pl"}]), "")
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=fx)], runs=1, log=quiet)
        self.assertIn("plugins loaded", plan_of(self.ws, n)["stopped"])

    def test_runs_keep_the_keychain_config_dir(self):
        """Round 6: no per-run config dir and no token: HOME / CLAUDE_CONFIG_DIR are untouched for the Keychain login."""
        spec, _ = self.spec(evals=evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        self.assertIsNone(plan_of(self.ws, n)["stopped"]); self.assertEqual(len(self.fc.calls), 4 + 20)
        for env in self.fc.envs:
            self.assertEqual(env["HOME"], self.home); self.assertNotIn("CLAUDE_CONFIG_DIR", env)
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        rec = rjson(os.path.join(self.ws, "iteration-%d" % n, *EVAL1[:-1], "run-1", "outputs", "transcript.json"))
        self.assertIsNone(rec["error"])
        self.assertEqual(se.ClaudeCodeEngine().auth_record()["method"], "claude.ai login of the current user session (Keychain)")

    def test_custom_command_files_are_refused(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        write_to = os.path.join(spec["skill_dir"], ".claude", "commands"); os.makedirs(write_to)
        write(os.path.join(write_to, "deploy.md"), "do it")
        spec["cases"][0]["files"] = [".claude/commands/deploy.md"]
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        self.assertIn("custom command files", plan_of(self.ws, n)["stopped"]); self.assertEqual(self.fc.calls, [])

    def test_init_api_key_source_must_be_none(self):
        e = se.ClaudeCodeEngine()
        init = lambda src: {"init": {"skills": ["b"], "plugins": [], "apiKeySource": src}}
        self.assertEqual(e.isolation_problems(init("none"), True, "b", []), [])
        for src, frag in (("apiKeyHelper", "may have used an API key"), ("ANTHROPIC_API_KEY", "may have used an API key"),
                          (None, "cannot verify subscription auth")):
            p = e.isolation_problems(init(src), True, "b", [])
            self.assertEqual(len(p), 1, src); self.assertIn(frag, p[0])
        spec, _ = self.spec(evals=evals_doc(human=False))
        fx = lambda cmd, cwd, t, env=None: (0, claude_stream("feat/x", skills=["branch-setup"], api_key_source="apiKeyHelper"), "")
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=fx)], runs=1, log=quiet)
        self.assertIn("apiKeySource is 'apiKeyHelper'", plan_of(self.ws, n)["stopped"])

class TestDocs(unittest.TestCase):
    def test_docs_document_settings_sources_and_failed_calls(self):
        docs = T.docs_text()
        for s in ("CLAUDE_CODE_USE_BEDROCK", "managed-settings.d", "com.anthropic.claudecode", "remote-settings.json",
                  "CLAUDE_CONFIG_DIR", "apiKeySource", "user-invocable: false", "max(reported, cap)",
                  "call failed after launch", "symlink"):
            self.assertIn(s, docs, s)

if __name__ == "__main__":
    unittest.main()
