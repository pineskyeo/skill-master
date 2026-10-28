"""Transcript verifiers and typed agent criteria (unit B1, plan.md:779-790). The verifiers moved unchanged from eva.py:
answer lookup outside the workspace, write calls, cited lines shown by a single-file read, URLs in tool results. One
typed dispatcher (criterion_evidence) maps a criterion object of schemas/agent-frontmatter.schema.json to its evidence,
and parse_agent reads an AGENT.md through that schema. Standard library only; no import of eva or skill_eval
(engine/writable.py)."""
import json, os, posixpath, re, shlex
from engine.loaders import parse_frontmatter_ex
from engine.primitives import parity
from engine.primitives.script_denylist import read_text


def _nonempty_str(v):
    return isinstance(v, str) and bool(v.strip())


# ---------- answer-lookup integrity (no-answer-lookup) ----------
PATH_KEYS = {"path", "paths", "file", "file_path", "filepath", "filename", "dir", "directory", "cwd", "root", "target"}
CMD_KEYS = {"command", "cmd", "script", "args"}
GIT_HISTORY_RE = re.compile(
    r"\bgit\b[^;&|\n]*?\b(?:reflog|fsck|cat-file|rev-list\b[^;&|\n]*--all|"
    r"(?:log|shortlog|whatchanged)\b[^;&|\n]*(?:--all|--reflog|--walk-reflogs|\s-g\b)|"
    r"[^;&|\n]*--(?:lost-found|unreachable))")

def _strings(v):
    if isinstance(v, str): yield v
    elif isinstance(v, list):
        for x in v: yield from _strings(x)
    elif isinstance(v, dict):
        for x in v.values(): yield from _strings(x)

def _call_inputs(inp):
    """Split a tool input into (path-like strings, command strings)."""
    if isinstance(inp, str):
        return [], [inp]
    paths, cmds = [], []
    if isinstance(inp, dict):
        for k, v in inp.items():
            if k in PATH_KEYS: paths += list(_strings(v))
            elif k in CMD_KEYS: cmds += list(_strings(v))
    elif isinstance(inp, list):
        cmds += list(_strings(inp))
    return paths, cmds

def _outside(token, workspace=None):
    """True if a path token points outside the workspace (.., ~, filesystem root, foreign absolute path)."""
    tok = token.strip().strip("'\"")
    if tok.startswith("-") and "=" in tok:
        tok = tok.split("=", 1)[1]
    if not tok: return False
    if tok == "~" or tok.startswith("~/"): return True
    if tok.startswith("/"):
        if tok.rstrip("/") == "": return True
        if workspace:
            ws = posixpath.normpath(workspace)
            norm = posixpath.normpath(tok)
            return not (norm == ws or norm.startswith(ws.rstrip("/") + "/"))
        return False
    norm = posixpath.normpath(tok)
    return norm == ".." or norm.startswith("../")

def answer_lookup_violations(calls, workspace=None):
    """List of '<tool>: <offending input>' for calls that look for answers instead of solving the task:
    searching outside the workspace or mining git history."""
    out = []
    for c in calls:
        paths, cmds = _call_inputs(c["input"])
        for p in paths:
            if _outside(p, workspace):
                out.append("%s: path %s" % (c["tool"], p))
        for cmd in cmds:
            if GIT_HISTORY_RE.search(cmd):
                out.append("%s: git history mining '%s'" % (c["tool"], cmd))
            try:
                toks = shlex.split(cmd)
            except ValueError:
                toks = cmd.split()
            bad = [t for t in toks if _outside(t, workspace)]
            if bad:
                out.append("%s: outside workspace %s in '%s'" % (c["tool"], bad, cmd))
    return out

# ---------- outcome checkers ----------
URL_RE = re.compile(r"https?://[^\s<>()\[\]|\"'`]+")
TABLE_RE = re.compile(r"^[ \t]*\|?.*\|.*\n[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$", re.M)
FILELINE_RE = re.compile(r"(?<![\w/.:-])((?:[\w.-]+/)*[\w-][\w.-]*\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?(?![\d])")
# Line-numbered tool output that proves a line was read: cat -n / nl / Read-tool style "N<tab|arrow|:>text"
# (cat -n '%6d\t', Read tool 'N' plus U+2192), attributed to the one file that call read (_read_call). Only a result that is
# line-numbered throughout counts (see _numbered_lines); single-file grep -n output is parsed in _grep_lines.
NUMBERED_LINE_RE = re.compile(r"^[ \t]*(\d+)(\t|\u2192|:)")
GREP_LINE_RE = re.compile(r"^(\d+)[:-]")
WRITE_WORDS = {"write", "edit", "editor", "multiedit", "create", "delete", "remove", "rm", "move", "mv", "rename",
               "patch", "apply", "replace", "insert", "append", "save", "commit", "push", "mkdir", "touch", "update"}
WRITE_CMD_RE = re.compile(r"(?:^|[;&|]\s*|\bsudo\s+)(?:rm|mv|cp|touch|mkdir|rmdir|tee|truncate|chmod|chown|ln|dd)\b|"
                          r"\bsed\s+(?:-\w*\s+)*-i|(?<![0-9&>])>>?\s*(?!&|/dev/null)\S|"
                          r"\bgit\s+(?:commit|push|add|rm|mv|reset|checkout|restore|apply|stash|merge|rebase|cherry-pick)\b")

def _norm_url(u):
    return u.rstrip(".,;:").rstrip("/")

def _tool_words(name):
    return {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)}

def write_calls(calls):
    """Tool calls that write/edit files: write-like tool names or write-like shell commands."""
    out = []
    for c in calls:
        if _tool_words(c["tool"]) & WRITE_WORDS:
            out.append(c["tool"]); continue
        _, cmds = _call_inputs(c["input"])
        hit = [cmd for cmd in cmds if WRITE_CMD_RE.search(cmd)]
        if hit:
            out.append("%s '%s'" % (c["tool"], hit[0]))
    return out

def _norm_path(p, cwd=None, workspace=None):
    """Canonical form of a file path for exact comparison: quotes and './' dropped, normalized, joined to cwd when
    relative, and made relative to the workspace when it lies inside it. No suffix matching: 'README.md' and
    'docs/README.md' are different files."""
    p = p.strip().strip("'\"")
    if cwd and not p.startswith("/"):
        p = posixpath.join(cwd.strip().strip("'\""), p)
    p = posixpath.normpath(p)
    if workspace and p.startswith("/"):
        ws = posixpath.normpath(workspace)
        if p.startswith(ws.rstrip("/") + "/"):
            p = posixpath.relpath(p, ws)
    return p[2:] if p.startswith("./") else p

SHELL_META_RE = re.compile(r"[|;&<>`$()\n]")
# Tool names (split into words by _tool_words) that read one file and return its lines: 'Read', 'read_file',
# 'view_file', 'open_file', 'cat'. Search/list/stat/count/write tools ('grep', 'search', 'wc', 'stat', 'write') are not.
READ_TOOL_WORDS = {"read", "view", "open", "cat"}
NOT_READ_TOOL_WORDS = WRITE_WORDS | {"search", "grep", "rg", "find", "glob", "list", "ls", "stat", "wc", "count",
                                     "head", "tail", "diff", "fetch", "web", "url", "http"}
# Tools whose input is a shell command; the command itself must then be a recognized single-file read.
SHELL_TOOL_WORDS = {"bash", "shell", "sh", "zsh", "terminal", "exec", "execute", "run", "command", "cmd", "console"}
CWD_KEYS = {"cwd"}
_GREP_FLAG_LETTERS = set("niwFExhHvsI")  # options that keep 'N:line' output for one file honest

def _numbered_read_cmd(cmd):
    """(kind, file) for a shell command that prints one file's lines with their real line numbers, else None.
    kind 'numbered': 'cat -n FILE' (not -b/-s, which skip or squeeze lines), 'nl -ba FILE' (optionally -w N).
    kind 'grep': 'grep -n [-i -w -F -E -x -v -h -H -s -A N -B N -C N] [-e] PATTERN FILE' on exactly one file
    (output 'N:line', context 'N-line', with -H 'FILE:N:line'). Pipes, redirects, several files, recursion and any
    other command (echo, printf, wc -l, head, ...) are not attributable."""
    if SHELL_META_RE.search(cmd): return None
    try: toks = shlex.split(cmd)
    except ValueError: return None
    if not toks: return None
    prog, args = os.path.basename(toks[0]), toks[1:]
    if prog == "grep":
        letters, pos, i, has_n, pattern = set(), [], 0, False, None
        while i < len(args):
            a = args[i]
            if a in ("-e", "--regexp") and i + 1 < len(args):
                if pattern is not None: return None
                pattern = args[i + 1]; i += 2; continue
            if a in ("-A", "-B", "-C") and i + 1 < len(args) and args[i + 1].isdigit():
                i += 2; continue
            if re.match(r"^-[ABC]\d+$", a):
                i += 1; continue
            if a in ("--line-number",):
                has_n = True; i += 1; continue
            if a.startswith("-") and a != "-" and not a.startswith("--"):
                letters |= set(a[1:]); i += 1; continue
            if a.startswith("--"):
                return None
            pos.append(a); i += 1
        if letters - _GREP_FLAG_LETTERS or not (has_n or "n" in letters):
            return None
        if pattern is None:
            if not pos: return None
            pos = pos[1:]
        return ("grep", pos[0]) if len(pos) == 1 and pos[0] != "-" else None
    files = [t for t in args if not t.startswith("-") or t == "-"]
    flags = [t for t in args if t.startswith("-") and t != "-"]
    if prog == "cat":
        letters = "".join(f[1:] for f in flags if not f.startswith("--"))
        longs = [f for f in flags if f.startswith("--")]
        ok = ("n" in letters or "--number" in longs) and not set(letters) - {"n"} and not set(longs) - {"--number"}
    elif prog == "nl":
        if "a" in files and "-b" in flags:  # 'nl -b a FILE'
            files.remove("a"); flags = ["-ba" if f == "-b" else f for f in flags]
        if "-w" in flags and files and files[0].isdigit():  # 'nl -w 3 FILE'
            files.pop(0); flags.remove("-w")
        ok = "-ba" in flags and all(f == "-ba" or re.match(r"^-w\d+$", f) for f in flags)
    else:
        ok = False
    return ("numbered", files[0]) if ok and len(files) == 1 and files[0] != "-" else None

def _read_call(call, workspace=None):
    """(kind, file as printed, canonical file) when a tool call read exactly one file, else None.
    Read-tool style: a read tool (READ_TOOL_WORDS, no NOT_READ_TOOL_WORDS) with exactly one path-key value and no
    command. Shell style: a shell tool (SHELL_TOOL_WORDS) whose one command is _numbered_read_cmd. An optional
    'cwd' key resolves relative paths. Anything else (echo/printf output, wc/stat/search/write tools with a path key,
    several paths) reads no attributable file."""
    inp, words = call["input"], _tool_words(call["tool"])
    cwd = None
    if isinstance(inp, dict):
        cw = [v for k, v in inp.items() if k in CWD_KEYS]
        if cw:
            if len(cw) != 1 or not _nonempty_str(cw[0]): return None
            cwd = cw[0]
        inp = {k: v for k, v in inp.items() if k not in CWD_KEYS}
    paths, cmds = _call_inputs(inp)
    paths = [x for x in paths if x.strip()]
    if words & NOT_READ_TOOL_WORDS:
        return None
    if paths and not cmds and words & READ_TOOL_WORDS:
        if len(paths) != 1: return None
        return "numbered", paths[0], _norm_path(paths[0], cwd, workspace)
    if cmds and not paths and words & SHELL_TOOL_WORDS and len(cmds) == 1:
        rc = _numbered_read_cmd(cmds[0])
        if rc: return rc[0], rc[1], _norm_path(rc[1], cwd, workspace)
    return None

def lines_seen(run):
    """{line number: {canonical file paths}} for lines a tool call showed with their real line numbers.
    A line counts only from the result of a call that read exactly that one file (_read_call) and only when the whole
    result has that call's line-numbered shape: 'N<sep>line' for a read tool / 'cat -n' / 'nl -ba' (_numbered_lines),
    'N:line' or 'N-line' (or 'FILE:N:line' for that same FILE) for single-file 'grep -n' (_grep_lines). Top-level tool_results and path-prefixed lines from any other call ('echo src/a.py:42:x', multi-file
    grep) are not attributable to a read and do not count."""
    seen = {}
    if not run: return seen
    ws = run.get("workspace")
    for c in run["calls"]:
        if not c["result"]: continue
        rc = _read_call(c, ws)
        if rc is None: continue
        kind, printed, target = rc
        nums = _grep_lines(c["result"], printed) if kind == "grep" else _numbered_lines(c["result"], printed, target, ws)
        for ln in nums:
            seen.setdefault(ln, set()).add(target)
    return seen

def _result_lines(result):
    """Lines of a tool result without the trailing empty lines a final newline leaves."""
    lines = [ln.rstrip("\r") for ln in result.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines

def _numbered_lines(result, printed, target, ws=None):
    """Line numbers of a read result that is line-numbered throughout: every line is 'N<sep>text' with one separator
    (tab, '\u2192' or ':') for the whole result and consecutive numbers N, N+1, ... Raw file content ('200 OK\\n404 Not
    Found', a '42 x' line) yields []. A 'wc -l' style 'N path' line is a count, not line N, and yields no number."""
    parsed = [(NUMBERED_LINE_RE.match(ln), ln) for ln in _result_lines(result)]
    if not parsed or any(m is None for m, _ in parsed): return []
    if len({m.group(2) for m, _ in parsed}) != 1: return []
    nums = [int(m.group(1)) for m, _ in parsed]
    if any(b != a + 1 for a, b in zip(nums, nums[1:])): return []
    out = []
    for n, (m, ln) in zip(nums, parsed):
        rest = ln[m.end():].strip()
        if rest and (_norm_path(rest) == _norm_path(printed) or _norm_path(rest, None, ws) == target):
            continue
        out.append(n)
    return out

def _grep_lines(result, printed):
    """Line numbers of single-file 'grep -n' output: every line is 'N:text' (match) or 'N-text' (context),
    optionally prefixed 'FILE:' / 'FILE-' for the read FILE (-H), or a '--' group separator, with increasing numbers.
    Any other line means the result is not that grep's output and yields []."""
    out = []
    for line in _result_lines(result):
        if line == "--": continue
        for pre in (printed + ":", printed + "-"):
            if line.startswith(pre) and re.match(r"^\d+%s" % re.escape(pre[-1]), line[len(pre):]):
                line = line[len(pre):]; break
        m = GREP_LINE_RE.match(line)
        if not m or (out and int(m.group(1)) <= out[-1]): return []
        out.append(int(m.group(1)))
    return out

# ---------- typed criteria (schemas/agent-frontmatter.schema.json $defs/criterion) ----------
KINDS = {"min_verified_sources": "sources", "file_line_citations": "file-lines", "no_writes": "no-writes",
         "max_chars": "length", "markdown_table": "table", "llm": "needs-review", "command_ran": "command-ran",
         "command_absent": "command-absent", "file_exists": "file-exists"}
AGENT_SCHEMA = "agent-frontmatter.schema.json"
MIGRATION = (
    "Migrate AGENT.md to v2: rename tools to capabilities and list values from read, search, write, shell, web_fetch, "
    "web_search, delegate, skill; write each success criterion as one typed JSON object in single quotes, one per list "
    "item: '{\"type\": \"min_verified_sources\", \"min\": N}', '{\"type\": \"file_line_citations\", \"min\": N}', "
    "'{\"type\": \"no_writes\"}', '{\"type\": \"max_chars\", \"max\": N}', '{\"type\": \"markdown_table\"}' or "
    "'{\"type\": \"llm\", \"text\": \"...\"}'; add model_policy (a role name such as judgment) and targets: [claude, "
    "codex, opencode, hermes]. Every AGENT.md must match schemas/agent-frontmatter.schema.json.")
MAX_SHOWN = 6


def show(crit):
    """A criterion as compact JSON for a row reason."""
    return json.dumps(crit, ensure_ascii=True)


def criterion_evidence(crit, final, run=None):
    """(kind, ok, observed, fix) of one schema-valid criterion object on the final answer and the run's tool calls and
    results. run: normalized transcript from eva.load_transcript (None = no tool calls, no tool results). ok is None only
    for llm, which a human or model grader decides, so llm is never PASS."""
    calls = run["calls"] if run else []
    results = run["results"] if run else []
    kind = crit["type"]
    if kind == "no_writes":
        w = write_calls(calls)
        return ("no-writes", not w, "%d write/edit tool calls%s" % (len(w), (": %s" % w) if w else ""),
                "Remove the file-changing tool calls %s (write, edit and the like) and work read-only." % w)
    if kind == "file_line_citations":
        n = crit["min"]
        seen, ws = lines_seen(run), (run or {}).get("workspace")
        cites = sorted({(f, int(a), int(b) if b else int(a)) for f, a, b in FILELINE_RE.findall(final)})
        shown = lambda f, ln: _norm_path(f, None, ws) in seen.get(ln, ())
        # a range 'a-b' needs a <= b and every line a..b shown (all() stops at the first unseen line)
        ok_cites = [c for c in cites if c[1] <= c[2] and all(shown(c[0], ln) for ln in range(c[1], c[2] + 1))]
        bad = [("%s:%d" % c[:2]) + ("-%d" % c[2] if c[2] != c[1] else "") for c in cites if c not in ok_cites]
        return ("file-lines", len(ok_cites) >= n,
                "%d file:line citations whose lines appear in captured read results (need >= %d)%s" % (len(ok_cites), n,
                ("; cited lines never shown by a tool: %s" % bad) if bad else ""),
                "Cite only lines that a tool call reading that one file (the Read tool, cat -n FILE, nl -ba FILE, grep -n "
                "PATTERN FILE) showed with their line numbers, as 'path:line' (for example src/app.py:42). A range "
                "'path:start-end' needs start <= end and every line in between shown. The path must equal the path that "
                "was read. Need at least %d (verified now: %d). Lines that do not exist or were never read do not count."
                % (n, len(ok_cites)))
    if kind == "min_verified_sources":
        n = crit["min"]
        urls = sorted(set(_norm_url(u) for u in URL_RE.findall(final)))
        fetched = set(_norm_url(u) for res in results for u in URL_RE.findall(res))
        ok_urls = [u for u in urls if u in fetched]
        bad = [u for u in urls if u not in fetched]
        return ("sources", len(ok_urls) >= n,
                "%d verified unique URLs (need >= %d)%s" % (len(ok_urls), n,
                ("; not found in tool results: %s" % bad) if bad else ""),
                "Cite at least %d source URLs that actually appear in a tool result (verified now: %d). Made-up URLs do "
                "not count." % (n, len(ok_urls)))
    if kind == "max_chars":
        mx = crit["max"]
        return ("length", len(final) <= mx, "%d chars (max %d)" % (len(final), mx),
                "Shorten the final answer to at most %d characters (now %d)." % (mx, len(final)))
    if kind == "markdown_table":
        ok = bool(TABLE_RE.search(final))
        return ("table", ok, "markdown table %s" % ("found" if ok else "not found"),
                "Put a Markdown table with a header row and a separator row (|---|) in the final answer.")
    if kind == "llm":
        return ("needs-review", None, "not machine-checkable, grade by human/model: '%s'" % crit["text"],
                "A human or model grader decides whether the final answer meets '%s'." % crit["text"])
    if kind in ("command_ran", "command_absent"):
        return _command_evidence(kind, crit["regex"], run)
    if kind == "file_exists":
        return _file_exists_evidence(crit["path"], run)
    raise ValueError("unknown criterion type %r" % (kind,))


def _complete(run):
    """Why run is not a complete normalized run (no transcript, or is_error not recorded false), else None."""
    if not run:
        return "no transcript"
    if run.get("is_error") is not False:
        return "the transcript records an error" if run.get("is_error") else "the transcript records no is_error: false"
    return None


def shell_commands(calls):
    """The command string of every call whose tool maps to the shell capability (rules/data/capabilities.json:
    Claude Bash, Codex shell); prompt, answer and tool results are never read."""
    table, _ = parity.capability_table()
    return [c["input"]["command"] for c in calls
            if isinstance(c.get("tool"), str) and table.get(c["tool"].strip().lower()) == "shell"
            and isinstance(c.get("input"), dict) and isinstance(c["input"].get("command"), str)]


def _command_evidence(kind, regex, run):
    pat = re.compile(regex)
    label = KINDS[kind]
    why = _complete(run)
    cmds = shell_commands(run["calls"]) if why is None else []
    hits = [c for c in cmds if pat.search(c)]
    ok = why is None and (bool(hits) if kind == "command_ran" else not hits)
    seen = why or "%d shell command(s), %d match %s%s" % (len(cmds), len(hits), json.dumps(regex),
                                                        (": %s" % json.dumps(hits[:3])) if hits else "")
    fix = ("Run a shell command that matches %s." if kind == "command_ran" else
           "Do not run a shell command that matches %s.") % json.dumps(regex)
    return label, ok, seen, fix


def _file_exists_evidence(path, run):
    why = _complete(run)
    manifest = (run or {}).get("work_manifest")
    if why is None and not isinstance(manifest, dict):
        why = "the transcript holds no work-folder manifest"
    rel = posixpath.normpath(path)
    entry = manifest.get(rel) if why is None else None
    ok = isinstance(entry, dict) and isinstance(entry.get("sha256"), str)
    seen = why or ("%s is a file in the work folder" % rel if ok else
                   "%s is a link in the work folder, not followed" % rel if isinstance(entry, dict) else
                   "%s is not a file in the work folder" % rel)
    return "file-exists", ok, seen, "Write the file %s inside the work folder." % rel


# ---------- AGENT.md v2 (schemas/agent-frontmatter.schema.json) ----------
_SCHEMAS = {}


def _schema(root, name):
    key = (root, name)
    if key not in _SCHEMAS:
        from engine import schema
        _SCHEMAS[key] = schema.compile_schema(schema.load(os.path.join(root, "schemas", name)))
    return _SCHEMAS[key]


def _ascii(text):
    return text.encode("ascii", "backslashreplace").decode("ascii")


class Agent:
    """meta: the frontmatter with every JSON-object criterion decoded (None when there is no usable frontmatter).
    problems: schema problems, ASCII. v1: what marks the file as AGENT.md v1 ([] for v2)."""
    def __init__(self, meta, problems, v1):
        self.meta, self.problems, self.v1 = meta, problems, v1

    @property
    def valid(self):
        return self.meta is not None and not self.problems and not self.v1

    @property
    def version(self):
        if self.v1:
            return "AGENT.md v1"
        return "AGENT.md v2 frontmatter invalid" if self.meta is not None else "AGENT.md frontmatter unusable"

    @property
    def detail(self):
        items = self.v1 + self.problems
        more = "; (+%d more)" % (len(items) - MAX_SHOWN) if len(items) > MAX_SHOWN else ""
        return "; ".join(items[:MAX_SHOWN]) + more

    @property
    def criteria(self):
        crit = (self.meta or {}).get("success_criteria")
        return crit if isinstance(crit, list) else []


def parse_agent(raw, root=parity.ROOT, schema_name=AGENT_SCHEMA):
    """The Agent of an AGENT.md text. A criterion item whose text starts with '{' is decoded as strict JSON; any other
    string criterion, a string success_criteria or a tools key marks the file as v1."""
    from engine import schema
    meta, _, ferr = parse_frontmatter_ex(raw)
    if meta is None:
        return Agent(None, [_ascii("frontmatter: %s" % ferr)], [])
    doc, marks, problems = dict(meta), [], []
    if "tools" in meta:
        marks.append("has a tools key")
    crit = meta.get("success_criteria")
    if isinstance(crit, str):
        marks.append("success_criteria is a string, not a list of typed objects")
    elif isinstance(crit, list):
        decoded = []
        for i, c in enumerate(crit):
            if isinstance(c, str) and c.strip().startswith("{"):
                try:
                    c = schema.loads(c)
                except schema.JsonError as e:
                    problems.append("/success_criteria/%d: not a JSON object: %s" % (i, e))
                else:
                    if not json.dumps(c, ensure_ascii=False).isascii():
                        problems.append("/success_criteria/%d: non-ASCII text in %s: a criterion holds English ASCII "
                                        "text only (JSON escapes included)" % (i, show(c)))
            elif isinstance(c, str):
                marks.append("success_criteria[%d] is a string, not a typed object" % i)
            decoded.append(c)
        doc["success_criteria"] = decoded
    problems += [schema.format_error(e) for e in _schema(root, schema_name).validate(doc)]
    return Agent(doc, [_ascii(p) for p in problems], marks)


def read_agent(path, root=parity.ROOT):
    """The Agent of path/AGENT.md."""
    raw, err = read_text(os.path.join(path, "AGENT.md"))
    if raw is None:
        return Agent(None, [_ascii(err)], [])
    return parse_agent(raw, root)


class AgentFrontmatterCheck:
    """op agent_frontmatter: pass when AGENT.md matches the schema; facts version (AGENT.md v1 named) and problems."""
    def __init__(self, node, root):
        self.root, self.name = root, node["schema"]
        if not os.path.isfile(os.path.join(root, "schemas", self.name)):
            raise ValueError("schema '%s' is not a file in the engine's schemas/ folder" % self.name)
        from engine import schema
        try:
            _schema(root, self.name)
        except (schema.JsonError, schema.SchemaRefused) as e:
            raise ValueError("schema '%s': %s" % (self.name, e)) from None

    def __call__(self, unit):
        raw = unit.read("AGENT.md")
        if not isinstance(raw, str):
            return "fail", {"version": "AGENT.md unreadable", "problems": _ascii(raw.reason)}, None, True
        agent = parse_agent(raw, self.root, self.name)
        if agent.valid:
            return "pass", {}, None, False
        return "fail", {"version": agent.version, "problems": agent.detail}, None, False
