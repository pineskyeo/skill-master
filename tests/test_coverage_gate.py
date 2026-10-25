"""G1: registration needs a graded dynamic run on every tool of coverage_required (policy/gate.rules.json) or a signed
coverage_waiver:<tool> per missing tool, bound to the unit, the eval set and the ruleset, with a reason typed at the
human gate. `report` lists each missing tool as a coverage-missing:<tool> gate problem.

Signing uses only the throwaway test key of tests/test_skill_eval.py, made under /tmp; subprocess is patched for the
whole module and only that key runner starts /usr/bin/ssh-keygen. approve, register and the loop are reached only as
Python calls on temp fixtures; builders and engines are fakes. The ruleset root is a temp copy of the bundle. ASCII only:
non-ASCII text is built from code points."""
import contextlib, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_ruleset_binding as RB
from test_skill_eval import se, eva, evals_doc, make_skill, write, wjson, rjson, quiet, FakeClaude
from engine import approvals as A

ROOT = T.ROOT
REASON = "OpenCode and Hermes engines do not exist yet; parity checked by hand"
AT = "2026-10-25T09:41:00Z"
SHA, OTHER = "ab" * 32, "cd" * 32
KOREAN = "".join(map(chr, (0xD55C, 0xAD6D, 0xC5B4)))
TOOLS = ("claude", "codex", "opencode", "hermes")
NOT_AVAILABLE = "not-run: engine not available"
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def state(path):
    if not os.path.exists(path): return None
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def missing_tools(gate):
    return [p[len("coverage-missing:"):].split(":", 1)[0] for p in gate["problems"] if p.startswith("coverage-missing:")]


def line_for(lines, tool):
    return next(p for p in lines if p.startswith("coverage-missing:%s:" % tool))


def waiver(tool):
    return "coverage_waiver:" + tool


class Cov(RB.Bound):
    """RB.Bound (temp home, temp ruleset root, the test approver, a skill with a workspace) plus a reason-typing reader
    and an event log of everything the gate wrote and asked."""
    def setUp(self):
        RB.Bound.setUp(self)
        self.events = []
        self.typing(REASON)

    def typing(self, reason, **kw):
        def read(prompt):
            self.events.append(("ask", prompt))
            if re.search(r"type [A-Z0-9]{6} to sign", prompt): return T.type_challenge(prompt)
            return reason + "\n"
        T.install_test_approver(self, read_line=read, write=lambda s: self.events.append(("show", s)), **kw)

    def asked(self, kind):
        if kind == "challenge": return [p for e, p in self.events if e == "ask" and "to sign" in p]
        return [p for e, p in self.events if e == "ask" and "to sign" not in p]

    def run_both(self, engines=None):
        n = se.run_iteration(self.spec, self.ws, engines or self.engines(), runs=1, log=quiet)
        se.grade_iteration(self.spec, self.ws, n, self.grader(), log=quiet)
        return n

    def report(self, n=1):
        return se.report_iteration(self.spec, self.ws, n)

    def waive(self, tool, reason=REASON):
        self.typing(reason)
        return se.approve(waiver(tool), self.spec, self.ws)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


class TestMissingTools(Cov):
    def test_a_claude_and_codex_iteration_lists_opencode_and_hermes_as_gate_problems(self):
        n = self.run_both()
        bench, gate = self.report(n)
        self.assertEqual(missing_tools(gate), ["opencode", "hermes"], gate["problems"])
        self.assertEqual([p for p in gate["problems"] if not p.startswith("coverage-missing:")], [],
                         "control: the claude and codex iteration is otherwise clean")
        self.assertTrue(gate["needs_approval"])
        for tool in ("opencode", "hermes"):
            text = line_for(gate["problems"], tool)
            self.assertIn(NOT_AVAILABLE, text); self.assertIn("waiver missing", text)
        self.assertFalse(any(x.startswith("per-tool dynamic coverage") for x in gate["notes"]), gate["notes"])
        self.assertEqual(rjson(os.path.join(self.ws, "iteration-%d" % n, "gate.json")), gate)

    def test_codex_not_selected_is_a_missing_tool(self):
        n, bench, gate = RB.Bound.report(self)
        self.assertEqual(missing_tools(gate), ["codex", "opencode", "hermes"])
        self.assertIn("not-run: engine not selected", line_for(gate["problems"], "codex"))

    def test_all_four_run_has_no_coverage_problem_and_a_bench_without_tool_coverage_fails_closed(self):
        clean = RB.B1_FIXTURES["clean"][0]
        ran = {t: {"status": "run", "engine": t, "graded_runs": 1, "pending_runs": 0} for t in TOOLS}
        self.assertEqual(se.compute_gate(dict(clean, tool_coverage=ran))["problems"], [])
        for label, cov in (("absent", None), ("not an object", "run"), ("empty", {})):
            with self.subTest(label):
                bench = dict(clean) if cov is None else dict(clean, tool_coverage=cov)
                gate = se.compute_gate(bench)
                self.assertEqual(missing_tools(gate), list(TOOLS), gate["problems"])
                self.assertTrue(gate["needs_approval"])
        no_hermes = {t: v for t, v in ran.items() if t != "hermes"}
        self.assertEqual(missing_tools(se.compute_gate(dict(clean, tool_coverage=no_hermes))), ["hermes"])
        zero = dict(ran, claude=dict(ran["claude"], graded_runs=0))
        self.assertEqual(missing_tools(se.compute_gate(dict(clean, tool_coverage=zero))), ["claude"])

    def test_the_loop_stops_with_needs_coverage_and_calls_no_builder(self):
        r = se.loop(self.spec, self.ws, self.engines(), self.grader(), lambda it, sd: self.fail("builder called"), runs=1,
                    log=quiet)
        self.assertEqual(r, {"status": "needs-coverage", "iteration": 1})
        self.assertEqual(se.iterations(self.ws), [1])

    def test_coverage_problems_leave_human_pending_only_and_mark_coverage_only(self):
        two = se.tool_coverage(["claude", "codex"], {"claude": 1, "codex": 1})
        gate = se.compute_gate(dict(RB.B1_FIXTURES["human-only"][0], tool_coverage=two))
        self.assertEqual(missing_tools(gate), ["opencode", "hermes"])
        self.assertTrue(gate["human_pending_only"]); self.assertFalse(gate["coverage_only"])
        gate = se.compute_gate(dict(RB.B1_FIXTURES["clean"][0], tool_coverage=two))
        self.assertEqual((missing_tools(gate), gate["coverage_only"], gate["human_pending_only"]),
                         (["opencode", "hermes"], True, False))

    def test_every_not_run_tool_of_the_comparison_table_has_a_problem_or_a_waiver(self):
        n = self.run_both()
        self.waive("hermes")
        bench, gate = self.report(n)
        md = T.read(os.path.join(self.ws, "iteration-%d" % n, "comparison.md"))
        self.assertIn("| tool | static parity (eva.py) | dynamic eval | graded runs | registration |", md)
        rows = {}
        for line in md.split("## Tool coverage")[1].split("\n## ")[0].splitlines():
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) == 5 and cells[0] in TOOLS: rows[cells[0]] = cells
        self.assertEqual(sorted(rows), sorted(TOOLS))
        self.assertEqual(rows["opencode"][2:], [NOT_AVAILABLE, "0", "coverage-missing"])
        self.assertEqual(rows["hermes"][2:], [NOT_AVAILABLE, "0", "waived by test-approver"])
        self.assertEqual((rows["claude"][2], rows["claude"][4]), ("run", "run"))
        for tool, cells in rows.items():
            if cells[2] == "run": continue
            if cells[4] == "coverage-missing": self.assertIn(tool, missing_tools(gate))
            else: self.assertTrue(any(x.startswith("coverage waived: %s " % tool) for x in gate["notes"]), gate["notes"])


class TestWaiver(Cov):
    def test_a_valid_waiver_clears_only_its_tool(self):
        n = self.run_both()
        rec = self.waive("hermes")
        self.assertEqual(rec["reason"], REASON)
        self.assertEqual(rjson(self.approvals)[waiver("hermes")], rec)
        self.assertEqual(se.approval_verdict(waiver("hermes"), self.spec, self.ws).status, "approved")
        bench, gate = self.report(n)
        self.assertEqual(missing_tools(gate), ["opencode"])
        self.assertIn("coverage waived: hermes (dynamic eval %s) by test-approver: %s" % (NOT_AVAILABLE, REASON),
                      gate["notes"])
        self.assertEqual(bench["coverage_waivers"]["hermes"]["status"], "approved")
        self.assertEqual(bench["coverage_waivers"]["opencode"]["status"], "missing")

    def test_the_reason_is_asked_after_the_summary_and_before_the_challenge(self):
        n = self.run_both(); self.report(n)
        del self.events[:]
        self.waive("hermes")
        shown = [s for e, s in self.events if e == "show"]
        order = [e if e == "show" else ("challenge" if "to sign" in s else "reason") for e, s in self.events]
        self.assertEqual(order.count("reason"), 1); self.assertEqual(order.count("challenge"), 1)
        self.assertLess(order.index("reason"), order.index("challenge"))
        self.assertLess(order.index("show"), order.index("reason"))
        self.assertTrue(any("hermes" in s and NOT_AVAILABLE in s and "iteration-%d" % n in s for s in shown), shown)
        self.assertTrue(any(se.skill_tree_hash(self.d) in s for s in shown), shown)
        self.assertTrue(any(se.gate_hash("eval_set", self.spec, self.ws) in s for s in shown), shown)
        self.assertTrue(any("clears only coverage-missing:hermes" in s and "branch-setup" in s for s in shown), shown)
        self.assertTrue(any(("reason " + REASON) in s for s in shown), shown)
        self.assertIn("ruleset_sha256 " + se.ruleset_sha256(), shown)

    def test_a_waiver_for_another_eval_set_no_longer_counts(self):
        n = self.run_both()
        self.waive("hermes")
        wjson(os.path.join(self.d, "evals", "trigger_evals.json"), T.TRIGGERS[::-1])
        bench, gate = self.report(n)
        self.assertEqual(missing_tools(gate), ["opencode", "hermes"])
        self.assertIn("waiver stale (the gated files changed since signing)", line_for(gate["problems"], "hermes"))

    def test_a_waiver_made_before_a_rule_change_reads_stale(self):
        n = self.run_both()
        self.waive("hermes")
        RB.change_rule_message(self.root)
        bench, gate = self.report(n)
        self.assertIn("hermes", missing_tools(gate))
        self.assertIn("waiver stale (%s)" % A.RULES_CHANGED, line_for(gate["problems"], "hermes"))
        self.assertEqual(se.approval_label(se.approval_verdict(waiver("hermes"), self.spec, self.ws)), RB.RULES_LABEL)

    def test_an_unsigned_waiver_does_not_count(self):
        n = self.run_both()
        h = se.gate_hash(waiver("hermes"), self.spec, self.ws)
        wjson(self.approvals, {waiver("hermes"): {"sha256": h, "by": "pinesky", "at": AT, "reason": REASON,
                                                  "ruleset_sha256": se.ruleset_sha256()}})
        bench, gate = self.report(n)
        self.assertEqual(missing_tools(gate), ["opencode", "hermes"])
        self.assertIn("waiver unsigned", line_for(gate["problems"], "hermes"))

    def test_a_wrong_key_waiver_reads_invalid(self):
        k = T.shared_keys()
        other_key, _ = T.make_key(tempfile.mkdtemp(dir=k["dir"]), "intruder", T.TEST_PASSPHRASE)
        gate, h, rules = waiver("hermes"), se.gate_hash(waiver("hermes"), self.spec, self.ws), se.ruleset_sha256()
        data = A.payload("approve", gate, "branch-setup", h, rules, AT, reason=REASON)
        def record(key):
            sig = A.sign_payload(T.keygen_runner(k["dir"]), key, data, passphrase=T.TEST_PASSPHRASE)
            return {"format": 1, "method": "ssh-sig", "sha256": h, "ruleset_sha256": rules, "principal": T.TEST_PRINCIPAL,
                    "at": AT, "reason": REASON, "signature": sig}
        ap = se.APPROVER["it"]
        self.assertEqual(ap.verdict(record(k["key"]), "approve", gate, "branch-setup", h, rules).status, "approved",
                         "control: the record shape verifies with the listed key")
        v = ap.verdict(record(other_key), "approve", gate, "branch-setup", h, rules)
        self.assertEqual(v.status, "invalid"); self.assertIn("signature does not verify", v.reason)

    def test_a_tampered_waiver_reads_invalid(self):
        rec = self.waive("hermes")
        gate, h, rules, ap = waiver("hermes"), se.gate_hash(waiver("hermes"), self.spec, self.ws), se.ruleset_sha256(), \
            se.APPROVER["it"]
        for label, bad in (("another reason", dict(rec, reason="a different reason that nobody typed")),
                           ("another time", dict(rec, at="2026-10-25T10:00:00Z"))):
            with self.subTest(label):
                v = ap.verdict(bad, "approve", gate, "branch-setup", h, rules)
                self.assertEqual(v.status, "invalid"); self.assertIn("signature does not verify", v.reason)
        for label, bad in (("no reason", {k: v for k, v in rec.items() if k != "reason"}),
                           ("non-ASCII reason", dict(rec, reason="ok " + KOREAN)), ("empty reason", dict(rec, reason="")),
                           ("long reason", dict(rec, reason="x" * 201)), ("reason not a string", dict(rec, reason=5))):
            with self.subTest(label):
                v = ap.verdict(bad, "approve", gate, "branch-setup", h, rules)
                self.assertEqual(v.status, "invalid"); self.assertIn("reason", v.reason)

    def test_a_waiver_does_not_move_to_another_tool_or_unit(self):
        n = self.run_both()
        rec = self.waive("hermes")
        wjson(self.approvals, {waiver("opencode"): rec})
        v = se.approval_verdict(waiver("opencode"), self.spec, self.ws)
        self.assertNotEqual(v.status, "approved", v)
        bench, gate = self.report(n)
        self.assertEqual(missing_tools(gate), ["opencode", "hermes"])
        h, rules = se.gate_hash(waiver("hermes"), self.spec, self.ws), se.ruleset_sha256()
        v = se.APPROVER["it"].verdict(rec, "approve", waiver("hermes"), "other-skill", h, rules)
        self.assertEqual(v.status, "invalid"); self.assertIn("signature does not verify", v.reason)

    def test_empty_non_ascii_control_and_long_reasons_refuse_and_sign_nothing(self):
        self.run_both()
        se.approve("eval_set", self.spec, self.ws)
        before = state(self.approvals)
        for bad in ("", "   ", "\t", KOREAN, "caf" + chr(0xE9), "ok" + chr(0x1B) + "[2J", "a" + chr(0x7F), "x" * 201):
            with self.subTest(bad=bad):
                del self.events[:]
                self.typing(bad)
                with self.assertRaises(A.Refused) as cm: se.approve(waiver("hermes"), self.spec, self.ws)
                self.assertIn("reason", str(cm.exception)); self.assertIn("nothing was signed", str(cm.exception))
                self.assertEqual(self.asked("challenge"), [])
                self.assertEqual(state(self.approvals), before)
        self.assertEqual(self.waive("hermes", "  " + "x" * 200 + "  ")["reason"], "x" * 200)

    def test_no_flag_variable_or_file_supplies_the_reason(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            se.build_parser().parse_args(["approve", self.d, "--gate", waiver("hermes"), "--reason", REASON])
        self.run_both()
        write(os.path.join(self.ws, "reason.txt"), REASON)
        with mock.patch.dict(os.environ, {"SKILL_EVAL_REASON": REASON, "REASON": REASON}):
            self.typing("")
            with self.assertRaises(A.Refused): se.approve(waiver("hermes"), self.spec, self.ws)
        self.assertEqual(len(self.asked("reason")), 1)
        self.assertFalse(os.path.exists(self.approvals))

    def test_the_waiver_gate_is_the_human_gate(self):
        self.run_both()
        se.approve("eval_set", self.spec, self.ws)
        before = state(self.approvals)
        argv = ("approve", self.d, "--workspace", self.ws, "--gate", waiver("hermes"))
        with mock.patch.object(A, "is_tty", lambda fd: False):
            rc, out, err = self.cli(*argv)
        self.assertEqual(rc, 2, err)
        self.assertEqual(err.splitlines()[0], "approve needs a human at an interactive terminal (stdin is not a TTY)")
        procs = {T.TEST_START_PID: (77, "python3", "/usr/bin/python3"), 77: (1, "claude.exe", "/opt/claude.exe"),
                 1: (0, "launchd", "/sbin/launchd")}
        self.typing(REASON, read_proc=A.table_reader(procs))
        rc, out, err = self.cli(*argv)
        self.assertEqual(rc, 2, err)
        self.assertIn("agent process in the parent chain: claude.exe (pid 77)", err)
        self.typing(REASON, environ=dict(T.AGENT_ENV, CLAUDECODE="1"))
        rc, out, err = self.cli(*argv)
        self.assertEqual(rc, 2, err); self.assertIn("agent environment variables are set: CLAUDECODE", err)
        self.assertEqual(self.asked("reason"), []); self.assertEqual(state(self.approvals), before)
        self.typing(REASON)
        rc, out, err = self.cli(*argv)
        self.assertEqual(rc, 0, err); self.assertIn("approved %s" % waiver("hermes"), out)
        self.assertEqual(rjson(self.approvals)[waiver("hermes")]["reason"], REASON)


class TestRegistration(Cov):
    def test_registration_refuses_and_names_the_missing_tools_while_a_registration_approval_exists(self):
        self.run_both(); self.report()
        se.approve("registration", self.spec, self.ws)
        self.assertEqual(se.approval_verdict("registration", self.spec, self.ws).status, "approved")
        del self.events[:]
        with self.assertRaises(se.EvalError) as cm: se.register(self.spec, self.ws)
        msg = str(cm.exception)
        self.assertIn("coverage-missing:opencode", msg); self.assertIn("coverage-missing:hermes", msg)
        self.assertNotIn("coverage-missing:claude", msg); self.assertNotIn("coverage-missing:codex", msg)
        self.assertIn("coverage_waiver:<tool>", msg)
        self.assertEqual(self.asked("challenge"), [])
        self.assertFalse(os.path.exists(os.path.join(self.ws, "registration.json")))

    def test_registration_recomputes_coverage_and_never_reads_it_from_gate_json(self):
        n = self.run_both(); bench, gate = self.report()
        clean = dict(gate, problems=[], needs_approval=False, coverage_only=False)
        wjson(os.path.join(self.ws, "iteration-%d" % n, "gate.json"), clean)
        wjson(os.path.join(self.ws, "iteration-%d" % n, "benchmark.json"), dict(bench, gate=clean))
        with self.assertRaises(se.EvalError) as cm: se.registration_record(self.spec, self.ws)
        self.assertIn("coverage-missing:hermes", str(cm.exception))

    def test_coverage_is_checked_before_the_other_registration_checks(self):
        self.run_both(); self.report()
        write(os.path.join(self.d, "SKILL.md"), T.SKILL_MD + "\nMore.\n")
        with self.assertRaises(se.EvalError) as cm: se.registration_record(self.spec, self.ws)
        self.assertIn("coverage-missing:opencode", str(cm.exception))

    def test_waivers_signed_after_a_report_count_at_the_next_report_and_then_registration_is_automatic(self):
        n = self.run_both(); self.report()
        self.waive("opencode"); self.waive("hermes")
        self.assertEqual(missing_tools(rjson(os.path.join(self.ws, "iteration-%d" % n, "gate.json"))),
                         ["opencode", "hermes"])
        with self.assertRaises(se.EvalError) as cm: se.registration_record(self.spec, self.ws)
        self.assertNotIn("cannot be registered", str(cm.exception))
        self.assertIn("needs `approve --gate registration`", str(cm.exception))
        bench, gate = self.report(n)
        self.assertEqual(gate["problems"], [])
        self.assertEqual(sorted(x for x in gate["notes"] if x.startswith("coverage waived: ")),
                         ["coverage waived: hermes (dynamic eval %s) by test-approver: %s" % (NOT_AVAILABLE, REASON),
                          "coverage waived: opencode (dynamic eval %s) by test-approver: %s" % (NOT_AVAILABLE, REASON)])
        rec = se.register(self.spec, self.ws)
        self.assertEqual(rec["approval"], "auto")

    def store(self, n, bench, gate_of_bench=True):
        if gate_of_bench: bench = dict(bench, gate=se.compute_gate(bench))
        wjson(os.path.join(self.ws, "iteration-%d" % n, "benchmark.json"), bench)
        wjson(os.path.join(self.ws, "iteration-%d" % n, "gate.json"), bench["gate"])

    def refused(self):
        del self.events[:]
        with self.assertRaises(se.EvalError) as cm: se.register(self.spec, self.ws)
        self.assertEqual(self.asked("challenge"), [])
        self.assertFalse(os.path.exists(os.path.join(self.ws, "registration.json")))
        return str(cm.exception)

    def test_a_stored_tool_coverage_forged_to_run_with_a_matching_gate_refuses_and_writes_nothing(self):
        n = self.run_both(); bench, gate = self.report(n)
        self.assertEqual(sorted({r["engine"] for r in bench["runs"]}), ["claude", "codex"])
        forged = dict(bench["tool_coverage"])
        for tool in ("opencode", "hermes"):
            forged[tool] = {"status": "run", "engine": tool, "graded_runs": 1, "pending_runs": 0}
        self.store(n, dict(bench, tool_coverage=forged))
        self.assertEqual(rjson(os.path.join(self.ws, "iteration-%d" % n, "gate.json"))["problems"], [],
                         "control: the forged pair carries a clean gate")
        msg = self.refused()
        self.assertIn("tool_coverage does not match its runs", msg)
        self.assertIn("opencode", msg); self.assertIn("hermes", msg)

    def test_a_stored_run_claim_the_runs_do_not_back_refuses_even_when_every_tool_is_waived(self):
        n = self.run_both(); self.waive("opencode"); self.waive("hermes")
        bench, gate = self.report(n)
        self.assertEqual(gate["problems"], [])
        forged = dict(bench["tool_coverage"], opencode={"status": "run", "engine": "opencode", "graded_runs": 1,
                                                        "pending_runs": 0})
        self.store(n, dict(bench, tool_coverage=forged))
        msg = self.refused()
        self.assertIn("tool_coverage does not match its runs: opencode", msg)
        self.assertNotIn("hermes", msg)

    def test_a_present_but_malformed_stored_tool_coverage_refuses(self):
        n = self.run_both(); self.waive("opencode"); self.waive("hermes")
        bench, gate = self.report(n)
        ran = bench["tool_coverage"]
        for label, cov in (("null", None), ("a string", "run"), ("a list", []),
                           ("an entry not an object", dict(ran, claude="run")),
                           ("an entry without a status", dict(ran, codex={"graded_runs": 1})),
                           ("an unknown tool", dict(ran, gemini={"status": "run"}))):
            with self.subTest(label):
                self.store(n, dict(bench, tool_coverage=cov), gate_of_bench=False)
                msg = self.refused()
                self.assertIn("tool_coverage is malformed", msg)

    def test_an_old_benchmark_without_stored_tool_coverage_and_with_waivers_reaches_the_signing_step(self):
        n = self.run_both(); self.waive("opencode"); self.waive("hermes")
        bench, gate = self.report(n)
        self.assertEqual(gate["problems"], [])
        self.store(n, {k: v for k, v in bench.items() if k != "tool_coverage"}, gate_of_bench=False)
        del self.events[:]
        rec = se.register(self.spec, self.ws)
        self.assertEqual(rec["approval"], "auto")
        self.assertEqual(len(self.asked("challenge")), 1)
        self.assertEqual(rjson(os.path.join(self.ws, "registration.json")), rec)

    def test_the_signing_summary_lists_each_required_tools_coverage_and_each_waivers_principal_and_reason(self):
        n = self.run_both(); self.waive("opencode"); self.waive("hermes", reason="Hermes has no engine yet")
        self.report(n)
        del self.events[:]
        se.register(self.spec, self.ws)
        shown = [s for e, s in self.events if e == "show"]
        self.assertEqual([s for s in shown if s.startswith("register coverage ")],
                         ["register coverage claude=run",
                          "register coverage codex=run",
                          "register coverage opencode=%s (waived by test-approver, reason: %s)" % (NOT_AVAILABLE, REASON),
                          "register coverage hermes=%s (waived by test-approver, reason: Hermes has no engine yet)"
                          % NOT_AVAILABLE])
        first_challenge = next(i for i, (e, p) in enumerate(self.events) if e == "ask" and "to sign" in p)
        last_coverage = max(i for i, (e, s) in enumerate(self.events) if e == "show" and s.startswith("register coverage "))
        self.assertLess(last_coverage, first_challenge)


class TestPolicy(Cov):
    def policy_copy(self, edit):
        d = tempfile.mkdtemp(dir=self.tmp)
        for name in os.listdir(os.path.join(ROOT, "policy")):
            shutil.copy(os.path.join(ROOT, "policy", name), d)
        fp = os.path.join(d, "gate.rules.json")
        doc = rjson(fp); edit(doc); wjson(fp, doc)
        return d

    def test_the_four_tools_are_pinned_to_eva(self):
        self.assertEqual(A.COVERAGE_TOOLS, eva.TOOLS)
        self.assertEqual(A.WAIVER_GATES, tuple("coverage_waiver:" + t for t in eva.TOOLS))
        self.assertEqual(rjson(os.path.join(ROOT, "policy", "gate.rules.json"))["coverage_required"], list(TOOLS))
        self.assertEqual(se.COVERAGE_REQUIRED, TOOLS)
        with mock.patch.object(se, "POLICY_DIR", self.policy_copy(lambda doc: None)):
            self.assertEqual(se.load_coverage_required(), TOOLS)

    def test_the_loader_refuses_a_malformed_coverage_required(self):
        def put(value):
            return lambda doc: doc.update(coverage_required=value)
        cases = (("missing", lambda doc: doc.pop("coverage_required")), ("a string", put("hermes")),
                 ("empty", put([])), ("duplicate", put(["claude", "claude"])), ("unknown tool", put(["claude", "gemini"])),
                 ("not a name", put(["claude", 1])), ("null", put(None)), ("an object", put({"claude": True})),
                 ("case", put(["Claude"])))
        for label, edit in cases:
            with self.subTest(label), mock.patch.object(se, "POLICY_DIR", self.policy_copy(edit)):
                with self.assertRaises(se.PolicyError) as cm: se.load_coverage_required()
                self.assertIn("policy/gate.rules.json: coverage_required", str(cm.exception))
        with mock.patch.object(se, "POLICY_DIR", self.policy_copy(put(["hermes", "claude"]))):
            self.assertEqual(se.load_coverage_required(), ("claude", "hermes"))

    def test_removing_hermes_moves_the_ruleset_and_existing_approvals_read_stale(self):
        n = self.run_both()
        self.waive("hermes"); se.approve("eval_set", self.spec, self.ws)
        before = se.ruleset_sha256()
        fp = os.path.join(self.root, "policy", "gate.rules.json")
        doc = rjson(fp); doc["coverage_required"].remove("hermes"); wjson(fp, doc)
        self.assertNotEqual(se.ruleset_sha256(), before)
        for gate in (waiver("hermes"), "eval_set"):
            v = se.approval_verdict(gate, self.spec, self.ws)
            self.assertEqual((v.status, v.reason), ("stale", A.RULES_CHANGED), gate)
        with mock.patch.object(se, "POLICY_DIR", os.path.join(self.root, "policy")):
            required = se.load_coverage_required()
        self.assertEqual(required, ("claude", "codex", "opencode"))
        with mock.patch.object(se, "COVERAGE_REQUIRED", required):
            bench, gate = self.report(n)
        self.assertEqual(missing_tools(gate), ["opencode"])
        self.assertTrue(any(x.startswith("coverage not required: hermes") for x in gate["notes"]), gate["notes"])
        self.assertIn("stale: rules changed since this iteration ran", gate["problems"])


class TestPayloadAndHash(Cov):
    def test_non_waiver_payloads_are_byte_identical_and_a_waiver_adds_one_reason_line(self):
        self.assertEqual(A.payload("approve", "eval_set", "u", SHA, OTHER, AT),
                         ("skill-master-approval v1\nkind approve\ngate eval_set\nunit u\nsha256 %s\nruleset_sha256 "
                          "%s\nat %s\n" % (SHA, OTHER, AT)).encode("ascii"))
        self.assertEqual(A.payload("approve", waiver("hermes"), "u", SHA, OTHER, AT, reason=REASON),
                         ("skill-master-approval v1\nkind approve\ngate coverage_waiver:hermes\nunit u\nsha256 %s\n"
                          "ruleset_sha256 %s\nreason %s\nat %s\n" % (SHA, OTHER, REASON, AT)).encode("ascii"))

    def test_the_payload_refuses_other_colon_names_and_misplaced_or_bad_reasons(self):
        for gate in ("coverage_waiver:gemini", "coverage_waiver:", "eval_set:x", "coverage_waiver:hermes:x",
                     "coverage_waiver:Hermes"):
            with self.subTest(gate=gate), self.assertRaises(ValueError):
                A.payload("approve", gate, "u", SHA, OTHER, AT, reason=REASON)
        for kind, budget in (("register", None), ("live", 1.0)):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                A.payload(kind, waiver("hermes"), "u", SHA, OTHER, AT, budget_usd=budget, reason=REASON)
        for bad in (None, "", " x", "x ", KOREAN, "a\nb", "x" * 201, 5):
            with self.subTest(reason=bad), self.assertRaises(ValueError):
                A.payload("approve", waiver("hermes"), "u", SHA, OTHER, AT, reason=bad)
        with self.assertRaises(ValueError): A.payload("approve", "eval_set", "u", SHA, OTHER, AT, reason=REASON)

    def test_a_reason_on_a_non_waiver_record_reads_invalid(self):
        rec = se.approve("eval_set", self.spec, self.ws)
        h, rules = se.gate_hash("eval_set", self.spec, self.ws), se.ruleset_sha256()
        self.assertEqual(se.APPROVER["it"].verdict(rec, "approve", "eval_set", "branch-setup", h, rules).status, "approved")
        v = se.APPROVER["it"].verdict(dict(rec, reason=REASON), "approve", "eval_set", "branch-setup", h, rules)
        self.assertEqual(v.status, "invalid"); self.assertIn("reason", v.reason)

    def test_the_waiver_hash_binds_the_tool_the_skill_and_the_eval_set(self):
        with self.assertRaises(se.EvalError) as cm: se.gate_hash("coverage_waiver:gemini", self.spec, self.ws)
        self.assertIn("claude, codex, opencode, hermes", str(cm.exception))
        hashes = {t: se.gate_hash(waiver(t), self.spec, self.ws) for t in TOOLS}
        self.assertEqual(len(set(hashes.values())), 4)
        self.assertTrue(all(re.fullmatch("[0-9a-f]{64}", h) for h in hashes.values()))
        write(os.path.join(self.d, "SKILL.md"), T.SKILL_MD + "\nMore.\n")
        after_skill = se.gate_hash(waiver("hermes"), self.spec, self.ws)
        self.assertNotEqual(after_skill, hashes["hermes"])
        wjson(os.path.join(self.d, "evals", "trigger_evals.json"), T.TRIGGERS[::-1])
        self.assertNotEqual(se.gate_hash(waiver("hermes"), self.spec, self.ws), after_skill)


class TestPlan(Cov):
    def test_plan_states_the_four_tool_or_signed_waiver_rule_and_each_waiver_state(self):
        line = self.line(self.plan(), "registration coverage: ")
        self.assertIn("coverage_required (claude, codex, opencode, hermes)", line)
        self.assertIn("coverage_waiver:<tool>", line)
        self.assertTrue(line.endswith("claude=NOT APPROVED, codex=NOT APPROVED, opencode=NOT APPROVED, "
                                      "hermes=NOT APPROVED"), line)
        self.waive("hermes")
        line = self.line(self.plan(), "registration coverage: ")
        self.assertTrue(line.endswith("hermes=approved by test-approver"), line)


class TestGoldenCopy(T.Base):
    """Lane 1 in process: report on a temp copy of golden csv-cleaner iteration 4 (a Claude-only iteration)."""
    def test_report_on_a_copy_of_golden_iteration_4_lists_the_missing_tools_as_problems(self):
        src = os.path.join(ROOT, "golden", "skills")
        skill = shutil.copytree(os.path.join(src, "csv-cleaner"), os.path.join(self.tmp, "csv-cleaner"))
        ws = shutil.copytree(os.path.join(src, "csv-cleaner-workspace"), os.path.join(self.tmp, "csv-cleaner-workspace"))
        spec, _ = se.load_eval_set(skill)
        bench, gate = se.report_iteration(spec, ws, 4)
        self.assertEqual(missing_tools(gate), ["codex", "opencode", "hermes"], gate["problems"])
        self.assertFalse(any(x.startswith("per-tool dynamic coverage") for x in gate["notes"]))


if __name__ == "__main__":
    unittest.main()
