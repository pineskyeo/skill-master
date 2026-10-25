"""The local_refs op (moved from eva.py, unit A4): local file references of a SKILL.md body (Markdown links, bundled-dir
paths in prose, run commands in fenced code), where they resolve (skill folder, enclosing git repo, outside), nested
reference chains, reference depth and the table of contents of long reference files. No import of eva or skill_eval."""
import os, posixpath, re
from engine.primitives.heading_present import fenced, headings, main_file, read_text, unfenced

SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")  # 'C:\x', 'C:/x' and drive-relative 'C:x': a path, never a 1-letter URL scheme
MD_LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^)]*[\"'])?\s*\)")
# Spec bundled directories (agentskills.io "Directory structure"); a path into one of them is a file reference
# whether written as a link, in inline code, or as plain text ("Run scripts/x.py").
BUNDLED_REF_RE = re.compile(r"(?<![\w./\\-])((?:scripts|references|assets)[/\\][^\s`'\"()<>\[\]{}|,;]+)")
GLOB_CHARS = set("*?")
HANGUL = "\u3131-\u318e\uac00-\ud7a3"
# Korean particle/ending glued to a path that ends in a Latin letter, digit, ')' or closing '**'
KO_PARTICLE_RE = re.compile(r"(?<=[A-Za-z0-9)\]*])[%s]+$" % HANGUL)
# Inside fenced code only run commands are references, checked in every command of a line (split on &&, ||, ;, |):
# an interpreter followed by a bundled-dir path ('python scripts/x.py', '$ bash assets/setup.sh', 'cd d && sh scripts/b.sh'),
# or direct execution of a script ('./scripts/x.sh', 'scripts/x.py args'). A bare 'scripts/x.py' is a file listing,
# not a run command, when it is indented, carries a '# note', or the block draws a tree.
RUNNER = r"(?:sudo\s+)?(?:python[0-9.]*|bash|sh|zsh|node|deno\s+run|ruby|perl|Rscript|php|pwsh|uv\s+run(?:\s+python[0-9.]*)?)"
_ARG = r"[^\s;&|<>'\"`]+"
_ENV = r"(?:[A-Za-z_]\w*=\S*\s+)*"  # 'LANG=C python scripts/x.py'
FENCED_RUN_RE = re.compile(r"^(?:\$\s+)?%s%s\s+(?:-\S+\s+)*(?:\./)?((?:scripts|references|assets)/%s)" % (_ENV, RUNNER, _ARG))
FENCED_EXEC_RE = re.compile(r"^(?:\$\s+)?%s(\./)?(scripts/%s)" % (_ENV, _ARG))
CMD_SPLIT_RE = re.compile(r"&&|\|\||;|\|")
TREE_CHARS = set("\u251c\u2514\u2502")


def _fenced_run_refs(code_lines):
    """Bundled-dir paths that fenced code lines run (see FENCED_RUN_RE / FENCED_EXEC_RE)."""
    out, tree = [], any(TREE_CHARS & set(ln) for ln in code_lines)
    for ln in code_lines:
        m = re.search(r"(^|\s)#", ln)  # shell comment
        cmd, note = (ln[:m.start()], True) if m else (ln, False)
        for i, seg in enumerate(CMD_SPLIT_RE.split(cmd)):
            indented = i == 0 and seg[:1] in (" ", "\t")
            seg = seg.strip()
            r = FENCED_RUN_RE.match(seg)
            if r:
                out.append(r.group(1)); continue
            e = FENCED_EXEC_RE.match(seg)
            if e and (e.group(1) or not (indented or tree or (note and seg == e.group(0)))):
                out.append(e.group(2))
    return out


def _clean_bundled(t, stars=0):
    """Bundled-dir path with trailing prose removed (anchor, punctuation, Korean particle, and up to `stars` closing
    '*' of the emphasis the path was opened with, as in '**scripts/x.py**'); None if what is left is a directory
    mention or a glob ('assets/icon*', 'scripts/*.py')."""
    t = t.split("#", 1)[0]
    prev = None
    while t != prev:
        prev = t
        t = KO_PARTICLE_RE.sub("", t.rstrip(".:!?"))
        k = len(t) - len(t.rstrip("*"))
        if 0 < k <= stars:
            t = t[:-k]
    if not t or t[-1] in "/\\" or GLOB_CHARS & set(t):
        return None
    return t


def local_refs(text):
    """Local file references in markdown: every relative markdown link target and bundled-dir path (scripts/,
    references/, assets/) in inline code, **bold** or plain text outside fenced code, plus run commands inside
    fenced code ('python scripts/x.py'). URLs, #anchors, directory mentions ('scripts/') and glob patterns
    ('assets/*.png') are not file references."""
    prose = unfenced(text)
    refs = set()
    for t in MD_LINK_RE.findall(prose):
        t = t.split("#", 1)[0].split("?", 1)[0]
        if t and (not SCHEME_RE.match(t) or WIN_DRIVE_RE.match(t)):  # 'C:\x' / 'C:/x' / 'C:x' is a path, not a URL scheme
            refs.add(t)
    found = [(m.group(1), len(prose[:m.start()]) - len(prose[:m.start()].rstrip("*")))
             for m in BUNDLED_REF_RE.finditer(prose)]
    found += [(t, 0) for t in _fenced_run_refs(fenced(text))]
    for t, stars in found:
        t = _clean_bundled(t, stars)
        if t: refs.add(t)
    return refs


def ref_problem(ref):
    """Why a reference is not a portable relative path inside the skill root (None if fine)."""
    if "\\" in ref: return "backslash (use forward slashes)"
    if ref.startswith("/") or ref.startswith("~") or WIN_DRIVE_RE.match(ref): return "absolute or drive path (use a path relative to the skill root)"
    norm = posixpath.normpath(ref)
    if norm == ".." or norm.startswith("../"): return "points outside the skill directory"
    return None


def git_root(path):
    """Nearest enclosing git work tree root of path (a directory holding '.git', dir or worktree file), or None."""
    d = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")): return d
        up = os.path.dirname(d)
        if up == d: return None
        d = up


def repo_root_ref(path, ref):
    """True if ref, missing from the skill folder, is a file relative to the enclosing git repo root and stays inside
    it (after resolving symlinks). Refs with a ref_problem ('..', absolute, backslash) never qualify."""
    root = git_root(path)
    if root is None or ref_problem(ref): return False
    target = os.path.realpath(os.path.join(root, ref))
    return os.path.commonpath([root, target]) == root and os.path.exists(target)


def within(root, target):
    return root is not None and os.path.commonpath([root, target]) == root


def ref_targets(path, refs):
    """Sort refs by where they resolve (after '..' normalization and symlinks). Returns sorted lists
    (missing, repo_refs, up_refs, escaped): missing = found nowhere; repo_refs = absent from the skill folder but found
    from the git repo root (repo_root_ref); up_refs = exist outside the skill folder but inside the repo;
    escaped = absolute / '~' / drive / backslash paths, or targets outside both the skill folder and the repo.
    Escapes are classified before the existence check, so a missing escaping path ('../../../../nope') or a dangling
    symlink pointing outside is escaped, not missing. Refs that resolve to an existing file inside the skill folder
    (bundled) are in none of the lists."""
    skill, repo = os.path.realpath(path), git_root(path)
    missing, repo_refs, up_refs, escaped = [], [], [], []
    for x in sorted(refs):
        if "\\" in x or x.startswith(("/", "~")) or WIN_DRIVE_RE.match(x):
            escaped.append(x); continue  # absolute, drive or backslash: never a portable skill reference
        target = os.path.realpath(os.path.join(skill, x))  # resolves dangling symlinks as far as they go
        if not (within(skill, target) or within(repo, target)):
            escaped.append(x); continue
        if os.path.exists(target):
            if within(skill, target): continue
            up_refs.append(x)
        elif repo_root_ref(path, x): repo_refs.append(x)
        else: missing.append(x)
    return missing, repo_refs, up_refs, escaped


def toc_first(text, pattern):
    """True if the first heading after the title (outside fences) matches pattern (a table of contents)."""
    heads = headings(text)
    if heads and heads[0][0] == 1:
        heads = heads[1:]
    return bool(heads) and bool(re.search(pattern, heads[0][1], re.I))


class _Facts:
    """The reference facts of one skill, computed once per Bundle.apply."""
    def __init__(self, path, body):
        self.refs = local_refs(body)
        self.probs = sorted("%s (%s)" % (x, ref_problem(x)) for x in self.refs if ref_problem(x))
        self.targets = ref_targets(path, self.refs)
        self.direct = {posixpath.normpath(x) for x in self.refs if not ref_problem(x)}
        self.md_refs = sorted(x for x in self.direct if x.lower().endswith(".md") and os.path.isfile(os.path.join(path, x)))
        self.texts = {x: read_text(os.path.join(path, x))[0] for x in self.md_refs}
        self.nested = []
        for x in self.md_refs:
            for t in local_refs(self.texts[x] or ""):
                if ref_problem(t): continue
                cand = posixpath.normpath(posixpath.join(posixpath.dirname(x), t))
                if not os.path.exists(os.path.join(path, cand)):
                    cand = posixpath.normpath(t)
                if cand not in self.direct and cand != x and cand != "SKILL.md":
                    self.nested.append("%s -> %s" % (x, t))


class RefsCheck:
    """{op: local_refs, check: relative|exist|one_level|path_depth|toc, depth, toc_lines, toc_pattern}. Facts carry the
    reason parts eva.py formatted, so the rule messages render the A3 reasons."""
    CHECKS = ("relative", "exist", "one_level", "path_depth", "toc")

    def __init__(self, node, folder=None):
        self.node, self.check = node, node["check"]
        if self.check not in self.CHECKS:
            raise ValueError("local_refs: unknown check '%s'" % self.check)
        if self.check == "toc":
            re.compile(node["toc_pattern"])

    def facts(self, unit):
        if "local_refs" not in unit.memo:
            unit.memo["local_refs"] = _Facts(unit.path, main_file(unit).body)
        return unit.memo["local_refs"]

    def __call__(self, unit):
        f, n = self.facts(unit), self.node
        if self.check == "relative":
            return verdict(not f.probs, {"detail": ": %s" % f.probs if f.probs else "", "problems": ", ".join(f.probs)})
        if self.check == "exist":
            missing, repo_refs, up_refs, escaped = f.targets
            if missing or escaped or not (repo_refs or up_refs):
                detail = (": missing %s" % missing if missing else "") + \
                         ("; outside the skill folder and the git repo (absolute, backslash or escaping path): %s" % escaped
                          if escaped else "") + \
                         ("; repo-root-relative reference, not bundled: %s" % repo_refs if repo_refs else "") + \
                         ("; outside the skill folder, inside the git repo, not bundled: %s" % up_refs if up_refs else "")
                return verdict(not (missing or escaped), {"detail": detail,
                                                          "refs": ", ".join(escaped + missing if escaped else missing)})
            detail = "; ".join(s for s in (
                "repo-root-relative reference, not bundled: %s (found from the enclosing git repo root, not the "
                "skill folder)" % repo_refs if repo_refs else "",
                "outside the skill folder, inside the git repo, not bundled: %s" % up_refs if up_refs else "") if s)
            return "review", {"detail": detail, "refs": ", ".join(repo_refs + up_refs)}, None, False
        if self.check == "one_level":
            return verdict(not f.nested, {"detail": ": %s" % f.nested if f.nested else "", "chains": ", ".join(f.nested)})
        if self.check == "path_depth":
            deep = sorted(x for x in f.direct if x.count("/") > n["depth"])
            return verdict(not deep, {"detail": ": %s" % deep if deep else ""})
        long_refs = [(x, toc_first(f.texts[x], n["toc_pattern"])) for x in f.md_refs
                     if f.texts[x] is not None and len(f.texts[x].splitlines()) > n["toc_lines"]]
        if not long_refs:
            return "na", {"toc_lines": n["toc_lines"]}, None, False
        no_toc = [x for x, ok in long_refs if not ok]
        return verdict(not no_toc, {"toc_lines": n["toc_lines"], "detail": ": missing in %s" % no_toc if no_toc else "",
                                    "files": ", ".join(no_toc)})


def verdict(ok, facts):
    return ("pass" if ok else "fail"), facts, None, False
