"""Unit B6: `skill_eval.py version bump`, `deprecate` and `drift` (engine/lifecycle.py).

version bump follows SemVer and needs a CHANGELOG.md entry; deprecate writes an unsigned marker that keeps the version
installable; drift lists pinned tool versions against observed ones, units recorded under an older ruleset hash and
rules whose verified date is old or null, and starts no process. Every unit here is a temp copy. ASCII only."""
import contextlib, io, json, os, re, shutil, socket, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_install as I
import test_eva as E
from test_skill_eval import se, eva, read, write, wjson, rjson

ROOT = T.ROOT
HANGUL = "".join(map(chr, (0xD55C, 0xAD6D, 0xC5B4)))
GOLDEN_UNITS = ("agent/code-reviewer", "agent/research-assistant", "skill/csv-cleaner", "skill/korean-proofreader",
                "skill/pdf-summarizer")
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def lifecycle():
    from engine import lifecycle
    return lifecycle


def cli(*argv):
    return I.cli(*argv)


def no_process():
    def boom(*a, **k): raise AssertionError("drift started a process or opened a socket: %r" % (a[:1],))
    stack = contextlib.ExitStack()
    for obj, name in ((socket, "socket"), (socket, "create_connection"), (os, "system"), (os, "posix_spawn"),
                      (os, "posix_spawnp"), (os, "fork"), (os, "execv"), (os, "execve")):
        if hasattr(obj, name): stack.enter_context(mock.patch.object(obj, name, boom))
    return stack


class TestSemver(unittest.TestCase):
    def test_next_release_clears_prerelease_and_build(self):
        L = lifecycle()
        for v, part, want in (("1.2.3", "patch", "1.2.4"), ("1.2.3", "minor", "1.3.0"), ("1.2.3", "major", "2.0.0"),
                              ("0.9.9", "minor", "0.10.0"), ("1.2.3-rc.1+b.5", "patch", "1.2.4"),
                              ("1.2.3+b", "major", "2.0.0")):
            self.assertEqual(L.next_version(v, part), want, (v, part))
        for v in ("1.2", "v1.2.3", "01.2.3", "", None):
            with self.assertRaises(L.LifecycleError): L.next_version(v, "patch")
        with self.assertRaises(L.LifecycleError): L.next_version("1.2.3", "build")

    def test_the_changelog_entry_rule(self):
        L = lifecycle()
        self.assertIsNone(L.changelog_problem("# Changelog\n\n## 1.0.1\n\nFixed the branch name.\n\n## 1.0.0\nFirst.\n",
                                              "1.0.1"))
        for text in (None, "", "## 1.0.0\nFirst.\n", "## 1.0.1\n\n## 1.0.0\nFirst.\n", "## 1.0.1\n   \n",
                     "## 1.0.1 \nFixed.\n", "### 1.0.1\nFixed.\n", "## 1.0.10\nFixed.\n",
                     "## 1.0.1\n%s\n" % HANGUL):
            self.assertIsNotNone(L.changelog_problem(text, "1.0.1"), repr(text))


class Units(T.Base):
    def skill(self, meta="metadata:\n  version: 1.0.0\n"):
        d = T.make_skill(os.path.join(self.tmp, "skills"))
        text = read(os.path.join(d, "SKILL.md")).replace("metadata:\n  version: 1.0.0\n", meta)
        write(os.path.join(d, "SKILL.md"), text)
        return d

    def changelog(self, d, text):
        write(os.path.join(d, "CHANGELOG.md"), text)


class TestVersionBump(Units):
    def test_a_bump_without_a_changelog_entry_refuses_and_writes_nothing(self):
        d = self.skill()
        before = read(os.path.join(d, "SKILL.md"))
        rc, out, err = cli("version", "bump", d, "patch")
        self.assertEqual(rc, 2, out + err)
        self.assertIn('version bump needs a changelog entry: add "## 1.0.1" and a line of text to %s'
                      % os.path.join(d, "CHANGELOG.md"), err)
        for text in ("## 1.0.0\nFirst.\n", "## 1.0.1\n\n## 1.0.0\nFirst.\n", "## 1.0.1\n%s\n" % HANGUL):
            self.changelog(d, text)
            self.assertEqual(cli("version", "bump", d, "patch")[0], 2, text)
        self.assertEqual(read(os.path.join(d, "SKILL.md")), before)

    def test_a_bump_rewrites_only_the_version_line(self):
        d = self.skill()
        p = os.path.join(d, "SKILL.md")
        before = read(p)
        self.changelog(d, "# Changelog\n\n## 1.1.0\n\nAdds a dry run.\n")
        rc, out, err = cli("version", "bump", d, "minor", "--dry-run")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("dry run: would bump branch-setup 1.0.0 -> 1.1.0", out)
        self.assertEqual(read(p), before)
        rc, out, err = cli("version", "bump", d, "minor")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out.strip().splitlines()[-1], "bumped branch-setup 1.0.0 -> 1.1.0")
        a, b = before.splitlines(), read(p).splitlines()
        self.assertEqual(len(a), len(b))
        self.assertEqual([(x, y) for x, y in zip(a, b) if x != y], [("  version: 1.0.0", "  version: 1.1.0")])

    def test_a_top_level_version_and_a_quoted_version_keep_their_form(self):
        for meta, old, new in (("version: 2.0.0\n", "version: 2.0.0", "version: 3.0.0"),
                               ("metadata:\n  version: '2.0.0'\n", "  version: '2.0.0'", "  version: '3.0.0'")):
            with self.subTest(meta):
                d = self.skill(meta)
                self.changelog(d, "## 3.0.0\nBreaking change.\n")
                rc, out, err = cli("version", "bump", d, "major")
                self.assertEqual(rc, 0, out + err)
                self.assertIn(new, read(os.path.join(d, "SKILL.md")).splitlines())
                self.assertNotIn(old, read(os.path.join(d, "SKILL.md")).splitlines())
                shutil.rmtree(d)

    def test_a_missing_or_malformed_version_refuses(self):
        for meta in ("", "metadata:\n  version: '1.0'\n", "version: v1.0.0\n"):
            with self.subTest(meta):
                d = self.skill(meta)
                self.changelog(d, "## 1.0.1\nFixed.\n")
                before = read(os.path.join(d, "SKILL.md"))
                rc, out, err = cli("version", "bump", d, "patch")
                self.assertEqual(rc, 2, out + err)
                self.assertIn("version", err)
                self.assertEqual(read(os.path.join(d, "SKILL.md")), before)
                shutil.rmtree(d)

    def test_an_agent_bump_rewrites_its_version_line(self):
        a = I.agent_copy(os.path.join(self.tmp, "agents"))
        self.changelog(a, "## 1.0.1\nTighter review rules.\n")
        rc, out, err = cli("version", "bump", a, "patch")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("bumped %s 1.0.0 -> 1.0.1" % I.NAME, out)
        self.assertIn("version: 1.0.1", read(os.path.join(a, "AGENT.md")).splitlines())
        b = I.agent_copy(os.path.join(self.tmp, "agents-b"), version=None)
        self.changelog(b, "## 1.0.1\nText.\n")
        self.assertEqual(cli("version", "bump", b, "patch")[0], 2)

    def test_a_changelog_moves_only_english_bundled_files_and_a_non_english_entry_fails_it(self):
        for unit in (self.skill(), I.agent_copy(os.path.join(self.tmp, "agents"))):
            with self.subTest(unit):
                rep = eva.check(unit)
                before = {c["id"]: (c["status"], c["reason"]) for c in rep.checks}
                self.changelog(unit, "# Changelog\n\n## 1.0.0\n\nFirst release.\n")
                after_rep = eva.check(unit)
                after = {c["id"]: (c["status"], c["reason"]) for c in after_rep.checks}
                self.assertEqual(after_rep.verdict, rep.verdict)
                changed = {k: (before[k][0], after[k][0]) for k in before if before[k] != after.get(k)}
                self.assertLessEqual(set(changed), {"english-bundled-files"}, changed)
                self.assertEqual(set(after), set(before))
                if changed: self.assertEqual(changed["english-bundled-files"], ("n/a", "pass"))
                self.changelog(unit, "## 1.0.0\n%s\n" % HANGUL)
                if unit.endswith("branch-setup"):
                    row = next(c for c in eva.check(unit).checks if c["id"] == "english-bundled-files")
                    self.assertEqual(row["status"], "fail")


class TestDeprecate(Units):
    def test_deprecate_writes_an_unsigned_marker_and_a_repeat_is_no_change(self):
        d = self.skill()
        rc, out, err = cli("deprecate", d, "--version", "1.0.0", "--reason", "superseded by 1.1.0")
        self.assertEqual(rc, 0, out + err)
        p = os.path.join(self.tmp, "skills", "branch-setup-workspace", "deprecations.json")
        doc = rjson(p)
        self.assertEqual(doc["format"], 1)
        self.assertEqual(sorted(doc["versions"]), ["1.0.0"])
        self.assertEqual(doc["versions"]["1.0.0"]["reason"], "superseded by 1.1.0")
        self.assertRegex(doc["versions"]["1.0.0"]["at"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
        raw = read(p)
        rc, out, err = cli("deprecate", d, "--version", "1.0.0", "--reason", "superseded by 1.1.0")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("no change", out.splitlines())
        self.assertEqual(read(p), raw)

    def test_deprecate_refuses_bad_versions_and_reasons(self):
        d = self.skill()
        for version, reason in (("1.0.1", "later"), ("1.0", "x"), ("v1.0.0", "x"), ("1.0.0", ""), ("1.0.0", "  "),
                                ("1.0.0", "old " + HANGUL), ("1.0.0", "tab\there")):
            with self.subTest((version, reason)):
                rc, out, err = cli("deprecate", d, "--version", version, "--reason", reason)
                self.assertEqual(rc, 2, out + err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "skills", "branch-setup-workspace", "deprecations.json")))

    def test_a_deprecated_version_still_installs_with_a_warning(self):
        a = I.agent_copy(os.path.join(self.tmp, "agents"))
        self.assertEqual(cli("deprecate", a, "--version", "1.0.0", "--reason", "use 1.1.0")[0], 0)
        target = os.path.join(self.tmp, "proj"); os.makedirs(target)
        rc, out, err = cli("install", a, "--target", target)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("warning: %s 1.0.0 is deprecated: use 1.1.0" % I.NAME, out + err)
        self.assertTrue(os.path.isfile(os.path.join(target, ".claude", "agents", I.NAME + ".md")))


class TestDrift(Units):
    def drift(self, *argv):
        with no_process():
            return cli("drift", *argv)

    def test_tools_read_observed_unknown_without_a_flag_and_drift_on_a_different_version(self):
        root = os.path.join(self.tmp, "empty"); os.makedirs(root)
        rc, out, err = self.drift(root, "--today", "2026-10-26")
        self.assertIn(rc, (0, 3), out + err)
        lines = out.splitlines()
        claude = next(ln for ln in lines if ln.startswith("tool claude:"))
        self.assertIn("pinned 2.1.302", claude)
        self.assertIn("observed unknown (drift starts no tool binary; pass --observed claude=<version>)", claude)
        self.assertIn("pinned 0.162.1", next(ln for ln in lines if ln.startswith("tool codex:")))
        self.assertIn("pinned 1.17.13", next(ln for ln in lines if ln.startswith("tool opencode:")))
        self.assertIn("no pinned version", next(ln for ln in lines if ln.startswith("tool hermes:")))
        rc, out, err = self.drift(root, "--observed", "claude=2.1.302", "--observed", "codex=0.163.0")
        self.assertEqual(rc, 3, out + err)
        lines = out.splitlines()
        self.assertNotIn("DRIFT", next(ln for ln in lines if ln.startswith("tool claude:")))
        self.assertIn("DRIFT", next(ln for ln in lines if ln.startswith("tool codex:")))
        for bad in ("cursor=1.0.0", "claude", "claude="):
            self.assertEqual(self.drift(root, "--observed", bad)[0], 2, bad)
        rc, out, err = self.drift(os.path.join(self.tmp, "missing"))
        self.assertEqual(rc, 2, out + err)
        self.assertIn("not a folder", err)

    def test_every_golden_unit_is_listed_under_an_older_ruleset(self):
        g = os.path.join(self.tmp, "golden")
        shutil.copytree(os.path.join(ROOT, "golden"), g)
        rc, out, err = self.drift(g)
        self.assertEqual(rc, 3, out + err)
        cur = se.ruleset_sha256()[:12]
        listed = sorted(m.group(1) for m in re.finditer(r"^older ruleset: (\S+) recorded \S+ current (\S+)$", out, re.M)
                        if m.group(2) == cur)
        self.assertEqual(listed, sorted(GOLDEN_UNITS), out)

    def test_a_unit_recorded_under_the_current_ruleset_is_not_listed(self):
        d = self.skill()
        ws = os.path.join(self.tmp, "skills", "branch-setup-workspace")
        wjson(os.path.join(ws, "iteration-1", "benchmark.json"), {"metadata": {"ruleset_sha256": se.ruleset_sha256()}})
        rc, out, err = self.drift(os.path.join(self.tmp, "skills"))
        self.assertNotIn("skill/branch-setup", out)
        wjson(os.path.join(ws, "iteration-2", "benchmark.json"), {"metadata": {"ruleset_sha256": "0" * 64}})
        rc, out, err = self.drift(os.path.join(self.tmp, "skills"))
        self.assertIn("older ruleset: skill/branch-setup recorded 000000000000 current %s" % se.ruleset_sha256()[:12],
                      out.splitlines())
        wjson(os.path.join(ws, "registration.json"), {"ruleset_sha256": se.ruleset_sha256()})
        rc, out, err = self.drift(os.path.join(self.tmp, "skills"))
        self.assertNotIn("skill/branch-setup", out, "registration.json comes before the last benchmark")

    def test_rules_with_an_old_or_null_verified_date_are_listed(self):
        root = os.path.join(self.tmp, "empty"); os.makedirs(root)
        self.assertEqual(se.RULE_VERIFIED_MAX_DAYS, 180)
        rc, out, err = self.drift(root, "--today", "2026-10-26")
        lines = out.splitlines()
        self.assertIn("rule name-reserved (rules/naming.rules.json): unverified (source.verified is null)", lines)
        self.assertFalse([ln for ln in lines if ln.startswith("rule name-format ")], out)
        rc, out, err = self.drift(root, "--today", "2027-06-01")
        self.assertEqual(rc, 3, out + err)
        self.assertIn("rule name-format (rules/naming.rules.json): verified 2026-10-19, older than 180 days before "
                      "2027-06-01", out.splitlines())
        self.assertEqual(self.drift(root, "--today", "2027-02-30")[0], 2)

    def test_nothing_listed_exits_0(self):
        L = lifecycle()
        lines, listed = L.drift_report({"claude": {"version": "1.0.0", "verified": "2026-10-19", "source": "s"}},
                                       {"claude": "1.0.0"}, [], "c" * 64, [], "2026-10-26", 180)
        self.assertEqual(listed, 0, lines)
        lines, listed = L.drift_report({}, {}, [("skill", "x", None)], "c" * 64, [], "2026-10-26", 180)
        self.assertEqual(listed, 1, lines)
        self.assertIn("older ruleset: skill/x recorded none current cccccccccccc", lines)


if __name__ == "__main__":
    unittest.main()
