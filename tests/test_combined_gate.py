"""S1 method 4: the requirements_eval_set gate hashes the labeled requirements and eval_set gate hashes, so an edit to
either stales it; the live approvals gate opens on one current combined record or on current requirements and eval_set
records, and never on one separate record alone.

Signing uses only the throwaway test key of tests/test_skill_eval.py; subprocess is patched for the whole module and
only that key runner starts /usr/bin/ssh-keygen. ASCII only."""
import hashlib, os, subprocess, sys, types, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_ruleset_binding as RB
from test_skill_eval import se, write, wjson, rjson

COMBINED = "requirements_eval_set"
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


class Combined(RB.Bound):
    def live_failures(self):
        out = []
        se._live_approvals(types.SimpleNamespace(spec=self.spec, ws=self.ws, verdict_of=se.approval_verdict,
                                                 fail=out.append))
        return out

    def edit_requirements(self):
        write(os.path.join(self.ws, "requirements.md"), "goal, edited")

    def edit_eval_set(self):
        p = os.path.join(self.d, "evals", "evals.json")
        doc = rjson(p); doc["evals"][0]["prompt"] += " (edited)"; wjson(p, doc)

    def test_the_hash_is_a_labeled_sha256_of_the_two_gate_hashes(self):
        want = hashlib.sha256(("requirements_eval_set v1\nrequirements %s\neval_set %s\n" % (
            se.gate_hash("requirements", self.spec, self.ws), se.gate_hash("eval_set", self.spec, self.ws))
        ).encode("ascii")).hexdigest()
        self.assertEqual(se.gate_hash(COMBINED, self.spec, self.ws), want)
        os.remove(os.path.join(self.ws, "requirements.md"))
        self.assertIsNone(se.gate_hash(COMBINED, self.spec, self.ws))

    def test_a_combined_approval_opens_the_live_gate(self):
        self.assertEqual(len(self.live_failures()), 2)
        se.approve(COMBINED, self.spec, self.ws)
        self.assertEqual(se.approval_verdict(COMBINED, self.spec, self.ws).status, "approved")
        self.assertEqual(self.live_failures(), [])
        self.assertNotIn("requirements", rjson(self.approvals)); self.assertNotIn("eval_set", rjson(self.approvals))

    def test_a_requirements_edit_stales_it(self):
        se.approve(COMBINED, self.spec, self.ws)
        self.edit_requirements()
        self.assertEqual(se.approval_verdict(COMBINED, self.spec, self.ws).status, "stale")
        self.assertTrue(self.live_failures())

    def test_an_eval_set_edit_stales_it(self):
        se.approve(COMBINED, self.spec, self.ws)
        self.edit_eval_set()
        self.spec = se.load_eval_set(self.d)[0]
        self.assertEqual(se.approval_verdict(COMBINED, self.spec, self.ws).status, "stale")
        self.assertTrue(self.live_failures())

    def test_two_separate_current_approvals_still_open_it(self):
        se.approve("requirements", self.spec, self.ws)
        se.approve("eval_set", self.spec, self.ws)
        self.assertEqual(self.live_failures(), [])

    def test_one_separate_approval_alone_does_not(self):
        for gate, other in (("requirements", "eval_set"), ("eval_set", "requirements")):
            with self.subTest(gate):
                wjson(self.approvals, {})
                se.approve(gate, self.spec, self.ws)
                fails = self.live_failures()
                self.assertEqual(len(fails), 1, fails)
                self.assertTrue(fails[0].startswith("gate %s not approved" % other), fails)

    def test_a_stale_combined_record_does_not_count_with_one_current_separate_record(self):
        se.approve(COMBINED, self.spec, self.ws)
        se.approve("eval_set", self.spec, self.ws)
        self.edit_requirements()
        self.assertTrue(self.live_failures())

    def test_the_signer_sees_the_requirements_and_every_eval_set_file(self):
        shown = se.approve_show(COMBINED, self.spec, self.ws, "ef" * 32)
        self.assertIn("requirements: %s sha256 %s" % (os.path.join(self.ws, "requirements.md"),
                                                     se.gate_hash("requirements", self.spec, self.ws)), shown)
        self.assertTrue(any(x.startswith("eval set file evals/evals.json: ") for x in shown), shown)
        self.assertIn("eval_set sha256 %s" % se.gate_hash("eval_set", self.spec, self.ws), shown)
        self.assertFalse(any("iteration" in x for x in shown), shown)
        self.assertEqual(shown[-1], "ruleset_sha256 " + "ef" * 32)

    def test_plan_prints_the_combined_line_and_keeps_the_separate_line(self):
        lines = self.plan()
        self.assertEqual(self.line(lines, "approval gates: "),
                         "approval gates: requirements=NOT APPROVED, eval_set=NOT APPROVED")
        self.assertEqual(self.line(lines, "combined approval gate: "),
                         "combined approval gate: requirements_eval_set=NOT APPROVED (the live gate needs it, or both "
                         "requirements and eval_set)")
        se.approve(COMBINED, self.spec, self.ws)
        self.assertEqual(self.line(self.plan(), "combined approval gate: "),
                         "combined approval gate: requirements_eval_set=approved by test-approver (the live gate needs "
                         "it, or both requirements and eval_set)")
        self.assertIn((7, "approvals", "pass"), self.states(self.plan()))

    def test_the_gates_declaration_names_the_combined_gate(self):
        doc = rjson(os.path.join(T.ROOT, "policy", "gates.json"))
        label = next(e["label"] for e in doc["live"] if e["id"] == "approvals")
        self.assertIn("requirements_eval_set", label)


if __name__ == "__main__":
    unittest.main()
