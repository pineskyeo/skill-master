"""Approvals: approve, register, the start of every --live run, the coverage waivers and the machine waiver write an
unsigned recorded record (method recorded, references/approval-gates.md). A record binds kind, gate, unit, sha256 and
ruleset_sha256, plus budget_usd for --live and the reason for a coverage waiver, and counts only for those values. No
key, ssh-agent, passphrase, challenge or TTY is needed (unit N1).

Records signed before N1 (method ssh-sig) stay readable: they verify with ssh-keygen against
~/.config/skill-master/approvers.allowed_signers and read invalid without it. A legacy record with neither signature
nor method reads unsigned. The command line uses cli_approver() only; tests build an Approver with their own signers
file, runner, line reader, writer and recorded_by. ASCII only.
"""
import calendar, collections, hashlib, json, math, os, pwd, re, secrets, shutil, subprocess, sys, tempfile, time

NAMESPACE = "skill-master-approval"
FORMAT = 1
RECORDED = "recorded"
SSH_KEYGEN = "/usr/bin/ssh-keygen"
IOREG = "/usr/sbin/ioreg"
KEYGEN_ENV = {"PATH": "/usr/bin:/bin"}
SIGNERS_PARTS = (".config", "skill-master", "approvers.allowed_signers")
KINDS = ("approve", "register", "live")
STATUSES = ("approved", "unsigned", "invalid", "stale", "missing")
TEST_MODULES = ("unittest", "pytest", "_pytest")
IN_TESTS = "refusing to use the real approver files inside a test process (unittest is loaded)"
RULES_CHANGED = "rules changed since approval"
UNSIGNED = "no signature (a record from before signed approvals)"
UNSIGNED_RULES = UNSIGNED + "; " + RULES_CHANGED

COVERAGE_TOOLS = ("claude", "opencode", "hermes")
WAIVER_PREFIX = "coverage_waiver:"
WAIVER_GATES = tuple(WAIVER_PREFIX + t for t in COVERAGE_TOOLS)
REASON_MAX = 200
REASON_PROMPT = "reason for this coverage waiver (printable ASCII, 1 to %d characters): " % REASON_MAX

MACHINE_WAIVER_DAYS = 90
FUTURE_SKEW = 300
MACHINE_WAIVER_FIELDS = ("format", "method", "machine_id", "tools", "expires", "reason", "principal", "at", "signature")
RECORDED_WAIVER_FIELDS = ("format", "method", "machine_id", "tools", "expires", "reason", "recorded_by", "at")
RECORDED_FIELDS = ("format", "method", "kind", "gate", "unit", "sha256", "ruleset_sha256", "recorded_by", "at")
UUID_RE = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", re.ASCII)

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)
PRINCIPAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}", re.ASCII)
SHA_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", re.ASCII)
SIG_RE = re.compile(r"-----BEGIN SSH SIGNATURE-----\n[A-Za-z0-9+/=\n]+-----END SSH SIGNATURE-----\n?", re.ASCII)

Verdict = collections.namedtuple("Verdict", "status principal reason")
Prepared = collections.namedtuple("Prepared", "unit sha256 ruleset_sha256 show")


class Refused(Exception):
    """The approval refused; reasons holds one line per failing check of a single step."""
    def __init__(self, what, reasons):
        Exception.__init__(self, "\n".join(reasons))
        self.what, self.reasons = what, list(reasons)


class Grant(object):
    """What Approver.sign returns after the new record read back approved: the bound fields and the stored record."""
    def __init__(self, kind, gate, unit, sha256, record):
        self.kind, self.gate, self.unit, self.sha256, self.record = kind, gate, unit, sha256, record


def label(kind, gate):
    return "%s --live" % gate if kind == "live" else kind


def reason_problem(reason):
    if not isinstance(reason, str) or not reason.strip(): return "it is empty"
    if len(reason) > REASON_MAX: return "it is longer than %d characters" % REASON_MAX
    if any(not " " <= c <= "~" for c in reason): return "it holds a character that is not printable ASCII"
    if reason != reason.strip(): return "it has leading or trailing spaces"
    return None


def payload(kind, gate, unit, sha256, ruleset_sha256, at, budget_usd=None, reason=None):
    """The canonical bytes a signature from before N1 covers. Every value is checked, so no two field sets give the same
    bytes; recorded_problem applies the same checks to a recorded record."""
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
    """The bytes a machine waiver signature from before N1 covers. Kind machine_waiver, no unit and no ruleset_sha256,
    so no approval payload can equal them and a rule change does not stale the waiver."""
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


def run_process(argv, input=None, new_session=True, timeout=30, env=None):
    p = subprocess.run(argv, input=input, stdin=None if input is not None else subprocess.DEVNULL, capture_output=True,
                       timeout=timeout, env=KEYGEN_ENV if env is None else env, start_new_session=new_session)
    return p.returncode, p.stdout, p.stderr


def printable(text):
    """text with every character that is not printable (ESC, CR, other controls, format marks) written as its Python
    escape, so a file name or path cannot clear or redraw the terminal."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in text)


def first_line(data):
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else (data or "")
    return next((x.strip() for x in text.splitlines() if x.strip()), "no output")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def in_test_process():
    return any(m in sys.modules for m in TEST_MODULES)


def os_user():
    return pwd.getpwuid(os.getuid()).pw_name


def decided_by(record):
    """Who made the decision a valid record holds: recorded_by of a recorded record, the principal of a signed one."""
    return record.get("recorded_by") if record.get("method") == RECORDED else record.get("principal")


class Approver(object):
    def __init__(self, signers, home, run, read_line, write, recorded_by=None, refuse_in_tests=False):
        self.signers, self.home, self.run = signers, home, run
        self.read_line, self.write = read_line, write
        self.recorded_by = recorded_by or os_user()
        self.refuse_in_tests = refuse_in_tests

    def shown(self, path):
        h = self.home.rstrip(os.sep) + os.sep
        return "~" + os.sep + path[len(h):] if path.startswith(h) else path

    def sign(self, kind, gate, prepare, budget_usd=None, reason=None):
        """prepare(), its lines with every non-printable character escaped, the coverage waiver reason (reason, else one
        line from read_line; a pipe is fine), the summary, then the recorded record, read back with verdict before it is
        returned. Nothing is recorded unless every step passed; prepare() may raise the caller's own error."""
        what = label(kind, gate)
        def refuse(why):
            return Refused(what, ["%s: %s; nothing was recorded" % (what, why)])
        prep = prepare()
        for line in prep.show: self.write(printable(line))
        if kind == "approve" and gate in WAIVER_GATES:
            if reason is None: reason = (self.read_line(REASON_PROMPT) or "").strip()
            problem = reason_problem(reason)
            if problem: raise refuse("reason refused: %s" % problem)
        elif reason is not None:
            raise refuse("only a coverage waiver takes a reason")
        record = {"format": FORMAT, "method": RECORDED, "kind": kind, "gate": gate, "unit": prep.unit,
                  "sha256": prep.sha256, "ruleset_sha256": prep.ruleset_sha256, "recorded_by": self.recorded_by,
                  "at": now_iso()}
        if budget_usd is not None: record["budget_usd"] = budget_usd
        if reason is not None: record["reason"] = reason
        problem = recorded_problem(record)
        if problem: raise refuse("cannot record: %s" % problem)
        summary = "%s: gate %s, unit %s, sha256 %s" % (what, gate, prep.unit, prep.sha256)
        if budget_usd is not None: summary += ", budget $%.2f" % budget_usd
        if reason is not None: summary += ", reason " + reason
        self.write(printable(summary + "; recorded by %s, unsigned" % self.recorded_by))
        v = self.verdict(record, kind, gate, prep.unit, prep.sha256, prep.ruleset_sha256, budget_usd)
        if v.status != "approved": raise refuse("the new record does not verify: %s" % v.reason)
        return Grant(kind, gate, prep.unit, prep.sha256, record)

    def verdict(self, record, kind, gate, unit, sha256, ruleset_sha256, budget_usd=None):
        """The first rule that matches decides: missing; unsigned (a legacy record with neither signature nor method);
        invalid (a malformed record; a recorded record that names another kind, gate, unit or budget than asked; a
        signed record without the signers file); stale (the gated files are gone or hash elsewhere); stale
        (RULES_CHANGED: no ruleset_sha256 or another one than the current bundle hash); invalid (ssh-keygen -Y verify
        fails, signed records only); approved by recorded_by or the signing principal. A recorded record starts no
        process and reads no file."""
        if record is None: return Verdict("missing", None, "no record")
        if isinstance(record, dict) and "signature" not in record and "method" not in record:
            bound = record.get("ruleset_sha256") is not None and record.get("ruleset_sha256") == ruleset_sha256
            return Verdict("unsigned", None, UNSIGNED if bound else UNSIGNED_RULES)
        recorded = isinstance(record, dict) and record.get("method") == RECORDED
        waiver = kind == "approve" and gate in WAIVER_GATES
        problem = recorded_problem(record) if recorded else record_problem(record, waiver)
        if problem: return Verdict("invalid", None, problem)
        if recorded:
            for name, value in (("kind", kind), ("gate", gate), ("unit", unit), ("budget_usd", budget_usd)):
                if record.get(name) != value:
                    return Verdict("invalid", None, "the record names %s %r, not %r" % (name, record.get(name), value))
        else:
            if self.refuse_in_tests and in_test_process(): return Verdict("invalid", None, IN_TESTS)
            if not os.path.isfile(self.signers):
                return Verdict("invalid", None, "approvers file missing: %s" % self.shown(self.signers))
        who = decided_by(record)
        if not sha256 or record["sha256"] != sha256:
            return Verdict("stale", who, "the gated files changed since signing" if sha256 else
                           "the gated files are missing")
        if record.get("ruleset_sha256") is None or record["ruleset_sha256"] != ruleset_sha256:
            return Verdict("stale", who, RULES_CHANGED)
        if not recorded:
            try:
                data = payload(kind, gate, unit, sha256, record["ruleset_sha256"], record["at"], budget_usd,
                               record["reason"] if waiver else None)
                problem = self.verify(data, record["signature"], record["principal"])
            except (OSError, ValueError, subprocess.SubprocessError) as ex:
                problem = str(ex)
            if problem: return Verdict("invalid", None, problem)
        return Verdict("approved", who, "")

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
        """The first rule that matches decides: missing, unsigned (neither signature nor method), invalid (a malformed
        record, a tool outside waivable, an expiry not within MACHINE_WAIVER_DAYS after `at`, an `at` in the future, a
        signed record without the signers file), other-machine (machine_id is None or another id), invalid
        (ssh-keygen -Y verify fails, signed records only), expired, approved."""
        if record is None: return Verdict("missing", None, "no machine waiver")
        if isinstance(record, dict) and "signature" not in record and "method" not in record:
            return Verdict("unsigned", None, UNSIGNED)
        problem = machine_waiver_problem(record, waivable)
        if problem: return Verdict("invalid", None, problem)
        at, expires = iso_seconds(record["at"]), iso_seconds(record["expires"])
        if not at < expires <= at + MACHINE_WAIVER_DAYS * 86400:
            return Verdict("invalid", None, "expires %s is not within %d days after %s" % (record["expires"],
                                                                                         MACHINE_WAIVER_DAYS, record["at"]))
        if at > now + FUTURE_SKEW: return Verdict("invalid", None, "dated in the future (%s)" % record["at"])
        signed = record["method"] != RECORDED
        if signed and self.refuse_in_tests and in_test_process(): return Verdict("invalid", None, IN_TESTS)
        if signed and not os.path.isfile(self.signers):
            return Verdict("invalid", None, "approvers file missing: %s" % self.shown(self.signers))
        who = decided_by(record)
        if machine_id is None or record["machine_id"] != machine_id:
            return Verdict("other-machine", who, "the waiver names machine %s, this Mac is %s"
                           % (record["machine_id"], machine_id or "unreadable"))
        if signed:
            try:
                problem = self.verify(machine_waiver_payload(record["machine_id"], record["tools"], record["expires"],
                                                             record["reason"], record["at"]),
                                      record["signature"], record["principal"])
            except (OSError, ValueError, subprocess.SubprocessError) as ex:
                problem = str(ex)
            if problem: return Verdict("invalid", None, problem)
        if now >= expires: return Verdict("expired", who, "expired %s" % record["expires"])
        return Verdict("approved", who, "")

    def sign_machine_waiver(self, machine_id, tools, waivable, reason=None):
        """The waiver shown, the reason (reason, else one line from read_line), then the recorded record, read back with
        machine_waiver_verdict. Returns the record; writes nothing."""
        what = "machine-waiver sign"
        def refuse(why):
            return Refused(what, ["%s: %s; nothing was recorded" % (what, why)])
        if not tools or len(set(tools)) != len(tools) or any(t not in waivable for t in tools):
            raise refuse("tools must be distinct names from machine_waivable (%s), got %s"
                         % (",".join(waivable), ",".join(tools) or "none"))
        if not (isinstance(machine_id, str) and UUID_RE.fullmatch(machine_id)):
            raise refuse("cannot read this Mac's IOPlatformUUID")
        tools = [t for t in COVERAGE_TOOLS if t in tools]
        for line in ("machine waiver: machine_id %s" % machine_id, "machine waiver: tools %s" % ",".join(tools),
                     "machine waiver: expires %d days after it is recorded; binds no unit and no ruleset_sha256"
                     % MACHINE_WAIVER_DAYS):
            self.write(printable(line))
        if reason is None: reason = (self.read_line(REASON_PROMPT) or "").strip()
        problem = reason_problem(reason)
        if problem: raise refuse("reason refused: %s" % problem)
        at = now_iso()
        expires = iso_at(iso_seconds(at) + MACHINE_WAIVER_DAYS * 86400)
        record = {"format": FORMAT, "method": RECORDED, "machine_id": machine_id, "tools": tools, "expires": expires,
                  "reason": reason, "recorded_by": self.recorded_by, "at": at}
        self.write(printable("%s: tools %s, machine %s, expires %s, reason %s; recorded by %s, unsigned"
                             % (what, ",".join(tools), machine_id, expires, reason, self.recorded_by)))
        v = self.machine_waiver_verdict(record, machine_id, waivable, time.time())
        if v.status != "approved": raise refuse("the new record does not verify: %s" % v.reason)
        return record


def machine_waiver_problem(record, waivable):
    if not isinstance(record, dict): return "the record is not an object"
    recorded = record.get("method") == RECORDED
    fields = RECORDED_WAIVER_FIELDS if recorded else MACHINE_WAIVER_FIELDS
    if sorted(record) != sorted(fields): return "the fields are not exactly %s" % ", ".join(fields)
    if record["format"] != FORMAT or record["method"] not in ("ssh-sig", RECORDED):
        return "format or method is not 1 / ssh-sig or recorded"
    names = (("recorded_by", PRINCIPAL_RE),) if recorded else (("principal", PRINCIPAL_RE), ("signature", SIG_RE))
    for name, rx in (("machine_id", UUID_RE), ("expires", AT_RE), ("at", AT_RE)) + names:
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


def record_problem(record, waiver=False):
    """The field checks of a signed record from before N1 (method ssh-sig)."""
    if not isinstance(record, dict): return "the record is not an object"
    if record.get("format") != FORMAT: return "format is %r, not %d" % (record.get("format"), FORMAT)
    if record.get("method") != "ssh-sig": return "method is %r, not ssh-sig or recorded" % (record.get("method"),)
    fields = (("sha256", SHA_RE), ("principal", PRINCIPAL_RE), ("at", AT_RE), ("signature", SIG_RE))
    if "ruleset_sha256" in record: fields += (("ruleset_sha256", SHA_RE),)
    for name, rx in fields:
        value = record.get(name)
        if not isinstance(value, str) or not rx.fullmatch(value): return "field %s is missing or malformed" % name
    if not waiver: return "field reason is only valid on a coverage waiver record" if "reason" in record else None
    problem = reason_problem(record.get("reason"))
    return "field reason is missing or malformed (%s)" % problem if problem else None


def recorded_problem(record):
    """None for a well-formed recorded record: exactly RECORDED_FIELDS, plus budget_usd on a live record and reason on a
    coverage waiver, each value valid by the checks payload applies to a signed record's fields."""
    if not isinstance(record, dict): return "the record is not an object"
    if record.get("method") != RECORDED: return "method is %r, not %s" % (record.get("method"), RECORDED)
    if set(record) - set(RECORDED_FIELDS) - {"budget_usd", "reason"} or not set(RECORDED_FIELDS) <= set(record):
        return "the fields are not %s, plus budget_usd (live) or reason (coverage waiver)" % ", ".join(RECORDED_FIELDS)
    if record["format"] != FORMAT: return "format is %r, not %d" % (record["format"], FORMAT)
    if not isinstance(record["recorded_by"], str) or not PRINCIPAL_RE.fullmatch(record["recorded_by"]):
        return "field recorded_by is missing or malformed"
    try:
        payload(record["kind"], record["gate"], record["unit"], record["sha256"], record["ruleset_sha256"], record["at"],
                record.get("budget_usd"), record.get("reason"))
    except (ValueError, TypeError, OverflowError) as ex:
        return str(ex)
    return None


def stdin_line(prompt):
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline()


def stderr_line(text):
    sys.stderr.write(text + "\n")


def account_home():
    return pwd.getpwuid(os.getuid()).pw_dir


def cli_approver():
    """The only approver the command line uses: the signers file that records signed before N1 verify against, at a
    fixed path under the account's home (from pwd, not $HOME), stdin for a coverage waiver reason and stderr for the
    lines shown. No flag, variable or file changes any of them."""
    home = account_home()
    return Approver(signers=os.path.join(home, *SIGNERS_PARTS), home=home, run=run_process, read_line=stdin_line,
                    write=stderr_line, refuse_in_tests=True)
