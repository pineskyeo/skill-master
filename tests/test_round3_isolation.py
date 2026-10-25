"""Round-3 regression tests (Codex review skill-phase2-codex-E2.md) and per-run isolation tests for skill_eval.py.
Everything is mocked: subprocess is patched to fail for the whole module, no claude/codex CLI or model is called."""
import contextlib, io, json, os, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import (eva, se, evals_doc, make_skill, claude_stream, codex_jsonl, FakeClaude, FakeGraderExec,
                             TRIGGERS, SKILL_MD, quiet, read, write, wjson, rjson)

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

class Base(T.Base):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(list(argv))
        return rc, out.getvalue(), err.getvalue()
    def approved_skill(self, **kw):
        d = make_skill(self.tmp, **kw)
        os.makedirs(self.ws, exist_ok=True); write(os.path.join(self.ws, "requirements.md"), "goal")
        for g in ("requirements", "eval_set"): self.cli("approve", d, "--workspace", self.ws, "--gate", g)
        return d

class TestEvalNamePaths(Base):
    """P1: an eval `name` became a directory path and could escape the workspace."""
    def test_eval_name_must_be_a_safe_component(self):
        for bad in ("x/../../../../target", "..", "a..b", "Upper", "-x", "a/b", "a\\b", "x" * 65, 5):
            ev = evals_doc(); ev["evals"][1]["name"] = bad
            d = make_skill(self.tmp, evals=ev)
            self.assertTrue(any("evals[1].name" in e for e in eva.evals_errors(d, "branch-setup")[1]), bad)
            self.assertTrue(se.load_eval_set(d)[1], bad)
            self.assertEqual(self.cli("validate", d)[0], 1)
        for ok in ("no-trigger", "a.b_c-1", "x" * 64):
            ev = evals_doc(); ev["evals"][1]["name"] = ok
            self.assertEqual(eva.evals_errors(make_skill(self.tmp, evals=ev), "branch-setup")[1], [], ok)

    def test_forged_name_never_writes_outside_the_workspace(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        target = os.path.join(self.tmp, "target"); os.makedirs(os.path.join(target, "workdir"))
        write(os.path.join(target, "workdir", "keep.txt"), "precious")
        spec["cases"][0]["name"] = "x/../../../../../../../../.." + target  # bypasses validation on purpose
        engs = self.engines()[:1]
        n = se.run_iteration(spec, self.ws, engs, runs=1, log=quiet)
        self.assertIn("not a safe path component", rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"])
        self.assertEqual(read(os.path.join(target, "workdir", "keep.txt")), "precious")
        self.assertEqual(self.fc.calls, [])
        with self.assertRaises(se.EvalError): se.ensure_inside(self.ws, os.path.join(self.ws, "..", "x"))
        se.ensure_inside(self.ws, os.path.join(self.ws, "a", "b"))

    def test_symlinked_workdir_pointing_outside_is_refused(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        outside = os.path.join(self.tmp, "outside"); os.makedirs(outside); write(os.path.join(outside, "f"), "x")
        rd = os.path.join(self.ws, "iteration-1", "claude", "eval-1-start-the-login-fix-task", "with_skill", "run-1")
        os.makedirs(rd); os.symlink(outside, os.path.join(rd, "workdir"))
        se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, iteration=1, log=quiet)
        self.assertIn("outside", rjson(os.path.join(self.ws, "iteration-1", "plan.json"))["stopped"])
        self.assertEqual(read(os.path.join(outside, "f")), "x")

class TestLoopGates(Base):
    """P1: loop must recheck every live gate before each iteration; P1: no unmetered shell builder."""
    def live_loop(self, d, builder_effect, *extra):
        bp = os.path.join(self.tmp, "builder.md"); write(bp, "Fix the skill.")
        self.builder_calls = []
        fake, fg = FakeClaude(regress=True), FakeGraderExec()
        def fake_live(cmd, cwd, timeout, env=None):
            if "--json-schema" in cmd: return fg(cmd, cwd, timeout)
            if "--tools" in cmd:  # the metered builder
                self.builder_calls.append((cmd, cwd)); builder_effect(d)
                return 0, json.dumps({"type": "result", "total_cost_usd": 0.5, "usage": {}}), ""
            return fake(cmd, cwd, timeout, env)
        with mock.patch.object(se, "live_exec", fake_live):
            rc, out, err = self.cli("loop", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "200", "--engines", "claude",
                                    "--runs", "1", "--builder-prompt", bp, "--run-root", self.runs, *extra)
        return rc, json.loads(out.strip().splitlines()[-1]) if out.strip() else None, err

    def test_loop_stops_when_builder_edits_the_eval_set(self):
        """The eval set is outside the builder's writable set (plan.md:501): the loop stops right after the call with
        builder-wrote-outside and the path (plan.md:502), before the next precheck could see the unapproved eval set."""
        d = self.approved_skill(evals=evals_doc(human=False))
        rc, r, err = self.live_loop(d, lambda d: wjson(os.path.join(d, "evals", "trigger_evals.json"), TRIGGERS[::-1]))
        self.assertEqual(rc, 3, err)
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["evals/trigger_evals.json"]))
        self.assertEqual(se.iterations(self.ws), [1]); self.assertEqual(len(self.builder_calls), 1)
        self.assertEqual(self.builder_calls[0][1], os.path.realpath(d))  # builder runs in the skill folder

    def test_loop_stops_when_builder_edits_a_fixture(self):
        """An eval fixture is outside the builder's writable set (plan.md:501): the loop stops right after the call with
        builder-wrote-outside and the path (plan.md:502), before the next precheck could see the changed fixture."""
        ev = evals_doc(human=False); ev["evals"][0]["files"] = ["evals/files/in.csv"]
        d = self.approved_skill(evals=ev, extra_files={"evals/files/in.csv": "a\n"})
        rc, r, err = self.live_loop(d, lambda d: write(os.path.join(d, "evals", "files", "in.csv"), "b\n"))
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["evals/files/in.csv"]), err)
        self.assertEqual(se.iterations(self.ws), [1]); self.assertEqual(len(self.builder_calls), 1)

    def test_loop_rechecks_preflight(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        def plant(d):  # something installs a same-named user-level copy between iterations
            p = os.path.join(self.home, ".claude", "skills", "branch-setup"); os.makedirs(p, exist_ok=True)
            write(os.path.join(p, "SKILL.md"), SKILL_MD)
        rc, r, err = self.live_loop(d, plant)
        self.assertEqual(r["status"], "gates-failed", err); self.assertIn("preflight", r["problems"][0])

    def test_loop_rechecks_cost_against_remaining_budget(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        left = []
        r = se.loop(spec, self.ws, self.engines(regress=True)[:1], self.grader(), lambda d, s: 0, max_iterations=3, runs=1,
                    log=quiet, precheck=lambda sp, k: left.append(k) or (["estimate $9 exceeds budget $1"] if k == 2 else []))
        self.assertEqual(r, {"status": "gates-failed", "iteration": 1, "problems": ["estimate $9 exceeds budget $1"]})
        self.assertEqual(left, [3, 2])
        # the CLI precheck prices the remaining iterations against the remaining budget
        d = self.approved_skill(evals=evals_doc(human=False))
        a = mock.Mock(cmd="loop", runs=1, baseline="none", grader_model="claude-sonnet-5-5", golden=None, max_iterations=3,
                      builder_prompt=None, run_root=self.runs)
        sp = se.load_eval_set(d)[0]
        engs = [se.ClaudeCodeEngine("claude-opus-5-5")]
        one = se.live_estimate(a, sp, self.ws, None, engs, se.DEFAULT_TOKENS, {}, iterations_left=1)["total_usd"]
        se.check_live_gates(a, sp, [], self.ws, None, engs, se.DEFAULT_TOKENS, {}, one + 0.01, iterations_left=1)
        with self.assertRaises(se.EvalError) as cm:
            se.check_live_gates(a, sp, [], self.ws, None, engs, se.DEFAULT_TOKENS, {}, one + 0.01, iterations_left=2)
        self.assertIn("exceeds budget", str(cm.exception))

    def test_shell_builder_is_rejected(self):
        d = self.approved_skill()
        for live in ([], ["--live", "--budget-usd", "100"]):
            rc, _, err = self.cli("loop", d, "--workspace", self.ws, "--builder", "codex exec 'fix it'", *live)
            self.assertEqual(rc, 2); self.assertIn("--builder (shell command) is not supported", err)
        self.assertEqual(se.iterations(self.ws), [])

    def test_builder_is_metered_and_capped(self):
        b = se.Budget(10.0)
        seen = []
        def fx(cmd, cwd, timeout, env=None):
            seen.append((cmd, cwd)); return 0, json.dumps({"type": "result", "total_cost_usd": 1.25}), ""
        it = os.path.join(self.tmp, "it"); os.makedirs(it); write(os.path.join(it, "comparison.md"), "# report\nregression")
        self.assertEqual(se.ClaudeBuilder("Improve it.", "claude-opus-5-5", b, exec_fn=fx)(it, "/skill"), 0)
        cmd, cwd = seen[0]
        self.assertEqual(cwd, "/skill"); self.assertIn("regression", cmd[2])
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "5.00")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read,Edit,Write,Glob,Grep")
        self.assertNotIn("Bash", " ".join(cmd[3:]))
        self.assertAlmostEqual(b.spent, 5.0)  # round 5: no usage, reported $1.25 < cap $5.00: max(reported, cap)
        with self.assertRaises(se.EvalError):  # round 7: unreadable output is charged the cap and stops
            se.ClaudeBuilder("x", "claude-opus-5-5", b, exec_fn=lambda cmd, cwd, timeout, env=None: (0, "not json", ""))(it, "/s")
        self.assertAlmostEqual(b.spent, 5.0 + 2.5)  # no usage reported: the cap (half of $5.00) is charged
        with self.assertRaises(se.LiveDisabled): se.ClaudeBuilder("x", "claude-opus-5-5", se.Budget(5))(it, "/s")
        with self.assertRaises(se.EvalError): se.ClaudeBuilder("x", "m", se.Budget(0.01), exec_fn=fx)(it, "/s")
        spec, _ = self.spec(evals=evals_doc(human=False))
        def broke(d, s): raise se.EvalError("budget exhausted before builder call")
        r = se.loop(spec, self.ws, self.engines(regress=True)[:1], self.grader(), broke, runs=1, log=quiet)
        self.assertEqual(r["status"], "builder-failed"); self.assertIn("budget exhausted", r["error"])

    def test_builder_calls_are_in_the_estimate(self):
        d = self.approved_skill(); bp = os.path.join(self.tmp, "b.md"); write(bp, "fix")
        rc, out, _ = self.cli("loop", d, "--workspace", self.ws, "--engines", "claude", "--builder-prompt", bp, "--max-iterations", "3")
        self.assertEqual(rc, 0); self.assertIn("DRY RUN", out)
        a = mock.Mock(cmd="loop", runs=1, baseline="none", grader_model="claude-sonnet-5-5", golden=None, max_iterations=3,
                      builder_prompt=bp, builder_model="claude-opus-5-5")
        sp, engs = se.load_eval_set(d)[0], [se.ClaudeCodeEngine("claude-opus-5-5")]
        brow = [r for r in se.live_estimate(a, sp, self.ws, None, engs, se.DEFAULT_TOKENS, {})["rows"] if r["kind"] == "builder"]
        self.assertEqual(brow[0]["calls"], 2)
        self.assertAlmostEqual(brow[0]["usd"], 2 * (80000 * 4 + 8000 * 20) / 1e6)
        last = se.live_estimate(a, sp, self.ws, None, engs, se.DEFAULT_TOKENS, {}, iterations_left=1)
        self.assertEqual([r for r in last["rows"] if r["kind"] == "builder"], [])
        empty = os.path.join(self.tmp, "empty.md"); write(empty, "  ")
        rc, _, err = self.cli("loop", d, "--workspace", self.ws, "--builder-prompt", empty)
        self.assertEqual(rc, 2); self.assertIn("is empty", err)

class TestGateFromGrading(Base):
    """P1: gate from grading.json and expected comparison counts, not the human queue file."""
    def test_queue_marked_done_without_regrade_is_not_clean(self):
        spec, _ = self.spec()
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=1, log=quiet)
        hp = os.path.join(self.ws, "iteration-%d" % n, "human_review.json")
        hr = rjson(hp)
        for x in hr["items"]: x["passed"] = True
        wjson(hp, hr)  # queue says done, grading.json still pending
        _, gate = se.report_iteration(spec, self.ws, n)
        self.assertIn("ungraded: 2 human item(s) pending review (fill human_review.json, re-run grade)", gate["problems"])
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=1, log=quiet)
        self.assertEqual(se.report_iteration(spec, self.ws, n)[1]["problems"], [])

    def test_static_only_dry_grade_is_not_clean(self):
        ev = evals_doc(human=False); ev["evals"] = ev["evals"][1:]  # one case, static items only
        spec, problems = self.spec(evals=ev)
        self.assertEqual(problems, [])
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=2, log=quiet)
        se.grade_iteration(spec, self.ws, n, None, log=quiet)
        _, gate = se.report_iteration(spec, self.ws, n)
        self.assertIn("incomplete [claude]: 0 of 2 blind pairwise comparisons (run `grade --live`)", gate["problems"])
        self.assertTrue(gate["needs_approval"]); self.assertFalse(gate["human_pending_only"])
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=1, log=quiet)
        self.assertEqual(se.report_iteration(spec, self.ws, n)[1]["problems"], [])

    def test_unparseable_comparison_does_not_count(self):
        ev = evals_doc(human=False); ev["evals"] = ev["evals"][1:]
        spec, _ = self.spec(evals=ev)
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        se.grade_iteration(spec, self.ws, n, None, log=quiet)
        wjson(os.path.join(self.ws, "iteration-1", "claude", "eval-2-no-trigger", "comparison-1.json"), {"winner": None})
        self.assertIn("incomplete [claude]: 0 of 1 blind pairwise comparisons (run `grade --live`)",
                      se.report_iteration(spec, self.ws, n)[1]["problems"])

class TestRegisterBinding(Base):
    """P2: register binds to the tested skill folder hash."""
    def test_register_refuses_when_skill_changed(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=1, log=quiet)
        self.assertEqual(se.report_iteration(spec, self.ws, n)[1]["problems"], [])
        plan = rjson(os.path.join(self.ws, "iteration-1", "plan.json"))
        self.assertEqual(plan["skill_sha256"], se.skill_tree_hash(spec["skill_dir"]))
        os.makedirs(os.path.join(spec["skill_dir"], "scripts"))
        write(os.path.join(spec["skill_dir"], "scripts", "new.py"), "print(1)")  # not SKILL.md: the whole folder counts
        with self.assertRaises(se.EvalError) as cm: se.register(spec, self.ws)
        self.assertIn("re-run the eval", str(cm.exception))
        self.assertIn("stale: the skill changed after this iteration ran (re-run before trusting this report)",
                      se.report_iteration(spec, self.ws, n)[1]["problems"])
        shutil.rmtree(os.path.join(spec["skill_dir"], "scripts"))
        se.report_iteration(spec, self.ws, n)
        rec = se.register(spec, self.ws)
        self.assertEqual(rec["skill_sha256"], plan["skill_sha256"])
        g = rjson(os.path.join(self.ws, "iteration-1", "gate.json")); g["needs_approval"] = True
        wjson(os.path.join(self.ws, "iteration-1", "gate.json"), g)  # edited gate.json no longer matches the benchmark
        with self.assertRaises(se.EvalError): se.register(spec, self.ws)

    def test_eval_set_change_blocks_register_but_not_skill_hash(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=1, log=quiet)
        se.report_iteration(spec, self.ws, n)
        h = se.skill_tree_hash(spec["skill_dir"])
        wjson(os.path.join(spec["skill_dir"], "evals", "trigger_evals.json"), TRIGGERS[::-1])
        self.assertEqual(se.skill_tree_hash(spec["skill_dir"]), h)  # evals/ is not installed into runs
        with self.assertRaises(se.EvalError) as cm: se.register(spec, self.ws)
        self.assertIn("different eval set", str(cm.exception))
        self.assertIn("unbound", "\n".join(se.compute_gate({"per_engine": {}, "completeness": {}, "metadata": {}})["problems"]))

class TestIsolation(Base):
    def test_claude_env_and_flags_keep_auth(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/cfg/keep"}):
            spec, _ = self.spec(evals=evals_doc(human=False))
            se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        env = self.fc.envs[0]
        self.assertEqual(env["HOME"], self.home)                 # HOME untouched (Keychain login)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/cfg/keep")  # inherited, never overridden
        self.assertEqual(env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1"); self.assertEqual(env["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        for cmd, cwd in self.fc.calls:
            self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "project,local")
            self.assertIn("--strict-mcp-config", cmd)
            st = json.loads(cmd[cmd.index("--settings") + 1])
            self.assertIs(st["disableBundledSkills"], True)
            # round 14: built-in plugins off; round 15: bundled skills (kill-switch survivors included) set off
            self.assertEqual(set(st), {"disableBundledSkills", "enabledPlugins", "skillOverrides"})
            self.assertFalse(any(st["enabledPlugins"].values()))
            self.assertTrue(cwd.startswith(os.path.realpath(self.runs)))
        t = rjson(os.path.join(self.ws, "iteration-1", "claude", "eval-1-start-the-login-fix-task", "with_skill", "run-1",
                               "outputs", "transcript.json"))
        self.assertIsNone(t["error"])
        self.assertEqual(os.listdir(self.runs), [])

    # Codex auth in the sandbox (read-only copy, ChatGPT only): tests/test_subscription_auth.py

    def test_codex_sandbox_discovery_check(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        eng = se.CodexEngine()
        sb = se.Sandbox(eng, spec, {"files": []}, spec["skill_dir"], self.runs)
        self.assertEqual(sb.problems(), [])
        self.assertFalse(os.path.exists(os.path.join(sb.work, ".agents", "skills", "branch-setup", "evals")))
        leak = os.path.join(sb.root, "home", ".agents", "skills", "leak"); os.makedirs(leak)
        write(os.path.join(leak, "SKILL.md"), SKILL_MD)
        sysd = os.path.join(sb.root, "codex_home", "skills", ".system", "sys"); os.makedirs(sysd)
        write(os.path.join(sysd, "SKILL.md"), SKILL_MD)  # bundled system skill: allowed
        self.assertEqual(len(sb.problems()), 1); self.assertIn("leak", sb.problems()[0])
        self.assertEqual(sb.cleanup(), []); self.assertFalse(os.path.exists(sb.root))
        off = se.Sandbox(eng, spec, {"files": []}, None, self.runs)
        os.makedirs(os.path.join(off.work, ".agents", "skills", "branch-setup"))
        write(os.path.join(off.work, ".agents", "skills", "branch-setup", "SKILL.md"), SKILL_MD)
        self.assertIn("expected []", off.problems()[0]); off.cleanup()

    def test_sandbox_problem_fails_closed_before_any_call(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        anc = os.path.join(self.tmp, ".claude", "skills", "other"); os.makedirs(anc); write(os.path.join(anc, "SKILL.md"), SKILL_MD)
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        self.assertIn("isolation (claude)", rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"])
        self.assertEqual(self.fc.calls, []); self.assertEqual(os.listdir(self.runs), [])

    def test_claude_init_report_fail_closed(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        user = os.path.join(self.home, ".claude", "skills", "style-lab"); os.makedirs(user)
        write(os.path.join(user, "SKILL.md"), SKILL_MD)
        for out, frag in ((claude_stream("x", skills=["branch-setup", "style-lab"]), "user-level skills visible: ['style-lab']"),
                          (claude_stream("x", skills=["branch-setup"], plugins=[{"name": "p"}]), "plugins loaded"),
                          (claude_stream("x", skills=[]), "with_skill run are [], expected exactly ['branch-setup']"),
                          (json.dumps({"type": "result", "result": "x"}), "no system/init event")):
            shutil.rmtree(self.ws, ignore_errors=True)
            calls = []
            fx = lambda cmd, cwd, timeout, env=None, out=out: calls.append(cmd) or (0, out, "")
            n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=fx)], runs=1, log=quiet)
            self.assertIn(frag, rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"])
            self.assertEqual(len(calls), 1)  # stopped after the first contaminated run
            t = rjson(os.path.join(self.ws, "iteration-1", "claude", "eval-1-start-the-login-fix-task", "with_skill",
                                   "run-1", "outputs", "transcript.json"))
            self.assertIn("isolation:", t["error"])
        e = se.ClaudeCodeEngine()
        init = lambda skills: {"init": {"skills": skills, "plugins": [], "apiKeySource": "none"}}
        self.assertIn("skills loaded in without_skill run", e.isolation_problems(init(["x"]), False, "b", [])[0])
        self.assertEqual(e.isolation_problems(init([]), False, "b", []), [])

    def test_sandbox_removed_when_the_cli_raises(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        def boom(cmd, cwd, timeout, env=None): raise RuntimeError("crash")
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=boom)], runs=1, log=quiet)
        self.assertIn("call failed after launch (RuntimeError)",  # round 5: recorded and charged, not re-raised
                      rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"])
        self.assertEqual(os.listdir(self.runs), [])

    def test_discovery_survives_dangling_and_symlinked_skills(self):
        sk = os.path.join(self.home, ".claude", "skills"); os.makedirs(sk)
        real = os.path.join(self.tmp, "repo", "style-lab"); os.makedirs(real); write(os.path.join(real, "SKILL.md"), SKILL_MD)
        os.symlink(real, os.path.join(sk, "style-lab"))
        os.symlink(os.path.join(self.tmp, "deleted", "dev-setup"), os.path.join(sk, "dev-setup"))  # removed repo
        os.symlink(sk, os.path.join(self.tmp, "repo", "loop"))  # cycle back up
        self.assertEqual(sorted(n for n, _ in se.claude_user_skills(self.home)), ["style-lab"])
        self.assertEqual(se.skills_under(os.path.join(self.tmp, "nope"), 3), [])
        spec, _ = self.spec(evals=evals_doc(human=False))
        self.assertEqual(se.preflight(spec, ["claude", "codex"], self.runs), [])
        self.assertEqual([os.path.basename(u) for u in eva.find_units(sk)], ["style-lab"])
        rc = eva.main(["check-all", os.path.join(self.tmp, "repo")])
        self.assertIn(rc, (0, 1, 3))  # no crash on the cycle

    def test_extra_args_cannot_reopen_isolation(self):
        for eng, args in (("claude", ["--setting-sources", "user,project"]), ("claude", ["--add-dir", "/x"]),
                          ("claude", ["--plugin-dir=/p"]), ("claude", ["--mcp-config", "{}"]), ("codex", ["-C", "/x"]),
                          ("codex", ["--cd=/x"])):
            with self.assertRaises(se.EvalError, msg=args): se.ENGINES[eng](extra_args=args)

class TestDocs(unittest.TestCase):
    def test_readme_documents_isolation_and_round3(self):
        readme = read(os.path.join(T.ROOT, "README.md"))
        for s in ("### Eval isolation", "--setting-sources project,local", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "--builder-prompt",
                  "https://code.claude.com/docs/en/authentication", "https://learn.chatgpt.com/docs/build-skills",
                  "추정", "golden/skills/csv-cleaner"):
            self.assertIn(s, readme)
        for gone in ("pinesky-skills/", "status-brief", "data-correlation", "skills-doctor"):
            self.assertNotIn(gone, readme)

class TestEvaDiscovery(unittest.TestCase):
    def test_check_all_follows_symlinked_skills_and_skips_dangling(self):
        import tempfile
        t = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, t)
        real = os.path.join(t, "real", "s1"); os.makedirs(real); write(os.path.join(real, "SKILL.md"), SKILL_MD)
        root = os.path.join(t, "root"); os.makedirs(os.path.join(root, "a")); write(os.path.join(root, "a", "SKILL.md"), SKILL_MD)
        os.symlink(real, os.path.join(root, "s1")); os.symlink(os.path.join(t, "gone"), os.path.join(root, "dead"))
        os.symlink(real, os.path.join(root, "s1-again"))  # same real folder twice: listed once
        self.assertEqual([os.path.basename(u) for u in eva.find_units(root)], ["a", "s1"])
