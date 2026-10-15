"""PreToolUse gate, exclusive output, and the header-only cleaning fixture."""
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_skill_eval import se

ROOT = os.path.dirname(os.path.dirname(__file__))
SKILL = os.path.join(ROOT, "golden", "skills", "csv-cleaner")
SCRIPT = os.path.join(SKILL, "scripts", "clean.py")
GATE = os.path.join(ROOT, "claude_bash_gate.py")
INVOCATION = "python3 -I .claude/skills/csv-cleaner/scripts/clean.py evals/files/empty_header_spaces.csv empty_clean.csv"


class TestRound17(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.realpath(os.path.join(self.tmp.name, "work"))
        os.makedirs(os.path.join(self.work, ".claude", "skills", "csv-cleaner", "scripts"))
        os.makedirs(os.path.join(self.work, "evals", "files"))
        shutil.copy2(SCRIPT, os.path.join(self.work, ".claude", "skills", "csv-cleaner", "scripts", "clean.py"))
        shutil.copy2(os.path.join(SKILL, "evals", "files", "empty_header_spaces.csv"),
                     os.path.join(self.work, "evals", "files", "empty_header_spaces.csv"))
        self.digest = hashlib.sha256(open(SCRIPT, "rb").read()).hexdigest()

    def gate(self, command, **changes):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                   "cwd": self.work}
        payload.update(changes)
        env = dict(os.environ, SKILL_EVAL_WORKDIR=self.work)
        result = subprocess.run([sys.executable, GATE, "csv-cleaner", "scripts/clean.py", self.digest,
                                 os.path.realpath(shutil.which("python3"))],
                                input=json.dumps(payload), text=True, capture_output=True, cwd=self.work, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["hookSpecificOutput"]

    def test_hook_is_in_settings_in_both_arms(self):
        engine = se.ClaudeCodeEngine("claude-sonnet-5-5", python_script="scripts/clean.py", skill_dir=SKILL)
        for with_skill in (True, False):
            cmd = engine.command("p", with_skill, skill_name="csv-cleaner")
            self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "default")
            self.assertEqual(cmd[cmd.index("--allowedTools") + 1],
                             "Write,Edit,Bash(python3 -I .claude/skills/csv-cleaner/scripts/clean.py *)")
            self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "project,local")
            self.assertEqual("--disable-slash-commands" in cmd, not with_skill)
            settings = json.loads(cmd[cmd.index("--settings") + 1])
            hook = settings["hooks"]["PreToolUse"][0]
            self.assertEqual(hook["matcher"], "Bash")
            self.assertEqual(hook["hooks"][0]["onFailure"], "block")
            self.assertEqual(hook["hooks"][0]["args"][0], GATE)
            self.assertFalse(settings["disableAllHooks"])
            self.assertNotIn("acceptEdits", cmd)
        for flag in ("--bare", "--safe-mode"):
            with self.assertRaises(se.EvalError):
                se.ClaudeCodeEngine(extra_args=[flag])
        with self.assertRaises(se.EvalError):
            se.ClaudeCodeEngine(python_script="scripts/clean.py", skill_dir=SKILL,
                                extra_args=["--permission-mode", "acceptEdits"]).command("p", True, skill_name="csv-cleaner")
        with self.assertRaises(se.EvalError):
            se.ClaudeCodeEngine(python_script="scripts/clean.py", skill_dir=SKILL,
                                extra_args=["--permission-mode=plan"]).command("p", False, skill_name="csv-cleaner")

    def test_server_managed_cache_hook_restrictions_refuse_gated_launch(self):
        cfg = os.path.join(self.tmp.name, "config")
        os.makedirs(cfg)
        remote = os.path.join(cfg, se.CLAUDE_REMOTE_SETTINGS)
        with mock.patch.object(se, "claude_managed_sources", return_value=([], [])), \
                mock.patch.object(se, "api_key_env_problems", return_value=[]):
            for key in ("allowManagedHooksOnly", "disableAllHooks"):
                with self.subTest(key=key):
                    with open(remote, "w") as f: json.dump({key: True}, f)
                    problems = se.claude_launch_problems(self.work, cfg, require_hooks=True)
                    self.assertTrue(any("managed hook policy" in p and remote in p for p in problems), problems)
                    self.assertFalse(any("managed hook policy" in p for p in
                                         se.claude_launch_problems(self.work, cfg, require_hooks=False)))
            with open(remote, "w") as f: json.dump({"allowManagedHooksOnly": False, "disableAllHooks": False}, f)
            self.assertFalse(any("managed hook policy" in p for p in
                                 se.claude_launch_problems(self.work, cfg, require_hooks=True)))

    def test_gate_allows_only_the_exact_invocation(self):
        self.assertEqual(self.gate(INVOCATION)["permissionDecision"], "allow")
        bad = [
            INVOCATION + "; mv a b", INVOCATION + " && mv a b", INVOCATION + " || true",
            INVOCATION + " | cat", INVOCATION + " > log", INVOCATION + " 2> log",
            INVOCATION.replace("empty_clean.csv", "$(touch bad).csv"),
            INVOCATION.replace("empty_clean.csv", "`touch bad`.csv"),
            "env A=1 " + INVOCATION, "bash -c '" + INVOCATION + "'",
            INVOCATION.replace("python3", "/usr/bin/python3", 1),
            INVOCATION.replace("evals/files/", "/tmp/"),
            INVOCATION.replace("empty_clean.csv", "../empty_clean.csv"),
            INVOCATION.replace("empty_clean.csv", "*.csv"),
            INVOCATION + " extra.csv", "alias python3='python3'; " + INVOCATION,
            INVOCATION.replace("evals/files/empty_header_spaces.csv", "evals/files/../files/empty_header_spaces.csv"),
        ]
        for command in bad:
            with self.subTest(command=command):
                self.assertEqual(self.gate(command)["permissionDecision"], "deny")
        self.assertEqual(self.gate("cat input.csv")["permissionDecision"], "deny")
        self.assertEqual(self.gate(INVOCATION, cwd=self.tmp.name)["permissionDecision"], "deny")

    def test_gate_rejects_symlinks_existing_output_and_changed_script(self):
        src = os.path.join(self.work, "evals", "files", "empty_header_spaces.csv")
        alias = os.path.join(self.work, "evals", "files", "alias.csv")
        os.symlink(src, alias)
        self.assertEqual(self.gate(INVOCATION.replace("empty_header_spaces.csv", "alias.csv"))["permissionDecision"], "deny")
        os.symlink("elsewhere", os.path.join(self.work, "empty_clean.csv"))
        self.assertEqual(self.gate(INVOCATION)["permissionDecision"], "deny")
        os.unlink(os.path.join(self.work, "empty_clean.csv"))
        with open(os.path.join(self.work, ".claude", "skills", "csv-cleaner", "scripts", "clean.py"), "a") as f:
            f.write("\n# changed")
        self.assertEqual(self.gate(INVOCATION)["permissionDecision"], "deny")

    def test_cleaner_refuses_hard_link_destination(self):
        src = os.path.join(self.work, "evals", "files", "empty_header_spaces.csv")
        dst = os.path.join(self.work, "hard_link.csv")
        os.link(src, dst)
        original = open(src, "rb").read()
        with mock.patch.object(sys, "argv", [SCRIPT, src, dst]), self.assertRaises(SystemExit):
            runpy.run_path(SCRIPT)
        self.assertEqual(open(src, "rb").read(), original)

    def test_eval3_rejects_a_copy_and_requires_unchanged_original(self):
        spec, problems = se.load_eval_set(SKILL)
        self.assertEqual(problems, [])
        checks = [i["check"] for i in spec["cases"][2]["items"] if i["layer"] == "static"]
        source = os.path.join(self.work, "evals", "files", "empty_header_spaces.csv")
        shutil.copy2(source, os.path.join(self.work, "empty_clean.csv"))
        self.assertFalse(next(se.static_check(c, {}, self.work, SKILL)[0] for c in checks
                              if c["type"] == "csv_cleaned"))
        os.unlink(os.path.join(self.work, "empty_clean.csv"))
        with mock.patch.object(sys, "argv", [SCRIPT, source, os.path.join(self.work, "empty_clean.csv")]):
            runpy.run_path(SCRIPT)
        self.assertTrue(all(se.static_check(c, {}, self.work, SKILL)[0] for c in checks))
        with open(source, "a") as f: f.write("changed")
        self.assertFalse(next(se.static_check(c, {}, self.work, SKILL)[0] for c in checks
                              if c["type"] == "file_equals_fixture"))


if __name__ == "__main__": unittest.main()
