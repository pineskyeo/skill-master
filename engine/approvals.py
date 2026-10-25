"""The human gate: approve, register and the start of every --live run need a person at a real terminal and an ssh
signature from a key listed in ~/.config/skill-master/approvers.allowed_signers (references/approval-gates.md).

The command line uses cli_approver() only; its paths and probes are fixed. Tests build an Approver with their own
signers file, key, environment, process table, line reader, ssh-add and runner. Python code that imports this module
can do the same, so the gate binds the command line, not the process (Appendix C, R1). ASCII only.
"""
import collections, ctypes, errno, math, os, pwd, re, secrets, shutil, struct, subprocess, sys, tempfile, time

NAMESPACE = "skill-master-approval"
FORMAT = 1
SSH_KEYGEN = "/usr/bin/ssh-keygen"
SSH_ADD = "/usr/bin/ssh-add"
LIBPROC = "/usr/lib/libproc.dylib"
KEYGEN_ENV = {"PATH": "/usr/bin:/bin"}
SIGNERS_PARTS = (".config", "skill-master", "approvers.allowed_signers")
KEY_PARTS = (".config", "skill-master", "approver_ed25519")
KINDS = ("approve", "register", "live")
STATUSES = ("approved", "unsigned", "invalid", "stale", "missing")
CHALLENGE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CHALLENGE_LENGTH = 6
NO_IDENTITIES = "The agent has no identities."
TEST_MODULES = ("unittest", "pytest", "_pytest")
IN_TESTS = "refusing to use the real approver files inside a test process (unittest is loaded)"

AGENT_NAMES = ("claude", "codex", "opencode", "hermes", "cursor")
HOST_MARKERS = {
    "exact": ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AS_ACTIVE", "CLAUDE_AS_CALLER", "CLAUDE_AS_AUTO_SWITCH",
              "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "CLAUDE_TMPDIR", "AI_AGENT",
              "AGENT_TRANSCRIPTS", "SAND_AGENT", "SANDBOX_RUNTIME", "OPENCODE"),
    "prefix": ("CLAUDE_CODE_", "CODEX_", "CURSOR_", "__CURSOR_", "OPENCODE_", "HERMES_"),
}
USER_CONFIG = ("CLAUDE_CONFIG_DIR", "CLAUDE_AS_HOME", "CLAUDE_ACCOUNTS", "CLAUDE_AS_REAL_CLAUDE", "CODEX_HOME",
               "SKILL_EVAL_ACCOUNT_SWITCH")

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)
PRINCIPAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}", re.ASCII)
SHA_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", re.ASCII)
SIG_RE = re.compile(r"-----BEGIN SSH SIGNATURE-----\n[A-Za-z0-9+/=\n]+-----END SSH SIGNATURE-----\n?", re.ASCII)
KEY_TYPE_RE = re.compile(r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521))", re.ASCII)
B64_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}", re.ASCII)
SIGNERS_LINE_RE = re.compile(r'(?P<principals>\S+)(?P<options>(\s+[A-Za-z-]+(="[^"]*")?)*)\s+(?P<type>\S+)\s+(?P<b64>\S+)'
                             r'(\s+.*)?', re.ASCII)

Verdict = collections.namedtuple("Verdict", "status principal reason")
Prepared = collections.namedtuple("Prepared", "unit sha256 show")


class Refused(Exception):
    """The gate refused; reasons holds one line per failing check of a single step."""
    def __init__(self, what, reasons):
        Exception.__init__(self, "\n".join(reasons))
        self.what, self.reasons = what, list(reasons)


class Grant(object):
    """What Approver.sign returns after the signature verified: the signed fields and the stored record."""
    def __init__(self, kind, gate, unit, sha256, record):
        self.kind, self.gate, self.unit, self.sha256, self.record = kind, gate, unit, sha256, record


def is_tty(fd):
    return os.isatty(fd)


def label(kind, gate):
    return "%s --live" % gate if kind == "live" else kind


def payload(kind, gate, unit, sha256, at, budget_usd=None):
    """The canonical bytes a signature covers. Every value is checked, so no two field sets give the same bytes."""
    if kind not in KINDS: raise ValueError("kind %r is not one of %s" % (kind, ", ".join(KINDS)))
    for name, value, rx in (("gate", gate, NAME_RE), ("unit", unit, NAME_RE), ("sha256", sha256, SHA_RE),
                            ("at", at, AT_RE)):
        if not isinstance(value, str) or not rx.fullmatch(value): raise ValueError("%s %r is not valid" % (name, value))
    lines = ["skill-master-approval v%d" % FORMAT, "kind " + kind, "gate " + gate, "unit " + unit, "sha256 " + sha256]
    if kind == "live":
        if isinstance(budget_usd, bool) or not isinstance(budget_usd, (int, float)) or not math.isfinite(budget_usd) \
                or budget_usd <= 0:
            raise ValueError("budget_usd %r is not a finite amount > 0" % (budget_usd,))
        lines.append("budget_usd %r" % float(budget_usd))
    elif budget_usd is not None:
        raise ValueError("only a live payload has a budget")
    lines.append("at " + at)
    return ("\n".join(lines) + "\n").encode("ascii")


def markers(environ):
    """Sorted names of agent-host variables that are set (HOST_MARKERS), never user configuration (USER_CONFIG)."""
    return sorted(k for k in environ if k not in USER_CONFIG
                  and (k in HOST_MARKERS["exact"] or k.startswith(HOST_MARKERS["prefix"])))


def agent_name(comm, path):
    for name in (comm, os.path.basename(path or "")):
        if name and name.lower().startswith(AGENT_NAMES): return name
    return None


def ancestry_problems(start_pid, read_proc, limit=64):
    """[] when no process from start_pid up to the root is an agent; one reason otherwise. read_proc(pid) returns
    (ppid, comm, path); any read failure, a loop or an overlong chain fails closed."""
    pid, seen = start_pid, set()
    while True:
        if pid in seen: return ["ancestry check failed closed: ppid loop at pid %d" % pid]
        if len(seen) >= limit: return ["ancestry check failed closed: parent chain longer than %d" % limit]
        seen.add(pid)
        try:
            ppid, comm, path = read_proc(pid)
        except (OSError, LookupError, ValueError) as ex:
            return ["ancestry check failed closed: cannot read pid %d (%s)" % (pid, ex)]
        name = agent_name(comm, path)
        if name: return ["agent process in the parent chain: %s (pid %d)" % (name, pid)]
        if ppid == 0: return []
        pid = ppid


def table_reader(table):
    def read(pid):
        return table[pid]
    return read


_LIB = {}


def libproc_read(pid):
    """(ppid, comm, path) of pid through libproc. PROC_PIDT_SHORTBSDINFO and proc_pidpath read processes of any uid.
    PROC_PIDTBSDINFO refused pid 1 (launchd, root) with EPERM when probed; that it refuses the root login process of a
    Terminal chain the same way is inferred, not probed."""
    if sys.platform != "darwin": raise OSError(errno.ENOSYS, "the ancestry probe supports macOS only, not %s" % sys.platform)
    if "lib" not in _LIB:
        lib = ctypes.CDLL(LIBPROC, use_errno=True)
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        _LIB["lib"] = lib
    lib = _LIB["lib"]
    buf = ctypes.create_string_buffer(64)
    ctypes.set_errno(0)
    n = lib.proc_pidinfo(pid, 13, 0, buf, 64)
    if n != 64: raise OSError(ctypes.get_errno() or errno.EIO, "proc_pidinfo returned %d" % n)
    got, ppid = struct.unpack_from("=II", buf.raw, 0)
    if got != pid: raise OSError(errno.EIO, "proc_pidinfo answered for pid %d" % got)
    comm = buf.raw[16:32].split(b"\0", 1)[0].decode("utf-8", "replace")
    pbuf = ctypes.create_string_buffer(4096)
    ctypes.set_errno(0)
    if lib.proc_pidpath(pid, pbuf, 4096) <= 0: raise OSError(ctypes.get_errno() or errno.EIO, "proc_pidpath failed")
    return ppid, comm, pbuf.value.decode("utf-8", "replace")


def run_process(argv, input=None, new_session=True, timeout=30, env=None):
    p = subprocess.run(argv, input=input, stdin=None if input is not None else subprocess.DEVNULL, capture_output=True,
                       timeout=timeout, env=KEYGEN_ENV if env is None else env, start_new_session=new_session)
    return p.returncode, p.stdout, p.stderr


def printable(text):
    """text with every character that is not printable (ESC, CR, other controls, format marks) written as its Python
    escape, so a file name or path cannot clear or redraw the terminal before the challenge."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in text)


def first_line(data):
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else (data or "")
    return next((x.strip() for x in text.splitlines() if x.strip()), "no output")


def sign_payload(run, key, data, passphrase=None, interactive=False):
    """Armored signature over data. interactive: the payload goes on stdin and the signature comes back on stdout, so no
    file exists that another process could swap while the passphrase is typed on the terminal. Otherwise the payload is
    a file in a fresh folder, the passphrase (if any) goes on stdin and ssh-keygen gets no controlling terminal."""
    if interactive:
        rc, out, err = run([SSH_KEYGEN, "-Y", "sign", "-n", NAMESPACE, "-f", key], input=data, new_session=False,
                           timeout=600)
        if rc != 0: raise OSError("ssh-keygen -Y sign exited %d: %s" % (rc, first_line(err)))
        return out.decode("ascii")
    folder = tempfile.mkdtemp(prefix="gate-sign-")
    try:
        path = os.path.join(folder, "payload")
        with open(path, "wb") as f: f.write(data)
        stdin = None if passphrase is None else (passphrase + "\n").encode("utf-8")
        rc, out, err = run([SSH_KEYGEN, "-Y", "sign", "-n", NAMESPACE, "-f", key, path], input=stdin, new_session=True)
        if rc != 0: raise OSError("ssh-keygen -Y sign exited %d: %s" % (rc, first_line(err)))
        with open(path + ".sig", encoding="ascii") as f: return f.read()
    finally:
        shutil.rmtree(folder, True)


def parse_signers(text):
    """{(type, base64)} of every key line; ValueError names the first line that does not parse."""
    keys = set()
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"): continue
        m = SIGNERS_LINE_RE.fullmatch(line.strip())
        if not m or not KEY_TYPE_RE.fullmatch(m.group("type")) or not B64_RE.fullmatch(m.group("b64")):
            raise ValueError("approvers file line %d does not parse as `principal [options] key-type base64`" % i)
        keys.add((m.group("type"), m.group("b64")))
    return keys


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def in_test_process():
    return any(m in sys.modules for m in TEST_MODULES)


class Approver(object):
    def __init__(self, signers, key, home, environ, read_proc, start_pid, ssh_add, read_line, write, run,
                 passphrase=None, interactive=False, refuse_in_tests=False):
        self.signers, self.key, self.home, self.environ = signers, key, home, environ
        self.read_proc, self.start_pid, self.ssh_add = read_proc, start_pid, ssh_add
        self.read_line, self.write, self.run = read_line, write, run
        self.passphrase, self.interactive, self.refuse_in_tests = passphrase, interactive, refuse_in_tests

    def shown(self, path):
        h = self.home.rstrip(os.sep) + os.sep
        return "~" + os.sep + path[len(h):] if path.startswith(h) else path

    def presence_problems(self):
        out = []
        for fd, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
            try: tty = is_tty(fd)
            except (OSError, ValueError): tty = False
            if not tty: out.append("%s is not a TTY" % name)
        found = markers(self.environ)
        if found: out.append("agent environment variables are set: %s" % ", ".join(found))
        return out + ancestry_problems(os.getpid() if self.start_pid is None else self.start_pid, self.read_proc)

    def signer_keys(self):
        with open(self.signers, encoding="utf-8") as f: return parse_signers(f.read())

    def key_problems(self):
        """approver-check: the signers file and the key exist, the key needs a passphrase, ssh-agent does not hold it."""
        if self.refuse_in_tests and in_test_process(): return [IN_TESTS]
        out, keys = [], set()
        if not os.path.isfile(self.signers):
            out.append("approvers file missing: %s" % self.shown(self.signers))
        else:
            try:
                keys = self.signer_keys()
                if not keys: out.append("approvers file %s lists no key" % self.shown(self.signers))
            except (OSError, ValueError) as ex:
                out.append(str(ex))
        if not os.path.isfile(self.key):
            out.append("approver key missing: %s" % self.shown(self.key))
        else:
            try:
                rc, _, err = self.run([SSH_KEYGEN, "-y", "-P", "", "-f", self.key])
            except (OSError, subprocess.SubprocessError) as ex:
                rc, err = None, str(ex).encode()
            if rc == 0:
                out.append("approver key opens without a passphrase: %s (pinesky's key needs one)" % self.shown(self.key))
            elif b"incorrect passphrase" not in (err or b""):
                out.append("cannot read approver key %s (exit %s: %s)" % (self.shown(self.key), rc, first_line(err)))
        out += self.agent_problems(keys)
        return out

    def agent_problems(self, keys):
        if not self.environ.get("SSH_AUTH_SOCK"):
            return ["SSH_AUTH_SOCK is not set, so ssh-agent cannot be asked whether the approver key is loaded; run this "
                    "from a Terminal where ssh-add -L works"]
        try:
            rc, out, err = self.ssh_add()
        except (OSError, subprocess.SubprocessError) as ex:
            rc, out, err = None, b"", str(ex).encode()
        if rc == 1 and out.decode("utf-8", "replace").strip() == NO_IDENTITIES and not (err or b"").strip():
            return []
        if rc == 0:
            listed = set()
            for line in out.decode("utf-8", "replace").splitlines():
                parts = line.split()
                if len(parts) >= 2: listed.add((parts[0], parts[1]))
            hit = sorted(listed & keys)
            if hit:
                return ["the approver key is loaded in ssh-agent (ssh-add -L lists %s %s...); remove it with "
                        "ssh-add -d %s" % (hit[0][0], hit[0][1][:16], self.shown(self.key))]
            return []
        return ["ssh-add -L could not ask ssh-agent (exit %s: %s); cannot confirm the approver key is not loaded"
                % (rc, first_line(err or out))]

    def sign(self, kind, gate, prepare, budget_usd=None):
        """Presence, then approver-check (which starts ssh-keygen -y and ssh-add -L), then prepare(), the summary with
        every non-printable character escaped, the typed challenge, signing and an immediate verify. Nothing is signed
        unless every earlier step passed; prepare() may raise the caller's own error."""
        what = label(kind, gate)
        problems = self.presence_problems()
        if problems:
            raise Refused(what, ["%s needs a human at an interactive terminal (%s)" % (what, p) for p in problems])
        problems = self.key_problems()
        if problems: raise Refused(what, ["%s: approver-check: %s" % (what, p) for p in problems])
        prep = prepare()
        at = now_iso()
        try:
            data = payload(kind, gate, prep.unit, prep.sha256, at, budget_usd)
        except ValueError as ex:
            raise Refused(what, ["%s: cannot sign: %s" % (what, ex)])
        summary = "%s: gate %s, unit %s, sha256 %s" % (what, gate, prep.unit, prep.sha256)
        if budget_usd is not None: summary += ", budget $%.2f" % budget_usd
        for line in list(prep.show) + [summary]: self.write(printable(line))
        challenge = "".join(secrets.choice(CHALLENGE_ALPHABET) for _ in range(CHALLENGE_LENGTH))
        typed = self.read_line("type %s to sign (anything else cancels): " % challenge)
        if (typed or "").strip() != challenge:
            raise Refused(what, ["%s: challenge mismatch: nothing was signed" % what])
        try:
            signature = sign_payload(self.run, self.key, data, self.passphrase, self.interactive)
            principal = self.find_principal(signature)
        except (OSError, ValueError, UnicodeDecodeError, subprocess.SubprocessError) as ex:
            raise Refused(what, ["%s: signing failed: %s" % (what, ex)])
        record = {"format": FORMAT, "method": "ssh-sig", "sha256": prep.sha256, "principal": principal, "at": at,
                  "signature": signature}
        v = self.verdict(record, kind, gate, prep.unit, prep.sha256, budget_usd)
        if v.status != "approved": raise Refused(what, ["%s: the new signature does not verify: %s" % (what, v.reason)])
        return Grant(kind, gate, prep.unit, prep.sha256, record)

    def find_principal(self, signature):
        folder = tempfile.mkdtemp(prefix="gate-find-")
        try:
            path = os.path.join(folder, "s.sig")
            with open(path, "w", encoding="ascii") as f: f.write(signature)
            rc, out, err = self.run([SSH_KEYGEN, "-Y", "find-principals", "-f", self.signers, "-s", path])
        finally:
            shutil.rmtree(folder, True)
        names = [x.strip() for x in out.decode("utf-8", "replace").splitlines() if x.strip()]
        if rc != 0 or len(names) != 1 or not PRINCIPAL_RE.fullmatch(names[0]):
            raise ValueError("the signature matches no single principal in the approvers file (%s)"
                             % (first_line(err) if rc != 0 else "%d principals" % len(names)))
        return names[0]

    def verdict(self, record, kind, gate, unit, sha256, budget_usd=None):
        """The first rule that matches decides: missing, unsigned, invalid (a malformed record or no signers file),
        stale (the gated files are gone or hash elsewhere), invalid (ssh-keygen -Y verify fails), approved. Only a
        signed record that reaches the verify step starts ssh-keygen."""
        if record is None: return Verdict("missing", None, "no record")
        if isinstance(record, dict) and "signature" not in record and "method" not in record:
            return Verdict("unsigned", None, "no signature (a record from before signed approvals)")
        problem = record_problem(record)
        if problem: return Verdict("invalid", None, problem)
        if self.refuse_in_tests and in_test_process(): return Verdict("invalid", None, IN_TESTS)
        if not os.path.isfile(self.signers):
            return Verdict("invalid", None, "approvers file missing: %s" % self.shown(self.signers))
        if not sha256 or record["sha256"] != sha256:
            return Verdict("stale", record["principal"], "the gated files changed since signing" if sha256 else
                           "the gated files are missing")
        try:
            data = payload(kind, gate, unit, sha256, record["at"], budget_usd)
            problem = self.verify(data, record["signature"], record["principal"])
        except (OSError, ValueError, subprocess.SubprocessError) as ex:
            problem = str(ex)
        if problem: return Verdict("invalid", None, problem)
        return Verdict("approved", record["principal"], "")

    def verify(self, data, signature, principal):
        folder = tempfile.mkdtemp(prefix="gate-verify-")
        try:
            path = os.path.join(folder, "s.sig")
            with open(path, "w", encoding="ascii") as f: f.write(signature)
            rc, out, err = self.run([SSH_KEYGEN, "-Y", "verify", "-f", self.signers, "-I", principal, "-n", NAMESPACE,
                                     "-s", path], input=data)
        finally:
            shutil.rmtree(folder, True)
        return None if rc == 0 else "signature does not verify: %s" % first_line(err or out)


def record_problem(record):
    if not isinstance(record, dict): return "the record is not an object"
    if record.get("format") != FORMAT: return "format is %r, not %d" % (record.get("format"), FORMAT)
    if record.get("method") != "ssh-sig": return "method is %r, not ssh-sig" % (record.get("method"),)
    for name, rx in (("sha256", SHA_RE), ("principal", PRINCIPAL_RE), ("at", AT_RE), ("signature", SIG_RE)):
        value = record.get(name)
        if not isinstance(value, str) or not rx.fullmatch(value): return "field %s is missing or malformed" % name
    return None


def stdin_line(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline()


def stderr_line(text):
    sys.stderr.write(text + "\n")


def cli_approver():
    """The only approver the command line uses: fixed paths under the account's home (from pwd, not $HOME), the real
    environment, process table, terminal and ssh-add. No flag, variable or file changes any of them."""
    home = pwd.getpwuid(os.getuid()).pw_dir
    return Approver(signers=os.path.join(home, *SIGNERS_PARTS), key=os.path.join(home, *KEY_PARTS), home=home,
                    environ=os.environ, read_proc=libproc_read, start_pid=None,
                    ssh_add=lambda: run_process([SSH_ADD, "-L"], env=dict(os.environ)), read_line=stdin_line,
                    write=stderr_line, run=run_process, interactive=True, refuse_in_tests=True)
