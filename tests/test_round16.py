"""Round 16: narrow Claude script permission and file-based CSV assertions; no model calls."""
import csv
import io
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_skill_eval import se

SKILL = os.path.join(os.path.dirname(os.path.dirname(__file__)), "golden", "skills", "csv-cleaner")


class TestCsvCleanerRound16(unittest.TestCase):
    def setUp(self):
        self.spec, problems = se.load_eval_set(SKILL)
        self.assertEqual([x for x in problems if not x.startswith("english-only rule:")], [])  # golden csv-cleaner is Korean: only english-only problems remain (golden/ is neither exempted nor edited)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_permissions_include_writes_edits_and_only_the_installed_script_for_bash_in_both_arms(self):
        e = se.ClaudeCodeEngine("claude-sonnet-5-5", python_script=self.spec["claude_python_script"], skill_dir=SKILL)
        rule = "Bash(python3 -I .claude/skills/csv-cleaner/scripts/clean.py *)"
        for with_skill in (True, False):
            cmd = e.command("clean", with_skill, 1.0, skill_name="csv-cleaner")
            self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "default")
            self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "Edit(/**)," + rule)
            self.assertNotIn("Bash", cmd)
            self.assertNotIn("Bash(*)", cmd)
        self.assertNotIn("--allowedTools", se.ClaudeCodeEngine().command("x", True, skill_name="other"))
        for extra in (["--allowedTools", "Bash"], ["--allowed-tools=Bash"],
                      ["--permission-mode", "bypassPermissions"]):
            with self.assertRaises(se.EvalError): se.ClaudeCodeEngine(extra_args=extra)

    def test_dry_run_prints_narrow_rule(self):
        e = se.ClaudeCodeEngine("claude-sonnet-5-5", python_script=self.spec["claude_python_script"], skill_dir=SKILL)
        a = mock.Mock(argv=["run", SKILL, "--live", "--dry-run"], grader_engine="claude", budget_usd=12.5)
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(se, "check_live_gates", return_value={"total_usd": 8.0}), \
                mock.patch.object(se, "plan_text", return_value=""):
            se.live_dry_run(a, self.spec, [], self.tmp.name, None, [e], {}, {})
        self.assertIn("claude --permission-mode (with_skill and without_skill): default", out.getvalue())
        self.assertIn("claude --allowedTools (with_skill and without_skill): Edit(/**)," +
                      e.script_allow_rule("csv-cleaner"), out.getvalue())
        self.assertIn("claude --disallowedTools (with_skill and without_skill): Monitor PowerShell", out.getvalue())

    def test_file_checks_reject_missing_modified_and_partial_outputs(self):
        src = "evals/files/events_large.csv"
        chk = {"type": "csv_cleaned", "path": "events_clean.csv", "fixture": src, "data_rows": 480}
        self.assertFalse(se.static_check(chk, {}, self.tmp.name, SKILL)[0])
        with open(os.path.join(SKILL, src), newline="", encoding="utf-8") as f: rows = list(csv.reader(f))
        seen, cleaned = set(), []
        for row in rows:
            row = tuple(c.strip() for c in row)
            if row not in seen: seen.add(row); cleaned.append(row)
        with open(os.path.join(self.tmp.name, "events_clean.csv"), "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(cleaned)
        self.assertTrue(se.static_check(chk, {}, self.tmp.name, SKILL)[0])
        with open(os.path.join(self.tmp.name, "events_clean.csv"), "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(cleaned[:-1])
        self.assertFalse(se.static_check(chk, {}, self.tmp.name, SKILL)[0])
        original = {"type": "file_equals_fixture", "path": src, "fixture": src}
        dest = os.path.join(self.tmp.name, src)
        os.makedirs(os.path.dirname(dest))
        with open(os.path.join(SKILL, src), "rb") as f: payload = f.read()
        with open(dest, "wb") as f: f.write(payload)
        self.assertTrue(se.static_check(original, {}, self.tmp.name, SKILL)[0])
        with open(dest, "ab") as f: f.write(b"changed")
        self.assertFalse(se.static_check(original, {}, self.tmp.name, SKILL)[0])

    def test_file_check_rejects_symlink_outside_workdir(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        path = os.path.join(outside.name, "secret.csv")
        with open(path, "w") as f: f.write("do not read")
        os.symlink(path, os.path.join(self.tmp.name, "events_clean.csv"))
        chk = {"type": "csv_cleaned", "path": "events_clean.csv", "fixture": "evals/files/events_large.csv",
               "data_rows": 480}
        self.assertEqual(se.static_check(chk, {}, self.tmp.name, SKILL), (False, "file path escapes its root"))

    def test_run_budget_estimate_excludes_later_grading(self):
        a = mock.Mock(cmd="run", grader_engine="codex", grader_model=None, baseline="none", runs=1)
        est = se.live_estimate(a, self.spec, self.tmp.name, None,
                               [se.ClaudeCodeEngine("claude-sonnet-5-5")], se.DEFAULT_TOKENS, {})
        self.assertEqual({row["kind"] for row in est["rows"]}, {"exec", "trigger"})

    def test_clean_script_refuses_same_input_and_output(self):
        src = os.path.join(self.tmp.name, "input.csv")
        with open(src, "w") as f: f.write("id,name\n1, apple \n1, apple \n")
        script = os.path.join(SKILL, "scripts", "clean.py")
        with mock.patch.object(sys, "argv", [script, src, src]):
            with self.assertRaises(SystemExit): runpy.run_path(script)
        with open(src) as f: self.assertEqual(f.read(), "id,name\n1, apple \n1, apple \n")
        out = os.path.join(self.tmp.name, "out.csv")
        with mock.patch.object(sys, "argv", [script, src, out]): runpy.run_path(script)
        with open(out) as f: self.assertEqual(f.read().splitlines(), ["id,name", "1,apple"])


if __name__ == "__main__": unittest.main()
