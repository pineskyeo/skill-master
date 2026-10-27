"""The human gate: approve, register and the start of every --live run need a person at a real terminal and an ssh
signature from a key listed in ~/.config/skill-master/approvers.allowed_signers (references/approval-gates.md).

The command line uses cli_approver() only; its paths and probes are fixed. Tests build an Approver with their own
signers file, key, environment, process table, line reader, ssh-add and runner. Python code that imports this module
can do the same, so the gate binds the command line, not the process (Appendix C, R1). ASCII only.
"""
import calendar, collections, ctypes, errno, fnmatch, getpass, glob, hashlib, hmac, json, math, os, pwd, re, secrets
import shutil, signal, stat, struct, subprocess, sys, tempfile, time

NAMESPACE = "skill-master-approval"
FORMAT = 1
SSH_KEYGEN = "/usr/bin/ssh-keygen"
SSH_ADD = "/usr/bin/ssh-add"
SSH_AGENT = "/usr/bin/ssh-agent"
IOREG = "/usr/sbin/ioreg"
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
RULES_CHANGED = "rules changed since approval"
UNSIGNED = "no signature (a record from before signed approvals)"
UNSIGNED_RULES = UNSIGNED + "; " + RULES_CHANGED

COVERAGE_TOOLS = ("claude", "codex", "opencode", "hermes")
WAIVER_PREFIX = "coverage_waiver:"
WAIVER_GATES = tuple(WAIVER_PREFIX + t for t in COVERAGE_TOOLS)
REASON_MAX = 200
REASON_PROMPT = "reason for this coverage waiver (printable ASCII, 1 to %d characters): " % REASON_MAX
PASSPHRASE_PROMPT = "passphrase of the approver key (asked once for this command): "
PROBE_HEADER = "skill-master-probe v1"

MACHINE_WAIVER_DAYS = 90
FUTURE_SKEW = 300
MACHINE_WAIVER_FIELDS = ("format", "method", "machine_id", "tools", "expires", "reason", "principal", "at", "signature")
UUID_RE = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", re.ASCII)

SESSION_ENV = "SKILL_MASTER_APPROVER_SESSION"
SESSION_PREFIX = "skill-master-approver-"
SESSION_BASE = "/tmp"
SESSION_SOCKET = "agent.sock"
SESSION_LIFETIME = 28800
LAUNCHD_GLOB = "/private/tmp/com.apple.launchd.*/Listeners"
SSH_SESSION_VARS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
UNSET_SOCK = ("SSH_AUTH_SOCK is not set, so ssh-agent cannot be asked whether the approver key is loaded; run this from "
              "a Terminal where ssh-add -L works")

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
Prepared = collections.namedtuple("Prepared", "unit sha256 ruleset_sha256 show")
Draft = collections.namedtuple("Draft", "kind gate unit sha256 ruleset_sha256 reason")


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


def reason_problem(reason):
    if not isinstance(reason, str) or not reason.strip(): return "it is empty"
    if len(reason) > REASON_MAX: return "it is longer than %d characters" % REASON_MAX
    if any(not " " <= c <= "~" for c in reason): return "it holds a character that is not printable ASCII"
    if reason != reason.strip(): return "it has leading or trailing spaces"
    return None


def payload(kind, gate, unit, sha256, ruleset_sha256, at, budget_usd=None, reason=None):
    """The canonical bytes a signature covers. Every value is checked, so no two field sets give the same bytes."""
    if kind not in KINDS: raise ValueError("kind %r is not one of %s" % (kind, ", ".join(KINDS)))
    waiver = kind == "approve" and gate in WAIVER_GATES
    if not waiver and not (isinstance(gate, str) and NAME_RE.fullmatch(gate)):
        raise ValueError("gate %r is not valid" % (gate,))
    for name, value, rx in (("unit", unit, NAME_RE), ("sha256", sha256, SHA_RE),
                            ("ruleset_sha256", ruleset_sha256, SHA_RE), ("at", at, AT_RE)):
        if not isinstance(value, str) or not rx.fullmatch(value): raise ValueError("%s %r is not valid" % (name, value))
    lines = ["skill-master-approval v%d" % FORMAT, "kind " + kind, "gate " + gate, "unit " + unit, "sha256 " + sha256,
             "ruleset_sha256 " + ruleset_sha256]
    if kind == "live":
        if isinstance(budget_usd, bool) or not isinstance(budget_usd, (int, float)) or not math.isfinite(budget_usd) \
                or budget_usd <= 0:
            raise ValueError("budget_usd %r is not a finite amount > 0" % (budget_usd,))
        lines.append("budget_usd %r" % float(budget_usd))
    elif budget_usd is not None:
        raise ValueError("only a live payload has a budget")
    if waiver:
        problem = reason_problem(reason)
        if problem: raise ValueError("reason %r is not valid: %s" % (reason, problem))
        lines.append("reason " + reason)
    elif reason is not None:
        raise ValueError("only a coverage waiver payload has a reason")
    lines.append("at " + at)
    return ("\n".join(lines) + "\n").encode("ascii")


def machine_waiver_payload(machine_id, tools, expires, reason, at):
    """The bytes a machine waiver signature covers. Kind machine_waiver, no unit and no ruleset_sha256, so no approval
    payload can equal them and a rule change does not stale the waiver."""
    for name, value, rx in (("machine_id", machine_id, UUID_RE), ("expires", expires, AT_RE), ("at", at, AT_RE)):
        if not isinstance(value, str) or not rx.fullmatch(value): raise ValueError("%s %r is not valid" % (name, value))
    if not (isinstance(tools, list) and tools and tools == [t for t in COVERAGE_TOOLS if t in tools]):
        raise ValueError("tools %r are not distinct names of %s in that order" % (tools, ", ".join(COVERAGE_TOOLS)))
    problem = reason_problem(reason)
    if problem: raise ValueError("reason %r is not valid: %s" % (reason, problem))
    lines = ["skill-master-approval v%d" % FORMAT, "kind machine_waiver", "machine_id " + machine_id,
             "tools " + ",".join(tools), "expires " + expires, "reason " + reason, "at " + at]
    return ("\n".join(lines) + "\n").encode("ascii")


def machine_waiver_sha256(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def iso_seconds(at):
    return calendar.timegm(time.strptime(at, "%Y-%m-%dT%H:%M:%SZ"))


def iso_at(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


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


def sign_with_agent(run, agent, data):
    """Armored signature over data by the approver-session agent: ssh-keygen gets the public key of the key that agent
    holds and SSH_AUTH_SOCK of that agent only. Whether this Mac's ssh-keygen signs this way is UNVERIFIED (premise box
    2 was blocked by the sandbox); approver-session refuses to start unless a probe signature through it verifies."""
    sock, keyline = agent
    folder = tempfile.mkdtemp(prefix="gate-sign-")
    try:
        pub, path = os.path.join(folder, "approver.pub"), os.path.join(folder, "payload")
        with open(pub, "w", encoding="ascii") as f: f.write(keyline + "\n")
        with open(path, "wb") as f: f.write(data)
        rc, out, err = run([SSH_KEYGEN, "-Y", "sign", "-n", NAMESPACE, "-f", pub, path], new_session=True,
                           env=dict(KEYGEN_ENV, SSH_AUTH_SOCK=sock))
        if rc != 0: raise OSError("ssh-keygen -Y sign through the session agent exited %d: %s" % (rc, first_line(err)))
        with open(path + ".sig", encoding="ascii") as f: return f.read()
    finally:
        shutil.rmtree(folder, True)


def listed_keys(out):
    keys = []
    for line in (out or b"").decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 2: keys.append((parts[0], parts[1]))
    return keys


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


class Sitting(object):
    """One presence check and one approver-check for every signature of a command (Approver.open_sitting). The sitting
    owns the typed challenge and the signing route: the approver-session agent, a passphrase typed once, or
    ssh-keygen's own prompt. A record signed through a sitting must match a draft confirmed with its challenge."""
    def __init__(self, approver, what, agent):
        self.approver, self.what, self.agent, self.principal = approver, what, agent, None
        self._code, self._passphrase, self._confirmed = None, None, {}

    def refuse(self, why):
        return Refused(self.what, ["%s: %s" % (self.what, why)])

    def challenge(self):
        self._code = "".join(secrets.choice(CHALLENGE_ALPHABET) for _ in range(CHALLENGE_LENGTH))
        return self._code

    def confirm(self, drafts, typed):
        """Spends the challenge; on a match each draft may be signed once through Approver.sign(..., sitting=self)."""
        code, self._code = self._code, None
        if code is None or not hmac.compare_digest((typed or "").strip().encode("utf-8", "replace"),
                                                   code.encode("ascii")):
            raise self.refuse("challenge mismatch: nothing was signed")
        for d in drafts:
            self._confirmed.setdefault(tuple(d[:5]), []).append(d.reason)

    def take(self, kind, gate, prep):
        reasons = self._confirmed.get((kind, gate, prep.unit, prep.sha256, prep.ruleset_sha256))
        if not reasons:
            raise self.refuse("%s for %s was not confirmed in this sitting or changed since the list was shown; nothing "
                              "was signed" % (gate, prep.unit))
        return reasons.pop(0)

    def tty_problems(self):
        return self.approver.tty_problems()

    def unlock(self):
        """The passphrase typed once unless the session agent signs, then one probe signature over fresh bytes that
        never parse as a payload, so a wrong passphrase or a dead agent stops before any item is signed."""
        ap = self.approver
        if self.agent is None and self._passphrase is None:
            self._passphrase = ap.passphrase if ap.passphrase is not None else ap.read_secret(PASSPHRASE_PROMPT)
        self.seal(("%s\nnonce %s\nat %s\n" % (PROBE_HEADER, secrets.token_hex(16), now_iso())).encode("ascii"))

    def seal(self, data):
        """(signature, principal) of data, verified against the signers file before it is returned."""
        ap = self.approver
        pw = self._passphrase if self._passphrase is not None else ap.passphrase
        if pw is None and self.agent is None and not ap.interactive and ap.read_secret is not None:
            pw = self._passphrase = ap.read_secret(PASSPHRASE_PROMPT)
        try:
            signature = (sign_with_agent(ap.run, self.agent, data) if self.agent is not None else
                         sign_payload(ap.run, ap.key, data, pw, ap.interactive and pw is None))
            principal = ap.find_principal(signature)
            problem = ap.verify(data, signature, principal)
        except (OSError, ValueError, UnicodeDecodeError, subprocess.SubprocessError) as ex:
            raise self.refuse("signing failed: %s" % ex)
        if problem: raise self.refuse("the new signature does not verify: %s" % problem)
        self.principal = self.principal or principal
        return signature, principal


class Approver(object):
    def __init__(self, signers, key, home, environ, read_proc, start_pid, ssh_add, read_line, write, run,
                 passphrase=None, interactive=False, refuse_in_tests=False, read_secret=None, launchd=None,
                 ask_agent=None, lstat=os.lstat):
        self.signers, self.key, self.home, self.environ = signers, key, home, environ
        self.read_proc, self.start_pid, self.ssh_add = read_proc, start_pid, ssh_add
        self.read_line, self.write, self.run = read_line, write, run
        self.passphrase, self.interactive, self.refuse_in_tests = passphrase, interactive, refuse_in_tests
        self.read_secret, self.launchd, self.ask_agent, self.lstat = read_secret, launchd, ask_agent, lstat

    def derive(self, environ):
        """The same approver with another environment (the approver-session subshell's)."""
        a = object.__new__(Approver)
        a.__dict__.update(self.__dict__, environ=environ)
        return a

    def shown(self, path):
        h = self.home.rstrip(os.sep) + os.sep
        return "~" + os.sep + path[len(h):] if path.startswith(h) else path

    def tty_problems(self):
        out = []
        for fd, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
            try: tty = is_tty(fd)
            except (OSError, ValueError): tty = False
            if not tty: out.append("%s is not a TTY" % name)
        return out

    def presence_problems(self):
        out = self.tty_problems()
        found = markers(self.environ)
        if found: out.append("agent environment variables are set: %s" % ", ".join(found))
        return out + ancestry_problems(os.getpid() if self.start_pid is None else self.start_pid, self.read_proc)

    def signer_keys(self):
        with open(self.signers, encoding="utf-8") as f: return parse_signers(f.read())

    def key_problems(self):
        return self.key_check()[0]

    def key_check(self):
        """([problem], agent): approver-check. The signers file and the key exist, the key needs a passphrase, and no
        ssh-agent holds it except a live approver-session agent, which is then returned as (socket, 'type base64')."""
        if self.refuse_in_tests and in_test_process(): return [IN_TESTS], None
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
        problems, agent = self.agent_check(keys)
        return out + problems, agent

    def session_problem(self, sock):
        """None when sock is a live-looking approver-session socket: SSH_AUTH_SOCK is <folder>/agent.sock of the folder
        SKILL_MASTER_APPROVER_SESSION names, the folder is skill-master-approver-* directly under /tmp, a directory (not
        a link) owned by this user with mode 0700, and the socket is a socket owned by this user. Whether the agent is
        alive is asked next, through ssh-add -L."""
        folder = self.environ.get(SESSION_ENV)
        if not folder or sock != os.path.join(folder, SESSION_SOCKET): return "not the approver-session socket"
        real = os.path.join(os.path.realpath(os.path.dirname(folder)), os.path.basename(folder))
        if not os.path.basename(folder).startswith(SESSION_PREFIX) or \
                os.path.dirname(real) != os.path.realpath(SESSION_BASE):
            return "not a skill-master-approver-* folder under %s" % SESSION_BASE
        try:
            d, s = self.lstat(real), self.lstat(os.path.join(real, SESSION_SOCKET))
        except OSError as ex:
            return "cannot read the session folder (%s)" % ex
        uid = os.getuid()
        if not (stat.S_ISDIR(d.st_mode) and d.st_uid == uid and stat.S_IMODE(d.st_mode) == 0o700):
            return "the session folder is not a 0700 directory of this user"
        if not (stat.S_ISSOCK(s.st_mode) and s.st_uid == uid): return "the session socket is not a socket of this user"
        return None

    def launchd_sockets(self):
        """The candidates of LAUNCHD_GLOB that are sockets of this user in folders of this user."""
        uid, out = os.getuid(), []
        for p in sorted(self.launchd()):
            try: s, d = self.lstat(p), self.lstat(os.path.dirname(p))
            except OSError: continue
            if stat.S_ISSOCK(s.st_mode) and s.st_uid == uid and stat.S_ISDIR(d.st_mode) and d.st_uid == uid:
                out.append(p)
        return out

    def ask(self, sock):
        """(rc, out, err) of ssh-add -L for sock through ask_agent (SSH_AUTH_SOCK=sock only); an approver without
        ask_agent asks only its environment's agent through ssh_add, and any other answer fails closed."""
        try:
            if self.ask_agent is not None: return self.ask_agent(sock)
            if sock == self.environ.get("SSH_AUTH_SOCK"): return self.ssh_add()
            return None, b"", b"no way to ask another agent"
        except (OSError, subprocess.SubprocessError) as ex:
            return None, b"", str(ex).encode()

    def agent_check(self, keys):
        """([problem], agent). The key may sit only in a live approver-session agent; every other agent asked must
        answer that it lists no signers key. The launchd agent is asked too whenever SSH_AUTH_SOCK does not name it and
        a resolver is set (always on the command line); it must resolve to exactly one socket of this user. SSH_AUTH_SOCK
        unset fails closed outside an SSH login; in one, the launchd agent is asked instead."""
        sock = self.environ.get("SSH_AUTH_SOCK")
        ssh = any(self.environ.get(k) for k in SSH_SESSION_VARS)
        if not sock and not ssh: return [UNSET_SOCK], None
        session = sock if sock and self.session_problem(sock) is None else None
        asks = [sock] if sock and not session else []
        if not (sock and fnmatch.fnmatchcase(sock, LAUNCHD_GLOB)) and (self.launchd is not None or not sock):
            found = self.launchd_sockets() if self.launchd is not None else []
            if len(found) != 1:
                return ["cannot resolve the launchd ssh-agent socket (%d sockets of this user match %s); cannot confirm "
                        "the approver key is not loaded there" % (len(found), LAUNCHD_GLOB)], None
            asks.append(found[0])
        out = []
        for s in asks:
            rc, listed, err = self.ask(s)
            where = "" if s == sock else " at %s" % s
            if rc == 1 and listed.decode("utf-8", "replace").strip() == NO_IDENTITIES and not (err or b"").strip():
                continue
            if rc == 0:
                hit = sorted(set(listed_keys(listed)) & keys)
                if hit:
                    out.append("the approver key is loaded in ssh-agent%s (ssh-add -L lists %s %s...); remove it with "
                               "ssh-add -d %s" % (where, hit[0][0], hit[0][1][:16], self.shown(self.key)))
                continue
            out.append("ssh-add -L could not ask ssh-agent%s (exit %s: %s); cannot confirm the approver key is not "
                       "loaded" % (where, rc, first_line(err or listed)))
        if out or not session: return out, None
        rc, listed, err = self.ask(session)
        if rc == 1 and listed.decode("utf-8", "replace").strip() == NO_IDENTITIES: return [], None
        if rc != 0:
            return ["ssh-add -L could not ask the approver-session agent at %s (exit %s: %s)"
                    % (session, rc, first_line(err or listed))], None
        hit = [k for k in listed_keys(listed) if k in keys]
        return [], (session, "%s %s" % hit[0]) if hit else None

    def open_sitting(self, what):
        """Presence, then approver-check (which starts ssh-keygen -y and ssh-add -L); the Sitting every signature of
        this command goes through."""
        problems = self.presence_problems()
        if problems:
            raise Refused(what, ["%s needs a human at an interactive terminal (%s)" % (what, p) for p in problems])
        problems, agent = self.key_check()
        if problems: raise Refused(what, ["%s: approver-check: %s" % (what, p) for p in problems])
        return Sitting(self, what, agent)

    def sign(self, kind, gate, prepare, budget_usd=None, sitting=None):
        """Without a sitting: presence, approver-check, prepare(), the summary with every non-printable character
        escaped, the typed challenge, signing and an immediate verify. With a sitting (approve --batch, approve-ui):
        only an approve record whose prepare() matches a draft that sitting confirmed with its challenge, signed through
        the same steps. Nothing is signed unless every earlier step passed; prepare() may raise the caller's own error."""
        what = label(kind, gate)
        own = sitting is None
        if own:
            sitting = self.open_sitting(what)
        elif sitting.approver is not self or kind != "approve":
            raise Refused(what, ["%s: a sitting signs only approve records of its own approver; nothing was signed"
                                 % what])
        prep = prepare()
        shown, reason = list(prep.show), None
        if not own:
            reason = sitting.take(kind, gate, prep)
        elif kind == "approve" and gate in WAIVER_GATES:
            for line in shown: self.write(printable(line))
            shown, reason = [], (self.read_line(REASON_PROMPT) or "").strip()
            problem = reason_problem(reason)
            if problem: raise Refused(what, ["%s: reason refused: %s; nothing was signed" % (what, problem)])
        at = now_iso()
        try:
            data = payload(kind, gate, prep.unit, prep.sha256, prep.ruleset_sha256, at, budget_usd, reason)
        except ValueError as ex:
            raise Refused(what, ["%s: cannot sign: %s" % (what, ex)])
        if own:
            summary = "%s: gate %s, unit %s, sha256 %s" % (what, gate, prep.unit, prep.sha256)
            if budget_usd is not None: summary += ", budget $%.2f" % budget_usd
            if reason is not None: summary += ", reason " + reason
            for line in shown + [summary]: self.write(printable(line))
            code = sitting.challenge()
            sitting.confirm([], self.read_line("type %s to sign (anything else cancels): " % code))
        signature, principal = sitting.seal(data)
        record = {"format": FORMAT, "method": "ssh-sig", "sha256": prep.sha256, "ruleset_sha256": prep.ruleset_sha256,
                  "principal": principal, "at": at, "signature": signature}
        if reason is not None: record["reason"] = reason
        v = self.verdict(record, kind, gate, prep.unit, prep.sha256, prep.ruleset_sha256, budget_usd)
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

    def verdict(self, record, kind, gate, unit, sha256, ruleset_sha256, budget_usd=None):
        """The first rule that matches decides: missing, unsigned, invalid (a malformed record or no signers file),
        stale (the gated files are gone or hash elsewhere), stale (RULES_CHANGED: the record binds no ruleset_sha256 or
        another one than ruleset_sha256, the current bundle hash), invalid (ssh-keygen -Y verify fails), approved. Only
        a signed record that reaches the verify step starts ssh-keygen."""
        if record is None: return Verdict("missing", None, "no record")
        if isinstance(record, dict) and "signature" not in record and "method" not in record:
            bound = record.get("ruleset_sha256") is not None and record.get("ruleset_sha256") == ruleset_sha256
            return Verdict("unsigned", None, UNSIGNED if bound else UNSIGNED_RULES)
        waiver = kind == "approve" and gate in WAIVER_GATES
        problem = record_problem(record, waiver)
        if problem: return Verdict("invalid", None, problem)
        if self.refuse_in_tests and in_test_process(): return Verdict("invalid", None, IN_TESTS)
        if not os.path.isfile(self.signers):
            return Verdict("invalid", None, "approvers file missing: %s" % self.shown(self.signers))
        if not sha256 or record["sha256"] != sha256:
            return Verdict("stale", record["principal"], "the gated files changed since signing" if sha256 else
                           "the gated files are missing")
        if record.get("ruleset_sha256") is None or record["ruleset_sha256"] != ruleset_sha256:
            return Verdict("stale", record["principal"], RULES_CHANGED)
        try:
            data = payload(kind, gate, unit, sha256, record["ruleset_sha256"], record["at"], budget_usd,
                           record["reason"] if waiver else None)
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

    def machine_waiver_verdict(self, record, machine_id, waivable, now):
        """The first rule that matches decides: missing, unsigned, invalid (a malformed record, a tool outside waivable,
        an expiry not within MACHINE_WAIVER_DAYS after signing, a signing time in the future, no signers file),
        other-machine (machine_id is None or another id), invalid (ssh-keygen -Y verify fails), expired, approved."""
        if record is None: return Verdict("missing", None, "no machine waiver")
        if isinstance(record, dict) and "signature" not in record and "method" not in record:
            return Verdict("unsigned", None, UNSIGNED)
        problem = machine_waiver_problem(record, waivable)
        if problem: return Verdict("invalid", None, problem)
        at, expires = iso_seconds(record["at"]), iso_seconds(record["expires"])
        if not at < expires <= at + MACHINE_WAIVER_DAYS * 86400:
            return Verdict("invalid", None, "expires %s is not within %d days after signing" % (record["expires"],
                                                                                              MACHINE_WAIVER_DAYS))
        if at > now + FUTURE_SKEW: return Verdict("invalid", None, "signed in the future (%s)" % record["at"])
        if self.refuse_in_tests and in_test_process(): return Verdict("invalid", None, IN_TESTS)
        if not os.path.isfile(self.signers):
            return Verdict("invalid", None, "approvers file missing: %s" % self.shown(self.signers))
        if machine_id is None or record["machine_id"] != machine_id:
            return Verdict("other-machine", record["principal"], "the waiver names machine %s, this Mac is %s"
                           % (record["machine_id"], machine_id or "unreadable"))
        try:
            problem = self.verify(machine_waiver_payload(record["machine_id"], record["tools"], record["expires"],
                                                         record["reason"], record["at"]),
                                  record["signature"], record["principal"])
        except (OSError, ValueError, subprocess.SubprocessError) as ex:
            problem = str(ex)
        if problem: return Verdict("invalid", None, problem)
        if now >= expires: return Verdict("expired", record["principal"], "expired %s" % record["expires"])
        return Verdict("approved", record["principal"], "")

    def sign_machine_waiver(self, machine_id, tools, waivable):
        """Presence, approver-check, the waiver shown, the typed reason, the typed challenge, signing and an immediate
        verdict. Returns the record; writes nothing."""
        what = "machine-waiver sign"
        sitting = self.open_sitting(what)
        if not tools or len(set(tools)) != len(tools) or any(t not in waivable for t in tools):
            raise sitting.refuse("tools must be distinct names from machine_waivable (%s), got %s; nothing was signed"
                                 % (",".join(waivable), ",".join(tools) or "none"))
        if not (isinstance(machine_id, str) and UUID_RE.fullmatch(machine_id)):
            raise sitting.refuse("cannot read this Mac's IOPlatformUUID; nothing was signed")
        tools = [t for t in COVERAGE_TOOLS if t in tools]
        for line in ("machine waiver: machine_id %s" % machine_id, "machine waiver: tools %s" % ",".join(tools),
                     "machine waiver: expires %d days after signing; binds no unit and no ruleset_sha256"
                     % MACHINE_WAIVER_DAYS):
            self.write(printable(line))
        reason = (self.read_line(REASON_PROMPT) or "").strip()
        problem = reason_problem(reason)
        if problem: raise sitting.refuse("reason refused: %s; nothing was signed" % problem)
        at = now_iso()
        expires = iso_at(iso_seconds(at) + MACHINE_WAIVER_DAYS * 86400)
        data = machine_waiver_payload(machine_id, tools, expires, reason, at)
        self.write(printable("%s: tools %s, machine %s, expires %s, reason %s" % (what, ",".join(tools), machine_id,
                                                                                  expires, reason)))
        code = sitting.challenge()
        sitting.confirm([], self.read_line("type %s to sign (anything else cancels): " % code))
        signature, principal = sitting.seal(data)
        record = {"format": FORMAT, "method": "ssh-sig", "machine_id": machine_id, "tools": tools, "expires": expires,
                  "reason": reason, "principal": principal, "at": at, "signature": signature}
        v = self.machine_waiver_verdict(record, machine_id, waivable, time.time())
        if v.status != "approved": raise sitting.refuse("the new signature does not verify: %s" % v.reason)
        return record


def machine_waiver_problem(record, waivable):
    if not isinstance(record, dict): return "the record is not an object"
    if sorted(record) != sorted(MACHINE_WAIVER_FIELDS):
        return "the fields are not exactly %s" % ", ".join(MACHINE_WAIVER_FIELDS)
    if record["format"] != FORMAT or record["method"] != "ssh-sig": return "format or method is not 1 / ssh-sig"
    for name, rx in (("machine_id", UUID_RE), ("expires", AT_RE), ("principal", PRINCIPAL_RE), ("at", AT_RE),
                     ("signature", SIG_RE)):
        if not isinstance(record[name], str) or not rx.fullmatch(record[name]):
            return "field %s is missing or malformed" % name
    try:
        iso_seconds(record["at"]), iso_seconds(record["expires"])
    except ValueError as ex:
        return "a time is not a calendar time (%s)" % ex
    tools = record["tools"]
    if not (isinstance(tools, list) and tools and tools == [t for t in COVERAGE_TOOLS if t in tools]):
        return "field tools is not a list of distinct tool names in the order %s" % ", ".join(COVERAGE_TOOLS)
    outside = [t for t in tools if t not in waivable]
    if outside: return "it lists %s, which machine_waivable (%s) does not name" % (",".join(outside), ",".join(waivable))
    problem = reason_problem(record["reason"])
    return "field reason is missing or malformed (%s)" % problem if problem else None


def ioreg_machine_id(run=None):
    """This Mac's IOPlatformUUID from ioreg, or None. Whether it is stable and readable without extra rights is
    UNVERIFIED (plan.md:1495)."""
    rc, out, _ = (run or run_process)([IOREG, "-rd1", "-c", "IOPlatformExpertDevice"])
    m = re.search(r'"IOPlatformUUID" = "([^"]+)"', out.decode("utf-8", "replace")) if rc == 0 else None
    return m.group(1) if m and UUID_RE.fullmatch(m.group(1)) else None


def append_log(path, entry):
    """One JSON line appended to approvals.log (mode 0600, the folder 0700); the caller never passes a secret."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="ascii") as f:
        f.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")


def write_private(path, text):
    """path replaced atomically by text, mode 0600, through a temp file in the same folder."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = "%s.%s.tmp" % (path, secrets.token_hex(8))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f: f.write(text)
    os.replace(tmp, path)


class _Hangup(Exception):
    pass


def _hangup(signum, frame):
    raise _Hangup(signum)


def start_ssh_agent(sock, lifetime):
    return subprocess.Popen([SSH_AGENT, "-D", "-t", str(lifetime), "-a", sock], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=KEYGEN_ENV, start_new_session=True)


def spawn_shell(env):
    shell = env.get("SHELL") if env.get("SHELL") in ("/bin/zsh", "/bin/bash", "/bin/sh") else "/bin/zsh"
    return subprocess.call([shell, "-i"], env=env)


def run_session(approver, start_agent=start_ssh_agent, shell=spawn_shell, lifetime=SESSION_LIFETIME, wait=5.0):
    """approver-session: presence and approver-check, a private ssh-agent on <new 0700 folder>/agent.sock with a
    lifetime ceiling, the approver key loaded with ssh-add -t <lifetime> at the terminal, one probe signature through
    that agent (the start refuses unless it verifies), then a subshell with that SSH_AUTH_SOCK, the session marker and
    an [approver] prompt. On exit, hangup or terminate the agent is killed and the folder deleted. Returns the subshell's
    exit status."""
    what = "approver-session"
    if approver.environ.get(SESSION_ENV):
        raise Refused(what, ["%s: already inside an approver session (%s)" % (what, approver.environ[SESSION_ENV])])
    approver.open_sitting(what)
    folder = tempfile.mkdtemp(prefix=SESSION_PREFIX, dir=SESSION_BASE)
    sock = os.path.join(folder, SESSION_SOCKET)
    agent, saved = None, {s: signal.signal(s, _hangup) for s in (signal.SIGHUP, signal.SIGTERM)}
    try:
        agent = start_agent(sock, lifetime)
        deadline = time.time() + wait
        while True:
            try:
                if stat.S_ISSOCK(approver.lstat(sock).st_mode): break
            except OSError:
                pass
            if time.time() > deadline or agent.poll() is not None:
                raise Refused(what, ["%s: the private ssh-agent did not start on %s" % (what, sock)])
            time.sleep(0.05)
        env = dict(approver.environ, SSH_AUTH_SOCK=sock, PS1="[approver] %~ %# ", PROMPT="[approver] %~ %# ",
                   **{SESSION_ENV: folder})
        rc, _, err = approver.run([SSH_ADD, "-t", str(lifetime), approver.key], new_session=False, timeout=600,
                                  env=dict(KEYGEN_ENV, SSH_AUTH_SOCK=sock))
        if rc != 0: raise Refused(what, ["%s: ssh-add could not load the approver key (exit %s: %s)"
                                         % (what, rc, first_line(err))])
        sitting = approver.derive(env).open_sitting(what)
        if sitting.agent is None:
            raise Refused(what, ["%s: the session agent does not hold a key of the approvers file" % what])
        sitting.unlock()
        approver.write("[approver] session agent %s holds the approver key for %d hours; every signing command still asks"
                       " its challenge. Do not run ssh, git or an agent in this shell: they can reach this agent."
                       % (sock, lifetime // 3600))
        return shell(env)
    except _Hangup:
        return 1
    finally:
        if agent is not None and agent.poll() is None:
            agent.kill()
            agent.wait(timeout=10)
        shutil.rmtree(folder, True)
        for s, h in saved.items(): signal.signal(s, h)
        approver.write("approver session ended: the agent is stopped and %s is deleted" % folder)


def record_problem(record, waiver=False):
    if not isinstance(record, dict): return "the record is not an object"
    if record.get("format") != FORMAT: return "format is %r, not %d" % (record.get("format"), FORMAT)
    if record.get("method") != "ssh-sig": return "method is %r, not ssh-sig" % (record.get("method"),)
    fields = (("sha256", SHA_RE), ("principal", PRINCIPAL_RE), ("at", AT_RE), ("signature", SIG_RE))
    if "ruleset_sha256" in record: fields += (("ruleset_sha256", SHA_RE),)
    for name, rx in fields:
        value = record.get(name)
        if not isinstance(value, str) or not rx.fullmatch(value): return "field %s is missing or malformed" % name
    if not waiver: return "field reason is only valid on a coverage waiver record" if "reason" in record else None
    problem = reason_problem(record.get("reason"))
    return "field reason is missing or malformed (%s)" % problem if problem else None


def stdin_line(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline()


def stderr_line(text):
    sys.stderr.write(text + "\n")


def ask_agent_at(sock):
    return run_process([SSH_ADD, "-L"], env=dict(KEYGEN_ENV, SSH_AUTH_SOCK=sock))


def account_home():
    return pwd.getpwuid(os.getuid()).pw_dir


def cli_approver():
    """The only approver the command line uses: fixed paths under the account's home (from pwd, not $HOME), the real
    environment, process table, terminal, ssh-add and launchd agent glob. No flag, variable or file changes any of
    them."""
    home = account_home()
    return Approver(signers=os.path.join(home, *SIGNERS_PARTS), key=os.path.join(home, *KEY_PARTS), home=home,
                    environ=os.environ, read_proc=libproc_read, start_pid=None,
                    ssh_add=lambda: run_process([SSH_ADD, "-L"], env=dict(os.environ)), read_line=stdin_line,
                    write=stderr_line, run=run_process, interactive=True, refuse_in_tests=True,
                    read_secret=getpass.getpass, launchd=lambda: glob.glob(LAUNCHD_GLOB), ask_agent=ask_agent_at)
