"""Human gate tests (unit H1). This file is ASCII only.

approve (every gate), register and the start of every --live run need a person at a real terminal and a valid ssh
signature from a key in the signers file. Unit cases (U1 to U14) build an engine.approvals.Approver with throwaway keys
made by /usr/bin/ssh-keygen in temp dirs under /tmp, a temp signers file, a fake process table, a scripted line reader
and a fake ssh-add; only the TTY probe (approvals.is_tty) is patched. Command cases (G6-1 to G6-8) run the real CLI in a
subprocess on temp fixtures with no valid signature and assert the refusal text, the exit code, byte-identical
approvals.json / registration.json, and that no stub claude or codex started.

No case reaches the real approver key, the real signers file or the real ssh-add, in any terminal: subprocess children
get a stdin that is not a TTY and a sitecustomize audit hook (GATE_SITE) that blocks and records any touch of the real
~/.config/skill-master or any start of ssh-add or ssh-keygen; the pty and in-process cases run only where the real
ancestry probe sees an agent (require_agent_ancestor) and carry GateSentinel traps after the presence checks.
"""
import contextlib, ctypes, hashlib, io, os, pwd, select, shutil, struct, subprocess, sys, tempfile, time, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, evals_doc, make_skill, write, wjson, rjson, quiet, FakeClaude, FakeGraderExec

ROOT = T.ROOT
NOT_TTY = "%s needs a human at an interactive terminal (stdin is not a TTY)"
ANCESTOR = "needs a human at an interactive terminal (agent process in the parent chain: "
SKIP_HUMAN = ("no agent ancestor and no agent marker (a human Terminal): skipped so that no test reaches approver-check, "
              "ssh-add, the approver key or the signers file")
GATES = ("requirements", "eval_set", "rule_review", "iteration-1", "registration")
SHA = "ab" * 32
OTHER = "cd" * 32
AT = "2026-10-20T09:41:00Z"
KOREAN = "".join(map(chr, (0xD55C, 0xAD6D, 0xC5B4, 0x20, 0xD14D, 0xC2A4, 0xD2B8)))
RLO = chr(0x202E)
SPOOF = "\x1b[2J\rX"
SPOOF_SHOWN = "\\x1b[2J\\rX"
AGENTS = ("claude", "claude.exe", "codex", "opencode", "hermes", "cursor")
EXACT_MARKERS = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AS_ACTIVE", "CLAUDE_AS_CALLER",
                 "CLAUDE_AS_AUTO_SWITCH", "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "CLAUDE_TMPDIR",
                 "AI_AGENT", "AGENT_TRANSCRIPTS", "SAND_AGENT", "SANDBOX_RUNTIME", "OPENCODE")
MARKER_PREFIXES = ("CLAUDE_CODE_", "CODEX_", "CURSOR_", "__CURSOR_", "OPENCODE_", "HERMES_")
NON_OBJECTS = ("[1]", "[]", '"x"', "5", "true", "null")


def gate_module():
    from engine import approvals
    return approvals


class GateSentinel(BaseException):
    """A test reached a step after the presence checks (approver-check, ssh-add, signing)."""


def sentinel(*a, **k):
    raise GateSentinel("reached a step after the presence checks: %r" % (a[:1],))


def state(path):
    if not os.path.exists(path): return None
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def require_agent_ancestor(case):
    """Run only where presence must fail on ancestry. A human Terminal (no agent ancestor, no agent marker) skips; a
    session whose markers are set while the probe sees no agent ancestor fails, because the probe is blind there."""
    A = gate_module()
    probs = A.ancestry_problems(os.getpid(), A.libproc_read)
    if any(p.startswith("agent process in the parent chain: ") for p in probs): return
    if probs: case.fail("the ancestry probe failed closed: %s" % probs)
    marks = A.markers(os.environ)
    if marks: case.fail("ancestry probe is blind: agent markers %s are set but no agent ancestor was found" % marks)
    case.skipTest(SKIP_HUMAN)


GATE_SITE = r'''
import os, pwd, sys
_mark = os.environ.get("GATE_SENTINEL")
_root = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".config", "skill-master")
_tools = ("ssh-add", "ssh-keygen")

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
    """Subprocess fixture: stub claude and codex first on a PATH that holds nothing else but /usr/bin:/bin, a temp
    HOME, the GATE_SITE sentinel, SSH_AUTH_SOCK removed, stdin from /dev/null, a 60 s timeout."""
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

    def child_env(self, drop=()):
        env = {k: v for k, v in os.environ.items() if k != "SSH_AUTH_SOCK" and k not in drop}
        env.update(PATH=self.stubs + ":/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=self.site,
                   GATE_SENTINEL=self.hits, HOME=self.chome)
        return env

    def run_cli(self, *argv, script="skill_eval.py"):
        p = subprocess.run([sys.executable, "-B", script] + list(argv), cwd=ROOT, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, env=self.child_env(), timeout=60)
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
        recs = {}
        for g in GATES:
            h = se.gate_hash(g, spec, self.ws)
            recs[g] = {"sha256": h or SHA, "by": "pinesky", "at": AT}
        wjson(self.approvals, recs)


class GateNonTty(GateFixture):
    """G6-1 (lane 1): `skill_eval.py approve` with a non-TTY stdin refuses, exits 2, and leaves approvals.json as it was."""
    def test_every_gate_refuses_and_keeps_an_unsigned_file(self):
        self.unsigned()
        for g in GATES:
            with self.subTest(g):
                before = state(self.approvals)
                rc, out, err = self.run_cli("approve", self.d, "--workspace", self.ws, "--gate", g)
                self.assertEqual(rc, 2, err)
                self.assertEqual(err.splitlines()[0], NOT_TTY % "approve", err)
                self.assertEqual(state(self.approvals), before)
                self.assert_nothing_started()

    def test_refuses_and_writes_no_file_when_none_exists(self):
        rc, out, err = self.run_cli("approve", self.d, "--workspace", self.ws, "--gate", "eval_set")
        self.assertEqual(rc, 2, err)
        self.assertEqual(err.splitlines()[0], NOT_TTY % "approve", err)
        self.assertIn("approve needs a human at an interactive terminal (stdout is not a TTY)", err)
        self.assertFalse(os.path.exists(self.approvals))
        self.assert_nothing_started()


class GateEvaRoute(GateFixture):
    """G6-6 (lane 6): `eva.py eval approve` reaches the same gate as G6-1."""
    def test_eva_route_refuses_like_the_direct_command(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing: self.unsigned()
                before = state(self.approvals)
                rc, out, err = self.run_cli("eval", "approve", self.d, "--workspace", self.ws, "--gate", "eval_set",
                                            script="eva.py")
                self.assertEqual(rc, 2, err)
                self.assertEqual(err.splitlines()[0], NOT_TTY % "approve", err)
                self.assertEqual(state(self.approvals), before)
                self.assert_nothing_started()


class GateReg(T.Base, Cli):
    """G6-7 (lane 7): `skill_eval.py register` refuses before registration.json is written, both where the record would
    be registered automatically and where it needs a registration approval."""
    def setUp(self):
        T.Base.setUp(self)
        self.cli_setup()

    def iteration(self, regress):
        spec, _ = self.spec(evals=evals_doc(human=False))
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude(regress))], runs=1,
                             log=quiet)
        se.grade_iteration(spec, self.ws, n, self.grader(), log=quiet)
        se.report_iteration(spec, self.ws, n)
        return spec

    def test_refuses_and_keeps_registration_json(self):
        reg = os.path.join(self.ws, "registration.json")
        for regress in (False, True):
            for existing in (False, True):
                with self.subTest(regress=regress, existing=existing):
                    spec = self.iteration(regress)
                    if existing: write(reg, '{"previous": true}\n')
                    elif os.path.exists(reg): os.remove(reg)
                    before = state(reg)
                    rc, out, err = self.run_cli("register", spec["skill_dir"], "--workspace", self.ws)
                    self.assertEqual(rc, 2, err)
                    self.assertEqual(err.splitlines()[0], NOT_TTY % "register", err)
                    self.assertEqual(state(reg), before)
                    self.assert_nothing_started()

    def test_an_unsigned_registration_record_does_not_let_it_through(self):
        """Lane 7 note: a needs-approval iteration with an unsigned `registration` record already in approvals.json. T1
        registers this fixture, because it accepted any record with the right hash; the head refuses and leaves
        registration.json as it was."""
        reg = os.path.join(self.ws, "registration.json")
        for existing in (False, True):
            with self.subTest(existing=existing):
                spec = self.iteration(True)
                n = se.iterations(self.ws)[-1]
                self.assertTrue(rjson(os.path.join(self.ws, "iteration-%d" % n, "gate.json"))["needs_approval"])
                wjson(os.path.join(self.ws, "approvals.json"), {"registration": {
                    "sha256": se.gate_hash("registration", spec, self.ws), "by": "pinesky", "at": AT}})
                if existing: write(reg, '{"previous": true}\n')
                elif os.path.exists(reg): os.remove(reg)
                before = state(reg)
                rc, out, err = self.run_cli("register", spec["skill_dir"], "--workspace", self.ws)
                self.assertEqual(rc, 2, err)
                self.assertEqual(err.splitlines()[0], NOT_TTY % "register", err)
                self.assertEqual(state(reg), before)
                self.assert_nothing_started()
                with self.assertRaises(se.EvalError) as cm: se.registration_record(spec, self.ws)
                self.assertIn("needs `approve --gate registration` (unsigned: ", str(cm.exception))


class GateLv(GateFixture):
    """G6-8 (lane 8): `skill_eval.py run --live --budget-usd 0.01` with stub claude and codex first on PATH refuses, starts
    no CLI and leaves approvals.json as it was."""
    def check(self, *extra):
        self.unsigned()
        before = state(self.approvals)
        rc, out, err = self.run_cli("run", self.d, "--workspace", self.ws, "--live", "--budget-usd", "0.01", *extra)
        self.assert_nothing_started()
        self.assertEqual(rc, 2, err)
        self.assertEqual(err.splitlines()[0], NOT_TTY % "run --live", err)
        self.assertEqual(state(self.approvals), before)

    def test_default_engines(self):
        """At the T1 head this command already refuses (--strict-budget refuses codex before `claude auth status` runs),
        so only the refusal text tells the gate apart there; no marker appears at either head."""
        self.check()

    def test_claude_engine(self):
        """Red at the T1 head: check_live_gates runs `claude auth status` (the stub writes its marker) before any
        approval check."""
        self.check("--engines", "claude")


class GateLvOther(GateFixture):
    """Coordinator question 5: grade, calibrate and loop --live refuse at the gate too. Each fixture passes argument
    parsing and the iteration checks and uses the Claude grader, so a missing or late gate starts the stub claude."""
    def check(self, cmd, *extra):
        self.unsigned()
        before = state(self.approvals)
        rc, out, err = self.run_cli(cmd, self.d, "--workspace", self.ws, "--live", "--budget-usd", "0.01", *extra)
        self.assert_nothing_started()
        self.assertEqual(rc, 2, err)
        self.assertEqual(err.splitlines()[0], NOT_TTY % (cmd + " --live"), err)
        self.assertEqual(state(self.approvals), before)

    def test_grade(self):
        self.check("grade", "--engines", "claude", "--iteration", "1")

    def test_calibrate(self):
        g = os.path.join(self.tmp, "golden.json")
        wjson(g, {"examples": [{"prompt": "p", "output": "o", "item": "i", "label": True}]})
        self.check("calibrate", "--engines", "claude", "--iteration", "1", "--golden", g)

    def test_loop(self):
        self.check("loop", "--engines", "claude", "--max-iterations", "1")


class PtyCase(GateFixture):
    def under_pty(self, drop):
        try:
            import pty
            master, slave = pty.openpty()
        except OSError as ex:
            self.skipTest("no pty device (%s)" % ex)
        self.addCleanup(os.close, master)
        require_agent_ancestor(self)
        before = state(self.approvals)
        env = self.child_env(drop)
        p = subprocess.Popen([sys.executable, "-B", "skill_eval.py", "approve", self.d, "--workspace", self.ws, "--gate",
                              "eval_set"], cwd=ROOT, stdin=slave, stdout=slave, stderr=slave, env=env,
                             start_new_session=True)
        os.close(slave)
        out, deadline = b"", time.time() + 60
        while time.time() < deadline:
            r, _, _ = select.select([master], [], [], 0.5)
            if r:
                try: chunk = os.read(master, 4096)
                except OSError: break
                if not chunk: break
                out += chunk
            elif p.poll() is not None:
                break
        else:
            p.kill()
        rc = p.wait(timeout=60)
        text = out.decode("utf-8", "replace")
        self.assertEqual(rc, 2, text)
        self.assertNotIn("is not a TTY", text)
        self.assertIn("approve " + ANCESTOR, text)
        self.assertEqual(state(self.approvals), before)
        self.assert_nothing_started()

    def in_process(self, unset):
        A = gate_module()
        require_agent_ancestor(self)
        before = state(self.approvals)
        env = {k: v for k, v in os.environ.items() if not unset(k)}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(A, "is_tty", lambda fd: True), \
                mock.patch.dict(se.APPROVER, {"it": A.cli_approver()}), \
                mock.patch.object(A.Approver, "key_problems", sentinel), \
                mock.patch.object(subprocess, "run", sentinel), mock.patch.object(subprocess, "Popen", sentinel), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(["approve", self.d, "--workspace", self.ws, "--gate", "eval_set"])
        self.assertEqual(rc, 2, err.getvalue())
        self.assertNotIn("is not a TTY", err.getvalue())
        self.assertIn("approve " + ANCESTOR, err.getvalue())
        self.assertEqual(state(self.approvals), before)


class GatePty(PtyCase):
    """G6-2 (lane 2): under a pty the TTY checks pass and the ancestry check refuses."""
    def test_subprocess_under_pty(self):
        self.under_pty(())

    def test_in_process_twin(self):
        """The real CLI entry with only the TTY probe patched, the real environment and the real process table."""
        self.in_process(lambda name: False)


class GatePtyNoMarker(PtyCase):
    """G6-3 (lane 3): G6-2 with CLAUDECODE unset, and with every marker of the table removed."""
    def test_subprocess_without_claudecode(self):
        self.under_pty(("CLAUDECODE",))

    def test_subprocess_without_any_marker(self):
        A = gate_module()
        self.under_pty(tuple(A.markers(os.environ)))

    def test_in_process_without_claudecode(self):
        self.in_process(lambda name: name == "CLAUDECODE")

    def test_in_process_without_any_marker(self):
        A = gate_module()
        self.in_process(lambda name: bool(A.markers({name: "1"})))


class Keys(unittest.TestCase):
    """Throwaway keys for the unit cases: the approver key (passphrase), a key without a passphrase, and an intruder
    key (passphrase) that is not in the signers file."""
    @classmethod
    def setUpClass(cls):
        cls.kdir = tempfile.mkdtemp(prefix="gate-unit-", dir="/tmp")
        cls.key, cls.pub = T.make_key(cls.kdir, "approver", T.TEST_PASSPHRASE)
        cls.nopass, cls.nopass_pub = T.make_key(cls.kdir, "nopass", "")
        cls.intruder, cls.intruder_pub = T.make_key(cls.kdir, "intruder", T.TEST_PASSPHRASE)
        cls.signers = os.path.join(cls.kdir, "allowed_signers")
        write(cls.signers, T.signers_line(T.TEST_PRINCIPAL, cls.pub) + T.signers_line("nopass", cls.nopass_pub))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.kdir, True)

    def setUp(self):
        self.A = gate_module()
        self.calls, self.shown = [], []
        tty = mock.patch.object(self.A, "is_tty", lambda fd: True); tty.start(); self.addCleanup(tty.stop)

    def approver(self, **kw):
        args = dict(signers=self.signers, key=self.key, home=self.kdir, environ=T.AGENT_ENV,
                    read_proc=self.A.table_reader(T.TEST_PROCS), start_pid=T.TEST_START_PID, ssh_add=lambda: T.NO_IDENTITIES,
                    read_line=T.type_challenge, write=self.shown.append, run=T.keygen_runner(self.kdir, self.calls),
                    passphrase=T.TEST_PASSPHRASE)
        args.update(kw)
        return self.A.Approver(**args)

    def prepared(self, sha=SHA):
        return lambda: self.A.Prepared("branch-setup", sha, ["eval set files: evals/evals.json"])

    def signed(self, gate="eval_set", sha=SHA, **kw):
        return self.approver(**kw).sign("approve", gate, self.prepared(sha)).record


class PresenceChecks(Keys):
    def test_u1_non_tty_stdin_refuses_before_approver_check(self):
        """U1."""
        with mock.patch.object(self.A, "is_tty", lambda fd: fd != 0):
            with self.assertRaises(self.A.Refused) as cm:
                self.approver(ssh_add=sentinel).sign("approve", "eval_set", sentinel)
        self.assertEqual(str(cm.exception).splitlines(), [NOT_TTY % "approve"])
        self.assertEqual(self.calls, [])
        for fd, name in ((1, "stdout"), (2, "stderr")):
            with mock.patch.object(self.A, "is_tty", lambda x, fd=fd: x != fd):
                self.assertEqual(self.approver().presence_problems(), ["%s is not a TTY" % name])

    def test_u2_agent_marker_refuses_and_is_named(self):
        """U2."""
        for env, names in (({"CLAUDECODE": "1"}, "CLAUDECODE"), ({"CLAUDE_CODE_NEW_THING": "x", "AI_AGENT": "a"},
                           "AI_AGENT, CLAUDE_CODE_NEW_THING"), ({"CODEX_SANDBOX": "seatbelt"}, "CODEX_SANDBOX"),
                           ({"CURSOR_AGENT": "1"}, "CURSOR_AGENT"), ({"HERMES_SESSION": "1"}, "HERMES_SESSION")):
            with self.subTest(names):
                with self.assertRaises(self.A.Refused) as cm:
                    self.approver(environ=env, ssh_add=sentinel).sign("register", "registration", sentinel)
                self.assertEqual(str(cm.exception), "register needs a human at an interactive terminal (agent environment "
                                 "variables are set: %s)" % names)
        for env in ({"CLAUDE_CONFIG_DIR": "/x"}, {"CODEX_HOME": "/x"}, {"CLAUDE_AS_HOME": "/x"}, {"SSH_AUTH_SOCK": "/s"}):
            self.assertEqual(self.approver(environ=env).presence_problems(), [], env)

    def test_u3_agent_ancestor_refuses(self):
        """U3: a fake process table with an agent ancestor; every agent name; unreadable and odd tables fail closed."""
        for comm, path in (("claude.exe", "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"),
                           ("claude", "/Users/x/.local/bin/claude"), ("codex", "/opt/homebrew/bin/codex"),
                           ("opencode", "/opt/homebrew/bin/opencode"), ("hermes", "/usr/local/bin/hermes"),
                           ("node", "/opt/homebrew/bin/codex-aarch64-apple-darwin")):
            with self.subTest(comm):
                table = {4242: (77, "Python", "/usr/bin/python3"), 77: (88, "zsh", "/bin/zsh"), 88: (1, comm, path),
                         1: (0, "launchd", "/sbin/launchd")}
                ap = self.approver(read_proc=self.A.table_reader(table), ssh_add=sentinel)
                with self.assertRaises(self.A.Refused) as cm: ap.sign("live", "run", sentinel, budget_usd=1.0)
                self.assertRegex(str(cm.exception), r"^run --live needs a human at an interactive terminal \(agent "
                                 r"process in the parent chain: \S+ \(pid 88\)\)$")
        for table, why in (({}, "cannot read pid 4242"), ({4242: (77, "Python", "/p")}, "cannot read pid 77"),
                           ({4242: (77, "a", "/a"), 77: (4242, "b", "/b")}, "ppid loop"),
                           ({4242 + i: (4243 + i, "x", "/x") for i in range(80)}, "longer than")):
            with self.subTest(why):
                probs = self.approver(read_proc=self.A.table_reader(table)).presence_problems()
                self.assertEqual(len(probs), 1); self.assertIn("ancestry check failed closed: ", probs[0])
                self.assertIn(why, probs[0])

    def test_every_presence_reason_is_reported(self):
        table = {4242: (1, "claude.exe", "/c/claude.exe"), 1: (0, "launchd", "/sbin/launchd")}
        with mock.patch.object(self.A, "is_tty", lambda fd: False):
            probs = self.approver(environ={"CLAUDECODE": "1"}, read_proc=self.A.table_reader(table)).presence_problems()
        self.assertEqual(probs, ["stdin is not a TTY", "stdout is not a TTY", "stderr is not a TTY",
                                 "agent environment variables are set: CLAUDECODE",
                                 "agent process in the parent chain: claude.exe (pid 4242)"])

    def test_the_real_probe_reads_this_process_chain(self):
        probs = self.A.ancestry_problems(os.getpid(), self.A.libproc_read)
        self.assertFalse([p for p in probs if p.startswith("ancestry check failed closed")], probs)
        ppid, comm, path = self.A.libproc_read(1)
        self.assertEqual((ppid, comm), (0, "launchd"))


class ChallengeAndSign(Keys):
    def test_u4_wrong_challenge_refuses_and_signs_nothing(self):
        """U4."""
        ap = self.approver(read_line=lambda prompt: "AAAAAA\n")
        with self.assertRaises(self.A.Refused) as cm: ap.sign("approve", "eval_set", self.prepared())
        self.assertEqual(str(cm.exception), "approve: challenge mismatch: nothing was signed")
        self.assertFalse([c for c in self.calls if c[1:3] == ["-Y", "sign"]])

    def test_u5_good_signature(self):
        """U5: method ssh-sig, principal from the signature, read back as approved."""
        g = self.approver().sign("approve", "eval_set", self.prepared())
        r = g.record
        self.assertEqual((r["format"], r["method"], r["sha256"], r["principal"]), (1, "ssh-sig", SHA, T.TEST_PRINCIPAL))
        self.assertTrue(r["signature"].startswith("-----BEGIN SSH SIGNATURE-----"))
        self.assertEqual(self.approver().verdict(r, "approve", "eval_set", "branch-setup", SHA),
                         self.A.Verdict("approved", T.TEST_PRINCIPAL, ""))
        self.assertTrue(any("gate eval_set" in s and "branch-setup" in s and SHA in s for s in self.shown), self.shown)
        self.assertIn("eval set files: evals/evals.json", self.shown)

    def test_u5_the_terminal_form_signs_from_stdin(self):
        """The CLI form: payload on stdin, signature on stdout, no file (a key without a passphrase, so no prompt)."""
        data = self.A.payload("approve", "eval_set", "branch-setup", SHA, AT)
        sig = self.A.sign_payload(T.keygen_runner(self.kdir), self.nopass, data, interactive=True)
        rec = {"format": 1, "method": "ssh-sig", "sha256": SHA, "principal": "nopass", "at": AT, "signature": sig}
        self.assertEqual(self.approver().verdict(rec, "approve", "eval_set", "branch-setup", SHA).status, "approved")

    def test_payload_bytes(self):
        self.assertEqual(self.A.payload("live", "run", "branch-setup", SHA, AT, budget_usd=0.01),
                         ("skill-master-approval v1\nkind live\ngate run\nunit branch-setup\nsha256 %s\nbudget_usd 0.01\n"
                          "at %s\n" % (SHA, AT)).encode("ascii"))
        for bad in (dict(unit="x\n"), dict(unit="-x"), dict(gate="a b"), dict(sha256="AB" * 32), dict(at=AT + "\n"),
                    dict(kind="other")):
            args = dict(kind="approve", gate="eval_set", unit="u", sha256=SHA, at=AT); args.update(bad)
            with self.assertRaises(ValueError, msg=bad): self.A.payload(**args)

    def test_failed_signing_refuses(self):
        ap = self.approver(passphrase="wrong")
        with self.assertRaises(self.A.Refused) as cm: ap.sign("approve", "eval_set", self.prepared())
        self.assertIn("approve: signing failed", str(cm.exception))

    def test_a_key_outside_the_signers_file_refuses_after_signing(self):
        with self.assertRaises(self.A.Refused) as cm:
            self.approver(key=self.intruder).sign("approve", "eval_set", self.prepared())
        self.assertIn("approve: ", str(cm.exception)); self.assertIn("principal", str(cm.exception))

    def test_a_signature_that_verifies_for_no_principal_refuses(self):
        """S3: find-principals names a listed principal that did not sign, so only the immediate verify can refuse."""
        real = T.keygen_runner(self.kdir, self.calls)
        def lying(argv, **kw):
            if argv[1:3] == ["-Y", "find-principals"]: return 0, b"nopass\n", b""
            return real(argv, **kw)
        with self.assertRaises(self.A.Refused) as cm:
            self.approver(run=lying).sign("approve", "eval_set", self.prepared())
        self.assertTrue(str(cm.exception).startswith("approve: the new signature does not verify: "), cm.exception)
        self.assertTrue([c for c in self.calls if c[1:3] == ["-Y", "verify"]], self.calls)

    def test_lines_before_the_challenge_are_escaped(self):
        """Audit A note: a fixture name or path cannot clear or redraw the terminal before the challenge."""
        show = ["file evals/files/%s.csv" % SPOOF, "mark %s here" % RLO]
        for kind, gate, budget in (("approve", "eval_set", None), ("register", "registration", None), ("live", "run", 1.0)):
            with self.subTest(kind):
                del self.shown[:]
                with self.assertRaises(self.A.Refused):
                    self.approver(read_line=lambda p: "\n").sign(kind, gate, lambda: self.A.Prepared("branch-setup", SHA, show),
                                                                 budget_usd=budget)
                self.assertEqual(len(self.shown), 3, self.shown)
                for line in self.shown: self.assertTrue(line.isprintable(), repr(line))
                self.assertEqual(self.shown[:2], ["file evals/files/%s.csv" % SPOOF_SHOWN, "mark \\u202e here"])


class SignedBytes(Keys):
    """Audit A issue 1: the signature itself binds sha256 and kind, not only the stale check that runs before it."""
    def test_a_hash_edited_to_match_changed_files_reads_invalid(self):
        """Signed over SHA; the record's sha256 and the gated files both move to OTHER, so only the signed bytes refuse."""
        for kind, gate in (("approve", "eval_set"), ("register", "registration")):
            with self.subTest(kind):
                rec = self.approver().sign(kind, gate, self.prepared()).record
                self.assertEqual(self.approver().verdict(rec, kind, gate, "branch-setup", SHA).status, "approved")
                v = self.approver().verdict(dict(rec, sha256=OTHER), kind, gate, "branch-setup", OTHER)
                self.assertEqual(v.status, "invalid", v)
                self.assertIn("signature does not verify", v.reason)

    def test_a_record_signed_as_one_kind_does_not_read_as_another(self):
        """Same gate, unit and hash; only the kind line differs."""
        for signed, read in (("register", "approve"), ("approve", "register")):
            with self.subTest(signed=signed, read=read):
                rec = self.approver().sign(signed, "registration", self.prepared()).record
                self.assertEqual(self.approver().verdict(rec, signed, "registration", "branch-setup", SHA).status,
                                 "approved")
                v = self.approver().verdict(rec, read, "registration", "branch-setup", SHA)
                self.assertEqual(v.status, "invalid", v)


class FakeLibproc(object):
    """Stands in for libproc.dylib: proc_pidinfo answers one PROC_PIDT_SHORTBSDINFO record, proc_pidpath one path."""
    def __init__(self, pid, ppid=0, comm=b"python3", path=b"/usr/bin/python3", info_n=64, path_ok=True):
        self.record = struct.pack("=IIII", pid, ppid, 0, 0) + comm.ljust(16, b"\0") + b"\0" * 32
        self.path, self.info_n, self.path_ok = path, info_n, path_ok

    def proc_pidinfo(self, pid, flavor, arg, buf, size):
        ctypes.memmove(buf, self.record, len(self.record))
        return self.info_n

    def proc_pidpath(self, pid, buf, size):
        if not self.path_ok: return 0
        ctypes.memmove(buf, self.path + b"\0", len(self.path) + 1)
        return len(self.path)


class LibprocAnswers(unittest.TestCase):
    """Audit A issue 4: a libproc answer that cannot be trusted makes the walk fail closed. The fake library goes in
    through the module's library cache; the command line still loads the real /usr/lib/libproc.dylib."""
    def walk(self, lib):
        A = gate_module()
        with mock.patch.dict(A._LIB, {"lib": lib}):
            return A.ancestry_problems(4321, A.libproc_read)

    def test_a_well_formed_answer_is_read(self):
        A = gate_module()
        with mock.patch.dict(A._LIB, {"lib": FakeLibproc(4321, 0)}):
            self.assertEqual(A.libproc_read(4321), (0, "python3", "/usr/bin/python3"))
        self.assertEqual(self.walk(FakeLibproc(4321, 0)), [])
        self.assertEqual(self.walk(FakeLibproc(4321, 0, comm=b"claude.exe")),
                         ["agent process in the parent chain: claude.exe (pid 4321)"])

    def test_untrusted_answers_fail_closed(self):
        for why, lib, reason in (("an answer for another pid", FakeLibproc(9999, 0), "answered for pid 9999"),
                                 ("proc_pidpath fails", FakeLibproc(4321, 0, path_ok=False), "proc_pidpath failed"),
                                 ("a short answer", FakeLibproc(4321, 0, info_n=32), "proc_pidinfo returned 32")):
            with self.subTest(why):
                probs = self.walk(lib)
                self.assertEqual(len(probs), 1, probs)
                self.assertTrue(probs[0].startswith("ancestry check failed closed: cannot read pid 4321 ("), probs)
                self.assertIn(reason, probs[0])


class Tables(Keys):
    """P7 and P9: every agent name and every marker of the tables refuses, from literal lists that a table edit cannot
    shrink along with it."""
    def test_every_agent_name_refuses(self):
        self.assertEqual(set(self.A.AGENT_NAMES) - set(AGENTS), set())
        for name in AGENTS:
            for comm, path in ((name, "/usr/bin/x"), ("node", "/opt/bin/" + name)):
                with self.subTest(comm=comm, path=path):
                    table = {4242: (77, comm, path), 77: (1, "zsh", "/bin/zsh"), 1: (0, "launchd", "/sbin/launchd")}
                    self.assertEqual(self.approver(read_proc=self.A.table_reader(table)).presence_problems(),
                                     ["agent process in the parent chain: %s (pid 4242)" % name])

    def test_every_marker_refuses(self):
        self.assertEqual(set(self.A.HOST_MARKERS["exact"]) - set(EXACT_MARKERS), set())
        self.assertEqual(set(self.A.HOST_MARKERS["prefix"]) - set(MARKER_PREFIXES), set())
        for name in EXACT_MARKERS + tuple(p + "X" for p in MARKER_PREFIXES):
            with self.subTest(name):
                self.assertEqual(self.approver(environ={name: ""}).presence_problems(),
                                 ["agent environment variables are set: %s" % name])


class RecordVerdicts(Keys):
    def verdict(self, rec, gate="eval_set", unit="branch-setup", sha=SHA, kind="approve"):
        return self.approver().verdict(rec, kind, gate, unit, sha)

    def test_u6_changed_signature_bytes_read_invalid(self):
        """U6."""
        r = self.signed()
        lines = r["signature"].splitlines()
        lines[2] = lines[2][:10] + ("A" if lines[2][10] != "A" else "B") + lines[2][11:]
        v = self.verdict(dict(r, signature="\n".join(lines) + "\n"))
        self.assertEqual(v.status, "invalid"); self.assertIn("signature does not verify", v.reason)

    def test_u7_wrong_namespace_reads_invalid(self):
        """U7."""
        data = self.A.payload("approve", "eval_set", "branch-setup", SHA, AT)
        d = tempfile.mkdtemp(dir=self.kdir); f = os.path.join(d, "payload")
        with open(f, "wb") as fh: fh.write(data)
        rc, out, err = T.keygen_runner(self.kdir)([self.A.SSH_KEYGEN, "-Y", "sign", "-n", "other-namespace", "-f",
                                                   self.nopass, f])
        self.assertEqual(rc, 0, err)
        sig = T.read(f + ".sig")
        rec = {"format": 1, "method": "ssh-sig", "sha256": SHA, "principal": "nopass", "at": AT, "signature": sig}
        self.assertEqual(self.verdict(rec).status, "invalid")

    def test_u8_unknown_principal_reads_invalid(self):
        """U8: a key not in the signers file, under a listed principal or its own name."""
        data = self.A.payload("approve", "eval_set", "branch-setup", SHA, AT)
        sig = self.A.sign_payload(T.keygen_runner(self.kdir), self.intruder, data, passphrase=T.TEST_PASSPHRASE)
        for who in (T.TEST_PRINCIPAL, "intruder", "nopass"):
            rec = {"format": 1, "method": "ssh-sig", "sha256": SHA, "principal": who, "at": AT, "signature": sig}
            self.assertEqual(self.verdict(rec).status, "invalid", who)

    def test_u9_tampered_hash_is_not_approved(self):
        """U9: the record hash, the payload time, or the gated files changed after signing."""
        r = self.signed()
        self.assertEqual(self.verdict(dict(r, sha256="cd" * 32)).status, "stale")
        self.assertEqual(self.verdict(r, sha="cd" * 32).status, "stale")
        self.assertEqual(self.verdict(r, sha=None).status, "stale")
        self.assertEqual(self.verdict(dict(r, at="2026-10-20T09:41:01Z")).status, "invalid")
        self.assertEqual(self.verdict(dict(r, principal="nopass")).status, "invalid")

    def test_u10_unsigned_legacy_record_reads_unsigned_without_ssh_keygen(self):
        """U10, including a byte copy of golden's approvals.json in a temp workspace."""
        self.assertEqual(self.verdict({"sha256": SHA, "by": "pinesky", "at": AT}).status, "unsigned")
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp)
        for name in ("csv-cleaner", "csv-cleaner-workspace"):
            shutil.copytree(os.path.join(ROOT, "golden", "skills", name), os.path.join(tmp, name))
        src = os.path.join(ROOT, "golden", "skills", "csv-cleaner-workspace", "approvals.json")
        self.assertEqual(state(os.path.join(tmp, "csv-cleaner-workspace", "approvals.json")), state(src))
        spec = se.load_eval_set(os.path.join(tmp, "csv-cleaner"))[0]
        ws = os.path.join(tmp, "csv-cleaner-workspace")
        with mock.patch.dict(se.APPROVER, {"it": self.approver()}):
            for gate in ("requirements", "eval_set", "iteration-4", "registration"):
                v = se.approval_verdict(gate, spec, ws)
                self.assertEqual((v.status, v.principal), ("unsigned", None), gate)
                self.assertFalse(se.approval_ok(gate, spec, ws), gate)
        self.assertEqual(self.calls, [])

    def test_malformed_signed_records_read_invalid(self):
        r = self.signed()
        for bad in ([], "x", dict(r, format=2), dict(r, method="gpg"), dict(r, signature=None), dict(r, principal="-x"),
                    dict(r, principal="a b"), dict(r, sha256="xyz"), {"method": "ssh-sig"}, {"signature": "s"}):
            with self.subTest(bad=str(bad)[:40]):
                self.assertEqual(self.verdict(bad).status, "invalid")
        self.assertEqual(self.verdict(None).status, "missing")

    def test_u14_a_record_does_not_move_to_another_gate_kind_or_unit(self):
        """U14: golden's iteration-4 and registration share one sha256, so the payload binds gate, kind and unit."""
        r = self.signed(gate="iteration-1")
        self.assertEqual(self.verdict(r, gate="iteration-1").status, "approved")
        self.assertEqual(self.verdict(r, gate="registration").status, "invalid")
        self.assertEqual(self.verdict(r, gate="iteration-1", unit="other-skill").status, "invalid")
        self.assertEqual(self.verdict(r, gate="registration", kind="register").status, "invalid")


class KeyChecks(Keys):
    def test_u11_key_loaded_in_ssh_agent_fails(self):
        """U11: a fake `ssh-add -L` that lists the approver public key."""
        env = {"SSH_AUTH_SOCK": "/tmp/agent.sock"}
        loaded = (0, (self.pub + " approver\n").encode(), b"")
        probs = self.approver(environ=env, ssh_add=lambda: loaded).key_problems()
        self.assertEqual(len(probs), 1); self.assertIn("approver key is loaded in ssh-agent", probs[0])
        with self.assertRaises(self.A.Refused) as cm:
            self.approver(environ=env, ssh_add=lambda: loaded).sign("register", "registration", sentinel)
        self.assertTrue(str(cm.exception).startswith("register: approver-check: the approver key is loaded"), cm.exception)
        other = (0, (self.intruder_pub + " someone\n").encode(), b"")
        self.assertEqual(self.approver(environ=env, ssh_add=lambda: other).key_problems(), [])
        self.assertEqual(self.approver(environ=env).key_problems(), [])

    def test_ssh_add_that_cannot_answer_fails_closed(self):
        """Coordinator question 2: SSH_AUTH_SOCK unset, exit 2, or exit 1 with other output all fail."""
        self.assertEqual(self.approver(environ={}, ssh_add=lambda: T.NO_IDENTITIES).key_problems(),
                         ["SSH_AUTH_SOCK is not set, so ssh-agent cannot be asked whether the approver key is loaded; run "
                          "this from a Terminal where ssh-add -L works"])
        env = {"SSH_AUTH_SOCK": "/tmp/agent.sock"}
        for result in ((2, b"", b"Could not open a connection to your authentication agent.\n"),
                       (1, b"", b"error fetching identities: x\n"), (127, b"", b"")):
            with self.subTest(result[0]):
                probs = self.approver(environ=env, ssh_add=lambda r=result: r).key_problems()
                self.assertEqual(len(probs), 1)
                self.assertTrue(probs[0].startswith("ssh-add -L could not ask ssh-agent (exit %d" % result[0]), probs)

    def test_u12_key_without_passphrase_fails_and_a_passphrase_key_passes(self):
        """U12."""
        env = {"SSH_AUTH_SOCK": "/tmp/agent.sock"}
        probs = self.approver(environ=env, key=self.nopass).key_problems()
        self.assertEqual(len(probs), 1); self.assertIn("approver key opens without a passphrase", probs[0])
        self.assertEqual(self.approver(environ=env).key_problems(), [])
        self.assertTrue(any(c[1:4] == ["-y", "-P", ""] for c in self.calls), self.calls)

    def test_a_corrupt_key_file_fails(self):
        """K2: ssh-keygen cannot parse the key at all, which is not the wrong-passphrase answer of a good key."""
        path = os.path.join(tempfile.mkdtemp(dir=self.kdir), "corrupt")
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            f.write(T.read(self.key)[:200])
        probs = self.approver(environ={"SSH_AUTH_SOCK": "/tmp/agent.sock"}, key=path).key_problems()
        self.assertEqual(len(probs), 1, probs)
        self.assertTrue(probs[0].startswith("cannot read approver key ~/"), probs)
        self.assertIn("/corrupt (exit ", probs[0])

    def test_signers_file_problems(self):
        env = {"SSH_AUTH_SOCK": "/tmp/agent.sock"}
        d = tempfile.mkdtemp(dir=self.kdir)
        for text, want in (("", "lists no key"), ("# only a comment\n", "lists no key"),
                           ("pinesky ssh-ed25519\n", "line 1 does not parse"),
                           (T.signers_line("p", self.pub) + "garbage\n", "line 2 does not parse")):
            with self.subTest(want):
                p = os.path.join(d, "signers"); write(p, text)
                probs = self.approver(environ=env, signers=p).key_problems()
                self.assertTrue(any(want in x for x in probs), probs)
        self.assertIn("approver key missing: ", self.approver(environ=env, key=os.path.join(d, "none")).key_problems()[0])


class FixedPaths(Keys):
    def test_u13_cli_paths_are_fixed(self):
        """U13: the CLI reads only ~/.config/skill-master, with home from pwd; no variable or option moves it."""
        home = pwd.getpwuid(os.getuid()).pw_dir
        with mock.patch.dict(os.environ, {"HOME": "/tmp/elsewhere", "XDG_CONFIG_HOME": "/tmp/elsewhere"}):
            ap = self.A.cli_approver()
        self.assertEqual(ap.signers, os.path.join(home, ".config", "skill-master", "approvers.allowed_signers"))
        self.assertEqual(ap.key, os.path.join(home, ".config", "skill-master", "approver_ed25519"))
        self.assertTrue(ap.interactive)
        for cmd in ("approve", "register", "run", "approver-check"):
            argv = {"approve": ["approve", "s", "--gate", "eval_set"], "register": ["register", "s"],
                    "run": ["run", "s", "--live"], "approver-check": ["approver-check"]}[cmd]
            for opt in ("--signers", "--allowed-signers", "--key", "--by"):
                with self.subTest(cmd=cmd, opt=opt), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit): se.build_parser().parse_args(argv + [opt, "/x"])

    def test_u13_a_missing_signers_file_fails_every_gate(self):
        """U13 through the Python API: approve, register and live refuse; a signed record reads invalid."""
        home = tempfile.mkdtemp(dir=self.kdir)
        missing = os.path.join(home, ".config", "skill-master", "approvers.allowed_signers")
        rec = self.signed()
        ap = self.approver(signers=missing, home=home, environ={"SSH_AUTH_SOCK": "/s"})
        for kind, gate in (("approve", "eval_set"), ("register", "registration"), ("live", "run")):
            with self.subTest(kind):
                with self.assertRaises(self.A.Refused) as cm:
                    ap.sign(kind, gate, sentinel, budget_usd=1.0 if kind == "live" else None)
                self.assertIn("approver-check: approvers file missing: ~/.config/skill-master/approvers.allowed_signers",
                              str(cm.exception))
        self.assertEqual(ap.verdict(rec, "approve", "eval_set", "branch-setup", SHA),
                         self.A.Verdict("invalid", None, "approvers file missing: "
                                        "~/.config/skill-master/approvers.allowed_signers"))

    def test_the_real_approver_refuses_inside_a_test_process(self):
        ap = self.A.cli_approver()
        msg = "refusing to use the real approver files inside a test process (unittest is loaded)"
        with mock.patch.object(ap, "ssh_add", sentinel), mock.patch.object(ap, "run", sentinel):
            self.assertEqual(ap.key_problems(), [msg])
            self.assertEqual(ap.verdict(self.signed(), "approve", "eval_set", "branch-setup", SHA).reason, msg)

    def test_the_cli_process_loads_no_test_module(self):
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp)
        probe = os.path.join(tmp, "probe.py")
        write(probe, "import sys\nsys.path.insert(0, %r)\nimport skill_eval, eva\n"
                     "print(sorted(m for m in ('unittest', 'pytest', '_pytest') if m in sys.modules))\n" % ROOT)
        p = subprocess.run([sys.executable, "-B", probe], cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=60, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "[]"), p.stderr)


class SkillGate(T.Base):
    """skill_eval through the gate: records, plan lines, registration, and refusals after presence passes."""
    def setUp(self):
        T.Base.setUp(self)
        self.A = gate_module()
        self.d = make_skill(self.tmp, evals=evals_doc(human=False))
        self.spec = se.load_eval_set(self.d)[0]
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")
        self.approvals = os.path.join(self.ws, "approvals.json")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def with_approver(self, **kw):
        return mock.patch.dict(se.APPROVER, {"it": T.gate_approver(**kw)})

    def test_u5_plan_shows_the_principal(self):
        """U5 through skill_eval: the record is written with method ssh-sig and plan reads it as approved by the principal."""
        for g in ("requirements", "eval_set"):
            rc, out, err = self.cli("approve", self.d, "--workspace", self.ws, "--gate", g)
            self.assertEqual(rc, 0, err); self.assertIn("approved %s" % g, out)
        recs = rjson(self.approvals)
        self.assertEqual({r["method"] for r in recs.values()}, {"ssh-sig"})
        self.assertNotIn("by", recs["eval_set"])
        rc, out, _ = self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
        self.assertIn("approval gates: requirements=approved by test-approver, eval_set=approved by test-approver", out)

    def test_plan_lines_name_each_status(self):
        wjson(self.approvals, {"requirements": {"sha256": se.gate_hash("requirements", self.spec, self.ws), "by": "x",
                                                "at": AT}})
        rc, out, _ = self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
        self.assertIn("approval gates: requirements=NOT APPROVED (unsigned), eval_set=NOT APPROVED", out)
        se.approve("eval_set", self.spec, self.ws)
        wjson(os.path.join(self.d, "evals", "trigger_evals.json"), T.TRIGGERS[::-1])
        rc, out, _ = self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
        self.assertIn("eval_set=NOT APPROVED (stale)", out)
        rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--budget-usd", "100", "--engines", "claude")
        self.assertEqual(rc, 2); self.assertIn("gate requirements not approved (unsigned)", err)

    def test_registration_is_signed_and_verifies(self):
        n = se.run_iteration(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude(True))],
                             runs=1, log=quiet)
        se.grade_iteration(self.spec, self.ws, n, self.grader(), log=quiet)
        se.report_iteration(self.spec, self.ws, n)
        rec = se.approve("registration", self.spec, self.ws)
        reg = se.register(self.spec, self.ws)
        self.assertEqual(reg["approval"], rec)
        self.assertEqual((reg["signed"]["method"], reg["signed"]["principal"]), ("ssh-sig", T.TEST_PRINCIPAL))
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "approved")
        on_disk = rjson(os.path.join(self.ws, "registration.json"))
        wjson(os.path.join(self.ws, "registration.json"), dict(on_disk, skill_sha256="0" * 64))
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "stale")

    def test_plan_starts_ssh_keygen_once_per_current_signed_record(self):
        """Coordinator question 4: 0 calls for unsigned records, 1 for one signed record, 2 for four signed records."""
        calls = []
        counted = T.gate_approver(run=T.keygen_runner(T.shared_keys()["dir"], calls))
        it = os.path.join(self.ws, "iteration-1"); os.makedirs(it); wjson(os.path.join(it, "benchmark.json"), {})
        verify = lambda: len([c for c in calls if c[1:3] == ["-Y", "verify"]])
        with mock.patch.dict(se.APPROVER, {"it": counted}):
            wjson(self.approvals, {g: {"sha256": se.gate_hash(g, self.spec, self.ws), "by": "x", "at": AT}
                                   for g in ("requirements", "eval_set", "iteration-1", "registration")})
            self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
            self.assertEqual(verify(), 0)
            os.remove(self.approvals)
            se.approve("requirements", self.spec, self.ws); del calls[:]
            self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
            self.assertEqual(verify(), 1)
            for g in ("eval_set", "iteration-1", "registration"): se.approve(g, self.spec, self.ws)
            del calls[:]
            self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude")
            self.assertEqual(verify(), 2)

    def failing_approvers(self):
        """(label, approver overrides, refusal text): each fails one step after the presence checks."""
        k = T.shared_keys()
        intruder = T.make_key(tempfile.mkdtemp(dir=k["dir"]), "intruder", T.TEST_PASSPHRASE)[0]
        return (("wrong challenge", dict(read_line=lambda p: "XXXXXX\n"), "challenge mismatch: nothing was signed"),
                ("missing signers file", dict(signers=os.path.join(self.tmp, "no-signers")),
                 "approver-check: approvers file missing"),
                ("key outside the signers file", dict(key=intruder), "principal"))

    def test_approve_refuses_after_presence_passes(self):
        for g in ("requirements", "eval_set"):
            for label, kw, want in self.failing_approvers():
                with self.subTest(gate=g, why=label), self.with_approver(**kw):
                    before = state(self.approvals)
                    rc, out, err = self.cli("approve", self.d, "--workspace", self.ws, "--gate", g)
                    self.assertEqual(rc, 2, err); self.assertIn(want, err); self.assertEqual(state(self.approvals), before)

    def test_register_and_live_starts_refuse_after_presence_passes(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        n = se.run_iteration(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude())], runs=1,
                             log=quiet)
        se.grade_iteration(self.spec, self.ws, n, self.grader(), log=quiet)
        se.report_iteration(self.spec, self.ws, n)
        golden = os.path.join(self.tmp, "golden.json")
        wjson(golden, {"examples": [{"prompt": "p", "output": "o", "item": "i", "label": True}]})
        reg = os.path.join(self.ws, "registration.json")
        live = (("run", "--engines", "claude", "--runs", "1"), ("grade", "--engines", "claude", "--iteration", str(n)),
                ("calibrate", "--iteration", str(n), "--golden", golden), ("loop", "--engines", "claude", "--runs", "1",
                                                                           "--max-iterations", "1"))
        started = []
        for label, kw, want in self.failing_approvers():
            with self.subTest(why=label), self.with_approver(**kw), \
                    mock.patch.object(se, "live_exec", lambda *a, **k: started.append(a) or (1, "", "")):
                rc, _, err = self.cli("register", self.d, "--workspace", self.ws)
                self.assertEqual(rc, 2, err); self.assertIn(want, err); self.assertFalse(os.path.exists(reg))
                for cmd, *extra in live:
                    before = state(self.approvals)
                    rc, _, err = self.cli(cmd, self.d, "--workspace", self.ws, "--live", "--budget-usd", "100", *extra)
                    self.assertEqual(rc, 2, (cmd, err)); self.assertIn(want, err, cmd)
                    self.assertEqual(state(self.approvals), before)
                self.assertEqual(self.status_calls, []); self.assertEqual(started, []); self.assertFalse(se._LIVE["on"])
        self.assertEqual(se.iterations(self.ws), [n])

    def test_a_signed_run_reaches_live_mode_only_with_a_grant(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        with self.assertRaises(se.EvalError): se.enable_live(None)
        with self.assertRaises(se.EvalError): se.enable_live(self.A.Grant("approve", "eval_set", "u", SHA, {}))
        self.assertFalse(se._LIVE["on"])
        seen = []
        fake = FakeClaude()
        with mock.patch.object(se, "live_exec", lambda cmd, cwd, timeout, env=None: seen.append(se._LIVE["on"]) or
                               fake(cmd, cwd, timeout)):
            rc, out, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--budget-usd", "100", "--engines",
                                    "claude", "--runs", "1")
        self.assertEqual(rc, 0, err); self.assertTrue(seen and all(seen))

    def test_inputs_changed_after_signing_refuse(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        skill_md = os.path.join(self.d, "SKILL.md")
        def edit_then_type(prompt):
            with open(skill_md, "a", encoding="utf-8") as f: f.write("\nedited while the challenge was shown\n")
            return T.type_challenge(prompt)
        with self.with_approver(read_line=edit_then_type), mock.patch.object(se, "live_exec", sentinel):
            rc, out, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--budget-usd", "100", "--engines",
                                    "claude", "--runs", "1")
        self.assertEqual(rc, 2, err); self.assertIn("changed after signing", err); self.assertFalse(se._LIVE["on"])

    def test_cli_approver_check(self):
        self.assertEqual(self.cli("approver-check")[:2], (0, "approver-check: OK\n"))
        with self.with_approver(environ={}):
            rc, out, _ = self.cli("approver-check")
        self.assertEqual((rc, out.strip()), (1, "approver-check: SSH_AUTH_SOCK is not set, so ssh-agent cannot be asked "
                                                "whether the approver key is loaded; run this from a Terminal where "
                                                "ssh-add -L works"))

    def ready_iteration(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        n = se.run_iteration(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude())], runs=1,
                             log=quiet)
        se.grade_iteration(self.spec, self.ws, n, self.grader(), log=quiet)
        se.report_iteration(self.spec, self.ws, n)
        return n

    def edited_after_signing(self, edit, *argv):
        def edit_then_type(prompt):
            edit()
            return T.type_challenge(prompt)
        with self.with_approver(read_line=edit_then_type), mock.patch.object(se, "live_exec", sentinel):
            rc, out, err = self.cli(*argv)
        self.assertEqual(rc, 2, err)
        self.assertIn("the command's inputs changed after signing", err)
        self.assertFalse(se._LIVE["on"])

    def test_golden_file_edited_after_signing_refuses(self):
        """Audit A issue 3: the --golden file is part of the signed plan."""
        n = self.ready_iteration()
        golden = os.path.join(self.tmp, "golden.json")
        wjson(golden, {"examples": [{"prompt": "p", "output": "o", "item": "i", "label": True}]})
        self.edited_after_signing(
            lambda: wjson(golden, {"examples": [{"prompt": "p2", "output": "o", "item": "i", "label": True}]}),
            "calibrate", self.d, "--workspace", self.ws, "--live", "--budget-usd", "100", "--iteration", str(n),
            "--golden", golden)

    def test_old_skill_folder_edited_after_signing_refuses(self):
        """Audit A issue 3: the --old-skill folder is part of the signed plan."""
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        old = os.path.join(self.tmp, "old-copy")
        shutil.copytree(self.d, old)
        def edit():
            with open(os.path.join(old, "SKILL.md"), "a", encoding="utf-8") as f: f.write("\nedited after signing\n")
        self.edited_after_signing(edit, "run", self.d, "--workspace", self.ws, "--live", "--budget-usd", "100",
                                  "--engines", "claude", "--runs", "1", "--baseline", "old", "--old-skill", old)

    def rolled_back_after_signing(self, gate, path, change):
        """The run is signed over the newer gated file and its newer signed approval. While the challenge is on screen
        both go back to the older pair, which is still validly signed, so the gate check after signing passes and only
        the gate's field in the live plan hash can notice."""
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        older = (T.read(path), T.read(self.approvals))
        change()
        se.approve(gate, self.spec, self.ws)
        self.assertNotEqual(T.read(self.approvals), older[1])
        def roll_back():
            write(path, older[0]); write(self.approvals, older[1])
        self.edited_after_signing(roll_back, "run", self.d, "--workspace", self.ws, "--live", "--budget-usd", "100",
                                  "--engines", "claude", "--runs", "1")
        self.assertEqual((T.read(path), T.read(self.approvals)), older)
        self.assertTrue(se.approval_ok(gate, self.spec, self.ws), "control: the older pair still reads approved")

    def test_eval_set_rolled_back_to_an_older_signed_pair_refuses(self):
        """Verifier round 1, F15: eval_set_sha256 is part of the signed plan."""
        trig = os.path.join(self.d, "evals", "trigger_evals.json")
        self.rolled_back_after_signing("eval_set", trig, lambda: wjson(trig, T.TRIGGERS[::-1]))

    def test_requirements_rolled_back_to_an_older_signed_pair_refuses(self):
        """Verifier round 1, F15: requirements_sha256 is part of the signed plan."""
        req = os.path.join(self.ws, "requirements.md")
        self.rolled_back_after_signing("requirements", req, lambda: write(req, "goal, revised"))

    def test_a_static_gate_failure_refuses_before_the_challenge(self):
        """Audit A issue 3: an eval set that is not signed refuses inside prepare(); nothing is shown or asked."""
        se.approve("requirements", self.spec, self.ws)
        asked, shown = [], []
        before = state(self.approvals)
        with self.with_approver(read_line=lambda p: asked.append(p) or "\n", write=shown.append), \
                mock.patch.object(se, "live_exec", sentinel):
            rc, out, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--budget-usd", "100", "--engines",
                                    "claude", "--runs", "1")
        self.assertEqual(rc, 2, err)
        self.assertIn("gate eval_set not approved", err)
        self.assertEqual((asked, shown), ([], []))
        self.assertEqual(state(self.approvals), before)
        self.assertEqual(self.status_calls, [])

    def test_a_signature_for_no_principal_writes_nothing(self):
        """S3 through skill_eval: find-principals lies, the immediate verify refuses, approvals.json is not written."""
        real = T.keygen_runner(T.shared_keys()["dir"])
        def lying(argv, **kw):
            if argv[1:3] == ["-Y", "find-principals"]: return 0, b"someone-else\n", b""
            return real(argv, **kw)
        with self.with_approver(run=lying):
            rc, out, err = self.cli("approve", self.d, "--workspace", self.ws, "--gate", "eval_set")
        self.assertEqual(rc, 2, err)
        self.assertIn("approve: the new signature does not verify: ", err)
        self.assertFalse(os.path.exists(self.approvals))

    def test_shown_paths_cannot_redraw_the_terminal(self):
        """Audit A note: approve_show and the --live show lines escape ESC[2J and a carriage return."""
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
        with self.with_approver(write=shown.append, read_line=lambda p: "\n"), mock.patch.object(se, "live_exec", sentinel):
            rc, _, err = self.cli("run", self.d, "--workspace", ws, "--live", "--budget-usd", "100", "--engines", "claude",
                                  "--runs", "1")
        self.assertEqual(rc, 2, err); self.assertIn("challenge mismatch", err)
        for line in shown: self.assertTrue(line.isprintable(), repr(line))
        self.assertIn("eval set file files:evals/files/%s.csv: <missing>" % SPOOF_SHOWN, shown)
        self.assertTrue(any(x.startswith("workspace: ") and x.endswith("ws" + SPOOF_SHOWN) for x in shown), shown)
        self.assertTrue(any(x.startswith("live command: ") and SPOOF_SHOWN in x for x in shown), shown)

    def test_a_non_object_approvals_file_refuses_before_the_challenge(self):
        """Audit A note: valid JSON that is not an object refuses inside prepare(); nothing is signed or written."""
        calls = []
        for text in NON_OBJECTS:
            with self.subTest(text):
                write(self.approvals, text)
                asked = []
                with self.with_approver(read_line=lambda p: asked.append(p) or T.type_challenge(p),
                                        run=T.keygen_runner(T.shared_keys()["dir"], calls)):
                    rc, out, err = self.cli("approve", self.d, "--workspace", self.ws, "--gate", "requirements")
                self.assertEqual(rc, 2, err)
                self.assertIn("approvals.json is not a JSON object (it holds %s); nothing was signed" % text, err)
                self.assertEqual(asked, [])
                self.assertEqual(T.read(self.approvals), text)
        self.assertFalse([c for c in calls if c[1:3] == ["-Y", "sign"]], calls)

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
    """Audit A issue 2: an unsigned rule_review record, or one signed by a key outside the signers file, is not a
    recorded decision for rule_review_problems, check_live_gates or the plan line. The fixture's regex matcher holds
    Korean text (escapes, so this file stays ASCII), which eva.review_items lists as a rule-review finding."""
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

    def test_control_a_signed_decision_is_recorded(self):
        self.assertTrue(self.h)
        self.assertEqual(self.problems, [])
        se.approve("rule_review", self.spec, self.ws)
        probs, gate, line = self.readers()
        self.assertEqual(probs, [])
        self.assertTrue(gate.startswith("gate requirements not approved"), gate)
        self.assertIn("RECORDED (signed by %s)" % T.TEST_PRINCIPAL, line)

    def refused_everywhere(self, status):
        probs, gate, line = self.readers()
        want = "rule review not recorded (%s): 1 finding(s) need a human decision" % status
        self.assertEqual(len(probs), 1, probs); self.assertTrue(probs[0].startswith(want), probs)
        self.assertTrue(gate.startswith(want), gate)
        self.assertTrue(line.endswith("NOT RECORDED (%s; blocks --live and the loop)" % status), line)

    def test_an_unsigned_record_is_not_a_decision(self):
        wjson(self.approvals, {"rule_review": {"sha256": self.h, "by": "pinesky", "at": AT}})
        self.refused_everywhere("unsigned")

    def test_a_record_signed_outside_the_signers_file_is_not_a_decision(self):
        A, k = gate_module(), T.shared_keys()
        intruder = T.make_key(tempfile.mkdtemp(dir=k["dir"]), "intruder", T.TEST_PASSPHRASE)[0]
        data = A.payload("approve", "rule_review", "branch-setup", self.h, AT)
        sig = A.sign_payload(T.keygen_runner(k["dir"]), intruder, data, passphrase=T.TEST_PASSPHRASE)
        wjson(self.approvals, {"rule_review": {"format": 1, "method": "ssh-sig", "sha256": self.h,
                                               "principal": T.TEST_PRINCIPAL, "at": AT, "signature": sig}})
        self.refused_everywhere("invalid")


if __name__ == "__main__":
    unittest.main()
