#!/usr/bin/env python3
"""Eva: SKILL.md skill evaluator (agent checks are slated to move to a separate "agent master").
stdlib only, Python 3.9+."""
import argparse, codecs, collections, json, os, posixpath, re, shlex, shutil, sys, unicodedata
from engine.loaders import (TomlError, _DQ_ESCAPES, _DQ_HEX, _TOML_BARE_KEY_RE, _TOML_ESCAPES, _TOML_FLOAT_RE, _TOML_INT_RE,
                            _TomlReader, _YAML_TYPED_RE, _block_scalar, _plain_scalar_error, _quoted_end, _scalar,
                            _strip_comment, _unquote, parse_flat_toml, parse_frontmatter_ex, yaml_typed_keys)
from engine import registry, rule_runner
from engine.primitives import description_heuristics, heading_present, local_refs as refs_primitive
from engine.primitives import parity as parity_primitive, script_denylist, transcript as transcript_primitive
from engine.primitives.transcript import (CMD_KEYS, CWD_KEYS, FILELINE_RE, GIT_HISTORY_RE, GREP_LINE_RE, NOT_READ_TOOL_WORDS,
                                          NUMBERED_LINE_RE, PATH_KEYS, READ_TOOL_WORDS, SHELL_META_RE, SHELL_TOOL_WORDS,
                                          TABLE_RE, URL_RE, WRITE_CMD_RE, WRITE_WORDS, _GREP_FLAG_LETTERS, _call_inputs,
                                          _grep_lines, _norm_path, _norm_url, _numbered_lines, _numbered_read_cmd,
                                          _outside, _read_call, _result_lines, _strings, _tool_words,
                                          answer_lookup_violations, lines_seen, write_calls)
from engine.primitives.script_denylist import (ENGLISH_MAX_SHOWN, LINK_CYCLE, LINK_DANGLING, LINK_LOOP, LINK_OUT, WRAPPER_OUT,
                                               EnglishScan, UnitTree, _ESC_PAIR_RE, _ESC_RE, _YAML_BLOCK_INDICATOR_RE,
                                               _YAML_COMPLEX_KEY_RE, _YAML_ITEM_RE, _YAML_KEY_RE, _decode_escapes,
                                               _decode_mode, _excerpt, _finding, _flow_scan, _front_end, _is_binary_format,
                                               _json_group, _json_strings, _scan_file, _show_findings, _skip_dirs,
                                               _toml_decode, _tree_view, _yaml_decode_lines, _yaml_decode_value,
                                               english_json_findings, english_text_findings, non_english_hits,
                                               non_english_script, read_scannable, unit_tree)

PASS_THRESHOLD = 0.8
CATEGORY_FLOOR = 0.5  # every category with applicable checks needs >=1 pass and pass rate >= this
NAME_MAX = 64
RESERVED_NAME_WORDS = ("anthropic", "claude")  # Claude skill authoring best practices: name "cannot contain"


# ---------- description heuristics (engine/primitives/description_heuristics.py, rules/data/description.json) ----------
desc_when_clause = description_heuristics.when_clause
desc_person_words = description_heuristics.person_words
desc_task_verbs = description_heuristics.task_verbs

# ---------- frontmatter (minimal YAML subset) ----------

def parse_frontmatter(text):
    """Returns (meta dict or None, body)."""
    meta, body, _ = parse_frontmatter_ex(text)
    return meta, body

def read_text(fp):
    """Returns (text, error) without raising on missing/undecodable files."""
    try:
        with open(fp, encoding="utf-8") as f:
            return f.read(), None
    except (OSError, UnicodeDecodeError) as e:
        return None, "cannot read %s: %s" % (os.path.basename(fp), e)

def as_list(v):
    if v is None or v == "": return []
    return v if isinstance(v, list) else [v]

headings = heading_present.headings
has_section = heading_present.has_section

class Report:
    """checks: status "pass"/"fail" (passed True/False), "n/a" (absent optional item) or "needs-review"
    (criterion a machine cannot check; a human or model grader must). n/a and needs-review have passed None and are
    excluded from score and category floor; any needs-review item turns an otherwise passing verdict into REVIEW."""
    def __init__(self, path, kind):
        self.path, self.kind, self.checks = path, kind, []
    def add(self, cid, category, ok, reason, fix="", must=False):
        self.checks.append({"id": cid, "category": category, "status": "pass" if ok else "fail", "passed": bool(ok),
                            "must": must or cid in CRITICAL, "reason": reason, "fix": "" if ok else fix})
    def na(self, cid, category, reason, must=False):
        self.checks.append({"id": cid, "category": category, "status": "n/a", "passed": None,
                            "must": must or cid in CRITICAL, "reason": "N/A: " + reason, "fix": ""})
    def review(self, cid, category, reason, fix):
        self.checks.append({"id": cid, "category": category, "status": "needs-review", "passed": None,
                            "must": True, "reason": "needs-review: " + reason, "fix": fix})
    @property
    def needs_review(self):
        return [c for c in self.checks if c["status"] == "needs-review"]
    @property
    def applicable(self):
        return [c for c in self.checks if c["passed"] is not None]
    @property
    def failed(self):
        return [c for c in self.checks if c["passed"] is False]
    @property
    def score(self):
        app = self.applicable
        return round(sum(c["passed"] for c in app) / len(app), 3) if app else 0.0
    @property
    def critical_failed(self):
        """Failed checks that fail the unit regardless of score (CRITICAL + present optional checks + outcomes)."""
        return [c for c in self.failed if c["must"]]
    @property
    def categories(self):
        out = {}
        for c in self.applicable:
            s = out.setdefault(c["category"], {"passed": 0, "total": 0})
            s["passed"] += c["passed"]; s["total"] += 1
        return out
    @property
    def category_failed(self):
        """Categories with zero passes or a pass rate below CATEGORY_FLOOR."""
        return sorted(k for k, s in self.categories.items()
                      if s["passed"] == 0 or s["passed"] / s["total"] < CATEGORY_FLOOR)
    @property
    def verdict(self):
        """FAIL if any machine check fails; else REVIEW if a needs-review item remains; else PASS."""
        ok = self.applicable and self.score >= PASS_THRESHOLD and not self.critical_failed and not self.category_failed
        if not ok: return "FAIL"
        return "REVIEW" if self.needs_review else "PASS"
    def to_dict(self):
        return {"path": self.path, "kind": self.kind, "score": self.score,
                "threshold": PASS_THRESHOLD, "category_floor": CATEGORY_FLOOR,
                "categories": self.categories, "category_failed": self.category_failed,
                "needs_review": [c["id"] for c in self.needs_review],
                "verdict": self.verdict, "checks": self.checks}

EXIT_CODES = {"PASS": 0, "FAIL": 1, "REVIEW": 3}

CRITICAL = {"frontmatter", "name-format", "name-matches-folder", "description-present",
            "goal-present", "tools-declared", "agent-frontmatter-v2"}

def detect_kind(path):
    if os.path.isfile(os.path.join(path, "SKILL.md")): return "skill"
    if os.path.isfile(os.path.join(path, "AGENT.md")): return "agent"
    return None

def name_errors(name):
    """Name rules exactly as skills-ref validator._validate_name: NFKC-normalized, 1..64 chars, lowercase,
    Unicode letters/digits (str.isalnum) and '-' only, no leading/trailing '-', no '--'."""
    if not name.strip():
        return ["must be a non-empty string"]
    e = []
    if len(name) > NAME_MAX: e.append("exceeds %d characters (%d)" % (NAME_MAX, len(name)))
    if name != name.lower(): e.append("must be lowercase")
    if name.startswith("-") or name.endswith("-"): e.append("cannot start or end with '-'")
    if "--" in name: e.append("cannot contain '--'")
    if not all(c.isalnum() or c == "-" for c in name): e.append("only letters, digits and '-' allowed")
    return e

def _str_field(meta, key):
    """meta[key] if it is a string, else "" (an empty 'key:' is None, which must not read as the text 'None';
    non-string values are reported by field-types)."""
    v = meta.get(key)
    return v.strip() if isinstance(v, str) else ""

# ---------- English-only rule (owner decision 2026-10-19; design and exceptions: notes/english-rule-plan.md) ----------
ENGLISH_BUILDER_RULE = ("Write all skill content in English only (every file a run installs, plus evals/evals.json and "
                        "evals/trigger_evals.json); the loop stops if any non-English text appears.")


ENGLISH_RULES = "english-only.rules.json"
PARITY_SKILL_RULES = "parity-skill.rules.json"
PARITY_AGENT_RULES = "parity-agent.rules.json"

def _family_bundle(bundle, fname):
    """bundle when it holds the rule file fname, else the default bundle, so a fixture bundle without the English or
    parity rules still gets those rows (fail closed)."""
    if bundle is not None and fname in bundle.files():
        return bundle
    return rule_runner.default_bundle()

def _english_groups(bundle=None):
    """[(rule id, applies_to, group spec, scripts)] of the script_denylist group rules, in rule order."""
    b = _family_bundle(bundle, ENGLISH_RULES)
    return [(r.id, r.applies_to, r.check.group, r.check.scripts) for r in b.rules
            if r.file == ENGLISH_RULES and getattr(r.check, "group", None) is not None and "repository" not in r.applies_to]

def english_scan(path, bundle=None):
    """Full English-only scan of the skill or agent folder at path (kind from detect_kind), built from the English rules
    of bundle (default: the default bundle), so the skill_eval gates and the rows share one source."""
    return script_denylist.scan_unit(path, detect_kind(path), _english_groups(bundle))


def english_violations(path):
    """FAIL-level English-only findings of a skill/agent folder (strings 'file:line (where) [script]: "excerpt"').
    skill_eval.load_eval_set (so validate and every live gate) and the loop after every builder call use it."""
    return english_scan(path).violations

def english_eval_problems(skill_dir):
    """(violations, review) for the files of the english-evals rule (evals/evals.json and evals/trigger_evals.json);
    skill_eval validate uses it."""
    rid, _, spec, scripts = next(g for g in _english_groups() if g[0] == "english-evals")
    with script_denylist.using(scripts):
        _, v, _, r = script_denylist.group_scan(skill_dir, spec)
    return v, r


def review_items(path):
    """Findings that need a recorded human decision before anything is spent: the needs-review rows of the English-only
    rule (non-English run-output matchers) and of the parity rule (for example Hermes invocation control), as
    'check-id: reason' strings. skill_eval refuses --live and stops the loop on them until `approve --gate rule_review`."""
    r = Report(path, detect_kind(path) or "unknown")
    s = english_scan(path)
    if s.review:
        rule_runner.default_bundle().apply(r, only=(ENGLISH_RULES,))
    add_parity_checks(r, path)
    return ["%s: %s" % (c["id"], c["reason"]) for c in r.needs_review]

# The files whose bytes a rule-review decision covers, by unit kind, from the rows review_items collects (a trailing '/'
# stands for every file under the folder). Skill: english-output-matchers reads the evals JSON. Agent:
# english-output-matchers reads scenarios/, parity-permissions-opencode reads the AGENT.md tools and its own wrapper.
# SKILL.md only turns parity-invocation-hermes on or off, which already changes the findings, so a builder edit to it
# keeps the decision.
REVIEW_SOURCES = {"skill": ("evals/evals.json", "evals/trigger_evals.json"),
                  "agent": ("AGENT.md", "scenarios/", "wrappers/opencode/")}

def review_sources(path):
    """Relative paths of the files whose bytes a recorded review_items decision also covers: a finding shows an excerpt
    only, so an edit outside it must still void the decision. For an agent: the unit_tree files under each folder entry,
    plus the wrapper file of each wrapper folder entry that parity_primitive.wrapper_states reads, whenever it is a file
    inside the agent folder, even through a link that the walk does not follow."""
    kind = detect_kind(path)
    if kind != "agent": return list(REVIEW_SOURCES.get(kind, ()))
    files, out = unit_tree(path, _skip_dirs).files, []
    for src in REVIEW_SOURCES[kind]:
        out += [r for r in files if r.startswith(src)] if src.endswith("/") else [src]
    name, root = _str_field(_frontmatter_of(os.path.join(path, "AGENT.md"))[1] or {}, "name"), os.path.realpath(path)
    tools = [src.split("/")[1] for src in REVIEW_SOURCES[kind] if src.startswith("wrappers/")] if name else []
    for rel in (_wrapper_rel(tool, name) for tool in tools):
        fp = os.path.join(path, *rel.split("/"))
        if os.path.isfile(fp) and _within(root, os.path.realpath(fp)): out.append(rel)
    return list(dict.fromkeys(out))


# ---------- Multi-tool parity rule (owner decision 2026-10-19 18:43 KST; design: notes/multi-tool-parity-plan.md) ----------
# Every skill and agent must be created for all three tools together. Formats per ~/workspace/pstack-trial/runs/engrule/
# design.md sections 2-3; nothing here was re-verified against a primary source (UNVERIFIED items: plan section 8).
# Skill: the shared SKILL.md is the single source. Agent: AGENT.md is the source and wrappers/<tool>/ holds one thin file
# per tool.
TOOLS = ("claude", "opencode", "hermes")
TOOL_LABELS = {"claude": "Claude Code", "codex": "Codex CLI", "opencode": "OpenCode", "hermes": "Hermes Agent"}
PARITY_BUILDER_RULE = (
    "Keep the skill loadable by all three tools together (Claude Code, OpenCode, Hermes Agent): SKILL.md is the "
    "single shared source. The name must match ^[a-z0-9]+(-[a-z0-9]+)*$ and the folder name; every metadata value must be "
    "a string; do not put Hermes keys (metadata.hermes) in the frontmatter; do not use disable-model-invocation unless "
    "metadata 'opencode/autoinvoke: \"false\"' and agents/openai.yaml 'policy: allow_implicit_invocation: false' are "
    "also set. The loop stops if the parity check fails.")


PARITY_ROW_TOOLS = {
    "parity-skill-claude": ("claude",), "parity-skill-opencode": ("opencode",),
    "parity-skill-hermes": ("hermes",), "parity-invocation-control": ("claude", "opencode"),
    "parity-invocation-hermes": ("hermes",), "parity-agent-name": TOOLS,
    "parity-wrapper-claude": ("claude",), "parity-wrapper-opencode": ("opencode",),
    "parity-wrapper-hermes": ("hermes",), "parity-permissions-claude": ("claude",),
    "parity-permissions-opencode": ("opencode",), "parity-permissions-hermes": ("hermes",)}


def _frontmatter_of(fp):
    """(raw, meta, error): meta is None when the file is missing, unreadable or its frontmatter does not parse."""
    raw, err = read_text(fp)
    if raw is None: return None, None, err
    meta, _, ferr = parse_frontmatter_ex(raw)
    return raw, meta, ferr


def codex_policy(path):
    """(problems, allow_implicit_invocation, unverified) for agents/openai.yaml, from targets/codex.json (see
    engine/primitives/parity.py codex_policy). A missing file is not a problem."""
    return parity_primitive.codex_policy(path, parity_primitive.target("codex"))


def _wrapper_rel(tool, name):
    return {"claude": "wrappers/claude/%s.md" % name, "codex": "wrappers/codex/%s.toml" % name,
            "opencode": "wrappers/opencode/%s.md" % name, "hermes": "wrappers/hermes/SOUL.md"}[tool]


def add_parity_checks(r, path, bundle=None, dedupe=True):
    """Adds the parity rows (category 'parity', required) of the skill or agent at path to report r from
    rules/parity-skill.rules.json or rules/parity-agent.rules.json of bundle (default: the default bundle). For a skill,
    dedupe False keeps the folder cause that name-matches-folder reports in check (A3 D2)."""
    kind = detect_kind(path)
    if kind == "skill":
        _family_bundle(bundle, PARITY_SKILL_RULES).apply(r, only=(PARITY_SKILL_RULES,), dedupe=dedupe)
    elif kind == "agent":
        _family_bundle(bundle, PARITY_AGENT_RULES).apply(r, only=(PARITY_AGENT_RULES,))

def parity_report(path):
    """A Report holding only the parity rows of the skill or agent folder at path (never raises). The rows keep every
    name cause, so parity_status, parity_violations and the skill_eval gates give the A2 result; only check rows drop
    the folder cause that name-matches-folder reports (A3 D2)."""
    kind = detect_kind(path)
    r = Report(path, kind or "unknown")
    add_parity_checks(r, path, dedupe=False)
    return r

def parity_status(path_or_report):
    """{tool: {"status": "pass"|"fail"|"review", "problems": [...], "unverified": [...]}} from the parity rows."""
    r = parity_report(path_or_report) if isinstance(path_or_report, str) else path_or_report
    out = {t: {"status": "pass", "problems": [], "unverified": []} for t in TOOLS}
    for c in r.checks:
        for t in PARITY_ROW_TOOLS.get(c["id"], ()):
            st = out[t]
            if c["status"] == "fail":
                st["status"] = "fail"; st["problems"].append("%s: %s" % (c["id"], c["reason"]))
            elif c["status"] == "needs-review":
                if st["status"] != "fail": st["status"] = "review"
                st["problems"].append("%s: %s" % (c["id"], c["reason"]))
            elif c["status"] == "n/a" and "UNVERIFIED" in c["reason"]:
                st["unverified"].append(c["id"])
    return out

def parity_violations(path):
    """FAIL-level parity findings ('[tool] reason') of a skill or agent folder. The skill_eval loop calls this after
    every builder call and load_eval_set reports them as validation problems."""
    return [c["reason"] for c in parity_report(path).failed]

def parity_line(status):
    """One line: 'claude=pass opencode=fail hermes=pass' (+ '(unverified: ...)' for tools with open items)."""
    return " ".join("%s=%s%s" % (t, status[t]["status"], " (unverified: %s)" % ",".join(
        x.replace("parity-", "") for x in status[t]["unverified"]) if status[t]["unverified"] else "") for t in TOOLS)

# ---------- skills (agentskills.io specification + Claude skill authoring best practices) ----------
local_refs = refs_primitive.local_refs
_ref_problem = refs_primitive.ref_problem
git_root = refs_primitive.git_root
repo_root_ref = refs_primitive.repo_root_ref
_within = refs_primitive.within
ref_targets = refs_primitive.ref_targets

# ---------- schema-backed shape checks (schemas/*.schema.json) ----------
SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas")
SHAPE_ROWS = ("evals-valid", "field-types",  # rows that a default-bundle rule now decides (rules/skill-*.rules.json)
              "name-format", "name-matches-folder", "name-reserved")  # rules/naming.rules.json (A3)
DISAGREE = "rule and eva.py disagree"
_PER_ERROR_KEYS = ("value", "repr", "key", "key_ascii", "path_reason", "type", "got", "error", "dup_index")
_SHAPES = {}

def shape_schema(name):
    """The ShapeSchema of schemas/<name>, compiled once per process."""
    if name not in _SHAPES:
        _SHAPES[name] = ShapeSchema(name)
    return _SHAPES[name]

class ShapeSchema:
    """A schema whose message rules are the $defs entries with a description: the description is the English message
    template (%(key)s), the other keywords are its constraint. The other nodes on the way only route ($ref, allOf,
    if/then/else, properties, items). Accept or reject comes from the whole schema; the messages come from the message
    rules the instance reaches and fails, each with the rule (if any), the schema pointer and the failing keyword."""
    def __init__(self, name):
        from engine import schema
        self.name, self._schema = name, schema
        self.doc = schema.load(os.path.join(SCHEMA_DIR, name))
        self.root = schema.compile_schema(self.doc)
        self._subs = {}

    def sub(self, path):
        """The compiled subschema at path, a tuple of tokens under $defs."""
        if path not in self._subs:
            self._subs[path] = self._schema.compile_schema({"$defs": self.doc["$defs"], "$ref": self._schema.pointer(path)})
        return self._subs[path]

    def node(self, path):
        n = self.doc
        for tok in path:
            n = n[tok]
        return n

    def problems(self, inst, start=None, rule=None, unit_root=None, typed_keys=None, where="", **extra):
        """Messages for inst ([] when valid); start is the tuple path of the node to begin at (default: the root $ref)."""
        path = start or tuple(self._schema.parse_pointer(self.doc["$ref"][1:]))
        whole = self.root if start is None else self.sub(path)
        errs = whole.validate(inst, unit_root, typed_keys)
        if any(kw == "depth" for _, kw, _ in errs):
            inst = _cut(inst, self._schema.MAX_DEPTH - 1)
            errs = whole.validate(inst, unit_root, typed_keys)
        if not errs:
            return []
        opt = dict(rule=rule, unit_root=unit_root, typed=typed_keys, where=where, extra=extra)
        out = []
        self._walk(self.node(path), path, inst, None, out, opt)
        return out

    def _walk(self, node, path, inst, index, out, opt):
        if not isinstance(node, dict) or not node:
            return
        if "description" in node:
            errs = self.sub(path).validate(inst, opt["unit_root"], opt["typed"])
            if errs:
                out += self._render(node, path, inst, errs, index, opt)
        elif "$ref" in node:
            ref = tuple(self._schema.parse_pointer(node["$ref"][1:]))
            self._walk(self.node(ref), ref, inst, index, out, opt)
        elif "allOf" in node:
            for j, sub in enumerate(node["allOf"]):
                self._walk(sub, path + ("allOf", j), inst, index, out, opt)
        elif "if" in node:
            branch = "else" if self.sub(path + ("if",)).validate(inst, opt["unit_root"], opt["typed"]) else "then"
            if branch in node:
                self._walk(node[branch], path + (branch,), inst, index, out, opt)
        elif "properties" in node:
            if isinstance(inst, dict):
                for k, sub in node["properties"].items():
                    if k in inst:
                        self._walk(sub, path + ("properties", k), inst[k], index, out, opt)
        elif "items" in node and isinstance(inst, list):
            for i, x in enumerate(inst):
                self._walk(node["items"], path + ("items",), x, i, out, opt)

    def _render(self, node, path, inst, errs, index, opt):
        template = node["description"]
        tag = " [%s%s%s, keyword %%s]" % ("rule %s, " % opt["rule"] if opt["rule"] else "", self.name,
                                          self._schema.pointer(path))
        dup_at = iter(self._duplicates(node, inst)[1])
        per_error = any("%%(%s)s" % k in template for k in _PER_ERROR_KEYS)
        out, seen = [], set()
        for e in (errs if per_error else errs[:1]):
            epath, kw, msg = e
            ctx = _MessageContext(self, node, inst, errs, e, index, opt, dup_at if kw == "x-unique-by" else None)
            text = template % ctx
            if (epath, text) not in seen:
                seen.add((epath, text))
                out.append(text + tag % kw)
        return out

    def _duplicates(self, node, inst):
        """(sorted duplicate values, indices of the later duplicates) of the leaf's x-unique-by key."""
        key = node.get("x-unique-by")
        if key is None or not isinstance(inst, list):
            return [], []
        seen, dups, later = set(), [], []
        for i, item in enumerate(inst):
            if isinstance(item, dict) and key in item:
                c = self._schema.canon(item[key])
                if c in seen:
                    later.append(i)
                    if item[key] not in dups:
                        dups.append(item[key])
                seen.add(c)
        return dups, later

class _MessageContext(dict):
    """The %(key)s values of one message, computed on first use."""
    def __init__(self, shape, node, inst, errs, err, index, opt, dup_at):
        dict.__init__(self, i=index, where=opt["where"], **opt["extra"])
        self.shape, self.node, self.inst, self.errs, self.err, self.opt, self.dup_at = \
            shape, node, inst, errs, err, opt, dup_at

    def __missing__(self, key):
        v = self.inst
        for tok in self.err[0]:
            v = v[tok]
        last = self.err[0][-1] if self.err[0] else ""
        if key == "value": return _shown(v, str)
        if key == "repr": return _shown(v, repr)
        if key == "type": return type(v).__name__
        if key == "got": return "empty" if v is None or v == "" else type(v).__name__
        if key == "key": return last
        if key == "key_ascii": return str(last).encode("ascii", "backslashreplace").decode("ascii")
        if key == "error": return self.err[2]
        if key == "extra": return str(sorted({e[0][-1] for e in self.errs if e[0]}))
        if key == "dup_index": return next(self.dup_at)
        if key == "dups":
            dups = self.shape._duplicates(self.node, self.inst)[0]
            ints = all(isinstance(x, int) and not isinstance(x, bool) for x in dups)
            return str(sorted(dups)) if ints else "[%s]" % ", ".join(json.dumps(x, ensure_ascii=False) for x in dups)
        if key == "path_reason":
            why = self.shape._schema.path_problem(v)
            if why: return why
            return "not found" if not os.path.isfile(os.path.join(self.opt["unit_root"], v)) else self.err[2]
        raise KeyError(key)

def _shown(v, show):
    """show(v), or a stand-in when v nests deeper than the Python recursion limit allows."""
    try:
        return show(v)
    except RecursionError:
        return "[...]" if isinstance(v, list) else "{...}"

class _Cut(list):
    """A list or object nested past engine.schema.MAX_DEPTH, which canon() refuses, while the legacy checks compared
    such values with == and printed them with repr. A _Cut holds one NaN of its own, so it equals no other value
    (NaN != NaN, and strict JSON has no NaN), and it prints as the value it stands for."""
    def __init__(self, original):
        list.__init__(self, [float("nan")])
        self.original = original

    def __repr__(self):
        return _shown(self.original, repr)

def _cut(doc, depth):
    """A copy of doc with each list or object `depth` levels down replaced by a _Cut. Iterative, because doc may nest
    deeper than the recursion limit allows."""
    top = [None]
    todo = [(top, 0, doc, 0)]
    while todo:
        parent, key, v, level = todo.pop()
        if isinstance(v, (list, dict)) and level >= depth:
            parent[key] = _Cut(v)
        elif isinstance(v, list):
            parent[key] = part = [None] * len(v)
            todo += [(part, i, x, level + 1) for i, x in enumerate(v)]
        elif isinstance(v, dict):
            parent[key] = part = dict.fromkeys(v)
            todo += [(part, k, x, level + 1) for k, x in v.items()]
        else:
            parent[key] = v
    return top[0]


def _field_type_errors(meta, typed):
    """Spec types per schemas/skill-frontmatter.schema.json (rule field-types). typed: keys whose unquoted value
    YAML 1.1 (PyYAML) would load as bool/null/number/date (yaml_typed_keys)."""
    return shape_schema("skill-frontmatter.schema.json").problems(meta, rule="field-types", typed_keys=typed)


def evals_errors(path, name):
    """Validate evals/evals.json against schemas/evals.schema.json (rule evals-valid). Returns (n_cases, errors).
    skill_name must equal name (the NFKC frontmatter name), a cross-file check kept here. JSON is read strictly
    (engine.schema.loads), as the rule's json: selector reads it."""
    fp = os.path.join(path, "evals", "evals.json")
    if not os.path.isfile(fp):
        return 0, ["missing evals/evals.json"]
    raw, err = read_text(fp)
    if err: return 0, [err]
    from engine import schema
    try:
        data = schema.loads(raw)
    except schema.JsonError as ex:
        return 0, ["invalid JSON: %s" % ex]
    e = shape_schema("evals.schema.json").problems(data, rule="evals-valid", unit_root=path)
    if isinstance(data, dict) and data.get("skill_name") != name:
        e.insert(0, "skill_name %r must equal frontmatter name %r" % (data.get("skill_name"), name))
    items = data.get("evals") if isinstance(data, dict) else None
    return (len(items) if isinstance(items, list) and items else 0), e


# The rule files whose rows check_skill and check_agent add: every one replaces built-in rows, so each is taken from the
# bundle in use when it holds the file, else from the default bundle (brief A4 D2).
FAMILY_FILES = ("skill-structure.rules.json", "skill-description.rules.json", "naming.rules.json",
                "skill-frontmatter.rules.json", "skill-evals.rules.json", "agent-structure.rules.json",
                ENGLISH_RULES, PARITY_SKILL_RULES, PARITY_AGENT_RULES)


def check_skill(path, bundle=None):
    """The rows of the skill at path: one runner call over FAMILY_FILES, then the A3 reasons of the SHAPE_ROWS."""
    r = Report(path, "skill")
    rule_runner.apply_families(r, FAMILY_FILES, bundle)
    _merge_shape_rows(r)
    return r


# ---------- host ops: rows whose A3 reasons need eva.py's parsers or outcome checkers (rules/*-structure.rules.json) ----------
def _meta(unit):
    return heading_present.main_file(unit).meta_or_empty


def _agent_capabilities(meta):
    return [str(t) for t in as_list(meta.get("capabilities"))]


def _verdict(ok, facts):
    return ("pass" if ok else "fail"), facts


def _host_frontmatter(node):
    def run(unit):
        f = heading_present.main_file(unit)
        return _verdict(f.meta is not None, {"error": ": %s" % f.error if f.error else ""})
    return run


def _host_frontmatter_fields(node):
    fields, host_fields = tuple(node["fields"]), tuple(node["host_fields"])

    def run(unit):
        meta = _meta(unit)
        extra = sorted(k for k in meta if k not in fields + host_fields)
        host = sorted(k for k in meta if k in host_fields)
        return _verdict(not extra, {"spec": "%s" % (fields,), "extra": "%s" % extra,
                                    "host": " + Claude Code host-specific %s" % host if host else "",
                                    "unexpected": ": unexpected %s" % extra if extra else ""})
    return run


def _host_field_length(node):
    field, lo, hi = node["field"], node["min"], node["max"]

    def run(unit):
        meta = _meta(unit)
        if field not in meta:
            return "na", {}
        n = len((meta[field] if isinstance(meta[field], str) else "").strip())
        return _verdict(lo <= n <= hi, {"length": n, "min": lo, "max": hi})
    return run


def _host_main_file_size(node):
    max_bytes, below = node["max_bytes"], node["lines_below"]

    def run(unit):
        raw = heading_present.main_file(unit).raw
        nbytes, nlines = len(raw.encode("utf-8")), len(raw.splitlines())
        return _verdict(nbytes <= max_bytes and nlines < below,
                        {"bytes": nbytes, "lines": nlines, "max_bytes": max_bytes, "lines_below": below})
    return run


def _host_metadata_version(node):
    pattern = re.compile(node["pattern"])

    def run(unit):
        meta = _meta(unit)
        md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
        ver = md.get("version")
        if ver is None:
            return "na", {}
        return _verdict(bool(pattern.match(str(ver))), {"version": str(ver)})
    return run


def _host_evals_count(node):
    lo = node["min"]

    def run(unit):
        n, _ = evals_errors(unit.path, "")
        return _verdict(n >= lo, {"count": n, "min": lo})
    return run


def _host_tools_declared(node):
    def run(unit):
        caps = _agent_capabilities(_meta(unit))
        return _verdict(bool(caps), {"tools": "%s" % caps})
    return run


def _host_success_measurable(node):
    """Measurable iff one typed criterion object of AGENT.md v2 has a machine-checkable type (any type but llm)."""
    def run(unit):
        crit = transcript_primitive.parse_agent(heading_present.main_file(unit).raw).criteria
        checkable = [c for c in crit if isinstance(c, dict) and isinstance(c.get("type"), str)
                     and c["type"] in transcript_primitive.KINDS and c["type"] != "llm"]
        review = [c["text"].encode("ascii", "backslashreplace").decode("ascii") for c in crit
                  if isinstance(c, dict) and c.get("type") == "llm" and isinstance(c.get("text"), str)]
        untyped = len(crit) - len(checkable) - len(review)
        return _verdict(bool(checkable), {
            "checkable": len(checkable), "total": len(crit),
            "detail": ("; needs-review (human/model grading) at transcript time: %s" % review if review else "") +
                      ("; %d not a typed criterion object (see agent-frontmatter-v2)" % untyped if untyped else "")})
    return run


def _scenarios(unit):
    if "scenarios" not in unit.memo:
        scen = load_scenarios(unit.path)
        unit.memo["scenarios"] = scen, [(f, s) for f, s, e in scen if isinstance(s, dict)]
    return unit.memo["scenarios"]


def _host_scenarios(node):
    measure, lo = node["measure"], node.get("min")

    def run(unit):
        scen, objs = _scenarios(unit)
        if measure == "present":
            valid = [f for f, s, e in scen if not e]
            return _verdict(len(valid) >= lo, {"valid": len(valid), "min": lo})
        if measure == "valid":
            invalid = ["%s (%s)" % (f, "; ".join(e)) for f, s, e in scen if e]
            return _verdict(bool(scen) and not invalid, {"detail": ": %s" % invalid if invalid else ""})
        if measure == "expected":
            noexp = [f for f, s in objs if not s.get("expected_outcome")]
            return _verdict(bool(objs) and not noexp, {"detail": ": missing in %s" % noexp if noexp else ""})
        if measure == "tools_subset":
            tools = _agent_capabilities(_meta(unit))
            undeclared = sorted({t for f, s in objs for t in _strs(s.get("expected_tools")) if t not in tools})
            return _verdict(not undeclared, {"detail": ": undeclared %s" % undeclared if undeclared else "",
                                             "undeclared": "%s" % undeclared})
        multi = [f for f, s in objs if isinstance(s.get("turns"), list) and len(s["turns"]) >= 2]
        return _verdict(bool(multi), {"multi": "%s" % multi})
    return run


rule_runner.install_host_ops({
    "host_frontmatter": _host_frontmatter, "host_frontmatter_fields": _host_frontmatter_fields,
    "host_field_length": _host_field_length, "host_main_file_size": _host_main_file_size,
    "host_metadata_version": _host_metadata_version, "host_evals_count": _host_evals_count,
    "host_tools_declared": _host_tools_declared, "host_success_measurable": _host_success_measurable,
    "host_scenarios": _host_scenarios})

# ---------- agents (slated to move to the separate "agent master"; kept here until then) ----------
def _strs(v):
    """String items of v if it is a list, else [] (safe access for malformed input)."""
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []

def scenario_errors(s):
    """Shape errors of one scenario per schemas/scenario.schema.json."""
    return shape_schema("scenario.schema.json").problems(s)


def load_scenarios(path):
    """Returns [(filename, data or None, errors)]; data is None when the JSON does not parse
    or the scenarios/ directory cannot be listed (e.g. PermissionError)."""
    d = os.path.join(path, "scenarios")
    out = []
    if os.path.isdir(d):
        try:
            names = os.listdir(d)
        except OSError as e:
            return [("scenarios/", None, ["cannot list scenarios/: %s" % e])]
        for f in sorted(names):
            if f.endswith(".json"):
                raw, err = read_text(os.path.join(d, f))  # OSError / UnicodeDecodeError -> error, not a crash
                if err:
                    out.append((f, None, [err])); continue
                try:
                    data = json.loads(raw)
                except ValueError as e:
                    out.append((f, None, ["invalid JSON: %s" % e])); continue
                out.append((f, data, scenario_errors(data)))
    return out

def check_agent(path, bundle=None):
    """The rows of the agent at path: one runner call over FAMILY_FILES, then the A3 reasons of the SHAPE_ROWS."""
    r = Report(path, "agent")
    rule_runner.apply_families(r, FAMILY_FILES, bundle)
    _merge_shape_rows(r)
    return r

def check(path, bundle=None):
    """The family rows (check_skill, check_agent), then the other rules of bundle (default: the cached default bundle,
    which raises rule_runner.BundleError when broken, so a direct caller such as loop.py fails closed)."""
    kind = detect_kind(path)
    bundle = rule_runner.default_bundle() if bundle is None else bundle
    if kind == "skill": r = check_skill(path, bundle)
    elif kind == "agent": r = check_agent(path, bundle)
    else:
        r = Report(path, "unknown")
        r.add("frontmatter", "structure", False, "no SKILL.md or AGENT.md", "Put a SKILL.md or an AGENT.md in the folder.")
    bundle.apply(r, skip=FAMILY_FILES)
    _merge_bundle_rows(r)
    return r

def _shape_reasons(path, kind):
    """{row id: (passed, reason)} for SHAPE_ROWS, computed as the A3 built-in rows of check_skill and _common_name
    computed them, so the rule rows keep the A3 reasons byte for byte."""
    raw, rerr = read_text(os.path.join(path, "SKILL.md" if kind == "skill" else "AGENT.md"))
    meta = (parse_frontmatter_ex(raw)[0] if raw is not None else None) or {}
    name = unicodedata.normalize("NFKC", _str_field(meta, "name"))
    ne = name_errors(name)
    folder = unicodedata.normalize("NFKC", os.path.basename(os.path.realpath(path)))  # '.', 'x/', symlinks
    out = {"name-format": (not ne, "name '%s' (skills-ref rules: NFKC, lowercase letters/digits/'-', <=%d)%s" % (
               name, NAME_MAX, (": %s" % ne) if ne else "")),
           "name-matches-folder": (name == folder, "name '%s' vs folder '%s'" % (name, folder))}
    if kind == "skill":
        bad_words = [w for w in RESERVED_NAME_WORDS if w in name.lower()]
        out["name-reserved"] = (not bad_words, "name has no reserved words %s" % (RESERVED_NAME_WORDS,) +
                                (": contains %s" % bad_words if bad_words else ""))
        te = _field_type_errors(meta, set(yaml_typed_keys(raw or "")) if meta else set())
        out["field-types"] = (not te, "field types per spec" + (": %s" % te if te else ""))
        _, ee = evals_errors(path, name)
        out["evals-valid"] = (not ee, "evals/evals.json format" + (": %s" % ee if ee else " valid"))
    return out

def _rule_fix(rid):
    """The fail fix of the default-bundle rule rid (for a row the rule passed but eva.py fails)."""
    rule = next((x for x in rule_runner.default_bundle().rules if x.id == rid), None)
    return rule.text(rule.fix, "fail", {}, "") if rule else ""

def _merge_shape_rows(r):
    """Each rule row of an id in SHAPE_ROWS keeps its place, status and fix and takes the reason eva.py computes
    (_shape_reasons), and is must. When the two disagree on pass, the row fails closed."""
    built = None
    for c in r.checks:
        if c["id"] not in SHAPE_ROWS:
            continue
        built = built or _shape_reasons(r.path, r.kind)
        if c["id"] not in built:
            continue
        passed, reason = built[c["id"]]
        if passed is not c["passed"]:
            c.update(status="fail", passed=False, must=True, fix=c["fix"] or _rule_fix(c["id"]),
                     reason="%s (%s, failing closed: the rule says %s: %s)" % (reason, DISAGREE, c["status"], c["reason"]))
        else:
            c.update(reason=reason, must=True)

def _merge_bundle_rows(r):
    """A rule of the bundle outside FAMILY_FILES that reuses an id in SHAPE_ROWS keeps its place, status and fix, takes
    the reason of the family row, which is dropped, so the id appears once, and is must when either row is. When the
    two disagree on pass, the row fails."""
    for rid in SHAPE_ROWS:
        rows = [c for c in r.checks if c["id"] == rid]
        if len(rows) != 2:
            continue
        built, ruled = rows
        if built["passed"] is not ruled["passed"]:
            ruled.update(status="fail", passed=False, must=True, fix=ruled["fix"] or built["fix"],
                         reason="%s (%s, failing closed: the rule says %s: %s)" % (
                             built["reason"], DISAGREE, ruled["status"], ruled["reason"]))
        else:
            ruled.update(reason=built["reason"], must=ruled["must"] or built["must"])
        r.checks = [c for c in r.checks if c is not built]

def find_units(root):
    """Folders holding SKILL.md/AGENT.md. Symlinked folders are followed (skills are often installed as links,
    e.g. ~/.agents/skills/<name> -> repo); dangling links are skipped and each real folder is visited once."""
    out, seen = [], set()
    for dp, dn, fn in os.walk(root, followlinks=True):
        real = os.path.realpath(dp)
        if real in seen:
            dn[:] = []; continue
        seen.add(real)
        dn[:] = sorted(d for d in dn if not d.startswith(".") and os.path.isdir(os.path.join(dp, d)))
        if "SKILL.md" in fn or "AGENT.md" in fn:
            out.append(dp); dn[:] = []
    return out

# ---------- transcript grading (agent side; slated for split-out with the agent checks) ----------
def load_transcript(path):
    """Parse a transcript and validate it against schemas/transcript.schema.json. Returns (run, errors); run is None
    when errors. run = {"scenario", "final", "workspace", "calls": [{"tool", "input", "result"}], "results": [str],
    "is_error", "work_manifest"} (the last two None when the transcript does not record them)."""
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError) as e:
        return None, ["cannot load transcript JSON: %s" % e]
    e = shape_schema("transcript.schema.json").problems(t)
    if e:
        return None, e
    calls = [{"tool": c, "input": None, "result": None} if isinstance(c, str) else
             {"tool": c["tool"], "input": c.get("input"), "result": c.get("result")} for c in t["tool_calls"]]
    results = [c["result"] for c in calls if c["result"]] + list(t.get("tool_results", []))
    return {"scenario": t["scenario"], "final": t["final"], "workspace": t.get("workspace"),
            "calls": calls, "results": results, "is_error": t.get("is_error"),
            "work_manifest": t.get("work_manifest")}, []


def grade_transcript(agent_path, transcript_path):
    """Grade one run transcript against the AGENT.md v2 declaration and the scenario v2 (see load_transcript for the
    transcript format). Each typed criterion is checked by engine/primitives/transcript.py criterion_evidence."""
    r = Report(transcript_path, "transcript")
    run, terr = load_transcript(transcript_path)
    r.add("transcript-valid", "structure", run is not None, "transcript shape" + (": %s" % terr if terr else " valid"),
          "Write the transcript as {\"scenario\": str, \"tool_calls\": [...], \"final\": str} (see LOOP.md).", must=True)
    if run is None: return r
    agent = transcript_primitive.read_agent(agent_path)
    r.add("agent-valid", "structure", agent.valid,
          "agent AGENT.md v2 frontmatter valid" if agent.valid else "%s: %s" % (agent.version, agent.detail),
          transcript_primitive.MIGRATION, must=True)
    if not agent.valid: return r
    meta = agent.meta
    scen = load_scenarios(agent_path)
    sid = run["scenario"]
    match = [(f, s, e) for f, s, e in scen if isinstance(s, dict) and s.get("id") == sid]
    s = match[0][1] if match and not match[0][2] else None
    reason = "scenario '%s' exists" % sid if s is not None else (
        "scenario '%s' invalid: %s" % (sid, match[0][2]) if match else "scenario '%s' not found" % sid)
    r.add("scenario-known", "goal", s is not None, reason.encode("ascii", "backslashreplace").decode("ascii"),
          "Make the transcript scenario the id of a valid scenario v2 file in the agent's scenarios/ folder.", must=True)
    if s is None: return r
    table, neutral = parity_primitive.capability_table()
    cap_of = {c["tool"]: table.get(c["tool"].strip().lower()) for c in run["calls"]
              if c["tool"].strip().lower() not in neutral}
    caps = {cap for cap in cap_of.values() if cap}
    unmapped = sorted(n for n, cap in cap_of.items() if cap is None)
    undeclared = sorted(n for n, cap in cap_of.items() if cap is not None and cap not in meta["capabilities"])
    illegal = sorted(unmapped + undeclared)
    r.add("only-declared-tools", "tool-use", not illegal, "illegal tools: %s" % illegal + (
          " (unmapped in rules/data/capabilities.json: %s; capability not declared: %s)" % (unmapped, undeclared)
          if illegal else ""),
          "Stop calling %s: every tool call must map through rules/data/capabilities.json to a capability the AGENT.md "
          "declares." % illegal, must=True)
    miss = [x for x in s["expected_tools"] if x not in caps]
    r.add("expected-tools-called", "tool-use", not miss, "missing calls: %s" % miss,
          "Call a tool of each expected capability %s." % miss, must=True)
    viol = answer_lookup_violations(run["calls"], run["workspace"])
    r.add("no-answer-lookup", "tool-use", not viol,
          "%d tool calls inspected%s" % (len(run["calls"]), (": answer lookup %s" % viol) if viol else ", no answer lookup"),
          "Stop searching outside the workspace (.., /, ~, absolute paths outside it) and mining git history (git log "
          "--all, reflog, fsck and the like); do not look the answer up, solve the task from the evidence inside the given "
          "workspace.", must=True)
    final = run["final"]
    lack = [k for k in s["must_include"] if k.lower() not in final.lower()]
    r.add("outcome-keywords", "goal", not lack, "final answer missing: %s" % lack,
          "Include %s in the final answer." % lack, must=True)
    sources = [("criteria", c) for c in meta["success_criteria"]]
    sources += [("criteria", c) for c in s.get("success_criteria", [])]
    sources += [("expected", c) for c in s["expected_outcome"]]
    seen, checked = {}, 0
    for origin, crit in sources:
        kind, ok, got, fix = transcript_primitive.criterion_evidence(crit, final, run)
        base = "%s-%s" % (origin, kind)
        seen[base] = seen.get(base, 0) + 1
        cid = base if seen[base] == 1 else "%s-%d" % (base, seen[base])
        if ok is None:
            r.review(cid, "goal", got, fix)
            continue
        checked += 1
        r.add(cid, "goal", ok, "%s -> %s" % (transcript_primitive.show(crit), got), fix, must=True)
    r.add("outcome-evidence", "goal", checked > 0, "%d checkable outcome criteria" % checked,
          "Add a machine-checkable typed criterion (min_verified_sources, file_line_citations, no_writes, max_chars or "
          "markdown_table) to the AGENT.md success_criteria or the scenario expected_outcome.", must=True)
    return r

# ---------- feedback / index ----------
def feedback_md(r, rnd=None):
    d = r.to_dict()
    lines = ["# Eva Feedback: %s" % os.path.basename(os.path.normpath(r.path)), "",
             "- kind: %s / score: %.0f%% / verdict: **%s**%s" % (d["kind"], d["score"] * 100, d["verdict"],
             (" / round %s" % rnd) if rnd else ""), ""]
    fails = r.failed
    if d["category_failed"]:
        lines += ["## Categories below the floor (each category needs a pass rate of %.0f%% or more and at least one pass)"
                  % (CATEGORY_FLOOR * 100), ""]
        for k in d["category_failed"]:
            lines.append("- %s: %d/%d passed" % (k, d["categories"][k]["passed"], d["categories"][k]["total"]))
        lines.append("")
    if d["needs_review"]:
        lines += ["## Needs human or model grading (needs-review, no automatic verdict)", ""]
        for c in r.needs_review:
            lines.append("- `%s`: %s" % (c["id"], c["reason"]))
        lines.append("")
    if not fails:
        lines.append("Every automatic check passed." if d["needs_review"] else "Every check passed. Nothing to fix.")
    else:
        lines += ["## What to fix (most important first)", ""]
        fails.sort(key=lambda c: not c["must"])
        for i, c in enumerate(fails, 1):
            tag = " [must]" if c["must"] else ""
            lines += ["%d. `%s`%s (%s)" % (i, c["id"], tag, c["category"]),
                      "   - Reason: %s" % c["reason"], "   - Fix: %s" % c["fix"]]
    lines += ["", "After you fix everything, check again with `python3 eva.py check %s`." % r.path]
    return "\n".join(lines) + "\n"

def english_state(rep):
    """'fail' when an English-only row failed, 'review' when a language row needs review (non-English run-output matchers),
    else 'pass'. The index writes this next to the verdict, so a review is never shown as a pass."""
    lang = [c for c in rep.checks if c["category"] == "language"]
    if any(c["status"] == "fail" for c in lang): return "fail"
    return "review" if any(c["status"] == "needs-review" for c in lang) else "pass"

def build_index(root):
    """(rows, INDEX.md text): one row per unit with its check, English, parity and registration state, verified now
    (skill_eval.registration_state, imported only for a record that needs its register approval and hashes checked).
    engine/registry.document builds registry.json from the registered rows only."""
    reg = []
    for u in find_units(root):
        kind = detect_kind(u)
        raw, _ = read_text(os.path.join(u, "SKILL.md" if kind == "skill" else "AGENT.md"))
        meta = (parse_frontmatter(raw)[0] if raw is not None else None) or {}
        rep = check(u)
        ws = (registry.unit_workspace(u, meta) if kind == "skill" else
              registry.agent_workspace(u) if kind == "agent" else None)
        state, reason, rec = registry.early_state(kind, ws)
        if state is None:
            import skill_eval
            state, reason, rec = skill_eval.registration_state(u, ws)
        reg.append({"kind": kind, "name": meta.get("name", ""), "version": registry.version_of(meta),
                    "description": meta.get("description", ""), "path": os.path.relpath(u, root),
                    "verdict": rep.verdict, "score": rep.score,
                    "english": english_state(rep),
                    "parity": {t: st["status"] for t, st in parity_status(u).items()},
                    "registration": state, "registration_reason": reason, "record": rec, "workspace": ws})
    md = ["# INDEX", "", "| kind | name | version | verdict | score | english | parity | registration | path | description |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for e in reg:
        md.append("| %s | %s | %s | %s | %.2f | %s | %s | %s | %s | %s |" % (e["kind"], e["name"], e["version"],
                  e["verdict"], e["score"], e["english"], " ".join("%s=%s" % kv for kv in e["parity"].items()),
                  registry.label(e["registration"], e["registration_reason"], e["record"]).replace("|", "/"), e["path"],
                  str(e["description"]).replace("|", "/")[:120]))
    return reg, "\n".join(md) + "\n"

def print_report(r):
    d = r.to_dict()
    print("[%s] %s (%s) score=%.2f threshold=%.2f" % (d["verdict"], r.path, d["kind"], d["score"], PASS_THRESHOLD))
    for c in d["checks"]:
        mark = {"pass": "✓", "fail": "✗", "n/a": "-", "needs-review": "?"}[c["status"]]
        print("  %s %-22s %-11s %s" % (mark, c["id"], c["category"], c["reason"]))
    if d["category_failed"]:
        print("  category below floor %.2f: %s" % (CATEGORY_FLOOR, d["category_failed"]))

def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["eval"]:  # skill-eval harness (skill_eval.py): plan|estimate|run|grade|report|...
        import skill_eval
        return skill_eval.main(argv[1:])
    p = argparse.ArgumentParser(prog="eva", description="Eva skill/agent evaluator")
    sp = p.add_subparsers(dest="cmd", required=True)
    for n in ("check", "check-all", "feedback", "index"):
        s = sp.add_parser(n); s.add_argument("path"); s.add_argument("--json", action="store_true")
        if n == "check": s.add_argument("--transcript")
        if n == "feedback": s.add_argument("--round", type=int)
        if n in ("check", "check-all"):
            s.add_argument("--rules", metavar="DIR", help="run a non-default rule bundle (fixtures); never gives PASS")
    s = sp.add_parser("schema-check"); s.add_argument("schema"); s.add_argument("instance")
    s.add_argument("--unit", metavar="DIR", help="unit root for x-path-exists")
    s = sp.add_parser("generate", help="render an agent's four tool files or a skill's invocation metadata")
    s.add_argument("path"); s.add_argument("--check", action="store_true", help="exit 1 when a generated file differs")
    a = p.parse_args(argv)
    if a.cmd == "schema-check":
        from engine import schema
        return schema.cli(a.schema, a.instance, a.unit)
    if a.cmd == "generate":  # before the bundle load, like schema-check; engine/generate.py loads it (B2 D1)
        from engine import generate
        return generate.cli(a.path, a.check)
    if getattr(a, "transcript", None) and a.rules is not None:
        p.error("--rules cannot be combined with --transcript")
    if not getattr(a, "transcript", None):
        try:  # before any check(), so a broken bundle prints no unit row (plan.md:581)
            bundle = rule_runner.default_bundle() if getattr(a, "rules", None) is None else rule_runner.load_bundle(a.rules)
            if not set(FAMILY_FILES) <= bundle.files():
                rule_runner.default_bundle()  # the family rows then come from it (rule_runner.apply_families)
        except rule_runner.BundleError as e:
            print("bundle error: %s" % e, file=sys.stderr)
            return 2
    if a.cmd == "check" and a.transcript:
        r = grade_transcript(a.path, a.transcript)
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2)) if a.json else print_report(r)
        return EXIT_CODES[r.verdict]
    if a.cmd == "check":
        r = check(a.path, bundle)
        if a.json:
            print(json.dumps(dict(r.to_dict(), ruleset_sha256=bundle.sha256), ensure_ascii=False, indent=2))
        else:
            print_report(r)
            print("ruleset_sha256 %s" % bundle.sha256)
        return EXIT_CODES[r.verdict]
    if a.cmd == "check-all":
        units = find_units(a.path)
        if not units:
            print("no SKILL.md/AGENT.md units found under %s" % a.path, file=sys.stderr)
            return 1
        reps = [check(u, bundle) for u in units]
        if a.json:
            print(json.dumps([dict(r.to_dict(), ruleset_sha256=bundle.sha256) for r in reps], ensure_ascii=False, indent=2))
        else:
            for r in reps: print("[%s] %.2f %s" % (r.verdict, r.score, r.path))
            print("total=%d pass=%d review=%d fail=%d" % (len(reps), sum(r.verdict == "PASS" for r in reps),
                  sum(r.verdict == "REVIEW" for r in reps), sum(r.verdict == "FAIL" for r in reps)))
            print("ruleset_sha256 %s" % bundle.sha256)
            if not bundle.default: print("bundle non-default %s" % bundle.display)
        verdicts = {r.verdict for r in reps}
        return EXIT_CODES["FAIL"] if "FAIL" in verdicts else EXIT_CODES["REVIEW"] if "REVIEW" in verdicts else 0
    if a.cmd == "feedback":
        r = check(a.path)
        open(os.path.join(a.path, "FEEDBACK.md"), "w", encoding="utf-8").write(feedback_md(r, a.round))
        json.dump(r.to_dict(), open(os.path.join(a.path, "FEEDBACK.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("%s -> FEEDBACK.md, FEEDBACK.json (%s %.2f)" % (a.path, r.verdict, r.score))
        return EXIT_CODES[r.verdict]
    if a.cmd == "index":
        reg, md = build_index(a.path)
        doc = registry.document(a.path, reg)
        for name, text in (("INDEX.md", md), ("registry.json", registry.document_text(doc))):
            with open(os.path.join(a.path, name), "w", encoding="utf-8") as f: f.write(text)
        print("indexed %d units -> INDEX.md, registry.json (%d registered)" % (len(reg), len(doc["units"])))
        return 0

if __name__ == "__main__":
    sys.exit(main())
