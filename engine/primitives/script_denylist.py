"""The script_denylist primitive (unit A3, plan.md:666): the English-only scanner moved from eva.py. It classifies
characters by Unicode name with the word lists and code-point ranges of rules/data/scripts.json (fail closed), decodes
escapes per format (JSON always, YAML double-quoted scalars, TOML basic strings), walks JSON, reads files as text or
skips binaries, mirrors what a copy of the unit reaches, and scans one group of files for a rule. Moved bodies are
unchanged apart from the lines that read the data file and the deep-JSON fix (A3 D8, D9). Standard library only; no
import of eva or skill_eval (engine/writable.py)."""
import codecs, collections, contextlib, json, os, re, shutil, unicodedata
from engine.loaders import _quoted_end, _strip_comment

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DATA = os.path.join(ROOT, "rules", "data", "scripts.json")
TOO_DEEP = ("%s:? JSON nests too deep to walk (past the Python recursion limit), so its content cannot be verified "
            "English-only")


def read_text(fp):
    """Returns (text, error) without raising on missing/undecodable files."""
    try:
        with open(fp, encoding="utf-8") as f:
            return f.read(), None
    except (OSError, UnicodeDecodeError) as e:
        return None, "cannot read %s: %s" % (os.path.basename(fp), e)


def _within(root, target):
    return root is not None and os.path.commonpath([root, target]) == root


ENGLISH_MAX_SHOWN = 5
# FEEDBACK.md and FEEDBACK.json are what `eva.py feedback` and loop.py write into the unit.
SKILL_IGNORE = ("evals", "__pycache__", "*-workspace", ".git", "FEEDBACK.md", "FEEDBACK.json")
SKILL_TOOL_FILES = ("agents/openai.yaml",)
SKIP_DIRS = ("__pycache__", ".git")
# Run-output matchers (review, not FAIL): only the fields that are matched against what a run produced. Key paths end-match,
# list indexes are ignored. Everything else under "check" (path, fixture, tool, type, ...) is configuration and stays FAIL.
OUTPUT_MATCHER_KEYS = {"evals": (("check", "pattern"), ("check", "input_regex"), ("check", "rows")),
                       "scenarios": (("must_include",),)}
_ESC_PAIR_RE = re.compile(r"\\u(d[89ab][0-9a-f]{2})\\u(d[c-f][0-9a-f]{2})", re.I)
_ESC_RE = re.compile(r"\\\\|\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})")

def non_english_script(ch):
    """Script label ('Hangul', 'CJK', 'Cherokee', ...) when ch is a non-English-script character, else None."""
    if ord(ch) < 0x80: return None
    d = _active()
    if ch not in d.cache:
        label, cat, cp = None, unicodedata.category(ch), ord(ch)
        if cat == "Cn":
            label = "Unassigned"
        elif cat[0] not in "PZC":
            name = unicodedata.name(ch, "")
            first = name.split(" ")[0]
            hit = next((l for lo, hi, l in d.ranges if lo <= cp <= hi), None)
            if hit is None:
                hit = next((w for w in re.split(r"[ -]", name) if w in d.words), None)
            if hit is None and cat[0] in "LMN" and first not in d.compatible:
                hit = first or "Unnamed"
            if hit: label = hit if hit in ("CJK", "Katakana") else hit.title()
        d.cache[ch] = label
    return d.cache[ch]

def non_english_hits(text):
    """[(index, char, script)] of every non-English-script character in text."""
    if text.isascii(): return []
    return [(i, c, s) for i, c in enumerate(text) for s in (non_english_script(c),) if s]

def _decode_escapes(line):
    """Decodes \\uXXXX / \\UXXXXXXXX (JSON strings, double-quoted YAML/TOML strings) so escaped Hangul is still seen;
    '\\\\' stays. Callers decide WHERE the format interprets escapes (_yaml_decode_lines, _toml_decode)."""
    line = _ESC_PAIR_RE.sub(lambda m: chr(0x10000 + ((int(m.group(1), 16) - 0xD800) << 10) + int(m.group(2), 16) - 0xDC00), line)
    def one(m):
        if m.group(0) == "\\\\": return m.group(0)
        try: return chr(int(m.group(1) or m.group(2), 16))
        except (ValueError, OverflowError): return m.group(0)
    return _ESC_RE.sub(one, line)

_YAML_BLOCK_INDICATOR_RE = re.compile(r"[|>](?:[+-][1-9]?|[1-9][+-]?)?$")
_YAML_KEY_RE = re.compile(r"^(?:[ \t]*-[ \t]+)*[ \t]*(?:[^\s'\"#\[\]{},-][^:#]*?|-[^\s:][^:#]*?):(?=[ \t]|$)[ \t]*")
_YAML_ITEM_RE = re.compile(r"^(?:[ \t]*-[ \t]+)+")
_YAML_COMPLEX_KEY_RE = re.compile(r"^((?:[ \t]*-[ \t]+)*[ \t]*)\?(?:[ \t]|$)")

def _flow_scan(v, depth, quote=None):
    """(depth, open quote) of a flow collection after the text v: '[' and '{' open and ']' and '}' close outside quoted
    scalars and comments. open quote: the quote of a scalar that continues on the next line."""
    prev, i = " ", 0
    while i < len(v) and depth:
        c = v[i]
        if quote:
            if quote == '"' and c == "\\":
                i += 2; continue
            if c == quote:
                if quote == "'" and v[i + 1:i + 2] == "'":
                    i += 2; continue
                quote = None
        elif c == "#" and prev in " \t":
            break
        elif c in "\"'" and prev in " \t[{,:":
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        prev, i = c, i + 1
    return depth, quote

def _yaml_decode_value(v, start=True):
    """(value, open quote, open flow): decodes \\u escapes only inside double-quoted scalars of one YAML value (a quoted
    scalar starts a value, also after a tag or anchor). A flow collection that opens at a value start is decoded strictly
    to the end of the line; open flow is its (depth, open quote) when it continues on the next line. Plain scalars,
    single-quoted scalars and comments keep backslashes literally. open quote: the quote of a scalar that continues on
    the next line."""
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c in " \t":
            out.append(c); i += 1; continue
        if c == "#" and (i == 0 or v[i - 1] in " \t"):
            out.append(v[i:]); break
        if start and c in "!&":
            j = i
            while j < len(v) and v[j] not in " \t": j += 1
            out.append(v[i:j]); i = j; continue
        if start and c in "[{":
            flow = _flow_scan(v[i + 1:], 1)
            return "".join(out) + _decode_escapes(v[i:]), None, flow if flow[0] else None
        if start and c in "\"'":
            end = _quoted_end(v[i:])
            span = v[i:] if end < 0 else v[i:i + end + 1]
            out.append(_decode_escapes(span) if c == '"' else span)
            if end < 0: return "".join(out), c, None
            i += len(span); start = False; continue
        out.append(c); i += 1
        start = c in "[{," or (c == ":" and v[i:i + 1] in (" ", "\t", ""))
    return "".join(out), None, None

def _yaml_decode_lines(lines):
    """Lines as a YAML reader sees them for the \\u rule: escapes are decoded in double-quoted scalars only, including their
    continuation lines; the content of a '|' / '>' block scalar (the lines indented deeper than its key) and everything in
    plain or single-quoted scalars stays as written. A key starts a block scalar only when its value, comment removed, is
    the block indicator. Two forms these line rules do not model are decoded strictly instead (fail closed): a flow
    collection that opens at a value start, up to the line where it closes, and a complex-key entry ('?' and the lines
    indented deeper than it)."""
    out, block_indent, quote, flow, key_indent = [], None, None, None, None
    for ln in lines:
        if flow:
            out.append(_decode_escapes(ln))
            flow = _flow_scan(ln, *flow)
            flow = flow if flow[0] else None
            continue
        if quote:
            end = _quoted_end(quote + ln)
            head = ln if end < 0 else ln[:end]
            head = _decode_escapes(head) if quote == '"' else head
            if end < 0:
                out.append(head); continue
            tail, quote, flow = _yaml_decode_value(ln[end:], start=False)
            out.append(head + tail); continue
        indent = len(ln) - len(ln.lstrip(" \t"))
        if block_indent is not None:
            if not ln.strip() or indent > block_indent:
                out.append(ln); continue
            block_indent = None
        if key_indent is not None:
            if not ln.strip() or indent > key_indent:
                out.append(_decode_escapes(ln)); continue
            key_indent = None
        complex_key = _YAML_COMPLEX_KEY_RE.match(ln)
        if complex_key:
            key_indent = len(complex_key.group(1))
            out.append(_decode_escapes(ln)); continue
        key = _YAML_KEY_RE.match(ln)
        m = key or _YAML_ITEM_RE.match(ln)
        cut = m.end() if m else 0
        value, quote, flow = _yaml_decode_value(ln[cut:])
        out.append(ln[:cut] + value)
        if key and _YAML_BLOCK_INDICATOR_RE.match(_strip_comment(ln[cut:])):
            block_indent = len(ln) - len(ln.lstrip(" \t-"))
    return out

def _toml_decode(text):
    """text with \\u / \\U escapes decoded inside TOML basic strings ("..." and \"\"\"...\"\"\") only; literal strings
    ('...' and \'\'\'...\'\'\') and comments keep backslashes literally (TOML does not interpret escapes there)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "#":
            j = text.find("\n", i); j = n if j < 0 else j
            out.append(text[i:j]); i = j; continue
        if c in "\"'":
            delim = c * 3 if text.startswith(c * 3, i) else c
            j = i + len(delim)
            while j < n:
                if c == '"' and text[j] == "\\": j += 2; continue
                if text.startswith(delim, j):
                    j += len(delim)
                    while len(delim) == 3 and text.startswith(c, j): j += 1  # up to two extra quotes belong to the string
                    break
                if len(delim) == 1 and text[j] == "\n": break
                j += 1
            j = min(j, n)
            span = text[i:j]
            out.append(_decode_escapes(span) if c == '"' else span); i = j; continue
        out.append(c); i += 1
    return "".join(out)

def _excerpt(s, i, width=24):
    lo, hi = max(0, i - width), min(len(s), i + width)
    return ("..." if lo else "") + s[lo:hi].strip() + ("..." if hi < len(s) else "")

def _finding(rel, line, where, hits, probe):
    return "%s:%s%s [%s]: \"%s\"" % (rel, line if line else "?", " (%s)" % where if where else "",
                                     "/".join(sorted({h[2] for h in hits})), _excerpt(probe, hits[0][0]))

def _front_end(lines):
    """1-based line number of the closing '---' of a frontmatter block, 0 when there is none."""
    if lines and lines[0].strip() == "---":
        for i, ln in enumerate(lines[1:], 2):
            if ln.strip() == "---": return i
    return 0

def english_text_findings(text, rel, frontmatter=False, decode=None):
    """One finding string per line of text with non-English-script characters. frontmatter: label lines before the
    closing '---' as 'frontmatter' and the rest as 'body'; the frontmatter lines are read as YAML. decode: where \\u escapes
    are decoded before scanning: 'yaml' (double-quoted scalars only), 'toml' (basic strings only), 'strict' or True
    (everywhere; used for JSON that does not parse), None/False (never: Markdown bodies, scripts, plain text)."""
    if text.isascii() and "\\" not in text: return []
    lines = text.split("\n")
    fend = _front_end(lines) if frontmatter else 0
    mode = "strict" if decode is True else decode or None
    if mode == "yaml": probes = _yaml_decode_lines(lines)
    elif mode == "toml": probes = _toml_decode(text).split("\n")
    elif mode == "strict": probes = [_decode_escapes(x) for x in lines]
    else: probes = list(lines)
    if fend: probes[:fend] = _yaml_decode_lines(lines[:fend])
    out = []
    for i, probe in enumerate(probes, 1):
        hits = non_english_hits(probe)
        if hits: out.append(_finding(rel, i, ("frontmatter" if i <= fend else "body") if frontmatter and fend else "", hits, probe))
    return out

def _json_strings(node, matchers, path=(), matcher=False):
    """(string, is_matcher) for every string (dict keys included) of a parsed JSON value, in document order. A string is
    a matcher when the dict-key path above it ends with one of matchers (tuples of keys)."""
    if isinstance(node, str):
        yield node, matcher
    elif isinstance(node, list):
        for x in node: yield from _json_strings(x, matchers, path, matcher)
    elif isinstance(node, dict):
        for k, v in node.items():
            yield k, matcher
            p = path + (k,)
            yield from _json_strings(v, matchers, p, matcher or any(p[-len(m):] == m for m in matchers))

def english_json_findings(raw, rel, matcher_keys=()):
    """(violations, review): findings per JSON string with line numbers. Strings under the key paths matcher_keys (run-output
    matchers) go to review. Invalid JSON is scanned as plain text, matchers included (strict)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return english_text_findings(raw, rel, decode="strict"), []
    except RecursionError:
        return [TOO_DEEP % rel] + english_text_findings(raw, rel, decode="strict"), []
    if raw.isascii() and "\\u" not in raw and "\\U" not in raw: return [], []
    try:
        walked = list(_json_strings(data, matcher_keys))
    except RecursionError:
        return [TOO_DEEP % rel] + english_text_findings(raw, rel, decode="strict"), []
    lines = [_decode_escapes(x) for x in raw.split("\n")]
    viol, review, cursor = [], [], 0
    for text, is_matcher in walked:
        hits = non_english_hits(text)
        if not hits: continue
        i = hits[0][0]
        run = 1
        while run < 6 and any(h[0] == i + run for h in hits): run += 1
        needle = text[i:i + run]
        at = next((n for n in range(cursor, len(lines)) if needle in lines[n]), None)
        if at is None: at = next((n for n in range(len(lines)) if needle in lines[n]), None)
        if at is not None: cursor = at
        seg_start = text.rfind("\n", 0, i) + 1
        seg_end = text.find("\n", i)
        seg = text[seg_start:seg_end if seg_end >= 0 else len(text)]
        f = _finding(rel, None if at is None else at + 1, "", [(h[0] - seg_start,) + h[1:] for h in hits], seg)
        (review if is_matcher else viol).append(f)
    return viol, review


# Binary assets are skipped, never read as text. Text that decodes (UTF-8 or UTF-16 with BOM) is always scanned, whatever its
# extension or first bytes say. Data that does not decode is binary only when its leading bytes match a known format
# (BINARY_SIGNATURES) or its extension is a known binary format (BINARY_EXTENSIONS); otherwise it fails: it cannot be verified.

def _is_binary_format(data, fp):
    head = data[:16]
    if any(head.startswith(sig) for sig in _active().signatures): return True
    if head[:4] == b"RIFF" and data[8:12] in (b"WEBP", b"WAVE", b"AVI "): return True
    return head[4:8] == b"ftyp"

def read_scannable(fp):
    """(text, note): text is None for supported binary formats (note 'binary') and for unreadable or undecodable files
    (note = problem)."""
    try:
        with open(fp, "rb") as f: data = f.read()
    except OSError as e:
        return None, "cannot read: %s" % e
    for bom, enc in ((codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            try: return data.decode(enc), None
            except UnicodeDecodeError: return None, "not valid UTF-16 text, cannot verify English-only"
    try: return data.decode("utf-8-sig"), None  # text that decodes is always scanned (a text file may start with 'true' or 'ID3')
    except UnicodeDecodeError: pass
    if _is_binary_format(data, fp) or os.path.splitext(fp)[1].lower() in _active().extensions: return None, "binary"
    return None, ("not valid UTF-8 text and not a supported binary format (known signature or extension), "
                  "cannot verify English-only")

UnitTree = collections.namedtuple("UnitTree", "files outside cycles")

def _skip_dirs(dp, names):
    """The ignore of the agent scan: folders named in SKIP_DIRS."""
    return {n for n in names if n in SKIP_DIRS and os.path.isdir(os.path.join(dp, n))}

def unit_tree(path, ignore=None):
    """What shutil.copytree(path, dst, ignore=ignore) reaches with symlinks=False, as three lists of relative paths
    (forward slashes) in walk order: top-down, a folder's files sorted, then its subfolders sorted. ignore: a
    shutil.ignore_patterns callable applied at every depth to folders and files; default SKILL_IGNORE, what a skill run
    does not install.
    files: every path the copy holds. An internal folder link is walked and each alias is listed under its own path; file
    links are listed, and so is a dangling link whose target path is inside the unit.
    outside: links whose real path leaves the unit. Never walked, never read.
    cycles: folder links whose real path is a folder on their own path from the unit root, the root included. Never
    walked: a copy through them would never end."""
    ignore = ignore or shutil.ignore_patterns(*SKILL_IGNORE)
    root = os.path.realpath(path)
    tree, above = UnitTree([], [], []), {path: {root}}
    rel = lambda p: os.path.relpath(p, path).replace(os.sep, "/")
    for dp, dn, fn in os.walk(path, followlinks=True):
        skip, up = ignore(dp, dn + fn), above.pop(dp)
        for p in (os.path.join(dp, f) for f in sorted(fn) if f not in skip):
            (tree.files if _within(root, os.path.realpath(p)) else tree.outside).append(rel(p))
        walk = []
        for d in sorted(x for x in dn if x not in skip):
            p = os.path.join(dp, d)
            real = os.path.realpath(p)
            if not _within(root, real): tree.outside.append(rel(p))
            elif os.path.islink(p) and real in up: tree.cycles.append(rel(p))
            else:
                walk.append(d); above[p] = up | {real}
        dn[:] = walk
    return tree

LINK_OUT = "%s (link) resolves outside the unit folder, so its content cannot be verified English-only"
LINK_CYCLE = "%s (link) points back at a folder on its own path (a cycle), so a copy of the unit would never end"
LINK_DANGLING = "%s (link) points at a path that does not exist (dangling), so a copy of the unit would fail"
LINK_LOOP = "%s (link) resolves in a loop of links that never reaches a target (a resolution loop), so a copy of the unit would fail"
WRAPPER_OUT = "%s resolves outside the agent folder, so it is not read"

def _tree_view(tree, top=""):
    """(files, link findings) of a unit_tree under the folder top ("" is the whole unit): one LINK_OUT finding per
    outside link and one LINK_CYCLE finding per cycle, a link named top included."""
    pre = top and top + "/"
    return ([r for r in tree.files if r.startswith(pre)],
            [LINK_OUT % r for r in tree.outside if (r + "/").startswith(pre)]
            + [LINK_CYCLE % r for r in tree.cycles if (r + "/").startswith(pre)])

def _decode_mode(rel):
    """Escape format of a tool or wrapper file by extension: TOML basic strings or YAML double-quoted scalars."""
    ext = os.path.splitext(rel)[1].lower()
    return "toml" if ext == ".toml" else "yaml" if ext in (".yaml", ".yml") else None

def _scan_file(path, rel, frontmatter=False, decode=None):
    """(findings, status): status 'scanned', 'binary' or 'missing'. decode: see english_text_findings."""
    fp = os.path.join(path, rel)
    if not os.path.isfile(fp): return [], "missing"
    text, note = read_scannable(fp)
    if text is None:
        return ([] if note == "binary" else ["%s:? %s" % (rel, note)]), ("binary" if note == "binary" else "scanned")
    if rel.endswith(".json"): return english_json_findings(text, rel)[0], "scanned"
    return english_text_findings(text, rel, frontmatter=frontmatter, decode=decode), "scanned"

def _json_group(path, rels, key):
    """(violations, review, n_scanned) for JSON files rels; matcher keys OUTPUT_MATCHER_KEYS[key] go to review."""
    viol, review, n = [], [], 0
    for rel in rels:
        fp = os.path.join(path, rel)
        if not os.path.isfile(fp): continue
        text, note = read_scannable(fp)
        n += 1
        if text is None:
            viol.append("%s:? %s" % (rel, note)); continue
        v, rv = english_json_findings(text, rel, OUTPUT_MATCHER_KEYS[key] if isinstance(key, str) else key)
        viol += v; review += rv
    return viol, review, n

class EnglishScan:
    """groups: ordered [(check id, label, findings, scanned count or None when nothing to scan)]; review: findings in
    output matchers (needs-review); violations: flat list of every FAIL-level finding."""
    def __init__(self, kind):
        self.kind, self.groups, self.review = kind, [], []
    def add(self, cid, label, findings, scanned):
        self.groups.append((cid, label, findings, scanned))
    @property
    def violations(self):
        return [f for _, _, fs, _ in self.groups for f in fs]

def _show_findings(findings):
    return "; ".join(findings[:ENGLISH_MAX_SHOWN]) + ("; (+%d more)" % (len(findings) - ENGLISH_MAX_SHOWN)
                                                      if len(findings) > ENGLISH_MAX_SHOWN else "")

# ---------- data: rules/data/scripts.json ----------
DATA_KEYS = ("non_english_script_words", "english_compatible_first_words", "non_english_ranges", "binary_signatures",
             "binary_extensions")


class DataError(ValueError):
    pass


class Scripts:
    """One loaded scripts.json with its own classification cache, so a changed copy never sees a stale label."""
    def __init__(self, doc):
        if not isinstance(doc, dict) or set(doc) - {"$comment"} != set(DATA_KEYS):
            raise DataError("needs exactly the keys %s" % ", ".join(DATA_KEYS))
        for k in ("non_english_script_words", "english_compatible_first_words", "binary_signatures", "binary_extensions"):
            if not isinstance(doc[k], list) or not all(isinstance(x, str) and x for x in doc[k]):
                raise DataError("%s must be a list of non-empty strings" % k)
        ranges = doc["non_english_ranges"]
        if not isinstance(ranges, list) or not all(isinstance(r, dict) and set(r) == {"first", "last", "label"}
                                                   and all(isinstance(r[x], str) for x in r) for r in ranges):
            raise DataError("non_english_ranges must be a list of {first, last, label} strings")
        try:
            self.ranges = tuple((int(r["first"], 16), int(r["last"], 16), r["label"]) for r in ranges)
            self.signatures = tuple(bytes.fromhex(x) for x in doc["binary_signatures"])
        except ValueError as e:
            raise DataError("not hexadecimal: %s" % e) from None
        self.words = frozenset(doc["non_english_script_words"])
        self.compatible = frozenset(doc["english_compatible_first_words"])
        self.extensions = frozenset(doc["binary_extensions"])
        self.cache = {}


_LOADED = {}
_DATA = None


def scripts_for(path):
    """The Scripts of the file at path, one object per distinct file content (strict JSON: engine.schema.loads)."""
    from engine import schema
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise DataError("cannot read: %s" % (e.strerror or e)) from None
    if raw not in _LOADED:
        try:
            doc = schema.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, schema.JsonError) as e:
            raise DataError(str(e)) from None
        _LOADED[raw] = Scripts(doc)
    return _LOADED[raw]


def _active():
    return _DATA if _DATA is not None else scripts_for(DEFAULT_DATA)


@contextlib.contextmanager
def using(scripts):
    """Classify with scripts inside the block (the rule's own data file)."""
    global _DATA
    old, _DATA = _DATA, scripts
    try:
        yield
    finally:
        _DATA = old


# ---------- groups: one rule's files ----------
def _ignore(spec):
    if "patterns" in spec:
        return shutil.ignore_patterns(*spec["patterns"])
    dirs = set(spec["dirs"])
    return lambda dp, names: {n for n in names if n in dirs and os.path.isdir(os.path.join(dp, n))}


def _tree(path, spec, memo):
    key = ("tree", path, json.dumps(spec, sort_keys=True))
    if memo is None or key not in memo:
        tree = unit_tree(path, _ignore(spec))
        if memo is None:
            return tree
        memo[key] = tree
    return memo[key]


def _name_finding(rel):
    hits = non_english_hits(rel)
    return "%s (file name) [%s]" % (rel, "/".join(sorted({h[2] for h in hits}))) if hits else None


def _relpath_problem(rel):
    """Why a literal group path is refused at bundle load (None if fine): A1 D10, plus any '..' part."""
    from engine import schema
    if "\0" in rel: return "NUL byte"
    if ".." in rel.split("/"): return "holds '..'"
    return schema.path_problem(rel)


def _inside(path, rels):
    """(the rels that stay inside the unit, a LINK_OUT finding per rel that resolves outside it; never read)."""
    root, keep, out = os.path.realpath(path), [], []
    for rel in rels:
        fp = os.path.join(path, *rel.split("/"))
        (keep if not os.path.lexists(fp) or _within(root, os.path.realpath(fp)) else out).append(rel)
    return keep, [LINK_OUT % rel for rel in out]


def group_scan(path, spec, memo=None):
    """(label, findings, scanned count or None when nothing was scanned, review findings) of one group of a unit."""
    mode = spec["mode"]
    if mode == "main":
        text, _ = read_text(os.path.join(path, spec["file"]))
        return (spec["label"], english_text_findings(text, spec["file"], frontmatter=True) if text is not None else [],
                1 if text is not None else None, [])
    if mode == "files":
        rels, found = _inside(path, spec["files"])
        n = 0
        for rel in rels:
            f, st = _scan_file(path, rel, decode=_decode_mode(rel))
            found += f; n += st == "scanned"
        return spec["label"], found, n or None, []
    if mode == "json":
        rels, outside = _inside(path, spec["files"])
        found, review, n = _json_group(path, tuple(rels), tuple(tuple(m) for m in spec["matchers"]))
        return spec["label"], outside + found, n or None, review
    tree = _tree(path, spec["ignore"], memo)
    files, found = _tree_view(tree, spec["top"])
    if mode == "tree-json":
        depth = spec["top"].count("/") + 1
        found_json, review, n = _json_group(path, [x for x in files if x.count("/") == depth and x.endswith(".json")],
                                            tuple(tuple(m) for m in spec["matchers"]))
        found = found + found_json
        found += [x for x in map(_name_finding, files) if x]
        return spec["label"], found, n or None, review
    n, skipped = 0, 0
    for rel in files:
        if rel in spec["exclude"]: continue
        hit = _name_finding(rel)
        if hit: found.append(hit)
        f, st = _scan_file(path, rel, frontmatter=spec["frontmatter_md"] and rel.endswith(".md"), decode=_decode_mode(rel))
        found += f; n += st == "scanned"; skipped += st == "binary"
    return spec["label"].replace("{binary}", "%d" % skipped), found, n or None, []


def repository_scan(spec, folders):
    """(label, findings, scanned count or None) over the repository folders: {label: real folder}."""
    found, n = [], 0
    for label in spec["folders"]:
        top = folders.get(label)
        if not top or not os.path.isdir(top):
            continue
        files, links = _tree_view(unit_tree(top, _ignore({"dirs": list(SKIP_DIRS)})))
        found += ["%s/%s" % (label, x) for x in links]
        for rel in files:
            hit = _name_finding(rel)
            if hit: found.append("%s/%s" % (label, hit))
            f, st = _scan_file(top, rel, decode=_decode_mode(rel))
            found += ["%s/%s" % (label, x) for x in f]; n += st == "scanned"
    return spec["label"], found, n or None


def scan_unit(path, kind, groups):
    """The EnglishScan of a unit from [(rule id, applies_to, group spec, scripts)] in rule order."""
    s, memo = EnglishScan(kind), {}
    for rid, applies_to, spec, scripts in groups:
        if kind not in applies_to: continue
        with using(scripts):
            label, findings, n, review = group_scan(path, spec, memo)
        s.add(rid, label, findings, n)
        s.review += review
    return s


# ---------- the script_denylist op ----------
class RuleCheck:
    """The compiled check of one script_denylist rule. A group check scans its files; a review_of check collects the
    run-output matcher findings of the groups it names, linked after the whole bundle compiles."""
    def __init__(self, node, folder):
        rel = node["data"]
        self.data_path = os.path.join(folder, *rel.split("/"))
        if not os.path.isfile(self.data_path):
            raise DataError("'%s' is not a file in the bundle" % rel)
        try:
            self.scripts = scripts_for(self.data_path)
        except DataError as e:
            raise DataError("%s: %s" % (rel, e)) from None
        self.group, self.review_of, self.linked = node.get("group"), node.get("review_of"), None
        for key in ("files", "exclude"):
            for rel in (self.group or {}).get(key, ()):
                why = _relpath_problem(rel)
                if why:
                    raise DataError("group %s path '%s' is not allowed: %s" % (key, rel, why))

    def link(self, rules):
        if self.review_of is None: return
        missing = [r for r in self.review_of if r not in rules or getattr(rules[r].check, "group", None) is None]
        if missing:
            raise DataError("review_of names no script_denylist group rule %s" % missing)
        self.linked = [rules[r] for r in self.review_of]

    def scan(self, unit):
        key = ("script_denylist", id(self))
        if key not in unit.memo:
            with using(self.scripts):
                unit.memo[key] = group_scan(unit.path, self.group, unit.memo)
        return unit.memo[key]

    def __call__(self, unit):
        if self.group is not None:
            label, findings, n, _ = self.scan(unit)
            facts = {"label": label, "count": len(findings), "findings": _show_findings(findings)}
            if n is None and not findings:
                return "na", facts, None, False
            return ("fail" if findings else "pass"), facts, None, False
        review, keys = [], None
        for rule in self.linked:
            if unit.kind not in rule.applies_to: continue
            review += rule.check.scan(unit)[3]
            keys = keys or rule.check.group["matchers"]
        facts = {"keys": ", ".join(".".join(m) for m in keys or ()), "findings": _show_findings(review)}
        return ("review" if review else "na"), facts, None, False

    def repository(self, rules_folder, root):
        """The outcome over a repository: the bundle folder as rules/ and root's schemas/, targets/, policy/."""
        folders = {"rules": rules_folder, "schemas": os.path.join(root, "schemas"),
                   "targets": os.path.join(root, "targets"), "policy": os.path.join(root, "policy")}
        with using(self.scripts):
            label, findings, n = repository_scan(self.group, folders)
        facts = {"label": label, "count": len(findings), "findings": _show_findings(findings)}
        if n is None and not findings:
            return "na", facts, None, False
        return ("fail" if findings else "pass"), facts, None, False
