"""OpenCode agent permissions (plan C3). The OpenCode wrapper must set every recorded permission key, because an unset key
keeps an UNVERIFIED default. Unknown keys fail, external_directory must be deny, and parity-permissions-opencode is REVIEW,
never PASS, while the recorded key list is not verified complete. Mocked like tests/test_codex_permissions.py: subprocess is
patched to fail for the whole module, no claude/codex/opencode/hermes call, no network."""
import contextlib, io, json, os, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva
from engine.primitives import parity as PR
import test_skill_eval as T
import test_multi_tool_parity as P

WRAPPER = "parity-wrapper-opencode"
ROW = "parity-permissions-opencode"
OPENCODE_REL = "wrappers/opencode/code-scout.md"
KEYS = P.OPENCODE_KEYS
INCOMPLETE = "the OpenCode permission key list is not verified complete; an unlisted key keeps its UNVERIFIED default"
EVERY_CLASS_MD = P.AGENT_MD.replace("[glob, grep, read_file]", "[read_file, write_file, bash, web_fetch, web_search, task, skill]")
BASH_PATTERNS = {"block map": '\n    "*": deny\n    "python3 *": allow', "flow map": ' {"*": deny, "python3 *": allow}'}
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def opencode_wrapper(caps=("read",), drop=(), values=None, extra=()):
    """An OpenCode wrapper for P.AGENT_MD that sets every recorded key: allow for a class in caps, deny otherwise. drop: keys
    to leave out; values: {key: raw text after 'key:'}; extra: more raw 'key: value' lines under permission."""
    desc = eva.parse_frontmatter(P.AGENT_MD)[0]["description"]
    lines = ["---", "description: %s" % json.dumps(desc), "mode: subagent", "permission:"]
    for key, cls in KEYS:
        if key in drop: continue
        raw = (values or {}).get(key, " allow" if cls in caps else " deny")
        lines.append("  %s:%s" % (key, raw))
    lines += ["  " + x for x in extra] + ["---", "You are code-scout. Load the skill and use the engine."]
    return "\n".join(lines) + "\n"

class OpenCodeBase(P.AgentBase):
    def opencode(self, md=None, **kw):
        return self.agent(md=md, replace={OPENCODE_REL: opencode_wrapper(**kw)})

    def wrapper_fails(self, r, fragment, label=""):
        self.assertEqual(P.row(r, WRAPPER)["status"], "fail", label)
        self.assertIn(fragment, P.row(r, WRAPPER)["reason"], label)
        self.assertNotEqual(P.row(r, ROW)["status"], "pass", label)

class TestTheWrapperSetsEveryRecordedKey(OpenCodeBase, unittest.TestCase):
    def test_a_missing_key_fails_the_wrapper_for_each_recorded_key(self):
        for key, _ in KEYS:
            with self.subTest(key):
                d, r = self.opencode(drop=(key,))
                self.wrapper_fails(r, "%s: permission.%s must be set explicitly" % (OPENCODE_REL, key), key)
                self.assertEqual(P.tool_status(d)["opencode"], "fail")
                shutil.rmtree(d)

    def test_a_wrapper_with_only_edit_bash_and_webfetch_names_every_other_key(self):
        d, r = self.opencode(drop=[k for k, _ in KEYS if k not in ("edit", "bash", "webfetch")])
        for key in ("read", "glob", "grep", "list", "task", "external_directory", "websearch", "lsp", "skill"):
            self.wrapper_fails(r, "permission.%s must be set explicitly" % key, key)

    def test_an_unknown_key_fails_the_wrapper_and_is_named(self):
        d, r = self.opencode(extra=("foo: deny",))
        self.wrapper_fails(r, "foo")
        self.assertIn("not in the recorded OpenCode permission key list", P.row(r, WRAPPER)["reason"])

    def test_external_directory_must_be_deny(self):
        for value in ("allow", "ask"):
            with self.subTest(value):
                d, r = self.opencode(values={"external_directory": " " + value})
                self.wrapper_fails(r, "permission.external_directory must be deny", value)
                shutil.rmtree(d)

    def test_a_value_must_be_allow_ask_or_deny(self):
        for raw in (" sometimes", " [allow]", ' "Allow"'):
            with self.subTest(raw):
                d, r = self.opencode(values={"edit": raw})
                self.wrapper_fails(r, "permission.edit must be one of allow/ask/deny", raw)
                shutil.rmtree(d)

    def test_a_bash_pattern_map_is_unsupported_and_never_passes(self):
        for label, raw in BASH_PATTERNS.items():
            for md in (P.AGENT_MD, P.AGENT_MD.replace("[glob, grep, read_file]", "[glob, grep, read_file, bash]")):
                with self.subTest(label, md=md.split("tools: ")[1].split("\n")[0]):
                    d, r = self.opencode(md=md, values={"bash": raw})
                    self.assertEqual(P.row(r, WRAPPER)["status"], "fail")
                    self.assertNotEqual(P.row(r, ROW)["status"], "pass")
                    self.assertEqual(P.tool_status(d)["opencode"], "fail")
                    shutil.rmtree(d)

class TestDerivedCapabilities(OpenCodeBase, unittest.TestCase):
    def test_websearch_allow_on_an_agent_without_web_search_fails(self):
        for value in ("allow", "ask"):
            with self.subTest(value):
                d, r = self.opencode(values={"websearch": " " + value})
                self.assertEqual(P.row(r, WRAPPER)["status"], "pass")
                c = P.row(r, ROW)
                self.assertEqual(c["status"], "fail")
                self.assertIn("wrapper grants ['read', 'web_search'], AGENT.md tools need ['read']", c["reason"])
                shutil.rmtree(d)

    def test_each_granting_key_fails_on_an_agent_without_its_class(self):
        for key, cls in KEYS:
            if cls in (None, "read"): continue
            with self.subTest(key):
                d, r = self.opencode(values={key: " allow"})
                self.assertEqual(P.row(r, ROW)["status"], "fail", key)
                self.assertIn("wrapper grants %s" % sorted({"read", cls}), P.row(r, ROW)["reason"])
                shutil.rmtree(d)

    def test_denying_every_read_key_fails_an_agent_that_needs_read(self):
        d, r = self.opencode(caps=())
        self.assertEqual(P.row(r, WRAPPER)["status"], "pass")
        self.assertIn("wrapper grants [], AGENT.md tools need ['read']", P.row(r, ROW)["reason"])

    def test_one_read_key_is_enough_to_grant_read(self):
        d, r = self.opencode(caps=(), values={"grep": " allow"})
        self.assertEqual(P.row(r, ROW)["status"], "needs-review")

class TestIncompleteKeyList(OpenCodeBase, unittest.TestCase):
    def test_a_correct_read_only_wrapper_is_review_while_the_list_is_incomplete(self):
        d, r = self.opencode()
        self.assertEqual(P.row(r, WRAPPER)["status"], "pass")
        c = P.row(r, ROW)
        self.assertEqual(c["status"], "needs-review")
        self.assertIn("['read']", c["reason"]); self.assertIn(INCOMPLETE, c["reason"])
        self.assertEqual(P.tool_status(d)["opencode"], "review")

    def test_the_same_wrapper_passes_once_the_list_is_complete(self):
        with mock.patch.dict(PR.target("opencode")["permissions"], {"complete": True}):
            d, r = self.opencode()
            self.assertEqual(P.row(r, ROW)["status"], "pass")
            self.assertEqual(P.tool_status(d)["opencode"], "pass")

    def test_an_agent_with_every_class_matches_a_wrapper_that_allows_every_granting_key(self):
        caps = ("read", "write", "shell", "delegate", "web_fetch", "web_search", "skill")
        d, r = self.opencode(md=EVERY_CLASS_MD, caps=caps)
        self.assertEqual(P.row(r, WRAPPER)["status"], "pass")
        self.assertEqual(P.row(r, ROW)["status"], "needs-review")
        self.assertIn(str(sorted(caps)), P.row(r, ROW)["reason"])
        with mock.patch.dict(PR.target("opencode")["permissions"], {"complete": True}):
            self.assertEqual(P.row(eva.check(d), ROW)["status"], "pass")

    def test_check_json_reports_the_review_with_the_derived_set(self):
        d, _ = self.opencode()
        out = io.StringIO()
        with contextlib.redirect_stdout(out): eva.main(["check", d, "--json"])
        data = json.loads(out.getvalue())
        c = next(c for c in data["checks"] if c["id"] == ROW)
        self.assertEqual(c["status"], "needs-review"); self.assertIn("['read']", c["reason"])
        self.assertIn(ROW, data["needs_review"])

if __name__ == "__main__":
    unittest.main()
