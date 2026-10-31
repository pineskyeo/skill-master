"""Round-6 regression tests (user decisions after Codex review codex-r5.md) for skill_eval.py and eva.py.
Everything is mocked: subprocess is patched to fail for the whole module; `claude auth status`, the CLIs, the grader
and the builder are fakes; the Codex auth.json is a fake file under a temp folder."""
import json, os, re, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import (eva, se, evals_doc, make_skill, claude_stream, codex_jsonl, FakeClaude, FakeCodex,
                             FakeGraderExec, quiet, read, write, wjson, rjson)
from test_round3_isolation import Base

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

EVAL1 = ("claude", "eval-1-start-the-login-fix-task", "with_skill", "run-1")
CSV_CLEANER = os.path.join(T.ROOT, "golden", "skills", "csv-cleaner")

def plan_of(ws, n):
    return rjson(os.path.join(ws, "iteration-%d" % n, "plan.json"))

def tree_text(root):
    out = []
    for dp, _, fn in os.walk(root):
        for f in fn:
            with open(os.path.join(dp, f), "rb") as h: out.append(h.read().decode("utf-8", "replace"))
    return "\n".join(out)

class TestCodexAuthCopyRisk(unittest.TestCase):
    """1) The Codex auth.json copy is readable by the agent under test: documented, no "never logged" claim left."""
    def test_no_never_logged_claim_and_risk_is_stated(self):
        src = read(os.path.join(T.ROOT, "skill_eval.py"))
        self.assertNotRegex(src, r"never (be )?(printed or )?logged[^.]*\n?[^.]*copy")
        self.assertNotIn("its content is\n        never printed or logged", src)
        self.assertIn("CAN read it", se.CodexEngine.sandbox_env.__doc__)
        self.assertIn("readable by the agent under test", se.CodexEngine().auth_record()["method"])
        docs = T.docs_text()
        self.assertIn("readable by the agent under test", docs)
        self.assertIn("the default `--strict-budget` refuses every `--live` run that selects Codex", docs)

class TestStrictBudgetDefault(Base):
    """2) --strict-budget is the default: Codex --live runs are refused unless --no-strict-budget (risky)."""
    def test_parser_default_and_opt_out(self):
        d = make_skill(self.tmp, evals=evals_doc(human=False))
        for argv, want in (((), True), (("--strict-budget",), True), (("--no-strict-budget",), False)):
            seen = {}
            with mock.patch.object(se, "_dispatch", lambda a: seen.setdefault("a", a) and 0):
                se.main(["plan", d] + list(argv))
            self.assertIs(seen["a"].strict_budget, want, argv)

    def test_codex_live_refused_by_default_before_any_call(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        bp = os.path.join(self.tmp, "b.md"); write(bp, "fix")
        calls = []
        with mock.patch.object(se, "live_exec", lambda *a, **k: calls.append(a) or (0, "", "")):
            for argv in (("run",), ("loop", "--builder-prompt", bp)):
                for engines in ("codex", "claude,codex"):
                    rc, out, err = self.cli(*(argv + (d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "50",
                                                      "--price", "codex=2,10", "--runs", "1", "--run-root", self.runs,
                                                      "--engines", engines)))
                    self.assertEqual(rc, 2, (argv, engines, err))
                    self.assertIn("--strict-budget (default): codex has no enforceable per-call budget cap", err)
                    self.assertIn("--no-strict-budget is risky and overrides only this gate", err)
        self.assertEqual(calls, []); self.assertEqual(self.status_calls, []); self.assertEqual(se.iterations(self.ws), [])
        self.assertFalse(se._LIVE["on"])

    def test_opt_out_runs_codex_with_both_warnings_and_claude_is_unaffected(self):
        self.allow_codex_under_test()
        d = self.approved_skill(evals=evals_doc(human=False))
        fc, fx = FakeClaude(), FakeCodex()
        live = lambda cmd, cwd, timeout, env=None: (fc if cmd[0] == "claude" else fx)(cmd, cwd, timeout, env)
        with mock.patch.object(se, "live_exec", live):
            rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "50", "--engines", "claude",
                                  "--runs", "1", "--run-root", self.runs)
            self.assertEqual(rc, 0, err); self.assertNotIn("SOFT LIMIT", err)
            rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "50", "--engines", "codex",
                                  "--price", "codex=2,10", "--runs", "1", "--run-root", self.runs, "--no-strict-budget")
        self.assertEqual(rc, 0, err)
        self.assertIn("WARNING: budget SOFT LIMIT: codex", err); self.assertIn("readable by the agent under test", err)
        self.assertTrue(fc.calls); self.assertTrue(fx.calls)

    def test_plan_shows_the_enforcement(self):
        d = make_skill(self.tmp, evals=evals_doc(human=False))
        _, out, _ = self.cli("plan", d, "--engines", "claude,codex", "--price", "codex=2,10")
        self.assertIn("budget enforcement: STRICT (default): --live refuses codex", out)
        _, out, _ = self.cli("plan", d, "--engines", "codex", "--price", "codex=2,10", "--no-strict-budget")
        self.assertIn("budget enforcement: WARNING --no-strict-budget", out)
        _, out, _ = self.cli("plan", d, "--engines", "claude")
        self.assertNotIn("budget enforcement", out)

class TestMalformedUsageIsCharged(Base):
    """3a) A paid call followed by a usage/output parse error is charged max(reported, cap) and stops."""
    def bad_claude(self, cost):
        def fx(cmd, cwd, t, env=None):
            fx.calls.append(cmd)
            out = claude_stream("feat/x", skills=["branch-setup"], cost=cost).replace('"input_tokens": 1000', '"input_tokens": "lots"')
            return 0, out, ""
        fx.calls = []
        return fx

    def test_executor_nonnumeric_usage_charges_cap_and_stops(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        for reported, want, src in ((0.05, 5.0, "cap_charged"), (7.5, 7.5, "cli_reported")):
            shutil.rmtree(self.ws, ignore_errors=True)
            fx, b = self.bad_claude(reported), se.Budget(10.0)  # cap = 0.5 x 10 = $5.00
            n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=fx)], runs=1, budget=b, log=quiet)
            self.assertEqual(len(fx.calls), 1); self.assertAlmostEqual(b.spent, want)
            stopped = plan_of(self.ws, n)["stopped"]
            self.assertIn("output could not be parsed after the call (ValueError)", stopped)
            self.assertIn("charged $%.2f (%s)" % (want, src), stopped)
            tm = rjson(os.path.join(self.ws, "iteration-%d" % n, *EVAL1, "timing.json"))
            self.assertEqual((tm["api_equivalent_usd"], tm["api_equivalent_source"]), (want, src))
            self.assertEqual(os.listdir(self.runs), [])

    def test_codex_malformed_usage_charges_the_bound(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        out = codex_jsonl("x").replace('"output_tokens": 50', '"output_tokens": {"n": 50}')
        b, prices = se.Budget(100.0), {"codex": (2.0, 10.0)}
        n = se.run_iteration(spec, self.ws, [se.CodexEngine(exec_fn=lambda *a, **k: (0, out, ""))], runs=1, budget=b,
                             prices=prices, log=quiet)
        self.assertAlmostEqual(b.spent, se.call_bound("codex", None, "exec", prices, se.DEFAULT_TOKENS))
        self.assertIn("output could not be parsed after the call (TypeError)", plan_of(self.ws, n)["stopped"])

    def test_failure_in_post_call_handling_is_charged(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        eng = se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude())
        b = se.Budget(10.0)
        with mock.patch.object(se.ClaudeCodeEngine, "isolation_problems", side_effect=KeyError("init")):
            n = se.run_iteration(spec, self.ws, [eng], runs=1, budget=b, log=quiet)
        self.assertEqual(b.spent, 5.0)
        self.assertIn("output handling failed after the call (KeyError)", plan_of(self.ws, n)["stopped"])

    def test_grader_malformed_usage_is_charged_and_recorded(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        for usage, reported, want in (({"input_tokens": "x"}, 0.01, 5.0), ([1, 2], 9.0, 9.0)):
            out = json.dumps({"structured_output": {"items": []}, "usage": usage, "total_cost_usd": reported})
            b = se.Budget(10.0)
            g = se.LLMGrader("claude-sonnet-5-5", exec_fn=lambda *a, **k: (0, out, ""), budget=b)
            with self.assertRaises(se.EvalError) as cm: se.grade_iteration(spec, self.ws, n, g, log=quiet)
            self.assertIn("grader usage could not be parsed after the call", str(cm.exception))
            self.assertAlmostEqual(b.spent, want)
            e = rjson(os.path.join(self.ws, "iteration-%d" % n, "cost.json"))["entries"][-1]
            self.assertEqual((e["command"], e["calls"]), ("grade", 1)); self.assertAlmostEqual(e["api_equivalent_usd"], want)

    def test_builder_malformed_usage_is_charged(self):
        it = os.path.join(self.tmp, "it"); os.makedirs(it)
        b = se.Budget(10.0)
        out = json.dumps({"type": "result", "usage": {"output_tokens": "many"}, "total_cost_usd": 0.2})
        bld = se.ClaudeBuilder("fix", "claude-opus-5-5", b, exec_fn=lambda *a, **k: (0, out, ""))
        with self.assertRaises(se.EvalError) as cm: bld(it, self.tmp)
        self.assertIn("builder usage could not be parsed after the call (ValueError); charged $5.00", str(cm.exception))
        self.assertEqual(b.spent, 5.0)
        self.assertEqual(rjson(os.path.join(it, "cost.json"))["entries"][-1]["api_equivalent_usd"], 5.0)

class TestReplacedAuthSymlink(Base):
    """3b) auth.json replaced by a symlink out of the sandbox: the link itself is unlinked, the target is untouched,
    and the sandbox tree is always removed."""
    def sandbox(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        return se.Sandbox(se.CodexEngine(), spec, {"files": []}, None, self.runs)

    def outside(self):
        p = os.path.join(self.tmp, "outside", "keep.json"); os.makedirs(os.path.dirname(p)); write(p, "precious")
        return p

    def test_symlink_to_outside_is_unlinked_not_followed(self):
        sb, target = self.sandbox(), self.outside()
        auth = os.path.join(sb.root, "codex_home", "auth.json")
        os.chmod(auth, 0o600); os.remove(auth); os.symlink(target, auth)  # what an agent could do
        probs = sb.cleanup()
        self.assertEqual(read(target), "precious")  # target neither deleted nor truncated
        self.assertFalse(os.path.lexists(sb.root)); self.assertEqual(os.listdir(self.runs), [])
        self.assertTrue(any("changed or removed" in p for p in probs), probs)
        self.assertFalse(any("could not be" in p for p in probs), probs)

    def test_codex_home_swapped_for_outside_link(self):
        sb, target = self.sandbox(), self.outside()
        ch = os.path.join(sb.root, "codex_home")
        for dp, dn, fn in os.walk(ch):
            os.chmod(dp, 0o700)
        shutil.rmtree(ch); os.symlink(os.path.dirname(target), ch)
        probs = sb.cleanup()
        self.assertEqual(read(target), "precious"); self.assertFalse(os.path.lexists(sb.root))
        self.assertTrue(any("was not followed" in p for p in probs), probs)

    def test_tree_is_removed_even_when_credential_removal_raises(self):
        sb = self.sandbox()
        with mock.patch.object(se.CodexEngine, "remove_credentials", side_effect=RuntimeError("boom")):
            probs = sb.cleanup()
        self.assertFalse(os.path.lexists(sb.root))
        self.assertTrue(any("removing the credential copy" in p and "RuntimeError" in p for p in probs), probs)
        spec, _ = self.spec(evals=evals_doc(human=False))
        eng = se.CodexEngine()
        eng.sandbox_env = mock.Mock(side_effect=OSError("late"))
        with mock.patch.object(se.CodexEngine, "remove_credentials", side_effect=RuntimeError("boom")):
            with self.assertRaises(se.EvalError): se.Sandbox(eng, spec, {"files": []}, None, self.runs)
        self.assertEqual(os.listdir(self.runs), [])

class TestKeychainOnly(Base):
    """5) No CLAUDE_CODE_OAUTH_TOKEN requirement: the existing Keychain login, nothing asked of the user."""
    def test_synced_and_plugin_skills_do_not_require_a_token(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        for rel in ("skills/synced/abc/helper", "plugins/cache/mp/pl/1.0/skills/helper2"):
            d = os.path.join(self.home, ".claude", rel); os.makedirs(d)
            write(os.path.join(d, "SKILL.md"), T.SKILL_MD.replace("name: branch-setup", "name: " + os.path.basename(rel)))
        self.assertEqual(se.preflight(spec, ["claude"], self.runs), [])
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=quiet)
        self.assertIsNone(plan_of(self.ws, n)["stopped"])

    def test_messages_never_ask_for_a_token_or_login(self):
        for doc in ({"loggedIn": False}, {"loggedIn": True, "authMethod": "api_key"}, {}):
            for p in se.claude_auth_problems(lambda cmd, timeout=30: (0, json.dumps(doc), "")):
                self.assertNotIn("setup-token", p); self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", p)
                self.assertNotIn("/login", p)
        src = read(os.path.join(T.ROOT, "skill_eval.py"))
        self.assertNotIn("export CLAUDE_CODE_OAUTH_TOKEN", src); self.assertNotIn("claude_config\")", src)

class TestLiveDryRun(Base):
    """6) `--live --dry-run`: every gate of the exact command, no model call, no auth status CLI, no files."""
    def args(self, d, *extra):
        return ("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--dry-run", "--budget-usd", "4.5", "--engines", "claude",
                "--runs", "1", "--run-root", self.runs) + extra

    def snapshot(self):
        return sorted(os.path.join(dp, f) for dp, _, fn in os.walk(self.tmp) for f in fn)

    def test_dry_run_executes_nothing_and_prints_the_live_command(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        calls, before = [], self.snapshot()
        with mock.patch.object(se, "live_exec", lambda *a, **k: calls.append(a) or (0, "", "")):
            rc, out, err = self.cli(*self.args(d, "--tokens", "trigger=1000,100", "--tokens", "exec=1000,100"))
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(calls, []); self.assertEqual(self.status_calls, []); self.assertFalse(se._LIVE["on"])
        self.assertEqual(self.snapshot(), before)  # no iteration, no plan.json, no sandbox
        self.assertIn("LIVE DRY RUN: nothing was executed", out)
        self.assertIn("live gates that would refuse this command (0):", out)
        self.assertIn(se.AUTH_STATUS_SKIPPED, out)
        live = next(l for l in out.splitlines() if l.startswith("live command: "))
        self.assertIn(" --live ", live); self.assertNotIn("--dry-run", live); self.assertIn("--budget-usd 4.5", live)

    def test_dry_run_lists_every_failing_gate(self):
        d = make_skill(self.tmp, evals=evals_doc(human=False))  # not approved
        rc, out, _ = self.cli("run", d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--dry-run", "--budget-usd", "1",
                              "--engines", "claude,codex", "--runs", "1", "--run-root", self.runs)
        self.assertEqual(rc, 3)
        for frag in ("--strict-budget (default): codex", "gate requirements not approved", "gate eval_set not approved",
                     "no price for", "EXCEEDS"):
            self.assertIn(frag, out, frag)
        self.assertEqual(self.status_calls, []); self.assertFalse(os.path.exists(self.ws))

    def test_dry_run_needs_live(self):
        d = make_skill(self.tmp, evals=evals_doc(human=False))
        rc, _, err = self.cli("run", d, "--workspace", self.ws, "--dry-run", "--engines", "claude")
        self.assertEqual(rc, 2); self.assertIn("add --live", err)

class TestCsvCleanerEvalSet(unittest.TestCase):
    """4) First target: valid eval set, 10 + 10 trigger queries, prompts name the fixture paths they get."""
    def test_valid_and_complete(self):
        spec, problems = se.load_eval_set(CSV_CLEANER)
        self.assertEqual(problems, [])
        self.assertEqual(sum(q["should_trigger"] for q in spec["triggers"]), 10)
        self.assertEqual(sum(not q["should_trigger"] for q in spec["triggers"]), 10)
        r = eva.check_skill(CSV_CLEANER)
        self.assertEqual({c["id"] for c in r.failed}, set())

    def test_prompt_paths_match_fixture_files(self):
        spec, _ = se.load_eval_set(CSV_CLEANER)
        for c in spec["cases"]:
            named = re.findall(r"[\w./-]+\.csv", c["prompt"])
            inputs = [p for p in named if not p.endswith(("_clean.csv",))]
            for p in inputs:
                self.assertIn(p, c["files"], (c["id"], p))
        self.assertIn("evals/files/orders_2025.csv", spec["cases"][0]["prompt"])
        self.assertNotIn("data/orders_2025.csv", spec["cases"][0]["prompt"])

class TestStyleLabHostField(unittest.TestCase):
    """7) disable-model-invocation is on the checker allowlist; style-lab keeps it set."""
    STYLE_LAB = "/Users/pinesky/workspace/style-lab/skills/style-lab/SKILL.md"
    def test_allowlist(self):
        rule = next(r for r in eva.rule_runner.default_bundle().rules if r.id == "frontmatter-fields")
        self.assertIn("disable-model-invocation", rule.node["host_fields"])
        self.assertNotIn("disable-model-invocation", rule.node["fields"])  # host allowance, not the spec

    def test_style_lab_frontmatter_shape_passes_field_checks(self):
        import tempfile
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp)
        d = os.path.join(tmp, "style-lab"); os.makedirs(d)
        write(os.path.join(d, "SKILL.md"), "---\nname: style-lab\ndescription: Dispatches style experiments. Use when "
              "the user runs /style-lab to compare writing arms.\ndisable-model-invocation: true\nallowed-tools: "
              "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/stylelab.py *)\n---\n# Style Lab\n")
        r = eva.check_skill(d)
        st = {c["id"]: c["status"] for c in r.checks}
        self.assertEqual((st["frontmatter-fields"], st["field-types"]), ("pass", "pass"), r.failed)

    @unittest.skipUnless(os.path.isfile(STYLE_LAB), "style-lab is not on this machine")
    def test_real_style_lab_keeps_the_field_and_passes(self):
        text = read(self.STYLE_LAB)
        self.assertRegex(text.split("---")[1], r"(?m)^disable-model-invocation: true$")
        r = eva.check_skill(os.path.dirname(self.STYLE_LAB))
        st = {c["id"]: c["status"] for c in r.checks}
        self.assertEqual((st["frontmatter-fields"], st["field-types"]), ("pass", "pass"))

class TestDocs(unittest.TestCase):
    def test_docs_document_strict_budget_and_keychain(self):
        docs = T.docs_text()
        for s in ("--no-strict-budget", "--dry-run", "Keychain", "user-invocable: false",
                  "readable by the agent under test", "max(reported, cap)"):
            self.assertIn(s, docs, s)
        self.assertNotIn("export CLAUDE_CODE_OAUTH_TOKEN", docs)

if __name__ == "__main__":
    unittest.main()
