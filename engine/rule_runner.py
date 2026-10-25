"""Rule bundles: find the rule files, validate them against schemas/rules.schema.json, resolve the selectors, run the
closed op set, and add one row per rule to an eva Report (plan.md:558-560).

A bundle is a folder. Its top-level rules.json and *.rules.json files are rule files; every other file in it (data/
for example) is hashed but not loaded. An unknown or reserved op, an unknown selector, a literal path that leaves the
unit, a rule file that fails the schema, or a duplicate rule id fails the whole load with BundleError, so no unit row
is ever printed for a broken bundle. engine.schema is imported only when a bundle holds a rule file, so the empty
default bundle costs a folder listing and a hash. Standard library only; no import of eva or skill_eval
(engine/writable.py)."""
import hashlib, json, os, re, stat, unicodedata
from engine.loaders import TomlError, parse_frontmatter_ex, parse_toml, yaml_typed_keys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_SCHEMA = "schemas/rules.schema.json"
HASHED_TREES = ("schemas", "targets")
POLICY_FILES = ("policy/eval-policy.json", "policy/gates.json", "policy/gate.rules.json")
NON_DEFAULT_ID = "bundle-non-default"
RESERVED_OPS = {"heading_present": "A4", "description_heuristics": "A4", "local_refs": "A4",
                "generated_up_to_date": "B2"}
SELECTORS = ("frontmatter", "json", "toml", "yaml", "markdown", "text", "glob", "path")
NORMALIZE = {"strip": str.strip, "nfkc": lambda s: unicodedata.normalize("NFKC", s), "lower": str.lower}
OR_EMPTY = "string-or-empty"
REPOSITORY = "repository"
MAIN_FILE = {"skill": "SKILL.md", "agent": "AGENT.md"}
TEMPLATE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_-]*)\}")


def sha256_labeled(pairs, marked=()):
    """pairs: [(label, path)]; the label (path relative to the skill) is hashed with the content. marked: [(label,
    marker bytes)], hashed after the pairs with the marker in place of content."""
    h = hashlib.sha256()
    for label, p in pairs:
        h.update(label.encode() + b"\0")
        if os.path.isfile(p):
            with open(p, "rb") as f: h.update(f.read())
        else:
            h.update(b"<missing>")
        h.update(b"\0")
    for label, marker in marked:
        h.update(label.encode() + b"\0" + marker + b"\0")
    return h.hexdigest()


class BundleError(ValueError):
    def __init__(self, where, reason):
        ValueError.__init__(self, "%s: %s" % (where, reason))


class RuleError(ValueError):
    pass


class Absent:
    """A value a selector could not produce: a missing file or pointer, an unreadable or unparsable file, a path that
    leaves the unit. Every op fails on it, and `not` keeps that failure."""
    def __init__(self, reason):
        self.reason = reason


def _tree_pairs(top, prefix):
    if not os.path.lexists(top):
        return []
    if not os.path.isdir(top):
        raise BundleError(prefix, "not a folder")

    def fail(e):
        raise BundleError(prefix, "cannot list: %s" % e)
    pairs = []
    for dp, dn, fn in os.walk(top, onerror=fail):
        dn.sort()
        for n in dn + fn:
            full = os.path.join(dp, n)
            label = prefix + "/" + os.path.relpath(full, top).replace(os.sep, "/")
            mode = os.lstat(full).st_mode
            if stat.S_ISREG(mode):
                pairs.append((label, full))
            elif not stat.S_ISDIR(mode):
                raise BundleError(label, "a hashed tree may hold only regular files and folders, not a link or "
                                         "special file")
    return pairs


def ruleset_pairs(folder, root):
    """Sorted (label, path) pairs of ruleset_sha256: the bundle folder as rules/**, the engine's schemas/** and
    targets/**, and the policy files A5 names (plan.md:842), hashed as <missing> until they exist. Labels are relative,
    so one bundle hashes the same in any folder (plan.md:559, :582)."""
    pairs = _tree_pairs(folder, "rules")
    for tree in HASHED_TREES:
        pairs += _tree_pairs(os.path.join(root, tree), tree)
    pairs += [(rel, os.path.join(root, *rel.split("/"))) for rel in POLICY_FILES]
    return sorted(pairs)


class Rule:
    def __init__(self, raw, check, file="rules.json"):
        self.id, self.category, self.severity = raw["id"], raw["category"], raw["severity"]
        self.applies_to, self.message, self.fix, self.check = tuple(raw["applies_to"]), raw["message"], raw["fix"], check
        self.file, self.source, self.protects = file, raw["source"], tuple(raw["protects"])
        self.na_must = raw.get("na_must", True)

    def text(self, value, outcome, facts, fallback):
        """The message or fix for outcome: one string for every outcome, or the outcome's entry of an object."""
        if isinstance(value, dict):
            value = value.get(outcome, fallback)
        return _render(value, facts)

    def add_row(self, report, unit):
        status, facts, note, _ = self.check(unit)
        if status == "fail" and self.severity == "review":
            status = "review"
        reason = self.text(self.message, status, facts, "rule %s: %s" % (self.id, status)) + (" (%s)" % note if note else "")
        must = self.severity != "soft"
        if status == "na":
            report.na(self.id, self.category, reason, must=must and self.na_must)
        elif status == "pass":
            report.add(self.id, self.category, True, reason, must=must)
        elif status == "review":
            report.review(self.id, self.category, reason, self.text(self.fix, "review", facts, ""))
        else:
            report.add(self.id, self.category, False, reason, self.text(self.fix, "fail", facts, ""), must=must)
        report.checks[-1]["rule"] = {"file": "rules/" + self.file, "source": self.source}


class Bundle:
    def __init__(self, rules, sha256, default, display):
        self.rules, self.sha256, self.default, self.display = rules, sha256, default, display

    def files(self):
        return {rule.file for rule in self.rules}

    def apply(self, report, only=None, skip=(), dedupe=True):
        """Adds one row per rule that applies to report.kind, in bundle order. only: apply just the rules of these rule
        files. Otherwise every rule outside the files in skip, then, for a bundle given with --rules, the review row
        that keeps the verdict from ever being PASS (plan.md:560); that row comes once per report. dedupe False: a
        primitive row keeps a cause that another rule of the bundle also reports (the gate rows of eva.parity_report)."""
        rules = [r for r in self.rules if (only is None or r.file in only) and r.file not in skip]
        if rules and report.kind in MAIN_FILE:
            unit = _Unit(report.path, report.kind, dedupe)
            for rule in rules:
                if report.kind in rule.applies_to:
                    rule.add_row(report, unit)
        if only is None and not self.default and not any(c["id"] == NON_DEFAULT_ID for c in report.checks):
            report.review(NON_DEFAULT_ID, "structure", "bundle non-default %s" % self.display,
                          "Run without --rules: only the default rule bundle can give PASS.")


def load_bundle(folder=None, root=ROOT):
    """The default bundle (root/rules, empty when missing) when folder is None, else the --rules folder."""
    default = folder is None
    path = os.path.join(root, "rules") if default else folder
    if not default and not os.path.isdir(path):
        raise BundleError(folder, "not a folder")
    pairs = ruleset_pairs(path, root)
    try:
        digest = sha256_labeled(pairs)
    except OSError as e:
        where = next((label for label, p in pairs if p == e.filename), path)
        raise BundleError(where, "cannot read: %s" % (e.strerror or e)) from None
    names = sorted(n for n in os.listdir(path) if n == "rules.json" or n.endswith(".rules.json")) \
        if os.path.isdir(path) else []
    display = "rules" if default else folder
    rules = _load_rules(path, names, root, display) if names else []
    for rule in [r for r in rules if REPOSITORY in r.applies_to]:
        status, facts, note, _ = rule.check.repository(path, root)
        if status == "fail":
            raise BundleError(os.path.join(display, rule.file), "rule '%s' fails: %s" % (
                rule.id, rule.text(rule.message, status, facts, "") + (" (%s)" % note if note else "")))
    return Bundle(rules, digest, default, display)


_DEFAULT = {}


def default_bundle(root=ROOT):
    if root not in _DEFAULT:
        _DEFAULT[root] = load_bundle(root=root)
    return _DEFAULT[root]


def _load_rules(path, names, root, display):
    from engine import schema
    try:
        rules_schema = schema.compile_schema(schema.load(os.path.join(root, *RULES_SCHEMA.split("/"))))
    except (schema.JsonError, schema.SchemaRefused) as e:
        raise BundleError(RULES_SCHEMA, str(e)) from None
    rules, seen = [], {}
    for name in names:
        where = os.path.join(display, name)
        try:
            doc = schema.load(os.path.join(path, name))
        except schema.JsonError as e:
            raise BundleError(where, str(e)) from None
        errs = rules_schema.validate(doc)
        if errs:
            more = "; (+%d more)" % (len(errs) - 5) if len(errs) > 5 else ""
            raise BundleError(where, "; ".join(schema.format_error(e) for e in errs[:5]) + more)
        for raw in doc["rules"]:
            rid = raw["id"]
            if rid == NON_DEFAULT_ID:
                raise BundleError(where, "rule id '%s' is reserved for the --rules row" % rid)
            if rid in seen:
                raise BundleError(where, "duplicate rule id '%s' (also in %s)" % (rid, seen[rid]))
            seen[rid] = name
            try:
                check = compile_check(raw["check"], root, path)
                if REPOSITORY in raw["applies_to"] and not hasattr(check, "repository"):
                    raise RuleError("a repository rule needs a script_denylist group check")
            except RuleError as e:
                raise BundleError(where, "rule '%s': %s" % (rid, e)) from None
            rules.append(Rule(raw, check, name))
    by_id = {r.id: r for r in rules}
    for rule in rules:
        if hasattr(rule.check, "link"):
            try:
                rule.check.link(by_id)
            except ValueError as e:
                raise BundleError(os.path.join(display, rule.file), "rule '%s': %s" % (rule.id, e)) from None
    return rules


def compile_check(node, root=ROOT, folder=None):
    """The runnable form of one check node. The runner's own closed op table: an op outside OPS fails here even if a
    schema let it through. folder: the bundle folder, where a data/ operand resolves (default root/rules)."""
    op = node.get("op") if isinstance(node, dict) else None
    if op in RESERVED_OPS:
        raise RuleError("op '%s' is reserved until unit %s adds its primitive" % (op, RESERVED_OPS[op]))
    if op not in OPS:
        raise RuleError("unknown op '%s'" % (op,))
    return OPS[op](node, root, folder or os.path.join(root, "rules"))


def _relpath(where, rel):
    from engine import schema
    if not rel or "\0" in rel:
        raise RuleError("%s has no usable path" % where)
    why = schema.path_problem(TEMPLATE_RE.sub("x", rel))
    if why:
        raise RuleError("%s: '%s' is not allowed: %s" % (where, rel, why))
    return rel


def _pointer(where, text):
    from engine import schema
    try:
        return schema.parse_pointer(text)
    except ValueError as e:
        raise RuleError("%s: %s" % (where, e)) from None


class _Unit:
    def __init__(self, path, kind, dedupe=True):
        self.path, self.kind, self.real, self._fm, self.dedupe = path, kind, os.path.realpath(path), None, dedupe
        self.memo = {}  # scans the rules of one Bundle.apply share (script_denylist groups, parity states)

    def frontmatter(self):
        """(meta dict or Absent, typed-key set or None) of the main file."""
        if self._fm is None:
            name = MAIN_FILE[self.kind]
            text = self.read(name)
            if isinstance(text, Absent):
                self._fm = (text, None)
            else:
                meta, _, err = parse_frontmatter_ex(text)
                self._fm = (meta, set(yaml_typed_keys(text))) if meta is not None else \
                    (Absent("%s has no valid frontmatter: %s" % (name, err)), None)
        return self._fm

    def render(self, template):
        meta = self.frontmatter()[0] if TEMPLATE_RE.search(template) else {}
        missing = []

        def value(m):
            v = meta.get(m.group(1)) if isinstance(meta, dict) else None
            if not isinstance(v, str) or not v:
                missing.append(m.group(1))
                return ""
            return v
        out = TEMPLATE_RE.sub(value, template)
        if missing:
            return Absent("template key '%s' has no string value in the frontmatter" % missing[0])
        return out

    def file(self, rel):
        """The unit path of rel, or Absent when rel is not a portable relative path or resolves outside the unit."""
        from engine import schema
        rel = self.render(rel)
        if isinstance(rel, Absent):
            return rel
        why = schema.path_problem(rel) or ("NUL byte" if "\0" in rel else None)
        if why:
            return Absent("'%s' is not allowed: %s" % (rel, why))
        full = os.path.join(self.path, *rel.split("/"))
        if not schema.inside(self.real, full):
            return Absent("'%s' resolves outside the unit" % rel)
        return full

    def regular(self, rel):
        full = self.file(rel)
        if not isinstance(full, Absent) and not os.path.isfile(full):
            return Absent("'%s' is not a file in the unit" % rel)
        return full

    def read(self, rel):
        full = self.regular(rel)
        if isinstance(full, Absent):
            return full
        try:
            with open(full, encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError) as e:
            return Absent("cannot read '%s': %s" % (rel, e))

    def glob(self, pattern):
        import glob
        from engine import schema
        pattern = self.render(pattern)
        if isinstance(pattern, Absent):
            return pattern
        why = schema.path_problem(pattern) or ("NUL byte" if "\0" in pattern else None)
        if why:
            return Absent("'%s' is not allowed: %s" % (pattern, why))
        hits = glob.glob(os.path.join(glob.escape(self.path), *pattern.split("/")), recursive=True)
        return sorted(os.path.relpath(h, self.path).replace(os.sep, "/") for h in hits
                      if os.path.isfile(h) and schema.inside(self.real, h))


def _normalized(v, names):
    for n in names:
        f = NORMALIZE[n]
        if isinstance(v, str):
            v = f(v)
        elif isinstance(v, list):
            v = [f(x) if isinstance(x, str) else x for x in v]
    return v


class _Value:
    def __init__(self, value):
        self.v = value

    def value(self, unit):
        return self.v, None


class _Sel:
    def __init__(self, text, normalize=()):
        kind, colon, rest = text.partition(":")
        if not colon or kind not in SELECTORS:
            raise RuleError("unknown selector '%s'" % text)
        if OR_EMPTY in normalize[1:]:
            raise RuleError("normalize '%s' must come first (selector '%s')" % (OR_EMPTY, text))
        self.text, self.kind, self.normalize, self.rel, self.tokens = text, kind, tuple(normalize), None, []
        where = "selector '%s'" % text
        if kind == "frontmatter":
            self.tokens = _pointer(where, rest[1:] if rest.startswith("#") else rest)
        elif kind in ("json", "toml", "yaml"):
            rel, _, ptr = rest.partition("#")
            self.rel, self.tokens = _relpath(where, rel), _pointer(where, ptr)
        elif kind in ("markdown", "text", "glob"):
            self.rel = _relpath(where, rest)
        elif rest != "basename":
            raise RuleError("unknown selector '%s'" % text)

    def at(self, doc):
        from engine import schema
        v = schema.pointer_get(doc, self.tokens)
        return Absent("no value at %s" % self.text) if v is schema.ABSENT else v

    def raw(self, unit):
        from engine import schema
        if self.kind == "path":
            return os.path.basename(unit.real), None
        if self.kind == "frontmatter":
            meta, typed = unit.frontmatter()
            return (meta, None) if isinstance(meta, Absent) else (self.at(meta), None if self.tokens else typed)
        if self.kind == "glob":
            return unit.glob(self.rel), None
        text = unit.read(self.rel)
        if isinstance(text, Absent) or self.kind == "text":
            return text, None
        if self.kind == "markdown":
            meta, body, _ = parse_frontmatter_ex(text)
            return (body if meta is not None else text), None
        if self.kind == "json":
            try:
                return self.at(schema.loads(text)), None
            except schema.JsonError as e:
                return Absent("'%s' is not valid JSON: %s" % (self.rel, e)), None
        if self.kind == "toml":
            try:
                return self.at(parse_toml(text)), None
            except TomlError as e:
                return Absent("'%s' is not valid TOML: %s" % (self.rel, e)), None
        if any(ln.rstrip() == "---" for ln in text.splitlines()):
            return Absent("'%s': YAML document markers ('---') are not supported" % self.rel), None
        wrapped = "---\n%s\n---\n" % text.strip("\n")
        meta, _, err = parse_frontmatter_ex(wrapped)
        if meta is None:
            return Absent("'%s' cannot be parsed by the strict YAML reader: %s" % (self.rel, err)), None
        return self.at(meta), None if self.tokens else set(yaml_typed_keys(wrapped))

    def value(self, unit):
        v, typed = self.raw(unit)
        if self.normalize[:1] == (OR_EMPTY,):
            return _normalized(v if isinstance(v, str) else "", self.normalize[1:]), typed
        return (v, typed) if isinstance(v, Absent) else (_normalized(v, self.normalize), typed)


def _operand(spec):
    return _Value(spec["value"]) if "value" in spec else _Sel(spec["sel"], spec.get("normalize", ()))


def _sel(node):
    return _Sel(node["sel"], node.get("normalize", ()))


def _unusable(v, want=None):
    """None when v can be used, else the note an op adds to its failing row."""
    from engine import schema
    if isinstance(v, Absent):
        return "absent: %s" % v.reason
    if want is not None and not isinstance(v, want):
        return "unusable: the value is %s, not %s" % (schema.type_name(v), "a string" if want is str else "an array")
    return None


def _fail(facts, note):
    return "fail", facts, note, True


def _verdict(ok, facts, note=None):
    return ("pass" if ok else "fail"), facts, note, False


def _canon_set(items):
    from engine import schema
    return {schema.canon(x) for x in items}


def _pair(test, want):
    def build(node, root, folder=None):
        left, right = _operand(node["left"]), _operand(node["right"])

        def run(unit):
            from engine import schema
            l, r = left.value(unit)[0], right.value(unit)[0]
            facts = {"left": l, "right": r}
            note = _unusable(l, want) or _unusable(r, want)
            if note:
                return _fail(facts, note)
            try:
                return _verdict(test(l, r, schema.canon), facts)
            except schema.TooDeep:
                return _fail(facts, "unusable: the value is nested too deep")
        return run
    return build


def _string_op(test):
    def build(node, root, folder=None):
        sel, arg = _sel(node), test(node)

        def run(unit):
            v = sel.value(unit)[0]
            facts = {"value": v}
            note = _unusable(v, str)
            return _fail(facts, note) if note else _verdict(arg(v), facts)
        return run
    return build


def _c_regex(node):
    try:
        pat = re.compile(node["pattern"])
    except Exception as e:
        raise RuleError("pattern %s does not compile: %s" % (json.dumps(node["pattern"]), e)) from None
    return (lambda v: bool(pat.search(v))) if node["op"] == "regex" else (lambda v: not pat.search(v))


def _c_contains(node):
    text = node["text"]
    return lambda v: text in v


def _file_op(test):
    def build(node, root, folder=None):
        rel = _relpath("path", node["path"])

        def run(unit):
            full = unit.file(rel)
            facts = {"path": rel}
            note = _unusable(full)
            return _fail(facts, note) if note else _verdict(test(full), facts)
        return run
    return build


def _size_op(measure):
    def build(node, root, folder=None):
        rel, limit = _relpath("path", node["path"]), node["max"]

        def run(unit):
            got = measure(unit, rel)
            facts = {"path": rel, "size": got}
            note = _unusable(got)
            return _fail(facts, note) if note else _verdict(got <= limit, facts)
        return run
    return build


def _bytes_of(unit, rel):
    full = unit.regular(rel)
    return full if isinstance(full, Absent) else os.path.getsize(full)


def _lines_of(unit, rel):
    text = unit.read(rel)
    return text if isinstance(text, Absent) else len(text.splitlines())


def _c_count(node, root, folder=None):
    sel, lo, hi = _sel(node), node.get("min"), node.get("max")

    def run(unit):
        v = sel.value(unit)[0]
        note = _unusable(v, list)
        if note:
            return _fail({"value": v}, note)
        n = len(v)
        return _verdict((lo is None or n >= lo) and (hi is None or n <= hi), {"value": v, "count": n})
    return run


def _c_schema(node, root, folder=None):
    from engine import schema
    name = node["schema"]
    fp = os.path.join(root, "schemas", name)
    if not re.match(r"^[a-z0-9][a-z0-9-]*\.schema\.json$", name) or not os.path.isfile(fp):
        raise RuleError("schema '%s' is not a file in the engine's schemas/ folder" % name)
    try:
        compiled = schema.compile_schema(schema.load(fp))
    except (schema.JsonError, schema.SchemaRefused) as e:
        raise RuleError("schema '%s': %s" % (name, e)) from None
    sel = _sel(node)

    def run(unit):
        v, typed = sel.value(unit)
        note = _unusable(v)
        if note:
            return _fail({"value": v}, note)
        errs = compiled.validate(v, unit_root=unit.path, typed_keys=typed)
        detail = "; ".join(schema.format_error(e) for e in errs[:3]) + ("; (+%d more)" % (len(errs) - 3) if len(errs) > 3 else "")
        return _verdict(not errs, {"value": v, "errors": detail}, detail or None)
    return run


def _children(node, root, folder):
    return [compile_check(c, root, folder) for c in node["of"]]


def _first(outs, status):
    """The first child result of status, an unusable one first, so a combinator's fail stays unusable in any order."""
    hits = [o for o in outs if o[0] == status]
    return next((o for o in hits if o[3]), hits[0]) if hits else None


def _c_all(node, root, folder=None):
    kids = _children(node, root, folder)

    def run(unit):
        outs = [k(unit) for k in kids]
        for status in ("fail", "pass"):
            hit = _first(outs, status)
            if hit:
                return hit
        return "na", {}, "every check under all is n/a", False
    return run


def _c_any(node, root, folder=None):
    kids = _children(node, root, folder)

    def run(unit):
        outs = [k(unit) for k in kids]
        for status in ("pass", "fail"):
            hit = _first(outs, status)
            if hit:
                return hit
        return "na", {}, "every check under any is n/a", False
    return run


def _c_not(node, root, folder=None):
    kid = compile_check(node["check"], root, folder)

    def run(unit):
        status, facts, note, unusable = kid(unit)
        if status == "pass":
            return "fail", facts, "the check under not passed", False
        if status == "fail" and not unusable:
            return "pass", facts, None, False
        return status, facts, note, unusable
    return run


def _c_when(node, root, folder=None):
    cond, then = compile_check(node["if"], root, folder), compile_check(node["then"], root, folder)

    def run(unit):
        status, facts, _, _ = cond(unit)
        return then(unit) if status == "pass" else ("na", facts, "the when condition is not met", False)
    return run


OPS = {
    "schema": _c_schema,
    "equals": _pair(lambda l, r, canon: canon(l) == canon(r), None),
    "subset": _pair(lambda l, r, canon: _canon_set(l) <= _canon_set(r), list),
    "set_equals": _pair(lambda l, r, canon: _canon_set(l) == _canon_set(r), list),
    "contains_text": _string_op(_c_contains),
    "regex": _string_op(_c_regex),
    "not_regex": _string_op(_c_regex),
    "file_exists": _file_op(os.path.isfile),
    "file_absent": _file_op(lambda full: not os.path.lexists(full)),
    "count": _c_count,
    "max_bytes": _size_op(_bytes_of),
    "max_lines": _size_op(_lines_of),
    "all": _c_all,
    "any": _c_any,
    "not": _c_not,
    "when": _c_when,
}


def _primitive(name):
    def build(node, root, folder=None):
        if name == "script_denylist":
            from engine.primitives import script_denylist as mod
            make = mod.RuleCheck
            args = (node, folder or os.path.join(root, "rules"))
        else:
            from engine.primitives import parity as mod
            make = mod.OPS[name]
            args = (node, root)
        try:
            return make(*args)
        except ValueError as e:
            raise RuleError(str(e)) from None
    return build


for _name in ("script_denylist", "parity_skill", "parity_invocation", "parity_agent_name", "parity_wrapper",
              "parity_permissions"):
    OPS[_name] = _primitive(_name)


def _render(message, facts):
    def value(m):
        if m.group(1) not in facts:
            return m.group(0)
        v = facts[m.group(1)]
        if isinstance(v, Absent):
            return "<absent>"
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return TEMPLATE_RE.sub(value, message)
