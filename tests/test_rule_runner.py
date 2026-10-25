"""engine/rule_runner.py and its eva.py wiring: bundle loading, the closed op and selector tables, the severity mapping,
the bundle-non-default cap, ruleset_sha256, the moved readers, and the CLI (plan.md:556-561, :566; D9 to D16 of the A1
how pass). Every unit and bundle lives in a temp folder; nothing runs a model or a tool binary."""
import contextlib, inspect, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva, skill_eval
from engine import loaders, rule_runner as RR

SELFTEST = os.path.join(ROOT, "tests", "rules", "_selftest")
PASSING_UNIT = os.path.join(ROOT, "tests", "fixtures", "skills", "csv-cleaner-en")
HEX64 = re.compile(r"^ruleset_sha256 [0-9a-f]{64}$")
DESC = "Checks one rule runner fixture. Use when a rule runner test needs a unit folder."
IMPLEMENTED = {"schema", "equals", "subset", "set_equals", "contains_text", "regex", "not_regex", "file_exists",
               "file_absent", "count", "max_bytes", "max_lines", "all", "any", "not", "when", "script_denylist",
               "parity_skill", "parity_invocation", "parity_agent_name", "parity_wrapper", "parity_permissions",
               "heading_present", "description_heuristics", "local_refs", "agent_frontmatter", "host_frontmatter",
               "host_frontmatter_fields",
               "host_field_length", "host_main_file_size", "host_metadata_version", "host_evals_count",
               "host_tools_declared", "host_success_measurable", "host_scenarios", "generated_up_to_date"}
RESERVED = set()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def rule(check, rid="t-rule", severity="fail", applies_to=("skill", "agent"), category="structure",
         message="left '{left}' right '{right}' value '{value}'"):
    return {"id": rid, "applies_to": list(applies_to), "category": category, "severity": severity,
            "protects": ["claude"], "source": {"house": "rule runner test"}, "check": check, "message": message,
            "fix": "Fix the fixture."}


def rule_file(*rules):
    return json.dumps({"ruleset": "test", "version": "1.0.0", "rules": list(rules)})


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = eva.main(argv)
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def unit(self, files=None, name="demo", fm_name=None, folder="units"):
        path = os.path.join(self.tmp, folder, name)
        write(os.path.join(path, "SKILL.md"), "---\nname: %s\ndescription: %s\n---\n\n# Demo\n\nBody text.\n"
              % (name if fm_name is None else fm_name, DESC))
        for rel, text in (files or {}).items():
            write(os.path.join(path, *rel.split("/")), text)
        return path

    def bundle_dir(self, files):
        folder = tempfile.mkdtemp(dir=self.tmp)
        for rel, text in files.items():
            write(os.path.join(folder, *rel.split("/")), text)
        return folder

    def bundle(self, *rules, root=RR.ROOT):
        return RR.load_bundle(self.bundle_dir({"rules.json": rule_file(*rules)}), root=root)

    def load_error(self, files, root=RR.ROOT):
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(self.bundle_dir(files), root=root)
        return str(cm.exception)

    def row(self, check, files=None, severity="fail", unit=None, root=RR.ROOT, **unit_kw):
        path = unit or self.unit(files, **unit_kw)
        b = self.bundle(rule(check, severity=severity), root=root)
        r = eva.Report(path, "skill")
        b.apply(r)
        rows = [c for c in r.checks if c["id"] == "t-rule"]
        self.assertEqual(len(rows), 1, r.checks)
        return rows[0]

    def status(self, check, files=None, **kw):
        return self.row(check, files, **kw)["status"]


FILE_A = {"op": "file_exists", "path": "a.txt"}
FILE_NONE = {"op": "file_exists", "path": "none.txt"}


class TestOps(Base):
    def test_the_op_tables_are_closed_and_disjoint(self):
        self.assertEqual(set(RR.OPS), IMPLEMENTED)
        self.assertEqual(set(RR.RESERVED_OPS), RESERVED)
        with open(os.path.join(ROOT, "schemas", "rules.schema.json"), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(set(doc["$defs"]["node"]["properties"]["op"]["enum"]), IMPLEMENTED)

    def test_schema(self):
        troot = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(troot, "schemas"))
        write(os.path.join(troot, "schemas", "thing.schema.json"), json.dumps({"type": "object", "required": ["a"]}))
        check = {"op": "schema", "sel": "json:data.json", "schema": "thing.schema.json"}
        self.assertEqual(self.status(check, {"data.json": '{"a": 1}'}, root=troot), "pass")
        row = self.row(check, {"data.json": "{}"}, root=troot, name="other")
        self.assertEqual(row["status"], "fail")
        self.assertIn("required property 'a' is missing", row["reason"])

    def test_schema_gets_the_typed_keys_of_a_whole_frontmatter(self):
        troot = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(troot, "schemas"))
        write(os.path.join(troot, "schemas", "fm.schema.json"),
              json.dumps({"properties": {"name": {"x-yaml-string": True}}}))
        check = {"op": "schema", "sel": "frontmatter:", "schema": "fm.schema.json"}
        self.assertEqual(self.status(check, root=troot), "pass")
        row = self.row(check, root=troot, name="typed", fm_name="yes")
        self.assertEqual(row["status"], "fail")
        self.assertIn("typed by YAML 1.1", row["reason"])

    def test_schema_on_a_sub_pointer_has_no_typed_keys_and_fails_closed(self):
        troot = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(troot, "schemas"))
        write(os.path.join(troot, "schemas", "s.schema.json"), json.dumps({"x-yaml-string": True}))
        row = self.row({"op": "schema", "sel": "frontmatter:#/name", "schema": "s.schema.json"}, root=troot)
        self.assertEqual(row["status"], "fail")
        self.assertIn("no typed-key annotation", row["reason"])

    def test_equals(self):
        check = {"op": "equals", "left": {"sel": "frontmatter:#/name"}, "right": {"sel": "path:basename"}}
        self.assertEqual(self.status(check), "pass")
        row = self.row(check, name="other", fm_name="else")
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["reason"], "left 'else' right 'other' value '{value}'")

    def test_equals_uses_json_types(self):
        files = {"d.json": '{"a": 1, "b": true}'}
        self.assertEqual(self.status({"op": "equals", "left": {"sel": "json:d.json#/a"}, "right": {"value": 1.0}},
                                     files), "pass")
        self.assertEqual(self.status({"op": "equals", "left": {"sel": "json:d.json#/b"}, "right": {"value": 1}},
                                     files, name="b"), "fail")

    def test_subset(self):
        check = {"op": "subset", "left": {"sel": "json:d.json#/a"}, "right": {"value": [1, 2, 3]}}
        self.assertEqual(self.status(check, {"d.json": '{"a": [1, 2]}'}), "pass")
        self.assertEqual(self.status(check, {"d.json": '{"a": [1, 4]}'}, name="b"), "fail")

    def test_set_equals(self):
        check = {"op": "set_equals", "left": {"sel": "json:d.json#/a"}, "right": {"value": [1, 2]}}
        self.assertEqual(self.status(check, {"d.json": '{"a": [2, 1, 2]}'}), "pass")
        self.assertEqual(self.status(check, {"d.json": '{"a": [1]}'}, name="b"), "fail")

    def test_contains_text(self):
        check = {"op": "contains_text", "sel": "text:notes.md", "text": "hello"}
        self.assertEqual(self.status(check, {"notes.md": "say hello\n"}), "pass")
        self.assertEqual(self.status(check, {"notes.md": "say bye\n"}, name="b"), "fail")

    def test_regex(self):
        self.assertEqual(self.status({"op": "regex", "sel": "frontmatter:#/name", "pattern": "^de"}), "pass")
        self.assertEqual(self.status({"op": "regex", "sel": "frontmatter:#/name", "pattern": "^zz"}, name="b"), "fail")

    def test_not_regex(self):
        self.assertEqual(self.status({"op": "not_regex", "sel": "frontmatter:#/name", "pattern": "^zz"}), "pass")
        self.assertEqual(self.status({"op": "not_regex", "sel": "frontmatter:#/name", "pattern": "^de"}, name="demo"),
                         "fail")

    def test_regex_and_not_regex_search_anywhere_in_the_value(self):
        self.assertEqual(self.status({"op": "regex", "sel": "frontmatter:#/name", "pattern": "em"}), "pass")
        self.assertEqual(self.status({"op": "not_regex", "sel": "frontmatter:#/name", "pattern": "em"}, name="demo",
                                     folder="u2"), "fail")
        self.assertEqual(self.status({"op": "regex", "sel": "frontmatter:#/name", "pattern": "zz"}, name="demo",
                                     folder="u3"), "fail")

    def test_file_exists(self):
        self.assertEqual(self.status(FILE_A, {"a.txt": "x"}), "pass")
        self.assertEqual(self.status(FILE_A, name="b"), "fail")
        self.assertEqual(self.status({"op": "file_exists", "path": "dir"}, {"dir/x.txt": "x"}, name="c"), "fail")

    def test_file_absent(self):
        self.assertEqual(self.status({"op": "file_absent", "path": "a.txt"}), "pass")
        self.assertEqual(self.status({"op": "file_absent", "path": "a.txt"}, {"a.txt": "x"}, name="b"), "fail")

    def test_count(self):
        check = {"op": "count", "sel": "glob:refs/*.md", "min": 1, "max": 2}
        self.assertEqual(self.status(check, {"refs/a.md": "a", "refs/b.md": "b"}), "pass")
        row = self.row(check, {"refs/a.md": "a", "refs/b.md": "b", "refs/c.md": "c"}, name="b")
        self.assertEqual(row["status"], "fail")
        self.assertEqual(self.status(check, name="c"), "fail")

    def test_max_bytes(self):
        self.assertEqual(self.status({"op": "max_bytes", "path": "a.txt", "max": 3}, {"a.txt": "abc"}), "pass")
        self.assertEqual(self.status({"op": "max_bytes", "path": "a.txt", "max": 2}, {"a.txt": "abc"}, name="b"),
                         "fail")

    def test_max_lines(self):
        self.assertEqual(self.status({"op": "max_lines", "path": "a.txt", "max": 2}, {"a.txt": "1\n2\n"}), "pass")
        self.assertEqual(self.status({"op": "max_lines", "path": "a.txt", "max": 1}, {"a.txt": "1\n2\n"}, name="b"),
                         "fail")

    def test_all(self):
        self.assertEqual(self.status({"op": "all", "of": [FILE_A, FILE_A]}, {"a.txt": "x"}), "pass")
        self.assertEqual(self.status({"op": "all", "of": [FILE_A, FILE_NONE]}, {"a.txt": "x"}, name="b"), "fail")

    def test_any(self):
        self.assertEqual(self.status({"op": "any", "of": [FILE_NONE, FILE_A]}, {"a.txt": "x"}), "pass")
        self.assertEqual(self.status({"op": "any", "of": [FILE_NONE, FILE_NONE]}, {"a.txt": "x"}, name="b"), "fail")

    def test_not(self):
        self.assertEqual(self.status({"op": "not", "check": FILE_NONE}), "pass")
        self.assertEqual(self.status({"op": "not", "check": FILE_A}, {"a.txt": "x"}, name="b"), "fail")

    def test_when(self):
        check = {"op": "when", "if": FILE_A, "then": {"op": "contains_text", "sel": "text:a.txt", "text": "ok"}}
        self.assertEqual(self.status(check, {"a.txt": "ok"}), "pass")
        self.assertEqual(self.status(check, {"a.txt": "no"}, name="b"), "fail")
        row = self.row(check, name="c")
        self.assertEqual((row["status"], row["passed"]), ("n/a", None))

    def test_when_inside_all_and_any(self):
        na = {"op": "when", "if": FILE_NONE, "then": FILE_A}
        self.assertEqual(self.status({"op": "all", "of": [na, na]}), "n/a")
        self.assertEqual(self.status({"op": "all", "of": [na, FILE_NONE]}, name="b"), "fail")
        self.assertEqual(self.status({"op": "any", "of": [na, FILE_A]}, {"a.txt": "x"}, name="c"), "pass")
        self.assertEqual(self.status({"op": "not", "check": na}, name="d"), "n/a")


NO_MATCH = {"op": "regex", "sel": "frontmatter:#/name", "pattern": "zz"}
ABSENT = {"op": "regex", "sel": "text:no-such-file.txt", "pattern": "zz"}


class TestCombinatorsKeepAnAbsentFailure(Base):
    def test_all_and_any_stay_unusable_in_either_child_order_and_not_never_passes(self):
        n = 0
        for op in ("all", "any"):
            for kids in ([NO_MATCH, ABSENT], [ABSENT, NO_MATCH]):
                for wrap in (False, True):
                    check = {"op": op, "of": kids}
                    if wrap:
                        check = {"op": "not", "check": check}
                    n += 1
                    with self.subTest(check=check):
                        row = self.row(check, name="u%d" % n)
                        self.assertEqual(row["status"], "fail")
                        self.assertIn("absent: 'no-such-file.txt' is not a file in the unit", row["reason"])

    def test_not_over_nested_combinators_never_passes_on_an_absent_value(self):
        inner = {"op": "all", "of": [{"op": "any", "of": [NO_MATCH, ABSENT]}, FILE_NONE]}
        for check in ({"op": "not", "check": inner}, {"op": "not", "check": {"op": "not", "check": {"op": "not", "check": inner}}}):
            with self.subTest(check=check):
                self.assertEqual(self.status(check, name="n%d" % len(str(check))), "fail")

    def test_all_and_any_report_the_first_failing_child(self):
        a = {"op": "file_exists", "path": "a.txt"}
        b = {"op": "file_exists", "path": "b.txt"}
        n = 0
        for op in ("all", "any"):
            for kids, first in (([a, b], "a.txt"), ([b, a], "b.txt")):
                n += 1
                with self.subTest(op=op, first=first):
                    path = self.unit(name="o%d" % n)
                    bnd = self.bundle(rule({"op": op, "of": kids}, message="missing {path}"))
                    r = eva.Report(path, "skill")
                    bnd.apply(r)
                    row = next(c for c in r.checks if c["id"] == "t-rule")
                    self.assertEqual((row["status"], row["reason"]), ("fail", "missing " + first))

    def test_a_usable_failure_under_not_is_still_a_pass(self):
        for check in ({"op": "not", "check": {"op": "any", "of": [NO_MATCH, FILE_NONE]}},
                      {"op": "not", "check": {"op": "all", "of": [FILE_NONE, NO_MATCH]}}):
            with self.subTest(check=check):
                self.assertEqual(self.status(check, name="p%d" % len(str(check))), "pass")


class TestSelectors(Base):
    def test_frontmatter(self):
        check = {"op": "equals", "left": {"sel": "frontmatter:#/name"}, "right": {"value": "demo"}}
        self.assertEqual(self.status(check), "pass")
        self.assertEqual(self.status({"op": "equals", "left": {"sel": "frontmatter:/name"}, "right": {"value": "demo"}},
                                     name="demo", folder="u2"), "pass")
        row = self.row({"op": "regex", "sel": "frontmatter:#/missing", "pattern": ""}, name="b")
        self.assertEqual(row["status"], "fail")
        self.assertIn("absent", row["reason"])

    def test_frontmatter_of_a_unit_without_valid_frontmatter_is_absent(self):
        path = os.path.join(self.tmp, "broken")
        write(os.path.join(path, "SKILL.md"), "no frontmatter\n")
        self.assertEqual(self.status({"op": "regex", "sel": "frontmatter:", "pattern": ""}, unit=path), "fail")

    def test_json(self):
        check = {"op": "equals", "left": {"sel": "json:data/d.json#/a/0"}, "right": {"value": "x"}}
        self.assertEqual(self.status(check, {"data/d.json": '{"a": ["x"]}'}), "pass")
        self.assertEqual(self.status(check, name="b"), "fail")
        self.assertEqual(self.status(check, {"data/d.json": '{"a": []}'}, name="c"), "fail")
        self.assertEqual(self.status(check, {"data/d.json": '{"a": ["x"], "a": ["x"]}'}, name="d"), "fail")
        self.assertEqual(self.status(check, {"data/d.json": '{"a": [NaN]}'}, name="e"), "fail")

    def test_toml(self):
        check = {"op": "equals", "left": {"sel": "toml:w.toml#/name"}, "right": {"value": "x"}}
        self.assertEqual(self.status(check, {"w.toml": 'name = "x"\n'}), "pass")
        self.assertEqual(self.status(check, name="b"), "fail")
        self.assertEqual(self.status(check, {"w.toml": "[table]\nname = 'x'\n"}, name="c"), "fail")
        self.assertEqual(self.status(check, {"w.toml": "[[table]]\nname = 'x'\n"}, name="d"), "fail")
        nested = {"op": "equals", "left": {"sel": "toml:w.toml#/table/name"}, "right": {"value": "x"}}
        self.assertEqual(self.status(nested, {"w.toml": "[table]\nname = 'x'\n"}, name="e"), "pass")

    def test_yaml(self):
        check = {"op": "equals", "left": {"sel": "yaml:agents/openai.yaml#/policy/allow_implicit_invocation"},
                 "right": {"value": "false"}}
        self.assertEqual(self.status(check, {"agents/openai.yaml": "policy:\n  allow_implicit_invocation: false\n"}),
                         "pass")
        self.assertEqual(self.status(check, name="b"), "fail")
        self.assertEqual(self.status(check, {"agents/openai.yaml": "---\npolicy:\n  allow_implicit_invocation: false\n"},
                                     name="c"), "fail")

    def test_yaml_gives_the_schema_op_its_typed_keys(self):
        troot = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(troot, "schemas"))
        write(os.path.join(troot, "schemas", "y.schema.json"),
              json.dumps({"properties": {"policy": {"properties": {"allow_implicit_invocation": {"x-yaml-string": True}}}}}))
        check = {"op": "schema", "sel": "yaml:agents/openai.yaml", "schema": "y.schema.json"}
        self.assertEqual(self.status(check, {"agents/openai.yaml": "policy:\n  allow_implicit_invocation: \"false\"\n"},
                                     root=troot), "pass")
        self.assertEqual(self.status(check, {"agents/openai.yaml": "policy:\n  allow_implicit_invocation: false\n"},
                                     root=troot, name="b"), "fail")

    def test_markdown(self):
        check = {"op": "contains_text", "sel": "markdown:SKILL.md", "text": "Body text."}
        self.assertEqual(self.status(check), "pass")
        self.assertEqual(self.status({"op": "contains_text", "sel": "markdown:SKILL.md", "text": "name:"}, name="b"),
                         "fail")
        self.assertEqual(self.status({"op": "contains_text", "sel": "markdown:none.md", "text": "x"}, name="c"), "fail")

    def test_text(self):
        check = {"op": "contains_text", "sel": "text:SKILL.md", "text": "name: demo"}
        self.assertEqual(self.status(check), "pass")
        self.assertEqual(self.status({"op": "contains_text", "sel": "text:none.md", "text": "x"}, name="b"), "fail")

    def test_glob(self):
        check = {"op": "set_equals", "left": {"sel": "glob:refs/**/*.md"}, "right": {"value": ["refs/a.md", "refs/x/b.md"]}}
        self.assertEqual(self.status(check, {"refs/a.md": "a", "refs/x/b.md": "b", "refs/c.txt": "c"}), "pass")
        self.assertEqual(self.status({"op": "count", "sel": "glob:refs/*.md", "min": 1}, name="b"), "fail")

    def test_glob_skips_links_that_leave_the_unit(self):
        path = self.unit({"refs/a.md": "a"})
        write(os.path.join(self.tmp, "outside.md"), "o")
        os.symlink(os.path.join(self.tmp, "outside.md"), os.path.join(path, "refs", "out.md"))
        check = {"op": "set_equals", "left": {"sel": "glob:refs/*.md"}, "right": {"value": ["refs/a.md"]}}
        self.assertEqual(self.status(check, unit=path), "pass")

    def test_a_nul_byte_from_a_template_in_a_glob_is_a_failing_row(self):
        path = os.path.join(self.tmp, "units", "hostile")
        write(os.path.join(path, "SKILL.md"), '---\nname: hostile\ndescription: %s\nnul: "a\\0b"\n---\n\nBody.\n' % DESC)
        row = self.row({"op": "count", "sel": "glob:{nul}/*", "min": 0}, unit=path)
        self.assertEqual(row["status"], "fail")
        self.assertIn("NUL byte", row["reason"])

    def test_path_basename(self):
        check = {"op": "equals", "left": {"sel": "path:basename"}, "right": {"value": "demo"}}
        self.assertEqual(self.status(check), "pass")
        self.assertEqual(self.status(check, name="other"), "fail")

    def test_name_template(self):
        check = {"op": "file_exists", "path": "data/{name}.json"}
        self.assertEqual(self.status(check, {"data/demo.json": "{}"}), "pass")
        self.assertEqual(self.status(check, name="b"), "fail")
        sel = {"op": "regex", "sel": "json:data/{name}.json#/a", "pattern": "^x$"}
        self.assertEqual(self.status(sel, {"data/c.json": '{"a": "x"}'}, name="c"), "pass")
        row = self.row({"op": "file_exists", "path": "data/{license}.json"}, name="d")
        self.assertEqual(row["status"], "fail")
        self.assertIn("license", row["reason"])

    def test_normalize(self):
        fullwidth = "\uff44\uff45\uff4d\uff4f"
        check = {"op": "equals", "left": {"sel": "frontmatter:#/name", "normalize": ["strip", "nfkc"]},
                 "right": {"sel": "path:basename", "normalize": ["nfkc"]}}
        self.assertEqual(self.status(check, fm_name=fullwidth), "pass")
        raw = {"op": "equals", "left": {"sel": "frontmatter:#/name"}, "right": {"sel": "path:basename"}}
        self.assertEqual(self.status(raw, fm_name=fullwidth, folder="u2"), "fail")
        self.assertEqual(self.status({"op": "contains_text", "sel": "text:a.txt", "text": "ab", "normalize": ["strip"]},
                                     {"a.txt": "  ab  "}, name="b"), "pass")


class TestAbsentIsNeverPresent(Base):
    def test_an_absent_value_fails_every_value_op(self):
        absent = {"sel": "frontmatter:#/missing"}
        for check in ({"op": "equals", "left": absent, "right": absent},
                      {"op": "equals", "left": absent, "right": {"value": None}},
                      {"op": "subset", "left": absent, "right": {"value": []}},
                      {"op": "set_equals", "left": absent, "right": absent},
                      {"op": "contains_text", "sel": "frontmatter:#/missing", "text": "x"},
                      {"op": "regex", "sel": "frontmatter:#/missing", "pattern": ""},
                      {"op": "not_regex", "sel": "frontmatter:#/missing", "pattern": "x"},
                      {"op": "count", "sel": "frontmatter:#/missing", "max": 5},
                      {"op": "max_bytes", "path": "none.txt", "max": 5},
                      {"op": "max_lines", "path": "none.txt", "max": 5}):
            with self.subTest(op=check["op"]):
                row = self.row(check)
                self.assertEqual(row["status"], "fail")
                self.assertIn("absent", row["reason"])

    def test_not_never_turns_an_absent_value_into_a_pass(self):
        write(os.path.join(self.tmp, "outside.txt"), "x")
        path = self.unit()
        os.symlink(os.path.join(self.tmp, "outside.txt"), os.path.join(path, "out.txt"))
        for inner in ({"op": "regex", "sel": "frontmatter:#/missing", "pattern": "x"},
                      {"op": "equals", "left": {"sel": "json:none.json#/a"}, "right": {"value": 1}},
                      {"op": "file_exists", "path": "out.txt"},
                      {"op": "regex", "sel": "json:d.json#/a", "pattern": "x"}):
            with self.subTest(inner=inner):
                self.assertEqual(self.status({"op": "not", "check": inner}, unit=path), "fail")

    def test_a_wrong_type_is_unusable(self):
        files = {"d.json": '{"a": 1, "l": "x"}'}
        self.assertEqual(self.status({"op": "regex", "sel": "json:d.json#/a", "pattern": "1"}, files), "fail")
        self.assertEqual(self.status({"op": "not", "check": {"op": "regex", "sel": "json:d.json#/a", "pattern": "1"}},
                                     files, name="b"), "fail")
        self.assertEqual(self.status({"op": "count", "sel": "json:d.json#/l", "max": 5}, files, name="c"), "fail")


class TestUnitEscape(Base):
    def test_a_literal_path_that_leaves_the_unit_fails_the_load(self):
        for check in ({"op": "regex", "sel": "json:../x.json#/a", "pattern": ""},
                      {"op": "regex", "sel": "text:/etc/hosts", "pattern": ""},
                      {"op": "regex", "sel": "markdown:~/x.md", "pattern": ""},
                      {"op": "count", "sel": "glob:../*", "min": 1},
                      {"op": "count", "sel": "glob:a/../../*", "min": 1},
                      {"op": "file_exists", "path": "refs" + chr(92) + "a.md"},
                      {"op": "file_absent", "path": "../{name}.json"},
                      {"op": "max_bytes", "path": "C:/x", "max": 1}):
            with self.subTest(check=check):
                self.assertIn("is not allowed", self.load_error({"rules.json": rule_file(rule(check))}))

    def test_a_link_that_leaves_the_unit_fails_at_run_time(self):
        write(os.path.join(self.tmp, "outside.txt"), "secret")
        path = self.unit()
        os.symlink(os.path.join(self.tmp, "outside.txt"), os.path.join(path, "out.txt"))
        for check in ({"op": "contains_text", "sel": "text:out.txt", "text": "secret"},
                      {"op": "file_exists", "path": "out.txt"},
                      {"op": "file_absent", "path": "out.txt"},
                      {"op": "max_bytes", "path": "out.txt", "max": 100}):
            with self.subTest(check=check):
                row = self.row(check, unit=path)
                self.assertEqual(row["status"], "fail")
                self.assertIn("outside the unit", row["reason"])

    def test_a_template_that_renders_outside_the_unit_fails_at_run_time(self):
        write(os.path.join(self.tmp, "units", "evil.json"), '{"a": "x"}')
        row = self.row({"op": "regex", "sel": "json:{name}.json#/a", "pattern": "x"}, fm_name="../evil")
        self.assertEqual(row["status"], "fail")
        self.assertIn("is not allowed", row["reason"])


class TestBundleLoadFails(Base):
    def test_a_reserved_op_fails_the_load(self):
        self.assertEqual(set(RR.RESERVED_OPS), RESERVED)
        with mock.patch.dict(RR.RESERVED_OPS, {"file_exists": "Z9"}):
            self.assertIn("file_exists", self.load_error({"rules.json": rule_file(rule(FILE_A))}))
            with self.assertRaises(RR.RuleError) as cm:
                RR.compile_check(FILE_A)
            self.assertIn("op 'file_exists' is reserved until unit Z9", str(cm.exception))

    def test_an_unknown_op_fails_the_load(self):
        self.assertIn("frobnicate", self.load_error({"rules.json": rule_file(rule({"op": "frobnicate"}))}))
        with self.assertRaises(RR.RuleError) as cm:
            RR.compile_check({"op": "frobnicate"})
        self.assertEqual(str(cm.exception), "unknown op 'frobnicate'")

    def test_an_unknown_selector_fails_the_load(self):
        for sel in ("env:HOME", "frontmatter", "path:dirname", "glob"):
            check = {"op": "regex", "sel": sel, "pattern": ""}
            with self.subTest(sel=sel):
                self.assertIn(sel, self.load_error({"rules.json": rule_file(rule(check))}))
                with self.assertRaises(RR.RuleError) as cm:
                    RR.compile_check(check)
                self.assertIn("unknown selector '%s'" % sel, str(cm.exception))

    def test_a_bad_pointer_or_regex_fails_the_load(self):
        for check in ({"op": "regex", "sel": "frontmatter:name", "pattern": ""},
                      {"op": "regex", "sel": "json:a.json#name", "pattern": ""},
                      {"op": "regex", "sel": "frontmatter:#/a~2", "pattern": ""},
                      {"op": "regex", "sel": "frontmatter:#/name", "pattern": "("}):
            with self.subTest(check=check):
                self.load_error({"rules.json": rule_file(rule(check))})

    def test_a_rule_file_that_fails_the_rules_schema_fails_the_load(self):
        r = rule(FILE_A)
        del r["fix"]
        self.assertIn("required property 'fix' is missing", self.load_error({"rules.json": rule_file(r)}))
        r = dict(rule(FILE_A), extra=1)
        self.assertIn("property 'extra' is not allowed", self.load_error({"rules.json": rule_file(r)}))
        for key, value in (("severity", "warn"), ("category", "style"), ("applies_to", []), ("applies_to", ["tool"]),
                           ("protects", ["cursor"]), ("source", {"url": "https://x"}), ("id", "Bad_Id"),
                           ("check", {"op": "file_exists", "path": "a", "extra": 1}),
                           ("check", {"op": "count", "sel": "glob:*", "min": 3, "max": 2}),
                           ("check", {"op": "count", "sel": "glob:*"}),
                           ("check", {"op": "regex", "sel": "text:a", "pattern": "("}),
                           ("check", {"op": "all", "of": []})):
            with self.subTest(key=key, value=value):
                self.load_error({"rules.json": rule_file(dict(rule(FILE_A), **{key: value}))})
        self.assertIn("rules", self.load_error({"rules.json": json.dumps({"ruleset": "t", "version": "1.0.0", "rules": []})}))

    def test_strict_json_in_a_rule_file(self):
        self.assertIn('duplicate key "id"', self.load_error({"rules.json": rule_file(rule(FILE_A)).replace(
            '"id": "t-rule"', '"id": "t-rule", "id": "t-rule"')}))
        self.assertIn("NaN", self.load_error({"rules.json": '{"ruleset": NaN}'}))

    def test_duplicate_rule_ids_across_files_fail_the_load(self):
        msg = self.load_error({"a.rules.json": rule_file(rule(FILE_A)), "b.rules.json": rule_file(rule(FILE_NONE))})
        self.assertIn("duplicate rule id 't-rule'", msg)
        self.assertIn("a.rules.json", msg)

    def test_a_rule_id_may_repeat_across_files_only_for_disjoint_kinds(self):
        skill_rule, agent_rule = rule(FILE_A, applies_to=("skill",)), rule(FILE_NONE, applies_to=("agent",))
        b = RR.load_bundle(self.bundle_dir({"a.rules.json": rule_file(skill_rule), "b.rules.json": rule_file(agent_rule)}))
        self.assertEqual([(r.id, r.applies_to) for r in b.rules], [("t-rule", ("skill",)), ("t-rule", ("agent",))])
        msg = self.load_error({"a.rules.json": rule_file(skill_rule),
                               "b.rules.json": rule_file(rule(FILE_NONE, applies_to=("skill", "agent")))})
        self.assertIn("duplicate rule id 't-rule'", msg)

    def test_the_id_of_a_repository_rule_never_repeats_for_another_kind(self):
        for name in ("a-dup.rules.json", "z-dup.rules.json"):
            with self.subTest(name=name):
                folder = os.path.join(tempfile.mkdtemp(dir=self.tmp), "rules")
                shutil.copytree(os.path.join(ROOT, "rules"), folder)
                write(os.path.join(folder, name), rule_file(rule(FILE_A, rid="english-ruleset", applies_to=("agent",))))
                with self.assertRaises(RR.BundleError) as cm:
                    RR.load_bundle(folder)
                self.assertIn("duplicate rule id 'english-ruleset'", str(cm.exception))

    def test_duplicate_rule_ids_in_one_file_fail_the_load(self):
        self.assertIn('duplicate id "t-rule"', self.load_error({"rules.json": rule_file(rule(FILE_A), rule(FILE_NONE))}))

    def test_the_reserved_row_id_fails_the_load(self):
        self.assertIn("bundle-non-default",
                      self.load_error({"rules.json": rule_file(rule(FILE_A, rid="bundle-non-default"))}))

    def test_a_missing_bundle_folder_or_a_file_fails(self):
        with self.assertRaises(RR.BundleError):
            RR.load_bundle(os.path.join(self.tmp, "missing"))
        write(os.path.join(self.tmp, "f"), "x")
        with self.assertRaises(RR.BundleError):
            RR.load_bundle(os.path.join(self.tmp, "f"))

    def test_a_link_inside_the_bundle_fails(self):
        folder = self.bundle_dir({"rules.json": rule_file(rule(FILE_A))})
        write(os.path.join(self.tmp, "elsewhere.json"), "{}")
        os.symlink(os.path.join(self.tmp, "elsewhere.json"), os.path.join(folder, "x.rules.json"))
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(folder)
        self.assertIn("x.rules.json", str(cm.exception))

    def test_only_rule_files_are_loaded_as_rules(self):
        b = RR.load_bundle(self.bundle_dir({"rules.json": rule_file(rule(FILE_A)), "data/x.json": "not json",
                                            "notes.txt": "x", "sub/y.rules.json": "not json"}))
        self.assertEqual([r.id for r in b.rules], ["t-rule"])

    def test_a_top_level_json_that_is_not_a_rule_file_is_hashed_but_not_loaded(self):
        folder = self.bundle_dir({"rules.json": rule_file(rule(FILE_A)), "notes.json": "not json",
                                  "extra.json": rule_file(rule(FILE_NONE, rid="other-rule")),
                                  "rules.json.bak": "not json"})
        b = RR.load_bundle(folder)
        self.assertEqual([r.id for r in b.rules], ["t-rule"])
        labels = [l for l, _ in RR.ruleset_pairs(folder, RR.ROOT)]
        self.assertIn("rules/notes.json", labels)
        self.assertIn("rules/extra.json", labels)

    def test_a_rule_regex_that_overflows_or_recurses_fails_the_load(self):
        for pattern in ("a{99999999999}", "(" * 5000):
            for op in ("regex", "not_regex"):
                check = {"op": op, "sel": "text:a.txt", "pattern": pattern}
                with self.subTest(op=op, pattern=pattern[:20]):
                    self.assertIn("does not compile", self.load_error({"rules.json": rule_file(rule(check))}))
                    with self.assertRaises(RR.RuleError) as cm:
                        RR.compile_check(check)
                    self.assertIn("does not compile", str(cm.exception))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads a file of mode 0")
    def test_an_unreadable_file_in_a_hashed_tree_or_a_rule_file_fails_the_load(self):
        folder = self.bundle_dir({"rules.json": rule_file(rule(FILE_A)), "data/notes.json": "{}"})
        locked = os.path.join(folder, "data", "notes.json")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o644)
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(folder)
        self.assertIn("rules/data/notes.json: cannot read", str(cm.exception))
        folder = self.bundle_dir({"rules.json": rule_file(rule(FILE_A))})
        locked = os.path.join(folder, "rules.json")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o644)
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(folder)
        self.assertIn("rules/rules.json: cannot read", str(cm.exception))
        troot = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(troot, "schemas"))
        write(os.path.join(troot, "targets", "t.json"), "{}")
        locked = os.path.join(troot, "targets", "t.json")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o644)
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(SELFTEST, root=troot)
        self.assertIn("targets/t.json: cannot read", str(cm.exception))


class TestNoSchemaBypass(Base):
    def test_a_rule_file_schema_key_does_not_select_the_schema(self):
        permissive = self.bundle_dir({"loose.schema.json": "{}"})
        doc = json.loads(rule_file(rule({"op": "frobnicate"})))
        doc["$schema"] = os.path.join(permissive, "loose.schema.json")
        self.assertIn("frobnicate", self.load_error({"rules.json": json.dumps(doc)}))

    def test_the_schema_op_reads_only_the_engine_schemas_folder(self):
        check = {"op": "schema", "sel": "json:d.json", "schema": "own.schema.json"}
        msg = self.load_error({"rules.json": rule_file(rule(check)), "own.schema.json": "{}"})
        self.assertIn("own.schema.json", msg)
        for name in ("../rules.schema.json", "sub/own.schema.json", "own.json"):
            with self.subTest(name=name):
                self.load_error({"rules.json": rule_file(rule(dict(check, schema=name)))})

    def test_a_schema_op_schema_that_the_validator_refuses_fails_the_load(self):
        troot = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(troot, "schemas"))
        write(os.path.join(troot, "schemas", "bad.schema.json"), json.dumps({"format": "email"}))
        check = {"op": "schema", "sel": "json:d.json", "schema": "bad.schema.json"}
        self.assertIn("format", self.load_error({"rules.json": rule_file(rule(check))}, root=troot))


class TestSeverity(Base):
    def test_fail_soft_and_review_on_a_failing_check(self):
        for severity, status, must in (("fail", "fail", True), ("soft", "fail", False), ("review", "needs-review", True)):
            with self.subTest(severity=severity):
                row = self.row(FILE_NONE, severity=severity, name="u-" + severity)
                self.assertEqual((row["status"], row["must"]), (status, must))
                self.assertEqual(row["fix"], "Fix the fixture.")

    def test_a_passing_check_is_a_pass_row_with_the_same_must(self):
        for severity, must in (("fail", True), ("soft", False), ("review", True)):
            with self.subTest(severity=severity):
                row = self.row(FILE_A, {"a.txt": "x"}, severity=severity, name="u-" + severity)
                self.assertEqual((row["status"], row["passed"], row["must"], row["fix"]), ("pass", True, must, ""))

    def test_a_false_when_is_na(self):
        for severity in ("fail", "soft", "review"):
            with self.subTest(severity=severity):
                row = self.row({"op": "when", "if": FILE_NONE, "then": FILE_A}, severity=severity, name="u-" + severity)
                self.assertEqual((row["status"], row["passed"]), ("n/a", None))
                self.assertTrue(row["reason"].startswith("N/A: "))

    def test_a_soft_fail_counts_in_the_score_and_a_critical_id_stays_must(self):
        path = self.unit()
        b = self.bundle(rule(FILE_NONE, severity="soft"), rule(FILE_NONE, rid="frontmatter", severity="soft"))
        r = eva.Report(path, "skill")
        r.add("x", "structure", True, "ok")
        b.apply(r)
        self.assertEqual(r.score, round(1 / 3, 3))
        self.assertEqual([(c["id"], c["must"]) for c in r.checks],
                         [("x", False), ("t-rule", False), ("frontmatter", True), ("bundle-non-default", True)])

    def test_rules_apply_only_to_their_kind(self):
        path = self.unit()
        b = self.bundle(rule(FILE_NONE, applies_to=["agent"]))
        for kind in ("skill", "unknown"):
            r = eva.Report(path, kind)
            b.apply(r)
            self.assertEqual([c["id"] for c in r.checks], ["bundle-non-default"])


class TestNonDefaultBundle(Base):
    def test_the_row_caps_a_would_be_pass_at_review(self):
        path = self.unit({"a.txt": "x"})
        b = self.bundle(rule(FILE_A, severity="soft"))
        r = eva.Report(path, "skill")
        r.add("x", "structure", True, "ok")
        self.assertEqual(r.verdict, "PASS")
        b.apply(r)
        self.assertEqual(r.verdict, "REVIEW")
        row = next(c for c in r.checks if c["id"] == "bundle-non-default")
        self.assertEqual(row["status"], "needs-review")
        self.assertIn("bundle non-default %s" % b.display, row["reason"])

    def test_fail_stays_fail(self):
        path = self.unit()
        b = self.bundle(rule(FILE_A))
        r = eva.Report(path, "skill")
        r.add("x", "structure", False, "bad", must=True)
        b.apply(r)
        self.assertEqual(r.verdict, "FAIL")
        self.assertIn("bundle-non-default", [c["id"] for c in r.checks])

    def test_an_empty_non_default_bundle_still_adds_the_row(self):
        b = RR.load_bundle(self.bundle_dir({}))
        r = eva.Report(self.unit(), "skill")
        r.add("x", "structure", True, "ok")
        b.apply(r)
        self.assertEqual(r.verdict, "REVIEW")

    def test_naming_the_default_folder_is_still_non_default(self):
        troot = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(troot, "rules"))
        self.assertTrue(RR.load_bundle(root=troot).default)
        named = RR.load_bundle(os.path.join(troot, "rules"), root=troot)
        self.assertFalse(named.default)
        r = eva.Report(self.unit(), "skill")
        r.add("x", "structure", True, "ok")
        named.apply(r)
        self.assertEqual(r.verdict, "REVIEW")

    def test_the_default_bundle_is_cached_and_an_empty_default_folder_adds_nothing(self):
        b = RR.default_bundle()
        self.assertTrue(b.default)
        self.assertIs(RR.default_bundle(), b)
        troot = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(troot, "rules"))
        empty = RR.load_bundle(root=troot)
        self.assertTrue(empty.default)
        self.assertEqual(empty.rules, [])
        r = eva.Report(self.unit(), "skill")
        r.add("x", "structure", True, "ok")
        empty.apply(r)
        self.assertEqual((r.verdict, len(r.checks)), ("PASS", 1))

    def test_eva_check_runs_the_given_bundle(self):
        b = RR.load_bundle(SELFTEST)
        r = eva.check(PASSING_UNIT, b)
        self.assertEqual(r.verdict, "REVIEW")
        row = next(c for c in r.checks if c["id"] == "selftest-name-matches-folder")
        self.assertEqual(row["status"], "pass")
        self.assertEqual(eva.check(PASSING_UNIT).verdict, "PASS")


class TestRulesetHash(Base):
    def test_the_engine_hash_function_is_skill_evals(self):
        self.assertEqual(inspect.getsource(RR.sha256_labeled), inspect.getsource(skill_eval.sha256_labeled))
        write(os.path.join(self.tmp, "a.txt"), "alpha")
        pairs = [("a.txt", os.path.join(self.tmp, "a.txt")), ("gone.txt", os.path.join(self.tmp, "gone.txt"))]
        marked = [("link", b"<outside>")]
        self.assertEqual(RR.sha256_labeled(pairs, marked), skill_eval.sha256_labeled(pairs, marked))
        self.assertEqual(RR.sha256_labeled(pairs), skill_eval.sha256_labeled(pairs))

    def test_the_hash_covers_rules_schemas_targets_and_the_policy_files(self):
        troot = os.path.join(self.tmp, "root")
        for rel in ("rules/data/words.json", "rules/a.rules.json", "schemas/s.schema.json", "targets/codex.json",
                    "policy/gates.json", "policy/pricing.json", "other/x.json"):
            write(os.path.join(troot, *rel.split("/")), rel)
        j = lambda rel: os.path.join(troot, *rel.split("/"))
        expected = [("policy/eval-policy.json", j("policy/eval-policy.json")), ("policy/gate.rules.json",
                    j("policy/gate.rules.json")), ("policy/gates.json", j("policy/gates.json")),
                    ("rules/a.rules.json", j("rules/a.rules.json")), ("rules/data/words.json", j("rules/data/words.json")),
                    ("schemas/s.schema.json", j("schemas/s.schema.json")), ("targets/codex.json", j("targets/codex.json"))]
        self.assertEqual(RR.ruleset_pairs(j("rules"), troot), expected)

    def test_the_default_hash_is_sha256_labeled_over_the_default_pairs(self):
        pairs = RR.ruleset_pairs(os.path.join(ROOT, "rules"), ROOT)
        self.assertIn(("schemas/rules.schema.json", os.path.join(ROOT, "schemas", "rules.schema.json")), pairs)
        self.assertIn(("policy/gates.json", os.path.join(ROOT, "policy", "gates.json")), pairs)
        self.assertEqual(RR.default_bundle().sha256, skill_eval.sha256_labeled(pairs))

    def test_bundle_files_are_labeled_by_relative_path(self):
        pairs = RR.ruleset_pairs(SELFTEST, ROOT)
        labels = [l for l, _ in pairs]
        self.assertIn("rules/rules.json", labels)
        self.assertIn("rules/selftest-name-matches-folder/pass/good-name/SKILL.md", labels)
        self.assertEqual(labels, sorted(labels))
        self.assertTrue(all(not os.path.isabs(l) for l in labels))

    def test_one_bundle_hashes_the_same_in_two_folders_and_one_byte_changes_it(self):
        a, b = os.path.join(self.tmp, "a", "_selftest"), os.path.join(self.tmp, "b", "x")
        shutil.copytree(SELFTEST, a)
        shutil.copytree(SELFTEST, b)
        ha, hb = RR.load_bundle(a).sha256, RR.load_bundle(b).sha256
        self.assertEqual(ha, hb)
        self.assertEqual(ha, RR.load_bundle(SELFTEST).sha256)
        fp = os.path.join(b, "rules.json")
        with open(fp, "rb") as f:
            data = f.read()
        with open(fp, "wb") as f:
            f.write(data.replace(b'"1.0.0"', b'"1.0.1"', 1))
        self.assertNotEqual(RR.load_bundle(b).sha256, ha)

    def test_a_schemas_targets_or_policy_change_changes_the_hash(self):
        troot = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(troot, "schemas"))
        seen = {RR.load_bundle(SELFTEST, root=troot).sha256}
        for rel in ("schemas/x.schema.json", "targets/claude.json", "policy/gates.json"):
            write(os.path.join(troot, *rel.split("/")), "{}")
            seen.add(RR.load_bundle(SELFTEST, root=troot).sha256)
        self.assertEqual(len(seen), 4)

    def test_a_link_inside_a_hashed_tree_is_a_bundle_error(self):
        troot = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(ROOT, "schemas"), os.path.join(troot, "schemas"))
        write(os.path.join(self.tmp, "t.json"), "{}")
        os.makedirs(os.path.join(troot, "targets"))
        os.symlink(os.path.join(self.tmp, "t.json"), os.path.join(troot, "targets", "t.json"))
        with self.assertRaises(RR.BundleError) as cm:
            RR.load_bundle(SELFTEST, root=troot)
        self.assertIn("targets/t.json", str(cm.exception))


class TestReadersMoved(unittest.TestCase):
    def test_eva_names_are_the_loader_objects(self):
        for name in ("parse_frontmatter_ex", "yaml_typed_keys", "parse_flat_toml", "TomlError", "_TomlReader",
                     "_quoted_end", "_strip_comment", "_unquote", "_scalar", "_plain_scalar_error", "_block_scalar",
                     "_DQ_ESCAPES", "_DQ_HEX", "_YAML_TYPED_RE", "_TOML_BARE_KEY_RE", "_TOML_ESCAPES", "_TOML_INT_RE",
                     "_TOML_FLOAT_RE"):
            with self.subTest(name=name):
                self.assertIs(getattr(eva, name), getattr(loaders, name))

    def test_eva_no_longer_defines_the_readers(self):
        with open(os.path.join(ROOT, "eva.py"), encoding="utf-8") as f:
            src = f.read()
        for name in ("def parse_frontmatter_ex", "def yaml_typed_keys", "class _TomlReader", "def parse_flat_toml",
                     "class TomlError", "def _quoted_end"):
            self.assertNotIn(name, src)

    def test_engine_modules_import_neither_eva_nor_skill_eval(self):
        for mod in ("loaders", "schema", "rule_runner"):
            with open(os.path.join(ROOT, "engine", mod + ".py"), encoding="utf-8") as f:
                src = f.read()
            with self.subTest(mod=mod):
                self.assertIsNone(re.search(r"^\s*(?:import|from)\s+(?:eva|skill_eval)\b", src, re.M))

    def test_typed_key_problem_prints_as_before(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        u = os.path.join(d, "yes-skill")
        write(os.path.join(u, "SKILL.md"), "---\nname: yes-skill\ndescription: yes\n---\n\n# X\n")
        row = next(c for c in eva.check(u).checks if c["id"] == "field-types")
        self.assertEqual(row["status"], "fail")
        self.assertIn("description: PyYAML (skill-creator quick_validate) loads 'yes' as non-string; quote it",
                      row["reason"])


class TestCli(Base):
    def unknown_op_bundle(self):
        return self.bundle_dir({"rules.json": rule_file(rule({"op": "frobnicate"}))})

    def test_check_text_ends_with_one_hash_line(self):
        rc, out, _ = run_main(["check", os.path.join(ROOT, "golden", "skills", "csv-cleaner")])
        lines = out.splitlines()
        self.assertEqual(rc, 1)
        self.assertRegex(lines[-1], HEX64)
        self.assertEqual(sum(bool(HEX64.match(l)) for l in lines), 1)
        self.assertEqual(lines[-1], "ruleset_sha256 " + RR.default_bundle().sha256)

    def test_check_all_text_has_one_hash_line_after_total(self):
        rc, out, _ = run_main(["check-all", os.path.join(ROOT, "golden")])
        lines = out.splitlines()
        self.assertEqual(rc, 1)
        self.assertTrue(lines[-2].startswith("total="), lines)
        self.assertRegex(lines[-1], HEX64)
        self.assertEqual(sum(bool(HEX64.match(l)) for l in lines), 1)

    def test_json_stays_one_document_with_the_hash_key(self):
        rc, out, _ = run_main(["check", PASSING_UNIT, "--json"])
        d = json.loads(out)
        self.assertEqual((rc, d["verdict"]), (0, "PASS"))
        self.assertRegex("ruleset_sha256 " + d["ruleset_sha256"], HEX64)
        rc, out, _ = run_main(["check-all", os.path.join(ROOT, "golden"), "--json"])
        docs = json.loads(out)
        self.assertEqual(len(docs), 5)
        self.assertTrue(all(HEX64.match("ruleset_sha256 " + x["ruleset_sha256"]) for x in docs))

    def test_a_bundle_error_prints_no_row_and_exits_2(self):
        bad = self.unknown_op_bundle()
        for argv in (["check", PASSING_UNIT, "--rules", bad], ["check-all", os.path.join(ROOT, "golden"), "--rules", bad],
                     ["check", PASSING_UNIT, "--rules", bad, "--json"]):
            with self.subTest(argv=argv[0]):
                rc, out, err = run_main(argv)
                self.assertEqual((rc, out), (2, ""))
                self.assertTrue(err.startswith("bundle error: "), err)
                self.assertIn("frobnicate", err)

    def test_rules_never_give_pass_and_say_bundle_non_default(self):
        rc, out, _ = run_main(["check", PASSING_UNIT, "--rules", SELFTEST])
        self.assertEqual(rc, 3)
        self.assertIn("bundle non-default %s" % SELFTEST, out)
        self.assertTrue(out.splitlines()[0].startswith("[REVIEW]"))
        self.assertEqual(out.splitlines()[-1], "ruleset_sha256 " + RR.load_bundle(SELFTEST).sha256)
        parent = os.path.dirname(PASSING_UNIT)
        rc, out, _ = run_main(["check-all", parent, "--rules", SELFTEST])
        lines = out.splitlines()
        self.assertNotEqual(rc, 0)
        self.assertNotIn("[PASS]", out)
        self.assertEqual(lines[-1], "bundle non-default %s" % SELFTEST)
        self.assertRegex(lines[-2], HEX64)
        rc, out, _ = run_main(["check", PASSING_UNIT, "--rules", SELFTEST, "--json"])
        self.assertEqual((rc, json.loads(out)["verdict"]), (3, "REVIEW"))

    def test_check_all_json_with_rules_never_gives_pass(self):
        parent = os.path.dirname(PASSING_UNIT)
        rc, out, _ = run_main(["check-all", parent, "--rules", SELFTEST, "--json"])
        docs = json.loads(out)
        self.assertNotEqual(rc, 0)
        self.assertTrue(docs)
        for d in docs:
            with self.subTest(path=d["path"]):
                self.assertNotEqual(d["verdict"], "PASS")
                self.assertIn("bundle-non-default", d["needs_review"])
        passing = [d for d in docs if os.path.abspath(d["path"]) == os.path.abspath(PASSING_UNIT)]
        self.assertEqual([d["verdict"] for d in passing], ["REVIEW"])
        self.assertEqual(eva.check(PASSING_UNIT).verdict, "PASS")

    def test_the_default_bundle_comes_from_the_eva_folder_not_the_cwd(self):
        cwd = os.path.join(self.tmp, "elsewhere")
        write(os.path.join(cwd, "rules", "rules.json"), rule_file(rule({"op": "frobnicate"})))
        write(os.path.join(cwd, "schemas", "rules.schema.json"), "{}")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, os.path.join(ROOT, "eva.py"), "check", PASSING_UNIT], cwd=cwd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertEqual(p.stdout.splitlines()[-1], "ruleset_sha256 " + RR.default_bundle().sha256)

    def test_a_rule_regex_that_overflows_is_a_bundle_error(self):
        regex = self.bundle_dir({"rules.json": rule_file(rule({"op": "regex", "sel": "text:a.txt",
                                                                "pattern": "a{99999999999}"}))})
        for argv in (["check", PASSING_UNIT, "--rules", regex], ["check-all", os.path.dirname(PASSING_UNIT), "--rules", regex]):
            with self.subTest(argv=argv[0]):
                rc, out, err = run_main(argv)
                self.assertEqual((rc, out), (2, ""))
                self.assertTrue(err.startswith("bundle error: "), err)
                self.assertIn("does not compile", err)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads a file of mode 0")
    def test_an_unreadable_bundle_file_is_a_bundle_error(self):
        locked = self.bundle_dir({"rules.json": rule_file(rule(FILE_A)), "data/notes.json": "{}"})
        os.chmod(os.path.join(locked, "data", "notes.json"), 0)
        self.addCleanup(os.chmod, os.path.join(locked, "data", "notes.json"), 0o644)
        for argv in (["check", PASSING_UNIT, "--rules", locked],
                     ["check-all", os.path.dirname(PASSING_UNIT), "--rules", locked]):
            with self.subTest(argv=argv[0]):
                rc, out, err = run_main(argv)
                self.assertEqual((rc, out), (2, ""))
                self.assertTrue(err.startswith("bundle error: rules/data/notes.json: cannot read"), err)

    def test_a_nul_glob_in_one_unit_gives_that_unit_a_failing_row_in_check_all(self):
        units = os.path.join(self.tmp, "nul-units")
        self.unit(name="good-one", folder="nul-units")
        write(os.path.join(units, "hostile", "SKILL.md"),
              '---\nname: hostile\ndescription: %s\nnul: "a\\0b"\n---\n\nBody.\n' % DESC)
        bnd = self.bundle_dir({"rules.json": rule_file(rule({"op": "count", "sel": "glob:{nul}/*", "min": 0},
                                                            severity="soft"))})
        rc, out, err = run_main(["check-all", units, "--rules", bnd, "--json"])
        docs = {os.path.basename(d["path"]): d for d in json.loads(out)}
        self.assertEqual(sorted(docs), ["good-one", "hostile"])
        rows = {n: next(c for c in d["checks"] if c["id"] == "t-rule") for n, d in docs.items()}
        self.assertEqual(rows["hostile"]["status"], "fail")
        self.assertIn("NUL byte", rows["hostile"]["reason"])
        self.assertEqual(rows["good-one"]["status"], "fail")
        self.assertIn("template key 'nul'", rows["good-one"]["reason"])
        self.assertEqual(err, "")

    def test_rules_with_a_transcript_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as cm:
            run_main(["check", PASSING_UNIT, "--transcript", "t.json", "--rules", SELFTEST])
        self.assertEqual(cm.exception.code, 2)

    def test_schema_check_exit_codes(self):
        d = self.bundle_dir({"s.json": json.dumps({"type": "object", "additionalProperties": False,
                                                    "properties": {"a": {}}}),
                             "ok.json": '{"a": 1}', "extra.json": '{"a": 1, "zz": 2}',
                             "fmt.json": json.dumps({"format": "email"}), "dup.json": '{"a": 1, "a": 2}',
                             "nan.json": '{"a": NaN}', "broken.json": "{"})
        p = lambda n: os.path.join(d, n)
        self.assertEqual(run_main(["schema-check", p("s.json"), p("ok.json")]), (0, "schema-check: valid\n", ""))
        rc, out, err = run_main(["schema-check", p("s.json"), p("extra.json")])
        self.assertEqual((rc, out, err), (1, "error #: additionalProperties: property 'zz' is not allowed\n", ""))
        rc, out, err = run_main(["schema-check", p("fmt.json"), p("ok.json")])
        self.assertEqual((rc, out, err), (2, "", "schema refused: #: format: unsupported keyword\n"))
        for schema, instance, name in (("dup.json", "ok.json", "dup.json"), ("s.json", "nan.json", "nan.json"),
                                       ("s.json", "broken.json", "broken.json"), ("s.json", "missing.json", "missing.json")):
            with self.subTest(name=name):
                rc, out, err = run_main(["schema-check", p(schema), p(instance)])
                self.assertEqual((rc, out), (2, ""))
                self.assertTrue(err.startswith("schema-check: %s: " % p(name)), err)

    def test_schema_check_unit_option(self):
        d = self.bundle_dir({"s.json": json.dumps({"x-path-exists": "unit"}), "i.json": '"SKILL.md"'})
        unit = self.unit()
        rc, out, _ = run_main(["schema-check", os.path.join(d, "s.json"), os.path.join(d, "i.json")])
        self.assertEqual(rc, 1)
        self.assertIn("x-path-exists: no unit root given", out)
        self.assertEqual(run_main(["schema-check", os.path.join(d, "s.json"), os.path.join(d, "i.json"), "--unit", unit])[0], 0)


if __name__ == "__main__":
    unittest.main()
