"""Codex review round 1 (REQUEST_CHANGES): one test group per finding. Design and policy choices: README.md ("English-only
rule", "Multi-tool parity"), notes/english-rule-plan.md section 11, notes/multi-tool-parity-plan.md section 12.
Everything is mocked like the other rule tests: subprocess is patched to fail for the whole module, builders are plain
functions, no claude/codex/opencode/hermes call, no network. ASCII only: non-English text is built from escapes."""
import json, os, shutil, subprocess, sys, tempfile, unicodedata, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva, skill_eval as se
import test_eva as E
import test_skill_eval as T
import test_multi_tool_parity as P
import test_english_rule as ER

KO = "\ud55c\uad6d\uc5b4 \ud14d\uc2a4\ud2b8"
CHEROKEE = "\u13a0\u13a1\u13a2"
YI = "\ua000\ua001\ua002"
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def ids(r):
    return {c["id"] for c in r.failed}

def row(r, cid):
    return next(c for c in r.checks if c["id"] == cid)

# ---------- P1-1: comprehensive scripts ----------
class TestScriptCoverage(unittest.TestCase):
    SAMPLES = {"Cherokee": "\u13a0", "Yi": "\ua000", "Canadian": "\u1401", "Ogham": "\u1681", "Runic": "\u16a0",
               "Coptic": "\u2c80", "Gothic": "\U00010330", "Tifinagh": "\u2d30", "Cuneiform": "\U00012000",
               "Egyptian": "\U00013000", "Linear": "\U00010000", "Georgian": "\u10d0", "Lisu": "\ua4d0", "Vai": "\ua500",
               "Bamum": "\ua6a0", "Tangut": "\U00017000", "Nushu": "\U0001b170", "Adlam": "\U0001e900",
               "Mongolian": "\u1820", "Khmer": "\u1780", "Syriac": "\u0710", "Thaana": "\u0780", "Nko": "\u07c0",
               "Samaritan": "\u0800", "Tibetan": "\u0f40", "Hangul": "\u1100", "Katakana": "\u3300", "CJK": "\u32ff",
               "Ideograph": "\u3280", "Kangxi": "\u2f00", "Armenian": "\u0531", "Ethiopic": "\u1200", "Javanese": "\ua984",
               "Brahmi": "\U00011005", "Phoenician": "\U00010900", "Old": "\U000103a0", "Signwriting": "\U0001d800"}

    def test_scripts_the_old_denylist_missed_are_flagged(self):
        for label, ch in self.SAMPLES.items():
            got = eva.non_english_script(ch)
            self.assertIsNotNone(got, (label, hex(ord(ch)), unicodedata.name(ch, "")))

    def test_cherokee_and_yi_text_is_reported_with_its_script(self):
        for text, label in ((CHEROKEE, "Cherokee"), (YI, "Yi")):
            self.assertEqual({h[2] for h in eva.non_english_hits(text)}, {label})

    def test_greek_latin_and_symbols_stay_allowed(self):
        for ch in "\u00e9\u00fc\u00df\u03b1\u03a9\u03bc\u0394\u03c0\u2192\u00b5\u2713\U0001f600\u2115\u210e\u212b\u2135\u00b2"\
                  "\u2160\u2460\uff21\uff10\u0301\u1d00\u2800\u3002\u3001\u20ac\u221a":
            self.assertIsNone(eva.non_english_script(ch), (hex(ord(ch)), unicodedata.name(ch, "")))

    def test_every_assigned_letter_mark_or_number_is_flagged_unless_its_name_is_english_compatible(self):
        """Fail closed: a script this list has never heard of (also one a newer Unicode adds) is flagged, not passed."""
        with open(E.p("rules", "data", "scripts.json"), encoding="utf-8") as f:
            data = json.load(f)
        compatible, scripts = set(data["english_compatible_first_words"]), set(data["non_english_script_words"])
        checked = flagged = 0
        for cp in range(0x80, 0x110000):
            ch = chr(cp); name = unicodedata.name(ch, "")
            if not name or unicodedata.category(ch)[0] not in "LMN": continue
            checked += 1
            first = name.split(" ")[0]
            got = eva.non_english_script(ch)
            if first in compatible:
                words = set(name.replace("-", " ").split())
                if not words & scripts: self.assertIsNone(got, name)
            else:
                self.assertIsNotNone(got, name); flagged += 1
        self.assertGreater(checked, 100000); self.assertGreater(flagged, 100000)

    def test_latin_and_greek_characters_are_never_flagged(self):
        for cp in range(0x80, 0x110000):
            name = unicodedata.name(chr(cp), "")
            if name.startswith(("LATIN ", "GREEK ")) and "CYRILLIC" not in name:
                self.assertIsNone(eva.non_english_script(chr(cp)), name)

    def test_the_fail_closed_label_is_the_first_word_of_the_name(self):
        self.assertEqual(eva.non_english_script("\u13a0"), "Cherokee")
        self.assertEqual(eva.non_english_script("\ua000"), "Yi")

class TestScriptEndToEnd(E.SkillMixin, unittest.TestCase):
    def test_cherokee_in_skill_md_and_yi_in_a_script_fail(self):
        r = self.skill(body="%s\n" % CHEROKEE)
        self.assertIn("english-skill-md", ids(r)); self.assertIn("[Cherokee]", row(r, "english-skill-md")["reason"])
        self.assertEqual(r.verdict, "FAIL")
        shutil.rmtree(os.path.join(self.tmp, "word-count"))
        r = self.skill(files={"scripts/run.py": "# %s\nx = 1\n" % YI})
        self.assertIn("[Yi]", row(r, "english-bundled-files")["reason"]); self.assertEqual(r.verdict, "FAIL")

# ---------- P1-2: Hermes permissions fail closed ----------
class TestHermesFailsClosed(P.AgentBase, unittest.TestCase):
    def test_a_complete_agent_is_never_hermes_pass(self):
        d, r = self.agent()
        c = row(r, P.HERMES_ROW)
        self.assertEqual((c["status"], c["must"]), ("fail", True)); self.assertEqual(r.verdict, "FAIL")
        self.assertEqual(eva.parity_status(d)["hermes"]["status"], "fail")
        self.assertNotIn("hermes=pass", eva.parity_line(eva.parity_status(d)))
        self.assertTrue(any("Hermes" in x for x in eva.parity_violations(d)), eva.parity_violations(d))

    def test_it_fails_for_every_tool_list_and_cannot_be_satisfied_by_the_wrapper(self):
        for tools in ("[read_file]", "[glob, grep, read_file]", "[read_file, write_file]"):
            d, r = self.agent(md=P.AGENT_MD.replace("[glob, grep, read_file]", tools))
            self.assertEqual(row(r, P.HERMES_ROW)["status"], "fail", tools); shutil.rmtree(d)
        d, r = self.agent()
        E.wtext(os.path.join(d, "wrappers/hermes/config.yaml"), "tools:\n  - read_file\n")
        self.assertEqual(row(eva.check(d), P.HERMES_ROW)["status"], "fail")

    def test_the_other_tools_are_still_judged_on_their_own(self):
        d, r = self.agent()
        self.assertEqual({t: s["status"] for t, s in eva.parity_status(d).items()},
                         {"claude": "pass", "codex": "review", "opencode": "review", "hermes": "fail"})

# ---------- P1-3: review findings stop the loop and the live gates until a decision is recorded ----------
def matcher_doc(**check):
    doc = T.evals_doc(human=False)
    doc["evals"][0]["assertions"][0]["check"] = dict({"type": "regex", "pattern": KO}, **check)
    return doc

class TestReviewDecision(P.CliBase):
    def skill(self, doc=None, **kw):
        return T.make_skill(self.tmp, evals=doc if doc is not None else matcher_doc(), **kw)

    def test_review_items_name_the_matcher_and_the_codex_metadata(self):
        d = self.skill(extra_files={"agents/openai.yaml": "interface:\n  display_name: X\n"})
        items = eva.review_items(d)
        self.assertEqual(sorted(i.split(":")[0] for i in items), ["english-output-matchers", "parity-skill-codex"])
        clean = os.path.join(self.tmp, "clean"); os.makedirs(clean)
        self.assertEqual(eva.review_items(T.make_skill(clean, evals=T.evals_doc(human=False))), [])

    def test_validate_exits_3_until_a_decision_is_recorded(self):
        d = self.skill(); os.makedirs(self.ws, exist_ok=True)
        rc, out, _ = self.cli("validate", d, "--workspace", self.ws)
        self.assertEqual(rc, 3, out); self.assertIn("RULE REVIEW NOT RECORDED", out)
        rc, out, _ = self.cli("approve", d, "--workspace", self.ws, "--gate", "rule_review")
        self.assertEqual(rc, 0, out); self.assertIn("approved rule_review", out)
        rc, out, _ = self.cli("validate", d, "--workspace", self.ws)
        self.assertEqual(rc, 0, out); self.assertIn("rule review decision recorded", out)

    def test_a_clean_skill_has_nothing_to_approve(self):
        d = T.make_skill(self.tmp, evals=T.evals_doc(human=False)); os.makedirs(self.ws, exist_ok=True)
        rc, out, _ = self.cli("validate", d, "--workspace", self.ws); self.assertEqual(rc, 0, out)
        spec, _ = se.load_eval_set(d)
        with self.assertRaises(se.EvalError) as cm: se.approve("rule_review", spec, self.ws)
        self.assertIn("nothing to approve", str(cm.exception))

    def test_live_gate_refuses_until_recorded_and_the_decision_is_bound_to_the_findings(self):
        d = self.skill(); spec, problems = se.load_eval_set(d); os.makedirs(self.ws, exist_ok=True)
        self.assertEqual(problems, [])
        a = mock.Mock(cmd="plan", strict_budget=True)
        def messages():
            rep = []
            with mock.patch.object(se, "live_estimate", return_value={"unpriced_models": [], "total_usd": 0.0}):
                se.check_live_gates(a, spec, problems, self.ws, None, [], se.DEFAULT_TOKENS, {}, 100.0, report=rep)
            return [m for m in rep if m.startswith("rule review not recorded")]
        self.assertEqual(len(messages()), 1)
        with mock.patch.object(se, "live_estimate", return_value={"unpriced_models": [], "total_usd": 0.0}):
            with self.assertRaises(se.EvalError) as cm:
                se.check_live_gates(a, spec, problems, self.ws, None, [], se.DEFAULT_TOKENS, {}, 100.0)
        self.assertIn("rule review not recorded", str(cm.exception))
        se.approve("rule_review", spec, self.ws)
        self.assertEqual(messages(), [])
        doc = matcher_doc(pattern=KO + "x")
        se.wjson(os.path.join(d, "evals", "evals.json"), doc)
        spec, _ = se.load_eval_set(d)
        a_ = se.rule_review_problems(spec, self.ws); self.assertEqual(len(a_), 1)

    def test_plan_shows_the_review_state(self):
        d = self.skill(); os.makedirs(self.ws, exist_ok=True)
        rc, out, _ = self.cli("plan", d, "--workspace", self.ws)
        self.assertIn("rule review (English-only / parity needs-review findings): NOT RECORDED", out)

class TestRegisteredGoldenSkillKeepsItsMeaning(unittest.TestCase):
    """Every recorded approval of a copy of the registered golden csv-cleaner still matches its gate hash, through
    library functions only: standing order 3 forbids calling register or approve. The records predate signed approvals
    (H1), so each reads unsigned and none counts as approved. The registration decision itself is tested in
    tests/test_review_round3.py (TestRegistrationDecision)."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.tmp)
        for name in ("csv-cleaner", "csv-cleaner-workspace"):
            shutil.copytree(os.path.join(T.ROOT, "golden", "skills", name), os.path.join(self.tmp, name))
        self.spec, _ = se.load_eval_set(os.path.join(self.tmp, "csv-cleaner"))
        self.ws = os.path.join(self.tmp, "csv-cleaner-workspace")
        self.approvals = se.rjson(os.path.join(self.ws, "approvals.json"))

    def test_every_recorded_hash_still_matches_and_reads_unsigned(self):
        self.assertEqual(sorted(self.approvals), ["eval_set", "iteration-4", "registration", "requirements"])
        for gate, rec in self.approvals.items():
            self.assertEqual(se.gate_hash(gate, self.spec, self.ws), rec["sha256"], gate)
            self.assertEqual(se.approval_verdict(gate, self.spec, self.ws).status, "unsigned", gate)
            self.assertFalse(se.approval_ok(gate, self.spec, self.ws), gate)

class TestReviewStopsTheLoop(T.Base):
    def spec(self, **kw):
        kw.setdefault("evals", T.evals_doc(human=False))
        return T.Base.spec(self, **kw)

    def loop(self, builder, spec, **kw):
        kw.setdefault("max_iterations", 3)
        return se.loop(spec, self.ws, self.engines(regress=True)[:1], self.grader(), builder, runs=1, log=T.quiet, **kw)

    def edit_evals(self, spec, fn):
        fp = os.path.join(spec["skill_dir"], "evals", "evals.json")
        doc = se.rjson(fp); fn(doc); se.wjson(fp, doc)

    def test_a_korean_output_matcher_added_by_the_builder_stops_the_loop(self):
        """Matchers live in evals/evals.json, outside the builder's writable set (plan.md:501), so a builder that adds one
        stops the loop with builder-wrote-outside evals/evals.json before the review check runs (plan.md:502, :508).
        The review check still stops the loop after the builder call on the same matcher planted before the loop."""
        spec, _ = self.spec(); calls = []
        def builder(it, sd):
            calls.append(it)
            self.edit_evals(spec, lambda doc: doc["evals"][0]["assertions"][0]["check"].update(pattern=KO)); return 0
        r = self.loop(builder, spec)
        self.assertEqual((r["status"], r["iteration"], r["problems"]), ("builder-wrote-outside", 1, ["evals/evals.json"]))
        self.assertEqual(len(calls), 1); self.assertEqual(se.iterations(self.ws), [1])
        shutil.rmtree(self.ws); shutil.rmtree(spec["skill_dir"])
        spec, _ = self.spec(evals=matcher_doc()); calls = []
        r = self.loop(lambda it, sd: calls.append(it) or 0, spec)
        self.assertEqual((r["status"], r["iteration"]), ("builder-review-required", 1))
        self.assertIn("rule review not recorded", r["problems"][0]); self.assertIn("english-output-matchers", r["problems"][0])
        self.assertEqual(len(calls), 1); self.assertEqual(se.iterations(self.ws), [1])

    def test_a_recorded_decision_for_exactly_those_findings_lets_the_loop_continue(self):
        doc = matcher_doc()
        spec, _ = self.spec(evals=doc); os.makedirs(self.ws, exist_ok=True)
        se.approve("rule_review", spec, self.ws)
        def builder(it, sd):
            with open(os.path.join(sd, "SKILL.md"), "a", encoding="utf-8") as f: f.write("\nMore detail.\n")
            return 0
        r = self.loop(builder, spec, max_iterations=2)
        self.assertEqual(r, {"status": "needs-approval", "iteration": 2})

    def test_a_changed_matcher_invalidates_the_recorded_decision(self):
        """A builder edit of the matcher is a write to evals/evals.json, outside the writable set (plan.md:501): it stops
        the loop with builder-wrote-outside evals/evals.json (plan.md:502, :508). The same change made after the
        decision was recorded and before the loop still voids the decision at the review check after the builder call."""
        spec, _ = self.spec(evals=matcher_doc()); os.makedirs(self.ws, exist_ok=True)
        se.approve("rule_review", spec, self.ws)
        def builder(it, sd):
            self.edit_evals(spec, lambda doc: doc["evals"][0]["assertions"][0]["check"].update(pattern=KO + "!")); return 0
        r = self.loop(builder, spec)
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["evals/evals.json"]))
        shutil.rmtree(self.ws); shutil.rmtree(spec["skill_dir"])
        spec, _ = self.spec(evals=matcher_doc()); os.makedirs(self.ws, exist_ok=True)
        se.approve("rule_review", spec, self.ws)
        self.edit_evals(spec, lambda doc: doc["evals"][0]["assertions"][0]["check"].update(pattern=KO + "!"))
        r = self.loop(lambda it, sd: 0, spec)
        self.assertEqual((r["status"], r["iteration"]), ("builder-review-required", 1))

    def test_the_precheck_gate_set_also_refuses(self):
        spec, _ = self.spec(evals=matcher_doc())
        a = mock.Mock(cmd="plan", strict_budget=True); rep = []
        with mock.patch.object(se, "live_estimate", return_value={"unpriced_models": [], "total_usd": 0.0}):
            se.check_live_gates(a, spec, [], self.ws, None, [], se.DEFAULT_TOKENS, {}, 100.0, report=rep)
        self.assertTrue(any(m.startswith("rule review not recorded") for m in rep), rep)

# ---------- P2-1: only the fields that match run output are exempt ----------
class TestMatcherFields(E.SkillMixin, unittest.TestCase):
    def evals(self, check):
        return ER.jtext(ER.evals_with(assertions=[{"text": "t", "layer": "static", "check": check}]))

    def test_pattern_input_regex_and_rows_are_matchers(self):
        for check in ({"type": "regex", "pattern": KO}, {"type": "tool_call", "tool": "Bash", "input_regex": KO},
                      {"type": "csv_rows", "path": "out.csv", "rows": [["a", KO]]}):
            r = self.skill(evals=self.evals(check))
            self.assertEqual([c["id"] for c in r.needs_review], ["english-output-matchers"], check)
            self.assertNotIn("english-evals", ids(r), check)
            shutil.rmtree(os.path.join(self.tmp, "word-count"))

    def test_paths_tool_names_fixtures_and_keys_are_configuration_and_fail(self):
        for check in ({"type": "file_exists", "path": KO + ".txt"}, {"type": "tool_call", "tool": KO},
                      {"type": "file_equals_fixture", "path": "o.csv", "fixture": "evals/files/" + KO},
                      {"type": "csv_cleaned", "path": KO, "fixture": "x", "data_rows": 1},
                      {"type": "regex", "pattern": "a", KO: 1}, {"type": "csv_rows", "path": KO, "rows": [["a"]]}):
            r = self.skill(evals=self.evals(check))
            self.assertIn("english-evals", ids(r), check); self.assertEqual(r.verdict, "FAIL", check)
            self.assertEqual(r.needs_review, [], check)
            shutil.rmtree(os.path.join(self.tmp, "word-count"))

    def test_validate_problem_for_a_korean_path_but_only_a_note_for_a_pattern(self):
        probs, review = eva.english_eval_problems(
            T.make_skill(self.tmp, evals=matcher_doc()))
        self.assertEqual(probs, []); self.assertEqual(len(review), 1)
        doc = T.evals_doc(human=False); doc["evals"][1]["assertions"][0]["check"] = {"type": "tool_call", "tool": KO}
        probs, review = eva.english_eval_problems(T.make_skill(self.tmp, evals=doc))
        self.assertEqual((len(probs), review), (1, []))

# ---------- P2-2: tools are compared by name ----------
class TestToolNames(P.AgentBase, unittest.TestCase):
    ONE = P.AGENT_MD.replace("[search, read]", "[read]")

    def claude(self, tools, md=None):
        return self.agent(md=md or self.ONE, replace={"wrappers/claude/code-scout.md": lambda t: __import__("re").sub(
            r"tools: .*", "tools: " + tools, t)})

    def test_grep_cannot_stand_in_for_read_file(self):
        d, r = self.claude("Grep")
        self.assertIn("parity-permissions-claude", ids(r)); self.assertIn("compared by capability class", row(r, "parity-permissions-claude")["reason"])
        self.assertIn("missing ['read']", row(r, "parity-permissions-claude")["reason"])

    def test_established_aliases_and_case_are_accepted(self):
        for tools in ("Read", "read_file", "READ"):
            d, r = self.claude(tools); self.assertNotIn("parity-permissions-claude", ids(r), tools); shutil.rmtree(d)

    def test_extra_and_missing_tools_both_fail(self):
        d, r = self.claude("Read, Grep"); self.assertIn("parity-permissions-claude", ids(r)); shutil.rmtree(d)
        d, r = self.claude("Read", md=P.AGENT_MD)
        self.assertIn("parity-permissions-claude", ids(r))

    def test_aliases_are_an_explicit_table(self):
        from engine.primitives import parity as PR
        self.assertEqual(PR.capabilities(["Read", "read_file", "TodoWrite"]), ({"read"}, []))
        self.assertEqual(PR.capabilities(["Skill"]), ({"skill"}, []))
        self.assertNotEqual(PR.capabilities(["Grep"]), PR.capabilities(["read_file"]))
        self.assertEqual(PR.capabilities(["list_dir"]), PR.capabilities(["Glob"]))

# ---------- P2-3: gates after every builder call ----------
class TestBuilderGatesAfterEveryCall(T.Base):
    def spec(self):
        return T.Base.spec(self, evals=T.evals_doc(human=False))

    def loop(self, builder, spec):
        return se.loop(spec, self.ws, self.engines(regress=True)[:1], self.grader(), builder, runs=1, log=T.quiet, max_iterations=3)

    def write(self, spec, text, rel="SKILL.md"):
        with open(os.path.join(spec["skill_dir"], rel), "a", encoding="utf-8") as f: f.write(text)

    def test_korean_content_and_nonzero_exit_reports_the_violation_and_keeps_the_return_code(self):
        spec, _ = self.spec()
        r = self.loop(lambda it, sd: self.write(spec, "\n%s\n" % KO) or 9, spec)
        self.assertEqual((r["status"], r["returncode"]), ("builder-non-english", 9)); self.assertIn("SKILL.md:", r["problems"][0])

    def test_a_builder_that_raises_after_writing_is_scanned_too(self):
        spec, _ = self.spec()
        def builder(it, sd):
            self.write(spec, "\n%s\n" % KO); raise se.EvalError("usage could not be parsed")
        r = self.loop(builder, spec)
        self.assertEqual(r["status"], "builder-non-english"); self.assertEqual(r["error"], "usage could not be parsed")
        self.assertNotIn("returncode", r)

    def test_parity_violation_with_nonzero_exit_is_reported(self):
        spec, _ = self.spec()
        r = self.loop(lambda it, sd: self._break_name(spec) or 3, spec)
        self.assertEqual((r["status"], r["returncode"]), ("builder-parity-missing", 3))

    def _break_name(self, spec):
        fp = os.path.join(spec["skill_dir"], "SKILL.md")
        with open(fp, encoding="utf-8") as f: t = f.read()
        with open(fp, "w", encoding="utf-8") as f: f.write(t.replace("name: branch-setup", "name: Branch_Setup"))

    def test_a_clean_failed_builder_is_still_builder_failed_with_its_code_or_error(self):
        spec, _ = self.spec()
        r = self.loop(lambda it, sd: 7, spec); self.assertEqual(r, {"status": "builder-failed", "iteration": 1, "returncode": 7})
        shutil.rmtree(self.ws, ignore_errors=True)
        def boom(it, sd): raise se.EvalError("boom")
        self.assertEqual(self.loop(boom, spec), {"status": "builder-failed", "iteration": 1, "error": "boom"})

# ---------- P2-4: binary detection ----------
class TestBinaryDetection(E.SkillMixin, unittest.TestCase):
    def check_with(self, rel, data):
        self.skill()
        d = os.path.join(self.tmp, "word-count"); fp = os.path.join(d, *rel.split("/")); os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f: f.write(data)
        return eva.check(d)

    def test_known_signature_without_nul_is_binary(self):
        r = self.check_with("assets/logo.dat", b"\x89PNG\r\n\x1a\n" + bytes(range(128, 250)) * 5)
        self.assertNotIn("english-bundled-files", ids(r)); self.assertIn("1 binary skipped", row(r, "english-bundled-files")["reason"])
        for rel, data in (("assets/a.bin2", b"\xff\xd8\xff\xe0" + bytes(range(128, 200))), ("assets/b.x", b"%PDF-1.4\n\xe2\xe3\xcf\xd3\n"),
                          ("assets/c.x", b"\x1f\x8b\x08\x00" + bytes(range(128, 200)))):
            shutil.rmtree(os.path.join(self.tmp, "word-count"))
            r = self.check_with(rel, data); self.assertNotIn("english-bundled-files", ids(r), rel)

    def test_known_extension_without_signature_or_nul_is_binary_when_it_does_not_decode(self):
        r = self.check_with("assets/photo.png", bytes(range(128, 256)) * 3)
        self.assertNotIn("english-bundled-files", ids(r)); self.assertIn("1 binary skipped", row(r, "english-bundled-files")["reason"])

    def test_unknown_undecodable_data_still_fails_with_or_without_nul(self):
        for data in (KO.encode("euc-kr"), bytes(range(128, 256)), b"\x00\x01" + KO.encode("utf-16-le")[:-1] + b"\xff"):
            r = self.check_with("references/blob.data", data)
            self.assertIn("english-bundled-files", ids(r), data[:6]); self.assertIn("not a supported binary format", row(r, "english-bundled-files")["reason"])
            shutil.rmtree(os.path.join(self.tmp, "word-count"))

    def test_text_with_a_binary_extension_is_still_scanned(self):
        r = self.check_with("assets/notes.png", ("hello\n%s\n" % KO).encode("utf-8"))
        self.assertIn("english-bundled-files", ids(r)); self.assertIn("assets/notes.png:2", row(r, "english-bundled-files")["reason"])

    def test_text_that_starts_like_a_binary_format_is_still_scanned(self):
        for head in (b"true\n", b"ID3 notes\n", b"BZh\n", b"%PDF-1.4\n", b"OTTO\n", b"GIF89a\n"):
            r = self.check_with("references/n.dat", head + KO.encode("utf-8") + b"\n")
            self.assertIn("references/n.dat:2", row(r, "english-bundled-files")["reason"], head)
            shutil.rmtree(os.path.join(self.tmp, "word-count"))

    def test_utf8_text_containing_a_nul_is_scanned_not_skipped(self):
        r = self.check_with("references/n.txt", b"ok\x00\n" + KO.encode("utf-8") + b"\n")
        self.assertIn("references/n.txt:2", row(r, "english-bundled-files")["reason"])

# ---------- P2-5: scenario file names ----------
class TestScenarioFileNames(P.AgentBase, unittest.TestCase):
    def test_a_non_english_scenario_file_name_fails(self):
        d, r = self.agent()
        self.assertNotIn("english-scenarios", ids(r))
        E.wtext(os.path.join(d, "scenarios", KO + ".json"), json.dumps(P.S1))
        r = eva.check(d)
        self.assertIn("english-scenarios", ids(r)); self.assertIn("scenarios/%s.json (file name) [Hangul]" % KO, row(r, "english-scenarios")["reason"])
        self.assertEqual(r.verdict, "FAIL")

    def test_other_files_under_scenarios_count_too(self):
        d, r = self.agent(); os.makedirs(os.path.join(d, "scenarios", "sub")); E.wtext(os.path.join(d, "scenarios", "sub", CHEROKEE + ".txt"), "x")
        self.assertIn("(file name) [Cherokee]", row(eva.check(d), "english-scenarios")["reason"])

# ---------- parity-1: unverified openai.yaml fields are a review, never a pass ----------
class TestCodexUnverifiedFields(E.SkillMixin, unittest.TestCase):
    def yaml(self, text):
        return self.skill(files={"agents/openai.yaml": text})

    def test_unrecognized_fields_give_review_and_never_codex_pass(self):
        for text, fields in (("interface:\n  display_name: Word Count\n", "interface.display_name"),
                             ("policy:\n  allow_implicit_invocation: false\n  other: 1\n", "policy.other"),
                             ("dependencies:\n  tools: [a]\n", "dependencies.tools"), ("unknown_key: x\n", "unknown_key")):
            r = self.yaml(text); c = row(r, "parity-skill-codex")
            self.assertEqual(c["status"], "needs-review", text); self.assertIn(fields, c["reason"]); self.assertEqual(r.verdict, "REVIEW")
            self.assertNotIn("parity-skill-codex", ids(r)); self.assertNotIn("satisfies the Codex CLI rules", c["reason"])
            self.assertEqual(eva.parity_status(os.path.join(self.tmp, "word-count"))["codex"]["status"], "review", text)
            self.assertIn("codex=review", eva.parity_line(eva.parity_status(os.path.join(self.tmp, "word-count"))))
            self.assertTrue(any(i.startswith("parity-skill-codex") for i in eva.review_items(os.path.join(self.tmp, "word-count"))))
            shutil.rmtree(os.path.join(self.tmp, "word-count"))

    def test_only_the_verified_field_passes(self):
        for text in ("policy:\n  allow_implicit_invocation: true\n", "# comment only\n", ""):
            r = self.yaml(text); self.assertEqual(r.verdict, "PASS", (text, r.failed, r.needs_review))
            self.assertIn("agents/openai.yaml", row(r, "parity-skill-codex")["reason"]); shutil.rmtree(os.path.join(self.tmp, "word-count"))

    def test_a_real_problem_is_still_a_fail_not_a_review(self):
        r = self.yaml("policy: strict\ninterface:\n  x: 1\n")
        self.assertIn("parity-skill-codex", ids(r))

# ---------- parity-2: escapes only where the format interprets them ----------
class TestEscapeContexts(unittest.TestCase):
    ESC = "\\uAC00"

    def hits(self, text, decode, **kw):
        return eva.english_text_findings(text, "f", decode=decode, **kw)

    def test_toml_literal_strings_are_not_decoded_but_basic_strings_are(self):
        e = self.ESC
        self.assertEqual(self.hits("a = '%s'\n" % e, "toml"), [])
        self.assertEqual(self.hits("a = '''\n%s\n'''\n" % e, "toml"), [])
        self.assertEqual(self.hits("a = 1 # %s\n" % e, "toml"), [])
        self.assertEqual(self.hits('a = "\\\\%s"\n' % e[1:], "toml"), [])
        self.assertEqual(len(self.hits('a = "%s"\n' % e, "toml")), 1)
        self.assertEqual(len(self.hits('a = """\nx %s\n"""\n' % e, "toml")), 1)

    def test_yaml_only_double_quoted_scalars_are_decoded(self):
        e = self.ESC
        for ok in ("a: '%s'" % e, "a: %s" % e, "a: x # \"%s\"" % e, "- '%s'" % e, "a: |\n  \"%s\"\nb: 1" % e, "a: >\n  %s" % e):
            self.assertEqual(self.hits(ok, "yaml"), [], ok)
        for bad in ('a: "%s"' % e, '- "%s"' % e, 'a: ["%s", x]' % e, 'a: {k: "%s"}' % e, 'a: |\n  x\nb: "%s"' % e):
            self.assertEqual(len(self.hits(bad, "yaml")), 1, bad)

    def test_frontmatter_follows_the_yaml_rule_and_the_body_never_decodes(self):
        e = self.ESC
        md = "---\nname: x\ndescription: 'ASCII %s'\n---\nbody %s\n" % (e, e)
        self.assertEqual(eva.english_text_findings(md, "SKILL.md", frontmatter=True), [])
        md = "---\nname: x\ndescription: \"%s\"\n---\nbody\n" % e
        f = eva.english_text_findings(md, "SKILL.md", frontmatter=True)
        self.assertEqual(len(f), 1); self.assertIn("SKILL.md:3 (frontmatter) [Hangul]", f[0])

    def test_invalid_json_is_still_scanned_strictly(self):
        self.assertEqual(len(eva.english_json_findings('{"a": \\uAC00 oops', "x.json")[0]), 1)

class TestEscapeContextsEndToEnd(E.SkillMixin, unittest.TestCase):
    def test_single_quoted_frontmatter_value_with_an_escape_text_is_ascii_and_passes(self):
        raw = E.GOOD_FM.format(name="word-count", extra="license: '\\uAC00'\n", body="")
        r = self.skill(raw=raw)
        self.assertNotIn("english-skill-md", ids(r))
        shutil.rmtree(os.path.join(self.tmp, "word-count"))
        r = self.skill(raw=E.GOOD_FM.format(name="word-count", extra='license: "\\uAC00"\n', body=""))
        self.assertIn("english-skill-md", ids(r))

class TestWrapperEscapes(P.AgentBase, unittest.TestCase):
    def test_toml_literal_escape_text_passes_and_basic_string_fails(self):
        d, r = self.agent(replace={"wrappers/codex/code-scout.toml": lambda t: t + "model = 'x\\uAC00'\n"})
        self.assertNotIn("english-wrappers", ids(r))
        shutil.rmtree(d)
        d, r = self.agent(replace={"wrappers/codex/code-scout.toml": lambda t: t + 'model = "x\\uAC00"\n'})
        self.assertIn("english-wrappers", ids(r))

# ---------- reporting-3: coverage uses the summarize() criterion ----------
class TestCoverageCriterion(T.Base):
    def test_collected_but_ungraded_runs_are_pending_not_run(self):
        c = se.tool_coverage(["claude", "codex"], {"claude": 0, "codex": 2}, {"claude": 3, "codex": 2})
        self.assertEqual(c["claude"]["status"], se.PENDING); self.assertEqual((c["claude"]["graded_runs"], c["claude"]["pending_runs"]), (0, 3))
        self.assertEqual((c["codex"]["status"], c["codex"]["pending_runs"]), ("run", 0))
        self.assertEqual(se.tool_coverage(["claude"], {"claude": 0}, {"claude": 0})["claude"]["status"], se.NOT_GRADED)
        self.assertNotIn("pass", c["claude"]["status"].replace("pending", ""))
        self.assertIn(T.coverage_missing("claude", se.PENDING), se.compute_gate({"tool_coverage": c})["problems"])

    def test_the_benchmark_counts_pass_rate_none_runs_as_pending(self):
        spec, problems = self.spec(); n = se.run_iteration(spec, self.ws, self.engines(), runs=1, log=T.quiet)
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=1, log=T.quiet)
        it = os.path.join(self.ws, "iteration-%d" % n); touched = 0
        for dp, _, fn in os.walk(it):
            if "grading.json" in fn:
                fp = os.path.join(dp, "grading.json"); g = se.rjson(fp)
                if os.sep + "claude" + os.sep in fp and g.get("summary") is not None:
                    g["summary"]["pass_rate"] = None; se.wjson(fp, g); touched += 1
        self.assertGreater(touched, 0)
        bench = se.build_benchmark(spec, self.ws, n)
        cov = bench["tool_coverage"]
        self.assertEqual(cov["claude"]["status"], se.PENDING); self.assertEqual(cov["claude"]["graded_runs"], 0)
        self.assertEqual(cov["claude"]["pending_runs"], touched); self.assertEqual(cov["codex"]["status"], "run")
        gate = se.compute_gate(bench)
        self.assertIn(T.coverage_missing("claude", se.PENDING), gate["problems"])
        md = se.comparison_md(bench, gate, n)
        self.assertIn("+%d pending" % touched, md)


# ---------- reporting-4: the index keeps english=review ----------
class TestIndexReviewState(E.SkillMixin, unittest.TestCase):
    def test_a_matcher_review_is_english_review_not_pass(self):
        doc = ER.evals_with(assertions=[{"text": "t", "layer": "static", "check": {"type": "regex", "pattern": KO}}])
        self.skill(evals=doc)
        self.skill(name="plain-skill", evals={"skill_name": "plain-skill", "evals": E.GOOD_EVALS})
        self.skill(name="ko-skill", body=KO + "\n", evals={"skill_name": "ko-skill", "evals": E.GOOD_EVALS})
        reg, md = eva.build_index(self.tmp)
        by = {e["name"]: e for e in reg}
        self.assertEqual((by["word-count"]["english"], by["word-count"]["verdict"]), ("review", "REVIEW"))
        self.assertEqual((by["plain-skill"]["english"], by["ko-skill"]["english"]), ("pass", "fail"))
        self.assertTrue(any("| REVIEW |" in ln and "| review |" in ln for ln in md.splitlines()), md)

    def test_fail_beats_review(self):
        doc = ER.evals_with(prompt=KO, assertions=[{"text": "t", "layer": "static", "check": {"type": "regex", "pattern": KO}}])
        r = self.skill(evals=doc)
        self.assertEqual(eva.english_state(r), "fail")

if __name__ == "__main__":
    unittest.main()
