"""Every rule id in a bundle has tests/rules/<rule-id>/pass/ and fail/ fixture units with an expected.json, and every
fixture unit gives that rule the expected row status (plan.md:562, :584; D17 and D18 of the A1 how pass). The default
bundle is checked against tests/rules/ (folders starting with '_' skipped) and the _selftest bundle against
tests/rules/_selftest/. Every problem names its rule id."""
import json, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva
from engine import rule_runner as RR

RULES_TESTS = os.path.join(ROOT, "tests", "rules")
SELFTEST = os.path.join(RULES_TESTS, "_selftest")
SELFTEST_ID = "selftest-name-matches-folder"
SELFTEST_FILES = ["rules.json", SELFTEST_ID + "/fail/bad-name/SKILL.md", SELFTEST_ID + "/fail/expected.json",
                  SELFTEST_ID + "/pass/expected.json", SELFTEST_ID + "/pass/good-name/SKILL.md"]


def subfolders(path, skip_underscore=False):
    return sorted(n for n in os.listdir(path) if os.path.isdir(os.path.join(path, n))
                  and not (skip_underscore and n.startswith("_")))


SIDES = ("pass", "fail", "review", "na")
STATUS_OF_SIDE = {"pass": "pass", "fail": "fail", "review": "needs-review", "na": "n/a"}
# Rules whose reachable outcomes are not pass and fail (brief A3 D13), with the reason. Every other rule needs pass/ and
# fail/; a review/ or na/ side, when present, is checked as well.
OUTCOMES = {
    "english-output-matchers": (("review", "na"), "run-output matchers give needs-review or n/a, never pass or fail"),
    "parity-invocation-hermes": (("review", "na"), "Hermes has no verified invocation control: needs-review or n/a"),
    "parity-permissions-opencode": (("fail", "review", "na"), "cannot reach pass while the OpenCode key list is not "
                                                               "complete (targets/opencode.json, gates.md G3)"),
    "parity-permissions-hermes": (("fail",), "Hermes permissions always fail closed (gates.md G4)"),
}
# Outcomes a rule reaches that also need a fixture side.
EXTRA_SIDES = {
    "english-bundled-files": ("na",), "english-evals": ("na",), "english-tool-files": ("na",),
    "english-scenarios": ("na",), "english-wrappers": ("na",), "parity-skill-codex": ("review",),
    "parity-invocation-control": ("na",), "parity-permissions-claude": ("na",), "parity-permissions-codex": ("review", "na"),
    "compatibility-length": ("na",), "reference-toc": ("na",), "version-format": ("na",),
}
# Reachable outcomes with no fixture side, and why: an unreadable main file needs non-UTF-8 bytes, which an ASCII-only
# fixture cannot hold, so tests/test_rule_targets.py builds that case in a temp folder; a refs-exist review needs an
# enclosing git work tree, which an exported tree does not have, so tests/test_rule_structure.py builds one.
NO_SIDE = {"english-skill-md": ("na", "needs a non-UTF-8 SKILL.md"), "english-agent-md": ("na", "needs a non-UTF-8 AGENT.md"),
           "refs-exist": ("review", "needs an enclosing git work tree")}


def required_sides(rule):
    if rule.id in OUTCOMES:
        return OUTCOMES[rule.id][0]
    return ("pass", "fail") + EXTRA_SIDES.get(rule.id, ())


def side_problems(bundle, rule, side, folder):
    rid = rule.id
    if not os.path.isdir(folder):
        return ["%s: no %s/ fixture folder" % (rid, side)]
    out = []
    units = subfolders(folder)
    if not units:
        out.append("%s: %s/ holds no fixture unit" % (rid, side))
    exp_path = os.path.join(folder, "expected.json")
    if not os.path.isfile(exp_path):
        return out + ["%s: %s/expected.json is missing" % (rid, side)]
    with open(exp_path, encoding="utf-8") as f:
        expected = json.load(f)
    if not isinstance(expected, dict):
        return out + ["%s: %s/expected.json is not an object" % (rid, side)]
    want = "needs-review" if side == "fail" and rule.severity == "review" else STATUS_OF_SIDE[side]
    for name in sorted(set(expected) - set(units)):
        out.append("%s: %s/expected.json names %s, which is not a fixture unit" % (rid, side, name))
    for name in units:
        if name not in expected:
            out.append("%s: %s/expected.json omits the fixture unit %s" % (rid, side, name))
            continue
        if expected[name] != want:
            out.append("%s: %s/expected.json says %r for %s, a %s/ fixture must be %r" % (rid, side, expected[name],
                                                                                          name, side, want))
        if "repository" in rule.applies_to:
            got, reason = repository_status(bundle, rule, os.path.join(folder, name))
            if got != expected[name]:
                out.append("%s: %s/%s gives status %r, expected.json says %r (%s)" % (rid, side, name, got,
                                                                                      expected[name], reason))
            continue
        rows = [c for c in eva.check(os.path.join(folder, name), bundle).checks if c["id"] == rid]
        if len(rows) != 1:
            out.append("%s: %s/%s gives %d rows for the rule, not 1" % (rid, side, name, len(rows)))
        elif rows[0]["status"] != expected[name]:
            out.append("%s: %s/%s gives status %r, expected.json says %r (%s)" % (rid, side, name, rows[0]["status"],
                                                                                  expected[name], rows[0]["reason"]))
    return out


def repository_status(bundle, rule, root):
    """The status of a repository rule over a fixture folder that is a mini repository root (rules/, schemas/,
    targets/, policy/)."""
    status, facts, _, _ = rule.check.repository(os.path.join(root, "rules"), root)
    return status, facts.get("findings", "")


def fixture_problems(bundle, fixtures, skip_underscore):
    ids = [r.id for r in bundle.rules]
    out = ["%s: fixture folder has no rule in the bundle" % n
           for n in subfolders(fixtures, skip_underscore) if n not in ids]
    for rule in bundle.rules:
        base = os.path.join(fixtures, rule.id)
        if not os.path.isdir(base):
            out.append("%s: no fixture folder tests/rules/%s/" % (rule.id, rule.id))
            continue
        need = required_sides(rule)
        for side in SIDES:
            if side in need or os.path.isdir(os.path.join(base, side)):
                out += side_problems(bundle, rule, side, os.path.join(base, side))
        out += ["%s: unknown fixture side %s/" % (rule.id, n) for n in subfolders(base) if n not in SIDES]
    return out


def tree_files(top):
    return sorted(os.path.relpath(os.path.join(dp, n), top).replace(os.sep, "/")
                  for dp, _, fn in os.walk(top) for n in fn)


class TestBundleFixtures(unittest.TestCase):
    def test_the_default_bundle_has_pass_and_fail_fixtures_for_every_rule(self):
        problems = fixture_problems(RR.default_bundle(), RULES_TESTS, skip_underscore=True)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_selftest_bundle_has_pass_and_fail_fixtures_for_every_rule(self):
        bundle = RR.load_bundle(SELFTEST)
        self.assertEqual([r.id for r in bundle.rules], [SELFTEST_ID])
        problems = fixture_problems(bundle, SELFTEST, skip_underscore=False)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_selftest_bundle_is_exactly_its_five_files(self):
        self.assertEqual(tree_files(SELFTEST), SELFTEST_FILES)

    def test_the_outcome_tables_name_rules_of_the_default_bundle(self):
        ids = {r.id for r in RR.default_bundle().rules}
        for table in (OUTCOMES, EXTRA_SIDES, NO_SIDE):
            self.assertEqual(set(table) - ids, set())
        for rid, (sides, reason) in OUTCOMES.items():
            self.assertNotIn("pass", sides, rid)
            self.assertTrue(reason, rid)

    def test_a_missing_outcome_side_is_found(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        copy = os.path.join(tmp, "rules")
        shutil.copytree(RULES_TESTS, copy, ignore=shutil.ignore_patterns("_selftest"))
        shutil.rmtree(os.path.join(copy, "parity-skill-codex", "review"))
        problems = fixture_problems(RR.default_bundle(), copy, skip_underscore=True)
        self.assertIn("parity-skill-codex: no review/ fixture folder", problems)


class TestFixtureGaps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.copy = os.path.join(self.tmp, "_selftest")
        shutil.copytree(SELFTEST, self.copy)
        self.base = os.path.join(self.copy, SELFTEST_ID)

    def problems(self):
        return fixture_problems(RR.load_bundle(self.copy), self.copy, skip_underscore=False)

    def assertNamesRule(self, problems, text):
        self.assertTrue(problems, "the gap was not found")
        self.assertTrue(any(p.startswith(SELFTEST_ID + ": ") and text in p for p in problems), problems)

    def test_a_missing_fail_folder_fails_naming_the_rule_id(self):
        shutil.rmtree(os.path.join(self.base, "fail"))
        self.assertNamesRule(self.problems(), "no fail/ fixture folder")

    def test_a_missing_pass_folder_fails_naming_the_rule_id(self):
        shutil.rmtree(os.path.join(self.base, "pass"))
        self.assertNamesRule(self.problems(), "no pass/ fixture folder")

    def test_a_missing_rule_folder_fails_naming_the_rule_id(self):
        shutil.rmtree(self.base)
        self.assertNamesRule(self.problems(), "no fixture folder")

    def test_a_missing_expected_json_fails_naming_the_rule_id(self):
        os.remove(os.path.join(self.base, "fail", "expected.json"))
        self.assertNamesRule(self.problems(), "fail/expected.json is missing")

    def test_a_side_without_a_unit_fails_naming_the_rule_id(self):
        shutil.rmtree(os.path.join(self.base, "pass", "good-name"))
        problems = self.problems()
        self.assertNamesRule(problems, "pass/ holds no fixture unit")
        self.assertNamesRule(problems, "names good-name, which is not a fixture unit")

    def test_an_unlisted_unit_fails_naming_the_rule_id(self):
        shutil.copytree(os.path.join(self.base, "fail", "bad-name"), os.path.join(self.base, "fail", "other-name"))
        self.assertNamesRule(self.problems(), "omits the fixture unit other-name")

    def test_a_wrong_expected_status_fails_naming_the_rule_id(self):
        with open(os.path.join(self.base, "fail", "expected.json"), "w", encoding="utf-8") as f:
            json.dump({"bad-name": "pass"}, f)
        problems = self.problems()
        self.assertNamesRule(problems, "a fail/ fixture must be 'fail'")
        self.assertNamesRule(problems, "gives status 'fail', expected.json says 'pass'")

    def test_a_fixture_that_does_not_trip_the_rule_fails_naming_the_rule_id(self):
        shutil.rmtree(os.path.join(self.base, "fail", "bad-name"))
        shutil.copytree(os.path.join(self.base, "pass", "good-name"), os.path.join(self.base, "fail", "bad-name"))
        fp = os.path.join(self.base, "fail", "bad-name", "SKILL.md")
        with open(fp, encoding="utf-8") as f:
            text = f.read()
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text.replace("name: good-name", "name: bad-name"))
        self.assertNamesRule(self.problems(), "fail/bad-name gives status 'pass'")

    def test_an_orphan_fixture_folder_fails_naming_it(self):
        shutil.copytree(self.base, os.path.join(self.copy, "no-such-rule"))
        problems = self.problems()
        self.assertIn("no-such-rule: fixture folder has no rule in the bundle", problems)


if __name__ == "__main__":
    unittest.main()
