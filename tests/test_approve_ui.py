"""S1 method 5, after unit N1: the local approval page is gone (engine/approve_ui.py is empty), because approvals are
unsigned recorded decisions. `approve-ui` and its launch script stay as names: they print one line that says so, start
no page, socket or process, record nothing and exit 2. ASCII only."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_human_gate as H
from test_skill_eval import se


class TestLaunchScript(unittest.TestCase):
    def test_the_script_changes_to_the_repository_and_passes_the_arguments(self):
        path = os.path.join(T.ROOT, "scripts", "approve-ui.sh")
        text = T.read(path)
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn('here=$(pwd)', text)
        self.assertIn('cd "$(dirname "$0")/.." || exit 1', text)
        self.assertIn('exec python3 skill_eval.py approve-ui --cwd "$here" "$@"', text)
        self.assertTrue(text.isascii())

    def test_cwd_resolves_relative_units(self):
        d = os.path.join("/tmp", "x-unit")
        self.assertEqual(se.resolve_under("/base", "rel/unit"), "/base/rel/unit")
        self.assertEqual(se.resolve_under("/base", d), d)


class TestNoPage(H.GateFixture):
    def test_the_cli_prints_one_line_and_starts_no_page(self):
        for extra in ((), ("--tailscale",)):
            with self.subTest(extra=extra):
                rc, out, err = self.run_cli("approve-ui", "--unit", self.d, "--workspace", self.ws, *extra)
                self.assertEqual(rc, 2, err)
                self.assertEqual(out, "approve-ui: %s; no page was started, use approve --batch --unit DIR\n"
                                 % H.UNSIGNED_NOTE)
                self.assertEqual(err, "")
                self.assertFalse(os.path.exists(self.approvals))
                self.assert_nothing_started()


if __name__ == "__main__":
    unittest.main()
