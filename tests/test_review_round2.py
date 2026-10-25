"""C1 owner review (round 2) of the fix-r1 changes, then the C4 fixes of Codex P1 round 1 (P1-1 whole-folder validation,
P1-2 internal symlinked folders, P1-3 agent rule-review sources) and of P1 audit rows 14 and 15 (links out of the unit
folder). Each code-defect test ran red before its fix. Three tests passed before their fix by nature: the over-reach
guard test_single_quoted_and_plain_continuations_stay_literal (green at C0, /tmp/c1-owner-r1/red-c0.txt) and the P1-2
cases test_references_itself_a_link_to_an_internal_folder and test_a_link_to_an_ancestor_terminates_and_reports_the_file_once
(green at C3, /tmp/c4-owner/red.txt; decisions.tsv row 15:36:31). The P2-4, P3-1 and P3-2 tests of the same C4 round live
in tests/test_review_round1.py and tests/test_english_rule.py. C5 changed the first P1-2 case to expect docs/ko.md, because
the scan of a skill now walks the whole folder (red at the C4 head, /tmp/c5-owner/red.txt). C6 changed the three P1-2
cases: the scan names every alias path of an internal folder link, and a link to an ancestor fails as a cycle (red at the
C5 head, /tmp/c6-owner/red.txt).
Mocked like tests/test_review_round1.py: subprocess is patched to fail for the whole module, no claude/codex/opencode/hermes
call, no network, no approve command (a decision is written to approvals.json directly). ASCII only: non-English text is
built from escapes."""
import json, os, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva
import skill_eval as se
import test_eva as E
import test_skill_eval as T
import test_multi_tool_parity as P

KO = "\ud55c\uad6d\uc5b4"
ESCAPED = "\\uAC00\\uB098"
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def ids(r):
    return {c["id"] for c in r.failed}

class TestUnassignedCodePointsFailClosed(E.SkillMixin, unittest.TestCase):
    def test_a_script_newer_than_the_python_unicode_database_fails(self):
        kawi = "\U00011f04\U00011f05\U00011f06"
        self.assertIsNotNone(eva.non_english_script(kawi[0]))
        r = self.skill(body="%s\n" % kawi)
        self.assertIn("english-skill-md", ids(r)); self.assertEqual(r.verdict, "FAIL")

class TestYamlBlockScope(E.SkillMixin, P.AgentBase, unittest.TestCase):
    def test_an_escaped_value_outside_a_block_scalar_is_decoded(self):
        extra = 'metadata: # note: |\n  author: "%s"\n' % ESCAPED
        meta, _, err = eva.parse_frontmatter_ex(E.GOOD_FM.format(name="word-count", extra=extra, body=""))
        self.assertEqual(meta["metadata"]["author"], "\uac00\ub098", err)
        r = self.skill(extra=extra)
        self.assertIn("english-skill-md", ids(r)); self.assertEqual(r.verdict, "FAIL")
        d, _ = self.agent()
        E.wtext(os.path.join(d, "wrappers", "hermes", "config.yaml"), '- key: |\n  other: "%s"\n' % ESCAPED)
        self.assertIn("english-wrappers", ids(eva.check(d)))

class TestYamlDoubleQuotedScalars(P.AgentBase, unittest.TestCase):
    def wrapper_yaml(self, text):
        fp = os.path.join(self.tmp, "code-scout", "wrappers", "hermes", "config.yaml")
        if os.path.exists(fp): os.remove(fp)
        d, before = self.agent()
        self.assertNotIn("english-wrappers", ids(before))
        E.wtext(os.path.join(d, "wrappers", "hermes", "config.yaml"), text)
        return ids(eva.check(d))

    def test_escapes_are_decoded_in_every_double_quoted_scalar(self):
        for label, text in (("continuation line", 'greeting: "hello\n  world %s"\n' % ESCAPED),
                            ("escaped line break", 'greeting: "hello \\\n  %s"\n' % ESCAPED),
                            ("tag", 'greeting: !!str "%s"\n' % ESCAPED),
                            ("anchor", 'greeting: &g "%s"\n' % ESCAPED),
                            ("tag in a flow sequence", 'greetings: [hi, !!str "%s"]\n' % ESCAPED)):
            with self.subTest(label):
                self.assertIn("english-wrappers", self.wrapper_yaml(text))

    def test_single_quoted_and_plain_continuations_stay_literal(self):
        for label, text in (("single-quoted", "greeting: 'hello\n  %s'\n" % ESCAPED),
                            ("plain", "greeting: hello\n  %s\n" % ESCAPED),
                            ("after a closed double-quoted scalar", 'greeting: "hello"\nnote: %s\n' % ESCAPED)):
            with self.subTest(label):
                self.assertNotIn("english-wrappers", self.wrapper_yaml(text))

class TestRuleReviewBoundToContent(P.CliBase):
    def matchers(self, patterns):
        doc = T.evals_doc(human=False)
        doc["evals"][0]["assertions"] = [{"text": "matcher %d" % i, "layer": "static", "check": {"type": "regex", "pattern": p}}
                                         for i, p in enumerate(patterns)]
        return doc

    def validate_after_change(self, label, before, after, rel):
        root = os.path.join(self.tmp, label); ws = os.path.join(root, "ws"); os.makedirs(ws)
        d = T.make_skill(root, evals=before if rel == "evals/evals.json" else None,
                         extra_files=None if rel == "evals/evals.json" else {rel: before})
        self.assertEqual(se.approve("rule_review", se.load_eval_set(d)[0], ws)["sha256"], se.rule_review_hash(d))
        self.assertEqual(self.cli("validate", d, "--workspace", ws)[0], 0, label)
        fp = os.path.join(d, *rel.split("/"))
        if rel == "evals/evals.json": T.wjson(fp, after)
        else: E.wtext(fp, after)
        return self.cli("validate", d, "--workspace", ws)

    def test_a_recorded_decision_does_not_cover_review_content_it_never_saw(self):
        six = [KO + str(i) for i in range(6)]
        long_tail = KO + "x" * 40
        for label, before, after, rel in (
                ("sixth-matcher", self.matchers(six), self.matchers(six[:5] + ["\uc804\ud600"]), "evals/evals.json"),
                ("long-matcher-tail", self.matchers([long_tail + "\uac00"]), self.matchers([long_tail + "\ub098"]),
                 "evals/evals.json"),
                ("codex-field-value", "interface:\n  display_name: Branch Setup\n", "interface:\n  display_name: Other\n",
                 "agents/openai.yaml")):
            with self.subTest(label):
                rc, out, _ = self.validate_after_change(label, before, after, rel)
                self.assertEqual(rc, 3, ascii(out[-160:])); self.assertIn("RULE REVIEW NOT RECORDED", out)

class TestValidateScansTheWholeFolder(P.CliBase):
    CASES = (("SKILL.md", T.SKILL_MD + KO + "\n"), ("references/notes.md", "# Notes\n%s\n" % KO))

    def korean_skill(self, rel, text):
        root = os.path.join(self.tmp, rel.replace("/", "-")); os.makedirs(root)
        return T.make_skill(root, evals=T.evals_doc(human=False), extra_files={rel: text})

    def test_validate_fails_on_non_english_skill_content_with_english_evals(self):
        for rel, text in self.CASES:
            with self.subTest(rel):
                rc, out, _ = self.cli("validate", self.korean_skill(rel, text))
                self.assertEqual(rc, 1, out); self.assertIn("english-only rule: %s:" % rel, out); self.assertIn("[Hangul]", out)

    def test_the_live_gates_refuse_before_the_estimate(self):
        for rel, text in self.CASES:
            spec, problems = se.load_eval_set(self.korean_skill(rel, text))
            for cmd in ("run", "loop"):
                with self.subTest(rel, cmd=cmd), mock.patch.object(se, "live_estimate") as est:
                    with self.assertRaises(se.EvalError) as cm:
                        se.check_live_gates(mock.Mock(cmd=cmd, strict_budget=True), spec, problems, self.ws, None, [],
                                            se.DEFAULT_TOKENS, {}, 100.0)
                    self.assertTrue(str(cm.exception).startswith("eval set has"), cm.exception)
                    est.assert_not_called()

class TestBundledFilesBehindInternalLinks(E.SkillMixin, unittest.TestCase):
    def linked(self, files, links, name="word-count"):
        self.skill(name=name, files=files)
        d = os.path.join(self.tmp, name)
        for rel, target in links.items():
            fp = os.path.join(d, *rel.split("/")); os.makedirs(os.path.dirname(fp), exist_ok=True)
            os.symlink(target, fp)
        return d

    def korean(self, d):
        return [f.split(" [")[0] for f in eva.english_violations(d)]

    def test_references_itself_a_link_to_an_internal_folder(self):
        d = self.linked({"docs/ko.md": KO + "\n"}, {"references": "docs"})
        self.assertEqual(self.korean(d), ["docs/ko.md:1", "references/ko.md:1"])

    def test_a_linked_folder_inside_references(self):
        d = self.linked({"references/a.md": "ok\n", "shared/ko.md": KO + "\n"}, {"references/more": "../shared"})
        self.assertEqual(self.korean(d), ["references/more/ko.md:1", "shared/ko.md:1"])
        self.assertIn("english-bundled-files", ids(eva.check(d)))

    def test_a_link_to_an_ancestor_terminates_and_reports_the_file_once(self):
        d = self.linked({"references/ko.md": KO + "\n", "references/sub/a.md": "ok\n"},
                        {"references/sub/up": "..", "references/root": ".."})
        found = eva.english_violations(d)
        self.assertEqual([f.split(" [")[0] for f in found if "cycle" not in f], ["references/ko.md:1"])
        self.assertEqual([f.split(" (link)")[0] for f in found if "cycle" in f], ["references/root", "references/sub/up"])

    def test_a_link_out_of_the_folder_fails_by_name_and_stays_an_escape(self):
        outside = os.path.join(self.tmp, "outside"); os.makedirs(outside)
        E.wtext(os.path.join(outside, "ko.md"), KO + "\n")
        for name, rel, target, ref in (("word-count", "references/ext", outside, "references/ext/ko.md"),
                                       ("file-link", "references/ko.md", os.path.join(outside, "ko.md"), "references/ko.md"),
                                       ("top-link", "references", outside, "references/ko.md")):
            with self.subTest(rel):
                d = self.linked({"assets/a.md": "ok\n"}, {rel: target}, name)
                found = eva.english_violations(d)
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith(rel + " "), found); self.assertIn("outside the unit folder", found[0])
                self.assertIn("english-bundled-files", ids(eva.check(d)))
                self.assertEqual(eva.ref_targets(d, [ref])[3], [ref])
                self.assertEqual(se.escaping_links(d), [rel])

class TestAgentLinksOutOfTheFolder(P.AgentBase, unittest.TestCase):
    def link_out(self, rel):
        d, _ = self.agent()
        src, dst = os.path.join(d, *rel.split("/")), os.path.join(self.tmp, "outside", *rel.split("/"))
        os.makedirs(os.path.dirname(dst)); os.rename(src, dst); os.symlink(dst, src)
        return d, dst

    def test_wrappers_linked_out_fail_the_english_row_and_every_wrapper_row(self):
        d, out = self.link_out("wrappers")
        with open(os.path.join(out, "hermes", "SOUL.md"), "a", encoding="utf-8") as f: f.write(KO + "\n")
        r = eva.check(d)
        en = P.row(r, "english-wrappers")
        self.assertEqual(en["status"], "fail", en["reason"]); self.assertIn("wrappers (link)", en["reason"])
        self.assertIn("outside the unit folder", en["reason"])
        for tool in eva.TOOLS:
            with self.subTest(tool):
                w = P.row(r, "parity-wrapper-" + tool)
                self.assertEqual(w["status"], "fail", w["reason"]); self.assertIn("outside the agent folder", w["reason"])
        self.assertNotIn(KO, json.dumps(r.checks, ensure_ascii=False))
        self.assertEqual(eva.review_items(d), [])

    def test_one_tool_folder_linked_out_fails_its_wrapper_row_and_leaves_no_review_item(self):
        d, _ = self.link_out("wrappers/opencode")
        r = eva.check(d)
        self.assertEqual(P.row(r, "parity-wrapper-opencode")["status"], "fail")
        self.assertEqual(P.row(r, "parity-permissions-opencode")["status"], "n/a")
        self.assertEqual([i for i in eva.review_items(d) if i.startswith("parity-permissions-opencode")], [])
        self.assertIn("wrappers/opencode (link)", P.row(r, "english-wrappers")["reason"])
        self.assertEqual(P.row(r, "parity-wrapper-codex")["status"], "pass")

    def test_a_hermes_config_linked_out_fails_the_hermes_wrapper_row(self):
        d, _ = self.agent()
        out = os.path.join(self.tmp, "outside.yaml"); E.wtext(out, "skills:\n  x: y\n")
        os.symlink(out, os.path.join(d, "wrappers", "hermes", "config.yaml"))
        r = eva.check(d)
        self.assertIn("wrappers/hermes/config.yaml resolves outside the agent folder", P.row(r, "parity-wrapper-hermes")["reason"])
        self.assertIn("wrappers/hermes/config.yaml (link)", P.row(r, "english-wrappers")["reason"])

    def test_a_scenario_linked_out_fails_by_name_and_feeds_no_matcher_review(self):
        d, out = self.link_out("scenarios/01.json")
        E.wtext(out, json.dumps(dict(P.S1, must_include=[KO])))
        en = P.row(eva.check(d), "english-scenarios")
        self.assertEqual(en["status"], "fail", en["reason"]); self.assertIn("scenarios/01.json (link)", en["reason"])
        self.assertEqual([i for i in eva.review_items(d) if i.startswith("english-output-matchers")], [])

class TestRuleReviewCoversAgentSources(P.AgentBase, unittest.TestCase):
    WRITE_MD = P.AGENT_MD.replace("[search, read]", "[search, read, write]")

    def recorded(self, md=None, scenario=None):
        d, _ = self.agent(md=md)
        if scenario is not None: E.wtext(os.path.join(d, "scenarios", "01.json"), json.dumps(scenario, indent=1))
        self.ws = os.path.join(self.tmp, "ws"); os.makedirs(self.ws)
        self.spec, _ = se.load_eval_set(d)
        T.install_test_approver(self)
        self.assertEqual(se.approve("rule_review", self.spec, self.ws)["sha256"], se.rule_review_hash(d))
        self.assertEqual(se.rule_review_problems(self.spec, self.ws), [])
        return d

    def assert_voided(self, d, rel, old, new):
        items, fp = eva.review_items(d), os.path.join(d, *rel.split("/"))
        with open(fp, encoding="utf-8") as f: text = f.read()
        self.assertIn(old, text)
        E.wtext(fp, text.replace(old, new))
        self.assertEqual(eva.review_items(d), items, "the findings alone cannot see this edit")
        self.assertEqual(len(se.rule_review_problems(self.spec, self.ws)), 1, rel)

    def test_an_agent_md_tool_change_within_the_same_class(self):
        self.assert_voided(self.recorded(), "AGENT.md", "capabilities: [search, read]", "capabilities: [read, search]")

    def test_a_scenario_matcher_beyond_the_shown_findings(self):
        six = [KO + str(i) for i in range(6)]
        d = self.recorded(scenario=dict(P.S1, must_include=six))
        self.assert_voided(d, "scenarios/01.json", json.dumps(six[5]), json.dumps(KO + "x"))

    def test_the_codex_wrapper_instructions(self):
        self.assert_voided(self.recorded(), "wrappers/codex/code-scout.toml", "Load the skill and use the engine.",
                           "Edit any file you need.")

    def test_the_opencode_wrapper_edit_permission_from_allow_to_ask(self):
        self.assert_voided(self.recorded(md=self.WRITE_MD), "wrappers/opencode/code-scout.md", "  edit: allow\n", "  edit: ask\n")

if __name__ == "__main__":
    unittest.main()
