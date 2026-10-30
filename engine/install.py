"""Unit installs (unit B6, plan.md:1367-1369): the install policy, destination resolution and refusal, the collision and
same-name checks, the backup, the all-checks-then-write apply, the dry-run text and the manifest hash. Standard library
only; no import of eva or skill_eval (skill_eval passes in the sources, the homes and the clock).

A plan is built with every check first; a refusal raises InstallError and writes nothing (no folder, no backup)."""
import collections, difflib, hashlib, json, os, pwd, re, shutil, stat, tempfile, unicodedata
from engine.primitives.script_denylist import unit_tree

TOOLS = ("claude", "codex", "opencode", "hermes")
KINDS = ("agent", "skill")
USER_AGENT_PATH = ".claude/agents/{name}.md"
FLOOR_ROOTS = (".claude", ".codex", ".config/opencode", ".hermes", ".agents", "Library/Keychains")
FLOOR_FILES = (".claude/settings.json", ".claude/settings.local.json", ".claude/hooks", ".codex/config.toml",
               ".codex/auth.json")
UNVERIFIED = "UNVERIFIED"
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
HERMES_NOTE = "hermes: config.yaml UNVERIFIED, not written (gates.md G4)"
NAME_FILES = (".md", ".toml")

Policy = collections.namedtuple("Policy", "backup_dir forbidden_roots forbidden_files destinations user_scope tool_pins")
Entry = collections.namedtuple("Entry", "tool rel base_rel source data mode dest action old unverified")
Plan = collections.namedtuple("Plan", "unit kind scope base entries")


class InstallError(Exception):
    def __init__(self, problems):
        Exception.__init__(self, "; ".join(problems))
        self.problems = list(problems)


def _rel_ok(p):
    return isinstance(p, str) and p and not p.startswith(("/", "~")) and "\\" not in p and \
        all(x not in ("", ".", "..") for x in p.rstrip("/").split("/"))


def _place(where, conf, kind):
    if not (isinstance(conf, dict) and _rel_ok(conf.get("path")) and "{name}" in conf["path"]
            and isinstance(conf.get("source"), str) and conf["source"].strip()
            and (conf.get("verified") == UNVERIFIED or isinstance(conf.get("verified"), str)
                 and DATE_RE.fullmatch(conf["verified"]))):
        return ["%s: needs a relative path with {name}, a source and verified (a date or %s)" % (where, UNVERIFIED)]
    if (kind == "skill") != conf["path"].endswith("/"):
        return ["%s: a skill destination is a folder ending in /, an agent destination a file" % where]
    return []


def load_policy(path):
    """The Policy of policy/install.json; InstallError names every problem, including a root or file of the code floor
    (FLOOR_ROOTS, FLOOR_FILES) the file does not list and a user-scope entry other than the Claude agent (Q12)."""
    try:
        with open(path, encoding="utf-8") as f: doc = json.load(f)
    except (OSError, ValueError) as ex:
        raise InstallError(["policy %s is not readable JSON (%s)" % (path, ex)])
    if not isinstance(doc, dict) or doc.get("format") != 1:
        raise InstallError(["policy %s: format must be 1" % path])
    out = []
    roots, files = doc.get("forbidden_roots"), doc.get("forbidden_files")
    for key, items, floor in (("forbidden_roots", roots, FLOOR_ROOTS), ("forbidden_files", files, FLOOR_FILES)):
        if not (isinstance(items, list) and all(_rel_ok(x) for x in items)):
            out.append("%s must be a list of relative paths" % key)
            continue
        out += ["%s lacks %s, which plan.md:1369 names (the code floor of engine/install.py)" % (key, x)
                for x in floor if x not in items]
    backup = doc.get("backup_dir")
    if not (_rel_ok(backup) and "/" not in backup and backup.startswith(".")):
        out.append("backup_dir must be one hidden folder name")
    dests = doc.get("destinations")
    if not (isinstance(dests, dict) and sorted(dests) == sorted(KINDS)
            and all(isinstance(dests[k], dict) and sorted(dests[k]) == sorted(TOOLS) for k in KINDS)):
        out.append("destinations must name exactly %s, each with exactly %s" % (", ".join(KINDS), ", ".join(TOOLS)))
    else:
        out += [p for k in KINDS for t in TOOLS for p in _place("destinations.%s.%s" % (k, t), dests[k][t], k)]
    user = doc.get("user_scope")
    if not (isinstance(user, dict) and list(user) == ["agent"] and isinstance(user["agent"], dict)
            and list(user["agent"]) == ["claude"]):
        out.append("user_scope must hold only agent.claude (Appendix E Q12: user scope is Claude agents only)")
    else:
        out += _place("user_scope.agent.claude", user["agent"]["claude"], "agent")
        if user["agent"]["claude"].get("path") != USER_AGENT_PATH:
            out.append("user_scope.agent.claude.path must be %s (plan.md:1368)" % USER_AGENT_PATH)
    pins = doc.get("tool_pins")
    if not (isinstance(pins, dict) and sorted(pins) == sorted(TOOLS) and all(
            p is None or isinstance(p, dict) and sorted(p) == ["source", "verified", "version"]
            and all(isinstance(p[k], str) and p[k] for k in p) for p in pins.values())):
        out.append("tool_pins must name exactly %s, each null or {version, verified, source}" % ", ".join(TOOLS))
    if out: raise InstallError(["policy %s: %s" % (path, x) for x in out])
    return Policy(backup, tuple(roots), tuple(files), dests, user, pins)


def homes(environ=None):
    """Both homes count, so a temp $HOME cannot hide the account's real user-scope folders."""
    env = os.environ if environ is None else environ
    out = []
    for h in (env.get("HOME") or os.path.expanduser("~"), pwd.getpwuid(os.getuid()).pw_dir):
        r = os.path.realpath(h)
        if r not in out: out.append(r)
    return tuple(out)


def _stat(path):
    try:
        return os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as ex:
        raise InstallError(["cannot read %s (%s); nothing was written" % (path, ex)])


def _fold(parts):
    return [unicodedata.normalize("NFC", x).casefold() for x in parts]


def _anchor(path):
    """(stat of the deepest existing ancestor of path, itself included, [the components below it])."""
    rest = []
    while True:
        st = _stat(path)
        if st is not None: return st, rest
        head, tail = os.path.split(path)
        if head == path: raise InstallError(["%s has no existing ancestor; nothing was written" % path])
        if tail: rest.insert(0, tail)
        path = head


def under(path, root):
    """path is root or lies under it, by file identity: a case-insensitive volume and the /System/Volumes/Data
    firmlink spell one folder many ways that realpath keeps. An existing ancestor of path (itself included) must be the
    deepest existing ancestor of root, and the components below it must start with root's remaining components,
    compared without case (a case-sensitive volume errs toward under)."""
    root_st, root_rest = _anchor(root)
    rest = []
    while True:
        st = _stat(path)
        if st is not None and os.path.samestat(st, root_st):
            return _fold(rest[:len(root_rest)]) == _fold(root_rest)
        head, tail = os.path.split(path)
        if head == path: return False
        if tail: rest.insert(0, tail)
        path = head


def same(a, b):
    return under(a, b) and under(b, a)


def forbidden(policy, home_list, rels=None):
    """[(path, label)] of every forbidden root and file (or of rels) under each home, lexical and resolved."""
    out = []
    for h in home_list:
        for rel in policy.forbidden_roots + policy.forbidden_files if rels is None else rels:
            lex = os.path.join(h, *rel.split("/"))
            for p in (lex, os.path.realpath(lex)):
                if (p, lex) not in out: out.append((p, lex))
    return out


def forbidden_hit(path, bad):
    return next((label for p, label in bad if under(path, p)), None)


def target_problems(target_real, home_list, policy):
    """Why a project --target (a realpath) is refused: a home, an ancestor of a home, or under a forbidden root."""
    out = []
    for h in home_list:
        if same(target_real, h): out.append("--target %s is the home folder %s" % (target_real, h))
        elif under(h, target_real): out.append("--target %s is an ancestor of the home folder %s" % (target_real, h))
    hit = forbidden_hit(target_real, forbidden(policy, home_list))
    if hit: out.append("--target %s lies under the forbidden %s" % (target_real, hit))
    return out


def skill_files(skill_dir):
    """[(relative POSIX path, absolute path)] of the skill folder's unit_tree files, the ones skill_tree_hash binds,
    sorted; InstallError when the folder holds a symbolic link or a special file anywhere, ignored paths included."""
    root, out, bad = os.path.realpath(skill_dir), [], []
    listed = set(unit_tree(root).files)
    for dp, dn, fn in os.walk(root):
        for n in sorted(dn + fn):
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            mode = os.lstat(p).st_mode
            if stat.S_ISLNK(mode): bad.append("%s is a symbolic link" % rel)
            elif n in fn and not stat.S_ISREG(mode): bad.append("%s is not a regular file" % rel)
            elif n in fn and rel in listed: out.append((rel, p))
    if bad: raise InstallError(["skill folder %s: %s; a skill installs regular files only" % (skill_dir, x) for x in bad])
    return sorted(out)


def declared_name(path):
    """The agent name a .md frontmatter (name:) or a .toml file (top-level name =) declares, else None."""
    try:
        with open(path, encoding="utf-8") as f: lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if path.endswith(".md"):
        if not lines or lines[0].strip() != "---": return None
        for ln in lines[1:]:
            if ln.strip() == "---": return None
            m = re.match(r"name:\s*(.*?)\s*$", ln)
            if m: return re.split(r"\s#", m.group(1), 1)[0].strip().strip("'\"")
        return None
    for ln in lines:
        if ln.lstrip().startswith("["): return None
        m = re.match(r"\s*name\s*=\s*(['\"])(.*?)\1\s*(#.*)?$", ln)
        if m: return m.group(2)
    return None


def _read(path):
    with open(path, "rb") as f: return f.read()


def build(unit, kind, scope, base, items, policy, home_list, force, check_dest):
    """The Plan of items [(tool, source path, destination relative to base or absolute, unverified note or None)].
    check_dest(rel, lexical, real) -> [problems] is the scope's destination rule. Every refusal is collected first."""
    bad, problems, entries = forbidden(policy, home_list), [], []
    for tool, src, rel, unverified in items:
        lexical = rel if os.path.isabs(rel) else os.path.join(base, *rel.split("/"))
        if os.path.islink(lexical):
            problems.append("%s: the destination is a symbolic link; refusing even with --force" % rel)
            continue
        real = os.path.realpath(lexical)
        found = check_dest(rel, lexical, real)
        parent = os.path.dirname(real)
        while not os.path.lexists(parent): parent = os.path.dirname(parent)
        if not found and os.path.lexists(real) and not os.path.isfile(real):
            found = ["%s: exists and is not a regular file" % rel]
        elif not found and not os.path.isdir(parent):
            found = ["%s: %s is not a folder" % (rel, parent)]
        if found:
            problems += found
            continue
        data, mode = _read(src), stat.S_IMODE(os.stat(src).st_mode)
        old = _read(real) if os.path.isfile(real) else None
        action = "create" if old is None else "unchanged" if old == data else "overwrite"
        if action == "overwrite" and not force:
            problems.append("collision: %s exists with other bytes; pass --force to back it up and overwrite it" % rel)
        entries.append(Entry(tool, rel, os.path.relpath(real, base).replace(os.sep, "/"), src, data, mode, real, action,
                             old, unverified))
    if kind == "agent":
        problems += name_collisions(unit, entries, base)
    if any(e.action == "overwrite" for e in entries):
        root = os.path.join(base, policy.backup_dir)
        if os.path.islink(root) or not under(os.path.realpath(root), base) or forbidden_hit(os.path.realpath(root), bad):
            problems.append("the backup folder %s is a link or leaves %s" % (root, base))
    if problems: raise InstallError(problems + ["nothing was written"])
    return Plan(unit, kind, scope, base, entries)


def name_collisions(name, entries, base):
    out = []
    for e in entries:
        if not e.dest.endswith(NAME_FILES): continue
        folder = os.path.dirname(e.dest)
        for n in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            p = os.path.join(folder, n)
            if p != e.dest and n.endswith(NAME_FILES) and os.path.isfile(p) and declared_name(p) == name:
                out.append("name collision: %s declares the same agent name %s as %s; refusing even with --force"
                           % (os.path.relpath(p, base).replace(os.sep, "/"), name, e.rel))
    return out


def project_check(target_real, policy, home_list):
    bad = forbidden(policy, home_list)
    def check(rel, lexical, real):
        if not under(real, target_real) or same(real, target_real):
            return ["%s: resolves to %s, outside the target %s" % (rel, real, target_real)]
        hit = forbidden_hit(real, bad)
        return ["%s: resolves to %s, under the forbidden %s" % (rel, real, hit)] if hit else []
    return check


def user_check(home_real, policy, home_list):
    files = forbidden(policy, home_list, policy.forbidden_files)
    def check(rel, lexical, real):
        out = ["%s is a symbolic link; user scope writes only the real %s"
               % (p, os.path.join(home_real, ".claude", "agents")) for p in (os.path.join(home_real, ".claude"),
                                                                            os.path.join(home_real, ".claude", "agents"))
               if os.path.islink(p)]
        if not out and real != lexical: out.append("%s resolves to %s" % (lexical, real))
        hit = forbidden_hit(real, files)
        if hit: out.append("%s: resolves to %s, under the forbidden %s" % (rel, real, hit))
        return out
    return check


def counts(plan):
    return tuple(sum(1 for e in plan.entries if e.action == a) for a in ("create", "overwrite", "unchanged"))


def summary(plan, name, dry_run):
    c, o, u = counts(plan)
    return "%sinstall %s (%s) -> %s: %d create, %d overwrite, %d unchanged" % (
        "dry run: " if dry_run else "", name, plan.kind, plan.base, c, o, u)


def entry_lines(plan):
    return ["%s %s%s" % (e.action, e.rel, " (UNVERIFIED destination: %s)" % e.unverified if e.unverified else "")
            for e in plan.entries]


def diff_lines(plan):
    out = []
    for e in plan.entries:
        if e.action != "overwrite": continue
        out += [ln.rstrip("\n") for ln in difflib.unified_diff(
            e.old.decode("utf-8", "replace").splitlines(True), e.data.decode("utf-8", "replace").splitlines(True),
            "a/" + e.base_rel, "b/" + e.base_rel)]
    return out


def manifest_hash(manifest):
    """sha256 of the canonical JSON of the manifest a user-scope signature binds."""
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii")).hexdigest()


def manifest(plan, registration_hash):
    return {"format": 1, "scope": plan.scope, "tool": "claude", "unit": plan.unit,
            "registration_hash": registration_hash,
            "entries": [{"destination": e.dest, "source": e.source, "source_sha256": hashlib.sha256(e.data).hexdigest()}
                        for e in plan.entries]}


def _fresh(root, stamp):
    for i in range(1, 1000):
        p = os.path.join(root, stamp if i == 1 else "%s-%d" % (stamp, i))
        try:
            os.mkdir(p)
            return p
        except FileExistsError:
            continue
    raise InstallError(["no free backup folder for %s under %s" % (stamp, root)])


def apply(plan, policy, stamp):
    """Backs up every file to overwrite into <base>/<backup_dir>/<stamp>/, then replaces each changed file atomically
    (a temp file in the same folder, os.replace). Returns the backup folder or None; writes nothing when nothing
    changes."""
    writes = [e for e in plan.entries if e.action != "unchanged"]
    over = [e for e in writes if e.action == "overwrite"]
    backup = None
    if over:
        root = os.path.join(plan.base, policy.backup_dir)
        os.makedirs(root, exist_ok=True)
        backup = _fresh(root, stamp)
        for e in over:
            b = os.path.join(backup, *e.base_rel.split("/"))
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(e.dest, b)
    for e in writes:
        folder = os.path.dirname(e.dest)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".install-")
        try:
            with os.fdopen(fd, "wb") as f: f.write(e.data)
            os.chmod(tmp, e.mode)
            os.replace(tmp, e.dest)
        except BaseException:
            if os.path.lexists(tmp): os.unlink(tmp)
            raise
    return backup
