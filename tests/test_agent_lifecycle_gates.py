"""K1: the agent live starts (`agent run --live`, `agent loop --live`) need a current requirements_eval_set approval (or
current requirements and eval_set approvals) and a current rule_review decision for the agent's review rows; `agent plan`
prints those gates; and the agent parts B6 and S1 built (coverage waivers, the machine waiver, the registration approval
that names the report problems, the format-2 kind agent record, pending and the batch) are pinned.

Every signature uses the throwaway test key of tests/test_skill_eval.py, HOME is the temp HOME of its Base, the machine id
is a fake, subprocess is patched for the whole module, and every engine, grader and builder is a fake. ASCII only."""
import contextlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_agent_eval as AE
import test_agent_loop as L
import test_install as I
from test_skill_eval import se, eva, write, wjson, rjson
from engine import approvals as A

COMBINED = "requirements_eval_set"
NAME = AE.NAME
SCENARIO = "scenarios/01-overview.json"
REVIEW_ROWS = ["parity-permissions-codex", "parity-permissions-opencode"]
HERMES_ROW = "parity-permissions-hermes"
CODEX_FINDING = "agent-load-unverified:codex"
MACHINE = "11111111-2222-3333-4444-555555555555"
NO_APPROVALS = "live approvals missing: requirements_eval_set is missing, requirements is missing, eval_set is missing"
NO_REVIEW = "rule review not recorded: 2 finding(s) need a human decision"
GATES_LINE = "agent live gates (agent run --live and agent loop --live refuse until each passes): "
INPUTS_CHANGED = "the command's inputs changed after signing"
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def append(path, text="\n"):
    with open(path, "a", encoding="utf-8") as f: f.write(text)


def set_capabilities(agent_dir, caps):
    p = os.path.join(agent_dir, "AGENT.md")
    write(p, T.read(p).replace("capabilities: [read, search]", "capabilities: [%s]" % caps, 1))


def regenerate(agent_dir):
    with contextlib.redirect_stdout(io.StringIO()):
        assert eva.main(["generate", agent_dir]) == 0


def line(out, prefix):
    found = [l for l in out.splitlines() if l.startswith(prefix)]
    assert len(found) == 1, (prefix, out)
    return found[0]


def late_edit(edit):
    """(calls, patch): edit runs after the post-sign problems check of a live start, right before its plan-hash
    recheck (claude_auth_problems is the last call in between)."""
    calls = []

    def hook():
        calls.append(1)
        edit()
        return []
    return calls, mock.patch.object(se, "claude_auth_problems", hook)


def codex_wrapper(agent_dir):
    return os.path.join(agent_dir, "wrappers", "codex", NAME + ".toml")


class Gates(AE.AgentBase):
    def setUp(self):
        AE.AgentBase.setUp(self)
        shutil.rmtree(self.agent)
        self.agent = I.agent_copy(os.path.join(self.tmp, "agents"))
        pt = mock.patch.dict(se.MACHINE, {"id": lambda: MACHINE}); pt.start(); self.addCleanup(pt.stop)

    def spec(self):
        return se.agent_spec(se.Agent(self.agent))

    def requirements(self, text="Review the repository read-only with file:line evidence.\n"):
        os.makedirs(self.ws, exist_ok=True)
        write(os.path.join(self.ws, "requirements.md"), text)

    def approve(self, *gates):
        for g in gates: se.approve(g, self.spec(), self.ws)

    def verdict(self, gate):
        return se.approval_verdict(gate, self.spec(), self.ws).status

    def run_live(self, fake=None, gates=False):
        return self.live("run", "--engines", "claude", fake=fake, gates=gates)

    def refused(self, err, *texts):
        for t in texts: self.assertIn(t, err)

    def report(self):
        rc, out, err = AE.cli("agent", "report", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 3, out + err)
        return out, rjson(os.path.join(self.ws, "iteration-%d" % se.iterations(self.ws)[-1], "gate.json"))


class TestLiveGate(Gates):
    def test_without_any_approval_the_run_is_refused_before_signing(self):
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "agent run --live refused: ", NO_REVIEW, NO_APPROVALS)
        self.assertNotIn("signed agent run", err)
        self.assertEqual(fake.calls, [])
        self.assertFalse(se._LIVE["on"])

    def test_an_agent_requirements_approval_alone_is_refused(self):
        self.requirements()
        self.approve("requirements", "rule_review")
        self.assertEqual(self.verdict("requirements"), "approved")
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "live approvals missing: requirements_eval_set is missing, eval_set is missing")
        self.assertNotIn("requirements is ", err)
        self.assertEqual(fake.calls, [])

    def test_an_eval_set_approval_alone_is_refused_and_reads_stale_after_a_scenario_edit(self):
        self.requirements()
        self.approve("eval_set", "rule_review")
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "live approvals missing: requirements_eval_set is missing, requirements is missing")
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.verdict("eval_set"), "approved")
        append(os.path.join(self.agent, *SCENARIO.split("/")))
        self.assertEqual(self.verdict("eval_set"), "stale")

    def test_both_separate_approvals_open_the_live_start(self):
        self.requirements()
        self.approve("requirements", "eval_set", "rule_review")
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(fake.calls), 1)

    def test_the_combined_approval_alone_opens_the_live_start_and_reads_stale_after_a_scenario_edit(self):
        self.requirements()
        self.approve(COMBINED, "rule_review")
        self.assertEqual((self.verdict("requirements"), self.verdict("eval_set")), ("missing", "missing"))
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(fake.calls), 1)
        append(os.path.join(self.agent, *SCENARIO.split("/")))
        self.assertEqual(self.verdict(COMBINED), "stale")
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "live approvals missing: requirements_eval_set is stale")
        self.assertEqual(fake.calls, [])

    def test_a_requirements_edit_after_signing_is_refused_after_signing(self):
        self.requirements()
        self.approve(COMBINED, "rule_review")
        with self.after_signing(lambda: self.requirements("Another goal.\n")):
            rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "agent run --live refused after signing: ", "requirements_eval_set is stale")
        self.assertEqual(fake.calls, [])
        self.assertFalse(se._LIVE["on"])

    def test_a_scenario_edit_after_signing_is_refused_after_signing(self):
        self.requirements()
        self.approve(COMBINED, "rule_review")
        with self.after_signing(lambda: append(os.path.join(self.agent, *SCENARIO.split("/")))):
            rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "agent run --live refused after signing: ", "requirements_eval_set is stale")
        self.assertEqual(fake.calls, [])

    def test_a_stale_requirements_approval_beside_a_current_eval_set_approval_is_refused(self):
        self.requirements()
        self.approve("requirements", "eval_set", "rule_review")
        self.requirements("Another goal.\n")
        self.assertEqual((self.verdict("requirements"), self.verdict("eval_set")), ("stale", "approved"))
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "live approvals missing: requirements_eval_set is missing, requirements is stale;")
        self.assertNotIn("rule review not recorded", err)
        self.assertEqual(fake.calls, [])

    def test_a_stale_eval_set_approval_beside_a_current_requirements_approval_is_refused(self):
        self.requirements()
        self.approve("requirements", "eval_set")
        append(os.path.join(self.agent, *SCENARIO.split("/")))
        self.approve("rule_review")
        self.assertEqual((self.verdict("requirements"), self.verdict("eval_set")), ("approved", "stale"))
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "live approvals missing: requirements_eval_set is missing, eval_set is stale;")
        self.assertNotIn("rule review not recorded", err)
        self.assertEqual(fake.calls, [])

    def test_a_requirements_edit_after_the_post_sign_check_is_refused_by_the_plan_hash(self):
        self.requirements()
        self.approve(COMBINED, "rule_review")
        calls, hook = late_edit(lambda: self.requirements("Another goal.\n"))
        with hook:
            rc, out, err, fake = self.run_live()
        self.assertEqual(calls, [1], out + err)
        self.assertEqual(rc, 2, out + err)
        self.refused(err, INPUTS_CHANGED)
        self.assertEqual(fake.calls, [])
        self.assertFalse(se._LIVE["on"])

    def test_a_codex_wrapper_edit_after_the_post_sign_check_is_refused_by_the_plan_hash(self):
        self.requirements()
        self.approve(COMBINED, "rule_review")
        calls, hook = late_edit(lambda: append(codex_wrapper(self.agent), "# late edit\n"))
        with hook:
            rc, out, err, fake = self.run_live()
        self.assertEqual(calls, [1], out + err)
        self.assertEqual(rc, 2, out + err)
        self.refused(err, INPUTS_CHANGED)
        self.assertEqual(fake.calls, [])
        self.assertFalse(se._LIVE["on"])

    def test_a_record_signed_by_a_foreign_key_is_refused(self):
        self.requirements()
        self.approve("rule_review")
        self.approve(COMBINED)
        k = T.shared_keys()
        other, _ = T.make_key(tempfile.mkdtemp(dir=k["dir"]), "intruder", T.TEST_PASSPHRASE)
        path = os.path.join(self.ws, "approvals.json")
        data = rjson(path)
        rec = data[COMBINED]
        signed = A.payload("approve", COMBINED, NAME, rec["sha256"], rec["ruleset_sha256"], rec["at"])
        data[COMBINED] = dict(rec, signature=A.sign_payload(T.keygen_runner(k["dir"]), other, signed,
                                                            passphrase=T.TEST_PASSPHRASE))
        wjson(path, data)
        self.assertEqual(self.verdict(COMBINED), "invalid")
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "live approvals missing: requirements_eval_set is invalid")
        self.assertEqual(fake.calls, [])

    def test_a_record_of_another_unit_is_refused(self):
        other = AE.generated_copy(os.path.join(self.tmp, "others"), name="other-reviewer")
        other_ws = os.path.join(self.tmp, "other-ws")
        self.requirements()
        os.makedirs(other_ws)
        shutil.copyfile(os.path.join(self.ws, "requirements.md"), os.path.join(other_ws, "requirements.md"))
        se.approve(COMBINED, se.agent_spec(se.Agent(other)), other_ws)
        self.approve("rule_review")
        data = rjson(os.path.join(self.ws, "approvals.json"))
        data[COMBINED] = rjson(os.path.join(other_ws, "approvals.json"))[COMBINED]
        wjson(os.path.join(self.ws, "approvals.json"), data)
        self.assertEqual(data[COMBINED]["sha256"], se.gate_hash(COMBINED, self.spec(), self.ws))
        self.assertEqual(self.verdict(COMBINED), "invalid")
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.assertEqual(fake.calls, [])

    def test_a_record_of_another_ruleset_reads_stale(self):
        self.requirements()
        self.approve(COMBINED, "rule_review")
        with mock.patch.object(se, "ruleset_sha256", lambda: "0" * 64):
            v = se.approval_verdict(COMBINED, self.spec(), self.ws)
        self.assertEqual((v.status, v.reason), ("stale", A.RULES_CHANGED))

    def test_a_corrupt_approvals_json_reads_invalid_and_refuses(self):
        self.requirements()
        write(os.path.join(self.ws, "approvals.json"), "{")
        self.assertEqual(self.verdict(COMBINED), "invalid")
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "requirements_eval_set is invalid")
        self.assertNotIn("Traceback", err)


class TestRuleReview(Gates):
    def test_the_agent_review_items_are_the_codex_and_opencode_rows(self):
        self.assertEqual([x.split(":")[0] for x in se.rule_review_items(self.agent)], REVIEW_ROWS)

    def test_a_signed_decision_clears_the_review_rows_and_never_the_hermes_fail_row(self):
        self.approve("rule_review")
        self.assertEqual(se.rule_review_problems(self.spec(), self.ws), [])
        rows = {c["id"]: c["status"] for c in eva.check(self.agent).checks}
        self.assertEqual(rows[HERMES_ROW], "fail")
        self.assertEqual([rows[r] for r in REVIEW_ROWS], ["needs-review", "needs-review"])
        I.fake_iteration(self.agent, self.ws, self.runs)
        out, gate = self.report()
        self.assertTrue(any(p.startswith(HERMES_ROW + ": ") for p in gate["problems"]), gate["problems"])
        self.assertIn(HERMES_ROW, out)

    def test_the_live_start_is_refused_without_a_decision(self):
        self.requirements()
        self.approve(COMBINED)
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, NO_REVIEW, "--gate rule_review")
        self.assertNotIn("live approvals missing", err)
        self.assertEqual(fake.calls, [])

    def test_the_live_start_is_refused_with_a_stale_decision(self):
        self.requirements()
        self.approve(COMBINED, "rule_review")
        append(os.path.join(self.agent, "AGENT.md"), "\nOne more body line.\n")
        regenerate(self.agent)
        self.assertEqual((self.verdict("rule_review"), self.verdict(COMBINED)), ("stale", "approved"))
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 2, out + err)
        self.refused(err, "rule review not recorded (stale): 2 finding(s) need a human decision", "--gate rule_review")
        self.assertNotIn("live approvals missing", err)
        self.assertEqual(fake.calls, [])

    def test_the_decision_reads_stale_after_an_agent_md_or_review_wrapper_edit(self):
        for rel in ("AGENT.md", "wrappers/codex/%s.toml" % NAME, "wrappers/opencode/%s.md" % NAME, SCENARIO):
            with self.subTest(rel):
                p = os.path.join(self.agent, *rel.split("/"))
                before = T.read(p)
                self.approve("rule_review")
                self.assertEqual(self.verdict("rule_review"), "approved")
                append(p, "# edited\n" if rel.endswith((".md", ".toml")) else "\n")
                self.assertEqual(self.verdict("rule_review"), "stale")
                write(p, before)


class TestPlan(Gates):
    def plan(self):
        with mock.patch.object(se, "live_exec", AE.FakeAgentExec()) as fake:
            rc, out, err = AE.cli("agent", "plan", self.agent, "--workspace", self.ws, "--runs", "1")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual((fake.calls, fake.grader_calls), ([], []))
        return out

    def test_an_empty_workspace_names_the_combined_approval_as_missing_and_writes_nothing(self):
        os.makedirs(self.ws)
        out = self.plan()
        self.assertEqual(os.listdir(self.ws), [])
        self.assertEqual(line(out, "combined approval gate: "), "combined approval gate: requirements_eval_set=NOT "
                         "APPROVED (the live gate needs it, or both requirements and eval_set)")
        self.assertEqual(line(out, "approval gates: "), "approval gates: requirements=NOT APPROVED, eval_set=NOT APPROVED")
        self.assertEqual(line(out, "rule review (English-only / parity needs-review findings): "),
                         "rule review (English-only / parity needs-review findings): NOT RECORDED (blocks agent run "
                         "--live and agent loop --live)")
        self.assertEqual(line(out, "coverage waivers: "), "coverage waivers: " + ", ".join(
            "%s=NOT APPROVED" % t for t in se.COVERAGE_REQUIRED))
        self.assertEqual(line(out, "machine waiver: "), "machine waiver: missing (~/.config/skill-master/machine-waiver.json)")
        self.assertEqual(line(out, GATES_LINE), GATES_LINE + "rule-review: fail: rule_review missing (2 finding(s)); "
                         "approvals: fail: requirements_eval_set missing, requirements missing, eval_set missing")

    def test_the_plan_reads_approved_after_signing_and_keeps_every_earlier_line_first(self):
        self.requirements()
        self.approve(COMBINED, "rule_review", "coverage_waiver:codex")
        out = self.plan()
        self.assertIn("combined approval gate: requirements_eval_set=approved by %s" % T.TEST_PRINCIPAL, out)
        self.assertIn("rule review (English-only / parity needs-review findings): RECORDED (signed by %s)"
                      % T.TEST_PRINCIPAL, out)
        self.assertIn("codex=approved by %s" % T.TEST_PRINCIPAL, line(out, "coverage waivers: "))
        self.assertEqual(line(out, GATES_LINE), GATES_LINE + "rule-review: pass; approvals: pass")
        lines = out.splitlines()
        last_old = lines.index(line(out, "multi-turn: "))
        for prefix in ("rule review (", "coverage waivers: ", "machine waiver: ", "combined approval gate: ",
                       "approval gates: ", GATES_LINE):
            self.assertGreater(lines.index(line(out, prefix)), last_old, prefix)
        for prefix in ("agent: ", "workspace: ", "scenarios: ", "engines: ", "cell overview claude: ", "estimate: ",
                       "grader: ", "launch claude: ", "budget: ", "tool coverage (dynamic): ", "registration coverage: "):
            self.assertLess(lines.index(next(l for l in lines if l.startswith(prefix))), last_old, prefix)

    def test_a_machine_waiver_shows_in_the_coverage_line(self):
        se.sign_machine_waiver(["opencode", "hermes"])
        out = self.plan()
        cov = line(out, "coverage waivers: ")
        for tool in ("opencode", "hermes"):
            self.assertIn("%s=machine-waived by %s" % (tool, T.TEST_PRINCIPAL), cov)
        for tool in ("claude", "codex"):
            self.assertIn("%s=NOT APPROVED" % tool, cov)

    def test_a_corrupt_approvals_json_reads_invalid_in_the_plan(self):
        self.requirements()
        write(os.path.join(self.ws, "approvals.json"), "[")
        out = self.plan()
        self.assertIn("requirements_eval_set=NOT APPROVED (invalid: ", out)


class TestPendingAndBatch(Gates):
    def test_pending_lists_the_agent_combined_and_rule_review_items_and_the_batch_signs_their_hashes(self):
        self.requirements()
        items = se.pending_items(se.unit_pairs([self.agent], [self.ws]))
        self.assertEqual([(i["gate"], i["kind"], i["status"]) for i in items],
                         [(COMBINED, "agent", "missing"), ("rule_review", "agent", "missing")])
        for i in items: self.assertEqual(i["sha256"], se.gate_hash(i["gate"], self.spec(), self.ws))
        rc, out, err = AE.cli("approve", "--batch", "--unit", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 0, out + err)
        recs = rjson(os.path.join(self.ws, "approvals.json"))
        for i in items:
            self.assertEqual(recs[i["gate"]]["sha256"], i["sha256"])
            self.assertEqual(self.verdict(i["gate"]), "approved")
        self.assertEqual(se.pending_items(se.unit_pairs([self.agent], [self.ws])), [])
        rc, out, err, fake = self.run_live()
        self.assertEqual(rc, 0, out + err)


class TestWaivers(Gates):
    def test_an_unsigned_agent_waiver_leaves_coverage_missing(self):
        I.fake_iteration(self.agent, self.ws, self.runs)
        data = {"coverage_waiver:codex": {"sha256": se.gate_hash("coverage_waiver:codex", self.spec(), self.ws),
                                          "reason": "codex is not installed here", "principal": T.TEST_PRINCIPAL}}
        wjson(os.path.join(self.ws, "approvals.json"), data)
        self.assertEqual(self.verdict("coverage_waiver:codex"), "unsigned")
        out, gate = self.report()
        self.assertIn(T.coverage_missing("codex", T.NOT_SELECTED, "unsigned"), gate["problems"])

    def test_a_signed_agent_waiver_clears_only_its_tool_with_its_typed_reason_and_binds_its_inputs(self):
        I.fake_iteration(self.agent, self.ws, self.runs)
        self.approve("coverage_waiver:codex")
        out, gate = self.report()
        self.assertEqual(gate["coverage"]["codex"], "waived by %s" % T.TEST_PRINCIPAL)
        self.assertFalse(any(p.startswith("coverage-missing:codex") for p in gate["problems"]), gate["problems"])
        for tool in ("opencode", "hermes"):
            self.assertTrue(any(p.startswith("coverage-missing:%s" % tool) for p in gate["problems"]), tool)
        w = se.waiver_verdicts(self.spec(), self.ws)["codex"]
        self.assertEqual((w["status"], w["source"], w["reason"]), ("approved", "unit", T.TEST_REASON))
        for rel in ("AGENT.md", SCENARIO):
            with self.subTest(rel):
                p = os.path.join(self.agent, *rel.split("/"))
                before = T.read(p)
                append(p)
                self.assertEqual(self.verdict("coverage_waiver:codex"), "stale")
                write(p, before)
        self.assertEqual(self.verdict("coverage_waiver:codex"), "approved")
        with mock.patch.object(se, "ruleset_sha256", lambda: "0" * 64):
            self.assertEqual(se.approval_verdict("coverage_waiver:codex", self.spec(), self.ws).status, "stale")

    def test_the_machine_waiver_clears_opencode_and_hermes_and_never_claude_or_codex(self):
        with self.assertRaises(A.Refused):
            se.sign_machine_waiver(["claude"])
        with self.assertRaises(A.Refused):
            se.sign_machine_waiver(["codex", "hermes"])
        se.sign_machine_waiver(["opencode", "hermes"])
        I.fake_iteration(self.agent, self.ws, self.runs)
        out, gate = self.report()
        missing = sorted(p.split(":")[1] for p in gate["problems"] if p.startswith("coverage-missing:"))
        self.assertEqual(missing, ["codex"])
        for tool in ("opencode", "hermes"):
            self.assertTrue(gate["coverage"][tool].startswith("waived by machine policy "), gate["coverage"])
        self.assertIn("machine waiver: approved by %s" % T.TEST_PRINCIPAL, out)


def claude_and_codex_iteration(agent_dir, ws, run_root):
    from engine import agent_eval
    agent = se.Agent(agent_dir)
    engines = [se.AgentClaudeEngine(model=se.DEFAULT_EXECUTOR_MODELS["claude"], exec_fn=AE.FakeAgentExec()),
               se.AgentCodexEngine(model=se.DEFAULT_EXECUTOR_MODELS["codex"], exec_fn=AE.FakeAgentExec())]
    prices = se.parse_price_overrides(["codex=1,10"])
    n = se.agent_run_iteration(agent, None, ws, engines, se.agent_cells(agent, engines), 1, se.Budget(5.0), prices,
                               se.DEFAULT_TOKENS, run_root, log=T.quiet)
    agent_eval.grade_runs(os.path.join(ws, "iteration-%d" % n), agent.dir, agent.scenarios,
                          lambda d, p: eva.grade_transcript(d, p).checks, log=T.quiet)
    return n


class TestRegistration(Gates):
    def setUp(self):
        Gates.setUp(self)
        self.ws = os.path.join(self.tmp, "agents", NAME + "-workspace")

    def named_iteration(self):
        claude_and_codex_iteration(self.agent, self.ws, self.runs)
        se.sign_machine_waiver(["opencode", "hermes"])
        out, gate = self.report()
        return gate

    def rewrite_gate(self, gate):
        it = os.path.join(self.ws, "iteration-1")
        wjson(os.path.join(it, "benchmark.json"), dict(rjson(os.path.join(it, "benchmark.json")), gate=gate))
        wjson(os.path.join(it, "gate.json"), gate)

    def test_the_record_command_refuses_a_signed_gate_that_omits_a_current_parity_fail_row(self):
        gate = self.named_iteration()
        older = [p for p in gate["problems"] if not p.startswith(HERMES_ROW + ": ")]
        self.assertEqual(len(older), len(gate["problems"]) - 1, gate["problems"])
        self.rewrite_gate(dict(gate, problems=older))
        self.approve("registration")
        self.assertEqual(self.verdict("registration"), "approved")
        rc, out, err = AE.cli("register", self.agent)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("does not name the agent's parity FAIL row(s) (%s: " % HERMES_ROW, err)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "registration.json")))

    def test_an_agent_gate_with_problems_never_registers_as_auto(self):
        gate = self.named_iteration()
        for label, g, refusal in (
                ("a gate that names nothing", dict(gate, problems=[], needs_approval=False),
                 "does not name the agent's parity FAIL row(s) (%s: " % HERMES_ROW),
                ("a gate whose problems are not flagged", dict(gate, needs_approval=False),
                 "registration needs `approve --gate registration` (missing)")):
            with self.subTest(label):
                self.rewrite_gate(g)
                rc, out, err = AE.cli("register", self.agent)
                self.assertEqual(rc, 2, out + err)
                self.assertIn(refusal, err)
                self.assertFalse(os.path.exists(os.path.join(self.ws, "registration.json")))

    def test_the_hub_page_shows_the_findings_the_registration_approval_named(self):
        from engine import hub
        gate = self.named_iteration()
        self.approve("registration")
        rc, out, err = AE.cli("register", self.agent)
        self.assertEqual(rc, 0, out + err)
        root, page_dir = os.path.join(self.tmp, "agents"), tempfile.mkdtemp(dir=self.tmp)
        rc, out, err = AE.cli("hub", "build", root, "--out", page_dir)
        self.assertEqual(rc, 0, out + err)
        page = T.read(os.path.join(page_dir, "units", NAME + ".html"))
        block = re.search(r'<h2>Findings the registration approval named</h2>\n<ul class="findings">\n(.*?)\n</ul>', page,
                          re.S)
        self.assertIsNotNone(block, page)
        named = re.findall(r"<li>([^<]*)</li>", block.group(1))
        self.assertEqual(named, [p.split(": ", 1)[0] for p in gate["problems"]])
        self.assertIn(HERMES_ROW, named)
        self.assertIn(CODEX_FINDING, named)
        row = next(r for r in eva.build_index(root)[0] if r["path"] == NAME)
        view = dict(se.unit_view(root, row), findings=['run-error:a<b&"c'])
        self.assertIn("<li>run-error:a&lt;b&amp;&quot;c</li>", hub.unit_page(view, eva.TOOL_LABELS,
                                                                            hub.load_templates(None)["unit.html"]))

    def test_the_registration_approval_names_the_hermes_and_codex_findings(self):
        gate = self.named_iteration()
        named = [p.split(":")[0] if p.startswith(HERMES_ROW) else ":".join(p.split(":")[:2]) for p in gate["problems"]]
        self.assertIn(HERMES_ROW, named)
        self.assertIn(CODEX_FINDING, named)
        self.assertFalse(any(p.startswith("coverage-missing:") for p in gate["problems"]), gate["problems"])
        self.approve("registration")
        bench = rjson(os.path.join(self.ws, "iteration-1", "benchmark.json"))
        self.assertEqual(bench["gate"]["problems"], gate["problems"])
        rc, out, err = AE.cli("register", self.agent)
        self.assertEqual(rc, 0, out + err)
        rec = rjson(os.path.join(self.ws, "registration.json"))
        self.assertEqual((rec["kind"], rec["registration_format"], rec["version"]), ("agent", 2, "1.0.0"))
        self.assertNotEqual(rec["approval"], "auto")
        self.assertEqual(rec["benchmark_sha256"], se.sha256_files(os.path.join(self.ws, "iteration-1", "benchmark.json")))
        self.assertIn("registration", rec["approvals"])
        self.assertIn("machine_waiver", rec)

    def test_the_approval_shows_the_problems_of_the_signed_benchmark(self):
        gate = self.named_iteration()
        gp = os.path.join(self.ws, "iteration-1", "gate.json")
        wjson(gp, dict(gate, problems=gate["problems"][:1]))
        shown = se.approve_show("registration", self.spec(), self.ws, se.ruleset_sha256())
        for p in gate["problems"]: self.assertIn(p, shown[0])
        items = [i for i in se.pending_items(se.unit_pairs([self.agent], [self.ws])) if i["gate"] == "registration"]
        self.assertEqual([x for _, x in items[0]["review"]], gate["problems"])

    def test_the_record_command_refuses_a_problem_the_approval_did_not_see(self):
        self.named_iteration()
        self.approve("registration")
        pp = os.path.join(self.ws, "iteration-1", "plan.json")
        wjson(pp, dict(rjson(pp), stopped="a problem the approver never saw"))
        out, gate = self.report()
        self.assertIn("stopped: a problem the approver never saw", gate["problems"])
        self.assertEqual(self.verdict("registration"), "stale")
        rc, out, err = AE.cli("register", self.agent)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("registration needs `approve --gate registration` (stale", err)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "registration.json")))

    def test_the_record_command_refuses_without_a_signature(self):
        self.named_iteration()
        rc, out, err = AE.cli("register", self.agent)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("registration needs `approve --gate registration` (missing)", err)
        wjson(os.path.join(self.ws, "approvals.json"),
              {"registration": {"sha256": se.gate_hash("registration", self.spec(), self.ws)}})
        rc, out, err = AE.cli("register", self.agent)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("(unsigned", err)
        os.remove(os.path.join(self.ws, "approvals.json"))
        self.approve("registration")
        with mock.patch.object(A, "is_tty", lambda fd: False):
            rc, out, err = AE.cli("register", self.agent)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("is not a TTY", err)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "registration.json")))

    def test_a_kind_agent_record_verifies_and_reads_stale_after_an_agent_md_edit(self):
        self.named_iteration()
        self.approve("registration")
        rc, out, err = AE.cli("register", self.agent)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("registered %s 1.0.0 iteration-1 (approved)" % NAME, out)
        self.assertEqual(se.registration_state(self.agent, self.ws)[:2], ("registered", ""))
        append(os.path.join(self.agent, "AGENT.md"), "\nOne more line.\n")
        self.assertEqual(se.registration_state(self.agent, self.ws)[:2], ("stale", "skill_sha256 changed"))

    def test_index_and_hub_show_the_record_with_the_codex_waiver_and_the_machine_waiver(self):
        I.fake_iteration(self.agent, self.ws, self.runs)
        self.approve("coverage_waiver:codex")
        mw = se.sign_machine_waiver(["opencode", "hermes"])
        out, gate = self.report()
        self.assertTrue(any(p.startswith(HERMES_ROW + ": ") for p in gate["problems"]), gate["problems"])
        self.approve("registration")
        rc, out, err = AE.cli("register", self.agent)
        self.assertEqual(rc, 0, out + err)
        root = os.path.join(self.tmp, "agents")
        rows, _ = eva.build_index(root)
        row = next(r for r in rows if r["path"] == NAME)
        self.assertEqual(row["registration"], "registered")
        page_dir = tempfile.mkdtemp(dir=self.tmp)
        rc, out, err = AE.cli("hub", "build", root, "--out", page_dir)
        self.assertEqual(rc, 0, out + err)
        page = T.read(os.path.join(page_dir, "units", NAME + ".html"))
        self.assertEqual(I.badge(page, "registration"), "registered 1.0.0 by %s" % T.TEST_PRINCIPAL)
        self.assertIn("<li>coverage_waiver:codex by %s" % T.TEST_PRINCIPAL, page)
        self.assertIn(T.TEST_REASON, page)
        self.assertIn(A.machine_waiver_sha256(mw), page)


class TestLoopGate(L.Cli):
    def setUp(self):
        L.Cli.setUp(self)
        pt = mock.patch.dict(se.MACHINE, {"id": lambda: MACHINE}); pt.start(); self.addCleanup(pt.stop)

    def test_without_approvals_the_loop_is_refused_before_signing(self):
        fake = L.LoopExec(L.allowed)
        rc, out, err = self.live(fake, "--max-iterations", "2")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("agent loop --live refused: ", err)
        self.assertIn(NO_REVIEW, err)
        self.assertIn(NO_APPROVALS, err)
        self.assertEqual((fake.calls, fake.builder_calls), ([], []))
        self.assertFalse(os.path.exists(self.ws))

    def test_a_body_edit_by_the_builder_keeps_the_loop_going_under_the_signed_findings(self):
        AE.open_gates(self.agent, self.ws)
        fake = L.LoopExec(L.allowed)
        rc, out, err = self.live(fake, "--max-iterations", "2")
        self.assertEqual(rc, 3, out + err)
        self.assertEqual(json.loads(out.strip().splitlines()[-1]), {"status": "needs-approval", "iteration": 2})
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(se.approval_verdict("rule_review", se.agent_spec(se.Agent(self.agent)), self.ws).status, "stale")

    def test_a_capability_edit_by_the_builder_stops_the_loop(self):
        AE.open_gates(self.agent, self.ws)
        fake = L.LoopExec(lambda d, k: set_capabilities(d, "read, search, write"))
        rc, out, err = self.live(fake, "--max-iterations", "3")
        self.assertEqual(rc, 3, out + err)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertIn(r["status"], ("builder-review-required", "builder-parity-missing"), r)
        self.assertEqual(len(fake.calls), 1)

    def test_a_capability_edit_by_another_writer_during_a_run_stops_the_next_iteration(self):
        AE.open_gates(self.agent, self.ws)

        def outside(k):
            if k == 1:
                set_capabilities(self.agent, "read, search, write")
                regenerate(self.agent)
        fake = L.LoopExec(L.allowed, on_run=outside)
        rc, out, err = self.live(fake, "--max-iterations", "3")
        self.assertEqual(rc, 3, out + err)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((r["status"], r["iteration"]), ("gates-failed", 1), r)
        self.assertTrue(any(p.startswith("rule review finding changed since signing: ") for p in r["problems"]), r)
        self.assertEqual(len(fake.calls), 1)

    def test_a_decision_made_stale_between_signing_and_iteration_1_stops_the_loop_before_any_run(self):
        AE.open_gates(self.agent, self.ws)
        real = se.agent_loop

        def edit_then_loop(*a, **k):
            append(os.path.join(self.agent, "AGENT.md"), "\nOne more body line.\n")
            regenerate(self.agent)
            return real(*a, **k)
        fake = L.LoopExec(L.allowed)
        with mock.patch.object(se, "agent_loop", edit_then_loop):
            rc, out, err = self.live(fake, "--max-iterations", "2")
        self.assertEqual(rc, 3, out + err)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((r["status"], r["iteration"]), ("gates-failed", None), r)
        self.assertTrue(any(p.startswith("rule review not recorded (stale)") for p in r["problems"]), r)
        self.assertEqual((fake.calls, fake.builder_calls), ([], []))

    def late_loop(self, edit):
        calls, hook = late_edit(edit)
        fake = L.LoopExec(L.allowed)
        with hook:
            rc, out, err = self.live(fake, "--max-iterations", "2")
        self.assertEqual(calls, [1], out + err)
        self.assertEqual(rc, 2, out + err)
        self.assertIn(INPUTS_CHANGED, err)
        self.assertEqual((fake.calls, fake.builder_calls), ([], []))
        self.assertFalse(se._LIVE["on"])

    def test_a_requirements_edit_after_the_post_sign_check_is_refused_by_the_loop_plan_hash(self):
        AE.open_gates(self.agent, self.ws)
        self.late_loop(lambda: write(os.path.join(self.ws, "requirements.md"), "Other goal.\n"))

    def test_a_codex_wrapper_edit_after_the_post_sign_check_is_refused_by_the_loop_plan_hash(self):
        AE.open_gates(self.agent, self.ws)
        self.late_loop(lambda: append(codex_wrapper(self.agent), "# late edit\n"))

    def test_a_requirements_edit_during_a_run_stops_the_next_iteration(self):
        AE.open_gates(self.agent, self.ws)
        fake = L.LoopExec(L.allowed, on_run=lambda k: write(os.path.join(self.ws, "requirements.md"), "Other.\n")
                          if k == 1 else None)
        rc, out, err = self.live(fake, "--max-iterations", "3")
        self.assertEqual(rc, 3, out + err)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((r["status"], r["iteration"]), ("gates-failed", 1), r)
        self.assertTrue(any("requirements_eval_set is stale" in p for p in r["problems"]), r)


class TestHelp(unittest.TestCase):
    def test_the_signing_record_and_agent_help_name_the_agent_gates(self):
        for argv, words in ((["--help"], ("requirements_eval_set", "rule_review")),
                            (["approve", "--help"], ("agent folder", "requirements_eval_set", "rule_review")),
                            (["register", "--help"], ("kind agent", "registration approval")),
                            (["agent", "--help"], ("requirements_eval_set", "rule_review")),
                            (["agent", "run", "--help"], ("requirements_eval_set", "rule_review")),
                            (["agent", "loop", "--help"], ("requirements_eval_set", "rule_review"))):
            with self.subTest(argv):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                    se.build_parser().parse_args(argv)
                text = " ".join(out.getvalue().split())
                self.assertTrue(all(ord(c) < 128 for c in text))
                for w in words: self.assertIn(w, text)


if __name__ == "__main__":
    unittest.main()
