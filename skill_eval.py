#!/usr/bin/env python3
"""Skill-eval harness: run a skill's eval set with the skill on vs off (or new vs old), grade, aggregate, gate.

File formats follow Anthropic skill-creator (references/schemas.md, scripts/aggregate_benchmark.py,
scripts/run_eval.py) and agentskills.io (evaluating-skills, optimizing-descriptions); deviations are listed
in references/eval-set-format.md ("Deviations from skill-creator"). Python 3.9 standard library only.

Safety: no model or CLI is ever executed unless the command line has --live AND a finite --budget-usd, the approval
gates pass, the cost estimate fits the budget, and the start is recorded as an unsigned approval bound to the plan
hash and the budget (engine/approvals.py, references/approval-gates.md). Without --live every command is a dry run: it prints the
plan and the estimated cost. Engines and graders take an injectable exec_fn so tests run fully mocked.
Auth under the personal-mac profile is subscription only (Claude Code claude.ai OAuth, Codex ChatGPT login;
references/engine-cli.md "Subscription auth"): API-key variables are refused and scrubbed from every child, and costs
are API-equivalent (usage x API prices), not spend. The company profile is in references/auth-and-cost.md.

Usage: python3 skill_eval.py <validate|estimate|plan|run|grade|calibrate|report|approve|register|snapshot|loop> <skill> ...
       python3 skill_eval.py <approver-check|check-builtin-plugins>
       python3 skill_eval.py builder-check <skill> --workspace <ws> [--compare SAVED]
       python3 skill_eval.py agent <validate|plan|run|grade|report|loop> <agent> ...  (AGENT.md v2 agents per tool)
       python3 skill_eval.py <install|deprecate> <unit> ... | version bump <unit> <part> | drift [ROOT]  (unit B6)
       (also reachable as: python3 eva.py eval <subcommand> ...)
"""
import argparse, contextlib, csv, datetime, errno, hashlib, io, itertools, json, math, mmap, os, platform, random, re, shlex, shutil, subprocess, sys, tempfile, time
from engine import approvals, registry, spend, writable

POLICY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy")
RULESET_ROOT = os.path.dirname(os.path.abspath(__file__))  # rules/, schemas/, targets/, policy/ of ruleset_sha256()
POLICY_TYPES = {"tuple": tuple, "dict": dict, "int": int, "float": float, "str": str}

class PolicyError(ValueError): pass

def _finite(lo, hi=math.inf, kind=(int, float)):
    return lambda x: isinstance(x, kind) and not isinstance(x, bool) and math.isfinite(x) and lo <= x <= hi

def _real_date(s):
    try:
        return isinstance(s, str) and bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", s)
                                           and datetime.date.fromisoformat(s))
    except ValueError:
        return False

def _https_url(s):
    return isinstance(s, str) and bool(re.fullmatch(
        r"https://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*(?::[0-9]+)?(?:[/?#][!-~]*)?", s))

def _each(ok):
    """Check of a non-empty tuple or dict whose items (dict: values) all pass ok: None, or (where, the bad value)."""
    def check(v):
        if not v:
            return "", v
        for k, x in (v.items() if isinstance(v, dict) else enumerate(v)):
            if not ok(x):
                return (".%s" % k if isinstance(v, dict) else "[%d]" % k), x
    return check

def _whole(ok):
    return lambda v: None if ok(v) else ("", v)

def _pair(ok):
    return lambda x: isinstance(x, tuple) and len(x) == 2 and all(ok(y) for y in x)

_STRING = lambda x: isinstance(x, str)
_FRACTION = _whole(_finite(0, 1))
_COUNT = _whole(_finite(1, kind=int))
class PolicySpec:
    """file: the policy file; type: the POLICY_TYPES key; check: the value after the type conversion -> None or
    (where, bad value); need: what check accepts; priced: each model id in it needs a PRICES row; unverified: the
    entry needs "verified": false."""
    def __init__(self, file, type, check, need, priced=False, unverified=False):
        self.file, self.type, self.check, self.need = file, type, check, need
        self.priced, self.unverified = priced, unverified

POLICY_SPECS = {
    "LAYERS": PolicySpec("eval-policy.json", "tuple", _each(_STRING), "a non-empty list of strings"),
    "BASELINES": PolicySpec("eval-policy.json", "dict", _each(_STRING), "a non-empty map to strings"),
    "PRIMARY": PolicySpec("eval-policy.json", "str", _whole(_STRING), "a string"),
    "DEFAULT_RUNS": PolicySpec("eval-policy.json", "int", _COUNT, "an integer >= 1"),
    "TRIGGER_THRESHOLD": PolicySpec("eval-policy.json", "float", _FRACTION, "a finite number from 0 to 1"),
    "TRIGGER_POS": PolicySpec("eval-policy.json", "int", _COUNT, "an integer >= 1"),
    "TRIGGER_NEG": PolicySpec("eval-policy.json", "int", _COUNT, "an integer >= 1"),
    "MAX_AUTO_ITERATIONS": PolicySpec("eval-policy.json", "int", _COUNT, "an integer >= 1"),
    "AGREEMENT_MIN": PolicySpec("gate.rules.json", "float", _FRACTION, "a finite number from 0 to 1"),
    "REGRESSION_TOLERANCE": PolicySpec("gate.rules.json", "float", _FRACTION, "a finite number from 0 to 1"),
    "HIGH_VARIANCE_STDDEV": PolicySpec("eval-policy.json", "float", _FRACTION, "a finite number from 0 to 1"),
    "RULE_VERIFIED_MAX_DAYS": PolicySpec("eval-policy.json", "int", _COUNT, "an integer >= 1"),
    "PRICES": PolicySpec("pricing.json", "dict", _each(_pair(_finite(0))), "a non-empty map of model ids to price rows"),
    "DEFAULT_TOKENS": PolicySpec("pricing.json", "dict", _each(_pair(_finite(0, kind=int))),
                                 "a non-empty map to [input, output] token counts, integers >= 0"),
    "CLAUDE_CAP_HEADROOM": PolicySpec("pricing.json", "float", _FRACTION, "a finite number from 0 to 1"),
    "WORST_CASE_FACTOR": PolicySpec("pricing.json", "float", _whole(_finite(1)), "a finite number >= 1"),
    "DEFAULT_EXECUTOR_MODELS": PolicySpec("models.json", "dict", _each(lambda x: x is None or _STRING(x)),
                                          "a non-empty map to model ids or null", priced=True),
    "DEFAULT_GRADER_MODEL": PolicySpec("models.json", "str", _whole(_STRING), "a model id", priced=True),
    "DEFAULT_BUILDER_MODEL": PolicySpec("models.json", "str", _whole(_STRING), "a model id", priced=True),
    "CLAUDE_EFFORTS": PolicySpec("models.json", "tuple", _each(_STRING), "a non-empty list of strings"),
    "CODEX_EFFORTS": PolicySpec("models.json", "tuple", _each(_STRING), "a non-empty list of strings"),
    "CODEX_EFFORT_CONFIG": PolicySpec("models.json", "str", _whole(_STRING), "a string", unverified=True),
    "GRADER_ENGINES": PolicySpec("models.json", "tuple", _each(_STRING), "a non-empty list of strings"),
    "MODEL_TIERS": PolicySpec("models.json", "tuple", _each(_STRING), "a non-empty list of strings"),
}
POLICY_FILES = ("eval-policy.json", "gate.rules.json", "pricing.json", "models.json")
PRICE_ROW = {  # field of a PRICES row -> (valid, what a valid field is)
    "input": (_finite(0, kind=float), "a finite float >= 0"),
    "output": (_finite(0, kind=float), "a finite float >= 0"),
    "source": (_https_url, "an https:// URL with a host"),
    "read_on": (_real_date, "a real date YYYY-MM-DD"),
}

def _policy_value(where, entry):
    if not (isinstance(entry, dict) and entry.get("type") in POLICY_TYPES and "value" in entry
            and isinstance(entry.get("source"), str) and entry["source"].strip()):
        raise PolicyError("%s: needs value, a source and a type from %s" % (where, sorted(POLICY_TYPES)))
    v, kind, item = entry["value"], entry["type"], entry.get("item_type")
    if item not in (None, "tuple", "price"):
        raise PolicyError("%s: unknown item_type %r" % (where, item))
    if kind == "tuple" and isinstance(v, list):
        v = tuple(v)
    elif kind == "dict" and isinstance(v, dict) and item == "tuple":
        if not all(isinstance(x, list) for x in v.values()):
            raise PolicyError("%s: every value must be a list" % where)
        v = {k: tuple(x) for k, x in v.items()}
    elif kind == "dict" and isinstance(v, dict) and item == "price":
        for k, row in v.items():
            for field, (ok, need) in PRICE_ROW.items():
                if not (isinstance(row, dict) and ok(row.get(field))):
                    raise PolicyError("%s.%s: needs float input and output, an https source and a read_on date; %s "
                                      "must be %s, got %s" % (where, k, field, need,
                                                              json.dumps(row.get(field) if isinstance(row, dict) else row)))
        v = {k: (row["input"], row["output"]) for k, row in v.items()}
    if type(v) is not POLICY_TYPES[kind]:
        raise PolicyError("%s: value is %s, not %s" % (where, type(v).__name__, kind))
    return v

def load_policy(fname):
    """The values that POLICY_SPECS places in policy/<fname> next to this file (never the current directory), as Python
    values; raises PolicyError on a missing or unreadable file, non-strict JSON, a missing or unknown name, or a value
    of the wrong type or outside its spec."""
    from engine import schema
    names = [n for n, spec in POLICY_SPECS.items() if spec.file == fname]
    where = "policy/" + fname
    try:
        doc = schema.load(os.path.join(POLICY_DIR, fname))
    except schema.JsonError as e:
        raise PolicyError("%s: %s" % (where, e)) from None
    values = doc.get("values") if isinstance(doc, dict) else None
    if not isinstance(values, dict) or sorted(values) != sorted(names):
        raise PolicyError("%s: values must name exactly %s" % (where, ", ".join(sorted(names))))
    if fname == "pricing.json" and not (isinstance(doc.get("codex"), dict) and doc["codex"].get("verified") is False):
        raise PolicyError("%s: codex.verified must be false (no Codex or OpenAI price is verified)" % where)
    out = {}
    for n in names:
        spec, entry = POLICY_SPECS[n], values[n]
        out[n] = _policy_value("%s: %s" % (where, n), entry)
        if entry["type"] != spec.type:
            raise PolicyError("%s: %s: type must be %s, got %s" % (where, n, json.dumps(spec.type),
                                                                     json.dumps(entry["type"])))
        bad = spec.check(out[n])
        if bad:
            raise PolicyError("%s: %s%s: must be %s, got %s" % (where, n, bad[0], spec.need, json.dumps(bad[1])))
        if spec.unverified and entry.get("verified") is not False:
            raise PolicyError("%s: %s: verified must be false (not verified from a primary source), got %s"
                              % (where, n, json.dumps(entry.get("verified"))))
    return out

def load_all_policy():
    """Every POLICY_SPECS value from the policy files; each default model id needs a PRICES row."""
    policy = {}
    for fname in POLICY_FILES:
        policy.update(load_policy(fname))
    for n, spec in POLICY_SPECS.items():
        ids = (policy[n].items() if isinstance(policy[n], dict) else [("", policy[n])]) if spec.priced else ()
        for k, model in ids:
            if model is not None and model not in policy["PRICES"]:
                raise PolicyError("policy/%s: %s%s: must name a PRICES row of policy/pricing.json, got %s"
                                  % (spec.file, n, k and "." + k, json.dumps(model)))
    return policy

def _policy_or_exit(load):
    try:
        return load()
    except PolicyError as e:
        if __name__ == "__main__":
            sys.exit("skill_eval: policy error: %s" % e)
        raise

_POLICY = _policy_or_exit(load_all_policy)

LAYERS = _POLICY["LAYERS"]
BASELINES = _POLICY["BASELINES"]
PRIMARY = _POLICY["PRIMARY"]
DEFAULT_RUNS = _POLICY["DEFAULT_RUNS"]
TRIGGER_THRESHOLD = _POLICY["TRIGGER_THRESHOLD"]
TRIGGER_POS = _POLICY["TRIGGER_POS"]
TRIGGER_NEG = _POLICY["TRIGGER_NEG"]
MAX_AUTO_ITERATIONS = _POLICY["MAX_AUTO_ITERATIONS"]
AGREEMENT_MIN = _POLICY["AGREEMENT_MIN"]
REGRESSION_TOLERANCE = _POLICY["REGRESSION_TOLERANCE"]
HIGH_VARIANCE_STDDEV = _POLICY["HIGH_VARIANCE_STDDEV"]
RULE_VERIFIED_MAX_DAYS = _POLICY["RULE_VERIFIED_MAX_DAYS"]
STATIC_TYPES = ("regex", "not_regex", "tool_call", "skill_triggered", "file_exists",
                "file_equals_fixture", "csv_cleaned", "csv_rows", "rule_check")
CSV_ROWS_KEYS = ("type", "path", "rows", "min", "max", "normalize", "unique")

PRICES = _POLICY["PRICES"]
DEFAULT_EXECUTOR_MODELS = _POLICY["DEFAULT_EXECUTOR_MODELS"]
DEFAULT_GRADER_MODEL = _POLICY["DEFAULT_GRADER_MODEL"]
DEFAULT_TOKENS = _POLICY["DEFAULT_TOKENS"]
DEFAULT_BUILDER_MODEL = _POLICY["DEFAULT_BUILDER_MODEL"]
CLAUDE_EFFORTS = _POLICY["CLAUDE_EFFORTS"]
CODEX_EFFORTS = _POLICY["CODEX_EFFORTS"]
CODEX_EFFORT_CONFIG = _POLICY["CODEX_EFFORT_CONFIG"]  # UNVERIFIED config key (policy/models.json)
CODEX_ADMIN_SKILLS = os.path.join("/etc", "codex", "skills")  # admin-scope skills: load in every Codex call
GRADER_ENGINES = _POLICY["GRADER_ENGINES"]
MODEL_TIERS = _POLICY["MODEL_TIERS"]
CLAUDE_CAP_HEADROOM = _POLICY["CLAUDE_CAP_HEADROOM"]
WORST_CASE_FACTOR = _POLICY["WORST_CASE_FACTOR"]
SOFT_LIMIT_NOTE = ("SOFT LIMIT: %s has no per-call budget cap; the worst-case bound is an estimate, so one call can "
                   "exceed the remaining budget (--live refuses it by default; --no-strict-budget opens only the "
                   "strict-budget gate)")
NO_REOPEN = " (--no-strict-budget does not reopen it)"
NO_STRICT_TIER = "A Codex model name holds no tier word, so grader-tier-distinct still refuses Codex under test."
NO_STRICT_WARNING = ("--no-strict-budget: %s runs with a SOFT budget limit (one call can exceed --budget-usd) and its "
                     "auth.json copy is readable by the agent under test (its tokens may be saved in transcripts or "
                     "output files)")

def budget_enforcement(engine_cls_or_obj):
    return "per_call_cap" if engine_cls_or_obj.has_call_cap else "soft_limit"
# Extra CLI args that would change the priced model or the per-call cap (exact flag or flag=value).
# Also flags that would add skill/plugin/config sources back into an isolated run (references/engine-cli.md "Eval
# isolation").
FORBIDDEN_EXTRA_ARGS = {"claude": ("--model", "--fallback-model", "--max-budget-usd", "--settings", "--setting-sources",
                                   "--plugin-dir", "--plugin-url", "--add-dir", "--mcp-config", "--effort",
                                   "--allowedTools", "--allowed-tools", "--dangerously-skip-permissions",
                                   "--disallowedTools", "--disallowed-tools",
                                   "--allow-dangerously-skip-permissions", "--bare", "--safe-mode"),
                        "codex": ("-m", "--model", "-p", "--profile", "--oss", "-C", "--cd", "--add-dir")}

SAFE_NAME_RE = registry.SAFE_NAME_RE  # an eval `name` must be one safe path component

class EvalError(Exception): pass
class LiveDisabled(RuntimeError): pass
class JudgeAnswerError(EvalError):
    """A paid pairwise call whose winner is not A, B or TIE."""
    def __init__(self, msg, winner):
        EvalError.__init__(self, msg); self.winner = winner

safe_name = registry.safe_name

def ensure_inside(root, path):
    """Raises unless path (after resolving symlinks and '..') is root itself or inside it. Called before every
    write under the workspace that uses eval-derived names, and before every rmtree."""
    r, p = os.path.realpath(root), os.path.realpath(path)
    if p != r and not p.startswith(r.rstrip(os.sep) + os.sep):
        raise EvalError("refusing to touch %s: outside %s" % (path, root))
    return path

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def rjson(path, default=None):
    if not os.path.isfile(path): return default
    with open(path, encoding="utf-8") as f: return json.load(f)

def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=2)

def wtext(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: f.write(text)

def sha256_files(*paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(os.path.basename(p).encode() + b"\0")
        if os.path.isfile(p):
            with open(p, "rb") as f: h.update(f.read())
        else:
            h.update(b"<missing>")
    return h.hexdigest()

def sha256_labeled(pairs, marked=()):
    """pairs: [(label, path)]; the label (path relative to the skill) is hashed with the content. marked: [(label,
    marker bytes)], hashed after the pairs with the marker in place of content."""
    h = hashlib.sha256()
    for label, p in pairs:
        h.update(label.encode() + b"\0")
        if os.path.isfile(p):
            with open(p, "rb") as f: h.update(f.read())
        else:
            h.update(b"<missing>")
        h.update(b"\0")
    for label, marker in marked:
        h.update(label.encode() + b"\0" + marker + b"\0")
    return h.hexdigest()

def ruleset_sha256():
    """The rule bundle hash under RULESET_ROOT (engine/rule_runner.ruleset_pairs: rules/**, schemas/**, targets/** and
    the bundle policy files), the ruleset_sha256 that eva.py check prints. Computed on every call, never through the
    cached default bundle, so a change made while this process runs (a loop builder call) is seen."""
    from engine import rule_runner
    try:
        return rule_runner.sha256_labeled(rule_runner.ruleset_pairs(os.path.join(RULESET_ROOT, "rules"), RULESET_ROOT))
    except (rule_runner.BundleError, OSError) as ex:
        raise EvalError("ruleset_sha256: %s" % ex)

def skill_tree_hash(skill_dir):
    """sha256 over every file a run installs (eva.unit_tree files), labeled by relative path and hashed by content. A
    link out of the folder and a cycle enter as their path with a marker, never as content and never walked."""
    import eva
    tree = eva.unit_tree(skill_dir)
    return sha256_labeled([(rel, os.path.join(skill_dir, *rel.split("/"))) for rel in tree.files],
                          [(rel, b"<outside>") for rel in tree.outside] + [(rel, b"<cycle>") for rel in tree.cycles])

def cents_floor(x):
    return math.floor(x * 100 + 1e-9) / 100.0

def slug(text, n=40):
    s = re.sub(r"[^0-9A-Za-z\uAC00-\uD7A3]+", "-", text or "").strip("-").lower()
    return s[:n].strip("-")

# ---------- eval set ----------
def check_object_problems(where, chk):
    """Problems of one static check object per schemas/evals.schema.json#/$defs/check."""
    import eva
    return eva.shape_schema("evals.schema.json").problems(chk, start=("$defs", "check"), where=where)

def load_eval_set(skill_dir):
    """Returns (spec, problems). spec: skill_name, skill_dir, description, cases[], triggers[] (or None if missing).
    The document shape is eva.evals_errors (schemas/evals.schema.json), each check object check_object_problems."""
    skill_dir = os.path.realpath(skill_dir)
    import eva  # frontmatter parser and the static evals-valid rules
    problems = []
    text, err = eva.read_text(os.path.join(skill_dir, "SKILL.md"))
    meta = (eva.parse_frontmatter(text)[0] if not err else None) or {}
    name = meta.get("name") if isinstance(meta.get("name"), str) else os.path.basename(skill_dir)
    if not safe_name(name):  # it becomes the install folder <work>/<skills_rel>/<name> and the workspace name
        problems.append("SKILL.md name: %r must match %s without '..' (one path component)" % (name, SAFE_NAME_RE.pattern))
    _, errs = eva.evals_errors(skill_dir, name)
    problems += ["evals.json: " + x for x in errs]
    data = rjson(os.path.join(skill_dir, "evals", "evals.json"), {}) if not errs else {}
    claude_script = data.get("claude_python_script")
    if claude_script is not None:
        if (not isinstance(claude_script, str) or not re.fullmatch(r"scripts/[A-Za-z0-9_./-]+\.py", claude_script)
                or ".." in claude_script.split("/") or not os.path.isfile(os.path.join(skill_dir, claude_script))):
            problems.append("evals.json.claude_python_script: existing relative scripts/*.py path required")
            claude_script = None
    cases = []
    for c in (data.get("evals") or []):
        default_layer = c.get("layer") or "llm"
        items = []
        raw = c.get("assertions") or c.get("expectations") or []
        for i, a in enumerate(raw):
            where = "evals[id=%s].assertions[%d]" % (c["id"], i)
            it = {"id": "%s.%d" % (c["id"], i), "text": a, "layer": default_layer, "check": None} if isinstance(a, str) \
                else {"id": "%s.%d" % (c["id"], i), "text": a["text"], "layer": a["layer"], "check": a.get("check")}
            if it["layer"] == "static":
                if it["check"] is None:
                    problems.append("%s: static layer needs a machine check object {\"text\", \"layer\": \"static\", "
                                    "\"check\": {\"type\": ...}} (%r)" % (where, it["text"]))
                else:
                    shape = check_object_problems(where, it["check"])
                    problems += shape
                    if not shape and it["check"].get("type") == "rule_check":
                        why = rule_check_target(it["check"])[2]
                        if why: problems.append("%s.%s" % (where, why))
                    if isinstance(it["check"], dict) and it["check"].get("type") in ("file_equals_fixture", "csv_cleaned"):
                        fixture = it["check"].get("fixture")
                        if isinstance(fixture, str) and fixture not in (c.get("files") or []):
                            problems.append("%s.check.fixture: must be one of this case's files" % where)
            items.append(it)
        pressure = c.get("pressure")
        if pressure is not None and not (isinstance(pressure, dict) and isinstance(pressure.get("kinds"), list)
                                         and pressure["kinds"] and all(isinstance(k, str) and k for k in pressure["kinds"])
                                         and isinstance(pressure.get("context", ""), str)):
            problems.append("evals[id=%s].pressure: {\"kinds\": [str, ...], \"context\": str} required" % c.get("id"))
            pressure = None
        if "name" in c and not safe_name(c["name"]):
            problems.append("evals[id=%s].name: %r must match %s without '..' (one path component)"
                            % (c["id"], c["name"], SAFE_NAME_RE.pattern))
        name_ = c["name"] if safe_name(c.get("name")) else (slug(c["prompt"]) or str(c["id"]))
        cases.append({"id": c["id"], "name": name_, "prompt": c["prompt"],
                      "expected_output": c["expected_output"], "files": c.get("files") or [], "items": items,
                      "pressure": pressure})
    triggers, tp = load_triggers(skill_dir)
    problems += tp
    problems += ["english-only rule: " + x for x in eva.english_violations(skill_dir)]
    problems += ["multi-tool parity rule: " + x for x in eva.parity_violations(skill_dir)]
    return {"skill_name": name, "skill_dir": skill_dir, "description": meta.get("description") or "",
            "claude_python_script": claude_script, "cases": cases, "triggers": triggers}, problems


def load_triggers(skill_dir):
    """evals/trigger_evals.json: skill-creator eval-set format [{"query": str, "should_trigger": bool}], shape per
    schemas/trigger-evals.schema.json. Returns (the well-formed queries or None, problems)."""
    fp = os.path.join(skill_dir, "evals", "trigger_evals.json")
    if not os.path.isfile(fp):
        return None, ["trigger_evals.json: missing evals/trigger_evals.json (%d should-trigger + %d should-not queries)"
                      % (TRIGGER_POS, TRIGGER_NEG)]
    try:
        data = rjson(fp)
    except ValueError as ex:
        return None, ["trigger_evals.json: invalid JSON: %s" % ex]
    import eva
    shape = eva.shape_schema("trigger-evals.schema.json")
    if not isinstance(data, list):
        return None, shape.problems(data)
    item = shape.sub(("$defs", "trigger-item"))
    out = [{"query": q["query"], "should_trigger": q["should_trigger"]} for q in data if not item.validate(q)]
    pos = sum(q["should_trigger"] for q in out)
    return out, shape.problems(data, pos=pos, neg=len(out) - pos)


def full_prompt(case):
    p = case.get("pressure")
    return (p["context"].strip() + "\n\n" + case["prompt"]) if p and p.get("context", "").strip() else case["prompt"]

# ---------- engines ----------
_LIVE = {"on": False, "gated": False, "ledger": None}

def enable_live(grant):
    """The only way main() turns live mode on: a --live start recorded and read back approved (engine/approvals.py).
    From then on every live_exec needs an open intent of the command's spend ledger (_LIVE["ledger"], engine/spend.py)."""
    if not (isinstance(grant, approvals.Grant) and grant.kind == "live"):
        raise EvalError("live mode needs a recorded --live start (engine/approvals.py)")
    _LIVE.update(on=True, gated=True)

def live_exec(cmd, cwd, timeout, env=None):
    """The only place a real CLI is started. Refuses unless main() enabled live mode (--live + --budget-usd + gates)
    and, after a signed start, unless the spend ledger holds an open intent for this call."""
    if not _LIVE["on"]:
        raise LiveDisabled("refusing to execute %r: live mode needs --live and --budget-usd" % (cmd[0],))
    if api_key_mode() and cmd and os.path.basename(cmd[0]) in spend.API_KEY_REFUSED:
        raise LiveDisabled("refusing to execute %r: %s" % (cmd[0], spend.api_key_refusal(
            "exec", os.path.basename(cmd[0]), AUTH["profile"].name, True)))
    if _LIVE.get("gated") and not (_LIVE.get("ledger") is not None and _LIVE["ledger"].consume()):
        raise LiveDisabled("refusing to execute %r: no open spend intent for this call (engine/spend.py)" % (cmd[0],))
    env = scrub_env(os.environ if env is None else env)  # no API key reaches a child, except below
    env.pop("CLAUDECODE", None)  # as skill-creator run_eval.py: allow nesting claude -p inside a Claude Code session
    env = with_account_marker(env, cmd)
    env.update(listed_child_env(cmd))
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                       stdin=subprocess.DEVNULL)  # codex exec waits for stdin when it is an open pipe/terminal
    return p.returncode, p.stdout, p.stderr

# ---------- subscription auth (references/engine-cli.md "Subscription auth") ----------
# Variables that switch Claude Code / Codex to API-key (per-token) billing. Their presence blocks --live (preflight)
# and they are removed from every child environment anyway. Values are never read, printed or logged.
API_KEY_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
               # other credential variables from https://code.claude.com/docs/en/env-vars (read 2026-10-15)
               "ANTHROPIC_AWS_API_KEY", "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
               "AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_IDENTITY_TOKEN")
# Variables that route Claude Code away from the claude.ai subscription (third-party provider, gateway/proxy base URL,
# credential profile/federation selectors). Same page, plus the "withheld" list of
# https://code.claude.com/docs/en/server-managed-settings (read 2026-10-15). Refused and scrubbed like API keys.
PROVIDER_ENV = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_MANTLE",
                "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST", "ANTHROPIC_BASE_URL",
                "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_BEDROCK_MANTLE_BASE_URL", "ANTHROPIC_AWS_BASE_URL",
                "ANTHROPIC_VERTEX_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID", "ANTHROPIC_FOUNDRY_BASE_URL",
                "ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_PROFILE", "ANTHROPIC_CONFIG_DIR", "ANTHROPIC_FEDERATION_RULE_ID")
NON_SUBSCRIPTION_ENV = API_KEY_ENV + PROVIDER_ENV
# Not refused, but removed from every child: Claude runs authenticate only with the existing Keychain login of the
# unchanged config dir, the same login `claude auth status` verified. An exported setup-token would replace it.
IGNORED_AUTH_ENV = ("CLAUDE_CODE_OAUTH_TOKEN",)

# ---------- auth profiles (policy/auth-profiles.json; references/auth-and-cost.md) ----------
# Picked only by --auth-profile, never detected. AUTH["profile"] is the profile of this command; None (direct calls,
# tests) and personal-mac keep every subscription rule. Checks key on the profile's data: billing api_key turns on the
# listed keys and gateways, the hard cap and the API-key records; account_shim false drops the shim marker.
AUTH_PROFILES_FILE = "auth-profiles.json"
AUTH_PROFILE_NAMES = ("personal-mac", "company")
AUTH_ENGINES = ("claude", "codex")
AUTH_TOP_KEYS = ("$comment", "policy", "version", "source", "profiles")
AUTH_PROFILE_KEYS = ("label", "billing", "account_shim", "subscription_checks", "engines")
PERSONAL_MAC = {"label": "Personal Mac: subscription auth only (claude.ai and ChatGPT logins), account shim and "
                         "subscription checks on",
                "billing": "subscription", "account_shim": True, "subscription_checks": True,
                "engines": {e: {"api_key_env": [], "gateways": {}} for e in AUTH_ENGINES}}
GATEWAY_URL_RE = re.compile(r"https://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/-]*)?")
AUTH = {"profile": None}
PROFILE_REQUIRED = "a live run requires --auth-profile (personal-mac or company); the engine never detects the profile"
CAP_REQUIRED = "a hard cap is required in API-key mode: pass a finite --budget-usd > 0 (auth profile %s)"
NEEDS_LEDGER = "API-key mode needs the spend ledger (engine/spend.py); refusing the call"
PERSONAL_DEFAULT_LINE = ("auth profile: personal-mac (default: no --auth-profile given; a live start requires "
                         "--auth-profile)")

class AuthProfile(object):
    def __init__(self, name, doc):
        self.name, self.label, self.billing = name, doc["label"], doc["billing"]
        self.account_shim, self.subscription_checks = doc["account_shim"], doc["subscription_checks"]
        self.engines = {e: {"api_key_env": tuple(x["api_key_env"]),
                            "gateways": {k: tuple(v) for k, v in x["gateways"].items()}} for e, x in doc["engines"].items()}

def _auth_profile_problem(p):
    if not isinstance(p, dict) or sorted(p) != sorted(AUTH_PROFILE_KEYS):
        return "must hold exactly %s" % ", ".join(AUTH_PROFILE_KEYS)
    if not (isinstance(p["label"], str) and p["label"].strip()): return "label must be a non-empty string"
    if p["billing"] not in ("subscription", "api_key"): return 'billing must be "subscription" or "api_key"'
    for k in ("account_shim", "subscription_checks"):
        if not isinstance(p[k], bool): return "%s must be true or false" % k
    if not isinstance(p["engines"], dict) or sorted(p["engines"]) != sorted(AUTH_ENGINES):
        return "engines must hold exactly %s" % ", ".join(AUTH_ENGINES)
    listed = False
    for e in AUTH_ENGINES:
        x = p["engines"][e]
        if not isinstance(x, dict) or sorted(x) != ["api_key_env", "gateways"]:
            return "engines.%s must hold exactly api_key_env, gateways" % e
        keys, gw = x["api_key_env"], x["gateways"]
        if not (isinstance(keys, list) and all(isinstance(k, str) and k in API_KEY_ENV for k in keys)
                and len(set(keys)) == len(keys)):
            return "engines.%s.api_key_env must be a list of distinct names from %s" % (e, ", ".join(API_KEY_ENV))
        if not isinstance(gw, dict): return "engines.%s.gateways must be an object" % e
        for var, urls in gw.items():
            if var not in PROVIDER_ENV or not var.endswith("_BASE_URL"):
                return "engines.%s.gateways: %s is not a *_BASE_URL variable of PROVIDER_ENV" % (e, json.dumps(var))
            if not (isinstance(urls, list) and urls and all(isinstance(u, str) and GATEWAY_URL_RE.fullmatch(u) for u in urls)
                    and len(set(urls)) == len(urls)):
                return ("engines.%s.gateways.%s must be a non-empty list of distinct https:// URLs without user info, query "
                        "or fragment" % (e, var))
        listed = listed or bool(keys) or bool(gw)
    if p["billing"] == "api_key" and (p["account_shim"] or p["subscription_checks"]):
        return "billing api_key needs account_shim and subscription_checks false"
    if p["billing"] == "subscription" and (listed or not p["subscription_checks"]):
        return "billing subscription needs subscription_checks true and no key or gateway listed"
    return None

def load_auth_profiles():
    """{name: AuthProfile} from policy/auth-profiles.json under RULESET_ROOT (the file policy_sha256 hashes). Read only
    by the commands that take --auth-profile, never at import; EvalError on a missing file, non-strict JSON or any value
    outside the rules (exactly the two profiles, personal-mac exactly as shipped, known key and gateway names)."""
    from engine import schema
    where = "policy/" + AUTH_PROFILES_FILE
    try:
        doc = schema.load(os.path.join(RULESET_ROOT, "policy", AUTH_PROFILES_FILE))
    except schema.JsonError as e:
        raise EvalError("%s: %s" % (where, e))
    if not isinstance(doc, dict) or sorted(doc) != sorted(AUTH_TOP_KEYS):
        raise EvalError("%s: the top level must hold exactly %s" % (where, ", ".join(AUTH_TOP_KEYS)))
    if doc["policy"] != "auth-profiles": raise EvalError('%s: policy must be "auth-profiles"' % where)
    for k in ("$comment", "version", "source"):
        if not (isinstance(doc[k], str) and doc[k].strip()): raise EvalError("%s: %s must be a non-empty string" % (where, k))
    profiles = doc["profiles"]
    if not isinstance(profiles, dict) or sorted(profiles) != sorted(AUTH_PROFILE_NAMES):
        raise EvalError("%s: profiles must be exactly %s" % (where, ", ".join(AUTH_PROFILE_NAMES)))
    for name in AUTH_PROFILE_NAMES:
        bad = _auth_profile_problem(profiles[name])
        if bad: raise EvalError("%s: profiles.%s: %s" % (where, name, bad))
    if profiles["personal-mac"] != PERSONAL_MAC:
        raise EvalError("%s: profiles.personal-mac must keep its shipped values (subscription billing, account shim and "
                        "subscription checks on, no key or gateway)" % where)
    return {name: AuthProfile(name, profiles[name]) for name in AUTH_PROFILE_NAMES}

def select_auth_profile(name):
    if name is not None and name not in AUTH_PROFILE_NAMES:
        raise EvalError("unknown --auth-profile %r: the profiles are %s" % (name, ", ".join(AUTH_PROFILE_NAMES)))
    AUTH["profile"] = load_auth_profiles()[name or "personal-mac"]
    return AUTH["profile"]

def api_key_mode():
    p = AUTH["profile"]
    return p is not None and p.billing == "api_key"

def current_billing():
    return BILLING if AUTH["profile"] is None else AUTH["profile"].billing

def account_shim_on():
    return AUTH["profile"] is None or AUTH["profile"].account_shim

def subscription_checks_on():
    return AUTH["profile"] is None or AUTH["profile"].subscription_checks

def _no_slash(u):
    return u[:-1] if u.endswith("/") else u

def company_env_problems(p, environ):
    """API-key mode: a set key variable must be listed for some engine; a set base-URL variable must be listed and hold
    one of its listed URLs (the only place a value is read); any other provider routing is refused. Names only."""
    keys = {k for x in p.engines.values() for k in x["api_key_env"]}
    urls = {}
    for x in p.engines.values():
        for var, listed in x["gateways"].items(): urls.setdefault(var, set()).update(_no_slash(u) for u in listed)
    out = []
    for k in NON_SUBSCRIPTION_ENV:
        if k not in environ: continue
        if k in API_KEY_ENV:
            if k not in keys:
                out.append("%s is set (value not shown): auth profile %s does not list it; unset it" % (k, p.name))
        elif k.endswith("_BASE_URL"):
            if _no_slash(str(environ[k])) not in urls.get(k, ()):
                out.append("%s is set to a gateway that auth profile %s does not list (value not shown); unset it or list "
                           "it in policy/auth-profiles.json" % (k, p.name))
        else:
            out.append("%s is set (value not shown): provider routing that auth profile %s does not list; unset it"
                       % (k, p.name))
    return out

# Set with the listed key on every child of that engine, so the agent's Bash tool, hooks and scripts do not inherit the
# key (the account shim's rule for its token). Claude children are refused in API-key mode today (plan.md:902).
LISTED_KEY_SCRUB_ENV = {"claude": {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"}}

def listed_child_env(cmd):
    """API-key mode: the listed key and gateway variables of the engine cmd[0] names, from os.environ, plus that
    engine's LISTED_KEY_SCRUB_ENV, which overrides an inherited value; else nothing."""
    if not (api_key_mode() and cmd): return {}
    engine = os.path.basename(cmd[0])
    x = AUTH["profile"].engines.get(engine)
    if not x: return {}
    env = {k: os.environ[k] for k in tuple(x["api_key_env"]) + tuple(x["gateways"]) if k in os.environ}
    env.update(LISTED_KEY_SCRUB_ENV.get(engine, {}))
    return env

# Account switching (2026-10-16, references/engine-cli.md "Subscription auth"). skill_eval itself still never passes a
# token (IGNORED_AUTH_ENV above is scrubbed first). Every `claude` child gets an explicit caller marker; the ~/bin/claude
# account shim (claude-accts v5) then may, only on a usage-limit error from acct0, retry once on ~/.claude-acctN with
# that account's Keychain token, which the shim alone injects (and CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1 so the eval
# agent's Bash/hooks do not inherit it). SKILL_EVAL_ACCOUNT_SWITCH=0 opts out (marker kept, shim pins acct0).
# Without the shim on PATH the marker is ignored by the real CLI.
ACCOUNT_MARKER_ENV = {"CLAUDE_AS_CALLER": "skill_eval"}
ACCOUNT_SWITCH_OPT_IN = "CLAUDE_AS_AUTO_SWITCH"

def account_switch_enabled(environ=None):
    return (os.environ if environ is None else environ).get("SKILL_EVAL_ACCOUNT_SWITCH", "1") != "0"

def with_account_marker(env, cmd):
    """env plus the caller marker (and opt-in) when cmd launches claude; other CLIs get env unchanged."""
    env = dict(env)
    for k in list(ACCOUNT_MARKER_ENV) + [ACCOUNT_SWITCH_OPT_IN]: env.pop(k, None)
    if not account_shim_on(): return env
    if cmd and os.path.basename(cmd[0]) == "claude":
        env.update(ACCOUNT_MARKER_ENV)
        env[ACCOUNT_SWITCH_OPT_IN] = "1" if account_switch_enabled() and claude_bash_disabled(cmd) else "0"
    return env

def claude_bash_disabled(cmd):
    """P1 option b (2026-10-17): only a claude call whose --tools values provably exclude Bash may account-switch
    (grader: --tools ""; builder: Read,Edit,Write,Glob,Grep). Executor runs keep Bash, so they always stay on acct0
    and no acctN token is ever in a Bash-capable agent's process environment. Same rule as claude-accts shim v8."""
    vals, i = [], 0
    while i < len(cmd):
        a = cmd[i]
        if a == "--tools":
            if i + 1 >= len(cmd): return False
            vals.append(cmd[i + 1]); i += 2; continue
        if a.startswith("--tools="): vals.append(a.split("=", 1)[1])
        i += 1
    # --tools does not restrict MCP/plugin tools: an explicit MCP config or plugin dir (e.g. via --claude-arg) keeps acct0
    if not vals or any(a in ("--mcp-config", "--plugin-dir", "--plugin-url")
                       or a.startswith(("--mcp-config=", "--plugin-dir=", "--plugin-url="))
                       for a in cmd): return False
    for v in vals:
        for t in re.split(r"[,\s]+", v.strip()):
            if t and (re.match(r"^bash(\(|$)", t, re.I) or t.lower() == "default"): return False
    return True

def switch_account_config_dirs(home=None, environ=None):
    """Config dirs of every account the shim may switch to (only when switching is enabled), derived from the same
    inputs as the shim: home = $CLAUDE_AS_HOME or ~, accounts = $CLAUDE_ACCOUNTS or every ~/.claude-acctN. Each gets
    the same preflight/launch checks as the default config dir (settings auth, remote cache, user/synced skills)."""
    environ = os.environ if environ is None else environ
    if not account_shim_on() or not account_switch_enabled(environ): return []
    home = home or environ.get("CLAUDE_AS_HOME") or os.path.expanduser("~")
    listed = environ.get("CLAUDE_ACCOUNTS")
    if listed:
        names = [".claude-" + n.strip() for n in listed.split(",") if n.strip() and n.strip() != "acct0"]
    else:
        try: names = sorted(os.listdir(home))
        except OSError: return []
    return [os.path.join(home, n) for n in names
            if re.match(r"^\.claude-acct[1-9][0-9]{0,2}$", n) and os.path.isdir(os.path.join(home, n))]
# `--json` and the method values "claude.ai" / "api_key" are from the Claude Code changelog
# (~/.claude/cache/changelog.md: "Added `configDirectory` to the output of `claude auth status --json`", "it now
# reports `api_key`"). The JSON key that holds the method is not documented there (UNVERIFIED), so every string value under
# a key named like auth/method/source/provider is classified, and anything unrecognized fails closed.
CLAUDE_AUTH_STATUS_CMD = ["claude", "auth", "status", "--json"]
# Recognized subscription method values (normalized: lowercase alphanumerics). "claude.ai" is from the changelog;
# "oauth"/"oauth_token" (setup-token) and "subscription" are (UNVERIFIED) spellings, accepted only under a method-like key.
CLAUDE_SUBSCRIPTION_METHODS = ("claudeai", "oauth", "oauthtoken", "claudeaioauth", "subscription")
CODEX_SUBSCRIPTION_MODES = ("chatgpt",)  # ~/.codex/auth.json "auth_mode" (value seen on this machine, 2026-10-15)
BILLING = "subscription"
ACTUAL_SPEND_NOTE = ("not metered: subscription runs (claude.ai plan / ChatGPT plan) are not billed per token; plan "
                     "limits and any extra-usage charges are visible only in your account")

# The ssh-agent variables and those of an approver-session shell started before unit N1. A child that inherits them
# reaches an agent that may hold an unlocked key, and the skill under test runs inside such a child (R25-1).
APPROVER_SESSION_ENV = ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "SKILL_MASTER_APPROVER_SESSION", "PS1", "PROMPT")

def scrub_env(env):
    """Copy of env without the API-key and provider-routing variables (NON_SUBSCRIPTION_ENV), IGNORED_AUTH_ENV and
    APPROVER_SESSION_ENV."""
    return {k: v for k, v in dict(env).items()
            if k not in NON_SUBSCRIPTION_ENV + IGNORED_AUTH_ENV + APPROVER_SESSION_ENV + (
                "BASH_ENV", "ENV", "BASHOPTS", "SHELLOPTS", "CLAUDE_CODE_SAFE_MODE",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
            and not k.startswith("BASH_FUNC_")}

def api_key_env_problems(environ=None):
    environ = os.environ if environ is None else environ
    if api_key_mode(): return company_env_problems(AUTH["profile"], environ)
    return ["%s is set (value not shown): %s is not allowed, eval runs use subscription auth only; unset it"
            % (k, "API-key auth" if k in API_KEY_ENV else "non-subscription provider routing")
            for k in NON_SUBSCRIPTION_ENV if k in environ]

def run_auth_status(cmd, timeout=30):
    """Runs a local status command (no model call, nothing billed) with the same scrubbed env the runs get. Only
    check_live_gates calls it, so dry runs still execute nothing."""
    env = scrub_env(os.environ); env.pop("CLAUDECODE", None)
    env = with_account_marker(env, cmd)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, p.stdout, p.stderr

AUTH_STATUS = {"fn": run_auth_status}  # tests replace this; it is never a model call

def _string_values(obj, keyre, depth=0):
    out = []
    if isinstance(obj, dict) and depth < 4:
        for k, v in obj.items():
            if isinstance(v, str) and keyre.search(str(k)): out.append(v)
            elif isinstance(v, (dict, list)): out += _string_values(v, keyre, depth + 1)
    elif isinstance(obj, list) and depth < 4:
        for v in obj: out += _string_values(v, keyre, depth + 1)
    return out

def _shown(v):
    """A method value is echoed in errors only when it looks like a method name (never an email or a token)."""
    return v if re.match(r"^[A-Za-z0-9._ -]{1,40}$", v) and "@" not in v else "<hidden>"

def claude_auth_problems(status_fn=None):
    """`claude auth status --json` must report `loggedIn: true` and a recognized claude.ai (OAuth / subscription)
    method under a method-like key (CLAUDE_SUBSCRIPTION_METHODS). API-key methods anywhere are refused; no JSON,
    loggedIn missing/false, or no (or any unrecognized) method also refuse (fail closed). Raw output is never logged."""
    try:
        rc, out, _ = (status_fn or AUTH_STATUS["fn"])(CLAUDE_AUTH_STATUS_CMD)
    except (OSError, subprocess.SubprocessError) as ex:
        return ["claude: `claude auth status --json` could not run (%s); cannot verify subscription auth" % type(ex).__name__]
    try: data = json.loads(out or "")
    except ValueError: data = None
    if rc != 0 or not isinstance(data, dict):
        return ["claude: `claude auth status --json` gave no JSON (exit %s); cannot verify subscription auth" % rc]
    if data.get("loggedIn") is False:
        return ["claude: `claude auth status` reports no login; live Claude runs use only the existing claude.ai "
                "Keychain login of this user session"]
    vals = _string_values(data, re.compile(r"auth|method|source|provider", re.I))
    norm = [re.sub(r"[^a-z0-9]", "", v.lower()) for v in vals]
    api = sorted({_shown(v) for v, n in zip(vals, norm) if "apikey" in n or n == "console"
                  or ("authtoken" in n and "oauth" not in n)})  # ANTHROPIC_AUTH_TOKEN, not oauth_token
    if api:
        return ["claude: auth status reports API-key auth %s; eval runs use only the existing claude.ai (OAuth "
                "subscription) Keychain login" % api]
    if data.get("loggedIn") is not True:
        return ["claude: auth status has no `loggedIn: true`; cannot verify subscription auth"]
    # The method must come from a key named like a method (authMethod, method, ...) and be one of the recognized
    # subscription values exactly; an OAuth-like string under some other key does not count.
    raw = _string_values(data, re.compile(r"method", re.I))
    if not raw or not all(re.sub(r"[^a-z0-9]", "", v.lower()) in CLAUDE_SUBSCRIPTION_METHODS for v in raw):
        return ["claude: auth status shows no recognized claude.ai/OAuth auth method (methods seen: %s); cannot "
                "verify subscription auth" % (sorted({_shown(v) for v in raw}) or "none")]
    return []

def codex_auth_mode(path):
    """Only the "auth_mode" field of a Codex auth.json; tokens are never returned, printed or logged."""
    try:
        with open(path, encoding="utf-8") as f: data = json.load(f)
    except (OSError, ValueError):
        return None
    mode = data.get("auth_mode") if isinstance(data, dict) else None
    return mode if isinstance(mode, str) else None

def codex_auth_problems():
    auth = os.path.join(real_codex_home(), "auth.json")
    if not os.path.isfile(auth):
        return ["codex: no file-based %s (ChatGPT login) to copy read-only into the isolated CODEX_HOME (keyring "
                "storage cannot be isolated without changing it; API keys are not allowed)" % auth]
    mode = codex_auth_mode(auth)
    if mode not in CODEX_SUBSCRIPTION_MODES:
        return ["codex: %s auth_mode is %s, not ChatGPT; eval runs use subscription auth only (`codex login` with "
                "ChatGPT)" % (auth, repr(_shown(mode)) if mode else "missing/unreadable")]
    return []

# Claude settings files that can supply auth (apiKeyHelper, env). Isolated runs use --setting-sources project,local:
# <cwd>/.claude/settings.json and settings.local.json; whether ancestors of the cwd are also read is UNVERIFIED,
# so every ancestor is checked too. Managed sources always apply. From https://code.claude.com/docs/en/managed-settings
# (read 2026-10-15): managed-settings.json plus every non-hidden *.json in managed-settings.d/ next to it
# (/Library/Application Support/ClaudeCode/ on macOS, /etc/claude-code/ on Linux and WSL); the macOS configuration
# profile domain com.anthropic.claudecode; Windows HKLM/HKCU registry values (cannot be read here: fail closed); and
# server-managed settings, cached at <config dir>/remote-settings.json (docs/en/server-managed-settings).
CLAUDE_SETTINGS_FILES = ("settings.json", "settings.local.json")
CLAUDE_MANAGED_SETTINGS = ("/Library/Application Support/ClaudeCode/managed-settings.json",
                           "/etc/claude-code/managed-settings.json")
# Where macOS stores a configuration profile's managed preferences for the domain: the standard macOS location, not
# named on the Claude Code pages (UNVERIFIED). Parsed with plistlib when present; an unreadable file fails closed.
CLAUDE_MDM_PLIST_DIR = "/Library/Managed Preferences"
CLAUDE_MDM_DOMAIN = "com.anthropic.claudecode"
CLAUDE_REMOTE_SETTINGS = "remote-settings.json"

def claude_config_dir(environ=None):
    """The effective Claude config dir: $CLAUDE_CONFIG_DIR, else ~/.claude (docs/en/env-vars)."""
    environ = os.environ if environ is None else environ
    return environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")

def claude_managed_sources():
    """(settings files to check, problems). Every endpoint-managed source we can read, and a problem for each one
    that exists but cannot be checked (fail closed)."""
    files, probs = [], []
    for p in CLAUDE_MANAGED_SETTINGS:
        files.append(p)
        d = os.path.join(os.path.dirname(p), "managed-settings.d")
        if os.path.lexists(d):
            try:
                names = sorted(os.listdir(d))
            except OSError as ex:
                probs.append("claude: %s exists but cannot be listed (%s); cannot verify managed settings"
                             % (d, type(ex).__name__)); continue
            files += [os.path.join(d, n) for n in names if n.endswith(".json") and not n.startswith(".")]
    plist = CLAUDE_MDM_DOMAIN + ".plist"
    if os.path.isdir(CLAUDE_MDM_PLIST_DIR):  # <dir>/<plist> (device) and <dir>/<user>/<plist> (per user)
        try:
            top = sorted(os.listdir(CLAUDE_MDM_PLIST_DIR))
        except OSError as ex:
            top = []
            probs.append("claude: %s cannot be listed (%s); cannot verify a %s configuration profile"
                         % (CLAUDE_MDM_PLIST_DIR, type(ex).__name__, CLAUDE_MDM_DOMAIN))
        for n in top:
            p = os.path.join(CLAUDE_MDM_PLIST_DIR, n)
            if n == plist: files.append(p)
            elif os.path.isdir(p):
                if not os.access(p, os.R_OK | os.X_OK):
                    probs.append("claude: %s cannot be read; cannot verify a %s configuration profile" % (p, CLAUDE_MDM_DOMAIN))
                else:
                    files.append(os.path.join(p, plist))
    if sys.platform.startswith(("win", "cygwin")) or "microsoft" in platform.release().lower():
        probs.append("claude: Windows/WSL registry policies (HKLM/HKCU\\SOFTWARE\\Policies\\ClaudeCode) cannot be "
                     "checked by this harness; refusing live Claude calls")
    return files, probs

def _read_settings(p):
    if p.endswith(".plist"):
        import plistlib
        with open(p, "rb") as f: return plistlib.load(f)
    return rjson(p, {})

def claude_settings_auth_problems(paths):
    """API-key auth, provider routing or a non-Keychain token configured in any of these Claude settings files:
    apiKeyHelper or a NON_SUBSCRIPTION_ENV / IGNORED_AUTH_ENV entry in "env" (key names only, values never read into
    messages). Env scrubbing removes CLAUDE_CODE_OAUTH_TOKEN from the shell env only; a settings "env" entry would be
    set by the CLI itself after scrubbing (whether it then overrides the Keychain login is UNVERIFIED), so it
    is refused. A file that exists but cannot be parsed fails closed."""
    out = []
    for p in paths:
        if not os.path.lexists(p): continue
        try:
            st = _read_settings(p)
        except Exception:  # unreadable, not JSON/plist, broken symlink: cannot be verified
            out.append("claude: %s exists but is not readable JSON; cannot verify it sets no API-key auth" % p); continue
        if not isinstance(st, dict): continue
        env = st.get("env") if isinstance(st.get("env"), dict) else {}
        keys = (["apiKeyHelper"] if "apiKeyHelper" in st else []) + sorted(k for k in env if k in NON_SUBSCRIPTION_ENV)
        if keys:
            out.append("claude: %s configures API-key auth (%s; values not read); eval runs use subscription auth only"
                       % (p, ", ".join(keys)))
        tok = sorted(k for k in env if k in IGNORED_AUTH_ENV)
        if tok:
            out.append("claude: %s sets %s in \"env\" (value not read); eval runs use only the existing Keychain login "
                       "(`claude auth status`), not a token from settings" % (p, ", ".join(tok)))
    return out

def claude_project_settings(dirs):
    return [os.path.join(d, ".claude", f) for d in dirs for f in CLAUDE_SETTINGS_FILES]

def claude_launch_problems(cwd, config_dir=None, require_hooks=False):
    """Everything a `claude -p` started in cwd could load auth from and that env scrubbing does not cover: project /
    local settings of cwd and every ancestor, every managed source, and the server-managed cache of the config dir
    the call uses. Checked before every Claude launch (executor, grader, builder); nothing is spent on a refusal."""
    files, probs = claude_managed_sources()
    probs = list(probs)
    cfg = config_dir or claude_config_dir()
    remote = os.path.join(cfg, CLAUDE_REMOTE_SETTINGS)
    alt = switch_account_config_dirs() if config_dir is None else []  # accounts the shim may retry this call on
    remotes = [remote] + [os.path.join(d, CLAUDE_REMOTE_SETTINGS) for d in alt]
    alt_user = [os.path.join(d, f) for d in alt for f in CLAUDE_SETTINGS_FILES]
    if require_hooks:
        for p in files + remotes:
            if not os.path.lexists(p): continue
            try: st = _read_settings(p)
            except Exception: continue  # the existing auth/settings validation reports unreadable sources
            if isinstance(st, dict):
                if st.get("allowManagedHooksOnly") is True or st.get("disableAllHooks") is True:
                    probs.append("claude: managed hook policy in %s may disable the Bash gate" % p)
                if st.get("allowManagedPermissionRulesOnly") is True:
                    probs.append("claude: managed permission policy in %s may discard --allowedTools approvals" % p)
                if "policyHelper" in st:
                    probs.append("claude: managed policyHelper in %s may override the gated launch policy" % p)
    return api_key_env_problems() + probs + claude_settings_auth_problems(
        claude_project_settings(ancestors(cwd)) + files + remotes + alt_user) + \
        [q for d in alt for q in claude_undetectable_skill_problems(d)]

# Built-in plugins ship inside the CLI as "<name>@builtin" and load unless settings.enabledPlugins["<name>@builtin"]
# is false (https://code.claude.com/docs/en/plugins/mods/admin: enabledPlugins / pluginConfigs with @builtin ids;
# builtinPlugins.ts getBuiltinPlugins reads settings.enabledPlugins[pluginId], user value over defaultEnabled).
# disableBundledSkills does not cover them and --safe-mode keeps them. Static list = the cc-plugin-* names in the
# 2.1.300 binary; at launch the installed binary is scanned again and the union is turned off. Whether
# cc-plugin-sec-default honors false is not verified: if it (or anything else) still loads, the init check
# (plugins == [], skills exactly as expected) refuses the run; it is never relaxed.
CLAUDE_BUILTIN_PLUGIN_NAMES = ("cc-plugin-agents-md", "cc-plugin-claude-test", "cc-plugin-diff", "cc-plugin-mermaid",
                               "cc-plugin-mods-guide", "cc-plugin-plugin-authoring", "cc-plugin-responsive-mode",
                               "cc-plugin-sec-default", "cc-plugin-telemetry", "cc-plugin-tips",
                               "cc-plugin-you-should-know")
BUILTIN_PLUGIN_RE = re.compile(rb"cc-plugin-[a-z0-9]+(?:-[a-z0-9]+)*")
_BUILTIN_SCAN = {}  # cache: one binary scan per process, so every launch of a run gets the same --settings

SHIM_REAL_RE = re.compile(rb'CLAUDE_AS_REAL_CLAUDE"\)\s*or\s*"([^"]+)"')

def resolve_past_shim(p, environ=None):
    """The real CLI behind the ~/bin/claude account shim (claude-accts), chosen exactly as the shim chooses what it
    executes: $CLAUDE_AS_REAL_CLAUDE, else the absolute default written in the shim. A non-shim path is returned unchanged; a
    shim whose target cannot be found returns None (scan reports it, the static list still applies)."""
    environ = os.environ if environ is None else environ
    try:
        with open(p, "rb") as f: head = f.read(65536)
    except OSError: return p
    if not head.startswith(b"#!") or b"CLAUDE_AS_REAL_CLAUDE" not in head: return p
    m = SHIM_REAL_RE.search(head)
    real = environ.get("CLAUDE_AS_REAL_CLAUDE") or (m.group(1).decode("utf-8", "replace") if m else None)
    if not real or not os.path.isabs(real): return None
    real = os.path.realpath(real)
    return None if real == os.path.realpath(p) or not os.path.isfile(real) else real

def claude_binary_path(which=None, environ=None):
    p = (which or shutil.which)("claude")
    return resolve_past_shim(os.path.realpath(p), environ) if p else None

def scan_builtin_plugin_names(path, chunk=1 << 24):
    """cc-plugin-* names found in the file at path (read in overlapping chunks; nothing is executed)."""
    found, tail = set(), b""
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            data = tail + buf
            for m in BUILTIN_PLUGIN_RE.finditer(data):
                # a match touching the end of the buffer (or followed only by a trailing "-") may be cut off: it is
                # seen again whole in the next tail
                if len(m.group()) <= 64 and (m.end() < len(data) - 1 or not buf):
                    found.add(m.group().decode("ascii"))
            if not buf: break
            tail = data[-80:]
    return found

def claude_builtin_plugins(path=None, refresh=False):
    """{"ids": sorted "<name>@builtin" ids to turn off, "binary": scanned path or None, "scanned": names found in it,
    "error": why the scan failed or None}. The static list is always included, so a failed scan still turns off every
    known built-in plugin."""
    if refresh or not _BUILTIN_SCAN:
        path = path or claude_binary_path()
        found, err = set(), None
        if not path: err = "claude not found on PATH"
        else:
            try: found = scan_builtin_plugin_names(path)
            except OSError as ex: err = "%s: %s" % (type(ex).__name__, ex)
        _BUILTIN_SCAN.clear()
        _BUILTIN_SCAN.update(ids=sorted("%s@builtin" % n for n in set(CLAUDE_BUILTIN_PLUGIN_NAMES) | found),
                             binary=path, scanned=sorted(found), error=err)
    return dict(_BUILTIN_SCAN)

# Bundled skills are registered in the CLI through registerBundledSkill({name, ...}). disableBundledSkills turns them
# all off EXCEPT those registered with survivesBundledKillSwitch (2.1.300: getBundledSkills returns only
# bundledSkillKillSwitchSurvivors under the kill switch); those are 'design' and 'doctor', the two the first live run
# still loaded. https://code.claude.com/docs/en/skills ("Override skill visibility from settings"): disableBundledSkills
# leaves /doctor (v2.1.205+), hidden by DISABLE_DOCTOR_COMMAND or skillOverrides {"doctor": "off"}; "off" hides a skill
# from Claude and the / menu and applies to bundled skills (plugin skills: enabledPlugins). So every launch also sets
# skillOverrides "off" for every bundled name: the static list below (the registerBundledSkill names in the 2.1.300
# binary, including the template-registered ones the scan cannot resolve) plus what a launch-time scan of the installed
# binary finds. The skill under test is never overridden. The init check is unchanged: anything still loaded stops the run.
CLAUDE_BUNDLED_SKILL_NAMES = (
    "artifact-capabilities", "artifact-components", "artifact-data-table", "artifact-design", "artifact-diagramming",
    "artifact-explainer", "artifact-pr-review", "artifact-report", "batch", "claude-api", "claude-code-docs",
    "claude-in-chrome", "code-review", "commit", "cowork-plugin", "dataviz", "debug", "design", "design-sync", "doc",
    "doctor", "explain-usage", "fewer-permission-prompts", "keybindings-help", "loop", "memory-types", "pr", "prototype",
    "run", "run-skill-generator", "schedule", "setup-claude", "simplify", "slides", "update-config", "verify",
    "whiteboard", "workflow-authoring", "workshop")
CLAUDE_BUNDLED_KILL_SWITCH_SURVIVORS = ("design", "doctor")
_SKILL_NAME_RE = re.compile(rb"[a-z0-9][a-z0-9-]{0,63}")
_BUN_CHUNK = b"// @bun @bytecode"
_BUNDLED_SCAN = {}  # cache like _BUILTIN_SCAN

def _js_boundary(mm, i):
    """True when position i does not continue an identifier or member access (i is the start of a token)."""
    return i == 0 or not (mm[i - 1:i].isalnum() or mm[i - 1:i] in (b"_", b"$", b"."))

def _find_all(mm, needle, start=0, end=None):
    end = len(mm) if end is None else end
    i = mm.find(needle, start, end)
    while i >= 0:
        yield i
        i = mm.find(needle, i + 1, end)

def _assigned_names(mm, ident, start=0, end=None):
    """Distinct skill-name strings assigned as <ident>="<name>" in mm[start:end]."""
    out = set()
    for i in _find_all(mm, ident + b'="', start, end):
        if not _js_boundary(mm, i): continue
        j = i + len(ident) + 2
        m = _SKILL_NAME_RE.match(mm[j:j + 66])
        if m and mm[j + m.end():j + m.end() + 1] == b'"': out.add(m.group().decode("ascii"))
    return out

def scan_bundled_skill_names(path):
    """(names, survivors): the literal or resolvable names passed to registerBundledSkill in the file at path, and those
    registered with survivesBundledKillSwitch. The minified alias comes from `<alias> as registerBundledSkill`; a name
    given as an identifier is resolved from `<ident>="<name>"` in the same bundle chunk, else in the whole file, and
    only when exactly one value is found. Template names (`artifact-${e}`) stay unresolved. Nothing is executed;
    literal searches only (a regex scan of the ~200 MB binary takes tens of seconds)."""
    with open(path, "rb") as f:
        try: mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError: return set(), set()  # empty file
    with mm:
        aliases = set()
        tag = b" as registerBundledSkill"
        for i in _find_all(mm, tag):
            nxt = mm[i + len(tag):i + len(tag) + 1]
            if nxt.isalnum() or nxt in (b"_", b"$"): continue  # registerBundledSkillSessionReset, ...
            m = re.search(rb"[\w$]+$", mm[max(0, i - 40):i])
            if m: aliases.add(m.group())
        names, survivors, pending = set(), set(), []
        def add(n, surv):
            names.add(n)
            if surv: survivors.add(n)
        for alias in sorted(aliases):
            for i in _find_all(mm, alias + b"({name:"):
                if not _js_boundary(mm, i): continue
                j = i + len(alias) + 7
                body = mm[j:j + 3000].split(b"getPromptForCommand")[0]
                surv = b"survivesBundledKillSwitch:!0" in body
                lit = re.match(b'"(' + _SKILL_NAME_RE.pattern + b')"', body)
                if lit: add(lit.group(1).decode("ascii"), surv); continue
                ident = re.match(rb"([\w$]+)[,}]", body)
                if not ident: continue  # template literal or member access: static list only
                ident = ident.group(1)
                cs = mm.rfind(_BUN_CHUNK, 0, i); ce = mm.find(_BUN_CHUNK, j)
                local = _assigned_names(mm, ident, max(cs, 0), ce if ce >= 0 else None)
                if len(local) == 1: add(local.pop(), surv)
                elif not local: pending.append((ident, surv))
        for ident, surv in pending:
            found = _assigned_names(mm, ident)
            if len(found) == 1: add(found.pop(), surv)
    return names, survivors

def claude_bundled_skills(path=None, refresh=False):
    """{"names": sorted bundled skill names (static list + scan), "survivors": the kill-switch survivors among them,
    "binary", "scanned", "error"} — same shape and caching as claude_builtin_plugins."""
    if refresh or not _BUNDLED_SCAN:
        path = path or claude_binary_path()
        found, surv, err = set(), set(), None
        if not path: err = "claude not found on PATH"
        else:
            try: found, surv = scan_bundled_skill_names(path)
            except OSError as ex: err = "%s: %s" % (type(ex).__name__, ex)
        _BUNDLED_SCAN.clear()
        _BUNDLED_SCAN.update(names=sorted(set(CLAUDE_BUNDLED_SKILL_NAMES) | found),
                             survivors=sorted(set(CLAUDE_BUNDLED_KILL_SWITCH_SURVIVORS) | surv),
                             binary=path, scanned=sorted(found), error=err)
    return dict(_BUNDLED_SCAN)

def claude_settings(skill_name=None, bash_gate=None, edit_root=None, edit_mode=writable.ALLOWLIST_MODE):
    """The --settings object of every Claude launch: bundled skills off (kill switch plus skillOverrides "off" for every
    known bundled name except skill_name, the skill under test) and every known built-in plugin disabled. bash_gate
    adds the executor's PreToolUse gate; edit_root adds the builder's (edit_mode of engine/writable.MODES for that
    folder: edit-allowlist for a skill, agent-allowlist for an agent)."""
    if bash_gate and edit_root is not None: raise ValueError("one PreToolUse gate per launch")
    settings = {"disableBundledSkills": True,
            "enabledPlugins": {i: False for i in claude_builtin_plugins()["ids"]},
            "skillOverrides": {n: "off" for n in claude_bundled_skills()["names"] if n != skill_name}}
    gate = ([skill_name, bash_gate["script"], bash_gate["sha256"], bash_gate["python3"]] if bash_gate else
            [edit_mode, edit_root] if edit_root is not None else None)
    if gate:
        settings["disableAllHooks"] = False
        settings["hooks"] = {"PreToolUse": [{"matcher": "*", "hooks": [{
            "type": "command", "command": sys.executable,
            "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_bash_gate.py")] + gate,
            "onFailure": "block"}]}]}
    return settings

def claude_settings_json(skill_name=None, bash_gate=None, edit_root=None, edit_mode=writable.ALLOWLIST_MODE):
    return json.dumps(claude_settings(skill_name, bash_gate, edit_root, edit_mode), sort_keys=True)

# One sanitized launch shape for every claude -p call (executor, grader, builder): project/local setting sources only,
# no MCP servers, bundled skills and built-in plugins off, auto memory, claude.ai MCP servers and /doctor off,
# API-key/provider variables scrubbed.
CLAUDE_CHILD_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
                    "DISABLE_DOCTOR_COMMAND": "1"}

def claude_isolation_args(skill_name=None, bash_gate=None, edit_root=None, edit_mode=writable.ALLOWLIST_MODE):
    return ["--setting-sources", "project,local", "--strict-mcp-config", "--settings",
            claude_settings_json(skill_name, bash_gate, edit_root, edit_mode)]

def builtin_plugin_check_command():
    """Free check (plugin management only, no model call) of what the launch settings do to the built-in plugins."""
    return ["claude", "--setting-sources", "project,local", "--settings", claude_settings_json(), "plugin", "list",
            "--json"]

def builtin_plugin_check(run_fn=None):
    """Runs builtin_plugin_check_command and reports, per expected @builtin id, the enabled state it lists. Returns
    (exit code, lines): 0 only when every listed @builtin entry says enabled false and every id we disable is listed;
    otherwise 1 with what could not be confirmed. Loaded skills are not part of this output: confirming them needs
    the system/init event of a real `claude -p` (a model call), which this check never makes."""
    cmd = builtin_plugin_check_command()
    run_fn = run_fn or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=120,
                                                 env=claude_child_env()))
    info = claude_builtin_plugins()
    lines = ["binary scanned: %s (%s)" % (info["binary"], info["error"] or "%d names found" % len(info["scanned"])),
             "command: " + shlex.join(cmd)]
    try:
        p = run_fn(cmd)
        data = json.loads(p.stdout)
    except Exception as ex:
        return 1, lines + ["cannot confirm: %s" % type(ex).__name__]
    items = data if isinstance(data, list) else (data.get("plugins") or data.get("installed") or []) \
        if isinstance(data, dict) else []
    listed = {}
    for it in items:
        if isinstance(it, dict):
            pid = it.get("id") or it.get("source") or it.get("name")
            if isinstance(pid, str) and pid.endswith("@builtin"): listed[pid] = it.get("enabled")
    bad = sorted(i for i, v in listed.items() if v is not False)
    missing = sorted(set(info["ids"]) - set(listed))
    lines += ["%s enabled=%s" % (i, json.dumps(listed[i])) for i in sorted(listed)]
    if bad: lines.append("NOT disabled: %s" % ", ".join(bad))
    if missing: lines.append("not listed (state unconfirmed): %s" % ", ".join(missing))
    if not listed:
        lines.append("cannot confirm: the listing has no @builtin entry (2.1.300 `plugin list --json` lists installed "
                     "plugins only and `plugin details` shows no enabled state); only the system/init event of a real "
                     "`claude -p` call (a model call) shows what loaded")
    return (0 if listed and not bad and not missing else 1), lines

def claude_child_env(config_dir=None):
    env = dict(os.environ, **CLAUDE_CHILD_ENV)
    if config_dir: env["CLAUDE_CONFIG_DIR"] = config_dir
    return with_account_marker(scrub_env(env), ["claude"])

def launch_claude(exec_fn, cmd, cwd, timeout, env=None, skill_name=None, edit_allowlist=False,
                  edit_mode=writable.ALLOWLIST_MODE):
    """Grader and builder launch path: refuses (EvalError, before the call) unless cmd carries the isolation args (for
    skill_name, the skill the launch must not override; with edit_allowlist, the builder's gate in edit_mode for the
    realpath of cwd) and cwd passes claude_launch_problems; the child gets claude_child_env(). The executor uses the
    same args, env and checks through its Sandbox."""
    iso = claude_isolation_args(skill_name, edit_root=os.path.realpath(cwd) if edit_allowlist else None,
                                edit_mode=edit_mode)
    if cmd[:2] != ["claude", "-p"] or not any(cmd[i:i + len(iso)] == iso for i in range(len(cmd))):
        raise EvalError("refusing a claude call without the isolation arguments")
    probs = claude_launch_problems(cwd, require_hooks=edit_allowlist)
    if probs: raise EvalError("claude launch refused: " + " | ".join(probs))
    return exec_fn(cmd, cwd, timeout, env=claude_child_env() if env is None else env)

def new_result():
    return {"final": "", "tool_calls": [], "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
            "cost_usd": None, "error": None, "init": None}

def reported_cost(stdout):
    """Largest numeric total_cost_usd in any JSON line of a CLI output, or None. Used only when the output as a whole
    could not be parsed, so a reported cost above the cap is still charged."""
    best = None
    for line in (stdout or "").splitlines():
        try: ev = json.loads(line)
        except ValueError: continue
        v = ev.get("total_cost_usd") if isinstance(ev, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0:
            best = v if best is None else max(best, v)
    return None if best is None else float(best)

def check_extra_args(engine, args):
    """Rejects extra CLI args that would change the priced model or the per-call budget cap."""
    bad, args = [], list(args)
    for i, x in enumerate(args):
        for f in FORBIDDEN_EXTRA_ARGS.get(engine, ()):
            if x == f or x.startswith(f + "=") or (len(f) == 2 and x.startswith(f) and not x.startswith("--")):
                bad.append(x)
        if engine == "codex":  # -c/--config model=... (or model_provider=...) overrides the model
            val = x.split("=", 1)[1] if x.startswith(("--config=", "-c=")) else \
                (args[i + 1] if x in ("-c", "--config") and i + 1 < len(args) else None)
            if val is not None and val.strip().startswith("model"): bad.append(x if "=" in x else "%s %s" % (x, val))
        if engine == "claude" and (x == "--permission-mode" or x.startswith("--permission-mode=")):
            mode = args[i + 1] if x == "--permission-mode" and i + 1 < len(args) else x.partition("=")[2]
            if mode not in ("default", "acceptEdits", "dontAsk", "plan"):
                bad.append("--permission-mode %s" % mode)
    if bad:
        raise EvalError("%s extra args may not set the model, effort or budget (priced from --%s-model, effort from "
                        "--%s-effort): %s" % (engine, engine, engine, ", ".join(bad)))
    return args

class Engine:
    name = "?"
    skills_rel = None  # where a project-scoped skill is installed inside the run workdir
    has_call_cap = False  # True when the CLI takes a per-call USD cap
    EFFORTS = ()
    def __init__(self, model=None, exec_fn=None, extra_args=(), timeout=900, effort=None):
        self.model, self.exec_fn, self.timeout = model, exec_fn or live_exec, timeout
        if effort is not None and effort not in self.EFFORTS:
            raise EvalError("%s effort %r: one of %s" % (self.name, effort, "|".join(self.EFFORTS) or "(none supported)"))
        self.effort = effort
        self.extra_args = check_extra_args(self.name, extra_args)
    def label(self):
        return "%s(%s%s)" % (self.name, self.model or "CLI default", ", effort %s" % self.effort if self.effort else "")
    def install_skill(self, workdir, skill_dir, name):
        """Copy exactly the eva.unit_tree files of the skill, so the run holds what the English scan read (SKILL_IGNORE of
        engine/primitives/script_denylist.py is left out, evals/ among them: the eval set must not leak into the run). A link that leaves the folder, dangles
        or resolves in a loop of links, and a cycle, refuse the install. The name (SKILL.md frontmatter) must be one safe
        path component and the destination must stay inside the skills folder of the sandbox."""
        import eva
        if not safe_name(name):
            raise EvalError("skill name %r is not a safe path component; refusing to install it" % (name,))
        tree = eva.unit_tree(skill_dir)
        links = link_refusals(skill_dir, tree)
        if links: raise EvalError("skill folder %s has symlinks that leave it, dangle, loop or point back at a folder on "
                                  "their own path: %s; refusing to install it, nothing was copied"
                                  % (skill_dir, "; ".join(links)))
        dst = ensure_inside(os.path.join(workdir, self.skills_rel), os.path.join(workdir, self.skills_rel, name))
        os.makedirs(dst)
        for rel in tree.files:
            to = os.path.join(dst, *rel.split("/"))
            os.makedirs(os.path.dirname(to), exist_ok=True)
            shutil.copy2(os.path.join(skill_dir, *rel.split("/")), to)
        return dst
    def command(self, prompt, with_skill, budget_usd=None, skill_name=None): raise NotImplementedError
    def parse(self, stdout): raise NotImplementedError
    def triggered(self, result, skill_name): raise NotImplementedError
    def sandbox_env(self, root):
        """Environment for one isolated run whose sandbox folder is root (references/engine-cli.md "Eval isolation")."""
        return scrub_env(os.environ)
    def remove_credentials(self, root):
        """Deletes and verifies the removal of any credential copy in the sandbox; None or an error string."""
        return None
    def auth_record(self):
        """How the run authenticates (names only, never values)."""
        return {}
    def sandbox_problems(self, root, work, skill_name, with_skill, pins=()):
        """Static check of what the engine could discover from this sandbox, before the CLI starts. pins: the skill
        names an agent run installs at project scope (K2)."""
        found = sorted(n for n, _ in skills_under(os.path.join(work, self.skills_rel), 1))
        want = [skill_name] if with_skill else sorted(pins)
        out = [] if found == want else ["project skills in the sandbox are %s, expected %s" % (found, want)]
        for a in ancestors(os.path.dirname(os.path.realpath(root))):
            hits = skills_under(os.path.join(a, self.skills_rel), 3)
            if hits: out.append("ancestor %s has skills %s" % (os.path.join(a, self.skills_rel), sorted(n for n, _ in hits)))
        return out
    def isolation_problems(self, res, with_skill, skill_name, user_names, pins=()):
        """Checks of the finished run's own report of what it loaded (fail closed when it cannot be verified)."""
        return []
    def after_run(self, root):
        return []
    def run(self, prompt, workdir, with_skill, budget_usd=None, env=None, skill_name=None, ticket=None):
        """A call that raises after it may have started (timeout, crash, ...) is not re-raised: it comes back as an
        error result with no usage and launch_failed set, so the caller charges the cap/bound, records it and stops.
        Output that cannot be parsed after the call (e.g. nonnumeric usage) is handled the same way, with any
        CLI-reported cost kept so max(reported, cap) is charged. LiveDisabled is raised before anything starts and
        passes through. ticket: the admitted spend ledger call, whose intent row is written right before the start."""
        cmd = self.command(prompt, with_skill, budget_usd, skill_name=skill_name)
        t0, start = time.monotonic(), now_iso()
        failed = None
        if ticket is not None: ticket.launch()
        try:
            rc, out, err = self.exec_fn(cmd, workdir, self.timeout, env=env)
        except LiveDisabled:
            raise
        except Exception as ex:
            rc, out, err, failed = None, "", "", "call failed after launch (%s)" % type(ex).__name__
        try:
            res = self.parse(out or "")
        except Exception as ex:
            res = dict(new_result(), cost_usd=reported_cost(out))
            failed = failed or "output could not be parsed after the call (%s)" % type(ex).__name__
        res.update(returncode=rc, stderr_tail=(err or "")[-2000:], duration_ms=int((time.monotonic() - t0) * 1000),
                   executor_start=start, executor_end=now_iso(), command=cmd, launch_failed=failed)
        if failed: res["error"] = failed
        elif rc != 0 and not res["error"]: res["error"] = "exit %s" % rc
        return res

class ClaudeCodeEngine(Engine):
    """claude -p (https://code.claude.com/docs/en/headless, /cli-reference). Skill on: copied to
    <sandbox>/work/.claude/skills/<name>/ (project skill). Skill off: --disable-slash-commands ("Disable all skills").
    Both: --setting-sources project,local (user skills under ~/.claude/skills are the "user" source), no MCP servers,
    bundled skills off. Auth: only the existing claude.ai Keychain login, so HOME and CLAUDE_CONFIG_DIR are left alone
    (the macOS Keychain entry is keyed to the config dir) and CLAUDE_CODE_OAUTH_TOKEN is removed from the child env.
    The shared config dir still holds skill sources --setting-sources is not documented to hide: plugin skills load
    only through an enabled plugin, which the post-run init check sees (plugins must be []); claude.ai synced skills
    show up in init `skills` unless `user-invocable: false`, and those are refused before launch."""
    name, skills_rel, has_call_cap = "claude", ".claude/skills", True
    ENV = CLAUDE_CHILD_ENV
    EFFORTS = CLAUDE_EFFORTS
    def __init__(self, *args, python_script=None, skill_dir=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.python_script, self.skill_dir = python_script, skill_dir
    def bash_gate(self):
        if not self.python_script: return None
        if not self.skill_dir: raise EvalError("script gate needs the source skill directory")
        source = os.path.join(self.skill_dir, self.python_script)
        with open(source, "rb") as f: digest = hashlib.sha256(f.read()).hexdigest()
        python3 = shutil.which("python3")
        if not python3: raise EvalError("script gate needs python3 on PATH")
        return {"script": self.python_script, "sha256": digest, "python3": os.path.realpath(python3)}
    def script_allow_rule(self, skill_name):
        if not self.python_script: return None
        if not safe_name(skill_name): raise EvalError("script permission needs a safe skill name")
        return "Bash(python3 -I .claude/skills/%s/%s *)" % (skill_name, self.python_script)
    def command(self, prompt, with_skill, budget_usd=None, skill_name=None):
        if self.python_script and any(arg == "--permission-mode" or arg.startswith("--permission-mode=")
                                      for arg in self.extra_args):
            raise EvalError("a gated Python script pins --permission-mode default")
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
        cmd += claude_isolation_args(skill_name, self.bash_gate())
        if self.model: cmd += ["--model", self.model]
        if self.effort: cmd += ["--effort", self.effort]
        if budget_usd is not None: cmd += ["--max-budget-usd", "%.2f" % budget_usd]
        rule = self.script_allow_rule(skill_name)
        # CLI path rules with one leading slash anchor at this session's primary workdir.
        # Edit covers the built-in file-editing tools, including Write and NotebookEdit.
        if rule: cmd += ["--permission-mode", "default", "--allowedTools", "Edit(/**)," + rule,
                         "--disallowedTools", "Monitor", "PowerShell"]
        if not with_skill: cmd += ["--disable-slash-commands"]
        return cmd + self.extra_args
    def sandbox_env(self, root):
        """Subscription auth from the user's own session: the CLI reads the existing Keychain login of the unchanged
        HOME / CLAUDE_CONFIG_DIR. Nothing is asked of the user; no token is created, passed or written. API-key,
        provider-routing and CLAUDE_CODE_OAUTH_TOKEN variables are removed."""
        env = claude_child_env()
        env["SKILL_EVAL_WORKDIR"] = os.path.realpath(os.path.join(root, "work"))
        return env
    def auth_record(self):
        if api_key_mode():
            p = AUTH["profile"]
            return {"billing": p.billing, "method": "API key from %s (auth profile %s; value not recorded)"
                    % (", ".join(p.engines["claude"]["api_key_env"]) or "no listed variable", p.name)}
        return {"billing": BILLING, "method": "claude.ai login of the current user session (Keychain)"}
    def sandbox_problems(self, root, work, skill_name, with_skill, pins=()):
        """Before launch: the project skill set (Engine), no auth/provider setting in any source the run can load
        (claude_launch_problems: cwd + ancestors, managed sources, the server-managed cache of the config dir), no
        custom command files in the cwd or an ancestor, and no synced skill that the init check could not see
        (claude_undetectable_skill_problems). A pinned skill may not share a bundled skill name: the launch settings
        turn every bundled name off."""
        out = Engine.sandbox_problems(self, root, work, skill_name, with_skill, pins)
        out += ["pinned skill %r is also a bundled skill name: the launch turns it off and the init check cannot tell "
                "the two apart" % p for p in pins if p in claude_bundled_skills()["names"]]
        for a in ancestors(work):
            cd = os.path.join(a, ".claude", "commands")
            if os.path.isdir(cd) and any(f.endswith(".md") for _, _, fn in os.walk(cd) for f in fn):
                out.append("custom command files in %s would load like skills" % cd)
        out += claude_undetectable_skill_problems(claude_config_dir())
        for acfg in switch_account_config_dirs():  # the shim may retry this run on another account's config dir
            out += claude_undetectable_skill_problems(acfg)
        if skill_name and skill_name in claude_bundled_skills()["names"]:
            out.append("skill name %r is also a bundled skill: the launch cannot set it off without hiding the skill "
                       "under test, and the init check cannot tell the two apart" % skill_name)
        return out + claude_launch_problems(work, require_hooks=bool(self.python_script))
    def isolation_problems(self, res, with_skill, skill_name, user_names, pins=()):
        """From the stream-json system/init event: apiKeySource "none" (docs/en/agent-sdk/typescript ApiKeySource:
        "No API key. The session authenticates another way, such as a claude.ai login"), an explicit empty plugin
        list, and a skill set that is exactly {skill under test} (with_skill) or empty (without_skill); any extra
        skill, recognized or not, is a leak. With pins (an agent run, K2) a skill outside the pins is a leak and fewer
        listed pins is left to skills_loaded. This runs after the call, as an extra check: init lists user-invocable
        skills only, so the sources are enforced before launch (sandbox_problems). Missing fields fail closed."""
        init = res.get("init")
        if not isinstance(init, dict): return ["no system/init event: cannot verify which skills were loaded"]
        if not isinstance(init.get("skills"), list): return ["system/init has no skills list: cannot verify isolation"]
        names = {str(s.get("name") if isinstance(s, dict) else s) for s in init["skills"]}
        out = []
        src = init.get("apiKeySource")
        if api_key_mode():  # the real CLI value under an API key is UNVERIFIED until operator lane 8
            want = AUTH["profile"].engines["claude"]["api_key_env"]
            if src not in want:
                out.append("system/init apiKeySource is %s, expected one of %s (auth profile %s, API-key mode): cannot "
                           "verify the run used a listed key" % (repr(_shown(src)) if isinstance(src, str) else "missing",
                                                                 ", ".join(want) or "<none listed>", AUTH["profile"].name))
        elif src != "none":
            out.append("system/init apiKeySource is %s, expected \"none\" (subscription login): %s" % (
                repr(_shown(src)) if isinstance(src, str) else "missing",
                "the run may have used an API key" if isinstance(src, str) else "cannot verify subscription auth"))
        if not isinstance(init.get("plugins"), list):
            out.append("system/init has no plugins list: cannot verify that no plugin was loaded")
        elif init["plugins"]:
            ids = [str((p.get("source") or p.get("name")) if isinstance(p, dict) else p) for p in init["plugins"]]
            out.append("plugins loaded: %s" % ", ".join(ids))
        want = {skill_name} if with_skill else set(pins)
        if names - want if pins else names != want:
            user = sorted({n.split(":")[-1] for n in names - want} & set(user_names))
            out.append("skills loaded in %s run are %s, expected %s %s%s" % (
                "pinned" if pins else "with_skill" if with_skill else "without_skill", sorted(names),
                "only the pins" if pins else "exactly", sorted(want), "; user-level skills visible: %s" % user if user else ""))
        return out
    def parse(self, stdout):
        r, texts = new_result(), []
        for line in stdout.splitlines():
            try: ev = json.loads(line)
            except ValueError: continue
            if not isinstance(ev, dict): continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                r["init"] = {"skills": ev.get("skills"), "plugins": ev.get("plugins"), "apiKeySource": ev.get("apiKeySource")}
            elif ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict): continue
                    if b.get("type") == "tool_use": r["tool_calls"].append({"tool": b.get("name"), "input": b.get("input")})
                    elif b.get("type") == "text" and b.get("text"): texts.append(b["text"])
            elif ev.get("type") == "result":
                r["final"] = ev.get("result") if isinstance(ev.get("result"), str) else ""
                u = ev.get("usage") or {}
                cached = int(u.get("cache_read_input_tokens") or 0)
                r["input_tokens"] = int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + cached
                r["cached_input_tokens"], r["output_tokens"] = cached, int(u.get("output_tokens") or 0)
                if isinstance(ev.get("total_cost_usd"), (int, float)): r["cost_usd"] = float(ev["total_cost_usd"])
                if ev.get("is_error"): r["error"] = ev.get("subtype") or "is_error"
        if not r["final"] and texts: r["final"] = texts[-1]
        return r
    def triggered(self, result, skill_name):
        """Skill tool call naming the skill (agentskills.io optimizing-descriptions jq check; run_eval.py), or a
        Read of .../skills/<name>/SKILL.md (run_eval.py also watches Read)."""
        for c in result["tool_calls"]:
            inp = c.get("input") if isinstance(c.get("input"), dict) else {}
            if c.get("tool") == "Skill" and str(inp.get("skill", "")).split(":")[-1] == skill_name: return True
            if c.get("tool") == "Read" and str(inp.get("file_path", "")).replace("\\", "/").endswith("skills/%s/SKILL.md" % skill_name):
                return True
        return False

class CodexEngine(Engine):
    """codex exec --json (https://learn.chatgpt.com/docs/non-interactive-mode). Skill on: copied to
    <workdir>/.agents/skills/<name>/ (https://learn.chatgpt.com/docs/build-skills: Codex scans .agents/skills from
    the cwd up to the repo root). `-m <model>` is not on those pages (unverified); it is added only with --codex-model.
    Trigger detection is (UNVERIFIED): Codex documents no skill-invocation event, so a run counts as triggered when an
    item mentions .../skills/<name>/SKILL.md."""
    name, skills_rel = "codex", ".agents/skills"
    EFFORTS = CODEX_EFFORTS
    def __init__(self, *a, **k):
        Engine.__init__(self, *a, **k)
        self._auth_digest = {}  # sandbox root -> sha256 of the copied auth.json (in memory only, never written)
    def sandbox_env(self, root):
        """HOME=<sandbox>/home hides $HOME/.agents/skills; CODEX_HOME=<sandbox>/codex_home hides $CODEX_HOME/skills
        (user + plugin skills) and config.toml. Auth: a read-only copy (0400, in a 0700 folder) of the real
        $CODEX_HOME/auth.json, only when its auth_mode is ChatGPT. The copy is removed with the sandbox. The harness
        itself does not print or log it, but the agent under test runs as the same user and CAN read it (0400 does not
        stop the owner; Codex workspace-write permits reads), so its tokens may end up in the saved final answer,
        transcript or output files. That is why Codex is refused by default (--strict-budget) and is not part of the
        first live run. A symlink is not used: chmod on it would change the user's own file."""
        home, ch = os.path.join(root, "home"), os.path.join(root, "codex_home")
        os.makedirs(home, mode=0o700); os.makedirs(ch, mode=0o700); os.chmod(ch, 0o700)
        probs = codex_auth_problems()
        if probs: raise EvalError("; ".join(probs))
        src, dst = os.path.join(real_codex_home(), "auth.json"), os.path.join(ch, "auth.json")
        with open(src, "rb") as f: data = f.read()
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try: os.write(fd, data)
        finally: os.close(fd)
        os.chmod(dst, 0o400)
        self._auth_digest[root] = hashlib.sha256(data).hexdigest()
        return scrub_env(dict(os.environ, HOME=home, CODEX_HOME=ch))
    def auth_record(self):
        return {"billing": current_billing(), "method": "ChatGPT login (auth_mode chatgpt), read-only copy of auth.json (readable "
                "by the agent under test), removed after the run"}
    def sandbox_problems(self, root, work, skill_name, with_skill, pins=()):
        out = Engine.sandbox_problems(self, root, work, skill_name, with_skill, pins)
        for label, d in (("sandbox HOME", os.path.join(root, "home", ".agents", "skills")),
                         ("sandbox CODEX_HOME", os.path.join(root, "codex_home", "skills")),
                         ("admin", CODEX_ADMIN_SKILLS)):
            hits = [n for n, p in skills_under(d, 4) if os.sep + ".system" + os.sep not in p + os.sep]
            if hits: out.append("%s skills folder %s has %s" % (label, d, sorted(hits)))
        return out
    def after_run(self, root):
        """The copy must be unchanged. If Codex rewrote it (token refresh, UNVERIFIED), the refreshed login is deleted with
        the sandbox and the real auth.json may hold a superseded token: stop and tell the user to check."""
        want, p = self._auth_digest.pop(root, None), os.path.join(root, "codex_home", "auth.json")
        if want is None: return []
        try:
            with open(p, "rb") as f: same = (not os.path.islink(p)) and hashlib.sha256(f.read()).hexdigest() == want
        except OSError:
            same = False
        if same: return []
        return ["codex changed or removed the read-only auth.json copy in its isolated CODEX_HOME (token refresh?). "
                "The copy was deleted with the sandbox; your real auth.json was not touched but may be superseded: "
                "run `codex login status` (and `codex login` if needed) before the next run"]
    def remove_credentials(self, root, attempts=3):
        """Deletes <sandbox>/codex_home/auth.json on its own, before the rest of the sandbox, and verifies it is gone.
        A failed unlink is retried after making the folder writable; as a last resort the copy is made writable and
        truncated, so no token stays on disk even when the file itself cannot be removed. The path is checked
        lexically (the folder holding it must really be inside root), never by resolving auth.json itself: if the run
        replaced it by a symlink pointing outside, the link is unlinked and its target is left alone."""
        ch = os.path.join(root, "codex_home")
        p = os.path.join(ch, "auth.json")
        try:
            ensure_inside(root, ch)
        except EvalError:  # codex_home itself was swapped for a link out of the sandbox: never follow it
            return ("%s was replaced by a link out of the sandbox; it was not followed (the sandbox removal deletes "
                    "the link itself)" % ch)
        err = None
        for _ in range(attempts):
            if not os.path.lexists(p): return None
            try:
                os.unlink(p)
            except OSError as ex:
                err = "%s: %s" % (type(ex).__name__, ex.strerror or ex)
                try: os.chmod(ch, 0o700)
                except OSError: pass
        if not os.path.lexists(p): return None
        emptied = False
        try:
            if not os.path.islink(p):
                os.chmod(p, 0o600)
                with open(p, "w"): pass
                emptied = os.path.getsize(p) == 0
        except OSError:
            pass
        return ("the Codex auth.json copy %s could not be deleted (%s)%s: delete it yourself" % (
            p, err or "still present", "; its content was erased" if emptied else "; IT MAY STILL HOLD A TOKEN"))
    def command(self, prompt, with_skill, budget_usd=None, skill_name=None):
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--sandbox", "workspace-write"]
        if self.model: cmd += ["-m", self.model]
        if self.effort: cmd += ["-c", CODEX_EFFORT_CONFIG % self.effort]
        return cmd + self.extra_args + [prompt]
    def parse(self, stdout):
        """Raises ValueError when no line is a JSON event with a "type" (empty or unreadable stream) or when the stream
        has no terminal turn.completed / turn.failed event (truncated stream): Engine.run then marks the call failed,
        so max(reported, cap/bound) is charged and the iteration stops."""
        r, events, terminal = new_result(), 0, False
        for line in stdout.splitlines():
            try: ev = json.loads(line)
            except ValueError: continue
            if not isinstance(ev, dict) or not isinstance(ev.get("type"), str): continue
            events += 1
            t = ev.get("type")
            if t == "item.completed" and isinstance(ev.get("item"), dict):
                it = ev["item"]
                if it.get("type") == "agent_message": r["final"] = it.get("text") or r["final"]
                elif it.get("type") == "command_execution":
                    r["tool_calls"].append({"tool": "command_execution", "input": {"command": it.get("command")}})
                else:
                    r["tool_calls"].append({"tool": it.get("type"), "input": it})
            elif t == "turn.completed":
                terminal = True
                u = ev.get("usage") or {}
                r["input_tokens"] += int(u.get("input_tokens") or 0)
                r["cached_input_tokens"] += int(u.get("cached_input_tokens") or 0)
                # reasoning_output_tokens counted as output: upper bound for cost (billing rule not verified)
                r["output_tokens"] += int(u.get("output_tokens") or 0) + int(u.get("reasoning_output_tokens") or 0)
            elif t in ("turn.failed", "error"):
                terminal = terminal or t == "turn.failed"
                r["error"] = json.dumps(ev.get("error") or ev.get("message") or t, ensure_ascii=False)[:500]
        if not events: raise ValueError("codex output has no readable JSON event")
        if not terminal: raise ValueError("codex output has no turn.completed or turn.failed event (truncated stream)")
        return r
    def triggered(self, result, skill_name):
        needle = "skills/%s/SKILL.md" % skill_name
        return any(needle in json.dumps(c.get("input"), ensure_ascii=False) for c in result["tool_calls"])

ENGINES = {"claude": ClaudeCodeEngine, "codex": CodexEngine}
# Multi-tool parity (owner decisions 2026-10-19 18:43, 2026-10-30): a skill is created for Claude Code, OpenCode and Hermes
# Agent together. Only claude and codex have an engine; OpenCode and Hermes engines do not exist and are never faked.
# codex stays a key: agent_cells passes it to cell_table and targets/codex.json names its engine. tool_coverage reads
# only eva.TOOLS.
TOOL_ENGINES = {"claude": "claude", "codex": "codex", "opencode": None, "hermes": None}
UNAVAILABLE_ENGINES = {"opencode": "OpenCode", "hermes": "Hermes Agent"}
NOT_AVAILABLE = "not-run: engine not available"
NOT_SELECTED = "not-run: engine not selected"
NOT_GRADED = "not-run: no graded runs"
PENDING = "pending: runs collected, none graded yet"

def tool_coverage(engine_names=None, graded=None, collected=None):
    """Per tool whether the dynamic eval ran: {tool: {"status", "engine", "graded_runs", "pending_runs"}}. status is "run"
    (at least one run graded, the summarize() criterion: pass_rate is not None; NOT "passed"), "planned" (plan time, nothing
    ran yet), "pending: ..." (runs were collected but none is graded yet) or "not-run: <reason>". engine_names: engines
    selected (None: unknown, treated as all available ones). graded: {engine: graded run count} of a finished iteration
    (None: plan time). collected: {engine: collected run count} (default: equal to graded). OpenCode and Hermes are always
    "not-run: engine not available"."""
    import eva
    out = {}
    for tool in eva.TOOLS:
        eng = TOOL_ENGINES[tool]
        n = (graded or {}).get(eng, 0) if eng else 0
        c = (collected if collected is not None else graded or {}).get(eng, 0) if eng else 0
        if eng is None: status = NOT_AVAILABLE
        elif engine_names is not None and eng not in engine_names: status = NOT_SELECTED
        elif graded is None: status = "planned"
        elif n > 0: status = "run"
        else: status = PENDING if c > n else NOT_GRADED
        out[tool] = {"status": status, "engine": eng, "graded_runs": n, "pending_runs": max(0, c - n)}
    return out

def runs_coverage(planned, runs):
    """tool_coverage of an iteration from its run records and its planned engines (None: the engines that ran)."""
    seen = {r["engine"] for r in runs}
    return tool_coverage(list(planned) if planned is not None else sorted(seen),
                         {e: sum(1 for r in runs if r["engine"] == e and r["result"]["pass_rate"] is not None) for e in seen},
                         {e: sum(1 for r in runs if r["engine"] == e) for e in seen})

def coverage_line(cov):
    return ", ".join("%s=%s" % (t, c["status"]) for t, c in cov.items())

# ---------- per-run isolation (references/engine-cli.md "Eval isolation") ----------
def ancestors(path):
    out, d = [], os.path.realpath(path)
    while True:
        out.append(d)
        if os.path.dirname(d) == d: return out
        d = os.path.dirname(d)

def skills_under(root, max_depth):
    """[(name, path)] of folders below root (depth <= max_depth) that hold a SKILL.md. Depth is counted on the visible
    path under root (a direct link to a deep folder is depth 1), not on the link target. Breadth-first, so each real
    folder is first reached by its shallowest visible path (a deeper alias seen first cannot hide it). Follows
    symlinked folders once; dangling links and unreadable folders are skipped, never raised."""
    out, seen = [], set()
    if not os.path.isdir(root): return out
    level = [root]
    for depth in range(max_depth + 1):
        nxt = []
        for dp in level:
            real = os.path.realpath(dp)
            if real in seen: continue
            seen.add(real)
            if depth and os.path.isfile(os.path.join(dp, "SKILL.md")):
                out.append((os.path.basename(dp), dp)); continue
            try: names = sorted(os.listdir(dp))
            except OSError: continue
            nxt += [os.path.join(dp, d) for d in names if os.path.isdir(os.path.join(dp, d))]
        level = nxt
    return out

def real_codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")

def claude_user_skills(base, config_dir=False):
    """[(name, path)] of user-level Claude skills: <config>/skills (incl. claude.ai synced/<id>/<name>) and plugins.
    base is a home folder (config = base/.claude) or, with config_dir=True, the config dir itself."""
    cfg = base if config_dir else os.path.join(base, ".claude")
    out = skills_under(os.path.join(cfg, "skills"), 3)
    pc = os.path.join(cfg, "plugins")
    for root, dirs, _ in os.walk(pc, onerror=lambda e: None) if os.path.isdir(pc) else []:
        if os.path.basename(root) == "skills":
            out += [(d, os.path.join(root, d)) for d in dirs if os.path.isfile(os.path.join(root, d, "SKILL.md"))]
            dirs[:] = []
        if root.count(os.sep) - pc.count(os.sep) > 6: dirs[:] = []
    return out

def _not_user_invocable(skill_dir):
    """True when SKILL.md frontmatter says `user-invocable: false` (such a skill loads but is missing from the
    system/init skills list, docs/en/agent-sdk/skills). An unreadable SKILL.md counts as not verifiable (True)."""
    import eva
    text, err = eva.read_text(os.path.join(skill_dir, "SKILL.md"))
    if err: return True
    meta = eva.parse_frontmatter(text)[0] or {}
    return str(meta.get("user-invocable", "")).strip().lower() == "false"

def claude_shared_skill_sources(cfg):
    """(synced, plugin) [(name, path)] in the user's config dir that --setting-sources project,local is not documented
    to hide. Plain <cfg>/skills/<name> folders are the "user" source (docs/en/agent-sdk/skills: include 'user' "to
    keep your personal skills") and are left out."""
    top = os.path.normpath(os.path.join(cfg, "skills"))  # logical path: a symlinked <cfg>/skills/<name> is still plain
    synced, plugin = [], []
    for n, p in claude_user_skills(cfg, config_dir=True):
        q = os.path.normpath(p)
        if os.path.dirname(q) == top: continue
        (synced if q.startswith(top + os.sep) else plugin).append((n, p))
    return synced, plugin

def claude_undetectable_skill_problems(cfg):
    """The runs share the user's config dir (Keychain login). A claude.ai synced skill that loads would be listed in
    system/init `skills` and stop the run, unless it is `user-invocable: false`: those are refused before launch.
    Plugin skills need an enabled plugin, which init `plugins` reports, so they are checked after the call."""
    synced, _ = claude_shared_skill_sources(cfg)
    hits = sorted({"%s (%s)" % (n, p) for n, p in synced if _not_user_invocable(p)})
    if not hits: return []
    return ["claude: %d synced skill(s) in %s are `user-invocable: false` (or unreadable), so the post-run init check "
            "could not see them if --setting-sources project,local does not hide them (not verified): %s"
            % (len(hits), cfg, ", ".join(hits[:5]) + (" ..." if len(hits) > 5 else ""))]

def claude_shared_skill_notes(cfg):
    """Plan-only information (not a problem): the user-level skill sources left to the post-run init check."""
    synced, plugin = claude_shared_skill_sources(cfg)
    if not (synced or plugin): return []
    return ["claude: %d synced and %d plugin skill(s) in %s are not hidden before launch (Keychain login shares the "
            "config dir); if any loads, the post-run init check (skills exactly as expected, plugins == []) stops "
            "the iteration after that run" % (len(synced), len(plugin), cfg)]

def escaping_links(root, tree=None):
    """Paths (relative to root, forward slashes) of symlinks a copy of root would follow out of root, or that dangle:
    the outside links and the dangling files of eva.unit_tree (tree, when the caller already has it). The
    SKILL_IGNORE entries of engine/primitives/script_denylist.py are not copied, so they are not checked."""
    import eva
    tree = tree or eva.unit_tree(root)
    return sorted(set(tree.outside) | {r for r in tree.files if not os.path.exists(os.path.join(root, *r.split("/")))})

def _resolves_in_loop(path):
    try: os.stat(path)
    except OSError as e: return e.errno == errno.ELOOP
    return False

def link_refusals(root, tree):
    """One eva reason per link of tree (the eva.unit_tree of root) that a copy cannot hold: outside links (LINK_OUT),
    dangling links (LINK_DANGLING), links that resolve in a loop (LINK_LOOP), then cycles (LINK_CYCLE)."""
    import eva
    broken = [r for r in escaping_links(root, tree) if r not in tree.outside]
    loops = [r for r in broken if _resolves_in_loop(os.path.join(root, *r.split("/")))]
    return ([eva.LINK_OUT % r for r in tree.outside] + [eva.LINK_DANGLING % r for r in broken if r not in loops]
            + [eva.LINK_LOOP % r for r in loops] + [eva.LINK_CYCLE % r for r in tree.cycles])

RMTREE_ATTEMPTS = 3

def remove_tree(root, path, attempts=RMTREE_ATTEMPTS, delay=0.2):
    """Deletes path (inside root) and verifies it is gone. A failed attempt (e.g. a folder without write permission,
    a file still being released) is retried after making every folder under path owner-writable. Returns None, or
    an error string when the folder is still there (the caller surfaces it: it may hold the Codex auth.json copy)."""
    ensure_inside(root, path)
    err = None
    for i in range(attempts):
        if not os.path.lexists(path): return None
        try:
            shutil.rmtree(path)
        except OSError as ex:
            err = "%s: %s" % (type(ex).__name__, ex.strerror or ex)
            for dp, dn, _ in os.walk(path, onerror=lambda e: None):
                try: os.chmod(dp, 0o700)
                except OSError: pass
        if not os.path.lexists(path): return None
        if i + 1 < attempts: time.sleep(delay)
    return ("sandbox %s could not be removed after %d attempts (%s); it may hold the read-only Codex auth.json copy: "
            "delete it yourself" % (path, attempts, err or "still present"))

class Sandbox:
    """One fresh temp folder per run: <run_root>/skill-eval-<engine>-XXXX/{work, home, codex_home}. work holds the
    case's fixture files plus, for with_skill, only the skill under test. Removed after the run."""
    def __init__(self, engine, spec, case, skill_path, run_root):
        self.engine, self.run_root = engine, os.path.realpath(run_root)
        self.root = tempfile.mkdtemp(prefix="skill-eval-%s-" % engine.name, dir=self.run_root)  # mode 0700
        try:
            self.work = os.path.join(self.root, "work"); os.makedirs(self.work)
            for f in case.get("files") or []:  # a fixture must resolve (symlinks included) inside the skill folder
                src = os.path.join(spec["skill_dir"], f)
                try:
                    ensure_inside(spec["skill_dir"], src)
                except EvalError:
                    raise EvalError("fixture %r resolves outside the skill folder %s; refusing to copy it"
                                    % (f, spec["skill_dir"]))
                dst = ensure_inside(self.work, os.path.join(self.work, f)); os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            self.name, self.with_skill = spec["skill_name"], bool(skill_path)
            if skill_path: engine.install_skill(self.work, skill_path, self.name)
            self.env = engine.sandbox_env(self.root)
        except BaseException as ex:  # never leave a half-built sandbox (it may hold the auth copy)
            left = "; ".join(remove_sandbox(engine, self.run_root, self.root))
            if left and isinstance(ex, Exception): raise EvalError("%s; and %s" % (ex, left)) from ex
            if left: print("WARNING: " + left, file=sys.stderr)
            raise
    def problems(self):
        return self.engine.sandbox_problems(self.root, self.work, self.name, self.with_skill)
    def record(self):
        """What the run saw (names and paths only; no environment values)."""
        return {"sandbox": self.root, "with_skill": self.with_skill, "auth": self.engine.auth_record(),
                "env_overrides": sorted(k for k in self.env if os.environ.get(k) != self.env[k]),
                "env_removed": sorted(k for k in os.environ if k not in self.env)}
    def copy_back(self, dst):
        """Keeps the run's output files for static checks (file_exists), without the installed skill copy."""
        skills = os.path.realpath(os.path.join(self.work, self.engine.skills_rel))
        ign = lambda d, names: [self.name] if os.path.realpath(d) == skills else []
        shutil.copytree(self.work, dst, symlinks=True, ignore=ign)
    def cleanup(self):
        """Always removes the sandbox and verifies it is gone: first the credential copy on its own (Codex auth.json,
        after after_run compared it), then the rest. Returns after_run problems plus any removal failure (fatal for
        the caller: the iteration stops and reports it)."""
        problems = []
        try:
            problems = list(self.engine.after_run(self.root))
        finally:
            problems += remove_sandbox(self.engine, self.run_root, self.root)
        return problems

def remove_sandbox(engine, run_root, root):
    """Credential copy first, then the whole tree in its own finally: the temp folder is removed even when the
    credential step fails or raises. Returns the problems of both steps."""
    out = []
    try:
        try:
            left = engine.remove_credentials(root)
        except Exception as ex:
            left = "removing the credential copy in %s failed (%s)" % (root, type(ex).__name__)
        if left: out.append(left)
    finally:
        left = remove_tree(run_root, root)
        if left: out.append(left)
    return out

def isolated_run(eng, prompt, spec, case, skill_path, run_dir, run_root, cap, user_names, keep_workdir, ticket=None):
    """Runs one CLI call in a fresh Sandbox and returns (result, fatal problems). Raises EvalError before the call
    (fail closed, nothing spent) when the sandbox could expose another skill. Afterwards, a leak shown by the run's
    own init report, or Codex changing its read-only auth copy, is returned as fatal: the caller charges the run, records it
    as an error and stops the iteration."""
    sb = Sandbox(eng, spec, case, skill_path, run_root)
    try:
        probs = sb.problems()
        if probs: raise EvalError("isolation (%s): %s" % (eng.name, " | ".join(probs)))
        res = eng.run(prompt, sb.work, with_skill=bool(skill_path), budget_usd=cap, env=sb.env, skill_name=sb.name,
                      ticket=ticket)
        try:  # the call has happened: a failure from here on is charged and stops, never escapes uncharged
            iso = eng.isolation_problems(res, bool(skill_path), spec["skill_name"], user_names)
            res["isolation"] = dict(sb.record(), problems=iso)
            if keep_workdir:
                wd = ensure_inside(run_dir, os.path.join(run_dir, "workdir"))
                if os.path.islink(wd): os.remove(wd)
                elif os.path.isdir(wd): shutil.rmtree(wd)
                sb.copy_back(wd)
        except Exception as ex:
            iso = []
            why = "%s: %s" % (type(ex).__name__, ex) if isinstance(ex, EvalError) else type(ex).__name__
            res["launch_failed"] = res.get("launch_failed") or "output handling failed after the call (%s)" % why
            res["error"] = res["launch_failed"]
    except BaseException as ex:
        after = sb.cleanup()
        if after and isinstance(ex, Exception):  # a leftover sandbox is never silent
            raise EvalError("%s; and %s" % (ex, "; ".join(after))) from ex
        if after: print("WARNING: " + "; ".join(after), file=sys.stderr)
        raise
    after = sb.cleanup()
    fatal = iso + after
    if fatal and not res.get("error"): res["error"] = "isolation: " + "; ".join(fatal)
    return res, fatal

# ---------- LLM grader (yes/no rubric) and blind pairwise comparator ----------
GRADE_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "required": ["id", "answer", "evidence"], "properties": {
        "id": {"type": "string"}, "answer": {"type": "string", "enum": ["yes", "no"]}, "evidence": {"type": "string"}}}}}}
COMPARE_SCHEMA = {"type": "object", "required": ["winner", "reasoning"], "properties": {
    "winner": {"type": "string", "enum": ["A", "B", "TIE"]}, "reasoning": {"type": "string"}}}
TRANSCRIPT_CHARS = 20000

def transcript_text(t):
    calls = json.dumps(t.get("tool_calls") or [], ensure_ascii=False)
    if len(calls) > TRANSCRIPT_CHARS: calls = calls[:TRANSCRIPT_CHARS] + " ...[truncated]"
    return "Final message:\n%s\n\nTool calls (JSON):\n%s" % (t.get("final") or "", calls)

class LLMGrader:
    """Grades with `claude -p --json-schema` and no tools (`--tools ""`). Its model tier must differ from every executor
    and from the builder (grader_tier_problems). With a finite budget every call gets --max-budget-usd
    (CLAUDE_CAP_HEADROOM of what is left)."""
    engine = "claude"
    EFFORTS = CLAUDE_EFFORTS
    def __init__(self, model=DEFAULT_GRADER_MODEL, exec_fn=None, timeout=300, budget=None, prices=None, effort=None):
        self.model, self.exec_fn, self.timeout, self.budget = model, exec_fn or live_exec, timeout, budget
        self.prices = prices or {}
        if effort is not None and effort not in self.EFFORTS:
            raise EvalError("%s grader effort %r: one of %s" % (self.engine, effort, "|".join(self.EFFORTS)))
        self.effort = effort
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "api_equivalent_usd": 0.0}
    def label(self):
        return grader_label(self.engine, self.model, self.effort)
    def command(self, prompt, schema, cap):
        cmd = ["claude", "-p", prompt, "--model", self.model, "--output-format", "json", "--json-schema",
               json.dumps(schema), "--tools", "", "--no-session-persistence", "--disable-slash-commands"]
        if self.effort: cmd += ["--effort", self.effort]
        cmd += claude_isolation_args()
        return cmd + (["--max-budget-usd", "%.2f" % cap] if cap is not None else [])
    def _charge_unknown(self, fallback, ticket=None, outcome="launch_failed", reported=None):
        self.usage["calls"] += 1; self.usage["api_equivalent_usd"] += fallback
        if self.budget is not None: self.budget.charge(fallback)
        spend_settle(ticket, self.budget, outcome, reported, 0, 0, price_info("grader", self.model, self.prices),
                     fallback, api_equivalent(0, 0, reported, None, fallback)[1])
    def _call(self, prompt, schema):
        cap, stop = None, None
        if self.budget is not None and self.budget.finite():
            cap = self.budget.claude_cap()
            if cap < 0.01:
                stop = "budget exhausted ($%.2f of $%.2f) before grader call" % (self.budget.spent, self.budget.limit)
        kind = "pairwise" if "winner" in schema.get("properties", {}) else "grader"
        bound = call_bound("grader", self.model, kind, self.prices, DEFAULT_TOKENS) or 0.0
        fallback = cap if cap is not None else bound  # what an unmetered call is charged
        scope = getattr(self, "spend_scope", None) or {}
        ticket, stop = spend_admit(self.budget, scope.get("kind") or kind, "claude", self.model, scope.get("iteration"),
                                   cap, fallback, True, stop)
        if stop: raise EvalError(stop)
        # A fresh empty cwd (no project settings, CLAUDE.md or skills of the folder grading was started from).
        cwd = tempfile.mkdtemp(prefix="skill-eval-grader-")
        try:
            if ticket is not None: ticket.launch()
            try:
                rc, out, err = launch_claude(self.exec_fn, self.command(prompt, schema, cap), cwd, self.timeout)
            except (EvalError, LiveDisabled):
                if ticket is not None: ticket.not_launched(self.budget.spent)
                raise  # refused before launch: nothing spent
            except Exception as ex:
                self._charge_unknown(fallback, ticket)
                raise EvalError("grader call failed after launch (%s); charged $%.2f and stopped" % (type(ex).__name__, fallback))
        finally:
            left = remove_tree(os.path.dirname(cwd), cwd)
            if left: print("WARNING: " + left, file=sys.stderr)
        try: data = json.loads(out)
        except (ValueError, TypeError):
            # usage unknown: charge the cap
            self._charge_unknown(max(fallback, reported_cost(out) or 0.0), ticket, "unparsed", reported_cost(out))
            raise EvalError("grader returned non-JSON (exit %s): %s" % (rc, (err or out or "")[-300:]))
        if not isinstance(data, dict): data = {}
        try:
            tin, tout = claude_usage(data)
        except Exception as ex:  # paid call, unreadable usage: charge max(reported, cap) and stop
            charged = api_equivalent(0, 0, data.get("total_cost_usd"), None, fallback)[0]
            self._charge_unknown(charged, ticket, "unparsed", data.get("total_cost_usd"))
            raise EvalError("grader usage could not be parsed after the call (%s); charged $%.2f and stopped"
                            % (type(ex).__name__, charged))
        pinfo = price_info("grader", self.model, self.prices)
        cost, source = api_equivalent(tin, tout, data.get("total_cost_usd"), pinfo[0], fallback)
        self.usage["calls"] += 1
        self.usage["input_tokens"] += tin; self.usage["output_tokens"] += tout; self.usage["api_equivalent_usd"] += cost
        if self.budget is not None: self.budget.charge(cost)
        spend_settle(ticket, self.budget, "error" if rc != 0 or data.get("is_error") else "ok", data.get("total_cost_usd"),
                     tin, tout, pinfo, cost, source)
        so = data.get("structured_output")
        if not isinstance(so, dict): raise EvalError("grader output has no structured_output")
        return so
    def grade(self, case, transcript, items):
        """items: [{"id", "text"}] -> {id: (passed bool|None, evidence)}"""
        rubric = "\n".join("- [%s] %s" % (it["id"], it["text"]) for it in items)
        prompt = ("You are grading one run of an AI coding agent against a yes/no rubric.\n"
                  "Answer each rubric item yes or no. Require concrete evidence quoted from the run for yes; "
                  "if the run does not show it, answer no.\n\nTask prompt:\n%s\n\nExpected outcome:\n%s\n\n%s\n\n"
                  "Rubric items:\n%s\n" % (case["prompt"], case["expected_output"], transcript_text(transcript), rubric))
        so = self._call(prompt, GRADE_SCHEMA)
        out = {it["id"]: (None, "grader returned no answer") for it in items}
        for a in so.get("items") or []:
            if isinstance(a, dict) and a.get("id") in out and a.get("answer") in ("yes", "no"):
                out[a["id"]] = (a["answer"] == "yes", str(a.get("evidence") or ""))
        return out
    def compare(self, case, out_a, out_b):
        prompt = ("Two AI agent runs attempted the same task. You do not know which configuration produced which.\n"
                  "Judge which run better accomplishes the task (correctness, completeness, following the request). "
                  "Answer A, B or TIE.\n\nTask prompt:\n%s\n\nExpected outcome:\n%s\n\n=== Run A ===\n%s\n\n=== Run B ===\n%s\n"
                  % (case["prompt"], case["expected_output"], transcript_text(out_a), transcript_text(out_b)))
        so = self._call(prompt, COMPARE_SCHEMA)  # charged by _call
        w = so.get("winner")
        if w not in ("A", "B", "TIE"):  # never read as a TIE: the comparison failed
            raise JudgeAnswerError("pairwise judge winner %r is not A, B or TIE; the call was charged and grading "
                                   "stopped" % (str(w)[:40],), w)
        return w, str(so.get("reasoning") or "")

def grader_label(engine, model, effort=None):
    return "%s(%s%s)" % (engine, model or "CLI default model", ", effort %s" % effort if effort else "")

def model_tier(model):
    """The tier word of a model name, or None when it holds no tier word or two different ones."""
    words = set(re.split("[^a-z0-9]+", model.lower())) if isinstance(model, str) else set()
    tiers = words.intersection(MODEL_TIERS)
    return tiers.pop() if len(tiers) == 1 else None

def grader_tier_problems(grader_model, under_test, builder_model=None):
    """[] when the grader tier differs from each model-under-test tier and the builder tier, else one line per problem.
    under_test: [(label, model)]. A model that is None or has no tier is a problem (fail closed)."""
    tiers = "|".join(MODEL_TIERS)
    grader, out = model_tier(grader_model), []
    if grader is None:
        out.append("grader model %s has no single tier word (%s); pick another --grader-model" % (grader_model, tiers))
    rows = [("%s model under test" % label, m) for label, m in under_test]
    if builder_model is not None: rows.append(("builder model", builder_model))
    for who, m in rows:
        tier = model_tier(m)
        if tier is None:
            out.append("%s %s has no single tier word (%s); this gate cannot show that the grader differs"
                       % (who, m or "(CLI default)", tiers))
        elif tier == grader:
            out.append("grader %s has the same tier (%s) as %s %s; pick another --grader-model"
                       % (grader_model, tier, who, m))
    return out

def make_grader(engine, model=None, effort=None, **kw):
    if engine not in GRADER_ENGINES: raise EvalError("unknown grader engine %r (%s)" % (engine, "|".join(GRADER_ENGINES)))
    return LLMGrader(model or DEFAULT_GRADER_MODEL, effort=effort, **kw)

# ---------- cost estimate ----------
def parse_price_overrides(items):
    out = {}
    for s in items or []:
        k, _, v = s.partition("=")
        try:
            i, o = (float(x) for x in v.split(","))
        except ValueError:
            raise EvalError("--price expects KEY=IN,OUT (USD per million tokens), got %r" % s)
        if not k or not all(math.isfinite(x) and x > 0 for x in (i, o)):
            raise EvalError("--price %r: KEY and finite prices > 0 required" % s)
        out[k] = (i, o)
    return out

def parse_token_overrides(items):
    out = dict(DEFAULT_TOKENS)
    for s in items or []:
        k, _, v = s.partition("=")
        if k not in DEFAULT_TOKENS: raise EvalError("--tokens key must be one of %s" % "|".join(DEFAULT_TOKENS))
        try:
            i, o = (int(x) for x in v.split(","))
        except ValueError:
            raise EvalError("--tokens expects KIND=IN,OUT, got %r" % s)
        if i <= 0 or o <= 0: raise EvalError("--tokens %r: token counts must be > 0" % s)
        out[k] = (i, o)
    return out

def price_for(engine, model, prices):
    """prices: overrides keyed by engine name or model id. Returns (in, out) or None when unknown."""
    if engine in prices: return prices[engine]
    if model and model in prices: return prices[model]
    if model and model in PRICES: return PRICES[model]
    return None

_PRICE_ROWS = {}

def price_info(engine, model, prices):
    """(price, source, read_on) behind price_for: an override is "--price" with no date; a PRICES row carries the source
    and read_on date of policy/pricing.json (read once, only for spend records)."""
    p = price_for(engine, model, prices)
    if p is None: return None, None, None
    if engine in prices or (model and model in prices): return p, "--price", None
    if "rows" not in _PRICE_ROWS:
        try:
            with open(os.path.join(POLICY_DIR, "pricing.json"), encoding="utf-8") as f:
                _PRICE_ROWS["rows"] = json.load(f)["values"]["PRICES"]["value"]
        except (OSError, ValueError, KeyError, TypeError):
            _PRICE_ROWS["rows"] = {}
    row = _PRICE_ROWS["rows"].get(model) if isinstance(_PRICE_ROWS["rows"], dict) else None
    row = row if isinstance(row, dict) else {}
    return p, row.get("source"), row.get("read_on")

def call_cost(tokens, price):
    return None if price is None else (tokens[0] * price[0] + tokens[1] * price[1]) / 1e6

def grader_call_usd(kind, model, tokens, prices):
    """Estimated USD of one grader/pairwise call."""
    return call_cost(tokens[kind], price_for("grader", model, prices))

def estimate(n_cases, n_llm_cases, n_triggers, runs, engines, baseline, grader_model, tokens=None, prices=None,
             pairwise=True):
    """engines: [(engine_name, executor_model)]. One grader call per run that has llm items; one pairwise call per
    (case, run) when two configurations are compared; trigger queries run only in configurations that have a skill
    (with_skill, plus old_skill when baseline is old)."""
    tokens, prices = tokens or DEFAULT_TOKENS, prices or {}
    n_configs = len(configs_for(baseline))
    skill_configs = 2 if baseline == "old" else 1
    rows = []
    for eng, model in engines:
        eprice = price_for(eng, model, prices)
        counts = {"exec": n_cases * n_configs * runs, "trigger": n_triggers * runs * skill_configs,
                  "grader": n_llm_cases * n_configs * runs, "pairwise": (n_cases * runs) if (pairwise and n_configs == 2) else 0}
        for kind, n in counts.items():
            if kind in ("exec", "trigger"):
                who, per = model or "%s CLI default" % eng, call_cost(tokens[kind], eprice)
            else:
                who, per = grader_model, grader_call_usd(kind, grader_model, tokens, prices)
            rows.append(est_row(eng, kind, who, n, tokens, per))
    return est_result(rows, tokens)

def est_row(engine, kind, model, n, tokens, per):
    return {"engine": engine, "kind": kind, "model": model, "calls": n, "tokens_in": n * tokens[kind][0],
            "tokens_out": n * tokens[kind][1], "usd": None if per is None else round(per * n, 4)}

def est_result(rows, tokens):
    known = [r["usd"] for r in rows if r["usd"] is not None]
    unknown = sorted({r["model"] for r in rows if r["usd"] is None and r["calls"]})
    return {"rows": rows, "total_usd": round(sum(known), 4), "unpriced_models": unknown,
            "calls": sum(r["calls"] for r in rows), "tokens": tokens}

def grader_estimate(n_grader, n_pairwise, grader_model, tokens, prices):
    """Grader-only cost: `grade --live` (counts from the iteration on disk) and `calibrate --live` (golden size)."""
    per = {k: grader_call_usd(k, grader_model, tokens, prices) for k in ("grader", "pairwise")}
    return est_result([est_row("grader", k, grader_model, n, tokens, per[k])
                       for k, n in (("grader", n_grader), ("pairwise", n_pairwise))], tokens)

def scale_estimate(est, k, extra_rows=()):
    rows = [dict(r, calls=r["calls"] * k, tokens_in=r["tokens_in"] * k, tokens_out=r["tokens_out"] * k,
                 usd=None if r["usd"] is None else round(r["usd"] * k, 4)) for r in list(est["rows"]) + list(extra_rows)]
    return est_result(rows, est["tokens"])

def format_estimate(est):
    lines = ["%-7s %-9s %-24s %6s %12s %10s %10s" % ("engine", "kind", "model", "calls", "tokens_in", "tokens_out", "usd")]
    for r in est["rows"]:
        lines.append("%-7s %-9s %-24s %6d %12d %10d %10s" % (r["engine"], r["kind"], r["model"][:24], r["calls"],
                     r["tokens_in"], r["tokens_out"], "?" if r["usd"] is None else "%.2f" % r["usd"]))
    api = AUTH["profile"].name if api_key_mode() else None
    billed = "(subscription: not billed per token)" if api is None else \
        "(API-key mode, auth profile %s: billed per token against the hard cap --budget-usd)" % api
    lines.append("total calls %d, API-equivalent total $%.2f %s%s" % (est["calls"], est["total_usd"], billed,
                 "" if not est["unpriced_models"] else "  + UNPRICED: %s (pass --price)" % ", ".join(est["unpriced_models"])))
    lines.append("assumed tokens per call (in,out): " + ", ".join("%s=%d,%d" % (k, v[0], v[1]) for k, v in est["tokens"].items()))
    soft = sorted({r["engine"] for r in est["rows"] if r["calls"] and r["engine"] in ENGINES
                   and budget_enforcement(ENGINES[r["engine"]]) == "soft_limit"})
    for e in soft:
        lines.append("budget: " + (SOFT_LIMIT_NOTE % e if api is None else spend.no_cap_reason("exec", e, api) + NO_REOPEN))
    return "\n".join(lines)

# ---------- workspace, approvals ----------
def default_workspace(spec):
    if not safe_name(spec["skill_name"]):
        raise EvalError("skill name %r is not a safe path component; fix SKILL.md name (or pass --workspace)"
                        % (spec["skill_name"],))
    return os.path.join(os.path.dirname(spec["skill_dir"]), spec["skill_name"] + "-workspace")  # skill-creator convention

def iterations(ws):
    out = []
    for d in os.listdir(ws) if os.path.isdir(ws) else []:
        m = re.match(r"iteration-(\d+)$", d)
        if m and os.path.isdir(os.path.join(ws, d)): out.append(int(m.group(1)))
    return sorted(out)

COMBINED_GATE = "requirements_eval_set"

def combined_hash(requirements, eval_set):
    if not requirements or not eval_set: return None
    return hashlib.sha256(("%s v1\nrequirements %s\neval_set %s\n" % (COMBINED_GATE, requirements, eval_set))
                          .encode("ascii")).hexdigest()

def gate_hash(gate, spec, ws):
    if gate == "requirements":
        return sha256_files(os.path.join(ws, "requirements.md")) if os.path.isfile(os.path.join(ws, "requirements.md")) else None
    if gate == "eval_set":
        return sha256_labeled(unit_eval_files(spec))
    if gate == COMBINED_GATE:
        return combined_hash(gate_hash("requirements", spec, ws), gate_hash("eval_set", spec, ws))
    if gate == "rule_review":
        return rule_review_hash(spec["skill_dir"])
    if gate.startswith(approvals.WAIVER_PREFIX):
        return waiver_hash(gate[len(approvals.WAIVER_PREFIX):], spec, ws)
    m = re.match(r"(?:iteration-(\d+)|registration)$", gate)
    if not m: raise EvalError("unknown gate %r (requirements|eval_set|requirements_eval_set|rule_review|iteration-N|"
                              "registration|coverage_waiver:<tool>)" % gate)
    n = int(m.group(1)) if m.group(1) else (iterations(ws) or [None])[-1]
    bp = os.path.join(ws, "iteration-%s" % n, "benchmark.json")
    return sha256_files(bp) if n is not None and os.path.isfile(bp) else None

def waiver_hash(tool, spec, ws):
    if tool not in approvals.COVERAGE_TOOLS:
        raise EvalError("unknown coverage waiver %r: the tool must be one of %s"
                        % (approvals.WAIVER_PREFIX + tool, ", ".join(approvals.COVERAGE_TOOLS)))
    text = "coverage_waiver v1\ntool %s\nskill_sha256 %s\neval_set_sha256 %s\n" % (
        tool, skill_tree_hash(spec["skill_dir"]), gate_hash("eval_set", spec, ws))
    return hashlib.sha256(text.encode("ascii")).hexdigest()

def rule_review_items(skill_dir):
    """Needs-review findings of the English-only and parity rules (eva.review_items): non-English run-output matchers,
    Hermes invocation control."""
    import eva
    return eva.review_items(skill_dir)

def rule_review_hash(skill_dir):
    """The findings show short excerpts, five at most per check, so the hash also covers the bytes of the files the review
    rows read (eva.review_sources); otherwise an edit outside the excerpts would keep an old decision valid."""
    import eva
    items = rule_review_items(skill_dir)
    if not items: return None
    files = sha256_labeled([(rel, os.path.join(skill_dir, *rel.split("/"))) for rel in eva.review_sources(skill_dir)])
    return hashlib.sha256(("\n".join(items) + "\n" + files).encode("utf-8")).hexdigest()

def rule_review_missing(spec, ws, verdict_of=None):
    """(findings, verdict) when the unit has rule-review findings and no recorded human decision (`approve --gate
    rule_review`) matches exactly these findings and the bytes of eva.review_sources, else None: changing a matcher,
    AGENT.md or an OpenCode wrapper invalidates the decision. verdict_of(gate, spec, ws):
    approval_verdict, or plan's memo of it."""
    items = rule_review_items(spec["skill_dir"])
    if not items: return None
    v = (verdict_of or approval_verdict)("rule_review", spec, ws)
    return None if v.status == "approved" else (items, v)

def rule_review_problems(spec, ws, verdict_of=None):
    missing = rule_review_missing(spec, ws, verdict_of)
    if missing is None: return []
    items, v = missing
    return ["rule review not recorded%s: %d finding(s) need a human decision (%s); read them with `validate`, then run "
            "`approve --gate rule_review` at a terminal (the signed decision is bound to these exact findings)"
            % ("" if v.status == "missing" else " (%s)" % v.status, len(items), "; ".join(i[:200] for i in items))]

def unapproved_live_gates(spec, ws, verdict_of=None):
    """(gate, verdict) of requirements, then eval_set, that a live start still lacks: nothing when requirements_eval_set
    is approved (S1 method 4). A generator, so a caller that raises on the first stops reading there."""
    verdict_of = verdict_of or approval_verdict
    if verdict_of(COMBINED_GATE, spec, ws).status == "approved": return
    for gate in ("requirements", "eval_set"):
        v = verdict_of(gate, spec, ws)
        if v.status != "approved": yield gate, v

def agent_gate_problems(agent, ws, verdict_of=None, review=True):
    """The approval and rule-review refusals of `agent run --live` and `agent loop --live`: a current rule_review
    decision for the agent's review rows (review=False skips it, see agent_loop_cli), and a current
    requirements_eval_set approval or current requirements and eval_set approvals."""
    spec, verdict_of = agent_spec(agent), verdict_of or approval_verdict
    approve = "`approve %s --workspace %s --gate %%s` at a terminal" % (agent.dir, ws)
    out = []
    missing = rule_review_missing(spec, ws, verdict_of) if review else None
    if missing is not None:
        items, v = missing
        out.append("rule review not recorded%s: %d finding(s) need a human decision (%s); read them with `python3 eva.py "
                   "check %s`, then run %s (the signed decision is bound to these exact findings)"
                   % ("" if v.status == "missing" else " (%s)" % v.status, len(items), "; ".join(i[:200] for i in items),
                      agent.dir, approve % "rule_review"))
    lacking = list(unapproved_live_gates(spec, ws, verdict_of))
    if lacking:
        out.append("live approvals missing: %s; write %s/requirements.md, then run %s (one approval of both; or approve "
                   "requirements and eval_set separately)" % (", ".join(
                       "%s is %s" % (g, v.status) for g, v in [(COMBINED_GATE, verdict_of(COMBINED_GATE, spec, ws))] +
                       lacking), ws, approve % COMBINED_GATE))
    return out

def eval_set_files(skill_dir):
    """[(label, path)] covered by the eval_set approval: evals.json, trigger_evals.json and every evals[].files fixture."""
    ev = os.path.join(skill_dir, "evals", "evals.json")
    out = [("evals/evals.json", ev), ("evals/trigger_evals.json", os.path.join(skill_dir, "evals", "trigger_evals.json"))]
    try: data = rjson(ev, {})
    except ValueError: data = {}
    cases = data.get("evals") if isinstance(data, dict) else None
    refs = sorted({f for c in (cases if isinstance(cases, list) else []) if isinstance(c, dict)
                   for f in (c.get("files") if isinstance(c.get("files"), list) else []) if isinstance(f, str)})
    return out + [("files:" + f, os.path.join(skill_dir, f)) for f in refs]

def agent_scenario_files(agent_dir):
    """[(label, path)] of every scenarios/*.json of an agent folder, sorted: an agent's eval set."""
    d = os.path.join(agent_dir, "scenarios")
    return [("scenarios/" + f, os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith(".json")] \
        if os.path.isdir(d) else []

def unit_eval_files(spec):
    if spec.get("kind") == "agent":
        return agent_scenario_files(spec["skill_dir"]) + agent_fixture_files(spec["skill_dir"])
    return eval_set_files(spec["skill_dir"])

APPROVER ={"it": approvals.cli_approver()}  # recorded approvals (engine/approvals.py); in-process tests replace it
POLICY_HASHED = (("pricing", "pricing.json"), ("models", "models.json"), ("auth_profiles", "auth-profiles.json"))

def file_sha256(path):
    try:
        with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None

def policy_sha256():
    """sha256 of each file outside the rule bundle that shapes a run (plan.md:842): pricing, models, auth profiles and
    the approver signers file, null while absent. plan.json records it; no approval binds it (live_plan_hash covers the
    auth profiles)."""
    out = {k: file_sha256(os.path.join(RULESET_ROOT, "policy", f)) for k, f in POLICY_HASHED}
    out["allowed_signers"] = file_sha256(APPROVER["it"].signers)
    return out

def approvals_data(ws):
    """approvals.json as a dict, {} when the file is absent; EvalError when it is JSON but not an object."""
    fp = os.path.join(ws, "approvals.json")
    data = rjson(fp, {})
    if not isinstance(data, dict): raise EvalError("%s is not a JSON object (it holds %s)" % (fp, json.dumps(data)[:40]))
    return data

def approval_record(gate, spec, ws):
    """(record, verdict) of one approvals.json gate, read once."""
    try: rec = approvals_data(ws).get(gate)
    except (EvalError, ValueError) as ex: return None, approvals.Verdict("invalid", None, str(ex))
    return rec, APPROVER["it"].verdict(rec, "approve", gate, spec["skill_name"], gate_hash(gate, spec, ws),
                                       ruleset_sha256())

def approval_verdict(gate, spec, ws):
    return approval_record(gate, spec, ws)[1]

def waiver_verdicts(spec, ws):
    """{tool: entry} per tool: its own coverage_waiver:<tool> record (source unit) or, when that does not count, a
    verifying machine waiver that lists the tool (source machine, with the waiver's sha256 and expiry)."""
    out, mw = {}, None
    for tool in approvals.COVERAGE_TOOLS:
        rec, v = approval_record(approvals.WAIVER_PREFIX + tool, spec, ws)
        out[tool] = {"status": v.status, "principal": v.principal, "verdict_reason": v.reason,
                     "reason": rec["reason"] if v.status == "approved" else None, "source": "unit"}
        if v.status != "approved" and tool in MACHINE_WAIVABLE:
            mw = mw or machine_waiver()
            if mw["status"] == "approved" and tool in mw["tools"]:
                out[tool] = {"status": "approved", "principal": mw["principal"], "verdict_reason": "",
                             "reason": mw["reason"], "source": "machine",
                             "machine_waiver": {"sha256": mw["sha256"], "expires": mw["expires"]}}
    return out

# ---------- the machine waiver (S1 method 3) ----------
_MACHINE_ID = {}
_MACHINE_SEEN = {}

def probe_machine_id():
    if "id" not in _MACHINE_ID: _MACHINE_ID["id"] = approvals.ioreg_machine_id()
    return _MACHINE_ID["id"]

MACHINE = {"id": probe_machine_id, "now": time.time}  # in-process tests replace both
CONFIG_PARTS = (".config", "skill-master")

def config_path(name):
    """A file of $HOME/.config/skill-master (machine-waiver.json, approvals.log, approve-ui.json), or None when HOME is
    not absolute. The signers file and the key stay at the password-database home (engine/approvals.py)."""
    home = os.environ.get("HOME") or ""
    return os.path.join(home, *(CONFIG_PARTS + (name,))) if os.path.isabs(home) else None

def machine_waiver_path():
    return config_path("machine-waiver.json")

def shown_config(path):
    home = os.environ.get("HOME") or ""
    return "~" + path[len(home):] if home and path and path.startswith(home.rstrip(os.sep) + os.sep) else str(path)

def real_config_in_tests(path):
    """True inside a test process for a path under the password-database home's .config/skill-master, which tests never
    open or write."""
    real = os.path.join(approvals.account_home(), *CONFIG_PARTS)
    return approvals.in_test_process() and (path == real or path.startswith(real + os.sep))

def machine_waiver(now=None):
    """The machine waiver of $HOME as {status, principal, reason, why, sha256, tools, expires, record}; principal is
    recorded_by of a recorded waiver. Inside a test process a path under the password-database home is never opened."""
    path = machine_waiver_path()
    empty = {"principal": None, "reason": None, "sha256": None, "tools": [], "expires": None, "record": None,
             "path": path}
    if path is None: return dict(empty, status="invalid", why="HOME is not an absolute path")
    if real_config_in_tests(path):
        return dict(empty, status="invalid", why="refusing to read the real machine waiver inside a test process")
    try:
        with open(path, "rb") as f: raw = f.read()
    except FileNotFoundError:
        return dict(empty, status="missing", why="no machine waiver")
    except OSError as ex:
        return dict(empty, status="invalid", why="cannot read %s (%s)" % (shown_config(path), ex))
    try: rec = json.loads(raw.decode("utf-8"))
    except ValueError as ex: return dict(empty, status="invalid", why="not JSON (%s)" % ex)
    try: machine = MACHINE["id"]()
    except (OSError, ValueError, subprocess.SubprocessError): machine = None
    now = MACHINE["now"]() if now is None else now
    key = (APPROVER["it"].signers, raw, machine, MACHINE_WAIVABLE, int(now // 60))
    if key not in _MACHINE_SEEN:
        _MACHINE_SEEN.clear()
        _MACHINE_SEEN[key] = APPROVER["it"].machine_waiver_verdict(rec, machine, MACHINE_WAIVABLE, now)
    v = _MACHINE_SEEN[key]
    ok = isinstance(rec, dict) and v.status not in ("unsigned", "invalid")
    return dict(empty, status=v.status, principal=v.principal, why=v.reason, record=rec,
                sha256=approvals.machine_waiver_sha256(rec) if isinstance(rec, dict) else None,
                reason=rec.get("reason") if ok else None, tools=list(rec.get("tools") or []) if ok else [],
                expires=rec.get("expires") if ok else None)

def machine_waiver_line(mw):
    if mw["status"] == "approved":
        return "machine waiver: approved by %s, sha256 %s, tools %s, expires %s, reason: %s" % (
            mw["principal"], mw["sha256"], ",".join(mw["tools"]), mw["expires"], mw["reason"])
    if mw["status"] == "missing": return "machine waiver: missing (%s)" % shown_config(mw["path"])
    return "machine waiver: NOT COUNTED (%s: %s)%s" % (mw["status"], mw["why"],
                                                       ", sha256 %s" % mw["sha256"] if mw["sha256"] else "")

def approvals_log(entry):
    path = config_path("approvals.log")
    if path is None: raise EvalError("HOME is not an absolute path; cannot write approvals.log")
    approvals.append_log(path, dict(entry, at=approvals.now_iso()))

def writable_config(path, what):
    if path is None: raise EvalError("HOME is not an absolute path; %s" % what)
    if real_config_in_tests(path): raise EvalError("refusing to change the real machine waiver inside a test process")

def sign_machine_waiver(tools, reason=None):
    """machine-waiver sign: the recorded waiver (engine/approvals.py) written to $HOME, and one approvals.log line."""
    path = machine_waiver_path()
    writable_config(path, "nothing was recorded")
    try: machine = MACHINE["id"]()
    except (OSError, ValueError, subprocess.SubprocessError): machine = None
    rec = APPROVER["it"].sign_machine_waiver(machine, list(tools), MACHINE_WAIVABLE, reason=reason)
    approvals.write_private(path, json.dumps(rec, indent=2, sort_keys=True) + "\n")
    approvals_log({"unit": "machine", "gate": "machine-waiver", "sha256": approvals.machine_waiver_sha256(rec),
                   "decision": "machine-waiver-signed", "reason": rec["reason"], "principal": rec["recorded_by"]})
    return rec

def revoke_machine_waiver():
    path = machine_waiver_path()
    writable_config(path, "nothing to revoke")
    mw = machine_waiver()
    if not os.path.isfile(path):
        raise EvalError("no machine waiver at %s; nothing to revoke" % shown_config(path))
    APPROVER["it"].write(approvals.printable(machine_waiver_line(mw)))
    os.remove(path)
    approvals_log({"unit": "machine", "gate": "machine-waiver", "sha256": mw["sha256"],
                   "decision": "machine-waiver-revoked", "reason": mw["reason"], "principal": mw["principal"]})
    return mw

def approval_ok(gate, spec, ws):
    return approval_verdict(gate, spec, ws).status == "approved"

def approval_label(v):
    if v.status == "approved": return "approved by %s" % v.principal
    if v.status == "missing": return "NOT APPROVED"
    if v.status == "invalid": return "NOT APPROVED (invalid: %s)" % v.reason
    rules = "stale: " + approvals.RULES_CHANGED
    if v.reason == approvals.RULES_CHANGED: return "NOT APPROVED (%s)" % rules
    if v.reason == approvals.UNSIGNED_RULES: return "NOT APPROVED (unsigned; %s)" % rules
    return "NOT APPROVED (%s)" % v.status

def approve(gate, spec, ws):
    return _approve_with(gate, lambda: (spec, ws))

def _approve_with(gate, load, reason=None, listed=None):
    """Records gate_hash(gate) as an unsigned approval (engine/approvals.py), then writes only that key of
    approvals.json. reason: a waiver gate's reason (else the approver reads one line). listed: the approve --batch
    item, whose sha256 and ruleset_sha256 the record must still match."""
    st = {}
    def prepare():
        spec, ws = load()
        h = gate_hash(gate, spec, ws)
        if not h: raise EvalError("nothing to approve for %s (file missing)" % gate)
        try: approvals_data(ws)
        except ValueError as ex: raise EvalError("%s/approvals.json is not valid JSON (%s); nothing was signed" % (ws, ex))
        except EvalError as ex: raise EvalError("%s; nothing was signed" % ex)
        st.update(ws=ws)
        rules = ruleset_sha256()
        if listed is not None and (h, rules) != (listed["sha256"], listed["ruleset_sha256"]):
            raise EvalError("%s for %s changed since the list was shown; nothing was recorded" % (gate, spec["skill_name"]))
        return approvals.Prepared(spec["skill_name"], h, rules, approve_show(gate, spec, ws, rules))
    grant = APPROVER["it"].sign("approve", gate, prepare, reason=reason)
    return store_approval(st["ws"], gate, grant.record)

def store_approval(ws, gate, record):
    data = approvals_data(ws)
    data[gate] = record
    wjson(os.path.join(ws, "approvals.json"), data)
    return record

def approve_show(gate, spec, ws, rules):
    """What the person approves, printed before the record is written; the last line is the rule bundle hash it binds."""
    if gate == "requirements": shown = ["requirements: %s" % os.path.join(ws, "requirements.md")]
    elif gate == COMBINED_GATE:
        shown = (["requirements: %s sha256 %s" % (os.path.join(ws, "requirements.md"),
                                                  gate_hash("requirements", spec, ws))] +
                 ["eval set file %s: %s" % (label, sha256_files(p)[:12] if os.path.isfile(p) else "<missing>")
                  for label, p in unit_eval_files(spec)] +
                 ["eval_set sha256 %s" % gate_hash("eval_set", spec, ws),
                  "requirements_eval_set: one approval of both; an edit to either stales it"])
    elif gate == "eval_set":
        shown = ["eval set file %s: %s" % (label, sha256_files(p)[:12] if os.path.isfile(p) else "<missing>")
                 for label, p in unit_eval_files(spec)]
    elif gate == "rule_review": shown = ["rule review finding: " + x[:300] for x in rule_review_items(spec["skill_dir"])]
    elif gate.startswith(approvals.WAIVER_PREFIX):
        tool, n = gate[len(approvals.WAIVER_PREFIX):], (iterations(ws) or [None])[-1]
        bench = rjson(os.path.join(ws, "iteration-%s" % n, "benchmark.json")) if n is not None else None
        cov = bench.get("tool_coverage") if isinstance(bench, dict) else None
        entry = cov.get(tool) if isinstance(cov, dict) else None
        status = entry.get("status") if isinstance(entry, dict) else None
        shown = ["coverage waiver: tool %s, dynamic eval %s" % (
                     tool, "in iteration-%s: %s" % (n, status or "not recorded") if bench else "not reported yet"),
                 "coverage waiver: skill sha256 %s" % skill_tree_hash(spec["skill_dir"]),
                 "coverage waiver: eval set sha256 %s" % gate_hash("eval_set", spec, ws),
                 "coverage waiver: clears only coverage-missing:%s for unit %s, this eval set and this ruleset"
                 % (tool, spec["skill_name"])]
    else:
        m = re.match(r"iteration-(\d+)$", gate)
        n = int(m.group(1)) if m else (iterations(ws) or [None])[-1]
        bench = rjson(os.path.join(ws, "iteration-%s" % n, "benchmark.json"))
        g = bench.get("gate") if isinstance(bench, dict) and isinstance(bench.get("gate"), dict) else {}
        shown = ["iteration-%s gate problems: %s" % (n, g.get("problems") or "none recorded")]
    return shown + ["ruleset_sha256 " + rules]

def preflight(spec, engines, run_root=None, home=None):
    """Read-only, blocks --live (fail closed). Runs execute in per-run sandboxes under run_root (references/engine-cli.md
    "Eval isolation"); this checks what the sandboxes cannot hide:
    - any .claude/skills (claude) or .agents/skills (codex) folder with skills in run_root or its ancestors
      (project skills are discovered from the cwd upward);
    - claude: a same-named copy in ~/.claude/skills (incl. synced/) or a plugin. --setting-sources project,local
      should hide user skills (docs: the "user" source loads ~/.claude/skills), but whether the CLI flag hides already
      downloaded claude.ai synced skills and plugin skills is UNVERIFIED, so a same-named copy still blocks;
    - any API-key variable (API_KEY_ENV) in the environment: subscription auth only;
    - claude (Keychain login, shared config dir): a claude.ai synced skill the post-run init check could not see
      (`user-invocable: false`, claude_undetectable_skill_problems). Other synced and plugin skills are left to the
      init check (plan shows them as notes);
    - any provider-routing variable (PROVIDER_ENV);
    - claude: API-key auth, provider routing or CLAUDE_CODE_OAUTH_TOKEN (apiKeyHelper / NON_SUBSCRIPTION_ENV /
      IGNORED_AUTH_ENV env entry) in ANY settings source
      a run could load: user settings(.local).json of the effective config dir, .claude/settings(.local).json of
      run_root and every ancestor, every managed source (managed-settings.json + managed-settings.d, MDM profile,
      registry: fail closed) and the server-managed cache; the sandbox's own work/.claude settings (fixtures) are
      checked again per run;
    - codex: skills in /etc/codex/skills (admin scope, cannot be hidden), and no file-based $CODEX_HOME/auth.json
      whose auth_mode is ChatGPT (only that field is read).
    `claude auth status` is checked in check_live_gates (it runs a CLI, so never in a dry run)."""
    name = spec["skill_name"]
    cfg = os.path.join(home, ".claude") if home else claude_config_dir()
    out = api_key_env_problems()
    root = os.path.realpath(run_root or tempfile.gettempdir())
    for e in engines:
        rel = {"claude": ".claude/skills", "codex": ".agents/skills"}.get(e)
        if rel is None: continue
        for a in ancestors(root):
            hits = skills_under(os.path.join(a, rel), 3)
            if hits:
                out.append("%s: %s has skills %s; every sandbox under %s would see them (use --run-root elsewhere)"
                           % (e, os.path.join(a, rel), sorted(n for n, _ in hits), root))
        if e == "claude":
            for n_, p in claude_user_skills(cfg, config_dir=True):
                if n_ == name:
                    out.append("claude: skill %r is also installed at %s; hiding it via --setting-sources is not "
                               "verified, so it could sit next to the copy under test" % (name, p))
            out += claude_undetectable_skill_problems(cfg)
            user_settings = [os.path.join(cfg, f) for f in CLAUDE_SETTINGS_FILES]
            # every settings source a run could load: user (dropped by --setting-sources, still refused), the
            # project/local files of run_root and its ancestors, every managed source (always loaded) and the
            # server-managed cache of the effective config dir
            files, mprobs = claude_managed_sources()
            out += mprobs + claude_settings_auth_problems(
                user_settings + [p for p in claude_project_settings(ancestors(root)) if p not in user_settings]
                + files + [os.path.join(cfg, CLAUDE_REMOTE_SETTINGS)])
            for acfg in switch_account_config_dirs(home):  # accounts the shim may retry on: same checks
                for n_, p in claude_user_skills(acfg, config_dir=True):
                    if n_ == name:
                        out.append("claude: skill %r is also installed at %s (account the shim may switch to)" % (name, p))
                out += claude_undetectable_skill_problems(acfg) + claude_settings_auth_problems(
                    [os.path.join(acfg, f) for f in CLAUDE_SETTINGS_FILES + (CLAUDE_REMOTE_SETTINGS,)])
        if e == "codex":
            hits = skills_under(CODEX_ADMIN_SKILLS, 4)
            if hits:
                out.append("codex: %s has %s (admin skills load in every run)" % (CODEX_ADMIN_SKILLS, sorted(n for n, _ in hits)))
            if subscription_checks_on(): out += codex_auth_problems()
    return out

# ---------- running ----------
def eval_dir_name(case):
    d = "eval-%s-%s" % (case["id"], case["name"]) if case["name"] != str(case["id"]) else "eval-%s" % case["id"]
    if not re.match(r"^[0-9A-Za-z\uAC00-\uD7A3._-]+$", d) or ".." in d:  # names come from safe_name() or slug(); defense in depth
        raise EvalError("eval directory name %r is not a safe path component" % d)
    return d

def configs_for(baseline):
    return [PRIMARY, BASELINES[baseline]]

def skill_path_for(config, spec, old_skill):
    return spec["skill_dir"] if config == PRIMARY else (old_skill if config == "old_skill" else None)

class Budget:
    """limit and spent are API-equivalent USD (usage x API prices): with subscription auth nothing is billed per
    token, so this is a usage limiter, not a record of money spent. ledger: the command's engine/spend.py ledger (the
    CLI live path always attaches one); every paid call is admitted and recorded through it."""
    def __init__(self, usd): self.limit, self.spent, self.ledger = usd, 0.0, None
    def remaining(self): return self.limit - self.spent
    def finite(self): return self.limit != float("inf")
    def charge(self, usd): self.spent += usd or 0.0
    def claude_cap(self):
        """--max-budget-usd for the next Claude call: CLAUDE_CAP_HEADROOM of the remaining budget, floored to cents."""
        return cents_floor(max(self.remaining(), 0.0) * CLAUDE_CAP_HEADROOM)

def call_bound(engine_name, model, kind, prices, tokens):
    """Conservative worst-case USD for one call (assumed tokens x WORST_CASE_FACTOR). Not a hard cap."""
    per = call_cost(tokens[kind], price_for(engine_name, model, prices))
    return None if per is None else per * WORST_CASE_FACTOR

def guard_call(engine, kind, budget, prices, tokens):
    """Before each executor call: returns (cap passed to the CLI or None, USD to charge if the CLI reports no usage,
    stop reason or None)."""
    if not budget.finite(): return None, 0.0, None
    if engine.has_call_cap:
        cap = budget.claude_cap()
        if cap < 0.01:
            return None, 0.0, "budget exhausted ($%.2f of $%.2f; no room for a capped %s call)" % (budget.spent, budget.limit, engine.name)
        return cap, cap, None
    bound = call_bound(engine.name, engine.model, kind, prices, tokens)
    if bound is None: return None, 0.0, "no price for %s: cannot bound a call" % engine.name
    if bound > budget.remaining():
        return None, 0.0, ("%s worst-case %s call $%.2f > remaining $%.2f (%s budget is a soft limit: no per-call cap)"
                      % (engine.name, kind, bound, budget.remaining(), engine.name))
    return None, bound, None

def spend_admit(budget, kind, engine, model, iteration, call_cap, bound, has_call_cap, refusal):
    """(ticket or None, the refusal to act on or None) for one paid call. refusal: the A5 check's own (None = passed).
    With the budget's ledger, the ledger records the decision (and in API-key mode adds its own hard-cap rules);
    without one, subscription keeps the A5 decision and API-key mode refuses every call."""
    led = getattr(budget, "ledger", None)
    if led is None:
        return None, (NEEDS_LEDGER if api_key_mode() else refusal)
    t = led.admit(kind, engine, model, iteration, budget.spent, call_cap, bound, has_call_cap, refusal)
    return (None, t.reason) if t.reason else (t, None)

def spend_settle(ticket, budget, outcome, reported, tin, tout, pinfo, charged, source):
    """The result row of an admitted call, written where its cost was just charged to the budget."""
    if ticket is None: return
    price = pinfo[0]
    computed = call_cost((tin, tout), price) if price is not None and (tin or tout) else None
    budget.charge(ticket.result(outcome, reported, tin, tout, computed, price, pinfo[1], pinfo[2], charged, source,
                                budget.spent))

def spend_halt(budget):
    """The stop a call's result set in API-key mode (an overshoot, spend above the hard cap, an unreported cost), or
    None. The next admission refuses on it; each command also checks it after its last paid call."""
    led = getattr(budget, "ledger", None)
    return None if led is None else led.halt

@contextlib.contextmanager
def spend_scope(grader, **scope):
    """The iteration (and kind, for calibrate) that the grader's spend rows record while the caller grades."""
    old = getattr(grader, "spend_scope", None)
    grader.spend_scope = scope
    try:
        yield
    finally:
        grader.spend_scope = old

def iteration_of(it_dir):
    m = re.match(r"iteration-(\d+)$", os.path.basename(os.path.normpath(it_dir)))
    return int(m.group(1)) if m else None

def api_equivalent(tin, tout, reported, price, fallback=0.0):
    """(USD, source) for one call. Usage tokens x API price come first; a CLI-reported cost only counts when it is
    larger (a reported 0 never hides nonzero usage). With no usage, the call's cap/bound (fallback) is the upper
    bound: max(reported, fallback) is charged, so a small reported cost never undercuts it. Shared by runs, the
    grader and the builder."""
    tok = call_cost((tin, tout), price) if (tin or tout) else None
    rep = float(reported) if isinstance(reported, (int, float)) and not isinstance(reported, bool) \
        and math.isfinite(reported) and reported > 0 else None
    if tok is not None and (rep is None or tok >= rep): return tok, "tokens_x_price"
    if rep is not None and (tok is not None or rep >= fallback): return rep, "cli_reported"
    return (fallback, "cap_charged") if fallback else (0.0, "unknown")

def claude_usage(data):
    """(input tokens incl. cache read/creation, output tokens) of a `claude -p --output-format json` result. Raises on
    a malformed usage block (nonnumeric, wrong type); callers charge max(reported, cap) and stop."""
    u = data.get("usage") or {}
    tin = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return tin, int(u.get("output_tokens") or 0)

def run_cost(res, engine, prices, fallback=0.0):
    """API-equivalent USD of one run and where it came from (api_equivalent: tokens x price first, a larger CLI
    report, else the cap/bound). Subscription runs are not billed per token: this is what the call would cost at
    API prices."""
    return api_equivalent(res["input_tokens"], res["output_tokens"], res.get("cost_usd"),
                          price_for(engine.name, engine.model, prices), fallback)

def append_cost(it_dir, command, usage):
    """Appends one non-run cost entry (grade, calibrate, builder) to iteration-N/cost.json; runs are in timing.json.
    Returns (path, fingerprint value it read, value it wrote), so loop can tell this write from a builder's."""
    fp = os.path.join(it_dir, "cost.json")
    read = writable.entry(fp)
    data = rjson(fp, {}) or {}
    data.setdefault("billing", current_billing())
    data.setdefault("entries", []).append({"command": command, "at": now_iso(), "calls": usage.get("calls", 0),
        "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
        "api_equivalent_usd": round(usage.get("api_equivalent_usd", 0.0), 6)})
    wjson(fp, data)
    return fp, read, writable.entry(fp)

def usage_delta(after, before):
    return {k: after.get(k, 0) - before.get(k, 0) for k in after}

def cost_summary(it_dir, ws=None):
    """API-equivalent cost of an iteration: every run's timing.json (exec + trigger, per engine) plus cost.json
    entries (grading, calibration, builder). actual_spend_usd stays null for subscription calls: the harness cannot see
    plan billing. When the workspace ledger (ws, default the parent of it_dir) holds API-key result rows of this
    iteration, actual_spend_usd is the sum of their CLI-reported costs (spend_summary)."""
    out = _a5_cost_summary(it_dir)
    n = iteration_of(it_dir)
    rows = spend.read_rows(os.path.join(ws or os.path.dirname(os.path.normpath(it_dir)), spend.FILE))[0] \
        if n is not None else []
    out.update(spend_summary([r for r in rows if r["iteration"] == n], rows))
    return out

def spend_summary(mine, rows):
    """The actual-spend fields of one iteration's ledger rows (mine); {} when none is an API-key result or intent row (an
    API-key intent alone, e.g. of a crashed call, still makes the iteration API-key billed)."""
    if not any(r["type"] in ("result", "intent") and r["billing"] == "api_key" for r in mine): return {}
    api = [r for r in mine if r["type"] == "result" and r["billing"] == "api_key"]
    paid = [r for r in api if r["outcome"] != "not_launched"]
    done = {r["call_id"] for r in rows if r["type"] == "result"} | {r["recovers"] for r in rows if r["type"] == "recovered"}
    open_ =[r for r in mine if r["type"] == "intent" and r["billing"] == "api_key" and r["decision"] == "allowed"
             and r["call_id"] not in done]
    caps, seen = [], set()
    for r in mine:
        k = (r["run_id"], r["command"], r["cap_usd"])
        if r["billing"] == "api_key" and k not in seen:
            seen.add(k); caps.append({"run_id": k[0], "command": k[1], "cap_usd": k[2]})
    mixed = any(r["type"] == "result" and r["billing"] != "api_key" for r in mine)
    unreported = sum(1 for r in paid if r["reported_cost_usd"] is None)
    note = ("CLI-reported total_cost_usd of the API-key calls of this iteration (spend.jsonl)"
            + ("; the subscription calls of this iteration are not billed per token and are not counted" if mixed else "")
            + ("; %d call(s) reported no cost and are not counted" % unreported if unreported else ""))
    return {"billing": "mixed" if mixed else "api_key",
            "actual_spend_usd": spend.usd(sum(r["reported_cost_usd"] or 0.0 for r in paid)),
            "actual_spend_basis": "cli_reported", "actual_spend_note": note, "unreported_calls": unreported,
            "unreconciled_calls": len(open_), "unreconciled_bound_usd": spend.usd(sum(r["bound_usd"] or 0.0 for r in open_)),
            "caps": caps, "auth_profile": ", ".join(sorted({r["profile"] for r in mine if r["billing"] == "api_key"}))}

def _a5_cost_summary(it_dir):
    by_engine, unknown = {}, 0
    for dp, dn, fn in os.walk(it_dir):
        dn[:] = [d for d in dn if d not in ("workdir", "outputs")]  # run outputs may contain any file
        if "timing.json" not in fn or dp == it_dir: continue
        tm = rjson(os.path.join(dp, "timing.json"), {}) or {}
        parts = os.path.relpath(dp, it_dir).split(os.sep)
        kind = "trigger" if len(parts) > 1 and parts[1] == "trigger" else "exec"
        v = tm.get("api_equivalent_usd", tm.get("cost_usd"))
        if not isinstance(v, (int, float)) or tm.get("api_equivalent_source") == "unknown":
            unknown += 1; v = 0.0
        e = by_engine.setdefault(parts[0], {"exec": 0.0, "trigger": 0.0})
        e[kind] = round(e[kind] + v, 6)
    other = {}
    for x in (rjson(os.path.join(it_dir, "cost.json"), {}) or {}).get("entries") or []:
        other[x.get("command", "?")] = round(other.get(x.get("command", "?"), 0.0) + (x.get("api_equivalent_usd") or 0.0), 6)
    total = sum(v for e in by_engine.values() for v in e.values()) + sum(other.values())
    return {"billing": BILLING, "budget_basis": "api_equivalent",
            "api_equivalent_usd": {"by_engine": by_engine, "other": other, "total": round(total, 4)},
            "runs_without_usage": unknown, "actual_spend_usd": None, "actual_spend_note": ACTUAL_SPEND_NOTE}

def write_run(run_dir, res, engine, config, prompt, triggered, api_eq=None):
    t = {"engine": engine.name, "model": engine.model, "configuration": config, "prompt": prompt, "final": res["final"],
         "tool_calls": res["tool_calls"], "triggered": triggered, "returncode": res.get("returncode"),
         "error": res.get("error"), "stderr_tail": res.get("stderr_tail", ""), "command": res.get("command")}
    wjson(os.path.join(run_dir, "outputs", "transcript.json"), t)
    wtext(os.path.join(run_dir, "outputs", "final.md"), res["final"] or "")
    total = res["input_tokens"] + res["output_tokens"]
    wjson(os.path.join(run_dir, "timing.json"), {
        "total_tokens": total, "duration_ms": res.get("duration_ms", 0),
        "total_duration_seconds": round(res.get("duration_ms", 0) / 1000.0, 3),
        "executor_start": res.get("executor_start"), "executor_end": res.get("executor_end"),
        "executor_duration_seconds": round(res.get("duration_ms", 0) / 1000.0, 3),
        "input_tokens": res["input_tokens"], "output_tokens": res["output_tokens"],
        "cached_input_tokens": res["cached_input_tokens"], "cost_usd": res.get("cost_usd"),  # CLI-reported
        "api_equivalent_usd": None if api_eq is None else round(api_eq[0], 6),
        "api_equivalent_source": None if api_eq is None else api_eq[1], "billing": current_billing()})

class _Fatal(Exception): pass

def run_iteration(spec, ws, engines, runs=DEFAULT_RUNS, baseline="none", old_skill=None, budget=None, prices=None,
                  iteration=None, log=print, tokens=None, run_root=None, builder_model=None):
    """Executes every (engine, case, config, run) and every trigger query, each in its own Sandbox under run_root
    (default: the system temp folder). builder_model: recorded in plan.json so a later `grade --live` compares it.
    Returns the iteration number."""
    configs = configs_for(baseline)
    for engine in engines:
        if isinstance(engine, ClaudeCodeEngine):
            engine.python_script, engine.skill_dir = spec.get("claude_python_script"), spec["skill_dir"]
    if baseline == "old" and not (old_skill and os.path.isfile(os.path.join(old_skill, "SKILL.md"))):
        raise EvalError("baseline old needs --old-skill (or `snapshot` first): %s" % old_skill)
    run_root = os.path.realpath(run_root or tempfile.gettempdir())
    n = iteration or ((iterations(ws) or [0])[-1] + 1)
    it_dir = os.path.join(ws, "iteration-%d" % n)
    plan = {"skill_name": spec["skill_name"], "skill_path": spec["skill_dir"],
          "old_skill": old_skill, "configurations": configs, "runs_per_configuration": runs,
          "engines": {e.name: e.model for e in engines}, "evals_run": [c["id"] for c in spec["cases"]],
          "tool_coverage": tool_coverage([e.name for e in engines]),
          "efforts": {e.name: getattr(e, "effort", None) for e in engines},
          "trigger_queries": len(spec["triggers"] or []), "started_at": now_iso(),
          "skill_sha256": skill_tree_hash(spec["skill_dir"]), "eval_set_sha256": gate_hash("eval_set", spec, ws),
          "old_skill_sha256": skill_tree_hash(old_skill) if baseline == "old" else None,
          "ruleset_sha256": ruleset_sha256(), "policy_sha256": policy_sha256(),
          "auth_profile": None if AUTH["profile"] is None else AUTH["profile"].name,
          "isolation": {"run_root": run_root, "per_run_sandbox": True},
          "budget_enforcement": {e.name: budget_enforcement(e) for e in engines}}
    if builder_model: plan["builder_model"] = builder_model
    wjson(os.path.join(it_dir, "plan.json"), plan)
    budget, prices, tokens = budget or Budget(float("inf")), prices or {}, tokens or DEFAULT_TOKENS
    spent0 = budget.spent
    user_names = sorted({nm for nm, _ in claude_user_skills(claude_config_dir(), config_dir=True)}) \
        if any(e.name == "claude" for e in engines) else []
    stopped = None

    def one(eng, kind, prompt, case, cfg, rd, keep):
        cap, fallback, stop = guard_call(eng, kind, budget, prices, tokens)
        ticket, stop = spend_admit(budget, kind, eng.name, eng.model, n, cap, fallback, eng.has_call_cap, stop)
        if stop:
            if api_key_mode(): raise _Fatal(stop)  # API-key mode: the first refused call ends the iteration
            return None, stop
        try:
            res, fatal = isolated_run(eng, prompt, spec, case, skill_path_for(cfg, spec, old_skill),
                                      ensure_inside(it_dir, rd), run_root, cap, user_names, keep, ticket)
        except Exception:
            if ticket is not None: ticket.not_launched(budget.spent)
            raise
        api_eq = run_cost(res, eng, prices, fallback)
        if res.get("launch_failed") and fallback > api_eq[0]:  # failed call: max(reported/usage, cap) is charged
            api_eq = (fallback, "cap_charged")
        budget.charge(api_eq[0])  # the budget limits API-equivalent cost
        failed = res.get("launch_failed") or ""
        spend_settle(ticket, budget, "unparsed" if "could not be parsed" in failed else "launch_failed" if failed else
                     "error" if res.get("error") else "ok", res.get("cost_usd"), res["input_tokens"],
                     res["output_tokens"], price_info(eng.name, eng.model, prices), api_eq[0], api_eq[1])
        try:
            write_run(rd, res, eng, cfg, prompt, eng.triggered(res, spec["skill_name"]), api_eq)
        except Exception as ex:
            raise _Fatal("[%s %s] could not record the run (%s); charged $%.2f (%s) and stopped"
                         % (eng.name, cfg, type(ex).__name__, api_eq[0], api_eq[1]))
        if res.get("launch_failed"):  # cleanup problems (e.g. a leftover sandbox) are reported with it
            raise _Fatal("[%s %s] %s; charged $%.2f (%s) and stopped%s" % (
                eng.name, cfg, res["launch_failed"], api_eq[0], api_eq[1],
                "".join("; and " + x for x in fatal)))
        if fatal: raise _Fatal("isolation [%s %s]: %s" % (eng.name, cfg, "; ".join(fatal)))
        return res, None

    try:
        for eng in engines:
            for case in spec["cases"]:
                ed = ensure_inside(it_dir, os.path.join(it_dir, eng.name, eval_dir_name(case)))
                wjson(os.path.join(ed, "eval_metadata.json"), {"eval_id": case["id"], "eval_name": case["name"],
                      "prompt": case["prompt"], "assertions": [it["text"] for it in case["items"]],
                      "layers": {it["id"]: it["layer"] for it in case["items"]}, "pressure": case["pressure"]})
                for cfg in configs:
                    for k in range(1, runs + 1):
                        res, stop = one(eng, "exec", full_prompt(case), case, cfg, os.path.join(ed, cfg, "run-%d" % k), True)
                        if stop: stopped = stop; break
                        log("[%s] %s %s run-%d: %s, %d tokens, %.1fs" % (eng.name, eval_dir_name(case), cfg, k,
                            res.get("error") or "ok", res["input_tokens"] + res["output_tokens"], res.get("duration_ms", 0) / 1000.0))
            for cfg in [c for c in configs if skill_path_for(c, spec, old_skill)]:
                results = []
                for qi, q in enumerate(spec["triggers"] or []):
                    hits, errors = [], 0
                    for k in range(1, runs + 1):
                        rd = os.path.join(it_dir, eng.name, "trigger", cfg, "q-%02d" % qi, "run-%d" % k)
                        res, stop = one(eng, "trigger", q["query"], {"files": []}, cfg, rd, False)
                        if stop: stopped = stop; break
                        hits.append(eng.triggered(res, spec["skill_name"])); errors += 1 if res.get("error") else 0
                    rate = (sum(hits) / len(hits)) if hits else None
                    results.append({"query": q["query"], "should_trigger": q["should_trigger"], "trigger_rate": rate,
                                    "triggers": sum(hits), "runs": len(hits), "errors": errors,
                                    "pass": None if rate is None else (rate >= TRIGGER_THRESHOLD if q["should_trigger"] else rate < TRIGGER_THRESHOLD)})
                if results:
                    passed = sum(1 for r in results if r["pass"])
                    wjson(os.path.join(it_dir, eng.name, "trigger", cfg, "results.json"), {
                        "skill_name": spec["skill_name"], "description": spec["description"], "results": results,
                        "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed}})
    except (_Fatal, EvalError, spend.SpendError) as ex:  # isolation or the record could not be guaranteed: stop (fail closed)
        stopped = str(ex)
    stopped = stopped or spend_halt(budget)
    meta = rjson(os.path.join(it_dir, "plan.json"))
    meta.update(finished_at=now_iso(), billing=current_billing(), stopped=stopped,
                api_equivalent_usd=round(budget.spent - spent0, 4))  # exec + trigger runs of this call
    wjson(os.path.join(it_dir, "plan.json"), meta)
    if stopped: log("STOPPED: " + stopped)
    return n

# ---------- grading ----------
def _csv_rows_result(chk, workdir):
    try: output = ensure_inside(workdir, os.path.join(workdir, chk["path"]))
    except EvalError: return False, "file path escapes its root"
    if not os.path.isfile(output): return False, "%s missing" % chk["path"]
    try:
        with open(output, newline="", encoding="utf-8") as f: records = list(csv.reader(f, strict=True))
    except (OSError, UnicodeError, csv.Error) as ex:
        return False, "CSV read failed: %s" % type(ex).__name__
    key = (lambda row: tuple(c.strip() for c in row)) if chk.get("normalize") == "strip" else tuple
    counts = {}
    for rec in records: counts[key(rec)] = counts.get(key(rec), 0) + 1
    lo, hi = chk.get("min", 1), chk.get("max")
    bad, checked = [], set()
    for row in chk.get("rows") or []:
        if key(row) in checked: continue
        checked.add(key(row))
        n = counts.get(key(row), 0)
        if n < lo or (hi is not None and n > hi): bad.append("%s x%d" % (json.dumps(row, ensure_ascii=False), n))
    dupes = sum(n - 1 for n in counts.values()) if chk.get("unique") else 0
    if not bad and not dupes:
        return True, "%s: %d listed row(s) within [%d, %s]%s" % (
            chk["path"], len(checked), lo, "∞" if hi is None else hi, ", no duplicate records" if chk.get("unique") else "")
    parts = []
    if bad: parts.append("row count outside [%d, %s]: %s" % (lo, "∞" if hi is None else hi, "; ".join(bad)))
    if dupes: parts.append("%d duplicate record(s)" % dupes)
    return False, ("%s: %s" % (chk["path"], ", ".join(parts)))[:200]

def static_check(chk, t, workdir, fixture_root=None):
    t_ = chk["type"]
    if t_ in ("regex", "not_regex"):
        text = t.get("final") or "" if chk.get("target", "final") == "final" else json.dumps(t, ensure_ascii=False)
        m = re.search(chk["pattern"], text, re.I if "i" in chk.get("flags", "") else 0)
        if t_ == "regex": return bool(m), ("matched %r" % m.group(0)[:200]) if m else "pattern not found in %s" % chk.get("target", "final")
        return not m, ("forbidden match %r" % m.group(0)[:200]) if m else "pattern absent"
    if t_ == "tool_call":
        rx = re.compile(chk["input_regex"]) if chk.get("input_regex") else None
        hits = [c for c in t.get("tool_calls") or [] if c.get("tool") == chk["tool"]
                and (rx is None or rx.search(json.dumps(c.get("input"), ensure_ascii=False)))]
        lo, hi = chk.get("min", 1), chk.get("max")
        ok = len(hits) >= lo and (hi is None or len(hits) <= hi)
        return ok, "%d matching %s call(s), allowed [%d, %s]" % (len(hits), chk["tool"], lo, "∞" if hi is None else hi)
    if t_ == "skill_triggered":
        return bool(t.get("triggered")) == chk["expected"], "triggered=%s" % bool(t.get("triggered"))
    if t_ == "file_exists":
        ok = os.path.exists(os.path.join(workdir, chk["path"]))
        return ok, "%s %s" % (chk["path"], "exists" if ok else "missing")
    if t_ in ("file_equals_fixture", "csv_cleaned"):
        if fixture_root is None: return False, "fixture root unavailable"
        try:
            output = ensure_inside(workdir, os.path.join(workdir, chk["path"]))
            source = ensure_inside(fixture_root, os.path.join(fixture_root, chk["fixture"]))
        except EvalError:
            return False, "file path escapes its root"
        if not os.path.isfile(output): return False, "%s missing" % chk["path"]
        if not os.path.isfile(source): return False, "%s fixture missing" % chk["fixture"]
        if t_ == "file_equals_fixture":
            with open(output, "rb") as f: output_bytes = f.read()
            with open(source, "rb") as f: source_bytes = f.read()
            ok = output_bytes == source_bytes
            return ok, "%s %s fixture" % (chk["path"], "equals" if ok else "differs from")
        try:
            with open(source, newline="", encoding="utf-8") as f: rows = list(csv.reader(f))
            with open(output, newline="", encoding="utf-8") as f: actual = list(csv.reader(f))
        except (OSError, UnicodeError, csv.Error) as ex:
            return False, "CSV read failed: %s" % type(ex).__name__
        seen, expected = set(), []
        for row in rows:
            trimmed = tuple(cell.strip() for cell in row)
            if trimmed not in seen:
                seen.add(trimmed); expected.append(list(trimmed))
        ok = actual == expected and len(actual) == chk["data_rows"] + 1
        return ok, "%s: %d rows, expected %d and exact cleaned fixture content %s" % (
            chk["path"], max(0, len(actual) - 1), chk["data_rows"], "matched" if actual == expected else "mismatched")
    if t_ == "csv_rows":
        ok, evidence = _csv_rows_result(chk, workdir)
        return ok, evidence[:200]
    if t_ == "rule_check":
        return rule_check_result(chk, workdir)
    return False, "unknown check type"

def rule_check_target(chk):
    """(rule file name, unit kind, None) of a rule_check object, or (None, None, why). The rule file must be a loaded
    rule file of the engine's rules/ folder (the default bundle under RULESET_ROOT). The kind is the one unit kind the
    file's rules apply to; a file with rules for both kinds takes it from the output file name (SKILL.md or AGENT.md)."""
    import eva  # installs the host ops the bundle's rules compile against
    from engine import rule_runner
    rules, path = chk.get("rules"), chk.get("path")
    top = os.path.realpath(os.path.join(RULESET_ROOT, "rules"))
    name = rules[len("rules/"):] if isinstance(rules, str) and rules.startswith("rules/") else None
    full = os.path.join(top, name) if name and "/" not in name and "\\" not in name and name not in (".", "..") else None
    if full is None or os.path.dirname(os.path.realpath(full)) != top or not os.path.isfile(full):
        return None, None, "check.rules: %s is not a rule file of the engine's rules/ folder" % json.dumps(rules)
    try:
        bundle = rule_runner.default_bundle(RULESET_ROOT)
    except rule_runner.BundleError as ex:
        return None, None, "check.rules: the rule bundle does not load (%s)" % ex
    if name not in bundle.files():
        return None, None, "check.rules: %s is not a loaded rule file of the bundle" % json.dumps(rules)
    kinds = sorted({k for r in bundle.rules if r.file == name for k in r.applies_to} & set(rule_runner.MAIN_FILE))
    base = path.replace("\\", "/").rsplit("/", 1)[-1] if isinstance(path, str) else ""
    by_name = {v: k for k, v in rule_runner.MAIN_FILE.items()}
    if len(kinds) == 1: return name, kinds[0], None
    if kinds and base in by_name: return name, by_name[base], None
    if kinds:
        return None, None, ("check.path: %s holds rules for skills and agents; name the output file SKILL.md or "
                            "AGENT.md" % json.dumps(rules))
    return None, None, "check.rules: %s holds no skill or agent rule" % json.dumps(rules)

RULE_CHECK_UNIT_OPS = ("host_scenarios",)

def _rule_ops(node):
    if isinstance(node, dict):
        if isinstance(node.get("op"), str): yield node["op"]
        for v in node.values(): yield from _rule_ops(v)
    elif isinstance(node, list):
        for v in node: yield from _rule_ops(v)

def rule_check_result(chk, workdir):
    """A rule_check over one output file: the file becomes the main file of a one-file unit in a fresh temp folder
    (named after the output file's parent folder, `unit` for a file directly in the output folder) and the rule file's
    rows grade it; it passes only when every graded rule gives exactly one row reading pass or n/a. A rule whose check
    uses an op of RULE_CHECK_UNIT_OPS reads files beside the main file that a one-file unit never has: it is not graded
    and the evidence names it."""
    import eva
    from engine import rule_runner, schema
    name, kind, why = rule_check_target(chk)
    if why: return False, "rule_check %s" % why
    rel = chk["path"]
    src = os.path.join(workdir, *rel.split("/"))
    if schema.path_problem(rel) or "\0" in rel or not schema.inside(os.path.realpath(workdir), src):
        return False, "%s leaves the output folder" % rel
    if os.path.islink(src) or not os.path.isfile(src):
        return False, "%s is not a regular file in the output folder" % rel
    parent = rel.rsplit("/", 2)[-2] if "/" in rel else "unit"
    tmp = tempfile.mkdtemp(prefix="rule-check-")
    try:
        unit = os.path.join(tmp, parent if safe_name(parent) else "unit")
        os.makedirs(unit)
        shutil.copyfile(src, os.path.join(unit, rule_runner.MAIN_FILE[kind]))
        report = eva.Report(unit, kind)
        bundle = rule_runner.default_bundle(RULESET_ROOT)
        rule_runner.apply_families(report, [name], bundle)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rules = [r for r in bundle.rules if r.file == name and kind in r.applies_to]
    excluded = [r.id for r in rules if set(_rule_ops(r.node)) & set(RULE_CHECK_UNIT_OPS)]
    want = [r.id for r in rules if r.id not in excluded]
    got = {}
    for c in report.checks: got.setdefault(c["id"], []).append(c["status"])
    bad = ["%s %s" % (i, "/".join(got[i])) for i in want if i in got and (len(got[i]) != 1 or got[i][0] not in ("pass", "n/a"))]
    missing = [i for i in want if i not in got]
    ok = bool(want) and not bad and not missing
    detail = "; ".join(bad + (["missing %s" % ", ".join(missing)] if missing else []))
    if ok: detail = "%d row(s) pass or n/a" % len(want)
    elif not want: detail = "no rule of the file can be graded on one file"
    tail = "; excluded, they read files beside %s: %s" % (rule_runner.MAIN_FILE[kind], ", ".join(excluded)) if excluded else ""
    return ok, ("rule_check %s on %s (%s unit): %s" % (chk["rules"], rel, kind, detail))[:200] + tail

def walk_runs(it_dir):
    """Yields (engine, case_dir, config, run_dir) for eval runs (not trigger runs)."""
    for eng in sorted(os.listdir(it_dir)):
        ep = os.path.join(it_dir, eng)
        if not os.path.isdir(ep): continue
        for ed in sorted(os.listdir(ep)):
            if not ed.startswith("eval-"): continue
            for cfg in sorted(os.listdir(os.path.join(ep, ed))):
                cp = os.path.join(ep, ed, cfg)
                if not os.path.isdir(cp): continue
                for rd in sorted(os.listdir(cp)):
                    if re.match(r"run-\d+$", rd): yield eng, os.path.join(ep, ed), cfg, os.path.join(cp, rd)

def check_grader_model(it_dir, grader_model):
    """The grader tier must differ from the executor models and the builder recorded in the iteration's plan.json (not
    from CLI flags). grader_tier_problems does the comparison."""
    plan = rjson(os.path.join(it_dir, "plan.json"))
    if not isinstance(plan, dict) or not isinstance(plan.get("engines"), dict):
        raise EvalError("%s has no plan.json with executor models; cannot check the grader differs" % it_dir)
    probs = grader_tier_problems(grader_model, list(plan["engines"].items()), plan.get("builder_model"))
    if probs: raise EvalError("; ".join(probs))

def parse_id_list(text, flag):
    """'1,2' -> frozenset({1, 2}); None -> None (no narrowing). Positive integers only."""
    if text is None: return None
    out = set()
    for x in str(text).split(","):
        x = x.strip()
        if not re.match(r"^[1-9][0-9]*$", x): raise EvalError("%s: comma-separated positive integers, got %r" % (flag, text))
        out.add(int(x))
    return frozenset(out)

def grade_scope(a):
    """(case ids, run numbers) from grade --grade-cases/--grade-runs; None = all."""
    return (parse_id_list(getattr(a, "grade_cases", None), "--grade-cases"),
            parse_id_list(getattr(a, "grade_runs", None), "--grade-runs"))

def in_scope(scope, case_id, run_k):
    cs, rs = scope or (None, None)
    return (cs is None or case_id in cs) and (rs is None or run_k in rs)

def check_scope(spec, scope):
    cs = (scope or (None, None))[0]
    unknown = sorted(cs - {c["id"] for c in spec["cases"]}) if cs else []
    if unknown: raise EvalError("--grade-cases: no eval case with id %s" % ", ".join(map(str, unknown)))

def run_number(rd):
    return int(os.path.basename(rd).split("-")[1])

def grade_iteration(spec, ws, n, grader=None, seed=None, log=print, scope=None):
    """Static checks first, then (with a grader) LLM yes/no items and blind pairwise comparison; human items are
    queued in human_review.json and merged back when a reviewer filled in "passed". The grader's API-equivalent
    cost is appended to cost.json even when grading stops early."""
    it_dir = os.path.join(ws, "iteration-%d" % n)
    if not os.path.isdir(it_dir): raise EvalError("no %s" % it_dir)
    if grader is not None: check_grader_model(it_dir, grader.model)
    check_scope(spec, scope)
    before = dict(grader.usage) if grader is not None else None
    try:
        if grader is None: _grade(spec, ws, it_dir, grader, seed, log, scope)
        else:
            with spend_scope(grader, iteration=n):
                _grade(spec, ws, it_dir, grader, seed, log, scope)
    finally:
        if grader is not None and grader.usage["calls"] != before["calls"]:
            append_cost(it_dir, "grade", usage_delta(grader.usage, before))

def _grade(spec, ws, it_dir, grader, seed, log, scope=None):
    cases = {c["id"]: c for c in spec["cases"]}
    hr_path = os.path.join(it_dir, "human_review.json")
    old_hr = {x["key"]: x for x in (rjson(hr_path, {}) or {}).get("items", [])}
    queue = []
    for eng, ed, cfg, rd in walk_runs(it_dir):
        md = rjson(os.path.join(ed, "eval_metadata.json"), {})
        case = cases.get(md.get("eval_id"))
        t = rjson(os.path.join(rd, "outputs", "transcript.json"))
        if case is None or t is None: continue
        if not in_scope(scope, case["id"], run_number(rd)): continue  # out of --grade-cases/--grade-runs: untouched
        res, llm_items = {}, []
        for it in case["items"]:
            if it["layer"] == "static":
                ok, ev = static_check(it["check"], t, os.path.join(rd, "workdir"), spec["skill_dir"])
                res[it["id"]] = {"passed": ok, "evidence": ev, "status": "graded", "grader": "static"}
            elif it["layer"] == "llm":
                llm_items.append(it)
            else:
                key = "%s#%s" % (os.path.relpath(rd, it_dir), it["id"])
                prev = old_hr.get(key, {})
                decided = isinstance(prev.get("passed"), bool)
                queue.append({"key": key, "engine": eng, "eval_id": case["id"], "configuration": cfg,
                              "text": it["text"], "run_dir": os.path.relpath(rd, ws),
                              "passed": prev.get("passed"), "note": prev.get("note", "")})
                res[it["id"]] = {"passed": prev["passed"] if decided else None, "evidence": prev.get("note", "") if decided
                                 else "queued for human review", "status": "graded" if decided else "pending-human",
                                 "grader": "human"}
        if llm_items:
            if grader is None:
                for it in llm_items:
                    res[it["id"]] = {"passed": None, "evidence": "LLM grader not run (dry run)", "status": "not-graded", "grader": None}
            else:
                g = grader.grade(case, t, llm_items)
                for it in llm_items:
                    ok, ev = g[it["id"]]
                    res[it["id"]] = {"passed": ok, "evidence": ev, "status": "graded" if ok is not None else "not-graded",
                                     "grader": grader.model}
        exps = [dict({"text": it["text"], "id": it["id"], "layer": it["layer"]}, **res[it["id"]]) for it in case["items"]]
        graded = [e for e in exps if isinstance(e["passed"], bool)]
        p = sum(e["passed"] for e in graded)
        timing = rjson(os.path.join(rd, "timing.json"), {}) or {}
        wjson(os.path.join(rd, "grading.json"), {
            "expectations": exps,
            "summary": {"passed": p, "failed": len(graded) - p, "total": len(graded),
                        "pass_rate": round(p / len(graded), 4) if graded else None,
                        "pending": len(exps) - len(graded)},
            "execution_metrics": {"total_tool_calls": len(t.get("tool_calls") or []),
                                  "errors_encountered": 1 if t.get("error") else 0,
                                  "output_chars": len(t.get("final") or "")},
            "timing": {"executor_duration_seconds": timing.get("executor_duration_seconds", 0.0),
                       "total_duration_seconds": timing.get("total_duration_seconds", 0.0)}})
    if scope is not None and scope != (None, None):  # narrowed: keep queue entries of runs outside the scope
        mine = {q["key"] for q in queue}
        queue = [x for x in old_hr.values() if x["key"] not in mine] + queue
    wjson(hr_path, {"instructions": "Set \"passed\" to true/false (and optionally \"note\"), then re-run grade.",
                    "items": queue})
    if grader is not None: pairwise(spec, it_dir, grader, seed, scope)
    log("graded %s (%d human item(s) queued)" % (it_dir, sum(1 for q in queue if not isinstance(q["passed"], bool))))

def pair_list(spec, it_dir, scope=None):
    """[(eval_dir, case, baseline_config, k, path_with_skill, path_baseline)] for the blind pairwise comparison."""
    cases, out = {c["id"]: c for c in spec["cases"]}, []
    for eng in sorted(os.listdir(it_dir)):
        ep = os.path.join(it_dir, eng)
        if not os.path.isdir(ep): continue
        for ed in sorted(d for d in os.listdir(ep) if d.startswith("eval-")):
            case = cases.get((rjson(os.path.join(ep, ed, "eval_metadata.json"), {}) or {}).get("eval_id"))
            cfgs = [c for c in os.listdir(os.path.join(ep, ed)) if os.path.isdir(os.path.join(ep, ed, c))]
            base = next((c for c in cfgs if c in BASELINES.values()), None)
            if case is None or PRIMARY not in cfgs or base is None: continue
            k = 1
            while True:
                pa = os.path.join(ep, ed, PRIMARY, "run-%d" % k, "outputs", "transcript.json")
                pb = os.path.join(ep, ed, base, "run-%d" % k, "outputs", "transcript.json")
                if not (os.path.isfile(pa) and os.path.isfile(pb)): break
                if in_scope(scope, case["id"], k): out.append((os.path.join(ep, ed), case, base, k, pa, pb))
                k += 1
    return out

def grade_counts(spec, it_dir, scope=None):
    """(LLM grader calls, pairwise calls) that `grade --live` would make for this iteration."""
    cases, n = {c["id"]: c for c in spec["cases"]}, 0
    for eng, ed, cfg, rd in walk_runs(it_dir):
        case = cases.get((rjson(os.path.join(ed, "eval_metadata.json"), {}) or {}).get("eval_id"))
        if case and in_scope(scope, case["id"], run_number(rd)) \
                and os.path.isfile(os.path.join(rd, "outputs", "transcript.json")) \
                and any(it["layer"] == "llm" for it in case["items"]):
            n += 1
    return n, len(pair_list(spec, it_dir, scope))

def pairwise(spec, it_dir, grader, seed=None, scope=None):
    """Blind A/B per (engine, case, run k): with_skill run-k vs baseline run-k, order randomized and recorded."""
    seed = random.SystemRandom().randrange(2 ** 31) if seed is None else seed
    rng = random.Random(seed)
    for ed, case, base, k, pa, pb in pair_list(spec, it_dir, scope):
        swap = rng.random() < 0.5
        a_cfg, b_cfg = (base, PRIMARY) if swap else (PRIMARY, base)
        ta, tb = rjson(pa if not swap else pb), rjson(pb if not swap else pa)
        try:
            w, why = grader.compare(case, ta, tb)
        except JudgeAnswerError as ex:  # paid call, unusable answer: recorded as failed (benchmark skips it), stop
            wjson(os.path.join(ed, "comparison-%d.json" % k), {
                "winner": None, "status": "failed", "error": str(ex), "invalid_winner": str(ex.winner)[:40],
                "mapping": {"A": a_cfg, "B": b_cfg}, "winner_configuration": None, "seed": seed,
                "grader_model": grader.model})
            raise
        winner_cfg = {"A": a_cfg, "B": b_cfg}.get(w, "tie")
        wjson(os.path.join(ed, "comparison-%d.json" % k), {
            "winner": w, "reasoning": why, "mapping": {"A": a_cfg, "B": b_cfg}, "winner_configuration": winner_cfg,
            "seed": seed, "grader_model": grader.model})

def load_golden(golden_path):
    try: data = rjson(golden_path)
    except ValueError as ex: raise EvalError("%s: invalid JSON: %s" % (golden_path, ex))
    ex = data.get("examples") if isinstance(data, dict) else None
    if not (isinstance(ex, list) and ex): raise EvalError("%s: {\"examples\": [...]} required" % golden_path)
    for i, e in enumerate(ex):
        if not (isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("prompt", "output", "item"))
                and isinstance(e.get("label"), bool)):
            raise EvalError("%s: examples[%d] needs prompt, output, item (str) and label (bool)" % (golden_path, i))
    return ex

def calibrate(golden_path, grader):
    """Golden examples: {"examples": [{"id", "prompt", "expected_output"?, "output", "item", "label": bool}]}.
    Returns agreement (fraction equal), Cohen's kappa and the disagreements."""
    rows = []
    for i, e in enumerate(load_golden(golden_path)):
        case = {"prompt": e["prompt"], "expected_output": e.get("expected_output", "")}
        g = grader.grade(case, {"final": e["output"], "tool_calls": e.get("tool_calls") or []}, [{"id": "g", "text": e["item"]}])["g"][0]
        rows.append({"id": e.get("id", i), "item": e["item"], "human": e["label"], "grader": g})
    n = len(rows)
    agree = sum(1 for r in rows if r["grader"] is r["human"]) / n
    ph = sum(r["human"] for r in rows) / n
    pg = sum(1 for r in rows if r["grader"] is True) / n
    pe = ph * pg + (1 - ph) * (1 - pg)
    kappa = 1.0 if pe == 1 else (agree - pe) / (1 - pe)
    return {"n": n, "agreement": round(agree, 4), "kappa": round(kappa, 4), "threshold": AGREEMENT_MIN,
            "grader_model": grader.model, "golden": os.path.abspath(golden_path),
            "disagreements": [r for r in rows if r["grader"] is not r["human"]]}

def calibrate_metered(golden_path, grader, it_dir):
    """calibrate() plus its API-equivalent grader cost in it_dir/cost.json (recorded even when it stops early)."""
    before = dict(grader.usage)
    try:
        with spend_scope(grader, iteration=iteration_of(it_dir), kind="calibrate"):
            return calibrate(golden_path, grader)
    finally:
        if grader.usage["calls"] != before["calls"]: append_cost(it_dir, "calibrate", usage_delta(grader.usage, before))

# ---------- aggregation, report, gate ----------
def stats(values):
    """Same as skill-creator aggregate_benchmark.calculate_stats (sample stddev, rounded to 4)."""
    if not values: return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}
    n, mean = len(values), sum(values) / len(values)
    sd = math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1)) if n > 1 else 0.0
    return {"mean": round(mean, 4), "stddev": round(sd, 4), "min": round(min(values), 4), "max": round(max(values), 4)}

def collect(it_dir):
    runs = []
    for eng, ed, cfg, rd in walk_runs(it_dir):
        md = rjson(os.path.join(ed, "eval_metadata.json"), {}) or {}
        g = rjson(os.path.join(rd, "grading.json"))
        tm = rjson(os.path.join(rd, "timing.json"), {}) or {}
        if g is None: continue
        s = g["summary"]
        runs.append({"engine": eng, "eval_id": md.get("eval_id"), "eval_name": md.get("eval_name"), "configuration": cfg,
                     "run_number": int(os.path.basename(rd).split("-")[1]), "pressure": bool(md.get("pressure")),
                     "result": {"pass_rate": s["pass_rate"], "passed": s["passed"], "failed": s["failed"], "total": s["total"],
                                "pending": s.get("pending", 0), "time_seconds": tm.get("total_duration_seconds", 0.0),
                                "tokens": tm.get("total_tokens", 0), "tool_calls": g["execution_metrics"]["total_tool_calls"],
                                "errors": g["execution_metrics"]["errors_encountered"], "cost_usd": tm.get("cost_usd")},
                     "expectations": g["expectations"], "notes": []})
    return runs

def summarize(runs):
    configs = sorted({r["configuration"] for r in runs}, key=lambda c: (c != PRIMARY, c))
    out = {}
    for c in configs:
        rs = [r for r in runs if r["configuration"] == c]
        out[c] = {"pass_rate": stats([r["result"]["pass_rate"] for r in rs if r["result"]["pass_rate"] is not None]),
                  "time_seconds": stats([r["result"]["time_seconds"] for r in rs]),
                  "tokens": stats([r["result"]["tokens"] for r in rs]),
                  "graded_runs": sum(1 for r in rs if r["result"]["pass_rate"] is not None), "runs": len(rs)}
    if len(configs) >= 2:
        a, b = out[configs[0]], out[configs[1]]
        out["delta"] = {"pass_rate": "%+.2f" % (a["pass_rate"]["mean"] - b["pass_rate"]["mean"]),
                        "time_seconds": "%+.1f" % (a["time_seconds"]["mean"] - b["time_seconds"]["mean"]),
                        "tokens": "%+.0f" % (a["tokens"]["mean"] - b["tokens"]["mean"])}
    return out

def analyzer_notes(runs):
    """agentskills.io 'Analyzing patterns': always-pass / always-fail assertions in both configs, high-variance evals."""
    notes, by_item = [], {}
    for r in runs:
        for e in r["expectations"]:
            if isinstance(e.get("passed"), bool):
                by_item.setdefault((r["engine"], r["eval_id"], e["text"]), {}).setdefault(r["configuration"], []).append(e["passed"])
    for (eng, eid, text), cf in sorted(by_item.items(), key=lambda x: str(x[0])):
        if len(cf) >= 2 and all(all(v) for v in cf.values()):
            notes.append("[%s] eval %s: '%s' passes in every configuration - may not differentiate skill value" % (eng, eid, text))
        if len(cf) >= 2 and all(not any(v) for v in cf.values()):
            notes.append("[%s] eval %s: '%s' fails in every configuration - assertion or case may be broken" % (eng, eid, text))
    groups = {}
    for r in runs:
        if r["result"]["pass_rate"] is not None:
            groups.setdefault((r["engine"], r["eval_id"], r["configuration"]), []).append(r["result"]["pass_rate"])
    for (eng, eid, cfg), v in sorted(groups.items(), key=lambda x: str(x[0])):
        s = stats(v)
        if len(v) > 1 and s["stddev"] >= HIGH_VARIANCE_STDDEV:
            notes.append("[%s] eval %s %s: high variance (%.0f%% ± %.0f%%) - flaky or ambiguous instructions"
                         % (eng, eid, cfg, s["mean"] * 100, s["stddev"] * 100))
    return notes

def build_benchmark(spec, ws, n):
    it_dir = os.path.join(ws, "iteration-%d" % n)
    plan = rjson(os.path.join(it_dir, "plan.json"), {}) or {}
    runs = collect(it_dir)
    engines = sorted({r["engine"] for r in runs})
    per_engine, trig, pw = {}, {}, {}
    for eng in engines:
        er = [r for r in runs if r["engine"] == eng]
        bench_e = {"metadata": {"skill_name": spec["skill_name"], "skill_path": spec["skill_dir"], "engine": eng,
                                "executor_model": (plan.get("engines") or {}).get(eng), "timestamp": now_iso(),
                                "evals_run": sorted({r["eval_id"] for r in er}, key=str),
                                "runs_per_configuration": plan.get("runs_per_configuration")},
                   "runs": [dict((k, v) for k, v in r.items() if k != "engine") for r in er],
                   "run_summary": summarize(er), "notes": analyzer_notes(er)}
        wjson(os.path.join(it_dir, eng, "benchmark.json"), bench_e)  # skill-creator-compatible per-engine file
        per_engine[eng] = bench_e["run_summary"]
        tdir = os.path.join(it_dir, eng, "trigger")
        for cfg in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
            tr = rjson(os.path.join(tdir, cfg, "results.json"))
            if tr:
                rs = tr["results"]
                trig.setdefault(eng, {})[cfg] = dict(tr["summary"],
                    should_trigger_passed=sum(1 for x in rs if x["should_trigger"] and x["pass"]),
                    should_not_trigger_passed=sum(1 for x in rs if not x["should_trigger"] and x["pass"]),
                    failed_queries=[x["query"] for x in rs if not x["pass"]])
        comps = []
        for ed in os.listdir(os.path.join(it_dir, eng)):
            if ed.startswith("eval-"):
                for f in os.listdir(os.path.join(it_dir, eng, ed)):
                    c = rjson(os.path.join(it_dir, eng, ed, f)) if re.match(r"comparison-\d+\.json$", f) else None
                    if isinstance(c, dict) and c.get("winner") in ("A", "B", "TIE"): comps.append(c)
        if comps:
            pw[eng] = {"with_skill_wins": sum(c["winner_configuration"] == PRIMARY for c in comps),
                       "baseline_wins": sum(c["winner_configuration"] not in (PRIMARY, "tie") for c in comps),
                       "ties": sum(c["winner_configuration"] == "tie" for c in comps), "total": len(comps)}
    static_fail = [{"engine": r["engine"], "eval_id": r["eval_id"], "run_number": r["run_number"], "text": e["text"],
                    "evidence": e["evidence"]} for r in runs if r["configuration"] == PRIMARY
                   for e in r["expectations"] if e["layer"] == "static" and e["passed"] is False]
    completeness = None
    if plan.get("engines") is not None:
        cfgs, rpc = plan.get("configurations") or [], plan.get("runs_per_configuration") or 0
        skill_cfgs = [c for c in cfgs if c in (PRIMARY, "old_skill")]
        completeness = {}
        for eng in sorted(plan["engines"]):
            tres = [x for c in skill_cfgs
                    for x in ((rjson(os.path.join(it_dir, eng, "trigger", c, "results.json"), {}) or {}).get("results") or [])]
            completeness[eng] = {"expected_runs": len(plan.get("evals_run") or []) * len(cfgs) * rpc,
                                 "graded_runs": sum(1 for r in runs if r["engine"] == eng),
                                 "expected_trigger_runs": (plan.get("trigger_queries") or 0) * rpc * len(skill_cfgs),
                                 "trigger_runs": sum(x.get("runs", 0) for x in tres),
                                 "trigger_errors": sum(x.get("errors", 0) for x in tres),
                                 # one blind comparison per (case, run k) when two configurations are compared
                                 "expected_comparisons": len(plan.get("evals_run") or []) * rpc if len(cfgs) == 2 else 0,
                                 "comparisons": (pw.get(eng) or {}).get("total", 0)}
    import eva
    coverage = runs_coverage(plan.get("engines"), runs)
    exec_errors = [{"engine": r["engine"], "eval_id": r["eval_id"], "configuration": r["configuration"],
                    "run_number": r["run_number"]} for r in runs if r["result"]["errors"]]
    pressure = {}
    for flag, label in ((True, "pressure"), (False, "normal")):
        rs = [r for r in runs if r["pressure"] is flag]
        if rs: pressure[label] = summarize(rs)
    bench = {"metadata": {"skill_name": spec["skill_name"], "skill_path": spec["skill_dir"], "timestamp": now_iso(),
                          "engines": plan.get("engines"), "configurations": plan.get("configurations"),
                          "evals_run": plan.get("evals_run"), "runs_per_configuration": plan.get("runs_per_configuration"),
                          "billing": plan.get("billing", BILLING), "stopped": plan.get("stopped"),
                          # what was tested (recorded at run start) vs what is on disk now; register compares them
                          "skill_sha256": plan.get("skill_sha256"), "eval_set_sha256": plan.get("eval_set_sha256"),
                          "current_skill_sha256": skill_tree_hash(spec["skill_dir"]),
                          "current_eval_set_sha256": gate_hash("eval_set", spec, ws),
                          "ruleset_sha256": plan.get("ruleset_sha256"), "current_ruleset_sha256": ruleset_sha256()},
             "tool_coverage": coverage,
             "parity": {t: st["status"] for t, st in eva.parity_status(spec["skill_dir"]).items()},
             "runs": runs, "run_summary": summarize(runs), "per_engine": per_engine, "trigger": trig, "pairwise": pw,
             "static_failures": static_fail, "pressure": pressure, "completeness": completeness,
             "execution_errors": exec_errors,
             # API-equivalent cost (usage x API prices / CLI-reported) kept apart from actual spend (subscription)
             "cost": cost_summary(it_dir, ws),
             # counted from grading.json (not the human_review.json queue): an item is graded only once grade wrote it
             "human_pending": sum(1 for r in runs for e in r["expectations"]
                                  if e.get("layer") == "human" and not isinstance(e.get("passed"), bool)),
             "llm_not_graded": sum(1 for r in runs for e in r["expectations"]
                                   if e.get("layer") != "human" and not isinstance(e.get("passed"), bool)),
             "calibration": rjson(os.path.join(it_dir, "calibration.json")),
             "notes": analyzer_notes(runs)}
    return bench

def _meta(bench):
    return bench.get("metadata") or {}

def _regression_baseline(bench, prev, eng, rs):
    base = next((c for c in rs if c in BASELINES.values()), None)
    if PRIMARY in rs and base and rs[PRIMARY]["graded_runs"] and rs[base]["graded_runs"]:
        w, b = rs[PRIMARY]["pass_rate"]["mean"], rs[base]["pass_rate"]["mean"]
        if w < b - REGRESSION_TOLERANCE: return {"engine": eng, "with_skill": w, "baseline": base, "baseline_rate": b}

def _regression_previous(bench, prev, eng, rs):
    pr = ((prev or {}).get("per_engine") or {}).get(eng, {}).get(PRIMARY)
    if pr and pr.get("graded_runs") and rs.get(PRIMARY, {}).get("graded_runs") \
            and rs[PRIMARY]["pass_rate"]["mean"] < pr["pass_rate"]["mean"] - REGRESSION_TOLERANCE:
        return {"engine": eng, "with_skill": rs[PRIMARY]["pass_rate"]["mean"], "previous": pr["pass_rate"]["mean"]}

def _low_agreement(bench, prev):
    cal = bench.get("calibration")
    if cal and cal.get("agreement", 1.0) < AGREEMENT_MIN:
        return {"agreement": cal["agreement"], "threshold": AGREEMENT_MIN, "n": cal["n"]}

COVERAGE_STATES = {"run": "run", "missing": "coverage-missing", "not-required": "not required"}

def coverage_states(bench):
    """[(tool, fields)] per tool of approvals.COVERAGE_TOOLS from bench alone (tool_coverage, coverage_waivers). A tool
    ran only with status run and a graded run; an absent or malformed entry counts as not run (fail closed)."""
    cov, waivers = bench.get("tool_coverage"), bench.get("coverage_waivers")
    out = []
    for tool in approvals.COVERAGE_TOOLS:
        c = cov.get(tool) if isinstance(cov, dict) else None
        status, graded = (c.get("status"), c.get("graded_runs")) if isinstance(c, dict) else (None, None)
        ran = status == "run" and type(graded) is int and graded > 0
        if not ran and (status == "run" or not isinstance(status, str)): status = "unknown (no valid tool_coverage entry)"
        w = waivers.get(tool) if isinstance(waivers, dict) else None
        w = w if isinstance(w, dict) and w.get("status") in approvals.STATUSES else {"status": "missing"}
        machine = w.get("machine_waiver") if w.get("source") == "machine" else None
        machine = machine if isinstance(machine, dict) and tool in MACHINE_WAIVABLE else None
        state = ("run" if ran else "not-required" if tool not in COVERAGE_REQUIRED else
                 "missing" if w["status"] != "approved" or (w.get("source") == "machine" and not machine) else
                 "machine-waived" if machine else "waived")
        out.append((tool, {"state": state, "tool": tool, "status": status, "principal": w.get("principal"),
                           "reason": w.get("reason"), "waiver": w["status"] if w["status"] in ("missing", "unsigned")
                           else "%s (%s)" % (w["status"], w.get("verdict_reason")),
                           "waiver_hash": (machine or {}).get("sha256"), "expires": (machine or {}).get("expires")}))
    return out

MACHINE_WAIVED_LABEL = "waived by machine policy "

def coverage_label(c):
    if c["state"] == "waived": return "waived by %s" % c["principal"]
    if c["state"] == "machine-waived": return MACHINE_WAIVED_LABEL + (c["waiver_hash"] or "")[:12]
    return COVERAGE_STATES[c["state"]]

def _hashes_bound(bench):
    m = _meta(bench)
    return bench.get("completeness") is not None and m.get("skill_sha256") and m.get("eval_set_sha256")

def _stale_rules(bench, prev):
    m = _meta(bench)
    if "current_ruleset_sha256" in m and m.get("ruleset_sha256") and m["ruleset_sha256"] != m["current_ruleset_sha256"]:
        return {}

def _stale_rules_unrecorded(bench, prev):
    m = _meta(bench)
    if "current_ruleset_sha256" in m and not m.get("ruleset_sha256"): return {}

def _trigger_failed(bench, prev, eng, cf):
    t = cf.get(PRIMARY)
    if t and t.get("failed"): return {"engine": eng, "failed": t["failed"], "total": t["total"]}

# (id, kind, scope, fields, body). body(bench, prev[, engine, value]) returns the fields its policy/gate.rules.json
# template renders, or None. Consecutive rows that share a scope run per engine of GATE_SCOPES[scope], interleaved.
# The stale-rules rows fire only when the metadata holds current_ruleset_sha256, which build_benchmark always writes.
GATE_PREDICATES = (
    ("regression-baseline", "problem", "per_engine", ("engine", "with_skill", "baseline", "baseline_rate"),
     _regression_baseline),
    ("regression-previous", "problem", "per_engine", ("engine", "with_skill", "previous"), _regression_previous),
    ("low-agreement", "problem", None, ("agreement", "threshold", "n"), _low_agreement),
    ("not-calibrated", "note", None, (), lambda b, p: None if b.get("calibration") else {}),
    ("static-failures", "problem", None, ("count",),
     lambda b, p: {"count": len(b["static_failures"])} if b.get("static_failures") else None),
    ("run-stopped", "problem", None, ("stopped",),
     lambda b, p: {"stopped": _meta(b)["stopped"]} if _meta(b).get("stopped") else None),
    ("no-plan", "problem", None, (), lambda b, p: {} if b.get("completeness") is None else None),
    ("eval-runs-incomplete", "problem", "completeness", ("engine", "graded", "expected"),
     lambda b, p, e, c: {"engine": e, "graded": c["graded_runs"], "expected": c["expected_runs"]}
     if c["graded_runs"] < c["expected_runs"] else None),
    ("trigger-runs-incomplete", "problem", "completeness", ("engine", "runs", "expected"),
     lambda b, p, e, c: {"engine": e, "runs": c["trigger_runs"], "expected": c["expected_trigger_runs"]}
     if c["trigger_runs"] < c["expected_trigger_runs"] else None),
    ("trigger-errors", "problem", "completeness", ("engine", "errors"),
     lambda b, p, e, c: {"engine": e, "errors": c["trigger_errors"]} if c.get("trigger_errors") else None),
    ("comparisons-incomplete", "problem", "completeness", ("engine", "comparisons", "expected"),
     lambda b, p, e, c: {"engine": e, "comparisons": c.get("comparisons", 0), "expected": c["expected_comparisons"]}
     if c.get("comparisons", 0) < c.get("expected_comparisons", 0) else None),
    ("unbound", "problem", None, (),
     lambda b, p: {} if b.get("completeness") is not None and not _hashes_bound(b) else None),
    ("stale-skill", "problem", None, (),
     lambda b, p: {} if _hashes_bound(b) and _meta(b).get("current_skill_sha256") != _meta(b)["skill_sha256"] else None),
    ("stale-eval-set", "problem", None, (),
     lambda b, p: {} if _hashes_bound(b) and _meta(b).get("current_eval_set_sha256") != _meta(b)["eval_set_sha256"]
     else None),
    ("stale-rules", "problem", None, (), _stale_rules),
    ("stale-rules-unrecorded", "problem", None, (), _stale_rules_unrecorded),
    ("execution-errors", "problem", None, ("count",),
     lambda b, p: {"count": len(b["execution_errors"])} if b.get("execution_errors") else None),
    ("trigger-failed", "problem", "trigger", ("engine", "failed", "total"), _trigger_failed),
    ("llm-ungraded", "problem", None, ("count",),
     lambda b, p: {"count": b["llm_not_graded"]} if b.get("llm_not_graded") else None),
    ("human-pending", "problem", None, ("count",),
     lambda b, p: {"count": b["human_pending"]} if b.get("human_pending") else None),
    ("coverage-missing", "problem", "coverage", ("tool", "status", "waiver"),
     lambda b, p, t, c: c if c["state"] == "missing" else None),
    ("coverage-waived", "note", "coverage", ("tool", "status", "principal", "reason"),
     lambda b, p, t, c: c if c["state"] == "waived" else dict(c, principal="machine policy %s (%s, expires %s)" % (
         c["waiver_hash"], c["principal"], c["expires"])) if c["state"] == "machine-waived" else None),
    ("coverage-not-required", "note", "coverage", ("tool", "status"),
     lambda b, p, t, c: c if c["state"] == "not-required" else None),
)
GATE_SCOPES = {"per_engine": lambda b: list((b.get("per_engine") or {}).items()),
               "completeness": lambda b: sorted((b.get("completeness") or {}).items()),
               "trigger": lambda b: sorted((b.get("trigger") or {}).items()),
               "coverage": coverage_states}
RULES_STALE_IDS = ("stale-rules", "stale-rules-unrecorded")
GATE_FIELD_RE = re.compile(r"%(?:%|\(([A-Za-z_]+)\)[#0 +-]*[0-9]*(?:\.[0-9]+)?[diouxXeEfFgGcrsa])")

def load_gate_messages():
    """{predicate id: template} from policy/gate.rules.json next to this file (POLICY_DIR); raises PolicyError on a
    missing or unreadable file, non-strict JSON, ids other than exactly those of GATE_PREDICATES, an entry without a
    non-empty ASCII template and a source, or a template whose % is not %% or %(field)<conversion> of a field its
    predicate supplies."""
    from engine import schema
    where = "policy/gate.rules.json"
    try:
        doc = schema.load(os.path.join(POLICY_DIR, "gate.rules.json"))
    except schema.JsonError as e:
        raise PolicyError("%s: %s" % (where, e)) from None
    msgs = doc.get("messages") if isinstance(doc, dict) else None
    ids = [row[0] for row in GATE_PREDICATES]
    if not isinstance(msgs, dict) or sorted(msgs) != sorted(ids):
        raise PolicyError("%s: messages must name exactly %s" % (where, ", ".join(ids)))
    out = {}
    for pid, _, _, fields, _ in GATE_PREDICATES:
        entry, at = msgs[pid], "%s: messages.%s" % (where, pid)
        t = entry.get("template") if isinstance(entry, dict) else None
        if not (isinstance(t, str) and t.strip() and t.isascii() and isinstance(entry.get("source"), str)
                and entry["source"].strip()):
            raise PolicyError("%s: needs a non-empty ASCII template and a source" % at)
        if "%" in GATE_FIELD_RE.sub("", t):
            raise PolicyError("%s: template %s holds a %% that is not %%%% or %%(field)<conversion>" % (at, json.dumps(t)))
        unknown = sorted(set(GATE_FIELD_RE.findall(t)) - set(fields) - {""})
        if unknown:
            raise PolicyError("%s: template field %s is not one the predicate supplies (%s)"
                              % (at, ", ".join(unknown), ", ".join(fields) or "none"))
        out[pid] = t
    return out

GATE_MESSAGES = _policy_or_exit(load_gate_messages)

def load_coverage_required():
    from engine import schema
    where = "policy/gate.rules.json"
    try:
        doc = schema.load(os.path.join(POLICY_DIR, "gate.rules.json"))
    except schema.JsonError as e:
        raise PolicyError("%s: %s" % (where, e)) from None
    v = doc.get("coverage_required") if isinstance(doc, dict) else None
    if not (isinstance(v, list) and v and all(isinstance(t, str) and t in approvals.COVERAGE_TOOLS for t in v)
            and len(set(v)) == len(v)):
        raise PolicyError("%s: coverage_required must be a non-empty list of unique names from %s, got %s"
                          % (where, ", ".join(approvals.COVERAGE_TOOLS), json.dumps(v)))
    return tuple(t for t in approvals.COVERAGE_TOOLS if t in v)

COVERAGE_REQUIRED = _policy_or_exit(load_coverage_required)

def load_machine_waivable():
    """machine_waivable of policy/gate.rules.json: the tools a machine waiver may clear, a non-empty list of unique
    names from opencode and hermes (gates.md G33); claude is never machine-waivable."""
    from engine import schema
    where = "policy/gate.rules.json"
    try:
        doc = schema.load(os.path.join(POLICY_DIR, "gate.rules.json"))
    except schema.JsonError as e:
        raise PolicyError("%s: %s" % (where, e)) from None
    v = doc.get("machine_waivable") if isinstance(doc, dict) else None
    allowed = ("opencode", "hermes")
    if not (isinstance(v, list) and v and all(isinstance(t, str) and t in allowed for t in v) and len(set(v)) == len(v)):
        raise PolicyError("%s: machine_waivable must be a non-empty list of unique names from %s, got %s"
                          % (where, ", ".join(allowed), json.dumps(v)))
    return tuple(t for t in approvals.COVERAGE_TOOLS if t in v)

MACHINE_WAIVABLE = _policy_or_exit(load_machine_waivable)

def compute_gate(bench, prev=None):
    """GATE_PREDICATES in order, each hit rendered with its policy/gate.rules.json template. human_pending_only ignores
    the stale-rules and coverage-missing problems, so it keeps its B1 result for every input."""
    problems, notes, ids = [], [], []
    for scope, rows in itertools.groupby(GATE_PREDICATES, key=lambda row: row[2]):
        rows = list(rows)
        for item in (GATE_SCOPES[scope](bench) if scope else [()]):
            for pid, kind, _, _, body in rows:
                fields = body(bench, prev, *item)
                if fields is None: continue
                (problems if kind == "problem" else notes).append(GATE_MESSAGES[pid] % fields)
                if kind == "problem": ids.append(pid)
    b1 = [pid for pid in ids if pid not in RULES_STALE_IDS and pid != "coverage-missing"]
    return {"problems": problems, "needs_approval": bool(problems), "notes": notes,
            "human_pending_only": bool(b1) and all(pid == "human-pending" for pid in b1),
            "coverage_only": bool(ids) and all(pid == "coverage-missing" for pid in ids),
            "coverage": {t: coverage_label(c) for t, c in coverage_states(bench)}}

def fmt_stat(s, pct=False):
    return ("%.0f%% ± %.0f%%" % (s["mean"] * 100, s["stddev"] * 100)) if pct else ("%.1f ± %.1f" % (s["mean"], s["stddev"]))

def comparison_md(bench, gate, n):
    m = bench["metadata"]
    spent = bench.get("cost") or {}
    metered = spent.get("actual_spend_usd") is not None
    total, stopped = (spent.get("api_equivalent_usd") or {}).get("total", 0.0), ", STOPPED: %s" % m["stopped"] if m.get("stopped") else ""
    L = ["# %s — iteration %d" % (m["skill_name"], n), "",
         "- configurations: %s, runs per configuration: %s, engines: %s" % (", ".join(m.get("configurations") or []),
             m.get("runs_per_configuration"), ", ".join("%s(%s)" % (k, v or "default") for k, v in (m.get("engines") or {}).items())),
         ("- API-equivalent cost: $%.2f (usage x API prices; the budget limit)%s; actual spend: $%s (CLI-reported, "
          "API-key mode, auth profile %s); hard cap: %s" % (total, stopped, spend.money(spent["actual_spend_usd"]),
                                                            spent.get("auth_profile"), ", ".join(
              "$%s (--budget-usd of %s)" % (spend.money(x["cap_usd"]), x["command"]) for x in spent.get("caps") or [])))
         if metered else
         "- API-equivalent cost: $%.2f (usage x API prices; the budget limit); actual spend: not metered (%s auth)%s"
         % (total, m.get("billing") or BILLING, stopped), "",
         "## Gate", "", "**%s**" % ("NEEDS HUMAN APPROVAL" if gate["needs_approval"] else "auto-approved (no problems)")]
    L += ["- problem: " + p for p in gate["problems"]] + ["- note: " + x for x in gate["notes"]]
    L += ["", "## Pass rate / time / tokens", "", "| engine | configuration | pass_rate | time_s | tokens | graded runs |",
          "|---|---|---|---|---|---|"]
    for eng, rs in sorted(bench["per_engine"].items()):
        for cfg, s in rs.items():
            if cfg == "delta": continue
            L.append("| %s | %s | %s | %s | %s | %d/%d |" % (eng, cfg, fmt_stat(s["pass_rate"], True), fmt_stat(s["time_seconds"]),
                     fmt_stat(s["tokens"]), s["graded_runs"], s["runs"]))
        if "delta" in rs:
            d = rs["delta"]
            L.append("| %s | **delta** | %s | %s | %s | |" % (eng, d["pass_rate"], d["time_seconds"], d["tokens"]))
    if bench.get("tool_coverage"):
        L += ["", "## Tool coverage (multi-tool parity)", "",
              "| tool | static parity (eva.py) | dynamic eval | graded runs | registration |", "|---|---|---|---|---|"]
        for t, c in bench["tool_coverage"].items():
            L.append("| %s | %s | %s | %d%s | %s |" % (t, (bench.get("parity") or {}).get(t, "unknown"), c["status"], c["graded_runs"],
                                              " (+%d pending)" % c["pending_runs"] if c.get("pending_runs") else "",
                                              gate["coverage"].get(t, "unknown")))
    if bench["trigger"]:
        L += ["", "## Trigger evals (threshold %.1f)" % TRIGGER_THRESHOLD, "",
              "| engine | configuration | passed | should-trigger ok | should-not ok |", "|---|---|---|---|---|"]
        for eng, cf in sorted(bench["trigger"].items()):
            for cfg, s in cf.items():
                L.append("| %s | %s | %d/%d | %d | %d |" % (eng, cfg, s["passed"], s["total"], s["should_trigger_passed"],
                                                          s["should_not_trigger_passed"]))
                L += ["  - failed: %s" % q for q in s["failed_queries"]]
    if bench["pairwise"]:
        L += ["", "## Blind pairwise (order randomized)", "", "| engine | with_skill wins | baseline wins | ties |", "|---|---|---|---|"]
        for eng, s in sorted(bench["pairwise"].items()):
            L.append("| %s | %d | %d | %d |" % (eng, s["with_skill_wins"], s["baseline_wins"], s["ties"]))
    if bench["pressure"]:
        L += ["", "## Pressure scenarios", ""]
        for label, rs in bench["pressure"].items():
            L.append("- %s: " % label + ", ".join("%s %s" % (c, fmt_stat(s["pass_rate"], True)) for c, s in rs.items() if c != "delta"))
    cal = bench.get("calibration")
    L += ["", "## Grader calibration", "", ("- agreement %.2f, kappa %.2f on %d golden examples (threshold %.2f), grader %s"
          % (cal["agreement"], cal["kappa"], cal["n"], cal["threshold"], cal["grader_model"])) if cal else "- not run"]
    if bench["static_failures"]:
        L += ["", "## Static check failures (with_skill)", ""]
        L += ["- [%s] eval %s run-%s: %s — %s" % (f["engine"], f["eval_id"], f["run_number"], f["text"], f["evidence"])
              for f in bench["static_failures"]]
    L += ["", "## Per eval", "", "| engine | eval | configuration | run | pass_rate | pending | time_s | tokens |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sorted(bench["runs"], key=lambda r: (r["engine"], str(r["eval_id"]), r["configuration"] != PRIMARY, r["run_number"])):
        x = r["result"]
        L.append("| %s | %s | %s | %d | %s | %d | %.1f | %d |" % (r["engine"], r["eval_name"], r["configuration"], r["run_number"],
                 "-" if x["pass_rate"] is None else "%.2f" % x["pass_rate"], x["pending"], x["time_seconds"], x["tokens"]))
    c = bench.get("cost")
    if c:
        ae = c["api_equivalent_usd"]
        L += ["", "## Cost", "", "Billing: %s. API-equivalent = CLI-reported cost, else usage tokens x API price (or the "
              "charged cap when a call reported no usage); it is what `--budget-usd` limits. Actual spend: %s."
              % (c["billing"], c["actual_spend_note"]), "", "| item | API-equivalent USD |", "|---|---|"]
        for eng, kinds in sorted(ae["by_engine"].items()):
            L += ["| %s %s | %.4f |" % (eng, k, v) for k, v in sorted(kinds.items())]
        L += ["| %s | %.4f |" % (k, v) for k, v in sorted(ae["other"].items())]
        if metered:
            L += ["| **total** | **%.4f** |" % ae["total"], "| actual spend | $%s |" % spend.money(c["actual_spend_usd"])]
            L += ["| hard cap | $%s (%s) |" % (spend.money(x["cap_usd"]), x["command"]) for x in c.get("caps") or []]
            if c.get("unreconciled_calls"):
                L.append("| unreconciled calls | %d (worst case $%s counted at a later start) |"
                         % (c["unreconciled_calls"], spend.money(c["unreconciled_bound_usd"])))
        else:
            L += ["| **total** | **%.4f** |" % ae["total"], "| actual spend | not metered |"]
        if c.get("runs_without_usage"):
            L.append("- %d run(s) reported no usage and no price: counted as $0 here" % c["runs_without_usage"])
    if bench["notes"]:
        L += ["", "## Notes", ""] + ["- " + x for x in bench["notes"]]
    return "\n".join(L) + "\n"

def report_iteration(spec, ws, n):
    bench = build_benchmark(spec, ws, n)
    bench["coverage_waivers"] = waiver_verdicts(spec, ws)
    prev = rjson(os.path.join(ws, "iteration-%d" % (n - 1), "benchmark.json")) if n > 1 else None
    gate = compute_gate(bench, prev)
    bench["gate"] = gate
    it_dir = os.path.join(ws, "iteration-%d" % n)
    wjson(os.path.join(it_dir, "benchmark.json"), bench)
    wjson(os.path.join(it_dir, "gate.json"), gate)
    wtext(os.path.join(it_dir, "comparison.md"), comparison_md(bench, gate, n))
    return bench, gate

def benchmark_runs(bench):
    """(runs, planned engines) of a benchmark when both are well formed, else None."""
    if not (isinstance(bench, dict) and isinstance(_meta(bench), dict)): return None
    runs, planned = bench.get("runs"), _meta(bench).get("engines")
    if not (isinstance(runs, list) and all(isinstance(r, dict) and isinstance(r.get("engine"), str)
                                           and isinstance(r.get("result"), dict) and "pass_rate" in r["result"] for r in runs)
            and (planned is None or isinstance(planned, (dict, list)) and all(isinstance(e, str) for e in planned))):
        return None
    return runs, planned

def bench_coverage(bench, agent):
    """tool_coverage of a benchmark recomputed from its runs and planned engines (agent_bench_coverage for an agent,
    runs_coverage for a skill), never its stored field; None when they are malformed."""
    if agent:
        return agent_bench_coverage(bench) if isinstance(bench, dict) and isinstance(_meta(bench), dict) else None
    got = benchmark_runs(bench)
    return runs_coverage(got[1], got[0]) if got is not None else None

def registration_coverage(spec, ws, bench, n):
    """(lines, waived): the register summary line of each required tool, its coverage recomputed from bench's runs and
    planned engines (never the stored field) and its current waiver, and the tools whose waiver counted. Refuses a
    stored tool_coverage that is malformed or claims run where the runs do not, then every required tool that neither
    ran nor has a valid waiver."""
    cov = bench_coverage(bench, spec.get("kind") == "agent")
    if cov is None:
        raise EvalError("iteration-%d cannot be registered: its benchmark.json runs or planned engines are malformed; "
                        "re-run `report`" % n)
    if "tool_coverage" in bench:
        stored = bench["tool_coverage"]
        if not (isinstance(stored, dict) and all(t in cov and isinstance(c, dict) and isinstance(c.get("status"), str)
                                                 for t, c in stored.items())):
            raise EvalError("iteration-%d cannot be registered: the benchmark's tool_coverage is malformed; re-run "
                            "`report`" % n)
        forged = ["%s claims run, its runs give %s" % (t, cov[t]["status"]) for t, c in stored.items()
                  if c["status"] == "run" and cov[t]["status"] != "run"]
        if forged:
            raise EvalError("iteration-%d cannot be registered: the benchmark's tool_coverage does not match its runs: %s; "
                            "re-run `report`" % (n, "; ".join(forged)))
    states = coverage_states(dict(bench, tool_coverage=cov, coverage_waivers=waiver_verdicts(spec, ws)))
    missing = [GATE_MESSAGES["coverage-missing"] % c for _, c in states if c["state"] == "missing"]
    if missing:
        raise EvalError("iteration-%d cannot be registered: %s. Registration needs a graded dynamic run on every tool of "
                        "coverage_required (%s) or, for each tool that did not run, a recorded `approve --gate "
                        "coverage_waiver:<tool>` with a reason (--reason or one line of stdin); a registration approval "
                        "does not clear coverage" % (n, "; ".join(missing), ", ".join(COVERAGE_REQUIRED)))
    waived = {t: " (waived by %s, reason: %s)" % (c["principal"], c["reason"]) for t, c in states if c["state"] == "waived"}
    waived.update((t, " (waived by machine policy %s, %s, expires %s, reason: %s)" % (
        c["waiver_hash"], c["principal"], c["expires"], c["reason"])) for t, c in states if c["state"] == "machine-waived")
    return (["register coverage %s=%s%s" % (t, c["status"], waived.get(t, "")) for t, c in states if t in COVERAGE_REQUIRED],
            [(t, c) for t, c in states if c["state"] in ("waived", "machine-waived")])

def registration_record(spec, ws, shown=None):
    """The format-2 record `register` writes for the last iteration (schemas/registration.schema.json, without signed),
    or EvalError when that iteration may not be registered. Writes nothing. shown, when given, receives the per-tool
    lines of the registration_coverage check."""
    its = iterations(ws)
    if not its: raise EvalError("no iteration to register in %s" % ws)
    n = its[-1]
    gate = rjson(os.path.join(ws, "iteration-%d" % n, "gate.json"))
    bench = rjson(os.path.join(ws, "iteration-%d" % n, "benchmark.json"))
    if gate is None or bench is None: raise EvalError("iteration-%d has no report; run `report` first" % n)
    lines, waived = registration_coverage(spec, ws, bench, n)
    if bench.get("gate") != gate: raise EvalError("iteration-%d gate.json does not match benchmark.json; re-run `report`" % n)
    m = bench.get("metadata") or {}
    now_skill, now_evals = skill_tree_hash(spec["skill_dir"]), gate_hash("eval_set", spec, ws)
    agent = spec.get("kind") == "agent"
    if agent:
        tested = sha256_labeled(Agent(spec["skill_dir"]).files(list(m.get("engines") or {})))
        if not m.get("agent_sha256") or m["agent_sha256"] != tested:
            raise EvalError("iteration-%d tested other agent files (AGENT.md, scenarios or wrappers) than the ones on "
                            "disk; re-run the eval before registering" % n)
        why = pin_binding_problem(spec["skill_dir"], m.get("pins", []))
        if why:
            raise EvalError("iteration-%d: %s; re-run the eval before registering" % (n, why))
        unnamed = [p for p in agent_parity_problems(spec["skill_dir"]) if p not in gate["problems"]]
        if unnamed:
            raise EvalError("iteration-%d gate does not name the agent's parity FAIL row(s) (%s); re-run `agent report`, "
                            "then `approve --gate registration`" % (n, "; ".join(unnamed)))
    elif not m.get("skill_sha256") or m["skill_sha256"] != now_skill:
        raise EvalError("iteration-%d tested skill %s but the skill folder now hashes to %s; re-run the eval before "
                        "registering" % (n, (m.get("skill_sha256") or "<unrecorded>")[:12], now_skill[:12]))
    elif not m.get("eval_set_sha256") or m["eval_set_sha256"] != now_evals:
        raise EvalError("iteration-%d used a different eval set than the one on disk; re-run the eval" % n)
    approval, relied = "auto", {}
    if gate["needs_approval"] or agent and gate["problems"]:
        approval, v = approval_record("registration", spec, ws)
        if v.status != "approved":
            raise EvalError("iteration-%d has problems %s; registration needs `approve --gate registration` (%s)"
                            % (n, gate["problems"], "missing" if v.status == "missing" else "%s: %s" % (v.status, v.reason)))
        relied["registration"] = approval
    machine = None
    for tool, c in waived:
        if c["state"] == "machine-waived":
            mw = machine_waiver()
            if mw["status"] != "approved" or mw["sha256"] != c["waiver_hash"]:
                raise EvalError("the machine waiver changed while registering")
            machine = {"sha256": mw["sha256"], "record": mw["record"]}
            continue
        rec, v = approval_record(approvals.WAIVER_PREFIX + tool, spec, ws)
        if v.status != "approved": raise EvalError("the waiver %s%s changed while registering" % (approvals.WAIVER_PREFIX, tool))
        relied[approvals.WAIVER_PREFIX + tool] = rec
    now_rules = ruleset_sha256()
    if m.get("ruleset_sha256") != now_rules:
        raise EvalError("iteration-%d ran under ruleset %s but the rules now hash to %s; re-run the eval before "
                        "registering" % (n, (m.get("ruleset_sha256") or "<unrecorded>")[:12], now_rules[:12]))
    main, field = ("AGENT.md", "version") if agent else ("SKILL.md", "metadata.version")
    version = registry.version_of(unit_spec(spec["skill_dir"])[1])
    if registry.parse_semver(version) is None:
        raise EvalError("%s %s; set %s to a SemVer 2.0.0 version such as 1.0.0 (MAJOR.MINOR.PATCH, no leading zeros, no "
                        "v) before registering" % (main, "has no version" if version in ("", None) else
                                                   "version %r is not SemVer 2.0.0" % (version,), field))
    rec = {"registration_format": registry.REGISTRATION_FORMAT, "kind": "agent" if agent else "skill",
           "skill_name": spec["skill_name"], "version": version, "skill_path": registry.posix_rel(spec["skill_dir"], ws),
           "iteration": n, "benchmark_sha256": gate_hash("iteration-%d" % n, spec, ws), "skill_sha256": now_skill,
           "eval_set_sha256": now_evals,
           "skill_md_sha256": sha256_files(os.path.join(spec["skill_dir"], main)), "ruleset_sha256": now_rules,
           "approvals": relied, "registered_at": now_iso(), "approval": approval}
    if machine is not None: rec["machine_waiver"] = machine
    errors = registration_schema_errors(rec)
    if errors:
        raise EvalError("the record does not match %s (%s at /%s); nothing was signed" % (
            registry.SCHEMA_NAME, errors[0][2], "/".join(map(str, errors[0][0]))))
    if shown is not None: shown.extend(lines)
    return rec

REGISTRATION_SIGNED = ("skill_name", "iteration", "benchmark_sha256", "skill_sha256", "eval_set_sha256", "skill_md_sha256",
                       "ruleset_sha256", "registration_format", "kind", "version", "skill_path", "approvals", "approval",
                       "registered_at")

def registration_hash(rec):
    """What a register signature covers: every field of the registration record but the signature itself, the
    machine_waiver a registration relied on included (a record without one keeps its old bytes)."""
    if not all(k in rec for k in REGISTRATION_SIGNED): return None
    keys = REGISTRATION_SIGNED + (("machine_waiver",) if "machine_waiver" in rec else ())
    return hashlib.sha256(json.dumps({k: rec[k] for k in keys}, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()

def registration_verdict(spec, ws):
    rec = rjson(os.path.join(ws, "registration.json"))
    if not isinstance(rec, dict):
        return APPROVER["it"].verdict(None, "register", "registration", spec["skill_name"], None, None)
    return APPROVER["it"].verdict(rec.get("signed"), "register", "registration", spec["skill_name"], registration_hash(rec),
                                  ruleset_sha256())

def register(spec, ws):
    return _register_with(lambda: (spec, ws))

def _register_with(load):
    st = {}
    def prepare():
        spec, ws = load()
        shown = []
        rec = registration_record(spec, ws, shown)
        try: old = rjson(os.path.join(ws, "registration.json"))
        except (ValueError, RecursionError): old = None
        problem = registry.bump_problem(rec, old)
        if problem: raise EvalError("%s; nothing was signed" % problem)
        st.update(rec=rec, ws=ws)
        return approvals.Prepared(spec["skill_name"], registration_hash(rec), rec["ruleset_sha256"],
                                  ["register %s=%s" % (k, rec[k]) for k in REGISTRATION_SIGNED
                                   if k not in ("approvals", "approval")] +
                                  ["register approvals: %s" % (", ".join(sorted(rec["approvals"])) or "none")] +
                                  (["register machine_waiver: %s" % rec["machine_waiver"]["sha256"]]
                                   if "machine_waiver" in rec else []) +
                                  ["register approval: %s" % ("auto (clean gate)" if rec["approval"] == "auto" else
                                                              "signed registration gate")] + shown)
    grant = APPROVER["it"].sign("register", "registration", prepare)
    rec = dict(st["rec"], signed=grant.record)
    wjson(os.path.join(st["ws"], "registration.json"), rec)
    return rec

# ---------- registry and hub (unit B5) ----------
HUB_NO_ITERATION = "not-run: no eval iteration"

def unit_spec(unit_dir):
    """(spec, frontmatter) of a skill or agent folder without reading its eval set: the SKILL.md (AGENT.md) name, else
    the folder name; an agent's spec has kind agent."""
    import eva
    agent = eva.detect_kind(unit_dir) == "agent"
    text, err = eva.read_text(os.path.join(unit_dir, "AGENT.md" if agent else "SKILL.md"))
    meta = (eva.parse_frontmatter(text)[0] if not err else None) or {}
    spec = {"skill_name": registry.unit_name(unit_dir, meta), "skill_dir": unit_dir}
    return (dict(spec, kind="agent") if agent else spec), meta

RECORD_SLOTS = dict([(("signed",), ("register", "registration")), (("approval",), ("approve", "registration")),
                     (("approvals", "registration"), ("approve", "registration")), (("machine_waiver", "record"), None)]
                    + [(("approvals", g), ("approve", g)) for g in approvals.WAIVER_GATES])

def registration_schema_errors(rec):
    """The registration schema's errors. The schema is part of the ruleset hash and names only records signed before
    unit N1, so an error inside a RECORD_SLOTS slot is dropped when the slot holds a well-formed recorded record of the
    slot's kind and gate (engine/approvals.py recorded_problem, or machine_waiver_problem for the machine waiver); the
    verdicts of classify still check every such record."""
    from engine import schema
    errors = schema.compile_schema(schema.load(os.path.join(RULESET_ROOT, registry.SCHEMA_NAME))).validate(rec)
    def recorded(slot, want):
        value = rec
        for k in slot: value = value.get(k) if isinstance(value, dict) else None
        if not (isinstance(value, dict) and value.get("method") == approvals.RECORDED): return False
        if want is None: return approvals.machine_waiver_problem(value, MACHINE_WAIVABLE) is None
        return approvals.recorded_problem(value) is None and (value["kind"], value["gate"]) == want
    slots = [s for s, want in RECORD_SLOTS.items() if recorded(s, want)]
    return [e for e in errors if not any(tuple(e[0][:len(s)]) == s for s in slots)]

def registration_state(unit_dir, ws=None):
    """(state, reason, record) of the unit's registration record, verified now (engine/registry.classify, B5 D4): the
    register signature, the hashes it binds and every approval it relied on are checked against the files as they are.
    Reads records and hashes only. ws: the workspace (default: the skill's, or the agent's by the rule of the agent
    commands, registry.agent_workspace)."""
    import eva
    kind = eva.detect_kind(unit_dir)
    spec, meta = unit_spec(unit_dir)
    ws = ws or (registry.unit_workspace(unit_dir, meta) if kind == "skill" else
                registry.agent_workspace(unit_dir) if kind == "agent" else None)
    rules = []
    def ruleset():
        if not rules: rules.append(ruleset_sha256())
        return rules[0]
    def signature(rec):
        return APPROVER["it"].verdict(rec["signed"], "register", "registration", spec["skill_name"], registration_hash(rec),
                                      ruleset())
    def current(rec):
        yield "skill_sha256", skill_tree_hash(unit_dir)
        yield "eval_set_sha256", gate_hash("eval_set", spec, ws)
        yield "skill_md_sha256", sha256_files(os.path.join(unit_dir, "AGENT.md" if kind == "agent" else "SKILL.md"))
        yield "benchmark_sha256", gate_hash("iteration-%d" % rec["iteration"], spec, ws)
        yield "version", registry.version_of(meta)
    def approval(gate, copy):
        return APPROVER["it"].verdict(copy, "approve", gate, spec["skill_name"], gate_hash(gate, spec, ws), ruleset())
    def pinned(rec):
        bench = rjson(os.path.join(ws, "iteration-%d" % rec["iteration"], "benchmark.json"), {})
        recorded = (bench.get("metadata") or {}).get("pins", []) if isinstance(bench, dict) else []
        if not meta.get("skills") and not recorded: return None
        return pin_binding_problem(unit_dir, recorded)
    return registry.classify(kind, ws, unit_dir, registration_schema_errors, signature, current, approval,
                             pinned if kind == "agent" else None)

def view_coverage(row):
    """({tool: status}, iteration) for the hub: recomputed with bench_coverage from the runs and planned engines of one
    benchmark.json, never its stored tool_coverage. The benchmark is the recorded iteration's while a registered or stale
    record names one that exists, else the last iteration's."""
    import eva
    ws, rec = row["workspace"], row["record"]
    bench_path = lambda k: os.path.join(ws, "iteration-%d" % k, "benchmark.json")
    n = rec.get("iteration") if isinstance(rec, dict) and row["registration"] in ("registered", "stale") else None
    if not (type(n) is int and os.path.isfile(bench_path(n))):
        its = iterations(ws) if ws else []
        n = its[-1] if its else None
    if n is None: return {t: HUB_NO_ITERATION for t in eva.TOOLS}, None
    try: cov = bench_coverage(rjson(bench_path(n)), row["kind"] == "agent")
    except (OSError, ValueError, RecursionError): cov = None
    if cov is None: return {t: "not-run: iteration-%d has no readable benchmark.json" % n for t in eva.TOOLS}, n
    return {t: c["status"] for t, c in cov.items()}, n

def registration_findings(ws, rec):
    """The finding ids the registration approval of a verified agent record named: the problem ids of the gate inside
    the benchmark.json whose sha256 the record binds; [] when the record relied on no registration approval."""
    if "registration" not in rec["approvals"]: return []
    bench = rjson(os.path.join(ws, "iteration-%d" % rec["iteration"], "benchmark.json"))
    gate = bench.get("gate") if isinstance(bench, dict) else None
    problems = gate.get("problems") if isinstance(gate, dict) else None
    return [p.split(": ", 1)[0] for p in problems if isinstance(p, str)] if isinstance(problems, list) else []

def unit_view(root, row):
    """What the hub shows for one eva.build_index row (B5 D8): the row's check, English, parity and registration results,
    the coverage per tool, the scripts flag, which agent wrappers exist, the hashes, and for a registered agent the
    findings its registration approval named."""
    import eva
    u = os.path.join(root, row["path"])
    rec = row["record"] if isinstance(row["record"], dict) else {}
    registered = row["registration"] == "registered"
    coverage, n = view_coverage(row)
    hashes = {"tree_sha256": skill_tree_hash(u)}
    hashes.update((k, rec[k]) for k in registry.RECORD_HASHES
                  if isinstance(rec.get(k), str) and approvals.SHA_RE.fullmatch(rec[k]))
    label = (("registered %s by %s" % (rec["version"], approvals.decided_by(rec["signed"]))) if registered else
             registry.label(row["registration"], row["registration_reason"], rec))
    files = eva.unit_tree(u).files
    return {"kind": row["kind"], "name": row["name"], "description": row["description"],
            "version": rec["version"] if registered else row["version"], "path": row["path"].replace(os.sep, "/"),
            "registration": row["registration"], "registration_label": label,
            "verdict": row["verdict"], "score": row["score"], "english": row["english"], "parity": row["parity"],
            "coverage": coverage, "coverage_iteration": n, "hashes": hashes,
            "scripts": any("scripts" in f.split("/")[:-1] or f.endswith((".py", ".sh", ".js")) for f in files),
            "wrappers": {t: os.path.isfile(os.path.join(u, *eva._wrapper_rel(t, row["name"]).split("/")))
                         for t in eva.TOOLS} if row["kind"] == "agent" else {},
            "approvals": [{"gate": g, "principal": approvals.decided_by(a), "reason": a.get("reason")}
                          for g, a in sorted(rec["approvals"].items())] if registered else [],
            "machine_waiver": rec.get("machine_waiver") if registered else None,
            "findings": registration_findings(row["workspace"], rec) if registered and row["kind"] == "agent" else None}

def hub_build(root, out, templates=None):
    """`hub build`: (exit code, lines). The site is rendered in memory from eva.build_index (the same rows and registry
    document as `eva.py index`), scanned (engine/hub.scan), and written into out only when out is absent or empty and
    the scan found nothing. Exit 2: out is not usable or two units share a page; exit 1: the scan refused the site."""
    import eva
    from engine import hub
    problem = hub.out_problem(out)
    if problem: return 2, [problem]
    try: rows, _ = eva.build_index(root)
    except eva.rule_runner.BundleError as ex: return 2, ["bundle error: %s" % ex]
    try:
        files = hub.render([unit_view(root, r) for r in rows], registry.document_text(registry.document(root, rows)),
                           eva.TOOL_LABELS, templates)
    except hub.HubError as ex:
        return 2, [str(ex)]
    local = {os.path.abspath(root), os.path.realpath(root), os.path.expanduser("~")}
    findings = hub.scan(files, local)
    if findings: return 1, ["hub build: the output scan refused the site, nothing was written:"] + findings
    try: hub.write(out, files)
    except hub.HubError as ex: return 2, [str(ex)]
    registered = sum(r["registration"] == "registered" for r in rows)
    return 0, ["hub build: %d units -> %s (index.html, registry.json, style.css, %d unit pages; %d registered)"
               % (len(rows), out, len(rows), registered)]

class ClaudeBuilder:
    """The only builder `loop --live` accepts: one metered `claude -p` call per iteration through live_exec, run in the
    skill folder with file tools only (no Bash, so it cannot start other paid CLIs), a --max-budget-usd cap from the
    shared Budget, and its cost charged to that Budget. The iteration's comparison.md is inlined into the prompt. Its
    PreToolUse gate (claude_bash_gate.py edit-allowlist mode) denies every write outside engine/writable.py's set."""
    TOOLS = "Read,Edit,Write,Glob,Grep"
    MODE = writable.ALLOWLIST_MODE
    def __init__(self, instructions, model, budget, prices=None, exec_fn=None, timeout=900, skill_name=None):
        self.instructions, self.model, self.budget, self.prices = instructions, model, budget, prices or {}
        self.exec_fn, self.timeout, self.calls = exec_fn or live_exec, timeout, 0
        self.skill_name = skill_name  # the skill being built: never set off by skillOverrides
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "api_equivalent_usd": 0.0}
        self.harness_writes = []  # append_cost records: loop does not count them as builder writes
    def command(self, prompt, cap, skill_dir=""):
        """skill_dir: the folder the gate lets the builder edit; empty makes the gate deny every edit."""
        cmd = ["claude", "-p", prompt, "--model", self.model, "--output-format", "json", "--no-session-persistence",
               "--permission-mode", "acceptEdits", "--tools", self.TOOLS, "--disable-slash-commands"]
        cmd += claude_isolation_args(self.skill_name, edit_root=os.path.realpath(skill_dir) if skill_dir else "",
                                     edit_mode=self.MODE)
        return cmd + (["--max-budget-usd", "%.2f" % cap] if cap is not None else [])
    def prompt(self, report):
        import eva
        return ("%s\n\nYou are improving the agent skill in the current directory (SKILL.md and its files). %s %s\n\n"
                "Eval report of the last iteration:\n%s\n" % (self.instructions.strip(), eva.ENGLISH_BUILDER_RULE,
                                                               eva.PARITY_BUILDER_RULE, report))
    def _record(self, it_dir, tin, tout, cost, ticket=None, outcome="ok", reported=None, source="unknown"):
        if self.budget is not None: self.budget.charge(cost)
        spend_settle(ticket, self.budget, outcome, reported, tin, tout, price_info("builder", self.model, self.prices),
                     cost, source)
        self.usage["calls"] += 1; self.usage["input_tokens"] += tin
        self.usage["output_tokens"] += tout; self.usage["api_equivalent_usd"] += cost
        self.harness_writes.append(append_cost(it_dir, "builder", {"calls": 1, "input_tokens": tin, "output_tokens": tout,
                                                                   "api_equivalent_usd": cost}))
    def __call__(self, it_dir, skill_dir):
        cap, stop = None, None
        if self.budget is not None and self.budget.finite():
            cap = self.budget.claude_cap()
            if cap < 0.01:
                stop = "budget exhausted ($%.2f of $%.2f) before builder call" % (self.budget.spent, self.budget.limit)
        bound = call_bound("builder", self.model, "builder", self.prices, DEFAULT_TOKENS) or 0.0
        fallback = cap if cap is not None else bound  # no usage: the cap
        ticket, stop = spend_admit(self.budget, "builder", "claude", self.model, iteration_of(it_dir), cap, fallback, True,
                                   stop)
        if stop: raise EvalError(stop)
        report = ""
        rp = os.path.join(it_dir, "comparison.md")
        if os.path.isfile(rp):
            with open(rp, encoding="utf-8") as f: report = f.read()[:TRANSCRIPT_CHARS]
        prompt = self.prompt(report)
        unknown = lambda rep: api_equivalent(0, 0, rep, None, fallback)[1]
        if ticket is not None: ticket.launch()
        try:
            rc, out, err = launch_claude(self.exec_fn, self.command(prompt, cap, skill_dir), skill_dir, self.timeout,
                                         skill_name=self.skill_name, edit_allowlist=True, edit_mode=self.MODE)
        except (EvalError, LiveDisabled):
            if ticket is not None: ticket.not_launched(self.budget.spent)
            raise  # refused before launch: nothing spent
        except Exception as ex:
            self.calls += 1; self._record(it_dir, 0, 0, fallback, ticket, "launch_failed", None, unknown(None))
            raise EvalError("builder call failed after launch (%s); charged $%.2f and stopped" % (type(ex).__name__, fallback))
        self.calls += 1
        try: data = json.loads(out or "")
        except (ValueError, TypeError): data = None
        if not isinstance(data, dict):  # paid call, unreadable output: charge max(reported, cap) and stop
            charged = max(fallback, reported_cost(out) or 0.0)
            self._record(it_dir, 0, 0, charged, ticket, "unparsed", reported_cost(out), unknown(reported_cost(out)))
            raise EvalError("builder output is not a JSON object (exit %s); charged $%.2f and stopped" % (rc, charged))
        if data.get("type") != "result":  # a JSON object but not the CLI result (e.g. {}): same charge and stop
            charged = max(fallback, reported_cost(out) or 0.0)
            self._record(it_dir, 0, 0, charged, ticket, "unparsed", reported_cost(out), unknown(reported_cost(out)))
            raise EvalError("builder output is not a result object (type %r, exit %s); charged $%.2f and stopped"
                            % (data.get("type"), rc, charged))
        if data.get("is_error"):  # the CLI reported a failed call (even with exit 0): same charge and stop
            charged = max(fallback, reported_cost(out) or 0.0)
            self._record(it_dir, 0, 0, charged, ticket, "error", reported_cost(out), unknown(reported_cost(out)))
            raise EvalError("builder result is an error (subtype %r, exit %s); charged $%.2f and stopped"
                            % (data.get("subtype"), rc, charged))
        try:
            tin, tout = claude_usage(data)
        except Exception as ex:  # paid call, unreadable usage: charge max(reported, cap) and stop
            charged, source = api_equivalent(0, 0, data.get("total_cost_usd"), None, fallback)
            self._record(it_dir, 0, 0, charged, ticket, "unparsed", data.get("total_cost_usd"), source)
            raise EvalError("builder usage could not be parsed after the call (%s); charged $%.2f and stopped"
                            % (type(ex).__name__, charged))
        cost, source = api_equivalent(tin, tout, data.get("total_cost_usd"), price_for("builder", self.model, self.prices),
                                      fallback)
        self._record(it_dir, tin, tout, cost, ticket, "ok" if rc == 0 else "error", data.get("total_cost_usd"), source)
        return rc

def loop(spec, ws, engines, grader, builder, max_iterations=MAX_AUTO_ITERATIONS, runs=DEFAULT_RUNS, baseline="none",
         old_skill=None, budget=None, prices=None, golden=None, seed=None, log=print, tokens=None, precheck=None,
         run_root=None):
    """run -> grade -> (calibrate) -> report; stop when the gate is clean, or with "needs-coverage" when only
    coverage-missing problems remain (no builder can fix them); otherwise call builder(iteration_dir,
    skill_dir) and reload the skill. precheck(spec, iterations_left) -> [problems] runs before EVERY iteration (the
    CLI passes the full --live gate set: validation, approvals incl. fixture hashes, preflight, cost vs remaining
    budget); any problem stops the loop. Every builder call is bracketed by engine/writable.py fingerprints of the skill
    folder and ws: any change outside the writable set that the harness did not write itself stops the loop with status
    "builder-wrote-outside" (the problems name every changed path) before anything else runs, also when the builder
    raised; next, a builder call that moved ruleset_sha256() stops it with status "rules-changed". The post-builder
    gates run in the order of POST_BUILDER_GATES, which must equal policy/gates.json, else the loop refuses to start.
    After every builder call the skill folder must pass the static English-only
    check (eva.english_violations), else the loop stops with status "builder-non-english", and then the multi-tool
    parity check (eva.parity_violations), else with status "builder-parity-missing", before anything more is
    spent. Run outputs are never checked. At most MAX_AUTO_ITERATIONS automatic iterations."""
    if max_iterations > MAX_AUTO_ITERATIONS:
        raise EvalError("max %d automatic iterations, got %d" % (MAX_AUTO_ITERATIONS, max_iterations))
    declared = gate_declaration_problem()
    if declared: raise EvalError(declared)
    n = None
    for i in range(max_iterations):
        if precheck is not None:
            probs = precheck(spec, max_iterations - i)
            if probs: return {"status": "gates-failed", "iteration": n, "problems": probs}
        n = run_iteration(spec, ws, engines, runs, baseline, old_skill, budget, prices, log=log, tokens=tokens,
                          run_root=run_root, builder_model=getattr(builder, "model", None))
        stopped = (rjson(os.path.join(ws, "iteration-%d" % n, "plan.json"), {}) or {}).get("stopped")
        if stopped: return {"status": "run-stopped", "iteration": n, "error": stopped}  # no grading, no builder
        grade_iteration(spec, ws, n, grader, seed, log=log)
        if golden: wjson(os.path.join(ws, "iteration-%d" % n, "calibration.json"),
                         calibrate_metered(golden, grader, os.path.join(ws, "iteration-%d" % n)))
        halt = spend_halt(budget)
        if halt: return {"status": "run-stopped", "iteration": n, "error": halt}
        _, gate = report_iteration(spec, ws, n)
        log("iteration-%d gate: %s" % (n, gate["problems"] or "clean"))
        if not gate["problems"]: return {"status": "clean", "iteration": n}
        if gate.get("human_pending_only"): return {"status": "needs-human-review", "iteration": n}
        if gate.get("coverage_only"): return {"status": "needs-coverage", "iteration": n}
        if budget is not None and budget.remaining() <= 0: return {"status": "budget-exhausted", "iteration": n}
        if i == max_iterations - 1 or builder is None: break
        harness = getattr(builder, "harness_writes", [])
        st = {"spec": spec, "ws": ws, "n": n, "log": log, "harness": harness, "seen": len(harness),
              "before": writable.fingerprint(spec["skill_dir"], ws), "rules": ruleset_sha256(), "rc": None, "error": None}
        crash, led = None, getattr(budget, "ledger", None)
        if led is not None: led.bracket = harness  # the ledger's own appends during the call are harness writes
        try:
            st["rc"] = builder(os.path.join(ws, "iteration-%d" % n), spec["skill_dir"])
        except EvalError as ex:
            st["error"] = str(ex)
        except Exception as ex:
            crash, st["error"] = ex, "builder raised %s: %s" % (type(ex).__name__, ex)
        finally:
            if led is not None: led.bracket = None
        st["extra"] = {} if st["error"] is None else {"error": st["error"]}
        if st["rc"] is not None: st["extra"]["returncode"] = st["rc"]
        for gid, gate in POST_BUILDER_GATES:
            if crash is not None and gid not in CRASH_GATES: raise crash
            stop = gate(st)
            if stop: return stop
        spec = st["spec"]
    return {"status": "needs-approval", "iteration": n}

def _stop(st, status, problems=None):
    out = {"status": status, "iteration": st["n"]}
    if problems is not None: out["problems"] = problems
    return dict(out, **st["extra"])

def _after_outside(st):
    skill, ws = st["spec"]["skill_dir"], st["ws"]
    outside = writable.changed(st["before"], writable.fingerprint(skill, ws),
                               [(writable.name(skill, p), r, w) for p, r, w in st["harness"][st["seen"]:]])
    if outside:
        st["log"]("builder-wrote-outside " + writable.shown(outside[0]))
        return _stop(st, "builder-wrote-outside", outside)

def _after_rules(st):
    try:
        now = ruleset_sha256()
        if now == st["rules"]: return None
        now = now[:12]
    except EvalError as ex:  # a bundle the builder left unhashable counts as moved
        now = str(ex).splitlines()[0]
    return _stop(st, "rules-changed", ["ruleset_sha256 %s -> %s" % (st["rules"][:12], now)])

def _after_english(st):
    import eva
    found = eva.english_violations(st["spec"]["skill_dir"])
    if found: return _stop(st, "builder-non-english", found)

def _after_parity(st):
    import eva
    found = eva.parity_violations(st["spec"]["skill_dir"])
    if found: return _stop(st, "builder-parity-missing", found)

def _after_review(st):
    found = rule_review_problems(st["spec"], st["ws"])
    if found: return _stop(st, "builder-review-required", found)

def _after_failed(st):
    if st["error"] is not None or st["rc"] != 0: return _stop(st, "builder-failed")

def _after_eval_set(st):
    spec, problems = load_eval_set(st["spec"]["skill_dir"])
    if problems: return {"status": "eval-set-invalid", "iteration": st["n"], "problems": problems}
    st["spec"] = spec

# The order policy/gates.json declares (post_builder); gate_declaration_problem() compares the two. A gate returns the
# loop's stop result or None. CRASH_GATES also run when the builder raised; the crash is re-raised after them.
POST_BUILDER_GATES = (("builder-wrote-outside", _after_outside), ("rules-changed", _after_rules),
                      ("builder-non-english", _after_english), ("builder-parity-missing", _after_parity),
                      ("builder-review-required", _after_review), ("builder-failed", _after_failed),
                      ("eval-set-invalid", _after_eval_set))
CRASH_GATES = ("builder-wrote-outside", "rules-changed")

def builder_check(skill, workspace, compare=None):
    """`builder-check`: prints the builder's writable set and the fingerprint of every entry outside it (one
    `outside [name, value]` line each, then the aggregate). With compare (a saved earlier output) it also prints every
    changed name and returns 1 when anything changed. Reads only: no write, no process, no approval."""
    skill, ws = os.path.realpath(skill), os.path.realpath(workspace)
    for d in (skill, ws):
        if not os.path.isdir(d): raise EvalError("%s is not a folder" % d)
    fp = writable.fingerprint(skill, ws)
    print("\n".join(["writable " + writable.SHOWN, "skill " + skill, "workspace " + ws] +
                    ["outside " + json.dumps([k, fp[k]]) for k in sorted(fp)] +
                    ["fingerprint %s %d entries" % (writable.digest(fp), len(fp))]))
    if compare is None: return 0
    try:
        with open(compare, encoding="utf-8") as f: saved = f.read().splitlines()
        old = dict(json.loads(l[len("outside "):]) for l in saved if l.startswith("outside "))
    except (OSError, ValueError, TypeError) as ex:
        raise EvalError("cannot read %s: %s" % (compare, ex))
    if not any(l.startswith("fingerprint ") for l in saved):
        raise EvalError("%s holds no builder-check output" % compare)
    diff = writable.changed(old, fp)
    print("\n".join(["changed " + writable.shown(n) for n in diff] + ["compare: %d changed" % len(diff)]))
    return 1 if diff else 0

# ---------- agent evaluation (unit B3; data shapes and logic in engine/agent_eval.py) ----------
class _AgentStream(object):
    """Keeps the raw stdout of the last call, so the stream is saved even when parse() raises."""
    def parse(self, stdout):
        self.last_stream = stdout
        return super(_AgentStream, self).parse(stdout)

class AgentClaudeEngine(_AgentStream, ClaudeCodeEngine):
    """`claude -p --agent <name>` (agent_eval.LAUNCH) with the executor's isolation args and per-call cap; the agent is
    the generated wrapper at .claude/agents/<name>.md of the sandbox. Slash commands (skills) stay off unless the agent
    pins skills (with_skill: the pinned skills are installed at project scope, K2)."""
    def command(self, prompt, with_skill, budget_usd=None, skill_name=None):
        from engine import agent_eval
        extra = claude_isolation_args()
        if self.model: extra += ["--model", self.model]
        if self.effort: extra += ["--effort", self.effort]
        if budget_usd is not None: extra += ["--max-budget-usd", "%.2f" % budget_usd]
        off = [] if with_skill else ["--disable-slash-commands"]
        return agent_eval.launch_argv("claude", skill_name, prompt, extra + off + self.extra_args)

class AgentCodexEngine(_AgentStream, CodexEngine):
    """`codex exec --json` with the agent asked for by name (agent_eval.LAUNCH, UNVERIFIED house choice); the agent is
    the generated wrapper at .codex/agents/<name>.toml of the sandbox."""
    def command(self, prompt, with_skill, budget_usd=None, skill_name=None):
        from engine import agent_eval
        extra = ["-m", self.model] if self.model else []
        if self.effort: extra += ["-c", CODEX_EFFORT_CONFIG % self.effort]
        return agent_eval.launch_argv("codex", skill_name, prompt, extra + self.extra_args)

AGENT_ENGINES = {"claude": AgentClaudeEngine, "codex": AgentCodexEngine}
AGENT_CONTROL_DIRS = (".claude/agents", ".codex/agents", ".claude/skills", ".agents/skills")

class Agent(object):
    """An agent folder as the agent commands read it: realpath, name, the AGENT.md v2 frontmatter (None when invalid)
    and the valid scenarios (agent_eval.scenarios)."""
    def __init__(self, path):
        import eva
        from engine import agent_eval
        from engine.primitives import transcript
        self.dir = os.path.realpath(path)
        if not os.path.isfile(os.path.join(self.dir, "AGENT.md")):
            raise EvalError("%s holds no AGENT.md" % path)
        info = transcript.read_agent(self.dir)
        self.meta = info.meta if info.valid else None
        self.name = registry.agent_name(self.dir, info)
        self.scenarios = agent_eval.scenarios(eva.load_scenarios(self.dir))
    def files(self, tools):
        """(label, path) of AGENT.md, every scenarios/*.json, every fixture file and the wrappers of tools: what a live
        start binds."""
        from engine import agent_eval
        rels = ["AGENT.md"] + [label for label, _ in agent_scenario_files(self.dir) + agent_fixture_files(self.dir)]
        rels += [agent_eval.wrapper_rel(t, self.name) for t in tools]
        return [(r, os.path.join(self.dir, *r.split("/"))) for r in rels]
    def wrapper(self, tool):
        from engine import agent_eval
        return ensure_inside(self.dir, os.path.join(self.dir, *agent_eval.wrapper_rel(tool, self.name).split("/")))
    @property
    def pins(self):
        from engine import agent_eval
        return agent_eval.pins_of(self.meta)

FIXTURE_CONTROL_DIRS = (".claude", ".agents", ".codex")

def control_name(name):
    """True for a name a case-insensitive filesystem (macOS) resolves to a sandbox install folder."""
    return name.casefold() in FIXTURE_CONTROL_DIRS

def skills_root(agent_dir):
    """The repository root a pin resolves in: the parent of the folder that holds the agent folder (realpath), so
    agents/<agent>/ pins <root>/skills/<name>/."""
    return os.path.dirname(os.path.dirname(os.path.realpath(agent_dir)))

def pin_states(agent):
    """One dict per AGENT.md skills entry, in order: pin, name, version, skill_dir, ok, reason, registration_sha256.
    ok only when <root>/skills/<name>/ holds SKILL.md named <name>, its registration verifies now (registration_state
    reads registered), the record is of kind skill with exactly that name and version, the skill is user-invocable (the
    load check reads the init skills list), and its workspace marks that version not deprecated; registration_sha256
    is the sha256 of its registration.json bytes."""
    from engine import agent_eval, lifecycle
    parsed = [agent_eval.parse_pin(p) for p in agent.pins]
    names = [p[0] for p in parsed if p]
    out = []
    for pin, got in zip(agent.pins, parsed):
        st = {"pin": pin, "name": None, "version": None, "skill_dir": None, "ok": False, "reason": "",
              "registration_sha256": None}
        out.append(st)
        if got is None:
            st["reason"] = "not <name>@<semver>"; continue
        name, version = got
        d = os.path.join(skills_root(agent.dir), "skills", name)
        st.update(name=name, version=version, skill_dir=d)
        if names.count(name) > 1:
            st["reason"] = "skill %s is pinned twice" % name; continue
        if not os.path.isfile(os.path.join(d, "SKILL.md")):
            st["reason"] = ("%s is not a skill folder (no SKILL.md)" % d if os.path.isdir(d) else
                            "unregistered: no skill folder %s" % d); continue
        spec, meta = unit_spec(d)
        state, why, rec = registration_state(d)
        if spec["skill_name"] != name:
            st["reason"] = "the SKILL.md name is %r, not %r" % (spec["skill_name"], name)
        elif state != "registered":
            st["reason"] = "%s: %s" % (state, why) if why else state
        elif rec.get("kind") != "skill" or rec.get("skill_name") != name:
            st["reason"] = "the record is of kind %r for %r, not a skill named %r" % (rec.get("kind"), rec.get("skill_name"), name)
        elif rec.get("version") != version:
            st["reason"] = "registered version is %s, the pin asks %s" % (rec.get("version"), version)
        elif _not_user_invocable(d):
            st["reason"] = ("user-invocable: false (or an unreadable SKILL.md): the init event never lists such a skill, "
                            "so the run could not verify that it loaded")
        else:
            ws = registry.unit_workspace(d, meta)
            try: deprecated = lifecycle.deprecated_reason(rjson(os.path.join(ws, "deprecations.json")), version)
            except ValueError: deprecated = "deprecations.json is not readable JSON"
            if deprecated: st["reason"] = "deprecated: %s" % deprecated
            else: st.update(ok=True, registration_sha256=file_sha256(os.path.join(ws, "registration.json")))
    return out

def pin_problems(agent):
    return ["pin %s does not verify: %s" % (s["pin"], s["reason"]) for s in pin_states(agent) if not s["ok"]]

def pin_record(states):
    return [{"pin": s["pin"], "name": s["name"], "version": s["version"], "registration_sha256": s["registration_sha256"]}
            for s in states]

def pin_binding_problem(agent_dir, recorded):
    """Why the agent's pins no longer verify at the registration hashes an iteration recorded (recorded: plan.json
    pins), or None. An agent without pins and without recorded pins has none."""
    now = pin_states(Agent(agent_dir))
    bad = [s for s in now if not s["ok"]]
    if bad: return "pin %s does not verify: %s" % (bad[0]["pin"], bad[0]["reason"])
    want = [(p.get("pin"), p.get("registration_sha256")) for p in recorded if isinstance(p, dict)] \
        if isinstance(recorded, list) else None
    got = [(s["pin"], s["registration_sha256"]) for s in now]
    if got == want: return None
    changed = [p for p in got if p not in (want or [])] or (want or [])
    return "pin %s changed since the iteration recorded it" % ", ".join(sorted({str(p) for p, _ in changed}))

def agent_fixture_files(agent_dir):
    """[(label, path)] of every file under the agent's fixtures/ folder, labeled fixtures/<slug>/<rel>, sorted: the
    files the eval_set gate and the live record bind besides the scenarios ([] without the folder)."""
    top = os.path.join(agent_dir, "fixtures")
    out = []
    for dp, dn, fn in os.walk(top):
        out += [("fixtures/" + os.path.relpath(os.path.join(dp, n), top).replace(os.sep, "/"), os.path.join(dp, n))
                for n in fn]
    return sorted(out)

def fixture_files(agent_dir, slug):
    """([(rel, path)], problems) of the fixture folder fixtures/<slug>/ of an agent: every regular file, rel relative to
    that folder. A missing folder, a symlink anywhere in it, a special file, a path whose realpath leaves the agent
    folder and an entry at any depth named like a sandbox install folder in any letter case (.claude, .agents, .codex;
    a tool may discover skills in a nested one) are problems."""
    base = os.path.join(agent_dir, "fixtures", slug)
    where = "fixtures/%s" % slug
    if os.path.islink(base): return [], ["%s is a symlink" % where]
    if not os.path.isdir(base): return [], ["%s is not a folder of the agent" % where]
    real = os.path.realpath(agent_dir)
    files, probs = [], []
    for dp, dn, fn in os.walk(base):
        dn.sort()
        for n in sorted(dn + fn):
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, base).replace(os.sep, "/")
            if os.path.islink(p):
                probs.append("%s/%s is a symlink" % (where, rel))
            elif control_name(n):
                probs.append("%s/%s is a sandbox install folder name" % (where, rel))
            elif os.path.realpath(p) != real and not os.path.realpath(p).startswith(real + os.sep):
                probs.append("%s/%s resolves outside the agent folder" % (where, rel))
            elif os.path.isfile(p):
                files.append((rel, p))
            elif not os.path.isdir(p):
                probs.append("%s/%s is not a regular file" % (where, rel))
        dn[:] = [d for d in dn if not os.path.islink(os.path.join(dp, d)) and not control_name(d)]
    return (sorted(files), []) if not probs else ([], probs)

def scenario_fixture(agent, scenario):
    """(slug, files, problems) of a scenario's fixture; (None, [], []) when the scenario names none."""
    slug = scenario["data"].get("fixture")
    if slug is None: return None, [], []
    files, probs = fixture_files(agent.dir, slug)
    return slug, files, ["fixture %s of scenario %s: %s" % (slug, scenario["id"], p) for p in probs]

def fixture_record(slug, files):
    return {"slug": slug, "sha256": sha256_labeled(files), "files": [[r, file_sha256(p)] for r, p in files]}

def agent_input_lines(agent):
    """The plan lines of the inputs K2 adds: each pin with its state, each scenario fixture with its hash and files."""
    L = []
    for s in pin_states(agent):
        L.append("pin %s: registered, registration sha256 %s (%s)" % (s["pin"], s["registration_sha256"], s["skill_dir"])
                 if s["ok"] else "pin %s: REFUSED (%s)" % (s["pin"], s["reason"]))
    for sc in agent.scenarios:
        slug, files, probs = scenario_fixture(agent, sc)
        if slug is None or probs: continue
        L.append("fixture %s %s: sha256 %s, %d file(s)" % (sc["id"], slug, sha256_labeled(files), len(files)))
        L += ["fixture file %s %s/%s: sha256 %s" % (sc["id"], slug, r, file_sha256(p)) for r, p in files]
    return L

def agent_input_problems(agent):
    """The K2 refusals of agent plan, run, grade --live and loop: the skills key without the skill capability, a pin
    that does not verify, a scenario fixture that cannot be copied."""
    out = []
    if agent.pins and "skill" not in (agent.meta or {}).get("capabilities", []):
        out.append("skills-need-skill-capability: skills is set but capabilities lacks skill")
    out += pin_problems(agent)
    for sc in agent.scenarios:
        out += scenario_fixture(agent, sc)[2]
    return out

AGENT_ROWS = (("AGENT.md v2", ("agent-frontmatter-v2",), True), ("scenario schema", ("scenarios-valid",), True),
              ("scenarios present", ("scenarios-present",), False), ("tool subset", ("tools-subset",), True),
              ("tools declared", ("tools-declared",), False))

def agent_rows(agent):
    """[(label, ok, reason, fails validate)] of the rule bundle rows of AGENT_ROWS (eva.check), then the agent name and
    scenario id checks of the agent commands."""
    import eva
    from engine import agent_eval
    rows = {c["id"]: c for c in eva.check(agent.dir).checks}
    out = []
    for label, ids, hard in AGENT_ROWS:
        got = [rows.get(i) for i in ids]
        ok = all(r is not None and r["status"] == "pass" for r in got)
        reason = "; ".join("%s: %s" % (i, r["reason"] if r else "row missing") for i, r in zip(ids, got))
        out.append((label, ok, reason, hard))
    pins = rows.get("skills-need-skill-capability")
    if pins is not None and pins["status"] != "n/a":
        out.append(("skills capability", pins["status"] == "pass", "%s: %s" % (pins["id"], pins["reason"]), True))
    reserved = agent_eval.reserved_agent_name(agent.name)
    name_reason = ("reserved agent name: Claude lists %r in the init agents of every session (or a colon names a "
                   "plugin agent), so a run could not show the wrapper was loaded; rename the agent" if reserved else
                   "%r is not a reserved Claude agent name") % agent.name
    out.append(("agent name", not reserved, name_reason, True))
    dup = agent_eval.duplicate_ids(agent.scenarios)
    files = lambda i: ", ".join(s["file"] for s in agent.scenarios if s["id"] == i)
    ids_reason = "; ".join("duplicate scenario id %r (%s)" % (i, files(i)) for i in dup) if dup else \
        "%d scenario id(s), each unique" % len(agent.scenarios)
    out.append(("scenario ids", not dup, ids_reason, True))
    return out

def agent_validation_problems(agent):
    return ["%s %s: %s" % (agent.name, label, reason) for label, ok, reason, hard in agent_rows(agent) if hard and not ok]

def agent_validate(agent):
    from engine import agent_eval
    rows = agent_rows(agent)
    print("agent: %s (%s)" % (agent.name, agent.dir))
    for label, ok, reason, _ in rows:
        print("%s: %s (%s)" % (label, "pass" if ok else "fail", reason))
    for tool, state in agent_eval.wrapper_states(agent.dir).items():
        print("wrapper %s: %s" % (tool, agent_eval.wrapper_label(state, agent_eval.wrapper_rel(tool, agent.name))))
    bad = [label for label, ok, _, hard in rows if hard and not ok]
    print("validate: %s" % ("FAIL (%s)" % ", ".join(bad) if bad else "OK (a missing or out-of-date wrapper is shown "
                                                                     "per cell by `agent plan`)"))
    return 1 if bad else 0

def make_agent_engines(a):
    out, seen = [], set()
    for name in [n.strip() for n in a.engines.split(",")]:
        if name in UNAVAILABLE_ENGINES:
            raise EvalError("engine %r (%s) is not available: no %s engine exists yet, so nothing could be run or graded "
                            "for it (never faked). Available engines: %s" % (name, UNAVAILABLE_ENGINES[name],
                                                                           UNAVAILABLE_ENGINES[name], "|".join(ENGINES)))
        if name not in AGENT_ENGINES: raise EvalError("unknown engine %r (%s)" % (name, "|".join(AGENT_ENGINES)))
        if name in seen: raise EvalError("engine %r is listed twice in --engines" % name)
        seen.add(name)
        extra = (a.claude_arg if name == "claude" else a.codex_arg) or ()
        if any(x in ("--agent", "--agents") or x.startswith(("--agent=", "--agents=")) for x in extra):
            raise EvalError("%s extra args may not choose an agent: the agent under test is the generated wrapper" % name)
        out.append(AGENT_ENGINES[name](model=a.claude_model if name == "claude" else a.codex_model, extra_args=extra,
                                       effort=_str_opt(a, "claude_effort" if name == "claude" else "codex_effort")))
    return out

def agent_workspace(a, agent):
    if a.workspace: return os.path.realpath(a.workspace)
    ws = registry.agent_workspace(agent.dir, agent.name)
    if ws is None:
        raise EvalError("agent name %r is not a safe path component; fix AGENT.md name (or pass --workspace)" % agent.name)
    return ws

def agent_user_scope(name):
    """{tool: path} of a same-named agent at user scope, which a run could load next to the wrapper under test:
    <Claude config dir>/agents/<name>.md (the config dir preflight reads) and agents/<name>.toml of ~/.codex and
    $CODEX_HOME. Read only."""
    found = {"claude": [os.path.join(claude_config_dir(), "agents", name + ".md")],
             "codex": [os.path.join(h, "agents", name + ".toml")
                       for h in (os.path.join(os.path.expanduser("~"), ".codex"), real_codex_home())]}
    out = {}
    for tool, paths in found.items():
        hits = [p for p in paths if os.path.lexists(p)]
        if hits: out[tool] = hits[0]
    return out

def agent_cells(agent, engines):
    from engine import agent_eval
    targets = (agent.meta or {}).get("targets") or []
    errors = {t: agent_eval.error_state("a same-named agent is installed at user scope: %s" % p)
              for t, p in agent_user_scope(agent.name).items()}
    if agent_eval.reserved_agent_name(agent.name): errors["claude"] = agent_eval.RESERVED_NAME
    return agent_eval.cell_table(agent.scenarios, targets, [t for t, e in TOOL_ENGINES.items() if e],
                                 [e.name for e in engines], agent_eval.wrapper_states(agent.dir), errors)

def agent_configs(old):
    return 2 if old is not None else 1

def agent_estimate(engines, cells, runs, old, prices, tokens):
    """Exec cost of every planned cell: runs x configurations calls per cell (API-equivalent USD)."""
    from engine import agent_eval
    rows = []
    for e in engines:
        n = sum(1 for c in cells if c["tool"] == e.name and c["state"] == agent_eval.PLANNED) * runs * agent_configs(old)
        rows.append(est_row(e.name, "exec", e.model or "%s CLI default" % e.name, n, tokens,
                            call_cost(tokens["exec"], price_for(e.name, e.model, prices))))
    return est_result(rows, tokens)

def agent_plan_text(a, agent, old, ws, engines, cells, prices, tokens):
    from engine import agent_eval
    per = {e.name: call_cost(tokens["exec"], price_for(e.name, e.model, prices)) for e in engines}
    calls = a.runs * agent_configs(old)
    L = ["agent: %s (%s)" % (agent.name, agent.dir), "workspace: %s" % ws,
         "scenarios: %d valid (%s)" % (len(agent.scenarios), ", ".join("%s: %s, %d turn(s)" % (s["file"], s["id"], s["turns"])
                                                                    for s in agent.scenarios))] + agent_input_lines(agent) + [
         "engines: %s | runs per cell: %d | baseline: %s" % (
             ", ".join(e.label() for e in engines), a.runs,
             "previous version %s (%s)" % (old.name, old.dir) if old else
             "none (pass --old-agent DIR to compare with the previous version)"),
         "cells (scenario x tool):"]
    for c in cells:
        est = ""
        if c["state"] == agent_eval.PLANNED:
            p = per.get(c["tool"])
            est = " (est unpriced: pass --price)" if p is None else " (est $%.2f for %d exec call%s)" % (
                p * calls, calls, "" if calls == 1 else "s")
        L.append("cell %s %s: %s%s" % (c["scenario"], c["tool"], c["state"], est))
    est = agent_estimate(engines, cells, a.runs, old, prices, tokens)
    L.append("estimate: exec API-equivalent $%.2f over %d call(s)%s" % (
        est["total_usd"], est["calls"], "; UNPRICED: %s (pass --price)" % ", ".join(est["unpriced_models"])
        if est["unpriced_models"] else ""))
    ge = grader_engine_of(a)
    n_llm = sum(1 for c in cells if c["state"] == agent_eval.PLANNED and any(
        agent_eval.llm_items(agent.dir, s["data"]) for s in agent.scenarios if s["id"] == c["scenario"])) * calls
    g = grader_call_usd("grader", a.grader_model, tokens, prices)
    L.append("grader: %s for llm criteria with `agent grade --live`: %d call(s), est %s" % (
        grader_label(ge, a.grader_model, _str_opt(a, "grader_effort")), n_llm,
        "unpriced" if g is None else "$%.2f" % (g * n_llm)))
    for tool, spec in sorted(agent_eval.LAUNCH.items()):
        L.append("launch %s: %s (%s: %s)" % (tool, " ".join(
            json.dumps(x) if " " in x else x for x in agent_eval.launch_argv(tool, agent.name, "<turn 1>")),
            "verified" if spec["verified"] else "UNVERIFIED", spec["source"]))
    p = AUTH["profile"]
    L.append(PERSONAL_DEFAULT_LINE if getattr(a, "auth_profile", None) is None else
             "auth profile: %s (billing %s)" % (p.name, p.billing))
    soft = [e.name for e in engines if budget_enforcement(e) == "soft_limit"]
    L.append("budget: %s%s" % (
        "--live needs --budget-usd X (a finite cap in API-equivalent USD)" if getattr(a, "budget_usd", None) is None
        else "--budget-usd $%.2f" % a.budget_usd,
        "; STRICT (default): --live refuses %s (no per-call cap)" % ", ".join(soft) if soft and a.strict_budget else
        "; WARNING " + NO_STRICT_WARNING % ", ".join(soft) if soft else ""))
    L += ["tool coverage (dynamic): " + coverage_line(tool_coverage([e.name for e in engines])),
          "registration coverage: registration needs a graded run on every tool of coverage_required (%s) or a recorded "
          "coverage_waiver:<tool> (G1, plan.md:1136); `agent report`, `agent loop` and `register` read the agent's "
          "recorded coverage waivers, so a tool that did not run stays coverage-missing unless its waiver verifies"
          % ", ".join(COVERAGE_REQUIRED),
          "multi-turn: non-interactive continuation is UNVERIFIED for %s; every scenario with more than one turn "
          "records %s until an operator probe verifies a tool" % (
              ", ".join(t for t, ok in agent_eval.MULTI_TURN_VERIFIED.items() if not ok), agent_eval.MULTI_TURN)]
    tier = agent_grader_problems(a, engines)
    L += agent_gate_lines(agent, ws)
    return "\n".join(L + ["grader-tier-distinct: " + ("fail: " + "; ".join(tier) if tier else "pass")])

def waiver_label(w):
    if w["status"] != "approved": return approval_label(approvals.Verdict(w["status"], w["principal"], w["verdict_reason"]))
    if w["source"] == "machine": return "machine-waived by %s (expires %s)" % (w["principal"], w["machine_waiver"]["expires"])
    return "approved by %s" % w["principal"]

def agent_gate_lines(agent, ws):
    """The approval gates of an agent in the form of skill plan_text, and the state of the agent live gates. Reads
    the approval verdicts and the machine waiver; writes nothing."""
    spec, verdict_of = agent_spec(agent), _memo_verdicts()
    findings = rule_review_items(agent.dir)
    rr = verdict_of("rule_review", spec, ws) if findings else None
    waivers = waiver_verdicts(spec, ws)
    lacking = list(unapproved_live_gates(spec, ws, verdict_of))
    review_state = "pass" if rr is None or rr.status == "approved" else "fail: rule_review %s (%d finding(s))" % (
        rr.status, len(findings))
    approvals_state = "pass" if not lacking else "fail: " + ", ".join(
        "%s %s" % (g, v.status) for g, v in [(COMBINED_GATE, verdict_of(COMBINED_GATE, spec, ws))] + lacking)
    return ["rule review (English-only / parity needs-review findings): " + (
                "none" if rr is None else "RECORDED (by %s)" % rr.principal if rr.status == "approved" else
                "NOT RECORDED (%sblocks agent run --live and agent loop --live)" % (
                    "" if rr.status == "missing" else rr.status + "; ")),
            "coverage waivers: " + ", ".join("%s=%s" % (t, waiver_label(waivers[t])) for t in COVERAGE_REQUIRED),
            machine_waiver_line(machine_waiver()),
            "combined approval gate: %s=%s (the live gate needs it, or both requirements and eval_set)" % (
                COMBINED_GATE, approval_label(verdict_of(COMBINED_GATE, spec, ws))),
            "approval gates: requirements=%s, eval_set=%s" % (
                approval_label(verdict_of("requirements", spec, ws)), approval_label(verdict_of("eval_set", spec, ws))),
            "agent live gates (agent run --live and agent loop --live refuse until each passes): rule-review: %s; "
            "approvals: %s" % (review_state, approvals_state)]

class AgentSandbox(object):
    """One fresh temp folder per agent run: <run_root>/agent-eval-<engine>-XXXX/{work, home, codex_home}. work holds
    exactly the generated wrapper at project scope, the scenario's fixture files (fixture: [(rel, path)]) and the
    pinned skills copied at project scope (pins: [(name, skill folder)], K2); the agent folder and its scenarios stay
    outside. Removed after the run."""
    def __init__(self, engine, wrapper, rel, run_root, fixture=(), pins=()):
        import eva
        self.engine, self.run_root, self.rel = engine, os.path.realpath(run_root), rel
        self.pins, self.installed = [n for n, _ in pins], [rel]
        self.expected = [rel]
        self.root = tempfile.mkdtemp(prefix="agent-eval-%s-" % engine.name, dir=self.run_root)
        try:
            self.work = os.path.join(self.root, "work"); os.makedirs(self.work)
            dst = ensure_inside(self.work, os.path.join(self.work, *rel.split("/")))
            os.makedirs(os.path.dirname(dst))
            shutil.copyfile(wrapper, dst)
            for frel, src in fixture:
                to = ensure_inside(self.work, os.path.join(self.work, *frel.split("/")))
                os.makedirs(os.path.dirname(to), exist_ok=True)
                shutil.copyfile(src, to)
            for name, skill_dir in pins:
                engine.install_skill(self.work, skill_dir, name)
                self.installed += ["%s/%s/%s" % (engine.skills_rel, name, f) for f in eva.unit_tree(skill_dir).files]
            self.expected = sorted(self.installed + [frel for frel, _ in fixture])
            self.env = engine.sandbox_env(self.root)
        except BaseException as ex:
            left = "; ".join(remove_sandbox(engine, self.run_root, self.root))
            if left and isinstance(ex, Exception): raise EvalError("%s; and %s" % (ex, left)) from ex
            if left: print("WARNING: " + left, file=sys.stderr)
            raise
    def problems(self):
        out = self.engine.sandbox_problems(self.root, self.work, None, False, pins=self.pins)
        found = sorted(os.path.relpath(os.path.join(dp, f), self.work).replace(os.sep, "/")
                       for dp, _, fn in os.walk(self.work) for f in fn)
        if found != self.expected:
            out.append("the sandbox work folder holds %s, expected exactly %s" % (found, self.expected))
        ctl = [f for f in found if f not in self.installed and any(control_name(c) for c in f.split("/"))]
        if ctl: out.append("the sandbox work folder holds %s under a sandbox install folder name" % ctl)
        for a in ancestors(os.path.dirname(self.root)):
            for d in AGENT_CONTROL_DIRS:
                p = os.path.join(a, d)
                if os.path.isdir(p) and os.listdir(p): out.append("ancestor %s is not empty; every run would see it" % p)
        return out
    def record(self):
        rec = {"sandbox": self.root, "installed": self.rel, "auth": self.engine.auth_record(),
               "env_overrides": sorted(k for k in self.env if os.environ.get(k) != self.env[k]),
               "env_removed": sorted(k for k in os.environ if k not in self.env)}
        if self.pins: rec["pinned_skills"] = list(self.pins)
        return rec
    def manifest(self):
        """{rel: {"sha256"} | {"link"}} of what the run left in work, the wrapper and the pinned skill files left out;
        a link is recorded with its target and never followed."""
        out, skip = {}, set(self.installed)
        for dp, dn, fn in os.walk(self.work):
            for n in dn + fn:
                p = os.path.join(dp, n)
                rel = os.path.relpath(p, self.work).replace(os.sep, "/")
                if os.path.islink(p): out[rel] = {"link": os.readlink(p)}
                elif n in fn and rel not in skip and os.path.isfile(p): out[rel] = {"sha256": file_sha256(p)}
        return out
    def cleanup(self):
        problems = []
        try:
            problems = list(self.engine.after_run(self.root))
        finally:
            problems += remove_sandbox(self.engine, self.run_root, self.root)
        return problems

def skills_loaded(res, pins):
    """verified when the run's init event lists exactly the pinned skills, else unverified (an empty list, a subset,
    or no init event: Codex has none), never a pass."""
    init = res.get("init")
    skills = init.get("skills") if isinstance(init, dict) else None
    names = {str(s.get("name") if isinstance(s, dict) else s) for s in skills} if isinstance(skills, list) else None
    return "verified" if names == set(pins) else "unverified"

def agent_isolated_run(eng, prompt, agent, run_root, cap, user_names, ticket=None, fixture=(), pins=None):
    """One CLI call in a fresh AgentSandbox: (result, fatal problems), as isolated_run for skills. fixture: the
    scenario's fixture files; pins: the pin states the iteration recorded for this agent, which the pins must still
    match (refused before the sandbox is built, nothing spent)."""
    from engine import agent_eval
    states = pin_states(agent)
    bad = [s for s in states if not s["ok"]]
    if bad: raise EvalError("pin %s does not verify: %s" % (bad[0]["pin"], bad[0]["reason"]))
    if pins is not None and pin_record(states) != pins:
        raise EvalError("a pinned skill of %s changed after the iteration started; nothing was run" % agent.name)
    names = [s["name"] for s in states]
    sb = AgentSandbox(eng, agent.wrapper(eng.name), agent_eval.install_rel(eng.name, agent.name), run_root, fixture,
                      [(s["name"], s["skill_dir"]) for s in states])
    eng.last_stream = None
    try:
        probs = sb.problems()
        if probs: raise EvalError("isolation (%s): %s" % (eng.name, " | ".join(probs)))
        res = eng.run(prompt, sb.work, with_skill=bool(names), budget_usd=cap, env=sb.env, skill_name=agent.name,
                      ticket=ticket)
        try:
            iso = eng.isolation_problems(res, False, None, user_names, pins=names)
            res["isolation"] = dict(sb.record(), problems=iso)
            if names: res["isolation"]["skills_loaded"] = skills_loaded(res, names)
            res["work_manifest"] = sb.manifest()
        except Exception as ex:
            iso = []
            res["launch_failed"] = res.get("launch_failed") or "output handling failed after the call (%s)" % type(ex).__name__
            res["error"] = res["launch_failed"]
    except BaseException as ex:
        after = sb.cleanup()
        if after and isinstance(ex, Exception): raise EvalError("%s; and %s" % (ex, "; ".join(after))) from ex
        if after: print("WARNING: " + "; ".join(after), file=sys.stderr)
        raise
    after = sb.cleanup()
    fatal = iso + after
    if fatal and not res.get("error"): res["error"] = "isolation: " + "; ".join(fatal)
    res["stream"] = eng.last_stream or ""
    res["workdir"] = os.path.realpath(sb.work)
    return res, fatal

def record_agent_run(rd, res, eng, scenario, config, k, agent, api_eq, fixture=None):
    """stream.jsonl, the normalized transcript.json (only when the stream normalizes and, for Claude, the init event
    lists the agent: D8) with the work-folder manifest, and run.json with the run's state and, for a scenario with a
    fixture, its slug and hash."""
    from engine import agent_eval
    from engine import normalize as N
    wtext(os.path.join(rd, "stream.jsonl"), res["stream"])
    state, loaded, t = agent_eval.RUN, None, None
    if res.get("error"):
        state = agent_eval.error_state(res["error"])
    else:
        try:
            t = N.normalize(eng.name, res["stream"], scenario["id"], agent=agent.name)
        except N.NormalizeError as ex:
            state = agent_eval.error_state("normalize: %s" % ex)
        else:
            loaded = t["agent_loaded"]
            if loaded == "not-loaded":
                state, t = agent_eval.AGENT_NOT_LOADED, None
            elif t["is_error"]:
                state = agent_eval.error_state("the stream reports is_error")
    if t is not None:
        t.setdefault("workspace", res["workdir"])
        t["turns"] = [{"user": agent_eval.prompt_of(scenario), "final": t["final"]}]
        t["work_manifest"] = res.get("work_manifest", {})
        wjson(os.path.join(rd, "transcript.json"), N.checked(t))
    extra = {"fixture": fixture} if fixture else {}
    wjson(os.path.join(rd, "run.json"), dict(extra, **{
        "scenario": scenario["id"], "file": scenario["file"], "tool": eng.name, "configuration": config, "run": k,
        "agent": agent.name, "agent_dir": agent.dir, "state": state, "agent_loaded": loaded, "model": eng.model,
        "effort": eng.effort, "command": res.get("command"), "returncode": res.get("returncode"), "error": res.get("error"),
        "stderr_tail": res.get("stderr_tail", ""), "duration_ms": res.get("duration_ms", 0),
        "input_tokens": res["input_tokens"], "output_tokens": res["output_tokens"], "cost_usd": res.get("cost_usd"),
        "api_equivalent_usd": round(api_eq[0], 6), "api_equivalent_source": api_eq[1], "billing": current_billing(),
        "isolation": res.get("isolation")}))
    return state

def agent_run_iteration(agent, old, ws, engines, cells, runs, budget, prices, tokens, run_root=None, log=print,
                        builder_model=None):
    """Every planned cell, runs times per configuration, each in its own AgentSandbox. builder_model: recorded in
    plan.json so a later `agent grade --live` compares it. Returns the iteration number."""
    from engine import agent_eval
    run_root = os.path.realpath(run_root or tempfile.gettempdir())
    n = (iterations(ws) or [0])[-1] + 1
    it_dir = os.path.join(ws, "iteration-%d" % n)
    configs = [("new_agent", agent)] + ([("old_agent", old)] if old else [])
    pinned = {c: pin_states(src) for c, src in configs}
    bad = [s for states in pinned.values() for s in states if not s["ok"]]
    if bad: raise EvalError("pin %s does not verify: %s" % (bad[0]["pin"], bad[0]["reason"]))
    pinned = {c: pin_record(states) for c, states in pinned.items()}
    planned = {c["scenario"] for c in cells if c["state"] == agent_eval.PLANNED}
    fixtures = {}
    for s in agent.scenarios:
        slug, files, probs = scenario_fixture(agent, s) if s["id"] in planned else (None, [], [])
        if probs: raise EvalError("; ".join(probs))
        if slug is not None: fixtures[s["id"]] = (files, fixture_record(slug, files))
    plan = {"agent_name": agent.name, "agent_path": agent.dir, "old_agent": old.dir if old else None,
            "engines": {e.name: e.model for e in engines}, "efforts": {e.name: e.effort for e in engines},
            "runs_per_cell": runs, "configurations": [c for c, _ in configs], "cells": cells,
            "launch": {t: s["verified"] for t, s in agent_eval.LAUNCH.items()},
            "multi_turn_verified": dict(agent_eval.MULTI_TURN_VERIFIED), "started_at": now_iso(),
            "agent_sha256": sha256_labeled(agent.files([e.name for e in engines])),
            "old_agent_sha256": sha256_labeled(old.files([e.name for e in engines])) if old else None,
            "ruleset_sha256": ruleset_sha256(), "policy_sha256": policy_sha256(),
            "auth_profile": None if AUTH["profile"] is None else AUTH["profile"].name,
            "isolation": {"run_root": run_root, "per_run_sandbox": True},
            "budget_enforcement": {e.name: budget_enforcement(e) for e in engines}}
    if pinned["new_agent"]: plan["pins"] = pinned["new_agent"]
    if fixtures: plan["fixtures"] = {sid: rec for sid, (_, rec) in fixtures.items()}
    if builder_model: plan["builder_model"] = builder_model
    wjson(os.path.join(it_dir, "plan.json"), plan)
    spent0, stopped = budget.spent, None
    user_names = sorted({nm for nm, _ in claude_user_skills(claude_config_dir(), config_dir=True)}) \
        if any(e.name == "claude" for e in engines) else []
    by_id = {s["id"]: s for s in agent.scenarios}
    states = {}

    def one(eng, scenario, config, src, k):
        cap, fallback, stop = guard_call(eng, "exec", budget, prices, tokens)
        ticket, stop = spend_admit(budget, "exec", eng.name, eng.model, n, cap, fallback, eng.has_call_cap, stop)
        if stop: raise _Fatal(stop)
        rd = ensure_inside(it_dir, agent_eval.run_dir(it_dir, eng.name, scenario["id"], config, k))
        files, fx = fixtures.get(scenario["id"], ((), None))
        try:
            res, fatal = agent_isolated_run(eng, agent_eval.prompt_of(scenario), src, run_root, cap, user_names, ticket,
                                            files, pinned[config])
        except Exception:
            if ticket is not None: ticket.not_launched(budget.spent)
            raise
        api_eq = run_cost(res, eng, prices, fallback)
        if res.get("launch_failed") and fallback > api_eq[0]: api_eq = (fallback, "cap_charged")
        budget.charge(api_eq[0])
        failed = res.get("launch_failed") or ""
        spend_settle(ticket, budget, "unparsed" if "could not be parsed" in failed else "launch_failed" if failed else
                     "error" if res.get("error") else "ok", res.get("cost_usd"), res["input_tokens"],
                     res["output_tokens"], price_info(eng.name, eng.model, prices), api_eq[0], api_eq[1])
        try:
            state = record_agent_run(rd, res, eng, scenario, config, k, src, api_eq,
                                     {"slug": fx["slug"], "sha256": fx["sha256"]} if fx else None)
        except Exception as ex:
            raise _Fatal("[%s %s] could not record the run (%s); charged $%.2f (%s) and stopped"
                         % (eng.name, config, type(ex).__name__, api_eq[0], api_eq[1]))
        log("[%s] %s %s run-%d: %s, %d tokens, %.1fs" % (eng.name, scenario["id"], config, k, state,
            res["input_tokens"] + res["output_tokens"], res.get("duration_ms", 0) / 1000.0))
        if res.get("launch_failed"):
            raise _Fatal("[%s %s] %s; charged $%.2f (%s) and stopped%s" % (
                eng.name, config, res["launch_failed"], api_eq[0], api_eq[1], "".join("; and " + x for x in fatal)))
        if fatal: raise _Fatal("isolation [%s %s]: %s" % (eng.name, config, "; ".join(fatal)))
        return state

    try:
        for eng in engines:
            for c in cells:
                if c["tool"] != eng.name or c["state"] != agent_eval.PLANNED: continue
                got = [one(eng, by_id[c["scenario"]], config, src, k) for config, src in configs
                       for k in range(1, runs + 1)]
                states[(c["scenario"], c["tool"])] = agent_eval.RUN if agent_eval.RUN in got else got[0]
    except (_Fatal, EvalError, spend.SpendError) as ex:
        stopped = str(ex)
    stopped = stopped or spend_halt(budget)
    plan = rjson(os.path.join(it_dir, "plan.json"))
    plan["cells"] = [dict(c, state=states.get((c["scenario"], c["tool"]), c["state"] if c["state"] != agent_eval.PLANNED
                                               else agent_eval.error_state("not run: the iteration stopped")))
                     for c in cells]
    plan.update(finished_at=now_iso(), billing=current_billing(), stopped=stopped,
                api_equivalent_usd=round(budget.spent - spent0, 4))
    wjson(os.path.join(it_dir, "plan.json"), plan)
    if stopped: log("STOPPED: " + stopped)
    return n

def agent_plan_hash(a, ws, parts):
    obj = dict(parts, format=1, argv=a.argv, workspace=ws, ruleset_sha256=ruleset_sha256(),
               auth_profiles_sha256=file_sha256(os.path.join(RULESET_ROOT, "policy", AUTH_PROFILES_FILE)))
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()

def agent_live_start(a, agent, ws, gate, problems, parts, show, claude_called):
    """The skill live order for an agent command: --budget-usd and --auth-profile were checked by the caller; then
    the ledger preview, problems(left) (every check that starts no agent, model or builder; only a signed approval
    from before N1 runs ssh-keygen) inside prepare(), the recorded live approval, problems(left) again plus `claude
    auth status` when Claude is called under subscription auth, the plan hash recheck, enable_live and the spend
    ledger. Returns the Budget."""
    dangling = spend.preview(ws)
    left = a.budget_usd - sum(r["bound_usd"] or 0.0 for r in dangling)
    def prepare():
        probs = problems(left)
        if probs: raise EvalError("agent %s --live refused: %s" % (a.agent_cmd, "; ".join(probs)))
        rules = ruleset_sha256()
        return approvals.Prepared(agent.name, agent_plan_hash(a, ws, parts()), rules,
                                  ["live command: " + shlex.join(["python3", "skill_eval.py"] + a.argv),
                                   "workspace: " + ws] + show + [x for x in (spend.preview_line(dangling),) if x]
                                  + ["ruleset_sha256 " + rules])
    grant = APPROVER["it"].sign("live", gate, prepare, budget_usd=a.budget_usd)
    print("recorded agent %s --live by %s (plan sha256 %s)" % (a.agent_cmd, grant.record["recorded_by"],
                                                                grant.sha256[:12]), file=sys.stderr)
    probs = problems(left) + (claude_auth_problems() if claude_called and not api_key_mode() else [])
    if probs: raise EvalError("agent %s --live refused after signing: %s" % (a.agent_cmd, "; ".join(probs)))
    if agent_plan_hash(a, ws, parts()) != grant.sha256:
        raise EvalError("the command's inputs changed after signing (agent folder, scenarios, wrappers, iteration or a "
                        "file named by an option); nothing was run, sign again")
    budget = Budget(a.budget_usd)
    enable_live(grant)
    p = AUTH["profile"]
    budget.ledger = _LIVE["ledger"] = spend.Ledger(ws, "agent-" + a.agent_cmd, p.name, p.billing, a.budget_usd)
    budget.spent = budget.ledger.open([r["call_id"] for r in dangling])
    if budget.ledger.torn:
        print("WARNING: spend ledger %s: dropped a torn last row (%s)" % (budget.ledger.path, budget.ledger.torn),
              file=sys.stderr)
    return budget

def agent_live_required(a):
    if a.budget_usd is None or not math.isfinite(a.budget_usd) or a.budget_usd <= 0:
        raise EvalError(CAP_REQUIRED % AUTH["profile"].name if api_key_mode() else NO_BUDGET)
    if a.auth_profile is None: raise EvalError(PROFILE_REQUIRED)

AGENT_LIVE_APPROVALS = ("a current requirements_eval_set approval (or current requirements and eval_set approvals), a "
                        "current rule_review decision when the agent has rule-review findings")
AGENT_GRADER_TIER = "a grader tier (opus, sonnet, haiku) that differs from every model under test and from any builder"

def agent_approval_inputs(agent, ws):
    """The approval inputs the plan hash does not bind through the agent files, so the last recheck of a live start
    also refuses a requirements.md or rule-review change made after the post-sign problems check."""
    spec = agent_spec(agent)
    return {"requirements": gate_hash("requirements", spec, ws), "rule_review": gate_hash("rule_review", spec, ws)}

def agent_run_problems(a, agent, old, engines, cells, est, prices, tokens, left):
    """Every check of an agent run --live that starts no process."""
    from engine import agent_eval
    out = []
    if api_key_mode():
        out += api_key_soft_refusals(engines, None, True, False)
    else:
        soft = [e.name for e in engines if budget_enforcement(e) == "soft_limit"]
        if a.strict_budget and soft:
            out.append("--strict-budget (default): %s has no enforceable per-call budget cap (its budget is only a soft "
                       "limit); refusing the live run. Drop it from --engines. --no-strict-budget is risky and overrides "
                       "only this gate. %s" % (", ".join(soft), NO_STRICT_TIER))
    out += [p for sc in agent.scenarios for p in scenario_fixture(agent, sc)[2]]
    for unit in [agent] + ([old] if old else []):
        out += agent_validation_problems(unit) + pin_problems(unit)
        states = agent_eval.wrapper_states(unit.dir)
        out += ["%s wrapper %s: %s" % (unit.name, e.name, agent_eval.wrapper_label(states[e.name], ""))
                for e in engines if states[e.name] is not None]
    if old and old.name != agent.name:
        out.append("--old-agent %s is named %s, not %s" % (old.dir, old.name, agent.name))
    for tool, p in sorted(agent_user_scope(agent.name).items()):
        if any(e.name == tool for e in engines):
            out.append("%s: agent %r is also installed at user scope at %s; hiding it from the run is not verified, so "
                       "it could load instead of the wrapper under test" % (tool, agent.name, p))
    pinned = {s["name"] for u in [agent] + ([old] if old else []) for s in pin_states(u) if s["ok"]}
    if pinned and any(e.name == "claude" for e in engines):
        out += ["claude: pinned skill %r is also installed at %s; hiding it via --setting-sources is not verified, so "
                "the init check could not tell it from the pinned copy" % (n, p)
                for n, p in claude_user_skills(claude_config_dir(), config_dir=True) if n in pinned]
    if not any(c["state"] == agent_eval.PLANNED for c in cells):
        out.append("no runnable cell: every scenario x tool cell is not-run (%s)" % ", ".join(
            sorted({c["state"] for c in cells})))
    if est["unpriced_models"]: out.append("no price for %s; pass --price" % est["unpriced_models"])
    for e in engines:
        b = None if e.has_call_cap else call_bound(e.name, e.model, "exec", prices, tokens)
        if b is not None and b > left:
            out.append("%s worst-case exec call bound $%.2f exceeds budget $%.2f (%s budget is a soft limit: no per-call "
                       "cap)" % (e.name, b, left, e.name))
    if est["total_usd"] > left:
        out.append("estimate $%.2f exceeds budget $%.2f" % (est["total_usd"], left))
    out += api_key_env_problems()
    if any(e.name == "codex" for e in engines) and subscription_checks_on(): out += codex_auth_problems()
    root = os.path.realpath(a.run_root or tempfile.gettempdir())
    for anc in ancestors(root):
        for d in AGENT_CONTROL_DIRS:
            p = os.path.join(anc, d)
            if os.path.isdir(p) and os.listdir(p):
                out.append("%s is not empty; every sandbox under %s would see it (use --run-root elsewhere)" % (p, root))
    return out

def plan_refused(agent, old):
    """Prints `agent plan refused: ...` for every K2 refusal of the agent and the previous version; True when any."""
    out = agent_input_problems(agent) + (agent_input_problems(old) if old else [])
    for p in out: print("agent plan refused: " + p)
    return bool(out)

def pin_parts(agent, old):
    """The plan-hash part of the pins (each pin with its registration hash), only for an agent with pins, so the
    plan hash of an agent without pins is the N1 one."""
    pins = {c: [[s["pin"], s["registration_sha256"]] for s in pin_states(u)]
            for c, u in (("agent", agent), ("old_agent", old)) if u is not None and u.pins}
    return {"pins": pins} if pins else {}

def agent_run(a, agent, ws, prices, tokens):
    engines = make_agent_engines(a)
    if a.runs < 1: raise EvalError("--runs must be >= 1")
    old = Agent(a.old_agent) if a.old_agent else None
    cells = agent_cells(agent, engines)
    if a.agent_cmd == "plan" or not a.live:
        print(agent_plan_text(a, agent, old, ws, engines, cells, prices, tokens))
        if plan_refused(agent, old): return 2
        print("\nDRY RUN: nothing was executed. A real run needs `agent run --live --auth-profile P --budget-usd X`, a "
              "clean `agent validate`, generated wrappers, " + AGENT_LIVE_APPROVALS + ", " + AGENT_GRADER_TIER +
              " and an estimate within budget; the start is recorded as an unsigned approval.")
        return 0
    agent_live_required(a)
    est = agent_estimate(engines, cells, a.runs, old, prices, tokens)
    names = [e.name for e in engines]
    parts = lambda: dict({"agent": sha256_labeled(agent.files(names)),
                          "old_agent": sha256_labeled(old.files(names)) if old else None,
                          "engines": {e.name: [e.model, e.effort] for e in engines}, "budget_usd": a.budget_usd,
                          "approvals": agent_approval_inputs(agent, ws)}, **pin_parts(agent, old))
    budget = agent_live_start(
        a, agent, ws, "agent-run", lambda left: agent_run_problems(a, agent, old, engines, cells, est, prices, tokens, left)
        + agent_gate_problems(agent, ws) + agent_grader_problems(a, engines), parts,
        ["agent %s: %d planned cell(s), runs %d, engines %s" % (
            agent.name, sum(1 for c in cells if c["state"] == "planned"), a.runs, ", ".join(names))], "claude" in names)
    for e in engines:
        if budget_enforcement(e) == "soft_limit":
            print("WARNING: budget " + SOFT_LIMIT_NOTE % e.name, file=sys.stderr)
            print("WARNING: " + NO_STRICT_WARNING % e.name, file=sys.stderr)
    n = agent_run_iteration(agent, old, ws, engines, cells, a.runs, budget, prices, tokens, a.run_root)
    stopped = (rjson(os.path.join(ws, "iteration-%d" % n, "plan.json"), {}) or {}).get("stopped")
    print("iteration-%d done, API-equivalent ~$%.2f%s" % (n, budget.spent, "; STOPPED: %s" % stopped if stopped else ""))
    return 3 if stopped else 0

def agent_iteration(a, ws):
    n = a.iteration or (iterations(ws) or [None])[-1]
    if n is None or not os.path.isdir(os.path.join(ws, "iteration-%d" % n)): raise EvalError("no iterations in %s" % ws)
    it_dir = os.path.join(ws, "iteration-%d" % n)
    if not isinstance(rjson(os.path.join(it_dir, "plan.json")), dict): raise EvalError("%s has no plan.json" % it_dir)
    return n, it_dir

def agent_grade(a, agent, ws, prices, tokens):
    import eva
    from engine import agent_eval
    n, it_dir = agent_iteration(a, ws)
    grade_fn = lambda agent_dir, path: eva.grade_transcript(agent_dir, path).checks
    if not a.live:
        done = agent_eval.grade_runs(it_dir, agent.dir, agent.scenarios, grade_fn)
        print("agent grade: iteration-%d, %d run(s) graded; llm criteria stay needs-review (no grader ran: agent grade "
              "--live grades them)" % (n, len(done)))
        return 0
    agent_live_required(a)
    ge = grader_engine_of(a)
    by_id = {s["id"]: s for s in agent.scenarios}
    todo = [r for r in agent_eval.iter_runs(it_dir) if r.get("state") == agent_eval.RUN
            and os.path.isfile(os.path.join(r["dir"], "transcript.json")) and r["scenario"] in by_id
            and agent_eval.llm_items(agent.dir, by_id[r["scenario"]]["data"])]
    est = grader_estimate(len(todo), 0, a.grader_model, tokens, prices)
    def problems(left):
        out = api_key_soft_refusals([], ge, False, True) if api_key_mode() else []
        try:
            check_grader_model(it_dir, a.grader_model)
        except EvalError as ex:
            out.append(str(ex))
        if not todo: out.append("nothing for a grader: no run of iteration-%d holds an llm criterion" % n)
        out += pin_problems(agent)
        if est["unpriced_models"]: out.append("no price for %s; pass --price" % est["unpriced_models"])
        if est["total_usd"] > left: out.append("estimate $%.2f exceeds budget $%.2f" % (est["total_usd"], left))
        out += api_key_env_problems()
        return out
    shas = lambda: {"runs": sorted((os.path.relpath(r["dir"], it_dir), sha256_files(os.path.join(r["dir"], "transcript.json")))
                                   for r in todo),
                    "agent": sha256_labeled(agent.files([])), "iteration": n,
                    "grader": [ge, a.grader_model, _str_opt(a, "grader_effort")], "budget_usd": a.budget_usd}
    budget = agent_live_start(a, agent, ws, "agent-grade", problems, shas, [
        "agent %s: %d run(s) with llm criteria, grader %s" % (
            agent.name, len(todo), grader_label(ge, a.grader_model, _str_opt(a, "grader_effort")))], ge == "claude")
    grader = make_grader(ge, a.grader_model, a.grader_effort, budget=budget, prices=prices)
    with spend_scope(grader, iteration=n):
        agent_eval.grade_runs(it_dir, agent.dir, agent.scenarios, grade_fn, grader, signed={r["dir"] for r in todo})
    append_cost(it_dir, "agent grade", grader.usage)
    if spend_halt(budget): raise EvalError(spend_halt(budget))
    print("graded iteration-%d with %s, API-equivalent ~$%.2f" % (n, grader.label(), budget.spent))
    return 0

def agent_report(a, agent, ws):
    n, it_dir = agent_iteration(a, ws)
    gate = agent_report_gate(ws, n, agent_spec(agent))
    print("iteration-%d: %s -> %s" % (n, "NEEDS APPROVAL" if gate["needs_approval"] else "clean",
                                     os.path.join(it_dir, "comparison.md")))
    if any(c.startswith(MACHINE_WAIVED_LABEL) for c in gate["coverage"].values()):
        print(approvals.printable(machine_waiver_line(machine_waiver())))
    for p in gate["problems"]: print("  - " + p)
    return 3 if gate["needs_approval"] else 0

def agent_spec(agent):
    return {"skill_name": agent.name, "skill_dir": agent.dir, "kind": "agent"}

def agent_bench_coverage(bench):
    """tool_coverage of an agent benchmark recomputed from its runs and planned engines, None when they are
    malformed."""
    from engine import agent_eval
    engines, runs = _meta(bench).get("engines"), bench.get("runs")
    if not (isinstance(engines, dict) and isinstance(runs, list)
            and all(isinstance(r, dict) and isinstance(r.get("tool"), str) for r in runs)):
        return None
    tested = [r for r in runs if r.get("configuration") == "new_agent"]
    graded = {e: sum(1 for r in tested if r["tool"] == e and r.get("graded") is True and r.get("pass_rate") is not None)
              for e in engines}
    collected = {e: sum(1 for r in tested if r["tool"] == e and r.get("state") == agent_eval.RUN) for e in engines}
    return tool_coverage(list(engines), graded, collected)

def agent_parity_problems(agent_dir):
    """The gate problem line of every parity FAIL row of the agent folder (a rule_review decision clears only
    needs-review rows)."""
    import eva
    return ["%s: %s" % (c["id"], c["reason"]) for c in eva.parity_report(agent_dir).failed]

def agent_report_gate(ws, n, spec):
    """Writes benchmark.json, gate.json and comparison.md of iteration-n and returns the gate. spec: the agent's, whose
    signed coverage waivers in ws clear exactly their tool (waiver_verdicts, as G1 does for skills). Every parity FAIL
    row of the agent folder is a gate problem too, so a registration approval names it."""
    from engine import agent_eval
    it_dir = os.path.join(ws, "iteration-%d" % n)
    plan = rjson(os.path.join(it_dir, "plan.json"))
    runs = agent_eval.collect(it_dir)
    bench = agent_eval.benchmark(plan, runs, None)
    cov = bench["tool_coverage"] = agent_bench_coverage(bench)
    states = coverage_states({"tool_coverage": cov, "coverage_waivers": waiver_verdicts(spec, ws)})
    missing = [GATE_MESSAGES["coverage-missing"] % c for _, c in states if c["state"] == "missing"]
    gate = agent_eval.gate(plan, runs, missing + agent_parity_problems(spec["skill_dir"]))
    gate["coverage"] = {t: coverage_label(c) for t, c in states}
    bench["gate"] = gate
    wjson(os.path.join(it_dir, "benchmark.json"), bench)
    wjson(os.path.join(it_dir, "gate.json"), gate)
    wtext(os.path.join(it_dir, "comparison.md"), agent_eval.comparison_md(
        bench, gate, n, [(t, cov[t]["status"], cov[t]["graded_runs"], gate["coverage"][t]) for t in cov]))
    return gate

# ---------- agent loop (unit B4): the builder may change only AGENT.md ----------
AGENT_ENGLISH_BUILDER_RULE = ("Write all agent content in English only (the AGENT.md frontmatter and body, from which the "
                              "wrappers are generated); the loop stops if any non-English text appears.")
AGENT_UNFIXABLE = ("coverage-missing:", "agent-load-unverified:", "skills-load-unverified:", "parity-permissions-hermes:")
AGENT_NOT_REGULAR = "AGENT.md is not a regular file directly in the agent folder"

class AgentBuilder(ClaudeBuilder):
    """The builder `agent loop --live` accepts: ClaudeBuilder's one metered, capped `claude -p` call per iteration through
    live_exec, run in the agent folder with its PreToolUse gate in agent-allowlist mode, so AGENT.md is the only file it
    may write."""
    MODE = writable.AGENT_MODE
    def prompt(self, report):
        return ("%s\n\nYou are improving the agent in the current directory. You may change only AGENT.md: the "
                "scenarios, the wrappers (generated from AGENT.md after your call) and every other file are read-only, "
                "and the loop stops if any of them changes. %s\n\nEval report of the last iteration:\n%s\n"
                % (self.instructions.strip(), AGENT_ENGLISH_BUILDER_RULE, report))

def agent_fingerprint(agent_dir, ws, old_dir=None):
    """The agent-mode fingerprint: every entry of the agent folder but AGENT.md, the workspace, the rule bundle files
    ruleset_sha256 hashes under RULESET_ROOT, named @ruleset/<path>, and every entry of the old agent folder, named
    @old-agent/<path>. AGENT.md is named only when it is not a regular file with one link (writable.odd_file)."""
    from engine import rule_runner
    fp = writable.fingerprint(agent_dir, ws, writable.AGENT_MODE)
    odd = writable.odd_file(os.path.join(os.path.realpath(agent_dir), "AGENT.md"))
    if odd: fp["AGENT.md"] = odd
    fp.update(writable.bundle_fingerprint(RULESET_ROOT, ("rules",) + rule_runner.HASHED_TREES + rule_runner.POLICY_FILES))
    if old_dir: fp.update(writable.folder_fingerprint(os.path.realpath(old_dir), writable.OLD_AGENT_PREFIX))
    return fp

def agent_folder_problems(agent_dir):
    """The start refusals of a folder a builder edits: AGENT.md must be a regular file with one link directly in it, and
    no link may leave it (link_refusals, as snapshot reads them)."""
    import eva
    p = os.path.join(agent_dir, "AGENT.md")
    out = [] if writable.odd_file(p) is None else [AGENT_NOT_REGULAR]
    return out + link_refusals(agent_dir, eva.unit_tree(agent_dir, shutil.ignore_patterns()))

def agent_regenerate(agent_dir):
    """The wrappers regenerated through the `eva.py generate` code path: ([(name, value before, value after)] of every
    file generate writes or removes, the refusal or error text or None)."""
    from engine import generate, rule_runner
    root = os.path.realpath(agent_dir)
    try:
        rule_runner.default_bundle(generate.ROOT)
        planned = generate.outcome(agent_dir)
    except (rule_runner.BundleError, generate.GenerateError) as ex:
        return [], "generate refused: %s" % ex
    rels = sorted(set(planned.writes) | set(planned.deletes))
    before = {r: writable.entry(os.path.join(root, *r.split("/"))) for r in rels}
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = generate.cli(agent_dir)
    writes = [(r, before[r], writable.entry(os.path.join(root, *r.split("/")))) for r in rels]
    return writes, None if rc == 0 else (err.getvalue().strip() or "generate exited %d" % rc)

def _agent_outside(st, outside):
    if outside:
        st["log"]("builder-wrote-outside " + writable.shown(outside[0]))
        return _stop(st, "builder-wrote-outside", outside)

def _agent_after_outside(st):
    d = st["agent"].dir
    st["post"] = agent_fingerprint(d, st["ws"], st["old"])
    return _agent_outside(st, writable.changed(st["before"], st["post"],
                                               [(writable.name(d, p), r, w) for p, r, w in st["harness"][st["seen"]:]]))

def _agent_after_regenerate(st):
    st["generated"], st["generate_error"] = agent_regenerate(st["agent"].dir)

def _agent_after_english(st):
    import eva
    found = eva.english_violations(st["agent"].dir)
    if found: return _stop(st, "builder-non-english", found)

def _agent_after_parity(st):
    import eva
    from engine import generate
    found = [st["generate_error"]] if st["generate_error"] else []
    if not found:
        check = generate.outcome(st["agent"].dir)
        found = [check.refusal] if check.refusal else ["generate --check: " + p for p in check.problems]
    found += [f for f in eva.parity_violations(st["agent"].dir) if f not in st["parity"]]
    if found: return _stop(st, "builder-parity-missing", found)

def _agent_after_review(st):
    import eva
    found = [x for x in eva.review_items(st["agent"].dir) if x not in st["review"]]
    if found: return _stop(st, "builder-review-required", found)

def _agent_after_outside_again(st):
    return _agent_outside(st, writable.changed(st["post"], agent_fingerprint(st["agent"].dir, st["ws"], st["old"]),
                                               st["generated"]))

def _agent_after_reload(st):
    try:
        agent = Agent(st["agent"].dir)
    except EvalError as ex:
        return _stop(st, "agent-invalid", [str(ex)])
    found = agent_folder_problems(agent.dir) + agent_validation_problems(agent)
    if found: return _stop(st, "agent-invalid", found)
    st["agent"] = agent

# Not declared in policy/gates.json: editing policy/ would move ruleset_sha256. The writable-set gate runs first so that
# regeneration never writes over a tree the builder changed outside AGENT.md.
AGENT_POST_BUILDER_GATES = (("writable-set", _agent_after_outside), ("regenerate", _agent_after_regenerate),
                            ("english", _agent_after_english), ("parity", _agent_after_parity),
                            ("rules-unchanged", _after_rules), ("rule-review", _agent_after_review),
                            ("writable-set-again", _agent_after_outside_again), ("builder-failed", _after_failed),
                            ("agent-reload", _agent_after_reload))
AGENT_CRASH_GATES = ("writable-set", "regenerate", "rules-unchanged")

def agent_loop(agent, ws, engines, grader, builder, max_iterations=MAX_AUTO_ITERATIONS, runs=DEFAULT_RUNS, old=None,
               budget=None, prices=None, tokens=None, precheck=None, run_root=None, log=print):
    """run -> typed grading (and grader for llm criteria) -> report; stop when the gate is clean, with "needs-coverage"
    when only AGENT_UNFIXABLE problems remain (coverage-missing, agent-load-unverified, skills-load-unverified, the fixed
    Hermes permission FAIL: no builder can fix them), else call
    builder(iteration_dir, agent_dir), which may change only AGENT.md, and run AGENT_POST_BUILDER_GATES after it,
    whatever it returned or raised; another exception than EvalError runs only AGENT_CRASH_GATES and is re-raised.
    precheck(agent, iterations_left) -> [problems] runs before every iteration; any problem stops the loop with
    "gates-failed". At most MAX_AUTO_ITERATIONS iterations."""
    import eva
    from engine import agent_eval
    if not 1 <= max_iterations <= MAX_AUTO_ITERATIONS:
        raise EvalError("max %d automatic iterations, got %d" % (MAX_AUTO_ITERATIONS, max_iterations))
    budget = Budget(float("inf")) if budget is None else budget
    prices, tokens = prices or {}, tokens or DEFAULT_TOKENS
    grade_fn = lambda agent_dir, path: eva.grade_transcript(agent_dir, path).checks
    n = None
    for i in range(max_iterations):
        if precheck is not None:
            probs = precheck(agent, max_iterations - i)
            if probs: return {"status": "gates-failed", "iteration": n, "problems": probs}
        n = agent_run_iteration(agent, old, ws, engines, agent_cells(agent, engines), runs, budget, prices, tokens,
                                run_root, log, getattr(builder, "model", None))
        it_dir = os.path.join(ws, "iteration-%d" % n)
        stopped = (rjson(os.path.join(it_dir, "plan.json"), {}) or {}).get("stopped")
        if stopped: return {"status": "run-stopped", "iteration": n, "error": stopped}
        if grader is None:
            agent_eval.grade_runs(it_dir, agent.dir, agent.scenarios, grade_fn, log=log)
        else:
            used = dict(grader.usage)
            with spend_scope(grader, iteration=n):
                agent_eval.grade_runs(it_dir, agent.dir, agent.scenarios, grade_fn, grader, log=log)
            append_cost(it_dir, "agent grade", usage_delta(grader.usage, used))
        halt = spend_halt(budget)
        if halt: return {"status": "run-stopped", "iteration": n, "error": halt}
        gate = agent_report_gate(ws, n, agent_spec(agent))
        log("iteration-%d gate: %s" % (n, gate["problems"] or "clean"))
        if not gate["problems"]: return {"status": "clean", "iteration": n}
        if all(p.startswith(AGENT_UNFIXABLE) for p in gate["problems"]): return {"status": "needs-coverage", "iteration": n}
        if budget.remaining() <= 0: return {"status": "budget-exhausted", "iteration": n}
        if i == max_iterations - 1 or builder is None: break
        harness = getattr(builder, "harness_writes", [])
        old_dir = old.dir if old else None
        st = {"agent": agent, "ws": ws, "old": old_dir, "n": n, "log": log, "harness": harness, "seen": len(harness),
              "before": agent_fingerprint(agent.dir, ws, old_dir), "rules": ruleset_sha256(),
              "parity": set(eva.parity_violations(agent.dir)), "review": set(eva.review_items(agent.dir)),
              "rc": None, "error": None}
        crash, led = None, getattr(budget, "ledger", None)
        if led is not None: led.bracket = harness
        try:
            st["rc"] = builder(it_dir, agent.dir)
        except EvalError as ex:
            st["error"] = str(ex)
        except Exception as ex:
            crash, st["error"] = ex, "builder raised %s: %s" % (type(ex).__name__, ex)
        finally:
            if led is not None: led.bracket = None
        st["extra"] = {} if st["error"] is None else {"error": st["error"]}
        if st["rc"] is not None: st["extra"]["returncode"] = st["rc"]
        for gid, gate_fn in AGENT_POST_BUILDER_GATES:
            if crash is not None and gid not in AGENT_CRASH_GATES: continue
            stop = gate_fn(st)
            if stop: return stop
        if crash is not None: raise crash
        agent = st["agent"]
    return {"status": "needs-approval", "iteration": n}

def agent_scenario_hashes(agent):
    return {rel: file_sha256(p) for rel, p in agent.files([]) if rel.startswith("scenarios/")}

def agent_bound_files(pairs):
    """(sha256_labeled(pairs), {label: file_sha256}) from one read of each file, so the per-file values are the bytes
    the plan hash covered."""
    h, each = hashlib.sha256(), {}
    for label, p in pairs:
        h.update(label.encode() + b"\0")
        if os.path.isfile(p):
            with open(p, "rb") as f: data = f.read()
            h.update(data)
            each[label] = hashlib.sha256(data).hexdigest()
        else:
            h.update(b"<missing>")
            each[label] = None
        h.update(b"\0")
    return h.hexdigest(), each

def agent_drift_problems(agent, signed, old=None, names=()):
    """A scenario file, a signed file of the old agent or the ruleset that differs from the value bound by the
    signature, and a rule-review finding outside the set the signed decision covered."""
    now = agent_scenario_hashes(agent)
    out = ["scenario %s changed since signing" % rel for rel in sorted(set(now) | set(signed["scenarios"]))
           if now.get(rel) != signed["scenarios"].get(rel)]
    out += ["rule review finding changed since signing: %s" % x[:200] for x in rule_review_items(agent.dir)
            if x not in signed["review"]]
    if old is not None:
        was = signed["old_agent"]
        now_old = {rel: file_sha256(p) for rel, p in old.files(names)}
        out += ["old agent %s changed since signing" % rel for rel in sorted(set(now_old) | set(was))
                if now_old.get(rel) != was.get(rel)]
    try:
        moved = ruleset_sha256() != signed["rules"]
    except EvalError:
        moved = True
    return out + (["ruleset_sha256 changed since signing"] if moved else [])

def agent_loop_estimate(a, agent, old, engines, cells, prices, tokens, k):
    """k iterations of exec and grader calls, plus a builder call between two iterations."""
    from engine import agent_eval
    calls = a.runs * agent_configs(old)
    n_llm = sum(1 for c in cells if c["state"] == agent_eval.PLANNED and any(
        agent_eval.llm_items(agent.dir, s["data"]) for s in agent.scenarios if s["id"] == c["scenario"])) * calls
    rows = agent_estimate(engines, cells, a.runs, old, prices, tokens)["rows"] + \
        grader_estimate(n_llm, 0, a.grader_model, tokens, prices)["rows"][:1]
    est = scale_estimate(est_result(rows, tokens), k)
    if a.builder_prompt and k > 1:
        per = call_cost(tokens["builder"], price_for("builder", a.builder_model, prices))
        est = est_result(est["rows"] + [est_row("builder", "builder", a.builder_model, k - 1, tokens, per)], tokens)
    return est

def agent_grader_problems(a, engines):
    return grader_tier_problems(a.grader_model, [(e.name, e.model) for e in engines],
                                a.builder_model if _str_opt(a, "builder_prompt") else None)

def agent_loop_cli(a, agent, ws, prices, tokens):
    from engine import agent_eval
    if not 1 <= a.max_iterations <= MAX_AUTO_ITERATIONS:
        raise EvalError("--max-iterations must be 1..%d" % MAX_AUTO_ITERATIONS)
    engines = make_agent_engines(a)
    if a.runs < 1: raise EvalError("--runs must be >= 1")
    old = Agent(a.old_agent) if a.old_agent else None
    builder_text = read_builder_prompt(a) if a.builder_prompt else None
    cells = agent_cells(agent, engines)
    gates = ", ".join(gid for gid, _ in AGENT_POST_BUILDER_GATES)
    loop_lines = ["loop: max iterations %d (MAX_AUTO_ITERATIONS %d, policy/eval-policy.json)" % (
                      a.max_iterations, MAX_AUTO_ITERATIONS),
                  "builder model: %s; builder prompt: %s; writable set: %s (hook mode %s)" % (
                      a.builder_model, a.builder_prompt or "none (one iteration, no builder call)",
                      writable.MODES[writable.AGENT_MODE].shown, writable.AGENT_MODE),
                  "post-builder gates: " + gates]
    if not a.live:
        print(agent_plan_text(a, agent, old, ws, engines, cells, prices, tokens))
        print("\n".join(loop_lines))
        if plan_refused(agent, old): return 2
        print("\nDRY RUN: nothing was executed. A real loop needs `agent loop --live --auth-profile P --budget-usd X "
              "--builder-prompt FILE`, a clean `agent validate`, generated wrappers, " + AGENT_LIVE_APPROVALS + ", " +
              AGENT_GRADER_TIER + " and an estimate within budget; the start is recorded as an unsigned approval.")
        return 0
    agent_live_required(a)
    ge, names = grader_engine_of(a), [e.name for e in engines]

    def problems(left, k=a.max_iterations, now=agent, first=True):
        cells_now = agent_cells(now, engines)
        est = agent_loop_estimate(a, now, old, engines, cells_now, prices, tokens, k)
        out = agent_folder_problems(now.dir) + agent_run_problems(a, now, old, engines, cells_now, est, prices, tokens,
                                                                  left) + agent_gate_problems(now, ws, review=first)
        if api_key_mode(): out += api_key_soft_refusals([], ge, False, True, bool(builder_text))
        out += agent_grader_problems(a, engines)
        if builder_text is not None:
            try: same = read_builder_prompt(a) == builder_text
            except EvalError: same = False
            if not same: out.append("--builder-prompt %s changed since it was read; sign again" % a.builder_prompt)
        return out
    bound = {}

    def parts():
        agent_sha, bound["agent"] = agent_bound_files(agent.files(list(agent_eval.TOOLS)))
        old_sha, bound["old_agent"] = agent_bound_files(old.files(names)) if old else (None, {})
        return dict({"agent": agent_sha, "old_agent": old_sha,
                     "engines": {e.name: [e.model, e.effort] for e in engines}, "budget_usd": a.budget_usd,
                     "builder": [a.builder_model,
                                 hashlib.sha256(builder_text.encode("utf-8")).hexdigest() if builder_text else None],
                     "max_iterations": a.max_iterations, "grader": [ge, a.grader_model, _str_opt(a, "grader_effort")],
                     "approvals": agent_approval_inputs(agent, ws)}, **pin_parts(agent, old))
    budget = agent_live_start(a, agent, ws, "agent-loop", problems, parts, [
        "agent %s: loop of at most %d iteration(s), %d planned cell(s), runs %d, engines %s" % (
            agent.name, a.max_iterations, sum(1 for c in cells if c["state"] == agent_eval.PLANNED), a.runs,
            ", ".join(names))] + loop_lines[1:], "claude" in names or ge == "claude" or bool(builder_text))
    # bound holds the per-file values of the post-sign recheck, which matched the signature; the reload makes
    # iteration 1 run those scenarios, and the iteration-1 precheck refuses any later change. The builder may change
    # AGENT.md, a rule-review source, so only the first precheck reads the signed decision; every later one refuses a
    # finding outside the set that decision covered.
    signed = {"scenarios": {r: v for r, v in bound["agent"].items() if r.startswith("scenarios/")},
              "old_agent": bound["old_agent"], "rules": ruleset_sha256(), "review": rule_review_items(agent.dir)}
    agent = Agent(agent.dir)
    for e in engines:
        if budget_enforcement(e) == "soft_limit":
            print("WARNING: budget " + SOFT_LIMIT_NOTE % e.name, file=sys.stderr)
            print("WARNING: " + NO_STRICT_WARNING % e.name, file=sys.stderr)
    grader = make_grader(ge, a.grader_model, a.grader_effort, budget=budget, prices=prices)
    builder = AgentBuilder(builder_text, a.builder_model, budget, prices) if builder_text else None
    precheck = lambda now, left: agent_drift_problems(now, signed, old, names) + problems(
        budget.remaining(), left, now, first=left == a.max_iterations)
    r = agent_loop(agent, ws, engines, grader, builder, a.max_iterations, a.runs, old, budget, prices, tokens, precheck,
                   a.run_root)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r["status"] == "clean" else 3

def agent_dispatch(a):
    agent = Agent(a.agent)
    if a.agent_cmd == "validate": return agent_validate(agent)
    select_auth_profile(getattr(a, "auth_profile", None))
    ws = agent_workspace(a, agent)
    if a.agent_cmd == "report": return agent_report(a, agent, ws)
    prices, tokens = parse_price_overrides(a.price), parse_token_overrides(a.tokens)
    make_grader(grader_engine_of(a), a.grader_model, a.grader_effort)
    if a.agent_cmd == "grade": return agent_grade(a, agent, ws, prices, tokens)
    if a.agent_cmd == "loop": return agent_loop_cli(a, agent, ws, prices, tokens)
    return agent_run(a, agent, ws, prices, tokens)

# ---------- install, version bump, deprecate, drift (unit B6) ----------
INSTALL_POLICY = os.path.join(POLICY_DIR, "install.json")
Q12 = "user scope is allowed only for Claude agents (Appendix E, Q12)"
INSTALL_USER_GATE = "install_user"

def install_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def install_policy():
    from engine import install
    return install.load_policy(INSTALL_POLICY)

def lifecycle_dispatch(a):
    from engine import install, lifecycle
    try:
        if a.cmd == "install": return install_cmd(a)
        if a.cmd == "version": return version_bump(a)
        if a.cmd == "deprecate": return deprecate_cmd(a)
        return drift_cmd(a)
    except (install.InstallError, lifecycle.LifecycleError) as ex:
        raise EvalError(str(ex))

def load_unit(path, workspace=None):
    """(spec, frontmatter, workspace) of a skill or AGENT.md v2 folder: the workspace is --workspace, else
    <parent>/<name>-workspace."""
    import eva
    if eva.detect_kind(path) is None: raise EvalError("%s holds no SKILL.md or AGENT.md" % path)
    spec, meta = unit_spec(os.path.realpath(path))
    if not safe_name(spec["skill_name"]):
        raise EvalError("unit name %r is not a safe path component; fix the frontmatter name" % (spec["skill_name"],))
    ws = os.path.realpath(workspace) if workspace else \
        os.path.join(os.path.dirname(spec["skill_dir"]), spec["skill_name"] + "-workspace")
    return spec, meta, ws

def unit_kind(spec):
    return spec.get("kind", "skill")

def parse_install_tools(text):
    tools = [t.strip() for t in text.split(",") if t.strip()]
    from engine import install
    if not tools or len(set(tools)) != len(tools) or any(t not in install.TOOLS for t in tools):
        raise EvalError("--tools %r: name tools from %s, each at most once" % (text, ",".join(install.TOOLS)))
    return [t for t in install.TOOLS if t in tools]

def target_wrapper_rel(tool, name):
    return rjson(os.path.join(RULESET_ROOT, "targets", tool + ".json"))["wrapper"]["path"].format(name=name)

def install_items(spec, tools, places, user_dest=None):
    """[(tool, source path, destination, UNVERIFIED source or None)]: an agent's generated wrappers, refused when one
    is missing or out of date (install never generates), or every regular file of a skill folder. places: the
    policy destinations of the unit kind; user_dest: the absolute user-scope destination."""
    from engine import agent_eval, install
    d, name, out = spec["skill_dir"], spec["skill_name"], []
    if unit_kind(spec) == "agent":
        states = agent_eval.wrapper_states(d)
        bad = ["wrapper %s is %s" % (target_wrapper_rel(t, name), states[t]) for t in tools if states[t] is not None]
        if bad:
            raise EvalError("%s: run `python3 eva.py generate %s` first (install never generates); nothing was written"
                            % ("; ".join(bad), d))
        files = {t: [("", os.path.join(d, *target_wrapper_rel(t, name).split("/")))] for t in tools}
    else:
        listed = install.skill_files(d)
        files = {t: [(rel, p) for rel, p in listed] for t in tools}
    for t in tools:
        conf = places[t]
        base = user_dest or conf["path"].format(name=name)
        for rel, p in files[t]:
            out.append((t, p, base + rel, conf["source"] if conf["verified"] == "UNVERIFIED" else None))
    return out

def deprecation_notes(spec, meta, ws):
    from engine import lifecycle
    version = registry.version_of(meta)
    try: data = rjson(os.path.join(ws, "deprecations.json"))
    except ValueError: data = None
    reason = lifecycle.deprecated_reason(data, version) if isinstance(version, str) else None
    return ["warning: %s %s is deprecated: %s" % (spec["skill_name"], version, reason)] if reason else []

def install_cmd(a):
    from engine import install
    spec, meta, ws = load_unit(a.unit, a.workspace)
    tools = parse_install_tools(a.tools)
    if a.scope == "user": return install_user_scope(a, spec, meta, ws, tools)
    if not a.target: raise EvalError("a project install needs --target <folder>; nothing was written")
    target = os.path.realpath(a.target)
    if not os.path.isdir(target): raise EvalError("--target %s is not a folder; nothing was written" % a.target)
    pol, homes = install_policy(), install.homes()
    probs = install.target_problems(target, homes, pol)
    if probs: raise EvalError("; ".join(probs) + "; nothing was written")
    items = install_items(spec, tools, pol.destinations[unit_kind(spec)])
    plan = install.build(spec["skill_name"], unit_kind(spec), "project", target, items, pol, homes, a.force,
                         install.project_check(target, pol, homes))
    notes = [install.HERMES_NOTE] if unit_kind(spec) == "agent" and "hermes" in tools else []
    return install_finish(plan, pol, a.dry_run, notes + deprecation_notes(spec, meta, ws))

def install_finish(plan, pol, dry_run, notes):
    from engine import install
    lines = install.entry_lines(plan) + (install.diff_lines(plan) if dry_run else []) + notes
    if not dry_run:
        backup = install.apply(plan, pol, install_stamp())
        if backup: lines.append("backup %s" % backup)
    if all(e.action == "unchanged" for e in plan.entries): lines.append("no change")
    print("\n".join(lines + [install.summary(plan, plan.unit, dry_run)]))
    return 0

def install_user_scope(a, spec, meta, ws, tools):
    """Only `--scope user --tools claude` on an agent, into exactly $HOME/.claude/agents/<name>.md, after a
    signature of the manifest hash (engine/install.py manifest) taken once the record reads registered now and the
    wrapper is fresh."""
    from engine import install
    if unit_kind(spec) != "agent": raise EvalError("%s: a skill is refused; nothing was written" % Q12)
    if tools != ["claude"]:
        raise EvalError("%s: %s refused; pass --tools claude; nothing was written"
                        % (Q12, ", ".join(t for t in tools if t != "claude") or "claude"))
    if a.target: raise EvalError("--target is for a project install; user scope writes only $HOME/.claude/agents")
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        raise EvalError("CLAUDE_CONFIG_DIR is set, so Claude Code may not read $HOME/.claude/agents; user scope "
                        "refuses (unset it); nothing was written")
    pol, homes = install_policy(), install.homes()
    home = os.path.realpath(os.path.expanduser("~"))
    dest = os.path.join(home, *install.USER_AGENT_PATH.format(name=spec["skill_name"]).split("/"))

    def planned():
        state, reason, rec = registration_state(spec["skill_dir"], ws)
        if state != "registered":
            raise EvalError("user scope needs a record that reads registered now: %s reads %s%s; nothing was signed"
                            % (ws, state, " (%s)" % reason if reason else ""))
        items = install_items(spec, tools, pol.user_scope["agent"], dest)
        plan = install.build(spec["skill_name"], "agent", "user", home, items, pol, homes, a.force,
                             install.user_check(home, pol, homes))
        return plan, rec, install.manifest_hash(install.manifest(plan, registration_hash(rec)))

    if a.dry_run:
        try:
            plan, rec, _ = planned()
        except (EvalError, install.InstallError) as ex:
            print("prerequisites: %s" % ex)
            return 2
        print("registration: registered %s (by %s)" % (rec["version"], approvals.decided_by(rec["signed"])))
        print("wrapper: claude up to date; nothing is signed or written in a dry run")
        return install_finish(plan, pol, True, [])
    try:
        plan, _, _ = planned()
        if all(e.action == "unchanged" for e in plan.entries): return install_finish(plan, pol, False, [])
    except (EvalError, install.InstallError):
        pass
    st = {}
    def prepare():
        plan, rec, h = planned()
        rules = ruleset_sha256()
        st.update(hash=h)
        return approvals.Prepared(spec["skill_name"], h, rules, [
            "install user scope: %s" % e.dest for e in plan.entries] + [
            "install source sha256 %s (%s)" % (hashlib.sha256(e.data).hexdigest(), e.source) for e in plan.entries] + [
            "install record: %s %s registered by %s" % (spec["skill_name"], rec["version"],
                                                       approvals.decided_by(rec["signed"])),
            "ruleset_sha256 " + rules])
    grant = APPROVER["it"].sign("approve", INSTALL_USER_GATE, prepare)
    plan, _, h = planned()
    if h != grant.sha256:
        raise EvalError("the install inputs changed after signing (a wrapper, the record or the destination); nothing "
                        "was written, sign again")
    store_approval(ws, INSTALL_USER_GATE, grant.record)
    return install_finish(plan, pol, False, deprecation_notes(spec, meta, ws))

def version_bump(a):
    import eva
    from engine import lifecycle
    spec, meta, _ = load_unit(a.unit)
    main = "AGENT.md" if unit_kind(spec) == "agent" else "SKILL.md"
    old = registry.version_of(meta)
    if registry.parse_semver(old) is None:
        raise EvalError("%s %s; set a SemVer 2.0.0 version such as 1.0.0 first" % (
            main, "has no version" if old in ("", None) else "version %r is not SemVer 2.0.0" % (old,)))
    new = lifecycle.next_version(old, a.part)
    text, _ = eva.read_text(os.path.join(spec["skill_dir"], "CHANGELOG.md"))
    problem = lifecycle.changelog_problem(text, new)
    if problem:
        raise EvalError('version bump needs a changelog entry: add "## %s" and a line of text to %s (%s); nothing was '
                        'written' % (new, os.path.join(a.unit, "CHANGELOG.md"), problem))
    path = os.path.join(spec["skill_dir"], main)
    with open(path, encoding="utf-8", newline="") as f: raw = f.read()
    out = lifecycle.set_version(raw, unit_kind(spec), new,
                                lambda t: registry.version_of(eva.parse_frontmatter(t)[0] or {}))
    if a.dry_run:
        print("dry run: would bump %s %s -> %s" % (spec["skill_name"], old, new))
        return 0
    with open(path, "w", encoding="utf-8", newline="") as f: f.write(out)
    if unit_kind(spec) == "agent":
        print("the wrappers embed the AGENT.md digest: run `python3 eva.py generate %s` before the next install" % a.unit)
    print("bumped %s %s -> %s" % (spec["skill_name"], old, new))
    return 0

def deprecate_cmd(a):
    from engine import lifecycle
    spec, meta, ws = load_unit(a.unit, a.workspace)
    p = os.path.join(ws, "deprecations.json")
    try: data = rjson(p)
    except ValueError as ex: raise EvalError("%s is not readable JSON (%s); nothing was written" % (p, ex))
    new, changed = lifecycle.deprecate(data, a.version, a.reason, registry.version_of(meta), now_iso())
    if not changed:
        print("no change")
        return 0
    wjson(p, new)
    print("deprecated %s %s: %s (an unsigned marker; the version stays installable)" % (spec["skill_name"], a.version,
                                                                                        a.reason))
    return 0

def recorded_ruleset(unit_dir, name):
    """The ruleset_sha256 a unit was last recorded under: its registration.json, else the metadata of its last
    iteration's benchmark.json, else None (workspace <parent>/<name>-workspace). Reads those two files only."""
    if not safe_name(name): return None
    ws = os.path.join(os.path.dirname(unit_dir), name + "-workspace")
    n = (iterations(ws) or [None])[-1]
    for p, pick in ((os.path.join(ws, "registration.json"), lambda d: d),
                    (os.path.join(ws, "iteration-%s" % n, "benchmark.json"), lambda d: d.get("metadata"))):
        try: doc = rjson(p)
        except (ValueError, RecursionError): doc = None
        got = pick(doc) if isinstance(doc, dict) else None
        if isinstance(got, dict) and isinstance(got.get("ruleset_sha256"), str): return got["ruleset_sha256"]
    return None

def rule_dates():
    """[(rules/<file>, rule id, source.verified)] of every rule of the bundle under RULESET_ROOT."""
    out, d = [], os.path.join(RULESET_ROOT, "rules")
    for f in sorted(x for x in os.listdir(d) if x.endswith(".rules.json")):
        doc = rjson(os.path.join(d, f))
        for r in doc.get("rules") or [] if isinstance(doc, dict) else []:
            src = r.get("source") if isinstance(r, dict) and isinstance(r.get("source"), dict) else {}
            out.append(("rules/" + f, r.get("id") if isinstance(r, dict) else None, src.get("verified")))
    return out

def drift_cmd(a):
    """Tools, units and rules, read from files only: drift starts no process and opens no socket."""
    import eva
    from engine import lifecycle
    if not os.path.isdir(a.root): raise EvalError("drift ROOT %s is not a folder" % a.root)
    observed = {}
    for item in a.observed:
        tool, _, version = item.partition("=")
        if tool not in lifecycle.TOOLS or not version.strip() or tool in observed:
            raise EvalError("--observed %r: give TOOL=VERSION once per tool, TOOL one of %s"
                            % (item, ", ".join(lifecycle.TOOLS)))
        observed[tool] = version.strip()
    today = a.today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    units = []
    for u in eva.find_units(a.root):
        spec, _ = unit_spec(u)
        units.append((unit_kind(spec), spec["skill_name"], recorded_ruleset(u, spec["skill_name"])))
    lines, listed = lifecycle.drift_report(install_policy().tool_pins, observed, units, ruleset_sha256(), rule_dates(),
                                           today, RULE_VERIFIED_MAX_DAYS)
    print("\n".join(lines))
    return 3 if listed else 0

# ---------- CLI ----------
def make_engines(a, spec=None):
    out = []
    for name in a.engines.split(","):
        name = name.strip()
        if name in UNAVAILABLE_ENGINES:
            raise EvalError("engine %r (%s) is not available: no %s engine exists yet, so nothing could be run or graded "
                            "for it (never faked). Available engines: %s" % (name, UNAVAILABLE_ENGINES[name],
                                                                           UNAVAILABLE_ENGINES[name], "|".join(ENGINES)))
        if name not in ENGINES: raise EvalError("unknown engine %r (%s)" % (name, "|".join(ENGINES)))
        model = a.claude_model if name == "claude" else a.codex_model
        extra = a.claude_arg if name == "claude" else a.codex_arg
        effort = _str_opt(a, "claude_effort" if name == "claude" else "codex_effort")
        kw = {"python_script": spec.get("claude_python_script"), "skill_dir": spec["skill_dir"]} if name == "claude" and spec else {}
        out.append(ENGINES[name](model=model, extra_args=extra or (), effort=effort, **kw))
    return out

def _str_opt(a, k):
    v = getattr(a, k, None)
    return v if isinstance(v, str) else None

def grader_engine_of(a):
    v = getattr(a, "grader_engine", None)
    return v if v in GRADER_ENGINES else "claude"

LIVE_GATES_HEADER = "live gates (policy/gates.json, checked as for run --live, nothing executed):"

def _memo_verdicts():
    """approval_verdict with one read per gate, so plan starts ssh-keygen at most once per signed record."""
    seen = {}
    def verdict_of(gate, spec, ws):
        if gate not in seen: seen[gate] = approval_verdict(gate, spec, ws)
        return seen[gate]
    return verdict_of

def plan_text(spec, problems, a, engines, ws, est, tokens=None, prices=None):
    configs = configs_for(a.baseline)
    layers = {l: sum(1 for c in spec["cases"] for it in c["items"] if it["layer"] == l) for l in LAYERS}
    trig = spec["triggers"] or []
    L = ["skill: %s (%s)" % (spec["skill_name"], spec["skill_dir"]), "workspace: %s" % ws,
         "configurations: %s | runs: %d | engines: %s" % (", ".join(configs), a.runs,
             ", ".join(e.label() for e in engines)),
         "grader: %s (yes/no rubric) + blind pairwise judge" % grader_label(
             grader_engine_of(a), a.grader_model, _str_opt(a, "grader_effort")),
         "eval set: %d case(s), items static=%d llm=%d human=%d, pressure cases=%d; trigger queries: %d (+%d / -%d)" % (
             len(spec["cases"]), layers["static"], layers["llm"], layers["human"],
             sum(1 for c in spec["cases"] if c["pressure"]), len(trig), sum(q["should_trigger"] for q in trig),
             sum(not q["should_trigger"] for q in trig))]
    L += ["validation problems (%d):" % len(problems)] + ["  - " + p for p in problems]
    import eva
    verdict_of = _memo_verdicts()
    rr = verdict_of("rule_review", spec, ws) if rule_review_items(spec["skill_dir"]) else None
    L += ["rule review (English-only / parity needs-review findings): " + (
              "none" if rr is None else "RECORDED (by %s)" % rr.principal if rr.status == "approved" else
              "NOT RECORDED (%sblocks --live and the loop)" % ("" if rr.status == "missing" else rr.status + "; ")),
          "multi-tool parity (static, eva.py): " + eva.parity_line(eva.parity_status(spec["skill_dir"])),
          "tool coverage (dynamic): " + coverage_line(tool_coverage([e.name for e in engines])),
          "registration coverage: registration needs a graded dynamic run on every tool of coverage_required (%s) or a "
          "recorded coverage_waiver:<tool> with a reason for each tool that did not run; waivers: %s" % (
              ", ".join(COVERAGE_REQUIRED), ", ".join("%s=%s" % (t, approval_label(verdict_of(
                  approvals.WAIVER_PREFIX + t, spec, ws))) for t in COVERAGE_REQUIRED))]
    L += [machine_waiver_line(machine_waiver()),
          "combined approval gate: %s=%s (the live gate needs it, or both requirements and eval_set)" % (
              COMBINED_GATE, approval_label(verdict_of(COMBINED_GATE, spec, ws))),
          "approval gates: requirements=%s, eval_set=%s" % (
              approval_label(verdict_of("requirements", spec, ws)), approval_label(verdict_of("eval_set", spec, ws)))]
    pf = preflight(spec, [e.name for e in engines], a.run_root)
    dangling = spend.preview(ws)
    L += ["ruleset " + ruleset_sha256(), LIVE_GATES_HEADER]
    declared = gate_declaration_problem()
    if declared: L.append("gate declaration mismatch (check_live_gates and loop refuse): " + declared)
    states = ([(gid, "fail: " + declared.splitlines()[0]) for gid, _ in LIVE_GATES] if declared else
              live_gate_states(a, spec, problems, ws, engines, DEFAULT_TOKENS if tokens is None else tokens,
                               prices or {}, pf, verdict_of, sum(r["bound_usd"] or 0.0 for r in dangling)))
    L += ["  %d. %s: %s" % (k, gid, state) for k, (gid, state) in enumerate(states, 1)]
    L += ["post-builder gates (loop): " + ", ".join(gid for gid, _ in POST_BUILDER_GATES)]
    L += ["preflight problems (block --live):"] + ["  - " + w for w in pf]
    names = [e.name for e in engines]
    notes = claude_shared_skill_notes(claude_config_dir()) if "claude" in names else []
    if notes: L += ["isolation notes (not blocking):"] + ["  - " + x for x in notes]
    p = AUTH["profile"]
    if spend.preview_line(dangling): L.append(spend.preview_line(dangling))
    if p is not None:
        L.append(PERSONAL_DEFAULT_LINE if getattr(a, "auth_profile", None) is None else
                 "auth profile: %s (billing %s)" % (p.name, p.billing))
    if api_key_mode():
        L += ["auth: API-key billing (auth profile %s): %s; gateways: %s; no `claude auth status` or ChatGPT login check; "
              "key and gateway values are never shown" % (p.name, "; ".join(
                  "%s reads %s" % (e, ", ".join(x["api_key_env"]) or "no key (refused)") for e, x in sorted(p.engines.items())),
                  ", ".join(sorted(v for x in p.engines.values() for v in x["gateways"])) or "none listed")]
    else:
        L += ["auth: subscription only (Claude: existing claude.ai Keychain login; Codex: ChatGPT login); `claude auth "
              "status --json` is checked only by a real --live (not by --dry-run)"]
    soft = [e.name for e in engines if budget_enforcement(e) == "soft_limit"]
    if api_key_mode():
        refused = api_key_soft_refusals(engines, grader_engine_of(a), True, True, builds_now(a))
        L += ["budget enforcement: " + (" | ".join(refused) if refused else
              "HARD CAP (auth profile %s): a call starts only when the spend so far plus its per-call cap fits "
              "--budget-usd" % p.name)]
    elif soft:
        L += ["budget enforcement: %s" % ("STRICT (default): --live refuses %s (no per-call cap)" % ", ".join(soft)
              if getattr(a, "strict_budget", True) else "WARNING " + NO_STRICT_WARNING % ", ".join(soft))]
    if spec["cases"]:
        c = spec["cases"][0]
        L.append("sample commands (eval %s, run in <workspace>/iteration-N/<engine>/%s/<config>/run-k/workdir):" % (c["id"], eval_dir_name(c)))
        for e in engines:
            for cfg in configs:
                cmd = e.command(full_prompt(c)[:60] + "…", bool(skill_path_for(cfg, spec, a.old_skill or "<old>")),
                                skill_name=spec["skill_name"])
                L.append("  [%s %s] %s" % (e.name, cfg, " ".join(json.dumps(x, ensure_ascii=False) if " " in x else x for x in cmd)))
    L += ["cost estimate:", format_estimate(est)]
    return "\n".join(L)

def live_estimate(a, spec, ws, n, engines, tokens, prices, iterations_left=None):
    """Cost of what a live command will really execute; never uses --cases/--llm-cases/--trigger-queries.
    loop: iterations_left x (one iteration + calibration) + (iterations_left - 1) builder calls."""
    if a.cmd == "grade":
        check_scope(spec, grade_scope(a))
        ng, npw = grade_counts(spec, os.path.join(ws, "iteration-%d" % n), grade_scope(a))
        return grader_estimate(ng, npw, a.grader_model, tokens, prices)
    if a.cmd == "calibrate":
        return grader_estimate(len(load_golden(a.golden)), 0, a.grader_model, tokens, prices)
    n_cases, n_llm, n_trig = spec_counts(spec, None)
    est = estimate(n_cases, n_llm, n_trig, a.runs, [(e.name, e.model) for e in engines], a.baseline, a.grader_model,
                   tokens, prices)
    if a.cmd == "run":
        return est_result([r for r in est["rows"] if r["kind"] in ("exec", "trigger")], tokens)
    if a.cmd == "loop":  # every automatic iteration may run, each with its calibration, plus a builder call between
        k = a.max_iterations if iterations_left is None else iterations_left
        cal = grader_estimate(len(load_golden(a.golden)), 0, a.grader_model, tokens, prices)["rows"][:1] if a.golden else []
        est = scale_estimate(est, k, cal)
        if getattr(a, "builder_prompt", None) and k > 1:
            per = call_cost(tokens["builder"], price_for("builder", a.builder_model, prices))
            est = est_result(est["rows"] + [est_row("builder", "builder", a.builder_model, k - 1, tokens, per)], tokens)
    return est

def read_builder_prompt(a):
    try:
        with open(a.builder_prompt, encoding="utf-8") as f: text = f.read()
    except (OSError, UnicodeDecodeError) as ex:
        raise EvalError("--builder-prompt: %s" % ex)
    if not text.strip(): raise EvalError("--builder-prompt %s is empty" % a.builder_prompt)
    return text

def subscription_problems(a, engines):
    """Live gate: no API-key variable anywhere (run/loop also get it from preflight), and `claude auth status` shows a
    claude.ai login whenever the command calls claude (Claude engine, grader for grade/calibrate/loop, builder). API-key
    mode: the profile's env rules and a listed key for every Claude role, for every command; no subscription check."""
    if api_key_mode(): return api_key_env_problems() + required_key_problems(a, engines)
    out = [] if a.cmd in ("run", "loop") else api_key_env_problems()  # run/loop: reported by preflight
    if needs_claude_auth(a, engines):
        out += claude_auth_problems()
    return out

def required_key_problems(a, engines):
    p = AUTH["profile"]
    listed = p.engines["claude"]["api_key_env"]
    if not needs_claude_auth(a, engines) or any(k in os.environ for k in listed): return []
    return ["claude: auth profile %s needs one of %s set (value not shown)" % (p.name, ", ".join(listed) or "<none listed>")]

def api_key_soft_refusals(engines, grader_engine, runs_engines, grades, builds=False):
    """API-key mode: the spend.api_key_refusal of every call the command makes (the engines under test, the grader,
    the builder): a Claude call (plan.md:902), and an engine without a per-call cap (D5). The calls without a per-call
    cap come first, so the D5 refusal stays the first one where it was."""
    calls = ([("exec", e.name, e.has_call_cap) for e in engines] if runs_engines else []) \
        + ([("grader", grader_engine, True)] if grades else []) \
        + ([("builder", "claude", True)] if builds else [])
    out = [spend.api_key_refusal(kind, engine, AUTH["profile"].name, cap)
           for kind, engine, cap in sorted(calls, key=lambda c: c[2])]
    return [x for x in out if x]

def builds_now(a):
    return a.cmd == "loop" and bool(_str_opt(a, "builder_prompt"))

def grades_now(a):
    return a.cmd in ("grade", "calibrate", "loop")

def needs_claude_auth(a, engines):
    """Claude is called by a Claude engine (run/loop only: grade/calibrate execute no engine), a Claude grader
    (grade/calibrate/loop) or the builder (loop)."""
    runs_engines = a.cmd not in ("grade", "calibrate")
    return (runs_engines and any(e.name == "claude" for e in engines)) \
        or (grades_now(a) and grader_engine_of(a) == "claude") \
        or (a.cmd == "loop" and bool(_str_opt(a, "builder_prompt")))

AUTH_STATUS_SKIPPED = "skipped in dry run: `claude auth status --json` (runs a CLI; only the real --live checks it)"

class _LiveGates(object):
    """What the live gate functions read. report: a list collects every failure instead of raising (dry runs, plan).
    preflight() runs preflight once; verdict_of(gate, spec, ws) reads one approval verdict (plan passes a memo)."""
    def __init__(self, a, spec, problems, ws, n, engines, tokens, prices, budget_left, iterations_left, report,
                 verdict_of=None, pf=None):
        self.a, self.spec, self.problems, self.ws, self.n = a, spec, problems, ws, n
        self.engines, self.tokens, self.prices = engines, tokens, prices
        self.budget_left, self.iterations_left, self.report = budget_left, iterations_left, report
        self.verdict_of, self.pf, self.live_est = verdict_of or approval_verdict, pf, None

    def fail(self, msg):
        if self.report is None: raise EvalError(msg)
        self.report.append(msg)

    def preflight(self):
        if self.pf is None: self.pf = preflight(self.spec, [e.name for e in self.engines], self.a.run_root)
        return self.pf

def _live_eval_set(g):
    if g.problems: g.fail("eval set has %d validation problem(s); run `validate`" % len(g.problems))

def _live_strict_budget(g):
    if api_key_mode():  # --no-strict-budget does not reopen a soft engine here
        for x in api_key_soft_refusals(g.engines, grader_engine_of(g.a), g.a.cmd in ("run", "loop"), grades_now(g.a),
                                       builds_now(g.a)):
            g.fail(x)
        return
    soft = [e.name for e in g.engines if budget_enforcement(e) == "soft_limit"] if g.a.cmd in ("run", "loop") else []
    if getattr(g.a, "strict_budget", True) and soft:
        g.fail("--strict-budget (default): %s has no enforceable per-call budget cap (its budget is only a soft limit); "
               "refusing the live run. Drop it from --engines. --no-strict-budget is risky and overrides only this gate. "
               "%s" % (", ".join(soft), NO_STRICT_TIER))

def _live_auth(g):
    a = g.a
    if api_key_mode():
        auth = subscription_problems(a, g.engines)
        if auth: g.fail("auth profile %s: %s" % (AUTH["profile"].name, " | ".join(auth)))
        return
    if g.report is None:
        auth = subscription_problems(a, g.engines)
        if auth: g.fail("subscription auth: " + " | ".join(auth))
        return
    env = [] if a.cmd in ("run", "loop") else api_key_env_problems()  # run/loop: reported by preflight
    if env: g.fail("subscription auth: " + " | ".join(env))
    if needs_claude_auth(a, g.engines):
        g.report.append(AUTH_STATUS_SKIPPED)

def _live_grader_tier(g):
    a = g.a
    if a.cmd == "grade":
        try:
            check_grader_model(os.path.join(g.ws, "iteration-%d" % g.n), a.grader_model)
        except EvalError as ex:
            g.fail(str(ex))
        return
    under_test = [(e.name, e.model) for e in g.engines] if a.cmd in ("run", "loop") else []
    builder = a.builder_model if _str_opt(a, "builder_prompt") else None
    for x in grader_tier_problems(a.grader_model, under_test, builder): g.fail(x)

def _live_rule_review(g):
    for x in rule_review_problems(g.spec, g.ws, g.verdict_of): g.fail(x)

def _live_approvals(g):
    for gate, v in unapproved_live_gates(g.spec, g.ws, g.verdict_of):
        g.fail("gate %s not approved%s (write %s/requirements.md, then `approve --gate %s` at a terminal)"
               % (gate, "" if v.status == "missing" else " (%s)" % v.status, g.ws, gate))

def _live_priced(g):
    g.live_est = live_estimate(g.a, g.spec, g.ws, g.n, g.engines, g.tokens, g.prices, g.iterations_left)
    if g.live_est["unpriced_models"]: g.fail("no price for %s; pass --price" % g.live_est["unpriced_models"])

def _live_preflight(g):
    if g.a.cmd in ("run", "loop"):
        pf = g.preflight()
        if pf: g.fail("preflight: " + " | ".join(pf))

def _live_call_bound(g):
    if g.a.cmd not in ("run", "loop"): return
    for e in g.engines:
        if e.has_call_cap: continue
        for kind in ("exec", "trigger"):
            b = call_bound(e.name, e.model, kind, g.prices, g.tokens)
            if b is not None and b > g.budget_left:
                g.fail("%s worst-case %s call bound $%.2f exceeds budget $%.2f (%s budget is a soft "
                       "limit: no per-call cap)" % (e.name, kind, b, g.budget_left, e.name))

def _live_estimate(g):
    est = g.live_est
    if est["total_usd"] > g.budget_left:
        g.fail("estimate $%.2f exceeds budget $%.2f\n%s" % (est["total_usd"], g.budget_left, format_estimate(est)))

# The order policy/gates.json declares (live); gate_declaration_problem() compares the two.
LIVE_GATES = (("eval-set-valid", _live_eval_set), ("strict-budget", _live_strict_budget),
              ("subscription-auth", _live_auth), ("grader-tier-distinct", _live_grader_tier),
              ("rule-review", _live_rule_review),
              ("approvals", _live_approvals), ("priced-models", _live_priced), ("preflight", _live_preflight),
              ("per-call-bound", _live_call_bound), ("estimate-within-budget", _live_estimate))
BUDGET_GATES = ("per-call-bound", "estimate-within-budget")
NO_BUDGET = "--live needs a finite --budget-usd > 0"

def gate_declaration_problem():
    """None when policy/gates.json under RULESET_ROOT declares exactly the ids of LIVE_GATES and POST_BUILDER_GATES, in
    their order; else one line naming the difference, or why the file cannot be read."""
    from engine import schema
    where = "policy/gates.json"
    try:
        doc = schema.load(os.path.join(RULESET_ROOT, "policy", "gates.json"))
    except schema.JsonError as e:
        return "%s: %s" % (where, e)
    for key, table in (("live", LIVE_GATES), ("post_builder", POST_BUILDER_GATES)):
        entries = doc.get(key) if isinstance(doc, dict) else None
        if not (isinstance(entries, list) and all(isinstance(e, dict) and set(e) == {"id", "label"}
                                                  and isinstance(e["id"], str) and isinstance(e["label"], str)
                                                  for e in entries)):
            return "%s: %s must be a list of {id, label} objects" % (where, key)
        ids, code = [e["id"] for e in entries], [gid for gid, _ in table]
        if ids != code:
            return "%s %s gates [%s] differ from the order the code runs [%s]" % (where, key, ", ".join(ids),
                                                                                 ", ".join(code))
    return None

def check_live_gates(a, spec, problems, ws, n, engines, tokens, prices, budget_left, iterations_left=None, report=None):
    """Every --live gate, in the order of LIVE_GATES; raises EvalError on the first failure, and before any gate when
    policy/gates.json declares another list (also with report). `loop` calls it again before each iteration with the
    reloaded eval set, the remaining iterations and the remaining budget. report: a list for --dry-run: every failure
    is appended instead of raised, and `claude auth status` is not executed (AUTH_STATUS_SKIPPED is appended)."""
    declared = gate_declaration_problem()
    if declared: raise EvalError(declared)
    g = _LiveGates(a, spec, problems, ws, n, engines, tokens, prices, budget_left, iterations_left, report)
    for _, gate in LIVE_GATES: gate(g)
    return g.live_est

def live_gate_states(a, spec, problems, ws, engines, tokens, prices, pf, verdict_of, spent=0.0):
    """[(id, state)] of LIVE_GATES for this command checked as `run --live` in report mode, starting no process: pass,
    fail: <first line of the gate's first failure>, or skipped: <the check only a real --live makes>. Without a finite
    --budget-usd > 0 the two budget gates fail as the live start would refuse. spent: the dangling bounds of the
    workspace ledger, which the next live start counts against the budget."""
    budget = getattr(a, "budget_usd", None)
    priced = budget is not None and math.isfinite(budget) and budget > 0
    g = _LiveGates(argparse.Namespace(**dict(vars(a), cmd="run")), spec, problems, ws, None, engines, tokens, prices,
                   budget - spent if priced else budget, None, [], verdict_of, pf)
    out = []
    for gid, gate in LIVE_GATES:
        if gid in BUDGET_GATES and not priced:
            out.append((gid, "fail: " + (CAP_REQUIRED % AUTH["profile"].name if api_key_mode() else NO_BUDGET)))
            continue
        start = len(g.report)
        try:
            gate(g)
        except Exception as ex:
            out.append((gid, "fail: " + (str(ex).splitlines() or [type(ex).__name__])[0]))
            continue
        said = g.report[start:]
        fails = [m for m in said if m != AUTH_STATUS_SKIPPED]
        out.append((gid, "fail: " + fails[0].splitlines()[0] if fails else "skipped: " + said[0] if said else "pass"))
    return out

def live_dry_run(a, spec, problems, ws, n, engines, tokens, prices, budget_left=None):
    """`--live --dry-run`: the plan of this exact command plus every live gate it would face, collected instead of
    raised. Nothing is executed: live mode stays off, no CLI (not even `claude auth status`) and no model is called,
    no workspace file is written. Returns 0 when only the skipped auth status check is left, else 3. budget_left: the
    budget minus the dangling bounds of the workspace ledger."""
    report = []
    live_est = check_live_gates(a, spec, problems, ws, n, engines, tokens, prices,
                                a.budget_usd if budget_left is None else budget_left, report=report)
    fails = [r for r in report if r != AUTH_STATUS_SKIPPED]
    print(plan_text(spec, problems, a, engines, ws, live_est, tokens, prices))
    print("\nLIVE DRY RUN: nothing was executed (no model call, no `claude auth status`, no files written).")
    left = a.budget_usd if budget_left is None else budget_left
    print("budget: API-equivalent estimate $%.2f vs %s (%s)" % (
        live_est["total_usd"], "--budget-usd $%.2f" % a.budget_usd if left == a.budget_usd else
        "$%.2f left of --budget-usd $%.2f after the dangling intents" % (left, a.budget_usd),
        "fits" if live_est["total_usd"] <= left else "EXCEEDS"))
    print("live gates that would refuse this command (%d):" % len(fails))
    for r in fails: print("  - " + r.replace("\n", "\n    "))
    if AUTH_STATUS_SKIPPED in report: print("  (%s)" % AUTH_STATUS_SKIPPED)
    if any(e.name == "claude" for e in engines) or getattr(a, "grader_engine", None) == "claude":
        info, bs = claude_builtin_plugins(), claude_bundled_skills()
        print("claude --settings (executor launches; grader/builder use separate settings): %s"
              % claude_settings_json(spec["skill_name"], next((e.bash_gate() for e in engines
                  if isinstance(e, ClaudeCodeEngine) and e.python_script), None)))
        print("  built-in plugins disabled: %d (binary %s: %s)" % (len(info["ids"]), info["binary"],
              info["error"] or "%d cc-plugin-* names found" % len(info["scanned"])))
        print("  bundled skills set off: %d, never %r (binary scan: %s); kill-switch survivors: %s; child env %s" % (
            len([n for n in bs["names"] if n != spec["skill_name"]]), spec["skill_name"],
            bs["error"] or "%d registerBundledSkill names found" % len(bs["scanned"]), ", ".join(bs["survivors"]),
            " ".join("%s=%s" % kv for kv in sorted(CLAUDE_CHILD_ENV.items()))))
        for e in engines:
            if isinstance(e, ClaudeCodeEngine) and e.script_allow_rule(spec["skill_name"]):
                print("claude --permission-mode (with_skill and without_skill): default")
                print("claude --allowedTools (with_skill and without_skill): Edit(/**),%s"
                      % e.script_allow_rule(spec["skill_name"]))
                print("claude --disallowedTools (with_skill and without_skill): Monitor PowerShell")
    print("live command: " + shlex.join(["python3", "skill_eval.py"] + [x for x in a.argv if x != "--dry-run"]))
    return 3 if fails else 0

def live_plan_hash(a, spec, ws, builder_text=None):
    """What a --live signature covers next to the budget: the exact command line, the workspace, the skill folder bytes,
    the approved inputs, the bytes of every file the command reads through its options, and of the auth profiles."""
    golden, old = getattr(a, "golden", None), getattr(a, "old_skill", None)
    obj = {"format": 1, "argv": a.argv, "workspace": ws, "skill_sha256": skill_tree_hash(spec["skill_dir"]),
           "eval_set_sha256": gate_hash("eval_set", spec, ws), "requirements_sha256": gate_hash("requirements", spec, ws),
           "builder_prompt_sha256": hashlib.sha256(builder_text.encode("utf-8")).hexdigest() if builder_text else None,
           "golden_sha256": sha256_files(golden) if golden else None,
           "old_skill_sha256": skill_tree_hash(old) if old and os.path.isdir(old) else None,
           "auth_profiles_sha256": file_sha256(os.path.join(RULESET_ROOT, "policy", AUTH_PROFILES_FILE))}
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()

def spec_counts(spec, a):
    """Counts from the eval set; a (argparse namespace) may override them for estimate/plan only (a=None: no overrides)."""
    o = lambda k: getattr(a, k, None) if a is not None else None
    n_cases = o("cases") if o("cases") is not None else len(spec["cases"])
    n_llm = o("llm_cases") if o("llm_cases") is not None else sum(1 for c in spec["cases"] if any(i["layer"] == "llm" for i in c["items"]))
    n_trig = o("trigger_queries") if o("trigger_queries") is not None else len(spec["triggers"] or [])
    return n_cases, n_llm, n_trig

# ---------- pending, approve --batch, approve-ui (unit S1, methods 1 and 5) ----------
def resolve_under(base, path):
    return path if os.path.isabs(path) else os.path.join(base, path)

def unit_pairs(units, workspaces, cwd=None):
    """[(unit folder, workspace)] of repeated --unit DIR with --workspace WS given once per --unit in the same order, or
    not at all (each unit's default workspace). A workspace does not record its unit folder before a run, so units are
    named explicitly (S1 D6). Relative paths resolve under cwd (the launch script passes the caller's folder)."""
    import eva
    if not units: raise EvalError("give one or more --unit DIR")
    if workspaces and len(workspaces) != len(units):
        raise EvalError("give --workspace once per --unit, in the same order, or not at all")
    base, out = cwd or os.getcwd(), []
    for i, u in enumerate(units):
        d = os.path.abspath(resolve_under(base, u))
        if workspaces: ws = os.path.realpath(resolve_under(base, workspaces[i]))
        elif eva.detect_kind(d) == "agent": ws = agent_workspace(argparse.Namespace(workspace=None), Agent(d))
        else: ws = default_workspace(load_eval_set(d)[0])
        out.append((d, ws))
    return out

def unit_loader(item):
    def load():
        if item["kind"] == "agent": return agent_spec(Agent(item["unit_dir"])), item["workspace"]
        return load_eval_set(item["unit_dir"])[0], item["workspace"]
    return load

def spend_so_far(ws):
    try: rows, _ = spend.read_rows(os.path.join(ws, spend.FILE))
    except spend.SpendError: return None
    return round(sum((r.get("charged_usd") or 0.0) if r["type"] == "result" else (r.get("counted_usd") or 0.0)
                     for r in rows if r["type"] in ("result", "recovered")), 6)

def plan_estimate_usd(unit_dir, ws):
    """The cost estimate `plan` prints for this unit with the default options, or None when it cannot be computed."""
    try:
        a = build_parser().parse_args(["plan", unit_dir, "--workspace", ws])
        spec, _ = load_eval_set(unit_dir)
        engines = make_engines(a, spec)
        n_cases, n_llm, n_trig = spec_counts(spec, a)
        est = estimate(n_cases, n_llm, n_trig, a.runs, [(e.name, e.model) for e in engines], a.baseline,
                       DEFAULT_GRADER_MODEL, parse_token_overrides(a.tokens), parse_price_overrides(a.price))
        return float(est["total_usd"])
    except (EvalError, ValueError, KeyError, TypeError):
        return None

def read_bytes(path):
    try:
        with open(path, "rb") as f: return f.read()
    except OSError:
        return None

REVIEW_MAX = 20000

def review_text(data):
    if data is None: return "<missing>"
    text = data.decode("utf-8", "replace")
    return text if len(text) <= REVIEW_MAX else text[:REVIEW_MAX] + "\n... (%d more characters not shown)" % (
        len(text) - REVIEW_MAX)

def combined_snapshot(spec, ws):
    """(hash, review): the requirements_eval_set hash computed with gate_hash's formulas from the very bytes the page
    shows, so a file changed after the read fails the signing step instead of being signed unseen."""
    req = read_bytes(os.path.join(ws, "requirements.md"))
    review, h = [("requirements.md", review_text(req))], hashlib.sha256()
    for label, p in unit_eval_files(spec):
        data = read_bytes(p) if os.path.isfile(p) else None
        h.update(label.encode() + b"\0" + (b"<missing>" if data is None else data) + b"\0")
        review.append((label, review_text(data)))
    req_hash = None if req is None else hashlib.sha256(b"requirements.md\0" + req).hexdigest()
    return combined_hash(req_hash, h.hexdigest()), review

def pending_gates(spec, ws, verdict_of):
    """[(gate, summary, sha256, review)] of every approval this unit's workspace still needs, a skill's or an agent's:
    the live approvals, the rule review, the registration of the last iteration (its problems read from the gate inside
    benchmark.json, the bytes the approval binds) and the coverage waivers."""
    out = []
    if next(unapproved_live_gates(spec, ws, verdict_of), None) is not None:
        sha, review = combined_snapshot(spec, ws)
        out.append((COMBINED_GATE, "requirements.md and %d eval set file(s), one approval of both" % (len(review) - 1),
                    sha, review))
    findings = rule_review_items(spec["skill_dir"])
    if findings and verdict_of("rule_review", spec, ws).status != "approved":
        out.append(("rule_review", "%d rule-review finding(s)" % len(findings), gate_hash("rule_review", spec, ws),
                    [("finding", x) for x in findings]))
    n = (iterations(ws) or [None])[-1]
    if n is None: return out
    try:
        bench = rjson(os.path.join(ws, "iteration-%d" % n, "benchmark.json"))
    except (ValueError, RecursionError):
        return out
    gate = bench.get("gate") if isinstance(bench, dict) else None
    if isinstance(gate, dict) and gate.get("needs_approval") and verdict_of("registration", spec, ws).status != "approved":
        problems = [str(p) for p in gate.get("problems") or []]
        out.append(("registration", "iteration-%d gate problems: %s" % (n, "; ".join(problems) or "none recorded"),
                    gate_hash("registration", spec, ws), [("problem", p) for p in problems]))
    if isinstance(bench, dict):
        for t, c in coverage_states(dict(bench, coverage_waivers=waiver_verdicts(spec, ws))):
            if c["state"] == "missing":
                gate_name = approvals.WAIVER_PREFIX + t
                out.append((gate_name, "tool %s did not run in iteration-%d (%s)" % (t, n, c["status"]),
                            gate_hash(gate_name, spec, ws), [("coverage", GATE_MESSAGES["coverage-missing"] % c)]))
    return out

def pending_items(pairs):
    """Every missing or stale approval of the given (unit folder, workspace) pairs, as engine/approval_batch items.
    Reads only; signs and writes nothing."""
    import eva
    rules, out = ruleset_sha256(), []
    for unit_dir, ws in pairs:
        agent = eva.detect_kind(unit_dir) == "agent"
        spec = agent_spec(Agent(unit_dir)) if agent else load_eval_set(unit_dir)[0]
        verdict_of = _memo_verdicts()
        base = {"unit": spec["skill_name"], "kind": "agent" if agent else "skill", "unit_dir": unit_dir, "workspace": ws,
                "ruleset_sha256": rules, "spend_usd": spend_so_far(ws),
                "estimate_usd": None if agent else plan_estimate_usd(unit_dir, ws)}
        for gate, summary, sha, review in pending_gates(spec, ws, verdict_of):
            out.append(dict(base, gate=gate, summary=summary, sha256=sha, status=verdict_of(gate, spec, ws).status,
                            needs_reason=gate in approvals.WAIVER_GATES, review=review))
    return out

def approve_batch(units, workspaces, cwd=None):
    """Every pending item recorded like a single approve; a coverage waiver item reads one reason line from stdin."""
    from engine import approval_batch
    items = pending_items(unit_pairs(units, workspaces, cwd))
    if not items:
        print("nothing is pending; nothing was recorded")
        return 0
    outcomes = approval_batch.run_batch(APPROVER["it"], items, lambda item, reason: _approve_with(
        item["gate"], unit_loader(item), reason=reason, listed=item), (approvals.Refused, EvalError))
    for o in outcomes: print(approvals.printable(o.line))
    return 0 if all(o.ok for o in outcomes) else 2

UNSIGNED_NOTE = "approvals are unsigned recorded decisions; no approver key, ssh-agent or approver session is needed"

def machine_waiver_show():
    mw = machine_waiver()
    print(machine_waiver_line(mw))
    rec = mw["record"] if isinstance(mw["record"], dict) else {}
    for k in ("sha256", "principal", "reason", "expires"):
        if mw.get(k): print("%s %s" % (k, mw[k]))
    if rec.get("tools"): print("tools %s" % ",".join(str(t) for t in rec["tools"]))
    if rec.get("machine_id"): print("machine_id %s" % rec["machine_id"])
    print("file %s" % shown_config(mw["path"]))
    return 0 if mw["status"] == "approved" else 1

def build_parser():
    from engine import install
    p = argparse.ArgumentParser(prog="skill_eval", description=__doc__.split("\n")[0])
    sp = p.add_subparsers(dest="cmd", required=True)
    def common(s, run_opts=True):
        s.add_argument("skill")
        s.add_argument("--workspace")
        if run_opts:
            s.add_argument("--engines", default="claude")
            s.add_argument("--runs", type=int, default=DEFAULT_RUNS)
            s.add_argument("--baseline", choices=sorted(BASELINES), default="none", help="none: without_skill, old: old_skill")
            s.add_argument("--old-skill")
            s.add_argument("--claude-model", default=DEFAULT_EXECUTOR_MODELS["claude"])
            s.add_argument("--codex-model", default=DEFAULT_EXECUTOR_MODELS["codex"])
            s.add_argument("--claude-arg", action="append", help="extra claude CLI arg (repeat), e.g. --claude-arg=--permission-mode --claude-arg=acceptEdits")
            s.add_argument("--codex-arg", action="append")
            s.add_argument("--claude-effort", choices=CLAUDE_EFFORTS, help="effort of the Claude model under test "
                           "(claude --effort); default: CLI/account default")
            s.add_argument("--codex-effort", choices=CODEX_EFFORTS)
            s.add_argument("--grader-engine", choices=GRADER_ENGINES, default="claude",
                           help="grader and blind pairwise judge: claude (claude -p, no tools, capped)")
            s.add_argument("--grader-model", default=None, help="default: %s" % DEFAULT_GRADER_MODEL)
            s.add_argument("--grader-effort", help="grader effort: %s" % "|".join(CLAUDE_EFFORTS))
            s.add_argument("--price", action="append", help="KEY=IN,OUT USD/MTok; KEY = engine name, model id or 'grader'")
            s.add_argument("--tokens", action="append", help="KIND=IN,OUT assumed tokens per call; KIND exec|trigger|grader|pairwise")
            s.add_argument("--cases", type=int); s.add_argument("--llm-cases", type=int); s.add_argument("--trigger-queries", type=int)
            s.add_argument("--live", action="store_true")
            s.add_argument("--auth-profile", help="personal-mac (subscription auth, the default of plan and estimate) or "
                           "company (API keys under a hard --budget-usd cap), from policy/auth-profiles.json; every "
                           "--live start requires it (the engine never detects a profile)")
            s.add_argument("--budget-usd", type=float, help="limit in API-equivalent USD (usage x API prices); "
                           "subscription runs are not billed per token")
            s.add_argument("--strict-budget", dest="strict_budget", action="store_true", default=True,
                           help="(default) refuse --live runs of engines without an enforceable per-call cap (codex)")
            s.add_argument("--no-strict-budget", dest="strict_budget", action="store_false",
                           help="RISKY: override only the strict-budget gate. A codex budget is only a soft limit (one "
                                "call can exceed --budget-usd) and its auth.json copy is readable by the agent under "
                                "test. " + NO_STRICT_TIER)
            s.add_argument("--dry-run", action="store_true",
                           help="with --live: check every live gate for this exact command and print the plan; executes "
                                "nothing (no model call, no `claude auth status`)")
            s.add_argument("--seed", type=int)
            s.add_argument("--run-root", help="folder for the per-run sandboxes (default: system temp folder)")
    for n in ("validate", "estimate", "plan", "run", "grade", "calibrate", "report", "loop"):
        s = sp.add_parser(n); common(s, n not in ("validate", "report"))
        if n in ("grade", "report", "calibrate"): s.add_argument("--iteration", type=int)
        if n == "grade":
            s.add_argument("--grade-cases", help="grade only these eval ids (comma-separated); estimate and budget gate "
                           "count only them; other runs' grading/comparison files are left untouched")
            s.add_argument("--grade-runs", help="grade only these run numbers k (comma-separated), e.g. 1")
        if n == "calibrate": s.add_argument("--golden", required=True)
        if n == "loop":
            s.add_argument("--builder", help=argparse.SUPPRESS)  # removed: an arbitrary shell command is not metered
            s.add_argument("--builder-prompt", help="file with instructions for the metered Claude builder (see "
                           "references/engine-cli.md)")
            s.add_argument("--builder-model", default=DEFAULT_BUILDER_MODEL)
            s.add_argument("--max-iterations", type=int, default=MAX_AUTO_ITERATIONS)
            s.add_argument("--golden")
    text = ("record one gate as an unsigned approval, or with --batch every pending approval of the --unit folders "
            "(engine/approvals.py; references/approval-gates.md); a skill or an agent folder, gates "
            "requirements_eval_set, requirements, eval_set, rule_review, registration, coverage_waiver:<tool>")
    s = sp.add_parser("approve", help=text, description=text)
    s.add_argument("skill", nargs="?")
    s.add_argument("--workspace", action="append")
    s.add_argument("--gate")
    s.add_argument("--reason", help="the reason of a coverage_waiver:<tool> gate (default: one line read from stdin)")
    s.add_argument("--batch", action="store_true")
    s.add_argument("--unit", action="append")
    s.add_argument("--cwd", help="the folder relative --unit and --workspace paths start from (default: this one)")
    s = sp.add_parser("pending", help="free: every missing or stale approval of the --unit folders with its gate hash, "
                      "ruleset_sha256, spend so far and plan estimate; records nothing")
    s.add_argument("--unit", action="append", required=True)
    s.add_argument("--workspace", action="append")
    s.add_argument("--cwd")
    s = sp.add_parser("approve-ui", help="removed (unit N1): prints that approvals are unsigned recorded decisions and "
                      "exits 2; use approve --batch")
    s.add_argument("--unit", action="append", required=True)
    s.add_argument("--workspace", action="append")
    s.add_argument("--cwd")
    s.add_argument("--tailscale", action="store_true")
    sp.add_parser("approver-session", help="removed (unit N1): prints that no approver session is needed")
    s = sp.add_parser("machine-waiver", help="the recorded machine waiver of this Mac for machine_waivable tools")
    x = s.add_subparsers(dest="waiver_cmd", required=True)
    s = x.add_parser("sign")
    s.add_argument("--tools", default=",".join(MACHINE_WAIVABLE))
    s.add_argument("--reason", help="the waiver's reason (default: one line read from stdin)")
    x.add_parser("show"); x.add_parser("revoke")
    text = ("record and write the format-2 record of a skill or an agent (kind agent) for the last iteration; a report "
            "with problems needs a current registration approval that names them, and every coverage_required tool a "
            "run or a waiver")
    s = sp.add_parser("register", help=text, description=text)
    common(s, False)
    s = sp.add_parser("snapshot"); common(s, False)
    sp.add_parser("check-builtin-plugins", help="free: `claude --settings <launch settings> plugin list --json` and "
                  "the enabled state of every @builtin plugin (no model call; loaded skills are not shown)")
    s = sp.add_parser("builder-check", help="free: the loop builder's writable set and the fingerprint of every entry "
                      "outside it in the skill folder and the workspace; writes nothing, starts no process")
    s.add_argument("skill"); s.add_argument("--workspace", required=True)
    s.add_argument("--compare", metavar="SAVED", help="an earlier builder-check output: print every changed path, exit "
                   "1 when any changed")
    sp.add_parser("approver-check", help="prints that approvals are unsigned recorded decisions and exits 0 (unit N1)")
    s = sp.add_parser("hub", help="the static registry site (no network, no push; publishing is a git push by a person)")
    x = s.add_subparsers(dest="hub_cmd", required=True).add_parser(
        "build", help="write index.html, registry.json, style.css and one page per unit of ROOT into --out, after an "
                      "English-only and link scan of every file")
    x.add_argument("root", nargs="?", default=".")
    x.add_argument("--out", required=True, help="an absent or empty folder")
    x.add_argument("--templates", help="a folder with index.html, unit.html and style.css (default engine/hub_templates)")
    gates = ("`agent run --live` and `agent loop --live` need a current requirements_eval_set approval (or requirements "
             "and eval_set), a current rule_review decision for the agent's review rows and " + AGENT_GRADER_TIER +
             ", and `agent plan` prints those gates")
    text = ("evaluate an AGENT.md v2 agent per tool from its typed scenarios (validate, plan, run, grade, report) and "
            "improve it in a loop whose builder may change only AGENT.md (loop); " + gates)
    s = sp.add_parser("agent", help=text, description=text)
    asp = s.add_subparsers(dest="agent_cmd", required=True)
    for n in ("validate", "plan", "run", "grade", "report", "loop"):
        x = asp.add_parser(n, description=gates if n in ("plan", "run", "loop") else None)
        x.add_argument("agent")
        if n != "validate": x.add_argument("--workspace")
        if n in ("grade", "report"): x.add_argument("--iteration", type=int)
        if n in ("plan", "run", "loop"):
            x.add_argument("--engines", default="claude")
            x.add_argument("--runs", type=int, default=DEFAULT_RUNS)
            x.add_argument("--old-agent", help="the previous AGENT.md v2 version of this agent (its baseline)")
            x.add_argument("--claude-model", default=DEFAULT_EXECUTOR_MODELS["claude"])
            x.add_argument("--codex-model", default=DEFAULT_EXECUTOR_MODELS["codex"])
            x.add_argument("--claude-arg", action="append")
            x.add_argument("--codex-arg", action="append")
            x.add_argument("--claude-effort", choices=CLAUDE_EFFORTS)
            x.add_argument("--codex-effort", choices=CODEX_EFFORTS)
            x.add_argument("--strict-budget", dest="strict_budget", action="store_true", default=True)
            x.add_argument("--no-strict-budget", dest="strict_budget", action="store_false",
                           help="RISKY: override only the strict-budget gate (soft budget limit, auth.json copy readable "
                                "by the agent). " + NO_STRICT_TIER)
            x.add_argument("--run-root", help="folder for the per-run sandboxes (default: system temp folder)")
        if n in ("plan", "run", "grade", "loop"):
            x.add_argument("--grader-engine", choices=GRADER_ENGINES, default="claude")
            x.add_argument("--grader-model", default=None)
            x.add_argument("--grader-effort")
            x.add_argument("--price", action="append")
            x.add_argument("--tokens", action="append")
            x.add_argument("--auth-profile")
            x.add_argument("--budget-usd", type=float)
        if n in ("run", "grade", "loop"): x.add_argument("--live", action="store_true")
        if n == "loop":
            x.add_argument("--builder-prompt", help="file with instructions for the metered Claude builder, which may "
                           "change only AGENT.md")
            x.add_argument("--builder-model", default=DEFAULT_BUILDER_MODEL)
            x.add_argument("--max-iterations", type=int, default=MAX_AUTO_ITERATIONS)
    s = sp.add_parser("install", help="install an agent's generated wrappers or a skill folder per tool (policy/install"
                      ".json; references/install.md): project scope under --target, or the Claude agent at user scope "
                      "after a recorded approval of the manifest")
    s.add_argument("unit")
    s.add_argument("--tools", default=",".join(install.TOOLS))
    s.add_argument("--scope", choices=("project", "user"), default="project")
    s.add_argument("--target", help="the project folder (project scope)")
    s.add_argument("--dry-run", action="store_true", help="print the plan and a diff per overwrite; write nothing")
    s.add_argument("--force", action="store_true", help="back up and overwrite a destination with other bytes")
    s.add_argument("--workspace")
    s = sp.add_parser("version", help="version bump: the next SemVer release, after a CHANGELOG.md entry")
    x = s.add_subparsers(dest="version_cmd", required=True).add_parser("bump")
    x.add_argument("unit")
    x.add_argument("part", choices=("major", "minor", "patch"))
    x.add_argument("--dry-run", action="store_true")
    s = sp.add_parser("deprecate", help="mark a version deprecated (an unsigned marker; it stays installable)")
    s.add_argument("unit")
    s.add_argument("--version", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--workspace")
    s = sp.add_parser("drift", help="pinned tool versions, units recorded under an older ruleset and old rule dates; "
                      "starts no process")
    s.add_argument("root", nargs="?", default=".")
    s.add_argument("--observed", action="append", default=[], metavar="TOOL=VERSION")
    s.add_argument("--today", metavar="YYYY-MM-DD")
    return p

def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if a.cmd == "check-builtin-plugins":
        rc, lines = builtin_plugin_check()
        print("\n".join(lines)); return rc
    if a.cmd in ("approver-check", "approver-session", "approve-ui"):
        ui = a.cmd == "approve-ui"
        print("%s: %s%s%s" % (a.cmd, "OK: " if a.cmd == "approver-check" else "", UNSIGNED_NOTE,
                              "; no page was started, use approve --batch --unit DIR" if ui else ""))
        return 2 if ui else 0
    a.argv = list(sys.argv[1:] if argv is None else argv)
    if hasattr(a, "grader_engine") and a.grader_model is None and a.grader_engine == "claude":
        a.grader_model = DEFAULT_GRADER_MODEL
    try:
        return _dispatch(a)
    except approvals.Refused as ex:
        print(str(ex), file=sys.stderr)
        return 2
    except (EvalError, spend.SpendError) as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 2
    finally:
        led = _LIVE.get("ledger")
        _LIVE.update(on=False, gated=False, ledger=None)
        AUTH["profile"] = None
        if led is not None: led.close()

def _dispatch(a):
    if a.cmd == "agent": return agent_dispatch(a)
    if a.cmd == "hub":
        rc, lines = hub_build(a.root, a.out, a.templates)
        print("\n".join(lines), file=sys.stdout if rc == 0 else sys.stderr)
        return rc
    if a.cmd == "builder-check": return builder_check(a.skill, a.workspace, a.compare)
    if a.cmd in ("install", "version", "deprecate", "drift"): return lifecycle_dispatch(a)
    if a.cmd == "pending":
        from engine import approval_batch
        print(approval_batch.pending_text(pending_items(unit_pairs(a.unit, a.workspace, a.cwd))))
        return 0
    if a.cmd == "machine-waiver":
        if a.waiver_cmd == "show": return machine_waiver_show()
        if a.waiver_cmd == "revoke":
            revoke_machine_waiver(); print("machine waiver revoked"); return 0
        rec = sign_machine_waiver([t for t in a.tools.split(",") if t], reason=a.reason)
        print("machine waiver recorded: tools %s, expires %s, sha256 %s" % (",".join(rec["tools"]), rec["expires"],
                                                                            approvals.machine_waiver_sha256(rec)))
        return 0
    if a.cmd == "approve":
        if a.batch and a.unit and not a.skill and not a.gate and a.reason is None:
            return approve_batch(a.unit, a.workspace, a.cwd)
        if a.batch or a.unit or a.cwd or not a.skill or not a.gate or len(a.workspace or []) > 1:
            raise EvalError("approve: give SKILL and --gate, or --batch with --unit")
        a.workspace = (a.workspace or [None])[0]
    if a.cmd in ("approve", "register"):  # recorded before the eval set is read, the same for every fixture
        def load():
            import eva
            if eva.detect_kind(a.skill) == "agent":
                agent = Agent(a.skill)
                return agent_spec(agent), agent_workspace(a, agent)
            spec, _ = load_eval_set(a.skill)
            return spec, os.path.realpath(a.workspace) if a.workspace else default_workspace(spec)
        if a.cmd == "approve":
            rec = _approve_with(a.gate, load, reason=a.reason)
            print("approved %s sha256=%s" % (a.gate, rec["sha256"][:12])); return 0
        rec = _register_with(load); print("registered %s %s iteration-%d (%s)" % (rec["skill_name"], rec["version"],
                                          rec["iteration"], "auto" if rec["approval"] == "auto" else "approved")); return 0
    if hasattr(a, "auth_profile"): select_auth_profile(a.auth_profile)  # plan/estimate/dry runs: personal-mac by default
    spec, problems = load_eval_set(a.skill)
    if a.cmd == "validate":  # before the workspace: an unsafe SKILL.md name is reported, not raised
        print("\n".join(problems) if problems else "eval set OK: %d cases, %d trigger queries" % (len(spec["cases"]), len(spec["triggers"] or [])))
        import eva
        for x in eva.english_eval_problems(spec["skill_dir"])[1]:
            print("review: non-English text in a run-output matcher (allowed, a human confirms the task needs it): " + x)
        rep_ = eva.parity_report(spec["skill_dir"])
        print("multi-tool parity: " + eva.parity_line(eva.parity_status(rep_)))
        for c in rep_.needs_review:
            print("review: " + c["reason"])
        unrecorded = []
        if rule_review_items(spec["skill_dir"]):
            try: ws_ = os.path.realpath(a.workspace) if a.workspace else default_workspace(spec)
            except EvalError: ws_ = None
            unrecorded = rule_review_problems(spec, ws_) if ws_ else ["rule review not recorded: no workspace"]
            print(("RULE REVIEW NOT RECORDED: " + unrecorded[0]) if unrecorded else "rule review decision recorded for these exact findings")
        return 1 if problems else 3 if unrecorded else 0
    ws = os.path.realpath(a.workspace) if a.workspace else default_workspace(spec)
    if a.cmd == "snapshot":
        dst = os.path.join(ws, "skill-snapshot")
        if os.path.exists(dst): raise EvalError("%s exists" % dst)
        import eva
        tree = eva.unit_tree(spec["skill_dir"], shutil.ignore_patterns())
        links = link_refusals(spec["skill_dir"], tree)
        if links: raise EvalError("skill folder %s: %s; refusing to snapshot it, nothing was copied"
                                  % (spec["skill_dir"], "; ".join(links)))
        shutil.copytree(spec["skill_dir"], dst); print("snapshot -> %s (use --baseline old --old-skill %s)" % (dst, dst)); return 0
    if a.cmd == "report":
        n = a.iteration or (iterations(ws) or [None])[-1]
        if n is None: raise EvalError("no iterations in %s" % ws)
        _, gate = report_iteration(spec, ws, n)
        print("iteration-%d: %s -> %s" % (n, "NEEDS APPROVAL: %s" % gate["problems"] if gate["needs_approval"] else "clean",
                                         os.path.join(ws, "iteration-%d" % n, "comparison.md")))
        return 3 if gate["needs_approval"] else 0
    overrides = [f for f, v in (("--cases", a.cases), ("--llm-cases", a.llm_cases), ("--trigger-queries", a.trigger_queries))
                 if v is not None]
    if overrides and a.cmd not in ("estimate", "plan"):
        raise EvalError("%s only apply to estimate/plan; %s always uses the validated eval set" % (", ".join(overrides), a.cmd))
    if a.runs < 1: raise EvalError("--runs must be >= 1")
    if a.cmd == "loop" and not 1 <= a.max_iterations <= MAX_AUTO_ITERATIONS:
        raise EvalError("--max-iterations must be 1..%d" % MAX_AUTO_ITERATIONS)
    if a.cmd == "loop" and a.builder:
        raise EvalError("--builder (shell command) is not supported: it would run outside the metered budget. Use "
                        "--builder-prompt FILE (one capped `claude -p` call per iteration, charged to --budget-usd)")
    builder_text = read_builder_prompt(a) if a.cmd == "loop" and a.builder_prompt else None
    engines = make_engines(a, spec)
    if a.baseline == "old" and not a.old_skill and os.path.isdir(os.path.join(ws, "skill-snapshot")):
        a.old_skill = os.path.join(ws, "skill-snapshot")
    prices = parse_price_overrides(a.price)
    tokens = parse_token_overrides(a.tokens)
    n_cases, n_llm, n_trig = spec_counts(spec, a)
    make_grader(a.grader_engine, a.grader_model, a.grader_effort)  # validates --grader-effort before anything runs
    est = estimate(n_cases, n_llm, n_trig, a.runs, [(e.name, e.model) for e in engines], a.baseline, a.grader_model,
                   tokens, prices)
    if a.cmd == "estimate":
        print(format_estimate(est)); return 0
    live_cmds = ("run", "grade", "calibrate", "loop")
    if a.dry_run and not a.live:
        raise EvalError("--dry-run checks the gates of a --live command: add --live (with --dry-run it still executes "
                        "nothing); without --live every command is already a dry run")
    if a.cmd == "plan" or (a.cmd in live_cmds and not a.live and a.cmd != "grade"):
        print(plan_text(spec, problems, a, engines, ws, est, tokens, prices))
        print("\nDRY RUN: nothing was executed. Real runs need " + (
            "--live --auth-profile %s --budget-usd X (a hard cap in USD: API-key calls are billed per token, and each "
            "call starts only when the spend so far plus its per-call cap fits), no claude call (the claude engine, the "
            "claude grader and the builder are refused in API-key mode: --max-budget-usd is not an enforceable per-call "
            "cap), no engine without a per-call cap (codex: no enforceable per-call cap in API-key mode; "
            "--no-strict-budget does not reopen it)" % AUTH["profile"].name if api_key_mode() else
            "--live --budget-usd X (API-equivalent USD), subscription auth only")
              + ", approved requirements/eval_set, no validation or preflight problems, and an estimate within budget.")
        return 0
    if a.cmd == "grade" and not a.live:
        n = a.iteration or (iterations(ws) or [None])[-1]
        if n is None: raise EvalError("no iterations in %s" % ws)
        grade_iteration(spec, ws, n, None, scope=grade_scope(a)); print("static + human queue only (dry run); LLM items not graded"); return 0
    # --- live: the same gates for run, loop, grade and calibrate ---
    if a.budget_usd is None or not math.isfinite(a.budget_usd) or a.budget_usd <= 0:
        raise EvalError(CAP_REQUIRED % AUTH["profile"].name if api_key_mode() else NO_BUDGET)
    if a.auth_profile is None: raise EvalError(PROFILE_REQUIRED)
    n = None
    if a.cmd in ("grade", "calibrate"):
        n = a.iteration or (iterations(ws) or [None])[-1]
        if n is None or not os.path.isdir(os.path.join(ws, "iteration-%d" % n)): raise EvalError("no iterations in %s" % ws)
    dangling = spend.preview(ws)  # read only: the bounds of crashed calls count against this command's cap
    left = a.budget_usd - sum(r["bound_usd"] or 0.0 for r in dangling)
    if a.dry_run:
        return live_dry_run(a, spec, problems, ws, n, engines, tokens, prices, left)
    def prepare():  # every gate that starts no process, before the live start is recorded
        report = []
        check_live_gates(a, spec, problems, ws, n, engines, tokens, prices, left, report=report)
        fails = [r for r in report if r != AUTH_STATUS_SKIPPED]
        if fails: raise EvalError(fails[0])
        rules = ruleset_sha256()
        return approvals.Prepared(spec["skill_name"], live_plan_hash(a, spec, ws, builder_text), rules,
                                  ["live command: " + shlex.join(["python3", "skill_eval.py"] + a.argv), "workspace: " + ws]
                                  + [x for x in (spend.preview_line(dangling),) if x] + ["ruleset_sha256 " + rules])
    grant = APPROVER["it"].sign("live", a.cmd, prepare, budget_usd=a.budget_usd)
    print("recorded %s --live by %s (plan sha256 %s)" % (a.cmd, grant.record["recorded_by"], grant.sha256[:12]),
          file=sys.stderr)
    check_live_gates(a, spec, problems, ws, n, engines, tokens, prices, left)
    if a.cmd in ("run", "loop"):
        for e in engines:
            if budget_enforcement(e) == "soft_limit":
                print("WARNING: budget " + SOFT_LIMIT_NOTE % e.name, file=sys.stderr)
                print("WARNING: " + NO_STRICT_WARNING % e.name, file=sys.stderr)
    budget = Budget(a.budget_usd)
    grader = make_grader(a.grader_engine, a.grader_model, a.grader_effort, budget=budget, prices=prices)
    if live_plan_hash(a, spec, ws, builder_text) != grant.sha256:
        raise EvalError("the command's inputs changed after signing (skill folder, eval set, requirements or a file named "
                        "by an option); nothing was run, sign again")
    enable_live(grant)
    p = AUTH["profile"]
    budget.ledger = _LIVE["ledger"] = spend.Ledger(ws, a.cmd, p.name, p.billing, a.budget_usd)
    budget.spent = budget.ledger.open([r["call_id"] for r in dangling])  # lock, re-read, one recovered row per crash
    if budget.ledger.torn:
        print("WARNING: spend ledger %s: dropped a torn last row (%s)" % (budget.ledger.path, budget.ledger.torn),
              file=sys.stderr)
    paid = (lambda what: "%s, API-equivalent ~$%.2f; CLI-reported spend in %s (auth profile %s, API-key mode)"
            % (what, budget.spent, budget.ledger.path, p.name)) if api_key_mode() else \
        (lambda what: "%s, API-equivalent ~$%.2f (subscription: not billed per token)" % (what, budget.spent))
    if a.cmd == "run":
        n = run_iteration(spec, ws, engines, a.runs, a.baseline, a.old_skill, budget, prices, tokens=tokens,
                          run_root=a.run_root)
        stopped = (rjson(os.path.join(ws, "iteration-%d" % n, "plan.json"), {}) or {}).get("stopped")
        print(paid("iteration-%d done" % n)); return 3 if stopped and api_key_mode() else 0
    if a.cmd == "grade":
        grade_iteration(spec, ws, n, grader, a.seed, scope=grade_scope(a))
        if spend_halt(budget): raise EvalError(spend_halt(budget))
        print(paid("graded iteration-%d" % n)); return 0
    if a.cmd == "calibrate":
        cal = calibrate_metered(a.golden, grader, os.path.join(ws, "iteration-%d" % n))
        if spend_halt(budget): raise EvalError(spend_halt(budget))
        wjson(os.path.join(ws, "iteration-%d" % n, "calibration.json"), cal)
        print("agreement %.2f kappa %.2f (n=%d)" % (cal["agreement"], cal["kappa"], cal["n"])); return 0
    if a.cmd == "loop":
        builder = ClaudeBuilder(builder_text, a.builder_model, budget, prices,
                                skill_name=spec["skill_name"]) if builder_text else None
        def precheck(spec_now, left):
            _, probs = load_eval_set(spec_now["skill_dir"])
            try:
                check_live_gates(a, spec_now, probs, ws, None, engines, tokens, prices, budget.remaining(), left)
            except EvalError as ex:
                return [str(ex)]
            return []
        r = loop(spec, ws, engines, grader, builder, a.max_iterations, a.runs, a.baseline,
                 a.old_skill, budget, prices, a.golden, a.seed, tokens=tokens, precheck=precheck, run_root=a.run_root)
        print(json.dumps(r, ensure_ascii=False)); return 0 if r["status"] == "clean" else 3
    return 2

if __name__ == "__main__":
    sys.exit(main())
