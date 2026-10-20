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

    def gate_without_command(self, tool_name, tool_input):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool_name,
                   "tool_input": tool_input, "cwd": self.work}
        env = dict(os.environ, SKILL_EVAL_WORKDIR=self.work)
        result = subprocess.run([sys.executable, GATE, "csv-cleaner", "scripts/clean.py", self.digest,
                                 os.path.realpath(shutil.which("python3"))],
                                input=json.dumps(payload), text=True, capture_output=True, cwd=self.work, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_hook_is_in_settings_in_both_arms(self):
        engine = se.ClaudeCodeEngine("claude-sonnet-5-5", python_script="scripts/clean.py", skill_dir=SKILL)
        for with_skill in (True, False):
            cmd = engine.command("p", with_skill, skill_name="csv-cleaner")
            self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "default")
            self.assertEqual(cmd[cmd.index("--allowedTools") + 1],
                             "Edit(/**),Bash(python3 -I .claude/skills/csv-cleaner/scripts/clean.py *)")
            self.assertFalse({"Write", "Edit"} & set(cmd[cmd.index("--allowedTools") + 1].split(",")))
            self.assertEqual(cmd[cmd.index("--disallowedTools") + 1:cmd.index("--disallowedTools") + 3],
                             ["Monitor", "PowerShell"])
            self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "project,local")
            self.assertEqual("--disable-slash-commands" in cmd, not with_skill)
            settings = json.loads(cmd[cmd.index("--settings") + 1])
            hook = settings["hooks"]["PreToolUse"][0]
            self.assertEqual(hook["matcher"], "*")
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
        for flag in ("--disallowedTools", "--disallowed-tools=Monitor"):
            with self.assertRaises(se.EvalError):
                se.ClaudeCodeEngine(extra_args=[flag])

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

    def test_managed_permission_rules_only_refuses_gated_launch(self):
        cfg = os.path.join(self.tmp.name, "config")
        os.makedirs(cfg)
        remote = os.path.join(cfg, se.CLAUDE_REMOTE_SETTINGS)
        managed = os.path.join(self.tmp.name, "managed-settings.json")
        with mock.patch.object(se, "claude_managed_sources", return_value=([managed], [])), \
                mock.patch.object(se, "api_key_env_problems", return_value=[]):
            for source in (managed, remote):
                with self.subTest(source=source):
                    with open(source, "w") as f: json.dump({"allowManagedPermissionRulesOnly": True}, f)
                    problems = se.claude_launch_problems(self.work, cfg, require_hooks=True)
                    self.assertTrue(any("managed permission policy" in p and source in p for p in problems), problems)
                    self.assertFalse(any("managed permission policy" in p for p in
                                         se.claude_launch_problems(self.work, cfg, require_hooks=False)))
            for source in (managed, remote):
                with open(source, "w") as f: json.dump({"allowManagedPermissionRulesOnly": False}, f)
            self.assertFalse(any("managed permission policy" in p for p in
                                 se.claude_launch_problems(self.work, cfg, require_hooks=True)))

    def test_managed_policy_helper_refuses_gated_launch(self):
        cfg = os.path.join(self.tmp.name, "config")
        os.makedirs(cfg)
        remote = os.path.join(cfg, se.CLAUDE_REMOTE_SETTINGS)
        managed = os.path.join(self.tmp.name, "managed-settings.json")
        with mock.patch.object(se, "claude_managed_sources", return_value=([managed], [])), \
                mock.patch.object(se, "api_key_env_problems", return_value=[]):
            for source in (managed, remote):
                with self.subTest(source=source):
                    with open(source, "w") as f: json.dump({"policyHelper": "managed-helper"}, f)
                    problems = se.claude_launch_problems(self.work, cfg, require_hooks=True)
                    self.assertTrue(any("managed policyHelper" in p and source in p for p in problems), problems)
                    self.assertFalse(any("managed policyHelper" in p for p in
                                         se.claude_launch_problems(self.work, cfg, require_hooks=False)))
            for source in (managed, remote):
                with open(source, "w") as f: json.dump({"policyHelper": ""}, f)
            self.assertTrue(any("managed policyHelper" in p for p in
                                se.claude_launch_problems(self.work, cfg, require_hooks=True)))
            for source in (managed, remote): os.unlink(source)
            self.assertFalse(any("managed policyHelper" in p for p in
                                 se.claude_launch_problems(self.work, cfg, require_hooks=True)))

    def test_gate_allows_only_the_exact_invocation(self):
        self.assertEqual(self.gate(INVOCATION)["permissionDecision"], "allow")
        self.assertEqual(self.gate(INVOCATION, tool_name="PowerShell")["permissionDecision"], "deny")
        self.assertEqual(self.gate(INVOCATION, tool_name="Monitor")["permissionDecision"], "deny")
        self.assertEqual(self.gate(INVOCATION, tool_name="FutureShell")["permissionDecision"], "deny")
        self.assertEqual(self.gate("cat input.csv", tool_name="FutureShell")["permissionDecision"], "deny")
        self.assertEqual(json.loads(self.gate_without_command("FutureShell", {"options": {"command": "cat x"}}))
                         ["hookSpecificOutput"]["permissionDecision"], "deny")
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

    def test_file_edits_are_scoped_in_both_arms(self):
        engine = se.ClaudeCodeEngine("claude-sonnet-5-5", python_script="scripts/clean.py", skill_dir=SKILL)
        installed = os.path.join(self.work, ".claude", "skills", "csv-cleaner", "scripts", "clean.py")
        outside = os.path.join(self.tmp.name, "outside.csv")
        os.symlink(self.tmp.name, os.path.join(self.work, "escape"))
        for with_skill in (True, False):
            with self.subTest(with_skill=with_skill):
                cmd = engine.command("p", with_skill, skill_name="csv-cleaner")
                self.assertEqual(cmd[cmd.index("--allowedTools") + 1].split(",")[0], "Edit(/**)")
                for tool, key in (("Write", "file_path"), ("Edit", "file_path"),
                                  ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
                    for path in (GATE, installed, "../outside.csv", "escape/outside.csv"):
                        result = json.loads(self.gate_without_command(tool, {key: path}))
                        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny", (tool, path))
                    self.assertEqual(self.gate_without_command(tool, {key: "normal_output.csv"}), "")
                self.assertEqual(json.loads(self.gate_without_command("FutureEdit", {"file_path": outside}))
                                 ["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertEqual(self.gate_without_command("FutureEdit", {"file_path": "normal_output.csv"}), "")
                self.assertEqual(json.loads(self.gate_without_command("MultiEdit", {
                    "edits": [{"file_path": "normal_output.csv"}, {"file_path": installed}]}))
                                 ["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_non_command_tools_get_no_hook_decision(self):
        for tool_name, tool_input in (("Read", {"file_path": "input.csv"}),
                                      ("Write", {"file_path": "out.csv", "content": "x"}),
                                      ("Edit", {"file_path": "out.csv", "old_string": "x", "new_string": "y"}),
                                      ("Glob", {"pattern": "*.csv"}), ("Grep", {"pattern": "x"}),
                                      ("Skill", {"skill": "csv-cleaner"}),
                                      ("TodoWrite", {"todos": []})):
            with self.subTest(tool_name=tool_name):
                self.assertEqual(self.gate_without_command(tool_name, tool_input), "")
        self.assertEqual(json.loads(self.gate_without_command("Monitor", {"source": "websocket"}))
                         ["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_readme_describes_command_tool_policy(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertIn('PreToolUse`/`*` 훅', readme)
        self.assertIn('--disallowedTools Monitor PowerShell', readme)
        self.assertIn('Read, Glob, Grep, Skill, TodoWrite', readme)

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
        self.assertEqual([x for x in problems if not x.startswith("english-only rule:")], [])  # golden csv-cleaner is Korean: only english-only problems remain (golden/ is neither exempted nor edited)
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
