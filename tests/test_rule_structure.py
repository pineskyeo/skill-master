"""Unit A4: the structure, description, reference and agent rows come from rules/skill-structure.rules.json,
rules/skill-description.rules.json, rules/agent-structure.rules.json, rules/data/description.json and three engine
primitives (plan.md:717-761). New behavior only; tests/test_policy_schemas.py pins the variant rows at the P7 commit."""
import ast, contextlib, inspect, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva
from engine import rule_runner as RR
from engine.primitives import description_heuristics as DH, heading_present as HP, local_refs as LR

DATA = os.path.join(ROOT, "rules", "data", "description.json")
HANGUL = re.compile("[\u3131-\u318e\uac00-\ud7a3]")
SPLIT = {
    "skill-description.rules.json": ["description-present", "description-length", "description-min-length",
                                     "description-no-xml", "description-when", "description-pov", "single-purpose"],
    "skill-structure.rules.json": ["frontmatter", "frontmatter-fields", "compatibility-length", "inputs-declared",
                                   "outputs-declared", "example-present", "size-limit", "refs-relative", "refs-exist",
                                   "refs-one-level", "refs-path-depth", "reference-toc", "version-format", "evals-count"],
    "agent-structure.rules.json": ["frontmatter", "agent-frontmatter-v2", "goal-present", "success-measurable", "scenarios-present",
                                   "scenarios-valid", "scenarios-expected", "tools-declared",
                                   "skills-need-skill-capability", "tools-subset",
                                   "tools-documented", "memory-section", "multi-turn-scenario"],
}
# id -> (applies_to, category, must when it fails, must when n/a or None when the rule never gives n/a): the A3 rows.
SHAPE = {
    "description-present": (("skill", "agent"), "single-task", True, None),
    "description-length": (("skill",), "single-task", True, None),
    "description-min-length": (("skill",), "single-task", False, None),
    "description-no-xml": (("skill",), "single-task", True, None),
    "description-when": (("skill",), "single-task", True, None),
    "description-pov": (("skill",), "single-task", True, None),
    "single-purpose": (("skill",), "single-task", False, None),
    "frontmatter-fields": (("skill",), "structure", True, None),
    "compatibility-length": (("skill",), "structure", True, True),
    "inputs-declared": (("skill",), "io", False, None),
    "outputs-declared": (("skill",), "io", False, None),
    "example-present": (("skill",), "io", False, None),
    "size-limit": (("skill",), "structure", False, None),
    "refs-relative": (("skill",), "structure", False, None),
    "refs-exist": (("skill",), "structure", True, None),
    "refs-one-level": (("skill",), "structure", False, None),
    "refs-path-depth": (("skill",), "structure", False, None),
    "reference-toc": (("skill",), "structure", False, False),
    "version-format": (("skill",), "structure", True, True),
    "evals-count": (("skill",), "evaluation", True, None),
    "goal-present": (("agent",), "goal", True, None),
    "success-measurable": (("agent",), "goal", True, None),
    "scenarios-present": (("agent",), "goal", False, None),
    "scenarios-valid": (("agent",), "goal", True, None),
    "scenarios-expected": (("agent",), "goal", False, None),
    "tools-declared": (("agent",), "tool-use", True, None),
    "tools-subset": (("agent",), "tool-use", False, None),
    "tools-documented": (("agent",), "tool-use", False, None),
    "memory-section": (("agent",), "context", False, None),
    "multi-turn-scenario": (("agent",), "context", False, None),
}
SKILL_MD = ("---\nname: {name}\ndescription: Cleans one CSV file for a structure test. Use when a test needs a skill "
            "unit.\nmetadata:\n  version: 1.0.0\n---\n# Test Skill\n## Inputs\n- csv_path\n## Outputs\n- clean_path\n"
            "## Example\nInput a.csv, output a_clean.csv.\n{extra}")
REVIEW_REASON = ("needs-review: repo-root-relative reference, not bundled: ['tools/run.py'] (found from the enclosing git "
                 "repo root, not the skill folder) (works only when the skill runs inside that repo)")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rule_docs():
    return {name: load(os.path.join(ROOT, "rules", name)) for name in SPLIT}


def texts(value):
    return list(value.values()) if isinstance(value, dict) else [value]


def row(report, rid):
    rows = [c for c in report.checks if c["id"] == rid]
    assert len(rows) == 1, (rid, [c["id"] for c in report.checks])
    return rows[0]


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def skill(self, name="test-skill", extra="", files=None, top="units"):
        path = os.path.join(self.tmp, top, name)
        write(os.path.join(path, "SKILL.md"), SKILL_MD.format(name=name, extra=extra))
        for rel, text in (files or {}).items():
            write(os.path.join(path, *rel.split("/")), text)
        return path

    def rules_copy(self, edit):
        """A --rules bundle: a copy of rules/ with one file edited by edit(folder)."""
        folder = os.path.join(self.tmp, "bundle")
        shutil.copytree(os.path.join(ROOT, "rules"), folder)
        edit(folder)
        return RR.load_bundle(folder)


class TestRuleFiles(unittest.TestCase):
    def test_the_three_rule_files_hold_the_brief_split_in_order(self):
        for name, doc in rule_docs().items():
            self.assertEqual([r["id"] for r in doc["rules"]], SPLIT[name], name)
        default = RR.default_bundle()
        for name, ids in SPLIT.items():
            self.assertEqual([r.id for r in default.rules if r.file == name], ids, name)

    def test_each_rule_keeps_the_a3_kind_category_and_must(self):
        rules = {(r.id, r.applies_to): r for r in RR.default_bundle().rules if r.file in SPLIT}
        for rid, (kinds, category, must, na_must) in SHAPE.items():
            r = rules[(rid, kinds)]
            self.assertEqual(r.category, category, rid)
            self.assertEqual(r.severity != "soft", must, rid)
            if na_must is not None:
                self.assertEqual(must and r.na_must, na_must, rid)
        for kind in ("skill", "agent"):
            r = rules[("frontmatter", (kind,))]
            self.assertEqual((r.category, r.severity), ("structure", "fail"), kind)

    def test_every_message_and_fix_is_ascii_english_and_every_fail_has_a_fix(self):
        for name, doc in rule_docs().items():
            for r in doc["rules"]:
                for key in ("message", "fix"):
                    for text in texts(r[key]):
                        self.assertTrue(text and text.isascii(), (name, r["id"], key))
                        self.assertNotRegex(text, HANGUL, (name, r["id"], key))
                fix = r["fix"]
                self.assertTrue(isinstance(fix, str) or "fail" in fix, r["id"])
        with open(DATA, "rb") as f:
            self.assertTrue(f.read().isascii())

    def test_every_source_carries_a_verified_field(self):
        for name, doc in rule_docs().items():
            for r in doc["rules"]:
                self.assertIn("verified", r["source"], (name, r["id"]))


class TestOps(unittest.TestCase):
    def test_the_a4_ops_left_the_reserved_table(self):
        self.assertEqual(set(RR.RESERVED_OPS), set())
        for op in ("heading_present", "description_heuristics", "local_refs", "generated_up_to_date") + RR.HOST_OPS:
            self.assertIn(op, RR.OPS, op)
        with open(os.path.join(ROOT, "schemas", "rules.schema.json"), encoding="utf-8") as f:
            enum = json.load(f)["$defs"]["node"]["properties"]["op"]["enum"]
        self.assertEqual(set(enum), set(RR.OPS))

    def test_host_op_names_are_closed(self):
        with self.assertRaises(ValueError) as cm:
            RR.install_host_ops({"host_anything": lambda node: None})
        self.assertIn("host_anything", str(cm.exception))
        self.assertEqual(set(RR._HOST), set(RR.HOST_OPS))

    def test_a_rule_on_a_host_op_without_a_callable_fails_the_load(self):
        with mock.patch.dict(RR._HOST, {}, clear=True):
            with self.assertRaises(RR.RuleError) as cm:
                RR.compile_check({"op": "host_tools_declared"})
            self.assertIn("host op 'host_tools_declared' has no installed callable", str(cm.exception))
            with self.assertRaises(RR.BundleError) as cm:
                RR.load_bundle(os.path.join(ROOT, "rules"))
            self.assertIn("agent-structure.rules.json", str(cm.exception))

    def test_engine_modules_never_import_eva_or_skill_eval(self):
        for dp, dn, fn in os.walk(os.path.join(ROOT, "engine")):
            dn[:] = [d for d in dn if d != "__pycache__"]
            for n in fn:
                if not n.endswith(".py"):
                    continue
                with open(os.path.join(dp, n), encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                names = {a.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names}
                names |= {node.module.split(".")[0] for node in ast.walk(tree)
                          if isinstance(node, ast.ImportFrom) and node.module}
                self.assertFalse(names & {"eva", "skill_eval"}, n)


class TestCheckFunctions(Tmp):
    def calls(self, fn):
        return [n for n in ast.walk(ast.parse(inspect.getsource(fn))) if isinstance(n, ast.Call)]

    def test_each_check_function_is_one_runner_call(self):
        for fn in (eva.check_skill, eva.check_agent):
            runner = [c for c in self.calls(fn) if isinstance(c.func, ast.Attribute)
                      and isinstance(c.func.value, ast.Name) and c.func.value.id == "rule_runner"]
            self.assertEqual([c.func.attr for c in runner], ["apply_families"], fn.__name__)
            adds = [c for c in self.calls(fn) if isinstance(c.func, ast.Attribute) and c.func.attr in ("add", "na", "review")]
            self.assertEqual(adds, [], fn.__name__)

    def test_the_r_add_count_drops_by_the_37_moved_rows(self):
        with open(os.path.join(ROOT, "eva.py"), encoding="utf-8") as f:
            self.assertEqual(sum(1 for ln in f if re.search(r"r\.add\(", ln)), 58 - 37 - 11)  # A6 deleted 11 in the legacy bodies

    def test_a_non_default_bundle_never_shrinks_the_family_rows(self):
        u = self.skill()
        default_ids = [c["id"] for c in eva.check(u).checks]
        selftest = RR.load_bundle(os.path.join(ROOT, "tests", "rules", "_selftest"))
        ids = [c["id"] for c in eva.check(u, selftest).checks]
        self.assertEqual(ids[:len(default_ids)], default_ids)
        self.assertEqual(ids[len(default_ids):], ["selftest-name-matches-folder", "bundle-non-default"])
        self.assertEqual(row(eva.check(u, selftest), "bundle-non-default")["status"], "needs-review")

    def test_a_family_file_in_the_given_bundle_replaces_the_default_one(self):
        u = self.skill()
        self.assertEqual(row(eva.check(u), "evals-count")["status"], "fail")

        def edit(folder):
            fp = os.path.join(folder, "skill-structure.rules.json")
            doc = load(fp)
            next(r for r in doc["rules"] if r["id"] == "evals-count")["check"]["min"] = 0
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        r = eva.check(u, self.rules_copy(edit))
        self.assertEqual((row(r, "evals-count")["status"], row(r, "evals-count")["reason"]),
                         ("pass", "0 eval cases (need >= 0)"))
        self.assertEqual(row(r, "bundle-non-default")["status"], "needs-review")

    def test_check_skill_alone_gives_the_rows_of_check(self):
        u = self.skill()
        direct = [(c["id"], c["status"], c["reason"]) for c in eva.check_skill(u).checks]
        self.assertEqual(direct, [(c["id"], c["status"], c["reason"]) for c in eva.check(u).checks])


class TestData(Tmp):
    def test_a_data_only_change_in_a_rules_copy_changes_a_row(self):
        u = self.skill()
        self.assertEqual(row(eva.check(u), "inputs-declared")["status"], "pass")
        self.assertEqual(row(eva.check(u), "single-purpose")["status"], "pass")

        def edit(folder):
            fp = os.path.join(folder, "data", "description.json")
            doc = load(fp)
            doc["HEADING_WORDS"]["inputs"] = ["parameters"]
            doc["TASK_VERBS"].remove("clean")
            doc["MULTI_PURPOSE_RE"] = r"\b(?:one csv file)\b"
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        r = eva.check(u, self.rules_copy(edit))
        self.assertEqual(row(r, "inputs-declared")["status"], "fail")
        self.assertEqual(row(r, "single-purpose")["reason"],
                         "description focuses on one purpose (distinct task verbs 0, max 2; add-on phrases 1): "
                         "task verbs [], phrases ['one csv file']")

    def test_a_threshold_is_a_rule_operand(self):
        u = self.skill()

        def edit(folder):
            fp = os.path.join(folder, "skill-description.rules.json")
            doc = load(fp)
            next(r for r in doc["rules"] if r["id"] == "description-min-length")["check"]["min"] = 500
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        got = row(eva.check(u, self.rules_copy(edit)), "description-min-length")
        n = len("Cleans one CSV file for a structure test. Use when a test needs a skill unit.")
        self.assertEqual((got["status"], got["reason"]), ("fail", "description length %d (>= 500, house rule)" % n))

    def test_a_broken_data_file_fails_the_load(self):
        def edit(folder):
            write(os.path.join(folder, "data", "description.json"), '{"TASK_VERBS": [}')
        with self.assertRaises(RR.BundleError) as cm:
            self.rules_copy(edit)
        self.assertIn("description.json", str(cm.exception))

    def desc_skill(self, text, name="test-skill"):
        path = self.skill(name)
        fp = os.path.join(path, "SKILL.md")
        with open(fp, encoding="utf-8") as f:
            md = f.read()
        write(fp, md.replace("Cleans one CSV file for a structure test. Use when a test needs a skill unit.", text, 1))
        return path

    def data_copy(self, **values):
        """A --rules bundle folder: a copy of rules/ whose data/description.json has values set (None deletes a key)."""
        folder = tempfile.mkdtemp(dir=self.tmp)
        shutil.copytree(os.path.join(ROOT, "rules"), os.path.join(folder, "rules"))
        fp = os.path.join(folder, "rules", "data", "description.json")
        doc = load(fp)
        for k, v in values.items():
            if v is None:
                del doc[k]
            else:
                doc[k] = v
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return os.path.join(folder, "rules")

    def test_the_trigger_and_joiner_literals_are_data(self):
        u = self.desc_skill("Cleans CSV files as well as trims them. Use when a test needs a skill unit.")
        self.assertEqual(row(eva.check(u), "single-purpose")["reason"],
                         "description focuses on one purpose (distinct task verbs 2, max 2; add-on phrases 1): "
                         "task verbs ['clean', 'trim'], phrases ['as well as']")
        r = eva.check(u, RR.load_bundle(self.data_copy(TASK_JOINER_PHRASE_RE=r"\bxyzzy\b")))
        self.assertEqual(row(r, "single-purpose")["reason"],
                         "description focuses on one purpose (distinct task verbs 1, max 2; add-on phrases 1): "
                         "task verbs ['clean'], phrases ['as well as']")
        u = self.desc_skill("Cleans one CSV file. (Use when a test needs a skill unit.)", name="marker-skill")
        self.assertEqual(row(eva.check(u), "description-when")["status"], "pass")
        r = eva.check(u, RR.load_bundle(self.data_copy(DESC_LIST_MARKERS="-*")))
        self.assertEqual(row(r, "description-when")["status"], "fail")
        u = self.skill(name="word-skill")
        self.assertEqual(row(eva.check(u), "description-when")["status"], "pass")
        r = eva.check(u, RR.load_bundle(self.data_copy(DESC_WORD_RE="[0-9]+")))
        self.assertEqual(row(r, "description-when")["status"], "fail")

    def bundle_error(self, folder):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = eva.main(["check-all", os.path.join(ROOT, "bad"), "--rules", folder])
        return rc, out.getvalue(), err.getvalue()

    def assert_refused(self, cases):
        for key, value in cases:
            with self.subTest(key=key, value=value):
                rc, out, err = self.bundle_error(self.data_copy(**{key: value}))
                self.assertEqual((rc, out), (2, ""), err)
                self.assertTrue(err.startswith("bundle error: ") and key.split(".")[0] in err, err)
                self.assertNotIn("Traceback", err)

    def test_a_malformed_word_list_is_a_bundle_error_before_any_row(self):
        self.assert_refused([("TASK_VERBS", "clean"), ("DESC_WHEN_VAGUE", [5]), ("TASK_JOINERS", ["and", ""]),
                             ("AMBIGUOUS_TASK_VERBS", {"run": 1})])

    def test_a_malformed_regex_is_a_bundle_error_before_any_row(self):
        self.assert_refused([("MULTI_PURPOSE_RE", 5), ("DESC_WORD_RE", ["x"]), ("DESC_SENTENCE_RE", "("),
                             ("DESC_USE_VERB", 1), ("DESC_LIST_MARKERS", 5)])

    def test_a_malformed_number_is_a_bundle_error_before_any_row(self):
        self.assert_refused([("SINGLE_PURPOSE_MAX_VERBS", "2"), ("DESC_WHEN_MIN_WORDS", True),
                             ("SINGLE_PURPOSE_MAX_VERBS", 2.5), ("DESC_WHEN_MIN_WORDS", -1)])

    def test_malformed_heading_words_are_a_bundle_error_before_any_row(self):
        words = load(DATA)["HEADING_WORDS"]
        self.assert_refused([("HEADING_WORDS", dict(words, inputs=[5])), ("HEADING_WORDS", dict(words, memory="memory")),
                             ("HEADING_WORDS", dict(words, outputs=[])), ("HEADING_WORDS", ["input"])])

    def test_every_data_key_is_checked_when_the_data_loads(self):
        keys = [k for k in load(DATA) if k != "$comment"]
        for key in keys:
            for value in (None, {"x": None}):
                doc = load(DATA)
                if value is None:
                    del doc[key]
                else:
                    doc[key] = value
                with self.subTest(key=key, value=value):
                    with self.assertRaises(DH.DataError) as cm:
                        DH.Data(doc)
                    self.assertIn(key, str(cm.exception))

    def test_data_compiles_once_per_file_content(self):
        self.assertIs(DH.data_for(DATA), DH.data_for(DATA))
        self.assertIs(DH.default(), DH.data_for(DATA))

    def test_no_korean_alternative_decides_the_add_on_heading_or_toc_rows(self):
        doc = load(DATA)
        self.assertNotRegex(doc["MULTI_PURPOSE_RE"], HANGUL)
        self.assertNotRegex(json.dumps(doc["HEADING_WORDS"], ensure_ascii=False), HANGUL)
        toc = next(r for r in load(os.path.join(ROOT, "rules", "skill-structure.rules.json"))["rules"]
                   if r["id"] == "reference-toc")
        self.assertEqual(toc["check"]["toc_pattern"], r"contents|\btoc\b")
        escaped = [k for k, v in doc.items() if isinstance(v, str) and re.search(r"\\u[0-9a-f]{4}", v)]
        self.assertEqual(escaped, ["DESC_PERSON_RE"])


class TestSuccessMeasurable(unittest.TestCase):
    CR = os.path.join(ROOT, "tests", "fixtures", "agents", "code-reviewer-v2")
    GOLDEN_CR = os.path.join(ROOT, "golden", "agents", "code-reviewer")
    VAGUE = os.path.join(ROOT, "bad", "agents", "vague-agent")

    def test_the_row_decides_through_the_typed_criteria(self):
        self.assertEqual(row(eva.check(self.CR), "success-measurable")["status"], "pass")
        self.assertEqual(row(eva.check(self.GOLDEN_CR), "success-measurable")["status"], "fail")
        self.assertEqual(row(eva.check(self.VAGUE), "success-measurable")["status"], "fail")
        self.assertFalse(hasattr(eva, "outcome_evidence"))

    def test_every_criterion_is_counted_and_the_llm_ones_are_named(self):
        got = row(eva.check(self.CR), "success-measurable")["reason"]
        self.assertEqual(got, "2/3 success criteria machine-checkable; needs-review (human/model grading) at transcript "
                              "time: ['Every finding cites file:line evidence.']")


class TestReferences(Tmp):
    def test_a_repo_root_reference_is_needs_review_with_the_a3_reason(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        write(os.path.join(repo, "tools", "run.py"), "print(1)\n")
        u = os.path.join(repo, "skills", "ref-skill")
        write(os.path.join(u, "SKILL.md"), SKILL_MD.format(name="ref-skill", extra="See [r](tools/run.py).\n"))
        got = row(eva.check(u), "refs-exist")
        self.assertEqual((got["status"], got["must"], got["reason"]), ("needs-review", True, REVIEW_REASON))
        self.assertTrue(got["fix"].isascii() and got["fix"].endswith(": tools/run.py"), got["fix"])

    def test_an_outside_link_and_a_two_level_reference_fail_their_rows(self):
        u = self.skill(extra="See [o](../outside.md) and [d](references/deep/d.md).\n",
                       files={"references/deep/d.md": "# Deep\n"})
        r = eva.check(u)
        self.assertEqual(row(r, "refs-relative")["status"], "fail")
        self.assertEqual(row(r, "refs-path-depth")["reason"], "refs at most one directory deep: ['references/deep/d.md']")

    def test_the_primitive_takes_the_depth_from_its_operand(self):
        u = self.skill(extra="See [d](references/deep/d.md).\n", files={"references/deep/d.md": "# Deep\n"})

        def edit(folder):
            fp = os.path.join(folder, "skill-structure.rules.json")
            doc = load(fp)
            next(r for r in doc["rules"] if r["id"] == "refs-path-depth")["check"]["depth"] = 2
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        self.assertEqual(row(eva.check(u, self.rules_copy(edit)), "refs-path-depth")["status"], "pass")


class TestFeedback(Tmp):
    def test_feedback_of_a_bad_skill_is_english_with_a_fix_line_per_failing_row(self):
        d = os.path.join(self.tmp, "DoEverything")
        shutil.copytree(os.path.join(ROOT, "bad", "skills", "DoEverything"), d)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eva.main(["feedback", d]), 1)
        with open(os.path.join(d, "FEEDBACK.md"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        failed = eva.check(d).failed
        self.assertTrue(failed)
        for c in failed:
            i = next(n for n, ln in enumerate(lines) if re.match(r"\d+\. `%s`" % re.escape(c["id"]), ln))
            self.assertTrue(lines[i + 1].startswith("   - Reason: "), lines[i + 1])
            self.assertTrue(lines[i + 2].startswith("   - Fix: ") and len(lines[i + 2]) > len("   - Fix: "), c["id"])
            self.assertTrue(lines[i + 2].isascii(), lines[i + 2])
        for ln in lines:
            if not ln.startswith("   - Reason: "):
                self.assertTrue(ln.isascii(), ln)
        self.assertIn("## What to fix (most important first)", lines)

    def feedback(self, path):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = eva.main(["feedback", path])
        with open(os.path.join(path, "FEEDBACK.md"), encoding="utf-8") as f:
            return rc, f.read().splitlines()

    def assert_english(self, lines, report):
        for ln in lines:
            self.assertTrue(ln.isascii(), ln)
        for c in report.failed:
            i = lines.index(next(ln for ln in lines if re.match(r"\d+\. `%s`" % re.escape(c["id"]), ln)))
            self.assertEqual(lines[i + 2], "   - Fix: %s" % c["fix"])
            self.assertTrue(c["fix"], c["id"])

    def test_feedback_of_a_passing_skill_says_nothing_to_fix_in_english(self):
        d = os.path.join(self.tmp, "csv-cleaner-en")
        shutil.copytree(os.path.join(ROOT, "tests", "fixtures", "skills", "csv-cleaner-en"), d)
        rc, lines = self.feedback(d)
        self.assertEqual((rc, eva.check(d).verdict), (0, "PASS"))
        self.assertIn("Every check passed. Nothing to fix.", lines)
        self.assert_english(lines, eva.check(d))

    def test_feedback_of_a_needs_review_skill_has_the_english_review_header(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        write(os.path.join(repo, "tools", "run.py"), "print(1)\n")
        d = os.path.join(repo, "skills", "csv-cleaner-en")
        shutil.copytree(os.path.join(ROOT, "tests", "fixtures", "skills", "csv-cleaner-en"), d)
        with open(os.path.join(d, "SKILL.md"), "a", encoding="utf-8") as f:
            f.write("\nSee [r](tools/run.py).\n")
        rc, lines = self.feedback(d)
        r = eva.check(d)
        self.assertEqual((rc, r.verdict, r.failed, [c["id"] for c in r.needs_review]), (3, "REVIEW", [], ["refs-exist"]))
        self.assertIn("## Needs human or model grading (needs-review, no automatic verdict)", lines)
        self.assertIn("Every automatic check passed.", lines)
        self.assert_english(lines, r)

    def test_a_row_the_rule_passes_and_eva_fails_takes_the_english_rule_fix(self):
        u = self.skill(name="claude-helper")

        def edit(folder):
            fp = os.path.join(folder, "naming.rules.json")
            doc = load(fp)
            next(r for r in doc["rules"] if r["id"] == "name-reserved")["check"]["pattern"] = "anthropic"
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        r = eva.check(u, self.rules_copy(edit))
        got = row(r, "name-reserved")
        self.assertEqual(got["status"], "fail")
        self.assertIn("rule and eva.py disagree, failing closed: the rule says pass", got["reason"])
        self.assertEqual(got["fix"], "Remove the reserved words anthropic and claude from the name.")
        self.assert_english(eva.feedback_md(r).splitlines(), r)


class TestMainPreload(Tmp):
    def test_a_broken_default_data_file_is_a_bundle_error_under_a_bundle_without_the_family_files(self):
        repo = os.path.join(self.tmp, "repo")
        skip = shutil.ignore_patterns("__pycache__")
        os.makedirs(repo)
        for n in os.listdir(ROOT):
            if n.endswith(".py"):
                shutil.copy(os.path.join(ROOT, n), os.path.join(repo, n))
        for d in ("engine", "rules", "schemas", "targets", "policy", "bad", os.path.join("tests", "rules", "_selftest")):
            if os.path.isdir(os.path.join(ROOT, d)):
                shutil.copytree(os.path.join(ROOT, d), os.path.join(repo, d), ignore=skip)
        write(os.path.join(repo, "rules", "data", "description.json"), '{"TASK_VERBS": [}')
        p = subprocess.run([sys.executable, "eva.py", "check-all", "bad", "--rules", os.path.join("tests", "rules", "_selftest")],
                           cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual((p.returncode, p.stdout), (2, ""), p.stderr)
        self.assertTrue(p.stderr.startswith("bundle error: ") and "description.json" in p.stderr, p.stderr)
        self.assertNotIn("Traceback", p.stderr)


class TestPublicNames(unittest.TestCase):
    def operand(self, fname, rid, key):
        doc = load(os.path.join(ROOT, "rules", fname))
        return next(r for r in doc["rules"] if r["id"] == rid)["check"][key]

    def test_the_thresholds_live_only_in_the_rule_operands(self):
        for name in ("REF_TOC_LINES", "MIN_EVALS", "SKILL_FIELDS", "HOST_SKILL_FIELDS", "SKILL_MAX_BYTES", "SKILL_MAX_LINES",
                     "COMPATIBILITY_MAX", "DESCRIPTION_MIN", "DESCRIPTION_MAX", "OPERANDS"):
            self.assertFalse(hasattr(eva, name), name)
        self.assertEqual(self.operand("skill-description.rules.json", "description-length", "max"), 1024)
        with self.assertRaises(AttributeError):
            eva.NO_SUCH_NAME

    def test_the_helpers_are_the_primitives(self):
        self.assertIs(eva.has_section, HP.has_section)
        self.assertIs(eva.headings, HP.headings)
        self.assertIs(eva.local_refs, LR.local_refs)
        self.assertIs(eva.ref_targets, LR.ref_targets)
        self.assertIs(eva._ref_problem, LR.ref_problem)
        self.assertEqual(eva.desc_when_clause("Counts words. Use when the user wants a word count."),
                         "the user wants a word count")
        self.assertEqual(eva.desc_person_words("I count words. Use when asked."), ["I"])
        self.assertEqual(eva.desc_task_verbs("Summarizes PDFs, cleans CSVs, sends emails."),
                         ["summarize", "clean", "send"])


if __name__ == "__main__":
    unittest.main()
