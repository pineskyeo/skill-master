"""Round-13 tests: the skill scanner keeps the shallowest visible depth per real folder, so visiting a deeper alias
first (root/a/b/link -> root/z with root/z/s/SKILL.md, depth limit 3) does not hide the shallower root/z/s. Checked
for skills_under itself and preflight. Everything is mocked."""
import os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se
from test_round3_isolation import Base
from test_round11 import skill

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

# both name orders, so the deeper alias is listed first in one of them whatever order the file system returns
LAYOUTS = (("a", "z"), ("z", "a"))

def alias_tree(root, link_top, real_top, name):
    """root/<link_top>/b/link -> root/<real_top>, and root/<real_top>/<name>/SKILL.md (visible depth 2)."""
    skill(os.path.join(root, real_top), name)
    os.makedirs(os.path.join(root, link_top, "b"))
    os.symlink(os.path.join(root, real_top), os.path.join(root, link_top, "b", "link"))

class TestShallowestDepthWins(Base):
    def test_skills_under_finds_shallow_path_after_deep_alias(self):
        for link_top, real_top in LAYOUTS:
            root = os.path.join(self.tmp, "scan-" + link_top)
            alias_tree(root, link_top, real_top, "s")
            hits = se.skills_under(root, 3)
            self.assertEqual(hits, [("s", os.path.join(root, real_top, "s"))], (link_top, real_top))

    def test_skills_under_deep_alias_alone_stays_out_of_reach(self):
        # only the alias path exists under root: root/a/b/link/s is depth 4, beyond the limit
        target = os.path.join(self.tmp, "outside"); skill(target, "s")
        root = os.path.join(self.tmp, "scan"); os.makedirs(os.path.join(root, "a", "b"))
        os.symlink(target, os.path.join(root, "a", "b", "link"))
        self.assertEqual(se.skills_under(root, 3), [])
        self.assertEqual([n for n, _ in se.skills_under(root, 4)], ["s"])

    def test_skills_under_listing_order_does_not_matter(self):
        root = os.path.join(self.tmp, "scan"); alias_tree(root, "a", "z", "s")
        real_listdir = os.listdir
        for order in (sorted, lambda xs: sorted(xs, reverse=True)):
            with mock.patch.object(se.os, "listdir", lambda p, o=order: o(real_listdir(p))):
                self.assertEqual([n for n, _ in se.skills_under(root, 3)], ["s"])

    def test_skills_under_cycle_terminates(self):
        root = os.path.join(self.tmp, "scan"); os.makedirs(os.path.join(root, "a"))
        os.symlink(root, os.path.join(root, "a", "loop")); skill(os.path.join(root, "a"), "s")
        self.assertEqual([n for n, _ in se.skills_under(root, 6)], ["s"])

    def test_preflight_sees_shallow_path_after_deep_alias(self):
        for link_top, real_top in LAYOUTS:
            ag = os.path.join(self.tmp, ".agents", "skills")
            alias_tree(ag, link_top, real_top, "hidden-" + real_top)
            probs = se.preflight(self.spec()[0], ["codex"], self.runs)
            self.assertTrue(any("hidden-" + real_top in p for p in probs), (link_top, real_top, probs))
            T.shutil.rmtree(os.path.join(self.tmp, ".agents"))

if __name__ == "__main__":
    unittest.main()
