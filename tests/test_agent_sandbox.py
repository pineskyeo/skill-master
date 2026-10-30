"""Unit K2: an agent pins registered skills (AGENT.md `skills`, valid only with the `skill` capability), a scenario
`fixture` is copied into the agent sandbox work folder, the sandbox holds exactly the wrapper, the fixture files and the
pinned skills, the criteria command_ran, command_absent and file_exists grade recorded Claude and Codex rows, and the
pins and fixture files bind the gate hashes, the iteration record and the registration.

Every engine is faked: subprocess is patched to fail for the whole module, and approvals and registrations are
recorded by the test approver of tests/test_skill_eval.py in temp folders (tests/test_registry.py Reg). ASCII only."""
import contextlib, hashlib, io, json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_registry as R
import test_agent_eval as AE
from test_skill_eval import se, eva, read
from engine import agent_eval as AEV, registry as REG
from engine.primitives import transcript as TP

NAME = AE.NAME
SKILL = "branch-setup"
PIN = SKILL + "@1.0.0"
RULE = "skills-need-skill-capability"
NOTES = "Release notes: version 7 ships the parser.\n"
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden)
        pt.start()
        _patches.append(pt)


def tearDownModule():
    for pt in _patches:
        pt.stop()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def agent_text(caps=("read", "search", "shell", "skill"), pins=(PIN,), criteria=()):
    text = read(os.path.join(AE.AGENT_FIXTURE, "AGENT.md"))
    text = text.replace("capabilities: [read, search]", "capabilities: [%s]" % ", ".join(caps))
    extra = "".join("  - '%s'\n" % json.dumps(c) for c in criteria)
    tail = "targets: [claude, codex, opencode, hermes]\n"
    pins_block = ("skills:\n" + "".join("  - %s\n" % p for p in pins)) if pins is not None else ""
    return text.replace(tail, extra + tail + pins_block)


def make_agent(parent, caps=("read", "search", "shell", "skill"), pins=(PIN,), fixture=None, slug="smoke",
               generate=True):
    """repo/agents/<NAME> from the B3 fixture agent with the given capabilities and pins; fixture: {rel: text} under
    fixtures/<slug>/, named by the overview scenario."""
    d = os.path.join(parent, NAME)
    shutil.copytree(AE.AGENT_FIXTURE, d)
    write(os.path.join(d, "AGENT.md"), agent_text(caps, pins))
    if fixture is not None:
        for rel, text in fixture.items():
            write(os.path.join(d, "fixtures", slug, *rel.split("/")), text)
        p = os.path.join(d, "scenarios", "01-overview.json")
        doc = AE.rjson(p)
        doc["fixture"] = slug
        AE.wjson(p, doc)
    if generate:
        with contextlib.redirect_stdout(io.StringIO()):
            assert eva.main(["generate", d]) == 0
    return d


def rows(unit, rid):
    return [c for c in eva.check(unit).checks if c["id"] == rid]


class TestSkillsKeyNeedsSkillCapability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_a_skills_key_without_the_skill_capability_fails_its_rule_and_the_agent_commands(self):
        d = make_agent(os.path.join(self.tmp, "gap"), caps=("read", "search"))
        got = rows(d, RULE)
        self.assertEqual([r["status"] for r in got], ["fail"], got)
        self.assertEqual(eva.check(d).verdict, "FAIL")
        rc, out, err = AE.cli("agent", "validate", d)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("skills capability: fail", out)
        rc, out, err = AE.cli("agent", "plan", d, "--workspace", os.path.join(self.tmp, "ws"))
        self.assertEqual(rc, 2, out + err)
        self.assertIn(RULE, out + err)

    def test_the_rule_passes_with_the_capability_and_is_na_without_the_key(self):
        d = make_agent(os.path.join(self.tmp, "ok"))
        self.assertEqual([r["status"] for r in rows(d, RULE)], ["pass"])
        plain = make_agent(os.path.join(self.tmp, "plain"), caps=("read", "search"), pins=None)
        self.assertEqual([r["status"] for r in rows(plain, RULE)], ["n/a"])
        empty = make_agent(os.path.join(self.tmp, "empty"), caps=("read", "search"), pins=None, generate=False)
        write(os.path.join(empty, "AGENT.md"), read(os.path.join(empty, "AGENT.md")).replace(
            "targets: [claude, codex, opencode, hermes]\n", "targets: [claude, codex, opencode, hermes]\nskills:\n"))
        self.assertEqual([r["status"] for r in rows(empty, RULE)], ["fail"])

    def test_the_schema_accepts_only_name_at_semver_pins(self):
        for pins, ok in (([PIN], True), (["a@1.0.0", "b@2.1.0-rc.1"], True), ([SKILL], False), (["Bad@1.0.0"], False),
                         (["x@1.0"], False), (["x@v1.0.0"], False), (["../x@1.0.0"], False), ([PIN, PIN], False),
                         ([], False)):
            agent = TP.parse_agent(agent_text(pins=pins))
            self.assertEqual(agent.valid, ok, (pins, agent.problems))

    def test_parse_pin(self):
        self.assertEqual(AEV.parse_pin(PIN), (SKILL, "1.0.0"))
        self.assertEqual(AEV.parse_pin("a.b@1.2.3-rc.1+b5"), ("a.b", "1.2.3-rc.1+b5"))
        for bad in ("x", "x@", "@1.0.0", "x@1.0", "x@1.0.0@1.0.0", "a..b@1.0.0", "X@1.0.0", None, 5):
            self.assertIsNone(AEV.parse_pin(bad), bad)


class Pinned(R.Reg):
    """R.Reg (temp home, temp ruleset root, the test approver, repo/skills/branch-setup at 1.0.0) with the skill
    registered, plus repo/agents/<NAME> pinning it with a one-file fixture."""
    fixture = {"notes.txt": NOTES, "data/input.csv": "a,b\n1,2\n"}

    def setUp(self):
        R.Reg.setUp(self)
        self.registered()
        self.assertEqual(self.state()[0], "registered")
        self.agent = make_agent(os.path.join(self.repo, "agents"), fixture=self.fixture)
        self.aws = os.path.join(self.tmp, "agent-ws")

    def plan(self, agent=None):
        return AE.cli("agent", "plan", agent or self.agent, "--workspace", self.aws, "--engines", "claude")

    def live(self, *extra, fake=None, agent=None):
        agent = agent or self.agent
        fake = fake or AE.FakeAgentExec(claude=AE.read(AE.CLAUDE_STREAM).replace('"skills":[]', '"skills":["%s"]' % SKILL))
        AE.open_gates(agent, self.aws)
        with mock.patch.object(se, "live_exec", fake):
            rc, out, err = AE.cli("agent", "run", agent, "--workspace", self.aws, "--live", "--auth-profile",
                                  "personal-mac", "--budget-usd", "1", "--runs", "1", *extra)
        return rc, out, err, fake

    def skill_files(self, rel_root):
        tree = eva.unit_tree(self.d)
        return {"%s/%s/%s" % (rel_root, SKILL, rel) for rel in tree.files}

    def after_live_start(self, action):
        """The test approver, patched to call action() right after it records the live start."""
        real = se.APPROVER["it"]

        class Signer(object):
            def __getattr__(self, k):
                return getattr(real, k)

            def sign(self, kind, gate, prepare, budget_usd=None, reason=None):
                grant = real.sign(kind, gate, prepare, budget_usd=budget_usd, reason=reason)
                if kind == "live":
                    action()
                return grant
        return mock.patch.dict(se.APPROVER, {"it": Signer()})

    def recorded_agent(self, folder):
        """repo/<folder>/<NAME> at version 1.0.0, pinning the skill, with its registration recorded."""
        import test_install as I
        d = make_agent(os.path.join(self.repo, folder), fixture=self.fixture, generate=False)
        write(os.path.join(d, "AGENT.md"), read(os.path.join(d, "AGENT.md")).replace(
            "targets: [", "version: 1.0.0\ntargets: [", 1))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eva.main(["generate", d]), 0)
        ws = REG.agent_workspace(d)
        I.register_agent_at(self, d, ws, self.runs)
        self.assertEqual(se.registration_state(d, ws)[:2], ("registered", ""))
        return d, ws


class TestPinResolution(Pinned):
    def test_plan_lists_the_pin_at_its_version_and_the_fixture_files_with_their_hash(self):
        rc, out, err = self.plan()
        self.assertEqual(rc, 0, out + err)
        reg = os.path.join(self.ws, "registration.json")
        self.assertIn("pin %s: registered, registration sha256 %s (%s)" % (PIN, sha(reg), os.path.realpath(self.d)), out)
        fx = os.path.join(self.agent, "fixtures", "smoke")
        files = [("data/input.csv", os.path.join(fx, "data", "input.csv")), ("notes.txt", os.path.join(fx, "notes.txt"))]
        self.assertIn("fixture overview smoke: sha256 %s, 2 file(s)" % se.sha256_labeled(files), out)
        for rel, p in files:
            self.assertIn("fixture file overview smoke/%s: sha256 %s" % (rel, sha(p)), out)
        self.assertIn("DRY RUN", out)

    def test_an_unregistered_pin_refuses_the_plan_naming_the_pin(self):
        d = make_agent(os.path.join(self.tmp, "nope", "agents"), pins=("nope@1.0.0",))
        rc, out, err = self.plan(d)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("agent plan refused: pin nope@1.0.0 does not verify", out)
        self.assertNotIn("DRY RUN", out)
        self.assertFalse(os.path.exists(self.aws))

    def pin_reason(self, pins, agent_parent=None):
        d = make_agent(agent_parent, pins=pins, generate=False)
        states = se.pin_states(se.Agent(d))
        return [s for s in states if not s["ok"]]

    def test_a_pin_at_another_version_or_spelling_or_twice_does_not_verify(self):
        bad = self.pin_reason((SKILL + "@1.0.1",), os.path.join(self.repo, "agents-v"))
        self.assertEqual(len(bad), 1)
        self.assertIn("registered version is 1.0.0", bad[0]["reason"])
        bad = self.pin_reason((SKILL + "@1.0.0+build.1",), os.path.join(self.repo, "agents-b"))
        self.assertEqual(len(bad), 1)
        bad = self.pin_reason((PIN, SKILL + "@2.0.0"), os.path.join(self.repo, "agents-twice"))
        self.assertTrue(bad and all("pinned twice" in s["reason"] for s in bad), bad)

    def test_a_pin_to_a_stale_or_unsigned_or_agent_record_does_not_verify(self):
        write(os.path.join(self.d, "SKILL.md"), R.skill_text("1.0.0", R.BODY + "\nEdited after the registration.\n"))
        self.assertEqual(self.state()[0], "stale")
        bad = self.pin_reason((PIN,), os.path.join(self.repo, "agents-stale"))
        self.assertIn("stale", bad[0]["reason"])
        R.Reg.set_version(self, "1.0.0")
        self.assertEqual(self.state()[0], "registered")
        rec = R.rjson(os.path.join(self.ws, "registration.json"))
        os.rename(os.path.join(self.ws, "registration.json"), os.path.join(self.tmp, "kept.json"))
        bad = self.pin_reason((PIN,), os.path.join(self.repo, "agents-unreg"))
        self.assertIn("unregistered", bad[0]["reason"])
        self.assertEqual(rec["kind"], "skill")
        fake = os.path.join(self.repo, "skills", "helper")
        shutil.copytree(AE.AGENT_FIXTURE, fake)
        bad = self.pin_reason(("helper@1.0.0",), os.path.join(self.repo, "agents-kind"))
        self.assertIn("not a skill folder", bad[0]["reason"])

    def test_a_pin_to_a_deprecated_version_does_not_verify(self):
        rc, out, err = AE.cli("deprecate", self.d, "--version", "1.0.0", "--reason", "replaced by 2.0.0")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.state()[0], "registered")
        bad = self.pin_reason((PIN,), os.path.join(self.repo, "agents-dep"))
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("deprecated: replaced by 2.0.0", bad[0]["reason"])

    def test_a_pinned_skill_edited_after_the_live_start_is_recorded_is_refused(self):
        text = R.skill_text("1.0.0", R.BODY + "\nEdited after the start.\n")
        with self.after_live_start(lambda: write(os.path.join(self.d, "SKILL.md"), text)):
            rc, out, err, fake = self.live("--engines", "claude")
        self.assertEqual(rc, 2, out + err)
        self.assertIn(PIN, err)
        self.assertEqual(fake.calls, [])
        self.assertFalse(se._LIVE["on"])

    def test_a_pinned_skill_re_recorded_at_the_same_version_after_the_live_start_is_refused(self):
        reg = os.path.join(self.ws, "registration.json")
        before = sha(reg)
        with self.after_live_start(self.registered):
            rc, out, err, fake = self.live("--engines", "claude")
        self.assertNotEqual(sha(reg), before)
        self.assertTrue(all(s["ok"] for s in se.pin_states(se.Agent(self.agent))))
        self.assertEqual(rc, 2, out + err)
        self.assertEqual(fake.calls, [])
        self.assertFalse(se._LIVE["on"])

    def test_a_fixtures_folder_linked_outside_the_agent_is_refused_by_plan_and_the_live_run(self):
        fixtures = os.path.join(self.agent, "fixtures")
        outside = os.path.join(self.tmp, "outside-fixtures")
        os.rename(fixtures, outside)
        os.symlink(outside, fixtures)
        rc, out, err = self.plan()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("agent plan refused: fixture smoke of scenario overview: fixtures/smoke/notes.txt resolves "
                      "outside the agent folder", out)
        self.assertNotIn("DRY RUN", out)
        rc, out, err, fake = self.live("--engines", "claude")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("fixtures/smoke/notes.txt resolves outside the agent folder", err)
        self.assertEqual(fake.calls, [])

    def test_a_same_named_user_level_skill_refuses_the_live_run(self):
        planted = os.path.join(self.home, ".claude", "skills", SKILL)
        write(os.path.join(planted, "SKILL.md"), T.SKILL_MD)
        rc, out, err, fake = self.live("--engines", "claude")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("pinned skill %r is also installed" % SKILL, err)
        self.assertEqual(fake.calls, [])

    def test_agent_loop_without_live_refuses_an_unverified_pin(self):
        d = make_agent(os.path.join(self.tmp, "nope3", "agents"), pins=("nope@1.0.0",))
        rc, out, err = AE.cli("agent", "loop", d, "--workspace", self.aws, "--engines", "claude")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("agent plan refused: pin nope@1.0.0 does not verify", out)
        self.assertNotIn("DRY RUN", out)
        self.assertFalse(os.path.exists(self.aws))

    def test_agent_grade_live_refuses_a_pin_that_no_longer_verifies(self):
        rc, out, err, _ = self.live("--engines", "claude")
        self.assertEqual(rc, 0, out + err)
        write(os.path.join(self.d, "SKILL.md"), R.skill_text("1.0.0", R.BODY + "\nEdited after the run.\n"))
        fake = AE.FakeAgentExec()
        with mock.patch.object(se, "live_exec", fake):
            rc, out, err = AE.cli("agent", "grade", self.agent, "--workspace", self.aws, "--live", "--auth-profile",
                                  "personal-mac", "--budget-usd", "1")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("pin %s does not verify" % PIN, err)
        self.assertEqual(fake.calls, [])

    def test_the_live_run_refuses_a_skills_key_without_the_skill_capability(self):
        d = make_agent(os.path.join(self.repo, "agents-gap"), caps=("read", "search", "shell"))
        rc, out, err, fake = self.live("--engines", "claude", agent=d)
        self.assertEqual(rc, 2, out + err)
        self.assertIn(RULE, err)
        self.assertEqual(fake.calls, [])

    def test_a_pin_to_a_skill_that_is_not_user_invocable_is_refused_before_launch(self):
        write(os.path.join(self.d, "SKILL.md"), R.skill_text("1.1.0").replace(
            "metadata:\n", "user-invocable: false\nmetadata:\n", 1))
        self.registered()
        self.assertEqual(self.state()[0], "registered")
        pin = SKILL + "@1.1.0"
        d = make_agent(os.path.join(self.repo, "agents-hidden"), pins=(pin,))
        rc, out, err = self.plan(d)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("agent plan refused: pin %s does not verify: user-invocable: false" % pin, out)
        self.assertNotIn("DRY RUN", out)
        rc, out, err, fake = self.live("--engines", "claude", agent=d)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("pin %s does not verify: user-invocable: false" % pin, err)
        self.assertEqual(fake.calls, [])

    def test_the_live_run_refuses_an_unverified_pin_before_recording(self):
        d = make_agent(os.path.join(self.tmp, "nope2", "agents"), pins=("nope@1.0.0",))
        rc, out, err, fake = self.live("--engines", "claude", agent=d)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("nope@1.0.0", err)
        self.assertEqual(fake.calls, [])
        self.assertFalse(se._LIVE["on"])


class TestPinnedSandbox(Pinned):
    def test_the_sandbox_holds_exactly_the_wrapper_the_fixture_and_the_pinned_skill(self):
        rc, out, err, fake = self.live("--engines", "claude")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        want = {".claude/agents/%s.md" % NAME, "notes.txt", "data/input.csv"} | self.skill_files(".claude/skills")
        self.assertEqual(set(call["files"]), want)
        self.assertEqual(call["files"]["notes.txt"], NOTES.encode())
        self.assertNotIn("--disable-slash-commands", call["cmd"])
        rd = os.path.join(self.aws, "iteration-1", "claude", "overview", "new_agent", "run-1")
        run = AE.rjson(os.path.join(rd, "run.json"))
        self.assertEqual(run["state"], "run")
        self.assertEqual(run["isolation"]["pinned_skills"], [SKILL])
        self.assertEqual(run["isolation"]["skills_loaded"], "verified")
        plan = AE.rjson(os.path.join(self.aws, "iteration-1", "plan.json"))
        self.assertEqual(plan["pins"], [{"pin": PIN, "name": SKILL, "version": "1.0.0",
                                         "registration_sha256": sha(os.path.join(self.ws, "registration.json"))}])
        self.assertEqual(plan["fixtures"]["overview"]["slug"], "smoke")
        self.assertEqual(run["fixture"], {"slug": "smoke", "sha256": plan["fixtures"]["overview"]["sha256"]})
        t = AE.rjson(os.path.join(rd, "transcript.json"))
        self.assertEqual(sorted(t["work_manifest"]), ["data/input.csv", "notes.txt"])
        self.assertEqual(t["work_manifest"]["notes.txt"], {"sha256": hashlib.sha256(NOTES.encode()).hexdigest()})
        self.assertFalse(os.listdir(self.runs))

    def agent_report(self, agent=None):
        """agent grade (typed rows), then agent report: (exit code, output, gate.json of the last iteration)."""
        agent = agent or self.agent
        rc, out, err = AE.cli("agent", "grade", agent, "--workspace", self.aws)
        self.assertEqual(rc, 0, out + err)
        rc, out, err = AE.cli("agent", "report", agent, "--workspace", self.aws)
        gate = AE.rjson(os.path.join(self.aws, "iteration-%d" % se.iterations(self.aws)[-1], "gate.json"))
        return rc, out + err, gate

    def test_an_init_event_without_the_pinned_skill_names_the_unverified_load_in_the_report_and_the_approval(self):
        rc, out, err, _ = self.live("--engines", "claude", fake=AE.FakeAgentExec())
        self.assertEqual(rc, 0, out + err)
        rd = os.path.join(self.aws, "iteration-1", "claude", "overview", "new_agent", "run-1")
        run = AE.rjson(os.path.join(rd, "run.json"))
        self.assertEqual((run["state"], run["isolation"]["skills_loaded"]), ("run", "unverified"))
        rc, out, gate = self.agent_report()
        self.assertEqual(rc, 3, out)
        line =[p for p in gate["problems"] if p.startswith("skills-load-unverified:claude: 1 pinned run(s) ")]
        self.assertEqual(len(line), 1, gate["problems"])
        self.assertIn("UNVERIFIED", line[0])
        self.assertIn(line[0], out)
        items = [i for i in se.pending_items(se.unit_pairs([self.agent], [self.aws])) if i["gate"] == "registration"]
        self.assertIn(line[0], [x for _, x in items[0]["review"]])

    def test_a_verified_pinned_run_and_an_unpinned_run_get_no_load_line(self):
        rc, out, err, _ = self.live("--engines", "claude")
        self.assertEqual(rc, 0, out + err)
        rc, out, gate = self.agent_report()
        self.assertEqual(rc, 3, out)
        self.assertFalse([p for p in gate["problems"] if p.startswith("skills-load-unverified:")], gate["problems"])
        plain = make_agent(os.path.join(self.tmp, "plain-gate", "agents"), caps=("read", "search"), pins=None)
        self.aws = os.path.join(self.tmp, "plain-ws")
        rc, out, err, _ = self.live("--engines", "claude", agent=plain, fake=AE.FakeAgentExec())
        self.assertEqual(rc, 0, out + err)
        rc, out, gate = self.agent_report(plain)
        self.assertFalse([p for p in gate["problems"] if p.startswith("skills-load-unverified:")], gate["problems"])

    def test_the_record_command_binds_the_pins_only_with_the_unverified_load_named(self):
        d, ws = self.recorded_agent("agents-unv")
        rec = R.rjson(os.path.join(ws, "registration.json"))
        self.assertIn("registration", rec["approvals"])
        self.assertIn("skills-load-unverified:claude", se.registration_findings(ws, rec))

    def test_the_agent_loop_gate_names_the_unverified_load_and_calls_no_builder(self):
        lines, calls = [], []
        grader = se.LLMGrader("claude-sonnet-5-5", exec_fn=AE.FakeAgentExec(answer="yes"))
        r = se.agent_loop(se.Agent(self.agent), self.aws, [se.AgentClaudeEngine(exec_fn=AE.FakeAgentExec())], grader,
                          lambda it, d: calls.append(it), max_iterations=2, runs=1, run_root=self.runs,
                          log=lines.append)
        self.assertTrue(any("skills-load-unverified:claude" in str(x) for x in lines), lines)
        self.assertEqual(r, {"status": "needs-coverage", "iteration": 1})
        self.assertEqual(calls, [])

    def test_an_init_skill_outside_the_pins_is_fatal(self):
        other = AE.read(AE.CLAUDE_STREAM).replace('"skills":[]', '"skills":["%s","other"]' % SKILL)
        rc, out, err, _ = self.live("--engines", "claude", fake=AE.FakeAgentExec(claude=other))
        self.assertEqual(rc, 3, out + err)
        rd = os.path.join(self.aws, "iteration-1", "claude", "overview", "new_agent", "run-1")
        run = AE.rjson(os.path.join(rd, "run.json"))
        self.assertTrue(run["state"].startswith("error: isolation"), run["state"])
        self.assertIn("other", run["state"])

    def test_a_pinned_claude_run_without_an_init_event_is_fatal(self):
        stream = "".join(l for l in AE.read(AE.CLAUDE_STREAM).splitlines(True) if '"subtype":"init"' not in l)
        rc, out, err, fake = self.live("--engines", "claude", fake=AE.FakeAgentExec(claude=stream))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(rc, 3, out + err)
        rd = os.path.join(self.aws, "iteration-1", "claude", "overview", "new_agent", "run-1")
        state = AE.rjson(os.path.join(rd, "run.json"))["state"]
        self.assertTrue(state.startswith("error: isolation"), state)
        self.assertIn("no system/init event", state)

    def test_a_pinned_skill_re_recorded_between_two_runs_of_one_iteration_refuses_the_second_sandbox(self):
        outer = self

        class RecordAgainAfterTheFirstCall(AE.FakeAgentExec):
            def __call__(self, cmd, cwd, timeout, env=None):
                got = AE.FakeAgentExec.__call__(self, cmd, cwd, timeout, env)
                if len(self.calls) == 1:
                    outer.registered()
                return got
        fake = RecordAgainAfterTheFirstCall(claude=AE.read(AE.CLAUDE_STREAM).replace(
            '"skills":[]', '"skills":["%s"]' % SKILL))
        rc, out, err, fake = self.live("--engines", "claude", "--runs", "2", fake=fake)
        self.assertTrue(all(s["ok"] for s in se.pin_states(se.Agent(self.agent))))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(rc, 3, out + err)
        self.assertIn("a pinned skill of %s changed after the iteration started; nothing was run" % NAME, out + err)

    def test_codex_installs_the_pin_under_agents_skills_and_claims_nothing(self):
        self.allow_codex_under_test()
        rc, out, err, fake = self.live("--engines", "codex", "--no-strict-budget", "--price", "codex=1,10")
        self.assertEqual(rc, 0, out + err)
        call = fake.calls[0]
        want = {".codex/agents/%s.toml" % NAME, "notes.txt", "data/input.csv"} | self.skill_files(".agents/skills")
        self.assertEqual(set(call["files"]), want)
        rd = os.path.join(self.aws, "iteration-1", "codex", "overview", "new_agent", "run-1")
        self.assertEqual(AE.rjson(os.path.join(rd, "run.json"))["isolation"]["skills_loaded"], "unverified")

    def build(self, eng, fixture=(), pins=()):
        sb = se.AgentSandbox(eng, se.Agent(self.agent).wrapper(eng.name), AEV.install_rel(eng.name, NAME), self.runs,
                             fixture=list(fixture), pins=list(pins))
        self.addCleanup(sb.cleanup)
        return sb

    def test_the_sandbox_refuses_any_other_file_or_skill(self):
        eng = se.AgentClaudeEngine()
        files, probs = se.fixture_files(self.agent, "smoke")
        self.assertEqual(probs, [])
        sb = self.build(eng, files, [(SKILL, self.d)])
        self.assertEqual(sb.problems(), [])
        write(os.path.join(sb.work, "planted.txt"), "x")
        self.assertTrue(any("planted.txt" in p for p in sb.problems()), sb.problems())
        os.remove(os.path.join(sb.work, "planted.txt"))
        other = os.path.join(sb.work, ".claude", "skills", "other")
        write(os.path.join(other, "SKILL.md"), T.SKILL_MD)
        self.assertTrue(any("project skills in the sandbox" in p for p in sb.problems()), sb.problems())

    def test_the_sandbox_refuses_a_fixture_file_under_a_control_folder_in_any_letter_case(self):
        eng = se.AgentClaudeEngine()
        src = os.path.join(self.tmp, "evil.md")
        write(src, T.SKILL_MD)
        for rel in ("sub/.claude/skills/evil/SKILL.md", "sub/.Claude/skills/evil/SKILL.md",
                    "a/.AGENTS/skills/evil/SKILL.md", "a/.Codex/config.toml"):
            probs = self.build(eng, [(rel, src)]).problems()
            self.assertTrue(any("sandbox install folder name" in p and rel in p for p in probs), (rel, probs))

    def test_a_pin_with_a_bundled_skill_name_is_refused_before_launch(self):
        eng = se.AgentClaudeEngine()
        bundled = se.claude_bundled_skills()["names"][0]
        probs = eng.sandbox_problems(self.runs, self.runs, None, False, pins=[bundled])
        self.assertTrue(any("bundled skill" in p for p in probs), probs)

    def test_without_pins_the_launch_keeps_slash_commands_off_and_the_sandbox_holds_only_the_wrapper(self):
        plain = make_agent(os.path.join(self.tmp, "plain", "agents"), caps=("read", "search"), pins=None)
        rc, out, err, fake = self.live("--engines", "claude", agent=plain, fake=AE.FakeAgentExec())
        self.assertEqual(rc, 0, out + err)
        call = fake.calls[0]
        self.assertIn("--disable-slash-commands", call["cmd"])
        self.assertEqual(set(call["files"]), {".claude/agents/%s.md" % NAME})
        ws_plan = AE.rjson(os.path.join(self.aws, "iteration-1", "plan.json"))
        self.assertNotIn("pins", ws_plan)
        self.assertNotIn("fixtures", ws_plan)
        rd = os.path.join(self.aws, "iteration-1", "claude", "overview", "new_agent", "run-1")
        run = AE.rjson(os.path.join(rd, "run.json"))
        self.assertNotIn("pinned_skills", run["isolation"])
        self.assertNotIn("fixture", run)


class TestSkillsLoaded(unittest.TestCase):
    def test_only_an_init_list_equal_to_the_pins_reads_verified(self):
        pins = [SKILL, "notes-writer"]
        for skills, want in (([SKILL], "unverified"), ([{"name": "notes-writer"}], "unverified"), ([], "unverified"),
                             ([SKILL, "notes-writer", "other"], "unverified"), (None, "unverified"),
                             ([{"name": SKILL}, "notes-writer"], "verified")):
            init = dict(AE.CLAUDE_INIT)
            init.pop("skills", None)
            if skills is not None:
                init["skills"] = skills
            self.assertEqual(se.skills_loaded({"init": init}, pins), want, skills)
        self.assertEqual(se.skills_loaded({}, pins), "unverified")


class TestPinnedLoadGateLine(unittest.TestCase):
    PINS = ["a", "b"]

    def rec(self, tool="claude", loaded="unverified", pins=PINS, state="run", config="new_agent", isolation=True):
        iso = dict({"pinned_skills": list(pins)} if pins else {}, sandbox="/tmp/x")
        if pins:
            iso["skills_loaded"] = loaded
        return {"scenario": "overview", "tool": tool, "configuration": config, "run": 1, "state": state,
                "isolation": iso if isolation else None, "grading": {"pass_rate": 1.0, "needs_review": 0, "failed": []}}

    def lines(self, runs, plan=None):
        return [p for p in AEV.gate(plan or {}, runs, [])["problems"] if p.startswith("skills-load-unverified:")]

    def test_one_line_per_tool_counts_the_pinned_runs_whose_load_is_not_verified(self):
        got = self.lines([self.rec(), self.rec(), self.rec(tool="codex"), self.rec(loaded="verified")])
        self.assertEqual([p.split(" pinned run(s) ")[0] for p in got],
                         ["skills-load-unverified:claude: 2", "skills-load-unverified:codex: 1"])
        self.assertTrue(all("UNVERIFIED" in p for p in got), got)

    def test_a_verified_or_unpinned_or_failed_run_adds_no_line(self):
        self.assertEqual(self.lines([self.rec(loaded="verified"), self.rec(pins=()),
                                     self.rec(state="error: isolation: x")]), [])

    def test_a_run_without_an_isolation_record_counts_against_the_plan_pins(self):
        plan = {"pins": [{"pin": "a@1.0.0", "name": "a", "version": "1.0.0", "registration_sha256": "0" * 64}]}
        self.assertEqual(len(self.lines([self.rec(isolation=False)], plan)), 1)
        self.assertEqual(self.lines([self.rec(isolation=False)]), [])
        self.assertEqual(self.lines([self.rec(isolation=False, config="old_agent")], plan), [])


class TestPinBinding(Pinned):
    def recorded(self):
        rc, out, err, _ = self.live("--engines", "claude")
        self.assertEqual(rc, 0, out + err)
        return AE.rjson(os.path.join(self.aws, "iteration-1", "plan.json"))["pins"]

    def test_a_re_registration_or_an_edit_of_the_pinned_skill_unbinds_the_recorded_pins(self):
        pins = self.recorded()
        self.assertIsNone(se.pin_binding_problem(self.agent, pins))
        rec = R.rjson(os.path.join(self.ws, "registration.json"))
        rec["registered_at"] = "2026-10-28T00:00:00Z"
        T.wjson(os.path.join(self.tmp, "moved.json"), rec)
        os.replace(os.path.join(self.tmp, "moved.json"), os.path.join(self.ws, "registration.json"))
        why = se.pin_binding_problem(self.agent, pins)
        self.assertIsNotNone(why)
        self.assertIn(PIN, why)

    def test_an_edited_pinned_skill_unbinds_and_a_missing_pin_record_is_refused(self):
        pins = self.recorded()
        write(os.path.join(self.d, "SKILL.md"), R.skill_text("1.0.0", R.BODY + "\nEdited.\n"))
        self.assertIn(PIN, se.pin_binding_problem(self.agent, pins))
        R.Reg.set_version(self, "1.0.0")
        self.assertIsNone(se.pin_binding_problem(self.agent, pins))
        self.assertIn(PIN, se.pin_binding_problem(self.agent, []))

    def test_a_recorded_pinning_agent_reads_stale_when_its_pinned_skill_or_fixture_changes(self):
        import test_install as I
        parent = os.path.join(self.repo, "agents-rec")
        d = make_agent(parent, fixture=self.fixture, generate=False)
        write(os.path.join(d, "AGENT.md"), read(os.path.join(d, "AGENT.md")).replace(
            "targets: [", "version: 1.0.0\ntargets: [", 1))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eva.main(["generate", d]), 0)
        ws = REG.agent_workspace(d)
        I.register_agent_at(self, d, ws, self.runs)
        skill_md = os.path.join(self.d, "SKILL.md")
        write(skill_md, R.skill_text("1.0.0", R.BODY + "\nEdited after the agent record.\n"))
        state, reason, _ = se.registration_state(d, ws)
        self.assertEqual(state, "stale")
        self.assertIn(PIN, reason)
        R.Reg.set_version(self, "1.0.0")
        self.assertEqual(se.registration_state(d, ws)[:2], ("registered", ""))
        write(os.path.join(d, "fixtures", "smoke", "notes.txt"), NOTES + "edited\n")
        self.assertEqual(se.registration_state(d, ws)[0], "stale")

    def test_a_recorded_pinning_agent_reads_stale_when_its_pinned_version_is_deprecated(self):
        d, ws = self.recorded_agent("agents-dep")
        rc, out, err = AE.cli("deprecate", self.d, "--version", "1.0.0", "--reason", "replaced by 2.0.0")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.state()[0], "registered")
        state, reason, _ = se.registration_state(d, ws)
        self.assertEqual(state, "stale")
        self.assertIn("pin %s does not verify: deprecated: replaced by 2.0.0" % PIN, reason)

    def test_a_recorded_pinning_agent_reads_stale_when_its_pinned_skill_record_is_withdrawn(self):
        d, ws = self.recorded_agent("agents-rev")
        os.rename(os.path.join(self.ws, "registration.json"), os.path.join(self.tmp, "withdrawn.json"))
        self.assertEqual(self.state()[0], "unregistered")
        state, reason, _ = se.registration_state(d, ws)
        self.assertEqual(state, "stale")
        self.assertIn("pin %s does not verify: unregistered" % PIN, reason)

    def test_the_record_command_refuses_an_agent_whose_pinned_skill_changed_after_the_iteration(self):
        import test_install as I
        parent = os.path.join(self.repo, "agents-ref")
        d = make_agent(parent, generate=False)
        write(os.path.join(d, "AGENT.md"), read(os.path.join(d, "AGENT.md")).replace(
            "targets: [", "version: 1.0.0\ntargets: [", 1))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eva.main(["generate", d]), 0)
        ws = REG.agent_workspace(d)
        I.fake_iteration(d, ws, self.runs)
        for tool in ("opencode", "hermes"):
            rc, out, err = AE.cli("approve", d, "--gate", "coverage_waiver:" + tool)
            self.assertEqual(rc, 0, out + err)
        rc, out, err = AE.cli("agent", "report", d)
        self.assertEqual(rc, 3, out + err)
        rec = R.rjson(os.path.join(self.ws, "registration.json"))
        rec["registered_at"] = "2026-10-28T00:00:00Z"
        T.wjson(os.path.join(self.tmp, "moved.json"), rec)
        os.replace(os.path.join(self.tmp, "moved.json"), os.path.join(self.ws, "registration.json"))
        with self.assertRaises(se.EvalError) as cm:
            se.registration_record(se.agent_spec(se.Agent(d)), ws)
        self.assertIn(PIN, str(cm.exception))

    def test_classify_reads_stale_when_the_pinned_check_fails(self):
        from engine import approvals as A
        ws = os.path.join(self.tmp, "cls-ws")
        os.makedirs(ws)
        unit = os.path.join(self.tmp, "cls-unit")
        os.makedirs(unit)
        rec = {"registration_format": 2, "skill_path": "../cls-unit", "signed": {}, "approvals": {}, "version": "1.0.0"}
        T.wjson(os.path.join(ws, "registration.json"), rec)
        ok = A.Verdict("approved", "p", "")
        args = ("agent", ws, unit, lambda r: [], lambda r: ok, lambda r: iter(()), lambda g, c: ok)
        self.assertEqual(REG.classify(*args)[0], "registered")
        self.assertEqual(REG.classify(*args, pinned=lambda r: None)[0], "registered")
        state, reason, _ = REG.classify(*args, pinned=lambda r: "pin x@1.0.0 changed")
        self.assertEqual((state, reason), ("stale", "pin x@1.0.0 changed"))


class TestFixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.agent = make_agent(os.path.join(self.tmp, "agents"), caps=("read", "search"), pins=None,
                                fixture={"notes.txt": NOTES})

    def test_fixture_files_join_the_eval_set_and_live_hashes_and_an_edit_changes_them(self):
        spec = se.agent_spec(se.Agent(self.agent))
        labels = [l for l, _ in se.unit_eval_files(spec)]
        self.assertIn("fixtures/smoke/notes.txt", labels)
        self.assertIn("fixtures/smoke/notes.txt", [l for l, _ in se.Agent(self.agent).files(["claude"])])
        write(os.path.join(self.tmp, "requirements.md"), "goal")
        hashes = lambda: (se.gate_hash("eval_set", spec, self.tmp), se.gate_hash(se.COMBINED_GATE, spec, self.tmp),
                          se.sha256_labeled(se.Agent(self.agent).files(["claude"])))
        before = hashes()
        write(os.path.join(self.agent, "fixtures", "smoke", "notes.txt"), NOTES + "more\n")
        after = hashes()
        for i in range(3):
            self.assertNotEqual(before[i], after[i], i)

    def test_an_agent_without_fixtures_keeps_the_n1_hashes(self):
        plain = AE.generated_copy(os.path.join(self.tmp, "plain"))
        spec = se.agent_spec(se.Agent(plain))
        self.assertEqual(se.unit_eval_files(spec), se.agent_scenario_files(os.path.realpath(plain)))
        rels = ["AGENT.md", "scenarios/01-overview.json", "scenarios/02-drill.json", "wrappers/claude/%s.md" % NAME]
        self.assertEqual([l for l, _ in se.Agent(plain).files(["claude"])], rels)

    def test_a_missing_fixture_folder_a_symlink_or_a_control_folder_is_refused(self):
        self.assertTrue(se.fixture_files(self.agent, "nope")[1])
        outside = os.path.join(self.tmp, "outside")
        write(os.path.join(outside, "secret.txt"), "x")
        os.symlink(os.path.join(outside, "secret.txt"), os.path.join(self.agent, "fixtures", "smoke", "link.txt"))
        probs = se.fixture_files(self.agent, "smoke")[1]
        self.assertTrue(any("link.txt" in p and "symlink" in p for p in probs), probs)
        os.remove(os.path.join(self.agent, "fixtures", "smoke", "link.txt"))
        os.symlink(outside, os.path.join(self.agent, "fixtures", "away"))
        self.assertTrue(any("symlink" in p for p in se.fixture_files(self.agent, "away")[1]))
        write(os.path.join(self.agent, "fixtures", "ctl", ".claude", "skills", "x", "SKILL.md"), T.SKILL_MD)
        self.assertTrue(any(".claude" in p for p in se.fixture_files(self.agent, "ctl")[1]))
        self.assertEqual(se.fixture_files(self.agent, "smoke"), ([("notes.txt", os.path.join(
            self.agent, "fixtures", "smoke", "notes.txt"))], []))

    def test_a_control_folder_nested_anywhere_in_the_fixture_is_refused(self):
        for slug, rel in (("nc", "sub/.claude/skills/x/SKILL.md"), ("na", "a/b/.agents/skills/x/SKILL.md"),
                          ("nx", "deep/.codex/config.toml")):
            write(os.path.join(self.agent, "fixtures", slug, *rel.split("/")), T.SKILL_MD)
            files, probs = se.fixture_files(self.agent, slug)
            self.assertEqual(files, [], rel)
            self.assertTrue(any("sandbox install folder name" in p for p in probs), (rel, probs))

    def test_a_control_folder_in_any_letter_case_is_refused(self):
        for slug, rel in (("uc", "sub/.Claude/skills/x/SKILL.md"), ("ut", ".CLAUDE/skills/x/SKILL.md"),
                          ("ua", "a/.AGENTS/skills/x/SKILL.md"), ("ux", "a/.Codex/config.toml")):
            write(os.path.join(self.agent, "fixtures", slug, *rel.split("/")), T.SKILL_MD)
            files, probs = se.fixture_files(self.agent, slug)
            self.assertEqual(files, [], rel)
            self.assertTrue(any("sandbox install folder name" in p for p in probs), (rel, probs))

    def test_the_scenario_schema_refuses_a_slug_that_could_leave_the_folder(self):
        shape = eva.shape_schema("scenario.schema.json")
        doc = AE.rjson(os.path.join(self.agent, "scenarios", "01-overview.json"))
        self.assertEqual(shape.problems(doc), [])
        for slug in ("../x", "/abs", "a/b", "A", ".hidden", "", "a\\b", 5):
            self.assertTrue(shape.problems(dict(doc, fixture=slug)), slug)

    def test_plan_refuses_a_missing_fixture(self):
        p = os.path.join(self.agent, "scenarios", "01-overview.json")
        doc = AE.rjson(p)
        doc["fixture"] = "nope"
        AE.wjson(p, doc)
        with contextlib.redirect_stdout(io.StringIO()):
            eva.main(["generate", self.agent])
        rc, out, err = AE.cli("agent", "plan", self.agent, "--workspace", os.path.join(self.tmp, "ws"))
        self.assertEqual(rc, 2, out + err)
        self.assertIn("agent plan refused: fixture nope of scenario overview", out)


def claude_bash_stream(command="cat notes.txt", result="hello", final="done", is_error=False):
    return AE.lines(
        dict(AE.CLAUDE_INIT, skills=[]),
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                                       "input": {"command": command}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": result}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": final}]}},
        dict(AE.CLAUDE_RESULT, result=final, is_error=is_error))


def codex_shell_stream(command="cat notes.txt", final="done", failed=False):
    tail = [{"type": "turn.failed", "error": {"message": "x"}}] if failed else AE.CODEX_TAIL
    return AE.lines(*(AE.CODEX_HEAD + [AE.codex_cmd(command, out="hello"),
                                       {"type": "item.completed", "item": {"id": "m", "type": "agent_message",
                                                                           "text": final}}] + tail))


class TestCriteria(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def run_of(self, tool, stream, manifest=None):
        t = AE.normalize(tool, stream, agent=NAME)
        if manifest is not None:
            t["work_manifest"] = manifest
        p = os.path.join(self.tmp, "%s-%d.json" % (tool, len(os.listdir(self.tmp))))
        AE.wjson(p, t)
        run, errs = eva.load_transcript(p)
        self.assertEqual(errs, [])
        return run, p

    def ev(self, crit, run):
        TP_crit = dict(crit)
        return TP.criterion_evidence(TP_crit, run["final"], run)

    def test_command_ran_and_command_absent_grade_claude_and_codex_shell_rows(self):
        for tool, stream in (("claude", claude_bash_stream()), ("codex", codex_shell_stream())):
            run, _ = self.run_of(tool, stream)
            self.assertEqual(self.ev({"type": "command_ran", "regex": r"^cat notes\.txt$"}, run)[:2],
                             ("command-ran", True), tool)
            self.assertFalse(self.ev({"type": "command_ran", "regex": "rm -rf"}, run)[1], tool)
            self.assertTrue(self.ev({"type": "command_absent", "regex": "rm -rf"}, run)[1], tool)
            self.assertFalse(self.ev({"type": "command_absent", "regex": "notes"}, run)[1], tool)

    def test_shell_criteria_never_read_the_prompt_the_answer_or_tool_results(self):
        run, _ = self.run_of("claude", claude_bash_stream(command="ls", result="cat notes.txt",
                                                          final="I ran cat notes.txt"))
        self.assertFalse(self.ev({"type": "command_ran", "regex": "cat notes"}, run)[1])
        self.assertTrue(self.ev({"type": "command_absent", "regex": "cat notes"}, run)[1])
        read_call = dict(run, calls=[{"tool": "Read", "input": {"command": "cat notes.txt"}, "result": ""}])
        self.assertFalse(self.ev({"type": "command_ran", "regex": "cat"}, read_call)[1])

    def test_an_errored_or_incomplete_transcript_never_passes(self):
        for tool, stream in (("claude", claude_bash_stream(is_error=True)), ("codex", codex_shell_stream(failed=True))):
            run, _ = self.run_of(tool, stream)
            self.assertTrue(run["is_error"], tool)
            self.assertFalse(self.ev({"type": "command_absent", "regex": "rm"}, run)[1], tool)
            self.assertFalse(self.ev({"type": "command_ran", "regex": "cat"}, run)[1], tool)
        run, _ = self.run_of("claude", claude_bash_stream())
        bare = {k: v for k, v in run.items() if k != "is_error"}
        self.assertFalse(self.ev({"type": "command_absent", "regex": "rm"}, bare)[1])
        self.assertFalse(TP.criterion_evidence({"type": "command_absent", "regex": "rm"}, "", None)[1])
        empty = dict(run, calls=[])
        self.assertTrue(self.ev({"type": "command_absent", "regex": "rm"}, empty)[1])
        self.assertFalse(self.ev({"type": "command_ran", "regex": "."}, empty)[1])

    def test_file_exists_reads_the_work_folder_manifest_only(self):
        manifest = {"out/report.md": {"sha256": "0" * 64}, "link.md": {"link": "/etc/hosts"}}
        for tool, stream in (("claude", claude_bash_stream()), ("codex", codex_shell_stream())):
            run, _ = self.run_of(tool, stream, manifest)
            self.assertEqual(self.ev({"type": "file_exists", "path": "out/report.md"}, run)[:2], ("file-exists", True))
            self.assertTrue(self.ev({"type": "file_exists", "path": "./out/report.md"}, run)[1])
            self.assertFalse(self.ev({"type": "file_exists", "path": "out"}, run)[1])
            self.assertFalse(self.ev({"type": "file_exists", "path": "link.md"}, run)[1])
            self.assertFalse(self.ev({"type": "file_exists", "path": "missing.md"}, run)[1])
        run, _ = self.run_of("claude", claude_bash_stream())
        self.assertFalse(self.ev({"type": "file_exists", "path": "out/report.md"}, run)[1])

    def test_validation_refuses_a_bad_regex_and_a_path_that_leaves_the_work_folder(self):
        shape = eva.shape_schema("scenario.schema.json")
        doc = AE.rjson(os.path.join(AE.AGENT_FIXTURE, "scenarios", "01-overview.json"))
        good = [{"type": "command_ran", "regex": "^make test$"}, {"type": "command_absent", "regex": "rm "},
                {"type": "file_exists", "path": "out/report.md"}]
        self.assertEqual(shape.problems(dict(doc, expected_outcome=good)), [])
        self.assertEqual(shape.problems(dict(doc, success_criteria=good)), [])
        bad = [{"type": "command_ran", "regex": "("}, {"type": "command_absent", "regex": "[a"},
               {"type": "command_ran"}, {"type": "command_ran", "regex": "x", "extra": 1},
               {"type": "file_exists", "path": "../x"}, {"type": "file_exists", "path": "/etc/passwd"},
               {"type": "file_exists", "path": "a/../../x"}, {"type": "file_exists", "path": "a\\b"},
               {"type": "file_exists", "path": "a\u0000b"}, {"type": "file_exists", "path": ""},
               {"type": "file_exists", "path": ".."}]
        for crit in bad:
            self.assertTrue(shape.problems(dict(doc, expected_outcome=[crit])), crit)
        for crit in good:
            self.assertTrue(TP.parse_agent(agent_text(pins=None, criteria=[crit])).valid, crit)
        self.assertFalse(TP.parse_agent(agent_text(pins=None, criteria=[{"type": "command_ran", "regex": "("}])).valid)

    def test_a_scenario_with_the_new_criteria_grades_end_to_end(self):
        agent = make_agent(os.path.join(self.tmp, "agents"), caps=("read", "search", "shell"), pins=None)
        p = os.path.join(agent, "scenarios", "01-overview.json")
        doc = AE.rjson(p)
        doc["expected_tools"] = ["shell"]
        doc["must_include"] = ["done"]
        doc["expected_outcome"] = [{"type": "command_ran", "regex": "^cat notes"},
                                   {"type": "command_absent", "regex": "rm "},
                                   {"type": "file_exists", "path": "notes.txt"}]
        AE.wjson(p, doc)
        _, path = self.run_of("codex", codex_shell_stream(), {"notes.txt": {"sha256": "1" * 64}})
        got = {c["id"]: c["status"] for c in eva.grade_transcript(agent, path).checks}
        self.assertEqual((got["expected-command-ran"], got["expected-command-absent"], got["expected-file-exists"]),
                         ("pass", "pass", "pass"), got)


if __name__ == "__main__":
    unittest.main()
