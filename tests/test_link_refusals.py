"""C9: the snapshot command refuses a skill folder that holds a dangling link (a file link or a folder link whose
target does not exist) before it copies anything. The one refusal names every dangling link next to every outside link
and every cycle, and nothing is created at the destination, so a rerun refuses for the same reason. A link that stays
inside the folder still snapshots. At 878eaa6 a dangling link made shutil.copytree raise an uncaught shutil.Error
(exit 1) after it had created the destination, a dangling link next to outside links and a cycle was not named, and
install_skill named only the first five links (red runs: /tmp/c9-owner/red1.txt with only this file,
/tmp/c9-owner/red2.txt with eva.LINK_DANGLING added).
C10: the reasons are pinned as literal text below (OUT, CYCLE, DANGLING, LOOP), not read from eva, so a change of the
wording fails here. A link that resolves in a loop of links (links that point at each other) is named with LOOP, not
as dangling, by snapshot and by install_skill. install_skill refuses once, naming every outside link, dangling link,
link that resolves in a loop and cycle with its reason, and creates no install folder. At the C9 state install_skill
raised on the outside and dangling links without naming the cycles, without these reasons, and both commands named a
resolution loop as dangling (red run: /tmp/c10-owner/red.txt with only this file).
Mocked like tests/test_snapshot_links.py: subprocess is patched to fail for the whole module, lifted only for the child
process that runs the snapshot command under a timeout. No claude/codex/opencode/hermes call, no network, register()
and approve() are never called. ASCII only."""
import os, re, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_eva as E
import test_skill_eval as T
import skill_eval as se

OUT = "%s (link) resolves outside the unit folder, so its content cannot be verified English-only"
CYCLE = "%s (link) points back at a folder on its own path (a cycle), so a copy of the unit would never end"
DANGLING = "%s (link) points at a path that does not exist (dangling), so a copy of the unit would fail"
LOOP = "%s (link) resolves in a loop of links that never reaches a target (a resolution loop), so a copy of the unit would fail"

SNAPSHOT_TIMEOUT = 20
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def names(text, s):
    return re.search(r"(?<![\w/])%s(?![\w/])" % re.escape(s), text) is not None

def read(path):
    with open(path, encoding="utf-8") as f: return f.read()

class LinkSkill(E.TmpMixin):
    def setUp(self):
        super().setUp()
        self.out_dir = os.path.join(self.tmp, "outside")
        os.makedirs(self.out_dir)
        E.wtext(os.path.join(self.out_dir, "secret.txt"), "outside content\n")

    def skill(self, label, links):
        d = T.make_skill(os.path.join(self.tmp, label), evals=T.evals_doc(human=False),
                         extra_files={"references/r.md": "ref\n"})
        for rel, target in links:
            fp = os.path.join(d, *rel.split("/")); os.makedirs(os.path.dirname(fp), exist_ok=True); os.symlink(target, fp)
        return d

class TestSnapshotDanglingLinks(LinkSkill, unittest.TestCase):
    def run_snapshot(self, d, ws):
        for pt in _patches: pt.stop()
        try:
            return subprocess.run([sys.executable, "-B", os.path.join(T.ROOT, "skill_eval.py"), "snapshot", d,
                                   "--workspace", ws], capture_output=True, text=True, timeout=SNAPSHOT_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.fail("snapshot did not return within %d s" % SNAPSHOT_TIMEOUT)
        finally:
            for pt in _patches: pt.start()

    def snapshot(self, label, links):
        d = self.skill(label, links)
        ws = os.path.join(self.tmp, label, "ws")
        return d, ws, os.path.join(ws, "skill-snapshot"), self.run_snapshot(d, ws)

    def assert_refused(self, res, dst, reasons):
        self.assertEqual(res.returncode, 2, res.stderr[-2000:])
        lines = [x for x in res.stderr.splitlines() if names(x, reasons[0])]
        self.assertEqual(len(lines), 1, "the refusal is one message: %r" % res.stderr[-2000:])
        for reason in reasons: self.assertTrue(names(lines[0], reason), "%r is named in %r" % (reason, lines[0]))
        self.assertFalse(os.path.lexists(dst), "nothing is created at the destination")

    def test_a_dangling_file_link_is_refused(self):
        _, _, dst, res = self.snapshot("dangling-file", (("references/gone.md", "missing.md"),))
        self.assert_refused(res, dst, [DANGLING % "references/gone.md"])

    def test_a_dangling_folder_link_is_refused(self):
        _, _, dst, res = self.snapshot("dangling-folder", (("scripts", "no-such-folder"),))
        self.assert_refused(res, dst, [DANGLING % "scripts"])

    def test_dangling_outside_and_cycle_links_are_named_in_one_message(self):
        _, _, dst, res = self.snapshot("mixed", (("references/gone.md", "missing.md"), ("references/ext", self.out_dir),
                                                 ("loop", ".")))
        self.assert_refused(res, dst, [DANGLING % "references/gone.md", OUT % "references/ext", CYCLE % "loop"])

    def test_a_resolution_loop_is_named_apart_from_a_missing_target(self):
        loops = ("references/a.md", "references/b.md", "s1", "s2")
        _, _, dst, res = self.snapshot("loops", (("references/a.md", "b.md"), ("references/b.md", "a.md"),
                                                 ("s1", "s2"), ("s2", "s1"), ("references/gone.md", "missing.md")))
        self.assert_refused(res, dst, [LOOP % rel for rel in loops] + [DANGLING % "references/gone.md"])
        for rel in loops: self.assertFalse(names(res.stderr, DANGLING % rel), "%s is not named as dangling" % rel)

    def test_a_refusal_creates_nothing_so_a_rerun_refuses_for_the_same_reason(self):
        d, ws, dst, res = self.snapshot("rerun", (("references/gone.md", "missing.md"),))
        self.assertFalse(os.path.lexists(dst), "nothing is created at the destination")
        again = self.run_snapshot(d, ws)
        self.assertNotIn("skill-snapshot exists", again.stderr)
        self.assert_refused(again, dst, [DANGLING % "references/gone.md"])

    def test_links_inside_the_folder_still_snapshot(self):
        _, _, dst, res = self.snapshot("inside", (("docs", "references"), ("references/alias.md", "r.md")))
        self.assertEqual(res.returncode, 0, res.stderr)
        for rel in ("docs/r.md", "references/r.md", "references/alias.md"):
            self.assertEqual(read(os.path.join(dst, *rel.split("/"))), "ref\n", rel)

class TestInstallNamesEveryLink(LinkSkill, unittest.TestCase):
    def install_error(self, label, links):
        d = self.skill(label, links)
        engine = se.ClaudeCodeEngine()
        with self.assertRaises(se.EvalError) as cm:
            engine.install_skill(os.path.join(self.tmp, label, "w"), d, "branch-setup")
        dst = os.path.join(self.tmp, label, "w", *engine.skills_rel.split("/"), "branch-setup")
        self.assertFalse(os.path.lexists(dst), "nothing is installed")
        return str(cm.exception)

    def assert_named(self, err, expected):
        self.assertEqual([e for e in expected if not names(err, e)], [], err)

    def test_every_outside_and_dangling_link_is_named(self):
        outs = ["references/o%d" % i for i in range(4)]
        dangling = ["references/d%d.md" % i for i in range(3)]
        err = self.install_error("install-links", [(rel, self.out_dir) for rel in outs]
                                 + [(rel, "missing%d.md" % i) for i, rel in enumerate(dangling)])
        self.assert_named(err, [OUT % rel for rel in outs] + [DANGLING % rel for rel in dangling])

    def test_every_cycle_is_named(self):
        cycles = ["c%d" % i for i in range(7)]
        err = self.install_error("install-cycles", [(rel, ".") for rel in cycles])
        self.assert_named(err, [CYCLE % rel for rel in cycles])

    def test_one_refusal_names_every_outside_dangling_loop_and_cycle_link(self):
        err = self.install_error("install-mixed", (("references/ext", self.out_dir), ("references/gone.md", "missing.md"),
                                                   ("references/a.md", "b.md"), ("references/b.md", "a.md"), ("loop", ".")))
        self.assert_named(err, [OUT % "references/ext", DANGLING % "references/gone.md", LOOP % "references/a.md",
                                LOOP % "references/b.md", CYCLE % "loop"])
        for rel in ("references/a.md", "references/b.md"):
            self.assertFalse(names(err, DANGLING % rel), "%s is not named as dangling" % rel)

if __name__ == "__main__":
    unittest.main()
