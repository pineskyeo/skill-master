"""S1 method 3: one machine waiver at $HOME/.config/skill-master/machine-waiver.json binds this Mac's id, tools from
machine_waivable only, an expiry 90 days after it was recorded and a reason. Since unit N1 it is an unsigned recorded
record; a waiver signed before N1 still verifies against the signers file. A valid record for this machine clears a
coverage_required tool that did not run; another machine, an expired, revoked, malformed or legacy record without a
method, or a record that lists a tool outside machine_waivable clears nothing. Registration binds the waiver's hash.

Every case runs with the temp HOME of tests/test_skill_eval.py Base and the test approver, which starts no process;
the machine id probe is a fake (skill_eval.MACHINE) and subprocess is patched for the whole module. ASCII only."""
import calendar, json, os, pwd, subprocess, sys, time, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_coverage_gate as CG
from test_skill_eval import se, write, wjson, rjson
from engine import approvals as A

THIS = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"
REASON = "OpenCode and Hermes run in-house only (gates.md G33)"
NOT_AVAILABLE = "not-run: engine not available"
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


class Waiver(CG.Cov):
    def setUp(self):
        CG.Cov.setUp(self)
        self.typing(REASON)
        self.machine = THIS
        self.now = time.time()
        pt = mock.patch.dict(se.MACHINE, {"id": lambda: self.machine, "now": lambda: self.now})
        pt.start(); self.addCleanup(pt.stop)
        self.path = os.path.join(self.home, ".config", "skill-master", "machine-waiver.json")
        self.log = os.path.join(self.home, ".config", "skill-master", "approvals.log")

    def sign(self, tools=("opencode", "hermes")):
        return se.sign_machine_waiver(list(tools))

    def state(self):
        return se.machine_waiver()

    def covered(self):
        n = self.run_both()
        return n, self.report(n)


class TestClears(Waiver):
    def test_a_waiver_clears_opencode_and_hermes_and_never_claude_or_codex(self):
        rec = self.sign()
        self.assertEqual(rjson(self.path), rec)
        st = self.state()
        self.assertEqual((st["status"], st["principal"], st["reason"], st["tools"]),
                         ("approved", T.TEST_PRINCIPAL, REASON, ["opencode", "hermes"]))
        self.assertEqual(st["sha256"], A.machine_waiver_sha256(rec))
        n, (bench, gate) = self.covered()
        self.assertEqual(CG.missing_tools(gate), [])
        for tool in ("opencode", "hermes"):
            self.assertEqual(bench["coverage_waivers"][tool]["source"], "machine")
            self.assertEqual(gate["coverage"][tool], "waived by machine policy %s" % st["sha256"][:12])
            self.assertIn("coverage waived: %s (dynamic eval %s) by machine policy %s (%s, expires %s): %s" % (
                tool, NOT_AVAILABLE, st["sha256"], T.TEST_PRINCIPAL, st["expires"], REASON), gate["notes"])
        self.assertEqual(gate["coverage"]["claude"], "run")
        md = T.read(os.path.join(self.ws, "iteration-%d" % n, "comparison.md"))
        self.assertIn("| waived by machine policy %s |" % st["sha256"][:12], md)

    def test_claude_not_run_is_not_cleared_by_the_machine_waiver(self):
        self.sign()
        self.assertNotEqual(se.waiver_verdicts(self.spec, self.ws)["claude"].get("source"), "machine")

    def test_a_per_unit_waiver_still_counts_and_is_shown_as_its_own(self):
        self.sign()
        self.waive("hermes", REASON)
        n, (bench, gate) = self.covered()
        self.assertEqual(bench["coverage_waivers"]["hermes"]["source"], "unit")
        self.assertEqual(gate["coverage"]["hermes"], "waived by %s" % T.TEST_PRINCIPAL)


class TestClearsNothing(Waiver):
    def assert_clears_nothing(self, status):
        self.assertEqual(self.state()["status"], status)
        n, (bench, gate) = self.covered()
        self.assertEqual(CG.missing_tools(gate), ["opencode", "hermes"])

    def test_another_machine_id(self):
        self.sign()
        self.machine = OTHER
        self.assert_clears_nothing("other-machine")

    def test_a_failing_machine_id_probe(self):
        self.sign()
        def broken(): raise OSError("ioreg failed")
        with mock.patch.dict(se.MACHINE, {"id": broken}):
            self.assert_clears_nothing("other-machine")

    def test_an_expired_record(self):
        rec = self.sign()
        at = calendar.timegm(time.strptime(rec["at"], "%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(rec["expires"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at + 90 * 86400)))
        self.now = at + 90 * 86400 + 1
        self.assert_clears_nothing("expired")

    def test_a_revoked_file(self):
        rec = self.sign()
        se.revoke_machine_waiver()
        self.assertFalse(os.path.exists(self.path))
        lines = [json.loads(x) for x in T.read(self.log).splitlines()]
        self.assertEqual([x["decision"] for x in lines], ["machine-waiver-signed", "machine-waiver-revoked"])
        self.assertEqual(lines[1]["sha256"], A.machine_waiver_sha256(rec))
        self.assert_clears_nothing("missing")

    def test_a_malformed_record(self):
        for field, value in (("reason", "x\ty"), ("expires", "2099-01-01T00:00:00Z"), ("tools", ["hermes", "opencode"]),
                             ("recorded_by", "a b"), ("principal", T.TEST_PRINCIPAL), ("signature", "s"), ("format", 2)):
            with self.subTest(field):
                rec = self.sign()
                wjson(self.path, dict(rec, **{field: value}))
                self.assertEqual(self.state()["status"], "invalid", field)
        self.assert_clears_nothing("invalid")

    def test_a_record_moved_to_another_machine_clears_nothing(self):
        rec = self.sign()
        wjson(self.path, dict(rec, machine_id=OTHER))
        self.assert_clears_nothing("other-machine")

    def test_a_legacy_record_without_method(self):
        rec = self.sign()
        wjson(self.path, {k: v for k, v in rec.items() if k != "method"})
        self.assert_clears_nothing("unsigned")

    def signed(self, tools=("opencode", "hermes"), at=None, key=None):
        """A machine waiver signed before N1 with the throwaway key."""
        at = at or A.now_iso()
        expires = A.iso_at(A.iso_seconds(at) + 86400)
        data = A.machine_waiver_payload(THIS, list(tools), expires, REASON, at)
        return {"format": 1, "method": "ssh-sig", "machine_id": THIS, "tools": list(tools), "expires": expires,
                "reason": REASON, "principal": T.TEST_PRINCIPAL, "at": at,
                "signature": T.sign_bytes(key or T.shared_keys()["key"], data)}

    def test_a_signed_waiver_from_before_n1_still_verifies(self):
        rec = self.signed()
        wjson(self.path, rec)
        self.assertEqual(self.state()["status"], "invalid", "no signers file: an old signed waiver does not count")
        T.install_test_approver(self, signers=T.shared_keys()["signers"], run=T.keygen_runner(T.shared_keys()["dir"]))
        st = self.state()
        self.assertEqual((st["status"], st["principal"], st["tools"]), ("approved", T.TEST_PRINCIPAL, ["opencode", "hermes"]))
        n, (bench, gate) = self.covered()
        self.assertEqual(CG.missing_tools(gate), [])
        wjson(self.path, dict(rec, reason=REASON + "!"))
        self.assertEqual(self.state()["status"], "invalid")

    def test_a_record_that_lists_a_tool_outside_machine_waivable(self):
        with self.assertRaises(A.Refused) as cm: self.sign(("claude", "opencode"))
        self.assertIn("claude", str(cm.exception)); self.assertIn("nothing was recorded", str(cm.exception))
        self.assertFalse(os.path.exists(self.path))
        T.install_test_approver(self, signers=T.shared_keys()["signers"], run=T.keygen_runner(T.shared_keys()["dir"]))
        wjson(self.path, self.signed(tools=("claude", "opencode")))
        st = self.state()
        self.assertEqual(st["status"], "invalid"); self.assertIn("claude", st["why"])
        rec = dict(self.signed(), method="recorded", recorded_by=T.TEST_PRINCIPAL, tools=["claude", "opencode"])
        del rec["principal"], rec["signature"]
        wjson(self.path, rec)
        st = self.state()
        self.assertEqual(st["status"], "invalid"); self.assertIn("claude", st["why"])
        n, (bench, gate) = self.covered()
        self.assertEqual(CG.missing_tools(gate), ["opencode", "hermes"])

    def test_a_record_that_lists_codex(self):
        with self.assertRaises(A.Refused) as cm: self.sign(("opencode", "codex"))
        self.assertIn("codex", str(cm.exception)); self.assertIn("nothing was recorded", str(cm.exception))
        self.assertFalse(os.path.exists(self.path))
        wjson(self.path, dict(self.sign(), tools=["codex", "opencode"]))
        st = self.state()
        self.assertEqual(st["status"], "invalid"); self.assertIn("field tools", st["why"])

    def test_a_record_from_the_future(self):
        self.sign()
        self.now -= 3600
        self.assertEqual(self.state()["status"], "invalid")


class TestRecording(Waiver):
    def test_recording_needs_no_tty_no_challenge_and_no_key(self):
        asked = []
        T.install_test_approver(self, read_line=lambda p: asked.append(p) or REASON + "\n", run=T.no_process)
        rec = self.sign()
        self.assertEqual(asked, [A.REASON_PROMPT])
        self.assertEqual(rjson(self.path), rec)
        self.assertEqual(se.sign_machine_waiver(["hermes"], reason="hermes runs in-house only")["reason"],
                         "hermes runs in-house only")
        self.assertEqual(asked, [A.REASON_PROMPT])
        se.revoke_machine_waiver()
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(T.gate_approver().signers))

    def test_the_record(self):
        rec = self.sign()
        self.assertEqual(sorted(rec), ["at", "expires", "format", "machine_id", "method", "reason", "recorded_by", "tools"])
        self.assertEqual((rec["format"], rec["method"], rec["machine_id"], rec["tools"], rec["reason"], rec["recorded_by"]),
                         (1, "recorded", THIS, ["opencode", "hermes"], REASON, T.TEST_PRINCIPAL))
        self.assertEqual(rec["expires"], A.iso_at(A.iso_seconds(rec["at"]) + 90 * 86400))
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), oct(0o600))
        line = json.loads(T.read(self.log).splitlines()[0])
        self.assertEqual((line["decision"], line["principal"]), ("machine-waiver-signed", T.TEST_PRINCIPAL))

    def test_a_refused_reason_or_an_unreadable_machine_id_records_nothing(self):
        for reason in ("", " padded", "x" * 201):
            with self.subTest(reason=reason[:10]):
                with self.assertRaises(A.Refused) as cm: se.sign_machine_waiver(["opencode"], reason=reason)
                self.assertIn("reason refused", str(cm.exception)); self.assertIn("nothing was recorded", str(cm.exception))
        self.machine = None
        with self.assertRaises(A.Refused) as cm: self.sign()
        self.assertIn("cannot read this Mac's IOPlatformUUID; nothing was recorded", str(cm.exception))
        self.assertFalse(os.path.exists(self.path))

    def test_a_test_process_never_writes_the_real_home_waiver(self):
        with mock.patch.object(A, "account_home", lambda: self.home):
            with self.assertRaises(se.EvalError) as cm: self.sign()
            self.assertIn("inside a test process", str(cm.exception))
            with self.assertRaises(se.EvalError): se.revoke_machine_waiver()
        self.assertFalse(os.path.exists(self.path))

    def test_show_prints_hash_principal_reason_and_expiry(self):
        rec = self.sign()
        rc, out, err = self.cli("machine-waiver", "show")
        self.assertEqual(rc, 0, err)
        st = self.state()
        for want in (st["sha256"], T.TEST_PRINCIPAL, REASON, rec["expires"], "opencode,hermes", "approved"):
            self.assertIn(want, out)

    def test_the_policy_lists_machine_waivable(self):
        doc = rjson(os.path.join(T.ROOT, "policy", "gate.rules.json"))
        self.assertEqual(doc["machine_waivable"], ["opencode", "hermes"])
        self.assertEqual(se.MACHINE_WAIVABLE, ("opencode", "hermes"))


class TestRegistration(Waiver):
    def test_a_registration_record_binds_the_waiver_hash(self):
        rec = self.sign()
        n, (bench, gate) = self.covered()
        reg = se.register(self.spec, self.ws)
        self.assertEqual(reg["machine_waiver"]["record"], rec)
        self.assertEqual(reg["machine_waiver"]["sha256"], A.machine_waiver_sha256(rec))
        self.assertNotIn("coverage_waiver:opencode", reg["approvals"])
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "approved")
        path = os.path.join(self.ws, "registration.json")
        stored = rjson(path)
        stored["machine_waiver"]["sha256"] = "ab" * 32
        wjson(path, stored)
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "stale")
        del stored["machine_waiver"]
        wjson(path, stored)
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "stale")

    def test_a_registration_without_the_machine_waiver_keeps_its_old_shape(self):
        n = self.run_both()
        self.waive("opencode", REASON); self.waive("hermes", REASON)
        self.report(n)
        reg = se.register(self.spec, self.ws)
        self.assertNotIn("machine_waiver", reg)
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "approved")

    def test_the_hub_unit_page_shows_hash_principal_reason_and_expiry(self):
        from engine import hub
        rec = self.sign()
        st = self.state()
        view = {"approvals": [], "machine_waiver": {"sha256": st["sha256"], "record": rec}}
        html = hub.machine_waiver_html(view)
        for want in (st["sha256"], T.TEST_PRINCIPAL, REASON, rec["expires"]):
            self.assertIn(want, html)
        self.assertEqual(hub.machine_waiver_html({"approvals": [], "machine_waiver": None}), "<li>none</li>")


class TestPlanLine(Waiver):
    def test_plan_prints_one_machine_waiver_line(self):
        self.assertEqual(self.line(self.plan(), "machine waiver: "),
                         "machine waiver: missing (~/.config/skill-master/machine-waiver.json)")
        self.sign()
        st = self.state()
        self.assertEqual(self.line(self.plan(), "machine waiver: "),
                         "machine waiver: approved by %s, sha256 %s, tools opencode,hermes, expires %s, reason: %s"
                         % (T.TEST_PRINCIPAL, st["sha256"], st["expires"], REASON))


class TestAgentReport(Waiver):
    def test_agent_report_prints_the_machine_waiver_with_hash_principal_reason_and_expiry(self):
        import test_install as I
        agent = I.agent_copy(os.path.join(self.tmp, "agents"))
        ws = os.path.join(self.tmp, "agents", I.NAME + "-workspace")
        I.fake_iteration(agent, ws, self.runs)
        rc, out, err = self.cli("agent", "report", agent, "--workspace", ws)
        self.assertEqual(rc, 3, out + err)
        self.assertNotIn("machine waiver:", out)
        self.sign()
        st = self.state()
        rc, out, err = self.cli("agent", "report", agent, "--workspace", ws)
        self.assertEqual(rc, 3, out + err)
        gate = rjson(os.path.join(ws, "iteration-1", "gate.json"))
        self.assertEqual(gate["coverage"]["opencode"], "waived by machine policy %s" % st["sha256"][:12])
        self.assertIn("machine waiver: approved by %s, sha256 %s, tools opencode,hermes, expires %s, reason: %s\n"
                      % (T.TEST_PRINCIPAL, st["sha256"], st["expires"], REASON), out)
        self.assertEqual(CG.missing_tools(gate), [])


class TestTestProcessGuard(unittest.TestCase):
    def test_a_test_process_never_opens_the_real_home_waiver(self):
        real = pwd.getpwuid(os.getuid()).pw_dir
        def trap(*a, **k): raise AssertionError("opened %r" % (a[:1],))
        with mock.patch.dict(os.environ, {"HOME": real}), mock.patch("builtins.open", trap), \
                mock.patch.object(os, "stat", trap), mock.patch.object(os, "lstat", trap):
            st = se.machine_waiver()
        self.assertEqual(st["status"], "invalid")
        self.assertIn("inside a test process", st["why"])

    def test_a_relative_home_reads_invalid(self):
        with mock.patch.dict(os.environ, {"HOME": "relative/home"}):
            st = se.machine_waiver()
        self.assertEqual(st["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
