"""B4: `skill_eval.py agent loop` improves an AGENT.md v2 agent with a builder that may change only AGENT.md. The hook
cases run the exact command the agent builder's --settings object names, as a subprocess with the payload on stdin.
Builders, engines and graders are fakes (plain callables, or AgentBuilder and the engines with a fake exec_fn); no
claude, codex, opencode or hermes CLI is started. The ruleset root is a temp copy of the bundle, and approvals are
recorded by the test approver of tests/test_skill_eval.py. This file stays ASCII: the Korean input is built from
escapes."""
import contextlib, io, json, os, re, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva, skill_eval as se
import test_skill_eval as T
import test_agent_eval as AE
from engine import generate, writable

ROOT = T.ROOT
GATE = os.path.join(ROOT, "claude_bash_gate.py")
NAME = AE.NAME
KOREAN = "".join(map(chr, (0xD55C, 0xAD6D, 0xC5B4, 0x20, 0xBB38, 0xC7A5, 0xC785, 0xB2C8, 0xB2E4, 0x2E)))
SCENARIO = "scenarios/01-overview.json"
CODEX_WRAPPER = "wrappers/codex/%s.toml" % NAME
NAMING = "@ruleset/rules/naming.rules.json"
GATE_IDS = ("writable-set", "regenerate", "english", "parity", "rules-unchanged", "rule-review", "writable-set-again",
            "builder-failed", "agent-reload")
BUILDER_RESULT = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.05,
                             "usage": {"input_tokens": 10, "output_tokens": 5}})
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden)
        pt.start()
        _patches.append(pt)


def tearDownModule():
    for pt in _patches:
        pt.stop()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def append(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def describe(agent_dir, word):
    p = os.path.join(agent_dir, "AGENT.md")
    write(p, re.sub(r"asks for a (?:[a-z0-9]+ )?code review\.", "asks for a %s code review." % word, read(p), 1))


def tree(root):
    out = {}
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            out[os.path.relpath(p, root)] = "link" if os.path.islink(p) else "dir" if os.path.isdir(p) else read(p)
    return out


def check_clean(agent_dir):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = generate.cli(agent_dir, True)
    return rc, out.getvalue()


def copy_bundle(dst):
    for d in ("rules", "schemas", "targets", "policy"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dst, d), ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def hook_entry(agent_dir):
    cmd = se.AgentBuilder("x", "claude-opus-5-5", None).command("p", None, agent_dir)
    st = json.loads(cmd[cmd.index("--settings") + 1])
    (entry,) = st["hooks"]["PreToolUse"]
    (hook,) = entry["hooks"]
    return st, entry, hook


class Base(T.Base):
    def setUp(self):
        T.Base.setUp(self)
        self.root = copy_bundle(os.path.join(self.tmp, "bundle"))
        pt = mock.patch.object(se, "RULESET_ROOT", self.root)
        pt.start()
        self.addCleanup(pt.stop)
        self.agent = AE.generated_copy(os.path.join(self.tmp, "agents"))
        self.ws = os.path.join(self.tmp, "agent-ws")
        self.lines = []

    def path(self, rel):
        return os.path.join(self.agent, *rel.split("/"))

    def loop(self, builder, max_iterations=3, grader=None, fake=None, **kw):
        self.fake = fake or AE.FakeAgentExec()
        engines = [se.AgentClaudeEngine(exec_fn=self.fake)]
        return se.agent_loop(se.Agent(self.agent), self.ws, engines, grader, builder, max_iterations=max_iterations,
                             runs=1, log=self.lines.append, **kw)


class Calls(object):
    def __init__(self, edit=None, rc=0, raise_=None):
        self.edit, self.rc, self.raise_, self.calls = edit, rc, raise_, []

    def __call__(self, it_dir, agent_dir):
        self.calls.append((it_dir, agent_dir))
        if self.edit:
            self.edit(agent_dir, len(self.calls))
        if self.raise_ is not None:
            raise self.raise_
        return self.rc


def allowed(agent_dir, k):
    describe(agent_dir, "careful%d" % k)


class TestAllowedEdit(Base):
    def test_description_edit_regenerates_the_wrappers_and_the_loop_goes_on(self):
        builder = Calls(allowed)
        seen = []
        real = se.agent_run_iteration

        def spy(agent, *a, **k):
            seen.append((read(agent.wrapper("claude")), check_clean(agent.dir)[0]))
            return real(agent, *a, **k)
        with mock.patch.object(se, "agent_run_iteration", spy):
            r = self.loop(builder, max_iterations=3)
        self.assertEqual(r, {"status": "needs-approval", "iteration": 3})
        self.assertEqual(len(builder.calls), 2)
        self.assertEqual(se.iterations(self.ws), [1, 2, 3])
        self.assertEqual([c for _, c in seen], [0, 0, 0])
        self.assertIn("careful1 code review", seen[1][0])
        self.assertIn("careful2 code review", seen[2][0])
        self.assertEqual(check_clean(self.agent)[0], 0)
        self.assertFalse([l for l in self.lines if "builder-wrote-outside" in str(l)])

    def test_the_builder_runs_in_the_agent_folder_with_the_iteration_folder(self):
        builder = Calls(allowed)
        self.loop(builder, max_iterations=2)
        self.assertEqual(builder.calls, [(os.path.join(self.ws, "iteration-1"), os.path.realpath(self.agent))])


class TestOutsideWrites(Base):
    def test_scenario_edit_stops_the_loop(self):
        builder = Calls(lambda d, k: append(os.path.join(d, *SCENARIO.split("/")), "\n"))
        r = self.loop(builder)
        self.assertEqual(r, {"status": "builder-wrote-outside", "iteration": 1, "problems": [SCENARIO], "returncode": 0})
        self.assertIn("builder-wrote-outside " + SCENARIO, self.lines)
        self.assertEqual(len(builder.calls), 1)
        self.assertEqual(se.iterations(self.ws), [1])

    def test_wrapper_edit_stops_the_loop_and_keeps_the_edited_bytes(self):
        builder = Calls(lambda d, k: append(os.path.join(d, *CODEX_WRAPPER.split("/")), "# extra\n"))
        r = self.loop(builder)
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", [CODEX_WRAPPER]))
        self.assertTrue(read(self.path(CODEX_WRAPPER)).endswith("# extra\n"))
        self.assertEqual(check_clean(self.agent)[0], 1)

    def test_wrapper_edit_is_reported_before_anything_regenerates(self):
        def edit(d, k):
            describe(d, "careful")
            append(os.path.join(d, *CODEX_WRAPPER.split("/")), "# extra\n")
        with mock.patch.object(se, "agent_regenerate", wraps=se.agent_regenerate) as regen:
            r = self.loop(Calls(edit))
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", [CODEX_WRAPPER]))
        self.assertEqual(regen.call_count, 0)
        self.assertNotIn("careful code review", read(self.path("wrappers/claude/%s.md" % NAME)))

    def test_rules_edit_stops_the_loop(self):
        builder = Calls(lambda d, k: append(os.path.join(self.root, "rules", "naming.rules.json"), " "))
        r = self.loop(builder)
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", [NAMING]))
        self.assertIn("builder-wrote-outside " + NAMING, self.lines)

    def test_rules_edit_through_a_link_the_builder_creates_stops_the_loop(self):
        def edit(d, k):
            os.symlink(os.path.join(self.root, "rules", "naming.rules.json"), os.path.join(d, "notes.json"))
            append(os.path.join(d, "notes.json"), " ")
        r = self.loop(Calls(edit))
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", [NAMING, "notes.json"]))
        self.assertIn("builder-wrote-outside " + NAMING, self.lines)

    def test_policy_and_workspace_writes_count(self):
        def edit(d, k):
            append(os.path.join(self.root, "policy", "gates.json"), " ")
            write(os.path.join(self.ws, "approvals.json"), "{}\n")
        r = self.loop(Calls(edit))
        self.assertEqual((r["status"], r["problems"]),
                         ("builder-wrote-outside", ["../../agent-ws/approvals.json", "@ruleset/policy/gates.json"]))

    def test_the_fingerprint_names_the_bundle_and_never_follows_a_link(self):
        os.symlink(os.path.join(self.root, "rules"), self.path("rules-link"))
        fp = se.agent_fingerprint(self.agent, self.ws)
        self.assertEqual(fp["rules-link"], "link:" + os.path.join(self.root, "rules"))
        self.assertFalse([n for n in fp if n.startswith("rules-link/")])
        self.assertTrue(fp[NAMING].startswith("sha256:"))
        self.assertEqual(fp["@ruleset/rules"], "dir")
        self.assertIn("@ruleset/policy/eval-policy.json", fp)
        self.assertIn(SCENARIO, fp)
        self.assertNotIn("AGENT.md", fp)

    def test_agent_md_replaced_by_a_hard_link_stops_the_loop(self):
        outside = os.path.join(self.tmp, "elsewhere", "kept.md")
        write(outside, read(self.path("AGENT.md")))

        def edit(d, k):
            os.remove(os.path.join(d, "AGENT.md"))
            os.link(outside, os.path.join(d, "AGENT.md"))
        builder = Calls(edit)
        r = self.loop(builder)
        self.assertEqual(r, {"status": "builder-wrote-outside", "iteration": 1, "problems": ["AGENT.md"], "returncode": 0})
        self.assertIn("builder-wrote-outside AGENT.md", self.lines)
        self.assertEqual(len(builder.calls), 1)
        self.assertEqual(se.agent_folder_problems(self.agent), [se.AGENT_NOT_REGULAR])

    def test_agent_md_replaced_by_a_symlink_stops_the_loop_as_outside(self):
        outside = os.path.join(self.tmp, "elsewhere", "linked.md")
        write(outside, read(self.path("AGENT.md")))

        def edit(d, k):
            os.remove(os.path.join(d, "AGENT.md"))
            os.symlink(outside, os.path.join(d, "AGENT.md"))
        r = self.loop(Calls(edit))
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["AGENT.md"]))

    def test_old_agent_edit_stops_the_loop(self):
        old_dir = AE.generated_copy(os.path.join(self.tmp, "old-agents"))

        def edit(d, k):
            describe(d, "careful%d" % k)
            describe(old_dir, "tampered%d" % k)
        r = self.loop(Calls(edit), max_iterations=2, old=se.Agent(old_dir))
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["@old-agent/AGENT.md"]))
        self.assertIn("builder-wrote-outside @old-agent/AGENT.md", self.lines)

    def test_the_fingerprint_names_every_old_agent_entry(self):
        old_dir = AE.generated_copy(os.path.join(self.tmp, "old-agents"))
        fp = se.agent_fingerprint(self.agent, self.ws, old_dir)
        self.assertTrue(fp["@old-agent/AGENT.md"].startswith("sha256:"))
        self.assertIn("@old-agent/" + SCENARIO, fp)
        self.assertIn("@old-agent/" + CODEX_WRAPPER, fp)
        self.assertEqual(fp["@old-agent/scenarios"], "dir")
        self.assertNotIn("AGENT.md", fp)


class TestKoreanEdit(Base):
    def test_korean_in_agent_md_stops_the_loop(self):
        builder = Calls(lambda d, k: append(os.path.join(d, "AGENT.md"), KOREAN + "\n"))
        r = self.loop(builder)
        self.assertEqual(r["status"], "builder-non-english")
        self.assertTrue(any("AGENT.md" in p for p in r["problems"]), r["problems"])
        self.assertEqual(len(builder.calls), 1)
        self.assertEqual(se.iterations(self.ws), [1])

    def test_english_runs_before_the_rules_gate(self):
        moving = iter("%064d" % i for i in range(100))
        with mock.patch.object(se, "ruleset_sha256", lambda: next(moving)):
            r = self.loop(Calls(lambda d, k: append(os.path.join(d, "AGENT.md"), KOREAN + "\n")))
        self.assertEqual(r["status"], "builder-non-english")
        shutil.rmtree(self.ws)
        shutil.rmtree(self.agent)
        self.agent = AE.generated_copy(os.path.join(self.tmp, "agents"))
        with mock.patch.object(se, "ruleset_sha256", lambda: next(moving)):
            r = self.loop(Calls(allowed))
        self.assertEqual(r["status"], "rules-changed")


class TestGateOrder(Base):
    def spied(self, ran):
        return mock.patch.object(se, "AGENT_POST_BUILDER_GATES", tuple(
            (gid, (lambda gid, fn: lambda st: ran.append(gid) or fn(st))(gid, fn))
            for gid, fn in se.AGENT_POST_BUILDER_GATES))

    def test_the_gate_tuple(self):
        self.assertEqual(tuple(gid for gid, _ in se.AGENT_POST_BUILDER_GATES), GATE_IDS)
        self.assertEqual(se.AGENT_CRASH_GATES, ("writable-set", "regenerate", "rules-unchanged"))

    def test_every_gate_runs_after_an_allowed_edit(self):
        ran = []
        with self.spied(ran):
            r = self.loop(Calls(allowed), max_iterations=2)
        self.assertEqual(r["status"], "needs-approval")
        self.assertEqual(tuple(ran), GATE_IDS)

    def test_every_gate_runs_after_a_non_zero_exit(self):
        ran = []
        with self.spied(ran):
            r = self.loop(Calls(allowed, rc=1))
        self.assertEqual(r, {"status": "builder-failed", "iteration": 1, "returncode": 1})
        self.assertEqual(tuple(ran), GATE_IDS[:-1])
        self.assertIn("careful1 code review", read(self.path("wrappers/claude/%s.md" % NAME)))
        self.assertEqual(check_clean(self.agent)[0], 0)

    def test_every_gate_runs_after_an_eval_error(self):
        ran = []
        with self.spied(ran):
            r = self.loop(Calls(allowed, raise_=se.EvalError("builder output is not a JSON object")))
        self.assertEqual(r, {"status": "builder-failed", "iteration": 1, "error": "builder output is not a JSON object"})
        self.assertEqual(tuple(ran), GATE_IDS[:-1])
        self.assertEqual(check_clean(self.agent)[0], 0)

    def test_a_non_zero_exit_with_an_outside_write_is_reported_as_outside(self):
        r = self.loop(Calls(lambda d, k: append(os.path.join(d, *SCENARIO.split("/")), "\n"), rc=2))
        self.assertEqual(r, {"status": "builder-wrote-outside", "iteration": 1, "problems": [SCENARIO], "returncode": 2})

    def test_another_exception_runs_the_crash_gates_then_propagates(self):
        ran = []
        with self.spied(ran), self.assertRaises(RuntimeError):
            self.loop(Calls(lambda d, k: append(os.path.join(d, "AGENT.md"), KOREAN + "\n"), raise_=RuntimeError("boom")))
        self.assertEqual(tuple(ran), se.AGENT_CRASH_GATES)
        self.assertIn(KOREAN, read(self.path("wrappers/claude/%s.md" % NAME)))

    def test_another_exception_with_an_outside_write_is_reported(self):
        r = self.loop(Calls(lambda d, k: append(os.path.join(d, *SCENARIO.split("/")), "\n"), raise_=RuntimeError("boom")))
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", [SCENARIO]))
        self.assertIn("RuntimeError: boom", r["error"])
        self.assertNotIn("returncode", r)

    def test_another_exception_that_moved_the_rules_hash_is_reported(self):
        def edit(d, k):
            os.symlink(os.path.join(self.tmp, "t.json"), os.path.join(self.root, "targets", "t.json"))
        with mock.patch.object(se, "agent_fingerprint", side_effect=[{}, {}, {}]):
            r = self.loop(Calls(edit, raise_=RuntimeError("boom")))
        self.assertEqual(r["status"], "rules-changed")
        self.assertIn("RuntimeError: boom", r["error"])

    def test_the_second_writable_check_allows_only_generator_writes(self):
        real = se.agent_regenerate

        def regen(agent_dir):
            out = real(agent_dir)
            write(os.path.join(agent_dir, "scenarios", "03-new.json"), "{}")
            return out
        with mock.patch.object(se, "agent_regenerate", regen):
            r = self.loop(Calls(allowed))
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["scenarios/03-new.json"]))

    def test_an_invalid_agent_after_the_builder_stops_the_loop(self):
        with mock.patch.object(se, "agent_validation_problems", lambda agent: ["%s broken" % agent.name]):
            r = self.loop(Calls(allowed))
        self.assertEqual(r, {"status": "agent-invalid", "iteration": 1, "problems": ["%s broken" % NAME],
                             "returncode": 0})


class TestBaselines(Base):
    def test_the_hermes_parity_fail_present_before_does_not_stop_the_loop(self):
        self.assertTrue(any(p.startswith("[hermes]") for p in eva.parity_violations(self.agent)))
        self.assertEqual(self.loop(Calls(allowed), max_iterations=2)["status"], "needs-approval")

    def test_a_new_parity_fail_stops_the_loop(self):
        real = eva.parity_violations
        state = {"builder": False}

        def builder(it, d):
            describe(d, "careful")
            state["builder"] = True
            return 0
        with mock.patch.object(eva, "parity_violations",
                               lambda p: real(p) + (["[claude] new finding"] if state["builder"] else [])):
            r = self.loop(builder)
        self.assertEqual((r["status"], r["problems"]), ("builder-parity-missing", ["[claude] new finding"]))

    def test_a_generator_refusal_stops_the_loop_as_parity(self):
        def edit(d, k):
            p = os.path.join(d, "AGENT.md")
            write(p, read(p).replace("name: %s" % NAME, "name: other-name"))
        r = self.loop(Calls(edit))
        self.assertEqual(r["status"], "builder-parity-missing")
        self.assertIn("name-matches-folder", r["problems"][0])

    def test_existing_review_items_do_not_stop_the_loop_and_a_new_one_does(self):
        self.assertTrue(eva.review_items(self.agent))
        self.assertEqual(self.loop(Calls(allowed), max_iterations=2)["status"], "needs-approval")
        shutil.rmtree(self.ws)

        def edit(d, k):
            p = os.path.join(d, "AGENT.md")
            write(p, read(p).replace("capabilities: [read, search]", "capabilities: [read, search, write]"))
        r = self.loop(Calls(edit))
        self.assertEqual(r["status"], "builder-review-required")
        self.assertTrue(any("['read', 'search', 'write']" in p for p in r["problems"]), r["problems"])


class TestIterations(Base):
    def test_the_cap_comes_from_the_policy(self):
        doc = T.rjson(os.path.join(ROOT, "policy", "eval-policy.json"))
        self.assertEqual(se.MAX_AUTO_ITERATIONS, doc["values"]["MAX_AUTO_ITERATIONS"]["value"])
        with self.assertRaises(se.EvalError):
            self.loop(Calls(allowed), max_iterations=se.MAX_AUTO_ITERATIONS + 1)
        with mock.patch.object(se, "MAX_AUTO_ITERATIONS", 2), self.assertRaises(se.EvalError):
            self.loop(Calls(allowed), max_iterations=3)
        self.assertFalse(os.path.exists(self.ws))

    def test_the_loop_runs_at_most_max_iterations(self):
        builder = Calls(allowed)
        r = self.loop(builder, max_iterations=se.MAX_AUTO_ITERATIONS)
        self.assertEqual(r, {"status": "needs-approval", "iteration": se.MAX_AUTO_ITERATIONS})
        self.assertEqual(len(builder.calls), se.MAX_AUTO_ITERATIONS - 1)

    def test_only_coverage_problems_need_no_builder(self):
        builder = Calls(allowed)
        grader = se.LLMGrader("claude-sonnet-5-5", exec_fn=AE.FakeAgentExec(answer="yes"))
        r = self.loop(builder, grader=grader)
        self.assertEqual(r, {"status": "needs-coverage", "iteration": 1})
        self.assertEqual(builder.calls, [])

    def test_a_failed_criterion_calls_the_builder(self):
        builder = Calls(allowed)
        grader = se.LLMGrader("claude-sonnet-5-5", exec_fn=AE.FakeAgentExec(answer="no"))
        self.assertEqual(self.loop(builder, grader=grader, max_iterations=2)["status"], "needs-approval")
        self.assertEqual(len(builder.calls), 1)
        g = T.rjson(os.path.join(self.ws, "iteration-1", "claude", "overview", "new_agent", "run-1", "grading.json"))
        self.assertEqual(g["grader_engine"], "claude")
        self.assertIn("criteria-needs-review", g["failed"])

    def test_an_exhausted_budget_stops_before_the_builder(self):
        text = re.sub(r'"total_cost_usd":\s*[0-9.]+', '"total_cost_usd": 1000.0', AE.read(AE.CLAUDE_STREAM))
        builder = Calls(allowed)
        r = self.loop(builder, budget=se.Budget(50.0), fake=AE.FakeAgentExec(claude=text))
        self.assertEqual(r, {"status": "budget-exhausted", "iteration": 1})
        self.assertEqual(builder.calls, [])

    def test_the_precheck_runs_before_every_iteration(self):
        seen = []

        def precheck(agent, left):
            seen.append((left, agent.dir))
            return [] if len(seen) == 1 else ["scenario drift"]
        r = self.loop(Calls(allowed), max_iterations=3, precheck=precheck)
        self.assertEqual(r, {"status": "gates-failed", "iteration": 1, "problems": ["scenario drift"]})
        self.assertEqual([x for x, _ in seen], [3, 2])
        self.assertEqual(se.iterations(self.ws), [1])

    def test_a_stopped_run_never_reaches_the_builder(self):
        builder = Calls(allowed)
        with mock.patch.object(se, "agent_isolated_run", side_effect=se.EvalError("isolation broke")):
            r = self.loop(builder)
        self.assertEqual(r["status"], "run-stopped")
        self.assertEqual(builder.calls, [])


class TestAgentBuilder(Base):
    def test_one_capped_call_in_the_agent_folder_charged_to_the_budget(self):
        seen = []
        fx = lambda cmd, cwd, timeout, env=None: seen.append((cmd, cwd)) or (0, BUILDER_RESULT, "")
        it = os.path.join(self.ws, "iteration-1")
        write(os.path.join(it, "comparison.md"), "# last report\n")
        budget = se.Budget(10.0)
        b = se.AgentBuilder("Improve it.", "claude-opus-5-5", budget, exec_fn=fx)
        d = os.path.realpath(self.agent)
        self.assertEqual(b(it, d), 0)
        ((cmd, cwd),) = seen
        self.assertEqual(cwd, d)
        self.assertEqual(cmd[:2], ["claude", "-p"])
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read,Edit,Write,Glob,Grep")
        self.assertIn("--max-budget-usd", cmd)
        self.assertIn("# last report", cmd[2])
        self.assertIn(se.AGENT_ENGLISH_BUILDER_RULE, cmd[2])
        self.assertIn("You may change only AGENT.md", cmd[2])
        self.assertNotIn(eva.ENGLISH_BUILDER_RULE, cmd[2])
        self.assertGreater(budget.spent, 0)
        self.assertEqual([e["command"] for e in T.rjson(os.path.join(it, "cost.json"))["entries"]], ["builder"])
        self.assertEqual(len(b.harness_writes), 1)

    def test_a_launch_without_the_agent_hook_or_under_a_managed_hook_policy_is_refused(self):
        calls = []
        fx = lambda cmd, cwd, timeout, env=None: calls.append(cwd) or (0, BUILDER_RESULT, "")
        d = os.path.realpath(self.agent)
        skill_cmd = se.ClaudeBuilder("x", "claude-opus-5-5", None).command("p", None, d)
        agent_cmd = se.AgentBuilder("x", "claude-opus-5-5", None).command("p", None, d)
        with self.assertRaises(se.EvalError):
            se.launch_claude(fx, skill_cmd, d, 5, edit_allowlist=True, edit_mode=writable.AGENT_MODE)
        with self.assertRaises(se.EvalError):
            se.launch_claude(fx, agent_cmd, d, 5, edit_allowlist=True)
        with self.assertRaises(se.EvalError):
            se.launch_claude(fx, ["claude", "-p", "x"], d, 5, edit_allowlist=True, edit_mode=writable.AGENT_MODE)
        se.launch_claude(fx, agent_cmd, d, 5, edit_allowlist=True, edit_mode=writable.AGENT_MODE)
        self.assertEqual(calls, [d])
        it = os.path.join(self.tmp, "it")
        os.makedirs(it)
        b = se.AgentBuilder("Improve it.", "claude-opus-5-5", se.Budget(10.0), exec_fn=fx)
        for key in ("disableAllHooks", "allowManagedHooksOnly"):
            write(self.managed, json.dumps({key: True}))
            with self.assertRaises(se.EvalError) as cm:
                b(it, d)
            self.assertIn("managed hook policy", str(cm.exception))
        self.assertEqual(calls, [d])


class Hook(Base):
    def setUp(self):
        Base.setUp(self)
        _, _, hook = hook_entry(self.agent)
        self.argv = [hook["command"]] + hook["args"]

    def run_hook(self, payload, argv=None, raw=None, cwd=None):
        p = T.REAL_POPEN(argv or self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         cwd=cwd or self.agent, universal_newlines=True)
        out, err = p.communicate(raw if raw is not None else json.dumps(payload), timeout=60)
        self.assertEqual(p.returncode, 0, err)
        if not out.strip():
            return None, ""
        o = json.loads(out)["hookSpecificOutput"]
        return o["permissionDecision"], o["permissionDecisionReason"]

    def hook(self, tool, tool_input, cwd=None, argv=None):
        return self.run_hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                              "cwd": self.agent if cwd is None else cwd}, argv)[0]


class TestAgentHook(Hook):
    def test_settings_name_the_agent_mode_for_the_agent_folder(self):
        st, entry, hook = hook_entry(self.agent)
        self.assertIs(st["disableAllHooks"], False)
        self.assertEqual(entry["matcher"], "*")
        self.assertEqual((hook["type"], hook["command"], hook["onFailure"]), ("command", sys.executable, "block"))
        self.assertEqual(hook["args"], [GATE, writable.AGENT_MODE, os.path.realpath(self.agent)])
        self.assertEqual(writable.AGENT_MODE, "agent-allowlist")

    def test_agent_md_is_allowed(self):
        md = self.path("AGENT.md")
        self.assertEqual(self.hook("Edit", {"file_path": md, "old_string": "a", "new_string": "b"}), "allow")
        self.assertEqual(self.hook("Write", {"file_path": md, "content": "x"}), "allow")
        self.assertEqual(self.hook("MultiEdit", {"file_path": md, "edits": []}), "allow")
        self.assertEqual(self.hook("NotebookEdit", {"notebook_path": md, "new_source": "x"}), "allow")
        self.assertEqual(self.hook("Edit", {"file_path": "AGENT.md"}), "allow")
        self.assertEqual(self.hook("Edit", {"file_path": self.path("scenarios/../AGENT.md")}), "allow")
        alias = os.path.join(self.tmp, "alias")
        os.symlink(self.agent, alias)
        self.assertEqual(self.hook("Edit", {"file_path": os.path.join(alias, "AGENT.md")}), "allow")

    def test_everything_else_is_denied(self):
        for rel in (CODEX_WRAPPER, "wrappers/claude/%s.md" % NAME, SCENARIO, "scenarios/01.json", "rules/naming.rules.json",
                    "README.md", "AGENT.md.bak", "agent.md", "scenarios/AGENT.md", ".claude/settings.json"):
            self.assertEqual(self.hook("Write", {"file_path": self.path(rel), "content": "x"}), "deny", rel)
        self.assertEqual(self.hook("Write", {"file_path": os.path.join(self.root, "rules", "naming.rules.json")}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": os.path.join(self.ws, "approvals.json")}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": self.agent}), "deny")
        self.assertEqual(self.hook("Edit", {"file_path": SCENARIO}), "deny")
        self.assertEqual(self.hook("MultiEdit", {"file_path": self.path("AGENT.md"), "more": {"file_path": self.path(SCENARIO)}}),
                         "deny")

    def test_deny_reason_names_the_agent_writable_set(self):
        _, reason = self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": self.agent,
                                   "tool_input": {"file_path": self.path(CODEX_WRAPPER)}})
        self.assertIn("outside the builder's writable set (AGENT.md)", reason)

    def test_commands_and_other_tools_are_denied_and_reads_pass(self):
        self.assertEqual(self.hook("Bash", {"command": "ls"}), "deny")
        self.assertEqual(self.hook("Monitor", {"command": "tail -f x"}), "deny")
        self.assertEqual(self.hook("PowerShell", {"command": "dir"}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": self.path("AGENT.md"), "x": [{"command": "ls"}]}), "deny")
        self.assertEqual(self.hook("WebFetch", {"url": "https://example.com"}), "deny")
        self.assertEqual(self.hook("Move", {"file_path": self.path("AGENT.md")}), "deny")
        for tool, inp in (("Read", {"file_path": self.path(SCENARIO)}), ("Glob", {"pattern": "**"}),
                          ("Grep", {"pattern": "x", "path": self.ws})):
            self.assertIsNone(self.hook(tool, inp), tool)

    def test_a_link_named_agent_md_to_outside_is_denied(self):
        outside = os.path.join(self.tmp, "outside.md")
        write(outside, "outside\n")
        linked = os.path.join(self.tmp, "linked", NAME)
        shutil.copytree(self.agent, linked)
        os.remove(os.path.join(linked, "AGENT.md"))
        os.symlink(outside, os.path.join(linked, "AGENT.md"))
        _, _, hook = hook_entry(linked)
        argv = [hook["command"]] + hook["args"]
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": linked,
                   "tool_input": {"file_path": os.path.join(linked, "AGENT.md")}}
        self.assertEqual(self.run_hook(payload, argv=argv, cwd=linked)[0], "deny")
        os.remove(os.path.join(linked, "AGENT.md"))
        os.symlink("scenarios/01-overview.json", os.path.join(linked, "AGENT.md"))
        self.assertEqual(self.run_hook(payload, argv=argv, cwd=linked)[0], "deny")

    def test_a_hard_link_is_denied(self):
        outside = os.path.join(self.tmp, "outside.md")
        shutil.copyfile(self.path("AGENT.md"), outside)
        os.remove(self.path("AGENT.md"))
        os.link(outside, self.path("AGENT.md"))
        decision, reason = self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": self.agent,
                                          "tool_input": {"file_path": self.path("AGENT.md")}})
        self.assertEqual(decision, "deny")
        self.assertIn("hard links", reason)

    def test_bad_input_root_mode_and_relative_paths_deny(self):
        for raw in ("not json", "[]", json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write",
                                                  "tool_input": {"file_path": self.path("AGENT.md")}})):
            self.assertEqual(self.run_hook(None, raw=raw)[0], "deny", raw)
        link = os.path.join(self.tmp, "agent-link")
        os.symlink(self.agent, link)
        for root in (NAME, os.path.join(self.tmp, "missing"), link, ""):
            self.assertEqual(self.hook("Write", {"file_path": self.path("AGENT.md")}, argv=self.argv[:-1] + [root]),
                             "deny", root)
        for mode in ("agent-allowlistx", "Agent-Allowlist", "agent"):
            argv = self.argv[:2] + [mode] + self.argv[3:]
            self.assertEqual(self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": self.agent,
                                            "tool_input": {"file_path": self.path("AGENT.md")}}, argv=argv),
                             ("deny", "unknown gate mode"), mode)
        self.assertEqual(self.hook("Write", {"file_path": "AGENT.md"}, cwd=self.ws), "deny")
        for bad in ({}, {"file_path": ""}, {"file_path": 7}, {"file_path": self.path("AGENT.md") + "\x00"}):
            self.assertEqual(self.hook("Edit", bad), "deny", bad)

    def test_the_writable_table_is_keyed_by_mode(self):
        self.assertTrue(writable.is_writable("AGENT.md", writable.AGENT_MODE))
        for rel in ("SKILL.md", "references/a.md", SCENARIO, CODEX_WRAPPER, "AGENT.md/x", None):
            self.assertFalse(writable.is_writable(rel, writable.AGENT_MODE), rel)
        self.assertEqual(writable.MODES[writable.AGENT_MODE].shown, "AGENT.md")
        self.assertEqual(writable.MODES[writable.ALLOWLIST_MODE].shown, writable.SHOWN)
        self.assertTrue(writable.is_writable("SKILL.md"))
        self.assertTrue(writable.is_writable("scripts/x.py"))
        self.assertFalse(writable.is_writable("AGENT.md"))
        skill_argv = self.argv[:2] + [writable.ALLOWLIST_MODE, os.path.realpath(self.agent)]
        self.assertEqual(self.hook("Edit", {"file_path": self.path("AGENT.md")}, argv=skill_argv), "deny")
        self.assertEqual(self.hook("Edit", {"file_path": self.path("SKILL.md")}, argv=skill_argv), "allow")
        self.assertEqual(self.hook("Edit", {"file_path": self.path("SKILL.md")}), "deny")


class TestSkillModeUnchanged(Hook):
    def setUp(self):
        Hook.setUp(self)
        self.spec_, _ = T.Base.spec(self, evals=T.evals_doc(human=False))
        self.skill = self.spec_["skill_dir"]
        self.sws = os.path.join(self.tmp, "ws")
        os.makedirs(self.sws, exist_ok=True)
        write(os.path.join(self.sws, "approvals.json"), "{}\n")
        cmd = se.ClaudeBuilder("x", "claude-opus-5-5", None).command("p", None, self.skill)
        st = json.loads(cmd[cmd.index("--settings") + 1])
        self.sargv = [st["hooks"]["PreToolUse"][0]["hooks"][0]["command"]] + st["hooks"]["PreToolUse"][0]["hooks"][0]["args"]

    def skill_hook(self, tool, tool_input):
        return self.run_hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                              "cwd": self.skill}, argv=self.sargv, cwd=self.skill)

    def test_skill_mode_argv_and_decisions(self):
        self.assertEqual(self.sargv[1:], [GATE, "edit-allowlist", os.path.realpath(self.skill)])
        self.assertEqual(self.skill_hook("Edit", {"file_path": os.path.join(self.skill, "SKILL.md")}),
                         ("allow", "edit inside the builder's writable set"))
        decision, reason = self.skill_hook("Edit", {"file_path": os.path.join(self.skill, "evals", "evals.json")})
        self.assertEqual(decision, "deny")
        self.assertIn("outside the builder's writable set (SKILL.md references/** scripts/** assets/**)", reason)
        self.assertEqual(self.skill_hook("Write", {"file_path": os.path.join(self.sws, "approvals.json")})[0], "deny")
        self.assertEqual(self.skill_hook("Bash", {"command": "ls"}), ("deny", "the builder may not run commands"))
        os.makedirs(os.path.join(self.skill, "references"))
        os.symlink(os.path.join(self.tmp, "outside.txt"), os.path.join(self.skill, "references", "link.md"))
        self.assertEqual(self.skill_hook("Edit", {"file_path": os.path.join(self.skill, "references", "link.md")})[0],
                         "deny")
        self.assertEqual(self.skill_hook("Write", {"file_path": "SKILL.md"}), ("allow", "edit inside the builder's writable set"))


class Cli(Base):
    def setUp(self):
        Base.setUp(self)
        self.prompt = os.path.join(self.tmp, "builder-prompt.txt")
        write(self.prompt, "Improve the agent.\n")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def argv(self, *extra, profile="personal-mac", budget="20", live=True, prompt=True):
        a = ["agent", "loop", self.agent, "--workspace", self.ws, "--engines", "claude", "--runs", "1"]
        if live:
            a.append("--live")
        if profile:
            a += ["--auth-profile", profile]
        if budget:
            a += ["--budget-usd", budget]
        if prompt:
            a += ["--builder-prompt", self.prompt]
        return a + list(extra)

    def live(self, fake, *extra, **kw):
        with mock.patch.object(se, "live_exec", fake):
            return self.cli(*self.argv(*extra, **kw))

    def after_signing(self, change):
        return self.signing(change, after=True)

    def before_signing(self, change):
        return self.signing(change, after=False)

    def signing(self, change, after):
        real = se.APPROVER["it"]

        class Signer(object):
            def __getattr__(self, k):
                return getattr(real, k)

            def sign(self, kind, gate, prepare, budget_usd=None, reason=None):
                if kind != "live":
                    return real.sign(kind, gate, prepare, budget_usd=budget_usd, reason=reason)
                if not after:
                    change()
                grant = real.sign(kind, gate, prepare, budget_usd=budget_usd)
                if after:
                    change()
                return grant
        return mock.patch.dict(se.APPROVER, {"it": Signer()})

    def open_gates(self):
        AE.open_gates(self.agent, self.ws)


class LoopExec(AE.FakeAgentExec):
    """FakeAgentExec plus the agent builder call (identified by its --tools value): edit(agent folder, call number)."""
    def __init__(self, edit=None, answer="no", on_run=None, **kw):
        AE.FakeAgentExec.__init__(self, answer=answer, **kw)
        self.edit, self.on_run, self.builder_calls = edit, on_run, []

    def __call__(self, cmd, cwd, timeout, env=None):
        if cmd[0] == "claude" and "--tools" in cmd and cmd[cmd.index("--tools") + 1] == se.AgentBuilder.TOOLS:
            self.builder_calls.append({"cmd": cmd, "cwd": cwd})
            if self.edit:
                self.edit(cwd, len(self.builder_calls))
            return 0, BUILDER_RESULT, ""
        if self.on_run and "--json-schema" not in cmd:
            self.on_run(len(self.calls) + 1)
        return AE.FakeAgentExec.__call__(self, cmd, cwd, timeout, env)


class TestCliRefusals(Cli):
    def refused(self, *extra, **kw):
        fake = LoopExec(allowed)
        before = tree(self.agent)
        rc, out, err = self.live(fake, *extra, **kw)
        self.assertEqual(rc, 2, out + err)
        self.assertEqual((fake.calls, fake.grader_calls, fake.builder_calls), ([], [], []))
        self.assertFalse(os.path.exists(self.ws))
        self.assertFalse(se._LIVE["on"])
        self.assertEqual(tree(self.agent), before)
        return err

    def test_refusals_before_recording(self):
        self.assertIn(se.NO_BUDGET, self.refused(budget=None))
        self.assertIn(se.PROFILE_REQUIRED, self.refused(profile=None))
        self.assertIn("claude builder: refused in API-key mode", self.refused(profile="company"))

    def test_a_link_that_leaves_the_agent_folder_is_refused(self):
        write(os.path.join(self.tmp, "outside.md"), "x\n")
        os.symlink(os.path.join(self.tmp, "outside.md"), self.path("notes.md"))
        self.assertIn("notes.md (link) resolves outside the unit folder", self.refused())

    def test_an_agent_md_that_is_not_a_regular_file_is_refused(self):
        real = os.path.join(self.tmp, "real-agent.md")
        shutil.copyfile(self.path("AGENT.md"), real)
        os.remove(self.path("AGENT.md"))
        os.symlink(real, self.path("AGENT.md"))
        self.assertIn("AGENT.md is not a regular file directly in the agent folder", self.refused())

    def test_a_hard_linked_agent_md_is_refused(self):
        os.link(self.path("AGENT.md"), os.path.join(self.tmp, "other-agent.md"))
        self.assertIn("AGENT.md is not a regular file directly in the agent folder", self.refused())

    def test_max_iterations_above_the_policy_cap_is_refused(self):
        for n in (str(se.MAX_AUTO_ITERATIONS + 1), "0"):
            err = self.refused("--max-iterations", n)
            self.assertIn("--max-iterations must be 1..%d" % se.MAX_AUTO_ITERATIONS, err)

    def test_a_changed_builder_prompt_after_signing_is_refused(self):
        self.open_gates()
        fake = LoopExec(allowed)
        with self.after_signing(lambda: append(self.prompt, "more\n")):
            rc, out, err = self.live(fake)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("refused after signing: --builder-prompt %s changed since it was read" % self.prompt, err)
        self.assertEqual((fake.calls, fake.builder_calls), ([], []))

    def test_a_builder_prompt_changed_between_read_and_signing_is_refused(self):
        fake = LoopExec(allowed)
        with self.before_signing(lambda: write(self.prompt, "Do something else.\n")):
            rc, out, err = self.live(fake)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("--builder-prompt %s changed since it was read" % self.prompt, err)
        self.assertNotIn("recorded agent loop", err)
        self.assertEqual((fake.calls, fake.builder_calls), ([], []))


class TestCliDryRun(Cli):
    def test_without_live_nothing_runs(self):
        fake = LoopExec(allowed)
        rc, out, err = self.live(fake, live=False, profile=None, budget=None)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("DRY RUN: nothing was executed", out)
        self.assertIn("loop: max iterations %d" % se.MAX_AUTO_ITERATIONS, out)
        self.assertIn("builder model: %s" % se.DEFAULT_BUILDER_MODEL, out)
        self.assertIn("post-builder gates: " + ", ".join(GATE_IDS), out)
        self.assertIn("cell overview claude: planned", out)
        self.assertEqual((fake.calls, fake.grader_calls, fake.builder_calls), ([], [], []))
        self.assertFalse(os.path.exists(self.ws))


class TestCliSigned(Cli):
    def setUp(self):
        Cli.setUp(self)
        self.open_gates()

    def test_a_signed_loop_runs_to_a_builder_stop(self):
        fake = LoopExec(lambda d, k: append(os.path.join(d, *SCENARIO.split("/")), "\n"))
        rc, out, err = self.live(fake, "--max-iterations", "3")
        self.assertEqual(rc, 3, out + err)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((r["status"], r["iteration"], r["problems"]), ("builder-wrote-outside", 1, [SCENARIO]))
        self.assertIn("recorded agent loop --live by %s" % T.TEST_PRINCIPAL, err)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(fake.grader_calls), 1)
        (call,) = fake.builder_calls
        self.assertEqual(call["cwd"], os.path.realpath(self.agent))
        st = json.loads(call["cmd"][call["cmd"].index("--settings") + 1])
        self.assertEqual(st["hooks"]["PreToolUse"][0]["hooks"][0]["args"],
                         [GATE, writable.AGENT_MODE, os.path.realpath(self.agent)])
        prompt = call["cmd"][2]
        self.assertIn("Improve the agent.", prompt)
        self.assertIn(se.AGENT_ENGLISH_BUILDER_RULE, prompt)
        self.assertIn("# %s agent, iteration 1" % NAME, prompt)
        self.assertIn("--max-budget-usd", call["cmd"])
        entries = T.rjson(os.path.join(self.ws, "iteration-1", "cost.json"))["entries"]
        self.assertEqual([e["command"] for e in entries], ["agent grade", "builder"])
        self.assertEqual(se.iterations(self.ws), [1])
        self.assertFalse(se._LIVE["on"])

    def test_a_signed_loop_with_description_edits_regenerates_and_reaches_the_cap(self):
        fake = LoopExec(allowed)
        rc, out, err = self.live(fake, "--max-iterations", "2")
        self.assertEqual(rc, 3, out + err)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(r, {"status": "needs-approval", "iteration": 2})
        self.assertEqual(len(fake.builder_calls), 1)
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("careful1 code review", fake.calls[1]["files"][".claude/agents/%s.md" % NAME].decode("utf-8"))
        self.assertEqual(check_clean(self.agent)[0], 0)

    def test_a_scenario_changed_after_signing_stops_the_next_iteration(self):
        fake = LoopExec(allowed, on_run=lambda k: append(self.path(SCENARIO), "\n") if k == 1 else None)
        rc, out, err = self.live(fake, "--max-iterations", "3")
        self.assertEqual(rc, 3, out + err)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((r["status"], r["iteration"]), ("gates-failed", 1))
        self.assertIn("%s changed since signing" % SCENARIO, r["problems"][0])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(se.iterations(self.ws), [1])

    def test_a_scenario_changed_between_load_and_signing_runs_the_signed_text(self):
        p = self.path(SCENARIO)
        old_text, new_text = "Give me an overview of this repository", "Summarize this repository in one line"
        self.assertIn(old_text, read(p))
        fake = LoopExec(allowed)

        def edit_and_approve():
            write(p, read(p).replace(old_text, new_text))
            self.open_gates()
        with self.before_signing(edit_and_approve):
            rc, out, err = self.live(fake, "--max-iterations", "1")
        self.assertEqual(rc, 3, out + err)
        self.assertIn("recorded agent loop --live", err)
        cmd = fake.calls[0]["cmd"]
        self.assertTrue(any(new_text in x for x in cmd), cmd)
        self.assertFalse(any(old_text in x for x in cmd), cmd)

    def test_an_old_agent_changed_after_signing_stops_the_next_iteration(self):
        old_dir = AE.generated_copy(os.path.join(self.tmp, "old-agents"))
        old_scenario = os.path.join(old_dir, *SCENARIO.split("/"))
        fake = LoopExec(allowed, on_run=lambda k: append(old_scenario, "\n") if k == 1 else None)
        rc, out, err = self.live(fake, "--max-iterations", "3", "--old-agent", old_dir)
        self.assertEqual(rc, 3, out + err)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((r["status"], r["iteration"]), ("gates-failed", 1))
        self.assertEqual(r["problems"], ["old agent %s changed since signing" % SCENARIO])
        self.assertEqual(len(fake.builder_calls), 1)
        self.assertEqual(se.iterations(self.ws), [1])


if __name__ == "__main__":
    unittest.main()
