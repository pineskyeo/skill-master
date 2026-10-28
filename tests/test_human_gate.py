"""Recorded approvals (unit N1, after the signed human gate of unit H1). This file is ASCII only.

approve (every gate), approve --batch, register, the start of every --live run, the coverage waivers and the machine
waiver need no approver key, signers file, ssh-agent, passphrase, typed challenge, TTY, agent-ancestry check or
approver session. Each writes an unsigned recorded record (engine/approvals.py, method recorded) that binds kind, gate,
unit, sha256 and ruleset_sha256, plus budget_usd for --live and the reason for a coverage waiver; a record moved to
other content, another gate, unit, kind, ruleset or budget is not approved. A signed record from before N1 still
verifies with ssh-keygen against a signers file; a legacy record with neither signature nor method stays unsigned.

Command cases run the real CLI in a subprocess on temp fixtures with a piped stdin (not a TTY), an empty temp HOME,
stub claude and codex first on PATH and a sitecustomize audit hook (GATE_SITE) that blocks and records any touch of the
real ~/.config/skill-master and any start of ssh-add or ssh-keygen. In-process cases use the command-line approver over
an empty temp account home with a non-TTY stdin and a process runner that refuses everything; only the signed-record
cases start /usr/bin/ssh-keygen, on throwaway keys under /tmp.
"""
import contextlib, hashlib, io, json, os, pwd, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_agent_eval as AE
import test_approval_batch as B
from test_skill_eval import se, evals_doc, make_skill, write, wjson, rjson, quiet, FakeClaude
from engine import approvals as A

ROOT = T.ROOT
GATES = ("requirements", "eval_set", "requirements_eval_set")
SHA = "ab" * 32
OTHER = "cd" * 32
RULES = "ef" * 32
AT = T.OLD_AT
REASON = "OpenCode is not installed on this Mac"
WAIVER = "coverage_waiver:opencode"
KOREAN = "".join(map(chr, (0xD55C, 0xAD6D, 0xC5B4, 0x20, 0xD14D, 0xC2A4, 0xD2B8)))
RLO = chr(0x202E)
SPOOF = "\x1b[2J\rX"
SPOOF_SHOWN = "\\x1b[2J\\rX"
NON_OBJECTS = ("[1]", "[]", '"x"', "5", "true", "null")
RECORD_FIELDS = ["at", "format", "gate", "kind", "method", "recorded_by", "ruleset_sha256", "sha256", "unit"]
USER = pwd.getpwuid(os.getuid()).pw_name
MACHINE = "11111111-2222-3333-4444-555555555555"
UNSIGNED_NOTE = "approvals are unsigned recorded decisions; no approver key, ssh-agent or approver session is needed"


class Stop(Exception):
    """Raised by a test hook to stop a command at a chosen line."""


def forbidden(*a, **k):
    raise AssertionError("reached a step that must not run: %r" % (a[:1],))


def state(path):
    if not os.path.exists(path): return None
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


GATE_SITE = r'''
import os, pwd, sys
_mark = os.environ.get("GATE_SENTINEL")
_root = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".config", "skill-master")
_tools = ("ssh-add", "ssh-keygen", "ssh-agent")

def _hit(what):
    with open(_mark, "a") as f: f.write(what + "\n")
    raise PermissionError("gate sentinel: " + what)

def _under(p):
    try: p = os.fsdecode(p)
    except TypeError: return False
    p = os.path.abspath(p)
    return p == _root or p.startswith(_root + os.sep)

def _tool(x):
    try: return os.path.basename(os.fsdecode(x)) in _tools
    except TypeError: return False

def _hook(event, args):
    if event in ("open", "os.listdir", "os.scandir") and args and _under(args[0]):
        _hit("%s %s" % (event, args[0]))
    elif event in ("subprocess.Popen", "os.posix_spawn", "os.exec", "os.spawn"):
        argv = args[1] if len(args) > 1 and isinstance(args[1], (list, tuple)) and args[1] else []
        if _tool(args[0] or "") or (argv and _tool(argv[0])):
            _hit("%s %r" % (event, args[0] or argv[0]))

def _wrap(fn):
    def stat(p, *a, **k):
        if _under(p): _hit("stat %s" % p)
        return fn(p, *a, **k)
    return stat

if _mark:
    os.stat, os.lstat = _wrap(os.stat), _wrap(os.lstat)
    sys.addaudithook(_hook)
'''


class Cli(object):
    """Subprocess fixture: stub claude and codex first on a PATH that holds nothing else but /usr/bin:/bin, an empty temp
    HOME, the GATE_SITE sentinel, SSH_AUTH_SOCK removed, a piped stdin (default: empty), a 60 s timeout."""
    def cli_setup(self):
        self.ctmp = tempfile.mkdtemp(prefix="gate-cli-"); self.addCleanup(shutil.rmtree, self.ctmp, True)
        self.stubs = os.path.join(self.ctmp, "stubs"); os.makedirs(self.stubs)
        self.marker = os.path.join(self.ctmp, "stub-started.txt")
        for name in ("claude", "codex"):
            p = os.path.join(self.stubs, name)
            write(p, '#!/bin/sh\necho "%s $*" >> "%s"\nexit 1\n' % (name, self.marker))
            os.chmod(p, 0o755)
        self.site = os.path.join(self.ctmp, "site"); os.makedirs(self.site)
        write(os.path.join(self.site, "sitecustomize.py"), GATE_SITE)
        self.hits = os.path.join(self.ctmp, "sentinel.txt")
        self.chome = os.path.join(self.ctmp, "home"); os.makedirs(self.chome)

    def child_env(self):
        env = {k: v for k, v in os.environ.items() if k != "SSH_AUTH_SOCK"}
        env.update(PATH=self.stubs + ":/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=self.site,
                   GATE_SENTINEL=self.hits, HOME=self.chome)
        return env

    def run_cli(self, *argv, script="skill_eval.py", stdin=""):
        p = subprocess.run([sys.executable, "-B", script] + list(argv), cwd=ROOT, input=stdin, capture_output=True,
                           text=True, env=self.child_env(), timeout=60)
        return p.returncode, p.stdout, p.stderr

    def assert_nothing_started(self):
        self.assertEqual(T.read(self.marker) if os.path.exists(self.marker) else "", "", "a stub CLI started")
        self.assertEqual(T.read(self.hits) if os.path.exists(self.hits) else "", "",
                         "the child reached the real approver files or an ssh tool")


class GateFixture(unittest.TestCase, Cli):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gate-fx-"); self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = os.path.join(self.tmp, "ws")
        self.cli_setup()
        self.d = make_skill(self.tmp)
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")
        it = os.path.join(self.ws, "iteration-1"); os.makedirs(it)
        wjson(os.path.join(it, "benchmark.json"), {"metadata": {}})
        self.approvals = os.path.join(self.ws, "approvals.json")

    def unsigned(self):
        spec = se.load_eval_set(self.d)[0]
        wjson(self.approvals, {g: {"sha256": se.gate_hash(g, spec, self.ws) or SHA, "by": "pinesky", "at": AT}
                               for g in ("requirements", "eval_set", "rule_review", "iteration-1", "registration")})

    def record(self, gate):
        return rjson(self.approvals)[gate]

    def assert_recorded(self, rec, gate):
        spec = se.load_eval_set(self.d)[0]
        self.assertEqual(sorted(rec), sorted(RECORD_FIELDS + (["reason"] if gate in A.WAIVER_GATES else [])))
        self.assertEqual((rec["format"], rec["method"], rec["kind"], rec["gate"], rec["unit"], rec["recorded_by"]),
                         (1, "recorded", "approve", gate, "branch-setup", USER))
        self.assertEqual((rec["sha256"], rec["ruleset_sha256"]), (se.gate_hash(gate, spec, self.ws), se.ruleset_sha256()))
        with mock.patch.dict(se.APPROVER, {"it": T.gate_approver()}):
            v = se.approval_verdict(gate, spec, self.ws)
        self.assertEqual((v.status, v.principal), ("approved", USER))


class CliRecords(GateFixture):
    """`approve` in a child process with no TTY, an empty HOME and no ssh tool: the record is written and verifies."""
    def test_every_gate_is_recorded_with_no_key_no_tty_and_no_session(self):
        for g in GATES:
            with self.subTest(g):
                rc, out, err = self.run_cli("approve", self.d, "--workspace", self.ws, "--gate", g)
                self.assertEqual(rc, 0, err)
                self.assertIn("approved %s sha256=" % g, out)
                self.assertIn("approve: gate %s, unit branch-setup, sha256 " % g, err)
                self.assert_recorded(self.record(g), g)
                self.assert_nothing_started()

    def test_the_eva_route_records_like_the_direct_command(self):
        rc, out, err = self.run_cli("eval", "approve", self.d, "--workspace", self.ws, "--gate", "eval_set", script="eva.py")
        self.assertEqual(rc, 0, err)
        self.assert_recorded(self.record("eval_set"), "eval_set")
        self.assert_nothing_started()

    def test_a_coverage_waiver_reason_comes_from_the_option_or_one_piped_line(self):
        for how, argv, stdin in (("option", ["--reason", REASON], ""), ("stdin", [], REASON + "\n")):
            with self.subTest(how):
                rc, out, err = self.run_cli("approve", self.d, "--workspace", self.ws, "--gate", WAIVER, *argv,
                                            stdin=stdin)
                self.assertEqual(rc, 0, err)
                self.assertEqual(self.record(WAIVER)["reason"], REASON)
                self.assert_recorded(self.record(WAIVER), WAIVER)
        self.assert_nothing_started()

    def test_a_refused_reason_records_nothing(self):
        for argv, stdin, why in (([], "", "it is empty"), ([], "\x1b[2J\n", "not printable ASCII"),
                                 (["--reason", " padded"], "", "leading or trailing spaces"),
                                 (["--reason", "x" * 201], "", "longer than 200")):
            with self.subTest(why):
                rc, out, err = self.run_cli("approve", self.d, "--workspace", self.ws, "--gate", WAIVER, *argv,
                                            stdin=stdin)
                self.assertEqual(rc, 2, err)
                self.assertIn("approve: reason refused: ", err); self.assertIn(why, err)
                self.assertFalse(os.path.exists(self.approvals))
        rc, out, err = self.run_cli("approve", self.d, "--workspace", self.ws, "--gate", "eval_set", "--reason", REASON)
        self.assertEqual(rc, 2, err)
        self.assertIn("only a coverage waiver takes a reason", err)
        self.assertFalse(os.path.exists(self.approvals))
        self.assert_nothing_started()


class CliLive(GateFixture):
    """--live keeps every rail but the signature: a finite --budget-usd, --auth-profile and approved gates. Stub claude
    and codex are first on PATH, so a missing rail would start one."""
    def live(self, cmd, *extra):
        before = state(self.approvals)
        rc, out, err = self.run_cli(cmd, self.d, "--workspace", self.ws, "--live", *extra)
        self.assert_nothing_started()
        self.assertEqual(rc, 2, err)
        self.assertEqual(state(self.approvals), before)
        return err

    def test_no_budget_or_no_profile_refuses(self):
        self.unsigned()
        for extra, want in (((), se.NO_BUDGET), (("--budget-usd", "inf", "--auth-profile", "personal-mac"), se.NO_BUDGET),
                            (("--budget-usd", "0"), se.NO_BUDGET), (("--budget-usd", "1"), se.PROFILE_REQUIRED)):
            with self.subTest(extra):
                self.assertIn(want, self.live("run", "--engines", "claude", *extra))

    def test_unapproved_gates_refuse_every_live_command(self):
        self.unsigned()
        golden = os.path.join(self.tmp, "golden.json")
        wjson(golden, {"examples": [{"prompt": "p", "output": "o", "item": "i", "label": True}]})
        for cmd, extra, want in (("run", (), "not approved"), ("grade", ("--iteration", "1"), "has no plan.json"),
                                 ("calibrate", ("--iteration", "1", "--golden", golden), "not approved"),
                                 ("loop", ("--max-iterations", "1"), "not approved")):
            with self.subTest(cmd):
                err = self.live(cmd, "--engines", "claude", "--auth-profile", "personal-mac", "--budget-usd", "100", *extra)
                self.assertIn(want, err)


class CliRegister(T.Base, Cli):
    """`register` in a child process with no TTY and an empty HOME, automatic and with a registration approval."""
    def setUp(self):
        T.Base.setUp(self)
        self.cli_setup()

    def iteration(self, regress):
        spec, _ = self.spec(evals=evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude(regress))], runs=1,
                             log=quiet)
        se.grade_iteration(spec, self.ws, n, self.grader(), log=quiet)
        T.waive_coverage(spec, self.ws)
        se.report_iteration(spec, self.ws, n)
        return spec

    def assert_registered(self, spec):
        reg = rjson(os.path.join(self.ws, "registration.json"))
        rec = reg["signed"]
        self.assertEqual(sorted(rec), RECORD_FIELDS)
        self.assertEqual((rec["method"], rec["kind"], rec["gate"], rec["unit"], rec["recorded_by"], rec["sha256"]),
                         ("recorded", "register", "registration", "branch-setup", USER, se.registration_hash(reg)))
        self.assertEqual(se.registration_state(spec["skill_dir"], self.ws)[:2], ("registered", ""))
        self.assertEqual(se.registration_verdict(spec, self.ws).status, "approved")
        return reg

    def test_an_automatic_registration_is_recorded(self):
        spec = self.iteration(False)
        rc, out, err = self.run_cli("register", spec["skill_dir"], "--workspace", self.ws)
        self.assertEqual(rc, 0, err)
        self.assertIn("(auto)", out)
        self.assertEqual(self.assert_registered(spec)["approval"], "auto")
        self.assert_nothing_started()

    def test_a_registration_with_problems_needs_a_current_registration_approval(self):
        spec = self.iteration(True)
        reg = os.path.join(self.ws, "registration.json")
        wjson(os.path.join(self.ws, "approvals.json"), dict(rjson(os.path.join(self.ws, "approvals.json")),
              registration={"sha256": se.gate_hash("registration", spec, self.ws), "by": "pinesky", "at": AT}))
        rc, out, err = self.run_cli("register", spec["skill_dir"], "--workspace", self.ws)
        self.assertEqual(rc, 2, err)
        self.assertIn("needs `approve --gate registration` (unsigned: ", err)
        self.assertFalse(os.path.exists(reg))
        rc, out, err = self.run_cli("approve", spec["skill_dir"], "--workspace", self.ws, "--gate", "registration")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_cli("register", spec["skill_dir"], "--workspace", self.ws)
        self.assertEqual(rc, 0, err)
        stored = self.assert_registered(spec)
        self.assertEqual(stored["approval"], stored["approvals"]["registration"])
        self.assertEqual(stored["approval"]["recorded_by"], USER)
        self.assert_nothing_started()


def use_cli_approver(case):
    """The command-line approver with an empty account home, a stdin that is not a TTY (case.stdin, fed by case.feed)
    and approvals.run_process refusing every process."""
    case.account = os.path.join(case.tmp, "account"); os.makedirs(case.account)
    case.stdin = io.StringIO("")
    for pt in (mock.patch.object(A, "account_home", lambda: case.account), mock.patch.object(A, "run_process", T.no_process),
               mock.patch.object(sys, "stdin", case.stdin)):
        pt.start(); case.addCleanup(pt.stop)
    pt = mock.patch.dict(se.APPROVER, {"it": A.cli_approver()}); pt.start(); case.addCleanup(pt.stop)
    case.assertFalse(case.stdin.isatty())


def feed(case, text):
    pos = case.stdin.tell()
    case.stdin.seek(0, io.SEEK_END); case.stdin.write(text); case.stdin.seek(pos)


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = se.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class CommandLineApprover(T.Base):
    """Every approval command through approvals.cli_approver: no key, no signers file, no TTY, no process."""
    def setUp(self):
        T.Base.setUp(self)
        use_cli_approver(self)
        self.d = make_skill(self.tmp, evals=evals_doc(human=False))
        self.spec = se.load_eval_set(self.d)[0]
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")
        self.approvals = os.path.join(self.ws, "approvals.json")
        self.assertEqual(os.listdir(self.account), [])

    def test_approve_and_a_waiver_whose_reason_is_piped(self):
        rc, out, err = cli("approve", self.d, "--workspace", self.ws, "--gate", "requirements_eval_set")
        self.assertEqual(rc, 0, err)
        feed(self, REASON + "\n")
        rc, out, err = cli("approve", self.d, "--workspace", self.ws, "--gate", WAIVER)
        self.assertEqual(rc, 0, err)
        self.assertIn(A.REASON_PROMPT, err)
        recs = rjson(self.approvals)
        self.assertEqual({g: (r["method"], r["recorded_by"], r.get("reason")) for g, r in recs.items()},
                         {"requirements_eval_set": ("recorded", USER, None), WAIVER: ("recorded", USER, REASON)})
        for g in recs: self.assertEqual(se.approval_verdict(g, self.spec, self.ws).status, "approved", g)

    def test_approve_batch_reads_one_reason_line_per_waiver_item(self):
        it = os.path.join(self.ws, "iteration-1"); os.makedirs(it)
        gate = {"needs_approval": False, "problems": []}
        wjson(os.path.join(it, "gate.json"), gate)
        cov = {"claude": B.RAN, "codex": B.RAN, "opencode": B.NOT_RUN, "hermes": B.RAN}
        wjson(os.path.join(it, "benchmark.json"), {"tool_coverage": cov, "gate": gate})
        feed(self, REASON + "\n")
        rc, out, err = cli("approve", "--batch", "--unit", self.d, "--workspace", self.ws)
        self.assertEqual(rc, 0, out + err)
        recs = rjson(self.approvals)
        self.assertEqual(sorted(recs), sorted(["requirements_eval_set", WAIVER]))
        self.assertEqual(recs[WAIVER]["reason"], REASON)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("recorded branch-setup ")]), 2, out)
        for g in recs: self.assertEqual(se.approval_verdict(g, self.spec, self.ws).status, "approved", g)

    def test_register_and_run_live(self):
        rc, out, err = cli("approve", self.d, "--workspace", self.ws, "--gate", "requirements_eval_set")
        self.assertEqual(rc, 0, err)
        seen, fake = [], FakeClaude()
        with mock.patch.object(se, "live_exec", lambda cmd, cwd, timeout, env=None: seen.append(se._LIVE["on"]) or
                               fake(cmd, cwd, timeout)):
            rc, out, err = cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac",
                               "--budget-usd", "100", "--engines", "claude", "--runs", "1")
        self.assertEqual(rc, 0, err)
        self.assertTrue(seen and all(seen))
        self.assertIn("recorded run --live by %s (plan sha256 " % USER, err)
        n = se.iterations(self.ws)[-1]
        se.grade_iteration(self.spec, self.ws, n, self.grader(), log=quiet)
        for t in ("codex", "opencode", "hermes"):
            feed(self, REASON + "\n")
            rc, out, err = cli("approve", self.d, "--workspace", self.ws, "--gate", "coverage_waiver:" + t)
            self.assertEqual(rc, 0, err)
        se.report_iteration(self.spec, self.ws, n)
        rc, out, err = cli("register", self.d, "--workspace", self.ws)
        self.assertEqual(rc, 0, err)
        reg = rjson(os.path.join(self.ws, "registration.json"))
        self.assertEqual((reg["signed"]["method"], reg["signed"]["recorded_by"]), ("recorded", USER))
        self.assertEqual(se.registration_state(self.d, self.ws)[:2], ("registered", ""))

    def test_machine_waiver_sign_show_and_revoke(self):
        path = os.path.join(self.home, ".config", "skill-master", "machine-waiver.json")
        with mock.patch.dict(se.MACHINE, {"id": lambda: MACHINE}):
            for how, argv, stdin in (("option", ["--reason", REASON], ""), ("stdin", [], REASON + "\n")):
                with self.subTest(how):
                    feed(self, stdin)
                    rc, out, err = cli("machine-waiver", "sign", *argv)
                    self.assertEqual(rc, 0, err)
                    self.assertTrue(out.startswith("machine waiver recorded: tools opencode,hermes, expires "), out)
                    rec = rjson(path)
                    self.assertEqual(sorted(rec), ["at", "expires", "format", "machine_id", "method", "reason",
                                                   "recorded_by", "tools"])
                    self.assertEqual((rec["method"], rec["recorded_by"], rec["machine_id"], rec["reason"]),
                                     ("recorded", USER, MACHINE, REASON))
            rc, out, err = cli("machine-waiver", "show")
            self.assertEqual(rc, 0, err)
            self.assertIn("machine waiver: approved by %s, sha256 " % USER, out)
            rc, out, err = cli("machine-waiver", "revoke")
            self.assertEqual((rc, out), (0, "machine waiver revoked\n"), err)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(cli("machine-waiver", "show")[0], 1)
            feed(self, "\n")
            rc, out, err = cli("machine-waiver", "sign")
            self.assertEqual(rc, 2); self.assertIn("reason refused: it is empty", err)
            self.assertFalse(os.path.exists(path))
        self.assertEqual(os.listdir(self.account), [])

    def test_the_session_and_page_commands_print_one_line_and_start_nothing(self):
        for argv, code in ((["approver-check"], 0), (["approver-session"], 0), (["approve-ui", "--unit", self.d], 2),
                           (["approve-ui", "--unit", self.d, "--tailscale"], 2)):
            with self.subTest(argv[0]):
                rc, out, err = cli(*argv)
                self.assertEqual(rc, code, err)
                self.assertEqual(len((out + err).strip().splitlines()), 1, out + err)
                self.assertIn(UNSIGNED_NOTE, out + err)
        self.assertFalse(os.path.exists(self.approvals))


class AgentCommandLine(AE.AgentBase):
    """agent run --live and agent grade --live through the command-line approver (the live calls are faked)."""
    def setUp(self):
        AE.AgentBase.setUp(self)
        use_cli_approver(self)

    def test_agent_run_and_grade_live(self):
        rc, out, err, fake = self.live("run", "--engines", "claude")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(fake.calls)
        self.assertIn("recorded agent run --live by %s" % USER, err)
        rc, out, err, fake = self.live("grade")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("recorded agent grade --live by %s" % USER, err)
        for gate, rec in rjson(os.path.join(self.ws, "approvals.json")).items():
            self.assertEqual((rec["method"], rec["recorded_by"]), ("recorded", USER), gate)


def prepared(sha=SHA, show=("eval set files: evals/evals.json",)):
    return lambda: A.Prepared("branch-setup", sha, RULES, list(show))


class Recording(unittest.TestCase):
    """Approver.sign: no process, no file, no TTY; the record binds kind, gate, unit, hashes and budget or reason."""
    def setUp(self):
        self.shown, self.asked = [], []

    def approver(self, **kw):
        args = dict(write=self.shown.append, read_line=lambda p: self.asked.append(p) or REASON + "\n", run=forbidden)
        args.update(kw)
        return T.gate_approver(**args)

    def test_each_kind_records_and_reads_back_approved(self):
        for kind, gate, budget, reason in (("approve", "eval_set", None, None), ("approve", WAIVER, None, REASON),
                                           ("register", "registration", None, None), ("live", "run", 2.5, None)):
            with self.subTest(kind=kind, gate=gate):
                g = self.approver().sign(kind, gate, prepared(), budget_usd=budget)
                r = g.record
                want = dict(format=1, method="recorded", kind=kind, gate=gate, unit="branch-setup", sha256=SHA,
                            ruleset_sha256=RULES, recorded_by=T.TEST_PRINCIPAL, at=r["at"])
                if budget is not None: want["budget_usd"] = budget
                if reason is not None: want["reason"] = reason
                self.assertEqual(r, want)
                self.assertEqual((g.kind, g.gate, g.unit, g.sha256), (kind, gate, "branch-setup", SHA))
                self.assertEqual(self.approver().verdict(r, kind, gate, "branch-setup", SHA, RULES, budget),
                                 A.Verdict("approved", T.TEST_PRINCIPAL, ""))

    def test_recorded_by_is_the_os_user_name_unless_given(self):
        self.assertEqual(T.gate_approver(recorded_by=None).recorded_by, USER)
        self.assertEqual(A.cli_approver().recorded_by, USER)
        self.assertEqual(self.approver(recorded_by="coordinator").sign("approve", "eval_set", prepared())
                         .record["recorded_by"], "coordinator")

    def test_the_summary_and_the_shown_lines_are_escaped_and_nothing_is_asked_but_a_waiver_reason(self):
        show = ["file evals/files/%s.csv" % SPOOF, "mark %s here" % RLO]
        for kind, gate, budget in (("approve", "eval_set", None), ("register", "registration", None), ("live", "run", 1.0)):
            with self.subTest(kind):
                del self.shown[:]
                self.approver().sign(kind, gate, prepared(show=show), budget_usd=budget)
                self.assertEqual(self.shown[:2], ["file evals/files/%s.csv" % SPOOF_SHOWN, "mark \\u202e here"])
                self.assertTrue(self.shown[2].startswith("%s: gate %s, unit branch-setup, sha256 %s"
                                                         % (A.label(kind, gate), gate, SHA)), self.shown)
                for line in self.shown: self.assertTrue(line.isprintable(), repr(line))
        self.assertEqual(self.asked, [])
        self.approver().sign("approve", WAIVER, prepared())
        self.assertEqual(self.asked, [A.REASON_PROMPT])
        self.approver().sign("approve", WAIVER, prepared(), reason=REASON)
        self.assertEqual(self.asked, [A.REASON_PROMPT])

    def test_bad_input_refuses_and_records_nothing(self):
        for why, call in (
                ("reason refused: it is empty", lambda ap: ap.sign("approve", WAIVER, prepared(), reason="")),
                ("only a coverage waiver takes a reason", lambda ap: ap.sign("approve", "eval_set", prepared(), reason=REASON)),
                ("budget_usd", lambda ap: ap.sign("live", "run", prepared(), budget_usd=float("inf"))),
                ("only a live", lambda ap: ap.sign("approve", "eval_set", prepared(), budget_usd=1.0)),
                ("unit", lambda ap: ap.sign("approve", "eval_set", lambda: A.Prepared("-x", SHA, RULES, []))),
                ("sha256", lambda ap: ap.sign("approve", "eval_set", prepared(sha="AB" * 32)))):
            with self.subTest(why):
                with self.assertRaises(A.Refused) as cm: call(self.approver())
                self.assertIn(why, str(cm.exception))
                self.assertIn("nothing was recorded", str(cm.exception))

    def test_prepare_errors_pass_through_before_anything_is_shown(self):
        def fail(): raise ValueError("caller's own error")
        with self.assertRaises(ValueError): self.approver().sign("approve", "eval_set", fail)
        self.assertEqual((self.shown, self.asked), ([], []))


class RecordedVerdicts(unittest.TestCase):
    """The hash binding of an unsigned record: it is approved only for the content, gate, unit, kind, ruleset and
    budget it names; field checks run first and no process starts."""
    def setUp(self):
        self.ap = T.gate_approver(run=forbidden)

    def record(self, kind="approve", gate="eval_set", budget=None, reason=None):
        return self.ap.sign(kind, gate, prepared(), budget_usd=budget, reason=reason).record

    def verdict(self, rec, kind="approve", gate="eval_set", unit="branch-setup", sha=SHA, rules=RULES, budget=None):
        return self.ap.verdict(rec, kind, gate, unit, sha, rules, budget)

    def test_changed_content_reads_stale(self):
        r = self.record()
        self.assertEqual(self.verdict(r, sha=OTHER), A.Verdict("stale", T.TEST_PRINCIPAL,
                                                               "the gated files changed since signing"))
        self.assertEqual(self.verdict(r, sha=None).status, "stale")
        self.assertEqual(self.verdict(r, rules=OTHER), A.Verdict("stale", T.TEST_PRINCIPAL, A.RULES_CHANGED))

    def test_a_record_does_not_move_to_another_gate_unit_kind_or_budget(self):
        r = self.record(gate="iteration-1")
        self.assertEqual(self.verdict(r, gate="iteration-1").status, "approved")
        for v in (self.verdict(r, gate="registration"), self.verdict(r, gate="iteration-1", unit="other-skill"),
                  self.verdict(r, gate="iteration-1", kind="register")):
            self.assertEqual(v.status, "invalid", v)
        live = self.record(kind="live", gate="run", budget=5.0)
        self.assertEqual(self.verdict(live, kind="live", gate="run", budget=5).status, "approved")
        self.assertEqual(self.verdict(live, kind="live", gate="run", budget=50.0).status, "invalid")
        self.assertEqual(self.verdict(live, kind="live", gate="loop", budget=5.0).status, "invalid")
        waiver = self.record(gate=WAIVER, reason=REASON)
        self.assertEqual(self.verdict(waiver, gate=WAIVER).status, "approved")
        self.assertEqual(self.verdict(waiver, gate="coverage_waiver:hermes").status, "invalid")

    def test_malformed_records_read_invalid(self):
        r = self.record()
        waiver = self.record(gate=WAIVER, reason=REASON)
        for bad in (dict(r, format=2), dict(r, method="gpg"), dict(r, signature="s"), dict(r, principal="x"),
                    {k: v for k, v in r.items() if k != "recorded_by"}, dict(r, recorded_by="a b"), dict(r, recorded_by=""),
                    dict(r, sha256="xyz"), dict(r, ruleset_sha256=None), dict(r, at="yesterday"), dict(r, kind="other"),
                    dict(r, unit="-x"), dict(r, budget_usd=1.0), dict(r, reason=REASON),
                    {k: v for k, v in waiver.items() if k != "reason"}, dict(waiver, reason="x\ty"),
                    {"method": "recorded"}, [], "x"):
            with self.subTest(bad=str(bad)[:60]):
                gate = bad.get("gate", "eval_set") if isinstance(bad, dict) else "eval_set"
                self.assertEqual(self.verdict(bad, gate=gate).status, "invalid")
        self.assertEqual(self.verdict(None).status, "missing")

    def test_a_budget_too_large_for_a_float_reads_invalid(self):
        live = dict(self.record(kind="live", gate="run", budget=5.0), budget_usd=10 ** 400)
        self.assertEqual(self.verdict(live, kind="live", gate="run", budget=10 ** 400).status, "invalid")

    def test_a_legacy_record_without_method_stays_unsigned(self):
        self.assertEqual(self.verdict({"sha256": SHA, "by": "pinesky", "at": AT}),
                         A.Verdict("unsigned", None, A.UNSIGNED_RULES))
        self.assertEqual(self.verdict({"sha256": SHA, "ruleset_sha256": RULES, "at": AT}).status, "unsigned")
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp)
        for name in ("csv-cleaner", "csv-cleaner-workspace"):
            shutil.copytree(os.path.join(ROOT, "golden", "skills", name), os.path.join(tmp, name))
        spec = se.load_eval_set(os.path.join(tmp, "csv-cleaner"))[0]
        ws = os.path.join(tmp, "csv-cleaner-workspace")
        with mock.patch.dict(se.APPROVER, {"it": self.ap}):
            for gate in ("requirements", "eval_set", "iteration-4", "registration"):
                v = se.approval_verdict(gate, spec, ws)
                self.assertEqual((v.status, v.principal), ("unsigned", None), gate)


class SignedRecords(unittest.TestCase):
    """Records signed before N1 stay readable: they verify with ssh-keygen when the signers file exists and read
    invalid when it does not or when any signed byte changed."""
    def setUp(self):
        self.calls = []
        k = T.shared_keys()
        self.ap = T.signed_approver(run=T.keygen_runner(k["dir"], self.calls))

    def verdict(self, rec, kind="approve", gate="eval_set", unit="branch-setup", sha=SHA, budget=None, ap=None):
        return (ap or self.ap).verdict(rec, kind, gate, unit, sha, RULES, budget)

    def test_every_kind_verifies(self):
        for kind, gate, budget, reason in (("approve", "eval_set", None, None), ("approve", WAIVER, None, REASON),
                                           ("register", "registration", None, None), ("live", "run", 3.0, None)):
            with self.subTest(kind=kind, gate=gate):
                rec = T.signed_record(kind, gate, "branch-setup", SHA, RULES, budget_usd=budget, reason=reason)
                self.assertEqual(self.verdict(rec, kind, gate, budget=budget), A.Verdict("approved", T.TEST_PRINCIPAL, ""))
        self.assertTrue([c for c in self.calls if c[1:3] == ["-Y", "verify"]])

    def test_without_a_signers_file_a_signed_record_reads_invalid(self):
        rec = T.signed_record("approve", "eval_set", "branch-setup", SHA, RULES)
        v = self.verdict(rec, ap=T.gate_approver())
        self.assertEqual(v.status, "invalid"); self.assertIn("approvers file missing", v.reason)

    def test_changed_signed_bytes_read_invalid(self):
        rec = T.signed_record("approve", "iteration-1", "branch-setup", SHA, RULES)
        lines = rec["signature"].splitlines()
        lines[2] = lines[2][:10] + ("A" if lines[2][10] != "A" else "B") + lines[2][11:]
        cases = ((dict(rec, signature="\n".join(lines) + "\n"), {}), (dict(rec, sha256=OTHER), dict(sha=OTHER)),
                 (dict(rec, at="2026-10-20T09:41:01Z"), {}), (dict(rec, principal="nopass"), {}),
                 (rec, dict(gate="registration")), (rec, dict(unit="other-skill")), (rec, dict(kind="register")))
        for i, (bad, kw) in enumerate(cases):
            with self.subTest(i):
                self.assertEqual(self.verdict(bad, **dict(dict(gate="iteration-1"), **kw)).status, "invalid")
        self.assertEqual(self.verdict(rec, gate="iteration-1", sha=OTHER).status, "stale")

    def test_a_key_outside_the_signers_file_reads_invalid(self):
        k = T.shared_keys()
        intruder = T.make_key(tempfile.mkdtemp(dir=k["dir"]), "intruder", T.TEST_PASSPHRASE)[0]
        rec = T.signed_record("approve", "eval_set", "branch-setup", SHA, RULES, key=intruder)
        self.assertEqual(self.verdict(rec).status, "invalid")

    def test_the_real_approver_refuses_to_verify_inside_a_test_process(self):
        rec = T.signed_record("approve", "eval_set", "branch-setup", SHA, RULES)
        with mock.patch.object(A, "run_process", forbidden):
            v = A.cli_approver().verdict(rec, "approve", "eval_set", "branch-setup", SHA, RULES)
        self.assertEqual(v, A.Verdict("invalid", None, A.IN_TESTS))


class FixedPaths(unittest.TestCase):
    def test_the_cli_signers_path_is_fixed_and_no_option_moves_it(self):
        """The signers file that old signed records verify against: home from pwd, no variable or option moves it."""
        home = pwd.getpwuid(os.getuid()).pw_dir
        with mock.patch.dict(os.environ, {"HOME": "/tmp/elsewhere", "XDG_CONFIG_HOME": "/tmp/elsewhere"}):
            ap = A.cli_approver()
        self.assertEqual(ap.signers, os.path.join(home, ".config", "skill-master", "approvers.allowed_signers"))
        for cmd in ("approve", "register", "run"):
            argv = {"approve": ["approve", "s", "--gate", "eval_set"], "register": ["register", "s"],
                    "run": ["run", "s", "--live"]}[cmd]
            for opt in ("--signers", "--allowed-signers", "--key", "--by", "--recorded-by"):
                with self.subTest(cmd=cmd, opt=opt), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit): se.build_parser().parse_args(argv + [opt, "/x"])

    def test_the_cli_process_loads_no_test_module(self):
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp)
        probe = os.path.join(tmp, "probe.py")
        write(probe, "import sys\nsys.path.insert(0, %r)\nimport skill_eval, eva\n"
                     "print(sorted(m for m in ('unittest', 'pytest', '_pytest') if m in sys.modules))\n" % ROOT)
        p = subprocess.run([sys.executable, "-B", probe], cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=60, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "[]"), p.stderr)


class SessionNeverReachesAChild(unittest.TestCase):
    """R25-1: no model child of a run, and no code such a child runs, gets an ssh-agent or an approver-session shell
    left from before N1. The fake child is a script in a temp folder started through live_exec, the one place a real
    CLI starts; no agent or socket exists."""
    FOLDER = "/tmp/skill-master-approver-r25child"
    SOCK = FOLDER + "/agent.sock"
    SESSION = {"SSH_AUTH_SOCK": SOCK, "SSH_AGENT_PID": "4321", "SKILL_MASTER_APPROVER_SESSION": FOLDER,
               "PS1": "[approver] %~ %# ", "PROMPT": "[approver] %~ %# "}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gate-child-"); self.addCleanup(shutil.rmtree, self.tmp, True)
        pt = mock.patch.dict(os.environ, dict(self.SESSION, R25_PROBE="kept"))
        pt.start(); self.addCleanup(pt.stop)

    def assert_no_session(self, env, where):
        """Failure messages name variables only: a child environment holds this machine's credentials."""
        self.assertEqual(sorted(set(self.SESSION) & set(env)), [], where)
        self.assertEqual(sorted(k for k, v in env.items() if self.FOLDER in v), [], where)
        self.assertEqual(env.get("R25_PROBE"), "kept", where)

    def test_every_child_environment_drops_the_session(self):
        for where, env in (("scrub_env", se.scrub_env(os.environ)), ("claude_child_env", se.claude_child_env()),
                           ("claude sandbox_env", se.ClaudeCodeEngine().sandbox_env(self.tmp))):
            with self.subTest(where):
                self.assert_no_session(env, where)

    def test_a_fake_child_started_through_live_exec_cannot_see_the_socket(self):
        child = os.path.join(self.tmp, "child.py")
        write(child, "import json, os, sys\nsys.stdout.write(json.dumps(dict(os.environ)))\n")
        for where, env in (("default env", None), ("an unscrubbed env passed in", dict(os.environ))):
            with self.subTest(where), mock.patch.dict(se._LIVE, {"on": True, "gated": False, "ledger": None}):
                rc, out, err = se.live_exec([sys.executable, "-B", child], self.tmp, 60, env=env)
                self.assertEqual(rc, 0, "the fake child failed: " + err[-300:])
                self.assertFalse(self.SOCK in out, "the fake child saw the session socket path (%s)" % where)
                self.assert_no_session(json.loads(out), where)


class SkillGate(T.Base):
    """skill_eval through recorded approvals: records, plan lines, registration, hash binding and the --live rechecks."""
    def setUp(self):
        T.Base.setUp(self)
        self.d = make_skill(self.tmp, evals=evals_doc(human=False))
        self.spec = se.load_eval_set(self.d)[0]
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")
        self.approvals = os.path.join(self.ws, "approvals.json")

    def cli(self, *argv):
        return cli(*argv)

    def with_approver(self, **kw):
        return mock.patch.dict(se.APPROVER, {"it": T.gate_approver(**kw)})

    def test_plan_shows_who_recorded(self):
        for g in ("requirements", "eval_set"):
            rc, out, err = self.cli("approve", self.d, "--workspace", self.ws, "--gate", g)
            self.assertEqual(rc, 0, err); self.assertIn("approved %s" % g, out)
        recs = rjson(self.approvals)
        self.assertEqual({r["method"] for r in recs.values()}, {"recorded"})
        rc, out, _ = self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
        self.assertIn("approval gates: requirements=approved by test-approver, eval_set=approved by test-approver", out)

    def test_plan_lines_name_each_status(self):
        wjson(self.approvals, {"requirements": {"sha256": se.gate_hash("requirements", self.spec, self.ws), "by": "x",
                                                "at": AT}})
        rc, out, _ = self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
        self.assertIn("approval gates: requirements=NOT APPROVED (unsigned; stale: rules changed since approval), "
                      "eval_set=NOT APPROVED\n", out)
        se.approve("eval_set", self.spec, self.ws)
        wjson(os.path.join(self.d, "evals", "trigger_evals.json"), T.TRIGGERS[::-1])
        rc, out, _ = self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
        self.assertIn("eval_set=NOT APPROVED (stale)", out)
        rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac",
                              "--budget-usd", "100", "--engines", "claude")
        self.assertEqual(rc, 2); self.assertIn("gate requirements not approved (unsigned)", err)

    def test_a_record_copied_to_another_gate_does_not_approve_it(self):
        se.approve("eval_set", self.spec, self.ws)
        recs = rjson(self.approvals)
        wjson(self.approvals, dict(recs, requirements=recs["eval_set"]))
        v = se.approval_verdict("requirements", self.spec, self.ws)
        self.assertEqual(v.status, "invalid", v)
        self.assertEqual(se.approval_verdict("eval_set", self.spec, self.ws).status, "approved")

    def test_a_signed_record_from_before_n1_still_approves(self):
        signed = T.signed_record("approve", "eval_set", "branch-setup", se.gate_hash("eval_set", self.spec, self.ws),
                                 se.ruleset_sha256())
        wjson(self.approvals, {"eval_set": signed})
        with mock.patch.dict(se.APPROVER, {"it": T.signed_approver()}):
            self.assertEqual(se.approval_verdict("eval_set", self.spec, self.ws),
                             A.Verdict("approved", T.TEST_PRINCIPAL, ""))
            rc, out, _ = self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
        self.assertIn("eval_set=approved by test-approver", out)

    def test_registration_is_recorded_and_verifies(self):
        n = se.run_iteration(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude(True))],
                             runs=1, log=quiet)
        se.grade_iteration(self.spec, self.ws, n, self.grader(), log=quiet)
        T.waive_coverage(self.spec, self.ws)
        se.report_iteration(self.spec, self.ws, n)
        rec = se.approve("registration", self.spec, self.ws)
        reg = se.register(self.spec, self.ws)
        self.assertEqual(reg["approval"], rec)
        self.assertEqual((reg["signed"]["method"], reg["signed"]["recorded_by"]), ("recorded", T.TEST_PRINCIPAL))
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "approved")
        self.assertEqual(se.registration_schema_errors(reg), [])
        on_disk = rjson(os.path.join(self.ws, "registration.json"))
        wjson(os.path.join(self.ws, "registration.json"), dict(on_disk, skill_sha256="0" * 64))
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "stale")
        bad = dict(on_disk, signed=dict(on_disk["signed"], extra=1))
        self.assertNotEqual(se.registration_schema_errors(bad), [])

    def test_plan_starts_ssh_keygen_only_for_a_signed_record(self):
        calls = []
        counted = T.signed_approver(run=T.keygen_runner(T.shared_keys()["dir"], calls))
        it = os.path.join(self.ws, "iteration-1"); os.makedirs(it); wjson(os.path.join(it, "benchmark.json"), {})
        verify = lambda: len([c for c in calls if c[1:3] == ["-Y", "verify"]])
        for g in ("requirements", "eval_set", "iteration-1", "registration"): se.approve(g, self.spec, self.ws)
        with mock.patch.dict(se.APPROVER, {"it": counted}):
            self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
            self.assertEqual(verify(), 0)
            recs = rjson(self.approvals)
            recs["requirements"] = T.signed_record("approve", "requirements", "branch-setup",
                                                   se.gate_hash("requirements", self.spec, self.ws), se.ruleset_sha256())
            wjson(self.approvals, recs)
            self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
            self.assertEqual(verify(), 1)

    def test_a_recorded_run_reaches_live_mode_only_with_a_grant(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        with self.assertRaises(se.EvalError): se.enable_live(None)
        with self.assertRaises(se.EvalError): se.enable_live(A.Grant("approve", "eval_set", "u", SHA, {}))
        self.assertFalse(se._LIVE["on"])
        seen = []
        fake = FakeClaude()
        with mock.patch.object(se, "live_exec", lambda cmd, cwd, timeout, env=None: seen.append(se._LIVE["on"]) or
                               fake(cmd, cwd, timeout)):
            rc, out, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac",
                                    "--budget-usd", "100", "--engines", "claude", "--runs", "1")
        self.assertEqual(rc, 0, err); self.assertTrue(seen and all(seen))

    def edited_after_recording(self, edit, *argv):
        """edit() runs when the approver prints the --live summary: after prepare() hashed the plan and before the
        rechecks, where a challenge used to wait."""
        def write_then_edit(line):
            if " --live: gate " in line: edit()
        with self.with_approver(write=write_then_edit), mock.patch.object(se, "live_exec", forbidden):
            rc, out, err = self.cli(*argv)
        self.assertEqual(rc, 2, err)
        self.assertIn("the command's inputs changed after signing", err)
        self.assertFalse(se._LIVE["on"])

    def live_argv(self, *extra):
        return ("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "100",
                "--engines", "claude", "--runs", "1") + extra

    def test_inputs_changed_after_recording_refuse(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        def edit():
            with open(os.path.join(self.d, "SKILL.md"), "a", encoding="utf-8") as f: f.write("\nedited\n")
        self.edited_after_recording(edit, *self.live_argv())

    def test_golden_file_edited_after_recording_refuses(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        n = se.run_iteration(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude())], runs=1,
                             log=quiet)
        se.grade_iteration(self.spec, self.ws, n, self.grader(), log=quiet)
        se.report_iteration(self.spec, self.ws, n)
        golden = os.path.join(self.tmp, "golden.json")
        wjson(golden, {"examples": [{"prompt": "p", "output": "o", "item": "i", "label": True}]})
        self.edited_after_recording(
            lambda: wjson(golden, {"examples": [{"prompt": "p2", "output": "o", "item": "i", "label": True}]}),
            "calibrate", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "100",
            "--iteration", str(n), "--golden", golden)

    def test_old_skill_folder_edited_after_recording_refuses(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        old = os.path.join(self.tmp, "old-copy")
        shutil.copytree(self.d, old)
        def edit():
            with open(os.path.join(old, "SKILL.md"), "a", encoding="utf-8") as f: f.write("\nedited\n")
        self.edited_after_recording(edit, *self.live_argv("--baseline", "old", "--old-skill", old))

    def rolled_back_after_recording(self, gate, path, change):
        """The run is recorded over the newer gated file and its newer approval. While the summary is printed both go
        back to the older pair, which still reads approved, so only the gate's field in the live plan hash notices."""
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        older = (T.read(path), T.read(self.approvals))
        change()
        se.approve(gate, self.spec, self.ws)
        self.assertNotEqual(T.read(self.approvals), older[1])
        def roll_back():
            write(path, older[0]); write(self.approvals, older[1])
        self.edited_after_recording(roll_back, *self.live_argv())
        self.assertEqual((T.read(path), T.read(self.approvals)), older)
        self.assertTrue(se.approval_ok(gate, self.spec, self.ws), "control: the older pair still reads approved")

    def test_eval_set_rolled_back_to_an_older_approved_pair_refuses(self):
        trig = os.path.join(self.d, "evals", "trigger_evals.json")
        self.rolled_back_after_recording("eval_set", trig, lambda: wjson(trig, T.TRIGGERS[::-1]))

    def test_requirements_rolled_back_to_an_older_approved_pair_refuses(self):
        req = os.path.join(self.ws, "requirements.md")
        self.rolled_back_after_recording("requirements", req, lambda: write(req, "goal, revised"))

    def test_a_static_gate_failure_refuses_before_anything_is_shown(self):
        se.approve("requirements", self.spec, self.ws)
        asked, shown = [], []
        before = state(self.approvals)
        with self.with_approver(read_line=lambda p: asked.append(p) or "\n", write=shown.append), \
                mock.patch.object(se, "live_exec", forbidden):
            rc, out, err = self.cli(*self.live_argv())
        self.assertEqual(rc, 2, err)
        self.assertIn("gate eval_set not approved", err)
        self.assertEqual((asked, shown), ([], []))
        self.assertEqual(state(self.approvals), before)
        self.assertEqual(self.status_calls, [])

    def test_shown_paths_cannot_redraw_the_terminal(self):
        doc = evals_doc(human=False)
        doc["evals"][0]["files"] = ["evals/files/%s.csv" % SPOOF]
        other = make_skill(os.path.join(self.tmp, "spoof"), evals=doc)
        ws = os.path.join(self.tmp, "ws" + SPOOF)
        os.makedirs(ws); write(os.path.join(ws, "requirements.md"), "goal")
        shown = []
        with self.with_approver(write=shown.append):
            rc, _, err = self.cli("approve", other, "--workspace", ws, "--gate", "eval_set")
            self.assertEqual(rc, 0, err)
        for g in ("requirements", "eval_set"):
            rc, _, err = self.cli("approve", self.d, "--workspace", ws, "--gate", g)
            self.assertEqual(rc, 0, err)
        def stop_at_summary(line):
            shown.append(line)
            if " --live: gate " in line: raise Stop()
        with self.with_approver(write=stop_at_summary), mock.patch.object(se, "live_exec", forbidden):
            with self.assertRaises(Stop):
                self.cli("run", self.d, "--workspace", ws, "--live", "--auth-profile", "personal-mac", "--budget-usd",
                         "100", "--engines", "claude", "--runs", "1")
        for line in shown: self.assertTrue(line.isprintable(), repr(line))
        self.assertIn("eval set file files:evals/files/%s.csv: <missing>" % SPOOF_SHOWN, shown)
        self.assertTrue(any(x.startswith("workspace: ") and x.endswith("ws" + SPOOF_SHOWN) for x in shown), shown)
        self.assertTrue(any(x.startswith("live command: ") and SPOOF_SHOWN in x for x in shown), shown)

    def test_a_non_object_approvals_file_refuses_before_anything_is_recorded(self):
        for text in NON_OBJECTS:
            with self.subTest(text):
                write(self.approvals, text)
                shown = []
                with self.with_approver(write=shown.append):
                    rc, out, err = self.cli("approve", self.d, "--workspace", self.ws, "--gate", "requirements")
                self.assertEqual(rc, 2, err)
                self.assertIn("approvals.json is not a JSON object (it holds %s); nothing was signed" % text, err)
                self.assertEqual(shown, [])
                self.assertEqual(T.read(self.approvals), text)

    def test_readers_take_a_non_object_approvals_file_as_not_approved(self):
        for text in NON_OBJECTS:
            with self.subTest(text):
                write(self.approvals, text)
                v = se.approval_verdict("requirements", self.spec, self.ws)
                self.assertEqual((v.status, v.principal), ("invalid", None))
                self.assertTrue(v.reason.endswith("approvals.json is not a JSON object (it holds %s)" % text), v)
                self.assertFalse(se.approval_ok("requirements", self.spec, self.ws))
                rc, out, err = self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
                self.assertEqual(rc, 0, err)
                self.assertIn("approval gates: requirements=NOT APPROVED (invalid: ", out)


class RuleReviewReaders(T.Base):
    """An unsigned legacy rule_review record, or one signed by a key outside the signers file, is not a recorded
    decision for rule_review_problems, check_live_gates or the plan line. The fixture's regex matcher holds Korean text
    (escapes, so this file stays ASCII), which eva.review_items lists as a rule-review finding."""
    def setUp(self):
        T.Base.setUp(self)
        doc = evals_doc(human=False)
        doc["evals"][0]["assertions"][0]["check"] = {"type": "regex", "pattern": KOREAN}
        self.d = make_skill(self.tmp, evals=doc)
        self.spec, self.problems = se.load_eval_set(self.d)
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")
        self.approvals = os.path.join(self.ws, "approvals.json")
        self.h = se.rule_review_hash(self.d)

    def readers(self):
        """(rule_review_problems, the error check_live_gates raises, the plan's rule-review line)."""
        probs = se.rule_review_problems(self.spec, self.ws)
        a = se.build_parser().parse_args(["run", self.d, "--workspace", self.ws, "--live", "--budget-usd", "100",
                                          "--engines", "claude"])
        a.grader_model = se.DEFAULT_GRADER_MODEL
        try:
            se.check_live_gates(a, self.spec, self.problems, self.ws, None, se.make_engines(a, self.spec),
                                se.parse_token_overrides(None), se.parse_price_overrides(None), 100.0)
            gate = ""
        except se.EvalError as ex:
            gate = str(ex)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            se.main(["plan", self.d, "--workspace", self.ws, "--engines", "claude"])
        line = next(x for x in out.getvalue().splitlines() if x.startswith("rule review (English-only"))
        return probs, gate, line

    def test_control_a_recorded_decision_counts(self):
        self.assertTrue(self.h)
        self.assertEqual(self.problems, [])
        se.approve("rule_review", self.spec, self.ws)
        probs, gate, line = self.readers()
        self.assertEqual(probs, [])
        self.assertTrue(gate.startswith("gate requirements not approved"), gate)
        self.assertIn("RECORDED (by %s)" % T.TEST_PRINCIPAL, line)

    def refused_everywhere(self, status):
        probs, gate, line = self.readers()
        want = "rule review not recorded (%s): 1 finding(s) need a human decision" % status
        self.assertEqual(len(probs), 1, probs); self.assertTrue(probs[0].startswith(want), probs)
        self.assertTrue(gate.startswith(want), gate)
        self.assertTrue(line.endswith("NOT RECORDED (%s; blocks --live and the loop)" % status), line)

    def test_an_unsigned_legacy_record_is_not_a_decision(self):
        wjson(self.approvals, {"rule_review": {"sha256": self.h, "by": "pinesky", "at": AT}})
        self.refused_everywhere("unsigned")

    def test_a_record_signed_outside_the_signers_file_is_not_a_decision(self):
        k = T.shared_keys()
        intruder = T.make_key(tempfile.mkdtemp(dir=k["dir"]), "intruder", T.TEST_PASSPHRASE)[0]
        wjson(self.approvals, {"rule_review": T.signed_record("approve", "rule_review", "branch-setup", self.h,
                                                              se.ruleset_sha256(), key=intruder)})
        with mock.patch.dict(se.APPROVER, {"it": T.signed_approver()}):
            self.refused_everywhere("invalid")


if __name__ == "__main__":
    unittest.main()
