"""engine/schema.py: the JSON Schema 2020-12 subset of the static assessment section 3.3 plus the six x- keywords.
Every case is written by hand (plan.md:562, Appendix E, Q9 default). Each supported keyword has a valid and an invalid
instance with the expected error text, and each unsupported keyword family has a refused schema whose refusal names
the keyword (plan.md:557, :570; D2 to D7 of the A1 how pass)."""
import contextlib, io, os, sys, tempfile, shutil, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import schema as S

SUPPORTED = ("type", "enum", "const", "required", "properties", "additionalProperties", "patternProperties", "items",
             "prefixItems", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum",
             "maximum", "allOf", "anyOf", "oneOf", "not", "if/then/else", "$ref", "$defs")
CUSTOM = ("x-python-regex", "x-unique-by", "x-count-where", "x-le", "x-path-exists", "x-yaml-string")


def errors(schema, instance, **context):
    return [S.format_error(e) for e in S.compile_schema(schema).validate(instance, **context)]


# (keyword, schema, valid instance, invalid instance, expected error text)
CASES = [
    ("type", {"type": "integer"}, 3, "3", "#: type: expected integer, got string"),
    ("type", {"type": ["string", "null"]}, None, 1, "#: type: expected string or null, got integer"),
    ("enum", {"enum": ["a", "b"]}, "a", "c", '#: enum: value "c" is not one of ["a", "b"]'),
    ("const", {"const": 3}, 3, 4, "#: const: value 4 is not 3"),
    ("required", {"required": ["name"]}, {"name": 1}, {}, "#: required: required property 'name' is missing"),
    ("properties", {"properties": {"n": {"type": "string"}}}, {"n": "x"}, {"n": 1},
     "#/n: type: expected string, got integer"),
    ("additionalProperties", {"properties": {"a": {}}, "additionalProperties": False}, {"a": 1}, {"a": 1, "extra": 2},
     "#: additionalProperties: property 'extra' is not allowed"),
    ("additionalProperties", {"additionalProperties": {"type": "integer"}}, {"a": 1}, {"a": "x"},
     "#/a: type: expected integer, got string"),
    ("patternProperties", {"patternProperties": {"^x-": {"type": "string"}}}, {"x-a": "s", "y": 1}, {"x-a": 1},
     "#/x-a: type: expected string, got integer"),
    ("items", {"items": {"type": "integer"}}, [1, 2], [1, "a"], "#/1: type: expected integer, got string"),
    ("prefixItems", {"prefixItems": [{"type": "string"}], "items": {"type": "integer"}}, ["a", 1], ["a", "b"],
     "#/1: type: expected integer, got string"),
    ("prefixItems", {"prefixItems": [{"type": "string"}]}, ["a", 1], [1], "#/0: type: expected string, got integer"),
    ("minItems", {"minItems": 2}, [1, 2], [1], "#: minItems: array has 1 item(s), fewer than 2"),
    ("maxItems", {"maxItems": 1}, [1], [1, 2], "#: maxItems: array has 2 item(s), more than 1"),
    ("uniqueItems", {"uniqueItems": True}, [1, "1", True], [1, 1.0], "#: uniqueItems: items 0 and 1 are equal"),
    ("minLength", {"minLength": 2}, "ab", "a", "#: minLength: string has 1 character(s), fewer than 2"),
    ("maxLength", {"maxLength": 2}, "ab", "abc", "#: maxLength: string has 3 character(s), more than 2"),
    ("pattern", {"pattern": "^a"}, "ab", "ba", '#: pattern: string "ba" does not match pattern "^a"'),
    ("minimum", {"minimum": 1}, 1, 0, "#: minimum: 0 is less than the minimum 1"),
    ("maximum", {"maximum": 1}, 1, 2, "#: maximum: 2 is greater than the maximum 1"),
    ("allOf", {"allOf": [{"type": "integer"}, {"minimum": 2}]}, 3, 1, "#: minimum: 1 is less than the minimum 2"),
    ("anyOf", {"anyOf": [{"type": "integer"}, {"type": "string"}]}, "a", None, "#: anyOf: no subschema matched"),
    ("oneOf", {"oneOf": [{"type": "integer"}, {"minimum": 0}]}, -1, 1,
     "#: oneOf: 2 subschemas matched, exactly one must"),
    ("oneOf", {"oneOf": [{"type": "integer"}, {"type": "string"}]}, 1, None,
     "#: oneOf: 0 subschemas matched, exactly one must"),
    ("not", {"not": {"type": "string"}}, 1, "a", "#: not: value matches the schema under not"),
    ("if/then/else", {"if": {"type": "integer"}, "then": {"minimum": 0}, "else": {"type": "string"}}, 5, -1,
     "#: minimum: -1 is less than the minimum 0"),
    ("if/then/else", {"if": {"type": "integer"}, "then": {"minimum": 0}, "else": {"type": "string"}}, "s", True,
     "#: type: expected string, got boolean"),
    ("$ref", {"$defs": {"n": {"type": "integer"}}, "properties": {"a": {"$ref": "#/$defs/n"}}}, {"a": 1}, {"a": "x"},
     "#/a: type: expected integer, got string"),
    ("$defs", {"$defs": {"pos": {"minimum": 0}}, "$ref": "#/$defs/pos"}, 0, -1,
     "#: minimum: -1 is less than the minimum 0"),
    ("x-python-regex", {"x-python-regex": True}, "a+", "(",
     "#: x-python-regex: string does not compile as a Python regex"),
    ("x-unique-by", {"x-unique-by": "id"}, [{"id": 1}, {"id": 2}, {"x": 1}], [{"id": "a"}, {"id": "a"}],
     '#: x-unique-by: duplicate id "a" at items 0 and 1'),
    ("x-count-where", {"x-count-where": {"where": {"const": True}, "min": 2, "max": 2}}, [True, True, False],
     [True, False], "#: x-count-where: 1 item(s) match where, need 2 to 2"),
    ("x-count-where", {"x-count-where": {"where": {"const": True}, "max": 1}}, [True, False], [True, True],
     "#: x-count-where: 2 item(s) match where, need at most 1"),
    ("x-count-where", {"x-count-where": {"where": {"const": True}, "min": 1}}, [True], [False],
     "#: x-count-where: 0 item(s) match where, need at least 1"),
    ("x-le", {"x-le": ["min", "max"]}, {"min": 1, "max": 2}, {"min": 3, "max": 2},
     "#: x-le: 'min' (3) is greater than 'max' (2)"),
    ("x-yaml-string", {"properties": {"name": {"x-yaml-string": True}}}, {"name": "yes"}, {"name": "yes"},
     "#/name: x-yaml-string: value at 'name' is typed by YAML 1.1, not a string (quote it)"),
]
CONTEXT = {"x-yaml-string": ({"typed_keys": set()}, {"typed_keys": {"name"}})}


class TestEveryKeyword(unittest.TestCase):
    def test_each_keyword_accepts_its_valid_instance_and_rejects_its_invalid_one(self):
        for kw, schema, good, bad, text in CASES:
            ok_ctx, bad_ctx = CONTEXT.get(kw, ({}, {}))
            with self.subTest(keyword=kw, schema=schema):
                self.assertEqual(errors(schema, good, **ok_ctx), [])
                out = errors(schema, bad, **bad_ctx)
                self.assertTrue(any(e.startswith(text) for e in out), out)

    def test_the_cases_cover_every_supported_and_custom_keyword(self):
        covered = {c[0] for c in CASES} | {"x-path-exists"}
        self.assertEqual(covered, set(SUPPORTED) | set(CUSTOM))
        self.assertEqual(len(SUPPORTED), 24)
        self.assertEqual(len(CUSTOM), 6)

    def test_the_module_tables_name_exactly_these_keywords(self):
        self.assertEqual(set(S.SUPPORTED), set(SUPPORTED) - {"if/then/else"} | {"if", "then", "else"})
        self.assertEqual(set(S.CUSTOM), set(CUSTOM))
        self.assertEqual(set(S.ANNOTATIONS), {"$schema", "$id", "$comment", "title", "description", "default",
                                              "examples"})

    def test_an_error_pointer_escapes_slash_and_tilde(self):
        self.assertEqual(errors({"properties": {"a/b~c": {"type": "string"}}}, {"a/b~c": 1}),
                         ["#/a~1b~0c: type: expected string, got integer"])

    def test_annotations_are_accepted_and_change_nothing(self):
        schema = {"$schema": S.DIALECT, "$id": "https://example.com/x.schema.json", "$comment": "c", "title": "t",
                  "description": "d", "default": 1, "examples": [1], "type": "integer"}
        self.assertEqual(errors(schema, 1), [])
        self.assertEqual(errors(schema, "1"), ["#: type: expected integer, got string"])


class TestXPathExists(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.unit = os.path.join(self.tmp, "unit")
        os.makedirs(os.path.join(self.unit, "references"))
        with open(os.path.join(self.unit, "references", "guide.md"), "w", encoding="utf-8") as f:
            f.write("guide\n")
        with open(os.path.join(self.tmp, "outside.md"), "w", encoding="utf-8") as f:
            f.write("outside\n")
        self.schema = {"x-path-exists": "unit"}

    def check(self, ref):
        return errors(self.schema, ref, unit_root=self.unit)

    def test_a_file_inside_the_unit_passes(self):
        self.assertEqual(self.check("references/guide.md"), [])
        self.assertEqual(self.check("./references/guide.md"), [])

    def test_a_missing_file_and_a_folder_fail(self):
        self.assertEqual(self.check("references/none.md"),
                         ["#: x-path-exists: 'references/none.md' is not a regular file in the unit"])
        self.assertEqual(self.check("references"), ["#: x-path-exists: 'references' is not a regular file in the unit"])

    def test_dotdot_absolute_home_drive_and_backslash_fail(self):
        for ref, why in (("../outside.md", "points outside the skill directory"),
                         ("references/../../outside.md", "points outside the skill directory"),
                         (os.path.join(self.tmp, "outside.md"), "absolute or drive path"),
                         ("~/outside.md", "absolute or drive path"), ("C:/x.md", "absolute or drive path"),
                         ("references" + chr(92) + "guide.md", "backslash")):
            with self.subTest(ref=ref):
                out = self.check(ref)
                self.assertEqual(len(out), 1, out)
                self.assertTrue(out[0].startswith("#: x-path-exists: "), out)
                self.assertIn(why, out[0])

    def test_a_link_that_leaves_the_unit_fails(self):
        os.symlink(os.path.join(self.tmp, "outside.md"), os.path.join(self.unit, "link.md"))
        self.assertEqual(self.check("link.md"), ["#: x-path-exists: 'link.md' resolves outside the unit"])

    def test_a_link_inside_the_unit_passes(self):
        os.symlink(os.path.join(self.unit, "references", "guide.md"), os.path.join(self.unit, "alias.md"))
        self.assertEqual(self.check("alias.md"), [])

    def test_without_a_unit_root_the_instance_fails_naming_the_keyword(self):
        self.assertEqual(errors(self.schema, "references/guide.md"),
                         ["#: x-path-exists: no unit root given, so the path cannot be checked (fail closed)"])

    def test_a_non_string_is_not_checked(self):
        self.assertEqual(errors(self.schema, 3), [])


class TestXYamlString(unittest.TestCase):
    SCHEMA = {"properties": {"name": {"x-yaml-string": True},
                             "metadata": {"properties": {"version": {"x-yaml-string": True}}},
                             "tags": {"items": {"x-yaml-string": True}}}}
    DOC = {"name": "x", "metadata": {"version": "1.0"}, "tags": ["a", "yes"]}

    def test_nested_keys_and_list_items_map_to_the_typed_key_names(self):
        self.assertEqual(errors(self.SCHEMA, self.DOC, typed_keys={"metadata.version", "tags[]"}),
                         ["#/metadata/version: x-yaml-string: value at 'metadata.version' is typed by YAML 1.1, not a "
                          "string (quote it)",
                          "#/tags/0: x-yaml-string: value at 'tags[]' is typed by YAML 1.1, not a string (quote it)",
                          "#/tags/1: x-yaml-string: value at 'tags[]' is typed by YAML 1.1, not a string (quote it)"])
        self.assertEqual(errors(self.SCHEMA, self.DOC, typed_keys={"other"}), [])

    def test_a_slash_in_a_nested_key_maps_back_to_the_dotted_name(self):
        schema = {"properties": {"metadata": {"additionalProperties": {"x-yaml-string": True}}}}
        doc = {"metadata": {"opencode/autoinvoke": "false"}}
        self.assertEqual(errors(schema, doc, typed_keys={"metadata.opencode/autoinvoke"}),
                         ["#/metadata/opencode~1autoinvoke: x-yaml-string: value at 'metadata.opencode/autoinvoke' is "
                          "typed by YAML 1.1, not a string (quote it)"])

    def test_without_typed_keys_the_instance_fails_naming_the_keyword(self):
        self.assertEqual(errors({"properties": {"name": {"x-yaml-string": True}}}, {"name": "x"}),
                         ["#/name: x-yaml-string: no typed-key annotation given, so YAML typing cannot be checked "
                          "(fail closed)"])

    def test_a_pointer_without_a_key_name_fails(self):
        self.assertEqual(errors({"x-yaml-string": True}, "x", typed_keys=set()),
                         ["#: x-yaml-string: pointer has no frontmatter key name"])
        deep = {"properties": {"a": {"properties": {"b": {"properties": {"c": {"x-yaml-string": True}}}}}}}
        self.assertEqual(errors(deep, {"a": {"b": {"c": "x"}}}, typed_keys=set()),
                         ["#/a/b/c: x-yaml-string: pointer has no frontmatter key name"])


class TestTyping(unittest.TestCase):
    def test_integer_excludes_bool_and_one_point_zero(self):
        self.assertEqual(errors({"type": "integer"}, 1), [])
        self.assertEqual(errors({"type": "integer"}, True), ["#: type: expected integer, got boolean"])
        self.assertEqual(errors({"type": "integer"}, 1.0), ["#: type: expected integer, got number"])

    def test_number_excludes_bool(self):
        self.assertEqual(errors({"type": "number"}, 1.5), [])
        self.assertEqual(errors({"type": "number"}, 2), [])
        self.assertEqual(errors({"type": "number"}, False), ["#: type: expected number, got boolean"])

    def test_numeric_bounds_skip_booleans(self):
        self.assertEqual(errors({"minimum": 5}, True), [])
        self.assertEqual(errors({"maximum": 0}, True), [])
        self.assertEqual(errors({"x-le": ["a", "b"]}, {"a": True, "b": False}), [])
        self.assertEqual(errors({"x-le": ["a", "b"]}, {"a": 3}), [])

    def test_enum_and_const_compare_by_json_type(self):
        self.assertEqual(errors({"enum": [1]}, 1.0), [])
        self.assertEqual(errors({"enum": [1]}, True), ["#: enum: value true is not one of [1]"])
        self.assertEqual(errors({"const": True}, 1), ["#: const: value 1 is not true"])
        self.assertEqual(errors({"const": {"a": [1]}}, {"a": [1.0]}), [])
        self.assertEqual(errors({"const": {"a": [1]}}, {"a": [True]}), ['#: const: value {"a": [true]} is not {"a": [1]}'])
        self.assertEqual(errors({"const": None}, False), ["#: const: value false is not null"])

    def test_unique_items_and_unique_by_compare_by_json_type(self):
        self.assertEqual(errors({"uniqueItems": True}, [1, True, [1], [True], {"a": 0}, {"a": False}]), [])
        self.assertEqual(errors({"uniqueItems": True}, [{"a": 1, "b": 2}, {"b": 2, "a": 1.0}]),
                         ["#: uniqueItems: items 0 and 1 are equal"])
        self.assertEqual(errors({"uniqueItems": False}, [1, 1]), [])
        self.assertEqual(errors({"x-unique-by": "id"}, [{"id": 1}, {"id": True}]), [])
        self.assertEqual(errors({"x-unique-by": "id"}, [{"id": 1}, {"id": 1.0}]),
                         ["#: x-unique-by: duplicate id 1.0 at items 0 and 1"])


class TestBooleanSchemasAndNames(unittest.TestCase):
    def test_true_accepts_anything_and_false_rejects_everything(self):
        self.assertEqual(errors(True, {"a": [1]}), [])
        self.assertEqual(errors(False, None), ["#: false: no value is allowed here"])
        self.assertEqual(errors({"properties": {"a": False}}, {"a": 1}), ["#/a: false: no value is allowed here"])
        self.assertEqual(errors({"items": True}, [1, "x"]), [])

    def test_property_names_are_names_not_keywords(self):
        schema = {"properties": {"format": {"type": "string"}, "$ref": {"type": "integer"}, "x-nope": {}},
                  "patternProperties": {"^contains$": {"type": "string"}}, "$defs": {"format": {"type": "string"}}}
        self.assertEqual(errors(schema, {"format": "x", "$ref": 1, "contains": "y"}), [])
        self.assertEqual(errors(schema, {"format": 1}), ["#/format: type: expected string, got integer"])


# (family, schema, the keyword the refusal must name)
REFUSED = [
    ("format and content", {"type": "string", "format": "email"}, "format"),
    ("format and content", {"contentMediaType": "text/plain"}, "contentMediaType"),
    ("format and content", {"contentEncoding": "base64"}, "contentEncoding"),
    ("dependencies", {"dependentRequired": {"a": ["b"]}}, "dependentRequired"),
    ("dependencies", {"dependentSchemas": {"a": {}}}, "dependentSchemas"),
    ("dependencies", {"dependencies": {"a": ["b"]}}, "dependencies"),
    ("unevaluated", {"unevaluatedProperties": False}, "unevaluatedProperties"),
    ("unevaluated", {"unevaluatedItems": False}, "unevaluatedItems"),
    ("object size and names", {"minProperties": 1}, "minProperties"),
    ("object size and names", {"maxProperties": 1}, "maxProperties"),
    ("object size and names", {"propertyNames": {"pattern": "^a"}}, "propertyNames"),
    ("contains", {"contains": {"const": 1}}, "contains"),
    ("contains", {"minContains": 1}, "minContains"),
    ("contains", {"maxContains": 1}, "maxContains"),
    ("numeric extras", {"exclusiveMinimum": 0}, "exclusiveMinimum"),
    ("numeric extras", {"exclusiveMaximum": 0}, "exclusiveMaximum"),
    ("numeric extras", {"multipleOf": 2}, "multipleOf"),
    ("referencing", {"$anchor": "a"}, "$anchor"),
    ("referencing", {"$dynamicRef": "#a"}, "$dynamicRef"),
    ("referencing", {"$dynamicAnchor": "a"}, "$dynamicAnchor"),
    ("referencing", {"$recursiveRef": "#"}, "$recursiveRef"),
    ("referencing", {"$recursiveAnchor": True}, "$recursiveAnchor"),
    ("referencing", {"properties": {"a": {"$id": "https://example.com/a"}}}, "$id"),
    ("referencing", {"$ref": "other.schema.json#/a"}, "$ref"),
    ("vocabulary and dialect", {"$vocabulary": {}}, "$vocabulary"),
    ("vocabulary and dialect", {"$schema": "http://json-schema.org/draft-07/schema#"}, "$schema"),
    ("legacy", {"definitions": {}}, "definitions"),
    ("legacy", {"additionalItems": False}, "additionalItems"),
    ("legacy", {"items": [{"type": "string"}]}, "items"),
    ("legacy", {"id": "x"}, "id"),
    ("legacy", {"x-nfkc-equals": True}, "x-nfkc-equals"),
]
FAMILIES = {"format and content", "dependencies", "unevaluated", "object size and names", "contains", "numeric extras",
            "referencing", "vocabulary and dialect", "legacy"}


class TestRefusedSchemas(unittest.TestCase):
    def assertRefused(self, schema, keyword, pointer=None):
        with self.assertRaises(S.SchemaRefused) as cm:
            S.compile_schema(schema)
        self.assertEqual(cm.exception.keyword, keyword, str(cm.exception))
        if pointer is not None:
            self.assertEqual(cm.exception.pointer, pointer, str(cm.exception))
        self.assertIn(keyword, str(cm.exception))
        return str(cm.exception)

    def test_each_unsupported_family_refuses_the_schema_and_names_the_keyword(self):
        for family, schema, kw in REFUSED:
            pointer = "#/properties/a" if kw == "$id" else "#"
            with self.subTest(family=family, keyword=kw):
                self.assertRefused(schema, kw, pointer)
        self.assertEqual({f for f, _, _ in REFUSED}, FAMILIES)

    def test_an_unsupported_keyword_is_refused_anywhere_a_schema_can_sit(self):
        for schema, pointer in (({"properties": {"a": {"format": "x"}}}, "#/properties/a"),
                                ({"items": {"format": "x"}}, "#/items"),
                                ({"$defs": {"d": {"format": "x"}}}, "#/$defs/d"),
                                ({"allOf": [{}, {"format": "x"}]}, "#/allOf/1"),
                                ({"not": {"format": "x"}}, "#/not"),
                                ({"if": {"format": "x"}}, "#/if"),
                                ({"x-count-where": {"where": {"format": "x"}, "min": 1}}, "#/x-count-where/where"),
                                ({"patternProperties": {"^a": {"format": "x"}}}, "#/patternProperties/^a"),
                                ({"additionalProperties": {"format": "x"}}, "#/additionalProperties"),
                                ({"prefixItems": [{}, {"format": "x"}]}, "#/prefixItems/1"),
                                ({"anyOf": [{}, {"format": "x"}]}, "#/anyOf/1"),
                                ({"oneOf": [{}, {"format": "x"}]}, "#/oneOf/1"),
                                ({"if": True, "then": {"format": "x"}}, "#/then"),
                                ({"if": True, "else": {"format": "x"}}, "#/else")):
            with self.subTest(pointer=pointer):
                self.assertEqual(self.assertRefused(schema, "format", pointer), "%s: format: unsupported keyword" % pointer)

    def test_malformed_keyword_values_refuse_the_schema(self):
        for schema, kw in (({"type": "foo"}, "type"), ({"type": []}, "type"), ({"type": ["string", "string"]}, "type"),
                           ({"enum": []}, "enum"), ({"enum": "a"}, "enum"),
                           ({"required": "x"}, "required"), ({"required": ["a", "a"]}, "required"),
                           ({"required": [1]}, "required"),
                           ({"properties": []}, "properties"), ({"properties": {"a": 5}}, "schema"),
                           ({"additionalProperties": "no"}, "schema"),
                           ({"patternProperties": {"(": {}}}, "patternProperties"),
                           ({"prefixItems": []}, "prefixItems"), ({"allOf": []}, "allOf"), ({"anyOf": {}}, "anyOf"),
                           ({"minItems": -1}, "minItems"), ({"maxItems": 1.0}, "maxItems"),
                           ({"minLength": True}, "minLength"), ({"maxLength": "2"}, "maxLength"),
                           ({"uniqueItems": "yes"}, "uniqueItems"), ({"pattern": "("}, "pattern"),
                           ({"pattern": 3}, "pattern"), ({"minimum": "1"}, "minimum"), ({"maximum": True}, "maximum"),
                           ({"$ref": 5}, "$ref"), ({"$defs": []}, "$defs"), ({"then": {}}, "then"),
                           ({"else": {}}, "else"), ({"title": 3}, "title"), ({"examples": 1}, "examples"),
                           ({"x-python-regex": False}, "x-python-regex"), ({"x-python-regex": "yes"}, "x-python-regex"),
                           ({"x-unique-by": ""}, "x-unique-by"), ({"x-unique-by": ["id"]}, "x-unique-by"),
                           ({"x-count-where": {}}, "x-count-where"),
                           ({"x-count-where": {"where": {}}}, "x-count-where"),
                           ({"x-count-where": {"where": {}, "min": 3, "max": 2}}, "x-count-where"),
                           ({"x-count-where": {"where": {}, "min": -1}}, "x-count-where"),
                           ({"x-count-where": {"where": {}, "min": True}}, "x-count-where"),
                           ({"x-count-where": {"where": {}, "min": 1, "most": 2}}, "x-count-where"),
                           ({"x-le": ["a"]}, "x-le"), ({"x-le": ["a", "a"]}, "x-le"), ({"x-le": ["a", 1]}, "x-le"),
                           ({"x-path-exists": "repo"}, "x-path-exists"), ({"x-path-exists": True}, "x-path-exists"),
                           ({"x-yaml-string": False}, "x-yaml-string")):
            with self.subTest(schema=schema):
                self.assertRefused(schema, kw)

    def test_a_schema_that_is_not_an_object_or_boolean_is_refused(self):
        for doc in (None, 1, "x", []):
            with self.subTest(doc=doc):
                self.assertRefused(doc, "schema")

    def test_non_local_refs_are_refused(self):
        for ref in ("other.json", "https://example.com/s.json#/a", "#a", "#/%24defs/a", "/$defs/a"):
            with self.subTest(ref=ref):
                self.assertRefused({"$defs": {"a": {}}, "$ref": ref}, "$ref")

    def test_a_non_local_ref_is_refused_even_when_its_local_reading_would_resolve(self):
        for schema, ref in (({"$defs": {"a": {}}, "$ref": "x/$defs/a"}, "x/$defs/a"),
                            ({"$defs": {"a": {}}, "$ref": "other.json#/$defs/a"}, "other.json#/$defs/a"),
                            ({"$defs": {"%61": {}}, "$ref": "#/$defs/%61"}, "#/$defs/%61"),
                            ({"$defs": {"a%2Fb": {}}, "$ref": "#/$defs/a%2Fb"}, "#/$defs/a%2Fb")):
            with self.subTest(ref=ref):
                self.assertIn("only local references", self.assertRefused(schema, "$ref"))

    def test_a_ref_that_points_at_no_schema_is_refused(self):
        self.assertIn("does not point at a schema", self.assertRefused({"$ref": "#/$defs/missing"}, "$ref"))
        self.assertIn("does not point at a schema",
                      self.assertRefused({"properties": {"a": {}}, "$ref": "#/properties"}, "$ref"))
        self.assertIn("does not point at a schema", self.assertRefused({"$ref": "#/$defs/a~2"}, "$ref"))

    def test_a_ref_into_a_defs_name_with_escapes_resolves(self):
        schema = {"$defs": {"a/b": {"type": "string"}, "c~d": {"type": "integer"}},
                  "properties": {"x": {"$ref": "#/$defs/a~1b"}, "y": {"$ref": "#/$defs/c~0d"}}}
        self.assertEqual(errors(schema, {"x": "s", "y": 1}), [])
        self.assertEqual(errors(schema, {"x": 1}), ["#/x: type: expected string, got integer"])


class TestRefCyclesAndDepth(unittest.TestCase):
    def test_a_ref_cycle_without_progress_is_refused(self):
        for schema in ({"$ref": "#"}, {"allOf": [{"$ref": "#"}]},
                       {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"anyOf": [{"$ref": "#/$defs/a"}]}},
                        "$ref": "#/$defs/a"},
                       {"$defs": {"a": {"not": {"$ref": "#/$defs/a"}}}},
                       {"$defs": {"a": {"if": {"$ref": "#/$defs/a"}}}}):
            with self.subTest(schema=schema):
                with self.assertRaises(S.SchemaRefused) as cm:
                    S.compile_schema(schema)
                self.assertEqual(cm.exception.keyword, "$ref")
                self.assertIn("cycle", str(cm.exception))

    def test_a_recursive_schema_that_consumes_the_instance_is_allowed(self):
        tree = {"type": "array", "items": {"$ref": "#"}}
        self.assertEqual(errors(tree, [[], [[]]]), [])
        self.assertEqual(errors(tree, [[1]]), ["#/0/0: type: expected array, got integer"])

    def test_a_deeply_nested_instance_ends_in_an_instance_error(self):
        deep = []
        for _ in range(400):
            deep = [deep]
        out = errors({"items": {"$ref": "#"}}, deep)
        self.assertEqual(len(out), 1, out)
        self.assertIn(": depth: nesting deeper than %d levels" % S.MAX_DEPTH, out[0])

    def test_a_deeply_nested_const_ends_in_an_instance_error(self):
        deep = []
        for _ in range(600):
            deep = [deep]
        out = errors({"const": []}, deep)
        self.assertEqual(out, ["#: depth: nesting deeper than %d levels" % S.MAX_DEPTH])

    def test_a_deeply_nested_schema_is_refused(self):
        doc = {}
        for _ in range(400):
            doc = {"not": doc}
        with self.assertRaises(S.SchemaRefused) as cm:
            S.compile_schema(doc)
        self.assertEqual(cm.exception.keyword, "depth")


class TestStrictJson(unittest.TestCase):
    def test_duplicate_keys_are_refused(self):
        with self.assertRaises(S.JsonError) as cm:
            S.loads('{"a": 1, "b": {"c": 1, "c": 2}}')
        self.assertIn('duplicate key "c"', str(cm.exception))

    def test_nan_and_infinity_are_refused(self):
        for text in ("NaN", "[1, Infinity]", '{"a": -Infinity}'):
            with self.subTest(text=text):
                with self.assertRaises(S.JsonError) as cm:
                    S.loads(text)
                self.assertIn("is not valid JSON", str(cm.exception))

    def test_syntax_errors_and_deep_nesting_are_json_errors(self):
        for text in ("{", "[1,]", "[" * 100000 + "]" * 100000):
            with self.subTest(text=text[:10]):
                with self.assertRaises(S.JsonError):
                    S.loads(text)

    def test_load_refuses_bad_utf8_and_missing_files(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        bad = os.path.join(d, "bad.json")
        with open(bad, "wb") as f:
            f.write(b'{"a": "\xff"}')
        with self.assertRaises(S.JsonError):
            S.load(bad)
        with self.assertRaises(S.JsonError):
            S.load(os.path.join(d, "missing.json"))
        good = os.path.join(d, "good.json")
        with open(good, "w", encoding="utf-8") as f:
            f.write('{"a": [1, 2.5, true, null, "s"]}')
        self.assertEqual(S.load(good), {"a": [1, 2.5, True, None, "s"]})


class TestKeywordEdges(unittest.TestCase):
    def test_x_le_accepts_equal_values(self):
        self.assertEqual(errors({"x-le": ["min", "max"]}, {"min": 2, "max": 2}), [])
        self.assertEqual(errors({"x-le": ["min", "max"]}, {"min": 2.5, "max": 2}),
                         ["#: x-le: 'min' (2.5) is greater than 'max' (2)"])

    def test_x_unique_by_skips_every_item_without_the_key(self):
        self.assertEqual(errors({"x-unique-by": "id"}, [{"x": 1}, {"y": 2}, {"id": 1}, {}]), [])
        self.assertEqual(errors({"x-unique-by": "id"}, [{"x": 1}, {"id": 1}, {"y": 2}, {"id": 1}]),
                         ["#: x-unique-by: duplicate id 1 at items 1 and 3"])

    def test_ref_siblings_apply_as_well(self):
        schema = {"$defs": {"s": {"type": "string"}}, "$ref": "#/$defs/s", "maxLength": 2}
        self.assertEqual(errors(schema, "ab"), [])
        self.assertEqual(errors(schema, "abc"), ["#: maxLength: string has 3 character(s), more than 2"])
        self.assertEqual(errors(schema, 3), ["#: type: expected string, got integer"])

    def test_additional_properties_skips_names_that_match_a_pattern_property(self):
        schema = {"properties": {"a": {}}, "patternProperties": {"^x-": {"type": "integer"}, "b": {}},
                  "additionalProperties": False}
        self.assertEqual(errors(schema, {"a": 1, "x-one": 2, "abc": 3}), [])
        self.assertEqual(errors(schema, {"x-one": 2, "zz": 3}), ["#: additionalProperties: property 'zz' is not allowed"])

    def test_pattern_and_pattern_properties_search_anywhere_in_the_string(self):
        self.assertEqual(errors({"pattern": "b"}, "abc"), [])
        self.assertEqual(errors({"pattern": "b"}, "xyz"), ['#: pattern: string "xyz" does not match pattern "b"'])
        schema = {"patternProperties": {"b": {"type": "string"}}}
        self.assertEqual(errors(schema, {"abc": "s"}), [])
        self.assertEqual(errors(schema, {"abc": 1}), ["#/abc: type: expected string, got integer"])


NO_UNIT = "x-path-exists: no unit root given, so the path cannot be checked (fail closed)"
NO_TYPED = "x-yaml-string: no typed-key annotation given, so YAML typing cannot be checked (fail closed)"


class TestMissingContextFailsUnderEveryCombinator(unittest.TestCase):
    def test_a_missing_unit_root_or_typed_key_set_fails_the_whole_validation(self):
        path, yaml = {"x-path-exists": "unit"}, {"x-yaml-string": True}
        for schema, instance, expected in (
                ({"not": path}, "SKILL.md", "#: " + NO_UNIT),
                ({"not": yaml}, "x", "#: " + NO_TYPED),
                ({"not": {"not": path}}, "SKILL.md", "#: " + NO_UNIT),
                ({"if": path, "then": True, "else": True}, "SKILL.md", "#: " + NO_UNIT),
                ({"if": yaml, "then": {"const": "x"}, "else": True}, "y", "#: " + NO_TYPED),
                ({"oneOf": [path, True]}, "SKILL.md", "#: " + NO_UNIT),
                ({"anyOf": [path, True]}, "SKILL.md", "#: " + NO_UNIT),
                ({"anyOf": [True, yaml]}, "x", "#: " + NO_TYPED),
                ({"allOf": [path]}, "SKILL.md", "#: " + NO_UNIT),
                ({"x-count-where": {"where": path, "max": 0}}, ["SKILL.md"], "#/0: " + NO_UNIT),
                ({"x-count-where": {"where": {"not": yaml}, "min": 1}}, ["x"], "#/0: " + NO_TYPED),
                ({"properties": {"name": {"not": yaml}}}, {"name": "x"}, "#/name: " + NO_TYPED)):
            with self.subTest(schema=schema):
                self.assertEqual(errors(schema, instance), [expected])

    def test_the_context_keywords_still_work_under_a_combinator_when_the_context_is_given(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("x\n")
        self.assertEqual(errors({"not": {"x-path-exists": "unit"}}, "none.md", unit_root=d), [])
        self.assertEqual(errors({"not": {"x-path-exists": "unit"}}, "SKILL.md", unit_root=d),
                         ["#: not: value matches the schema under not"])
        self.assertEqual(errors({"properties": {"n": {"not": {"x-yaml-string": True}}}}, {"n": "yes"},
                                typed_keys={"n"}), [])


class TestPatternsThatRaiseOtherErrors(unittest.TestCase):
    HUGE, DEEP = "a{99999999999}", "(" * 5000

    def test_a_pattern_that_overflows_or_recurses_refuses_the_schema(self):
        for schema, kw in (({"pattern": self.HUGE}, "pattern"), ({"pattern": self.DEEP}, "pattern"),
                           ({"patternProperties": {self.HUGE: {}}}, "patternProperties"),
                           ({"patternProperties": {self.DEEP: {}}}, "patternProperties"),
                           ({"properties": {"a": {"pattern": self.HUGE}}}, "pattern")):
            with self.subTest(kw=kw, pattern=str(schema)[:40]):
                with self.assertRaises(S.SchemaRefused) as cm:
                    S.compile_schema(schema)
                self.assertEqual(cm.exception.keyword, kw)
                self.assertIn("does not compile", str(cm.exception))

    def test_an_x_python_regex_instance_that_overflows_or_recurses_is_an_instance_error(self):
        for inst in (self.HUGE, self.DEEP):
            with self.subTest(inst=inst[:20]):
                out = errors({"x-python-regex": True}, inst)
                self.assertEqual(len(out), 1, out)
                self.assertTrue(out[0].startswith("#: x-python-regex: string does not compile as a Python regex: "), out)

    def schema_check(self, schema_text, instance_text):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        for name, text in (("s.json", schema_text), ("i.json", instance_text)):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(text)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = S.cli(os.path.join(d, "s.json"), os.path.join(d, "i.json"))
        return rc, out.getvalue(), err.getvalue()

    def test_schema_check_refuses_an_overflowing_pattern_with_exit_2(self):
        rc, out, err = self.schema_check('{"pattern": "a{99999999999}"}', '"a"')
        self.assertEqual((rc, out), (2, ""))
        self.assertTrue(err.startswith("schema refused: #: pattern: "), err)

    def test_schema_check_reports_an_overflowing_x_python_regex_instance_with_exit_1(self):
        rc, out, err = self.schema_check('{"x-python-regex": true}', '"a{99999999999}"')
        self.assertEqual((rc, err), (1, ""))
        self.assertTrue(out.startswith("error #: x-python-regex: string does not compile"), out)


if __name__ == "__main__":
    unittest.main()
