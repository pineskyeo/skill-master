"""Unit A3 (plan.md:658-706): per-tool targets and their schema, the TOML reader with tables and dotted keys, the
script_denylist primitive and its data, the english-ruleset repository rule, row provenance, the Codex [features]
contract of lanes 2 and 3, the deep-JSON fix (D9), the naming dedupe of lane 7, and the runner extensions (D4). Every unit,
bundle and repository copy lives in a temp folder; nothing runs a model or a tool binary. ASCII only: non-English text is
built from escapes."""
import ast, contextlib, hashlib, inspect, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import eva, skill_eval
from engine import loaders, rule_runner as RR, schema as S
import test_multi_tool_parity as P
import test_codex_permissions as C

TOOLS = ("claude", "codex", "opencode", "hermes")
KO = "\ud55c\uad6d\uc5b4"
GREEK = "\u03b1\u03b2\u03b3"
CODEX_REL = "wrappers/codex/code-scout.toml"
NO_SHELL_MD = P.AGENT_MD
A3_RULE_FILES = ("naming.rules.json", "english-only.rules.json", "parity-skill.rules.json", "parity-agent.rules.json")
RULE_ROW_PREFIXES = ("name-", "english-", "parity-")
# sha256 of the A2 source segments of parse_flat_toml and _TomlReader (computed from the A2 export by
# /tmp/a3-owner/pins.py, /tmp/a3-owner/pins.txt).
TOML_PINS = {"parse_flat_toml": "447a50dea639c7502addb10b16f169da473a06839544cc28198d31b6dda7edfe",
             "_TomlReader": "753956049e2a50ae4520b227c7caf8ff084976d6ecd5384e0d37298f534ae169"}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def quiet(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a)
    return rc, out.getvalue(), err.getvalue()


def parity():
    from engine.primitives import parity as PR
    return PR


def denylist():
    from engine.primitives import script_denylist as SD
    return SD


def row(r, cid):
    rows = [c for c in r.checks if c["id"] == cid]
    if len(rows) != 1:
        raise AssertionError("%d rows for %s" % (len(rows), cid))
    return rows[0]


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def repo_copy(self, folders=("rules", "schemas", "targets", "policy")):
        """A temp repository root holding copies of the engine folders a bundle load reads."""
        root = os.path.join(self.tmp, "repo")
        for name in folders:
            if os.path.isdir(os.path.join(ROOT, name)):
                shutil.copytree(os.path.join(ROOT, name), os.path.join(root, name))
        return root

    def agent(self, md=NO_SHELL_MD, codex=None, name="code-scout"):
        d = os.path.join(self.tmp, "agents", name)
        write(os.path.join(d, "AGENT.md"), md)
        write(os.path.join(d, "scenarios", "01.json"), json.dumps(P.S1))
        write(os.path.join(d, "scenarios", "02.json"), json.dumps(P.S2))
        P.write_wrappers(d, replace={CODEX_REL: codex} if codex is not None else None)
        return d

    def skill(self, name="word-count", folder=None, extra="", files=None):
        d = os.path.join(self.tmp, "skills", folder or name)
        write(os.path.join(d, "SKILL.md"), "---\nname: %s\ndescription: Counts words in one text file. Use when the "
              "user asks for a word count.\n%s---\n# Word Count\n## Inputs\n- path\n## Outputs\n- count\n## Example\n"
              "Input a.txt, output 3.\n" % (name, extra))
        for rel, text in (files or {}).items():
            write(os.path.join(d, *rel.split("/")), text)
        return d


def leaves(node, path=()):
    """(dotted path, value) of every leaf of a target: a scalar, a list of scalars, or an empty object."""
    if isinstance(node, dict) and node:
        for k, v in node.items():
            yield from leaves(v, path + (k,))
    elif isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
        for i, v in enumerate(node):
            yield from leaves(v, path + (str(i),))
    else:
        yield ".".join(path), node


# The dated source lines of the recorded doc URLs (inputs/agent-master-skills.md:338-342, section 9).
RECORDED_DOCS = {
    "https://code.claude.com/docs/en/sub-agents":
        "- Claude Code subagents: https://code.claude.com/docs/en/sub-agents (VERIFIED 2026-10-19)",
    "https://developers.openai.com/codex/subagents":
        "- Codex subagents / custom agents: https://developers.openai.com/codex/subagents (VERIFIED 2026-10-19)",
    "https://opencode.ai/docs/agents/": "- OpenCode agents: https://opencode.ai/docs/agents/ (VERIFIED 2026-10-19)",
    "https://hermes-agent.nousresearch.com/docs/user-guide/profiles":
        "- Hermes profiles: https://hermes-agent.nousresearch.com/docs/user-guide/profiles ; tools: "
        "https://hermes-agent.nousresearch.com/docs/user-guide/features/tools (VERIFIED 2026-10-19)",
    "https://hermes-agent.nousresearch.com/docs/user-guide/features/tools":
        "- Hermes profiles: https://hermes-agent.nousresearch.com/docs/user-guide/profiles ; tools: "
        "https://hermes-agent.nousresearch.com/docs/user-guide/features/tools (VERIFIED 2026-10-19)",
}
NOTE9 = "inputs/agent-master-skills.md section 9"


def doc_pins(docs):
    out = {}
    for i, (url, section, verified, note) in enumerate(docs):
        if verified:
            assert url in RECORDED_DOCS and note == NOTE9, url
        out.update({"docs.%d.url" % i: (url, None), "docs.%d.section" % i: (section, None),
                    "docs.%d.verified" % i: (verified, None), "docs.%d.note" % i: (note, None)})
    return out


# The A4 eva.OPENCODE_PERMISSION_GRANTS (now kept by tests/differential for the legacy bodies) with glob, grep and list
# granting search (unit B1 D6).
B1_OPENCODE_GRANTS = {"read": "read", "edit": "write", "glob": "search", "grep": "search", "list": "search", "bash": "shell",
                      "task": "delegate", "external_directory": None, "webfetch": "web_fetch", "websearch": "web_search",
                      "lsp": "read", "skill": "skill"}
# Unit B1 (D7, D9) changes on purpose the agent rows that read AGENT.md v2 fields and the permission reasons.
B1_AGENT_ROWS = ("agent-frontmatter-v2", "success-measurable", "tools-declared", "tools-subset", "scenarios-present",
                 "scenarios-valid", "scenarios-expected", "multi-turn-scenario", "parity-permissions-")


def target_pins():
    """{tool: {leaf path: (value, legacy source text or None)}}: every target field with the legacy eva.py constant or
    the legacy body line it replaces, so data and oracle cannot drift. Empty values (no derive table, no extra file)
    are what the legacy branch of that tool does without; derive.shell is the D1 record."""
    common = {t: {"tool": (t, None), "label": (eva.TOOL_LABELS[t], None), "engine": (skill_eval.TOOL_ENGINES[t], None)}
              for t in TOOLS}
    ok_claude = "SKILL.md satisfies the Claude Code rules (fields, reserved words, limits)"
    ok_opencode = "SKILL.md satisfies the OpenCode rules (name regex, folder name, string metadata)"
    ok_hermes = "SKILL.md satisfies the Hermes Agent baseline (agentskills.io fields, no metadata.hermes mapping)"
    claude = dict(common["claude"], **doc_pins([
        ("https://code.claude.com/docs/en/sub-agents", "Claude Code sub-agents", "2026-10-19", NOTE9),
        ("https://code.claude.com/docs/en/skills", "skills frontmatter reference", None,
         "cited at eva.py HOST_SKILL_FIELDS; no dated record in the repository or the plan inputs")]), **{
        "skill.reserved_words": (list(eva.RESERVED_NAME_WORDS), None), "skill.name_max": (eva.NAME_MAX, None),
        "skill.description_max": (eva.DESCRIPTION_MAX, None), "skill.forbidden_chars": ("<>", 'for c in "<>"'),
        "skill.ok": (ok_claude, ok_claude), "invocation.key": ("disable-model-invocation", '"disable-model-invocation" in meta'),
        "invocation.honored": (True, "is only honored by Claude Code"),
        "wrapper.path": ("wrappers/claude/{name}.md", '"claude": "wrappers/claude/%s.md"'),
        "wrapper.format": ("frontmatter", 'if tool in ("claude", "opencode"):'),
        "wrapper.required": (["name", "description"], 'for k in ("name", "description"):'),
        "wrapper.fields.tools.kind": ("tools", 'names = _tool_names(m.get("tools"))'),
        "wrapper.fields.model.kind": ("string", '"%s: model must be a non-empty string"'),
        "wrapper.forbidden": ({}, None), "wrapper.unknown_keys": ("allow", None), "wrapper.extra_files": ([], None),
        "permissions.compare": ("tool-classes", None),
        "permissions.derive": ({}, None), "permissions.unrestrictable": ([], None), "permissions.fixed": (None, None)})
    codex = dict(common["codex"], **doc_pins([
        ("https://developers.openai.com/codex/subagents", "Codex custom agents", "2026-10-19", NOTE9),
        ("https://developers.openai.com/codex/skills", "skills and agents/openai.yaml", None,
         "inputs/design.md section 2.2 lists it with no date")]), **{
        "skill.tool_file": (eva.OPENAI_YAML, None), "skill.verified_fields": (list(eva.CODEX_VERIFIED_FIELDS), None),
        "invocation.tool_file": (eva.OPENAI_YAML, None),
        "invocation.field": ("policy.allow_implicit_invocation", "with policy.allow_implicit_invocation: false"),
        "invocation.value": (False, "if allow is not False:"),
        "wrapper.path": ("wrappers/codex/{name}.toml", '"codex": "wrappers/codex/%s.toml"'),
        "wrapper.format": ("toml", "t = parse_flat_toml(raw_t)"),
        "wrapper.required": (list(eva.CODEX_AGENT_REQUIRED_KEYS), None),
        "wrapper.fields.%s.kind" % eva.CODEX_AGENT_OPTIONAL_KEYS[0]: ("string", None),
        "wrapper.fields.%s.kind" % eva.CODEX_AGENT_OPTIONAL_KEYS[1]: ("string", None),
        "wrapper.fields.features.shell_tool.kind": ("boolean", None),
        "wrapper.forbidden.sandbox_mode": (C.REMOVE, C.REMOVE),
        "wrapper.unknown_keys": ("fail", "are not in the recorded Codex custom-agent key list"),
        "wrapper.extra_files": ([], None), "permissions.compare": ("none", 'elif tool == "codex":'),
        "permissions.derive.shell.key": ("features.shell_tool", None), "permissions.derive.shell.off": ([False], None),
        "permissions.derive.shell.verified": (False, None),
        "permissions.derive.shell.evidence": ("<plan>/prototypes/codex-sandbox/shell-tool.txt does not exist: the C2 probe "
                                              "never ran (A3 how D1)", None),
        "permissions.unrestrictable": (list(eva.CODEX_UNRESTRICTABLE), None), "permissions.fixed": (None, None)})
    opencode = dict(common["opencode"], **doc_pins([
        ("https://opencode.ai/docs/agents/", "OpenCode agents and permissions", "2026-10-19", NOTE9),
        ("https://opencode.ai/docs/skills/", "skills name and metadata rules", None,
         "inputs/design.md section 2.3 lists it with no date")]), **{
        "skill.name_pattern": (eva.PORTABLE_NAME_RE.pattern, None), "skill.folder": ("exact", "if name and name != folder:"),
        "skill.metadata": ("strings", "must be a string, got %s"), "skill.autoinvoke_key": (eva.OPENCODE_AUTOINVOKE_KEY, None),
        "skill.autoinvoke_values": (["true", "false"], 'av not in ("true", "false")'), "skill.ok": (ok_opencode, ok_opencode),
        "invocation.metadata_key": (eva.OPENCODE_AUTOINVOKE_KEY, None),
        "invocation.value": ("false", "md.get(OPENCODE_AUTOINVOKE_KEY) != \"false\""),
        "wrapper.path": ("wrappers/opencode/{name}.md", '"opencode": "wrappers/opencode/%s.md"'),
        "wrapper.format": ("frontmatter", 'if tool in ("claude", "opencode"):'),
        "wrapper.required": (["description"], '"%s: description: non-empty string required"'),
        "wrapper.fields.name.kind": ("string", '"%s: name must be a non-empty string"'),
        "wrapper.fields.mode.kind": ("enum", 'm["mode"] not in OPENCODE_MODES'),
        "wrapper.fields.mode.values": (list(eva.OPENCODE_MODES), None),
        "wrapper.fields.model.kind": ("string", '"%s: model must be a non-empty string"'),
        "wrapper.fields.permission.kind": ("permission", 'perm = m.get("permission")'),
        "wrapper.forbidden": ({}, None), "wrapper.unknown_keys": ("allow", None), "wrapper.extra_files": ([], None),
        "permissions.compare": ("classes", "compared by capability class"),
        "permissions.actions": (list(eva.PERMISSION_ACTIONS), None),
        "permissions.granting": (["allow", "ask"], 'perm[k] != "deny"'), "permissions.derive": ({}, None),
        "permissions.unrestrictable": ([], None), "permissions.complete": (eva.OPENCODE_PERMISSION_KEYS_COMPLETE, None),
        "permissions.fixed": (None, None)})
    opencode.update({"permissions.grants." + k: (v, None) for k, v in B1_OPENCODE_GRANTS.items()})
    hermes = dict(common["hermes"], **doc_pins([
        ("https://hermes-agent.nousresearch.com/docs/user-guide/profiles", "Hermes profiles (SOUL.md, config.yaml)",
         "2026-10-19", NOTE9),
        ("https://hermes-agent.nousresearch.com/docs/user-guide/features/tools", "Hermes tools", "2026-10-19", NOTE9)]), **{
        "skill.metadata_key": ("hermes", '"hermes" in md'), "skill.ok": (ok_hermes, ok_hermes),
        "invocation.verified": (False, "has no verified Hermes equivalent"),
        "wrapper.path": ("wrappers/hermes/SOUL.md", '"hermes": "wrappers/hermes/SOUL.md"'),
        "wrapper.format": ("text", "if not text.strip(): p.append(\"%s is empty\" % rel)"),
        "wrapper.required": ([], None), "wrapper.fields": ({}, None), "wrapper.forbidden": ({}, None),
        "wrapper.extra_files.0.path": ("wrappers/hermes/config.yaml", 'os.path.join(path, "wrappers", "hermes", "config.yaml")'),
        "wrapper.extra_files.0.optional": (True, "if os.path.lexists(cfg):"),
        "wrapper.extra_files.0.non_empty": (True, "is empty (remove it or fill it)"),
        "permissions.compare": ("none", None), "permissions.derive": ({}, None), "permissions.unrestrictable": ([], None),
        "permissions.fixed": ("fail", "failing closed, so Hermes is never reported as consistent")})
    return {"claude": claude, "codex": codex, "opencode": opencode, "hermes": hermes}


class TestTargets(Tmp):
    def target(self, tool):
        return load_json(os.path.join(ROOT, "targets", tool + ".json"))

    def test_every_target_validates_against_the_schema(self):
        compiled = S.compile_schema(S.load(os.path.join(ROOT, "schemas", "targets.schema.json")))
        for tool in TOOLS:
            self.assertEqual(compiled.validate(self.target(tool)), [], tool)
            self.assertEqual(parity().target(tool)["tool"], tool)

    def test_the_schema_is_closed(self):
        compiled = S.compile_schema(S.load(os.path.join(ROOT, "schemas", "targets.schema.json")))
        for tool in TOOLS:
            doc = self.target(tool)
            doc["extra"] = 1
            self.assertTrue(compiled.validate(doc), tool)
            doc = self.target(tool)
            doc["wrapper"]["extra"] = 1
            self.assertTrue(compiled.validate(doc), tool)
            doc = self.target(tool)
            del doc["permissions"]
            self.assertTrue(compiled.validate(doc), tool)

    def test_common_values_equal_the_legacy_constants(self):
        for tool in TOOLS:
            t = self.target(tool)
            self.assertEqual(t["label"], eva.TOOL_LABELS[tool])
            self.assertEqual(t["engine"], skill_eval.TOOL_ENGINES[tool])
            self.assertEqual(t["wrapper"]["path"].replace("{name}", "x-y"), eva._wrapper_rel(tool, "x-y"))
            self.assertEqual("{name}" in t["wrapper"]["path"], tool != "hermes")

    def test_claude_values(self):
        t = self.target("claude")
        self.assertEqual(t["skill"]["reserved_words"], list(eva.RESERVED_NAME_WORDS))
        self.assertEqual((t["skill"]["name_max"], t["skill"]["description_max"]), (eva.NAME_MAX, eva.DESCRIPTION_MAX))
        self.assertEqual(t["skill"]["forbidden_chars"], "<>")
        self.assertEqual(t["wrapper"]["required"], ["name", "description"])
        self.assertEqual(list(t["wrapper"]["fields"]), ["tools", "model"])
        self.assertEqual(t["permissions"]["compare"], "tool-classes")

    def test_codex_values(self):
        t = self.target("codex")
        self.assertEqual(t["skill"]["tool_file"], eva.OPENAI_YAML)
        self.assertEqual(t["skill"]["verified_fields"], list(eva.CODEX_VERIFIED_FIELDS))
        self.assertEqual(t["wrapper"]["required"], list(eva.CODEX_AGENT_REQUIRED_KEYS))
        strings = [k for k, f in t["wrapper"]["fields"].items() if f["kind"] == "string"]
        self.assertEqual(strings, list(eva.CODEX_AGENT_OPTIONAL_KEYS))
        self.assertEqual(t["wrapper"]["fields"]["features.shell_tool"], {"kind": "boolean"})
        self.assertEqual(list(t["wrapper"]["forbidden"]), ["sandbox_mode"])
        self.assertEqual(t["wrapper"]["forbidden"]["sandbox_mode"], C.REMOVE)
        self.assertEqual(t["wrapper"]["unknown_keys"], "fail")
        self.assertEqual(t["permissions"]["unrestrictable"], list(eva.CODEX_UNRESTRICTABLE))
        self.assertIn("shell", t["permissions"]["unrestrictable"])
        self.assertEqual(t["permissions"]["derive"]["shell"]["verified"], False)
        self.assertEqual(t["permissions"]["compare"], "none")

    def test_opencode_values(self):
        t = self.target("opencode")
        self.assertEqual(t["skill"]["name_pattern"], eva.PORTABLE_NAME_RE.pattern)
        self.assertEqual(t["skill"]["autoinvoke_key"], eva.OPENCODE_AUTOINVOKE_KEY)
        self.assertEqual(t["wrapper"]["fields"]["mode"]["values"], list(eva.OPENCODE_MODES))
        self.assertEqual(list(t["wrapper"]["fields"]), ["name", "mode", "model", "permission"])
        self.assertEqual(list(t["permissions"]["grants"].items()), list(B1_OPENCODE_GRANTS.items()))
        self.assertEqual(t["permissions"]["actions"], list(eva.PERMISSION_ACTIONS))
        self.assertEqual(t["permissions"]["complete"], eva.OPENCODE_PERMISSION_KEYS_COMPLETE)
        self.assertIs(t["permissions"]["complete"], False)

    def test_hermes_values(self):
        t = self.target("hermes")
        self.assertEqual(t["permissions"]["fixed"], "fail")
        self.assertEqual(t["wrapper"]["format"], "text")
        self.assertEqual(t["wrapper"]["extra_files"][0]["path"], "wrappers/hermes/config.yaml")

    def test_docs_are_the_recorded_urls_with_their_recorded_dates(self):
        recorded = {"https://code.claude.com/docs/en/sub-agents", "https://developers.openai.com/codex/subagents",
                    "https://opencode.ai/docs/agents/", "https://hermes-agent.nousresearch.com/docs/user-guide/profiles",
                    "https://hermes-agent.nousresearch.com/docs/user-guide/features/tools"}
        for tool in TOOLS:
            docs = self.target(tool)["docs"]
            self.assertTrue(docs, tool)
            self.assertTrue(any(d["verified"] == "2026-10-19" for d in docs), tool)
            for d in docs:
                self.assertIn(d["verified"], ("2026-10-19", None), d)
                if d["verified"]:
                    self.assertIn(d["url"], recorded, d)
                else:
                    self.assertTrue(d["note"], d)

    def test_the_vocabulary_constants_equal_eva(self):
        PR = parity()
        for mod in (PR, eva):
            for name in ("CAPABILITY_BY_TOOL", "NEUTRAL_TOOLS", "TOOL_ALIASES", "tool_keys"):
                self.assertFalse(hasattr(mod, name), (mod.__name__, name))
        table, neutral = PR.capability_table()
        self.assertLessEqual(set(table.values()), set(PR.vocabulary()))
        self.assertEqual(neutral, ("todowrite", "todo_write"))
        self.assertEqual(set(B1_OPENCODE_GRANTS.values()) - {None}, set(PR.vocabulary()))

    def test_rule_protects_follow_the_parity_row_tools(self):
        for rule in RR.default_bundle().rules:
            if rule.id in eva.PARITY_ROW_TOOLS:
                self.assertEqual(tuple(rule.protects), eva.PARITY_ROW_TOOLS[rule.id], rule.id)

    def test_every_target_field_is_pinned_to_its_legacy_source(self):
        import test_differential as D
        src = D.legacy_source("add_parity_checks_skill", "agent_wrapper_states", "add_parity_checks_agent",
                              "codex_policy") + inspect.getsource(eva._wrapper_rel)
        pins = target_pins()
        for tool in TOOLS:
            got = dict(leaves({k: v for k, v in self.target(tool).items() if k != "$comment"}))
            self.assertEqual(sorted(got), sorted(pins[tool]), tool)
            for path, (want, snippet) in pins[tool].items():
                with self.subTest((tool, path)):
                    self.assertEqual((type(got[path]), got[path]), (type(want), want))
                    if snippet:
                        self.assertIn(snippet, src)
        for url, line in RECORDED_DOCS.items():
            self.assertIn(url, line)
            self.assertIn("VERIFIED 2026-10-19", line)

    def test_a_missing_or_invalid_target_is_a_bundle_error(self):
        root = self.repo_copy()
        os.remove(os.path.join(root, "targets", "codex.json"))
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(os.path.join(root, "rules"), root=root)
        self.assertIn("codex", str(cm.exception))
        root2 = os.path.join(self.tmp, "repo2")
        shutil.copytree(root, root2)
        shutil.copy(os.path.join(ROOT, "targets", "codex.json"), os.path.join(root2, "targets", "codex.json"))
        doc = load_json(os.path.join(root2, "targets", "opencode.json"))
        doc["permissions"]["complete"] = "no"
        write(os.path.join(root2, "targets", "opencode.json"), json.dumps(doc))
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(os.path.join(root2, "rules"), root=root2)
        self.assertIn("opencode", str(cm.exception))


class TestParseToml(unittest.TestCase):
    def ok(self, text, want):
        self.assertEqual(loaders.parse_toml(text), want, text)

    def bad(self, text, frag):
        with self.assertRaises(loaders.TomlError) as cm:
            loaders.parse_toml(text)
        self.assertIn(frag, str(cm.exception), text)

    def test_tables_dotted_tables_and_dotted_keys_nest(self):
        self.ok('name = "x"\n[features]\nshell_tool = false\n', {"name": "x", "features": {"shell_tool": False}})
        self.ok("[a.b]\nc = 1\n[a.d]\ne = 2\n", {"a": {"b": {"c": 1}, "d": {"e": 2}}})
        self.ok("features.shell_tool = false\nx.y.z = 'q'\n", {"features": {"shell_tool": False}, "x": {"y": {"z": "q"}}})
        self.ok("[t]\na.b = 1\nc = [1, 2]\n", {"t": {"a": {"b": 1}, "c": [1, 2]}})
        self.ok("[ t ]\n[ u . v ]  # comment\nw = 1\n", {"t": {}, "u": {"v": {"w": 1}}})

    def test_refusals(self):
        self.bad("[[x]]\na = 1\n", "tables")
        self.bad('"a" = 1\n', "quoted keys")
        self.bad('a."b" = 1\n', "quoted keys")
        self.bad('["a"]\n', "quoted keys")
        self.bad("a = {x = 1}\n", "inline tables")
        self.bad("a = 1\na = 2\n", "duplicate key 'a'")
        self.bad("[t]\n[t]\n", "defined twice")
        self.bad("a = 1\n[a]\n", "a value and a table")
        self.bad("a = 1\na.b = 2\n", "a value and a table")
        self.bad("[a]\nb = 1\n[a.b]\n", "a value and a table")
        self.bad("a.b = 1\na.b = 2\n", "duplicate key 'b'")
        self.bad("[a\nb = 1\n", "table header")
        self.bad("[]\n", "table header")
        self.bad("a. = 1\n", "dotted key")

    def test_every_table_redefinition_is_refused(self):
        for text in ("a.b = 1\n[a]\n", "a.b = 1\n[a]\nb = 2\n", "features.shell_tool = false\n[features]\n",
                     "[a.b.c]\nx = 1\n[a]\nb.d = 2\n", "a.b.c = 1\n[a.b]\n", "[t]\na.b = 1\n[t.a]\n",
                     "[a.b.c]\nz = 9\n[a]\nb.c.t = 1\n", "[a]\nb.c = 1\n[a.b]\nd = 2\n", "a.b = 1\n[x]\n[a]\n",
                     "[a]\n[a.b]\n[a]\n", "[x.y]\n[x]\n[x]\n"):
            self.bad(text, "defined twice")

    def test_valid_tables_keep_their_result(self):
        self.ok("[fruit]\napple.color = 'red'\napple.taste.sweet = true\n[fruit.apple.texture]\nsmooth = true\n",
                {"fruit": {"apple": {"color": "red", "taste": {"sweet": True}, "texture": {"smooth": True}}}})
        self.ok("[x.y]\n[x]\nz = 1\n", {"x": {"y": {}, "z": 1}})
        self.ok("a.b = 1\na.c = 2\n", {"a": {"b": 1, "c": 2}})
        self.ok("[a]\nb.c = 1\nb.e = 3\n[a.b.d]\ne = 2\n", {"a": {"b": {"c": 1, "e": 3, "d": {"e": 2}}}})
        self.ok("[a]\nx = 1\n[b]\n[a.c]\ny = 2\n", {"a": {"x": 1, "c": {"y": 2}}, "b": {}})

    def test_nesting_deeper_than_the_schema_limit_is_refused(self):
        deep = 1
        for _ in range(S.MAX_DEPTH):
            deep = {"a": deep}
        self.ok(".".join(["a"] * S.MAX_DEPTH) + " = 1\n", deep)
        why = "deeper than %d levels" % S.MAX_DEPTH
        self.bad(".".join(["a"] * (S.MAX_DEPTH + 1)) + " = 1\n", why)
        self.bad("[" + ".".join(["a"] * 900) + "]\n", why)
        self.bad("[" + ".".join(["a"] * 100) + "]\n" + ".".join(["b"] * 100) + " = 1\n", why)

    def test_flat_files_read_the_same_as_the_flat_reader(self):
        for text in ('a = "x" # c\nb = 1', 's = "\\u00e9"\nl = \'lit\'\ni = -42\nf = 1.5\nt = true',
                     'd = """\nline\n"""\n', 'a = [1, 2]\nb = ["x", \'y\']\n', ""):
            self.assertEqual(loaders.parse_toml(text), loaders.parse_flat_toml(text), text)
        for text in ("a = 1\na = 2", "a =", "a = bare", 'a = "open', "\ufeffa = 1", "a = {x = 1}", '"a" = 1'):
            msgs = []
            for fn in (loaders.parse_toml, loaders.parse_flat_toml):
                with self.assertRaises(loaders.TomlError) as cm:
                    fn(text)
                msgs.append(str(cm.exception))
            self.assertEqual(msgs[0], msgs[1], text)

    def test_the_flat_reader_is_unchanged(self):
        with open(os.path.join(ROOT, "engine", "loaders.py"), encoding="utf-8") as f:
            src = f.read()
        nodes = {n.name: n for n in ast.parse(src).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        for name, pin in TOML_PINS.items():
            seg = ast.get_source_segment(src, nodes[name])
            self.assertEqual(hashlib.sha256(seg.encode("utf-8")).hexdigest(), pin, name)
        with self.assertRaises(loaders.TomlError) as cm:
            loaders.parse_flat_toml("[table]\na = 1")
        self.assertIn("tables are not supported", str(cm.exception))

    def test_the_toml_selector_reads_tables(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        unit = os.path.join(d, "demo")
        write(os.path.join(unit, "SKILL.md"), "---\nname: demo\ndescription: x\n---\nx\n")
        write(os.path.join(unit, "w.toml"), "[features]\nshell_tool = false\n")
        check = RR.compile_check({"op": "equals", "left": {"sel": "toml:w.toml#/features/shell_tool"},
                                  "right": {"value": False}})
        self.assertEqual(check(RR._Unit(unit, "skill"))[0], "pass")


class TestScriptData(Tmp):
    def data(self):
        return load_json(os.path.join(ROOT, "rules", "data", "scripts.json"))

    def test_every_value_equals_the_a2_constants(self):
        d = self.data()
        self.assertEqual(set(d["non_english_script_words"]), set(eva.NON_ENGLISH_SCRIPT_WORDS))
        self.assertEqual(len(d["non_english_script_words"]), len(eva.NON_ENGLISH_SCRIPT_WORDS))
        self.assertEqual(set(d["english_compatible_first_words"]), set(eva.ENGLISH_COMPATIBLE_FIRST_WORDS))
        self.assertEqual(len(d["english_compatible_first_words"]), len(eva.ENGLISH_COMPATIBLE_FIRST_WORDS))
        self.assertEqual([(int(r["first"], 16), int(r["last"], 16), r["label"]) for r in d["non_english_ranges"]],
                         list(eva._NON_ENGLISH_RANGES))
        self.assertEqual([bytes.fromhex(x) for x in d["binary_signatures"]], list(eva.BINARY_SIGNATURES))
        self.assertEqual(set(d["binary_extensions"]), set(eva.BINARY_EXTENSIONS))
        self.assertEqual(len(d["binary_extensions"]), len(eva.BINARY_EXTENSIONS))

    def test_the_scanner_reads_the_data_file_not_code(self):
        SD = denylist()
        for name in ("NON_ENGLISH_SCRIPT_WORDS", "ENGLISH_COMPATIBLE_FIRST_WORDS", "_NON_ENGLISH_RANGES",
                     "BINARY_SIGNATURES", "BINARY_EXTENSIONS"):
            self.assertFalse(hasattr(SD, name), name)
        with open(os.path.join(ROOT, "engine", "primitives", "script_denylist.py"), encoding="utf-8") as f:
            src = f.read()
        for word in ("HANGUL", "CHEROKEE", "LATIN", "PNG", "0x3300"):
            self.assertNotIn(word, src, word)

    def changed_bundle(self, word="GREEK"):
        root = self.repo_copy()
        doc = load_json(os.path.join(root, "rules", "data", "scripts.json"))
        doc["non_english_script_words"].append(word)
        write(os.path.join(root, "rules", "data", "scripts.json"), json.dumps(doc, indent=1))
        return os.path.join(root, "rules")

    def greek_skill(self):
        return self.skill(name="greek-note", files={"references/note.md": "Angle %s.\n" % GREEK})

    def test_a_data_only_change_makes_a_fixture_in_the_added_script_fail_in_this_process(self):
        unit = self.greek_skill()
        self.assertEqual(row(eva.check(unit), "english-bundled-files")["status"], "pass")
        bundle = RR.load_bundle(self.changed_bundle())
        r = eva.check(unit, bundle)
        self.assertEqual(row(r, "english-bundled-files")["status"], "fail")
        self.assertIn("[Greek]", row(r, "english-bundled-files")["reason"])
        self.assertEqual(row(eva.check(unit), "english-bundled-files")["status"], "pass")
        self.assertIsNone(eva.non_english_script(GREEK[0]))

    def test_a_data_only_change_makes_a_fixture_in_the_added_script_fail_in_a_fresh_process(self):
        unit = self.greek_skill()
        rules = self.changed_bundle()
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "eva.py"), "check", unit, "--rules", rules, "--json"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        data = json.loads(proc.stdout.decode("utf-8"))
        rowj = next(c for c in data["checks"] if c["id"] == "english-bundled-files")
        self.assertEqual(rowj["status"], "fail")
        self.assertIn("[Greek]", rowj["reason"])

    def test_two_data_objects_keep_separate_caches(self):
        SD = denylist()
        doc = self.data()
        a = SD.Scripts(doc)
        doc2 = dict(doc, non_english_script_words=doc["non_english_script_words"] + ["GREEK"])
        b = SD.Scripts(doc2)
        with SD.using(a):
            self.assertIsNone(SD.non_english_script(GREEK[0]))
        with SD.using(b):
            self.assertEqual(SD.non_english_script(GREEK[0]), "Greek")
        with SD.using(a):
            self.assertIsNone(SD.non_english_script(GREEK[0]))

    def test_a_broken_data_file_is_a_bundle_error(self):
        rules = self.changed_bundle()
        write(os.path.join(rules, "data", "scripts.json"), '{"non_english_script_words": [1], "x": NaN}')
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(rules)
        self.assertIn("scripts.json", str(cm.exception))


class TestEnglishRuleset(Tmp):
    def plant(self, root, rel, edit):
        fp = os.path.join(root, *rel.split("/"))
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        write(fp, edit(text))

    def assert_fails(self, root, rel):
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(os.path.join(root, "rules"), root=root)
        msg = str(cm.exception)
        self.assertIn("english-ruleset", msg)
        self.assertIn(rel, msg)
        self.assertIn("[Hangul]", msg)

    def test_the_shipped_folders_pass(self):
        RR.load_bundle(os.path.join(ROOT, "rules"))
        self.assertTrue(RR.default_bundle().rules)

    def test_an_escaped_hangul_word_in_one_rule_message_fails(self):
        root = self.repo_copy()
        self.plant(root, "rules/naming.rules.json",
                   lambda t: t.replace('"message": "name', '"message": "\\ud55c\\uad6d\\uc5b4 name', 1))
        self.assert_fails(root, "rules/naming.rules.json")

    def test_each_scanned_folder_is_covered(self):
        for rel in ("schemas/zz-note.json", "targets/zz-note.json", "policy/zz-note.json", "rules/data/zz-note.json",
                    "rules/zz-note.json"):
            with self.subTest(rel):
                root = os.path.join(self.tmp, rel.replace("/", "-"))
                for name in ("rules", "schemas", "targets", "policy"):
                    shutil.copytree(os.path.join(ROOT, name), os.path.join(root, name))
                write(os.path.join(root, *rel.split("/")), json.dumps({"note": "plain " + KO}))
                self.assert_fails(root, rel)

    def test_a_non_json_file_and_a_file_name_are_scanned(self):
        root = self.repo_copy()
        write(os.path.join(root, "targets", "notes.txt"), "plain %s\n" % KO)
        self.assert_fails(root, "targets/notes.txt")
        root2 = os.path.join(self.tmp, "repo-name")
        for name in ("rules", "schemas", "targets", "policy"):
            shutil.copytree(os.path.join(ROOT, name), os.path.join(root2, name))
        write(os.path.join(root2, "policy", KO + ".json"), "{}")
        self.assert_fails(root2, "(file name)")

    def test_the_cli_prints_a_bundle_error_and_no_row(self):
        root = self.repo_copy()
        self.plant(root, "rules/english-only.rules.json",
                   lambda t: t.replace('"fix": "Rewrite', '"fix": "\\ud55c Rewrite', 1))
        unit = self.skill()
        for argv in (["check", unit, "--rules", os.path.join(root, "rules")],
                     ["check-all", os.path.dirname(unit), "--rules", os.path.join(root, "rules")]):
            rc, out, err = quiet(eva.main, argv)
            self.assertEqual((rc, out), (2, ""), argv)
            self.assertIn("bundle error:", err)
            self.assertIn("english-ruleset", err)

    def test_english_text_passes(self):
        root = self.repo_copy()
        write(os.path.join(root, "policy", "extra.json"), json.dumps({"note": "plain English, caf\u00e9 and \u03b1"}))
        RR.load_bundle(os.path.join(root, "rules"), root=root)


class TestProvenance(Tmp):
    def rows(self, unit):
        rc, out, _ = quiet(eva.main, ["check", unit, "--json"])
        return [c for c in json.loads(out)["checks"] if c["id"].startswith(RULE_ROW_PREFIXES)]

    def test_every_naming_english_and_parity_row_names_its_rule_file_source_and_date(self):
        units = [os.path.join(ROOT, "golden", "skills", "csv-cleaner"), self.agent()]
        seen = set()
        for unit in units:
            for c in self.rows(unit):
                with self.subTest(c["id"]):
                    self.assertIn("rule", c)
                    self.assertIn(c["rule"]["file"], ["rules/" + n for n in A3_RULE_FILES])
                    src = c["rule"]["source"]
                    self.assertTrue(("url" in src) != ("house" in src), src)
                    self.assertIn("verified", src)
                    self.assertTrue(src["verified"] is None or re.match(r"^\d{4}-\d{2}-\d{2}$", src["verified"]), src)
                    seen.add(c["id"])
        self.assertGreaterEqual(len(seen), 25)

    def test_the_rules_without_a_date_are_named(self):
        nulls = sorted(r.id for r in RR.default_bundle().rules
                       if r.file in A3_RULE_FILES and r.source.get("verified") is None)
        self.assertEqual(nulls, ["name-reserved"])

    def test_text_output_and_reasons_are_unchanged_by_the_key(self):
        import test_differential as D
        units = [os.path.join(ROOT, "golden", "skills", "csv-cleaner"), os.path.join(ROOT, "golden", "agents", "code-reviewer"),
                 self.agent(), self.skill()]
        for unit in units:
            with self.subTest(unit):
                with D.legacy():
                    old = eva.check(unit, D.EMPTY_BUNDLE)
                new = eva.check(unit)
                agent = new.kind == "agent"
                kept = lambda rid: not agent or not rid.startswith(B1_AGENT_ROWS)
                rows = [c for c in new.checks if c["id"].startswith(RULE_ROW_PREFIXES)]
                self.assertTrue(rows and all("rule" in c for c in rows))
                self.assertFalse(any("rule" in c for c in old.checks))
                self.assertEqual({c["id"]: c["reason"] for c in rows if kept(c["id"])},
                                 {c["id"]: c["reason"] for c in old.checks
                                  if c["id"].startswith(RULE_ROW_PREFIXES) and kept(c["id"])})

                def lines(r):
                    out = quiet(eva.print_report, r)[1].splitlines()
                    if not agent:
                        return sorted(out)
                    return sorted(ln for ln in out if ln.startswith("  ") and not ln.startswith("  category below floor")
                                  and kept(ln.split()[1]))
                # row order in the text output is out of scope (check-row-text); every line must stay
                self.assertEqual(lines(new), lines(old))


class TestEnglishOperands(unittest.TestCase):
    def groups(self):
        return {r.id: r.check.group for r in RR.default_bundle().rules
                if r.file == "english-only.rules.json" and r.check.group is not None}

    def test_every_group_operand_equals_the_code_constant_it_duplicates(self):
        import test_differential as D
        SD, g = denylist(), self.groups()
        legacy = D.legacy_source("english_scan")
        self.assertEqual(g["english-bundled-files"]["ignore"], {"patterns": list(eva.SKILL_IGNORE)})
        self.assertEqual(eva.SKILL_IGNORE, SD.SKILL_IGNORE)
        self.assertEqual(g["english-bundled-files"]["exclude"], ["SKILL.md"] + list(eva.SKILL_TOOL_FILES))
        self.assertEqual((g["english-bundled-files"]["top"], g["english-bundled-files"]["frontmatter_md"]), ("", False))
        self.assertIn('if rel == "SKILL.md" or rel in SKILL_TOOL_FILES: continue', legacy)
        self.assertEqual(g["english-tool-files"]["files"], list(eva.SKILL_TOOL_FILES))
        self.assertEqual(eva.SKILL_TOOL_FILES, SD.SKILL_TOOL_FILES)
        self.assertEqual(g["english-evals"]["files"], ["evals/evals.json", "evals/trigger_evals.json"])
        self.assertIn('_json_group(path, ("evals/evals.json", "evals/trigger_evals.json"), "evals")', legacy)
        self.assertEqual(eva.OUTPUT_MATCHER_KEYS, SD.OUTPUT_MATCHER_KEYS)
        self.assertEqual([tuple(m) for m in g["english-evals"]["matchers"]], list(eva.OUTPUT_MATCHER_KEYS["evals"]))
        self.assertEqual([tuple(m) for m in g["english-scenarios"]["matchers"]], list(eva.OUTPUT_MATCHER_KEYS["scenarios"]))
        self.assertEqual(eva.SKIP_DIRS, SD.SKIP_DIRS)
        for rid, top, md in (("english-scenarios", "scenarios", None), ("english-wrappers", "wrappers", True)):
            self.assertEqual(g[rid]["ignore"], {"dirs": list(eva.SKIP_DIRS)}, rid)
            self.assertEqual(g[rid]["top"], top, rid)
            self.assertIn('_tree_view(tree, "%s")' % top, legacy)
        self.assertEqual((g["english-wrappers"]["exclude"], g["english-wrappers"]["frontmatter_md"]), ([], True))
        self.assertIn('frontmatter=rel.endswith(".md")', legacy)
        self.assertEqual((g["english-skill-md"]["file"], g["english-agent-md"]["file"]), ("SKILL.md", "AGENT.md"))
        self.assertEqual(g["english-ruleset"]["folders"], ["rules"] + list(RR.HASHED_TREES) + ["policy"])

    def test_the_bundled_scan_ignores_what_the_unit_tree_ignores(self):
        g = self.groups()["english-bundled-files"]
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        for name in eva.SKILL_IGNORE:
            write(os.path.join(d, name.replace("*", "x"), "a.md") if "." not in name or name.startswith(".") else
                  os.path.join(d, name), KO + "\n")
        write(os.path.join(d, "SKILL.md"), "---\nname: x\n---\nx\n")
        self.assertEqual(denylist().group_scan(d, g)[1], [])
        self.assertEqual(eva.unit_tree(d).files, ["SKILL.md"])


class TestCodexFeatures(Tmp):
    def wrapper(self, features):
        return C.codex_wrapper() + features

    def test_lane2_shell_tool_false_parses_and_the_review_still_names_shell(self):
        d = self.agent(codex=self.wrapper("[features]\nshell_tool = false\n"))
        r = eva.check(d)
        self.assertEqual(row(r, "parity-wrapper-codex")["status"], "pass", row(r, "parity-wrapper-codex")["reason"])
        c = row(r, "parity-permissions-codex")
        self.assertEqual(c["status"], "needs-review")
        self.assertEqual(c["reason"], "needs-review: [codex] " + C.READ_ONLY)
        self.assertIn("shell", c["reason"])
        self.assertEqual(eva.agent_wrapper_states(d)["codex"]["problems"], [])

    def test_lane3_the_key_removed_gives_the_same_review(self):
        d = self.agent(codex=self.wrapper(""))
        c = row(eva.check(d), "parity-permissions-codex")
        self.assertEqual(c["reason"], "needs-review: [codex] " + C.READ_ONLY)

    def test_a_dotted_key_form_is_read_the_same(self):
        d = self.agent(codex=C.codex_wrapper().replace('name = "code-scout"', 'features.shell_tool = false\nname = "code-scout"'))
        self.assertEqual(row(eva.check(d), "parity-wrapper-codex")["status"], "pass")

    def test_shell_tool_is_never_read_as_a_permission(self):
        for value in ("false", "true"):
            d = self.agent(codex=self.wrapper("[features]\nshell_tool = %s\n" % value), name="code-scout")
            c = row(eva.check(d), "parity-permissions-codex")
            self.assertIn(", shell,", c["reason"], value)
            shutil.rmtree(d)

    def test_wrong_types_and_other_table_keys_fail_naming_the_dotted_path(self):
        cases = (("[features]\nshell_tool = \"no\"\n", "features.shell_tool must be a boolean"),
                 ("[features]\nother = true\n", "'features.other'"),
                 ("[skills]\nconfig = \"x\"\n", "'skills.config'"),
                 ("[mcp_servers.x]\ncommand = \"y\"\n", "'mcp_servers.x.command'"),
                 ("[features]\n", "'features'"),
                 ("[[agents]]\nname = \"x\"\n", "tables"))
        for text, frag in cases:
            with self.subTest(text):
                d = self.agent(codex=self.wrapper(text))
                c = row(eva.check(d), "parity-wrapper-codex")
                self.assertEqual(c["status"], "fail")
                self.assertIn(frag, c["reason"])
                self.assertNotEqual(row(eva.check(d), "parity-permissions-codex")["status"], "pass")
                shutil.rmtree(d)

    def test_an_agent_with_every_class_still_passes(self):
        d = self.agent(md=C.ALL_CAPS_MD, codex=self.wrapper("[features]\nshell_tool = false\n"))
        self.assertEqual(row(eva.check(d), "parity-permissions-codex")["status"], "pass")

    def test_a_dotted_key_then_its_table_header_fails_the_wrapper(self):
        d = self.agent(codex=self.wrapper("features.shell_tool = false\n[features]\n"))
        c = row(eva.check(d), "parity-wrapper-codex")
        self.assertEqual(c["status"], "fail")
        self.assertIn("table 'features' is defined twice", c["reason"])

    def test_a_deep_dotted_key_or_header_fails_without_a_traceback(self):
        self.skill()
        for extra in (".".join(["a"] * 900) + " = 1\n", "[" + ".".join(["a"] * 900) + "]\n"):
            with self.subTest(extra[:12]):
                d = self.agent(codex=self.wrapper(extra))
                r = eva.check(d)
                c = row(r, "parity-wrapper-codex")
                self.assertEqual(c["status"], "fail")
                self.assertIn("deeper than %d levels" % S.MAX_DEPTH, c["reason"])
                self.assertNotEqual(row(r, "parity-permissions-codex")["status"], "pass")
                rc, out, err = quiet(eva.main, ["check-all", self.tmp])
                self.assertEqual(rc, 1)
                self.assertNotIn("Traceback", err)
                self.assertIn("agents/code-scout", out)
                self.assertIn("skills/word-count", out)
                self.assertIn("total=2 ", out)
                shutil.rmtree(d)

    def test_a_frontmatter_boolean_field_reports_like_the_toml_one(self):
        PR = parity()
        tg = PR.targets()
        with mock.patch.dict(tg["claude"]["wrapper"]["fields"], {"flag": {"kind": "boolean"}}):
            d = self.agent(codex=self.wrapper("[features]\nshell_tool = \"no\"\n"))
            fp = os.path.join(d, "wrappers", "claude", "code-scout.md")
            with open(fp, encoding="utf-8") as f:
                text = f.read()
            write(fp, text.replace("---\nYou", "flag: \"yes\"\n---\nYou", 1))
            st = PR.wrapper_states(d, tg)
        self.assertIn("wrappers/claude/code-scout.md: flag must be a boolean", st["claude"]["problems"])
        self.assertIn("wrappers/codex/code-scout.toml: features.shell_tool must be a boolean", st["codex"]["problems"])


class TestDeepJson(Tmp):
    def deep_skill(self, depth=5000):
        d = self.skill(name="deep-evals")
        write(os.path.join(d, "evals", "evals.json"), "[" * depth + "]" * depth)
        return d

    def test_check_does_not_traceback_and_does_not_pass(self):
        d = self.deep_skill()
        r = eva.check(d)
        c = row(r, "english-evals")
        self.assertEqual(c["status"], "fail")
        self.assertIn("too deep", c["reason"])
        self.assertEqual(r.verdict, "FAIL")
        rc, out, err = quiet(eva.main, ["check", d])
        self.assertEqual(rc, 1)
        self.assertNotIn("Traceback", err)

    def test_a_deep_bundled_json_file_fails_its_group(self):
        d = self.skill(name="deep-file", files={"assets/data.json": "{\"a\": " * 4000 + "1" + "}" * 4000})
        c = row(eva.check(d), "english-bundled-files")
        self.assertEqual(c["status"], "fail")
        self.assertIn("too deep", c["reason"])

    def test_the_scanner_function_reports_the_depth(self):
        viol, review = eva.english_json_findings("[" * 5000 + "]" * 5000, "x.json")
        self.assertEqual(review, [])
        self.assertTrue(viol and "too deep" in viol[0], viol)


class TestNameOnce(Tmp):
    def folder_rows(self, r):
        return [c["id"] for c in r.checks if c["status"] == "fail" and "folder" in c["reason"]]

    def test_lane7_a_name_that_differs_from_its_folder_fails_once(self):
        d = self.skill(name="other-name", folder="word-count")
        r = eva.check(d)
        self.assertEqual(row(r, "name-matches-folder")["status"], "fail")
        self.assertEqual(self.folder_rows(r), ["name-matches-folder"])
        self.assertNotIn("must equal the folder name", row(r, "parity-skill-opencode")["reason"])

    def test_the_gates_keep_the_folder_cause_of_the_a2_rows(self):
        d = self.skill(name="other-name", folder="word-count")
        self.assertEqual(eva.parity_violations(d),
                         ["[opencode] name 'other-name' must equal the folder name 'word-count' exactly"])
        self.assertEqual(eva.parity_line(eva.parity_status(d)), "claude=pass codex=pass opencode=fail hermes=pass")
        self.assertEqual(self.folder_rows(eva.check(d)), ["name-matches-folder"])

    def test_validate_refuses_a_clean_skill_whose_name_differs_from_its_folder(self):
        import test_skill_eval as T
        src = T.make_skill(os.path.join(self.tmp, "a"))
        self.assertEqual(skill_eval.load_eval_set(src)[1], [])
        d = os.path.join(self.tmp, "b", "other-folder")
        shutil.copytree(src, d)
        self.assertEqual(skill_eval.load_eval_set(d)[1], [
            "multi-tool parity rule: [opencode] name 'branch-setup' must equal the folder name 'other-folder' exactly"])

    def test_the_nfkc_hidden_byte_mismatch_still_fails_opencode(self):
        d = self.skill(name="\uff57\uff4f\uff52\uff44-\uff43\uff4f\uff55\uff4e\uff54", folder="word-count")
        r = eva.check(d)
        self.assertEqual(row(r, "name-matches-folder")["status"], "pass")
        self.assertIn("must equal the folder name", row(r, "parity-skill-opencode")["reason"])
        d = self.skill(name='" word-count"', folder="word-count")
        r = eva.check(d)
        self.assertEqual(row(r, "name-matches-folder")["status"], "pass")
        self.assertIn("must equal the folder name", row(r, "parity-skill-opencode")["reason"])

    def test_the_naming_rows_are_one_list_for_skills_and_agents(self):
        rules = [r for r in RR.default_bundle().rules if r.file == "naming.rules.json"]
        self.assertEqual([r.id for r in rules], ["name-format", "name-matches-folder", "name-reserved"])
        self.assertEqual([r.applies_to for r in rules], [("skill", "agent"), ("skill", "agent"), ("skill",)])
        for unit in (os.path.join(ROOT, "golden", "skills", "csv-cleaner"), self.agent()):
            ids = [c["id"] for c in eva.check(unit).checks]
            for rid in ("name-format", "name-matches-folder"):
                self.assertEqual(ids.count(rid), 1, (unit, rid))

    def test_naming_rows_merge_the_built_in_reason_and_take_the_english_fix(self):
        d = self.skill(name="Bad--Name", folder="Bad--Name")
        c = row(eva.check(d), "name-format")
        self.assertEqual(c["status"], "fail")
        self.assertTrue(c["reason"].startswith("name 'Bad--Name' (skills-ref rules"), c["reason"])
        self.assertTrue(c["fix"].isascii() and c["fix"], c["fix"])
        self.assertEqual(c["rule"]["file"], "rules/naming.rules.json")


class TestRunnerExtensions(Tmp):
    def unit(self):
        return self.skill(name="demo-unit")

    def bundle(self, rules, extra=None):
        folder = os.path.join(self.tmp, "bundle-%d" % len(os.listdir(self.tmp)))
        write(os.path.join(folder, "rules.json"), json.dumps({"ruleset": "t", "version": "1.0.0", "rules": rules}))
        for rel, text in (extra or {}).items():
            write(os.path.join(folder, *rel.split("/")), text)
        return RR.load_bundle(folder)

    def rule(self, check, rid="t-rule", **kw):
        out = {"id": rid, "applies_to": ["skill", "agent"], "category": "structure", "severity": "fail",
               "protects": ["claude"], "source": {"house": "test", "verified": None}, "check": check,
               "message": "m {value}", "fix": "f"}
        out.update(kw)
        return out

    def apply(self, bundle, **kw):
        r = eva.Report(self.unit(), "skill")
        bundle.apply(r, **kw)
        return r

    def test_na_must_false_and_outcome_messages_and_fixes(self):
        when = {"op": "when", "if": {"op": "file_exists", "path": "none.txt"}, "then": {"op": "file_exists", "path": "x"}}
        b = self.bundle([self.rule(when, na_must=False, message={"na": "nothing here", "fail": "bad"},
                                   fix={"fail": "repair it"})])
        c = row(self.apply(b), "t-rule")
        self.assertEqual((c["status"], c["must"], c["reason"]),
                         ("n/a", False, "N/A: nothing here (the when condition is not met)"))
        b = self.bundle([self.rule({"op": "file_exists", "path": "none.txt"}, message={"fail": "bad"}, fix={"fail": "repair it"})])
        c = row(self.apply(b), "t-rule")
        self.assertEqual((c["status"], c["reason"], c["fix"], c["must"]), ("fail", "bad", "repair it", True))
        b = self.bundle([self.rule(FILE_ANY, message={"fail": "bad"})])
        self.assertEqual(row(self.apply(b), "t-rule")["reason"], "rule t-rule: pass")

    def test_na_must_false_changes_only_the_na_row(self):
        for severity, path, want in (("fail", "none.txt", ("fail", True)), ("fail", "SKILL.md", ("pass", True)),
                                     ("review", "none.txt", ("needs-review", True))):
            with self.subTest((severity, path)):
                b = self.bundle([self.rule({"op": "file_exists", "path": path}, na_must=False, severity=severity)])
                c = row(self.apply(b), "t-rule")
                self.assertEqual((c["status"], c["must"]), want)
        for rid in ("english-output-matchers", "parity-invocation-hermes"):
            rule = next(r for r in RR.default_bundle().rules if r.id == rid)
            self.assertIs(rule.na_must, False, rid)
            self.assertEqual(rule.severity, "review", rid)

    def scripts_data(self):
        with open(os.path.join(ROOT, "rules", "data", "scripts.json"), encoding="utf-8") as f:
            return f.read()

    def group(self, mode, rels):
        g = {"mode": mode, "label": "x"}
        if mode == "files":
            g["files"] = rels
        elif mode == "json":
            g.update(files=rels, matchers=[["check", "pattern"]])
        else:
            g.update(top="", ignore={"dirs": [".git"]}, exclude=rels, frontmatter_md=False)
        return {"op": "script_denylist", "data": "data/scripts.json", "group": g}

    def test_a_literal_group_relpath_that_leaves_the_unit_fails_the_bundle_load(self):
        data = self.scripts_data()
        for mode in ("files", "json", "tree"):
            for rel in ("../outside.txt", "/etc/hosts", "a/../../b", "a/../b", "..", "~/x", "a\\b", "C:/x"):
                with self.subTest((mode, rel)):
                    with self.assertRaises(RR.BundleError) as cm:
                        self.bundle([self.rule(self.group(mode, [rel]))], extra={"data/scripts.json": data})
                    self.assertIn("t-rule", str(cm.exception))
        self.bundle([self.rule(self.group("files", ["agents/openai.yaml"]))], extra={"data/scripts.json": data})

    def test_a_group_path_that_resolves_outside_the_unit_is_refused_and_never_read(self):
        outside = os.path.join(self.tmp, "outside")
        write(os.path.join(outside, "openai.yaml"), "interface:\n  display_name: %s\n" % KO)
        write(os.path.join(outside, "evals.json"), json.dumps({"skill_name": "x", "evals": [{"id": 1, "prompt": KO}]}))
        d = self.skill(name="link-tools")
        os.symlink(outside, os.path.join(d, "agents"))
        c = row(eva.check(d), "english-tool-files")
        self.assertEqual(c["status"], "fail")
        self.assertIn("agents/openai.yaml (link) resolves outside the unit folder", c["reason"])
        self.assertNotIn("Hangul", c["reason"])
        d2 = self.skill(name="link-evals")
        os.symlink(outside, os.path.join(d2, "evals"))
        c = row(eva.check(d2), "english-evals")
        self.assertEqual(c["status"], "fail")
        self.assertIn("evals/evals.json (link) resolves outside the unit folder", c["reason"])
        self.assertNotIn("Hangul", c["reason"])
        viol, review = eva.english_eval_problems(d2)
        self.assertTrue(viol and all("resolves outside" in v for v in viol), viol)
        self.assertFalse(any("Hangul" in v for v in viol + review), viol)

    def test_lower_and_string_or_empty_normalize(self):
        eq = {"op": "equals", "left": {"sel": "frontmatter:#/missing", "normalize": ["string-or-empty"]}, "right": {"value": ""}}
        self.assertEqual(row(self.apply(self.bundle([self.rule(eq)])), "t-rule")["status"], "pass")
        low = {"op": "equals", "left": {"sel": "frontmatter:#/name", "normalize": ["lower"]}, "right": {"value": "demo-unit"}}
        self.assertEqual(row(self.apply(self.bundle([self.rule(low)])), "t-rule")["status"], "pass")
        bad = self.rule({"op": "equals", "left": {"sel": "frontmatter:#/name", "normalize": ["strip", "string-or-empty"]},
                         "right": {"value": ""}})
        with self.assertRaises(RR.BundleError):
            self.bundle([bad])

    def test_apply_only_and_skip_and_the_non_default_row_once(self):
        a = self.rule(FILE_ANY, rid="a-rule")
        folder = os.path.join(self.tmp, "two")
        write(os.path.join(folder, "one.rules.json"), json.dumps({"ruleset": "one", "version": "1.0.0", "rules": [a]}))
        write(os.path.join(folder, "two.rules.json"), json.dumps(
            {"ruleset": "two", "version": "1.0.0", "rules": [self.rule(FILE_ANY, rid="b-rule")]}))
        b = RR.load_bundle(folder)
        r = eva.Report(self.unit(), "skill")
        b.apply(r, only=("one.rules.json",))
        b.apply(r, skip=("one.rules.json",))
        self.assertEqual([c["id"] for c in r.checks], ["a-rule", "b-rule", RR.NON_DEFAULT_ID])
        self.assertEqual(r.checks[0]["rule"], {"file": "rules/one.rules.json", "source": {"house": "test", "verified": None}})

    def test_check_gives_one_non_default_row(self):
        b = RR.load_bundle(os.path.join(ROOT, "tests", "rules", "_selftest"))
        ids = [c["id"] for c in eva.check(os.path.join(ROOT, "golden", "skills", "csv-cleaner"), b).checks]
        self.assertEqual(ids.count(RR.NON_DEFAULT_ID), 1)
        self.assertEqual(ids.count("english-skill-md"), 1)
        self.assertEqual(ids.count("parity-skill-claude"), 1)

    def test_a_review_outcome_becomes_a_review_row(self):
        b = RR.load_bundle(os.path.join(ROOT, "rules"))
        d = self.skill(name="review-me", extra="disable-model-invocation: true\n")
        r = eva.Report(d, "skill")
        b.apply(r, only=("parity-skill.rules.json",))
        c = row(r, "parity-invocation-hermes")
        self.assertEqual((c["status"], c["must"]), ("needs-review", True))

    def test_review_of_must_name_a_rule_of_the_bundle(self):
        bad = self.rule({"op": "script_denylist", "data": "data/scripts.json", "review_of": ["no-such-rule"]},
                        severity="review")
        with open(os.path.join(ROOT, "rules", "data", "scripts.json"), encoding="utf-8") as f:
            data = f.read()
        with self.assertRaises(RR.BundleError) as cm:
            self.bundle([bad], extra={"data/scripts.json": data})
        self.assertIn("no-such-rule", str(cm.exception))

    def test_a_data_operand_must_stay_inside_the_bundle(self):
        bad = self.rule({"op": "script_denylist", "data": "../scripts.json", "group": {"mode": "main", "file": "SKILL.md",
                                                                                         "label": "x"}})
        with self.assertRaises(RR.BundleError):
            self.bundle([bad])

    def test_the_primitives_import_as_a_namespace_package_without_eva(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "engine", "primitives", "__init__.py")))
        code = ("import sys; sys.path.insert(0, %r); from engine.primitives import parity, script_denylist; "
                "print(sorted(m for m in ('eva', 'skill_eval') if m in sys.modules))" % ROOT)
        proc = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(proc.stdout.decode().strip(), "[]", proc.stderr.decode())
        for name in ("parity.py", "script_denylist.py"):
            with open(os.path.join(ROOT, "engine", "primitives", name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
                   {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            self.assertFalse(mods & {"eva", "skill_eval"}, name)

    def test_the_op_tables(self):
        self.assertNotIn("script_denylist", RR.RESERVED_OPS)
        for op in ("script_denylist", "parity_skill", "parity_invocation", "parity_agent_name", "parity_wrapper",
                   "parity_permissions"):
            self.assertIn(op, RR.OPS)


class TestUnreadableMainFile(Tmp):
    def test_a_main_file_that_is_not_utf8_gives_an_na_english_row(self):
        for kind, main, rid in (("skill", "SKILL.md", "english-skill-md"), ("agent", "AGENT.md", "english-agent-md")):
            d = os.path.join(self.tmp, kind, "bad-bytes")
            os.makedirs(d)
            with open(os.path.join(d, main), "wb") as f:
                f.write(b"---\nname: bad-bytes\n---\n\xff\xfe\x80\n")
            c = row(eva.check(d), rid)
            self.assertEqual((c["status"], c["must"]), ("n/a", True), kind)
            self.assertEqual(c["reason"], "N/A: nothing to scan in %s (frontmatter and body)" % main)


class TestAsciiFiles(unittest.TestCase):
    def test_every_file_a3_adds_or_writes_is_ascii(self):
        files = [os.path.join(ROOT, "targets", t + ".json") for t in TOOLS]
        files += [os.path.join(ROOT, "schemas", n) for n in ("targets.schema.json", "rules.schema.json")]
        files += [os.path.join(ROOT, "rules", n) for n in A3_RULE_FILES] + [os.path.join(ROOT, "rules", "data", "scripts.json")]
        files += [os.path.join(ROOT, "engine", "primitives", n) for n in ("parity.py", "script_denylist.py")]
        files += [os.path.join(ROOT, "engine", "rule_runner.py")]
        files += [os.path.join(ROOT, "tests", n) for n in ("test_rule_targets.py", "test_rule_fixtures.py",
                                                         "test_differential.py", "test_rule_runner.py")]
        ids = [r.id for r in RR.default_bundle().rules if r.file in A3_RULE_FILES]
        for rid in ids:
            for dp, dn, fn in os.walk(os.path.join(ROOT, "tests", "rules", rid)):
                files += [os.path.join(dp, n) for n in fn]
        self.assertGreater(len(files), 300)
        for fp in files:
            with open(fp, "rb") as f:
                self.assertTrue(f.read().isascii(), fp)
        # eva.py and engine/loaders.py held non-ASCII text before A3; the definitions A3 adds there are ASCII
        for fname, names in (("eva.py", ("_family_bundle", "_english_groups", "english_scan", "english_eval_problems",
                                         "add_english_checks", "codex_policy", "add_parity_checks_skill",
                                         "agent_wrapper_states", "add_parity_checks_agent", "add_parity_checks",
                                         "check")),
                             (os.path.join("engine", "loaders.py"), ("_TableTomlReader", "parse_toml"))):
            with open(os.path.join(ROOT, fname), encoding="utf-8") as f:
                src = f.read()
            nodes = {n.name: n for n in ast.parse(src).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
            for name in names:
                seg = ast.get_source_segment(src, nodes[name])
                if name == "check":
                    seg = "\n".join(x for x in seg.splitlines() if "r.add(\"frontmatter\"" not in x)
                self.assertTrue(seg.isascii(), (fname, name))


FILE_ANY = {"op": "file_exists", "path": "SKILL.md"}


if __name__ == "__main__":
    unittest.main()
