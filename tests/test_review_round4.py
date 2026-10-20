"""Codex P1 review round 3 (REQUEST_CHANGES), C6 fixes. One walker, eva.unit_tree, lists what a skill install copies, and
every reader is a view of it: each alias of an internal folder link is name-checked and scanned under its own path, a
wrapper file a review row reads through a link stays in the rule-review hash, and a folder link that points back at a
folder on its own path (a cycle) fails by name and is never walked, so skill_tree_hash returns. At the C5 head 11 of
the first 13 tests ran red (/tmp/c6-owner/red.txt). Two passed there by nature and must keep passing:
test_adding_a_cycle_link_changes_the_hash (the old walk re-entered the cycle and hashed the copies) and the guard
test_a_copy_of_the_registered_golden_skill_hashes_as_recorded. Round r1 adds TestSnapshotOfACycle: the snapshot command
refuses a cycle by name instead of copying until the OS stops it (red before the fix, /tmp/c6-r1/red.txt), and a clean
skill still snapshots whole, evals/ included.
Mocked like tests/test_review_round3.py: subprocess is patched to fail for the whole module, lifted only for the child
processes that run skill_tree_hash or the snapshot command under a timeout. No claude/codex/opencode/hermes call, no network, register()
and approve() are never called. ASCII only: non-English text is built from code points."""
import json, os, re, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva
import skill_eval as se
import test_eva as E
import test_skill_eval as T
import test_multi_tool_parity as P

KO = "".join(map(chr, (0xD55C, 0xAD6D, 0xC5B4)))
ALIAS = "".join(map(chr, (0xCC38, 0xACE0)))
LINE = "note: %s\n" % KO
HASH_TIMEOUT = 20
CHILD = "import sys; sys.path.insert(0, sys.argv[1]); import skill_eval; print(skill_eval.skill_tree_hash(sys.argv[2]))"
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def files_under(root):
    return sorted(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/") for dp, _, fn in os.walk(root) for f in fn)

def write_files(d, files):
    for rel, text in files.items():
        fp = os.path.join(d, *rel.split("/")); os.makedirs(os.path.dirname(fp), exist_ok=True); E.wtext(fp, text)

def link(d, rel, target):
    fp = os.path.join(d, *rel.split("/")); os.makedirs(os.path.dirname(fp), exist_ok=True); os.symlink(target, fp)

def row_findings(d, cid):
    return next(fs for c, _, fs, _ in eva.english_scan(d).groups if c == cid)

def named_paths(d):
    return {re.match(r"(.+?)(?::(?:\d+|\?)| \()", f).group(1) for cid, _, fs, _ in eva.english_scan(d).groups
            if cid != "english-evals" for f in fs}

def cycles_named(findings):
    return [f.split(" (link)")[0] for f in findings if "cycle" in f]

def gates_refuse(test, d):
    spec, problems = se.load_eval_set(d)
    for cmd in ("run", "loop"):
        with test.subTest(cmd=cmd), mock.patch.object(se, "live_estimate") as est:
            with test.assertRaises(se.EvalError) as cm:
                se.check_live_gates(mock.Mock(cmd=cmd, strict_budget=True), spec, problems, test.ws, None, [],
                                    se.DEFAULT_TOKENS, {}, 100.0)
            test.assertTrue(str(cm.exception).startswith("eval set has"), cm.exception)
            est.assert_not_called()

class TestAliasOfAnInternalFolder(P.CliBase):
    def alias_skill(self):
        d = T.make_skill(self.tmp, evals=T.evals_doc(human=False), extra_files={"references/r.md": "ok\n"})
        link(d, ALIAS, "references")
        return d

    def test_validate_names_the_alias_path(self):
        rc, out, _ = self.cli("validate", self.alias_skill())
        self.assertEqual(rc, 1, out)
        self.assertIn("english-only rule: %s/r.md (file name) [Hangul]" % ALIAS, out.splitlines())

    def test_the_live_gates_refuse_before_the_estimate(self):
        gates_refuse(self, self.alias_skill())

    def test_content_behind_an_alias_is_reported_under_each_path(self):
        d = T.make_skill(self.tmp, evals=T.evals_doc(human=False), extra_files={"references/ko.md": KO + "\n"})
        link(d, "docs", "references")
        self.assertEqual([f.split(" [")[0] for f in eva.english_violations(d)], ["docs/ko.md:1", "references/ko.md:1"])

class TestWrapperReadThroughALink(P.AgentBase, unittest.TestCase):
    OLD, NEW = "Load the skill and use the engine.", "Edit any file you need."

    def linked_agent(self):
        d, _ = self.agent()
        codex = os.path.join(d, "wrappers", "codex")
        os.rename(os.path.join(codex, "code-scout.toml"), os.path.join(d, "code-scout.toml"))
        os.rmdir(codex); os.symlink("..", codex)
        return d

    def test_the_rule_review_hash_covers_the_wrapper_bytes(self):
        d = self.linked_agent()
        items, before = eva.review_items(d), se.rule_review_hash(d)
        self.assertTrue(items)
        fp = os.path.join(d, "code-scout.toml")
        with open(fp, encoding="utf-8") as f: text = f.read()
        self.assertIn(self.OLD, text)
        E.wtext(fp, text.replace(self.OLD, self.NEW))
        self.assertEqual(eva.review_items(d), items, "the findings alone cannot see this edit")
        self.assertNotEqual(se.rule_review_hash(d), before)

    def test_english_wrappers_fails_naming_the_cycle(self):
        d = self.linked_agent()
        self.assertEqual(cycles_named(row_findings(d, "english-wrappers")), ["wrappers/codex"])
        self.assertEqual(P.row(eva.check(d), "english-wrappers")["status"], "fail")

class TestSkillCycles(P.CliBase):
    CASES = (("loop", "."), ("references/up", ".."), ("references/sub/up", ".."))

    def cycle_skill(self, rel, target):
        root = os.path.join(self.tmp, rel.replace("/", "-")); os.makedirs(root)
        d = T.make_skill(root, evals=T.evals_doc(human=False),
                         extra_files={"references/r.md": "ok\n", "references/sub/s.md": "ok\n"})
        link(d, rel, target)
        return d

    def test_validate_fails_naming_the_link(self):
        for rel, target in self.CASES:
            with self.subTest(rel):
                rc, out, _ = self.cli("validate", self.cycle_skill(rel, target))
                self.assertEqual(rc, 1, out)
                lines = [x[len("english-only rule: "):] for x in out.splitlines() if x.startswith("english-only rule: ")]
                self.assertEqual(cycles_named(lines), [rel], out)

    def test_the_live_gates_refuse_before_the_estimate(self):
        for rel, target in self.CASES:
            with self.subTest(rel):
                gates_refuse(self, self.cycle_skill(rel, target))

    def test_install_refuses_naming_the_link(self):
        for rel, target in self.CASES:
            with self.subTest(rel):
                d = self.cycle_skill(rel, target)
                with self.assertRaises(se.EvalError) as cm:
                    se.ClaudeCodeEngine().install_skill(os.path.join(os.path.dirname(d), "w"), d, "branch-setup")
                self.assertIn(rel, str(cm.exception))

class TestAgentCycle(P.AgentBase, unittest.TestCase):
    def test_a_cycle_under_scenarios_fails_english_scenarios(self):
        d, _ = self.agent()
        link(d, "scenarios/again", ".")
        self.assertEqual(cycles_named(row_findings(d, "english-scenarios")), ["scenarios/again"])
        self.assertEqual(P.row(eva.check(d), "english-scenarios")["status"], "fail")

class TestTreeHashOfACycle(E.TmpMixin, unittest.TestCase):
    def test_two_cycle_links_hash_in_bounded_time(self):
        d = T.make_skill(self.tmp, evals=T.evals_doc(human=False))
        link(d, "a", "."); link(d, "b", ".")
        for pt in _patches: pt.stop()
        try:
            res = subprocess.run([sys.executable, "-B", "-c", CHILD, T.ROOT, d], capture_output=True, text=True,
                                 timeout=HASH_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.fail("skill_tree_hash did not return within %d s" % HASH_TIMEOUT)
        finally:
            for pt in _patches: pt.start()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertRegex(res.stdout.strip(), "^[0-9a-f]{64}$")

    def test_adding_a_cycle_link_changes_the_hash(self):
        d = T.make_skill(self.tmp, evals=T.evals_doc(human=False))
        before = se.skill_tree_hash(d)
        link(d, "loop", ".")
        self.assertNotEqual(se.skill_tree_hash(d), before)

class TestSnapshotOfACycle(E.TmpMixin, unittest.TestCase):
    CASES = (("c1", (("loop", "."),)), ("c2", (("evals/up", ".."),)), ("c3", (("back-1", "."), ("back-2", "."))))

    def snapshot(self, label, links):
        d = T.make_skill(os.path.join(self.tmp, label), evals=T.evals_doc(human=False))
        for rel, target in links: link(d, rel, target)
        ws = os.path.join(self.tmp, label, "ws")
        for pt in _patches: pt.stop()
        try:
            res = subprocess.run([sys.executable, "-B", os.path.join(T.ROOT, "skill_eval.py"), "snapshot", d,
                                  "--workspace", ws], capture_output=True, text=True, timeout=HASH_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.fail("snapshot did not return within %d s" % HASH_TIMEOUT)
        finally:
            for pt in _patches: pt.start()
        return d, os.path.join(ws, "skill-snapshot"), res

    def test_snapshot_refuses_a_cycle_naming_the_links(self):
        for label, links in self.CASES:
            with self.subTest([rel for rel, _ in links]):
                _, dst, res = self.snapshot(label, links)
                self.assertEqual(res.returncode, 2, res.stderr[-2000:])
                for rel, _ in links: self.assertIn(rel, res.stderr)
                self.assertFalse(os.path.lexists(dst))

    def test_a_clean_skill_snapshots_whole(self):
        d, dst, res = self.snapshot("clean", ())
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("evals/evals.json", files_under(dst))
        self.assertEqual(files_under(dst), files_under(d))

SKILL_KO = E.GOOD_FM.format(name="word-count", extra="", body=KO + "\n")
ROUND3_FILES = {
    "SKILL.md": SKILL_KO, "agents/openai.yaml": "interface:\n  display_name: %s\n" % KO, "agents/extra.yaml": LINE,
    "templates/x.md": LINE, "NOTES.md": LINE, "references/r.md": LINE, "references/deep/n.txt": LINE,
    "scripts/s.py": "# %s\n" % KO, "assets/a.json": json.dumps({"k": KO}),
    ".git/x": LINE, "references/.git/x": LINE, "FEEDBACK.md": LINE, "FEEDBACK.json": json.dumps({"k": KO}),
    "evals/evals.json": json.dumps({"skill_name": "word-count", "evals": [{"id": 1, "prompt": KO, "expected_output": "o"}]}),
    "scripts/__pycache__/m.txt": LINE, "notes-workspace/w.md": LINE, "references/evals/e.md": LINE}

class TestInstallEqualsWalkEqualsScan(E.TmpMixin, unittest.TestCase):
    CASES = (("alias-name", {"SKILL.md": SKILL_KO, "references/r.md": LINE}, {ALIAS: "references"},
              ["SKILL.md", "references/r.md", ALIAS + "/r.md"]),
             ("alias-content", {"SKILL.md": SKILL_KO, "references/ko.md": LINE}, {"docs": "references"},
              ["SKILL.md", "docs/ko.md", "references/ko.md"]),
             ("round-3", ROUND3_FILES, {},
              ["NOTES.md", "SKILL.md", "agents/extra.yaml", "agents/openai.yaml", "assets/a.json", "references/deep/n.txt",
               "references/r.md", "scripts/s.py", "templates/x.md"]))

    def test_install_walk_and_scan_name_the_same_paths(self):
        for label, files, links, want in self.CASES:
            with self.subTest(label):
                d = os.path.join(self.tmp, label, "word-count")
                write_files(d, files)
                for rel, target in links.items(): link(d, rel, target)
                installed = files_under(se.ClaudeCodeEngine().install_skill(os.path.join(self.tmp, label, "w"), d,
                                                                            "word-count"))
                self.assertEqual(installed, want)
                self.assertEqual(sorted(eva.unit_tree(d).files), installed)
                self.assertEqual(named_paths(d), set(installed))

class TestGoldenHashUnchanged(E.TmpMixin, unittest.TestCase):
    def test_a_copy_of_the_registered_golden_skill_hashes_as_recorded(self):
        src = os.path.join(T.ROOT, "golden", "skills")
        d = os.path.join(self.tmp, "csv-cleaner")
        shutil.copytree(os.path.join(src, "csv-cleaner"), d)
        rec = T.rjson(os.path.join(src, "csv-cleaner-workspace", "registration.json"))
        self.assertEqual(se.skill_tree_hash(d), rec["skill_sha256"])

if __name__ == "__main__":
    unittest.main()
