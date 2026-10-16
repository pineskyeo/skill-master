"""claude_binary_path resolves past the ~/bin/claude account shim; label_blind.py hides the grader and stores the
human's own pass/fail. No model calls."""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import skill_eval as se
import label_blind as lb


def w(path, text, mode=0o700):
    with open(path, "w") as f: f.write(text)
    os.chmod(path, mode); return path


class BinaryPath(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.real = w(os.path.join(self.d, "real-claude"), "binary cc-plugin-foo")
        self.shim = w(os.path.join(self.d, "claude"),
                      '#!/usr/bin/env python3\nREAL = os.environ.get("CLAUDE_AS_REAL_CLAUDE") or "%s"\n' % self.real)

    def test_shim_resolved_to_default_target(self):
        self.assertEqual(se.claude_binary_path(lambda n: self.shim, {}), os.path.realpath(self.real))

    def test_env_overrides(self):
        other = w(os.path.join(self.d, "other"), "x")
        self.assertEqual(se.claude_binary_path(lambda n: self.shim, {"CLAUDE_AS_REAL_CLAUDE": other}), os.path.realpath(other))
        self.assertEqual(se.claude_binary_path(lambda n: self.shim, {"CLAUDE_REAL_BIN": other}), os.path.realpath(other))

    def test_non_shim_unchanged_and_missing_target_none(self):
        self.assertEqual(se.claude_binary_path(lambda n: self.real, {}), os.path.realpath(self.real))
        os.remove(self.real)
        self.assertIsNone(se.claude_binary_path(lambda n: self.shim, {}))
        self.assertIsNone(se.claude_binary_path(lambda n: None, {}))

    def test_self_target_refused(self):
        w(self.shim, '#!/bin/sh\nCLAUDE_AS_REAL_CLAUDE") or "%s"\n' % self.shim)
        self.assertIsNone(se.claude_binary_path(lambda n: self.shim, {}))

    def test_scan_uses_real_binary(self):
        info = se.claude_builtin_plugins(path=se.claude_binary_path(lambda n: self.shim, {}), refresh=True)
        try: self.assertIn("cc-plugin-foo", info["scanned"])
        finally: se._BUILTIN_SCAN.clear()


ITEMS = [{"id": "a/1", "case": 1, "arm": "on", "run": "run-1", "pressure": False, "prompt": "p", "output": "o",
          "item": "rule", "tool_calls": [], "grader": "codex", "grader_passed": True, "grader_evidence": "EVID-SECRET"},
         {"id": "a/2", "case": 1, "arm": "off", "run": "run-1", "pressure": False, "prompt": "p", "output": "o2",
          "item": "rule", "tool_calls": [], "grader": "codex", "grader_passed": False, "grader_evidence": "EVID2"}]


class BlindLabel(unittest.TestCase):
    def build(self):
        d = tempfile.mkdtemp(); src = os.path.join(d, "items.json"); out = os.path.join(d, "page.html")
        json.dump({"iteration": 3, "items": ITEMS}, open(src, "w")); lb.build(src, out); return out

    def test_page_has_no_grader_data_or_fill_all(self):
        page = open(self.build()).read()
        for bad in ("grader_passed", "EVID-SECRET", "codex", "fillAll", "allok", "agree"):
            self.assertNotIn(bad, page)
        _, items = lb.load_items(self.build())
        self.assertFalse(any(k in it for it in items for k in lb.GRADER_KEYS))

    def test_build_never_overwrites(self):
        out = self.build()
        with self.assertRaises(FileExistsError): lb.build(out, out)

    def test_all_items_required(self):
        items = lb.blind_items(ITEMS)
        g, err = lb.validate_submission({"answers": {"a/1": {"human_passed": True}}}, items)
        self.assertIsNone(g); self.assertIn("1 item", err)
        g, err = lb.validate_submission({"answers": {"a/1": {"human_passed": True}, "a/2": {"human_passed": "yes"}}}, items)
        self.assertIsNone(g)

    def test_label_is_human_choice_not_grader(self):
        items = lb.blind_items(ITEMS)
        g, err = lb.validate_submission({"answers": {"a/1": {"human_passed": False}, "a/2": {"human_passed": True}}}, items)
        self.assertIsNone(err)
        self.assertEqual([e["label"] for e in g["examples"]], [False, True])
        self.assertEqual([e["human_passed"] for e in g["examples"]], [False, True])
        self.assertNotIn("grader_passed", json.dumps(g))

    def test_unknown_ids_refused_and_golden_loads(self):
        items = lb.blind_items(ITEMS)
        _, err = lb.validate_submission({"answers": {"a/1": {"human_passed": 1 == 1}, "a/2": {"human_passed": True},
                                                     "zz": {"human_passed": True}}}, items)
        self.assertIn("unknown", err)
        g, _ = lb.validate_submission({"answers": {"a/1": {"human_passed": True}, "a/2": {"human_passed": False}}}, items)
        p = os.path.join(tempfile.mkdtemp(), "g.json"); json.dump(g, open(p, "w"))
        self.assertEqual(len(se.load_golden(p)), 2)


if __name__ == "__main__":
    unittest.main()
