#!/usr/bin/env python3
"""Skill-eval harness: run a skill's eval set with the skill on vs off (or new vs old), grade, aggregate, gate.

File formats follow Anthropic skill-creator (references/schemas.md, scripts/aggregate_benchmark.py,
scripts/run_eval.py) and agentskills.io (evaluating-skills, optimizing-descriptions); deviations are listed
in README.md ("스킬 평가 하네스"). Python 3.9 standard library only.

Safety: no model or CLI is ever executed unless the command line has --live AND a finite --budget-usd, the approval
gates pass, and the cost estimate fits the budget. Without --live every command is a dry run: it prints the
plan and the estimated cost. Engines and graders take an injectable exec_fn so tests run fully mocked.
Auth is subscription only (Claude Code claude.ai OAuth, Codex ChatGPT login; README "Subscription auth"): API-key
variables are refused and scrubbed from every child, and costs are API-equivalent (usage x API prices), not spend.

Usage: python3 skill_eval.py <validate|estimate|plan|run|grade|calibrate|report|approve|register|snapshot|loop> <skill> ...
       (also reachable as: python3 eva.py eval <subcommand> ...)
"""
import argparse, csv, datetime, hashlib, json, math, mmap, os, platform, random, re, shlex, shutil, subprocess, sys, tempfile, time

LAYERS = ("static", "llm", "human")
BASELINES = {"none": "without_skill", "old": "old_skill"}  # skill-creator configuration names
PRIMARY = "with_skill"
DEFAULT_RUNS = 3
TRIGGER_THRESHOLD = 0.5           # run_eval.py --trigger-threshold default / agentskills.io "0.5 is a reasonable default"
TRIGGER_POS = TRIGGER_NEG = 10     # house rule (H): exactly 10 + 10 (skill-creator / agentskills.io say 8-10 each)
MAX_AUTO_ITERATIONS = 5            # skill-creator run_loop --max-iterations 5 / agentskills.io "Five iterations"
AGREEMENT_MIN = 0.8                # H: grader-vs-human agreement below this is a gate problem
REGRESSION_TOLERANCE = 0.0         # H: any drop of mean pass_rate counts as regression
HIGH_VARIANCE_STDDEV = 0.3         # H: per-eval pass_rate stddev that gets a "flaky" note
STATIC_TYPES = ("regex", "not_regex", "tool_call", "skill_triggered", "file_exists",
                "file_equals_fixture", "csv_cleaned")

# USD per million tokens (input, output). Source: https://platform.claude.com/docs/en/about-claude/pricing,
# read 2026-10-15 (base input / output columns; Haiku 5.5 row "for prompts up to 100,000 tokens").
# Codex/OpenAI prices are not listed: not verified from a primary source. Pass --price codex=IN,OUT.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
DEFAULT_EXECUTOR_MODELS = {"claude": "claude-opus-5-5", "codex": None}  # codex: CLI default (model unknown)
DEFAULT_GRADER_MODEL = "claude-sonnet-5-5"
# Assumed tokens per call (input, output). Assumptions, not measurements: replace with --tokens after a pilot.
DEFAULT_TOKENS = {"exec": (60000, 4000), "trigger": (25000, 500), "grader": (12000, 800), "pairwise": (16000, 800),
                  "builder": (80000, 8000)}
DEFAULT_BUILDER_MODEL = "claude-opus-5-5"
# Effort levels. Claude: `--effort` CLI flag; low/medium/high, xhigh and max are named in the Claude Code changelog
# (~/.claude/cache/changelog.md: "Available via `/effort`, `--effort`"). Codex: values accepted by the Codex plugin
# runtime (~/.claude/plugins/cache/openai-codex/codex/1.0.4/skills/codex-cli-runtime/SKILL.md: none, minimal, low,
# medium, high, xhigh). The `codex exec` spelling `-c model_reasoning_effort=<level>` is NOT verified from a primary
# source in this repo (추정): check `codex exec --help` / the Codex config docs before a live run.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
CODEX_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
CODEX_EFFORT_CONFIG = "model_reasoning_effort=%s"  # (추정) config key, see above
CODEX_ADMIN_SKILLS = os.path.join("/etc", "codex", "skills")  # admin-scope skills: load in every Codex call
GRADER_ENGINES = ("claude", "codex")
# Codex grader price per million tokens (input, output). OpenAI/Codex prices are not verified from a primary source
# here, so the most expensive row of PRICES is used as a conservative assumption (추정: not shown to be an upper
# bound of the real price). Override with --price grader=IN,OUT. Every Codex grader call is charged at least its
# worst-case bound (assumed tokens x WORST_CASE_FACTOR x this price): Codex has no per-call cap.
CODEX_GRADER_ASSUMED_PRICE = max(PRICES.values(), key=lambda p: p[0] + p[1])
# Per-call budget control (H). Claude: --max-budget-usd gets only this share of the remaining budget, because how far
# the CLI can overshoot its cap is not documented. Codex has no per-call cap, so its budget is a SOFT LIMIT: a call
# starts only when its estimated worst-case bound (assumed tokens x WORST_CASE_FACTOR x price) fits the remaining
# budget, but nothing stops one call from costing more. --strict-budget (the default) refuses live runs of such
# engines; --no-strict-budget is an explicit, risky opt-out.
CLAUDE_CAP_HEADROOM = 0.5
WORST_CASE_FACTOR = 3.0
SOFT_LIMIT_NOTE = ("SOFT LIMIT: %s has no per-call budget cap; the worst-case bound is an estimate, so one call can "
                   "exceed the remaining budget (--live refuses it by default; --no-strict-budget allows it, risky)")
NO_STRICT_WARNING = ("--no-strict-budget: %s runs with a SOFT budget limit (one call can exceed --budget-usd) and its "
                     "auth.json copy is readable by the agent under test (its tokens may be saved in transcripts or "
                     "output files)")

def budget_enforcement(engine_cls_or_obj):
    return "per_call_cap" if engine_cls_or_obj.has_call_cap else "soft_limit"
# Extra CLI args that would change the priced model or the per-call cap (exact flag or flag=value).
# Also flags that would add skill/plugin/config sources back into an isolated run (README "Eval isolation").
FORBIDDEN_EXTRA_ARGS = {"claude": ("--model", "--fallback-model", "--max-budget-usd", "--settings", "--setting-sources",
                                   "--plugin-dir", "--plugin-url", "--add-dir", "--mcp-config", "--effort",
                                   "--allowedTools", "--allowed-tools", "--dangerously-skip-permissions",
                                   "--disallowedTools", "--disallowed-tools",
                                   "--allow-dangerously-skip-permissions", "--bare", "--safe-mode"),
                        "codex": ("-m", "--model", "-p", "--profile", "--oss", "-C", "--cd", "--add-dir")}

SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")  # an eval `name` must be one safe path component
SKILL_IGNORE = ("evals", "__pycache__", "*-workspace")      # not installed into runs, not part of the skill hash

class EvalError(Exception): pass
class LiveDisabled(RuntimeError): pass
class JudgeAnswerError(EvalError):
    """A paid pairwise call whose winner is not A, B or TIE."""
    def __init__(self, msg, winner):
        EvalError.__init__(self, msg); self.winner = winner

def safe_name(name):
    return isinstance(name, str) and bool(SAFE_NAME_RE.match(name)) and ".." not in name

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

def sha256_labeled(pairs):
    """pairs: [(label, path)]; the label (path relative to the skill) is hashed with the content."""
    h = hashlib.sha256()
    for label, p in pairs:
        h.update(label.encode() + b"\0")
        if os.path.isfile(p):
            with open(p, "rb") as f: h.update(f.read())
        else:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()

def skill_tree_hash(skill_dir):
    """sha256 over every file a run installs (the skill folder minus SKILL_IGNORE), labeled by relative path.
    Symlinks are followed and hashed by content, as copytree copies content too."""
    pairs, ign = [], shutil.ignore_patterns(*SKILL_IGNORE)
    for dp, dn, fn in os.walk(skill_dir, followlinks=True):
        skip = ign(dp, dn + fn)
        dn[:] = sorted(d for d in dn if d not in skip)
        for f in sorted(fn):
            if f not in skip:
                p = os.path.join(dp, f)
                pairs.append((os.path.relpath(p, skill_dir).replace(os.sep, "/"), p))
    return sha256_labeled(pairs)

def cents_floor(x):
    return math.floor(x * 100 + 1e-9) / 100.0

def slug(text, n=40):
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "-", text or "").strip("-").lower()
    return s[:n].strip("-")

# ---------- eval set ----------
def _check_problems(where, chk):
    if not isinstance(chk, dict) or chk.get("type") not in STATIC_TYPES:
        return ["%s.check.type: one of %s required" % (where, "|".join(STATIC_TYPES))]
    t, e = chk["type"], []
    if t in ("regex", "not_regex"):
        if not isinstance(chk.get("pattern"), str) or not chk["pattern"]:
            e.append("%s.check.pattern: non-empty string required" % where)
        else:
            try: re.compile(chk["pattern"])
            except re.error as ex: e.append("%s.check.pattern: %s" % (where, ex))
        if chk.get("target", "final") not in ("final", "transcript"):
            e.append("%s.check.target: final|transcript" % where)
    elif t == "tool_call":
        if not isinstance(chk.get("tool"), str) or not chk["tool"]:
            e.append("%s.check.tool: non-empty string required" % where)
        for k in ("min", "max"):
            if k in chk and not (isinstance(chk[k], int) and not isinstance(chk[k], bool) and chk[k] >= 0):
                e.append("%s.check.%s: integer >= 0 required" % (where, k))
        if "input_regex" in chk:
            try: re.compile(chk["input_regex"])
            except (re.error, TypeError) as ex: e.append("%s.check.input_regex: %s" % (where, ex))
    elif t == "skill_triggered":
        if not isinstance(chk.get("expected"), bool): e.append("%s.check.expected: true|false required" % where)
    elif t in ("file_exists", "file_equals_fixture", "csv_cleaned"):
        for key in (("path",) if t == "file_exists" else ("path", "fixture")):
            p = chk.get(key)
            if not isinstance(p, str) or not p or os.path.isabs(p) or ".." in p.replace("\\", "/").split("/"):
                e.append("%s.check.%s: relative path required" % (where, key))
        if t == "csv_cleaned" and (not isinstance(chk.get("data_rows"), int) or isinstance(chk.get("data_rows"), bool)
                                    or chk["data_rows"] < 0):
            e.append("%s.check.data_rows: integer >= 0 required" % where)
    return e

def load_eval_set(skill_dir):
    """Returns (spec, problems). spec: skill_name, skill_dir, description, cases[], triggers[] (or None if missing)."""
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
                    problems += _check_problems(where, it["check"])
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
    return {"skill_name": name, "skill_dir": skill_dir, "description": meta.get("description") or "",
            "claude_python_script": claude_script, "cases": cases, "triggers": triggers}, problems

def load_triggers(skill_dir):
    """evals/trigger_evals.json: skill-creator eval-set format [{"query": str, "should_trigger": bool}]."""
    fp = os.path.join(skill_dir, "evals", "trigger_evals.json")
    if not os.path.isfile(fp):
        return None, ["trigger_evals.json: missing evals/trigger_evals.json (%d should-trigger + %d should-not queries)"
                      % (TRIGGER_POS, TRIGGER_NEG)]
    try:
        data = rjson(fp)
    except ValueError as ex:
        return None, ["trigger_evals.json: invalid JSON: %s" % ex]
    if not isinstance(data, list):
        return None, ["trigger_evals.json: top level must be a list"]
    e, out, seen = [], [], set()
    for i, q in enumerate(data):
        if not (isinstance(q, dict) and isinstance(q.get("query"), str) and q["query"].strip()
                and isinstance(q.get("should_trigger"), bool)):
            e.append("trigger_evals.json[%d]: {\"query\": str, \"should_trigger\": bool} required" % i); continue
        if q["query"] in seen: e.append("trigger_evals.json[%d]: duplicate query" % i)
        seen.add(q["query"]); out.append({"query": q["query"], "should_trigger": q["should_trigger"]})
    pos = sum(q["should_trigger"] for q in out); neg = len(out) - pos
    if (pos, neg) != (TRIGGER_POS, TRIGGER_NEG):
        e.append("trigger_evals.json: need %d should-trigger + %d should-not, got %d + %d" % (TRIGGER_POS, TRIGGER_NEG, pos, neg))
    return out, e

def full_prompt(case):
    p = case.get("pressure")
    return (p["context"].strip() + "\n\n" + case["prompt"]) if p and p.get("context", "").strip() else case["prompt"]

# ---------- engines ----------
_LIVE = {"on": False}

def live_exec(cmd, cwd, timeout, env=None):
    """The only place a real CLI is started. Refuses unless main() enabled live mode (--live + --budget-usd + gates)."""
    if not _LIVE["on"]:
        raise LiveDisabled("refusing to execute %r: live mode needs --live and --budget-usd" % (cmd[0],))
    env = scrub_env(os.environ if env is None else env)  # subscription auth only: no API key reaches a child
    env.pop("CLAUDECODE", None)  # as skill-creator run_eval.py: allow nesting claude -p inside a Claude Code session
    env = with_account_marker(env, cmd)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                       stdin=subprocess.DEVNULL)  # codex exec waits for stdin when it is an open pipe/terminal
    return p.returncode, p.stdout, p.stderr

# ---------- subscription auth (README "Eval isolation" > "Subscription auth") ----------
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
# Account switching (2026-10-16, README "Subscription auth" > "계정 자동 전환"). skill_eval itself still never passes a
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
    if cmd and os.path.basename(cmd[0]) == "claude":
        env.update(ACCOUNT_MARKER_ENV)
        env[ACCOUNT_SWITCH_OPT_IN] = "1" if account_switch_enabled() else "0"
    return env

def switch_account_config_dirs(home=None, environ=None):
    """Config dirs of every account the shim may switch to (only when switching is enabled), derived from the same
    inputs as the shim: home = $CLAUDE_AS_HOME or ~, accounts = $CLAUDE_ACCOUNTS or every ~/.claude-acctN. Each gets
    the same preflight/launch checks as the default config dir (settings auth, remote cache, user/synced skills)."""
    environ = os.environ if environ is None else environ
    if not account_switch_enabled(environ): return []
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
# reports `api_key`"). The JSON key that holds the method is not documented there (추정), so every string value under
# a key named like auth/method/source/provider is classified, and anything unrecognized fails closed.
CLAUDE_AUTH_STATUS_CMD = ["claude", "auth", "status", "--json"]
# Recognized subscription method values (normalized: lowercase alphanumerics). "claude.ai" is from the changelog;
# "oauth"/"oauth_token" (setup-token) and "subscription" are (추정) spellings, accepted only under a method-like key.
CLAUDE_SUBSCRIPTION_METHODS = ("claudeai", "oauth", "oauthtoken", "claudeaioauth", "subscription")
CODEX_SUBSCRIPTION_MODES = ("chatgpt",)  # ~/.codex/auth.json "auth_mode" (value seen on this machine, 2026-10-15)
BILLING = "subscription"
ACTUAL_SPEND_NOTE = ("not metered: subscription runs (claude.ai plan / ChatGPT plan) are not billed per token; plan "
                     "limits and any extra-usage charges are visible only in your account")

def scrub_env(env):
    """Copy of env without the API-key and provider-routing variables (NON_SUBSCRIPTION_ENV) and IGNORED_AUTH_ENV."""
    return {k: v for k, v in dict(env).items()
            if k not in NON_SUBSCRIPTION_ENV + IGNORED_AUTH_ENV + (
                "BASH_ENV", "ENV", "BASHOPTS", "SHELLOPTS", "CLAUDE_CODE_SAFE_MODE",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
            and not k.startswith("BASH_FUNC_")}

def api_key_env_problems(environ=None):
    environ = os.environ if environ is None else environ
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
# <cwd>/.claude/settings.json and settings.local.json; whether ancestors of the cwd are also read is not verified (추정),
# so every ancestor is checked too. Managed sources always apply. From https://code.claude.com/docs/en/managed-settings
# (read 2026-10-15): managed-settings.json plus every non-hidden *.json in managed-settings.d/ next to it
# (/Library/Application Support/ClaudeCode/ on macOS, /etc/claude-code/ on Linux and WSL); the macOS configuration
# profile domain com.anthropic.claudecode; Windows HKLM/HKCU registry values (cannot be read here: fail closed); and
# server-managed settings, cached at <config dir>/remote-settings.json (docs/en/server-managed-settings).
CLAUDE_SETTINGS_FILES = ("settings.json", "settings.local.json")
CLAUDE_MANAGED_SETTINGS = ("/Library/Application Support/ClaudeCode/managed-settings.json",
                           "/etc/claude-code/managed-settings.json")
# Where macOS stores a configuration profile's managed preferences for the domain: the standard macOS location, not
# named on the Claude Code pages (추정). Parsed with plistlib when present; an unreadable file fails closed.
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
    set by the CLI itself after scrubbing (whether it then overrides the Keychain login is not verified, 추정), so it
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

def claude_settings(skill_name=None, bash_gate=None):
    """The --settings object of every Claude launch: bundled skills off (kill switch plus skillOverrides "off" for every
    known bundled name except skill_name, the skill under test) and every known built-in plugin disabled."""
    settings = {"disableBundledSkills": True,
            "enabledPlugins": {i: False for i in claude_builtin_plugins()["ids"]},
            "skillOverrides": {n: "off" for n in claude_bundled_skills()["names"] if n != skill_name}}
    if bash_gate:
        settings["disableAllHooks"] = False
        settings["hooks"] = {"PreToolUse": [{"matcher": "*", "hooks": [{
            "type": "command", "command": sys.executable,
            "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_bash_gate.py"),
                     skill_name, bash_gate["script"], bash_gate["sha256"], bash_gate["python3"]],
            "onFailure": "block"}]}]}
    return settings

def claude_settings_json(skill_name=None, bash_gate=None):
    return json.dumps(claude_settings(skill_name, bash_gate), sort_keys=True)

# One sanitized launch shape for every claude -p call (executor, grader, builder): project/local setting sources only,
# no MCP servers, bundled skills and built-in plugins off, auto memory, claude.ai MCP servers and /doctor off,
# API-key/provider variables scrubbed.
CLAUDE_CHILD_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
                    "DISABLE_DOCTOR_COMMAND": "1"}

def claude_isolation_args(skill_name=None, bash_gate=None):
    return ["--setting-sources", "project,local", "--strict-mcp-config", "--settings", claude_settings_json(skill_name, bash_gate)]

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

def launch_claude(exec_fn, cmd, cwd, timeout, env=None, skill_name=None):
    """Grader and builder launch path: refuses (EvalError, before the call) unless cmd carries the isolation args (for
    skill_name, the skill the launch must not override) and cwd passes claude_launch_problems; the child gets
    claude_child_env(). The executor uses the same args, env and checks through its Sandbox."""
    iso = claude_isolation_args(skill_name)
    if cmd[:2] != ["claude", "-p"] or not any(cmd[i:i + len(iso)] == iso for i in range(len(cmd))):
        raise EvalError("refusing a claude call without the isolation arguments")
    probs = claude_launch_problems(cwd)
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
        """Copy the skill without evals/ (the eval set must not leak into the run). The name (SKILL.md frontmatter)
        must be one safe path component and the destination must stay inside the skills folder of the sandbox."""
        if not safe_name(name):
            raise EvalError("skill name %r is not a safe path component; refusing to install it" % (name,))
        bad = escaping_links(skill_dir)
        if bad: raise EvalError("skill folder %s has symlinks that leave it (or dangle): %s; refusing to copy them "
                                "into the run" % (skill_dir, ", ".join(bad[:5])))
        dst = ensure_inside(os.path.join(workdir, self.skills_rel), os.path.join(workdir, self.skills_rel, name))
        shutil.copytree(skill_dir, dst, ignore=shutil.ignore_patterns(*SKILL_IGNORE))
        return dst
    def command(self, prompt, with_skill, budget_usd=None, skill_name=None): raise NotImplementedError
    def parse(self, stdout): raise NotImplementedError
    def triggered(self, result, skill_name): raise NotImplementedError
    def sandbox_env(self, root):
        """Environment for one isolated run whose sandbox folder is root (see README "Eval isolation")."""
        return scrub_env(os.environ)
    def remove_credentials(self, root):
        """Deletes and verifies the removal of any credential copy in the sandbox; None or an error string."""
        return None
    def auth_record(self):
        """How the run authenticates (names only, never values)."""
        return {}
    def sandbox_problems(self, root, work, skill_name, with_skill):
        """Static check of what the engine could discover from this sandbox, before the CLI starts."""
        found = sorted(n for n, _ in skills_under(os.path.join(work, self.skills_rel), 1))
        want = [skill_name] if with_skill else []
        out = [] if found == want else ["project skills in the sandbox are %s, expected %s" % (found, want)]
        for a in ancestors(os.path.dirname(os.path.realpath(root))):
            hits = skills_under(os.path.join(a, self.skills_rel), 3)
            if hits: out.append("ancestor %s has skills %s" % (os.path.join(a, self.skills_rel), sorted(n for n, _ in hits)))
        return out
    def isolation_problems(self, res, with_skill, skill_name, user_names):
        """Checks of the finished run's own report of what it loaded (fail closed when it cannot be verified)."""
        return []
    def after_run(self, root):
        return []
    def run(self, prompt, workdir, with_skill, budget_usd=None, env=None, skill_name=None):
        """A call that raises after it may have started (timeout, crash, ...) is not re-raised: it comes back as an
        error result with no usage and launch_failed set, so the caller charges the cap/bound, records it and stops.
        Output that cannot be parsed after the call (e.g. nonnumeric usage) is handled the same way, with any
        CLI-reported cost kept so max(reported, cap) is charged. LiveDisabled is raised before anything starts and
        passes through."""
        cmd = self.command(prompt, with_skill, budget_usd, skill_name=skill_name)
        t0, start = time.monotonic(), now_iso()
        failed = None
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
        return {"billing": BILLING, "method": "claude.ai login of the current user session (Keychain)"}
    def sandbox_problems(self, root, work, skill_name, with_skill):
        """Before launch: the project skill set (Engine), no auth/provider setting in any source the run can load
        (claude_launch_problems: cwd + ancestors, managed sources, the server-managed cache of the config dir), no
        custom command files in the cwd or an ancestor, and no synced skill that the init check could not see
        (claude_undetectable_skill_problems)."""
        out = Engine.sandbox_problems(self, root, work, skill_name, with_skill)
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
    def isolation_problems(self, res, with_skill, skill_name, user_names):
        """From the stream-json system/init event: apiKeySource "none" (docs/en/agent-sdk/typescript ApiKeySource:
        "No API key. The session authenticates another way, such as a claude.ai login"), an explicit empty plugin
        list, and a skill set that is exactly {skill under test} (with_skill) or empty (without_skill); any extra
        skill, recognized or not, is a leak. This runs after the call, as an extra check: init lists user-invocable
        skills only, so the sources are enforced before launch (sandbox_problems). Missing fields fail closed."""
        init = res.get("init")
        if not isinstance(init, dict): return ["no system/init event: cannot verify which skills were loaded"]
        if not isinstance(init.get("skills"), list): return ["system/init has no skills list: cannot verify isolation"]
        names = {str(s.get("name") if isinstance(s, dict) else s) for s in init["skills"]}
        out = []
        src = init.get("apiKeySource")
        if src != "none":
            out.append("system/init apiKeySource is %s, expected \"none\" (subscription login): %s" % (
                repr(_shown(src)) if isinstance(src, str) else "missing",
                "the run may have used an API key" if isinstance(src, str) else "cannot verify subscription auth"))
        if not isinstance(init.get("plugins"), list):
            out.append("system/init has no plugins list: cannot verify that no plugin was loaded")
        elif init["plugins"]:
            ids = [str((p.get("source") or p.get("name")) if isinstance(p, dict) else p) for p in init["plugins"]]
            out.append("plugins loaded: %s" % ", ".join(ids))
        want = {skill_name} if with_skill else set()
        if names != want:
            user = sorted({n.split(":")[-1] for n in names - want} & set(user_names))
            out.append("skills loaded in %s run are %s, expected exactly %s%s" % (
                "with_skill" if with_skill else "without_skill", sorted(names), sorted(want),
                "; user-level skills visible: %s" % user if user else ""))
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
    Trigger detection is (추정): Codex documents no skill-invocation event, so a run counts as triggered when an
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
        return {"billing": BILLING, "method": "ChatGPT login (auth_mode chatgpt), read-only copy of auth.json (readable "
                "by the agent under test), removed after the run"}
    def sandbox_problems(self, root, work, skill_name, with_skill):
        out = Engine.sandbox_problems(self, root, work, skill_name, with_skill)
        for label, d in (("sandbox HOME", os.path.join(root, "home", ".agents", "skills")),
                         ("sandbox CODEX_HOME", os.path.join(root, "codex_home", "skills")),
                         ("admin", CODEX_ADMIN_SKILLS)):
            hits = [n for n, p in skills_under(d, 4) if os.sep + ".system" + os.sep not in p + os.sep]
            if hits: out.append("%s skills folder %s has %s" % (label, d, sorted(hits)))
        return out
    def after_run(self, root):
        """The copy must be unchanged. If Codex rewrote it (token refresh, 추정), the refreshed login is deleted with
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

# ---------- per-run isolation (README "Eval isolation") ----------
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

def escaping_links(root):
    """Paths (relative to root) of symlinks a copy of root would follow out of root, or that dangle. SKILL_IGNORE
    entries are not copied, so they are not checked."""
    r, bad, ign = os.path.realpath(root), [], shutil.ignore_patterns(*SKILL_IGNORE)
    for dp, dn, fn in os.walk(root):
        skip = ign(dp, dn + fn)
        dn[:] = [d for d in dn if d not in skip]
        for n in [d for d in dn] + [f for f in fn if f not in skip]:
            p = os.path.join(dp, n)
            if not os.path.islink(p): continue
            t = os.path.realpath(p)
            if not os.path.exists(t) or (t != r and not t.startswith(r.rstrip(os.sep) + os.sep)):
                bad.append(os.path.relpath(p, root))
    return sorted(bad)

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

def isolated_run(eng, prompt, spec, case, skill_path, run_dir, run_root, cap, user_names, keep_workdir):
    """Runs one CLI call in a fresh Sandbox and returns (result, fatal problems). Raises EvalError before the call
    (fail closed, nothing spent) when the sandbox could expose another skill. Afterwards, a leak shown by the run's
    own init report, or Codex changing its read-only auth copy, is returned as fatal: the caller charges the run, records it
    as an error and stops the iteration."""
    sb = Sandbox(eng, spec, case, skill_path, run_root)
    try:
        probs = sb.problems()
        if probs: raise EvalError("isolation (%s): %s" % (eng.name, " | ".join(probs)))
        res = eng.run(prompt, sb.work, with_skill=bool(skill_path), budget_usd=cap, env=sb.env, skill_name=sb.name)
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
    """Grades with `claude -p --json-schema` and no tools (`--tools ""`). Must use a model different from the executor.
    With a finite budget every call gets --max-budget-usd (CLAUDE_CAP_HEADROOM of what is left)."""
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
    def _charge_unknown(self, fallback):
        self.usage["calls"] += 1; self.usage["api_equivalent_usd"] += fallback
        if self.budget is not None: self.budget.charge(fallback)
    def _call(self, prompt, schema):
        cap = None
        if self.budget is not None and self.budget.finite():
            cap = self.budget.claude_cap()
            if cap < 0.01:
                raise EvalError("budget exhausted ($%.2f of $%.2f) before grader call" % (self.budget.spent, self.budget.limit))
        kind = "pairwise" if "winner" in schema.get("properties", {}) else "grader"
        bound = call_bound("grader", self.model, kind, self.prices, DEFAULT_TOKENS) or 0.0
        fallback = cap if cap is not None else bound  # what an unmetered call is charged
        # A fresh empty cwd (no project settings, CLAUDE.md or skills of the folder grading was started from).
        cwd = tempfile.mkdtemp(prefix="skill-eval-grader-")
        try:
            try:
                rc, out, err = launch_claude(self.exec_fn, self.command(prompt, schema, cap), cwd, self.timeout)
            except (EvalError, LiveDisabled):
                raise  # refused before launch: nothing spent
            except Exception as ex:
                self._charge_unknown(fallback)
                raise EvalError("grader call failed after launch (%s); charged $%.2f and stopped" % (type(ex).__name__, fallback))
        finally:
            left = remove_tree(os.path.dirname(cwd), cwd)
            if left: print("WARNING: " + left, file=sys.stderr)
        try: data = json.loads(out)
        except (ValueError, TypeError):
            self._charge_unknown(max(fallback, reported_cost(out) or 0.0))  # usage unknown: charge the cap
            raise EvalError("grader returned non-JSON (exit %s): %s" % (rc, (err or out or "")[-300:]))
        if not isinstance(data, dict): data = {}
        try:
            tin, tout = claude_usage(data)
        except Exception as ex:  # paid call, unreadable usage: charge max(reported, cap) and stop
            charged = api_equivalent(0, 0, data.get("total_cost_usd"), None, fallback)[0]
            self._charge_unknown(charged)
            raise EvalError("grader usage could not be parsed after the call (%s); charged $%.2f and stopped"
                            % (type(ex).__name__, charged))
        cost = api_equivalent(tin, tout, data.get("total_cost_usd"), price_for("grader", self.model, self.prices), fallback)[0]
        self.usage["calls"] += 1
        self.usage["input_tokens"] += tin; self.usage["output_tokens"] += tout; self.usage["api_equivalent_usd"] += cost
        if self.budget is not None: self.budget.charge(cost)
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
    @property
    def record_name(self):
        """What grading.json / comparison-k.json / calibration.json record as the grader."""
        return self.model

def grader_label(engine, model, effort=None):
    return "%s(%s%s)" % (engine, model or "CLI default model", ", effort %s" % effort if effort else "")

CODEX_GRADER_SANDBOX = "read-only"
# Tools the Codex grader never needs (the transcript is inline in the prompt): each is switched off with
# `--disable <feature>` (= -c features.<name>=false, codex-cli 0.162.1 `codex features list`), plus web search off.
# Without a shell / exec / browser / image tool the model has no way to read auth.json from the isolated CODEX_HOME
# (the read-only sandbox only blocks writes). Enforced after the call too: any non-message item fails closed.
CODEX_GRADER_DISABLED_FEATURES = ("shell_tool", "unified_exec", "shell_snapshot", "view_image", "browser_use",
                                  "browser_use_external", "in_app_browser", "computer_use", "apps", "plugins",
                                  "multi_agent", "image_generation", "code_mode_host", "skill_search", "tool_suggest",
                                  "hooks", "sleep_tool")
CODEX_GRADER_ALLOWED_ITEMS = ("agent_message", "reasoning", "error")  # "error": CLI notice (e.g. code mode off), not a tool
_CODEX_GRADER_NOTE_OLD = ("the Codex grader runs `codex exec --sandbox read-only` in a fresh temp folder with an isolated "
                     "CODEX_HOME holding a read-only copy of auth.json; that copy is READABLE by the grader agent (the "
                     "read-only sandbox permits reads), and the grader prompt contains the untrusted transcript of the "
                     "run under test, so a prompt injection could make it quote the token into grading.json / "
                     "comparison-k.json evidence")

CODEX_GRADER_NOTE = ("the Codex grader runs `codex exec --sandbox read-only` with its tools switched off (no shell, "
                     "exec, browser, image or web tool: CODEX_GRADER_DISABLED_FEATURES) in a fresh temp folder with an "
                     "isolated CODEX_HOME holding a read-only copy of auth.json that the codex process needs to log in; "
                     "a call whose events show any tool use, or whose answer contains any auth.json value, is refused")

def codex_grader_command_problems(cmd):
    """The Codex grader launch shape: codex exec --json with exactly one --sandbox read-only, no flag that widens it."""
    out = []
    if cmd[:3] != ["codex", "exec", "--json"]: out.append("not a `codex exec --json` call")
    modes = [cmd[i + 1] for i, x in enumerate(cmd[:-1]) if x in ("--sandbox", "-s")]
    if modes != [CODEX_GRADER_SANDBOX]: out.append("sandbox must be exactly one `--sandbox read-only`, got %s" % modes)
    wide = [x for x in cmd if x in ("--full-auto", "--dangerously-bypass-approvals-and-sandbox", "--yolo", "--add-dir")
            or x.startswith(("--sandbox=", "--add-dir="))]
    if wide: out.append("flags that change the sandbox: %s" % wide)
    off = {cmd[i + 1] for i, x in enumerate(cmd[:-1]) if x == "--disable"}
    miss = [f for f in CODEX_GRADER_DISABLED_FEATURES if f not in off]
    if miss: out.append("grader tools not disabled: %s" % miss)
    if "--enable" in cmd or any(x.startswith("--enable=") for x in cmd): out.append("--enable is not allowed for the grader")
    if 'web_search="disabled"' not in [cmd[i + 1] for i, x in enumerate(cmd[:-1]) if x == "-c"]:
        out.append('web search must be off (-c web_search="disabled")')
    return out

def auth_secret_strings(data):
    """Every string of 16+ chars in a Codex auth.json (and each JWT segment of 24+), in memory only, never printed."""
    try: o = json.loads(data.decode("utf-8"))
    except Exception: return set()
    out, stack = set(), [o]
    while stack:
        x = stack.pop()
        if isinstance(x, dict): stack.extend(x.values())
        elif isinstance(x, list): stack.extend(x)
        elif isinstance(x, str) and len(x) >= 16:
            out.add(x); out.update(p for p in x.split(".") if len(p) >= 24)
    return out

def extract_json_object(text):
    """The JSON object a Codex grader was told to reply with: the whole message, or one ```json fenced block."""
    t = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if m: t = m.group(1)
    data = json.loads(t)
    if not isinstance(data, dict): raise ValueError("not a JSON object")
    return data

class CodexGrader(LLMGrader):
    """Grader and blind pairwise judge through `codex exec --json --sandbox read-only` (Codex is never an eval target
    here). Model: the Codex CLI default unless --grader-model is given; reasoning effort via `-c
    model_reasoning_effort=<level>` (spelling 추정, see CODEX_EFFORT_CONFIG). Runs in a fresh temp folder with the
    same isolated HOME / CODEX_HOME as a Codex run (CodexEngine.sandbox_env: read-only auth.json copy, removed and
    verified gone after the call; see CODEX_GRADER_NOTE for the readability risk). The answer is the final agent
    message, which must be one JSON object (the schema is in the prompt; `--output-schema` is not used: unverified).
    Budget: Codex has no per-call cap, so a call starts only when its worst-case bound (assumed tokens x
    WORST_CASE_FACTOR x price, CODEX_GRADER_ASSUMED_PRICE unless --price grader=...) fits the remaining budget, and
    every call is charged max(usage x price, bound): a conservative charge, still a soft limit."""
    engine = "codex"
    EFFORTS = CODEX_EFFORTS
    def __init__(self, model=None, exec_fn=None, timeout=600, budget=None, prices=None, effort=None, tokens=None):
        LLMGrader.__init__(self, model, exec_fn, timeout, budget, prices, effort)
        self.tokens = tokens or DEFAULT_TOKENS
        self._env_engine = CodexEngine(exec_fn=self.exec_fn)
    @property
    def record_name(self):
        return self.label()
    def price(self):
        return grader_price("codex", self.model, self.prices)
    def bound(self, kind):
        return grader_call_usd(kind, "codex", self.model, self.tokens, self.prices)
    def command(self, prompt, schema, cap=None):
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--sandbox", CODEX_GRADER_SANDBOX]
        for f in CODEX_GRADER_DISABLED_FEATURES: cmd += ["--disable", f]
        cmd += ["-c", 'web_search="disabled"']
        if self.model: cmd += ["-m", self.model]
        if self.effort: cmd += ["-c", CODEX_EFFORT_CONFIG % self.effort]
        return cmd + [codex_grader_prompt(prompt, schema)]
    def _call(self, prompt, schema):
        kind = "pairwise" if "winner" in schema.get("properties", {}) else "grader"
        bound = self.bound(kind)
        if self.budget is not None and self.budget.finite() and bound > self.budget.remaining():
            raise EvalError("codex grader worst-case %s call $%.2f > remaining budget $%.2f (no per-call cap); stopped "
                            "before the call" % (kind, bound, self.budget.remaining()))
        cmd = self.command(prompt, schema)
        probs = codex_grader_command_problems(cmd)
        if probs: raise EvalError("codex grader launch refused: " + "; ".join(probs))
        run_root = tempfile.gettempdir()
        root = tempfile.mkdtemp(prefix="skill-eval-grader-codex-", dir=run_root)
        eng, out, err, rc, failed, left = self._env_engine, "", "", None, None, []
        try:
            work = os.path.join(root, "work"); os.makedirs(work)
            env = eng.sandbox_env(root)  # raises before launch when there is no ChatGPT auth.json: nothing spent
            # the executor's static checks: no project skill in work, none in an ancestor of the sandbox, the
            # sandbox HOME / CODEX_HOME or /etc/codex/skills (refused before launch: nothing spent)
            skills = eng.sandbox_problems(root, work, None, False)
            if skills: raise EvalError("codex grader launch refused: " + "; ".join(skills))
            try:
                rc, out, err = self.exec_fn(cmd, work, self.timeout, env=env)
            except LiveDisabled:
                raise
            except Exception as ex:
                failed = "call failed after launch (%s)" % type(ex).__name__
            left = list(eng.after_run(root))
            try:
                with open(os.path.join(real_codex_home(), "auth.json"), "rb") as f: secrets = auth_secret_strings(f.read())
            except OSError: secrets = set()
            if any(v in (out or "") or v in (err or "") for v in secrets):
                failed = failed or "output contained an auth.json value (discarded, not saved)"
                out, err = "", ""
            secrets = None
        finally:
            gone = remove_sandbox(eng, os.path.realpath(run_root), root)
            left += gone
            if gone and sys.exc_info()[0] is not None: print("WARNING: " + "; ".join(gone), file=sys.stderr)
        res = None
        if failed is None:
            try:
                res = eng.parse(out or "")
            except Exception as ex:
                failed = "output could not be parsed after the call (%s)" % type(ex).__name__
        tin, tout = (res["input_tokens"], res["output_tokens"]) if res else (0, 0)
        cost = max(call_cost((tin, tout), self.price()) or 0.0, bound)  # conservative: never below the bound
        self.usage["calls"] += 1
        self.usage["input_tokens"] += tin; self.usage["output_tokens"] += tout; self.usage["api_equivalent_usd"] += cost
        if self.budget is not None: self.budget.charge(cost)
        if left: raise EvalError("codex grader sandbox: %s; charged $%.2f and stopped" % ("; ".join(left), cost))
        if res is not None and failed is None:
            used = sorted({c.get("tool") for c in res["tool_calls"]} - set(CODEX_GRADER_ALLOWED_ITEMS))
            if used: failed = "used tools although they are disabled (%s); answer discarded" % ", ".join(map(str, used))
        if failed: raise EvalError("codex grader %s; charged $%.2f and stopped" % (failed, cost))
        if res["error"] or rc != 0:
            raise EvalError("codex grader call failed (exit %s, %s); charged $%.2f and stopped"
                            % (rc, (res["error"] or "")[:200], cost))
        try:
            return extract_json_object(res["final"])
        except ValueError as ex:
            raise EvalError("codex grader answer is not one JSON object (%s); charged $%.2f and stopped" % (ex, cost))

def codex_grader_prompt(prompt, schema):
    return ("%s\n\nDo not run commands, read files or use tools: everything you need is above. Reply with exactly one "
            "JSON object and nothing else, matching this JSON Schema:\n%s\n" % (prompt, json.dumps(schema)))

def grader_price(engine, model, prices):
    """(in, out) USD/MTok for the grader: --price grader=... / model overrides first; a Codex grader without one uses
    CODEX_GRADER_ASSUMED_PRICE (assumption)."""
    p = price_for("grader", model, prices)
    if p is None and engine == "codex": p = prices.get("codex") or CODEX_GRADER_ASSUMED_PRICE
    return p

def make_grader(engine, model=None, effort=None, **kw):
    if engine not in GRADER_ENGINES: raise EvalError("unknown grader engine %r (%s)" % (engine, "|".join(GRADER_ENGINES)))
    if engine == "codex": return CodexGrader(model, effort=effort, **kw)
    kw.pop("tokens", None)
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

def call_cost(tokens, price):
    return None if price is None else (tokens[0] * price[0] + tokens[1] * price[1]) / 1e6

def grader_call_usd(kind, engine, model, tokens, prices):
    """Estimated USD of one grader/pairwise call. A Codex grader is charged at least its worst-case bound per call
    (CodexGrader), so the bound is what is estimated."""
    per = call_cost(tokens[kind], grader_price(engine, model, prices))
    return per * WORST_CASE_FACTOR if (per is not None and engine == "codex") else per

def grader_row_model(engine, model):
    return model if engine == "claude" else "codex:%s" % (model or "CLI default")

def estimate(n_cases, n_llm_cases, n_triggers, runs, engines, baseline, grader_model, tokens=None, prices=None,
             pairwise=True, grader_engine="claude"):
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
                who = grader_row_model(grader_engine, grader_model)
                per = grader_call_usd(kind, grader_engine, grader_model, tokens, prices)
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

def grader_estimate(n_grader, n_pairwise, grader_model, tokens, prices, grader_engine="claude"):
    """Grader-only cost: `grade --live` (counts from the iteration on disk) and `calibrate --live` (golden size)."""
    per = {k: grader_call_usd(k, grader_engine, grader_model, tokens, prices) for k in ("grader", "pairwise")}
    return est_result([est_row("grader", k, grader_row_model(grader_engine, grader_model), n, tokens, per[k])
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
    lines.append("total calls %d, API-equivalent total $%.2f (subscription: not billed per token)%s" % (est["calls"], est["total_usd"],
                 "" if not est["unpriced_models"] else "  + UNPRICED: %s (pass --price)" % ", ".join(est["unpriced_models"])))
    lines.append("assumed tokens per call (in,out): " + ", ".join("%s=%d,%d" % (k, v[0], v[1]) for k, v in est["tokens"].items()))
    soft = sorted({r["engine"] for r in est["rows"] if r["calls"] and r["engine"] in ENGINES
                   and budget_enforcement(ENGINES[r["engine"]]) == "soft_limit"})
    for e in soft: lines.append("budget: " + SOFT_LIMIT_NOTE % e)
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

def gate_hash(gate, spec, ws):
    if gate == "requirements":
        return sha256_files(os.path.join(ws, "requirements.md")) if os.path.isfile(os.path.join(ws, "requirements.md")) else None
    if gate == "eval_set":
        return sha256_labeled(eval_set_files(spec["skill_dir"]))
    m = re.match(r"(?:iteration-(\d+)|registration)$", gate)
    if not m: raise EvalError("unknown gate %r (requirements|eval_set|iteration-N|registration)" % gate)
    n = int(m.group(1)) if m.group(1) else (iterations(ws) or [None])[-1]
    bp = os.path.join(ws, "iteration-%s" % n, "benchmark.json")
    return sha256_files(bp) if n is not None and os.path.isfile(bp) else None

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

def approval_ok(gate, spec, ws):
    a = (rjson(os.path.join(ws, "approvals.json"), {}) or {}).get(gate)
    h = gate_hash(gate, spec, ws)
    return bool(a and h and a.get("sha256") == h)

def approve(gate, spec, ws, by):
    h = gate_hash(gate, spec, ws)
    if not h: raise EvalError("nothing to approve for %s (file missing)" % gate)
    fp = os.path.join(ws, "approvals.json")
    data = rjson(fp, {}) or {}
    data[gate] = {"sha256": h, "by": by, "at": now_iso()}
    wjson(fp, data)
    return data[gate]

def preflight(spec, engines, run_root=None, home=None):
    """Read-only, blocks --live (fail closed). Runs execute in per-run sandboxes under run_root (README "Eval
    isolation"); this checks what the sandboxes cannot hide:
    - any .claude/skills (claude) or .agents/skills (codex) folder with skills in run_root or its ancestors
      (project skills are discovered from the cwd upward);
    - claude: a same-named copy in ~/.claude/skills (incl. synced/) or a plugin. --setting-sources project,local
      should hide user skills (docs: the "user" source loads ~/.claude/skills), but whether the CLI flag hides already
      downloaded claude.ai synced skills and plugin skills is not verified (추정), so a same-named copy still blocks;
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
            out += codex_auth_problems()
    return out

# ---------- running ----------
def eval_dir_name(case):
    d = "eval-%s-%s" % (case["id"], case["name"]) if case["name"] != str(case["id"]) else "eval-%s" % case["id"]
    if not re.match(r"^[0-9A-Za-z가-힣._-]+$", d) or ".." in d:  # names come from safe_name() or slug(); defense in depth
        raise EvalError("eval directory name %r is not a safe path component" % d)
    return d

def configs_for(baseline):
    return [PRIMARY, BASELINES[baseline]]

def skill_path_for(config, spec, old_skill):
    return spec["skill_dir"] if config == PRIMARY else (old_skill if config == "old_skill" else None)

class Budget:
    """limit and spent are API-equivalent USD (usage x API prices): with subscription auth nothing is billed per
    token, so this is a usage limiter, not a record of money spent."""
    def __init__(self, usd): self.limit, self.spent = usd, 0.0
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
    """Appends one non-run cost entry (grade, calibrate, builder) to iteration-N/cost.json; runs are in timing.json."""
    fp = os.path.join(it_dir, "cost.json")
    data = rjson(fp, {}) or {}
    data.setdefault("billing", BILLING)
    data.setdefault("entries", []).append({"command": command, "at": now_iso(), "calls": usage.get("calls", 0),
        "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
        "api_equivalent_usd": round(usage.get("api_equivalent_usd", 0.0), 6)})
    wjson(fp, data)

def usage_delta(after, before):
    return {k: after.get(k, 0) - before.get(k, 0) for k in after}

def cost_summary(it_dir):
    """API-equivalent cost of an iteration: every run's timing.json (exec + trigger, per engine) plus cost.json
    entries (grading, calibration, builder). actual_spend_usd stays null: the harness cannot see plan billing."""
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
        "api_equivalent_source": None if api_eq is None else api_eq[1], "billing": BILLING})

class _Fatal(Exception): pass

def run_iteration(spec, ws, engines, runs=DEFAULT_RUNS, baseline="none", old_skill=None, budget=None, prices=None,
                  iteration=None, log=print, tokens=None, run_root=None):
    """Executes every (engine, case, config, run) and every trigger query, each in its own Sandbox under run_root
    (default: the system temp folder). Returns the iteration number."""
    configs = configs_for(baseline)
    for engine in engines:
        if isinstance(engine, ClaudeCodeEngine):
            engine.python_script, engine.skill_dir = spec.get("claude_python_script"), spec["skill_dir"]
    if baseline == "old" and not (old_skill and os.path.isfile(os.path.join(old_skill, "SKILL.md"))):
        raise EvalError("baseline old needs --old-skill (or `snapshot` first): %s" % old_skill)
    run_root = os.path.realpath(run_root or tempfile.gettempdir())
    n = iteration or ((iterations(ws) or [0])[-1] + 1)
    it_dir = os.path.join(ws, "iteration-%d" % n)
    wjson(os.path.join(it_dir, "plan.json"), {"skill_name": spec["skill_name"], "skill_path": spec["skill_dir"],
          "old_skill": old_skill, "configurations": configs, "runs_per_configuration": runs,
          "engines": {e.name: e.model for e in engines}, "evals_run": [c["id"] for c in spec["cases"]],
          "efforts": {e.name: getattr(e, "effort", None) for e in engines},
          "trigger_queries": len(spec["triggers"] or []), "started_at": now_iso(),
          "skill_sha256": skill_tree_hash(spec["skill_dir"]), "eval_set_sha256": gate_hash("eval_set", spec, ws),
          "old_skill_sha256": skill_tree_hash(old_skill) if baseline == "old" else None,
          "isolation": {"run_root": run_root, "per_run_sandbox": True},
          "budget_enforcement": {e.name: budget_enforcement(e) for e in engines}})
    budget, prices, tokens = budget or Budget(float("inf")), prices or {}, tokens or DEFAULT_TOKENS
    spent0 = budget.spent
    user_names = sorted({nm for nm, _ in claude_user_skills(claude_config_dir(), config_dir=True)}) \
        if any(e.name == "claude" for e in engines) else []
    stopped = None

    def one(eng, kind, prompt, case, cfg, rd, keep):
        cap, fallback, stop = guard_call(eng, kind, budget, prices, tokens)
        if stop: return None, stop
        res, fatal = isolated_run(eng, prompt, spec, case, skill_path_for(cfg, spec, old_skill), ensure_inside(it_dir, rd),
                                  run_root, cap, user_names, keep)
        api_eq = run_cost(res, eng, prices, fallback)
        if res.get("launch_failed") and fallback > api_eq[0]:  # failed call: max(reported/usage, cap) is charged
            api_eq = (fallback, "cap_charged")
        budget.charge(api_eq[0])  # the budget limits API-equivalent cost
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
    except (_Fatal, EvalError) as ex:  # isolation could not be guaranteed: stop everything (fail closed)
        stopped = str(ex)
    meta = rjson(os.path.join(it_dir, "plan.json"))
    meta.update(finished_at=now_iso(), billing=BILLING, stopped=stopped,
                api_equivalent_usd=round(budget.spent - spent0, 4))  # exec + trigger runs of this call
    wjson(os.path.join(it_dir, "plan.json"), meta)
    if stopped: log("STOPPED: " + stopped)
    return n

# ---------- grading ----------
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
    return False, "unknown check type"

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

def check_grader_model(it_dir, grader_model, grader_engine="claude"):
    """The grader must differ from the executor models recorded in the iteration's plan.json (not from CLI flags).
    A Codex grader may only grade iterations without Codex runs (Codex is a grader here, never an eval target)."""
    plan = rjson(os.path.join(it_dir, "plan.json"))
    if not isinstance(plan, dict) or not isinstance(plan.get("engines"), dict):
        raise EvalError("%s has no plan.json with executor models; cannot check the grader differs" % it_dir)
    models = plan["engines"]
    if grader_engine == "codex":
        if "codex" in models: raise EvalError("a codex grader cannot grade codex runs; codex is a grader only")
        return
    same = [e for e, m in models.items() if m and m == grader_model]
    if same: raise EvalError("grader model %s equals the executor model of %s; use a different model" % (grader_model, same))
    if any(e == "claude" and not m for e, m in models.items()):
        raise EvalError("claude executor model unknown (CLI default) in plan.json; the grader cannot be shown to differ")

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
    if grader is not None: check_grader_model(it_dir, grader.model, getattr(grader, "engine", "claude"))
    check_scope(spec, scope)
    before = dict(grader.usage) if grader is not None else None
    try:
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
                                     "grader": grader.record_name}
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
                "grader_model": grader.record_name})
            raise
        winner_cfg = {"A": a_cfg, "B": b_cfg}.get(w, "tie")
        wjson(os.path.join(ed, "comparison-%d.json" % k), {
            "winner": w, "reasoning": why, "mapping": {"A": a_cfg, "B": b_cfg}, "winner_configuration": winner_cfg,
            "seed": seed, "grader_model": grader.record_name})

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
            "grader_model": grader.record_name, "golden": os.path.abspath(golden_path),
            "disagreements": [r for r in rows if r["grader"] is not r["human"]]}

def calibrate_metered(golden_path, grader, it_dir):
    """calibrate() plus its API-equivalent grader cost in it_dir/cost.json (recorded even when it stops early)."""
    before = dict(grader.usage)
    try:
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
                          "current_eval_set_sha256": gate_hash("eval_set", spec, ws)},
             "runs": runs, "run_summary": summarize(runs), "per_engine": per_engine, "trigger": trig, "pairwise": pw,
             "static_failures": static_fail, "pressure": pressure, "completeness": completeness,
             "execution_errors": exec_errors,
             # API-equivalent cost (usage x API prices / CLI-reported) kept apart from actual spend (subscription)
             "cost": cost_summary(it_dir),
             # counted from grading.json (not the human_review.json queue): an item is graded only once grade wrote it
             "human_pending": sum(1 for r in runs for e in r["expectations"]
                                  if e.get("layer") == "human" and not isinstance(e.get("passed"), bool)),
             "llm_not_graded": sum(1 for r in runs for e in r["expectations"]
                                   if e.get("layer") != "human" and not isinstance(e.get("passed"), bool)),
             "calibration": rjson(os.path.join(it_dir, "calibration.json")),
             "notes": analyzer_notes(runs)}
    return bench

def compute_gate(bench, prev=None):
    problems, notes = [], []
    for eng, rs in (bench.get("per_engine") or {}).items():
        base = next((c for c in rs if c in BASELINES.values()), None)
        if PRIMARY in rs and base and rs[PRIMARY]["graded_runs"] and rs[base]["graded_runs"]:
            w, b = rs[PRIMARY]["pass_rate"]["mean"], rs[base]["pass_rate"]["mean"]
            if w < b - REGRESSION_TOLERANCE:
                problems.append("regression [%s]: with_skill pass_rate %.2f < %s %.2f" % (eng, w, base, b))
        pr = ((prev or {}).get("per_engine") or {}).get(eng, {}).get(PRIMARY)
        if pr and pr.get("graded_runs") and rs.get(PRIMARY, {}).get("graded_runs"):
            if rs[PRIMARY]["pass_rate"]["mean"] < pr["pass_rate"]["mean"] - REGRESSION_TOLERANCE:
                problems.append("regression [%s]: with_skill pass_rate %.2f < previous iteration %.2f"
                                % (eng, rs[PRIMARY]["pass_rate"]["mean"], pr["pass_rate"]["mean"]))
    cal = bench.get("calibration")
    if cal and cal.get("agreement", 1.0) < AGREEMENT_MIN:
        problems.append("low grader agreement: %.2f < %.2f on %d golden examples" % (cal["agreement"], AGREEMENT_MIN, cal["n"]))
    elif not cal:
        notes.append("grader not calibrated for this iteration (run `calibrate --golden ...`)")
    if bench.get("static_failures"):
        problems.append("failing static checks in with_skill runs: %d" % len(bench["static_failures"]))
    stopped = (bench.get("metadata") or {}).get("stopped")
    if stopped: problems.append("incomplete: run stopped early (%s)" % stopped)
    comp = bench.get("completeness")
    if comp is None:
        problems.append("incomplete: no plan.json, expected run counts unknown")
    for eng, c in sorted((comp or {}).items()):
        if c["graded_runs"] < c["expected_runs"]:
            problems.append("incomplete [%s]: %d of %d eval runs graded" % (eng, c["graded_runs"], c["expected_runs"]))
        if c["trigger_runs"] < c["expected_trigger_runs"]:
            problems.append("incomplete [%s]: %d of %d trigger runs" % (eng, c["trigger_runs"], c["expected_trigger_runs"]))
        if c.get("trigger_errors"):
            problems.append("execution failed [%s]: %d trigger run(s) with errors" % (eng, c["trigger_errors"]))
        if c.get("comparisons", 0) < c.get("expected_comparisons", 0):
            problems.append("incomplete [%s]: %d of %d blind pairwise comparisons (run `grade --live`)"
                            % (eng, c.get("comparisons", 0), c["expected_comparisons"]))
    m = bench.get("metadata") or {}
    if comp is not None:
        if not m.get("skill_sha256") or not m.get("eval_set_sha256"):
            problems.append("unbound: plan.json records no skill/eval-set hash; re-run the iteration")
        else:
            if m.get("current_skill_sha256") != m["skill_sha256"]:
                problems.append("stale: the skill changed after this iteration ran (re-run before trusting this report)")
            if m.get("current_eval_set_sha256") != m["eval_set_sha256"]:
                problems.append("stale: the eval set changed after this iteration ran")
    if bench.get("execution_errors"):
        problems.append("execution failed in %d eval run(s)" % len(bench["execution_errors"]))
    for eng, cf in sorted((bench.get("trigger") or {}).items()):
        t = cf.get(PRIMARY)
        if t and t.get("failed"):
            problems.append("trigger criteria failed [%s]: %d of %d queries" % (eng, t["failed"], t["total"]))
    if bench.get("llm_not_graded"):
        problems.append("ungraded: %d LLM item(s) not graded (run `grade --live`)" % bench["llm_not_graded"])
    if bench.get("human_pending"):
        problems.append("ungraded: %d human item(s) pending review (fill human_review.json, re-run grade)" % bench["human_pending"])
    return {"problems": problems, "needs_approval": bool(problems), "notes": notes,
            "human_pending_only": bool(problems) and all(p.startswith("ungraded: ") and "human" in p for p in problems)}

def fmt_stat(s, pct=False):
    return ("%.0f%% ± %.0f%%" % (s["mean"] * 100, s["stddev"] * 100)) if pct else ("%.1f ± %.1f" % (s["mean"], s["stddev"]))

def comparison_md(bench, gate, n):
    m = bench["metadata"]
    L = ["# %s — iteration %d" % (m["skill_name"], n), "",
         "- configurations: %s, runs per configuration: %s, engines: %s" % (", ".join(m.get("configurations") or []),
             m.get("runs_per_configuration"), ", ".join("%s(%s)" % (k, v or "default") for k, v in (m.get("engines") or {}).items())),
         "- API-equivalent cost: $%.2f (usage x API prices; the budget limit); actual spend: not metered (%s auth)%s"
         % (((bench.get("cost") or {}).get("api_equivalent_usd") or {}).get("total", 0.0), m.get("billing") or BILLING,
            ", STOPPED: %s" % m["stopped"] if m.get("stopped") else ""), "",
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
        L += ["| **total** | **%.4f** |" % ae["total"], "| actual spend | not metered |"]
        if c.get("runs_without_usage"):
            L.append("- %d run(s) reported no usage and no price: counted as $0 here" % c["runs_without_usage"])
    if bench["notes"]:
        L += ["", "## Notes", ""] + ["- " + x for x in bench["notes"]]
    return "\n".join(L) + "\n"

def report_iteration(spec, ws, n):
    bench = build_benchmark(spec, ws, n)
    prev = rjson(os.path.join(ws, "iteration-%d" % (n - 1), "benchmark.json")) if n > 1 else None
    gate = compute_gate(bench, prev)
    bench["gate"] = gate
    it_dir = os.path.join(ws, "iteration-%d" % n)
    wjson(os.path.join(it_dir, "benchmark.json"), bench)
    wjson(os.path.join(it_dir, "gate.json"), gate)
    wtext(os.path.join(it_dir, "comparison.md"), comparison_md(bench, gate, n))
    return bench, gate

def register(spec, ws, by=None):
    its = iterations(ws)
    if not its: raise EvalError("no iteration to register in %s" % ws)
    n = its[-1]
    gate = rjson(os.path.join(ws, "iteration-%d" % n, "gate.json"))
    bench = rjson(os.path.join(ws, "iteration-%d" % n, "benchmark.json"))
    if gate is None or bench is None: raise EvalError("iteration-%d has no report; run `report` first" % n)
    if bench.get("gate") != gate: raise EvalError("iteration-%d gate.json does not match benchmark.json; re-run `report`" % n)
    m = bench.get("metadata") or {}
    now_skill, now_evals = skill_tree_hash(spec["skill_dir"]), gate_hash("eval_set", spec, ws)
    if not m.get("skill_sha256") or m["skill_sha256"] != now_skill:
        raise EvalError("iteration-%d tested skill %s but the skill folder now hashes to %s; re-run the eval before "
                        "registering" % (n, (m.get("skill_sha256") or "<unrecorded>")[:12], now_skill[:12]))
    if not m.get("eval_set_sha256") or m["eval_set_sha256"] != now_evals:
        raise EvalError("iteration-%d used a different eval set than the one on disk; re-run the eval" % n)
    if gate["needs_approval"] and not approval_ok("registration", spec, ws):
        raise EvalError("iteration-%d has problems %s; registration needs `approve --gate registration`" % (n, gate["problems"]))
    rec = {"skill_name": spec["skill_name"], "skill_path": spec["skill_dir"], "iteration": n,
           "benchmark_sha256": gate_hash("iteration-%d" % n, spec, ws), "skill_sha256": now_skill,
           "eval_set_sha256": now_evals,
           "skill_md_sha256": sha256_files(os.path.join(spec["skill_dir"], "SKILL.md")), "registered_at": now_iso(),
           "approval": (rjson(os.path.join(ws, "approvals.json"), {}) or {}).get("registration") if gate["needs_approval"] else "auto"}
    wjson(os.path.join(ws, "registration.json"), rec)
    return rec

class ClaudeBuilder:
    """The only builder `loop --live` accepts: one metered `claude -p` call per iteration through live_exec, run in the
    skill folder with file tools only (no Bash, so it cannot start other paid CLIs), a --max-budget-usd cap from the
    shared Budget, and its cost charged to that Budget. The iteration's comparison.md is inlined into the prompt."""
    TOOLS = "Read,Edit,Write,Glob,Grep"
    def __init__(self, instructions, model, budget, prices=None, exec_fn=None, timeout=900, skill_name=None):
        self.instructions, self.model, self.budget, self.prices = instructions, model, budget, prices or {}
        self.exec_fn, self.timeout, self.calls = exec_fn or live_exec, timeout, 0
        self.skill_name = skill_name  # the skill being built: never set off by skillOverrides
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "api_equivalent_usd": 0.0}
    def command(self, prompt, cap):
        cmd = ["claude", "-p", prompt, "--model", self.model, "--output-format", "json", "--no-session-persistence",
               "--permission-mode", "acceptEdits", "--tools", self.TOOLS, "--disable-slash-commands"]
        cmd += claude_isolation_args(self.skill_name)
        return cmd + (["--max-budget-usd", "%.2f" % cap] if cap is not None else [])
    def _record(self, it_dir, tin, tout, cost):
        if self.budget is not None: self.budget.charge(cost)
        self.usage["calls"] += 1; self.usage["input_tokens"] += tin
        self.usage["output_tokens"] += tout; self.usage["api_equivalent_usd"] += cost
        append_cost(it_dir, "builder", {"calls": 1, "input_tokens": tin, "output_tokens": tout, "api_equivalent_usd": cost})
    def __call__(self, it_dir, skill_dir):
        cap = None
        if self.budget is not None and self.budget.finite():
            cap = self.budget.claude_cap()
            if cap < 0.01:
                raise EvalError("budget exhausted ($%.2f of $%.2f) before builder call" % (self.budget.spent, self.budget.limit))
        report = ""
        rp = os.path.join(it_dir, "comparison.md")
        if os.path.isfile(rp):
            with open(rp, encoding="utf-8") as f: report = f.read()[:TRANSCRIPT_CHARS]
        prompt = ("%s\n\nYou are improving the agent skill in the current directory (SKILL.md and its files). "
                  "Do not edit evals/ (the eval set is approved separately; editing it stops the loop).\n\n"
                  "Eval report of the last iteration:\n%s\n" % (self.instructions.strip(), report))
        bound = call_bound("builder", self.model, "builder", self.prices, DEFAULT_TOKENS) or 0.0
        fallback = cap if cap is not None else bound  # no usage: the cap
        try:
            rc, out, err = launch_claude(self.exec_fn, self.command(prompt, cap), skill_dir, self.timeout,
                                         skill_name=self.skill_name)
        except (EvalError, LiveDisabled):
            raise  # refused before launch: nothing spent
        except Exception as ex:
            self.calls += 1; self._record(it_dir, 0, 0, fallback)
            raise EvalError("builder call failed after launch (%s); charged $%.2f and stopped" % (type(ex).__name__, fallback))
        self.calls += 1
        try: data = json.loads(out or "")
        except (ValueError, TypeError): data = None
        if not isinstance(data, dict):  # paid call, unreadable output: charge max(reported, cap) and stop
            charged = max(fallback, reported_cost(out) or 0.0)
            self._record(it_dir, 0, 0, charged)
            raise EvalError("builder output is not a JSON object (exit %s); charged $%.2f and stopped" % (rc, charged))
        if data.get("type") != "result":  # a JSON object but not the CLI result (e.g. {}): same charge and stop
            charged = max(fallback, reported_cost(out) or 0.0)
            self._record(it_dir, 0, 0, charged)
            raise EvalError("builder output is not a result object (type %r, exit %s); charged $%.2f and stopped"
                            % (data.get("type"), rc, charged))
        if data.get("is_error"):  # the CLI reported a failed call (even with exit 0): same charge and stop
            charged = max(fallback, reported_cost(out) or 0.0)
            self._record(it_dir, 0, 0, charged)
            raise EvalError("builder result is an error (subtype %r, exit %s); charged $%.2f and stopped"
                            % (data.get("subtype"), rc, charged))
        try:
            tin, tout = claude_usage(data)
        except Exception as ex:  # paid call, unreadable usage: charge max(reported, cap) and stop
            charged = api_equivalent(0, 0, data.get("total_cost_usd"), None, fallback)[0]
            self._record(it_dir, 0, 0, charged)
            raise EvalError("builder usage could not be parsed after the call (%s); charged $%.2f and stopped"
                            % (type(ex).__name__, charged))
        cost = api_equivalent(tin, tout, data.get("total_cost_usd"), price_for("builder", self.model, self.prices), fallback)[0]
        self._record(it_dir, tin, tout, cost)
        return rc

def loop(spec, ws, engines, grader, builder, max_iterations=MAX_AUTO_ITERATIONS, runs=DEFAULT_RUNS, baseline="none",
         old_skill=None, budget=None, prices=None, golden=None, seed=None, log=print, tokens=None, precheck=None,
         run_root=None):
    """run -> grade -> (calibrate) -> report; stop when the gate is clean; otherwise call builder(iteration_dir,
    skill_dir) and reload the skill. precheck(spec, iterations_left) -> [problems] runs before EVERY iteration (the
    CLI passes the full --live gate set: validation, approvals incl. fixture hashes, preflight, cost vs remaining
    budget); any problem stops the loop. At most MAX_AUTO_ITERATIONS automatic iterations."""
    if max_iterations > MAX_AUTO_ITERATIONS:
        raise EvalError("max %d automatic iterations, got %d" % (MAX_AUTO_ITERATIONS, max_iterations))
    n = None
    for i in range(max_iterations):
        if precheck is not None:
            probs = precheck(spec, max_iterations - i)
            if probs: return {"status": "gates-failed", "iteration": n, "problems": probs}
        n = run_iteration(spec, ws, engines, runs, baseline, old_skill, budget, prices, log=log, tokens=tokens,
                          run_root=run_root)
        stopped = (rjson(os.path.join(ws, "iteration-%d" % n, "plan.json"), {}) or {}).get("stopped")
        if stopped: return {"status": "run-stopped", "iteration": n, "error": stopped}  # no grading, no builder
        grade_iteration(spec, ws, n, grader, seed, log=log)
        if golden: wjson(os.path.join(ws, "iteration-%d" % n, "calibration.json"),
                         calibrate_metered(golden, grader, os.path.join(ws, "iteration-%d" % n)))
        _, gate = report_iteration(spec, ws, n)
        log("iteration-%d gate: %s" % (n, gate["problems"] or "clean"))
        if not gate["problems"]: return {"status": "clean", "iteration": n}
        if gate.get("human_pending_only"): return {"status": "needs-human-review", "iteration": n}
        if budget is not None and budget.remaining() <= 0: return {"status": "budget-exhausted", "iteration": n}
        if i == max_iterations - 1 or builder is None: break
        try:
            rc = builder(os.path.join(ws, "iteration-%d" % n), spec["skill_dir"])
        except EvalError as ex:
            return {"status": "builder-failed", "iteration": n, "error": str(ex)}
        if rc != 0: return {"status": "builder-failed", "iteration": n, "returncode": rc}
        spec, problems = load_eval_set(spec["skill_dir"])
        if problems: return {"status": "eval-set-invalid", "iteration": n, "problems": problems}
    return {"status": "needs-approval", "iteration": n}

# ---------- CLI ----------
def make_engines(a, spec=None):
    out = []
    for name in a.engines.split(","):
        name = name.strip()
        if name not in ENGINES: raise EvalError("unknown engine %r (claude|codex)" % name)
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

def plan_text(spec, problems, a, engines, ws, est):
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
    L += ["approval gates: requirements=%s, eval_set=%s" % (
        "approved" if approval_ok("requirements", spec, ws) else "NOT APPROVED",
        "approved" if approval_ok("eval_set", spec, ws) else "NOT APPROVED")]
    L += ["preflight problems (block --live):"] + ["  - " + w for w in preflight(spec, [e.name for e in engines], a.run_root)]
    names = [e.name for e in engines]
    notes = claude_shared_skill_notes(claude_config_dir()) if "claude" in names else []
    if notes: L += ["isolation notes (not blocking):"] + ["  - " + x for x in notes]
    L += ["auth: subscription only (Claude: existing claude.ai Keychain login; Codex: ChatGPT login); `claude auth "
          "status --json` is checked only by a real --live (not by --dry-run)"]
    if grader_engine_of(a) == "codex":
        L += ["grader budget: SOFT LIMIT (codex has no per-call cap): each grader/pairwise call starts only if its "
              "worst-case bound fits the remaining budget and is charged max(usage x price, bound); price %s" % (
                  "from --price" if price_for("grader", a.grader_model, parse_price_overrides(getattr(a, "price", None)))
                  else "ASSUMED $%.2f/$%.2f per MTok (not verified, override with --price grader=IN,OUT)"
                  % CODEX_GRADER_ASSUMED_PRICE),
              "grader isolation: " + CODEX_GRADER_NOTE]
    soft = [e.name for e in engines if budget_enforcement(e) == "soft_limit"]
    if soft:
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
    ge = grader_engine_of(a)
    if a.cmd == "grade":
        check_grader_model(os.path.join(ws, "iteration-%d" % n), a.grader_model, ge)
        check_scope(spec, grade_scope(a))
        ng, npw = grade_counts(spec, os.path.join(ws, "iteration-%d" % n), grade_scope(a))
        return grader_estimate(ng, npw, a.grader_model, tokens, prices, ge)
    if a.cmd == "calibrate":
        return grader_estimate(len(load_golden(a.golden)), 0, a.grader_model, tokens, prices, ge)
    n_cases, n_llm, n_trig = spec_counts(spec, None)
    est = estimate(n_cases, n_llm, n_trig, a.runs, [(e.name, e.model) for e in engines], a.baseline, a.grader_model,
                   tokens, prices, grader_engine=ge)
    if a.cmd == "run":
        return est_result([r for r in est["rows"] if r["kind"] in ("exec", "trigger")], tokens)
    if a.cmd == "loop":  # every automatic iteration may run, each with its calibration, plus a builder call between
        k = a.max_iterations if iterations_left is None else iterations_left
        cal = grader_estimate(len(load_golden(a.golden)), 0, a.grader_model, tokens, prices, ge)["rows"][:1] if a.golden else []
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
    claude.ai login whenever the command calls claude (Claude engine, grader for grade/calibrate/loop, builder)."""
    out = [] if a.cmd in ("run", "loop") else api_key_env_problems()  # run/loop: reported by preflight
    if needs_claude_auth(a, engines):
        out += claude_auth_problems()
    if grades_now(a) and grader_engine_of(a) == "codex":
        out += codex_auth_problems()  # reads only the auth_mode field of auth.json
    return out

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
CODEX_AUTH_SKIPPED = ("skipped in dry run: the Codex grader's ChatGPT login (auth_mode of auth.json; only the real "
                      "--live reads it)")

def check_live_gates(a, spec, problems, ws, n, engines, tokens, prices, budget_left, iterations_left=None, report=None):
    """Every --live gate; raises EvalError on the first failure. `loop` calls it again before each iteration with the
    reloaded eval set, the remaining iterations and the remaining budget. report: a list for --dry-run: every failure
    is appended instead of raised, and `claude auth status` is not executed (AUTH_STATUS_SKIPPED is appended)."""
    def fail(msg):
        if report is None: raise EvalError(msg)
        report.append(msg)
    if problems: fail("eval set has %d validation problem(s); run `validate`" % len(problems))
    soft = [e.name for e in engines if budget_enforcement(e) == "soft_limit"] if a.cmd in ("run", "loop") else []
    if getattr(a, "strict_budget", True) and soft:
        fail("--strict-budget (default): %s has no enforceable per-call budget cap (its budget is only a soft limit); "
             "refusing the live run (drop it from --engines; --no-strict-budget overrides this, risky)" % ", ".join(soft))
    if a.cmd in ("run", "loop") and grader_engine_of(a) == "codex" and any(e.name == "codex" for e in engines):
        fail("--grader-engine codex with codex under test: a codex grader cannot grade codex runs (`grade` refuses "
             "them), so the codex runs would be paid for and never graded; drop codex from --engines or use "
             "--grader-engine claude")
    if report is None:
        auth = subscription_problems(a, engines)
        if auth: fail("subscription auth: " + " | ".join(auth))
    else:
        env = [] if a.cmd in ("run", "loop") else api_key_env_problems()  # run/loop: reported by preflight
        if env: fail("subscription auth: " + " | ".join(env))
        if needs_claude_auth(a, engines):
            report.append(AUTH_STATUS_SKIPPED)
        if grades_now(a) and grader_engine_of(a) == "codex":
            report.append(CODEX_AUTH_SKIPPED)
    if grader_engine_of(a) == "claude":  # the grader must differ from the model under test
        same = [e.label() for e in engines if e.name == "claude" and e.model and e.model == a.grader_model]
        if same and a.cmd in ("run", "loop"):
            fail("grader %s is the model under test %s; pick another --grader-model or --grader-engine codex"
                 % (a.grader_model, ", ".join(same)))
    for g in ("requirements", "eval_set"):
        if not approval_ok(g, spec, ws): fail("gate %s not approved (write %s/requirements.md, then `approve --gate %s`)" % (g, ws, g))
    live_est = live_estimate(a, spec, ws, n, engines, tokens, prices, iterations_left)
    if live_est["unpriced_models"]: fail("no price for %s; pass --price" % live_est["unpriced_models"])
    if a.cmd in ("run", "loop"):
        pf = preflight(spec, [e.name for e in engines], a.run_root)
        if pf: fail("preflight: " + " | ".join(pf))
        for e in engines:
            if e.has_call_cap: continue
            for kind in ("exec", "trigger"):
                b = call_bound(e.name, e.model, kind, prices, tokens)
                if b is not None and b > budget_left:
                    fail("%s worst-case %s call bound $%.2f exceeds budget $%.2f (%s budget is a soft "
                         "limit: no per-call cap)" % (e.name, kind, b, budget_left, e.name))
    if live_est["total_usd"] > budget_left:
        fail("estimate $%.2f exceeds budget $%.2f\n%s" % (live_est["total_usd"], budget_left, format_estimate(live_est)))
    return live_est

def live_dry_run(a, spec, problems, ws, n, engines, tokens, prices):
    """`--live --dry-run`: the plan of this exact command plus every live gate it would face, collected instead of
    raised. Nothing is executed: live mode stays off, no CLI (not even `claude auth status`) and no model is called,
    no workspace file is written. Returns 0 when only the skipped auth status check is left, else 3."""
    report = []
    live_est = check_live_gates(a, spec, problems, ws, n, engines, tokens, prices, a.budget_usd, report=report)
    skipped = (AUTH_STATUS_SKIPPED, CODEX_AUTH_SKIPPED)
    fails = [r for r in report if r not in skipped]
    print(plan_text(spec, problems, a, engines, ws, live_est))
    print("\nLIVE DRY RUN: nothing was executed (no model call, no `claude auth status`, no files written).")
    print("budget: API-equivalent estimate $%.2f vs --budget-usd $%.2f (%s)" % (
        live_est["total_usd"], a.budget_usd, "fits" if live_est["total_usd"] <= a.budget_usd else "EXCEEDS"))
    print("live gates that would refuse this command (%d):" % len(fails))
    for r in fails: print("  - " + r.replace("\n", "\n    "))
    for r in skipped:
        if r in report: print("  (%s)" % r)
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

def spec_counts(spec, a):
    """Counts from the eval set; a (argparse namespace) may override them for estimate/plan only (a=None: no overrides)."""
    o = lambda k: getattr(a, k, None) if a is not None else None
    n_cases = o("cases") if o("cases") is not None else len(spec["cases"])
    n_llm = o("llm_cases") if o("llm_cases") is not None else sum(1 for c in spec["cases"] if any(i["layer"] == "llm" for i in c["items"]))
    n_trig = o("trigger_queries") if o("trigger_queries") is not None else len(spec["triggers"] or [])
    return n_cases, n_llm, n_trig

def build_parser():
    p = argparse.ArgumentParser(prog="skill_eval", description=__doc__.split("\n")[0])
    sp = p.add_subparsers(dest="cmd", required=True)
    def common(s, run_opts=True):
        s.add_argument("skill")
        s.add_argument("--workspace")
        if run_opts:
            s.add_argument("--engines", default="claude,codex")
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
                           help="grader and blind pairwise judge: claude (claude -p, no tools, capped) or codex (codex "
                                "exec --sandbox read-only, CLI default model, soft budget; its auth.json copy is "
                                "readable by the grader). Codex is never an eval target through this option")
            s.add_argument("--grader-model", default=None,
                           help="default: %s for --grader-engine claude, the CLI default model for codex"
                                % DEFAULT_GRADER_MODEL)
            s.add_argument("--grader-effort", help="grader effort: claude %s, codex %s (codex: -c %s, 추정)"
                           % ("|".join(CLAUDE_EFFORTS), "|".join(CODEX_EFFORTS), CODEX_EFFORT_CONFIG % "<level>"))
            s.add_argument("--price", action="append", help="KEY=IN,OUT USD/MTok; KEY = engine name, model id or 'grader'")
            s.add_argument("--tokens", action="append", help="KIND=IN,OUT assumed tokens per call; KIND exec|trigger|grader|pairwise")
            s.add_argument("--cases", type=int); s.add_argument("--llm-cases", type=int); s.add_argument("--trigger-queries", type=int)
            s.add_argument("--live", action="store_true")
            s.add_argument("--budget-usd", type=float, help="limit in API-equivalent USD (usage x API prices); "
                           "subscription runs are not billed per token")
            s.add_argument("--strict-budget", dest="strict_budget", action="store_true", default=True,
                           help="(default) refuse --live runs of engines without an enforceable per-call cap (codex)")
            s.add_argument("--no-strict-budget", dest="strict_budget", action="store_false",
                           help="RISKY: allow codex --live runs: its budget is only a soft limit (one call can exceed "
                                "--budget-usd) and its auth.json copy is readable by the agent under test")
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
            s.add_argument("--builder-prompt", help="file with instructions for the metered Claude builder (see README)")
            s.add_argument("--builder-model", default=DEFAULT_BUILDER_MODEL)
            s.add_argument("--max-iterations", type=int, default=MAX_AUTO_ITERATIONS)
            s.add_argument("--golden")
    s = sp.add_parser("approve"); common(s, False)
    s.add_argument("--gate", required=True); s.add_argument("--by", required=True)
    s = sp.add_parser("register"); common(s, False)
    s = sp.add_parser("snapshot"); common(s, False)
    sp.add_parser("check-builtin-plugins", help="free: `claude --settings <launch settings> plugin list --json` and "
                  "the enabled state of every @builtin plugin (no model call; loaded skills are not shown)")
    return p

def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if a.cmd == "check-builtin-plugins":
        rc, lines = builtin_plugin_check()
        print("\n".join(lines)); return rc
    a.argv = list(sys.argv[1:] if argv is None else argv)
    if hasattr(a, "grader_engine") and a.grader_model is None and a.grader_engine == "claude":
        a.grader_model = DEFAULT_GRADER_MODEL
    try:
        return _dispatch(a)
    except EvalError as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 2
    finally:
        _LIVE["on"] = False

def _dispatch(a):
    spec, problems = load_eval_set(a.skill)
    if a.cmd == "validate":  # before the workspace: an unsafe SKILL.md name is reported, not raised
        print("\n".join(problems) if problems else "eval set OK: %d cases, %d trigger queries" % (len(spec["cases"]), len(spec["triggers"] or [])))
        return 1 if problems else 0
    ws = os.path.realpath(a.workspace) if a.workspace else default_workspace(spec)
    if a.cmd == "approve":
        rec = approve(a.gate, spec, ws, a.by); print("approved %s sha256=%s" % (a.gate, rec["sha256"][:12])); return 0
    if a.cmd == "register":
        rec = register(spec, ws); print("registered %s iteration-%d (%s)" % (rec["skill_name"], rec["iteration"],
                                        "auto" if rec["approval"] == "auto" else "approved")); return 0
    if a.cmd == "snapshot":
        dst = os.path.join(ws, "skill-snapshot")
        if os.path.exists(dst): raise EvalError("%s exists" % dst)
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
    if a.cmd == "loop" and a.builder_prompt: read_builder_prompt(a)
    engines = make_engines(a, spec)
    if a.baseline == "old" and not a.old_skill and os.path.isdir(os.path.join(ws, "skill-snapshot")):
        a.old_skill = os.path.join(ws, "skill-snapshot")
    prices = parse_price_overrides(a.price)
    tokens = parse_token_overrides(a.tokens)
    n_cases, n_llm, n_trig = spec_counts(spec, a)
    make_grader(a.grader_engine, a.grader_model, a.grader_effort)  # validates --grader-effort before anything runs
    est = estimate(n_cases, n_llm, n_trig, a.runs, [(e.name, e.model) for e in engines], a.baseline, a.grader_model,
                   tokens, prices, grader_engine=a.grader_engine)
    if a.cmd == "estimate":
        print(format_estimate(est)); return 0
    live_cmds = ("run", "grade", "calibrate", "loop")
    if a.dry_run and not a.live:
        raise EvalError("--dry-run checks the gates of a --live command: add --live (with --dry-run it still executes "
                        "nothing); without --live every command is already a dry run")
    if a.cmd == "plan" or (a.cmd in live_cmds and not a.live and a.cmd != "grade"):
        print(plan_text(spec, problems, a, engines, ws, est))
        print("\nDRY RUN: nothing was executed. Real runs need --live --budget-usd X (API-equivalent USD), subscription "
              "auth only, approved requirements/eval_set, no validation or preflight problems, and an estimate within budget.")
        return 0
    if a.cmd == "grade" and not a.live:
        n = a.iteration or (iterations(ws) or [None])[-1]
        if n is None: raise EvalError("no iterations in %s" % ws)
        grade_iteration(spec, ws, n, None, scope=grade_scope(a)); print("static + human queue only (dry run); LLM items not graded"); return 0
    # --- live: the same gates for run, loop, grade and calibrate ---
    if a.budget_usd is None or not math.isfinite(a.budget_usd) or a.budget_usd <= 0:
        raise EvalError("--live needs a finite --budget-usd > 0")
    n = None
    if a.cmd in ("grade", "calibrate"):
        n = a.iteration or (iterations(ws) or [None])[-1]
        if n is None or not os.path.isdir(os.path.join(ws, "iteration-%d" % n)): raise EvalError("no iterations in %s" % ws)
    if a.dry_run:
        return live_dry_run(a, spec, problems, ws, n, engines, tokens, prices)
    check_live_gates(a, spec, problems, ws, n, engines, tokens, prices, a.budget_usd)
    if a.cmd in ("run", "loop"):
        for e in engines:
            if budget_enforcement(e) == "soft_limit":
                print("WARNING: budget " + SOFT_LIMIT_NOTE % e.name, file=sys.stderr)
                print("WARNING: " + NO_STRICT_WARNING % e.name, file=sys.stderr)
    budget = Budget(a.budget_usd)
    grader = make_grader(a.grader_engine, a.grader_model, a.grader_effort, budget=budget, prices=prices, tokens=tokens)
    _LIVE["on"] = True
    if a.cmd == "run":
        n = run_iteration(spec, ws, engines, a.runs, a.baseline, a.old_skill, budget, prices, tokens=tokens,
                          run_root=a.run_root)
        print("iteration-%d done, API-equivalent ~$%.2f (subscription: not billed per token)" % (n, budget.spent)); return 0
    if a.cmd == "grade":
        grade_iteration(spec, ws, n, grader, a.seed, scope=grade_scope(a))
        print("graded iteration-%d, API-equivalent ~$%.2f (subscription: not billed per token)" % (n, budget.spent)); return 0
    if a.cmd == "calibrate":
        cal = calibrate_metered(a.golden, grader, os.path.join(ws, "iteration-%d" % n))
        wjson(os.path.join(ws, "iteration-%d" % n, "calibration.json"), cal)
        print("agreement %.2f kappa %.2f (n=%d)" % (cal["agreement"], cal["kappa"], cal["n"])); return 0
    if a.cmd == "loop":
        builder = ClaudeBuilder(read_builder_prompt(a), a.builder_model, budget, prices,
                                skill_name=spec["skill_name"]) if a.builder_prompt else None
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
