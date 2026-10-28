"""Unit K2: the skill eval check rule_check {path, rules} runs the rule runner over one output file of a case with one
rule file of the engine's rules/ folder; the case passes the check only when every row of that file reads pass or n/a.
Nothing here starts a process: subprocess is patched to fail for the whole module. ASCII only."""
import contextlib, io, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, eva, evals_doc, make_skill, read
from engine import rule_runner as RR

AGENT_MD = read(os.path.join(T.ROOT, "tests", "fixtures", "agents", "code-reviewer-v2", "AGENT.md"))
AGENT_RULES = "rules/agent-structure.rules.json"
SCENARIO_RULES = ("scenarios-present", "scenarios-valid", "scenarios-expected", "tools-subset", "multi-turn-scenario")
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden)
        pt.start()
        _patches.append(pt)


def tearDownModule():
    for pt in _patches:
        pt.stop()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def rule_check(path, rules):
    return {"type": "rule_check", "path": path, "rules": rules}


def with_checks(*checks):
    doc = evals_doc(human=False)
    doc["evals"][0]["assertions"] += [{"text": "output passes rule file %d" % i, "layer": "static", "check": c}
                                      for i, c in enumerate(checks)]
    return doc


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = os.path.join(self.tmp, "run-1", "workdir")
        os.makedirs(self.out)

    def problems(self, *checks):
        d = make_skill(os.path.join(self.tmp, "s%d" % len(os.listdir(self.tmp))), evals=with_checks(*checks))
        return se.load_eval_set(d)[1]

    def grade(self, chk):
        return se.static_check(chk, {"final": ""}, self.out, None)


class TestValidation(Base):
    def test_a_rule_check_against_a_rule_file_of_the_engine_validates(self):
        self.assertEqual(self.problems(rule_check("agents/x/AGENT.md", "rules/agent-structure.rules.json"),
                                       rule_check("SKILL.md", "rules/skill-frontmatter.rules.json")), [])

    def test_an_unknown_or_outside_rule_file_is_refused(self):
        for rules in ("rules/missing.rules.json", "rules/../schemas/evals.schema.json", "../rules/naming.rules.json",
                      "/abs/rules/naming.rules.json", "schemas/evals.schema.json", "rules/data/capabilities.json",
                      "rules\\naming.rules.json", "naming.rules.json", ""):
            probs = self.problems(rule_check("AGENT.md", rules))
            self.assertTrue(any("check.rules" in p for p in probs), (rules, probs))

    def test_an_output_path_outside_the_output_folder_is_refused(self):
        for path in ("../AGENT.md", "/etc/AGENT.md", "a/../../AGENT.md", "a\\AGENT.md", ""):
            probs = self.problems(rule_check(path, "rules/agent-structure.rules.json"))
            self.assertTrue(any("check.path" in p for p in probs), (path, probs))

    def test_a_rule_file_for_both_kinds_needs_a_main_file_name(self):
        self.assertEqual(self.problems(rule_check("out/SKILL.md", "rules/naming.rules.json")), [])
        probs = self.problems(rule_check("out/draft.md", "rules/naming.rules.json"))
        self.assertTrue(any("SKILL.md or AGENT.md" in p for p in probs), probs)

    def test_validate_accepts_the_check_and_refuses_a_missing_rule_file(self):
        good = make_skill(os.path.join(self.tmp, "good"),
                          evals=with_checks(rule_check("AGENT.md", "rules/agent-structure.rules.json")))
        bad = make_skill(os.path.join(self.tmp, "bad"),
                         evals=with_checks(rule_check("AGENT.md", "rules/nope.rules.json")))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc_good = se.main(["validate", good])
            rc_bad = se.main(["validate", bad])
        self.assertEqual((rc_good, rc_bad), (0, 1), out.getvalue())
        self.assertIn("rules/nope.rules.json", out.getvalue())


class TestGrading(Base):
    def test_a_passing_rule_check(self):
        write(os.path.join(self.out, "branch-setup", "SKILL.md"), T.SKILL_MD)
        ok, ev = self.grade(rule_check("branch-setup/SKILL.md", "rules/skill-frontmatter.rules.json"))
        self.assertTrue(ok, ev)
        self.assertIn("rules/skill-frontmatter.rules.json", ev)

    def test_a_well_formed_agent_md_passes_and_the_evidence_names_the_rules_that_read_other_files(self):
        write(os.path.join(self.out, "AGENT.md"), AGENT_MD)
        ok, ev = self.grade(rule_check("AGENT.md", AGENT_RULES))
        self.assertTrue(ok, ev)
        self.assertIn("8 row(s) pass or n/a", ev)
        self.assertIn("excluded", ev)
        for rid in SCENARIO_RULES:
            self.assertIn(rid, ev)

    def test_a_malformed_agent_md_fails_on_its_row_and_never_on_an_excluded_rule(self):
        no_goal = AGENT_MD.replace(AGENT_MD[AGENT_MD.index("goal:"):AGENT_MD.index("capabilities:")], "")
        for rid, text in (("goal-present", no_goal), ("frontmatter", AGENT_MD.replace("---\n", "--\n", 1))):
            write(os.path.join(self.out, "AGENT.md"), text)
            ok, ev = self.grade(rule_check("AGENT.md", AGENT_RULES))
            self.assertFalse(ok, (rid, ev))
            self.assertIn("%s fail" % rid, ev)
            failing = ev.split("; excluded", 1)[0]
            for sid in SCENARIO_RULES:
                self.assertNotIn(sid, failing, (rid, ev))
            self.assertIn("excluded", ev)

    def test_the_pairing_that_validates_grades_a_well_formed_output_to_pass(self):
        chk = rule_check("agents/code-reviewer-v2/AGENT.md", AGENT_RULES)
        self.assertEqual(self.problems(chk), [])
        write(os.path.join(self.out, "agents", "code-reviewer-v2", "AGENT.md"), AGENT_MD)
        ok, ev = self.grade(chk)
        self.assertTrue(ok, ev)

    def test_a_needs_review_or_missing_row_fails_the_check(self):
        write(os.path.join(self.out, "branch-setup", "SKILL.md"), T.SKILL_MD)
        chk = rule_check("branch-setup/SKILL.md", "rules/skill-frontmatter.rules.json")
        real = RR.apply_families

        def review(report, files, bundle=None):
            real(report, files, bundle)
            for c in report.checks:
                c.update(status="needs-review", passed=None)
        with mock.patch.object(RR, "apply_families", review):
            ok, ev = self.grade(chk)
        self.assertFalse(ok)
        self.assertIn("needs-review", ev)
        with mock.patch.object(RR, "apply_families", lambda report, files, bundle=None: None):
            ok, ev = self.grade(chk)
        self.assertFalse(ok)
        self.assertIn("missing", ev)

    def test_an_unknown_rule_file_never_passes_at_grading(self):
        write(os.path.join(self.out, "AGENT.md"), AGENT_MD)
        for rules in ("rules/nope.rules.json", "../rules/naming.rules.json", "schemas/evals.schema.json"):
            ok, ev = self.grade(rule_check("AGENT.md", rules))
            self.assertFalse(ok, (rules, ev))

    def test_an_output_path_outside_the_output_folder_or_a_link_never_passes(self):
        outside = os.path.join(self.tmp, "outside", "branch-setup")
        write(os.path.join(outside, "SKILL.md"), T.SKILL_MD)
        os.symlink(os.path.join(outside, "SKILL.md"), os.path.join(self.out, "SKILL.md"))
        os.symlink(outside, os.path.join(self.out, "linked"))
        for path in ("SKILL.md", "linked/SKILL.md", "../outside/branch-setup/SKILL.md", "missing/SKILL.md"):
            ok, ev = self.grade(rule_check(path, "rules/skill-frontmatter.rules.json"))
            self.assertFalse(ok, (path, ev))

    def test_the_check_runs_next_to_the_other_static_checks(self):
        self.assertIn("rule_check", se.STATIC_TYPES)
        for t in ("regex", "tool_call", "file_exists", "csv_rows"):
            self.assertIn(t, se.STATIC_TYPES)


if __name__ == "__main__":
    unittest.main()
