"""The contracts tests/test_differential.py pinned besides legacy-equals-new, kept when unit A6 deleted that suite and
its oracle (brief A6 D3): the schemas compile and carry source, severity and fix; the two shape rules load with every
field; the policy values, their sources and their refusals; deep nesting without a traceback; the shape-row merge; the
description data and the threshold operands; every schema message rule is reachable; ASCII of the files A2 and A4 added.
Where a test compared against a legacy body, the expected side is the value that body produced at the P7 commit
53edf4c. The variant tables pin, per variant, the status of its row and whether its input is rejected at that commit,
and digests pin every row and every function message of the inputs the differential suite compared."""
import contextlib, copy, functools, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import eva, skill_eval
from engine import rule_runner as RR, schema as S
from engine.primitives import local_refs as LR
import test_multi_tool_parity as P

SCHEMAS = ("evals.schema.json", "trigger-evals.schema.json", "scenario.schema.json", "transcript.schema.json",
           "skill-frontmatter.schema.json")
RULE_FILES = {"evals-valid": "skill-evals.rules.json", "field-types": "skill-frontmatter.rules.json"}
CORPUS_ROOTS = ("golden", "bad", os.path.join("tests", "fixtures"), os.path.join("tests", "rules"))
TRANSCRIPTS = ("bad/code-reviewer-answer-lookup.json", "bad/research-fabricated-urls.json", "code-reviewer-drill.json",
               "research-followup-cited.json", "research-followup.json")
CR_V2 = "tests/fixtures/agents/code-reviewer-v2"
# The rows unit B1 may change on an agent (brief B1 D9): the v2 row, the D8 rows and the permission rows.
B1_AGENT_ROWS = {"agent-frontmatter-v2", "success-measurable", "tools-declared", "tools-subset", "scenarios-present",
                 "scenarios-valid", "scenarios-expected", "multi-turn-scenario", "parity-permissions-claude",
                 "parity-permissions-codex", "parity-permissions-opencode", "parity-permissions-hermes"}
B2_ROWS = ("wrappers-generated", "tool-metadata-generated")
SUFFIX_RE = re.compile(r" \[(?:rule [a-z-]+, )?([a-z-]+\.schema\.json)#(/[^,\]]*), keyword ([^\]]+)\]$")
DISAGREE = "rule and eva.py disagree"
# A4 rule ids by rule file (brief A4 D1). With the five rows of SHAPE_IDS they are the 37 rows A4 moved out of
# check_skill, check_agent and _common_name.
A4_RULE_FILES = {}
for _fname, _ids in (("skill-description.rules.json", ("description-present", "description-length",
                                                       "description-min-length", "description-no-xml",
                                                       "description-when", "description-pov", "single-purpose")),
                     ("skill-structure.rules.json", ("frontmatter", "frontmatter-fields", "compatibility-length",
                                                     "inputs-declared", "outputs-declared", "example-present",
                                                     "size-limit", "refs-relative", "refs-exist", "refs-one-level",
                                                     "refs-path-depth", "reference-toc", "version-format",
                                                     "evals-count")),
                     ("agent-structure.rules.json", ("goal-present", "success-measurable", "scenarios-present",
                                                     "scenarios-valid", "scenarios-expected", "tools-declared",
                                                     "tools-subset", "tools-documented", "memory-section",
                                                     "multi-turn-scenario"))):
    for _rid in _ids:
        A4_RULE_FILES[_rid] = _fname
SHAPE_IDS = ("name-format", "name-matches-folder", "name-reserved", "field-types", "evals-valid")
DESC_DATA = os.path.join(ROOT, "rules", "data", "description.json")
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
EVAL_NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"


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



def quiet(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a)
    return rc, out.getvalue(), err.getvalue()


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

# name -> (edit of BASE_EVALS or raw bytes or None for no file, rejected at the P7 commit)
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


# name -> (meta, typed, rejected at the P7 commit)
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
    "base": (BASE_SCENARIO, True),
    "outcome-list": (edit(BASE_SCENARIO, expected_outcome=["a", "b"]), True),
    "no-success-criteria": (edit(BASE_SCENARIO, success_criteria=KeyError), True),
    "extra-keys": (edit(BASE_SCENARIO, note=1, turns=[{"user": "u", "x": 2}]), True),
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
    "normalized-fields-bad": (edit(BASE_TRANSCRIPT, tool="cursor", format=" ", model=5, session_id="",
                                   turns=[{"user": "u"}], usage={"input_tokens": -1, "output_tokens": 0}, cost_usd=-0.1,
                                   is_error="no", agent_loaded="maybe"), True),
    "tool-call-normalized-fields-bad": (edit(BASE_TRANSCRIPT, tool_calls=[{"tool": "t", "id": 1, "is_error": "x"}]), True),
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


# ---------- A3 variants: one per branch of the English, parity and naming rows (brief A3 D14) ----------
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



AGENT_MD_V1 = ("---\nname: code-scout\ndescription: Reads a repository read-only and explains it. Use when the user asks for "
               "an overview.\ngoal: Explain the repository structure with file and line evidence.\ntools: [glob, grep, "
               "read_file]\nsuccess_criteria:\n  - at least 2 file:line citations\n  - no file changes (read-only)\n---\n"
               "# Code Scout\n## Tools\n- glob: map the structure / grep: find places / read_file: confirm lines\n"
               "## Memory & Handoff\nRemember the topic of earlier turns and hand open questions to the next agent.\n")
S1_V1 = {"id": "overview", "turns": [{"user": "Give me an overview of this repo"}], "expected_tools": ["glob"],
         "must_include": ["tests"], "expected_outcome": "an overview with file:line evidence"}
S2_V1 = {"id": "drill", "turns": [{"user": "Give me an overview"}, {"user": "Explain the test folder you just mentioned"}],
         "expected_tools": ["grep", "read_file"], "must_include": ["test"],
         "expected_outcome": ["explains the test folder from the earlier turn", "no file changes"]}


def agent_variant(md_edit=None, caps=None, skip=(), replace=None, files=None, links=None, wrappers=True, folder="code-scout",
                  scenarios=(S1_V1, S2_V1)):
    def build(top):
        d = os.path.join(top, folder)
        md = md_edit(AGENT_MD_V1) if md_edit else AGENT_MD_V1
        write(os.path.join(d, "AGENT.md"), md)
        for i, s in enumerate(scenarios, 1):
            write(os.path.join(d, "scenarios", "%02d.json" % i), json.dumps(s, indent=1))
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
# name -> (builder, row id, status of that row at the P7 commit)
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
                            "parity-permissions-claude", "n/a"),
    "agent-all-caps": (agent_variant(md_edit=sub("[glob, grep, read_file]", ALL_CAPS)), "parity-permissions-codex", "needs-review"),
    "agent-grants-more": (agent_variant(caps={"read", "write", "shell", "web_fetch", "web_search"}),
                          "parity-permissions-opencode", "fail"),
    "agent-grants-less": (agent_variant(md_edit=sub("[glob, grep, read_file]", "[read_file, write_file, web_fetch]"),
                                        caps={"read"}), "parity-permissions-claude", "fail"),
    "agent-write-web": (agent_variant(md_edit=sub("[glob, grep, read_file]", "[read_file, write_file, web_fetch]")),
                        "parity-permissions-claude", "fail"),
    "agent-ko-agent-md": (agent_variant(md_edit=lambda t: t + KO + "\n"), "english-agent-md", "fail"),
    "agent-ko-scenario": (agent_variant(files={"scenarios/03.json": json.dumps(dict(S1_V1, id=KO))}), "english-scenarios",
                          "fail"),
    "agent-ko-matcher": (agent_variant(files={"scenarios/03.json": json.dumps(dict(S1_V1, must_include=[KO]))}),
                         "english-output-matchers", "needs-review"),
    "agent-ko-scenario-name": (agent_variant(files={"scenarios/%s.json" % KO: json.dumps(S1_V1)}), "english-scenarios", "fail"),
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


@functools.lru_cache(maxsize=None)
def a3_variant_unit(name):
    return A3_VARIANTS[name][0](os.path.join(tmpdir(), "a3", name))


# ---------- A4 variants: one per branch of the rows A4 moved out of check_skill, check_agent and _common_name ----------
# The dropped Korean alternatives, written as escapes: MULTI_PURPOSE_RE phrases, has_section words, the TOC word.
KO_AND_ALSO, KO_NOT_ONLY = "\uadf8\ub9ac\uace0 \ub610", "\ubfd0\ub9cc \uc544\ub2c8\ub77c"
KO_INPUT, KO_OUTPUT, KO_EXAMPLE = "\uc785\ub825", "\ucd9c\ub825", "\uc608\uc2dc"
KO_TOOL, KO_MEMORY, KO_HANDOFF, KO_TOC = "\ub3c4\uad6c", "\uae30\uc5b5", "\uc778\uc218\uc778\uacc4", "\ubaa9\ucc28"
KO_PRONOUN, KO_PARTICLE = "\uc81c\uac00", "\ub97c"
LONG_REF = "".join("Line %d of the guide.\n" % i for i in range(120))
CRITERIA = "  - at least 2 file:line citations\n  - no file changes (read-only)\n"
EVERY_KIND = ("  - at least 3 source links\n  - at most 800 characters\n  - a comparison table\n  - read-only\n"
              "  - at least 2 file:line citations\n  - be helpful\n")


def desc(text):
    return sub("Cleans one CSV file for the differential suite. Use when a test needs a valid skill unit.", text)


def body(old, new):
    return sub(old, new)


def link(text, files=None):
    return skill_variant(md_edit=sub("## Example\n", text + "\n## Example\n"), files=files)


def repo_variant(text):
    """A skill inside a git work tree (a .git folder at the top) with tools/run.py at the repo root."""
    def build(top):
        os.makedirs(os.path.join(top, ".git"))
        write(os.path.join(top, "tools", "run.py"), "print(1)\n")
        return link(text)(os.path.join(top, "skills"))
    return build


def edits(*fns):
    return lambda t: functools.reduce(lambda acc, f: f(acc), fns, t)


def to_bytes(n):
    return lambda t: t + "x" * (n - len(t.encode("utf-8")) - 1) + "\n"


def to_lines(n):
    return lambda t: t + "".join("Note %d.\n" % i for i in range(n - len(t.splitlines())))


def ref_lines(n):
    """A reference file of exactly n lines whose first heading after the title is not a table of contents."""
    return "# Long\n## Part\n" + "".join("Line %d of the guide.\n" % i for i in range(n - 2))


NO_IO_HEADINGS = edits(body("## Inputs\n", "## Notes\n"), body("## Outputs\n", "## Notes\n"),
                       body("## Example\n", "## Notes\n"))
D19 = "Cleans one CSV file"


# name -> (builder, row id, status of that row at the P7 commit)
A4_VARIANTS = {
    "ko-multi-and-also": (skill_variant(md_edit=sub("suite.", "suite %s." % KO_AND_ALSO)), "single-purpose", "pass"),
    "ko-multi-not-only": (skill_variant(md_edit=sub("suite.", "suite %s." % KO_NOT_ONLY)), "single-purpose", "pass"),
    "ko-heading-input": (skill_variant(md_edit=body("## Inputs\n", "## %s\n" % KO_INPUT)), "inputs-declared", "fail"),
    "ko-heading-output": (skill_variant(md_edit=body("## Outputs\n", "## %s\n" % KO_OUTPUT)), "outputs-declared", "fail"),
    "ko-heading-example": (skill_variant(md_edit=body("## Example\n", "## %s\n" % KO_EXAMPLE)), "example-present", "fail"),
    "ko-heading-tool": (agent_variant(md_edit=body("## Tools\n", "## %s\n" % KO_TOOL)), "tools-documented", "fail"),
    "ko-heading-memory": (agent_variant(md_edit=body("## Memory & Handoff\n", "## %s\n" % KO_MEMORY)), "memory-section",
                          "fail"),
    "ko-heading-handoff": (agent_variant(md_edit=body("## Memory & Handoff\n", "## %s\n" % KO_HANDOFF)), "memory-section",
                           "fail"),
    "ko-toc": (link("See [l](references/long.md).", {"references/long.md": "# Long\n## %s\n%s" % (KO_TOC, LONG_REF)}),
               "reference-toc", "fail"),
    "s-base": (skill_variant(), "frontmatter", "pass"),
    "s-frontmatter-error": (skill_variant(md_edit=fm("bad line\n")), "frontmatter", "fail"),
    "s-extra-field": (skill_variant(md_edit=fm("inputs: csv_path\n")), "frontmatter-fields", "fail"),
    "s-host-field": (skill_variant(md_edit=fm('argument-hint: "[file]"\n')), "frontmatter-fields", "pass"),
    "s-desc-missing": (skill_variant(md_edit=lambda t: re.sub(r"description: .*\n", "", t)), "description-present", "fail"),
    "s-desc-empty": (skill_variant(md_edit=desc('""')), "description-present", "fail"),
    "s-desc-list": (skill_variant(md_edit=desc("[a, b]")), "description-present", "fail"),
    "s-desc-long": (skill_variant(md_edit=desc("Cleans %s files. Use when a test needs a skill." % ("very " * 220))),
                    "description-length", "fail"),
    "s-desc-short": (skill_variant(md_edit=desc("Cleans CSVs.")), "description-min-length", "fail"),
    "s-desc-gt": (skill_variant(md_edit=desc("Cleans one CSV file > 2 MB. Use when a test needs a valid skill unit.")),
                  "description-no-xml", "fail"),
    "s-desc-no-when": (skill_variant(md_edit=desc("Cleans one CSV file.")), "description-when", "fail"),
    "s-desc-negated-when": (skill_variant(md_edit=desc("Cleans one CSV file. Do not use when the file is huge.")),
                            "description-when", "fail"),
    "s-desc-quoted-when": (skill_variant(md_edit=desc('Cleans one CSV file, as in "Use when the user asks for it".')),
                           "description-when", "fail"),
    "s-desc-read-before": (skill_variant(md_edit=desc("Cleans CSV files. Read this skill before editing a CSV file.")),
                           "description-when", "pass"),
    "s-desc-subject": (skill_variant(md_edit=desc("Cleans CSV files. This skill should be used when a CSV file needs "
                                                  "cleaning.")), "description-when", "pass"),
    "s-desc-tight-stop": (skill_variant(md_edit=desc("Cleans CSV files. Use when needed.The user asks for a clean "
                                                     "file.")), "description-when", "fail"),
    "s-desc-pov": (skill_variant(md_edit=desc("I clean one CSV file. Use when the user wants a clean file.")),
                   "description-pov", "fail"),
    "s-desc-pov-ko": (skill_variant(md_edit=desc("%s cleans one CSV file. Use when the user wants a clean file."
                                                 % KO_PRONOUN)), "description-pov", "fail"),
    "s-desc-roman": (skill_variant(md_edit=desc("Cleans Phase I CSV files. Use when a test needs a valid skill unit.")),
                     "description-pov", "pass"),
    "s-desc-quoted-pov": (skill_variant(md_edit=desc('Cleans files the user calls "my data". Use when a test needs a '
                                                     'valid skill unit.')), "description-pov", "pass"),
    "s-many-verbs": (skill_variant(md_edit=desc("Summarizes PDFs, cleans CSVs and sends emails. Use when a test needs a "
                                                "valid skill unit.")), "single-purpose", "fail"),
    "s-add-on": (skill_variant(md_edit=desc("Cleans CSV files and also trims them. Use when a test needs a valid skill "
                                            "unit.")), "single-purpose", "fail"),
    "s-verb-after-to": (skill_variant(md_edit=desc("Cleans CSV files to report totals and to schedule runs. Use when a "
                                                   "test needs a valid skill unit.")), "single-purpose", "fail"),
    "s-compat-ok": (skill_variant(md_edit=fm("compatibility: Python 3.9\n")), "compatibility-length", "pass"),
    "s-compat-long": (skill_variant(md_edit=fm("compatibility: %s\n" % ("x" * 600))), "compatibility-length", "fail"),
    "s-compat-list": (skill_variant(md_edit=fm("compatibility: [a, b]\n")), "compatibility-length", "fail"),
    "s-no-inputs": (skill_variant(md_edit=body("## Inputs\n", "## Notes\n")), "inputs-declared", "fail"),
    "s-no-outputs": (skill_variant(md_edit=body("## Outputs\n", "## Notes\n")), "outputs-declared", "fail"),
    "s-fenced-example": (skill_variant(md_edit=body("## Example\n", "```\n## Example\n```\n")), "example-present", "fail"),
    "s-size-bytes": (skill_variant(md_edit=lambda t: t + "x" * 21000 + "\n"), "size-limit", "fail"),
    "s-size-lines": (skill_variant(md_edit=lambda t: t + "".join("Note %d.\n" % i for i in range(520))), "size-limit",
                     "fail"),
    "s-ref-ok": (link("See [g](references/g.md).", {"references/g.md": "# G\nShort.\n"}), "refs-exist", "pass"),
    "s-ref-missing": (link("See [m](references/missing.md)."), "refs-exist", "fail"),
    "s-ref-backslash": (link("See [b](references\\g.md).", {"references/g.md": "# G\n"}), "refs-relative", "fail"),
    "s-ref-absolute": (link("See [a](/etc/hosts)."), "refs-exist", "fail"),
    "s-ref-escaping": (link("See [u](../../../../nope.md)."), "refs-relative", "fail"),
    "s-ref-chain": (link("See [g](references/g.md).", {"references/g.md": "# G\nRun scripts/x.py first.\n",
                                                       "scripts/x.py": "print(1)\n"}), "refs-one-level", "fail"),
    "s-ref-chain-back": (link("See [g](references/g.md).", {"references/g.md": "# G\n[back](../SKILL.md) [self](g.md)\n"}),
                         "refs-one-level", "pass"),
    "s-ref-deep": (link("See [d](references/deep/d.md).", {"references/deep/d.md": "# D\n"}), "refs-path-depth", "fail"),
    "s-ref-fenced-run": (link("```bash\npython scripts/x.py\n```", {"scripts/x.py": "print(1)\n"}), "refs-exist", "pass"),
    "s-ref-fenced-missing": (link("```bash\npython scripts/x.py\n```"), "refs-exist", "fail"),
    "s-ref-ko-particle": (link("Run scripts/x.py%s now." % KO_PARTICLE, {"scripts/x.py": "print(1)\n"}), "refs-exist",
                          "pass"),
    "s-ref-glob": (link("Files in assets/*.png and scripts/ are listed."), "refs-exist", "pass"),
    "s-ref-repo-root": (repo_variant("See [r](tools/run.py)."), "refs-exist", "needs-review"),
    "s-ref-up": (repo_variant("See [u](../../tools/run.py)."), "refs-exist", "needs-review"),
    "s-ref-repo-and-missing": (repo_variant("See [r](tools/run.py) and [m](references/missing.md)."), "refs-exist",
                               "fail"),
    "s-toc-ok": (link("See [l](references/long.md).", {"references/long.md": "# Long\n## Contents\n" + LONG_REF}),
                 "reference-toc", "pass"),
    "s-toc-missing": (link("See [l](references/long.md).", {"references/long.md": "# Long\n## Part\n" + LONG_REF}),
                      "reference-toc", "fail"),
    "s-toc-word": (link("See [l](references/long.md).", {"references/long.md": "# Long\n## TOC\n" + LONG_REF}),
                   "reference-toc", "pass"),
    "s-toc-title-only": (link("See [l](references/long.md).", {"references/long.md": "# Long\n" + LONG_REF}),
                         "reference-toc", "fail"),
    "s-version-none": (skill_variant(md_edit=sub("metadata:\n  version: 1.0.0\n", "")), "version-format", "n/a"),
    "s-version-bad": (skill_variant(md_edit=sub("version: 1.0.0", "version: v1")), "version-format", "fail"),
    "s-version-list": (skill_variant(md_edit=sub("version: 1.0.0", "version: [1, 2]")), "version-format", "fail"),
    "s-evals-none": (skill_variant(evals_edit=lambda d: None), "evals-count", "fail"),
    "s-evals-two": (skill_variant(evals_edit=lambda d: dict(d, evals=d["evals"][:2])), "evals-count", "fail"),
    "a-base": (agent_variant(), "frontmatter", "pass"),
    "a-no-frontmatter": (agent_variant(md_edit=lambda t: t.replace("---\n", "", 2), wrappers=False), "frontmatter", "fail"),
    "a-desc-missing": (agent_variant(md_edit=lambda t: re.sub(r"description: .*\n", "", t), wrappers=False),
                       "description-present", "fail"),
    "a-short-goal": (agent_variant(md_edit=sub("Explain the repository structure with file and line evidence.",
                                               "Explain it.")), "goal-present", "fail"),
    "a-no-criteria": (agent_variant(md_edit=sub("success_criteria:\n" + CRITERIA, "")), "success-measurable", "fail"),
    "a-vague-criteria": (agent_variant(md_edit=sub(CRITERIA, "  - be helpful\n")), "success-measurable", "fail"),
    "a-every-criterion-kind": (agent_variant(md_edit=sub(CRITERIA, EVERY_KIND)), "success-measurable", "fail"),
    "a-criterion-string": (agent_variant(md_edit=sub("success_criteria:\n" + CRITERIA, "success_criteria: read-only\n")),
                           "success-measurable", "fail"),
    "a-no-scenarios": (agent_variant(scenarios=()), "scenarios-valid", "fail"),
    "a-one-scenario": (agent_variant(scenarios=(S2_V1,)), "scenarios-present", "fail"),
    "a-invalid-json": (agent_variant(files={"scenarios/03.json": "{"}), "scenarios-valid", "fail"),
    "a-scenario-list": (agent_variant(files={"scenarios/03.json": "[]"}), "scenarios-valid", "fail"),
    "a-scenario-undecodable": (agent_variant(files={"scenarios/03.json": b"\xff\xfe"}), "scenarios-valid", "fail"),
    "a-no-expected": (agent_variant(scenarios=(S2_V1, {k: v for k, v in S1_V1.items() if k != "expected_outcome"})),
                      "scenarios-expected", "fail"),
    "a-single-turn": (agent_variant(scenarios=(S1_V1, dict(S1_V1, id="other"))), "multi-turn-scenario", "fail"),
    "a-undeclared-tool": (agent_variant(scenarios=(S2_V1, dict(S1_V1, expected_tools=["glob", "bash"]))), "tools-subset",
                          "fail"),
    "a-tools-string": (agent_variant(md_edit=sub("tools: [glob, grep, read_file]", "tools: glob")), "tools-declared",
                       "fail"),
    "a-no-tools": (agent_variant(md_edit=sub("tools: [glob, grep, read_file]\n", "")), "tools-declared", "fail"),
    "a-no-tools-section": (agent_variant(md_edit=body("## Tools\n", "## Usage\n")), "tools-documented", "fail"),
    "a-context-heading": (agent_variant(md_edit=body("## Memory & Handoff\n", "## Context\n")), "memory-section", "pass"),
    "a-no-memory": (agent_variant(md_edit=body("## Memory & Handoff\n", "## Notes\n")), "memory-section", "fail"),
    "s-fm-comment-headings": (skill_variant(md_edit=edits(NO_IO_HEADINGS, fm("# Inputs\n# Outputs\n# Example\n"))),
                              "inputs-declared", "fail"),
    "s-fm-block-headings": (skill_variant(md_edit=edits(NO_IO_HEADINGS, desc(
        "|\n  Cleans one CSV file. Use when a test needs a valid skill unit.\n  ## Inputs\n  ## Outputs\n  ## Example"))),
                            "example-present", "fail"),
    "s-fm-compat-ref": (skill_variant(md_edit=fm("compatibility: needs scripts/missing.py\n")), "refs-exist", "pass"),
    "s-size-bytes-at": (skill_variant(md_edit=to_bytes(20000)), "size-limit", "pass"),
    "s-size-bytes-over": (skill_variant(md_edit=to_bytes(20001)), "size-limit", "fail"),
    "s-size-lines-at": (skill_variant(md_edit=to_lines(499)), "size-limit", "pass"),
    "s-size-lines-over": (skill_variant(md_edit=to_lines(500)), "size-limit", "fail"),
    "s-toc-lines-at": (link("See [l](references/long.md).", {"references/long.md": ref_lines(100)}), "reference-toc",
                       "n/a"),
    "s-toc-lines-over": (link("See [l](references/long.md).", {"references/long.md": ref_lines(101)}), "reference-toc",
                         "fail"),
    "s-compat-blank": (skill_variant(md_edit=fm('compatibility: "   "\n')), "compatibility-length", "fail"),
    "s-compat-empty": (skill_variant(md_edit=fm("compatibility: ''\n")), "compatibility-length", "fail"),
    "s-compat-padded": (skill_variant(md_edit=fm('compatibility: "  Python 3.9  "\n')), "compatibility-length", "pass"),
    "s-compat-padded-max": (skill_variant(md_edit=fm('compatibility: " %s "\n' % ("x" * 500))), "compatibility-length",
                            "pass"),
    "s-desc-blank": (skill_variant(md_edit=desc('"   "')), "description-present", "fail"),
    "s-desc-padded-short": (skill_variant(md_edit=desc('"  %s  "' % D19)), "description-min-length", "fail"),
    "s-desc-padded-min": (skill_variant(md_edit=desc('"  %s.  "' % D19)), "description-min-length", "pass"),
    "s-desc-padded-max": (skill_variant(md_edit=desc('" C%s "' % ("x" * 1023))), "description-length", "pass"),
    "s-desc-padded-over": (skill_variant(md_edit=desc('" C%s "' % ("x" * 1024))), "description-length", "fail"),
    "a-padded-goal": (agent_variant(md_edit=sub("Explain the repository structure with file and line evidence.",
                                                '"Explain things\\n"')), "goal-present", "fail"),
}


@functools.lru_cache(maxsize=None)
def a4_variant_unit(name):
    return A4_VARIANTS[name][0](os.path.join(tmpdir(), "a4", name))


def unescape(text):
    """text with the \\uXXXX escapes of the oracle (non-ASCII written as escapes) decoded."""
    return re.sub(r"\\u([0-9a-f]{4})", lambda m: chr(int(m.group(1), 16)), text)


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
    return [os.path.join(ROOT, "transcripts", *t.split("/")) for t in TRANSCRIPTS]


def schema_problems_of_every_input():
    """[(case, problems)] of every schema-backed function on every corpus input and every variant."""
    out = []
    for u in corpus_units():
        if eva.detect_kind(u) != "skill":
            continue
        raw, _ = eva.read_text(os.path.join(u, "SKILL.md"))
        meta = (eva.parse_frontmatter_ex(raw)[0] if raw is not None else None) or {}
        typed = set(eva.yaml_typed_keys(raw)) if meta else set()
        name = eva.unicodedata.normalize("NFKC", eva._str_field(meta, "name"))
        out += [("evals_errors:" + rel(u), eva.evals_errors(u, name)[1]),
                ("_field_type_errors:" + rel(u), eva._field_type_errors(meta, typed)),
                ("load_triggers:" + rel(u), skill_eval.load_triggers(u)[1])]
    out += [("scenario_errors:" + rel(f), eva.scenario_errors(load_json(f))) for f in scenario_files()]
    out += [("load_transcript:" + rel(t), eva.load_transcript(t)[1]) for t in transcript_files()]
    out += [("evals_errors:" + n, eva.evals_errors(evals_variant_unit(n), "diff-base")[1]) for n in EVALS_VARIANTS]
    out += [("_field_type_errors:" + n, eva._field_type_errors(meta, typed)) for n, (meta, typed, _) in FIELD_VARIANTS.items()]
    out += [("scenario_errors:" + n, eva.scenario_errors(data)) for n, (data, _) in SCENARIO_VARIANTS.items()]
    out += [("load_transcript:" + n, eva.load_transcript(transcript_variant_file(n))[1]) for n in TRANSCRIPT_VARIANTS]
    out += [("load_triggers:" + n, skill_eval.load_triggers(trigger_variant_unit(n))[1]) for n in TRIGGER_VARIANTS]
    out += [("check_object:" + n, skill_eval.check_object_problems("w", c)) for n, (c, _) in CHECK_VARIANTS.items()]
    return out


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
        self.assertEqual(tuple(doc["$defs"]["case-layer"]["properties"]["layer"]["enum"]), skill_eval.LAYERS)
        self.assertEqual(doc["$defs"]["case-name"]["properties"]["name"]["pattern"], EVAL_NAME_PATTERN)
        self.assertIn(EVAL_NAME_PATTERN, doc["$defs"]["case-name"]["description"])

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
                 ("scenario.schema.json", os.path.join(ROOT, *CR_V2.split("/"), "scenarios", "01-overview.json"), scen_bad,
                  None),
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

    def test_the_rows_keep_their_category_and_must(self):
        rows = {c["id"]: (c["category"], c["must"]) for c in eva.check(os.path.join(ROOT, "golden", "skills",
                                                                                    "csv-cleaner")).checks}
        self.assertEqual({rid: rows[rid] for rid in RULE_FILES}, RULE_ROW_CATEGORY_MUST)

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
            "float-as-int": {"eval-policy.json": ("HIGH_VARIANCE_STDDEV", "value", 1),
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


EP, PR, MO, GR = "eval-policy.json", "pricing.json", "models.json", "gate.rules.json"
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
    "agreement-min-negative": (GR, set_value("AGREEMENT_MIN", -1.0),
                               ("policy/gate.rules.json: AGREEMENT_MIN: ", "got -1.0")),
    "regression-tolerance-above-one": (GR, set_value("REGRESSION_TOLERANCE", 2.0),
                                       ("policy/gate.rules.json: REGRESSION_TOLERANCE: ", "got 2.0")),
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
    def test_check_all_prints_the_p7_verdict_of_every_unit_beside_a_deeply_nested_one(self):
        top, units = deep_units()
        rc, out, err = quiet(eva.main, ["check-all", top])
        lines = out.splitlines()
        for u in units:
            want = DEEP_VERDICTS[os.path.basename(u)]
            self.assertEqual([l.split()[0] for l in lines if l.endswith(os.sep + os.path.basename(u))],
                             ["[%s]" % want], out)
        self.assertIn("total=3 ", out)
        self.assertEqual(rc, 1)

    def test_validate_gives_the_p7_problems_for_a_deep_id_and_a_deep_query(self):
        for u in deep_units()[1][:2]:
            name = os.path.basename(u)
            with self.subTest(unit=name):
                spec, problems = skill_eval.load_eval_set(u)
                self.assertEqual([strip(m) for m in problems], DEEP_PROBLEMS[name])
                self.assertTrue(same_typed({k: v for k, v in spec.items() if k != "skill_dir"}, DEEP_SPECS[name]))
                rc, out, err = quiet(skill_eval.main, ["validate", u])
                self.assertEqual(rc, 1)
                shown = [strip(l) for l in out.splitlines()]
                self.assertEqual([p for p in DEEP_PROBLEMS[name] if p not in shown], [])

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


# name -> (edit of BASE_EVALS, a phrase of the problem that the branch adds, evals_errors replaced by a stub
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
    def test_each_load_eval_set_branch_gives_its_p7_problems(self):
        for name, (edit_fn, phrase, stub) in LOAD_SET_VARIANTS.items():
            with self.subTest(variant=name):
                u = make_unit(os.path.join(tmpdir(), "load-set", name), evals=edit_fn(copy.deepcopy(BASE_EVALS)))
                no_errors = mock.patch.object(eva, "evals_errors", lambda path, skill_name: (3, [])) if stub else \
                    contextlib.nullcontext()
                with no_errors:
                    _, problems = skill_eval.load_eval_set(u)
                self.assertTrue(any(phrase in p for p in problems), problems)
                self.assertEqual([strip(p) for p in problems], LOAD_SET_PROBLEMS[name])


def operand(fname, rid, key):
    doc = load_json(os.path.join(ROOT, "rules", fname))
    return next(r for r in doc["rules"] if r["id"] == rid)["check"][key]


# The whole check node of every A4 rule on a generic op (not a primitive and not a host op).
GENERIC_NODES = {"goal-present": {"op": "regex", "sel": "frontmatter:#/goal", "normalize": ["string-or-empty", "strip"],
                                  "pattern": r"[\s\S]{15}"}}


class TestDescriptionData(unittest.TestCase):
    def test_every_data_value_equals_its_p7_value(self):
        doc = load_json(DESC_DATA)
        got = {k: unescape(v) if isinstance(v, str) else v for k, v in doc.items() if k != "$comment"}
        self.assertEqual(got, DESCRIPTION_DATA)
        self.assertEqual(DESCRIPTION_DATA["HEADING_WORDS"]["memory"], ["memory", "handoff", "context"])

    def test_every_threshold_operand_equals_its_p7_value(self):
        s, d, a = "skill-structure.rules.json", "skill-description.rules.json", "agent-structure.rules.json"
        v = A3_OPERANDS
        pairs = [
            (operand(s, "frontmatter-fields", "fields"), v["SKILL_FIELDS"]),
            (operand(s, "frontmatter-fields", "host_fields"), v["HOST_SKILL_FIELDS"]),
            (operand(s, "compatibility-length", "max"), v["COMPATIBILITY_MAX"]), (operand(s, "compatibility-length", "min"), 1),
            (operand(s, "size-limit", "max_bytes"), v["SKILL_MAX_BYTES"]), (operand(s, "size-limit", "lines_below"), v["SKILL_MAX_LINES"]),
            (operand(s, "reference-toc", "toc_lines"), v["REF_TOC_LINES"]), (operand(s, "evals-count", "min"), v["MIN_EVALS"]),
            (operand(s, "refs-path-depth", "depth"), 1), (operand(s, "version-format", "pattern"), r"^\d+\.\d+\.\d+$"),
            (operand(s, "reference-toc", "toc_pattern"), v["TOC_PATTERN"]),
            (operand(d, "description-length", "max"), v["DESCRIPTION_MAX"]), (operand(d, "description-length", "min"), 1),
            (operand(d, "description-min-length", "min"), v["DESCRIPTION_MIN"]), (operand(d, "description-no-xml", "chars"), "<>"),
            (operand(a, "scenarios-present", "min"), 2), (operand(a, "goal-present", "pattern"), r"[\s\S]{15}"),
        ]
        for got, want in pairs:
            self.assertEqual(got, want)

    def test_every_a4_rule_on_a_generic_op_has_its_whole_check_node_pinned(self):
        generic = {}
        for fname in sorted(set(A4_RULE_FILES.values())):
            for r in load_json(os.path.join(ROOT, "rules", fname))["rules"]:
                if r["check"]["op"] not in RR.HOST_OPS and r["check"]["op"] not in RR.PRIMITIVES:
                    generic[r["id"]] = r["check"]
        self.assertEqual(generic, GENERIC_NODES)
        self.assertEqual(eva._str_field({"goal": " x\n"}, "goal"), "x")


class TestVariantRows(unittest.TestCase):
    def test_every_variant_row_has_its_p7_status(self):
        wrong = {}
        for table, unit_of in ((A3_VARIANTS, a3_variant_unit), (A4_VARIANTS, a4_variant_unit)):
            for name, (_, rid, status) in table.items():
                got = [c["status"] for c in eva.check(unit_of(name)).checks if c["id"] == rid]
                if got != [status]:
                    wrong[name] = (rid, status, got)
        self.assertEqual(wrong, {})

    def test_every_a4_rule_has_a_variant(self):
        self.assertEqual(set(A4_RULE_FILES) - {v[1] for v in A4_VARIANTS.values()}, set())
        self.assertGreaterEqual(len(A3_VARIANTS), 90)

    def test_the_edge_variants_sit_on_their_edge(self):
        want = {"s-size-bytes-at": ("size-limit", "SKILL.md 20000 bytes / "),
                "s-size-bytes-over": ("size-limit", "SKILL.md 20001 bytes / "),
                "s-size-lines-at": ("size-limit", " / 499 lines ("), "s-size-lines-over": ("size-limit", " / 500 lines ("),
                "s-toc-lines-at": ("reference-toc", "no referenced .md file over 100 lines"),
                "s-toc-lines-over": ("reference-toc", "over 100 lines start with a table of contents: missing in "),
                "s-compat-padded": ("compatibility-length", "compatibility length 10 (1..500)"),
                "s-compat-padded-max": ("compatibility-length", "compatibility length 500 (1..500)"),
                "s-compat-blank": ("compatibility-length", "compatibility length 0 (1..500)"),
                "s-compat-empty": ("compatibility-length", "compatibility length 0 (1..500)"),
                "s-desc-padded-short": ("description-min-length", "description length 19 ("),
                "s-desc-padded-min": ("description-min-length", "description length 20 ("),
                "s-desc-padded-max": ("description-length", "description length 1024 (1..1024)"),
                "s-desc-padded-over": ("description-length", "description length 1025 (1..1024)"),
                "s-fm-comment-headings": ("frontmatter", "YAML frontmatter between --- lines"),
                "s-fm-block-headings": ("frontmatter", "YAML frontmatter between --- lines"),
                "s-fm-compat-ref": ("compatibility-length", "compatibility length 24 (1..500)"),
                "a-padded-goal": ("frontmatter", "YAML frontmatter")}
        got = {}
        for name, (rid, text) in want.items():
            reasons = [c["reason"] for c in eva.check(a4_variant_unit(name)).checks if c["id"] == rid]
            got[name] = (rid, text if len(reasons) == 1 and (reasons[0] == text if rid == "frontmatter" else
                                                             text in reasons[0]) else reasons)
        self.assertEqual(got, want)
        with open(os.path.join(a4_variant_unit("a-padded-goal"), "AGENT.md"), encoding="utf-8") as f:
            goal = eva.parse_frontmatter(f.read())[0]["goal"]
        self.assertEqual((goal, len(goal.strip())), ("Explain things\n", 14))

    def test_an_unreadable_skill_md_gives_the_p7_frontmatter_row(self):
        u = skill_variant(files={"SKILL.md": b"---\nname: diff-base\n---\n\xff\xfe\n"})(os.path.join(tmpdir(), "a4x", "u"))
        rows = [(c["status"], c["must"], c["reason"]) for c in eva.check(u).checks if c["id"] == "frontmatter"]
        self.assertEqual(rows, UNREADABLE_FRONTMATTER_ROW)
        self.assertIn("cannot read SKILL.md", rows[0][2])


def p7_norm(text):
    for old, new in sorted({(tmpdir(), "<TMP>"), (os.path.realpath(tmpdir()), "<TMP>"), (ROOT, "<ROOT>"),
                            (os.path.realpath(ROOT), "<ROOT>")}, key=lambda kv: -len(kv[0])):
        text = text.replace(old, new)
    return text


def pin_digest(obj):
    text = json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:12]


def compared_unit_rows():
    """{unit or variant: [id, status, must, category, reason] of each row, verdict and score} of every corpus unit and
    every evals, frontmatter, A3 and A4 variant, the units whose rows the differential suite compared at the P7 commit."""
    units = [(rel(u), u) for u in corpus_units()]
    units += [("variant:evals:" + n, evals_variant_unit(n)) for n in EVALS_VARIANTS]
    units += [("variant:frontmatter:" + n, frontmatter_variant_unit(n)) for n in FRONTMATTER_UNITS]
    units += [("variant:a3:" + n, a3_variant_unit(n)) for n in A3_VARIANTS]
    units += [("variant:a4:" + n, a4_variant_unit(n)) for n in A4_VARIANTS]
    out = {}
    for key, u in units:
        rep = eva.check(u)
        out[key] = {"rows": [[c["id"], c["status"], c["must"], c["category"], p7_norm(c["reason"])] for c in rep.checks],
                    "verdict": rep.verdict, "score": rep.score}
    return out


def mismatches(want, got):
    return sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))


class TestP7RowsAndMessages(unittest.TestCase):
    """The rows and the schema-backed function messages of every input the differential suite compared, pinned as
    digests of the values the P7 commit gave (P7_UNIT_ROWS, P7_ROW_IDS, P7_MESSAGES)."""

    def test_every_row_of_every_compared_unit_and_variant_is_its_p7_row(self):
        rows = compared_unit_rows()
        no_git = LR.git_root(ROOT) is None
        want_units = {**P7_UNIT_ROWS, **(P7_NO_GIT_UNIT_ROWS if no_git else {})}
        want_ids = {**P7_ROW_IDS, **(P7_NO_GIT_ROW_IDS if no_git else {})}
        got_units = {k: pin_digest(v) for k, v in rows.items()}
        got_ids = {rid: pin_digest({k: [r for r in v["rows"] if r[0] == rid] for k, v in rows.items()
                                    if any(r[0] == rid for r in v["rows"])})
                   for rid in {r[0] for v in rows.values() for r in v["rows"]}}
        units, ids = mismatches(want_units, got_units), mismatches(want_ids, got_ids)
        shown = ["%s: %s" % (k, [r for r in rows[k]["rows"] if r[0] in ids] if ids and k in rows else rows.get(k))
                 for k in units]
        self.assertEqual((units, ids), ([], []), "units and row ids whose rows differ from the P7 commit, with the head "
                                                 "rows of those ids:\n" + "\n".join(shown))

    def test_every_message_of_every_function_case_is_its_p7_message(self):
        got = {case: [p7_norm(m) for m in errs] for case, errs in schema_problems_of_every_input()}
        wrong = mismatches(P7_MESSAGES, {k: pin_digest(v) for k, v in got.items()})
        self.assertEqual(wrong, [], "cases whose messages differ from the P7 commit, as the head gives them:\n" +
                         "\n".join("%s: %r" % (k, got.get(k)) for k in wrong))


class TestCorpusRows(unittest.TestCase):
    def test_status_and_reason_never_disagree(self):
        units = corpus_units() + [evals_variant_unit(n) for n in EVALS_VARIANTS] + \
            [frontmatter_variant_unit(n) for n in FRONTMATTER_UNITS] + [a3_variant_unit(n) for n in A3_VARIANTS] + \
            [a4_variant_unit(n) for n in A4_VARIANTS]
        self.assertGreater(len(units), 300)
        split = {u: ids for u in units for ids in [[c["id"] for c in eva.check(u).checks if DISAGREE in c["reason"]]] if ids}
        self.assertEqual(split, {})

    def test_each_shape_row_appears_once_and_comes_from_the_rule(self):
        u = os.path.join(ROOT, "golden", "skills", "csv-cleaner")
        r = eva.check(u)
        ids = [c["id"] for c in r.checks]
        self.assertEqual(sorted(set(ids)), sorted(ids))
        self.assertLessEqual(set(SHAPE_IDS), set(ids))
        family =[rule.id for f in eva.FAMILY_FILES for rule in RR.default_bundle().rules
                  if rule.file == f and "skill" in rule.applies_to]
        self.assertEqual(ids, family)
        self.assertTrue(all("rule" in c for c in r.checks))
        self.assertEqual([c["id"] for c in eva.check_skill(u).checks], ids)

    def test_a_non_default_bundle_without_the_rules_keeps_the_built_in_rows(self):
        selftest = RR.load_bundle(os.path.join(ROOT, "tests", "rules", "_selftest"))
        u = os.path.join(ROOT, "golden", "skills", "csv-cleaner")
        ids = [c["id"] for c in eva.check(u, selftest).checks]
        for rid in RULE_FILES:
            self.assertEqual(ids.count(rid), 1, ids)
        self.assertEqual(ids, [c["id"] for c in eva.check(u).checks] + ["selftest-name-matches-folder", "bundle-non-default"])

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


class TestGateFunctions(unittest.TestCase):
    def test_the_folder_cause_reaches_the_gate_functions_as_at_p7(self):
        u = os.path.join(ROOT, "tests", "rules", "_selftest", "selftest-name-matches-folder", "fail", "bad-name")
        status = eva.parity_status(u)
        s = eva.english_scan(u)
        got = {"parity_status": status, "review_items": eva.review_items(u), "english_violations": eva.english_violations(u),
               "parity_violations": eva.parity_violations(u), "parity_line": eva.parity_line(status),
               "english_scan": (s.kind, s.groups, s.review), "codex_policy": eva.codex_policy(u),
               "english_eval_problems": eva.english_eval_problems(u)}
        self.assertEqual(got, FOLDER_CAUSE_FUNCTIONS)
        self.assertEqual(status["opencode"]["status"], "fail")
        self.assertTrue(any(v.startswith("[opencode]") and "must equal the folder name" in v
                            for v in got["parity_violations"]), got["parity_violations"])

    def test_the_opencode_complete_branch_passes_with_the_target_patched(self):
        from engine.primitives import parity as PR
        v2 = os.path.join(tmpdir(), "complete-v2", "code-scout")
        write(os.path.join(v2, "AGENT.md"), P.AGENT_MD)
        for i, s in enumerate((P.S1, P.S2), 1):
            write(os.path.join(v2, "scenarios", "%02d.json" % i), json.dumps(s, indent=1))
        P.write_wrappers(v2)
        with mock.patch.dict(PR.target("opencode")["permissions"], {"complete": True}):
            rep = eva.check(v2)
            status = eva.parity_status(v2)["opencode"]["status"]
        rows = {c["id"]: (c["status"], c["must"], c["category"]) for c in rep.checks}
        reason = next(c["reason"] for c in rep.checks if c["id"] == "parity-permissions-opencode")
        self.assertEqual(rows["parity-permissions-opencode"][0], "pass")
        self.assertEqual(reason, "[opencode] wrapper permissions ['read', 'search'] match the AGENT.md capabilities (compared "
                                 "by capability class, because OpenCode permission keys are not tool names)")
        self.assertEqual({k: v for k, v in rows.items() if k not in B1_AGENT_ROWS and k not in B2_ROWS},
                         OPENCODE_COMPLETE_ROWS)
        self.assertEqual(status, OPENCODE_COMPLETE_STATUS)


class TestMessageRules(unittest.TestCase):
    def test_every_variant_is_rejected_as_its_table_says(self):
        tables = {"evals_errors": EVALS_VARIANTS, "_field_type_errors": FIELD_VARIANTS, "scenario_errors": SCENARIO_VARIANTS,
                  "load_transcript": TRANSCRIPT_VARIANTS, "load_triggers": TRIGGER_VARIANTS, "check_object": CHECK_VARIANTS}
        wrong = {}
        for case, problems in schema_problems_of_every_input():
            fn, name = case.split(":", 1)
            if name in tables[fn] and tables[fn][name][-1] != bool(problems):
                wrong[case] = problems
        self.assertEqual(wrong, {})

    def test_every_message_rule_fires_in_some_input_and_every_message_is_text(self):
        fired = set()
        for _, problems in schema_problems_of_every_input():
            for m in problems:
                self.assertIsInstance(m, str)
                m.encode("utf-8")
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


class TestAscii(unittest.TestCase):
    def test_every_file_a2_and_a4_add_and_this_file_are_ascii(self):
        files = [os.path.join(ROOT, *p.split("/")) for p in (
            "tests/test_policy_schemas.py", "rules/skill-structure.rules.json", "rules/skill-description.rules.json",
            "rules/agent-structure.rules.json", "rules/data/description.json", "engine/primitives/heading_present.py",
            "engine/primitives/description_heuristics.py", "engine/primitives/local_refs.py", "tests/test_rule_structure.py")]
        files += [os.path.join(ROOT, "schemas", n) for n in SCHEMAS] + \
                 [os.path.join(ROOT, "rules", n) for n in RULE_FILES.values()] + \
                 [os.path.join(ROOT, "policy", n) for n in POLICY_FILES]
        for rid in list(RULE_FILES) + list(A4_RULE_FILES):
            for dp, dn, fn in os.walk(os.path.join(ROOT, "tests", "rules", rid)):
                files += [os.path.join(dp, n) for n in fn]
        self.assertGreater(len(files), 170)
        for f in files:
            with open(f, "rb") as fh:
                self.assertTrue(fh.read().isascii(), rel(f))


class TestOracleGone(unittest.TestCase):
    """Unit A6 deleted the legacy bodies, the oracle helpers, the operand shim and the four plan-named functions."""

    def test_eva_and_skill_eval_hold_no_legacy_body_shim_or_plan_named_function(self):
        self.assertEqual([n for n in dir(eva) if n.startswith("_legacy_")], [])
        self.assertEqual([n for n in dir(skill_eval) if n.startswith("_legacy_")], [])
        for name in ("add_english_checks", "add_parity_checks_skill", "add_parity_checks_agent", "agent_wrapper_states",
                     "OPERANDS", "__getattr__", "SKILL_MAX_BYTES", "MIN_EVALS", "DESCRIPTION_MIN"):
            self.assertFalse(hasattr(eva, name), name)
        for name in ("_check_problems", "_csv_rows_problems"):
            self.assertFalse(hasattr(skill_eval, name), name)

    def test_the_engine_sources_name_none_of_them(self):
        for fname in ("eva.py", "skill_eval.py"):
            with open(os.path.join(ROOT, fname), encoding="utf-8") as f:
                src = f.read()
            for word in ("_legacy_", "def add_parity_checks_agent", "def add_english_checks", "OPERANDS",
                         "_check_problems", "_csv_rows_problems", "test_differential", "tests/differential"):
                self.assertFalse(word in src, (fname, word))

    def test_the_differential_suite_and_its_oracle_folder_are_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "tests", "test_differential.py")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "tests", "differential")))


# The values the legacy bodies gave at the P7 commit 53edf4c, captured before A6 deleted them.
RULE_ROW_CATEGORY_MUST = {'evals-valid': ('evaluation', True), 'field-types': ('structure', True)}
DEEP_VERDICTS = {'deep-id': 'FAIL', 'deep-query': 'PASS', 'plain': 'PASS'}
DEEP_PROBLEMS = {'deep-id': ['evals.json: evals[0].id: integer required'],
                 'deep-query': ['trigger_evals.json[0]: {"query": str, "should_trigger": bool} required',
                                'trigger_evals.json: need 10 should-trigger + 10 should-not, got 9 + 10']}
DEEP_SPECS = {'deep-id': {'cases': [],
                          'claude_python_script': None,
                          'description': 'Cleans one CSV file for the differential suite. Use when a test needs a '
                                         'valid skill unit.',
                          'skill_name': 'deep-id',
                          'triggers': [{'query': 'dedupe rows in file 0.csv', 'should_trigger': True},
                                       {'query': 'dedupe rows in file 1.csv', 'should_trigger': True},
                                       {'query': 'dedupe rows in file 2.csv', 'should_trigger': True},
                                       {'query': 'dedupe rows in file 3.csv', 'should_trigger': True},
                                       {'query': 'dedupe rows in file 4.csv', 'should_trigger': True},
                                       {'query': 'dedupe rows in file 5.csv', 'should_trigger': True},
                                       {'query': 'dedupe rows in file 6.csv', 'should_trigger': True},
                                       {'query': 'dedupe rows in file 7.csv', 'should_trigger': True},
                                       {'query': 'dedupe rows in file 8.csv', 'should_trigger': True},
                                       {'query': 'dedupe rows in file 9.csv', 'should_trigger': True},
                                       {'query': 'draw a chart of file 0', 'should_trigger': False},
                                       {'query': 'draw a chart of file 1', 'should_trigger': False},
                                       {'query': 'draw a chart of file 2', 'should_trigger': False},
                                       {'query': 'draw a chart of file 3', 'should_trigger': False},
                                       {'query': 'draw a chart of file 4', 'should_trigger': False},
                                       {'query': 'draw a chart of file 5', 'should_trigger': False},
                                       {'query': 'draw a chart of file 6', 'should_trigger': False},
                                       {'query': 'draw a chart of file 7', 'should_trigger': False},
                                       {'query': 'draw a chart of file 8', 'should_trigger': False},
                                       {'query': 'draw a chart of file 9', 'should_trigger': False}]},
              'deep-query': {'cases': [{'expected_output': 'A cleaned copy.',
                                        'files': ['evals/files/a.csv'],
                                        'id': 1,
                                        'items': [{'check': {'pattern': 'done', 'target': 'final', 'type': 'regex'},
                                                   'id': '1.0',
                                                   'layer': 'static',
                                                   'text': 'check 0'},
                                                  {'check': {'pattern': 'error', 'type': 'not_regex'},
                                                   'id': '1.1',
                                                   'layer': 'static',
                                                   'text': 'check 1'},
                                                  {'check': {'input_regex': 'clean',
                                                             'max': 3,
                                                             'min': 1,
                                                             'tool': 'Bash',
                                                             'type': 'tool_call'},
                                                   'id': '1.2',
                                                   'layer': 'static',
                                                   'text': 'check 2'},
                                                  {'check': {'expected': True, 'type': 'skill_triggered'},
                                                   'id': '1.3',
                                                   'layer': 'static',
                                                   'text': 'check 3'},
                                                  {'check': {'path': 'out/a_clean.csv', 'type': 'file_exists'},
                                                   'id': '1.4',
                                                   'layer': 'static',
                                                   'text': 'check 4'},
                                                  {'check': {'fixture': 'evals/files/a.csv',
                                                             'path': 'out/a_clean.csv',
                                                             'type': 'file_equals_fixture'},
                                                   'id': '1.5',
                                                   'layer': 'static',
                                                   'text': 'check 5'},
                                                  {'check': {'data_rows': 2,
                                                             'fixture': 'evals/files/a.csv',
                                                             'path': 'out/a_clean.csv',
                                                             'type': 'csv_cleaned'},
                                                   'id': '1.6',
                                                   'layer': 'static',
                                                   'text': 'check 6'},
                                                  {'check': {'max': 1,
                                                             'min': 1,
                                                             'normalize': 'strip',
                                                             'path': 'out/a_clean.csv',
                                                             'rows': [['1', 'x']],
                                                             'type': 'csv_rows',
                                                             'unique': True},
                                                   'id': '1.7',
                                                   'layer': 'static',
                                                   'text': 'check 7'}],
                                        'name': 'case-one',
                                        'pressure': None,
                                        'prompt': 'Clean evals/files/a.csv into out/a_clean.csv.'},
                                       {'expected_output': 'Nothing changes.',
                                        'files': [],
                                        'id': 2,
                                        'items': [{'check': None,
                                                   'id': '2.0',
                                                   'layer': 'llm',
                                                   'text': 'no file is written'}],
                                        'name': 'clean-nothing',
                                        'pressure': None,
                                        'prompt': 'Clean nothing.'},
                                       {'expected_output': 'A short explanation.',
                                        'files': [],
                                        'id': 3,
                                        'items': [{'check': None,
                                                   'id': '3.0',
                                                   'layer': 'llm',
                                                   'text': 'mentions duplicates'},
                                                  {'check': None, 'id': '3.1', 'layer': 'llm', 'text': 'graded'}],
                                        'name': 'explain-the-cleaner',
                                        'pressure': None,
                                        'prompt': 'Explain the cleaner.'}],
                             'claude_python_script': None,
                             'description': 'Cleans one CSV file for the differential suite. Use when a test needs a '
                                            'valid skill unit.',
                             'skill_name': 'deep-query',
                             'triggers': [{'query': 'dedupe rows in file 1.csv', 'should_trigger': True},
                                          {'query': 'dedupe rows in file 2.csv', 'should_trigger': True},
                                          {'query': 'dedupe rows in file 3.csv', 'should_trigger': True},
                                          {'query': 'dedupe rows in file 4.csv', 'should_trigger': True},
                                          {'query': 'dedupe rows in file 5.csv', 'should_trigger': True},
                                          {'query': 'dedupe rows in file 6.csv', 'should_trigger': True},
                                          {'query': 'dedupe rows in file 7.csv', 'should_trigger': True},
                                          {'query': 'dedupe rows in file 8.csv', 'should_trigger': True},
                                          {'query': 'dedupe rows in file 9.csv', 'should_trigger': True},
                                          {'query': 'draw a chart of file 0', 'should_trigger': False},
                                          {'query': 'draw a chart of file 1', 'should_trigger': False},
                                          {'query': 'draw a chart of file 2', 'should_trigger': False},
                                          {'query': 'draw a chart of file 3', 'should_trigger': False},
                                          {'query': 'draw a chart of file 4', 'should_trigger': False},
                                          {'query': 'draw a chart of file 5', 'should_trigger': False},
                                          {'query': 'draw a chart of file 6', 'should_trigger': False},
                                          {'query': 'draw a chart of file 7', 'should_trigger': False},
                                          {'query': 'draw a chart of file 8', 'should_trigger': False},
                                          {'query': 'draw a chart of file 9', 'should_trigger': False}]}}
LOAD_SET_PROBLEMS = {'claude-python-script-invalid': ['evals.json.claude_python_script: existing relative '
                                                      'scripts/*.py path required'],
                     'name-unsafe': ["evals[id=1].name: 'Case One' must match ^[a-z0-9][a-z0-9._-]{0,63}$ without "
                                     "'..' (one path component)"],
                     'pressure-invalid': ['evals[id=2].pressure: {"kinds": [str, ...], "context": str} required'],
                     'static-layer-without-check': ['evals[id=3].assertions[0]: static layer needs a machine check '
                                                    'object {"text", "layer": "static", "check": {"type": ...}} '
                                                    "('mentions duplicates')"]}
FOLDER_CAUSE_FUNCTIONS = {'codex_policy': ([], None, []),
                          'english_eval_problems': ([], []),
                          'english_scan': ('skill',
                                           [('english-skill-md', 'SKILL.md (frontmatter and body)', [], 1),
                                            ('english-bundled-files',
                                             'installed files other than SKILL.md and agents/openai.yaml (0 binary '
                                             'skipped)',
                                             [], None),
                                            ('english-evals', 'evals/evals.json, evals/trigger_evals.json', [], None),
                                            ('english-tool-files', 'agents/openai.yaml', [], None)],
                                           []),
                          'english_violations': [],
                          'parity_line': 'claude=pass codex=pass opencode=fail hermes=pass',
                          'parity_status': {'claude': {'problems': [], 'status': 'pass', 'unverified': []},
                                            'codex': {'problems': [], 'status': 'pass', 'unverified': []},
                                            'hermes': {'problems': [], 'status': 'pass', 'unverified': []},
                                            'opencode': {'problems': ['parity-skill-opencode: [opencode] name '
                                                                      "'other-name' must equal the folder name "
                                                                      "'bad-name' exactly"],
                                                         'status': 'fail',
                                                         'unverified': []}},
                          'parity_violations': ["[opencode] name 'other-name' must equal the folder name 'bad-name' "
                                                'exactly'],
                          'review_items': []}
OPENCODE_COMPLETE_ROWS = {'description-present': ('pass', True, 'single-task'),
                          'english-agent-md': ('pass', True, 'language'),
                          'english-output-matchers': ('n/a', False, 'language'),
                          'english-scenarios': ('pass', True, 'language'),
                          'english-wrappers': ('pass', True, 'language'),
                          'frontmatter': ('pass', True, 'structure'),
                          'goal-present': ('pass', True, 'goal'),
                          'memory-section': ('pass', False, 'context'),
                          'name-format': ('pass', True, 'single-task'),
                          'name-matches-folder': ('pass', True, 'structure'),
                          'parity-agent-name': ('pass', True, 'parity'),
                          'parity-wrapper-claude': ('pass', True, 'parity'),
                          'parity-wrapper-codex': ('pass', True, 'parity'),
                          'parity-wrapper-hermes': ('pass', True, 'parity'),
                          'parity-wrapper-opencode': ('pass', True, 'parity'),
                          'tools-documented': ('pass', False, 'tool-use')}
OPENCODE_COMPLETE_STATUS = 'pass'
DESCRIPTION_DATA = {'AMBIGUOUS_TASK_VERBS': ['benchmark', 'chart', 'code', 'design', 'draft', 'email', 'estimate',
                                             'export', 'forecast', 'format', 'import', 'index', 'label', 'list',
                                             'map', 'order', 'patch', 'plan', 'post', 'print', 'process', 'profile',
                                             'query', 'rank', 'record', 'release', 'report', 'review', 'run',
                                             'schedule', 'score', 'search', 'tag', 'test', 'track', 'update'],
                    'DESC_LIST_MARKERS': '-*\u2022(',
                    'DESC_NEGATION_RE': "\\b(?:not|never|cannot|avoid(?:s|ing)?|skip(?:s|ping)?|without)\\b|n['\u2019]t\\b",
                    'DESC_PERSON_RE': "(?<![\\w/.(-])(?-i:I)(?![\\w/)-]|\\.\\w)|(?<![\\w/.-])(?-i:i)(?=\\s|['\u2019])|(?<![\\w/.-])(?:me|my|mine|we|our|ours|you|your|yours|yourself|yourselves)(?![\\w/-])|(?<![\\w/.-])(?-i:us|Us|uS)(?![\\w/-])|(?<!\\w)(?:\uc800\ub294|\uc81c\uac00|\uc800\ud76c|\ub098\ub294|\ub0b4\uac00|\uc6b0\ub9ac\ub294|\uc6b0\ub9ac\uac00|\uc6b0\ub9ac\uc758|\ub2f9\uc2e0|\ub108\ub294|\ub124\uac00|\uc5ec\ub7ec\ubd84)",
                    'DESC_READ_RE': 'read\\s+this\\s+skill\\s+(?:before|when|whenever)\\b([^;]*)',
                    'DESC_SENTENCE_RE': '(?<=[.!?])\\s+|\\n+',
                    'DESC_THIS_SKILL_OBJ_RE': '\\b(?:use|using|invoke|invoking|trigger|triggering|read|reading)\\s+this\\s+skill\\s+(?:when|whenever)\\b([^;]*)',
                    'DESC_THIS_SKILL_SUBJ_RE': 'this\\s+skill\\s+(?:(?:should|must|can|may|will|could|might|shall)\\s+be|is)\\s+(?:used|invoked|triggered|read)\\s+(?:when|whenever)\\b([^;]*)',
                    'DESC_TIGHT_STOP_RE': '[.!?](?=[A-Z])',
                    'DESC_USE_START_RE': '(?:use|used|invoke|trigger)(?:\\s+(?:this|the|it))?(?:\\s+(?:skill|tool))?(?:\\s+[A-Za-z]+ly)?(?:\\s+(?:for|to)\\b[^;]{1,150}?)?\\s+(?:when|whenever)\\b([^;]*)',
                    'DESC_USE_VERB': '(?:used|invoked|triggered|read)',
                    'DESC_WHEN_MIN_WORDS': 2,
                    'DESC_WHEN_VAGUE': ['a', 'an', 'and', 'anything', 'applicable', 'appropriate', 'are', 'as',
                                        'asked', 'be', 'desired', 'for', 'helpful', 'if', 'in', 'is', 'it',
                                        'necessary', 'need', 'needed', 'of', 'or', 'possible', 'relevant',
                                        'requested', 'required', 'so', 'someone', 'something', 'suitable', 'that',
                                        'the', 'there', 'this', 'to', 'useful', 'user', 'users', 'wanted', 'you'],
                    'DESC_WORD_RE': "[A-Za-z][A-Za-z'-]*",
                    'HEADING_WORDS': {'example': ['example'],
                                      'inputs': ['input'],
                                      'memory': ['memory', 'handoff', 'context'],
                                      'outputs': ['output'],
                                      'tools': ['tool']},
                    'MULTI_PURPOSE_RE': '\\b(?:and also|as well as|plus)\\b',
                    'QUOTED_RE': '\\"[^\\"\\n]*\\"|\u201c[^\u201d\\n]*\u201d|\u2018[^\u2019\\n]*\u2019|`[^`\\n]*`|(?<![\\w])\'[^\'\\n]*\'(?![\\w])',
                    'ROMAN_I_BEFORE': '(?:phase|part|type|chapter|level|stage|class|tier|section|volume|vol\\.?|appendix|war|step|grade|act|book|round|schedule|article|title|figure|table)',
                    'SINGLE_PURPOSE_MAX_VERBS': 2,
                    'TASK_EXCLUSION_RE': "\\b(?:do\\s+not|don['\u2019]t|never|not)\\s+(?:use|trigger|invoke)\\b|\\bnot\\s+for\\b",
                    'TASK_JOINERS': ['also', 'and', 'or', 'plus', 'then', 'to'],
                    'TASK_JOINER_PHRASE_RE': '\\bas\\s+well\\s+as\\b',
                    'TASK_TOKEN_RE': '[A-Za-z]+(?:[-/][A-Za-z]+)*|[.;:!?,()&]',
                    'TASK_VERBS': ['add', 'aggregate', 'analyze', 'annotate', 'archive', 'audit', 'automate',
                                   'backup', 'benchmark', 'build', 'bundle', 'calculate', 'chart', 'check', 'clean',
                                   'classify', 'code', 'combine', 'commit', 'compare', 'compile', 'compose',
                                   'compress', 'compute', 'configure', 'convert', 'copy', 'count', 'crawl', 'create',
                                   'crop', 'debug', 'decrypt', 'delete', 'deploy', 'design', 'detect', 'diff',
                                   'draft', 'draw', 'edit', 'email', 'encrypt', 'enrich', 'estimate', 'evaluate',
                                   'export', 'extract', 'fetch', 'fill', 'filter', 'find', 'fix', 'forecast',
                                   'format', 'generate', 'grade', 'import', 'index', 'inspect', 'install', 'label',
                                   'lint', 'list', 'load', 'manage', 'map', 'merge', 'migrate', 'minify', 'modify',
                                   'monitor', 'move', 'normalize', 'notify', 'optimize', 'order', 'organize', 'parse',
                                   'patch', 'plan', 'plot', 'post', 'predict', 'print', 'process', 'profile',
                                   'proofread', 'publish', 'query', 'rank', 'read', 'rebase', 'record', 'refactor',
                                   'release', 'remove', 'rename', 'render', 'reorder', 'repair', 'replace', 'report',
                                   'resize', 'restore', 'review', 'rewrite', 'rotate', 'run', 'sanitize', 'scan',
                                   'scrape', 'schedule', 'score', 'search', 'send', 'share', 'sign', 'simulate',
                                   'sort', 'split', 'summarize', 'synthesize', 'tag', 'test', 'track', 'train',
                                   'transcribe', 'transform', 'translate', 'trim', 'update', 'upload', 'validate',
                                   'verify', 'visualize', 'watermark', 'write']}
A3_OPERANDS = {'COMPATIBILITY_MAX': 500,
               'DESCRIPTION_MAX': 1024,
               'DESCRIPTION_MIN': 20,
               'HOST_SKILL_FIELDS': ['argument-hint', 'disable-model-invocation'],
               'MIN_EVALS': 3,
               'REF_TOC_LINES': 100,
               'SKILL_FIELDS': ['name', 'description', 'license', 'compatibility', 'metadata', 'allowed-tools'],
               'SKILL_MAX_BYTES': 20000,
               'SKILL_MAX_LINES': 500,
               'TOC_PATTERN': 'contents|\\btoc\\b'}
UNREADABLE_FRONTMATTER_ROW = [('fail', True,
                               "YAML frontmatter between --- lines: cannot read SKILL.md: 'utf-8' codec can't decode "
                               'byte 0xff in position 24: invalid start byte')]

# Digests of the rows and messages the P7 commit 53edf4c gave, captured on the trunk export by a scratch script.
# Outside a git work tree refs-exist reads '../outside.md' as escaping the repository, so two digests differ.
P7_UNIT_ROWS = {'bad/agents/no-context-agent': '6d70de5e787b', 'bad/agents/vague-agent': 'f019c98f59e1',
    'bad/skills/DoEverything': 'a6804891be37', 'bad/skills/bad-version-skill': 'bb68fa134c21',
    'bad/skills/no-io-skill': '4d7fce9b9faf', 'bad/skills/wrong-folder-name': '8961d418f8aa',
    'golden/agents/code-reviewer': 'fc882a1d3862', 'golden/agents/research-assistant': '301517451429',
    'golden/skills/csv-cleaner': 'b50664d6b7f9', 'golden/skills/korean-proofreader': '3c90270c72fb',
    'golden/skills/pdf-summarizer': '0b561148d0d0', 'tests/fixtures/agents/code-reviewer-v2': 'a04792ea4674',
    'tests/fixtures/agents/research-assistant-v2': 'c4d41d339544',
    'tests/fixtures/skills/csv-cleaner-en': 'a679d882d208',
    'tests/rules/_selftest/selftest-name-matches-folder/fail/bad-name': 'b0383a2f881e',
    'tests/rules/_selftest/selftest-name-matches-folder/pass/good-name': '3582e10400b9',
    'tests/rules/agent-frontmatter-v2/fail/v1-agent': '5a468d601f06',
    'tests/rules/agent-frontmatter-v2/pass/v2-agent': '5ab9b3ded9fe',
    'tests/rules/compatibility-length/fail/long-compatibility': 'f615f88d4a08',
    'tests/rules/compatibility-length/na/plain-skill': '5aa3d529d64b',
    'tests/rules/compatibility-length/pass/with-compatibility': 'ab5496a7b6a4',
    'tests/rules/description-length/fail/long-description': '90970540def3',
    'tests/rules/description-length/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/description-min-length/fail/short-description': '0fa98a28f777',
    'tests/rules/description-min-length/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/description-no-xml/fail/angle-description': '0db989d9901c',
    'tests/rules/description-no-xml/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/description-pov/fail/first-person': '6bf0fd4a15e5',
    'tests/rules/description-pov/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/description-present/fail/no-description': '57ae15939ed3',
    'tests/rules/description-present/fail/no-description-agent': '2a4aaf15257d',
    'tests/rules/description-present/pass/plain-agent': 'bff0536472d7',
    'tests/rules/description-present/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/description-when/fail/no-trigger': '5a427f230e62',
    'tests/rules/description-when/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/english-agent-md/fail/escaped-agent': 'a56bfc94d321',
    'tests/rules/english-agent-md/pass/plain-agent': '894bcfde4d02',
    'tests/rules/english-bundled-files/fail/json-asset': '22c98747c68b',
    'tests/rules/english-bundled-files/na/only-skill-md': '419daf707b85',
    'tests/rules/english-bundled-files/pass/with-reference': '16d1a6a9b7ad',
    'tests/rules/english-evals/fail/hangul-evals': '16966a3ee8f1',
    'tests/rules/english-evals/na/no-evals': 'eeefe2698f0c',
    'tests/rules/english-evals/pass/english-evals': 'c34e3be2994b',
    'tests/rules/english-output-matchers/na/plain-skill': 'c8fed4ad8628',
    'tests/rules/english-output-matchers/review/matcher-skill': 'c2df6d713d14',
    'tests/rules/english-scenarios/fail/hangul-scenario': 'eeb41239b010',
    'tests/rules/english-scenarios/na/no-scenarios': 'fb181fe737a7',
    'tests/rules/english-scenarios/pass/plain-agent': '894bcfde4d02',
    'tests/rules/english-skill-md/fail/escaped-skill': 'ab34d607354c',
    'tests/rules/english-skill-md/pass/plain-skill': 'c8fed4ad8628',
    'tests/rules/english-tool-files/fail/escaped-yaml': '9912310f672e',
    'tests/rules/english-tool-files/na/no-yaml': 'fbb39e04bf79',
    'tests/rules/english-tool-files/pass/plain-yaml': '6e3082c56b57',
    'tests/rules/english-wrappers/fail/escaped-wrapper': 'd32dc35af2c0',
    'tests/rules/english-wrappers/na/no-wrappers': '946ef1cfa381',
    'tests/rules/english-wrappers/pass/plain-agent': '894bcfde4d02',
    'tests/rules/evals-count/fail/no-evals': 'c2dff2ca2ea1', 'tests/rules/evals-count/pass/three-evals': '3c638a349615',
    'tests/rules/evals-valid/fail/missing-id': 'd24480265237',
    'tests/rules/evals-valid/pass/good-evals': '6b5b3902513e',
    'tests/rules/example-present/fail/no-section': '1f8f634013ec',
    'tests/rules/example-present/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/field-types/fail/unquoted-yes': '6c2f29004536',
    'tests/rules/field-types/pass/quoted-name': 'd5514b35acad',
    'tests/rules/frontmatter-fields/fail/extra-field': 'f2e5e5fe469c',
    'tests/rules/frontmatter-fields/pass/host-field': '8347958b8fdd',
    'tests/rules/frontmatter/fail/no-frontmatter': 'd92e648f3c6f',
    'tests/rules/frontmatter/fail/no-frontmatter-agent': 'd30bacfefbeb',
    'tests/rules/frontmatter/pass/plain-agent': 'bff0536472d7',
    'tests/rules/frontmatter/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/goal-present/fail/short-goal': 'dcf0f37cca16',
    'tests/rules/goal-present/pass/plain-agent': 'bff0536472d7',
    'tests/rules/inputs-declared/fail/no-section': '783c0a314323',
    'tests/rules/inputs-declared/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/memory-section/fail/no-memory-section': 'e7162e70f8bf',
    'tests/rules/memory-section/pass/plain-agent': 'bff0536472d7',
    'tests/rules/multi-turn-scenario/fail/single-turn': 'bbd1f1f70724',
    'tests/rules/multi-turn-scenario/pass/plain-agent': 'a2dd1546dcbf',
    'tests/rules/name-format/fail/Bad-Name': '5701f40b7c78', 'tests/rules/name-format/pass/plain-skill': 'c8fed4ad8628',
    'tests/rules/name-matches-folder/fail/folder-agent': 'fd91e953a501',
    'tests/rules/name-matches-folder/pass/plain-agent': '894bcfde4d02',
    'tests/rules/name-reserved/fail/claude-helper': 'f5d1455ee6b8',
    'tests/rules/name-reserved/pass/plain-skill': 'c8fed4ad8628',
    'tests/rules/outputs-declared/fail/no-section': '33e489fa3619',
    'tests/rules/outputs-declared/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/parity-agent-name/fail/Bad_Agent': '690eb370c438',
    'tests/rules/parity-agent-name/pass/plain-agent': '894bcfde4d02',
    'tests/rules/parity-invocation-control/fail/claude-only': 'f9d1b5eccdc9',
    'tests/rules/parity-invocation-control/na/plain-skill': 'c8fed4ad8628',
    'tests/rules/parity-invocation-control/pass/both-controls': '537de2ac3298',
    'tests/rules/parity-invocation-hermes/na/plain-skill': 'c8fed4ad8628',
    'tests/rules/parity-invocation-hermes/review/both-controls': '537de2ac3298',
    'tests/rules/parity-permissions-claude/fail/claude-grants-more': '773853a5ba78',
    'tests/rules/parity-permissions-claude/na/no-claude': 'f2c5e4e76e7e',
    'tests/rules/parity-permissions-claude/pass/plain-agent': '561a6f6ed777',
    'tests/rules/parity-permissions-codex/fail/unmapped-tool': 'dda71dcfd8f3',
    'tests/rules/parity-permissions-codex/na/no-codex': 'f36c95c818d4',
    'tests/rules/parity-permissions-codex/pass/all-caps-agent': '1dfe49133f95',
    'tests/rules/parity-permissions-codex/review/plain-agent': '561a6f6ed777',
    'tests/rules/parity-permissions-hermes/fail/plain-agent': '894bcfde4d02',
    'tests/rules/parity-permissions-opencode/fail/opencode-ask': '2f3c147f29f6',
    'tests/rules/parity-permissions-opencode/na/no-opencode': '37302d950d0f',
    'tests/rules/parity-permissions-opencode/review/plain-agent': '561a6f6ed777',
    'tests/rules/parity-skill-claude/fail/no-description': '7bd82944ad11',
    'tests/rules/parity-skill-claude/pass/plain-skill': 'c8fed4ad8628',
    'tests/rules/parity-skill-codex/fail/quoted-policy': '1ea433ec511a',
    'tests/rules/parity-skill-codex/pass/plain-yaml': '6e3082c56b57',
    'tests/rules/parity-skill-codex/review/extra-field': '545610a24dc0',
    'tests/rules/parity-skill-hermes/fail/hermes-mapping': '6b170e3b0d74',
    'tests/rules/parity-skill-hermes/pass/plain-skill': 'c8fed4ad8628',
    'tests/rules/parity-skill-opencode/fail/typed-metadata': '4bb408fea1c4',
    'tests/rules/parity-skill-opencode/pass/plain-skill': 'c8fed4ad8628',
    'tests/rules/parity-wrapper-claude/fail/no-claude': '9a0c53da3a1b',
    'tests/rules/parity-wrapper-claude/pass/plain-agent': '894bcfde4d02',
    'tests/rules/parity-wrapper-codex/fail/no-codex': '00403c64d4aa',
    'tests/rules/parity-wrapper-codex/pass/plain-agent': '894bcfde4d02',
    'tests/rules/parity-wrapper-hermes/fail/no-hermes': '8754349de71c',
    'tests/rules/parity-wrapper-hermes/pass/plain-agent': '894bcfde4d02',
    'tests/rules/parity-wrapper-opencode/fail/no-opencode': 'bb5a7b110fd5',
    'tests/rules/parity-wrapper-opencode/pass/plain-agent': '894bcfde4d02',
    'tests/rules/reference-toc/fail/long-without-toc': 'a458cb5ac647',
    'tests/rules/reference-toc/na/plain-skill': '5aa3d529d64b',
    'tests/rules/reference-toc/pass/long-with-toc': '7cb7bd88c810',
    'tests/rules/refs-exist/fail/missing-ref': '8dcd6742bd51',
    'tests/rules/refs-exist/pass/existing-ref': '4ba53c3b37e8',
    'tests/rules/refs-one-level/fail/chained': '1ca598198796',
    'tests/rules/refs-one-level/pass/one-level': '0c3d71739c71',
    'tests/rules/refs-path-depth/fail/deep-ref': '1fe42b25b3ee',
    'tests/rules/refs-path-depth/pass/shallow-ref': 'ae126d1c63aa',
    'tests/rules/refs-relative/fail/outside-ref': '298167205782',
    'tests/rules/refs-relative/pass/relative-ref': 'b62a94620591',
    'tests/rules/scenarios-expected/fail/no-outcome': '19e033a3c8d8',
    'tests/rules/scenarios-expected/pass/plain-agent': 'a2dd1546dcbf',
    'tests/rules/scenarios-present/fail/one-scenario': '38aee7da42eb',
    'tests/rules/scenarios-present/pass/plain-agent': 'a2dd1546dcbf',
    'tests/rules/scenarios-valid/fail/no-turns': '4d94968bb3b2',
    'tests/rules/scenarios-valid/pass/plain-agent': 'a2dd1546dcbf',
    'tests/rules/single-purpose/fail/many-tasks': '1be5fa03d2c4',
    'tests/rules/single-purpose/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/size-limit/fail/long-skill': '3cc0dc866a75', 'tests/rules/size-limit/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/success-measurable/fail/vague-criteria': '0bcf427f7846',
    'tests/rules/success-measurable/pass/plain-agent': 'a2dd1546dcbf',
    'tests/rules/tool-metadata-generated/fail/stale-skill': '3e9eb4a769bf',
    'tests/rules/tool-metadata-generated/na/plain-skill': 'c8fed4ad8628',
    'tests/rules/tool-metadata-generated/pass/quiet-skill': '6f70a8495022',
    'tests/rules/tools-declared/fail/no-tools': '1040dd4ee47d',
    'tests/rules/tools-declared/pass/plain-agent': 'a2dd1546dcbf',
    'tests/rules/tools-documented/fail/no-tools-section': 'b7daf1ab5af3',
    'tests/rules/tools-documented/pass/plain-agent': 'bff0536472d7',
    'tests/rules/tools-subset/fail/undeclared-tool': 'c55254bd920e',
    'tests/rules/tools-subset/pass/plain-agent': 'a2dd1546dcbf',
    'tests/rules/version-format/fail/bad-version': '652ce08a1be6',
    'tests/rules/version-format/na/no-version': '06242694437d',
    'tests/rules/version-format/pass/plain-skill': '5aa3d529d64b',
    'tests/rules/wrappers-generated/fail/bare-agent': '74d74a55f892',
    'tests/rules/wrappers-generated/na/old-agent': '8c277903d765',
    'tests/rules/wrappers-generated/pass/scout-reader': '8526b09aacb2', 'variant:a3:agent-all-caps': 'bf19f8954382',
    'variant:a3:agent-bad-name': '9f1b435a730d', 'variant:a3:agent-claude-body-empty': 'd8fafb464437',
    'variant:a3:agent-claude-description-differs': '04d58761df59',
    'variant:a3:agent-claude-escaped-frontmatter': '7619041d9d45', 'variant:a3:agent-claude-extra-key': '7784679b2331',
    'variant:a3:agent-claude-model-empty': '38a614dac6dc', 'variant:a3:agent-claude-name-differs': 'af97277a85ed',
    'variant:a3:agent-claude-no-tools': 'd3efce7c5385', 'variant:a3:agent-claude-outside': '65457312dbad',
    'variant:a3:agent-claude-unmapped-tool': '2b0487356574', 'variant:a3:agent-claude-unparsable': '2ae5495cde25',
    'variant:a3:agent-codex-duplicate': '1c29ba89c825', 'variant:a3:agent-codex-model-empty': '3424909eaac3',
    'variant:a3:agent-codex-no-instructions': 'bbab11dc4bca', 'variant:a3:agent-codex-optional-keys': '7784679b2331',
    'variant:a3:agent-codex-parse-error': '62443d78b2b9', 'variant:a3:agent-codex-sandbox': '3f6a4faac029',
    'variant:a3:agent-codex-undecodable': '51719a806240', 'variant:a3:agent-codex-unknown-key': '244623834897',
    'variant:a3:agent-complete': '7784679b2331', 'variant:a3:agent-escaped-toml': '199d04a1ab35',
    'variant:a3:agent-grants-less': 'c7b30fe1c59b', 'variant:a3:agent-grants-more': '714910db2498',
    'variant:a3:agent-hermes-config-empty': 'fa0f61be04cd', 'variant:a3:agent-hermes-config-ok': '7784679b2331',
    'variant:a3:agent-hermes-config-outside': '93aaff779797', 'variant:a3:agent-hermes-description': 'f864e54d9cc7',
    'variant:a3:agent-hermes-empty': 'd0f19ce901f2', 'variant:a3:agent-hermes-no-name': '01de437831c6',
    'variant:a3:agent-hermes-undecodable': '74b1f8659203', 'variant:a3:agent-ko-agent-md': '34f77e75359f',
    'variant:a3:agent-ko-matcher': '2e4152d81d45', 'variant:a3:agent-ko-scenario': '45ee3efddd2f',
    'variant:a3:agent-ko-scenario-name': 'ac5f42d0d9c4', 'variant:a3:agent-ko-wrapper': '2e36230193ff',
    'variant:a3:agent-ko-wrapper-name': 'f2f994c66e5c', 'variant:a3:agent-md-not-utf8': 'f8a4f01d08f0',
    'variant:a3:agent-no-name': 'bcf3d44425c0', 'variant:a3:agent-no-wrappers': '1f2b0c2e53db',
    'variant:a3:agent-opencode-ask': '1de5a70e1e7b', 'variant:a3:agent-opencode-bad-value': '0308afe33ac7',
    'variant:a3:agent-opencode-escaped-frontmatter': '9d4e05a0e22b',
    'variant:a3:agent-opencode-external-allow': 'dd900949e8a2', 'variant:a3:agent-opencode-extra-key': '7784679b2331',
    'variant:a3:agent-opencode-key-missing': 'fc420967b7fb', 'variant:a3:agent-opencode-mode': '8dc91cfde84f',
    'variant:a3:agent-opencode-model-empty': '60827e5732b3', 'variant:a3:agent-opencode-name-differs': '256dfb647c78',
    'variant:a3:agent-opencode-name-empty': '71c4c6a3e829', 'variant:a3:agent-opencode-no-description': 'cdce0cceb198',
    'variant:a3:agent-opencode-no-permission': '810ab27c4232', 'variant:a3:agent-opencode-unknown-key': '873a35c82ecb',
    'variant:a3:agent-opencode-unparsable': 'a5d114e6ac32', 'variant:a3:agent-scenario-link-outside': '35dd392ddaa2',
    'variant:a3:agent-skip-claude': 'ef14401e8749', 'variant:a3:agent-skip-codex': '0fe658d7eb0a',
    'variant:a3:agent-skip-hermes': '0ec5bee02fa6', 'variant:a3:agent-skip-opencode': 'b541131233eb',
    'variant:a3:agent-unmapped-tool': '5e9bca7ed8da', 'variant:a3:agent-wrapper-binary': '679493193b31',
    'variant:a3:agent-write-web': 'c514a79dba1d', 'variant:a3:skill-angle': '05ee6179b0c1',
    'variant:a3:skill-autoinvoke-bad': '9e2fabd3bb3a', 'variant:a3:skill-base': 'bba62b0b1c4e',
    'variant:a3:skill-binary': '2c52c4bc3f5e', 'variant:a3:skill-cherokee-yi': '596b30875fea',
    'variant:a3:skill-codex-empty': '3f57d73db6b2', 'variant:a3:skill-codex-folder': '4101dc7052fd',
    'variant:a3:skill-codex-markers': 'ae8c3aa06d4a', 'variant:a3:skill-codex-not-mapping': '630d6c0beddc',
    'variant:a3:skill-codex-other-policy': '89b367bbf229', 'variant:a3:skill-codex-parse-error': '23f426c73375',
    'variant:a3:skill-codex-quoted': 'a244e3eba009', 'variant:a3:skill-codex-true': '3f57d73db6b2',
    'variant:a3:skill-codex-undecodable': 'd84e7a9e908b', 'variant:a3:skill-codex-unverified': 'bd2a21c8a530',
    'variant:a3:skill-disabled': '1e3271b923b3', 'variant:a3:skill-disabled-both': 'b2809839bae7',
    'variant:a3:skill-disabled-codex-only': '271d4de67759', 'variant:a3:skill-disabled-false': '51d62e0d8ea1',
    'variant:a3:skill-disabled-opencode-only': 'a7509636008a', 'variant:a3:skill-disabled-string': 'aea710150743',
    'variant:a3:skill-evals-invalid-json': 'ec0c9293ed73', 'variant:a3:skill-hermes-flow': 'a335adc523fd',
    'variant:a3:skill-hermes-list': '9021791a8332', 'variant:a3:skill-hermes-mapping': 'f05b226e2535',
    'variant:a3:skill-hermes-string': '6cc282923199', 'variant:a3:skill-json-bundled': '1ef48b3e5db8',
    'variant:a3:skill-ko-body': '5645ea2652f9', 'variant:a3:skill-ko-bundled': '467d99739e8e',
    'variant:a3:skill-ko-evals': '33ed0262ed2f', 'variant:a3:skill-ko-file-name': '796c6fe8d7ee',
    'variant:a3:skill-ko-frontmatter-escape': 'b1dfb0cf20e1', 'variant:a3:skill-ko-matcher': 'c72274ba2570',
    'variant:a3:skill-ko-tool-file': '12ed98fcba2f', 'variant:a3:skill-link-cycle': '97e9ab2844f8',
    'variant:a3:skill-link-outside': '6096c29d3d55', 'variant:a3:skill-long-description': 'fdf84a4f9ebe',
    'variant:a3:skill-long-name': '8cb5803f7d16', 'variant:a3:skill-many-findings': '43d4eb8a49e9',
    'variant:a3:skill-metadata-list': 'f887816b7893', 'variant:a3:skill-metadata-not-mapping': '937e2229b9da',
    'variant:a3:skill-metadata-typed': '4fbc0c865f43', 'variant:a3:skill-name-64': '249776ea1fa1',
    'variant:a3:skill-name-64-raw-65-nfkc': '957ca2069965', 'variant:a3:skill-name-65': 'db38be377bf6',
    'variant:a3:skill-name-format': 'a163604dd7c0', 'variant:a3:skill-nfkc-name': '88cad7c2eb6c',
    'variant:a3:skill-no-description': 'e52bd001c588', 'variant:a3:skill-no-frontmatter': '84131ae8855a',
    'variant:a3:skill-opencode-regex': '5005e524b69c', 'variant:a3:skill-reserved-word': '050acbc8f064',
    'variant:a3:skill-toml-bundled': 'e390b159175f', 'variant:a3:skill-undecodable': '84e91b14bb26',
    'variant:a4:a-base': '7784679b2331', 'variant:a4:a-context-heading': '7784679b2331',
    'variant:a4:a-criterion-string': '1a47a4285e65', 'variant:a4:a-desc-missing': 'e793d09ba9f9',
    'variant:a4:a-every-criterion-kind': '442be1605363', 'variant:a4:a-invalid-json': '20e6a08219b2',
    'variant:a4:a-no-criteria': '72697cc1f2b6', 'variant:a4:a-no-expected': '7bea15bffc6b',
    'variant:a4:a-no-frontmatter': '75489dcf5811', 'variant:a4:a-no-memory': '87f80275de6e',
    'variant:a4:a-no-scenarios': '5a76c107515b', 'variant:a4:a-no-tools': 'b1adf0142c36',
    'variant:a4:a-no-tools-section': '62ae7b0ffafb', 'variant:a4:a-one-scenario': '960e81801bdd',
    'variant:a4:a-padded-goal': '8e6fd29d3934', 'variant:a4:a-scenario-list': '45591c4701c6',
    'variant:a4:a-scenario-undecodable': '2a6d9c4b0c97', 'variant:a4:a-short-goal': '8e6fd29d3934',
    'variant:a4:a-single-turn': 'ffbb5c627a8e', 'variant:a4:a-tools-string': '07af290c71fa',
    'variant:a4:a-undeclared-tool': '82f3e0a17fa2', 'variant:a4:a-vague-criteria': '8aaa02ae0d44',
    'variant:a4:ko-heading-example': 'cd49fd59c539', 'variant:a4:ko-heading-handoff': '703f14e4d4b5',
    'variant:a4:ko-heading-input': '914dc661602f', 'variant:a4:ko-heading-memory': 'd1fb15d4bc85',
    'variant:a4:ko-heading-output': 'dd7e9f447b7b', 'variant:a4:ko-heading-tool': '7c54f37a6416',
    'variant:a4:ko-multi-and-also': '831fc5818a33', 'variant:a4:ko-multi-not-only': 'f38d20e0e791',
    'variant:a4:ko-toc': '1029a0619df9', 'variant:a4:s-add-on': 'e1457d791236', 'variant:a4:s-base': 'bba62b0b1c4e',
    'variant:a4:s-compat-blank': '16b29d44a98c', 'variant:a4:s-compat-empty': '564c3905f2b8',
    'variant:a4:s-compat-list': 'd8a06bfbff68', 'variant:a4:s-compat-long': 'd1212769e6ae',
    'variant:a4:s-compat-ok': 'bc80ca9b742b', 'variant:a4:s-compat-padded': '057a4681fcdc',
    'variant:a4:s-compat-padded-max': 'a8e654af3f52', 'variant:a4:s-desc-blank': '9a6b1b93aaf6',
    'variant:a4:s-desc-empty': '68a0cb9d441a', 'variant:a4:s-desc-gt': '127ceaa39dc3',
    'variant:a4:s-desc-list': 'e2e821407f54', 'variant:a4:s-desc-long': 'ca3761bf60e8',
    'variant:a4:s-desc-missing': 'e52bd001c588', 'variant:a4:s-desc-negated-when': '1060ccf9e54c',
    'variant:a4:s-desc-no-when': 'a28e402e3567', 'variant:a4:s-desc-padded-max': 'be3819257aa7',
    'variant:a4:s-desc-padded-min': 'a91e60d4a5ff', 'variant:a4:s-desc-padded-over': 'ac7e6866c082',
    'variant:a4:s-desc-padded-short': '5916d8ecbdc1', 'variant:a4:s-desc-pov': 'a4289c4b4e7c',
    'variant:a4:s-desc-pov-ko': '9678cea42a46', 'variant:a4:s-desc-quoted-pov': '00f088dc9f30',
    'variant:a4:s-desc-quoted-when': 'edf8af46041f', 'variant:a4:s-desc-read-before': '25aa9c7b6e5f',
    'variant:a4:s-desc-roman': '8499a30fe6c3', 'variant:a4:s-desc-short': '87518e958ef9',
    'variant:a4:s-desc-subject': 'f65f7c177f27', 'variant:a4:s-desc-tight-stop': 'abe0fff64d93',
    'variant:a4:s-evals-none': 'da4a03f5dbe5', 'variant:a4:s-evals-two': '48ca8c577c7a',
    'variant:a4:s-extra-field': '7b644deac449', 'variant:a4:s-fenced-example': 'f81542d43562',
    'variant:a4:s-fm-block-headings': 'c77dfff97cbb', 'variant:a4:s-fm-comment-headings': '13a8df8f5970',
    'variant:a4:s-fm-compat-ref': '745473a81a59', 'variant:a4:s-frontmatter-error': '6c1249b06e3d',
    'variant:a4:s-host-field': '05e600f2368e', 'variant:a4:s-many-verbs': '85be5ee8ae41',
    'variant:a4:s-no-inputs': 'ab2493a05772', 'variant:a4:s-no-outputs': 'a5e53e091ee0',
    'variant:a4:s-ref-absolute': '1a96e194f3bd', 'variant:a4:s-ref-backslash': 'f96bb17f9372',
    'variant:a4:s-ref-chain': '209440dafa5e', 'variant:a4:s-ref-chain-back': '8456667fd455',
    'variant:a4:s-ref-deep': '68a0abf2647d', 'variant:a4:s-ref-escaping': '0cc8bc65b24b',
    'variant:a4:s-ref-fenced-missing': '0f4152032a9c', 'variant:a4:s-ref-fenced-run': '7bb6623a6f8c',
    'variant:a4:s-ref-glob': '6eb707a65f4a', 'variant:a4:s-ref-ko-particle': 'fb0c39f519f5',
    'variant:a4:s-ref-missing': '72356057b44b', 'variant:a4:s-ref-ok': '8456667fd455',
    'variant:a4:s-ref-repo-and-missing': '9cc8ecfa8f72', 'variant:a4:s-ref-repo-root': '0bacebac6ce2',
    'variant:a4:s-ref-up': '5daece208e91', 'variant:a4:s-size-bytes': 'f0eac3b7d423',
    'variant:a4:s-size-bytes-at': 'cc4676d8215e', 'variant:a4:s-size-bytes-over': 'c9e90c34d9ba',
    'variant:a4:s-size-lines': 'f123d551b104', 'variant:a4:s-size-lines-at': '7e675fdfb232',
    'variant:a4:s-size-lines-over': '0bd8d46f2adb', 'variant:a4:s-toc-lines-at': '34af57100e52',
    'variant:a4:s-toc-lines-over': '407876868f28', 'variant:a4:s-toc-missing': '407876868f28',
    'variant:a4:s-toc-ok': '902069423372', 'variant:a4:s-toc-title-only': '407876868f28',
    'variant:a4:s-toc-word': '902069423372', 'variant:a4:s-verb-after-to': '09a960659c47',
    'variant:a4:s-version-bad': '858c811d161b', 'variant:a4:s-version-list': 'f887816b7893',
    'variant:a4:s-version-none': '8212128f2816', 'variant:evals:assertion-blank': '764119b3ebb8',
    'variant:evals:assertion-number': '764119b3ebb8', 'variant:evals:assertion-object-check-list': '764119b3ebb8',
    'variant:evals:assertion-object-layer': '764119b3ebb8', 'variant:evals:assertion-object-layer-deep': '764119b3ebb8',
    'variant:evals:assertion-object-no-check': 'bba62b0b1c4e',
    'variant:evals:assertion-object-no-layer': '764119b3ebb8', 'variant:evals:assertion-object-no-text': '764119b3ebb8',
    'variant:evals:assertion-object-text-blank': '764119b3ebb8', 'variant:evals:assertions-empty': '310b7661024b',
    'variant:evals:assertions-string': '2fb4766c2d8e', 'variant:evals:base': 'bba62b0b1c4e',
    'variant:evals:case-not-object': '6b0932cb3ad7', 'variant:evals:duplicate-ids': '147796901ebc',
    'variant:evals:duplicate-ids-two': 'fa9114d5ff3d', 'variant:evals:evals-empty': '50a3eb779232',
    'variant:evals:evals-missing': 'b83886a73821', 'variant:evals:evals-not-list': 'd0bbad2e4293',
    'variant:evals:expectations-empty': 'bed446855b99', 'variant:evals:expected-output-blank': '9a720c968958',
    'variant:evals:expected-output-missing': '064418dfdad3', 'variant:evals:expected-output-null': '73cfc0d2db9e',
    'variant:evals:extra-keys': 'bba62b0b1c4e', 'variant:evals:files-absolute': '89d3c3aa4646',
    'variant:evals:files-backslash': 'ec2209d57f32', 'variant:evals:files-drive': '0da74d82d052',
    'variant:evals:files-empty-list': 'bba62b0b1c4e', 'variant:evals:files-entry-blank': 'b1f57bc5b26f',
    'variant:evals:files-entry-number': '8bf29e115423', 'variant:evals:files-folder': '9cce5b53fa5b',
    'variant:evals:files-home': 'd48d19e49c6e', 'variant:evals:files-missing': 'c0e8e77378fd',
    'variant:evals:files-not-list': '8bf29e115423', 'variant:evals:files-outside': '931ac668ee47',
    'variant:evals:files-outside-deep': 'aae440852c6e', 'variant:evals:files-two-missing': '9f9cecee007d',
    'variant:evals:id-big': 'bba62b0b1c4e', 'variant:evals:id-bool': '579ef5ef64e8',
    'variant:evals:id-deep': '579ef5ef64e8', 'variant:evals:id-deep-in-every-case': '7ea7a4181fb2',
    'variant:evals:id-float': '579ef5ef64e8', 'variant:evals:id-missing': 'ad0d66e9a8fb',
    'variant:evals:id-string': '579ef5ef64e8', 'variant:evals:invalid-json': '214b87a97ecf',
    'variant:evals:layer-deep': '0f5aa0bc6b8c', 'variant:evals:layer-null': 'c2ba02172c0b',
    'variant:evals:layer-uppercase': '24146d98fccc', 'variant:evals:missing-file': 'da4a03f5dbe5',
    'variant:evals:name-dotdot': '44f54734c6c7', 'variant:evals:name-number': 'f4b42e998580',
    'variant:evals:name-too-long': '176b3ee99d6d', 'variant:evals:name-trailing-newline': 'bba62b0b1c4e',
    'variant:evals:name-uppercase': 'eb7c11922896', 'variant:evals:not-utf8': '5e2af6101620',
    'variant:evals:prompt-blank': '65f66cd96d8d', 'variant:evals:prompt-empty': '65f66cd96d8d',
    'variant:evals:prompt-missing': 'fb023abbbb0b', 'variant:evals:skill-name-mismatch': 'a9fc85d63d89',
    'variant:evals:skill-name-missing': '0711e250efbd', 'variant:evals:top-level-list': 'ae2e0bc9118e',
    'variant:evals:two-errors': '9e25738c3c2a', 'variant:frontmatter:fm-argument-hint-typed': 'bcf78020593e',
    'variant:frontmatter:fm-broken-frontmatter': 'f5e9f097f032',
    'variant:frontmatter:fm-compatibility-typed': '52502a183810', 'variant:frontmatter:fm-dmi-quoted': '7769836ec136',
    'variant:frontmatter:fm-dmi-true': '7e19ec457c60', 'variant:frontmatter:fm-license-blank': '951ef88af9d0',
    'variant:frontmatter:fm-unknown-field': '78c829ae4ca2'}
P7_ROW_IDS = {'agent-frontmatter-v2': 'fb0807e8a307', 'compatibility-length': '3d9f0bf83d8b',
    'description-length': '0f11efa6ba63', 'description-min-length': '47a2c6432dd6',
    'description-no-xml': '97a8392c00d8', 'description-pov': 'b7798b92062c', 'description-present': 'e4f5710b9cf6',
    'description-when': '048f81cd2efe', 'english-agent-md': '3997f0320faa', 'english-bundled-files': '03537f84520f',
    'english-evals': '721689bc4efb', 'english-output-matchers': '787f81a177f7', 'english-scenarios': '979ff0cf8fd7',
    'english-skill-md': '9c294609faae', 'english-tool-files': '32aae90c9edd', 'english-wrappers': 'd8827dc75617',
    'evals-count': 'c4a853a24539', 'evals-valid': '244aae9831cb', 'example-present': '464e7451f946',
    'field-types': 'e60089f705a4', 'frontmatter': '01315e7f728b', 'frontmatter-fields': '9ef9b1d7fe16',
    'goal-present': '1393824f0a0e', 'inputs-declared': '73503f7ee030', 'memory-section': '7b504a19a0cb',
    'multi-turn-scenario': '5d4c823abfc7', 'name-format': '8984dc1ba763', 'name-matches-folder': '5ffcaa5c66a9',
    'name-reserved': '3269fedbdd79', 'outputs-declared': 'ad1c73f08ab8', 'parity-agent-name': 'a03b272d4abf',
    'parity-invocation-control': '1fd07b735f75', 'parity-invocation-hermes': '876193fc9cdd',
    'parity-permissions-claude': '4aa250ee108d', 'parity-permissions-codex': 'd6566e3f7141',
    'parity-permissions-hermes': 'a4ae9c8aa4c5', 'parity-permissions-opencode': '6076bc1e2e82',
    'parity-skill-claude': '1ebf21ed58e6', 'parity-skill-codex': 'c644b38dceac', 'parity-skill-hermes': 'e7d558e68149',
    'parity-skill-opencode': 'e725736658c3', 'parity-wrapper-claude': '134bc4f46797',
    'parity-wrapper-codex': '915af22a17fd', 'parity-wrapper-hermes': 'd4112eea4df6',
    'parity-wrapper-opencode': '47017e559102', 'reference-toc': '210be3a376d3', 'refs-exist': '6a043f7cffe1',
    'refs-one-level': '35c635520510', 'refs-path-depth': '28db17ad3d22', 'refs-relative': '24f376eceef0',
    'scenarios-expected': '5d0f74505552', 'scenarios-present': 'a3085280d5d0', 'scenarios-valid': 'f38adad63810',
    'single-purpose': '71be2942637e', 'size-limit': '6324ebb4b63d', 'success-measurable': 'a4343faacd2f',
    'tool-metadata-generated': '3e78d62253e2', 'tools-declared': 'ac081a2d75ca', 'tools-documented': '68458b4908e1',
    'tools-subset': '2ddee6662cb7', 'version-format': 'aea18184a570', 'wrappers-generated': 'fb381fdf31d8'}
P7_MESSAGES = {'_field_type_errors:allowed-tools-empty': '76b38fe73114',
    '_field_type_errors:argument-hint-empty': '6099070198ed', '_field_type_errors:argument-hint-list': 'b4252524e3ab',
    '_field_type_errors:argument-hint-typed': 'dc11f66235b2',
    '_field_type_errors:bad/skills/DoEverything': '4f53cda18c2b',
    '_field_type_errors:bad/skills/bad-version-skill': '4f53cda18c2b',
    '_field_type_errors:bad/skills/no-io-skill': '4f53cda18c2b',
    '_field_type_errors:bad/skills/wrong-folder-name': '4f53cda18c2b',
    '_field_type_errors:compatibility-blank': 'd9ec6c58e4df', '_field_type_errors:compatibility-typed': '19050a00ad05',
    '_field_type_errors:description-blank': '265a1e3d4347', '_field_type_errors:description-typed': '9581a4f93675',
    '_field_type_errors:dmi-list': 'c7d6b7b376bb', '_field_type_errors:dmi-quoted': '4f8b2d8bb428',
    '_field_type_errors:dmi-word': '076bb7b3eb9f', '_field_type_errors:golden/skills/csv-cleaner': '4f53cda18c2b',
    '_field_type_errors:golden/skills/korean-proofreader': '4f53cda18c2b',
    '_field_type_errors:golden/skills/pdf-summarizer': '4f53cda18c2b',
    '_field_type_errors:license-dict': 'e1140c9fbe39', '_field_type_errors:metadata-empty': '54a33d06d20e',
    '_field_type_errors:metadata-string': 'eab92bb2508d', '_field_type_errors:metadata-value-list': 'd1a860875e05',
    '_field_type_errors:metadata-value-null': 'f6a2ae5bbd5b', '_field_type_errors:name-empty': 'b3fdc8543012',
    '_field_type_errors:name-list': '7c56093aae6e', '_field_type_errors:name-null': '1c37c143f6d7',
    '_field_type_errors:name-typed': '8b5e25a303ea', '_field_type_errors:ok-argument-hint': '4f53cda18c2b',
    '_field_type_errors:ok-dmi-false-caps': '4f53cda18c2b', '_field_type_errors:ok-dmi-true': '4f53cda18c2b',
    '_field_type_errors:ok-empty': '4f53cda18c2b', '_field_type_errors:ok-license-typed': '4f53cda18c2b',
    '_field_type_errors:ok-metadata-empty-map': '4f53cda18c2b',
    '_field_type_errors:ok-metadata-typed-value': '4f53cda18c2b', '_field_type_errors:ok-strings': '4f53cda18c2b',
    '_field_type_errors:tests/fixtures/skills/csv-cleaner-en': '4f53cda18c2b',
    '_field_type_errors:tests/rules/_selftest/selftest-name-matches-folder/fail/bad-name': '4f53cda18c2b',
    '_field_type_errors:tests/rules/_selftest/selftest-name-matches-folder/pass/good-name': '4f53cda18c2b',
    '_field_type_errors:tests/rules/compatibility-length/fail/long-compatibility': '4f53cda18c2b',
    '_field_type_errors:tests/rules/compatibility-length/na/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/compatibility-length/pass/with-compatibility': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-length/fail/long-description': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-length/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-min-length/fail/short-description': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-min-length/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-no-xml/fail/angle-description': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-no-xml/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-pov/fail/first-person': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-pov/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-present/fail/no-description': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-present/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-when/fail/no-trigger': '4f53cda18c2b',
    '_field_type_errors:tests/rules/description-when/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-bundled-files/fail/json-asset': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-bundled-files/na/only-skill-md': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-bundled-files/pass/with-reference': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-evals/fail/hangul-evals': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-evals/na/no-evals': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-evals/pass/english-evals': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-output-matchers/na/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-output-matchers/review/matcher-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-skill-md/fail/escaped-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-skill-md/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-tool-files/fail/escaped-yaml': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-tool-files/na/no-yaml': '4f53cda18c2b',
    '_field_type_errors:tests/rules/english-tool-files/pass/plain-yaml': '4f53cda18c2b',
    '_field_type_errors:tests/rules/evals-count/fail/no-evals': '4f53cda18c2b',
    '_field_type_errors:tests/rules/evals-count/pass/three-evals': '4f53cda18c2b',
    '_field_type_errors:tests/rules/evals-valid/fail/missing-id': '4f53cda18c2b',
    '_field_type_errors:tests/rules/evals-valid/pass/good-evals': '4f53cda18c2b',
    '_field_type_errors:tests/rules/example-present/fail/no-section': '4f53cda18c2b',
    '_field_type_errors:tests/rules/example-present/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/field-types/fail/unquoted-yes': '19050a00ad05',
    '_field_type_errors:tests/rules/field-types/pass/quoted-name': '4f53cda18c2b',
    '_field_type_errors:tests/rules/frontmatter-fields/fail/extra-field': '4f53cda18c2b',
    '_field_type_errors:tests/rules/frontmatter-fields/pass/host-field': '4f53cda18c2b',
    '_field_type_errors:tests/rules/frontmatter/fail/no-frontmatter': '4f53cda18c2b',
    '_field_type_errors:tests/rules/frontmatter/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/inputs-declared/fail/no-section': '4f53cda18c2b',
    '_field_type_errors:tests/rules/inputs-declared/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/name-format/fail/Bad-Name': '4f53cda18c2b',
    '_field_type_errors:tests/rules/name-format/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/name-reserved/fail/claude-helper': '4f53cda18c2b',
    '_field_type_errors:tests/rules/name-reserved/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/outputs-declared/fail/no-section': '4f53cda18c2b',
    '_field_type_errors:tests/rules/outputs-declared/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-invocation-control/fail/claude-only': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-invocation-control/na/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-invocation-control/pass/both-controls': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-invocation-hermes/na/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-invocation-hermes/review/both-controls': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-skill-claude/fail/no-description': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-skill-claude/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-skill-codex/fail/quoted-policy': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-skill-codex/pass/plain-yaml': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-skill-codex/review/extra-field': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-skill-hermes/fail/hermes-mapping': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-skill-hermes/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-skill-opencode/fail/typed-metadata': '4f53cda18c2b',
    '_field_type_errors:tests/rules/parity-skill-opencode/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/reference-toc/fail/long-without-toc': '4f53cda18c2b',
    '_field_type_errors:tests/rules/reference-toc/na/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/reference-toc/pass/long-with-toc': '4f53cda18c2b',
    '_field_type_errors:tests/rules/refs-exist/fail/missing-ref': '4f53cda18c2b',
    '_field_type_errors:tests/rules/refs-exist/pass/existing-ref': '4f53cda18c2b',
    '_field_type_errors:tests/rules/refs-one-level/fail/chained': '4f53cda18c2b',
    '_field_type_errors:tests/rules/refs-one-level/pass/one-level': '4f53cda18c2b',
    '_field_type_errors:tests/rules/refs-path-depth/fail/deep-ref': '4f53cda18c2b',
    '_field_type_errors:tests/rules/refs-path-depth/pass/shallow-ref': '4f53cda18c2b',
    '_field_type_errors:tests/rules/refs-relative/fail/outside-ref': '4f53cda18c2b',
    '_field_type_errors:tests/rules/refs-relative/pass/relative-ref': '4f53cda18c2b',
    '_field_type_errors:tests/rules/single-purpose/fail/many-tasks': '4f53cda18c2b',
    '_field_type_errors:tests/rules/single-purpose/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/size-limit/fail/long-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/size-limit/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/tool-metadata-generated/fail/stale-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/tool-metadata-generated/na/plain-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/tool-metadata-generated/pass/quiet-skill': '4f53cda18c2b',
    '_field_type_errors:tests/rules/version-format/fail/bad-version': '4f53cda18c2b',
    '_field_type_errors:tests/rules/version-format/na/no-version': '4f53cda18c2b',
    '_field_type_errors:tests/rules/version-format/pass/plain-skill': '4f53cda18c2b',
    '_field_type_errors:two-errors': 'fdc31259140b', 'check_object:csv-cleaned-data-rows-bool': '08853f11dc94',
    'check_object:csv-cleaned-data-rows-missing': '95ddb1aece9b',
    'check_object:csv-cleaned-data-rows-negative': '0d63704c9325',
    'check_object:csv-cleaned-fixture-absolute': '511ca2d355b0',
    'check_object:csv-rows-asserts-nothing': 'dcb020010ed8',
    'check_object:csv-rows-asserts-nothing-unique-false': 'bf9025497f05',
    'check_object:csv-rows-empty-rows-with-max': 'f674a38278de',
    'check_object:csv-rows-empty-rows-with-min': 'f674a38278de',
    'check_object:csv-rows-float-min-gt-max': '77d4305051c3', 'check_object:csv-rows-many': 'f41889154409',
    'check_object:csv-rows-max-float': 'f5241342cb2a', 'check_object:csv-rows-max-zero-without-min': '9544eac0f664',
    'check_object:csv-rows-min-deep': '77d4305051c3', 'check_object:csv-rows-min-greater-than-max': '70ddb64efc62',
    'check_object:csv-rows-min-negative': 'c585757728bf', 'check_object:csv-rows-min-string': '77d4305051c3',
    'check_object:csv-rows-min-without-rows': 'e5491eca06d8',
    'check_object:csv-rows-min-zero-without-max': 'c3274ff030fe', 'check_object:csv-rows-normalize-bad': '9f3a3ef77265',
    'check_object:csv-rows-normalize-deep': '9f3a3ef77265', 'check_object:csv-rows-normalize-null': '9f3a3ef77265',
    'check_object:csv-rows-path-dotdot': 'ddac0aab5dcc', 'check_object:csv-rows-path-missing': '877dbbcbae41',
    'check_object:csv-rows-path-surrogate': 'c31928017434',
    'check_object:csv-rows-rows-bad-unique-true': '27838ea62ffe',
    'check_object:csv-rows-rows-cell-number': '27838ea62ffe',
    'check_object:csv-rows-rows-cell-surrogate': 'b6d8096b908d', 'check_object:csv-rows-rows-empty': '6599b2998c90',
    'check_object:csv-rows-rows-empty-unique-false': '6599b2998c90',
    'check_object:csv-rows-rows-inner-not-list': '27838ea62ffe', 'check_object:csv-rows-rows-nested': '27838ea62ffe',
    'check_object:csv-rows-rows-not-list': '27838ea62ffe', 'check_object:csv-rows-two-unknown-keys': 'c28fa8ebeba0',
    'check_object:csv-rows-unique-deep': '493797d66450', 'check_object:csv-rows-unique-string': '0b6d30377163',
    'check_object:csv-rows-unknown-key': 'aa4e52338d2e', 'check_object:csv-rows-unknown-key-surrogate': '7209ac048893',
    'check_object:file-equals-fixture-missing': '9986770ae552',
    'check_object:file-equals-path-dotdot-end': 'ddac0aab5dcc',
    'check_object:file-exists-path-absolute': 'ddac0aab5dcc',
    'check_object:file-exists-path-backslash-dotdot': 'ddac0aab5dcc',
    'check_object:file-exists-path-dotdot': 'ddac0aab5dcc', 'check_object:file-exists-path-empty': '7dda7554d629',
    'check_object:file-exists-path-list': 'ab79ebc476e3', 'check_object:file-exists-path-missing': '877dbbcbae41',
    'check_object:not-object': 'd96c8fbc7ca0', 'check_object:ok-base-0': '4f53cda18c2b',
    'check_object:ok-base-1': '4f53cda18c2b', 'check_object:ok-base-2': '4f53cda18c2b',
    'check_object:ok-base-3': '4f53cda18c2b', 'check_object:ok-base-4': '4f53cda18c2b',
    'check_object:ok-base-5': '4f53cda18c2b', 'check_object:ok-base-6': '4f53cda18c2b',
    'check_object:ok-base-7': '4f53cda18c2b', 'check_object:ok-csv-rows-astral': '4f53cda18c2b',
    'check_object:ok-csv-rows-blank-line': '4f53cda18c2b', 'check_object:ok-csv-rows-empty-cell': '4f53cda18c2b',
    'check_object:ok-csv-rows-empty-unique': '4f53cda18c2b', 'check_object:ok-csv-rows-min0-max0': '4f53cda18c2b',
    'check_object:ok-csv-rows-min1': '4f53cda18c2b', 'check_object:ok-csv-rows-unique-only': '4f53cda18c2b',
    'check_object:ok-extra-keys-regex': '4f53cda18c2b', 'check_object:ok-input-regex-empty': '4f53cda18c2b',
    'check_object:ok-path-dotdot-newline': '4f53cda18c2b', 'check_object:ok-path-dots': '4f53cda18c2b',
    'check_object:ok-regex-empty-target-default': '4f53cda18c2b', 'check_object:regex-pattern-empty': 'b125ee479067',
    'check_object:regex-pattern-invalid': '41fcfeeb634e', 'check_object:regex-pattern-missing': '9051853ecfa3',
    'check_object:regex-pattern-number': 'a3434d679db4', 'check_object:regex-target-bad': '76a576f76256',
    'check_object:regex-target-deep': '76a576f76256', 'check_object:regex-target-null': '76a576f76256',
    'check_object:skill-triggered-expected-missing': '77b1421cf82b',
    'check_object:skill-triggered-expected-string': 'ba63eb8f607d',
    'check_object:tool-call-input-regex-invalid': '46f5429b1a7a',
    'check_object:tool-call-input-regex-number': '3fcb6b541448', 'check_object:tool-call-max-bool': 'f5241342cb2a',
    'check_object:tool-call-max-float': 'f5241342cb2a', 'check_object:tool-call-min-negative': 'c585757728bf',
    'check_object:tool-call-tool-empty': 'eca62be9e82e', 'check_object:tool-call-tool-missing': 'edd33feedef8',
    'check_object:type-deep': 'db442379c6c8', 'check_object:type-missing': '47b795e80c62',
    'check_object:type-number': 'db442379c6c8', 'check_object:type-unknown': 'db442379c6c8',
    'evals_errors:assertion-blank': 'fb53c5f3c46d', 'evals_errors:assertion-number': 'fb53c5f3c46d',
    'evals_errors:assertion-object-check-list': 'fb53c5f3c46d', 'evals_errors:assertion-object-layer': 'fb53c5f3c46d',
    'evals_errors:assertion-object-layer-deep': 'fb53c5f3c46d',
    'evals_errors:assertion-object-no-check': '4f53cda18c2b', 'evals_errors:assertion-object-no-layer': 'fb53c5f3c46d',
    'evals_errors:assertion-object-no-text': 'fb53c5f3c46d', 'evals_errors:assertion-object-text-blank': 'fb53c5f3c46d',
    'evals_errors:assertions-empty': '85d847c5037b', 'evals_errors:assertions-string': '0036765bd83c',
    'evals_errors:bad/skills/DoEverything': 'f784f8612e6b', 'evals_errors:bad/skills/bad-version-skill': '4f53cda18c2b',
    'evals_errors:bad/skills/no-io-skill': 'f784f8612e6b', 'evals_errors:bad/skills/wrong-folder-name': 'f784f8612e6b',
    'evals_errors:base': '4f53cda18c2b', 'evals_errors:case-not-object': 'bf35b435455d',
    'evals_errors:duplicate-ids': 'f4922a63c0f1', 'evals_errors:duplicate-ids-two': 'f4922a63c0f1',
    'evals_errors:evals-empty': '493df2db5393', 'evals_errors:evals-missing': '50c8029d5b31',
    'evals_errors:evals-not-list': '66f4d83878e5', 'evals_errors:expectations-empty': 'b49ca51641ad',
    'evals_errors:expected-output-blank': '4a1192ea3c1b', 'evals_errors:expected-output-missing': 'fa4008510851',
    'evals_errors:expected-output-null': 'f090a6333403', 'evals_errors:extra-keys': '4f53cda18c2b',
    'evals_errors:files-absolute': '53824d275c5b', 'evals_errors:files-backslash': '888f71b1b38c',
    'evals_errors:files-drive': 'd466e0cbaa9b', 'evals_errors:files-empty-list': '4f53cda18c2b',
    'evals_errors:files-entry-blank': '6e84db3c4dc3', 'evals_errors:files-entry-number': '18619db52825',
    'evals_errors:files-folder': 'ddbc105b3a79', 'evals_errors:files-home': '3f70d19bbb9c',
    'evals_errors:files-missing': 'b863d1fe60e8', 'evals_errors:files-not-list': '18619db52825',
    'evals_errors:files-outside': 'c7bc47cf8736', 'evals_errors:files-outside-deep': 'cd07aa1b4985',
    'evals_errors:files-two-missing': '3586cb7e9792', 'evals_errors:golden/skills/csv-cleaner': '4f53cda18c2b',
    'evals_errors:golden/skills/korean-proofreader': '4f53cda18c2b',
    'evals_errors:golden/skills/pdf-summarizer': '4f53cda18c2b', 'evals_errors:id-big': '4f53cda18c2b',
    'evals_errors:id-bool': 'f8b399c809c0', 'evals_errors:id-deep': 'f8b399c809c0',
    'evals_errors:id-deep-in-every-case': '3a50b28e1437', 'evals_errors:id-float': 'f8b399c809c0',
    'evals_errors:id-missing': '2d76c7842d9c', 'evals_errors:id-string': 'f8b399c809c0',
    'evals_errors:invalid-json': '80b2c126258a', 'evals_errors:layer-deep': 'a6788be2bea5',
    'evals_errors:layer-null': '568e5cba5e45', 'evals_errors:layer-uppercase': '0a43f7ec59f2',
    'evals_errors:missing-file': 'f784f8612e6b', 'evals_errors:name-dotdot': 'c34f56c56f54',
    'evals_errors:name-number': '4feb7b7d9785', 'evals_errors:name-too-long': '73d8ad3ac2a9',
    'evals_errors:name-trailing-newline': '4f53cda18c2b', 'evals_errors:name-uppercase': '4dcd5da1ffd1',
    'evals_errors:not-utf8': 'df2b279d3827', 'evals_errors:prompt-blank': 'e8952c7dc393',
    'evals_errors:prompt-empty': 'e8952c7dc393', 'evals_errors:prompt-missing': '397dfbb09a83',
    'evals_errors:skill-name-mismatch': '311865bd588f', 'evals_errors:skill-name-missing': '68d1a274b52b',
    'evals_errors:tests/fixtures/skills/csv-cleaner-en': '4f53cda18c2b',
    'evals_errors:tests/rules/_selftest/selftest-name-matches-folder/fail/bad-name': 'f784f8612e6b',
    'evals_errors:tests/rules/_selftest/selftest-name-matches-folder/pass/good-name': 'f784f8612e6b',
    'evals_errors:tests/rules/compatibility-length/fail/long-compatibility': 'f784f8612e6b',
    'evals_errors:tests/rules/compatibility-length/na/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/compatibility-length/pass/with-compatibility': 'f784f8612e6b',
    'evals_errors:tests/rules/description-length/fail/long-description': 'f784f8612e6b',
    'evals_errors:tests/rules/description-length/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/description-min-length/fail/short-description': 'f784f8612e6b',
    'evals_errors:tests/rules/description-min-length/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/description-no-xml/fail/angle-description': 'f784f8612e6b',
    'evals_errors:tests/rules/description-no-xml/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/description-pov/fail/first-person': 'f784f8612e6b',
    'evals_errors:tests/rules/description-pov/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/description-present/fail/no-description': 'f784f8612e6b',
    'evals_errors:tests/rules/description-present/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/description-when/fail/no-trigger': 'f784f8612e6b',
    'evals_errors:tests/rules/description-when/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/english-bundled-files/fail/json-asset': 'f784f8612e6b',
    'evals_errors:tests/rules/english-bundled-files/na/only-skill-md': 'f784f8612e6b',
    'evals_errors:tests/rules/english-bundled-files/pass/with-reference': 'f784f8612e6b',
    'evals_errors:tests/rules/english-evals/fail/hangul-evals': '4f53cda18c2b',
    'evals_errors:tests/rules/english-evals/na/no-evals': 'f784f8612e6b',
    'evals_errors:tests/rules/english-evals/pass/english-evals': '4f53cda18c2b',
    'evals_errors:tests/rules/english-output-matchers/na/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/english-output-matchers/review/matcher-skill': '2bd26be2652c',
    'evals_errors:tests/rules/english-skill-md/fail/escaped-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/english-skill-md/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/english-tool-files/fail/escaped-yaml': 'f784f8612e6b',
    'evals_errors:tests/rules/english-tool-files/na/no-yaml': 'f784f8612e6b',
    'evals_errors:tests/rules/english-tool-files/pass/plain-yaml': 'f784f8612e6b',
    'evals_errors:tests/rules/evals-count/fail/no-evals': 'f784f8612e6b',
    'evals_errors:tests/rules/evals-count/pass/three-evals': '4f53cda18c2b',
    'evals_errors:tests/rules/evals-valid/fail/missing-id': '2d76c7842d9c',
    'evals_errors:tests/rules/evals-valid/pass/good-evals': '4f53cda18c2b',
    'evals_errors:tests/rules/example-present/fail/no-section': 'f784f8612e6b',
    'evals_errors:tests/rules/example-present/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/field-types/fail/unquoted-yes': 'f784f8612e6b',
    'evals_errors:tests/rules/field-types/pass/quoted-name': 'f784f8612e6b',
    'evals_errors:tests/rules/frontmatter-fields/fail/extra-field': 'f784f8612e6b',
    'evals_errors:tests/rules/frontmatter-fields/pass/host-field': 'f784f8612e6b',
    'evals_errors:tests/rules/frontmatter/fail/no-frontmatter': 'f784f8612e6b',
    'evals_errors:tests/rules/frontmatter/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/inputs-declared/fail/no-section': 'f784f8612e6b',
    'evals_errors:tests/rules/inputs-declared/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/name-format/fail/Bad-Name': 'f784f8612e6b',
    'evals_errors:tests/rules/name-format/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/name-reserved/fail/claude-helper': 'f784f8612e6b',
    'evals_errors:tests/rules/name-reserved/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/outputs-declared/fail/no-section': 'f784f8612e6b',
    'evals_errors:tests/rules/outputs-declared/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-invocation-control/fail/claude-only': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-invocation-control/na/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-invocation-control/pass/both-controls': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-invocation-hermes/na/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-invocation-hermes/review/both-controls': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-skill-claude/fail/no-description': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-skill-claude/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-skill-codex/fail/quoted-policy': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-skill-codex/pass/plain-yaml': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-skill-codex/review/extra-field': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-skill-hermes/fail/hermes-mapping': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-skill-hermes/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-skill-opencode/fail/typed-metadata': 'f784f8612e6b',
    'evals_errors:tests/rules/parity-skill-opencode/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/reference-toc/fail/long-without-toc': 'f784f8612e6b',
    'evals_errors:tests/rules/reference-toc/na/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/reference-toc/pass/long-with-toc': 'f784f8612e6b',
    'evals_errors:tests/rules/refs-exist/fail/missing-ref': 'f784f8612e6b',
    'evals_errors:tests/rules/refs-exist/pass/existing-ref': 'f784f8612e6b',
    'evals_errors:tests/rules/refs-one-level/fail/chained': 'f784f8612e6b',
    'evals_errors:tests/rules/refs-one-level/pass/one-level': 'f784f8612e6b',
    'evals_errors:tests/rules/refs-path-depth/fail/deep-ref': 'f784f8612e6b',
    'evals_errors:tests/rules/refs-path-depth/pass/shallow-ref': 'f784f8612e6b',
    'evals_errors:tests/rules/refs-relative/fail/outside-ref': 'f784f8612e6b',
    'evals_errors:tests/rules/refs-relative/pass/relative-ref': 'f784f8612e6b',
    'evals_errors:tests/rules/single-purpose/fail/many-tasks': 'f784f8612e6b',
    'evals_errors:tests/rules/single-purpose/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/size-limit/fail/long-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/size-limit/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/tool-metadata-generated/fail/stale-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/tool-metadata-generated/na/plain-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/tool-metadata-generated/pass/quiet-skill': 'f784f8612e6b',
    'evals_errors:tests/rules/version-format/fail/bad-version': 'f784f8612e6b',
    'evals_errors:tests/rules/version-format/na/no-version': 'f784f8612e6b',
    'evals_errors:tests/rules/version-format/pass/plain-skill': 'f784f8612e6b',
    'evals_errors:top-level-list': 'ffccd6502b95', 'evals_errors:two-errors': '07c9748b8a86',
    'load_transcript:base': '4f53cda18c2b', 'load_transcript:extra-top-keys': '4f53cda18c2b',
    'load_transcript:final-empty': '4f53cda18c2b', 'load_transcript:final-missing': '288211febe04',
    'load_transcript:final-number': 'b92429c8d4c7', 'load_transcript:invalid-json': 'f33b372440a6',
    'load_transcript:missing-file': '74918413c67a', 'load_transcript:no-workspace-no-results': '4f53cda18c2b',
    'load_transcript:normalized-fields-bad': '84da33c85e36', 'load_transcript:not-object': 'a52ad47e4ecd',
    'load_transcript:not-utf8': '17394736ccf5', 'load_transcript:scenario-blank': '947fcde75113',
    'load_transcript:scenario-missing': '28a8f0d68e6d', 'load_transcript:tool-call-blank': 'ddccba92ee60',
    'load_transcript:tool-call-no-tool': '3c9dc2272778',
    'load_transcript:tool-call-normalized-fields-bad': 'a504ea9da103',
    'load_transcript:tool-call-number': '3c9dc2272778', 'load_transcript:tool-call-result-and-unknown': 'e891fc3944ca',
    'load_transcript:tool-call-result-number': '4865bacb8fdf', 'load_transcript:tool-call-tool-blank': '3c9dc2272778',
    'load_transcript:tool-call-unknown-fields': 'ab70f267846b', 'load_transcript:tool-calls-missing': '66e0edce510d',
    'load_transcript:tool-calls-object': '105ea9043685', 'load_transcript:tool-results-item-number': '99e71a0ffce3',
    'load_transcript:tool-results-null': '99e71a0ffce3', 'load_transcript:tool-results-string': '99e71a0ffce3',
    'load_transcript:transcripts/bad/code-reviewer-answer-lookup.json': '4f53cda18c2b',
    'load_transcript:transcripts/bad/research-fabricated-urls.json': '4f53cda18c2b',
    'load_transcript:transcripts/code-reviewer-drill.json': '4f53cda18c2b',
    'load_transcript:transcripts/research-followup-cited.json': '4f53cda18c2b',
    'load_transcript:transcripts/research-followup.json': '4f53cda18c2b',
    'load_transcript:workspace-blank': '56fe636de697', 'load_transcript:workspace-null': 'bfcc5329687a',
    'load_transcript:workspace-whitespace': '56fe636de697', 'load_triggers:bad/skills/DoEverything': '013ac6057d35',
    'load_triggers:bad/skills/bad-version-skill': '013ac6057d35',
    'load_triggers:bad/skills/no-io-skill': '013ac6057d35',
    'load_triggers:bad/skills/wrong-folder-name': '013ac6057d35', 'load_triggers:base': '4f53cda18c2b',
    'load_triggers:count-0-0': '0003ea0ac1c3', 'load_triggers:count-10-11': '8a169957065f',
    'load_triggers:count-9-10': 'e56523ae3206', 'load_triggers:count-9-11': 'c779b65a388c',
    'load_triggers:duplicate-query': '1e26b9f72850', 'load_triggers:extra-keys': '4f53cda18c2b',
    'load_triggers:golden/skills/csv-cleaner': '4f53cda18c2b',
    'load_triggers:golden/skills/korean-proofreader': '013ac6057d35',
    'load_triggers:golden/skills/pdf-summarizer': '013ac6057d35', 'load_triggers:invalid-json': '2ef15d64a691',
    'load_triggers:item-not-object': '436f557ccd10', 'load_triggers:missing-file': '013ac6057d35',
    'load_triggers:not-list': '706212f2a35c', 'load_triggers:query-blank': 'cccb265b6376',
    'load_triggers:query-deep': 'e4380c2e4ddf', 'load_triggers:query-deep-twice': '2050535c6f1c',
    'load_triggers:query-missing': '897fa64e965e', 'load_triggers:should-trigger-string': 'e4380c2e4ddf',
    'load_triggers:tests/fixtures/skills/csv-cleaner-en': '4f53cda18c2b',
    'load_triggers:tests/rules/_selftest/selftest-name-matches-folder/fail/bad-name': '013ac6057d35',
    'load_triggers:tests/rules/_selftest/selftest-name-matches-folder/pass/good-name': '013ac6057d35',
    'load_triggers:tests/rules/compatibility-length/fail/long-compatibility': '013ac6057d35',
    'load_triggers:tests/rules/compatibility-length/na/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/compatibility-length/pass/with-compatibility': '013ac6057d35',
    'load_triggers:tests/rules/description-length/fail/long-description': '013ac6057d35',
    'load_triggers:tests/rules/description-length/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/description-min-length/fail/short-description': '013ac6057d35',
    'load_triggers:tests/rules/description-min-length/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/description-no-xml/fail/angle-description': '013ac6057d35',
    'load_triggers:tests/rules/description-no-xml/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/description-pov/fail/first-person': '013ac6057d35',
    'load_triggers:tests/rules/description-pov/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/description-present/fail/no-description': '013ac6057d35',
    'load_triggers:tests/rules/description-present/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/description-when/fail/no-trigger': '013ac6057d35',
    'load_triggers:tests/rules/description-when/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/english-bundled-files/fail/json-asset': '013ac6057d35',
    'load_triggers:tests/rules/english-bundled-files/na/only-skill-md': '013ac6057d35',
    'load_triggers:tests/rules/english-bundled-files/pass/with-reference': '013ac6057d35',
    'load_triggers:tests/rules/english-evals/fail/hangul-evals': '013ac6057d35',
    'load_triggers:tests/rules/english-evals/na/no-evals': '013ac6057d35',
    'load_triggers:tests/rules/english-evals/pass/english-evals': '013ac6057d35',
    'load_triggers:tests/rules/english-output-matchers/na/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/english-output-matchers/review/matcher-skill': '013ac6057d35',
    'load_triggers:tests/rules/english-skill-md/fail/escaped-skill': '013ac6057d35',
    'load_triggers:tests/rules/english-skill-md/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/english-tool-files/fail/escaped-yaml': '013ac6057d35',
    'load_triggers:tests/rules/english-tool-files/na/no-yaml': '013ac6057d35',
    'load_triggers:tests/rules/english-tool-files/pass/plain-yaml': '013ac6057d35',
    'load_triggers:tests/rules/evals-count/fail/no-evals': '013ac6057d35',
    'load_triggers:tests/rules/evals-count/pass/three-evals': '013ac6057d35',
    'load_triggers:tests/rules/evals-valid/fail/missing-id': '013ac6057d35',
    'load_triggers:tests/rules/evals-valid/pass/good-evals': '013ac6057d35',
    'load_triggers:tests/rules/example-present/fail/no-section': '013ac6057d35',
    'load_triggers:tests/rules/example-present/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/field-types/fail/unquoted-yes': '013ac6057d35',
    'load_triggers:tests/rules/field-types/pass/quoted-name': '013ac6057d35',
    'load_triggers:tests/rules/frontmatter-fields/fail/extra-field': '013ac6057d35',
    'load_triggers:tests/rules/frontmatter-fields/pass/host-field': '013ac6057d35',
    'load_triggers:tests/rules/frontmatter/fail/no-frontmatter': '013ac6057d35',
    'load_triggers:tests/rules/frontmatter/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/inputs-declared/fail/no-section': '013ac6057d35',
    'load_triggers:tests/rules/inputs-declared/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/name-format/fail/Bad-Name': '013ac6057d35',
    'load_triggers:tests/rules/name-format/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/name-reserved/fail/claude-helper': '013ac6057d35',
    'load_triggers:tests/rules/name-reserved/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/outputs-declared/fail/no-section': '013ac6057d35',
    'load_triggers:tests/rules/outputs-declared/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/parity-invocation-control/fail/claude-only': '013ac6057d35',
    'load_triggers:tests/rules/parity-invocation-control/na/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/parity-invocation-control/pass/both-controls': '013ac6057d35',
    'load_triggers:tests/rules/parity-invocation-hermes/na/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/parity-invocation-hermes/review/both-controls': '013ac6057d35',
    'load_triggers:tests/rules/parity-skill-claude/fail/no-description': '013ac6057d35',
    'load_triggers:tests/rules/parity-skill-claude/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/parity-skill-codex/fail/quoted-policy': '013ac6057d35',
    'load_triggers:tests/rules/parity-skill-codex/pass/plain-yaml': '013ac6057d35',
    'load_triggers:tests/rules/parity-skill-codex/review/extra-field': '013ac6057d35',
    'load_triggers:tests/rules/parity-skill-hermes/fail/hermes-mapping': '013ac6057d35',
    'load_triggers:tests/rules/parity-skill-hermes/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/parity-skill-opencode/fail/typed-metadata': '013ac6057d35',
    'load_triggers:tests/rules/parity-skill-opencode/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/reference-toc/fail/long-without-toc': '013ac6057d35',
    'load_triggers:tests/rules/reference-toc/na/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/reference-toc/pass/long-with-toc': '013ac6057d35',
    'load_triggers:tests/rules/refs-exist/fail/missing-ref': '013ac6057d35',
    'load_triggers:tests/rules/refs-exist/pass/existing-ref': '013ac6057d35',
    'load_triggers:tests/rules/refs-one-level/fail/chained': '013ac6057d35',
    'load_triggers:tests/rules/refs-one-level/pass/one-level': '013ac6057d35',
    'load_triggers:tests/rules/refs-path-depth/fail/deep-ref': '013ac6057d35',
    'load_triggers:tests/rules/refs-path-depth/pass/shallow-ref': '013ac6057d35',
    'load_triggers:tests/rules/refs-relative/fail/outside-ref': '013ac6057d35',
    'load_triggers:tests/rules/refs-relative/pass/relative-ref': '013ac6057d35',
    'load_triggers:tests/rules/single-purpose/fail/many-tasks': '013ac6057d35',
    'load_triggers:tests/rules/single-purpose/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/size-limit/fail/long-skill': '013ac6057d35',
    'load_triggers:tests/rules/size-limit/pass/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/tool-metadata-generated/fail/stale-skill': '013ac6057d35',
    'load_triggers:tests/rules/tool-metadata-generated/na/plain-skill': '013ac6057d35',
    'load_triggers:tests/rules/tool-metadata-generated/pass/quiet-skill': '013ac6057d35',
    'load_triggers:tests/rules/version-format/fail/bad-version': '013ac6057d35',
    'load_triggers:tests/rules/version-format/na/no-version': '013ac6057d35',
    'load_triggers:tests/rules/version-format/pass/plain-skill': '013ac6057d35',
    'scenario_errors:all-wrong': 'b2e6838418a2',
    'scenario_errors:bad/agents/no-context-agent/scenarios/01-one.json': 'c610a8e79c80',
    'scenario_errors:bad/agents/no-context-agent/scenarios/02-two.json': 'c610a8e79c80',
    'scenario_errors:bad/agents/vague-agent/scenarios/01.json': 'd97273267a48', 'scenario_errors:base': '30f59a093d6b',
    'scenario_errors:expected-outcome-blank-list': 'dfbe6cd7da63',
    'scenario_errors:expected-outcome-missing': '994d1b3e4b40',
    'scenario_errors:expected-outcome-number': '30f59a093d6b',
    'scenario_errors:expected-tools-item-blank': '30f59a093d6b',
    'scenario_errors:expected-tools-missing': 'f7b70f7250d6', 'scenario_errors:extra-keys': '30f59a093d6b',
    'scenario_errors:golden/agents/code-reviewer/scenarios/01-overview.json': '7c236b275f8c',
    'scenario_errors:golden/agents/code-reviewer/scenarios/02-drill.json': '7c236b275f8c',
    'scenario_errors:golden/agents/research-assistant/scenarios/01-basic.json': '891b2b66e5c0',
    'scenario_errors:golden/agents/research-assistant/scenarios/02-followup.json': 'c610a8e79c80',
    'scenario_errors:id-blank': 'a14ed48f9a31', 'scenario_errors:id-missing': '3fce6c69e9e7',
    'scenario_errors:id-number': '13174cbad335', 'scenario_errors:must-include-empty': '131d452c4d0c',
    'scenario_errors:must-include-string': '48c85991254d', 'scenario_errors:no-success-criteria': 'c610a8e79c80',
    'scenario_errors:not-object': 'fa21e9111f3d', 'scenario_errors:null': '99e4f3e28c46',
    'scenario_errors:outcome-list': '6505470e10da', 'scenario_errors:success-criteria-null': '6bc3f6c2782f',
    'scenario_errors:success-criteria-number': '6bc3f6c2782f',
    'scenario_errors:tests/fixtures/agents/code-reviewer-v2/scenarios/01-overview.json': '4f53cda18c2b',
    'scenario_errors:tests/fixtures/agents/code-reviewer-v2/scenarios/02-drill.json': '4f53cda18c2b',
    'scenario_errors:tests/fixtures/agents/research-assistant-v2/scenarios/01-basic.json': '4f53cda18c2b',
    'scenario_errors:tests/fixtures/agents/research-assistant-v2/scenarios/02-followup.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/description-present/fail/no-description-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/description-present/fail/no-description-agent/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/description-present/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/description-present/pass/plain-agent/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/english-agent-md/fail/escaped-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/english-agent-md/fail/escaped-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/english-agent-md/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/english-agent-md/pass/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/english-scenarios/fail/hangul-scenario/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/english-scenarios/fail/hangul-scenario/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/english-scenarios/fail/hangul-scenario/scenarios/03.json': 'a960bfa0483b',
    'scenario_errors:tests/rules/english-scenarios/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/english-scenarios/pass/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/english-wrappers/fail/escaped-wrapper/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/english-wrappers/fail/escaped-wrapper/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/english-wrappers/na/no-wrappers/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/english-wrappers/na/no-wrappers/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/english-wrappers/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/english-wrappers/pass/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/frontmatter/fail/no-frontmatter-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/frontmatter/fail/no-frontmatter-agent/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/frontmatter/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/frontmatter/pass/plain-agent/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/goal-present/fail/short-goal/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/goal-present/fail/short-goal/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/goal-present/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/goal-present/pass/plain-agent/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/memory-section/fail/no-memory-section/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/memory-section/fail/no-memory-section/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/memory-section/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/memory-section/pass/plain-agent/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/multi-turn-scenario/fail/single-turn/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/multi-turn-scenario/fail/single-turn/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/multi-turn-scenario/pass/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/multi-turn-scenario/pass/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/name-matches-folder/fail/folder-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/name-matches-folder/fail/folder-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/name-matches-folder/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/name-matches-folder/pass/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-agent-name/fail/Bad_Agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-agent-name/fail/Bad_Agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-agent-name/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-agent-name/pass/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-permissions-claude/fail/claude-grants-more/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-claude/fail/claude-grants-more/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-claude/na/no-claude/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-claude/na/no-claude/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-claude/pass/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-claude/pass/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-codex/fail/unmapped-tool/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-codex/fail/unmapped-tool/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-codex/na/no-codex/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-codex/na/no-codex/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-codex/pass/all-caps-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-codex/pass/all-caps-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-codex/review/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-codex/review/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-hermes/fail/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-permissions-hermes/fail/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-permissions-opencode/fail/opencode-ask/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-opencode/fail/opencode-ask/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-opencode/na/no-opencode/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-opencode/na/no-opencode/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-opencode/review/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-permissions-opencode/review/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/parity-wrapper-claude/fail/no-claude/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-wrapper-claude/fail/no-claude/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-wrapper-claude/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-wrapper-claude/pass/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-wrapper-codex/fail/no-codex/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-wrapper-codex/fail/no-codex/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-wrapper-codex/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-wrapper-codex/pass/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-wrapper-hermes/fail/no-hermes/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-wrapper-hermes/fail/no-hermes/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-wrapper-hermes/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-wrapper-hermes/pass/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-wrapper-opencode/fail/no-opencode/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-wrapper-opencode/fail/no-opencode/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/parity-wrapper-opencode/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/parity-wrapper-opencode/pass/plain-agent/scenarios/02.json': '7c236b275f8c',
    'scenario_errors:tests/rules/scenarios-expected/fail/no-outcome/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/scenarios-expected/fail/no-outcome/scenarios/02.json': 'e021fb40b5bd',
    'scenario_errors:tests/rules/scenarios-expected/pass/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/scenarios-expected/pass/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/scenarios-present/fail/one-scenario/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/scenarios-present/pass/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/scenarios-present/pass/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/scenarios-valid/fail/no-turns/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/scenarios-valid/fail/no-turns/scenarios/02.json': '07b22c3616ae',
    'scenario_errors:tests/rules/scenarios-valid/pass/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/scenarios-valid/pass/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/success-measurable/fail/vague-criteria/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/success-measurable/fail/vague-criteria/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/success-measurable/pass/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/success-measurable/pass/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/tools-declared/fail/no-tools/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/tools-declared/fail/no-tools/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/tools-declared/pass/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/tools-declared/pass/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/tools-documented/fail/no-tools-section/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/tools-documented/fail/no-tools-section/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/tools-documented/pass/plain-agent/scenarios/01.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/tools-documented/pass/plain-agent/scenarios/02.json': 'c610a8e79c80',
    'scenario_errors:tests/rules/tools-subset/fail/undeclared-tool/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/tools-subset/fail/undeclared-tool/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/tools-subset/pass/plain-agent/scenarios/01.json': '4f53cda18c2b',
    'scenario_errors:tests/rules/tools-subset/pass/plain-agent/scenarios/02.json': '4f53cda18c2b',
    'scenario_errors:turn-not-object': '9f395d6583a9', 'scenario_errors:turn-user-blank': 'e17c56f7277f',
    'scenario_errors:turn-user-missing': 'e88bad9a0e5a', 'scenario_errors:turns-empty': '850ab956afe8',
    'scenario_errors:turns-missing': 'e88bad9a0e5a'}
P7_NO_GIT_UNIT_ROWS = {'tests/rules/refs-relative/fail/outside-ref': '63da9d53c879'}
P7_NO_GIT_ROW_IDS = {'refs-exist': 'dd1d725731df'}

if __name__ == "__main__":
    unittest.main()
