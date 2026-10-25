"""English-only rule (owner decision 2026-10-19): static checks in eva.py, validate in skill_eval.py and the loop builder
gate. Design: notes/english-rule-plan.md. Everything is mocked: subprocess is patched to fail for the whole module, the
builder is a plain function, no claude/codex call, no network. This file stays ASCII: non-English text is built from
escapes (KO = Hangul, ZH = CJK ideographs, JA = kana, RU = Cyrillic)."""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva, skill_eval as se
import test_eva as E
import test_skill_eval as T
import test_multi_tool_parity as P

KO = "\ud55c\uad6d\uc5b4 \ud14d\uc2a4\ud2b8"
ZH = "\u4e2d\u6587\u6587\u672c"
JA = "\u3053\u3093\u306b\u3061\u306f"
RU = "\u0420\u0443\u0441\u0441\u043a\u0438\u0439"
FINE = "caf\u00e9 na\u00efve \u2192 \u201cquoted\u201d \u2014 \u03bcs \u0394t \u221a2 \u2713 \u00b5s \U0001F600 \u3001"
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def jtext(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1)

def line_of(path, needle):
    with open(path, encoding="utf-8") as f:
        for i, ln in enumerate(f.read().split("\n"), 1):
            if needle in ln: return i
    raise AssertionError("%r not in %s" % (needle, path))

def failed_ids(r):
    return {c["id"] for c in r.failed}

def reason(r, cid):
    return next(c["reason"] for c in r.checks if c["id"] == cid)

def evals_with(**case):
    base = {"id": 1, "prompt": "p1", "expected_output": "o1"}
    base.update(case)
    return {"skill_name": "word-count", "evals": [base, {"id": 2, "prompt": "p2", "expected_output": "o2"},
                                                   {"id": 3, "prompt": "p3", "expected_output": "o3"}]}

class TestScanner(unittest.TestCase):
    def test_non_english_scripts_are_flagged(self):
        for label, text in (("Hangul", "\uac00"), ("Hangul", "\u1100"), ("Hangul", "\u3131"), ("Hangul", "\uffa1"),
                            ("Hangul", "\u326e"), ("Hangul", "\u3164"), ("CJK", "\u4e2d"), ("CJK", "\U00020000"),
                            ("Hiragana", "\u3042"), ("Katakana", "\u30a2"), ("Cyrillic", "\u0416"),
                            ("Arabic", "\u0627"), ("Hebrew", "\u05d0"), ("Thai", "\u0e01"), ("Devanagari", "\u0905")):
            self.assertEqual(eva.non_english_script(text), label, repr(text))

    def test_plain_unicode_is_fine(self):
        self.assertEqual(eva.non_english_hits(FINE), [])
        for ch in "\u3001\u3000\uff01\u060c\u00e9\u00fc\u00df\u2192\u00b5\u03b1\u2713\U0001F600":
            self.assertIsNone(eva.non_english_script(ch), repr(ch))

    def test_escaped_hangul_is_decoded_but_escaped_backslash_is_not(self):
        self.assertEqual(eva._decode_escapes('"\\ud55c"'), '"\ud55c"')
        self.assertEqual(eva._decode_escapes("\\ud840\\udc00"), "\U00020000")
        self.assertEqual(eva._decode_escapes("\\\\uac00"), "\\\\uac00")
        self.assertEqual(eva.english_text_findings('x: "\\uac00"', "f.md", decode=True)[0][:8], "f.md:1 [")

class TestSkillMd(E.SkillMixin, unittest.TestCase):
    def fm(self, desc="Counts words in one text file. Use when the user wants a word count.", extra="", body=""):
        return E.GOOD_FM.format(name="word-count", extra=extra, body=body).replace(
            "description: Counts words in one text file. Use when the user wants a word count.", "description: " + desc)

    def test_english_skill_passes_with_the_new_rows(self):
        r = self.skill(body=FINE + "\n")
        self.assertEqual(r.verdict, "PASS", r.failed)
        self.assertEqual(self.status(r, "english-skill-md"), True)
        self.assertIsNone(self.status(r, "english-bundled-files")); self.assertIsNone(self.status(r, "english-output-matchers"))

    def test_description_in_frontmatter_fails_with_file_line_and_excerpt(self):
        r = self.skill(raw=self.fm(desc="Counts words. Use when the user wants a word count. %s" % KO))
        self.assertEqual(r.verdict, "FAIL"); self.assertIn("english-skill-md", failed_ids(r))
        self.assertIn("english-skill-md", {c["id"] for c in r.critical_failed})
        why = reason(r, "english-skill-md")
        self.assertIn("SKILL.md:3 (frontmatter) [Hangul]", why); self.assertIn(KO, why)

    def test_other_frontmatter_field_values_fail(self):
        for extra in ("license: %s\n" % KO, "compatibility: %s\n" % KO, "metadata:\n  author: %s\n" % KO,
                      "allowed-tools: %s\n" % KO):
            r = self.skill(extra=extra)
            self.assertIn("english-skill-md", failed_ids(r), extra)
            self.assertIn("(frontmatter)", reason(r, "english-skill-md"), extra)

    def test_yaml_escaped_hangul_in_frontmatter_fails(self):
        r = self.skill(raw=self.fm(desc='"Counts words. Use when the user wants a word count. \\uac00\\ub098"'))
        self.assertIn("english-skill-md", failed_ids(r))

    def test_body_fails_with_line_number(self):
        r = self.skill(body="\nline two\n%s\n" % KO)
        why = reason(r, "english-skill-md"); self.assertEqual(r.verdict, "FAIL")
        n = line_of(os.path.join(self.tmp, "word-count", "SKILL.md"), KO)
        self.assertIn("SKILL.md:%d (body) [Hangul]" % n, why)

    def test_cjk_kana_and_cyrillic_also_fail(self):
        for text, label in ((ZH, "CJK"), (JA, "Hiragana"), (RU, "Cyrillic")):
            r = self.skill(body=text + "\n")
            self.assertIn("[%s]" % label, reason(r, "english-skill-md"), text)

    def test_more_than_five_lines_are_summarised(self):
        r = self.skill(body="\n".join(KO for _ in range(8)) + "\n")
        self.assertIn("(+3 more)", reason(r, "english-skill-md"))

class TestBundledFiles(E.SkillMixin, unittest.TestCase):
    def test_references_assets_scripts_each_fail_on_their_own_lines(self):
        files = {"references/guide.md": "# Guide\n%s\n" % KO, "assets/template.txt": "ok\n%s\n" % KO,
                 "scripts/run.py": "# %s\nx = 1\n" % KO}
        r = self.skill(files=files); why = reason(r, "english-bundled-files")
        self.assertEqual(r.verdict, "FAIL"); self.assertIn("english-bundled-files", failed_ids(r))
        for frag in ("references/guide.md:2 [Hangul]", "assets/template.txt:2 [Hangul]", "scripts/run.py:1 [Hangul]"):
            self.assertIn(frag, why)

    def test_script_docstring_and_string_literal_count(self):
        src = 'def f():\n    """%s"""\n    print("%s")\n' % (KO, KO)
        why = reason(self.skill(files={"scripts/run.py": src}), "english-bundled-files")
        self.assertIn("scripts/run.py:2", why); self.assertIn("scripts/run.py:3", why)

    def test_script_with_escapes_instead_of_literals_passes(self):
        src = 'import re\nHANGUL = re.compile("[\\uac00-\\ud7a3]")\n'
        r = self.skill(files={"scripts/run.py": src})
        self.assertEqual(r.verdict, "PASS", r.failed)

    def test_english_bundled_files_pass(self):
        r = self.skill(files={"references/guide.md": "# Guide\n" + FINE + "\n", "scripts/run.py": "print('hi')\n"})
        self.assertEqual(r.verdict, "PASS", r.failed); self.assertTrue(self.status(r, "english-bundled-files"))

    def test_nested_dirs_are_scanned_and_pycache_is_skipped(self):
        r = self.skill(files={"references/sub/deep.md": KO + "\n", "scripts/__pycache__/x.txt": KO + "\n"})
        why = reason(r, "english-bundled-files")
        self.assertIn("references/sub/deep.md:1", why); self.assertNotIn("__pycache__", why)

    def test_binary_asset_is_skipped_but_counted(self):
        d = os.path.join(self.tmp, "word-count")
        r = self.skill(); os.makedirs(os.path.join(d, "assets"))
        with open(os.path.join(d, "assets", "logo.png"), "wb") as f: f.write(b"\x89PNG\r\n\x00\x00" + KO.encode("utf-8"))
        r = eva.check(d); self.assertEqual(r.verdict, "PASS", r.failed)
        self.assertIn("1 binary skipped", reason(r, "english-bundled-files"))

    def test_non_utf8_text_cannot_hide_as_binary(self):
        d = os.path.join(self.tmp, "word-count"); self.skill(); os.makedirs(os.path.join(d, "references"))
        with open(os.path.join(d, "references", "legacy.txt"), "wb") as f: f.write(KO.encode("euc-kr"))
        r = eva.check(d)
        self.assertIn("english-bundled-files", failed_ids(r)); self.assertIn("not valid UTF-8", reason(r, "english-bundled-files"))

    def test_utf16_text_is_decoded_and_scanned(self):
        d = os.path.join(self.tmp, "word-count"); self.skill(); os.makedirs(os.path.join(d, "assets"))
        with open(os.path.join(d, "assets", "u16.txt"), "wb") as f: f.write(("x\n%s\n" % KO).encode("utf-16"))
        self.assertIn("assets/u16.txt:2", reason(eva.check(d), "english-bundled-files"))

    def test_non_english_file_name_fails(self):
        r = self.skill(files={"references/%s.md" % KO: "ok\n"})
        self.assertIn("(file name) [Hangul]", reason(r, "english-bundled-files"))

class TestEvalsFiles(E.SkillMixin, unittest.TestCase):
    def evals_text(self, **case):
        return jtext(evals_with(**case))

    def test_each_prose_field_fails_with_its_line(self):
        cases = (("prompt", {"prompt": "do it: " + KO}), ("expected_output", {"expected_output": KO}),
                 ("assertions", {"assertions": [KO]}), ("expectations", {"expectations": [KO]}),
                 ("assertion object text", {"assertions": [{"text": KO, "layer": "llm"}]}),
                 ("pressure.context", {"pressure": {"kinds": ["time"], "context": KO}}),
                 ("files name", {"files": ["evals/files/%s.csv" % KO]}))
        for label, case in cases:
            text = self.evals_text(**case)
            r = self.skill(evals=text); why = reason(r, "english-evals")
            self.assertEqual(r.verdict, "FAIL", label); self.assertIn("english-evals", failed_ids(r), label)
            n = line_of(self._evals_path(), KO)
            self.assertIn("evals/evals.json:%d [Hangul]" % n, why, label)

    def _evals_path(self):
        return os.path.join(self.tmp, "word-count", "evals", "evals.json")

    def test_ensure_ascii_escaped_hangul_is_still_found(self):
        text = json.dumps(evals_with(prompt=KO), indent=1)
        self.assertNotIn(KO, text)
        r = self.skill(evals=text)
        self.assertIn("english-evals", failed_ids(r)); self.assertIn("evals/evals.json:", reason(r, "english-evals"))

    def test_trigger_queries_fail(self):
        trig = [{"query": "q%d" % i, "should_trigger": i % 2 == 0} for i in range(20)]
        trig[3]["query"] = KO
        r = self.skill(files={"evals/trigger_evals.json": jtext(trig)})
        self.assertIn("english-evals", failed_ids(r))
        self.assertIn("evals/trigger_evals.json:%d [Hangul]" % line_of(
            os.path.join(self.tmp, "word-count", "evals", "trigger_evals.json"), KO), reason(r, "english-evals"))

    def test_english_evals_and_triggers_pass(self):
        trig = [{"query": "q%d %s" % (i, FINE), "should_trigger": i % 2 == 0} for i in range(20)]
        r = self.skill(evals=self.evals_text(prompt="do it " + FINE), files={"evals/trigger_evals.json": jtext(trig)})
        self.assertEqual(r.verdict, "PASS", r.failed); self.assertTrue(self.status(r, "english-evals"))

    def test_evals_files_directory_is_exempt(self):
        r = self.skill(files={"evals/files/input.csv": "name\n%s\n" % KO, "evals/files/%s.txt" % KO: KO,
                              "evals/evals.json.bak-1": KO})
        self.assertEqual(r.verdict, "PASS", r.failed)
        self.assertNotIn("evals/files", reason(r, "english-evals"))

    def test_invalid_json_is_scanned_as_plain_text(self):
        r = self.skill(evals="{not json %s" % KO)
        self.assertIn("evals/evals.json:1", reason(r, "english-evals"))

    def test_output_matcher_is_review_not_fail(self):
        check = {"type": "regex", "pattern": KO}
        case = {"assertions": [{"text": "final names the fix", "layer": "static", "check": check}]}
        r = self.skill(evals=self.evals_text(**case))
        self.assertEqual(r.verdict, "REVIEW", (r.failed, r.needs_review))
        self.assertEqual([c["id"] for c in r.needs_review], ["english-output-matchers"])
        self.assertTrue(self.status(r, "english-evals"))
        self.assertIn("evals/evals.json:%d" % line_of(self._evals_path(), KO), reason(r, "english-output-matchers"))

    def test_prose_next_to_a_matcher_still_fails(self):
        case = {"assertions": [{"text": KO, "layer": "static", "check": {"type": "regex", "pattern": KO}}]}
        r = self.skill(evals=self.evals_text(**case))
        self.assertEqual(r.verdict, "FAIL"); self.assertIn("english-evals", failed_ids(r))
        self.assertEqual([c["id"] for c in r.needs_review], ["english-output-matchers"])

    def test_matcher_with_escapes_needs_no_review(self):
        case = {"assertions": [{"text": "answer is Korean", "layer": "static",
                                "check": {"type": "regex", "pattern": "[\\uac00-\\ud7a3]"}}]}
        r = self.skill(evals=json.dumps(evals_with(**case), indent=1))
        self.assertEqual(r.verdict, "PASS", (r.failed, r.needs_review))

class TestAgents(E.TmpMixin, unittest.TestCase):
    AGENT = ("---\nname: code-scout\ndescription: Reads a repository read-only and explains it. Use when the user asks for an overview.\n"
             "goal: Explain the repository structure with file and line evidence.\ncapabilities: [search, read]\n"
             "model_policy: judgment\nsuccess_criteria:\n  - '{\"type\": \"file_line_citations\", \"min\": 2}'\n"
             "  - '{\"type\": \"llm\", \"text\": \"No file changes (read-only).\"}'\n"
             "targets: [claude, codex, opencode, hermes]\n---\n# Code Scout\n"
             "## Tools\n- search: map the structure and find places / read: confirm lines\n"
             "## Memory & Handoff\nRemember the topic of earlier turns and hand open questions to the next agent.\n")
    S1 = {"id": "overview", "turns": [{"user": "Give me an overview of this repo"}], "expected_tools": ["search"],
          "must_include": ["tests"], "expected_outcome": [{"type": "file_line_citations", "min": 2}]}
    S2 = {"id": "drill", "turns": [{"user": "Give me an overview"}, {"user": "Explain the test folder you just mentioned"}],
          "expected_tools": ["search", "read"], "must_include": ["test"],
          "expected_outcome": [{"type": "llm", "text": "Explains the test folder from the earlier turn."}, {"type": "no_writes"}]}

    def agent(self, md=None, s1=None, s2=None, raw1=None):
        d = os.path.join(self.tmp, "code-scout"); os.makedirs(os.path.join(d, "scenarios"))
        E.wtext(os.path.join(d, "AGENT.md"), md or self.AGENT)
        E.wtext(os.path.join(d, "scenarios", "01.json"), raw1 or jtext(s1 or self.S1))
        E.wtext(os.path.join(d, "scenarios", "02.json"), jtext(s2 or self.S2))
        P.write_wrappers(d)  # the English rule is under test here; the unit must satisfy the parity rule too
        return d, eva.check(d)

    def test_english_agent_passes(self):
        d, r = self.agent()
        self.assertEqual(failed_ids(r), {P.HERMES_ROW}, r.failed); self.assertEqual(r.kind, "agent")
        for cid in ("english-agent-md", "english-scenarios"): self.assertTrue(E.SkillMixin.status(self, r, cid), cid)

    def test_agent_md_frontmatter_and_body_fail(self):
        for old, new, where in (("goal: Explain", "goal: %s Explain" % KO, "(frontmatter)"),
                                ("No file changes (read-only).", KO, "(frontmatter)"),
                                ("Remember the topic", "%s Remember the topic" % KO, "(body)")):
            d, r = self.agent(md=self.AGENT.replace(old, new))
            self.assertEqual(r.verdict, "FAIL", old); self.assertIn("english-agent-md", failed_ids(r))
            self.assertIn("AGENT.md:%d %s [Hangul]" % (line_of(os.path.join(d, "AGENT.md"), KO), where), reason(r, "english-agent-md"))
            shutil.rmtree(d)

    def test_scenario_prompt_and_expectations_fail(self):
        for key, s in (("user", dict(self.S1, turns=[{"user": KO}])),
                       ("expected_outcome", dict(self.S1, expected_outcome=[{"type": "llm", "text": KO}])),
                       ("success_criteria", dict(self.S1, success_criteria=[{"type": "llm", "text": KO}]))):
            d, r = self.agent(s1=s)
            self.assertEqual(r.verdict, "FAIL", key); self.assertIn("english-scenarios", failed_ids(r), key)
            self.assertIn("scenarios/01.json:%d [Hangul]" % line_of(os.path.join(d, "scenarios", "01.json"), KO),
                          reason(r, "english-scenarios"), key)
            shutil.rmtree(d)

    def test_must_include_is_review_not_fail(self):
        d, r = self.agent(s1=dict(self.S1, must_include=[KO]))
        self.assertEqual(failed_ids(r), {P.HERMES_ROW}, r.failed)
        self.assertEqual([c["id"] for c in r.needs_review],
                         ["english-output-matchers", "parity-permissions-codex", "parity-permissions-opencode"])
        self.assertIn("scenarios/01.json:", reason(r, "english-output-matchers"))

    def test_scenario_json_escapes_are_decoded(self):
        d, r = self.agent(raw1=json.dumps(dict(self.S1, expected_outcome=[{"type": "llm", "text": KO}]), indent=1))
        self.assertIn("english-scenarios", failed_ids(r))

WRAPPERS_MISSING = {"parity-wrapper-" + t for t in eva.TOOLS} | {P.HERMES_ROW}
# AGENT.md v1 rows (unit B1 D9): the golden agents stay v1, so they also fail the rows that read the v2 fields.
V1_AGENT_ROWS = {"agent-frontmatter-v2", "success-measurable", "tools-declared", "tools-subset", "scenarios-valid",
                 "scenarios-present"}

class TestIndexAndGolden(E.SkillMixin, unittest.TestCase):
    def test_index_records_the_english_verdict(self):
        self.skill(); self.skill(name="ko-skill", body=KO + "\n", evals={"skill_name": "ko-skill", "evals": E.GOOD_EVALS})
        reg, _ = eva.build_index(self.tmp)
        self.assertEqual({e["name"]: e["verdict"] for e in reg}, {"word-count": "PASS", "ko-skill": "FAIL"})

    def test_golden_units_fail_exactly_on_these_checks(self):
        """The list in notes/english-rule-plan.md section 8 plus the missing wrappers of the two golden agents
        (notes/multi-tool-parity-plan.md section 11). golden/ is not edited and not exempted."""
        want = {"skills/csv-cleaner": {"english-skill-md", "english-evals"},
                "skills/korean-proofreader": {"english-skill-md", "english-evals"},
                "skills/pdf-summarizer": {"english-skill-md", "english-bundled-files", "english-evals"},
                "agents/code-reviewer": {"english-agent-md", "english-scenarios"} | WRAPPERS_MISSING | V1_AGENT_ROWS,
                "agents/research-assistant": {"english-agent-md", "english-scenarios"} | WRAPPERS_MISSING | V1_AGENT_ROWS}
        for rel, ids in want.items():
            r = eva.check(E.p("golden", *rel.split("/")))
            self.assertEqual((r.verdict, failed_ids(r)), ("FAIL", ids), rel)
            self.assertEqual(r.needs_review, [], rel)

    def test_register_approve_and_hashing_do_not_run_eva_checks(self):
        """Registration and approvals are hash-bound; the English rule must not invalidate a registered skill."""
        import inspect
        fns = (se.register, se._register_with, se.registration_record, se.registration_hash, se.registration_verdict,
               se.approve, se._approve_with, se.approve_show, se.approvals_data, se.approval_record, se.approval_verdict,
               se.approval_ok, se.gate_hash, se.skill_tree_hash)
        P.assert_record_readers_listed(self, fns)
        for fn in fns:
            src = inspect.getsource(fn)
            for name in ("eva.check", "check_skill", "check_agent", "english_"):
                self.assertNotIn(name, src, (fn.__name__, name))
        with mock.patch.object(eva, "check", side_effect=AssertionError("eva.check called")), \
                mock.patch.object(eva, "check_skill", side_effect=AssertionError("eva.check_skill called")):
            spec, problems = se.load_eval_set(E.p("golden", "skills", "csv-cleaner"))
            self.assertTrue(problems and all(x.startswith("english-only rule:") for x in problems))
            self.assertEqual(len(se.skill_tree_hash(spec["skill_dir"])), 64)

class TestValidate(T.Base):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def skill(self, evals=None, triggers=T.TRIGGERS, extra_files=None):
        return T.make_skill(self.tmp, evals=evals, triggers=triggers, extra_files=extra_files)

    def test_english_eval_set_validates(self):
        rc, out, _ = self.cli("validate", self.skill())
        self.assertEqual(rc, 0, out); self.assertIn("eval set OK", out)

    def test_prompt_expected_output_and_assertions_fail(self):
        for path in (("evals", 0, "prompt"), ("evals", 0, "expected_output"), ("evals", 0, "assertions", 2),
                     ("evals", 1, "assertions", 0, "text"), ("evals", 2, "pressure", "context")):
            doc = T.evals_doc(); node = doc
            for k in path[:-1]: node = node[k]
            node[path[-1]] = "do it. %s" % KO
            d = self.skill(evals=doc)
            rc, out, _ = self.cli("validate", d)
            self.assertEqual(rc, 1, path); self.assertIn("english-only rule: evals/evals.json:", out)
            self.assertIn("[Hangul]", out); shutil.rmtree(d)

    def test_trigger_queries_fail(self):
        trig = [dict(q) for q in T.TRIGGERS]; trig[0]["query"] = KO
        rc, out, _ = self.cli("validate", self.skill(triggers=trig))
        self.assertEqual(rc, 1); self.assertIn("english-only rule: evals/trigger_evals.json:", out)

    def test_validate_reports_each_finding_of_the_shared_scan_once(self):
        doc = T.evals_doc(); doc["evals"][0]["prompt"] = "start the task. %s" % KO
        d = self.skill(evals=doc)
        rc, out, _ = self.cli("validate", d)
        lines = [ln for ln in out.splitlines() if ln.startswith("english-only rule: ")]
        self.assertEqual(rc, 1, out)
        self.assertEqual(lines, ["english-only rule: " + f for f in eva.english_violations(d)])
        self.assertEqual(len(lines), 1, lines); self.assertRegex(lines[0], r"^english-only rule: evals/evals\.json:\d+ \[Hangul\]: ")

    def test_evals_files_content_is_exempt(self):
        doc = T.evals_doc(); doc["evals"][0]["files"] = ["evals/files/input.csv"]
        d = self.skill(evals=doc, extra_files={"evals/files/input.csv": "a,b\n%s,1\n" % KO})
        rc, out, _ = self.cli("validate", d)
        self.assertEqual(rc, 0, out)

    def test_matcher_pattern_is_review_note_not_failure(self):
        doc = T.evals_doc(); doc["evals"][0]["assertions"][0]["check"]["pattern"] = KO
        rc, out, _ = self.cli("validate", self.skill(evals=doc))
        self.assertEqual(rc, 3, out); self.assertIn("review: non-English text in a run-output matcher", out)
        self.assertIn("RULE REVIEW NOT RECORDED", out)

    def test_live_gate_refuses_non_english_eval_set(self):
        doc = T.evals_doc(); doc["evals"][0]["prompt"] = KO
        spec, problems = se.load_eval_set(self.skill(evals=doc))
        self.assertTrue(problems)
        a = mock.Mock(cmd="run", strict_budget=True)
        with self.assertRaises(se.EvalError) as cm:
            se.check_live_gates(a, spec, problems, self.ws, None, [], se.DEFAULT_TOKENS, {}, 100.0)
        self.assertIn("validation problem", str(cm.exception))

class TestBuilderGate(T.Base):
    def spec(self, **kw):
        kw.setdefault("evals", T.evals_doc(human=False))
        return T.Base.spec(self, **kw)

    def loop(self, builder, spec, **kw):
        kw.setdefault("max_iterations", 3)
        return se.loop(spec, self.ws, self.engines(regress=True)[:1], self.grader(), builder, runs=1, log=T.quiet, **kw)

    def append(self, spec, rel, text):
        with open(os.path.join(spec["skill_dir"], rel), "a", encoding="utf-8") as f: f.write(text)

    def test_korean_in_skill_md_stops_the_loop_before_anything_else_runs(self):
        spec, _ = self.spec(); calls = []
        def builder(it, skill_dir):
            calls.append(it); self.append(spec, "SKILL.md", "\n%s\n" % KO); return 0
        r = self.loop(builder, spec)
        self.assertEqual(r["status"], "builder-non-english"); self.assertEqual(r["iteration"], 1)
        self.assertTrue(r["problems"] and "SKILL.md:" in r["problems"][0] and "[Hangul]" in r["problems"][0])
        self.assertEqual(len(calls), 1); self.assertEqual(se.iterations(self.ws), [1])

    def test_each_scanned_location_trips_the_gate(self):
        """evals/evals.json is outside the builder's writable set (plan.md:501), so a builder write to it stops the loop
        with builder-wrote-outside before the English gate runs (plan.md:502). The gate still scans that file: Korean
        text planted there before the loop trips it after a builder call that writes nothing."""
        def write_text(fp):
            with open(fp, "a", encoding="utf-8") as f: f.write("\n# " + KO + "\n")
        def edit_evals(fp):
            with open(fp, encoding="utf-8") as f: doc = json.load(f)
            doc["evals"][0]["prompt"] = KO
            with open(fp, "w", encoding="utf-8") as f: json.dump(doc, f, ensure_ascii=False, indent=1)
        for rel, edit, status in (("references/note.md", write_text, "builder-non-english"),
                                  ("scripts/helper.py", write_text, "builder-non-english"),
                                  ("evals/evals.json", edit_evals, "builder-wrote-outside")):
            spec, _ = self.spec()
            def builder(it, skill_dir, rel=rel, edit=edit):
                fp = os.path.join(skill_dir, rel); os.makedirs(os.path.dirname(fp), exist_ok=True)
                edit(fp); return 0
            r = self.loop(builder, spec)
            self.assertEqual(r["status"], status, rel); self.assertIn(rel, r["problems"][0])
            shutil.rmtree(self.ws, ignore_errors=True); shutil.rmtree(spec["skill_dir"])
        spec, _ = self.spec(); edit_evals(os.path.join(spec["skill_dir"], "evals", "evals.json"))
        r = self.loop(lambda it, sd: 0, spec)
        self.assertEqual(r["status"], "builder-non-english"); self.assertIn("evals/evals.json", r["problems"][0])

    def test_gate_is_a_code_gate_independent_of_the_prompt(self):
        spec, _ = self.spec()
        r = self.loop(lambda it, sd: self.append(spec, "SKILL.md", "\n" + ZH + "\n") or 0, spec)
        self.assertEqual(r["status"], "builder-non-english"); self.assertIn("[CJK]", r["problems"][0])

    def test_english_builder_output_continues_the_loop(self):
        spec, _ = self.spec(); seen = []
        def builder(it, sd):
            seen.append(it); self.append(spec, "SKILL.md", "\nMore detail: " + FINE + "\n"); return 0
        r = self.loop(builder, spec, max_iterations=2)
        self.assertEqual(r, {"status": "needs-approval", "iteration": 2}); self.assertEqual(len(seen), 1)

    def test_evals_files_written_by_the_builder_are_exempt(self):
        """The builder may no longer write evals/files/ (outside its writable set, plan.md:501): the write stops the loop
        with builder-wrote-outside (plan.md:502). The English gate's exemption of evals/files/ still holds: Korean
        fixture text planted before the loop lets it run on."""
        spec, _ = self.spec()
        def builder(it, sd):
            os.makedirs(os.path.join(sd, "evals", "files"), exist_ok=True)
            with open(os.path.join(sd, "evals", "files", "in.csv"), "w", encoding="utf-8") as f: f.write(KO)
            return 0
        r = self.loop(builder, spec, max_iterations=2)
        self.assertEqual(r["status"], "builder-wrote-outside"); self.assertIn("evals/files/in.csv", r["problems"])
        shutil.rmtree(self.ws, ignore_errors=True); shutil.rmtree(spec["skill_dir"])
        spec, _ = self.spec(extra_files={"evals/files/in.csv": KO})
        r = self.loop(lambda it, sd: 0, spec, max_iterations=2)
        self.assertEqual(r, {"status": "needs-approval", "iteration": 2})

    def test_failed_builder_keeps_its_own_status(self):
        spec, _ = self.spec()
        r = self.loop(lambda it, sd: self.append(spec, "SKILL.md", KO) or 7, spec)
        self.assertEqual((r["status"], r["returncode"]), ("builder-non-english", 7))

    def test_no_builder_means_no_gate(self):
        spec, _ = self.spec()
        self.assertEqual(self.loop(None, spec, max_iterations=2), {"status": "needs-approval", "iteration": 1})

    def test_korean_skill_before_the_builder_is_caught_after_its_first_call(self):
        spec, _ = self.spec(); self.append(spec, "SKILL.md", "\n%s\n" % KO)
        r = self.loop(lambda it, sd: 0, spec)
        self.assertEqual(r["status"], "builder-non-english")

    def test_run_output_is_not_checked(self):
        """A Korean final answer from the executor must not trip anything: only skill and eval content is scanned."""
        spec, _ = self.spec(); self.engines(regress=True)
        eng = se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=lambda cmd, cwd, timeout, env=None:
                                  (0, T.claude_stream("\ud55c\uad6d\uc5b4\ub85c \ub2f5\ud569\ub2c8\ub2e4. feat/x"), ""))
        r = se.loop(spec, self.ws, [eng], self.grader(), lambda it, sd: 0, runs=1, max_iterations=2, log=T.quiet)
        self.assertNotEqual(r["status"], "builder-non-english")

class TestBuilderInstruction(T.Base):
    def test_prompt_states_english_only(self):
        seen = []
        def fx(cmd, cwd, timeout, env=None):
            seen.append(cmd[2]); return 0, json.dumps({"type": "result", "total_cost_usd": 0.5}), ""
        it = os.path.join(self.tmp, "it"); os.makedirs(it)
        self.assertEqual(se.ClaudeBuilder("Improve it.", "claude-opus-5-5", se.Budget(10.0), exec_fn=fx)(it, "/skill"), 0)
        self.assertIn("Write all skill content in English only", seen[0])
        self.assertIn(eva.ENGLISH_BUILDER_RULE, seen[0])

if __name__ == "__main__":
    unittest.main()
