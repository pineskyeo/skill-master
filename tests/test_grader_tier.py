"""The grader tier rule (owner decision 2026-10-30): the grader differs from every model under test and from the builder by
tier (opus, sonnet, haiku, from the model name); the same tier is the same model, models of one company with different
tiers are different, and an unknown or unparseable name fails closed. Everything is mocked: no model or CLI call."""
import argparse, json, os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_agent_eval as AE
import test_agent_loop as AL
import test_multi_tool_parity as P
from test_skill_eval import se, rjson, wjson

OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"
SAME_TIER = "has the same tier (%s)"
NO_TIER = "has no single tier word"
BAD_NAMES = ("gpt-5", "claude-fable-5-1", "claude-opus-sonnet-5", "opusculum-5")
GATE = "grader-tier-distinct"
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


class TestModelTier(unittest.TestCase):
    def test_a_name_reads_as_its_tier_word(self):
        for name, tier in ((OPUS, "opus"), (SONNET, "sonnet"), (HAIKU, "haiku"), ("claude-3-5-sonnet-20241022", "sonnet"),
                           ("sonnet", "sonnet"), ("Claude-Opus-5-5", "opus"), ("us.anthropic.claude-haiku-5-5", "haiku")):
            with self.subTest(name):
                self.assertEqual(se.model_tier(name), tier)

    def test_a_repeated_tier_word_is_still_one_tier(self):
        self.assertEqual(se.model_tier("sonnet-sonnet-5"), "sonnet")

    def test_no_tier_word_two_tier_words_or_a_word_that_contains_one_read_none(self):
        for name in BAD_NAMES + ("", None, 5, "sonnet4", "myopus"):
            with self.subTest(name):
                self.assertIsNone(se.model_tier(name))

    def test_the_tier_list_is_policy_data(self):
        doc = rjson(os.path.join(T.ROOT, "policy", "models.json"))
        self.assertEqual(tuple(doc["values"]["MODEL_TIERS"]["value"]), se.MODEL_TIERS)
        self.assertEqual(se.MODEL_TIERS, ("opus", "sonnet", "haiku"))
        with mock.patch.object(se, "MODEL_TIERS", ("opus", "sonnet", "haiku", "fable")):
            self.assertEqual(se.model_tier("claude-fable-5-1"), "fable")


class TestPolicyGates(unittest.TestCase):
    def test_the_gate_replaces_the_two_old_ids_at_the_old_position(self):
        ids = [e["id"] for e in rjson(os.path.join(T.ROOT, "policy", "gates.json"))["live"]]
        self.assertEqual(ids[:5], ["eval-set-valid", "strict-budget", "subscription-auth", GATE, "rule-review"])
        self.assertNotIn("grader-not-model-under-test", ids)
        self.assertNotIn("codex-grader-not-under-test", ids)
        self.assertIsNone(se.gate_declaration_problem())


class TestLiveGate(T.Base):
    def setUp(self):
        T.Base.setUp(self)
        self.d = T.make_skill(self.tmp, evals=T.evals_doc(human=False))
        self.spec = se.load_eval_set(self.d)[0]
        os.makedirs(self.ws)

    def args(self, cmd, *extra):
        argv = [cmd, self.d, "--workspace", self.ws] + list(extra)
        a = se.build_parser().parse_args(argv)
        a.argv = argv
        a.grader_model = a.grader_model or se.DEFAULT_GRADER_MODEL
        return a, se.make_engines(a, self.spec)

    def state(self, cmd="run", *extra):
        a, engines = self.args(cmd, *extra)
        return dict(se.live_gate_states(a, self.spec, [], self.ws, engines, se.DEFAULT_TOKENS, {}, [],
                                        se.approval_verdict))[GATE]

    def run_state(self, model, grader):
        return self.state("run", "--engines", "claude", "--claude-model", model, "--grader-model", grader)

    def loop_state(self, model=OPUS, grader=SONNET, builder=OPUS):
        return self.state("loop", "--engines", "claude", "--claude-model", model, "--grader-model", grader,
                          "--builder-prompt", "prompt.txt", "--builder-model", builder)

    def test_different_tiers_pass(self):
        self.assertEqual(self.run_state(OPUS, SONNET), "pass")
        self.assertEqual(self.run_state(HAIKU, OPUS), "pass")

    def test_the_defaults_pass(self):
        self.assertEqual(self.state("run"), "pass")
        self.assertEqual(self.state("loop", "--builder-prompt", "prompt.txt"), "pass")

    def test_the_same_company_with_another_tier_passes(self):
        self.assertEqual(self.run_state("claude-3-5-sonnet-20241022", OPUS), "pass")

    def test_the_same_tier_as_a_model_under_test_fails_even_for_another_model_name(self):
        state = self.run_state("claude-3-5-sonnet-20241022", SONNET)
        self.assertTrue(state.startswith("fail: "), state); self.assertIn(SAME_TIER % "sonnet", state)

    def test_the_same_tier_as_the_builder_fails(self):
        state = self.loop_state(builder="claude-sonnet-4-0")
        self.assertTrue(state.startswith("fail: "), state)
        self.assertIn(SAME_TIER % "sonnet", state); self.assertIn("builder model claude-sonnet-4-0", state)

    def test_an_unknown_name_fails_for_the_grader_the_model_under_test_and_the_builder(self):
        for name in BAD_NAMES:
            for label, state in (("grader", self.run_state(OPUS, name)), ("under test", self.run_state(name, SONNET)),
                                 ("builder", self.loop_state(builder=name))):
                with self.subTest(name=name, who=label):
                    self.assertTrue(state.startswith("fail: "), state); self.assertIn(NO_TIER, state)

    def test_none_for_a_model_under_test_fails_closed(self):
        state = self.state("run", "--engines", "codex")
        self.assertTrue(state.startswith("fail: "), state)
        self.assertIn("codex model under test (CLI default)", state); self.assertIn(NO_TIER, state)

    def test_a_real_live_check_raises_and_the_report_mode_collects(self):
        a, engines = self.args("run", "--engines", "claude", "--claude-model", SONNET)
        with self.assertRaises(se.EvalError) as cm:
            se.check_live_gates(a, self.spec, [], self.ws, None, engines, se.DEFAULT_TOKENS, {}, 50.0)
        self.assertIn(SAME_TIER % "sonnet", str(cm.exception))
        report = []
        se.check_live_gates(a, self.spec, [], self.ws, None, engines, se.DEFAULT_TOKENS, {}, 50.0, report=report)
        self.assertTrue(any(SAME_TIER % "sonnet" in r for r in report), report)

    def test_calibrate_checks_only_the_graders_own_name(self):
        golden = os.path.join(self.tmp, "golden.json")
        wjson(golden, {"examples": [{"prompt": "p", "output": "o", "item": "i", "label": True}]})
        a, engines = self.args("calibrate", "--golden", golden, "--claude-model", SONNET)
        report = []
        se.check_live_gates(a, self.spec, [], self.ws, 1, engines, se.DEFAULT_TOKENS, {}, 50.0, report=report)
        self.assertFalse(any(SAME_TIER % "sonnet" in r for r in report), report)
        a.grader_model = "claude-fable-5-1"
        report = []
        se.check_live_gates(a, self.spec, [], self.ws, 1, engines, se.DEFAULT_TOKENS, {}, 50.0, report=report)
        self.assertTrue(any("grader model claude-fable-5-1 " + NO_TIER in r for r in report), report)


class TestCheckGraderModel(T.Base):
    def check(self, grader, engines, **plan):
        it = os.path.join(self.tmp, "it")
        wjson(os.path.join(it, "plan.json"), dict(plan, engines=engines))
        se.check_grader_model(it, grader)

    def refused(self, grader, engines, **plan):
        with self.assertRaises(se.EvalError) as cm: self.check(grader, engines, **plan)
        return str(cm.exception)

    def test_different_tiers_pass(self):
        self.check(SONNET, {"claude": OPUS})
        self.check(HAIKU, {"claude": OPUS}, builder_model=SONNET)

    def test_every_engine_of_the_plan_is_compared(self):
        self.assertIn(SAME_TIER % "opus", self.refused("claude-opus-4-0", {"claude": OPUS}))
        self.assertIn(SAME_TIER % "haiku", self.refused(HAIKU, {"claude": OPUS, "other": "fake-haiku"}))

    def test_the_recorded_builder_is_compared(self):
        self.assertIn("builder model " + SONNET, self.refused(SONNET, {"claude": OPUS}, builder_model=SONNET))

    def test_unknown_names_fail(self):
        self.assertIn(NO_TIER, self.refused("gpt-5", {"claude": OPUS}))
        self.assertIn(NO_TIER, self.refused(SONNET, {"claude": "claude-fable-5-1"}))
        self.assertIn(NO_TIER, self.refused(SONNET, {"claude": OPUS}, builder_model="claude-fable-5-1"))

    def test_none_for_any_engine_fails_closed(self):
        self.assertIn("codex model under test (CLI default)", self.refused(SONNET, {"claude": OPUS, "codex": None}))
        self.assertIn("claude model under test (CLI default)", self.refused(SONNET, {"claude": None}))

    def test_an_iteration_without_a_plan_is_refused(self):
        it = os.path.join(self.tmp, "none"); os.makedirs(it)
        with self.assertRaises(se.EvalError) as cm: se.check_grader_model(it, SONNET)
        self.assertIn("no plan.json", str(cm.exception))


class TestGradeOfAPlanThatFailsTheRule(P.CliBase):
    NONE_MODEL = "codex model under test (CLI default)"

    def setUp(self):
        P.CliBase.setUp(self)
        self.d = T.make_skill(self.tmp, evals=T.evals_doc(human=False))
        wjson(os.path.join(self.ws, "iteration-1", "plan.json"), {"engines": {"claude": SONNET, "codex": None}})

    def grade(self, *extra):
        return self.cli("grade", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac",
                        "--budget-usd", "5", *extra)

    def refusal(self, out, text):
        return next(l[4:] for l in out.splitlines() if l.startswith("  - ") and text in l)

    def test_the_dry_run_prints_the_plan_lists_the_problem_and_returns_3(self):
        rc, out, err = self.grade("--dry-run")
        self.assertEqual(rc, 3, out + err)
        self.assertIn("LIVE DRY RUN: nothing was executed", out)
        self.assertIn(NO_TIER, self.refusal(out, self.NONE_MODEL))
        self.assertIn(SAME_TIER % "sonnet", self.refusal(out, self.NONE_MODEL))

    def test_a_real_start_refuses_with_the_problem_the_dry_run_lists(self):
        _, out, _ = self.grade("--dry-run")
        rc, _, err = self.grade()
        self.assertEqual(rc, 2, err)
        self.assertIn(self.refusal(out, self.NONE_MODEL), err)

    def test_an_iteration_without_a_plan_is_collected_as_well(self):
        os.makedirs(os.path.join(self.ws, "iteration-2"))
        rc, out, err = self.grade("--dry-run", "--iteration", "2")
        self.assertEqual(rc, 3, out + err)
        self.assertIn("cannot check the grader differs", self.refusal(out, "no plan.json"))


class Builder:
    def __init__(self, model): self.model, self.calls = model, 0
    def __call__(self, it_dir, skill_dir):
        self.calls += 1
        return 0


class TestSkillPlanRecordsTheBuilder(T.Base):
    def plan_of(self, n=1):
        return rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))

    def test_a_run_without_a_builder_records_none(self):
        spec, _ = self.spec(evals=T.evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, self.engines(), runs=1, log=T.quiet)
        self.assertNotIn("builder_model", self.plan_of(n))

    def test_the_loop_records_the_builder_so_a_later_grade_compares_it(self):
        spec, _ = self.spec(evals=T.evals_doc(human=False))
        se.loop(spec, self.ws, self.engines(), self.grader(), Builder(OPUS), max_iterations=1, runs=1, log=T.quiet)
        self.assertEqual(self.plan_of()["builder_model"], OPUS)
        it = os.path.join(self.ws, "iteration-1")
        se.check_grader_model(it, SONNET)
        wjson(os.path.join(it, "plan.json"), dict(self.plan_of(), engines={"claude": HAIKU}))
        with self.assertRaises(se.EvalError) as cm: se.check_grader_model(it, "claude-opus-4-0")
        self.assertIn("builder model " + OPUS, str(cm.exception))

    def test_a_loop_grader_of_the_builder_tier_stops_at_grading(self):
        spec, _ = self.spec(evals=T.evals_doc(human=False))
        with self.assertRaises(se.EvalError) as cm:
            se.loop(spec, self.ws, [se.ClaudeCodeEngine(HAIKU, exec_fn=T.FakeClaude())], self.grader(), Builder(SONNET),
                    max_iterations=1, runs=1, log=T.quiet)
        self.assertIn("builder model " + SONNET, str(cm.exception))


def agent_args(grader=SONNET, builder_prompt=None, builder_model=OPUS):
    return argparse.Namespace(grader_model=grader, builder_prompt=builder_prompt, builder_model=builder_model)


class TestAgentGraderProblems(unittest.TestCase):
    def problems(self, *models, **kw):
        return se.agent_grader_problems(agent_args(**kw), [se.AgentClaudeEngine(model=m) for m in models])

    def test_different_tiers_pass(self):
        self.assertEqual(self.problems(OPUS), [])
        self.assertEqual(self.problems(OPUS, grader=HAIKU, builder_prompt="p.txt", builder_model=SONNET), [])

    def test_the_same_tier_as_a_model_under_test_fails(self):
        self.assertIn(SAME_TIER % "opus", self.problems(OPUS, grader="claude-opus-4-0")[0])

    def test_the_builder_counts_only_with_a_builder_prompt(self):
        self.assertEqual(self.problems(OPUS, builder_model=SONNET), [])
        self.assertIn("builder model " + SONNET, self.problems(OPUS, builder_prompt="p.txt", builder_model=SONNET)[0])

    def test_unknown_names_and_none_fail(self):
        self.assertIn(NO_TIER, self.problems(OPUS, grader="claude-fable-5-1")[0])
        self.assertIn(NO_TIER, self.problems("claude-fable-5-1")[0])
        self.assertIn(NO_TIER, self.problems(OPUS, builder_prompt="p.txt", builder_model="gpt-5")[0])
        self.assertIn("codex model under test (CLI default)",
                      se.agent_grader_problems(agent_args(), [se.AgentCodexEngine()])[0])


class TestAgentCommandsReachTheRule(AE.AgentBase):
    def test_agent_plan_shows_the_failure_and_still_exits_zero(self):
        rc, out, err = AE.cli("agent", "plan", self.agent, "--workspace", self.ws, "--claude-model", SONNET)
        self.assertEqual(rc, 0, out + err)
        line = next(l for l in out.splitlines() if l.startswith(GATE + ": "))
        self.assertTrue(line.startswith(GATE + ": fail: "), line); self.assertIn(SAME_TIER % "sonnet", line)

    def test_agent_plan_shows_pass_for_the_defaults(self):
        rc, out, err = AE.cli("agent", "plan", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 0, out + err)
        self.assertIn(GATE + ": pass", out.splitlines())

    def test_agent_run_live_refuses_before_any_call(self):
        rc, out, err, fake = self.live("run", "--engines", "claude", "--claude-model", SONNET)
        self.assertEqual(rc, 2, out + err); self.assertIn(SAME_TIER % "sonnet", err)
        self.assertEqual(fake.calls, [])

    def test_agent_grade_live_compares_the_plan_models(self):
        rc, out, err, _ = self.live("run", "--engines", "claude")
        self.assertEqual(rc, 0, out + err)
        rc, out, err, fake = self.live("grade", "--grader-model", OPUS)
        self.assertEqual(rc, 2, out + err); self.assertIn(SAME_TIER % "opus", err)
        self.assertEqual(fake.grader_calls, [])


class TestAgentLoopRecordsTheBuilder(AL.Cli):
    def test_the_plan_records_the_builder_model(self):
        self.open_gates()
        rc, out, err = self.live(AL.LoopExec(), "--max-iterations", "1")
        self.assertEqual(json.loads(out.splitlines()[-1])["iteration"], 1, out + err)
        plan = rjson(os.path.join(self.ws, "iteration-1", "plan.json"))
        self.assertEqual(plan["builder_model"], se.DEFAULT_BUILDER_MODEL)

    def test_a_grader_of_the_builder_tier_is_refused(self):
        self.open_gates()
        fake = AL.LoopExec()
        rc, out, err = self.live(fake, "--claude-model", HAIKU, "--grader-model", OPUS)
        self.assertEqual(rc, 2, out + err); self.assertIn("builder model " + OPUS, err)
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
