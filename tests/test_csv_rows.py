"""csv_rows static check: per-row record counts and uniqueness, no fixture, no model calls."""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_skill_eval import se, make_skill, evals_doc

ORDERS_TRIMMED = "order_id,item,qty\n1,apple,2\n2,pear,1\n3,plum,5\n"
ORDERS_UNTRIMMED = "order_id,item,qty\n1, apple ,2\n2,pear , 1\n3,plum,5\n"
CUSTOMERS_KEPT = ("email,name,city\nkim@example.com,Kim,Seoul\nKIM@example.com,Kim,Seoul\n"
                  "lee@example.com,Lee,busan\nlee@example.com,Lee,Busan\npark@example.com,Park,Incheon\n")
CUSTOMERS_MERGED = ("email,name,city\nkim@example.com,Kim,Seoul\nlee@example.com,Lee,Busan\n"
                    "park@example.com,Park,Incheon\n")

CASE1 = {"type": "csv_rows", "path": "orders_clean.csv", "normalize": "strip", "unique": True, "min": 1,
         "rows": [["1", "apple", "2"], ["2", "pear", "1"], ["3", "plum", "5"]]}
CASE6_KEPT = {"type": "csv_rows", "path": "customers_clean.csv", "normalize": "none", "min": 1,
              "rows": [["kim@example.com", "Kim", "Seoul"], ["KIM@example.com", "Kim", "Seoul"],
                       ["lee@example.com", "Lee", "busan"], ["lee@example.com", "Lee", "Busan"]]}
CASE6_ONCE = {"type": "csv_rows", "path": "customers_clean.csv", "normalize": "none", "min": 1, "max": 1,
              "rows": [["kim@example.com", "Kim", "Seoul"], ["park@example.com", "Park", "Incheon"]]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.join(self.tmp.name, "work")
        os.makedirs(self.work)

    def write(self, name, text):
        with open(os.path.join(self.work, name), "w", encoding="utf-8", newline="") as f: f.write(text)

    def run_check(self, chk, name=None, text=None):
        if text is not None: self.write(name, text)
        return se.static_check(chk, {}, self.work)


class TestIterationThreeScenarios(Base):
    def test_case1_trim_insensitive_dedupe_passes_trimmed_and_untrimmed_outputs(self):
        for text in (ORDERS_TRIMMED, ORDERS_UNTRIMMED):
            ok, evidence = self.run_check(CASE1, "orders_clean.csv", text)
            self.assertTrue(ok, evidence)

    def test_case1_with_normalize_none_the_untrimmed_output_fails(self):
        chk = dict(CASE1, normalize="none")
        self.assertTrue(self.run_check(chk, "orders_clean.csv", ORDERS_TRIMMED)[0])
        self.assertFalse(self.run_check(chk, "orders_clean.csv", ORDERS_UNTRIMMED)[0])

    def test_case1_still_fails_when_duplicates_remain(self):
        ok, evidence = self.run_check(CASE1, "orders_clean.csv", ORDERS_UNTRIMMED + "3,plum,5\n3, plum ,5\n")
        self.assertFalse(ok)
        self.assertIn("2 duplicate record(s)", evidence)

    def test_case6_1_case_variants_kept(self):
        self.assertTrue(self.run_check(CASE6_KEPT, "customers_clean.csv", CUSTOMERS_KEPT)[0])
        ok, evidence = self.run_check(CASE6_KEPT, "customers_clean.csv", CUSTOMERS_MERGED)
        self.assertFalse(ok)
        self.assertIn('["KIM@example.com", "Kim", "Seoul"] x0', evidence)
        self.assertIn('["lee@example.com", "Lee", "busan"] x0', evidence)
        self.assertNotIn('["lee@example.com", "Lee", "Busan"]', evidence)

    def test_case6_2_exact_duplicates_kept_once_passes_both_shapes(self):
        for text in (CUSTOMERS_KEPT, CUSTOMERS_MERGED):
            ok, evidence = self.run_check(CASE6_ONCE, "customers_clean.csv", text)
            self.assertTrue(ok, evidence)

    def test_case6_2_fails_when_the_exact_duplicate_is_not_collapsed(self):
        ok, evidence = self.run_check(CASE6_ONCE, "customers_clean.csv", CUSTOMERS_KEPT + "park@example.com,Park,Incheon\n")
        self.assertFalse(ok)
        self.assertIn('["park@example.com", "Park", "Incheon"] x2', evidence)


class TestEvaluation(Base):
    def test_missing_file(self):
        chk = {"type": "csv_rows", "path": "nope.csv", "unique": True}
        self.assertEqual(self.run_check(chk), (False, "nope.csv missing"))

    def test_directory_at_path_counts_as_missing(self):
        os.makedirs(os.path.join(self.work, "out.csv"))
        self.assertEqual(self.run_check({"type": "csv_rows", "path": "out.csv", "unique": True}),
                         (False, "out.csv missing"))

    def test_read_errors_report_the_exception_type(self):
        with open(os.path.join(self.work, "bin.csv"), "wb") as f: f.write(b"a,b\n\xff\xfe,1\n")
        self.assertEqual(self.run_check({"type": "csv_rows", "path": "bin.csv", "unique": True}),
                         (False, "CSV read failed: UnicodeDecodeError"))
        self.write("nul.csv", "a,b\x00c\n")
        ok, evidence = self.run_check({"type": "csv_rows", "path": "nul.csv", "unique": True})
        self.assertFalse(ok)
        self.assertTrue(evidence.startswith("CSV read failed: "), evidence)

    def test_symlink_outside_workdir_is_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = os.path.join(outside.name, "secret.csv")
        with open(target, "w") as f: f.write("a,b\n")
        os.symlink(target, os.path.join(self.work, "out.csv"))
        self.assertEqual(self.run_check({"type": "csv_rows", "path": "out.csv", "unique": True}),
                         (False, "file path escapes its root"))

    def test_duplicates_found_under_strip_but_not_under_none(self):
        self.write("d.csv", "a,b\n1, x \n1,x\n")
        chk = {"type": "csv_rows", "path": "d.csv", "unique": True}
        self.assertTrue(self.run_check(dict(chk, normalize="none"))[0])
        self.assertTrue(self.run_check(chk)[0])  # default normalize is none
        ok, evidence = self.run_check(dict(chk, normalize="strip"))
        self.assertFalse(ok)
        self.assertIn("1 duplicate record(s)", evidence)

    def test_strip_applies_to_the_listed_rows_too(self):
        self.write("s.csv", "a,b\n1,x\n")
        chk = {"type": "csv_rows", "path": "s.csv", "rows": [[" 1 ", "x "]]}
        self.assertFalse(self.run_check(chk)[0])
        self.assertTrue(self.run_check(dict(chk, normalize="strip"))[0])

    def test_header_is_an_ordinary_record(self):
        self.write("h.csv", "a,b\n1,2\n")
        self.assertTrue(self.run_check({"type": "csv_rows", "path": "h.csv", "rows": [["a", "b"]]})[0])

    def test_max_exceeded_names_the_row_and_its_count(self):
        self.write("m.csv", "a,b\n1,2\n1,2\n1,2\n")
        chk = {"type": "csv_rows", "path": "m.csv", "rows": [["1", "2"]], "max": 2}
        ok, evidence = self.run_check(chk)
        self.assertFalse(ok)
        self.assertIn('["1", "2"] x3', evidence)
        self.assertIn("[1, 2]", evidence)
        self.assertTrue(self.run_check(dict(chk, max=3))[0])

    def test_min_above_one(self):
        self.write("m.csv", "a,b\n1,2\n1,2\n")
        chk = {"type": "csv_rows", "path": "m.csv", "rows": [["1", "2"]], "min": 2}
        self.assertTrue(self.run_check(chk)[0])
        self.assertFalse(self.run_check(dict(chk, min=3))[0])

    def test_min_0_max_0_requires_the_row_to_be_absent(self):
        chk = {"type": "csv_rows", "path": "a.csv", "rows": [["9", "9"]], "min": 0, "max": 0}
        self.assertTrue(self.run_check(chk, "a.csv", "a,b\n1,2\n")[0])
        ok, evidence = self.run_check(chk, "a.csv", "a,b\n1,2\n9,9\n")
        self.assertFalse(ok)
        self.assertIn('["9", "9"] x1', evidence)

    def test_every_listed_row_must_be_within_bounds(self):
        self.write("e.csv", "a,b\n1,2\n")
        chk = {"type": "csv_rows", "path": "e.csv", "rows": [["1", "2"], ["3", "4"], ["5", "6"]]}
        ok, evidence = self.run_check(chk)
        self.assertFalse(ok)
        self.assertIn('["3", "4"] x0', evidence)
        self.assertIn('["5", "6"] x0', evidence)
        self.assertNotIn('["1", "2"] x', evidence)

    def test_rows_equal_after_normalize_are_reported_once(self):
        self.write("e.csv", "a,b\n")
        chk = {"type": "csv_rows", "path": "e.csv", "normalize": "strip", "rows": [["1", "2"], [" 1", "2 "]]}
        self.assertEqual(self.run_check(chk)[1].count("x0"), 1)

    def test_cells_are_compared_as_a_whole_row(self):
        self.write("w.csv", "a,b\n1,2,3\n1\n")
        self.assertFalse(self.run_check({"type": "csv_rows", "path": "w.csv", "rows": [["1", "2"]]})[0])
        self.assertTrue(self.run_check({"type": "csv_rows", "path": "w.csv", "rows": [["1", "2", "3"], ["1"]]})[0])

    def test_quoted_cells_with_commas_and_newlines(self):
        self.write("q.csv", 'a,b\n"x, y","line1\nline2"\n')
        self.assertTrue(self.run_check({"type": "csv_rows", "path": "q.csv", "rows": [["x, y", "line1\nline2"]]})[0])

    def test_nested_path(self):
        os.makedirs(os.path.join(self.work, "out"))
        self.assertTrue(self.run_check({"type": "csv_rows", "path": "out/o.csv", "unique": True},
                                       "out/o.csv", "a,b\n1,2\n")[0])

    def test_evidence_is_truncated(self):
        long_cell = "x" * 300
        self.write("l.csv", "a\n")
        ok, evidence = self.run_check({"type": "csv_rows", "path": "l.csv", "rows": [[long_cell]]})
        self.assertFalse(ok)
        self.assertLessEqual(len(evidence), 200)

    def test_unique_only_check_passes_on_a_clean_file(self):
        ok, evidence = self.run_check({"type": "csv_rows", "path": "u.csv", "unique": True}, "u.csv", "a,b\n1,2\n3,4\n")
        self.assertTrue(ok, evidence)
        self.assertIn("no duplicate records", evidence)

    def test_unique_only_with_empty_rows_list_checks_only_uniqueness(self):
        chk = {"type": "csv_rows", "path": "u.csv", "rows": [], "unique": True}
        self.assertTrue(self.run_check(chk, "u.csv", "a,b\n1,2\n3,4\n")[0])
        ok, evidence = self.run_check(chk, "u.csv", "a,b\n1,2\n1,2\n")
        self.assertFalse(ok)
        self.assertIn("1 duplicate record(s)", evidence)

    def test_empty_row_matches_blank_line_records(self):
        chk = {"type": "csv_rows", "path": "b.csv", "rows": [[]], "min": 0, "max": 0}
        ok, evidence = self.run_check(chk, "b.csv", "a,b\n\n1,2\n")
        self.assertFalse(ok)
        self.assertIn("[] x1", evidence)
        ok, evidence = self.run_check(chk, "b.csv", "a,b\n1,2\n")
        self.assertTrue(ok, evidence)

    def test_empty_row_with_min_1_requires_a_blank_line(self):
        chk = {"type": "csv_rows", "path": "b.csv", "rows": [[]]}
        self.assertTrue(self.run_check(chk, "b.csv", "a,b\n\n1,2\n")[0])
        self.assertFalse(self.run_check(chk, "b.csv", "a,b\n1,2\n")[0])

    def test_empty_row_under_strip_still_matches_only_blank_lines(self):
        chk = {"type": "csv_rows", "path": "b.csv", "rows": [[]], "min": 0, "max": 0, "normalize": "strip"}
        self.assertFalse(self.run_check(chk, "b.csv", "a,b\n\n1,2\n")[0])
        self.assertTrue(self.run_check(chk, "b.csv", "a,b\n , \n1,2\n")[0])

    def test_malformed_csv_fails_instead_of_being_read_leniently(self):
        chk = {"type": "csv_rows", "path": "m.csv", "rows": [["a"]]}
        for text in ('"a\n', '"a', 'x,"a\nb,c\n', '"a"b,c\n'):
            self.write("m.csv", text)
            self.assertEqual(self.run_check(chk), (False, "CSV read failed: Error"), text)
            self.assertEqual(self.run_check({"type": "csv_rows", "path": "m.csv", "unique": True}),
                             (False, "CSV read failed: Error"), text)

    def test_well_formed_quoting_still_passes_under_strict_parsing(self):
        self.write("q.csv", 'h\n"a"\n"b,c"\n"d""e"\n')
        chk = {"type": "csv_rows", "path": "q.csv", "rows": [["a"], ["b,c"], ['d"e']]}
        ok, evidence = self.run_check(chk)
        self.assertTrue(ok, evidence)

    def test_evidence_is_capped_on_every_return_path_with_a_long_path(self):
        long_rel = "/".join(["d" * 40] * 6) + "/" + "f" * 60 + ".csv"
        base = {"type": "csv_rows", "path": long_rel}
        self.assertGreater(len(long_rel), 200)
        # missing
        ok, evidence = self.run_check(dict(base, unique=True))
        self.assertFalse(ok); self.assertLessEqual(len(evidence), 200)
        # pass
        os.makedirs(os.path.dirname(os.path.join(self.work, long_rel)))
        self.write(long_rel, "a,b\n1,2\n")
        ok, evidence = self.run_check(dict(base, rows=[["1", "2"]], unique=True))
        self.assertTrue(ok); self.assertEqual(len(evidence), 200)
        # fail
        ok, evidence = self.run_check(dict(base, rows=[["9", "9"]]))
        self.assertFalse(ok); self.assertLessEqual(len(evidence), 200)
        # read error
        self.write(long_rel, '"a\n')
        ok, evidence = self.run_check(dict(base, unique=True))
        self.assertFalse(ok); self.assertLessEqual(len(evidence), 200)

    def test_escape_evidence_is_capped_too(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = os.path.join(outside.name, "secret.csv")
        with open(target, "w") as f: f.write("a,b\n")
        link = "l" * 250 + ".csv"
        try: os.symlink(target, os.path.join(self.work, link))
        except OSError as ex: self.skipTest("symlink/name unsupported: %s" % ex)
        ok, evidence = self.run_check({"type": "csv_rows", "path": link, "unique": True})
        self.assertFalse(ok); self.assertLessEqual(len(evidence), 200)

    def test_unique_false_does_not_count_duplicates(self):
        chk = {"type": "csv_rows", "path": "u.csv", "unique": False, "rows": [["1", "2"]], "max": 2}
        self.assertTrue(self.run_check(chk, "u.csv", "a,b\n1,2\n1,2\n")[0])


class TestValidation(unittest.TestCase):
    def problems(self, **over):
        chk = {"type": "csv_rows", "path": "o.csv", "rows": [["1", "2"]]}
        chk.update(over)
        for k in [k for k, v in chk.items() if v is KeyError]: del chk[k]
        return se._check_problems("w", chk)

    def test_type_is_registered(self):
        self.assertIn("csv_rows", se.STATIC_TYPES)

    def test_valid_shapes(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.problems(rows=KeyError, unique=True), [])
        self.assertEqual(self.problems(min=0, max=0, normalize="strip", unique=False), [])
        self.assertEqual(self.problems(min=2, max=2), [])
        for chk in (CASE1, CASE6_KEPT, CASE6_ONCE):
            self.assertEqual(se._check_problems("w", chk), [])

    def test_asserts_nothing(self):
        for over in ({"rows": KeyError}, {"rows": KeyError, "unique": False}):
            ps = self.problems(**over)
            self.assertEqual(len(ps), 1, ps)
            self.assertIn("asserts nothing", ps[0])

    def test_rows_must_be_lists_of_lists_of_strings(self):
        for bad in (["a"], "a", [[1]], [["a", None]], [["a"], "b"], {"a": 1}, [None], [[["a"]]]):
            ps = self.problems(rows=bad)
            self.assertTrue(any("w.check.rows:" in p for p in ps), (bad, ps))
        self.assertEqual(self.problems(rows=[[""]]), [])  # an empty cell is a legitimate cell value

    def test_empty_inner_row_matches_a_blank_line_record_and_is_accepted(self):
        for rows in ([[]], [["a"], []], [[], []]):
            self.assertEqual(self.problems(rows=rows), [], rows)
        self.assertEqual(self.problems(rows=[[]], min=0, max=0), [])
        self.assertEqual(self.problems(rows=[[]], unique=True), [])

    def test_bad_rows_with_unique_true_is_still_a_problem(self):
        for bad in ([[1]], "a", {"a": 1}, [["a"], "b"]):
            self.assertTrue(any("w.check.rows" in p for p in self.problems(rows=bad, unique=True)), bad)

    def test_empty_inner_row_does_not_relax_the_outer_list_rules(self):
        ps = self.problems(rows=[[]], min=0)
        self.assertEqual(len(ps), 1, ps)
        self.assertIn("w.check.min: 0 without max", ps[0])

    def test_empty_rows_list_is_allowed_only_with_unique_true(self):
        self.assertEqual(self.problems(rows=[], unique=True), [])
        for over in ({"rows": []}, {"rows": [], "unique": False}):
            ps = self.problems(**over)
            self.assertTrue(any("w.check.rows:" in p and "unique: true" in p for p in ps), (over, ps))

    def test_min_max_are_meaningless_with_an_empty_rows_list(self):
        for over in ({"min": 1}, {"max": 2}, {"min": 0, "max": 1}):
            ps = self.problems(rows=[], unique=True, **over)
            self.assertEqual(len(ps), 1, (over, ps))
            self.assertIn("need a non-empty rows list", ps[0])

    def test_min_0_without_max_asserts_nothing_about_the_rows(self):
        for over in ({"min": 0}, {"min": 0, "unique": True}, {"min": 0, "unique": False}, {"min": 0, "normalize": "strip"}):
            ps = self.problems(**over)
            self.assertEqual(len(ps), 1, (over, ps))
            self.assertIn("w.check.min: 0 without max", ps[0])
            self.assertIn("set max or a min >= 1", ps[0])

    def test_min_0_with_max_or_min_at_least_1_is_accepted(self):
        for over in ({"min": 0, "max": 0}, {"min": 0, "max": 3}, {"min": 1}, {"min": 2, "unique": True}):
            self.assertEqual(self.problems(**over), [], over)

    def test_bad_normalize(self):
        for bad in ("lower", "STRIP", "", None, True):
            self.assertTrue(any("w.check.normalize: none|strip" in p for p in self.problems(normalize=bad)), bad)

    def test_bad_unique(self):
        for bad in ("true", 1, None):
            self.assertTrue(any("w.check.unique" in p for p in self.problems(unique=bad)), bad)

    def test_min_max_types(self):
        for k in ("min", "max"):
            for bad in (True, False, -1, 1.5, "1", None):
                self.assertTrue(any("w.check.%s: integer >= 0 required" % k in p for p in self.problems(**{k: bad})), (k, bad))

    def test_min_greater_than_max(self):
        ps = self.problems(min=3, max=2)
        self.assertEqual(len(ps), 1, ps)
        self.assertIn("w.check.max: must be >= min", ps[0])

    def test_max_0_without_min_contradicts_the_default_min(self):
        ps = self.problems(max=0)
        self.assertEqual(len(ps), 1, ps)
        self.assertIn("set min: 0", ps[0])

    def test_min_max_need_rows(self):
        ps = self.problems(rows=KeyError, unique=True, min=1)
        self.assertEqual(len(ps), 1, ps)
        self.assertIn("w.check.rows: required when min/max", ps[0])

    def test_paths(self):
        for bad in ("", "/abs/o.csv", "../o.csv", "a/../o.csv", "a\\..\\o.csv", None, 5, ["o.csv"]):
            ps = self.problems(path=bad)
            self.assertTrue(any("w.check.path: relative path required" in p for p in ps), (bad, ps))
        self.assertTrue(any("w.check.path" in p for p in self.problems(path=KeyError)))
        self.assertEqual(self.problems(path="out/o.csv"), [])

    def test_lone_surrogate_in_path_from_escaped_json_is_rejected(self):
        chk = json.loads('{"type": "csv_rows", "path": "o\\ud800.csv", "rows": [["1", "2"]]}')
        self.assertEqual(chk["path"], "o\ud800.csv")
        ps = se._check_problems("w", chk)
        self.assertEqual(len(ps), 1, ps)
        self.assertTrue(ps[0].startswith("w.check.path:") and "surrogate" in ps[0], ps)

    def test_lone_surrogate_in_a_cell_from_escaped_json_is_rejected(self):
        for text in ('[["1", "x\\udfffy"]]', '[["ok"], ["\\ud800"]]'):
            chk = {"type": "csv_rows", "path": "o.csv", "rows": json.loads(text)}
            ps = se._check_problems("w", chk)
            self.assertEqual(len(ps), 1, (text, ps))
            self.assertTrue(ps[0].startswith("w.check.rows:") and "surrogate" in ps[0], ps)

    def test_lone_surrogate_in_a_cell_is_rejected_with_unique_true_too(self):
        chk = {"type": "csv_rows", "path": "o.csv", "rows": json.loads('[["\\ud800"]]'), "unique": True}
        self.assertTrue(any("surrogate" in p for p in se._check_problems("w", chk)))

    def test_a_valid_surrogate_pair_escape_is_one_astral_character_and_accepted(self):
        chk = {"type": "csv_rows", "path": json.loads('"o\\ud83d\\ude00.csv"'),
               "rows": json.loads('[["\\ud83d\\ude00"]]')}
        self.assertEqual(se._check_problems("w", chk), [])

    def test_lone_surrogate_surfaces_through_load_eval_set_from_escaped_json_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev = copy.deepcopy(evals_doc())
            ev["evals"][0]["assertions"] = [{"text": "t", "layer": "static", "check": {
                "type": "csv_rows", "path": "o.csv", "rows": [["PLACEHOLDER"]]}}]
            skill = make_skill(tmp, evals=ev)
            path = os.path.join(skill, "evals", "evals.json")
            with open(path, encoding="utf-8") as f: raw = f.read()
            self.assertIn("PLACEHOLDER", raw)
            with open(path, "w", encoding="utf-8") as f: f.write(raw.replace("PLACEHOLDER", "\\ud800"))
            _, problems = se.load_eval_set(skill)
            self.assertTrue(any("check.rows" in p and "surrogate" in p for p in problems), problems)

    def _skill_with_unknown_key(self, tmp, escaped_key):
        ev = copy.deepcopy(evals_doc())
        ev["evals"][0]["assertions"] = [{"text": "t", "layer": "static", "check": {
            "type": "csv_rows", "path": "o.csv", "rows": [["1"]], "PLACEHOLDER": 1}}]
        skill = make_skill(tmp, evals=ev)
        path = os.path.join(skill, "evals", "evals.json")
        with open(path, encoding="utf-8") as f: raw = f.read()
        self.assertIn('"PLACEHOLDER"', raw)
        with open(path, "w", encoding="utf-8") as f: f.write(raw.replace("PLACEHOLDER", escaped_key))
        return skill

    def test_unknown_key_with_lone_surrogate_gives_a_utf8_encodable_problem(self):
        for escaped in ("\\ud800", "x\\udfffy", "\\ud83d"):
            chk = json.loads('{"type": "csv_rows", "path": "o.csv", "rows": [["1"]], "%s": 1}' % escaped)
            bad = [k for k in chk if k not in se.CSV_ROWS_KEYS][0]
            self.assertTrue(se._has_lone_surrogate(bad))
            ps = se._csv_rows_problems("w", chk)
            self.assertEqual(len(ps), 1, ps)
            self.assertIn("not a csv_rows key", ps[0])
            ps[0].encode("utf-8")
            self.assertFalse(se._has_lone_surrogate(ps[0]))
            with tempfile.TemporaryDirectory() as tmp:
                _, problems = se.load_eval_set(self._skill_with_unknown_key(tmp, escaped))
            self.assertTrue(any("not a csv_rows key" in p for p in problems), problems)
            for p in problems: p.encode("utf-8")

    def test_non_ascii_unknown_key_is_reported_without_raising(self):
        ps = se._csv_rows_problems("w", {"type": "csv_rows", "path": "o.csv", "rows": [["1"]], "열": 1})
        self.assertEqual(len(ps), 1, ps)
        ps[0].encode("ascii")

    def test_validate_command_reports_a_surrogate_unknown_key_instead_of_raising(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._skill_with_unknown_key(tmp, "\\ud800")
            out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
            err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = se.main(["validate", skill])
            out.flush(); err.flush()
            text = out.buffer.getvalue().decode("utf-8")
        self.assertEqual(rc, 1)
        self.assertIn("not a csv_rows key", text)
        self.assertIn("\\ud800", text)

    def test_fixture_and_unknown_keys_are_rejected(self):
        for key in ("fixture", "data_rows", "mni"):
            ps = self.problems(**{key: "x"})
            self.assertTrue(any("w.check.%s: not a csv_rows key" % key in p for p in ps), (key, ps))


class TestLoadEvalSet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def spec(self, check):
        ev = copy.deepcopy(evals_doc())
        ev["evals"][0]["assertions"] = [{"text": "orders deduped", "layer": "static", "check": check}]
        return se.load_eval_set(make_skill(self.tmp.name, evals=ev))

    def test_accepted_without_a_fixture_or_case_files(self):
        spec, problems = self.spec(CASE1)
        self.assertEqual(problems, [])
        item = spec["cases"][0]["items"][0]
        self.assertEqual((item["layer"], item["check"]), ("static", CASE1))

    def test_validation_problems_surface_through_load_eval_set(self):
        _, problems = self.spec({"type": "csv_rows", "path": "../o.csv"})
        joined = "\n".join(problems)
        self.assertIn("check.path: relative path required", joined)
        self.assertIn("asserts nothing", joined)
        self.assertNotIn("check.fixture", joined)


if __name__ == "__main__": unittest.main()
