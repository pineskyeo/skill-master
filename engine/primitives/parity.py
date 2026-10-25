"""The parity primitive (unit A3, plan.md:667, :673; A3 how D4b, D5, D6): the four-tool rows of a skill or an agent,
driven by targets/<tool>.json. Each row's reason is the legacy eva.py text, returned as the op fact {reason}, so review
reasons stay byte-equal (they are hashed into rule_review records). Ops: parity_skill, parity_invocation,
parity_agent_name, parity_wrapper, parity_permissions. Standard library only; no import of eva or skill_eval
(engine/writable.py)."""
import os, re, unicodedata
from engine.loaders import TomlError, parse_frontmatter_ex, parse_toml, yaml_typed_keys
from engine.primitives.script_denylist import WRAPPER_OUT, _within, read_text

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = ("claude", "codex", "opencode", "hermes")
TARGETS_SCHEMA = "schemas/targets.schema.json"
# The AGENT.md vocabulary is tool-independent, so it stays here, pinned equal to eva.py (A3 how D5).
CAPABILITY_BY_TOOL = {}
for _cap, _names in (("read", "read_file read grep glob list_dir ls find search view cat"),
                     ("write", "write_file edit_file write edit multiedit create_file delete_file notebookedit apply_patch"),
                     ("shell", "bash shell terminal exec run_command"),
                     ("web_fetch", "web_fetch webfetch"), ("web_search", "web_search websearch"),
                     ("delegate", "task"), ("skill", "skill")):
    for _n in _names.split():
        CAPABILITY_BY_TOOL[_n] = _cap
NEUTRAL_TOOLS = ("todowrite", "todo_write")
TOOL_ALIASES = {"read": "read", "read_file": "read", "write": "write", "write_file": "write", "edit": "edit",
                "edit_file": "edit", "bash": "shell", "shell": "shell", "websearch": "web_search", "web_search": "web_search",
                "webfetch": "web_fetch", "web_fetch": "web_fetch"}
CLASSES = " (compared by capability class, because OpenCode permission keys are not tool names)"


class TargetError(ValueError):
    pass


_TARGETS, _SCHEMAS = {}, {}


def target(tool, root=ROOT):
    """targets/<tool>.json under root, validated against schemas/targets.schema.json, loaded once per process."""
    key = (root, tool)
    if key not in _TARGETS:
        from engine import schema
        if root not in _SCHEMAS:
            try:
                _SCHEMAS[root] = schema.compile_schema(schema.load(os.path.join(root, *TARGETS_SCHEMA.split("/"))))
            except (schema.JsonError, schema.SchemaRefused) as e:
                raise TargetError("%s: %s" % (TARGETS_SCHEMA, e)) from None
        rel = "targets/%s.json" % tool
        try:
            doc = schema.load(os.path.join(root, "targets", tool + ".json"))
        except schema.JsonError as e:
            raise TargetError("%s: %s" % (rel, e)) from None
        errs = _SCHEMAS[root].validate(doc)
        if errs:
            raise TargetError("%s: %s" % (rel, "; ".join(schema.format_error(e) for e in errs[:3])))
        if doc["tool"] != tool:
            raise TargetError("%s: tool is %r" % (rel, doc["tool"]))
        _TARGETS[key] = doc
    return _TARGETS[key]


_ALL = {}


def targets(root=ROOT):
    """{tool: target} of the four tools, one dict per root, so the rows of one unit are computed once."""
    if root not in _ALL:
        _ALL[root] = {t: target(t, root) for t in TOOLS}
    return _ALL[root]


# ---------- helpers (the eva.py bodies) ----------
def as_list(v):
    if v is None or v == "": return []
    return v if isinstance(v, list) else [v]


def _nonempty_str(v):
    return isinstance(v, str) and bool(v.strip())


def _str_field(meta, key):
    v = meta.get(key)
    return v.strip() if isinstance(v, str) else ""


def _norm_ws(s):
    return " ".join(str(s).split())


def _tag(tools, text):
    return "[%s] %s" % ("/".join(tools), text)


def _frontmatter_of(fp):
    raw, err = read_text(fp)
    if raw is None: return None, None, err
    meta, _, ferr = parse_frontmatter_ex(raw)
    return raw, meta, ferr


def _tool_names(v):
    if isinstance(v, str): v = v.split(",")
    if not isinstance(v, list): return None
    out = [x.strip() for x in v if isinstance(x, str) and x.strip()]
    return out if out and len(out) == len(v) else None


def tool_keys(names):
    keys = {n.strip().lower() for n in names}
    return {TOOL_ALIASES.get(k, k) for k in keys if k not in NEUTRAL_TOOLS}


def capabilities(names):
    caps, bad = set(), []
    for n in names:
        k = n.strip().lower()
        if k in NEUTRAL_TOOLS: continue
        if k in CAPABILITY_BY_TOOL: caps.add(CAPABILITY_BY_TOOL[k])
        else: bad.append(n)
    return caps, bad


def _leaf_paths(node, prefix=""):
    if isinstance(node, dict) and node:
        return [x for k, v in node.items() for x in _leaf_paths(v, "%s%s." % (prefix, k))]
    return [prefix[:-1]] if prefix else []


def _skill_core(meta):
    return ["%s: non-empty string required" % k for k in ("name", "description") if not _nonempty_str(meta.get(k))]


def _nfkc(s):
    return unicodedata.normalize("NFKC", s)


def _lookup(doc, dotted):
    node = doc
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


# ---------- skill rows ----------
def codex_policy(path, tgt):
    """(problems, allow_implicit_invocation, unverified) for the Codex tool file of a skill (eva.codex_policy)."""
    tool_file, verified = tgt["skill"]["tool_file"], tuple(tgt["skill"]["verified_fields"])
    fp = os.path.join(path, *tool_file.split("/"))
    if not os.path.lexists(fp): return [], None, []
    raw, err = read_text(fp)
    if raw is None: return ["%s: %s" % (tool_file, err)], None, []
    if any(ln.rstrip() == "---" for ln in raw.splitlines()):
        return ["%s: YAML document markers ('---') are not supported" % tool_file], None, []
    wrapped = "---\n%s\n---\n" % raw.strip("\n")
    meta, _, ferr = parse_frontmatter_ex(wrapped)
    if meta is None:
        ferr = re.sub(r"frontmatter line (\d+)", lambda m: "line %d" % (int(m.group(1)) - 1), ferr or "")
        return ["%s cannot be parsed by the strict YAML reader: %s" % (tool_file, ferr)], None, []
    unverified = [x for x in _leaf_paths(meta) if x not in verified]
    if "policy" not in meta: return [], None, unverified
    pol = meta["policy"]
    if not isinstance(pol, dict): return ["%s: policy must be a mapping" % tool_file], None, unverified
    if "allow_implicit_invocation" not in pol: return [], None, unverified
    v = pol["allow_implicit_invocation"]
    if "policy.allow_implicit_invocation" not in set(yaml_typed_keys(wrapped)) or str(v).lower() not in ("true", "false"):
        return ["%s: policy.allow_implicit_invocation must be an unquoted boolean true/false, got %r" % (tool_file, v)], \
            None, unverified
    return [], str(v).lower() == "true", unverified


def skill_rows(path, tg, dedupe=True):
    """{row id: (status, reason)} of the six skill parity rows. dedupe False keeps every folder cause, as the A2 rows
    that the skill_eval gates read through eva.parity_report."""
    raw, meta, err = _frontmatter_of(os.path.join(path, "SKILL.md"))
    typed = set(yaml_typed_keys(raw)) if raw is not None and meta else set()
    out = {}

    def row(cid, tools, problems, ok_text):
        out[cid] = ("fail" if problems else "pass", _tag(tools, ok_text if not problems else "; ".join(problems)))
    bad = ["SKILL.md frontmatter unusable: %s" % err] if meta is None else []
    meta = meta or {}
    name = meta.get("name") if isinstance(meta.get("name"), str) else ""
    desc = meta.get("description") if isinstance(meta.get("description"), str) else ""
    cl = tg["claude"]["skill"]
    p = bad + (_skill_core(meta) if not bad else [])
    if not p:
        reserved = [w for w in cl["reserved_words"] if w in name.lower()]
        if reserved: p.append("name contains reserved word(s) %s" % reserved)
        if len(name) > cl["name_max"]: p.append("name exceeds %d characters" % cl["name_max"])
        if len(desc) > cl["description_max"]: p.append("description exceeds %d characters" % cl["description_max"])
        if any(c in name + desc for c in cl["forbidden_chars"]):
            p.append("name or description contains %s" % " or ".join("'%s'" % c for c in cl["forbidden_chars"]))
    row("parity-skill-claude", ("claude",), p, cl["ok"])
    cx = tg["codex"]["skill"]
    pol_problems, allow, unverified = codex_policy(path, tg["codex"])
    p = bad + (_skill_core(meta) if not bad else []) + pol_problems
    if not p and unverified:
        out["parity-skill-codex"] = ("review", "[codex] %s declares fields this package cannot verify: %s (only %s is "
                                     "verified); Codex loadability is unconfirmed"
                                     % (cx["tool_file"], ", ".join(unverified), ", ".join(cx["verified_fields"])))
    else:
        row("parity-skill-codex", ("codex",), p, "SKILL.md satisfies the Codex CLI rules (%s)" % (
            "%s: only the verified field %s" % (cx["tool_file"], ", ".join(cx["verified_fields"]))
            if os.path.lexists(os.path.join(path, *cx["tool_file"].split("/"))) else "no %s" % cx["tool_file"]))
    oc = tg["opencode"]["skill"]
    p = list(bad)
    if not bad:
        p += _skill_core(meta)
        if name and not re.match(oc["name_pattern"], name):
            p.append("name %r must match %s" % (name, oc["name_pattern"]))
        folder = os.path.basename(os.path.realpath(path))
        # in check, name-matches-folder reports every mismatch NFKC can see; only the byte mismatch NFKC hides stays here (A3 D2)
        if name and name != folder and (not dedupe or _nfkc(name.strip()) == _nfkc(folder)):
            p.append("name %r must equal the folder name %r exactly" % (name, folder))
        md = meta.get("metadata")
        if isinstance(md, dict):
            for k, v in md.items():
                if not isinstance(v, str): p.append("metadata.%s must be a string, got %s" % (k, type(v).__name__))
                elif "metadata." + k in typed: p.append("metadata.%s: YAML loads %r as a non-string; quote it" % (k, v))
            av = md.get(oc["autoinvoke_key"])
            if av is not None and av not in oc["autoinvoke_values"]:
                p.append("metadata.%s must be the string %s, got %r" % (
                    oc["autoinvoke_key"], " or ".join('"%s"' % x for x in oc["autoinvoke_values"]), av))
        elif md is not None:
            p.append("metadata must be a mapping of string keys to string values")
    row("parity-skill-opencode", ("opencode",), p, oc["ok"])
    hk = tg["hermes"]["skill"]["metadata_key"]
    p = list(bad)
    if not bad:
        p += _skill_core(meta)
        md = meta.get("metadata")
        if isinstance(md, dict) and hk in md and not isinstance(md[hk], str):
            p.append("metadata.%s must not be a mapping or list in the portable package (OpenCode needs string metadata)" % hk)
    if raw is not None and re.search(r"(?m)^[ \t]+%s:[ \t]*(?:$|[{\[])" % re.escape(hk), raw.split("\n---", 1)[0]):
        p.append("metadata.%s mapping/list is not allowed in the portable package (OpenCode needs string metadata "
                 "values); Hermes uses the plain agentskills.io fields" % hk)
    row("parity-skill-hermes", ("hermes",), sorted(set(p)), tg["hermes"]["skill"]["ok"])
    key = tg["claude"]["invocation"]["key"]
    disabled = key in meta and str(meta[key]).strip().lower() == "true"
    if not disabled:
        out["parity-invocation-control"] = ("na", "[claude/codex/opencode] %s is not used (absent or false)" % key)
        out["parity-invocation-hermes"] = ("na", "[hermes] %s is not used (absent or false)" % key)
        return out
    md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    oi, ci = tg["opencode"]["invocation"], tg["codex"]["invocation"]
    miss = []
    if md.get(oi["metadata_key"]) != oi["value"]:
        miss.append("[opencode] metadata '%s: \"%s\"' (OpenCode ignores %s)" % (oi["metadata_key"], oi["value"], key))
    if allow is not ci["value"]:
        miss.append("[codex] %s with %s: %s" % (ci["tool_file"], ci["field"], "true" if ci["value"] else "false"))
    out["parity-invocation-control"] = ("fail" if miss else "pass", _tag(("claude", "codex", "opencode"),
        "%s: true has the equivalent controls for Codex and OpenCode" % key if not miss else
        "%s: true is only honored by Claude Code; missing: %s" % (key, "; ".join(miss))))
    out["parity-invocation-hermes"] = ("review", "[hermes] %s: true has no verified Hermes equivalent (design 2.4); Hermes "
                                       "may auto-select this skill" % key)
    return out


# ---------- agent wrappers and rows ----------
def _wrapper_common(meta_name, meta_desc, name, desc, body):
    p = []
    if meta_name is not None and meta_name != name: p.append("name %r differs from AGENT.md name %r" % (meta_name, name))
    if _norm_ws(meta_desc or "") != desc: p.append("description differs from AGENT.md")
    if body is not None and not body.strip(): p.append("prompt body is empty")
    return p


def _permission_field(st, tgt, perm, rel, key):
    p, pm = st["problems"], tgt["permissions"]
    grants, actions, granting = pm["grants"], pm["actions"], pm["granting"]
    if not isinstance(perm, dict):
        p.append("%s: %s: a mapping that sets every recorded %s permission key is required" % (rel, key, tgt["label"]))
        return
    closed = [a for a in actions if a not in granting][0]
    for k, cap in grants.items():
        if k not in perm: p.append("%s: %s.%s must be set explicitly" % (rel, key, k))
        elif perm[k] not in actions:
            p.append("%s: %s.%s must be one of %s" % (rel, key, k, "/".join(actions)))
        elif cap is None and perm[k] in granting:
            p.append("%s: %s.%s must be %s, because no capability class covers it" % (rel, key, k, closed))
    unknown = sorted(k for k in perm if k not in grants)
    if unknown: p.append("%s: %s keys %s are not in the recorded %s permission key list" % (rel, key, unknown, tgt["label"]))
    if not any(x.startswith("%s: %s" % (rel, key)) for x in p):
        st["caps"] = {cap for k, cap in grants.items() if cap and perm[k] in granting}


def _frontmatter_wrapper(st, tgt, fp, rel, name, desc):
    p, w = st["problems"], tgt["wrapper"]
    _, m, err = _frontmatter_of(fp)
    if m is None:
        p.append("%s: frontmatter unusable: %s" % (rel, err)); return
    body = parse_frontmatter_ex(read_text(fp)[0])[1]
    for k in w["required"]:
        if not _nonempty_str(m.get(k)): p.append("%s: %s: non-empty string required" % (rel, k))
    p += ["%s: %s" % (rel, x) for x in _wrapper_common(m.get("name") if _nonempty_str(m.get("name")) else None,
                                                        m.get("description"), name, desc, body)]
    for key, f in w["fields"].items():
        kind = f["kind"]
        if kind == "string":
            if key in m and not _nonempty_str(m[key]): p.append("%s: %s must be a non-empty string" % (rel, key))
        elif kind == "boolean":
            if key in m and not isinstance(m[key], bool): p.append("%s: %s must be a boolean" % (rel, key))
        elif kind == "enum":
            if key in m and m[key] not in f["values"]: p.append("%s: %s must be one of %s" % (rel, key, "/".join(f["values"])))
        elif kind == "tools":
            names = _tool_names(m.get(key))
            if names is None: p.append("%s: %s: a non-empty list or comma-separated string is required" % (rel, key))
            else:
                caps, bad = capabilities(names)
                if bad: p.append("%s: %s %s are not in the capability table" % (rel, key, bad))
                else: st["caps"], st["names"] = caps, tool_keys(names)
        else:
            _permission_field(st, tgt, m.get(key), rel, key)


def _toml_wrapper(st, tgt, fp, rel, name, desc):
    p, w = st["problems"], tgt["wrapper"]
    raw_t, err = read_text(fp)
    if raw_t is None:
        p.append(err); return
    try:
        t = parse_toml(raw_t)
    except TomlError as e:
        p.append("%s cannot be parsed by the strict flat-TOML reader: %s" % (rel, e)); return
    for k in w["required"]:
        if not _nonempty_str(t.get(k)): p.append("%s: %s: non-empty string required" % (rel, k))
    p += ["%s: %s" % (rel, x) for x in _wrapper_common(t.get("name") if _nonempty_str(t.get("name")) else None,
                                                        t.get("description"), name, desc, None)]
    for key, f in w["fields"].items():
        found, v = _lookup(t, key)
        if not found: continue
        if f["kind"] == "string" and not _nonempty_str(v): p.append("%s: %s must be a non-empty string" % (rel, key))
        elif f["kind"] == "boolean" and not isinstance(v, bool): p.append("%s: %s must be a boolean" % (rel, key))
    for key, message in w["forbidden"].items():
        if key in t: p.append("%s: %s" % (rel, message))
    known = list(w["required"]) + list(w["fields"])
    unknown = sorted(set(_leaf_paths(t)) - set(known) - set(w["forbidden"]))
    # the parenthesis lists the flat keys only, so a flat wrapper keeps the A2 text byte for byte (A3 D11)
    if unknown: p.append("%s: keys %s are not in the recorded Codex custom-agent key list (%s)"
                         % (rel, unknown, ", ".join(k for k in known if "." not in k)))


def _text_wrapper(st, tgt, path, fp, rel, name, desc, outside):
    p = st["problems"]
    text, err = read_text(fp)
    if text is None:
        p.append(err); return
    if not text.strip(): p.append("%s is empty" % rel)
    else:
        if not re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(name), text): p.append("%s does not state the agent name %r" % (rel, name))
        if desc and desc not in _norm_ws(text): p.append("%s does not contain the AGENT.md description verbatim" % rel)
    for extra in tgt["wrapper"]["extra_files"]:
        cfg = os.path.join(path, *extra["path"].split("/"))
        if os.path.lexists(cfg):
            ctext, cerr = (None, WRAPPER_OUT % extra["path"]) if outside(cfg) else read_text(cfg)
            if ctext is None: p.append(cerr)
            elif extra["non_empty"] and not ctext.strip(): p.append("%s is empty (remove it or fill it)" % extra["path"])
        elif not extra["optional"]:
            p.append("missing wrapper file %s" % extra["path"])


def wrapper_states(path, tg):
    """{tool: {"rel", "problems", "caps", "names"}} for the four wrappers of an agent folder (eva.agent_wrapper_states)."""
    raw, meta, _ = _frontmatter_of(os.path.join(path, "AGENT.md"))
    meta = meta or {}
    name, desc = _str_field(meta, "name"), _norm_ws(_str_field(meta, "description"))
    root, out = os.path.realpath(path), {}
    outside = lambda fp: not _within(root, os.path.realpath(fp))
    for tool in TOOLS:
        tgt = tg[tool]
        rel = tgt["wrapper"]["path"].replace("{name}", name or "<name>")
        st = out[tool] = {"rel": rel, "problems": [], "caps": None, "names": None}
        p, fp = st["problems"], os.path.join(path, *rel.split("/"))
        if not name:
            p.append("AGENT.md has no usable name"); continue
        if not os.path.isfile(fp):
            p.append("missing wrapper file %s" % rel); continue
        if outside(fp):
            p.append(WRAPPER_OUT % rel); continue
        fmt = tgt["wrapper"]["format"]
        if fmt == "frontmatter": _frontmatter_wrapper(st, tgt, fp, rel, name, desc)
        elif fmt == "toml": _toml_wrapper(st, tgt, fp, rel, name, desc)
        else: _text_wrapper(st, tgt, path, fp, rel, name, desc, outside)
    return out


def agent_rows(path, tg):
    """{row id: (status, reason, rel or None)} of the wrapper and permission rows of an agent."""
    _, meta, _ = _frontmatter_of(os.path.join(path, "AGENT.md"))
    meta = meta or {}
    states, out = wrapper_states(path, tg), {}
    for tool in TOOLS:
        st = states[tool]
        out["parity-wrapper-" + tool] = ("fail" if st["problems"] else "pass", _tag((tool,), "%s is present, parseable and "
                                         "consistent with AGENT.md" % st["rel"] if not st["problems"]
                                         else "; ".join(st["problems"])), st["rel"])
    tools = [str(t) for t in as_list(meta.get("tools"))]
    want, unmapped = capabilities(tools)
    for tool in TOOLS:
        st, pm, cid = states[tool], tg[tool]["permissions"], "parity-permissions-" + tool
        tag = lambda text: _tag((tool,), text)
        if pm["fixed"] == "fail":
            out[cid] = ("fail", tag("AGENT.md tools cannot be checked against Hermes: no verified per-agent tool-permission "
                                    "format exists (UNVERIFIED, design 2.4); failing closed, so Hermes is never reported "
                                    "as consistent"), None)
        elif unmapped:
            out[cid] = ("fail", tag("AGENT.md tools %s are not in the capability table; permissions cannot be compared"
                                    % unmapped), None)
        elif pm["compare"] == "none":
            loose = [c for c in pm["unrestrictable"] if c not in want]
            if st["problems"]:
                out[cid] = ("na", tag("wrapper is not usable (see parity-wrapper-%s)" % tool), None)
            elif loose:
                out[cid] = ("review", tag("Codex cannot restrict %s per agent; the agent runs with the parent session's "
                                          "sandbox" % ", ".join(loose)), None)
            else:
                out[cid] = ("pass", tag("AGENT.md tools need %s, so the agent needs no restriction that Codex cannot "
                                        "apply per agent" % ", ".join(pm["unrestrictable"])), None)
        elif st["caps"] is None:
            out[cid] = ("na", tag("wrapper permissions not readable (see parity-wrapper-%s)" % tool), None)
        elif pm["compare"] == "names":
            have, need = sorted(st["names"]), sorted(tool_keys(tools))
            out[cid] = ("pass" if have == need else "fail", tag(
                "wrapper tools %s match the AGENT.md tools by name" % have if have == need else
                "wrapper tools %s differ from the AGENT.md tools %s by name (missing %s, extra %s)" % (
                    have, need, sorted(set(need) - set(have)), sorted(set(have) - set(need)))), None)
        else:
            have, need = sorted(st["caps"]), sorted(want)
            if have != need:
                out[cid] = ("fail", tag("wrapper grants %s, AGENT.md tools need %s%s" % (have, need, CLASSES)), None)
            elif not pm["complete"]:
                out[cid] = ("review", tag("wrapper permissions grant %s, as the AGENT.md tools need, but the OpenCode "
                                          "permission key list is not verified complete; an unlisted key keeps its "
                                          "UNVERIFIED default" % have), None)
            else:
                out[cid] = ("pass", tag("wrapper permissions %s match the AGENT.md tools%s" % (have, CLASSES)), None)
    return out


# ---------- ops ----------
class _ParityCheck:
    def __init__(self, node, root):
        try:
            self.targets = targets(root)
        except TargetError as e:
            raise ValueError(str(e)) from None
        self.node = node

    def rows(self, unit, kind):
        key = ("parity", kind, id(self.targets), unit.dedupe)
        if key not in unit.memo:
            unit.memo[key] = skill_rows(unit.path, self.targets, unit.dedupe) if kind == "skill" else \
                agent_rows(unit.path, self.targets)
        return unit.memo[key]

    def result(self, row):
        facts = {"reason": row[1]}
        if len(row) > 2 and row[2]:
            facts["rel"] = row[2]
        return row[0], facts, None, False


class SkillCheck(_ParityCheck):
    def __call__(self, unit):
        return self.result(self.rows(unit, "skill")["parity-skill-" + self.node["tool"]])


class InvocationCheck(_ParityCheck):
    def __call__(self, unit):
        rid = "parity-invocation-hermes" if self.node["tools"] == ["hermes"] else "parity-invocation-control"
        return self.result(self.rows(unit, "skill")[rid])


class AgentNameCheck(_ParityCheck):
    def __init__(self, node, root):
        _ParityCheck.__init__(self, node, root)
        self.pattern = re.compile(node["pattern"])

    def __call__(self, unit):
        _, meta, _ = _frontmatter_of(os.path.join(unit.path, "AGENT.md"))
        name = _str_field(meta or {}, "name")
        ok = bool(self.pattern.match(name))
        pat = self.pattern.pattern
        return self.result(("pass" if ok else "fail", _tag(TOOLS, "agent name %r is a portable file stem (%s)" % (name, pat)
                                                          if ok else "agent name %r must match %s (it is the wrapper file "
                                                          "name)" % (name, pat))))


class WrapperCheck(_ParityCheck):
    def __call__(self, unit):
        return self.result(self.rows(unit, "agent")["parity-wrapper-" + self.node["tool"]])


class PermissionsCheck(_ParityCheck):
    def __call__(self, unit):
        return self.result(self.rows(unit, "agent")["parity-permissions-" + self.node["tool"]])


OPS = {"parity_skill": SkillCheck, "parity_invocation": InvocationCheck, "parity_agent_name": AgentNameCheck,
       "parity_wrapper": WrapperCheck, "parity_permissions": PermissionsCheck}
