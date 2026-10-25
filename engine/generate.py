"""eva.py generate and generate --check (unit B2, plan.md:948-996). generate renders the four tool files of an AGENT.md v2
agent, or the agents/openai.yaml and the metadata.opencode/autoinvoke line of a skill, from the definition and
targets/*.json, and writes them only after every read, render, self-check and path check passed, so a refusal writes
nothing. outcome() is the one primitive: the expected bytes and the sorted problems that --check prints and that the rule
op generated_up_to_date returns. Standard library only; no import of eva or skill_eval (engine/writable.py)."""
import hashlib, os, re, stat, sys, tempfile
from engine import schema
from engine.loaders import (TomlError, _strip_comment, parse_frontmatter_ex, parse_frontmatter_maps, parse_toml,
                            yaml_typed_keys)
from engine.primitives import parity, transcript

ROOT = parity.ROOT
GENERATOR_VERSION = "1"
TOOLS = parity.TOOLS
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 64
OWNED_DIRS = tuple("wrappers/" + t for t in TOOLS)
IGNORED = ("wrappers/hermes/config.yaml",)
CLUTTER = (".DS_Store",)
TEMP_PREFIX = ".eva-generate-"
SCHEMAS = {"claude": "wrapper-claude.schema.json", "codex": "wrapper-codex.schema.json",
           "opencode": "wrapper-opencode.schema.json", "hermes": "wrapper-hermes.schema.json",
           "openai": "codex-openai-yaml.schema.json"}
HEADER_KEYS = {"source", "digest", "version"}
BODY_KEYS = {"name", "goal", "body", "description"}
FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
BARE_KEY_RE = re.compile(r"^[A-Za-z_][\w./-]*$")
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
ESCAPED_RE = re.compile("[\\\\\"\x00-\x1f\x7f-\x9f\u2028\u2029]")
UNSAFE_LINK = "unsafe path: a symbolic link; generate never writes through a link"
NOT_OURS = "not written by eva.py generate (no generator header); move it away and rerun"


class GenerateError(ValueError):
    pass


# ---------- escapers (one per format, B2 D10) ----------
def _escape(s, keep_newline=False):
    def one(m):
        c = m.group(0)
        if c == "\\": return "\\\\"
        if c == '"': return '\\"'
        if c == "\n" and keep_newline: return "\n"
        return "\\u%04x" % ord(c)
    return ESCAPED_RE.sub(one, s)


def yaml_str(s):
    return '"%s"' % _escape(s)


def yaml_key(s):
    return s if BARE_KEY_RE.match(s) else yaml_str(s)


def toml_str(s):
    return '"%s"' % _escape(s)


def toml_multiline(s):
    return '"""\n%s"""' % _escape(s, keep_newline=True)


def _fill(template, values):
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


# ---------- the engine data ----------
class _Context:
    def __init__(self, root):
        try:
            self.targets = parity.targets(root)
        except parity.TargetError as e:
            raise GenerateError(str(e)) from None
        self.root, self.schemas = root, {}
        for kind, name in SCHEMAS.items():
            try:
                self.schemas[kind] = schema.compile_schema(schema.load(os.path.join(root, "schemas", name)))
            except (schema.JsonError, schema.SchemaRefused) as e:
                raise GenerateError("schemas/%s: %s" % (name, e)) from None
        self.markers = {}
        for tool, tgt in self.targets.items():
            rel, render = "targets/%s.json" % tool, tgt["render"]
            for what, keys in (("header", HEADER_KEYS), ("body", BODY_KEYS)):
                extra = set(PLACEHOLDER_RE.findall(render[what])) - keys
                if extra:
                    raise GenerateError("%s: render.%s uses unknown placeholders %s" % (rel, what, sorted(extra)))
            self.markers[tool] = render["header"].split("{source}")[0].rstrip()
            known = set(tgt["wrapper"]["required"]) | set(tgt["wrapper"]["fields"])
            for role, fields in tgt["models"].items():
                bad = [k for k in fields if k not in known or not FIELD_RE.match(k)]
                if bad:
                    raise GenerateError("%s: models.%s sets %s, which are not wrapper fields" % (rel, role, bad))
        pm = self.targets["opencode"]["permissions"]
        if any(pm["grants"].get(k) != "shell" for k in pm["pattern_keys"]):
            raise GenerateError("targets/opencode.json: permissions.pattern_keys must name the shell key, because "
                                "render.bash_deny is its pattern list")

    def header(self, tool, source, digest):
        return _fill(self.targets[tool]["render"]["header"], {"source": source, "digest": digest,
                                                              "version": GENERATOR_VERSION})

    def owned(self, data, kind=None):
        """True when data starts with a generator header line in the comment form of kind (any form when kind is None)."""
        lines = data.decode("utf-8", "replace").split("\n")[:2]
        lines = [ln.rstrip("\r") for ln in lines] + ["", ""]
        forms = {"md": lines[0] == "---" and any(lines[1].startswith("# " + m) for m in self.markers.values()),
                 "comment": any(lines[0].startswith("# " + m) for m in self.markers.values()),
                 "html": any(lines[0].startswith("<!-- " + m) for m in self.markers.values())}
        want = {"claude": "md", "opencode": "md", "codex": "comment", "openai": "comment", "hermes": "html"}.get(kind)
        return forms[want] if want else any(forms.values())


_CONTEXTS = {}


def context(root=ROOT):
    if root not in _CONTEXTS:
        _CONTEXTS[root] = _Context(root)
    return _CONTEXTS[root]


# ---------- readers of the generated formats (the readers parity uses) ----------
def parse_soul(text):
    """(parts, error) of a SOUL.md in the fixed generated layout: header comment, '# <title>', blank, description, blank,
    body, one trailing newline. The body opens with 'You are <title>.', a blank line and 'Goal: ' (B2 D9), so the
    description ends at the first such opening and may itself hold blank lines."""
    if not text.endswith("\n") or text.endswith("\n\n"):
        return None, "the file must end with exactly one newline"
    lines = text[:-1].split("\n")
    m = re.match(r"^<!-- (.*) -->$", lines[0])
    if not m or len(lines) < 6 or not lines[1].startswith("# ") or lines[2] != "":
        return None, "expected the layout '<!-- header -->', '# <name>', a blank line, the description, a blank line, the body"
    title, rest = lines[1][2:], "\n".join(lines[3:])
    at = rest.find("\n\nYou are %s.\n\nGoal: " % title)
    if at < 0:
        return None, "no blank line and 'You are %s.' between the description and the body" % _ascii(title)
    return {"header": m.group(1), "title": title, "description": rest[:at], "body": rest[at + 2:]}, None


def _typed(meta, typed):
    """meta with the YAML 1.1 booleans of the typed keys as booleans (two levels)."""
    def conv(v, key):
        if key in typed and isinstance(v, str) and v.lower() in ("true", "false"):
            return v.lower() == "true"
        return v
    return {k: ({k2: conv(v2, "%s.%s" % (k, k2)) for k2, v2 in v.items()} if isinstance(v, dict) else conv(v, k))
            for k, v in meta.items()}


def read_generated(ctx, kind, text):
    """(object, body or None, error) of one generated file read with the reader parity uses."""
    if kind == "claude":
        return parse_frontmatter_ex(text)
    if kind == "opencode":
        maps = [k for k, f in ctx.targets["opencode"]["wrapper"]["fields"].items() if f["kind"] == "permission"]
        return parse_frontmatter_maps(text, maps)
    if kind == "codex":
        try:
            return parse_toml(text), None, None
        except TomlError as e:
            return None, None, str(e)
    if kind == "hermes":
        parts, err = parse_soul(text)
        return parts, None, err
    if any(ln.rstrip() == "---" for ln in text.splitlines()):
        return None, None, "YAML document markers ('---') are not supported"
    wrapped = "---\n%s\n---\n" % text.strip("\n")
    meta, _, err = parse_frontmatter_ex(wrapped)
    return (_typed(meta, set(yaml_typed_keys(wrapped))) if meta is not None else None), None, err


def schema_problem(ctx, kind, data):
    """None when the bytes parse and match the closed schema of kind, else the problem text."""
    name = SCHEMAS[kind]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return "closed schema %s: not UTF-8 (%s)" % (name, e.reason)
    obj, _, err = read_generated(ctx, kind, text)
    if obj is None:
        return "closed schema %s: cannot parse: %s" % (name, err)
    errs = ctx.schemas[kind].validate(obj)
    if errs:
        return "closed schema %s: %s" % (name, "; ".join(schema.format_error(e) for e in errs[:3]) +
                                         ("; (+%d more)" % (len(errs) - 3) if len(errs) > 3 else ""))
    return None


# ---------- the outcome of one unit ----------
class Outcome:
    """kind: skill, agent or None. refusal: the refusal text (generate and --check exit 2). na: the D13 n/a cases, with
    na_reason. found: (rel, problem, fixable); a problem generate cannot fix (unsafe path, a file without the generator
    header, an unreadable file) makes generate refuse. writes {rel: bytes}, deletes [rel], modes {rel: mode}: what
    generate does. expected: the rels the render owns. nothing: a skill with nothing to generate."""
    def __init__(self, kind):
        self.kind, self.refusal, self.na, self.na_reason = kind, None, False, ""
        self.found, self.writes, self.deletes, self.modes = [], {}, [], {}
        self.expected, self.nothing, self.source = [], False, ""

    def refuse(self, text, na=False, na_reason=""):
        self.refusal, self.na, self.na_reason = text, na, na_reason

    def add(self, rel, problem, fixable):
        self.found.append((rel, problem, fixable))

    @property
    def problems(self):
        return ["%s: %s" % (rel, text) for rel, text, _ in sorted(set(self.found))]

    @property
    def blocked(self):
        return ["%s: %s" % (rel, text) for rel, text, fix in sorted(set(self.found)) if not fix]


def _ascii(s):
    return s.encode("ascii", "backslashreplace").decode("ascii")


def _name_refusal(name, root_real):
    if not (isinstance(name, str) and NAME_RE.match(name) and len(name) <= NAME_MAX):
        return "refused: name '%s' fails the naming rule (name-format, parity-agent-name)" % _ascii(str(name))
    if name != os.path.basename(root_real):
        return "refused: name '%s' fails the naming rule (name-matches-folder)" % name
    return None


def _check_paths(out, root_real, rels):
    """lstat every existing component of every destination; returns the unsafe components (problems recorded)."""
    unsafe = set()
    for rel in rels:
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            comp = "/".join(parts[:i])
            full = os.path.join(root_real, *parts[:i])
            try:
                mode = os.lstat(full).st_mode
            except FileNotFoundError:
                break
            except OSError as e:
                out.add(comp, "unsafe path: cannot inspect: %s" % (e.strerror or e), False); unsafe.add(comp); break
            why = None
            if stat.S_ISLNK(mode): why = UNSAFE_LINK
            elif i < len(parts) and not stat.S_ISDIR(mode): why = "unsafe path: not a folder"
            elif i == len(parts) and not stat.S_ISREG(mode): why = "unsafe path: not a regular file"
            elif not schema.inside(root_real, os.path.dirname(full)): why = "unsafe path: resolves outside the unit"
            if why:
                out.add(comp, why, False); unsafe.add(comp); break
    return unsafe


def _blocked_by(rel, unsafe):
    return any(rel == u or rel.startswith(u + "/") for u in unsafe)


def _read(root_real, rel):
    with open(os.path.join(root_real, *rel.split("/")), "rb") as f:
        return f.read()


def _compare(ctx, out, root_real, rel, want, kind):
    """Compare one expected file with the folder, bytes first, then its closed schema."""
    full = os.path.join(root_real, *rel.split("/"))
    if not os.path.lexists(full):
        out.add(rel, "missing", True); out.writes[rel] = want; return
    try:
        data = _read(root_real, rel)
    except OSError as e:
        out.add(rel, "cannot read: %s" % (e.strerror or e), False); return
    mine = ctx.owned(data, kind)
    if data != want:
        out.add(rel, "bytes differ from the render" + ("" if mine else "; " + NOT_OURS), mine)
        out.writes[rel] = want
    if kind in SCHEMAS:
        why = schema_problem(ctx, kind, data)
        if why:
            out.add(rel, why, mine)


def _case_twin(root_real, rel, st, expected):
    """The expected rel that rel spells in another case and that is the same file (a case-insensitive volume), or None."""
    for e in expected:
        if e != rel and e.lower() == rel.lower():
            try:
                if os.path.samestat(st, os.lstat(os.path.join(root_real, *e.split("/")))):
                    return e
            except FileNotFoundError:
                pass
    return None


def _unexpected(ctx, out, root_real, unsafe):
    for folder in OWNED_DIRS:
        full = os.path.join(root_real, *folder.split("/"))
        if _blocked_by(folder, unsafe) or not os.path.isdir(full) or os.path.islink(full):
            continue
        for n in sorted(os.listdir(full)):
            rel = folder + "/" + n
            if rel in out.expected or rel in IGNORED or n in CLUTTER:
                continue
            st = os.lstat(os.path.join(full, n))
            if not stat.S_ISREG(st.st_mode):
                out.add(rel, "unexpected entry in a generated folder (not a regular file); move it away and rerun", False)
                continue
            twin = _case_twin(root_real, rel, st, out.expected)
            if twin:
                out.add(rel, "names the same file as %s on this case-insensitive volume; rename it to %s and rerun"
                        % (twin, twin.rsplit("/", 1)[1]), False)
                continue
            try:
                mine = ctx.owned(_read(root_real, rel))
            except OSError as e:
                out.add(rel, "unexpected file in a generated folder; cannot read: %s" % (e.strerror or e), False)
                continue
            out.add(rel, "unexpected file in a generated folder" + ("" if mine else "; " + NOT_OURS), mine)
            if mine:
                out.deletes.append(rel)


# ---------- agents ----------
def _agent_files(ctx, meta, body, digest):
    """{rel: (kind, bytes)} of the four files, and the per-file self-check expectations."""
    name, desc, goal = meta["name"], meta["description"], meta["goal"]
    caps = [c for c in parity.vocabulary() if c in meta["capabilities"]]
    role = meta["model_policy"]
    tg = ctx.targets
    model = {t: [(k, v) for k, v in tg[t]["models"][role].items() if v is not None] for t in TOOLS}
    values = {"name": name, "description": desc, "goal": goal, "body": body}
    text = {t: _fill(tg[t]["render"]["body"], values).rstrip("\n") for t in TOOLS}
    head = {t: ctx.header(t, "AGENT.md", "sha256 " + digest) for t in TOOLS}
    files, expect = {}, {}

    tools = ", ".join(n for c in caps for n in tg["claude"]["render"]["tool_names"][c])
    lines = ["---", "# " + head["claude"], "name: " + yaml_str(name), "description: " + yaml_str(desc),
             "tools: " + yaml_str(tools)] + ["%s: %s" % (k, yaml_str(v)) for k, v in model["claude"]]
    files["wrappers/claude/%s.md" % name] = ("claude", "\n".join(lines) + "\n---\n" + text["claude"] + "\n")
    expect["claude"] = (dict([("name", name), ("description", desc), ("tools", tools)] + model["claude"]), text["claude"])

    shell = "shell" in caps
    lines = ["# " + head["codex"], "name = " + toml_str(name), "description = " + toml_str(desc)]
    lines += ["%s = %s" % (k, toml_str(v)) for k, v in model["codex"]]
    lines += ["developer_instructions = " + toml_multiline(text["codex"]), "", "[features]",
              "shell_tool = " + ("true" if shell else "false")]
    files["wrappers/codex/%s.toml" % name] = ("codex", "\n".join(lines) + "\n")
    expect["codex"] = (dict([("name", name), ("description", desc)] + model["codex"] +
                            [("developer_instructions", text["codex"]), ("features", {"shell_tool": shell})]), None)

    oc = tg["opencode"]
    pm = oc["permissions"]
    allow, deny = pm["granting"][0], [a for a in pm["actions"] if a not in pm["granting"]][0]
    perm = {pm["wildcard"]: deny}
    lines = ["---", "# " + head["opencode"], "description: " + yaml_str(desc), "mode: " + yaml_str(oc["render"]["mode"])]
    lines += ["%s: %s" % (k, yaml_str(v)) for k, v in model["opencode"]]
    lines += ["permission:", "  %s: %s" % (yaml_str(pm["wildcard"]), yaml_str(deny))]
    for k, cap in pm["grants"].items():
        on = cap is not None and cap in caps
        if k in pm["pattern_keys"] and on:
            perm[k] = dict([(pm["wildcard"], allow)] + [(p, deny) for p in oc["render"]["bash_deny"]])
            lines.append("  %s:" % yaml_key(k))
            lines += ["    %s: %s" % (yaml_str(p), yaml_str(v)) for p, v in perm[k].items()]
        else:
            perm[k] = allow if on else deny
            lines.append("  %s: %s" % (yaml_key(k), yaml_str(perm[k])))
    files["wrappers/opencode/%s.md" % name] = ("opencode", "\n".join(lines) + "\n---\n" + text["opencode"] + "\n")
    expect["opencode"] = (dict([("description", desc), ("mode", oc["render"]["mode"])] + model["opencode"] +
                               [("permission", perm)]), text["opencode"])

    files["wrappers/hermes/SOUL.md"] = ("hermes", "<!-- %s -->\n%s\n" % (head["hermes"], text["hermes"]))
    prefix = "# %s\n\n%s\n\n" % (name, desc)
    hbody = text["hermes"][len(prefix):] if text["hermes"].startswith(prefix) else None
    expect["hermes"] = ({"header": head["hermes"], "title": name, "description": desc, "body": hbody}, None)
    return files, expect, caps, head


def _self_check(ctx, files, expect, caps, head):
    """None, or why a rendered file does not read back as its source (B2 D10)."""
    pm = ctx.targets["opencode"]["permissions"]
    allow, deny = pm["granting"][0], [a for a in pm["actions"] if a not in pm["granting"]][0]
    for rel, (kind, text) in sorted(files.items()):
        obj, body, err = read_generated(ctx, kind, text)
        want_obj, want_body = expect[kind]
        if obj is None:
            return "%s: the strict reader refuses it: %s" % (rel, err)
        if obj != want_obj or (want_body is not None and body != want_body):
            return "%s: the parsed fields differ from AGENT.md" % rel
        if kind == "opencode" and list(obj["permission"]) != list(want_obj["permission"]):
            return "%s: the permission keys are not in the target order" % rel
        why = schema_problem(ctx, kind, text.encode("utf-8"))
        if why:
            return "%s: %s" % (rel, why)
        want_head = "<!-- %s -->" % head[kind] if kind == "hermes" else "# " + head[kind]
        if text.split("\n")[1 if kind in ("claude", "opencode") else 0] != want_head or not ctx.owned(text.encode(), kind):
            return "%s: the generator header is not where the format puts it" % rel
        if kind == "claude":
            names = parity._tool_names(obj.get("tools"))
            if not names or parity.capabilities(names) != (set(caps), []):
                return "%s: tools do not map back to exactly the declared capabilities" % rel
        if kind == "codex" and obj["features"]["shell_tool"] is not ("shell" in caps):
            return "%s: features.shell_tool must be false exactly when shell is not declared" % rel
        if kind == "opencode":
            perm = obj["permission"]
            if list(perm)[:1] != [pm["wildcard"]] or perm[pm["wildcard"]] != deny:
                return "%s: the permission map must start with '%s': %s" % (rel, pm["wildcard"], deny)
            for k in pm["pattern_keys"]:
                v = perm.get(k)
                if "shell" in caps:
                    deny_list = ctx.targets["opencode"]["render"]["bash_deny"]
                    if not isinstance(v, dict) or list(v) != [pm["wildcard"]] + deny_list or v[pm["wildcard"]] != allow \
                            or any(v[p] != deny for p in deny_list):
                        return "%s: %s must hold every bash_deny pattern as %s after '%s'" % (rel, k, deny, pm["wildcard"])
                elif v != deny:
                    return "%s: %s must be %s without the shell capability" % (rel, k, deny)
            grant = {k: (v[pm["wildcard"]] if isinstance(v, dict) else v) for k, v in perm.items()}
            granted = {cap for k, cap in pm["grants"].items() if cap and grant.get(k) in pm["granting"]}
            if granted != set(caps) or any(grant.get(k) not in (allow, deny) for k in pm["grants"]) \
                    or grant.get("external_directory", deny) != deny:
                return "%s: the permission map grants %s, not exactly %s" % (rel, sorted(granted), caps)
    return None


def _agent(ctx, out, root_real):
    fp = os.path.join(root_real, "AGENT.md")
    try:
        with open(fp, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeDecodeError) as e:
        out.refuse("refused: AGENT.md frontmatter unusable: cannot read AGENT.md: %s" % _ascii(str(e)), True,
                   "AGENT.md is unreadable, so nothing is generated (see agent-frontmatter-v2)")
        return
    agent = transcript.parse_agent(text, ctx.root)
    if agent.v1:
        out.refuse("refused: AGENT.md v1: %s\n%s" % (agent.detail, transcript.MIGRATION), True,
                   "AGENT.md v1, so nothing is generated until agent-frontmatter-v2 passes")
        return
    if not agent.valid:
        out.refuse("refused: %s: %s" % (agent.version, agent.detail), True,
                   "%s, so nothing is generated until agent-frontmatter-v2 passes" % agent.version)
        return
    meta = agent.meta
    why = _name_refusal(meta["name"], root_real)
    if why:
        out.refuse(why); return
    role = meta["model_policy"]
    for tool in TOOLS:
        models = ctx.targets[tool]["models"]
        if role not in models:
            out.refuse("refused: model_policy '%s' has no model map in targets/%s.json (roles: %s)"
                       % (role, tool, ", ".join(sorted(models)) or "none"))
            return
    body = parse_frontmatter_ex(text)[1].strip("\n")
    files, expect, caps, head = _agent_files(ctx, meta, body, hashlib.sha256(raw).hexdigest())
    why = _self_check(ctx, files, expect, caps, head)
    if why:
        out.refuse("refused: self-check failed for " + why); return
    out.expected, out.source = sorted(files), "AGENT.md"
    unsafe = _check_paths(out, root_real, out.expected)
    for rel in out.expected:
        if not _blocked_by(rel, unsafe):
            kind, text_out = files[rel]
            _compare(ctx, out, root_real, rel, text_out.encode("utf-8"), kind)
    _unexpected(ctx, out, root_real, unsafe)


# ---------- skills ----------
_METADATA_RE = re.compile(r"^metadata:(?:\s+(.*))?$")


def _skill_lines(raw):
    """(lines, closing index, error) of a SKILL.md that generate may edit: LF only, no tab in the frontmatter."""
    if b"\r" in raw:
        return None, None, "SKILL.md uses CR line endings; generate edits LF files only"
    text = raw.decode("utf-8")
    lines = text.split("\n")
    if text.splitlines() != (lines[:-1] if lines[-1] == "" else lines):
        return None, None, "SKILL.md holds a line break other than LF"
    end = lines.index("---", 1)
    if any("\t" in ln for ln in lines[1:end]):
        return None, None, "SKILL.md has a tab in its frontmatter; generate edits space-indented files only"
    return lines, end, None


def _metadata_block(lines, end):
    """(metadata line index or None, its child line indexes, error)."""
    mi = next((i for i in range(1, end) if _METADATA_RE.match(lines[i])), None)
    if mi is None:
        return None, [], None
    if _strip_comment(_METADATA_RE.match(lines[mi]).group(1) or ""):
        return None, [], "SKILL.md metadata is a scalar, list or flow mapping; generate needs a block mapping"
    j = mi + 1
    while j < end and (not lines[j].strip() or lines[j][0] in " #"):
        j += 1
    kids = [k for k in range(mi + 1, j) if lines[k].strip() and not lines[k].lstrip().startswith("#")]
    if any(lines[k].lstrip().startswith("- ") for k in kids):
        return None, [], "SKILL.md metadata is a list; generate needs a block mapping"
    return mi, kids, None


def _key_line(lines, kids, key):
    pat = re.compile(r"^( +)%s:(?:\s+(.*))?$" % re.escape(key))
    return next((k for k in kids if pat.match(lines[k])), None)


def _one_owned_change(old, new, entry):
    """True when new is old with exactly the one owned line replaced or inserted, or the metadata block appended."""
    if old == new:
        return True
    if len(new) == len(old):
        diff = [i for i in range(len(old)) if old[i] != new[i]]
        return len(diff) == 1 and new[diff[0]].strip() == entry
    for n in (1, 2):
        if len(new) == len(old) + n:
            i = next((k for k in range(len(old)) if old[k] != new[k]), len(old))
            ins = new[i:i + n]
            return new[:i] + new[i + n:] == old and ins[-1].strip() == entry and (n == 1 or ins[0] == "metadata:")
    return False


def _skill_edit(raw, meta, key, value, put):
    """(new bytes, error) of SKILL.md with the owned line set (put) or removed."""
    lines, end, err = _skill_lines(raw)
    if err:
        return None, err
    mi, kids, err = _metadata_block(lines, end)
    if err:
        return None, err
    if mi is not None and not isinstance(meta.get("metadata"), (dict, type(None))):
        return None, "SKILL.md metadata is not a mapping"
    entry = "%s: %s" % (key, yaml_str(value))
    hit = _key_line(lines, kids, key) if mi is not None else None
    new = list(lines)
    if put:
        if mi is None:
            # A clip block scalar that ends the frontmatter reads without its final newline (loaders._block_scalar eof),
            # so when the block appended last does not read back, it goes before the last top-level key instead.
            tops = [i for i in range(1, end) if lines[i][:1] not in ("", " ", "#")]
            tries = [lines[:at] + ["metadata:", "  " + entry] + lines[at:] for at in [end] + tops[-1:]]
        elif hit is not None:
            new[hit] = re.match(r"^( +)", lines[hit]).group(1) + entry
            tries = [new]
        else:
            indent = re.match(r"^( +)", lines[kids[0]]).group(1) if kids else "  "
            at = (kids[-1] if kids else mi) + 1
            new[at:at] = [indent + entry]
            tries = [new]
        want = dict(meta)
        want["metadata"] = dict(meta.get("metadata") or {}, **{key: value})
    else:
        if hit is None:
            return None, "SKILL.md metadata.%s is not a plain 'key: value' line generate can remove" % key
        gone = [mi, hit] if len(kids) == 1 else [hit]
        tries = [[ln for i, ln in enumerate(lines) if i not in gone]]
        want = dict(meta)
        rest = dict(meta.get("metadata") or {})
        rest.pop(key, None)
        if len(kids) == 1:
            want.pop("metadata", None)
        else:
            want["metadata"] = rest
    old_body, first = parse_frontmatter_ex(raw.decode("utf-8"))[1], None
    for new in tries:
        text = "\n".join(new)
        got, body, perr = parse_frontmatter_ex(text)
        if put and not _one_owned_change(lines, new, entry):
            err = "SKILL.md edit would change more than the one owned line"
        elif got != want or body != old_body:
            err = "SKILL.md does not read back with only the owned line changed (%s)" % (perr or "fields differ")
        elif put and "metadata." + key in yaml_typed_keys(text):
            err = "SKILL.md metadata.%s would read as a YAML-typed value" % key
        else:
            return text.encode("utf-8"), None
        first = first or err
    return None, first


def _skill(ctx, out, root_real):
    fp = os.path.join(root_real, "SKILL.md")
    try:
        with open(fp, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeDecodeError) as e:
        out.refuse("refused: SKILL.md frontmatter unusable: cannot read SKILL.md: %s" % _ascii(str(e)), True,
                   "SKILL.md is unreadable, so nothing is generated")
        return
    meta, _, err = parse_frontmatter_ex(text)
    if meta is None:
        out.refuse("refused: SKILL.md frontmatter unusable: %s" % err, True,
                   "SKILL.md frontmatter unusable, so nothing is generated")
        return
    flag_key = ctx.targets["claude"]["invocation"]["key"]
    flag = flag_key in meta and str(meta[flag_key]).strip().lower() == "true"
    oinv, cinv = ctx.targets["opencode"]["invocation"], ctx.targets["codex"]["invocation"]
    akey, aval = oinv["metadata_key"], oinv["value"]
    md = meta.get("metadata")
    leftover = isinstance(md, dict) and md.get(akey) == aval
    yaml_rel = cinv["tool_file"]
    unsafe = _check_paths(out, root_real, ["SKILL.md", yaml_rel])
    y_mine = False
    if not _blocked_by(yaml_rel, unsafe) and os.path.isfile(os.path.join(root_real, *yaml_rel.split("/"))):
        try:
            y_mine = ctx.owned(_read(root_real, yaml_rel), "openai")
        except OSError:
            y_mine = False
    out.source = "SKILL.md %s: true" % flag_key
    if not flag:
        out.nothing = True
        if not leftover and not y_mine and not unsafe:
            out.na, out.na_reason = True, "%s is not true and no generated file remains" % flag_key
            return
        why = _name_refusal(meta.get("name"), root_real)
        if why:
            out.refuse(why); return
        if y_mine:
            out.add(yaml_rel, "generated file remains while %s is not true" % flag_key, True)
            out.deletes.append(yaml_rel)
        if leftover and not _blocked_by("SKILL.md", unsafe):
            new, err = _skill_edit(raw, meta, akey, aval, put=False)
            if err:
                out.refuse("refused: " + err); return
            out.add("SKILL.md", 'metadata.%s: "%s" remains while %s is not true' % (akey, aval, flag_key), True)
            out.writes["SKILL.md"], out.modes["SKILL.md"] = new, stat.S_IMODE(os.stat(fp).st_mode)
        return
    why = _name_refusal(meta.get("name"), root_real)
    if why:
        out.refuse(why); return
    new, err = _skill_edit(raw, meta, akey, aval, put=True)
    if err:
        out.refuse("refused: " + err); return
    parts = cinv["field"].split(".")
    value = "true" if cinv["value"] else "false"
    head = ctx.header("codex", "SKILL.md", "%s: true" % flag_key)
    yaml_text = "# %s\n%s:\n  %s: %s\n" % (head, parts[0], parts[1], value)
    obj, _, perr = read_generated(ctx, "openai", yaml_text)
    if obj != {parts[0]: {parts[1]: cinv["value"]}} or schema_problem(ctx, "openai", yaml_text.encode("utf-8")) \
            or not ctx.owned(yaml_text.encode("utf-8"), "openai"):
        out.refuse("refused: self-check failed for %s: %s" % (yaml_rel, perr or "it does not read back")); return
    out.expected = sorted(["SKILL.md", yaml_rel])
    if not _blocked_by("SKILL.md", unsafe) and new != raw:
        out.add("SKILL.md", 'metadata.%s must be "%s" (the line eva.py generate owns)' % (akey, aval), True)
        out.writes["SKILL.md"], out.modes["SKILL.md"] = new, stat.S_IMODE(os.stat(fp).st_mode)
    if not _blocked_by(yaml_rel, unsafe):
        _compare(ctx, out, root_real, yaml_rel, yaml_text.encode("utf-8"), "openai")


def outcome(path, root=ROOT):
    """The Outcome of the unit folder path: refusal, problems and the writes generate would do. Writes nothing."""
    ctx = context(root)
    if not os.path.isdir(path):
        out = Outcome(None)
        out.refuse("refused: %s is not a folder" % path)
        return out
    root_real = os.path.realpath(path)
    kind = "skill" if os.path.isfile(os.path.join(path, "SKILL.md")) else \
        "agent" if os.path.isfile(os.path.join(path, "AGENT.md")) else None
    out = Outcome(kind)
    if kind is None:
        out.refuse("refused: %s holds no SKILL.md or AGENT.md" % path)
        return out
    source = "SKILL.md" if kind == "skill" else "AGENT.md"
    if os.path.islink(os.path.join(root_real, source)):
        out.refuse("refused: %s: unsafe path: a symbolic link; generate reads its source only from a regular file in "
                   "the unit folder" % source)
        return out
    try:
        (_skill if kind == "skill" else _agent)(ctx, out, root_real)
    except OSError as e:
        out.refuse("refused: I/O error: %s" % _ascii(str(e)))
    return out


# ---------- writing ----------
def _make_folders(root_real, rel):
    parts = rel.split("/")[:-1]
    for i in range(1, len(parts) + 1):
        full = os.path.join(root_real, *parts[:i])
        if not os.path.lexists(full):
            os.mkdir(full)


def _apply(out, root_real):
    """(done rels, error or None): every write through a temp file and os.replace, then every delete."""
    done, rel = [], None
    try:
        for rel in sorted(out.writes):
            full = os.path.join(root_real, *rel.split("/"))
            _make_folders(root_real, rel)
            fd, tmp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".tmp", dir=os.path.dirname(full))
            try:
                with os.fdopen(fd, "wb") as f:
                    os.fchmod(f.fileno(), out.modes.get(rel, 0o644))
                    f.write(out.writes[rel])
                os.replace(tmp, full)
            except BaseException:
                if os.path.lexists(tmp):
                    os.remove(tmp)
                raise
            done.append(rel)
        for rel in sorted(out.deletes):
            os.remove(os.path.join(root_real, *rel.split("/")))
            done.append(rel)
    except OSError as e:
        return done, "cannot write %s: %s" % (rel, e.strerror or e)
    return done, None


def cli(path, check=False, root=ROOT):
    """python3 eva.py generate PATH [--check]: 0 written or up to date, 1 (--check) a problem, 2 every refusal."""
    from engine import rule_runner
    try:
        rule_runner.default_bundle(root)
        out = outcome(path, root)
    except rule_runner.BundleError as e:
        print("bundle error: %s" % e, file=sys.stderr)
        return 2
    except GenerateError as e:
        print("refused: %s" % e, file=sys.stderr)
        return 2
    if out.refusal:
        print(out.refusal, file=sys.stderr)
        return 2
    if check:
        if out.problems:
            for line in out.problems:
                print(line)
            print("generate --check: %d problem(s) in %s; run python3 eva.py generate %s" % (len(out.problems), path, path))
            return 1
        print("nothing to generate" if out.nothing else "generate --check: up to date: %s" % ", ".join(out.expected))
        return 0
    if out.blocked:
        for line in out.blocked:
            print("refused: %s" % line, file=sys.stderr)
        return 2
    done, err = _apply(out, os.path.realpath(path))
    for rel in done:
        print("%s %s" % ("removed" if rel in out.deletes else "wrote", rel))
    if err:
        print("error: %s; already replaced: %s" % (err, ", ".join(done) or "none"), file=sys.stderr)
        return 2
    if out.nothing:
        print("nothing to generate")
    else:
        n = len([r for r in done if r not in out.deletes])
        print("generate: %s: %d written, %d removed, %d unchanged" % (path, n, len(done) - n, len(out.expected) - n))
    return 0


# ---------- the rule op ----------
class GeneratedCheck:
    """op generated_up_to_date (scope wrappers or tool-metadata): the problems of outcome() as the row reason (B2 D13)."""
    SCOPES = {"wrappers": "agent", "tool-metadata": "skill"}

    def __init__(self, node, root):
        if node.get("scope") not in self.SCOPES:
            raise ValueError("generated_up_to_date: scope must be one of %s, got %r" % (sorted(self.SCOPES),
                                                                                       node.get("scope")))
        self.kind, self.root = self.SCOPES[node["scope"]], root
        context(root)

    def __call__(self, unit):
        key = ("generated", self.root)
        if key not in unit.memo:
            unit.memo[key] = outcome(unit.path, self.root)
        out = unit.memo[key]
        if out.kind != self.kind:
            return "na", {"reason": "not a%s %s unit" % ("n" if self.kind == "agent" else "", self.kind)}, None, False
        if out.refusal:
            if out.na:
                return "na", {"reason": out.na_reason}, None, False
            return "fail", {"reason": out.refusal.split("\n")[0]}, None, False
        if out.problems:
            return "fail", {"reason": "; ".join(out.problems)}, None, False
        if out.na:
            return "na", {"reason": out.na_reason}, None, False
        return "pass", {"reason": "%s equal what eva.py generate renders from %s (generator %s)"
                                  % (", ".join(out.expected), out.source, GENERATOR_VERSION)}, None, False
