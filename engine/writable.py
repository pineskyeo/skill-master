"""The loop builder's writable set and the fingerprint of everything outside it, one table keyed by mode.

Skill mode (edit-allowlist): the skill loop builder may change SKILL.md, references/**, scripts/** and assets/** of the
skill folder. Agent mode (agent-allowlist): the agent loop builder may change AGENT.md of the agent folder, nothing
else. The PreToolUse hook (claude_bash_gate.py), both loops' before-and-after checks and `skill_eval.py builder-check`
all read MODES. Standard library only, and no import of skill_eval or eva: the hook imports it.

A fingerprint maps a name to a value for every entry outside the writable set, under the unit folder and under its
workspace. A name is the POSIX path relative to the unit folder's realpath, so a workspace entry outside the folder
reads "../<workspace>/approvals.json". Values come from lstat and never follow a link."""
import collections, hashlib, json, os, stat

WRITABLE_FILES = ("SKILL.md",)
WRITABLE_TREES = ("references", "scripts", "assets")
SHOWN = " ".join(WRITABLE_FILES + tuple(t + "/**" for t in WRITABLE_TREES))
ALLOWLIST_MODE = "edit-allowlist"
AGENT_MODE = "agent-allowlist"
Writable = collections.namedtuple("Writable", "files trees shown unit")
MODES = {ALLOWLIST_MODE: Writable(WRITABLE_FILES, WRITABLE_TREES, SHOWN, "skill"),
         AGENT_MODE: Writable(("AGENT.md",), (), "AGENT.md", "agent")}
BUNDLE_PREFIX = "@ruleset/"
OLD_AGENT_PREFIX = "@old-agent/"

def is_writable(rel, mode=ALLOWLIST_MODE):
    w = MODES[mode]
    return rel is not None and (rel in w.files or rel.split("/")[0] in w.trees)

def under(root, path):
    """The POSIX path of the normalized absolute path below root, or None. Root is found by file identity among the
    path's ancestors, so an alias of root (a link, or another letter case on a case-insensitive disk) still matches."""
    root_st, parts = os.stat(root), []
    while True:
        try:
            if os.path.samestat(os.stat(path), root_st): return "/".join(reversed(parts)) or None
        except OSError:
            pass
        parent, leaf = os.path.split(path)
        if parent == path: return None
        parts.append(leaf); path = parent

def entry(path):
    try: st = os.lstat(path)
    except FileNotFoundError: return None
    except OSError as ex: return "error:" + type(ex).__name__
    if stat.S_ISLNK(st.st_mode): return "link:" + os.readlink(path)
    if stat.S_ISDIR(st.st_mode): return "dir"
    if not stat.S_ISREG(st.st_mode): return "type:%o" % stat.S_IFMT(st.st_mode)
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""): h.update(block)
    except OSError as ex:
        return "error:" + type(ex).__name__
    return "sha256:" + h.hexdigest()

def odd_file(path):
    """None for a regular file with one link, else what the entry is ("missing", "links:<n>" or an entry value). The
    content of a regular file is never read: it stays writable."""
    try: st = os.lstat(path)
    except FileNotFoundError: return "missing"
    except OSError as ex: return "error:" + type(ex).__name__
    if stat.S_ISREG(st.st_mode): return None if st.st_nlink == 1 else "links:%d" % st.st_nlink
    return entry(path)

def _walk(folder, base, skip, out):
    try: names = sorted(os.listdir(folder))
    except OSError as ex:
        out[os.path.relpath(folder, base).replace(os.sep, "/")] = "error:" + type(ex).__name__
        return
    for n in names:
        path = os.path.join(folder, n)
        if skip(path): continue
        value = entry(path)
        out[os.path.relpath(path, base).replace(os.sep, "/")] = value
        if value == "dir": _walk(path, base, skip, out)

def fingerprint(skill_dir, workspace, mode=ALLOWLIST_MODE):
    skill, ws = os.path.realpath(skill_dir), os.path.realpath(workspace)
    top = {os.path.join(skill, n) for n in MODES[mode].files + MODES[mode].trees}
    out = {}
    if os.path.isdir(skill): _walk(skill, skill, top.__contains__, out)
    if os.path.isdir(ws) and ws != skill: _walk(ws, skill, lambda p: p == skill, out)
    return out

def bundle_fingerprint(root, rels):
    """BUNDLE_PREFIX + rel for each rel (a file or a folder walked below it) of the rule bundle under root, so no name
    can collide with a path of the unit folder or its workspace."""
    out = {}
    for rel in rels:
        path = os.path.join(root, *rel.split("/"))
        value = entry(path)
        out[BUNDLE_PREFIX + rel] = value
        if value == "dir":
            sub = {}
            _walk(path, path, lambda p: False, sub)
            out.update((BUNDLE_PREFIX + rel + "/" + k, v) for k, v in sub.items())
    return out

def folder_fingerprint(folder, prefix):
    """prefix + rel for every entry below folder, AGENT.md included, never following a link."""
    out = {}
    _walk(folder, folder, lambda p: False, out)
    return {prefix + k: v for k, v in out.items()}

def name(skill_dir, path):
    real = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    return os.path.relpath(real, os.path.realpath(skill_dir)).replace(os.sep, "/")

def _harness_owned(before, after, writes):
    """True when the harness's own writes, (read value, written value) in order, explain before -> after exactly."""
    value = before
    for read, written in writes:
        if read != value: return False
        value = written
    return bool(writes) and value == after

def changed(before, after, harness=()):
    """Sorted names added, removed or changed, minus those the harness writes (name, read, written) fully explain."""
    names = sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))
    return [n for n in names
            if not _harness_owned(before.get(n), after.get(n), [(r, w) for h, r, w in harness if h == n])]

def digest(fp):
    return hashlib.sha256(json.dumps(sorted(fp.items()), separators=(",", ":")).encode("ascii")).hexdigest()

def shown(name_):
    return json.dumps(name_)[1:-1]
