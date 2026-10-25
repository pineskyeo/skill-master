"""H2: the loop builder may change only SKILL.md, references/**, scripts/** and assets/** of the skill folder. The hook
cases run the exact command that the builder's --settings object names, as a subprocess with the payload on stdin. The
builder is always a fake (a plain callable, or ClaudeBuilder with a fake exec_fn); no claude or codex CLI is started.
This file stays ASCII: non-English text is built from escapes."""
import contextlib, io, json, os, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva, skill_eval as se
import test_skill_eval as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "claude_bash_gate.py")
KO = "".join(map(chr, (0xD55C, 0xAD6D, 0xC5B4)))
RESULT = json.dumps({"type": "result", "total_cost_usd": 0.5})
DELETED_SENTENCE = "Do not edit evals/"

def settings_of(cmd):
    return json.loads(cmd[cmd.index("--settings") + 1])

def builder_hook(skill_dir):
    """The PreToolUse hook entry of the builder's generated --settings object."""
    st = settings_of(se.ClaudeBuilder("x", "claude-opus-5-5", None).command("p", None, skill_dir))
    (entry,) = st["hooks"]["PreToolUse"]
    (hook,) = entry["hooks"]
    return st, entry, hook

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: f.write(text)

def reformat(path, **changes):
    with open(path, encoding="utf-8") as f: doc = json.load(f)
    doc.update(changes)
    with open(path, "w", encoding="utf-8") as f: json.dump(doc, f, ensure_ascii=False, indent=4)

def tree_state(root):
    out = {}
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            if os.path.islink(p) or os.path.isdir(p): out[p] = "x"
            else:
                with open(p, "rb") as f: out[p] = f.read()
    return out

class Fixture(T.Base):
    """A skill folder next to its workspace (tmp/branch-setup and tmp/ws), and the builder's hook command for it."""
    def setUp(self):
        T.Base.setUp(self)
        self.spec_, _ = T.Base.spec(self, evals=T.evals_doc(human=False))
        self.skill = self.spec_["skill_dir"]
        os.makedirs(self.ws, exist_ok=True)
        write(os.path.join(self.ws, "approvals.json"), "{}\n")
        _, _, hook = builder_hook(self.skill)
        self.argv = [hook["command"]] + hook["args"]
        self.lines = []

    def run_hook(self, payload, argv=None, raw=None):
        p = subprocess.run(argv or self.argv, input=raw if raw is not None else json.dumps(payload),
                           capture_output=True, text=True, cwd=self.skill, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        if not p.stdout.strip(): return None, ""
        out = json.loads(p.stdout)["hookSpecificOutput"]
        return out["permissionDecision"], out["permissionDecisionReason"]

    def hook(self, tool, tool_input, cwd=None, argv=None):
        return self.run_hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                              "cwd": self.skill if cwd is None else cwd}, argv)[0]

    def path(self, rel):
        return os.path.join(self.skill, rel)

    def loop(self, builder, max_iterations=3, ws=None):
        return se.loop(self.spec_, ws or self.ws, self.engines(regress=True)[:1], self.grader(), builder, runs=1,
                       log=self.lines.append, max_iterations=max_iterations)

class TestAllowedEdits(Fixture):
    WRITABLE = ("SKILL.md", "references/x.md", "scripts/x.py", "assets/x.txt")

    def test_hook_allows_writes_and_edits_inside_the_writable_set(self):
        for rel in self.WRITABLE:
            self.assertEqual(self.hook("Write", {"file_path": self.path(rel), "content": "text"}), "allow", rel)
        self.assertEqual(self.hook("Edit", {"file_path": self.path("SKILL.md"), "old_string": "a", "new_string": "b"}), "allow")
        self.assertEqual(self.hook("MultiEdit", {"file_path": self.path("references/deep/a.md"), "edits": []}), "allow")
        self.assertEqual(self.hook("NotebookEdit", {"notebook_path": self.path("assets/n.ipynb"), "new_source": "x"}), "allow")

    def test_relative_path_from_the_skill_folder_is_allowed(self):
        self.assertEqual(self.hook("Write", {"file_path": "references/x.md", "content": "t"}), "allow")

    def test_same_folder_through_a_path_alias_is_allowed(self):
        alias = os.path.join(self.tmp, "alias")
        os.symlink(self.skill, alias)
        self.assertEqual(self.hook("Edit", {"file_path": os.path.join(alias, "SKILL.md")}), "allow")
        upper = self.skill[:-len("branch-setup")] + "BRANCH-SETUP"
        if os.path.exists(upper):  # case-insensitive file system: the root as typed, the path as stored
            argv = self.argv[:-1] + [upper]
            self.assertEqual(self.hook("Edit", {"file_path": self.path("SKILL.md")}, argv=argv), "allow")

    def test_loop_keeps_running_after_writable_edits(self):
        calls = []
        def builder(it, sd):
            calls.append(it)
            for rel in self.WRITABLE[1:]: write(os.path.join(sd, rel), "More detail.\n")
            with open(os.path.join(sd, "SKILL.md"), "a", encoding="utf-8") as f: f.write("\nMore detail.\n")
            return 0
        r = self.loop(builder, max_iterations=2)
        self.assertEqual(r, {"status": "needs-approval", "iteration": 2})
        self.assertEqual(len(calls), 1)
        self.assertFalse([l for l in self.lines if "builder-wrote-outside" in str(l)])

class TestEvalsEdit(Fixture):
    def test_hook_denies_every_write_into_evals(self):
        ev = self.path("evals/evals.json")
        self.assertEqual(self.hook("Edit", {"file_path": ev, "old_string": "a", "new_string": "b"}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": self.path("evals/new.json"), "content": "{}"}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": self.path("references/../evals/evals.json")}), "deny")
        self.assertEqual(self.hook("Edit", {"file_path": "evals/evals.json"}), "deny")
        self.assertEqual(self.hook("MultiEdit", {"file_path": self.path("SKILL.md"), "edits": [],
                                                 "more": {"file_path": ev}}), "deny")

    def test_hook_denies_the_rest_of_the_skill_folder(self):
        for rel in ("agents/openai.yaml", "README.md", ".claude/settings.json", "Skill.md", "referencesX/a.md"):
            self.assertEqual(self.hook("Write", {"file_path": self.path(rel), "content": "x"}), "deny", rel)
        self.assertEqual(self.hook("Write", {"file_path": self.skill}), "deny")

    def test_writable_names_match_exactly(self):
        for rel in ("SKILL.md.bak", "SKILL.md~", "SKILL.mdx", "References/a.md", "SCRIPTS/x.py", "Assets/x.txt"):
            self.assertEqual(self.hook("Write", {"file_path": self.path(rel), "content": "x"}), "deny", rel)

    def test_builder_write_to_a_hidden_settings_file_stops_the_loop(self):
        def builder(it, sd):
            write(os.path.join(sd, ".claude", "settings.json"), '{"hooks": {}}'); return 0
        r = self.loop(builder)
        self.assertEqual((r["status"], r["iteration"], r.get("problems")),
                         ("builder-wrote-outside", 1, [".claude", ".claude/settings.json"]))

    def test_deny_reason_names_the_writable_set(self):
        _, reason = self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": self.skill,
                                   "tool_input": {"file_path": self.path("evals/evals.json")}})
        self.assertIn("SKILL.md references/** scripts/** assets/**", reason)

    def test_stub_builder_that_writes_evals_json_stops_the_loop_after_one_call(self):
        calls = []
        def builder(it, sd):
            calls.append(it); reformat(os.path.join(sd, "evals", "evals.json")); return 0
        with mock.patch.object(se, "run_iteration", wraps=se.run_iteration) as runs:
            r = self.loop(builder, max_iterations=3)
        self.assertEqual((r["status"], r["iteration"], r["problems"]), ("builder-wrote-outside", 1, ["evals/evals.json"]))
        self.assertEqual(len(calls), 1)
        self.assertEqual(runs.call_count, 1)
        self.assertEqual(se.iterations(self.ws), [1])
        self.assertIn("builder-wrote-outside evals/evals.json", self.lines)

class TestWorkspaceApprovals(Fixture):
    def test_hook_denies_the_sibling_workspace(self):
        target = os.path.join(self.ws, "approvals.json")
        self.assertEqual(self.hook("Write", {"file_path": target, "content": "{}"}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": os.path.realpath(target), "content": "{}"}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": "../ws/approvals.json", "content": "{}"}), "deny")
        self.assertEqual(self.hook("Edit", {"file_path": os.path.join(self.ws, "iteration-1", "cost.json")}), "deny")

    def test_builder_write_to_approvals_json_stops_the_loop(self):
        def builder(it, sd):
            write(os.path.join(self.ws, "approvals.json"), '{"forged": true}\n'); return 0
        r = self.loop(builder)
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["../ws/approvals.json"]))
        self.assertIn("builder-wrote-outside ../ws/approvals.json", self.lines)

    def test_new_and_removed_workspace_files_count(self):
        def builder(it, sd):
            os.remove(os.path.join(self.ws, "approvals.json")); write(os.path.join(self.ws, "requirements.md"), "x"); return 0
        r = self.loop(builder)
        self.assertEqual(r["problems"], ["../ws/approvals.json", "../ws/requirements.md"])

class TestSymlinkEscape(Fixture):
    def setUp(self):
        Fixture.setUp(self)
        self.outside = os.path.join(self.tmp, "outside")
        write(os.path.join(self.outside, "secret.txt"), "secret\n")
        os.makedirs(self.path("references"))

    def test_hook_denies_a_link_in_references_that_points_outside(self):
        os.symlink(os.path.join(self.outside, "secret.txt"), self.path("references/link.md"))
        self.assertEqual(self.hook("Edit", {"file_path": self.path("references/link.md")}), "deny")

    def test_hook_denies_a_folder_link_and_a_dangling_link(self):
        os.symlink(self.outside, self.path("references/dir"))
        os.symlink("../evals/new.json", self.path("references/new.json"))
        self.assertEqual(self.hook("Write", {"file_path": self.path("references/dir/new.md")}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": self.path("references/new.json")}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": self.path("references/dir/../SKILL.md")}), "deny")

    def test_hook_denies_a_hard_link(self):
        os.link(os.path.join(self.outside, "secret.txt"), self.path("references/hard.md"))
        self.assertEqual(self.hook("Edit", {"file_path": self.path("references/hard.md")}), "deny")

    def test_write_through_a_link_the_hook_missed_stops_the_loop(self):
        os.symlink("../evals/evals.json", self.path("references/evals-link.json"))
        r = self.loop(lambda it, sd: reformat(os.path.join(sd, "references", "evals-link.json")) or 0)
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["evals/evals.json"]))

class TestBashDenied(Fixture):
    def test_command_tools_are_denied(self):
        self.assertEqual(self.hook("Bash", {"command": "ls"}), "deny")
        self.assertEqual(self.hook("Monitor", {"command": "tail -f x"}), "deny")
        self.assertEqual(self.hook("PowerShell", {"command": "dir"}), "deny")
        self.assertEqual(self.hook("Write", {"file_path": self.path("SKILL.md"), "x": [{"command": "rm -rf /"}]}), "deny")

    def test_reads_keep_the_normal_flow_and_unknown_tools_are_denied(self):
        for tool, inp in (("Read", {"file_path": self.path("evals/evals.json")}), ("Glob", {"pattern": "**"}),
                          ("Grep", {"pattern": "x", "path": self.ws})):
            self.assertIsNone(self.hook(tool, inp), tool)
        self.assertEqual(self.hook("Move", {"file_path": self.path("SKILL.md"), "destination": self.path("evals/x")}), "deny")
        self.assertEqual(self.hook("WebFetch", {"url": "https://example.com"}), "deny")

class TestHookFailsClosed(Fixture):
    def test_bad_input_denies(self):
        for raw in ("not json", "[]", json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write",
                                                  "tool_input": {"file_path": self.path("SKILL.md")}})):
            self.assertEqual(self.run_hook(None, raw=raw)[0], "deny", raw)
        deep = '{"x": ' * 5000 + json.dumps({"file_path": self.path("SKILL.md")}) + "}" * 5000
        raw = '{"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": %s, "tool_input": %s}' % (
            json.dumps(self.skill), deep)
        self.assertEqual(self.run_hook(None, raw=raw)[0], "deny")
        for bad in ({}, {"file_path": ""}, {"file_path": 7}, {"file_path": self.path("SKILL.md") + "\x00"}):
            self.assertEqual(self.hook("Edit", bad), "deny", bad)

    def test_bad_root_denies(self):
        link = os.path.join(self.tmp, "skill-link"); os.symlink(self.skill, link)
        for root in ("branch-setup", os.path.join(self.tmp, "missing"), link, ""):
            argv = self.argv[:-1] + [root]
            self.assertEqual(self.hook("Write", {"file_path": self.path("SKILL.md")}, argv=argv), "deny", root)

    def test_unknown_mode_word_denies(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": self.skill,
                   "tool_input": {"file_path": self.path("SKILL.md")}}
        for mode in ("edit-allowlistx", "Edit-Allowlist", "executor", ""):
            argv = self.argv[:2] + [mode] + self.argv[3:]
            self.assertEqual(self.run_hook(payload, argv=argv), ("deny", "unknown gate mode"), mode)

    def test_relative_path_from_another_folder_denies(self):
        self.assertEqual(self.hook("Write", {"file_path": "SKILL.md"}, cwd=self.ws), "deny")

class TestCheckOrder(Fixture):
    def test_outside_check_runs_before_the_english_check(self):
        r = self.loop(lambda it, sd: reformat(os.path.join(sd, "evals", "evals.json"), note=KO) or 0)
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["evals/evals.json"]))

    def test_no_later_check_runs_after_an_outside_write(self):
        after = []
        def builder(it, sd):
            after.append("builder"); reformat(os.path.join(sd, "evals", "evals.json")); return 0
        def spy(name, fn):
            return lambda *a, **k: after.append(name) or fn(*a, **k)
        with mock.patch.object(eva, "english_violations", spy("english", eva.english_violations)), \
             mock.patch.object(eva, "parity_violations", spy("parity", eva.parity_violations)), \
             mock.patch.object(se, "rule_review_problems", spy("review", se.rule_review_problems)), \
             mock.patch.object(se, "load_eval_set", spy("eval-set", se.load_eval_set)):
            r = self.loop(builder)
        self.assertEqual(r["status"], "builder-wrote-outside")
        self.assertEqual(after[after.index("builder"):], ["builder"])

    def test_builder_that_raises_after_launch_is_checked(self):
        def builder(it, sd):
            reformat(os.path.join(sd, "evals", "evals.json"))
            raise se.EvalError("builder output is not a JSON object")
        r = self.loop(builder)
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["evals/evals.json"]))
        self.assertIn("not a JSON object", r["error"])

    def test_builder_that_crashes_is_checked_and_a_clean_crash_propagates(self):
        def dirty(it, sd):
            reformat(os.path.join(sd, "evals", "evals.json")); raise RuntimeError("boom")
        r = self.loop(dirty)
        self.assertEqual(r["status"], "builder-wrote-outside"); self.assertIn("RuntimeError", r["error"])
        def clean(it, sd): raise RuntimeError("boom")
        with self.assertRaises(RuntimeError): self.loop(clean)

class TestHarnessCost(Fixture):
    def builder(self, exec_fn):
        return se.ClaudeBuilder("Improve it.", "claude-opus-5-5", se.Budget(10.0), exec_fn=exec_fn,
                                skill_name=self.spec_["skill_name"])

    def test_harness_cost_write_does_not_stop_the_loop(self):
        seen = []
        r = self.loop(self.builder(lambda cmd, cwd, timeout, env=None: seen.append(cwd) or (0, RESULT, "")),
                      max_iterations=2)
        self.assertEqual(r, {"status": "needs-approval", "iteration": 2})
        self.assertEqual(len(seen), 1)
        entries = T.rjson(os.path.join(self.ws, "iteration-1", "cost.json"))["entries"]
        self.assertEqual([e["command"] for e in entries if e["command"] == "builder"], ["builder"])

    def test_builder_write_to_cost_json_stops_the_loop(self):
        cost, calls = os.path.join(self.ws, "iteration-1", "cost.json"), []
        def fx(cmd, cwd, timeout, env=None):
            calls.append(cwd); write(cost, '{"entries": []}'); return 0, RESULT, ""
        r = self.loop(self.builder(fx), max_iterations=3)
        self.assertEqual((r["status"], r["iteration"], r["problems"]),
                         ("builder-wrote-outside", 1, ["../ws/iteration-1/cost.json"]))
        self.assertEqual(len(calls), 1)

    def test_harness_write_does_not_hide_a_builder_write_elsewhere(self):
        def fx(cmd, cwd, timeout, env=None):
            reformat(os.path.join(cwd, "evals", "evals.json")); return 0, RESULT, ""
        r = self.loop(self.builder(fx))
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["evals/evals.json"]))

class TestBuilderCheck(Fixture):
    def cli(self, *extra, ws=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = se.main(["builder-check", self.skill, "--workspace", ws or self.ws] + list(extra))
        return rc, out.getvalue()

    def saved(self, text):
        p = os.path.join(self.tmp, "saved.txt"); write(p, text); return p

    def outside(self, out):
        return dict(json.loads(l[len("outside "):]) for l in out.splitlines() if l.startswith("outside "))

    def compared(self, first):
        rc, out = self.cli("--compare", self.saved(first))
        return rc, [l for l in out.splitlines() if l.startswith("changed ")]

    def test_output_lists_the_writable_set_and_only_outside_entries(self):
        write(self.path("references/x.md"), "x")
        rc, out = self.cli()
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertIn("writable SKILL.md references/** scripts/** assets/**", lines)
        names = [json.loads(l[len("outside "):])[0] for l in lines if l.startswith("outside ")]
        self.assertIn("evals/evals.json", names); self.assertIn("../ws/approvals.json", names)
        self.assertNotIn("SKILL.md", names); self.assertFalse([n for n in names if n.startswith("references")])
        self.assertRegex(lines[-1], r"^fingerprint [0-9a-f]{64} %d entries$" % len(names))

    def test_two_runs_without_a_change_are_equal(self):
        rc1, first = self.cli(); rc2, second = self.cli()
        self.assertEqual((rc1, rc2), (0, 0)); self.assertEqual(first, second)
        rc, out = self.cli("--compare", self.saved(first))
        self.assertEqual(rc, 0); self.assertIn("compare: 0 changed", out); self.assertNotIn("changed evals", out)

    def test_compare_names_each_changed_path(self):
        _, first = self.cli()
        reformat(self.path("evals/evals.json"))
        write(os.path.join(self.ws, "new.txt"), "n")
        os.remove(os.path.join(self.ws, "approvals.json"))
        rc, out = self.cli("--compare", self.saved(first))
        self.assertEqual(rc, 1)
        self.assertEqual([l for l in out.splitlines() if l.startswith("changed ")],
                         ["changed ../ws/approvals.json", "changed ../ws/new.txt", "changed evals/evals.json"])
        self.assertIn("compare: 3 changed", out)

    def test_links_are_recorded_by_target_and_never_followed(self):
        outside = os.path.join(self.tmp, "outside")
        secret = os.path.join(outside, "secret.txt"); write(secret, "secret\n")
        os.symlink(outside, os.path.join(self.ws, "dir-link"))
        os.symlink(secret, os.path.join(self.ws, "file-link"))
        _, first = self.cli()
        fp = self.outside(first)
        self.assertEqual((fp["../ws/dir-link"], fp["../ws/file-link"]), ("link:" + outside, "link:" + secret))
        self.assertEqual([n for n in fp if n.startswith("../ws/dir-link/")], [])
        os.remove(os.path.join(self.ws, "file-link")); os.symlink(os.path.join(outside, "other.txt"),
                                                                   os.path.join(self.ws, "file-link"))
        self.assertEqual(self.compared(first), (1, ["changed ../ws/file-link"]))

    def test_skill_folder_inside_the_workspace_is_left_to_the_skill_walk(self):
        write(self.path("references/x.md"), "x")
        rc, out = self.cli(ws=self.tmp)
        fp = self.outside(out)
        self.assertEqual(rc, 0)
        self.assertIn("evals/evals.json", fp); self.assertIn("../ws/approvals.json", fp)
        self.assertEqual([n for n in fp if n in (".", "SKILL.md") or n.startswith("references")], [])

    def test_a_change_past_the_first_mebibyte_is_seen(self):
        big = os.path.join(self.ws, "big.bin")
        with open(big, "wb") as f: f.write(b"a" * (3 << 20))
        _, first = self.cli()
        with open(big, "r+b") as f: f.seek((3 << 20) - 1); f.write(b"b")
        self.assertEqual(self.compared(first), (1, ["changed ../ws/big.bin"]))

    def test_deep_entries_are_walked(self):
        deep = "evals/a/b/c/d/e/deep.json"
        write(self.path(deep), "{}")
        _, first = self.cli()
        self.assertEqual(self.outside(first).get(deep, "missing")[:7], "sha256:")
        write(self.path(deep), '{"x": 1}')
        self.assertEqual(self.compared(first), (1, ["changed " + deep]))

    def test_aggregate_digest_covers_values(self):
        _, first = self.cli()
        reformat(self.path("evals/evals.json"))
        _, second = self.cli()
        self.assertEqual(sorted(self.outside(first)), sorted(self.outside(second)))
        self.assertNotEqual(first.splitlines()[-1], second.splitlines()[-1])

    def test_an_unreadable_folder_is_recorded_as_an_error(self):
        locked = os.path.join(self.ws, "locked"); write(os.path.join(locked, "f.txt"), "x")
        os.chmod(locked, 0)
        try: rc, out = self.cli()
        finally: os.chmod(locked, 0o700)
        self.assertEqual(rc, 0)
        self.assertEqual(self.outside(out)["../ws/locked"], "error:PermissionError")

    def test_it_writes_nothing_and_starts_no_process(self):
        before = tree_state(self.tmp)
        real_open, real_os_open = open, os.open
        def read_only_open(file, mode="r", *a, **k):
            if any(c in mode for c in "wax+"): raise AssertionError("builder-check opened %r for writing" % (file,))
            return real_open(file, mode, *a, **k)
        def read_only_os_open(path, flags, *a, **k):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT): raise AssertionError("os.open for writing %r" % (path,))
            return real_os_open(path, flags, *a, **k)
        forbid = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
        with mock.patch("builtins.open", read_only_open), mock.patch.object(os, "open", read_only_os_open), \
             mock.patch.object(subprocess, "Popen", forbid), mock.patch.object(os, "system", forbid), \
             mock.patch.object(os, "posix_spawn", forbid), mock.patch.object(os, "fork", forbid):
            rc, _ = self.cli()
        self.assertEqual(rc, 0)
        self.assertEqual(tree_state(self.tmp), before)

    def test_missing_folder_or_a_saved_file_without_a_fingerprint_is_an_error(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = se.main(["builder-check", self.skill, "--workspace", os.path.join(self.tmp, "missing")])
        self.assertEqual(rc, 2)
        self.assertEqual(self.cli("--compare", self.saved("nothing here\n"))[0], 2)

class TestBuilderSettings(Fixture):
    def test_builder_settings_carry_the_hook_in_edit_allowlist_mode(self):
        st, entry, hook = builder_hook(self.skill)
        self.assertIs(st["disableAllHooks"], False)
        self.assertEqual(entry["matcher"], "*")
        self.assertEqual((hook["type"], hook["command"], hook["onFailure"]), ("command", sys.executable, "block"))
        self.assertEqual(hook["args"], [GATE, "edit-allowlist", os.path.realpath(self.skill)])

    def test_executor_hook_arguments_are_unchanged(self):
        gate = {"script": "scripts/clean.py", "sha256": "ab" * 32, "python3": "/usr/bin/python3"}
        (entry,) = se.claude_settings("csv-cleaner", gate)["hooks"]["PreToolUse"]
        self.assertEqual(entry["hooks"][0]["args"], [GATE, "csv-cleaner", "scripts/clean.py", "ab" * 32, "/usr/bin/python3"])

    def test_executor_mode_decisions_are_unchanged(self):
        work = os.path.realpath(os.path.join(self.tmp, "work")); os.makedirs(work)
        argv = [sys.executable, GATE, "csv-cleaner", "scripts/clean.py", "ab" * 32, "/usr/bin/python3"]
        env = dict(os.environ, SKILL_EVAL_WORKDIR=work)
        def gate(tool, inp):
            p = subprocess.run(argv, input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": work,
                                                       "tool_input": inp}), capture_output=True, text=True, cwd=work, env=env)
            return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] if p.stdout.strip() else None
        self.assertEqual(gate("Bash", {"command": "ls"}), "deny")
        self.assertIsNone(gate("Write", {"file_path": os.path.join(work, "out.txt")}))
        self.assertEqual(gate("Write", {"file_path": os.path.join(self.tmp, "elsewhere.txt")}), "deny")
        self.assertIsNone(gate("Read", {"file_path": "/etc/hosts"}))

    def test_launch_claude_accepts_the_builder_command_and_still_refuses_bare_commands(self):
        calls = []
        fx = lambda cmd, cwd, timeout, env=None: calls.append(cwd) or (0, RESULT, "")
        cmd = se.ClaudeBuilder("x", "claude-opus-5-5", None, skill_name="branch-setup").command("p", None, self.skill)
        se.launch_claude(fx, cmd, self.skill, 5, skill_name="branch-setup", edit_allowlist=True)
        self.assertEqual(calls, [self.skill])
        for bad, allow in ((["claude", "-p", "x"], True), (["claude", "-p", "x"], False), (cmd, False)):
            with self.assertRaises(se.EvalError): se.launch_claude(fx, bad, self.skill, 5, skill_name="branch-setup",
                                                                   edit_allowlist=allow)
        with self.assertRaises(se.EvalError): se.launch_claude(fx, cmd, self.ws, 5, skill_name="branch-setup",
                                                               edit_allowlist=True)
        self.assertEqual(calls, [self.skill])

    def test_builder_launch_without_its_hook_is_refused(self):
        calls = []
        fx = lambda cmd, cwd, timeout, env=None: calls.append(cwd) or (0, RESULT, "")
        plain = ["claude", "-p", "x"] + se.claude_isolation_args("branch-setup")
        se.launch_claude(fx, plain, self.skill, 5, skill_name="branch-setup")
        with self.assertRaises(se.EvalError) as cm:
            se.launch_claude(fx, plain, self.skill, 5, skill_name="branch-setup", edit_allowlist=True)
        self.assertIn("isolation arguments", str(cm.exception))
        self.assertEqual(calls, [self.skill])

    def test_builder_launch_is_refused_when_managed_policy_may_disable_its_hook(self):
        calls = []
        fx = lambda cmd, cwd, timeout, env=None: calls.append(cwd) or (0, RESULT, "")
        it = os.path.join(self.tmp, "it"); os.makedirs(it)
        builder = se.ClaudeBuilder("Improve it.", "claude-opus-5-5", se.Budget(10.0), exec_fn=fx,
                                   skill_name="branch-setup")
        for key in ("disableAllHooks", "allowManagedHooksOnly"):
            write(self.managed, json.dumps({key: True}))
            with self.assertRaises(se.EvalError) as cm: builder(it, self.skill)
            self.assertIn("managed hook policy", str(cm.exception))
        self.assertEqual(calls, [])
        write(self.managed, json.dumps({"disableAllHooks": False, "allowManagedHooksOnly": False}))
        builder(it, self.skill)
        self.assertEqual(calls, [self.skill])

    def test_no_settings_file_is_written(self):
        import pwd
        real = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".claude")
        def stamp(d):
            return {n: os.stat(os.path.join(d, n)).st_mtime_ns for n in os.listdir(d)
                    if n.startswith("settings")} if os.path.isdir(d) else {}
        before = (stamp(real), stamp(os.path.join(self.home, ".claude")))
        builder = se.ClaudeBuilder("Improve it.", "claude-opus-5-5", se.Budget(10.0), skill_name="branch-setup",
                                   exec_fn=lambda cmd, cwd, timeout, env=None: (0, RESULT, ""))
        self.loop(builder, max_iterations=2)
        self.assertEqual((stamp(real), stamp(os.path.join(self.home, ".claude"))), before)
        for d in (self.skill, self.ws):
            found = [os.path.join(p, n) for p, _, files in os.walk(d) for n in files if "settings" in n]
            self.assertEqual(found, [], d)

class TestPromptSentence(Fixture):
    def test_builder_prompt_no_longer_carries_the_evals_sentence(self):
        seen = []
        fx = lambda cmd, cwd, timeout, env=None: seen.append(cmd[2]) or (0, RESULT, "")
        it = os.path.join(self.tmp, "it"); os.makedirs(it)
        se.ClaudeBuilder("Improve it.", "claude-opus-5-5", se.Budget(10.0), exec_fn=fx)(it, self.skill)
        self.assertEqual(len(seen), 1)
        self.assertNotIn(DELETED_SENTENCE, seen[0])
        self.assertNotIn("the eval set is approved separately", seen[0])
        self.assertIn(eva.ENGLISH_BUILDER_RULE, seen[0])

if __name__ == "__main__":
    unittest.main()
