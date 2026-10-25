"""Codex custom-agent permissions (plan C2). The Codex doc says sandbox_mode in a custom agent file does not override the
parent session (UNVERIFIED on codex-cli 0.162.1), so a Codex wrapper restricts nothing and parity-permissions-codex is
REVIEW for every capability class AGENT.md lacks. Mocked like tests/test_review_round2.py: subprocess is patched to fail
for the whole module, no claude/codex/opencode/hermes call, no network, no approve command (a decision is written to
approvals.json directly)."""
import contextlib, io, json, os, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva
import skill_eval as se
import test_eva as E
import test_skill_eval as T
import test_multi_tool_parity as P

ROW = "parity-permissions-codex"
CODEX_REL = "wrappers/codex/code-scout.toml"
REMOVE = "sandbox_mode in a Codex custom agent file does not override the parent session; remove it"
READ_ONLY = ("Codex cannot restrict write, shell, web_fetch, web_search, delegate, skill per agent; the agent runs with the "
             "parent session's sandbox")
ALL_CAPS_MD = P.AGENT_MD.replace("[search, read]", "[read, write, shell, web_fetch, web_search, delegate, skill]")
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def codex_wrapper(sandbox_mode=None, instructions=True, extra=()):
    """A Codex wrapper for P.AGENT_MD, built here so these tests do not depend on the shared fixture's Codex text."""
    desc = eva.parse_frontmatter(P.AGENT_MD)[0]["description"]
    lines = ['name = "code-scout"', "description = %s" % json.dumps(desc)]
    if sandbox_mode is not None: lines.append('sandbox_mode = "%s"' % sandbox_mode)
    if instructions: lines += ['developer_instructions = """', "You are code-scout.", '"""']
    return "\n".join(lines + list(extra)) + "\n"

class TestSandboxModeIsNotAPermission(P.AgentBase, unittest.TestCase):
    def test_a_wrapper_that_sets_sandbox_mode_fails_with_the_remove_message(self):
        for label, md in (("read-only agent", P.AGENT_MD), ("agent with every capability", ALL_CAPS_MD)):
            for value in ("read-only", "workspace-write", "danger-full-access", "yolo"):
                with self.subTest(label, sandbox_mode=value):
                    d, r = self.agent(md=md, replace={CODEX_REL: codex_wrapper(value)})
                    wrapper = P.row(r, "parity-wrapper-codex")
                    self.assertEqual(wrapper["status"], "fail")
                    self.assertIn("%s: %s" % (CODEX_REL, REMOVE), wrapper["reason"])
                    self.assertNotEqual(P.row(r, ROW)["status"], "pass")
                    self.assertEqual(P.tool_status(d)["codex"], "fail")
                    shutil.rmtree(d)

    def test_a_wrapper_with_problems_never_passes_the_permission_row(self):
        for label, kw in (("missing wrapper", {"skip": ("codex",)}),
                          ("no developer_instructions", {"replace": {CODEX_REL: codex_wrapper(instructions=False)}})):
            with self.subTest(label):
                d, r = self.agent(md=ALL_CAPS_MD, **kw)
                self.assertEqual(P.row(r, "parity-wrapper-codex")["status"], "fail")
                self.assertNotEqual(P.row(r, ROW)["status"], "pass")
                shutil.rmtree(d)

class TestCodexKeyAllowlist(P.AgentBase, unittest.TestCase):
    def wrapper_row(self, *extra):
        d, r = self.agent(replace={CODEX_REL: codex_wrapper(extra=extra)})
        shutil.rmtree(d)
        return P.row(r, "parity-wrapper-codex")

    def test_a_key_outside_the_recorded_list_fails_and_is_named(self):
        for key in ("foo", "approval_policy", "model_provider"):
            with self.subTest(key):
                row = self.wrapper_row('%s = "x"' % key)
                self.assertEqual(row["status"], "fail")
                self.assertIn("'%s'" % key, row["reason"])

    def test_the_five_recorded_keys_pass_the_wrapper_row(self):
        row = self.wrapper_row('model = "gpt-5"', 'model_reasoning_effort = "high"')
        self.assertEqual(row["status"], "pass", row["reason"])

class TestCodexPermissionRow(P.AgentBase, unittest.TestCase):
    def test_a_read_only_agent_is_review_never_pass(self):
        d, r = self.agent(replace={CODEX_REL: codex_wrapper()})
        self.assertEqual(P.row(r, "parity-wrapper-codex")["status"], "pass")
        c = P.row(r, ROW)
        self.assertEqual(c["status"], "needs-review")
        self.assertEqual(c["reason"], "needs-review: [codex] " + READ_ONLY)
        self.assertEqual(P.tool_status(d)["codex"], "review")

    def test_the_message_names_each_missing_class_in_the_codex_unrestrictable_order(self):
        for tools, missing in (("[read, shell]", "write, web_fetch, web_search, delegate, skill"),
                               ("[read, write]", "shell, web_fetch, web_search, delegate, skill"),
                               ("[read, write, shell]", "web_fetch, web_search, delegate, skill"),
                               ("[web_fetch]", "write, shell, web_search, delegate, skill"),
                               ("[web_search, delegate]", "write, shell, web_fetch, skill"),
                               ("[read, write, shell, web_fetch, web_search, delegate]", "skill")):
            with self.subTest(tools):
                d, r = self.agent(md=P.AGENT_MD.replace("[search, read]", tools), replace={CODEX_REL: codex_wrapper()})
                self.assertEqual(P.row(r, ROW)["reason"], "needs-review: [codex] Codex cannot restrict %s per agent; "
                                                          "the agent runs with the parent session's sandbox" % missing)
                shutil.rmtree(d)

    def test_an_agent_with_every_capability_passes(self):
        d, r = self.agent(md=ALL_CAPS_MD, replace={CODEX_REL: codex_wrapper()})
        self.assertEqual(P.row(r, ROW)["status"], "pass")
        self.assertEqual(P.tool_status(d)["codex"], "pass")

    def test_check_json_reports_the_row_as_needs_review(self):
        d, _ = self.agent(replace={CODEX_REL: codex_wrapper()})
        out = io.StringIO()
        with contextlib.redirect_stdout(out): eva.main(["check", d, "--json"])
        data = json.loads(out.getvalue())
        self.assertEqual(next(c for c in data["checks"] if c["id"] == ROW)["status"], "needs-review")
        self.assertIn(ROW, data["needs_review"])

class TestCodexReviewReachesTheRuleReviewGate(P.CliBase):
    def agent_dir(self, md):
        d = os.path.join(self.tmp, "code-scout"); os.makedirs(os.path.join(d, "scenarios"), exist_ok=True)
        E.wtext(os.path.join(d, "AGENT.md"), md)
        E.wtext(os.path.join(d, "scenarios", "01.json"), json.dumps(P.S1))
        P.write_wrappers(d, replace={CODEX_REL: codex_wrapper()})
        return d

    def test_the_review_needs_a_recorded_decision_bound_to_the_missing_classes(self):
        d = self.agent_dir(P.AGENT_MD)
        spec, _ = se.load_eval_set(d)
        unrecorded = se.rule_review_problems(spec, self.ws)
        self.assertEqual(len(unrecorded), 1, unrecorded)
        self.assertIn("%s: needs-review: [codex] %s" % (ROW, READ_ONLY), unrecorded[0])
        _, out, _ = self.cli("validate", d, "--workspace", self.ws)
        self.assertIn("RULE REVIEW NOT RECORDED", out); self.assertIn("review: needs-review: [codex] " + READ_ONLY, out)
        os.makedirs(self.ws)
        self.assertEqual(se.approve("rule_review", spec, self.ws)["sha256"], se.rule_review_hash(d))
        self.assertEqual(se.rule_review_problems(spec, self.ws), [])
        self.assertIn("rule review decision recorded for these exact findings", self.cli("validate", d, "--workspace", self.ws)[1])
        self.agent_dir(P.AGENT_MD.replace("[search, read]", "[search, read, shell]"))
        again = se.rule_review_problems(spec, self.ws)
        self.assertEqual(len(again), 1, again); self.assertIn("Codex cannot restrict write, web_fetch, web_search, delegate, skill per agent", again[0])

if __name__ == "__main__":
    unittest.main()
