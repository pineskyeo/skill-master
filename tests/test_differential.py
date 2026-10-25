"""A2 differential suite: the schema-backed shape checks and the two default-bundle rules against the A1 bodies kept as
_legacy_<name> (plan.md:602-647, brief A2 D1 to D12). For every corpus unit the legacy path and the CLI path give the
same {row id: status}, must flags, score, categories and verdict; for every corpus file and every named single-edit
variant each public function accepts exactly what its legacy body accepts and returns equal data. A difference must be
listed in tests/differential/intentional.json, and every listed entry must occur. Also here (A6 deletes this file with
the oracle): the schema, rule-entry and policy tests of A2."""
import ast, contextlib, copy, functools, hashlib, inspect, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import eva, skill_eval
from engine import rule_runner as RR, schema as S
import test_multi_tool_parity as P

INTENTIONAL_PATH = os.path.join(ROOT, "tests", "differential", "intentional.json")
SCHEMAS = ("evals.schema.json", "trigger-evals.schema.json", "scenario.schema.json", "transcript.schema.json",
           "skill-frontmatter.schema.json")
RULE_FILES = {"evals-valid": "skill-evals.rules.json", "field-types": "skill-frontmatter.rules.json"}
CORPUS_ROOTS = ("golden", "bad", os.path.join("tests", "fixtures"), os.path.join("tests", "rules"))
GOLDEN_AND_BAD = {"golden/agents/code-reviewer", "golden/agents/research-assistant", "golden/skills/csv-cleaner",
                  "golden/skills/korean-proofreader", "golden/skills/pdf-summarizer", "bad/agents/no-context-agent",
                  "bad/agents/vague-agent", "bad/skills/DoEverything", "bad/skills/bad-version-skill",
                  "bad/skills/no-io-skill", "bad/skills/wrong-folder-name"}
TRANSCRIPT_AGENTS = {"code-reviewer-drill.json": "golden/agents/code-reviewer",
                     "research-followup-cited.json": "golden/agents/research-assistant",
                     "research-followup.json": "golden/agents/research-assistant"}
SUFFIX_RE = re.compile(r" \[(?:rule [a-z-]+, )?([a-z-]+\.schema\.json)#(/[^,\]]*), keyword ([^\]]+)\]$")
DISAGREE = "rule and eva.py disagree"
EMPTY_BUNDLE = RR.Bundle([], "", True, "rules")
# A1 sha256 of each body (ast source segment, def name as at A1), computed from the A1 export by
# /tmp/a2-owner/legacy_pins.py (/tmp/a2-owner/legacy-pins.txt).
LEGACY_PINS = {
    ("eva.py", "evals_errors"): "ac4ea917b3df4f843309efb1e09431007e45a1fd7b56f82f6450292299100abd",
    ("eva.py", "scenario_errors"): "33f26de8156143fb10a3e35a5925eefadddce65c29991429d1952a912a3c7949",
    ("eva.py", "load_transcript"): "f62e296577f37f5a4685a03f0e37ca7b94b7774ff22dacfa01216fb86f4e7d03",
    ("eva.py", "_field_type_errors"): "8949b0a4b456895b6da77ae9420ea23a595887ebbd2dfccc624698a648eb2435",
    ("skill_eval.py", "load_eval_set"): "37487b394b54db63f25e8a02a951583882794582c047fe33f1b10cee5242de12",
    ("skill_eval.py", "load_triggers"): "187630cc42d713e85870646c9afdeba9ac0c4b530ac5ff63315294b7385e1f22",
    # A3: A2 sha256 of each body, computed from the A2 export by /tmp/a3-owner/pins.py (/tmp/a3-owner/pins.txt).
    ("eva.py", "add_english_checks"): "5093b3950fffffe3d2466680dbf1ebf05cde9c7bbdb99976bd6998d33c7b91e7",
    ("eva.py", "add_parity_checks_skill"): "23747214bc7515d22523aa29d1a349f21af6e8d3925d1c179059dfa20282f618",
    ("eva.py", "add_parity_checks_agent"): "be5db8692b6e9343dc654e49468593752175c41544297344521ee8b805234809",
    ("eva.py", "agent_wrapper_states"): "9c497d38a7c97b7924186afe7004413c450f80679ab1a693d55704099c59357b",
    ("eva.py", "english_scan"): "de21cf62408d10608403bddafaa2114032c3b2d3470be7f05db2024687aabe41",
    ("eva.py", "codex_policy"): "e07fc4c7768f851c89b931137c5231ead9b0bf8652a3418379383a6a8dd5aee8",
    ("eva.py", "english_eval_problems"): "26d6aa52179dbcd6be4f7eb663aaaeb54607f35b741e45e620ff53a8164070f9",
}
# A3 rule ids by rule file (brief A3 D3). Their rows come from the bundle; the naming rows merge like the A2 shape rows.
A3_RULE_FILES = {}
for _fname, _ids in (("naming.rules.json", ("name-format", "name-matches-folder", "name-reserved")),
                     ("english-only.rules.json", ("english-skill-md", "english-bundled-files", "english-evals",
                                                  "english-tool-files", "english-agent-md", "english-scenarios",
                                                  "english-wrappers", "english-output-matchers", "english-ruleset")),
                     ("parity-skill.rules.json", ("parity-skill-claude", "parity-skill-codex", "parity-skill-opencode",
                                                  "parity-skill-hermes", "parity-invocation-control",
                                                  "parity-invocation-hermes")),
                     ("parity-agent.rules.json", ("parity-agent-name",) + tuple(
                         "parity-%s-%s" % (k, t) for k in ("wrapper", "permissions")
                         for t in ("claude", "codex", "opencode", "hermes")))):
    for _rid in _ids:
        A3_RULE_FILES[_rid] = _fname
ROW_FAMILY_FILES = ("english-only.rules.json", "parity-skill.rules.json", "parity-agent.rules.json")
UNCHANGED_PINS = {
    ("skill_eval.py", "_check_problems"): "9b96dbde4cc903ed854c49f0607aa88823c4aeb1756c4b817bdcf12400b756ae",
    ("skill_eval.py", "_csv_rows_problems"): "17c8f420caa72778c36bf9f8e7656d582e04b63a30b7ee674a942b5d06527824",
}
# skill_eval.py literals at the A1 head (WT lines 23-32 and 40-73).
A1_CONSTANTS = {
    "LAYERS": ("static", "llm", "human"), "BASELINES": {"none": "without_skill", "old": "old_skill"},
    "PRIMARY": "with_skill", "DEFAULT_RUNS": 3, "TRIGGER_THRESHOLD": 0.5, "TRIGGER_POS": 10, "TRIGGER_NEG": 10,
    "MAX_AUTO_ITERATIONS": 5, "AGREEMENT_MIN": 0.8, "REGRESSION_TOLERANCE": 0.0, "HIGH_VARIANCE_STDDEV": 0.3,
    "PRICES": {"claude-fable-5-1": (10.0, 50.0), "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
               "claude-haiku-5-5": (0.10, 0.50)},
    "DEFAULT_EXECUTOR_MODELS": {"claude": "claude-opus-5-5", "codex": None}, "DEFAULT_GRADER_MODEL": "claude-sonnet-5-5",
    "DEFAULT_TOKENS": {"exec": (60000, 4000), "trigger": (25000, 500), "grader": (12000, 800),
                       "pairwise": (16000, 800), "builder": (80000, 8000)},
    "DEFAULT_BUILDER_MODEL": "claude-opus-5-5", "CLAUDE_EFFORTS": ("low", "medium", "high", "xhigh", "max"),
    "CODEX_EFFORTS": ("none", "minimal", "low", "medium", "high", "xhigh"),
    "CODEX_EFFORT_CONFIG": "model_reasoning_effort=%s", "CODEX_ADMIN_SKILLS": os.path.join("/etc", "codex", "skills"),
    "GRADER_ENGINES": ("claude", "codex"), "CODEX_GRADER_ASSUMED_PRICE": (10.0, 50.0), "CLAUDE_CAP_HEADROOM": 0.5,
    "WORST_CASE_FACTOR": 3.0,
}
POLICY_FILES = ("eval-policy.json", "pricing.json", "models.json")
# The regex compile messages carry the validator's text, a wording change no older test pins (design.md section 6).
WORDING_ONLY = {"check_object:regex-pattern-invalid", "check_object:tool-call-input-regex-invalid",
                "check_object:tool-call-input-regex-number"}


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def wjson(path, obj):
    write(path, json.dumps(obj, indent=1))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def strip(msg):
    return SUFFIX_RE.sub("", msg)


def same_typed(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same_typed(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(same_typed(x, y) for x, y in zip(a, b))
    return a == b


@contextlib.contextmanager
def legacy():
    """The A1 behavior: the public names point at the legacy bodies (the rows then come from check_skill as at A1)."""
    with mock.patch.object(eva, "evals_errors", eva._legacy_evals_errors), \
            mock.patch.object(eva, "scenario_errors", eva._legacy_scenario_errors), \
            mock.patch.object(eva, "load_transcript", eva._legacy_load_transcript), \
            mock.patch.object(eva, "_field_type_errors", eva._legacy_field_type_errors), \
            mock.patch.object(skill_eval, "load_triggers", skill_eval._legacy_load_triggers), \
            mock.patch.object(eva, "add_english_checks",
                              lambda r, path, scan=None, bundle=None: eva._legacy_add_english_checks(r, path, scan)), \
            mock.patch.object(eva, "add_parity_checks_skill",
                              lambda r, path, bundle=None, dedupe=True: eva._legacy_add_parity_checks_skill(r, path)), \
            mock.patch.object(eva, "add_parity_checks_agent",
                              lambda r, path, bundle=None: eva._legacy_add_parity_checks_agent(r, path)), \
            mock.patch.object(eva, "agent_wrapper_states", eva._legacy_agent_wrapper_states), \
            mock.patch.object(eva, "english_scan", lambda path, bundle=None: eva._legacy_english_scan(path)), \
            mock.patch.object(eva, "codex_policy", eva._legacy_codex_policy), \
            mock.patch.object(eva, "english_eval_problems", eva._legacy_english_eval_problems):
        yield


def legacy_source(*names):
    """The source of the legacy bodies eva._legacy_<name>, for the target and operand pins of tests/test_rule_targets.py
    (only this file names the oracle)."""
    return "\n".join(inspect.getsource(getattr(eva, "_legacy_" + n)) for n in names)


def quiet(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a)
    return rc, out.getvalue(), err.getvalue()


def row_view(rep):
    return {"rows": {c["id"]: (c["status"], c["must"], c["category"]) for c in rep.checks},
            "ids": sorted(c["id"] for c in rep.checks), "verdict": rep.verdict, "score": rep.score,
            "categories": rep.categories}


def legacy_check(unit):
    with legacy():
        return eva.check(unit, EMPTY_BUNDLE)


# ---------- the base unit that every skill variant edits once ----------
SKILL_MD = ("---\nname: diff-base\ndescription: Cleans one CSV file for the differential suite. Use when a test needs a "
            "valid skill unit.\nlicense: MIT\nmetadata:\n  version: 1.0.0\n---\n# Diff Base\n## Inputs\n- csv_path\n"
            "## Outputs\n- clean_path\n## Example\nInput a.csv, output a_clean.csv.\n")
BASE_CHECKS = [
    {"type": "regex", "pattern": "done", "target": "final"},
    {"type": "not_regex", "pattern": "error"},
    {"type": "tool_call", "tool": "Bash", "min": 1, "max": 3, "input_regex": "clean"},
    {"type": "skill_triggered", "expected": True},
    {"type": "file_exists", "path": "out/a_clean.csv"},
    {"type": "file_equals_fixture", "path": "out/a_clean.csv", "fixture": "evals/files/a.csv"},
    {"type": "csv_cleaned", "path": "out/a_clean.csv", "fixture": "evals/files/a.csv", "data_rows": 2},
    {"type": "csv_rows", "path": "out/a_clean.csv", "rows": [["1", "x"]], "min": 1, "max": 1, "normalize": "strip",
     "unique": True},
]
BASE_EVALS = {"skill_name": "diff-base", "evals": [
    {"id": 1, "name": "case-one", "prompt": "Clean evals/files/a.csv into out/a_clean.csv.",
     "expected_output": "A cleaned copy.", "files": ["evals/files/a.csv"], "layer": "static",
     "assertions": [{"text": "check %d" % i, "layer": "static", "check": c} for i, c in enumerate(BASE_CHECKS)]},
    {"id": 2, "prompt": "Clean nothing.", "expected_output": "Nothing changes.", "expectations": ["no file is written"]},
    {"id": 3, "prompt": "Explain the cleaner.", "expected_output": "A short explanation.",
     "assertions": ["mentions duplicates", {"text": "graded", "layer": "llm"}]},
]}
BASE_TRIGGERS = [{"query": "dedupe rows in file %d.csv" % i, "should_trigger": True} for i in range(10)] + \
                [{"query": "draw a chart of file %d" % i, "should_trigger": False} for i in range(10)]


def make_unit(top, name="diff-base", skill_md=SKILL_MD, evals=BASE_EVALS, triggers=BASE_TRIGGERS, raw_evals=None):
    path = os.path.join(top, name)
    write(os.path.join(path, "SKILL.md"), skill_md)
    write(os.path.join(path, "evals", "files", "a.csv"), "id,v\n1,x\n2,y\n")
    if raw_evals is not None:
        with open(os.path.join(path, "evals", "evals.json"), "wb") as f:
            f.write(raw_evals)
    elif evals is not None:
        wjson(os.path.join(path, "evals", "evals.json"), evals)
    if triggers is not None:
        wjson(os.path.join(path, "evals", "trigger_evals.json"), triggers)
    return path


def edit_case(i, **kw):
    def f(doc):
        for k, v in kw.items():
            if v is KeyError:
                doc["evals"][i].pop(k, None)
            else:
                doc["evals"][i][k] = v
        return doc
    return f


def edit_doc(**kw):
    def f(doc):
        for k, v in kw.items():
            if v is KeyError:
                doc.pop(k, None)
            else:
                doc[k] = v
        return doc
    return f


def nested(depth):
    v = []
    for _ in range(depth):
        v = [v]
    return v


# Deeper than engine.schema.MAX_DEPTH (128), well inside what json and repr handle.
DEEP = nested(200)

# name -> (edit of BASE_EVALS or raw bytes or None for no file, rejected by the legacy body)
EVALS_VARIANTS = {
    "missing-file": (None, True),
    "not-utf8": (b"\xff\xfe{", True),
    "invalid-json": (b"{oops", True),
    "top-level-list": (lambda d: d["evals"], True),
    "skill-name-mismatch": (edit_doc(skill_name="other"), True),
    "skill-name-missing": (edit_doc(skill_name=KeyError), True),
    "evals-missing": (edit_doc(evals=KeyError), True),
    "evals-empty": (edit_doc(evals=[]), True),
    "evals-not-list": (edit_doc(evals={"id": 1}), True),
    "case-not-object": (lambda d: dict(d, evals=d["evals"] + ["x"]), True),
    "id-missing": (edit_case(0, id=KeyError), True),
    "id-string": (edit_case(0, id="1"), True),
    "id-bool": (edit_case(0, id=True), True),
    "id-float": (edit_case(0, id=1.0), True),
    "prompt-empty": (edit_case(0, prompt=""), True),
    "prompt-blank": (edit_case(0, prompt=" \u3000\n"), True),
    "prompt-missing": (edit_case(1, prompt=KeyError), True),
    "expected-output-null": (edit_case(0, expected_output=None), True),
    "expected-output-missing": (edit_case(0, expected_output=KeyError), True),
    "expected-output-blank": (edit_case(0, expected_output=" \t\n"), True),
    "files-not-list": (edit_case(0, files="evals/files/a.csv"), True),
    "files-entry-blank": (edit_case(0, files=[" "]), True),
    "files-entry-number": (edit_case(0, files=[1]), True),
    "files-backslash": (edit_case(0, files=["evals\\files\\a.csv"]), True),
    "files-absolute": (edit_case(0, files=["/etc/hosts"]), True),
    "files-home": (edit_case(0, files=["~/a.csv"]), True),
    "files-drive": (edit_case(0, files=["C:a.csv"]), True),
    "files-outside": (edit_case(0, files=["../x.csv"]), True),
    "files-outside-deep": (edit_case(0, files=["evals/../../x.csv"]), True),
    "files-missing": (edit_case(0, files=["evals/files/missing.csv"]), True),
    "files-folder": (edit_case(0, files=["evals/files"]), True),
    "files-two-missing": (edit_case(0, files=["evals/files/m1.csv", "evals/files/m1.csv"]), True),
    "assertions-string": (edit_case(2, assertions="x"), True),
    "assertions-empty": (edit_case(2, assertions=[]), True),
    "assertion-blank": (edit_case(2, assertions=[" "]), True),
    "assertion-object-layer": (edit_case(2, assertions=[{"text": "x", "layer": "LLM"}]), True),
    "assertion-object-no-text": (edit_case(2, assertions=[{"layer": "llm"}]), True),
    "assertion-object-text-blank": (edit_case(2, assertions=[{"text": " ", "layer": "llm"}]), True),
    "assertion-object-no-layer": (edit_case(2, assertions=[{"text": "x"}]), True),
    "assertion-object-layer-deep": (edit_case(2, assertions=[{"text": "x", "layer": DEEP}]), True),
    "assertion-object-check-list": (edit_case(2, assertions=[{"text": "x", "layer": "llm", "check": []}]), True),
    "assertion-number": (edit_case(2, assertions=[3]), True),
    "expectations-empty": (edit_case(1, expectations=[]), True),
    "name-uppercase": (edit_case(0, name="Case-One"), True),
    "name-dotdot": (edit_case(0, name="a..b"), True),
    "name-number": (edit_case(0, name=7), True),
    "name-too-long": (edit_case(0, name="a" * 65), True),
    "layer-uppercase": (edit_case(1, layer="Static"), True),
    "layer-null": (edit_case(1, layer=None), True),
    "layer-deep": (edit_case(1, layer=DEEP), True),
    "id-deep": (edit_case(0, id=DEEP), True),
    "id-deep-in-every-case": (lambda d: dict(d, evals=[dict(c, id=DEEP) for c in d["evals"]]), True),
    "duplicate-ids": (edit_case(1, id=1), True),
    "duplicate-ids-two": (lambda d: dict(d, evals=[dict(c, id=c["id"] % 2) for c in d["evals"]] +
                                         [dict(d["evals"][1], id=1)]), True),
    "two-errors": (lambda d: edit_case(0, id="x", prompt="")(d), True),
    "base": (lambda d: d, False),
    "files-empty-list": (edit_case(0, files=[]), False),
    "name-trailing-newline": (edit_case(0, name="case-one\n"), False),
    "assertion-object-no-check": (edit_case(2, assertions=[{"text": "x", "layer": "human"}]), False),
    "extra-keys": (lambda d: dict(d, extra=1, evals=[dict(d["evals"][0], note={"k": 1})] + d["evals"][1:]), False),
    "id-big": (edit_case(1, id=10 ** 20), False),
}


# name -> (meta, typed, rejected by the legacy body)
FIELD_VARIANTS = {
    "name-list": ({"name": ["a", "b"]}, set(), True),
    "name-empty": ({"name": ""}, set(), True),
    "name-null": ({"name": None}, set(), True),
    "description-blank": ({"description": "  "}, set(), True),
    "license-dict": ({"license": {"a": "b"}}, set(), True),
    "compatibility-blank": ({"compatibility": "\t"}, set(), True),
    "compatibility-typed": ({"compatibility": "yes"}, {"compatibility"}, True),
    "name-typed": ({"name": "2024"}, {"name"}, True),
    "description-typed": ({"description": "yes"}, {"description"}, True),
    "allowed-tools-empty": ({"allowed-tools": None}, set(), True),
    "metadata-string": ({"metadata": "x"}, set(), True),
    "metadata-empty": ({"metadata": ""}, set(), True),
    "metadata-value-list": ({"metadata": {"tags": ["a"], "v": "1"}}, set(), True),
    "metadata-value-null": ({"metadata": {"v": None}}, set(), True),
    "argument-hint-empty": ({"argument-hint": ""}, set(), True),
    "argument-hint-list": ({"argument-hint": ["file"]}, set(), True),
    "argument-hint-typed": ({"argument-hint": "42"}, {"argument-hint"}, True),
    "dmi-quoted": ({"disable-model-invocation": "true"}, set(), True),
    "dmi-word": ({"disable-model-invocation": "yes"}, {"disable-model-invocation"}, True),
    "dmi-list": ({"disable-model-invocation": ["true"]}, set(), True),
    "two-errors": ({"name": "", "metadata": {"a": 1}}, set(), True),
    "ok-strings": ({"name": "a", "description": "b", "license": "MIT", "compatibility": "x", "allowed-tools": "Bash",
                    "metadata": {"version": "1.0"}}, set(), False),
    "ok-license-typed": ({"license": "2024"}, {"license"}, False),
    "ok-dmi-true": ({"disable-model-invocation": "true"}, {"disable-model-invocation"}, False),
    "ok-dmi-false-caps": ({"disable-model-invocation": "FALSE"}, {"disable-model-invocation"}, False),
    "ok-argument-hint": ({"argument-hint": "<file>"}, set(), False),
    "ok-empty": ({}, set(), False),
    "ok-metadata-empty-map": ({"metadata": {}}, set(), False),
    "ok-metadata-typed-value": ({"metadata": {"version": "1.0"}}, {"metadata.version"}, False),
}

# name -> (SKILL.md frontmatter lines replacing 'license: MIT', expected legacy field-types status)
FRONTMATTER_UNITS = {
    "fm-license-blank": ('license: "  "', "fail"),
    "fm-compatibility-typed": ("compatibility: yes", "fail"),
    "fm-argument-hint-typed": ("argument-hint: 42", "fail"),
    "fm-dmi-quoted": ('disable-model-invocation: "true"', "fail"),
    "fm-unknown-field": ("metadata2: x", "pass"),
    "fm-dmi-true": ("disable-model-invocation: true", "pass"),
    "fm-broken-frontmatter": (None, "pass"),
}

BASE_SCENARIO = {"id": "s1", "turns": [{"user": "hello"}, {"user": "more"}], "expected_tools": ["grep"],
                 "must_include": ["x"], "expected_outcome": "done", "success_criteria": ["one"]}


def edit(base, **kw):
    out = copy.deepcopy(base)
    for k, v in kw.items():
        if v is KeyError:
            out.pop(k, None)
        else:
            out[k] = v
    return out


SCENARIO_VARIANTS = {
    "not-object": (["s1"], True),
    "null": (None, True),
    "id-missing": (edit(BASE_SCENARIO, id=KeyError), True),
    "id-blank": (edit(BASE_SCENARIO, id=" "), True),
    "id-number": (edit(BASE_SCENARIO, id=1), True),
    "turns-missing": (edit(BASE_SCENARIO, turns=KeyError), True),
    "turns-empty": (edit(BASE_SCENARIO, turns=[]), True),
    "turn-not-object": (edit(BASE_SCENARIO, turns=["hi"]), True),
    "turn-user-blank": (edit(BASE_SCENARIO, turns=[{"user": ""}]), True),
    "turn-user-missing": (edit(BASE_SCENARIO, turns=[{"text": "hi"}]), True),
    "expected-tools-missing": (edit(BASE_SCENARIO, expected_tools=KeyError), True),
    "expected-tools-item-blank": (edit(BASE_SCENARIO, expected_tools=["grep", " "]), True),
    "must-include-empty": (edit(BASE_SCENARIO, must_include=[]), True),
    "must-include-string": (edit(BASE_SCENARIO, must_include="x"), True),
    "expected-outcome-missing": (edit(BASE_SCENARIO, expected_outcome=KeyError), True),
    "expected-outcome-blank-list": (edit(BASE_SCENARIO, expected_outcome=[" "]), True),
    "expected-outcome-number": (edit(BASE_SCENARIO, expected_outcome=3), True),
    "success-criteria-number": (edit(BASE_SCENARIO, success_criteria=3), True),
    "success-criteria-null": (edit(BASE_SCENARIO, success_criteria=None), True),
    "all-wrong": ({"turns": 1}, True),
    "base": (BASE_SCENARIO, False),
    "outcome-list": (edit(BASE_SCENARIO, expected_outcome=["a", "b"]), False),
    "no-success-criteria": (edit(BASE_SCENARIO, success_criteria=KeyError), False),
    "extra-keys": (edit(BASE_SCENARIO, note=1, turns=[{"user": "u", "x": 2}]), False),
}

BASE_TRANSCRIPT = {"scenario": "s1", "final": "done", "workspace": "/tmp/ws",
                   "tool_calls": ["grep", {"tool": "read_file", "input": {"path": "a"}, "result": "1\tline"},
                                  {"tool": "list_dir"}],
                   "tool_results": ["extra"]}
TRANSCRIPT_VARIANTS = {
    "missing-file": (None, True),
    "invalid-json": (b"{oops", True),
    "not-utf8": (b"\xff{}", True),
    "not-object": ([1], True),
    "scenario-missing": (edit(BASE_TRANSCRIPT, scenario=KeyError), True),
    "scenario-blank": (edit(BASE_TRANSCRIPT, scenario=" "), True),
    "final-missing": (edit(BASE_TRANSCRIPT, final=KeyError), True),
    "final-number": (edit(BASE_TRANSCRIPT, final=1), True),
    "workspace-blank": (edit(BASE_TRANSCRIPT, workspace=""), True),
    "workspace-whitespace": (edit(BASE_TRANSCRIPT, workspace=" \t"), True),
    "workspace-null": (edit(BASE_TRANSCRIPT, workspace=None), True),
    "tool-calls-missing": (edit(BASE_TRANSCRIPT, tool_calls=KeyError), True),
    "tool-calls-object": (edit(BASE_TRANSCRIPT, tool_calls={"tool": "x"}), True),
    "tool-call-blank": (edit(BASE_TRANSCRIPT, tool_calls=["grep", " "]), True),
    "tool-call-number": (edit(BASE_TRANSCRIPT, tool_calls=[5]), True),
    "tool-call-no-tool": (edit(BASE_TRANSCRIPT, tool_calls=[{"input": "x"}]), True),
    "tool-call-tool-blank": (edit(BASE_TRANSCRIPT, tool_calls=[{"tool": ""}]), True),
    "tool-call-result-number": (edit(BASE_TRANSCRIPT, tool_calls=[{"tool": "t", "result": 1}]), True),
    "tool-call-unknown-fields": (edit(BASE_TRANSCRIPT, tool_calls=[{"tool": "t", "zeta": 1, "alpha": 2}]), True),
    "tool-call-result-and-unknown": (edit(BASE_TRANSCRIPT, tool_calls=[{"tool": "t", "result": None, "x": 1}]), True),
    "tool-results-string": (edit(BASE_TRANSCRIPT, tool_results="x"), True),
    "tool-results-null": (edit(BASE_TRANSCRIPT, tool_results=None), True),
    "tool-results-item-number": (edit(BASE_TRANSCRIPT, tool_results=["a", 2]), True),
    "base": (BASE_TRANSCRIPT, False),
    "no-workspace-no-results": (edit(BASE_TRANSCRIPT, workspace=KeyError, tool_results=KeyError), False),
    "final-empty": (edit(BASE_TRANSCRIPT, final="", tool_calls=[]), False),
    "extra-top-keys": (edit(BASE_TRANSCRIPT, note=1), False),
}


def triggers(pos, neg):
    return BASE_TRIGGERS[:pos] + BASE_TRIGGERS[10:10 + neg] + \
        [{"query": "extra negative %d" % i, "should_trigger": False} for i in range(max(0, neg - 10))]


TRIGGER_VARIANTS = {
    "missing-file": (None, True),
    "invalid-json": (b"[oops", True),
    "not-list": ({"query": "x"}, True),
    "item-not-object": (BASE_TRIGGERS + ["x"], True),
    "query-blank": ([dict(BASE_TRIGGERS[0], query=" ")] + BASE_TRIGGERS[1:], True),
    "query-missing": ([{"should_trigger": True}] + BASE_TRIGGERS[1:], True),
    "should-trigger-string": ([dict(BASE_TRIGGERS[0], should_trigger="true")] + BASE_TRIGGERS[1:], True),
    "query-deep": ([dict(BASE_TRIGGERS[0], query=DEEP)] + BASE_TRIGGERS[1:], True),
    "query-deep-twice": ([dict(q, query=DEEP) for q in BASE_TRIGGERS[:2]] + BASE_TRIGGERS[2:], True),
    "duplicate-query": (BASE_TRIGGERS[:19] + [dict(BASE_TRIGGERS[10])], True),
    "count-9-10": (triggers(9, 10), True),
    "count-9-11": (triggers(9, 11), True),
    "count-10-11": (triggers(10, 11), True),
    "count-0-0": ([], True),
    "base": (BASE_TRIGGERS, False),
    "extra-keys": ([dict(q, note=1) for q in BASE_TRIGGERS], False),
}


def chk(base, **kw):
    return edit(base, **kw)


REGEX, TOOL, SKILLT, FILE_E, FILE_F, CLEANED, ROWS = BASE_CHECKS[0], BASE_CHECKS[2], BASE_CHECKS[3], BASE_CHECKS[4], \
    BASE_CHECKS[5], BASE_CHECKS[6], {"type": "csv_rows", "path": "o.csv", "rows": [["1", "2"]]}
CHECK_VARIANTS = {
    "not-object": ("regex", True),
    "type-missing": ({"pattern": "x"}, True),
    "type-unknown": ({"type": "nope"}, True),
    "type-number": ({"type": 1}, True),
    "regex-pattern-missing": (chk(REGEX, pattern=KeyError), True),
    "regex-pattern-empty": (chk(REGEX, pattern=""), True),
    "regex-pattern-number": (chk(REGEX, pattern=5), True),
    "regex-pattern-invalid": (chk(REGEX, pattern="("), True),
    "regex-target-bad": (chk(REGEX, target="stdout"), True),
    "regex-target-null": (chk(REGEX, target=None), True),
    "tool-call-tool-missing": (chk(TOOL, tool=KeyError), True),
    "tool-call-tool-empty": (chk(TOOL, tool=""), True),
    "tool-call-min-negative": (chk(TOOL, min=-1), True),
    "tool-call-max-bool": (chk(TOOL, max=True), True),
    "tool-call-max-float": (chk(TOOL, max=2.0), True),
    "tool-call-input-regex-invalid": (chk(TOOL, input_regex="["), True),
    "tool-call-input-regex-number": (chk(TOOL, input_regex=5), True),
    "skill-triggered-expected-missing": ({"type": "skill_triggered"}, True),
    "skill-triggered-expected-string": (chk(SKILLT, expected="yes"), True),
    "file-exists-path-missing": ({"type": "file_exists"}, True),
    "file-exists-path-empty": (chk(FILE_E, path=""), True),
    "file-exists-path-absolute": (chk(FILE_E, path="/abs/o.csv"), True),
    "file-exists-path-dotdot": (chk(FILE_E, path="a/../o.csv"), True),
    "file-exists-path-backslash-dotdot": (chk(FILE_E, path="a\\..\\o.csv"), True),
    "file-exists-path-list": (chk(FILE_E, path=["o.csv"]), True),
    "file-equals-fixture-missing": (chk(FILE_F, fixture=KeyError), True),
    "file-equals-path-dotdot-end": (chk(FILE_F, path="out/.."), True),
    "csv-cleaned-data-rows-missing": (chk(CLEANED, data_rows=KeyError), True),
    "csv-cleaned-data-rows-negative": (chk(CLEANED, data_rows=-1), True),
    "csv-cleaned-data-rows-bool": (chk(CLEANED, data_rows=False), True),
    "csv-cleaned-fixture-absolute": (chk(CLEANED, fixture="/x"), True),
    "csv-rows-unknown-key": (chk(ROWS, fixture="x"), True),
    "csv-rows-two-unknown-keys": (chk(ROWS, mni=1, data_rows=2), True),
    "csv-rows-unknown-key-surrogate": (chk(ROWS, **{"\ud800": 1}), True),
    "csv-rows-path-missing": (chk(ROWS, path=KeyError), True),
    "csv-rows-path-dotdot": (chk(ROWS, path="../o.csv"), True),
    "csv-rows-path-surrogate": (chk(ROWS, path="o\ud800.csv"), True),
    "csv-rows-rows-not-list": (chk(ROWS, rows="a"), True),
    "csv-rows-rows-inner-not-list": (chk(ROWS, rows=["a"]), True),
    "csv-rows-rows-cell-number": (chk(ROWS, rows=[[1]]), True),
    "csv-rows-rows-nested": (chk(ROWS, rows=[[["a"]]]), True),
    "csv-rows-rows-empty": (chk(ROWS, rows=[]), True),
    "csv-rows-rows-empty-unique-false": (chk(ROWS, rows=[], unique=False), True),
    "csv-rows-rows-bad-unique-true": (chk(ROWS, rows=[[1]], unique=True), True),
    "csv-rows-rows-cell-surrogate": (chk(ROWS, rows=[["ok"], ["\udfff"]]), True),
    "csv-rows-min-negative": (chk(ROWS, min=-1), True),
    "csv-rows-min-string": (chk(ROWS, min="1"), True),
    "csv-rows-max-float": (chk(ROWS, max=1.5), True),
    "csv-rows-min-without-rows": ({"type": "csv_rows", "path": "o.csv", "unique": True, "min": 1}, True),
    "csv-rows-max-zero-without-min": (chk(ROWS, max=0), True),
    "csv-rows-min-greater-than-max": (chk(ROWS, min=3, max=2), True),
    "csv-rows-min-zero-without-max": (chk(ROWS, min=0), True),
    "csv-rows-empty-rows-with-min": (chk(ROWS, rows=[], unique=True, min=1), True),
    "csv-rows-empty-rows-with-max": (chk(ROWS, rows=[], unique=True, max=2), True),
    "csv-rows-normalize-bad": (chk(ROWS, normalize="lower"), True),
    "csv-rows-normalize-null": (chk(ROWS, normalize=None), True),
    "csv-rows-unique-string": (chk(ROWS, unique="true"), True),
    "csv-rows-asserts-nothing": ({"type": "csv_rows", "path": "o.csv"}, True),
    "csv-rows-asserts-nothing-unique-false": ({"type": "csv_rows", "path": "o.csv", "unique": False}, True),
    "csv-rows-float-min-gt-max": (chk(ROWS, min=1.5, max=1), True),
    "csv-rows-many": ({"type": "csv_rows", "rows": [], "min": -1, "max": 0, "normalize": 1, "unique": 2, "z": 0}, True),
    "type-deep": ({"type": DEEP}, True),
    "regex-target-deep": (chk(REGEX, target=DEEP), True),
    "csv-rows-normalize-deep": (chk(ROWS, normalize=DEEP), True),
    "csv-rows-unique-deep": (chk(ROWS, rows=[], unique=DEEP), True),
    "csv-rows-min-deep": (chk(ROWS, min=DEEP), True),
}
for _i, _c in enumerate(BASE_CHECKS):
    CHECK_VARIANTS["ok-base-%d" % _i] = (_c, False)
CHECK_VARIANTS.update({
    "ok-regex-empty-target-default": ({"type": "regex", "pattern": "x"}, False),
    "ok-input-regex-empty": (chk(TOOL, input_regex=""), False),
    "ok-path-dots": (chk(FILE_E, path="a/.../b..c"), False),
    "ok-path-dotdot-newline": (chk(FILE_E, path="..\n"), False),
    "ok-csv-rows-blank-line": (chk(ROWS, rows=[[]]), False),
    "ok-csv-rows-min0-max0": (chk(ROWS, rows=[[]], min=0, max=0), False),
    "ok-csv-rows-unique-only": ({"type": "csv_rows", "path": "o.csv", "unique": True}, False),
    "ok-csv-rows-empty-unique": (chk(ROWS, rows=[], unique=True), False),
    "ok-csv-rows-empty-cell": (chk(ROWS, rows=[[""]]), False),
    "ok-csv-rows-astral": (chk(ROWS, path="o\U0001F600.csv", rows=[["\U0001F600"]]), False),
    "ok-csv-rows-min1": (chk(ROWS, min=1), False),
    "ok-extra-keys-regex": (chk(REGEX, note=1), False),
})


@functools.lru_cache(maxsize=None)
def tmpdir():
    d = tempfile.mkdtemp(prefix="a2-diff-")
    import atexit
    atexit.register(shutil.rmtree, d, True)
    return d


def evals_variant_unit(name):
    edit_fn, _ = EVALS_VARIANTS[name]
    top = os.path.join(tmpdir(), "evals", name)
    if edit_fn is None:
        return make_unit(top, evals=None)
    if isinstance(edit_fn, bytes):
        return make_unit(top, raw_evals=edit_fn)
    return make_unit(top, evals=edit_fn(copy.deepcopy(BASE_EVALS)))


def frontmatter_variant_unit(name):
    line, _ = FRONTMATTER_UNITS[name]
    top = os.path.join(tmpdir(), "fm", name)
    md = SKILL_MD.replace("license: MIT", line) if line is not None else SKILL_MD.replace("---\n# Diff", "# Diff", 1)
    return make_unit(top, skill_md=md)


def transcript_variant_file(name):
    data, _ = TRANSCRIPT_VARIANTS[name]
    path = os.path.join(tmpdir(), "transcripts", name + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(data, bytes):
        with open(path, "wb") as f:
            f.write(data)
    elif data is not None:
        wjson(path, data)
    return path


def trigger_variant_unit(name):
    data, _ = TRIGGER_VARIANTS[name]
    top = os.path.join(tmpdir(), "triggers", name)
    path = make_unit(top, triggers=None if isinstance(data, bytes) else data)
    if isinstance(data, bytes):
        with open(os.path.join(path, "evals", "trigger_evals.json"), "wb") as f:
            f.write(data)
    return path


# ---------- A3 variants: one per legacy branch of the English, parity and naming bodies (brief A3 D14) ----------
KO = "\ud55c\uad6d\uc5b4"
CHEROKEE, YI = "\u13a0\u13a1\u13a2", "\ua000\ua001\ua002"
CODEX_REL, OPENCODE_REL, CLAUDE_REL, HERMES_REL = ("wrappers/codex/code-scout.toml", "wrappers/opencode/code-scout.md",
                                                   "wrappers/claude/code-scout.md", "wrappers/hermes/SOUL.md")


def sub(old, new):
    return lambda t: t.replace(old, new, 1)


def skill_variant(name="diff-base", md_edit=None, files=None, links=None, raw=None, evals_edit=None):
    def build(top):
        md = SKILL_MD.replace("name: diff-base", "name: " + name, 1) if name != "diff-base" else SKILL_MD
        md = md_edit(md) if md_edit else md
        ev = evals_edit(copy.deepcopy(BASE_EVALS)) if evals_edit else BASE_EVALS
        path = make_unit(top, name=name, skill_md=md, evals=ev)
        for rel_path, text in (files or {}).items():
            fp = os.path.join(path, *rel_path.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            if isinstance(text, bytes):
                with open(fp, "wb") as f:
                    f.write(text)
            elif text is None:
                os.makedirs(fp, exist_ok=True)
            else:
                write(fp, text)
        for rel_path, target in (links or {}).items():
            os.symlink(target(path) if callable(target) else target, os.path.join(path, *rel_path.split("/")))
        return path
    return build


def agent_variant(md_edit=None, caps=None, skip=(), replace=None, files=None, links=None, wrappers=True, folder="code-scout"):
    def build(top):
        d = os.path.join(top, folder)
        md = md_edit(P.AGENT_MD) if md_edit else P.AGENT_MD
        write(os.path.join(d, "AGENT.md"), md)
        write(os.path.join(d, "scenarios", "01.json"), json.dumps(P.S1, indent=1))
        write(os.path.join(d, "scenarios", "02.json"), json.dumps(P.S2, indent=1))
        if wrappers:
            P.write_wrappers(d, caps=caps, skip=skip, replace=replace)
        for rel_path, text in (files or {}).items():
            fp = os.path.join(d, *rel_path.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            if isinstance(text, bytes):
                with open(fp, "wb") as f:
                    f.write(text)
            else:
                write(fp, text)
        for rel_path, target in (links or {}).items():
            fp = os.path.join(d, *rel_path.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            os.symlink(target(d) if callable(target) else target, fp)
        return d
    return build


def outside_file(text):
    def make(unit):
        fp = os.path.dirname(unit) + "-outside.txt"
        write(fp, text)
        return fp
    return make


def fm(extra):
    return sub("license: MIT\n", "license: MIT\n" + extra)


YAML = "agents/openai.yaml"
ALL_CAPS = "[read_file, write_file, bash, web_fetch, web_search, task, skill]"
# name -> (builder, row id, legacy status that proves the branch is reached)
A3_VARIANTS = {
    "skill-base": (skill_variant(), "parity-skill-claude", "pass"),
    "skill-no-frontmatter": (skill_variant(md_edit=lambda t: t.replace("---\n", "", 2)), "parity-skill-claude", "fail"),
    "skill-no-description": (skill_variant(md_edit=lambda t: re.sub(r"description: .*\n", "", t)), "parity-skill-hermes",
                             "fail"),
    "skill-reserved-word": (skill_variant(name="claude-helper"), "parity-skill-claude", "fail"),
    "skill-long-name": (skill_variant(name="a" * 70), "parity-skill-claude", "fail"),
    "skill-long-description": (skill_variant(md_edit=sub("Use when a test", "x" * 1100 + " Use when a test")),
                               "parity-skill-claude", "fail"),
    "skill-angle": (skill_variant(md_edit=sub("one CSV file", "one <csv> file")), "parity-skill-claude", "fail"),
    "skill-codex-unverified": (skill_variant(files={YAML: "interface:\n  display_name: X\n"}), "parity-skill-codex",
                               "needs-review"),
    "skill-codex-quoted": (skill_variant(files={YAML: 'policy:\n  allow_implicit_invocation: "false"\n'}),
                           "parity-skill-codex", "fail"),
    "skill-codex-not-mapping": (skill_variant(files={YAML: "policy: strict\n"}), "parity-skill-codex", "fail"),
    "skill-codex-markers": (skill_variant(files={YAML: "a: 1\n---\nb: 2\n"}), "parity-skill-codex", "fail"),
    "skill-codex-parse-error": (skill_variant(files={YAML: "a: 1\nb: 2\nc d\n"}), "parity-skill-codex", "fail"),
    "skill-codex-folder": (skill_variant(files={YAML + "/x.txt": "x"}), "parity-skill-codex", "fail"),
    "skill-codex-undecodable": (skill_variant(files={YAML: b"\xff\xfe\xfa\x80"}), "parity-skill-codex", "fail"),
    "skill-codex-true": (skill_variant(files={YAML: "policy:\n  allow_implicit_invocation: true\n"}),
                         "parity-skill-codex", "pass"),
    "skill-codex-other-policy": (skill_variant(files={YAML: "policy:\n  other: 1\n"}), "parity-skill-codex",
                                 "needs-review"),
    "skill-codex-empty": (skill_variant(files={YAML: ""}), "parity-skill-codex", "pass"),
    "skill-opencode-regex": (skill_variant(name="diff_base"), "parity-skill-opencode", "fail"),
    "skill-nfkc-name": (skill_variant(md_edit=sub("name: diff-base", "name: \uff44\uff49\uff46\uff46-base")),
                        "parity-skill-opencode", "fail"),
    "skill-metadata-typed": (skill_variant(md_edit=sub("version: 1.0.0", "version: 1.0")), "parity-skill-opencode",
                             "fail"),
    "skill-metadata-list": (skill_variant(md_edit=sub("version: 1.0.0", "version: [1, 2]")), "parity-skill-opencode",
                            "fail"),
    "skill-metadata-not-mapping": (skill_variant(md_edit=sub("metadata:\n  version: 1.0.0\n", "metadata: text\n")),
                                   "parity-skill-opencode", "fail"),
    "skill-autoinvoke-bad": (skill_variant(md_edit=sub("version: 1.0.0", 'version: 1.0.0\n  opencode/autoinvoke: "no"')),
                             "parity-skill-opencode", "fail"),
    "skill-hermes-mapping": (skill_variant(md_edit=sub("version: 1.0.0", "version: 1.0.0\n  hermes:\n    tags: [a]")),
                             "parity-skill-hermes", "fail"),
    "skill-hermes-flow": (skill_variant(md_edit=sub("version: 1.0.0", "version: 1.0.0\n  hermes: {tags: [a]}")),
                          "parity-skill-hermes", "fail"),
    "skill-hermes-string": (skill_variant(md_edit=sub("version: 1.0.0", "version: 1.0.0\n  hermes: terminal")),
                            "parity-skill-hermes", "pass"),
    "skill-disabled": (skill_variant(md_edit=fm("disable-model-invocation: true\n")), "parity-invocation-control", "fail"),
    "skill-disabled-opencode-only": (skill_variant(md_edit=lambda t: fm("disable-model-invocation: true\n")(t).replace(
        "version: 1.0.0", 'version: 1.0.0\n  opencode/autoinvoke: "false"')), "parity-invocation-control", "fail"),
    "skill-disabled-codex-only": (skill_variant(md_edit=fm("disable-model-invocation: true\n"),
                                                files={YAML: "policy:\n  allow_implicit_invocation: false\n"}),
                                  "parity-invocation-control", "fail"),
    "skill-disabled-both": (skill_variant(md_edit=lambda t: fm("disable-model-invocation: true\n")(t).replace(
        "version: 1.0.0", 'version: 1.0.0\n  opencode/autoinvoke: "false"'),
        files={YAML: "policy:\n  allow_implicit_invocation: false\n"}), "parity-invocation-hermes", "needs-review"),
    "skill-disabled-false": (skill_variant(md_edit=fm("disable-model-invocation: false\n")), "parity-invocation-control",
                             "n/a"),
    "skill-disabled-string": (skill_variant(md_edit=fm('disable-model-invocation: "TRUE"\n')), "parity-invocation-control",
                              "fail"),
    "skill-name-format": (skill_variant(name="Diff--Base"), "name-format", "fail"),
    "skill-ko-body": (skill_variant(md_edit=lambda t: t + KO + "\n"), "english-skill-md", "fail"),
    "agent-md-not-utf8": (agent_variant(files={"AGENT.md": b"---\nname: code-scout\n---\n\xff\xfe\x80\n"}),
                          "english-agent-md", "n/a"),
    "skill-ko-frontmatter-escape": (skill_variant(md_edit=fm('compatibility: "\\uac00"\n')), "english-skill-md", "fail"),
    "skill-cherokee-yi": (skill_variant(md_edit=lambda t: t + CHEROKEE + "\n", files={"scripts/run.py": "# %s\n" % YI}),
                          "english-bundled-files", "fail"),
    "skill-ko-bundled": (skill_variant(files={"references/a.md": "x %s\n" % KO}), "english-bundled-files", "fail"),
    "skill-ko-file-name": (skill_variant(files={"references/%s.md" % KO: "x\n"}), "english-bundled-files", "fail"),
    "skill-many-findings": (skill_variant(files={"references/a.md": ("%s\n" % KO) * 8}), "english-bundled-files", "fail"),
    "skill-binary": (skill_variant(files={"assets/x.png": b"\x89PNG\r\n\x1a\n\x00\xff"}), "english-bundled-files", "n/a"),
    "skill-undecodable": (skill_variant(files={"assets/x.dat": b"\x80\x81\x82"}), "english-bundled-files", "fail"),
    "skill-json-bundled": (skill_variant(files={"assets/d.json": json.dumps({"k": KO})}), "english-bundled-files", "fail"),
    "skill-toml-bundled": (skill_variant(files={"assets/d.toml": 'k = "\\uac00"\n'}), "english-bundled-files", "fail"),
    "skill-link-outside": (skill_variant(links={"references": outside_file("x\n")}), "english-bundled-files", "fail"),
    "skill-link-cycle": (skill_variant(files={"references/a.md": "x\n"}, links={"references/loop": lambda u: os.path.join(
        u, "references")}), "english-bundled-files", "fail"),
    "skill-ko-evals": (skill_variant(evals_edit=edit_case(1, prompt=KO)), "english-evals", "fail"),
    "skill-ko-matcher": (skill_variant(evals_edit=lambda d: (d["evals"][0]["assertions"][0]["check"].update(pattern=KO), d)[1]),
                         "english-output-matchers", "needs-review"),
    "skill-evals-invalid-json": (skill_variant(files={"evals/evals.json": '{"skill_name": "%s",' % KO}), "english-evals",
                                 "fail"),
    "skill-ko-tool-file": (skill_variant(files={YAML: 'interface:\n  display_name: "\\uAC00"\n'}), "english-tool-files",
                           "fail"),
    "agent-complete": (agent_variant(), "parity-permissions-codex", "needs-review"),
    "agent-no-wrappers": (agent_variant(wrappers=False), "parity-wrapper-claude", "fail"),
    "agent-skip-claude": (agent_variant(skip=("claude",)), "parity-permissions-claude", "n/a"),
    "agent-skip-codex": (agent_variant(skip=("codex",)), "parity-permissions-codex", "n/a"),
    "agent-skip-opencode": (agent_variant(skip=("opencode",)), "parity-permissions-opencode", "n/a"),
    "agent-skip-hermes": (agent_variant(skip=("hermes",)), "parity-wrapper-hermes", "fail"),
    "agent-no-name": (agent_variant(md_edit=sub("name: code-scout\n", ""), wrappers=False), "parity-wrapper-codex", "fail"),
    "agent-bad-name": (agent_variant(md_edit=sub("name: code-scout", "name: Code_Scout")), "parity-agent-name", "fail"),
    "agent-claude-unparsable": (agent_variant(replace={CLAUDE_REL: "no frontmatter\n"}), "parity-wrapper-claude", "fail"),
    "agent-claude-name-differs": (agent_variant(replace={CLAUDE_REL: sub("name: code-scout", "name: other")}),
                                  "parity-wrapper-claude", "fail"),
    "agent-claude-description-differs": (agent_variant(replace={CLAUDE_REL: sub("Reads a repository", "Scans a repository")}),
                                         "parity-wrapper-claude", "fail"),
    "agent-claude-no-tools": (agent_variant(replace={CLAUDE_REL: lambda t: re.sub(r"tools: .*\n", "", t)}),
                              "parity-wrapper-claude", "fail"),
    "agent-claude-unmapped-tool": (agent_variant(replace={CLAUDE_REL: sub("Glob, Grep, Read", "Read, CrmLookup")}),
                                   "parity-wrapper-claude", "fail"),
    "agent-claude-model-empty": (agent_variant(replace={CLAUDE_REL: sub("---\nYou", "model:\n---\nYou")}),
                                 "parity-wrapper-claude", "fail"),
    "agent-claude-body-empty": (agent_variant(replace={CLAUDE_REL: lambda t: t.split("---\n")[0] + "---\n" + t.split("---\n")[1]
                                                       + "---\n"}), "parity-wrapper-claude", "fail"),
    "agent-claude-outside": (agent_variant(skip=("claude",), links={CLAUDE_REL: outside_file("---\nname: code-scout\n---\nx\n")}),
                             "parity-wrapper-claude", "fail"),
    "agent-opencode-unparsable": (agent_variant(replace={OPENCODE_REL: "---\ndescription: x\n"}), "parity-wrapper-opencode",
                                  "fail"),
    "agent-opencode-mode": (agent_variant(replace={OPENCODE_REL: sub("subagent", "sidekick")}), "parity-wrapper-opencode",
                            "fail"),
    "agent-opencode-name-differs": (agent_variant(replace={OPENCODE_REL: sub("mode: subagent", "name: other\nmode: subagent")}),
                                    "parity-wrapper-opencode", "fail"),
    "agent-opencode-name-empty": (agent_variant(replace={OPENCODE_REL: sub("mode: subagent", "name: ''\nmode: subagent")}),
                                  "parity-wrapper-opencode", "fail"),
    "agent-opencode-model-empty": (agent_variant(replace={OPENCODE_REL: sub("mode: subagent", "model: ''\nmode: subagent")}),
                                   "parity-wrapper-opencode", "fail"),
    "agent-opencode-no-description": (agent_variant(replace={OPENCODE_REL: lambda t: re.sub(r"description: .*\n", "", t)}),
                                      "parity-wrapper-opencode", "fail"),
    "agent-opencode-no-permission": (agent_variant(replace={OPENCODE_REL: lambda t: re.sub(r"permission:\n(  .*\n)+", "", t)}),
                                     "parity-wrapper-opencode", "fail"),
    "agent-opencode-key-missing": (agent_variant(replace={OPENCODE_REL: sub("  lsp: allow\n", "")}), "parity-wrapper-opencode",
                                   "fail"),
    "agent-opencode-bad-value": (agent_variant(replace={OPENCODE_REL: sub("edit: deny", "edit: sometimes")}),
                                 "parity-wrapper-opencode", "fail"),
    "agent-opencode-external-allow": (agent_variant(replace={OPENCODE_REL: sub("external_directory: deny",
                                                                               "external_directory: allow")}),
                                      "parity-wrapper-opencode", "fail"),
    "agent-opencode-unknown-key": (agent_variant(replace={OPENCODE_REL: sub("webfetch: deny", "webfetch: deny\n  foo: deny")}),
                                   "parity-wrapper-opencode", "fail"),
    "agent-opencode-ask": (agent_variant(replace={OPENCODE_REL: sub("bash: deny", "bash: ask")}),
                           "parity-permissions-opencode", "fail"),
    "agent-codex-parse-error": (agent_variant(replace={CODEX_REL: "name = code-scout\n"}), "parity-wrapper-codex", "fail"),
    "agent-codex-duplicate": (agent_variant(replace={CODEX_REL: lambda t: t + 'name = "x"\n'}), "parity-wrapper-codex",
                              "fail"),
    "agent-codex-sandbox": (agent_variant(replace={CODEX_REL: lambda t: t + 'sandbox_mode = "read-only"\n'}),
                            "parity-wrapper-codex", "fail"),
    "agent-codex-unknown-key": (agent_variant(replace={CODEX_REL: lambda t: t + 'foo = "x"\n'}), "parity-wrapper-codex",
                                "fail"),
    "agent-codex-model-empty": (agent_variant(replace={CODEX_REL: lambda t: t + 'model = ""\n'}), "parity-wrapper-codex",
                                "fail"),
    "agent-codex-optional-keys": (agent_variant(replace={CODEX_REL: lambda t: t + 'model = "m"\nmodel_reasoning_effort = "high"\n'}),
                                  "parity-wrapper-codex", "pass"),
    "agent-codex-no-instructions": (agent_variant(replace={CODEX_REL: 'name = "code-scout"\ndescription = "x"\n'}),
                                    "parity-wrapper-codex", "fail"),
    "agent-codex-undecodable": (agent_variant(files={CODEX_REL: b"name = \"\xff\"\n"}), "parity-wrapper-codex", "fail"),
    "agent-hermes-empty": (agent_variant(replace={HERMES_REL: "   \n"}), "parity-wrapper-hermes", "fail"),
    "agent-hermes-no-name": (agent_variant(replace={HERMES_REL: sub("# code-scout", "# scout")}), "parity-wrapper-hermes",
                             "fail"),
    "agent-hermes-description": (agent_variant(replace={HERMES_REL: sub("Reads a repository", "Scans a repository")}),
                                 "parity-wrapper-hermes", "fail"),
    "agent-hermes-config-ok": (agent_variant(files={"wrappers/hermes/config.yaml": "skills:\n  x: y\n"}),
                               "parity-wrapper-hermes", "pass"),
    "agent-hermes-config-empty": (agent_variant(files={"wrappers/hermes/config.yaml": "\n"}), "parity-wrapper-hermes", "fail"),
    "agent-hermes-config-outside": (agent_variant(links={"wrappers/hermes/config.yaml": outside_file("a: b\n")}),
                                    "parity-wrapper-hermes", "fail"),
    "agent-unmapped-tool": (agent_variant(md_edit=sub("[glob, grep, read_file]", "[read_file, crm_lookup]")),
                            "parity-permissions-claude", "fail"),
    "agent-all-caps": (agent_variant(md_edit=sub("[glob, grep, read_file]", ALL_CAPS)), "parity-permissions-codex", "pass"),
    "agent-grants-more": (agent_variant(caps={"read", "write", "shell", "web_fetch", "web_search"}),
                          "parity-permissions-opencode", "fail"),
    "agent-grants-less": (agent_variant(md_edit=sub("[glob, grep, read_file]", "[read_file, write_file, web_fetch]"),
                                        caps={"read"}), "parity-permissions-claude", "fail"),
    "agent-write-web": (agent_variant(md_edit=sub("[glob, grep, read_file]", "[read_file, write_file, web_fetch]")),
                        "parity-permissions-claude", "pass"),
    "agent-ko-agent-md": (agent_variant(md_edit=lambda t: t + KO + "\n"), "english-agent-md", "fail"),
    "agent-ko-scenario": (agent_variant(files={"scenarios/03.json": json.dumps(dict(P.S1, id=KO))}), "english-scenarios",
                          "fail"),
    "agent-ko-matcher": (agent_variant(files={"scenarios/03.json": json.dumps(dict(P.S1, must_include=[KO]))}),
                         "english-output-matchers", "needs-review"),
    "agent-ko-scenario-name": (agent_variant(files={"scenarios/%s.json" % KO: json.dumps(P.S1)}), "english-scenarios", "fail"),
    "agent-scenario-link-outside": (agent_variant(links={"scenarios/04.json": outside_file("{}")}), "english-scenarios", "fail"),
    "agent-ko-wrapper": (agent_variant(replace={HERMES_REL: lambda t: t + KO + "\n"}), "english-wrappers", "fail"),
    "agent-ko-wrapper-name": (agent_variant(files={"wrappers/hermes/%s.md" % KO: "x\n"}), "english-wrappers", "fail"),
    "agent-escaped-toml": (agent_variant(replace={CODEX_REL: lambda t: t + 'model = "\\uAC00"\n'}), "english-wrappers", "fail"),
    "agent-wrapper-binary": (agent_variant(files={"wrappers/hermes/logo.png": b"\x89PNG\r\n\x1a\n\x00\xff"}),
                             "english-wrappers", "pass"),
    "skill-name-64": (skill_variant(name="a" * 64), "name-format", "pass"),
    "skill-name-65": (skill_variant(name="a" * 65), "name-format", "fail"),
    "skill-name-64-raw-65-nfkc": (skill_variant(name="a" * 63 + "\ufb00"), "name-format", "fail"),
    "skill-hermes-list": (skill_variant(md_edit=sub("version: 1.0.0", "version: 1.0.0\n  hermes: [a, b]")),
                          "parity-skill-hermes", "fail"),
    "agent-hermes-undecodable": (agent_variant(files={HERMES_REL: b"# code-scout \xff\xfe\x80\n"}), "parity-wrapper-hermes",
                                 "fail"),
    "agent-claude-escaped-frontmatter": (agent_variant(replace={CLAUDE_REL: sub("---\nYou", 'model: "\\uAC00"\n---\nYou')}),
                                         "english-wrappers", "fail"),
    "agent-claude-extra-key": (agent_variant(replace={CLAUDE_REL: sub("---\nYou", "color: blue\n---\nYou")}),
                               "parity-wrapper-claude", "pass"),
    "agent-opencode-extra-key": (agent_variant(replace={OPENCODE_REL: sub("---\nYou", "color: blue\n---\nYou")}),
                                 "parity-wrapper-opencode", "pass"),
    "agent-opencode-escaped-frontmatter": (agent_variant(replace={OPENCODE_REL: sub("---\nYou",
                                                                                    'model: "\\uAC00"\n---\nYou')}),
                                           "english-wrappers", "fail"),
}
# The OpenCode PASS branch needs a complete key list; the variant patches the legacy constant and the target together.
COMPLETE_OPENCODE = "agent-complete"


@functools.lru_cache(maxsize=None)
def a3_variant_unit(name):
    return A3_VARIANTS[name][0](os.path.join(tmpdir(), "a3", name))


def a3_functions(unit):
    """The A3 public functions on one unit, compared legacy against new on every corpus unit and variant."""
    kind = eva.detect_kind(unit)
    out = {"parity_status": eva.parity_status(unit), "review_items": eva.review_items(unit),
           "english_violations": eva.english_violations(unit), "parity_violations": eva.parity_violations(unit)}
    out["parity_line"] = eva.parity_line(out["parity_status"])
    s = eva.english_scan(unit)
    out["english_scan"] = (s.kind, s.groups, s.review)
    if kind == "skill":
        out["codex_policy"] = eva.codex_policy(unit)
        out["english_eval_problems"] = eva.english_eval_problems(unit)
    if kind == "agent":
        out["agent_wrapper_states"] = eva.agent_wrapper_states(unit)
    return out


def a3_row_view(rep):
    """row_view plus the reason of every naming, English and parity row (D11)."""
    return dict(row_view(rep), reasons={c["id"]: c["reason"] for c in rep.checks if c["id"] in A3_RULE_FILES})


def corpus_units():
    out = []
    for top in CORPUS_ROOTS:
        out += eva.find_units(os.path.join(ROOT, top))
    return sorted(set(out))


def scenario_files():
    out = []
    for u in corpus_units():
        d = os.path.join(u, "scenarios")
        if os.path.isdir(d):
            out += [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".json")]
    return out


def transcript_files():
    d = os.path.join(ROOT, "transcripts")
    return [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".json")]


def accept_diff(diffs, key, old_errs, new_errs):
    if bool(old_errs) != bool(new_errs):
        diffs["accept:" + key] = {"legacy": old_errs, "new": new_errs}


# ---------- comparison areas: each returns {difference key: detail} ----------
@functools.lru_cache(maxsize=None)
def area_rows():
    diffs, compared = {}, []
    units = [(rel(u), u) for u in corpus_units()]
    units += [("variant:evals:" + n, evals_variant_unit(n)) for n in EVALS_VARIANTS]
    units += [("variant:frontmatter:" + n, frontmatter_variant_unit(n)) for n in FRONTMATTER_UNITS]
    units += [("variant:a3:" + n, a3_variant_unit(n)) for n in A3_VARIANTS]
    for key, u in units:
        with legacy():
            old, old_fn = a3_row_view(eva.check(u, EMPTY_BUNDLE)), a3_functions(u)
        new = eva.check(u)
        new_rows, new_fn = a3_row_view(new), a3_functions(u)
        compared.append(key)
        if old != new_rows:
            diffs["row-map:" + key] = {"legacy": old, "new": new_rows}
        if old_fn != new_fn:
            diffs["functions:" + key] = {"legacy": old_fn, "new": new_fn}
        split = [c["id"] for c in new.checks if DISAGREE in c["reason"]]
        if split:
            diffs["status-reason:" + key] = split
    return diffs, tuple(compared)


@functools.lru_cache(maxsize=None)
def area_transcript_rows():
    diffs = {}
    pairs = [(rel(t), os.path.join(ROOT, TRANSCRIPT_AGENTS[os.path.basename(t)])) for t in transcript_files()]
    for name in TRANSCRIPT_VARIANTS:
        pairs.append(("variant:transcript:" + name, os.path.join(ROOT, "golden", "agents", "code-reviewer")))
    for key, agent in pairs:
        path = os.path.join(ROOT, key) if not key.startswith("variant:") else \
            transcript_variant_file(key.split(":", 2)[2])
        with legacy():
            old = row_view(eva.grade_transcript(agent, path))
        new = row_view(eva.grade_transcript(agent, path))
        if old != new:
            diffs["transcript-rows:" + key] = {"legacy": old, "new": new}
    return diffs


def function_cases():
    """(function, case name, legacy callable, new callable, rejected by legacy or None for corpus inputs)."""
    out = []
    for u in corpus_units():
        if eva.detect_kind(u) != "skill":
            continue
        raw, _ = eva.read_text(os.path.join(u, "SKILL.md"))
        meta = (eva.parse_frontmatter_ex(raw)[0] if raw is not None else None) or {}
        typed = set(eva.yaml_typed_keys(raw)) if meta else set()
        name = eva.unicodedata.normalize("NFKC", eva._str_field(meta, "name"))
        out.append(("evals_errors", rel(u), functools.partial(eva._legacy_evals_errors, u, name),
                    functools.partial(eva.evals_errors, u, name), None))
        out.append(("_field_type_errors", rel(u), functools.partial(eva._legacy_field_type_errors, meta, typed),
                    functools.partial(eva._field_type_errors, meta, typed), None))
        out.append(("load_triggers", rel(u), functools.partial(skill_eval._legacy_load_triggers, u),
                    functools.partial(skill_eval.load_triggers, u), None))
    for f in scenario_files():
        data = load_json(f)
        out.append(("scenario_errors", rel(f), functools.partial(eva._legacy_scenario_errors, data),
                    functools.partial(eva.scenario_errors, data), None))
    for t in transcript_files():
        out.append(("load_transcript", rel(t), functools.partial(eva._legacy_load_transcript, t),
                    functools.partial(eva.load_transcript, t), None))
    for n, (_, rejected) in EVALS_VARIANTS.items():
        u = evals_variant_unit(n)
        out.append(("evals_errors", n, functools.partial(eva._legacy_evals_errors, u, "diff-base"),
                    functools.partial(eva.evals_errors, u, "diff-base"), rejected))
    for n, (meta, typed, rejected) in FIELD_VARIANTS.items():
        out.append(("_field_type_errors", n, functools.partial(eva._legacy_field_type_errors, meta, typed),
                    functools.partial(eva._field_type_errors, meta, typed), rejected))
    for n, (data, rejected) in SCENARIO_VARIANTS.items():
        out.append(("scenario_errors", n, functools.partial(eva._legacy_scenario_errors, data),
                    functools.partial(eva.scenario_errors, data), rejected))
    for n, (_, rejected) in TRANSCRIPT_VARIANTS.items():
        t = transcript_variant_file(n)
        out.append(("load_transcript", n, functools.partial(eva._legacy_load_transcript, t),
                    functools.partial(eva.load_transcript, t), rejected))
    for n, (_, rejected) in TRIGGER_VARIANTS.items():
        u = trigger_variant_unit(n)
        out.append(("load_triggers", n, functools.partial(skill_eval._legacy_load_triggers, u),
                    functools.partial(skill_eval.load_triggers, u), rejected))
    for n, (c, rejected) in CHECK_VARIANTS.items():
        out.append(("check_object", n, functools.partial(skill_eval._check_problems, "w", c),
                    functools.partial(skill_eval.check_object_problems, "w", c), rejected))
    return out


def split_result(fn, result):
    """(errors, data) of one call: evals_errors gives (n, errors), the loaders (data, errors), the rest errors."""
    if fn in ("evals_errors", "load_transcript", "load_triggers"):
        return result[1], result[0]
    return result, None


@functools.lru_cache(maxsize=None)
def area_functions():
    diffs, messages, branches = {}, {}, {}
    for fn, case, old_call, new_call, rejected in function_cases():
        key = "%s:%s" % (fn, case)
        old_errs, old_data = split_result(fn, old_call())
        new_errs, new_data = split_result(fn, new_call())
        accept_diff(diffs, key, old_errs, new_errs)
        if not same_typed(old_data, new_data):
            diffs["data:" + key] = {"legacy": old_data, "new": new_data}
        if [strip(m) for m in new_errs] != old_errs:
            messages[key] = {"legacy": old_errs, "new": new_errs}
        branches[key] = (rejected, bool(old_errs), new_errs)
    return diffs, messages, branches


@functools.lru_cache(maxsize=None)
def area_eval_sets():
    diffs = {}
    units = [u for u in corpus_units() if eva.detect_kind(u) == "skill"]
    units += [evals_variant_unit(n) for n in EVALS_VARIANTS] + [trigger_variant_unit(n) for n in TRIGGER_VARIANTS]
    for c in ("csv-rows-min-greater-than-max", "regex-pattern-invalid", "csv-rows-unknown-key-surrogate", "not-object"):
        ev = copy.deepcopy(BASE_EVALS)
        ev["evals"][0]["assertions"][0]["check"] = CHECK_VARIANTS[c][0]
        units.append(make_unit(os.path.join(tmpdir(), "sets", c), evals=ev))
    for u in units:
        key = rel(u) if u.startswith(ROOT + os.sep) else "variant:" + os.path.relpath(u, tmpdir())
        with legacy():
            old_spec, old_p = skill_eval._legacy_load_eval_set(u)
        new_spec, new_p = skill_eval.load_eval_set(u)
        accept_diff(diffs, "validate:" + key, old_p, new_p)
        if not same_typed(old_spec, new_spec):
            diffs["spec:" + key] = {"legacy": old_spec, "new": new_spec}
    return diffs


def all_differences():
    out = {}
    out.update(area_rows()[0])
    out.update(area_transcript_rows())
    out.update(area_functions()[0])
    out.update(area_eval_sets())
    return out


def intentional():
    return load_json(INTENTIONAL_PATH)


def unrecorded(diffs):
    allowed = set(intentional())
    return {k: v for k, v in diffs.items() if k not in allowed}


# ---------- tests ----------
class TestIntentionalFile(unittest.TestCase):
    def test_it_is_a_json_list_of_difference_keys(self):
        data = intentional()
        self.assertIsInstance(data, list)
        self.assertTrue(all(isinstance(x, str) for x in data), data)
        self.assertEqual(len(set(data)), len(data))

    def test_every_listed_difference_occurred(self):
        self.assertEqual(sorted(set(intentional()) - set(all_differences())), [])

    def test_no_difference_outside_the_file(self):
        self.assertEqual(unrecorded(all_differences()), {})


class TestCorpusRows(unittest.TestCase):
    def test_the_corpus_holds_every_golden_and_bad_unit_and_every_fixture_unit(self):
        _, compared = area_rows()
        self.assertLessEqual(GOLDEN_AND_BAD, set(compared))
        for top in ("tests/fixtures/skills/csv-cleaner-en", "tests/rules/_selftest/selftest-name-matches-folder/pass/good-name",
                    "tests/rules/_selftest/selftest-name-matches-folder/fail/bad-name",
                    "tests/rules/evals-valid/pass/good-evals", "tests/rules/evals-valid/fail/missing-id",
                    "tests/rules/field-types/pass/quoted-name", "tests/rules/field-types/fail/unquoted-yes"):
            self.assertIn(top, compared)
        self.assertEqual(len([c for c in compared if c.startswith("variant:evals:")]), len(EVALS_VARIANTS))

    def test_legacy_and_cli_rows_verdicts_and_scores_are_equal(self):
        diffs = {k: v for k, v in area_rows()[0].items() if k.startswith("row-map:")}
        self.assertEqual(unrecorded(diffs), {})

    def test_status_and_reason_never_disagree(self):
        diffs = {k: v for k, v in area_rows()[0].items() if k.startswith("status-reason:")}
        self.assertEqual(unrecorded(diffs), {})

    def test_transcript_rows_are_equal(self):
        self.assertEqual(unrecorded(area_transcript_rows()), {})

    def test_each_shape_row_appears_once_and_comes_from_the_rule(self):
        u = os.path.join(ROOT, "golden", "skills", "csv-cleaner")
        r = eva.check(u)
        ids = [c["id"] for c in r.checks]
        for rid in RULE_FILES:
            self.assertEqual(ids.count(rid), 1, ids)
        bundle_ids = [rule.id for rule in RR.default_bundle().rules
                      if rule.file not in ROW_FAMILY_FILES and "skill" in rule.applies_to]
        self.assertEqual(ids[-len(bundle_ids):], bundle_ids)
        family = [rule.id for rule in RR.default_bundle().rules
                  if rule.file in ROW_FAMILY_FILES and "skill" in rule.applies_to]
        self.assertEqual([i for i in ids if i in family], family)
        self.assertLess(ids.index(family[0]), ids.index(bundle_ids[0]))
        direct = [c["id"] for c in eva.check_skill(u).checks]
        self.assertIn("field-types", direct)
        self.assertIn("evals-valid", direct)

    def test_a_non_default_bundle_without_the_rules_keeps_the_built_in_rows(self):
        selftest = RR.load_bundle(os.path.join(ROOT, "tests", "rules", "_selftest"))
        ids = [c["id"] for c in eva.check(os.path.join(ROOT, "golden", "skills", "csv-cleaner"), selftest).checks]
        for rid in RULE_FILES:
            self.assertEqual(ids.count(rid), 1, ids)

    def test_the_evals_valid_reason_keeps_the_old_text_and_names_the_rule(self):
        u = evals_variant_unit("id-missing")
        row = next(c for c in eva.check(u).checks if c["id"] == "evals-valid")
        self.assertEqual(row["status"], "fail")
        self.assertIn("evals[0].id: integer required [rule evals-valid, evals.schema.json#", row["reason"])
        self.assertTrue(row["fix"].isascii() and row["fix"])

    def test_a_status_reason_split_fails_closed(self):
        top = os.path.join(tmpdir(), "split")
        md = SKILL_MD.replace("name: diff-base\n", "")
        u = make_unit(top, name="split-unit", skill_md=md, evals=dict(BASE_EVALS, skill_name=""))
        self.assertEqual(eva.evals_errors(u, "")[1], [])
        row = next(c for c in eva.check(u).checks if c["id"] == "evals-valid")
        self.assertEqual((row["status"], row["must"]), ("fail", True))
        self.assertIn(DISAGREE, row["reason"])


class TestA3Variants(unittest.TestCase):
    def test_every_a3_variant_reaches_its_legacy_branch(self):
        wrong = {}
        for name, (_, rid, status) in A3_VARIANTS.items():
            u = a3_variant_unit(name)
            with legacy():
                rows = [c for c in eva.check(u, EMPTY_BUNDLE).checks if c["id"] == rid]
            if [c["status"] for c in rows] != [status]:
                wrong[name] = (rid, status, [(c["status"], c["reason"]) for c in rows])
        self.assertEqual(wrong, {})

    def test_the_variants_and_fixtures_are_compared(self):
        _, compared = area_rows()
        self.assertEqual(len([c for c in compared if c.startswith("variant:a3:")]), len(A3_VARIANTS))
        for rid in A3_RULE_FILES:
            if rid == "english-ruleset":
                continue
            self.assertTrue(any(c.startswith("tests/rules/%s/" % rid) for c in compared), rid)
        self.assertGreaterEqual(len(A3_VARIANTS), 90)

    def test_the_functions_are_compared_for_every_kind(self):
        u = a3_variant_unit("agent-complete")
        self.assertIn("agent_wrapper_states", a3_functions(u))
        u = a3_variant_unit("skill-codex-unverified")
        self.assertEqual(set(a3_functions(u)), {"parity_status", "review_items", "english_violations", "english_scan",
                                                "codex_policy", "english_eval_problems", "parity_violations",
                                                "parity_line"})

    def test_the_only_recorded_difference_is_the_naming_dedupe(self):
        unit = "tests/rules/_selftest/selftest-name-matches-folder/fail/bad-name"
        self.assertEqual(intentional(), ["row-map:" + unit])
        diffs = all_differences()
        d = diffs[intentional()[0]]
        old = d["legacy"]["rows"]["parity-skill-opencode"]
        new = d["new"]["rows"]["parity-skill-opencode"]
        self.assertEqual((old[0], new[0]), ("fail", "pass"))
        for part in ("rows", "reasons"):
            changed = {k for k in set(d["legacy"][part]) | set(d["new"][part])
                       if d["legacy"][part].get(k) != d["new"][part].get(k)}
            self.assertEqual(changed, {"parity-skill-opencode"}, part)
        self.assertIn("must equal the folder name", d["legacy"]["reasons"]["parity-skill-opencode"])
        self.assertNotIn("functions:" + unit, diffs)

    def test_the_folder_cause_reaches_the_gate_functions_as_in_the_legacy_body(self):
        u = os.path.join(ROOT, "tests", "rules", "_selftest", "selftest-name-matches-folder", "fail", "bad-name")
        with legacy():
            old = a3_functions(u)
        new = a3_functions(u)
        self.assertEqual(new, old)
        self.assertEqual(new["parity_status"]["opencode"]["status"], "fail")
        self.assertTrue(new["parity_violations"] and all(v.startswith("[") for v in new["parity_violations"]))
        self.assertTrue(any(v.startswith("[opencode]") and "must equal the folder name" in v
                            for v in new["parity_violations"]), new["parity_violations"])

    def test_the_opencode_complete_branch_matches_with_the_constant_and_target_patched(self):
        from engine.primitives import parity as PR
        u = a3_variant_unit(COMPLETE_OPENCODE)
        with mock.patch.object(eva, "OPENCODE_PERMISSION_KEYS_COMPLETE", True), \
                mock.patch.dict(PR.target("opencode")["permissions"], {"complete": True}):
            with legacy():
                old_rep = eva.check(u, EMPTY_BUNDLE)
                old, old_fn = a3_row_view(old_rep), a3_functions(u)
            new, new_fn = a3_row_view(eva.check(u)), a3_functions(u)
        self.assertEqual(old["rows"]["parity-permissions-opencode"][0], "pass")
        self.assertIn("match the AGENT.md tools (compared by capability class",
                      old["reasons"]["parity-permissions-opencode"])
        self.assertEqual(new, old)
        self.assertEqual(new_fn, old_fn)


class TestFunctions(unittest.TestCase):
    def test_accept_sets_and_data_are_equal(self):
        self.assertEqual(unrecorded(area_functions()[0]), {})

    def test_every_variant_hits_its_branch(self):
        wrong = {k: (rejected, old) for k, (rejected, old, _) in area_functions()[2].items()
                 if rejected is not None and rejected != old}
        self.assertEqual(wrong, {})

    def test_messages_are_the_old_strings_plus_the_schema_suffix(self):
        _, messages, branches = area_functions()
        self.assertEqual({k: v for k, v in messages.items() if k not in WORDING_ONLY}, {})
        for k in WORDING_ONLY:
            self.assertIn(k, branches)
        for k, (_, _, new_errs) in branches.items():
            for m in new_errs:
                self.assertIsInstance(m, str)
                m.encode("utf-8")

    def test_every_message_rule_fires_in_some_variant(self):
        fired = set()
        for _, (_, _, new_errs) in area_functions()[2].items():
            for m in new_errs:
                hit = SUFFIX_RE.search(m)
                if hit:
                    fired.add((hit.group(1), hit.group(2)))
        self.assertEqual(sorted(set(message_rules()) - fired), [])

    def test_schema_problems_name_the_schema_and_the_keyword(self):
        cases = {"csv-rows-min-greater-than-max": "x-le", "regex-pattern-invalid": "x-python-regex",
                 "tool-call-input-regex-invalid": "x-python-regex"}
        for name, kw in cases.items():
            errs = skill_eval.check_object_problems("w", CHECK_VARIANTS[name][0])
            self.assertTrue(any(m.endswith("keyword %s]" % kw) and "evals.schema.json#" in m for m in errs), errs)
        _, errs = skill_eval.load_triggers(trigger_variant_unit("count-9-11"))
        self.assertTrue(any("got 9 + 11" in m and m.endswith("keyword x-count-where]") for m in errs), errs)
        _, errs = eva.evals_errors(evals_variant_unit("id-missing"), "diff-base")
        self.assertEqual(errs, ["evals[0].id: integer required [rule evals-valid, evals.schema.json#/$defs/case-id, "
                                "keyword required]"])


class TestEvalSets(unittest.TestCase):
    def test_validate_problems_and_specs_are_equal(self):
        self.assertEqual(unrecorded(area_eval_sets()), {})


class TestLegacyOracle(unittest.TestCase):
    def source_of(self, fname, name):
        with open(os.path.join(ROOT, fname), encoding="utf-8") as f:
            src = f.read()
        defs = {n.name: n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
        return ast.get_source_segment(src, defs[name])

    def test_legacy_bodies_are_byte_identical_to_the_a1_bodies(self):
        for (fname, name), pin in LEGACY_PINS.items():
            legacy_name = "_legacy_" + name.lstrip("_")
            seg = self.source_of(fname, legacy_name).replace("def %s(" % legacy_name, "def %s(" % name, 1)
            self.assertEqual(hashlib.sha256(seg.encode("utf-8")).hexdigest(), pin, name)
        for (fname, name), pin in UNCHANGED_PINS.items():
            self.assertEqual(hashlib.sha256(self.source_of(fname, name).encode("utf-8")).hexdigest(), pin, name)

    def test_no_production_source_names_a_legacy_function(self):
        own = os.path.join(ROOT, "tests", "test_differential.py")
        names = r"_legacy_(?:%s)\b" % "|".join(n.lstrip("_") for _, n in LEGACY_PINS)
        for dp, dn, fn in os.walk(ROOT):
            dn[:] = [d for d in dn if not d.startswith(".") and d != "__pycache__"]
            for n in fn:
                p = os.path.join(dp, n)
                if not n.endswith(".py") or p == own:
                    continue
                with open(p, encoding="utf-8") as f:
                    src = f.read()
                hits = re.findall(names, src)
                defs = re.findall(r"^def (%s)\(" % names, src, re.M)
                self.assertEqual(sorted(hits), sorted(defs), rel(p))
                if rel(p) not in ("eva.py", "skill_eval.py"):
                    self.assertEqual(hits, [], rel(p))
        self.assertEqual(sorted(n for n in dir(eva) if n.startswith("_legacy_")),
                         ["_legacy_add_english_checks", "_legacy_add_parity_checks_agent", "_legacy_add_parity_checks_skill",
                          "_legacy_agent_wrapper_states", "_legacy_codex_policy", "_legacy_english_eval_problems",
                          "_legacy_english_scan", "_legacy_evals_errors", "_legacy_field_type_errors",
                          "_legacy_load_transcript", "_legacy_scenario_errors"])
        self.assertEqual(sorted(n for n in dir(skill_eval) if n.startswith("_legacy_")),
                         ["_legacy_load_eval_set", "_legacy_load_triggers"])

    def test_no_cli_path_reaches_a_legacy_function_or_the_old_check_object_code(self):
        calls = []

        def trip(name):
            def f(*a, **k):
                calls.append(name)
                raise AssertionError("legacy reached: " + name)
            return f
        targets = [(eva, n) for n in dir(eva) if n.startswith("_legacy_")] + \
                  [(skill_eval, n) for n in dir(skill_eval) if n.startswith("_legacy_")] + \
                  [(skill_eval, "_check_problems"), (skill_eval, "_csv_rows_problems"), (eva, "_assertion_list")]
        with contextlib.ExitStack() as stack:
            for mod, n in targets:
                stack.enter_context(mock.patch.object(mod, n, trip(n)))
            for top in ("golden", "bad", os.path.join("tests", "fixtures"), os.path.join("tests", "rules")):
                quiet(eva.main, ["check-all", os.path.join(ROOT, top)])
                quiet(eva.main, ["check-all", os.path.join(ROOT, top), "--json"])
            for t in transcript_files():
                quiet(eva.main, ["check", os.path.join(ROOT, TRANSCRIPT_AGENTS[os.path.basename(t)]), "--transcript", t])
            for skill in (os.path.join(ROOT, "golden", "skills", "csv-cleaner"),
                          os.path.join(ROOT, "tests", "fixtures", "skills", "csv-cleaner-en"),
                          evals_variant_unit("id-missing")):
                for cmd in ("validate", "estimate"):
                    quiet(skill_eval.main, [cmd, skill])
            ev = copy.deepcopy(BASE_EVALS)
            ev["evals"][0]["assertions"][0]["check"] = CHECK_VARIANTS["csv-rows-many"][0]
            quiet(skill_eval.main, ["validate", make_unit(os.path.join(tmpdir(), "trip"), evals=ev)])
        self.assertEqual(calls, [])


def schema_doc(name):
    return load_json(os.path.join(ROOT, "schemas", name))


def resolve(doc, ref):
    node = doc
    for tok in S.parse_pointer(ref[1:]):
        node = node[tok]
    return node


ROUTING = ("$ref", "allOf", "if", "properties", "items")


def walk_rules(doc, node, ptr, out, seen):
    """Pointers of every message rule reachable from node; asserts the structure-node shape."""
    if ptr in seen:
        return
    seen.add(ptr)
    if isinstance(node, dict) and "description" in node:
        out.append(ptr)
        return
    if node is True or node == {}:
        return
    keys = set(node) - {"$comment"}
    routing = [k for k in ROUTING if k in keys]
    assert len(routing) == 1 and keys - {"then", "else"} == {routing[0]}, (ptr, sorted(keys))
    kw = routing[0]
    if kw == "$ref":
        walk_rules(doc, resolve(doc, node["$ref"]), node["$ref"][1:], out, seen)
    elif kw == "allOf":
        for i, sub in enumerate(node["allOf"]):
            walk_rules(doc, sub, "%s/allOf/%d" % (ptr, i), out, seen)
    elif kw == "if":
        for b in ("then", "else"):
            if b in node:
                walk_rules(doc, node[b], "%s/%s" % (ptr, b), out, seen)
    elif kw == "properties":
        for k, sub in node["properties"].items():
            walk_rules(doc, sub, "%s/properties/%s" % (ptr, k), out, seen)
    else:
        walk_rules(doc, node["items"], ptr + "/items", out, seen)


def message_rules():
    out = []
    for name in SCHEMAS:
        doc = schema_doc(name)
        found = []
        walk_rules(doc, resolve(doc, doc["$ref"]), doc["$ref"][1:], found, set())
        if name == "evals.schema.json":
            walk_rules(doc, doc["$defs"]["check"], "/$defs/check", found, set())
        out += [(name, p) for p in found]
    return out


def strings_of(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from strings_of(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings_of(x)


class TestSchemas(unittest.TestCase):
    def test_each_schema_compiles_with_the_a1_validator(self):
        for name in SCHEMAS:
            with self.subTest(schema=name):
                S.compile_schema(S.load(os.path.join(ROOT, "schemas", name)))

    def test_each_schema_root_carries_source_severity_and_fix(self):
        for name in SCHEMAS:
            doc = schema_doc(name)
            with self.subTest(schema=name):
                self.assertEqual(set(doc), {"$schema", "title", "description", "$comment", "$ref", "$defs"})
                self.assertIn("source:", doc["$comment"])
                self.assertIn("severity:", doc["$comment"])
                self.assertIn("protects:", doc["$comment"])
                self.assertIn("Fix:", doc["description"])

    def test_structure_nodes_only_route_and_every_rule_has_a_message(self):
        rules = message_rules()
        self.assertGreater(len(rules), 60)
        for name, ptr in rules:
            node = resolve(schema_doc(name), "#" + ptr)
            self.assertTrue(node["description"].strip(), (name, ptr))

    def test_every_written_text_is_ascii_english(self):
        files = [os.path.join("schemas", n) for n in SCHEMAS] + [os.path.join("rules", n) for n in RULE_FILES.values()] + \
                [os.path.join("policy", n) for n in POLICY_FILES]
        for f in files:
            with open(os.path.join(ROOT, f), "rb") as fh:
                raw = fh.read()
            with self.subTest(file=f):
                self.assertTrue(raw.isascii(), f)
                self.assertTrue(all(s.isascii() for s in strings_of(json.loads(raw.decode("ascii")))), f)

    def test_shape_data_matches_skill_eval_and_eva(self):
        doc = schema_doc("evals.schema.json")
        self.assertEqual(tuple(doc["$defs"]["check-type"]["properties"]["type"]["enum"]), skill_eval.STATIC_TYPES)
        self.assertIn("|".join(skill_eval.STATIC_TYPES), doc["$defs"]["check-type"]["description"])
        self.assertEqual(tuple(doc["$defs"]["csv-rows-keys"]["properties"]), skill_eval.CSV_ROWS_KEYS)
        self.assertIn(", ".join(skill_eval.CSV_ROWS_KEYS), doc["$defs"]["csv-rows-keys"]["description"])
        self.assertEqual(tuple(doc["$defs"]["case-layer"]["properties"]["layer"]["enum"]), eva.EVAL_LAYERS)
        self.assertEqual(doc["$defs"]["case-name"]["properties"]["name"]["pattern"], eva.EVAL_NAME_RE.pattern)
        self.assertIn(eva.EVAL_NAME_RE.pattern, doc["$defs"]["case-name"]["description"])
        self.assertEqual(eva.PYYAML_STR_FIELDS, ("name", "description", "compatibility"))

    def test_the_trigger_count_bounds_are_the_policy_values(self):
        doc = schema_doc("trigger-evals.schema.json")
        rule = doc["$defs"]["trigger-counts"]
        bounds = [(x["x-count-where"]["where"]["properties"]["should_trigger"]["const"], x["x-count-where"]["min"],
                   x["x-count-where"]["max"]) for x in rule["allOf"]]
        self.assertEqual(bounds, [(True, skill_eval.TRIGGER_POS, skill_eval.TRIGGER_POS),
                                  (False, skill_eval.TRIGGER_NEG, skill_eval.TRIGGER_NEG)])
        self.assertIn("need %d should-trigger + %d should-not" % (skill_eval.TRIGGER_POS, skill_eval.TRIGGER_NEG),
                      rule["description"])

    def schema_check(self, schema, instance, unit=None):
        argv = ["schema-check", os.path.join(ROOT, "schemas", schema), instance] + (["--unit", unit] if unit else [])
        return quiet(eva.main, argv)

    def test_schema_check_accepts_a_corpus_file_and_rejects_a_broken_one(self):
        csv = os.path.join(ROOT, "golden", "skills", "csv-cleaner")
        fm = os.path.join(tmpdir(), "fm-ok.json")
        wjson(fm, {"license": "MIT", "metadata": {"version": "1.0.0"}})
        fm_bad = os.path.join(tmpdir(), "fm-bad.json")
        wjson(fm_bad, {"license": "", "metadata": {"version": 1}})
        scen_bad, tr_bad, trig_bad, ev_bad = (os.path.join(tmpdir(), n) for n in ("s.json", "t.json", "g.json", "e.json"))
        wjson(scen_bad, edit(BASE_SCENARIO, turns=KeyError))
        wjson(tr_bad, edit(BASE_TRANSCRIPT, tool_calls=[5]))
        wjson(trig_bad, triggers(9, 11))
        wjson(ev_bad, edit_case(0, id=KeyError)(copy.deepcopy(load_json(os.path.join(csv, "evals", "evals.json")))))
        cases = [("evals.schema.json", os.path.join(csv, "evals", "evals.json"), ev_bad, csv),
                 ("trigger-evals.schema.json", os.path.join(csv, "evals", "trigger_evals.json"), trig_bad, None),
                 ("scenario.schema.json", os.path.join(ROOT, "golden", "agents", "code-reviewer", "scenarios",
                                                       "01-overview.json"), scen_bad, None),
                 ("transcript.schema.json", os.path.join(ROOT, "transcripts", "code-reviewer-drill.json"), tr_bad, None),
                 ("skill-frontmatter.schema.json", fm, fm_bad, None)]
        for schema, good, bad, unit in cases:
            with self.subTest(schema=schema):
                rc, out, err = self.schema_check(schema, good, unit)
                self.assertEqual((rc, out.strip(), err), (0, "schema-check: valid", ""))
                rc, out, err = self.schema_check(schema, bad, unit)
                self.assertEqual(rc, 1, (out, err))
                self.assertTrue(out.startswith("error #"), out)


class TestRuleEntries(unittest.TestCase):
    def test_both_rules_load_in_the_default_bundle_with_every_field(self):
        rules = {r.id: r for r in RR.default_bundle().rules}
        self.assertEqual(set(RULE_FILES) - set(rules), set())
        for rid, fname in RULE_FILES.items():
            doc = load_json(os.path.join(ROOT, "rules", fname))
            raw = next(r for r in doc["rules"] if r["id"] == rid)
            with self.subTest(rule=rid):
                self.assertEqual(raw["applies_to"], ["skill"])
                self.assertEqual(raw["severity"], "fail")
                self.assertEqual(raw["protects"], ["claude", "codex", "opencode", "hermes"])
                self.assertTrue(raw["source"])
                for k in ("message", "fix"):
                    self.assertTrue(raw[k].isascii() and len(raw[k]) > 20, raw[k])
                self.assertEqual(rules[rid].category, {"evals-valid": "evaluation", "field-types": "structure"}[rid])

    def test_the_rows_keep_their_old_category_and_must(self):
        old = {c["id"]: (c["category"], c["must"]) for c in legacy_check(os.path.join(ROOT, "golden", "skills",
                                                                                      "csv-cleaner")).checks}
        new = {c["id"]: (c["category"], c["must"]) for c in eva.check(os.path.join(ROOT, "golden", "skills",
                                                                                   "csv-cleaner")).checks}
        for rid in RULE_FILES:
            self.assertEqual(old[rid], new[rid])

    def test_the_schemas_compile_once_per_process(self):
        a = eva.shape_schema("evals.schema.json")
        self.assertIs(a, eva.shape_schema("evals.schema.json"))

    def test_check_skill_and_the_rule_give_the_same_english_fix(self):
        ruled = {r.id: r.fix for r in RR.default_bundle().rules}
        built = {c["id"]: c["fix"] for c in eva.check_skill(evals_variant_unit("id-missing")).checks}
        self.assertEqual(built["evals-valid"], ruled["evals-valid"])
        built = {c["id"]: c["fix"] for c in eva.check_skill(frontmatter_variant_unit("fm-compatibility-typed")).checks}
        self.assertEqual(built["field-types"], ruled["field-types"])


class TestPolicy(unittest.TestCase):
    def test_every_constant_equals_its_a1_literal_in_type_and_value(self):
        for name, want in A1_CONSTANTS.items():
            with self.subTest(name=name):
                self.assertTrue(same_typed(getattr(skill_eval, name), want), (name, getattr(skill_eval, name)))

    def test_every_value_has_a_source_and_every_price_row_a_read_on_date(self):
        for fname in POLICY_FILES:
            doc = load_json(os.path.join(ROOT, "policy", fname))
            for name, entry in doc["values"].items():
                with self.subTest(file=fname, name=name):
                    self.assertTrue(entry["source"].strip())
                    self.assertIn(entry["type"], ("tuple", "dict", "int", "float", "str"))
        pricing = load_json(os.path.join(ROOT, "policy", "pricing.json"))
        rows = pricing["values"]["PRICES"]["value"]
        self.assertEqual(sorted(rows), sorted(A1_CONSTANTS["PRICES"]))
        for model, row in rows.items():
            self.assertEqual((row["read_on"], row["source"]),
                             ("2026-10-15", "https://platform.claude.com/docs/en/about-claude/pricing"))
        self.assertIs(pricing["codex"]["verified"], False)
        self.assertIn("unverified", pricing["codex"]["note"].lower())
        models = load_json(os.path.join(ROOT, "policy", "models.json"))
        self.assertIs(models["values"]["CODEX_EFFORT_CONFIG"]["verified"], False)

    def tree(self):
        t = os.path.join(tempfile.mkdtemp(dir=tmpdir()), "tree")
        for d in ("engine", "schemas", "rules", "policy", "targets"):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(t, d),
                            ignore=shutil.ignore_patterns("__pycache__"))
        for f in ("skill_eval.py", "eva.py"):
            shutil.copy(os.path.join(ROOT, f), os.path.join(t, f))
        return t

    def run_estimate(self, tree, cwd=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        skill = os.path.join(ROOT, "tests", "fixtures", "skills", "csv-cleaner-en")
        p = subprocess.run([sys.executable, os.path.join(tree, "skill_eval.py"), "estimate", skill],
                           cwd=cwd or tmpdir(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        return p.returncode, p.stdout, p.stderr

    def exec_row(self, out):
        line = next(l for l in out.splitlines() if l.split()[:2] == ["claude", "exec"])
        parts = line.split()
        return int(parts[4]), float(parts[6])

    def test_a_changed_price_changes_the_estimate_by_the_computed_amount(self):
        t = self.tree()
        rc, base_out, _ = self.run_estimate(t)
        self.assertEqual(rc, 0)
        fp = os.path.join(t, "policy", "pricing.json")
        doc = load_json(fp)
        doc["values"]["PRICES"]["value"]["claude-opus-5-5"]["input"] = 5.0
        wjson(fp, doc)
        rc, out, _ = self.run_estimate(t)
        self.assertEqual(rc, 0)
        tokens_in, usd = self.exec_row(base_out)
        _, usd2 = self.exec_row(out)
        self.assertAlmostEqual(usd2 - usd, round(tokens_in * 1.0 / 1e6, 2), delta=0.011)
        self.assertNotEqual(out, base_out)

    def test_policy_is_read_next_to_skill_eval_not_from_the_cwd(self):
        t = self.tree()
        cwd = os.path.join(tmpdir(), "cwd-policy")
        os.makedirs(os.path.join(cwd, "policy"), exist_ok=True)
        doc = load_json(os.path.join(ROOT, "policy", "pricing.json"))
        doc["values"]["PRICES"]["value"]["claude-opus-5-5"]["input"] = 99.0
        wjson(os.path.join(cwd, "policy", "pricing.json"), doc)
        self.assertEqual(self.run_estimate(t, cwd=cwd), self.run_estimate(t))

    def test_a_missing_or_malformed_policy_file_is_refused(self):
        texts = {"invalid-json": "{", "duplicate-key": '{"policy": "x", "policy": "y"}'}
        # label -> {file: (value name, field, new field value)}
        edits = {
            "name-missing": {"eval-policy.json": "TRIGGER_POS", "pricing.json": "WORST_CASE_FACTOR",
                             "models.json": "CODEX_EFFORTS"},
            "unknown-type": {"eval-policy.json": ("TRIGGER_POS", "type", "list"),
                             "pricing.json": ("WORST_CASE_FACTOR", "type", "list"),
                             "models.json": ("CODEX_EFFORTS", "type", "list")},
            "tuple-as-string": {"eval-policy.json": ("LAYERS", "value", "low"),
                                "pricing.json": ("DEFAULT_TOKENS", "value", "low"),
                                "models.json": ("CODEX_EFFORTS", "value", "low")},
            "float-as-int": {"eval-policy.json": ("AGREEMENT_MIN", "value", 1),
                             "pricing.json": ("CLAUDE_CAP_HEADROOM", "value", 1),
                             "models.json": ("DEFAULT_GRADER_MODEL", "value", 1)},
        }
        for label in ["missing"] + list(texts) + list(edits):
            for fname in POLICY_FILES:
                with self.subTest(change=label, file=fname):
                    t = self.tree()
                    fp = os.path.join(t, "policy", fname)
                    doc = load_json(fp)
                    if label == "missing":
                        os.remove(fp)
                    elif label in texts:
                        write(fp, texts[label])
                    elif label == "name-missing":
                        doc["values"].pop(edits[label][fname])
                        wjson(fp, doc)
                    else:
                        name, field, value = edits[label][fname]
                        doc["values"][name][field] = value
                        wjson(fp, doc)
                    rc, out, err = self.run_estimate(t)
                    self.assertNotEqual(rc, 0)
                    self.assertEqual(out, "")
                    self.assertIn("policy error: policy/%s" % fname, err)


INF = "@INF@"  # written as the JSON number 1e999, which loads as float inf


def set_value(name, value):
    def f(doc):
        doc["values"][name]["value"] = value
    return f


def set_item(name, key, value):
    def f(doc):
        if value is KeyError:
            doc["values"][name]["value"].pop(key)
        else:
            doc["values"][name]["value"][key] = value
    return f


def set_price(field, value, model="claude-opus-5-5"):
    def f(doc):
        row = doc["values"]["PRICES"]["value"][model]
        if value is KeyError:
            row.pop(field)
        else:
            row[field] = value
    return f


def set_entry(name, key, value):
    def f(doc):
        if value is KeyError:
            doc["values"][name].pop(key)
        else:
            doc["values"][name][key] = value
    return f


EP, PR, MO = "eval-policy.json", "pricing.json", "models.json"
ROW = "needs float input and output, an https source and a read_on date; "
# name -> (policy file to edit, the edit, texts the refusal must hold: the file and value name, then the value)
POLICY_REFUSALS = {
    "layers-number-items": (EP, set_value("LAYERS", [1, 2, 3]), ("policy/eval-policy.json: LAYERS[0]: ", "got 1")),
    "layers-empty": (EP, set_value("LAYERS", []), ("policy/eval-policy.json: LAYERS: ", "got []")),
    "layers-typed-as-string": (EP, lambda d: d["values"]["LAYERS"].update(type="str", value="static"),
                               ('policy/eval-policy.json: LAYERS: type must be "tuple"', 'got "str"')),
    "baselines-empty": (EP, set_value("BASELINES", {}), ("policy/eval-policy.json: BASELINES: ", "got {}")),
    "baselines-number-value": (EP, set_item("BASELINES", "none", 1), ("policy/eval-policy.json: BASELINES.none: ", "got 1")),
    "default-runs-zero": (EP, set_value("DEFAULT_RUNS", 0), ("policy/eval-policy.json: DEFAULT_RUNS: ", "got 0")),
    "default-runs-negative": (EP, set_value("DEFAULT_RUNS", -1), ("policy/eval-policy.json: DEFAULT_RUNS: ", "got -1")),
    "max-auto-iterations-zero": (EP, set_value("MAX_AUTO_ITERATIONS", 0),
                                 ("policy/eval-policy.json: MAX_AUTO_ITERATIONS: ", "got 0")),
    "trigger-pos-zero": (EP, set_value("TRIGGER_POS", 0), ("policy/eval-policy.json: TRIGGER_POS: ", "got 0")),
    "trigger-neg-zero": (EP, set_value("TRIGGER_NEG", 0), ("policy/eval-policy.json: TRIGGER_NEG: ", "got 0")),
    "trigger-threshold-above-one": (EP, set_value("TRIGGER_THRESHOLD", 1.5),
                                    ("policy/eval-policy.json: TRIGGER_THRESHOLD: ", "got 1.5")),
    "trigger-threshold-infinite": (EP, set_value("TRIGGER_THRESHOLD", INF),
                                   ("policy/eval-policy.json: TRIGGER_THRESHOLD: ", "got Infinity")),
    "agreement-min-negative": (EP, set_value("AGREEMENT_MIN", -1.0),
                               ("policy/eval-policy.json: AGREEMENT_MIN: ", "got -1.0")),
    "regression-tolerance-above-one": (EP, set_value("REGRESSION_TOLERANCE", 2.0),
                                       ("policy/eval-policy.json: REGRESSION_TOLERANCE: ", "got 2.0")),
    "high-variance-stddev-above-one": (EP, set_value("HIGH_VARIANCE_STDDEV", 1.5),
                                       ("policy/eval-policy.json: HIGH_VARIANCE_STDDEV: ", "got 1.5")),
    "price-input-infinite": (PR, set_price("input", INF),
                             ("policy/pricing.json: PRICES.claude-opus-5-5: " + ROW + "input must be ", "got Infinity")),
    "price-read-on-impossible-date": (PR, set_price("read_on", "2099-99-99"),
                                      ("policy/pricing.json: PRICES.claude-opus-5-5: " + ROW + "read_on must be ",
                                       'got "2099-99-99"')),
    "price-read-on-trailing-newline": (PR, set_price("read_on", "2026-10-15\n"),
                                       ("policy/pricing.json: PRICES.claude-opus-5-5: " + ROW + "read_on must be ",
                                        'got "2026-10-15\\n"')),
    "price-read-on-missing": (PR, set_price("read_on", KeyError),
                              ("policy/pricing.json: PRICES.claude-opus-5-5: " + ROW + "read_on must be ", "got null")),
    "price-source-without-host": (PR, set_price("source", "https://"),
                                  ("policy/pricing.json: PRICES.claude-opus-5-5: " + ROW + "source must be ",
                                   'got "https://"')),
    "prices-empty": (PR, set_value("PRICES", {}), ("policy/pricing.json: PRICES: ", "got {}")),
    "tokens-string-items": (PR, set_item("DEFAULT_TOKENS", "exec", ["x", "y"]),
                            ("policy/pricing.json: DEFAULT_TOKENS.exec: ", 'got ["x", "y"]')),
    "tokens-one-item": (PR, set_item("DEFAULT_TOKENS", "exec", [60000]),
                        ("policy/pricing.json: DEFAULT_TOKENS.exec: ", "got [60000]")),
    "tokens-negative": (PR, set_item("DEFAULT_TOKENS", "grader", [-1, 800]),
                        ("policy/pricing.json: DEFAULT_TOKENS.grader: ", "got [-1, 800]")),
    "tokens-empty": (PR, set_value("DEFAULT_TOKENS", {}), ("policy/pricing.json: DEFAULT_TOKENS: ", "got {}")),
    "cap-headroom-above-one": (PR, set_value("CLAUDE_CAP_HEADROOM", 50.0),
                               ("policy/pricing.json: CLAUDE_CAP_HEADROOM: ", "got 50.0")),
    "cap-headroom-negative": (PR, set_value("CLAUDE_CAP_HEADROOM", -0.5),
                              ("policy/pricing.json: CLAUDE_CAP_HEADROOM: ", "got -0.5")),
    "worst-case-factor-below-one": (PR, set_value("WORST_CASE_FACTOR", 0.5),
                                    ("policy/pricing.json: WORST_CASE_FACTOR: ", "got 0.5")),
    "worst-case-factor-negative": (PR, set_value("WORST_CASE_FACTOR", -3.0),
                                   ("policy/pricing.json: WORST_CASE_FACTOR: ", "got -3.0")),
    "worst-case-factor-infinite": (PR, set_value("WORST_CASE_FACTOR", INF),
                                   ("policy/pricing.json: WORST_CASE_FACTOR: ", "got Infinity")),
    "default-model-price-row-missing": (PR, set_item("PRICES", "claude-opus-5-5", KeyError),
                                        ("policy/models.json: DEFAULT_EXECUTOR_MODELS.claude: ",
                                         'got "claude-opus-5-5"')),
    "executor-models-empty": (MO, set_value("DEFAULT_EXECUTOR_MODELS", {}),
                              ("policy/models.json: DEFAULT_EXECUTOR_MODELS: ", "got {}")),
    "executor-model-number": (MO, set_item("DEFAULT_EXECUTOR_MODELS", "claude", 5),
                              ("policy/models.json: DEFAULT_EXECUTOR_MODELS.claude: ", "got 5")),
    "executor-model-unpriced": (MO, set_item("DEFAULT_EXECUTOR_MODELS", "claude", "claude-unknown"),
                                ("policy/models.json: DEFAULT_EXECUTOR_MODELS.claude: ", 'got "claude-unknown"')),
    "grader-model-unpriced": (MO, set_value("DEFAULT_GRADER_MODEL", ""),
                              ("policy/models.json: DEFAULT_GRADER_MODEL: ", 'got ""')),
    "builder-model-unpriced": (MO, set_value("DEFAULT_BUILDER_MODEL", "claude-unknown"),
                               ("policy/models.json: DEFAULT_BUILDER_MODEL: ", 'got "claude-unknown"')),
    "claude-efforts-empty": (MO, set_value("CLAUDE_EFFORTS", []), ("policy/models.json: CLAUDE_EFFORTS: ", "got []")),
    "claude-efforts-number-items": (MO, set_value("CLAUDE_EFFORTS", [1, 2]),
                                    ("policy/models.json: CLAUDE_EFFORTS[0]: ", "got 1")),
    "codex-efforts-empty": (MO, set_value("CODEX_EFFORTS", []), ("policy/models.json: CODEX_EFFORTS: ", "got []")),
    "grader-engines-empty": (MO, set_value("GRADER_ENGINES", []), ("policy/models.json: GRADER_ENGINES: ", "got []")),
    "codex-effort-config-verified-missing": (MO, set_entry("CODEX_EFFORT_CONFIG", "verified", KeyError),
                                             ("policy/models.json: CODEX_EFFORT_CONFIG: verified must be false",
                                              "got null")),
    "codex-effort-config-verified-true": (MO, set_entry("CODEX_EFFORT_CONFIG", "verified", True),
                                          ("policy/models.json: CODEX_EFFORT_CONFIG: verified must be false",
                                           "got true")),
}


def policy_refusal_test(fname, edit_fn, texts):
    def test(self):
        t = TestPolicy.tree(self)
        fp = os.path.join(t, "policy", fname)
        doc = load_json(fp)
        edit_fn(doc)
        write(fp, json.dumps(doc, indent=1).replace('"%s"' % INF, "1e999"))
        rc, out, err = TestPolicy.run_estimate(self, t)
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertIn("skill_eval: policy error: ", err)
        for text in texts:
            self.assertIn(text, err)
    return test


class TestPolicyRefusals(unittest.TestCase):
    """One test per policy value outside its spec (skill_eval.POLICY_SPECS): estimate refuses to start and the
    refusal names the file, the value name and the value."""


for _name, _case in POLICY_REFUSALS.items():
    setattr(TestPolicyRefusals, "test_refuses_" + _name.replace("-", "_"), policy_refusal_test(*_case))


@functools.lru_cache(maxsize=None)
def deep_units():
    """A folder with a unit whose evals id nests past engine.schema.MAX_DEPTH, one whose trigger query does, and a
    plain unit."""
    top = os.path.join(tmpdir(), "deep-units")

    def unit(name, evals=BASE_EVALS, triggers=BASE_TRIGGERS):
        md = SKILL_MD.replace("name: diff-base", "name: " + name)
        return make_unit(top, name, md, dict(evals, skill_name=name), triggers)
    return top, (unit("deep-id", edit_case(0, id=DEEP)(copy.deepcopy(BASE_EVALS))),
                 unit("deep-query", triggers=TRIGGER_VARIANTS["query-deep"][0]), unit("plain"))


class TestDeepNesting(unittest.TestCase):
    def test_check_all_prints_the_legacy_verdict_of_every_unit_beside_a_deeply_nested_one(self):
        top, units = deep_units()
        rc, out, err = quiet(eva.main, ["check-all", top])
        lines = out.splitlines()
        for u in units:
            want = legacy_check(u).verdict
            self.assertEqual([l.split()[0] for l in lines if l.endswith(os.sep + os.path.basename(u))],
                             ["[%s]" % want], out)
        self.assertIn("total=3 ", out)
        self.assertEqual(rc, 1)

    def test_validate_gives_the_legacy_problems_for_a_deep_id_and_a_deep_query(self):
        for u in deep_units()[1][:2]:
            with self.subTest(unit=os.path.basename(u)):
                with legacy():
                    old_spec, old = skill_eval._legacy_load_eval_set(u)
                new_spec, new = skill_eval.load_eval_set(u)
                self.assertTrue(old)
                self.assertEqual([strip(m) for m in new], old)
                self.assertTrue(same_typed(old_spec, new_spec))
                rc, out, err = quiet(skill_eval.main, ["validate", u])
                self.assertEqual(rc, 1)
                shown = [strip(l) for l in out.splitlines()]
                self.assertEqual([p for p in old if p not in shown], [])

    def test_no_schema_backed_check_raises_on_nesting_past_the_recursion_limit(self):
        v = nested(5000)
        u = evals_variant_unit("base")
        evals = eva.shape_schema("evals.schema.json")
        triggers = eva.shape_schema("trigger-evals.schema.json")
        transcript = eva.shape_schema("transcript.schema.json")

        def ev(fn):
            return lambda: evals.problems(fn(copy.deepcopy(BASE_EVALS)), rule="evals-valid", unit_root=u)

        def trig(items):
            return lambda: triggers.problems(items, pos=9, neg=10)
        cases = {
            "evals id": ev(edit_case(0, id=v)), "evals second id": ev(edit_case(1, id=v)),
            "evals every id": ev(lambda d: dict(d, evals=[dict(c, id=v) for c in d["evals"]])),
            "evals layer": ev(edit_case(1, layer=v)), "evals name": ev(edit_case(0, name=v)),
            "evals prompt": ev(edit_case(0, prompt=v)), "evals expected_output": ev(edit_case(0, expected_output=v)),
            "evals files entry": ev(edit_case(0, files=[v])), "evals assertion": ev(edit_case(2, assertions=[v])),
            "evals assertion layer": ev(edit_case(2, assertions=[{"text": "x", "layer": v}])),
            "evals assertion text": ev(edit_case(2, assertions=[{"text": v, "layer": "llm"}])),
            "evals list": ev(lambda d: dict(d, evals=v)), "evals case": ev(lambda d: dict(d, evals=[v])),
            "check type": lambda: skill_eval.check_object_problems("w", {"type": v}),
            "check target": lambda: skill_eval.check_object_problems("w", chk(REGEX, target=v)),
            "check pattern": lambda: skill_eval.check_object_problems("w", chk(REGEX, pattern=v)),
            "check input_regex": lambda: skill_eval.check_object_problems("w", chk(TOOL, input_regex=v)),
            "check normalize": lambda: skill_eval.check_object_problems("w", chk(ROWS, normalize=v)),
            "check unique": lambda: skill_eval.check_object_problems("w", chk(ROWS, rows=[], unique=v)),
            "check min": lambda: skill_eval.check_object_problems("w", chk(ROWS, min=v)),
            "check rows": lambda: skill_eval.check_object_problems("w", chk(ROWS, rows=v)),
            "check rows cell": lambda: skill_eval.check_object_problems("w", chk(ROWS, rows=[[v]])),
            "check unknown key": lambda: skill_eval.check_object_problems("w", chk(ROWS, z=v)),
            "trigger query": trig([dict(BASE_TRIGGERS[0], query=v)] + BASE_TRIGGERS[1:]),
            "trigger two queries": trig([dict(q, query=v) for q in BASE_TRIGGERS[:2]] + BASE_TRIGGERS[2:]),
            "trigger should_trigger": trig([dict(BASE_TRIGGERS[0], should_trigger=v)] + BASE_TRIGGERS[1:]),
            "trigger item": trig([v] + BASE_TRIGGERS[1:]),
            "scenario id": lambda: eva.scenario_errors(edit(BASE_SCENARIO, id=v)),
            "scenario turns": lambda: eva.scenario_errors(edit(BASE_SCENARIO, turns=[v])),
            "scenario outcome": lambda: eva.scenario_errors(edit(BASE_SCENARIO, expected_outcome=v)),
            "scenario criteria": lambda: eva.scenario_errors(edit(BASE_SCENARIO, success_criteria=[v])),
            "scenario document": lambda: eva.scenario_errors(v),
            "transcript final": lambda: transcript.problems(edit(BASE_TRANSCRIPT, final=v)),
            "transcript workspace": lambda: transcript.problems(edit(BASE_TRANSCRIPT, workspace=v)),
            "transcript tool call": lambda: transcript.problems(edit(BASE_TRANSCRIPT, tool_calls=[v])),
            "transcript tool": lambda: transcript.problems(edit(BASE_TRANSCRIPT, tool_calls=[{"tool": v}])),
            "transcript result": lambda: transcript.problems(edit(BASE_TRANSCRIPT, tool_calls=[{"tool": "t",
                                                                                                "result": v}])),
            "transcript tool results": lambda: transcript.problems(edit(BASE_TRANSCRIPT, tool_results=[v])),
            "frontmatter name": lambda: eva._field_type_errors({"name": v}, set()),
            "frontmatter metadata": lambda: eva._field_type_errors({"metadata": {"k": v}}, set()),
            "frontmatter argument-hint": lambda: eva._field_type_errors({"argument-hint": v}, set()),
            "frontmatter dmi": lambda: eva._field_type_errors({"disable-model-invocation": v}, set()),
        }
        for name, call in cases.items():
            with self.subTest(case=name):
                try:
                    errs = call()
                except (RecursionError, S.TooDeep) as e:
                    self.fail("%s escaped" % type(e).__name__)
                self.assertTrue(errs and all(isinstance(m, str) for m in errs), len(errs))


AUDIT_RULE_UNITS = {"missing-id": os.path.join(ROOT, "tests", "rules", "evals-valid", "fail", "missing-id"),
                    "unquoted-yes": os.path.join(ROOT, "tests", "rules", "field-types", "fail", "unquoted-yes")}


def probe_rule(rid, severity, passes=False):
    return {"id": rid, "applies_to": ["skill"], "category": {"evals-valid": "evaluation", "field-types": "structure"}[rid],
            "severity": severity, "protects": ["claude"], "source": {"house": "probe: a rule that reuses a built-in id"},
            "check": {"op": "file_exists", "path": "SKILL.md" if passes else "no-such-file.txt"},
            "message": "probe %s rule %s" % (severity, rid), "fix": "Probe fix text for the %s rule." % rid}


# The --rules bundles of audit A2-r1-5 (/tmp/swarm-A2/worker-6/bundles): name -> (rules, unit). A1 exits 1 for each
# (/tmp/swarm-A2/worker-6/captures/merge-a1-soft.txt, soft-ft-a1.txt, merge-a1-default.txt).
AUDIT_BUNDLES = {
    "soft-evals": ([probe_rule("evals-valid", "soft")], "missing-id"),
    "soft-ft": ([probe_rule("field-types", "soft")], "fm-compatibility-typed"),
    "soft-both": ([probe_rule("evals-valid", "soft"), probe_rule("field-types", "soft")], "unquoted-yes"),
    "pass-evals": ([probe_rule("evals-valid", "fail", True), probe_rule("field-types", "fail", True)], "missing-id"),
    "review-evals": ([probe_rule("evals-valid", "review")], "missing-id"),
}


class TestShapeRowMerge(unittest.TestCase):
    def test_a_rule_row_with_a_built_in_id_is_never_weaker_than_the_built_in_row(self):
        for name, (rules, unit_name) in AUDIT_BUNDLES.items():
            with self.subTest(bundle=name):
                d = os.path.join(tmpdir(), "bundles", name)
                wjson(os.path.join(d, name + ".rules.json"), {"ruleset": name, "version": "1.0.0", "rules": rules})
                unit = AUDIT_RULE_UNITS.get(unit_name) or frontmatter_variant_unit(unit_name)
                rc, out, err = quiet(eva.main, ["check", unit, "--rules", d])
                self.assertEqual(rc, 1, out)
                rows = eva.check(unit, RR.load_bundle(d)).checks
                built = {c["id"]: c for c in eva.check_skill(unit).checks}
                for rid in RULE_FILES:
                    row = [c for c in rows if c["id"] == rid]
                    self.assertEqual(len(row), 1, rid)
                    self.assertTrue(row[0]["must"] or not built[rid]["must"], rid)
                    if built[rid]["passed"] is False:
                        self.assertEqual(row[0]["status"], "fail", rid)


# name -> (edit of BASE_EVALS, a phrase of the problem that the legacy branch adds, evals_errors replaced by a stub
# that finds nothing). The last branch sits behind the same name check in evals_errors, so only the stub reaches it.
LOAD_SET_VARIANTS = {
    "claude-python-script-invalid": (edit_doc(claude_python_script="scripts/missing.py"),
                                     "evals.json.claude_python_script: existing relative scripts/*.py path required",
                                     False),
    "static-layer-without-check": (edit_case(2, layer="static"), "static layer needs a machine check object", False),
    "pressure-invalid": (edit_case(1, pressure={"kinds": []}), "evals[id=2].pressure: ", False),
    "name-unsafe": (edit_case(0, name="Case One"), "evals[id=1].name: 'Case One' must match", True),
}


class TestEvalSetBranches(unittest.TestCase):
    def test_each_legacy_load_eval_set_branch_has_a_variant_where_legacy_and_new_agree(self):
        for name, (edit_fn, phrase, stub) in LOAD_SET_VARIANTS.items():
            with self.subTest(variant=name):
                u = make_unit(os.path.join(tmpdir(), "load-set", name), evals=edit_fn(copy.deepcopy(BASE_EVALS)))

                def no_errors():
                    if stub:
                        return mock.patch.object(eva, "evals_errors", lambda path, skill_name: (3, []))
                    return contextlib.nullcontext()
                with legacy(), no_errors():
                    old_spec, old = skill_eval._legacy_load_eval_set(u)
                with no_errors():
                    new_spec, new = skill_eval.load_eval_set(u)
                self.assertTrue(any(phrase in p for p in old), old)
                self.assertEqual([strip(p) for p in new], old)
                self.assertTrue(same_typed(old_spec, new_spec))


class TestAscii(unittest.TestCase):
    def test_every_file_a2_adds_is_ascii(self):
        files = [os.path.join(ROOT, "tests", "test_differential.py"), INTENTIONAL_PATH] + \
                [os.path.join(ROOT, "schemas", n) for n in SCHEMAS] + \
                [os.path.join(ROOT, "rules", n) for n in RULE_FILES.values()] + \
                [os.path.join(ROOT, "policy", n) for n in POLICY_FILES]
        for rid in RULE_FILES:
            for dp, dn, fn in os.walk(os.path.join(ROOT, "tests", "rules", rid)):
                files += [os.path.join(dp, n) for n in fn]
        self.assertGreater(len(files), 20)
        for f in files:
            with open(f, "rb") as fh:
                self.assertTrue(fh.read().isascii(), rel(f))


if __name__ == "__main__":
    unittest.main()
