"""Fail-closed validator for a JSON Schema 2020-12 subset plus six x- keywords (plan.md:557, assessment section 3.3).

compile_schema() checks every keyword of every subschema first: an unsupported keyword, a malformed keyword value, a
non-local or dangling $ref, a $ref cycle that never consumes the instance, or nesting deeper than MAX_DEPTH refuses the
whole schema, naming the keyword and its schema pointer. Schema.validate() then returns instance errors as
(path, keyword, message). JSON is read strictly: duplicate keys and NaN or Infinity are errors.

Deliberate differences from 2020-12: `integer` excludes 1.0 and booleans, `number` excludes booleans (eva.py:1860);
`then`/`else` without `if` and an empty `enum` refuse the schema. Standard library only; no import of eva or
skill_eval (engine/writable.py)."""
import json, os, posixpath, re, sys

DIALECT = "https://json-schema.org/draft/2020-12/schema"
MAX_DEPTH = 128
TYPES = ("null", "boolean", "object", "array", "number", "string", "integer")
WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class JsonError(ValueError):
    pass


class SchemaRefused(ValueError):
    def __init__(self, pointer, keyword, reason):
        ValueError.__init__(self, "%s: %s: %s" % (pointer, keyword, reason))
        self.pointer, self.keyword, self.reason = pointer, keyword, reason


class TooDeep(Exception):
    def __init__(self, path=None):
        Exception.__init__(self)
        self.path = path


class _NoContext(Exception):
    """A context keyword ran without its context. It ends the whole validation, so not, if, oneOf, anyOf or a
    count bound can never turn it into a pass (D6, fail closed)."""
    def __init__(self, error):
        Exception.__init__(self)
        self.error = error


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise JsonError("duplicate key %s" % json.dumps(k))
        out[k] = v
    return out


def _constant(name):
    raise JsonError("%s is not valid JSON" % name)


def loads(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except RecursionError:
        raise JsonError("nesting is too deep") from None
    except json.JSONDecodeError as e:
        raise JsonError(str(e)) from None


def load(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise JsonError("cannot read: %s" % (e.strerror or e)) from None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise JsonError("not UTF-8: %s" % e) from None
    return loads(text)


def _esc(token):
    return str(token).replace("~", "~0").replace("/", "~1")


def pointer(path):
    return "#" + "".join("/" + _esc(t) for t in path)


def parse_pointer(text):
    """Tokens of an RFC 6901 pointer ('' or '/a/b~1c'); raises ValueError on any other form or a bad '~' escape."""
    if text == "":
        return []
    if not text.startswith("/"):
        raise ValueError("pointer %r must be empty or start with '/'" % text)
    out = []
    for tok in text[1:].split("/"):
        if re.search(r"~(?![01])", tok):
            raise ValueError("pointer %r has a '~' that is not '~0' or '~1'" % text)
        out.append(tok.replace("~1", "/").replace("~0", "~"))
    return out


class _Absent:
    pass


ABSENT = _Absent()


def pointer_get(doc, tokens):
    """The value at tokens inside doc, or ABSENT."""
    for tok in tokens:
        if isinstance(doc, dict):
            if tok not in doc:
                return ABSENT
            doc = doc[tok]
        elif isinstance(doc, list):
            if not re.match(r"^(?:0|[1-9][0-9]*)$", tok) or int(tok) >= len(doc):
                return ABSENT
            doc = doc[int(tok)]
        else:
            return ABSENT
    return doc


def path_problem(ref):
    """Why ref is not a portable relative path inside a unit (None if fine); the rules of eva.py _ref_problem."""
    if "\\" in ref: return "backslash (use forward slashes)"
    if ref.startswith("/") or ref.startswith("~") or WIN_DRIVE_RE.match(ref): return "absolute or drive path (use a path relative to the skill root)"
    norm = posixpath.normpath(ref)
    if norm == ".." or norm.startswith("../"): return "points outside the skill directory"
    return None


def inside(root_real, path):
    """True when the realpath of path is root_real (a realpath) or below it."""
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real.rstrip(os.sep) + os.sep)


def type_name(v):
    if v is None: return "null"
    if isinstance(v, bool): return "boolean"
    if isinstance(v, int): return "integer"
    if isinstance(v, float): return "number"
    if isinstance(v, str): return "string"
    if isinstance(v, list): return "array"
    return "object"


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_type(v, t):
    if t == "integer": return isinstance(v, int) and not isinstance(v, bool)
    if t == "number": return is_number(v)
    return type_name(v) == t


def canon(v, depth=0):
    """Hashable JSON value for type-aware equality: 1 == 1.0, but true, 1 and "1" stay distinct."""
    if depth > MAX_DEPTH:
        raise TooDeep()
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return (type_name(v), v)
    if isinstance(v, (int, float)):
        return ("number", int(v) if isinstance(v, float) and v.is_integer() else v)
    if isinstance(v, list):
        return ("array", tuple(canon(x, depth + 1) for x in v))
    return ("object", frozenset((k, canon(x, depth + 1)) for k, x in v.items()))


def _show(v, limit=60):
    if isinstance(v, str) and len(v) > limit:
        v = v[:limit] + "..."
    return json.dumps(v)


def _nonneg_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


class _Compiler:
    def __init__(self, doc):
        self.nodes, self.refs, self.edges = {}, [], {}
        self.schema(doc, (), 0)
        for site, ref in self.refs:
            target = self.ref_target(site, ref)
            self.edges.setdefault(pointer(site), []).append(target)
        self.check_cycles()

    def refuse(self, path, keyword, reason):
        raise SchemaRefused(pointer(path), keyword, reason)

    def schema(self, node, path, depth):
        if depth > MAX_DEPTH:
            self.refuse(path, "depth", "schema nesting deeper than %d levels" % MAX_DEPTH)
        self.nodes[pointer(path)] = node
        if isinstance(node, bool):
            return
        if not isinstance(node, dict):
            self.refuse(path, "schema", "a schema must be an object or a boolean")
        for kw in ("then", "else"):
            if kw in node and "if" not in node:
                self.refuse(path, kw, "needs an 'if' keyword in the same schema")
        for kw, val in node.items():
            check = _CHECKS.get(kw)
            if check is None:
                self.refuse(path, kw, "unsupported keyword")
            check(self, kw, val, path, depth)

    def sub(self, val, path, depth, parent, same):
        self.schema(val, path, depth + 1)
        if same:
            self.edges.setdefault(pointer(parent), []).append(pointer(path))

    def ref_target(self, site, ref):
        if ref != "#" and not ref.startswith("#/") or "%" in ref:
            self.refuse(site, "$ref", "only local references ('#' or '#/<pointer>') are supported, got %s" % _show(ref))
        try:
            tokens = parse_pointer(ref[1:])
        except ValueError:
            tokens = None
        target = pointer(tokens) if tokens is not None else None
        if target not in self.nodes:
            self.refuse(site, "$ref", "%s does not point at a schema in this document" % _show(ref))
        return target

    def check_cycles(self):
        state = {}
        for start in self.edges:
            if state.get(start):
                continue
            stack = [(start, iter(self.edges.get(start, ())))]
            state[start] = 1
            while stack:
                node, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    state[node] = 2
                    stack.pop()
                elif state.get(nxt) == 1:
                    raise SchemaRefused(node, "$ref", "reference cycle through %s that never consumes the instance" % nxt)
                elif not state.get(nxt):
                    state[nxt] = 1
                    stack.append((nxt, iter(self.edges.get(nxt, ()))))


def _c_annotation_string(c, kw, val, path, depth):
    if not isinstance(val, str): c.refuse(path, kw, "must be a string")


def _c_any(c, kw, val, path, depth):
    pass


def _c_examples(c, kw, val, path, depth):
    if not isinstance(val, list): c.refuse(path, kw, "must be an array")


def _c_dialect(c, kw, val, path, depth):
    if val != DIALECT: c.refuse(path, kw, "only %s is supported" % DIALECT)


def _c_id(c, kw, val, path, depth):
    if path: c.refuse(path, kw, "allowed only at the schema root")
    if not isinstance(val, str): c.refuse(path, kw, "must be a string")


def _c_ref(c, kw, val, path, depth):
    if not isinstance(val, str): c.refuse(path, kw, "must be a string")
    c.refs.append((path, val))


def _c_type(c, kw, val, path, depth):
    names = val if isinstance(val, list) else [val]
    if not names or len(set(map(str, names))) != len(names) or not all(isinstance(t, str) and t in TYPES for t in names):
        c.refuse(path, kw, "must be a type name or a non-empty array of unique type names from %s" % (TYPES,))


def _c_enum(c, kw, val, path, depth):
    if not isinstance(val, list) or not val: c.refuse(path, kw, "must be a non-empty array")


def _c_required(c, kw, val, path, depth):
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val) or len(set(val)) != len(val):
        c.refuse(path, kw, "must be an array of unique strings")


def _c_schema_map(c, kw, val, path, depth):
    if not isinstance(val, dict): c.refuse(path, kw, "must be an object of schemas")
    for name, sub in val.items():
        if kw == "patternProperties":
            try:
                re.compile(name)
            except Exception as e:
                c.refuse(path, kw, "pattern %s does not compile: %s" % (_show(name), e))
        c.sub(sub, path + (kw, name), depth, path, False)


def _schema_one(same):
    def check(c, kw, val, path, depth):
        c.sub(val, path + (kw,), depth, path, same)
    return check


def _c_items(c, kw, val, path, depth):
    if isinstance(val, list): c.refuse(path, kw, "the array form is not supported (use prefixItems)")
    c.sub(val, path + (kw,), depth, path, False)


def _schema_list(same):
    def check(c, kw, val, path, depth):
        if not isinstance(val, list) or not val: c.refuse(path, kw, "must be a non-empty array of schemas")
        for i, sub in enumerate(val):
            c.sub(sub, path + (kw, i), depth, path, same)
    return check


def _c_count(c, kw, val, path, depth):
    if not _nonneg_int(val): c.refuse(path, kw, "must be a non-negative integer")


def _c_number(c, kw, val, path, depth):
    if not is_number(val): c.refuse(path, kw, "must be a number")


def _c_bool(c, kw, val, path, depth):
    if not isinstance(val, bool): c.refuse(path, kw, "must be a boolean")


def _c_pattern(c, kw, val, path, depth):
    if not isinstance(val, str): c.refuse(path, kw, "must be a string")
    try:
        re.compile(val)
    except Exception as e:
        c.refuse(path, kw, "pattern %s does not compile: %s" % (_show(val), e))


def _c_true(c, kw, val, path, depth):
    if val is not True: c.refuse(path, kw, "must be true")


def _c_unique_by(c, kw, val, path, depth):
    if not isinstance(val, str) or not val: c.refuse(path, kw, "must be a non-empty string")


def _c_count_where(c, kw, val, path, depth):
    shape = "must be an object with 'where' and at least one of 'min' and 'max', non-negative integers with min <= max"
    if not isinstance(val, dict) or "where" not in val or not ({"min", "max"} & set(val)):
        c.refuse(path, kw, shape)
    for k in val:
        if k not in ("where", "min", "max"): c.refuse(path, kw, "unknown member %s; %s" % (_show(k), shape))
    if not all(_nonneg_int(val[k]) for k in ("min", "max") if k in val): c.refuse(path, kw, shape)
    if "min" in val and "max" in val and val["min"] > val["max"]: c.refuse(path, kw, "min is greater than max")
    c.sub(val["where"], path + (kw, "where"), depth, path, False)


def _c_le(c, kw, val, path, depth):
    if not (isinstance(val, list) and len(val) == 2 and all(isinstance(x, str) for x in val) and val[0] != val[1]):
        c.refuse(path, kw, "must be an array of two different property names")


def _c_path_exists(c, kw, val, path, depth):
    if val != "unit": c.refuse(path, kw, 'must be "unit"')


class _Validator:
    def __init__(self, compiled, unit_root, typed_keys):
        self.nodes = compiled.nodes
        self.unit_root = unit_root
        self.unit_real = os.path.realpath(unit_root) if unit_root is not None else None
        self.typed = typed_keys

    def run(self, node, inst, path, depth, errs):
        if depth > MAX_DEPTH:
            raise TooDeep(path)
        if node is True:
            return
        if node is False:
            errs.append((path, "false", "no value is allowed here"))
            return
        for kw, val in node.items():
            fn = _APPLY.get(kw)
            if fn is not None:
                fn(self, kw, val, node, inst, path, depth, errs)

    def ok(self, node, inst, path, depth):
        errs = []
        self.run(node, inst, path, depth + 1, errs)
        return not errs


def _a_type(v, kw, val, node, inst, path, depth, errs):
    names = val if isinstance(val, list) else [val]
    if not any(_is_type(inst, t) for t in names):
        errs.append((path, kw, "expected %s, got %s" % (" or ".join(names), type_name(inst))))


def _a_enum(v, kw, val, node, inst, path, depth, errs):
    c = canon(inst)
    if not any(c == canon(x) for x in val):
        errs.append((path, kw, "value %s is not one of %s" % (_show(inst), json.dumps(val))))


def _a_const(v, kw, val, node, inst, path, depth, errs):
    if canon(inst) != canon(val):
        errs.append((path, kw, "value %s is not %s" % (_show(inst), json.dumps(val))))


def _a_required(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, dict):
        errs.extend((path, kw, "required property '%s' is missing" % k) for k in val if k not in inst)


def _a_properties(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, dict):
        for k, sub in val.items():
            if k in inst:
                v.run(sub, inst[k], path + (k,), depth + 1, errs)


def _a_pattern_properties(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, dict):
        for pat, sub in val.items():
            for k in inst:
                if re.search(pat, k):
                    v.run(sub, inst[k], path + (k,), depth + 1, errs)


def _a_additional(v, kw, val, node, inst, path, depth, errs):
    if not isinstance(inst, dict):
        return
    named, pats = node.get("properties", {}), list(node.get("patternProperties", {}))
    for k in inst:
        if k in named or any(re.search(p, k) for p in pats):
            continue
        if val is False:
            errs.append((path, kw, "property '%s' is not allowed" % k))
        else:
            v.run(val, inst[k], path + (k,), depth + 1, errs)


def _a_items(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, list):
        for i in range(len(node.get("prefixItems", ())), len(inst)):
            v.run(val, inst[i], path + (i,), depth + 1, errs)


def _a_prefix_items(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, list):
        for i, sub in enumerate(val[:len(inst)]):
            v.run(sub, inst[i], path + (i,), depth + 1, errs)


def _a_min_items(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, list) and len(inst) < val:
        errs.append((path, kw, "array has %d item(s), fewer than %d" % (len(inst), val)))


def _a_max_items(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, list) and len(inst) > val:
        errs.append((path, kw, "array has %d item(s), more than %d" % (len(inst), val)))


def _a_unique(v, kw, val, node, inst, path, depth, errs):
    if val and isinstance(inst, list):
        seen = {}
        for i, x in enumerate(inst):
            c = canon(x)
            if c in seen:
                errs.append((path, kw, "items %d and %d are equal" % (seen[c], i)))
                return
            seen[c] = i


def _a_min_length(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, str) and len(inst) < val:
        errs.append((path, kw, "string has %d character(s), fewer than %d" % (len(inst), val)))


def _a_max_length(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, str) and len(inst) > val:
        errs.append((path, kw, "string has %d character(s), more than %d" % (len(inst), val)))


def _a_pattern(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, str) and not re.search(val, inst):
        errs.append((path, kw, "string %s does not match pattern %s" % (_show(inst), json.dumps(val))))


def _a_minimum(v, kw, val, node, inst, path, depth, errs):
    if is_number(inst) and inst < val:
        errs.append((path, kw, "%s is less than the minimum %s" % (json.dumps(inst), json.dumps(val))))


def _a_maximum(v, kw, val, node, inst, path, depth, errs):
    if is_number(inst) and inst > val:
        errs.append((path, kw, "%s is greater than the maximum %s" % (json.dumps(inst), json.dumps(val))))


def _a_all_of(v, kw, val, node, inst, path, depth, errs):
    for sub in val:
        v.run(sub, inst, path, depth + 1, errs)


def _a_any_of(v, kw, val, node, inst, path, depth, errs):
    if not any([v.ok(sub, inst, path, depth) for sub in val]):  # no short-circuit: order must not hide _NoContext
        errs.append((path, kw, "no subschema matched"))


def _a_one_of(v, kw, val, node, inst, path, depth, errs):
    n = sum(v.ok(sub, inst, path, depth) for sub in val)
    if n != 1:
        errs.append((path, kw, "%d subschemas matched, exactly one must" % n))


def _a_not(v, kw, val, node, inst, path, depth, errs):
    if v.ok(val, inst, path, depth):
        errs.append((path, kw, "value matches the schema under not"))


def _a_if(v, kw, val, node, inst, path, depth, errs):
    branch = "then" if v.ok(val, inst, path, depth) else "else"
    if branch in node:
        v.run(node[branch], inst, path, depth + 1, errs)


def _a_ref(v, kw, val, node, inst, path, depth, errs):
    v.run(v.nodes[pointer(parse_pointer(val[1:]))], inst, path, depth + 1, errs)


def _a_python_regex(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, str):
        try:
            re.compile(inst)
        except Exception as e:
            errs.append((path, kw, "string does not compile as a Python regex: %s" % e))


def _a_unique_by(v, kw, val, node, inst, path, depth, errs):
    if isinstance(inst, list):
        seen = {}
        for i, item in enumerate(inst):
            if isinstance(item, dict) and val in item:
                c = canon(item[val])
                if c in seen:
                    errs.append((path, kw, "duplicate %s %s at items %d and %d" % (val, _show(item[val]), seen[c], i)))
                else:
                    seen[c] = i


def _a_count_where(v, kw, val, node, inst, path, depth, errs):
    if not isinstance(inst, list):
        return
    n = sum(v.ok(val["where"], x, path + (i,), depth) for i, x in enumerate(inst))
    lo, hi = val.get("min"), val.get("max")
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        need = "%d to %d" % (lo, hi) if lo is not None and hi is not None else \
            "at least %d" % lo if lo is not None else "at most %d" % hi
        errs.append((path, kw, "%d item(s) match where, need %s" % (n, need)))


def _a_le(v, kw, val, node, inst, path, depth, errs):
    a, b = val
    if isinstance(inst, dict) and a in inst and b in inst and is_number(inst[a]) and is_number(inst[b]) \
            and inst[a] > inst[b]:
        errs.append((path, kw, "'%s' (%s) is greater than '%s' (%s)" % (a, json.dumps(inst[a]), b, json.dumps(inst[b]))))


def _a_path_exists(v, kw, val, node, inst, path, depth, errs):
    if not isinstance(inst, str):
        return
    if v.unit_root is None:
        raise _NoContext((path, kw, "no unit root given, so the path cannot be checked (fail closed)"))
    why = path_problem(inst)
    full = os.path.join(v.unit_root, *inst.split("/")) if why is None else None
    if why is not None:
        errs.append((path, kw, "%s is not allowed: %s" % (_show(inst), why)))
    elif not inside(v.unit_real, full):
        errs.append((path, kw, "'%s' resolves outside the unit" % inst))
    elif not os.path.isfile(full):
        errs.append((path, kw, "'%s' is not a regular file in the unit" % inst))


def _typed_name(path):
    if len(path) == 1 and isinstance(path[0], str):
        return path[0]
    if len(path) == 2 and isinstance(path[0], str):
        return path[0] + ("[]" if isinstance(path[1], int) else "." + path[1])
    return None


def _a_yaml_string(v, kw, val, node, inst, path, depth, errs):
    if not isinstance(inst, str):
        return
    if v.typed is None:
        raise _NoContext((path, kw, "no typed-key annotation given, so YAML typing cannot be checked (fail closed)"))
    name = _typed_name(path)
    if name is None:
        errs.append((path, kw, "pointer has no frontmatter key name"))
    elif name in v.typed:
        errs.append((path, kw, "value at '%s' is typed by YAML 1.1, not a string (quote it)" % name))


_CHECKS = {
    "$schema": _c_dialect, "$id": _c_id, "$comment": _c_annotation_string, "title": _c_annotation_string,
    "description": _c_annotation_string, "default": _c_any, "examples": _c_examples,
    "type": _c_type, "enum": _c_enum, "const": _c_any, "required": _c_required,
    "properties": _c_schema_map, "additionalProperties": _schema_one(False), "patternProperties": _c_schema_map,
    "items": _c_items, "prefixItems": _schema_list(False), "minItems": _c_count, "maxItems": _c_count,
    "uniqueItems": _c_bool, "minLength": _c_count, "maxLength": _c_count, "pattern": _c_pattern,
    "minimum": _c_number, "maximum": _c_number, "allOf": _schema_list(True), "anyOf": _schema_list(True),
    "oneOf": _schema_list(True), "not": _schema_one(True), "if": _schema_one(True), "then": _schema_one(True),
    "else": _schema_one(True), "$ref": _c_ref, "$defs": _c_schema_map,
    "x-python-regex": _c_true, "x-unique-by": _c_unique_by, "x-count-where": _c_count_where, "x-le": _c_le,
    "x-path-exists": _c_path_exists, "x-yaml-string": _c_true,
}
_APPLY = {
    "type": _a_type, "enum": _a_enum, "const": _a_const, "required": _a_required, "properties": _a_properties,
    "additionalProperties": _a_additional, "patternProperties": _a_pattern_properties, "items": _a_items,
    "prefixItems": _a_prefix_items, "minItems": _a_min_items, "maxItems": _a_max_items, "uniqueItems": _a_unique,
    "minLength": _a_min_length, "maxLength": _a_max_length, "pattern": _a_pattern, "minimum": _a_minimum,
    "maximum": _a_maximum, "allOf": _a_all_of, "anyOf": _a_any_of, "oneOf": _a_one_of, "not": _a_not, "if": _a_if,
    "$ref": _a_ref, "x-python-regex": _a_python_regex, "x-unique-by": _a_unique_by, "x-count-where": _a_count_where,
    "x-le": _a_le, "x-path-exists": _a_path_exists, "x-yaml-string": _a_yaml_string,
}
ANNOTATIONS = ("$schema", "$id", "$comment", "title", "description", "default", "examples")
CUSTOM = ("x-python-regex", "x-unique-by", "x-count-where", "x-le", "x-path-exists", "x-yaml-string")
SUPPORTED = tuple(k for k in _CHECKS if k not in ANNOTATIONS and k not in CUSTOM)


class Schema:
    def __init__(self, doc):
        self.nodes = _Compiler(doc).nodes
        self.doc = doc

    def validate(self, instance, unit_root=None, typed_keys=None):
        """[(path tuple, keyword, message)]; [] when valid. typed_keys: the yaml_typed_keys names, or None."""
        errs = []
        try:
            _Validator(self, unit_root, typed_keys).run(self.doc, instance, (), 0, errs)
        except TooDeep as e:
            return [(e.path or (), "depth", "nesting deeper than %d levels" % MAX_DEPTH)]
        except _NoContext as e:
            return [e.error]
        return errs


def compile_schema(doc):
    return Schema(doc)


def format_error(error):
    path, keyword, message = error
    return "%s: %s: %s" % (pointer(path), keyword, message)


def cli(schema_path, instance_path, unit=None):
    """python3 eva.py schema-check: exit 0 valid, 1 invalid instance, 2 refused schema or unreadable input."""
    try:
        doc = load(schema_path)
    except JsonError as e:
        print("schema-check: %s: %s" % (schema_path, e), file=sys.stderr)
        return 2
    try:
        compiled = compile_schema(doc)
    except SchemaRefused as e:
        print("schema refused: %s" % e, file=sys.stderr)
        return 2
    try:
        inst = load(instance_path)
    except JsonError as e:
        print("schema-check: %s: %s" % (instance_path, e), file=sys.stderr)
        return 2
    if unit is not None and not os.path.isdir(unit):
        print("schema-check: %s: the unit is not a folder" % unit, file=sys.stderr)
        return 2
    errs = compiled.validate(inst, unit_root=unit)
    for e in errs:
        print("error %s" % format_error(e))
    if not errs:
        print("schema-check: valid")
    return 1 if errs else 0
