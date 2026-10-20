"""C8: the snapshot command refuses a skill folder that holds a link resolving outside it (a nested folder link, a file
link, a top-level folder that is itself a link, a link to the folder that holds the skill) or a folder link cycle, before
it copies anything. One message names every offending link with the eva.LINK_OUT and eva.LINK_CYCLE reasons, and nothing
is created at the destination. A link that stays inside the folder still snapshots. At 392e01d shutil.copytree followed
the outside links and copied their content, the link to the parent folder copied until the timeout, and a cycle next to
outside links was the only one named (red run: /tmp/c8-owner/red.txt).
Mocked like tests/test_review_round4.py: subprocess is patched to fail for the whole module, lifted only for the child
process that runs the snapshot command under a timeout. No claude/codex/opencode/hermes call, no network, register()
and approve() are never called. ASCII only."""
import os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva
import test_eva as E
import test_skill_eval as T

SNAPSHOT_TIMEOUT = 20
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def files_under(root):
    return sorted(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/") for dp, _, fn in os.walk(root) for f in fn)

class TestSnapshotLinks(E.TmpMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.out_dir = os.path.join(self.tmp, "outside")
        os.makedirs(self.out_dir)
        E.wtext(os.path.join(self.out_dir, "secret.txt"), "outside content\n")
        self.out_file = os.path.join(self.tmp, "outside-file.md")
        E.wtext(self.out_file, "outside file content\n")

    def snapshot(self, label, links):
        d = T.make_skill(os.path.join(self.tmp, label), evals=T.evals_doc(human=False),
                         extra_files={"references/r.md": "ref\n"})
        for rel, target in links:
            fp = os.path.join(d, *rel.split("/")); os.makedirs(os.path.dirname(fp), exist_ok=True); os.symlink(target, fp)
        ws = os.path.join(self.tmp, label, "ws")
        for pt in _patches: pt.stop()
        try:
            res = subprocess.run([sys.executable, "-B", os.path.join(T.ROOT, "skill_eval.py"), "snapshot", d,
                                  "--workspace", ws], capture_output=True, text=True, timeout=SNAPSHOT_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.fail("snapshot did not return within %d s" % SNAPSHOT_TIMEOUT)
        finally:
            for pt in _patches: pt.start()
        return d, os.path.join(ws, "skill-snapshot"), res

    def assert_refused(self, res, dst, reasons):
        self.assertEqual(res.returncode, 2, res.stderr[-2000:])
        lines = [x for x in res.stderr.splitlines() if reasons[0] in x]
        self.assertEqual(len(lines), 1, "the refusal is one message: %r" % res.stderr[-2000:])
        for reason in reasons: self.assertIn(reason, lines[0], "every offending link is named in the one message")
        self.assertFalse(os.path.lexists(dst), "nothing is created at the destination")

    def test_a_nested_folder_link_out_is_refused(self):
        _, dst, res = self.snapshot("nested-dir", (("references/ext", self.out_dir),))
        self.assert_refused(res, dst, [eva.LINK_OUT % "references/ext"])

    def test_a_file_link_out_is_refused(self):
        _, dst, res = self.snapshot("file", (("references/secret.md", self.out_file),))
        self.assert_refused(res, dst, [eva.LINK_OUT % "references/secret.md"])

    def test_a_top_level_folder_that_links_out_is_refused(self):
        _, dst, res = self.snapshot("top-dir", (("scripts", self.out_dir),))
        self.assert_refused(res, dst, [eva.LINK_OUT % "scripts"])

    def test_a_link_to_the_folder_holding_the_skill_is_refused_in_bounded_time(self):
        _, dst, res = self.snapshot("parent", (("up", ".."),))
        self.assert_refused(res, dst, [eva.LINK_OUT % "up"])

    def test_a_cycle_is_still_refused(self):
        _, dst, res = self.snapshot("cycle", (("loop", "."),))
        self.assert_refused(res, dst, [eva.LINK_CYCLE % "loop"])

    def test_every_offending_link_is_named_in_one_message(self):
        _, dst, res = self.snapshot("several", (("references/ext", self.out_dir), ("assets", self.out_dir), ("loop", ".")))
        self.assert_refused(res, dst, [eva.LINK_OUT % "assets", eva.LINK_OUT % "references/ext", eva.LINK_CYCLE % "loop"])

    def test_a_link_inside_the_folder_still_snapshots(self):
        d, dst, res = self.snapshot("inside", (("docs", "references"),))
        self.assertEqual(res.returncode, 0, res.stderr)
        copied = files_under(dst)
        self.assertIn("docs/r.md", copied)
        self.assertIn("references/r.md", copied)
        self.assertIn("evals/evals.json", copied)
        self.assertEqual(copied, sorted(files_under(d) + ["docs/r.md"]), "the inside link is copied as a folder of its own")

if __name__ == "__main__":
    unittest.main()
