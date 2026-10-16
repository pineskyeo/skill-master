"""Account-switch caller marker (2026-10-16): every claude child carries CLAUDE_AS_CALLER=skill_eval plus the opt-in,
no token is ever passed by skill_eval, other CLIs get no marker, and acctN server-managed caches are checked."""
import json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import skill_eval as se


class AccountMarker(unittest.TestCase):
    def test_claude_gets_marker_and_optin(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SKILL_EVAL_ACCOUNT_SWITCH", None)
            env = se.with_account_marker({"PATH": "/bin"}, ["claude", "-p", "x"])
        self.assertEqual(env["CLAUDE_AS_CALLER"], "skill_eval"); self.assertEqual(env["CLAUDE_AS_AUTO_SWITCH"], "1")

    def test_opt_out(self):
        with mock.patch.dict(os.environ, {"SKILL_EVAL_ACCOUNT_SWITCH": "0"}):
            env = se.with_account_marker({}, ["claude", "-p"])
            self.assertEqual(env["CLAUDE_AS_AUTO_SWITCH"], "0")
            self.assertEqual(se.switch_account_config_dirs(), [])

    def test_codex_gets_no_marker_and_inherited_marker_removed(self):
        env = se.with_account_marker({"CLAUDE_AS_CALLER": "x", "CLAUDE_AS_AUTO_SWITCH": "1"}, ["codex", "exec"])
        self.assertNotIn("CLAUDE_AS_CALLER", env); self.assertNotIn("CLAUDE_AS_AUTO_SWITCH", env)

    def test_live_exec_never_passes_token_but_marks(self):
        seen = {}
        def fake_run(cmd, **kw):
            seen.update(kw["env"])
            class P: returncode, stdout, stderr = 0, "", ""
            return P()
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "SECRETVAL"}), \
             mock.patch.object(se.subprocess, "run", fake_run), mock.patch.dict(se._LIVE, {"on": True}):
            se.live_exec(["claude", "-p", "x"], "/tmp", 5)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", seen); self.assertEqual(seen["CLAUDE_AS_CALLER"], "skill_eval")

    def test_auth_status_and_child_env_marked(self):
        seen = {}
        def fake_run(cmd, **kw):
            seen.update(kw["env"])
            class P: returncode, stdout, stderr = 0, "{}", ""
            return P()
        with mock.patch.object(se.subprocess, "run", fake_run):
            se.run_auth_status(se.CLAUDE_AUTH_STATUS_CMD)
        self.assertEqual(seen["CLAUDE_AS_CALLER"], "skill_eval")
        self.assertEqual(se.claude_child_env()["CLAUDE_AS_CALLER"], "skill_eval")

    def test_acctN_remote_settings_checked(self):
        home = tempfile.mkdtemp()
        os.mkdir(os.path.join(home, ".claude-acct1")); os.mkdir(os.path.join(home, ".claude-acctx"))
        with open(os.path.join(home, ".claude-acct1", "remote-settings.json"), "w") as f:
            json.dump({"env": {"ANTHROPIC_API_KEY": "v"}}, f)
        dirs = se.switch_account_config_dirs(home)
        self.assertEqual(dirs, [os.path.join(home, ".claude-acct1")])
        with mock.patch.object(se, "switch_account_config_dirs", lambda: dirs):
            probs = se.claude_launch_problems(tempfile.mkdtemp())
        self.assertTrue(any(".claude-acct1" in p and "ANTHROPIC_API_KEY" in p for p in probs), probs)
        self.assertFalse(any("v\"" in p for p in probs))


if __name__ == "__main__":
    unittest.main()
