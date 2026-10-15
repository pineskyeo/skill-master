#!/usr/bin/env python3
"""Skill-eval harness: run a skill's eval set with the skill on vs off (or new vs old), grade, aggregate, gate.

File formats follow Anthropic skill-creator (references/schemas.md, scripts/aggregate_benchmark.py,
scripts/run_eval.py) and agentskills.io (evaluating-skills, optimizing-descriptions); deviations are listed
in README.md ("스킬 평가 하네스"). Python 3.9 standard library only.

Safety: no model or CLI is ever executed unless the command line has --live AND a finite --budget-usd, the approval
gates pass, and the cost estimate fits the budget. Without --live every command is a dry run: it prints the
plan and the estimated cost. Engines and graders take an injectable exec_fn so tests run fully mocked.

Usage: python3 skill_eval.py <validate|estimate|plan|run|grade|calibrate|report|approve|register|snapshot|loop> <skill> ...
       (also reachable as: python3 eva.py eval <subcommand> ...)
"""
import argparse, datetime, hashlib, json, math, os, random, re, shutil, subprocess, sys, tempfile, time

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
STATIC_TYPES = ("regex", "not_regex", "tool_call", "skill_triggered", "file_exists")

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
# Per-call budget control (H). Claude: --max-budget-usd gets only this share of the remaining budget, because how far
# the CLI can overshoot its cap is not documented. Codex has no per-call cap: a call starts only when its worst-case
# bound (assumed tokens x WORST_CASE_FACTOR x price) fits the remaining budget. That bound is NOT a hard cap.
CLAUDE_CAP_HEADROOM = 0.5
WORST_CASE_FACTOR = 3.0
# Extra CLI args that would change the priced model or the per-call cap (exact flag or flag=value).
# Also flags that would add skill/plugin/config sources back into an isolated run (README "Eval isolation").
FORBIDDEN_EXTRA_ARGS = {"claude": ("--model", "--fallback-model", "--max-budget-usd", "--settings", "--setting-sources",
                                   "--plugin-dir", "--plugin-url", "--add-dir", "--mcp-config"),
                        "codex": ("-m", "--model", "-p", "--profile", "--oss", "-C", "--cd", "--add-dir")}

SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")  # an eval `name` must be one safe path component
SKILL_IGNORE = ("evals", "__pycache__", "*-workspace")      # not installed into runs, not part of the skill hash

class EvalError(Exception): pass
class LiveDisabled(RuntimeError): pass

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
    elif t == "file_exists":
        p = chk.get("path")
        if not isinstance(p, str) or not p or os.path.isabs(p) or ".." in p.replace("\\", "/").split("/"):
            e.append("%s.check.path: relative path inside the run workdir required" % where)
    return e

def load_eval_set(skill_dir):
    """Returns (spec, problems). spec: skill_name, skill_dir, description, cases[], triggers[] (or None if missing)."""
    skill_dir = os.path.realpath(skill_dir)
    import eva  # frontmatter parser and the static evals-valid rules
    problems = []
    text, err = eva.read_text(os.path.join(skill_dir, "SKILL.md"))
    meta = (eva.parse_frontmatter(text)[0] if not err else None) or {}
    name = meta.get("name") if isinstance(meta.get("name"), str) else os.path.basename(skill_dir)
    _, errs = eva.evals_errors(skill_dir, name)
    problems += ["evals.json: " + x for x in errs]
    data = rjson(os.path.join(skill_dir, "evals", "evals.json"), {}) if not errs else {}
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
            "cases": cases, "triggers": triggers}, problems

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
    env = dict(os.environ if env is None else env)
    env.pop("CLAUDECODE", None)  # as skill-creator run_eval.py: allow nesting claude -p inside a Claude Code session
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, p.stdout, p.stderr

def new_result():
    return {"final": "", "tool_calls": [], "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
            "cost_usd": None, "error": None, "init": None}

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
    if bad:
        raise EvalError("%s extra args may not set the model or budget (priced from --%s-model): %s"
                        % (engine, engine, ", ".join(bad)))
    return args

class Engine:
    name = "?"
    skills_rel = None  # where a project-scoped skill is installed inside the run workdir
    has_call_cap = False  # True when the CLI takes a per-call USD cap
    def __init__(self, model=None, exec_fn=None, extra_args=(), timeout=900):
        self.model, self.exec_fn, self.timeout = model, exec_fn or live_exec, timeout
        self.extra_args = check_extra_args(self.name, extra_args)
    def install_skill(self, workdir, skill_dir, name):
        """Copy the skill without evals/ (the eval set must not leak into the run)."""
        dst = os.path.join(workdir, self.skills_rel, name)
        shutil.copytree(skill_dir, dst, ignore=shutil.ignore_patterns(*SKILL_IGNORE))
        return dst
    def command(self, prompt, with_skill, budget_usd=None): raise NotImplementedError
    def parse(self, stdout): raise NotImplementedError
    def triggered(self, result, skill_name): raise NotImplementedError
    def sandbox_env(self, root):
        """Environment for one isolated run whose sandbox folder is root (see README "Eval isolation")."""
        return dict(os.environ)
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
    def run(self, prompt, workdir, with_skill, budget_usd=None, env=None):
        cmd = self.command(prompt, with_skill, budget_usd)
        t0, start = time.monotonic(), now_iso()
        rc, out, err = self.exec_fn(cmd, workdir, self.timeout, env=env)
        res = self.parse(out or "")
        res.update(returncode=rc, stderr_tail=(err or "")[-2000:], duration_ms=int((time.monotonic() - t0) * 1000),
                   executor_start=start, executor_end=now_iso(), command=cmd)
        if rc != 0 and not res["error"]: res["error"] = "exit %s" % rc
        return res

class ClaudeCodeEngine(Engine):
    """claude -p (https://code.claude.com/docs/en/headless, /cli-reference). Skill on: copied to
    <sandbox>/work/.claude/skills/<name>/ (project skill). Skill off: --disable-slash-commands ("Disable all skills").
    Both: --setting-sources project,local (user skills under ~/.claude/skills are the "user" source), no MCP servers,
    bundled skills off. HOME and CLAUDE_CONFIG_DIR are left alone: the macOS Keychain login is keyed to the config
    dir, so changing it would lose the login (code.claude.com/docs/en/authentication)."""
    name, skills_rel, has_call_cap = "claude", ".claude/skills", True
    SETTINGS = json.dumps({"disableBundledSkills": True})
    ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
    def command(self, prompt, with_skill, budget_usd=None):
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--no-session-persistence",
               "--setting-sources", "project,local", "--strict-mcp-config", "--settings", self.SETTINGS]
        if self.model: cmd += ["--model", self.model]
        if budget_usd is not None: cmd += ["--max-budget-usd", "%.2f" % budget_usd]
        if not with_skill: cmd += ["--disable-slash-commands"]
        return cmd + self.extra_args
    def sandbox_env(self, root):
        return dict(os.environ, **self.ENV)
    def isolation_problems(self, res, with_skill, skill_name, user_names):
        """From the stream-json system/init event: no plugins, the skill under test loaded only in with_skill, and no
        user-level skill name. The init field names are (추정) from the Agent SDK docs; if absent we fail closed."""
        init = res.get("init")
        if not isinstance(init, dict): return ["no system/init event: cannot verify which skills were loaded"]
        if not isinstance(init.get("skills"), list): return ["system/init has no skills list: cannot verify isolation"]
        names = {str(s.get("name") if isinstance(s, dict) else s).split(":")[-1] for s in init["skills"]}
        out = []
        if init.get("plugins"): out.append("plugins loaded: %s" % json.dumps(init["plugins"], ensure_ascii=False)[:200])
        if with_skill and skill_name not in names: out.append("skill under test %r not loaded" % skill_name)
        if not with_skill and names: out.append("skills loaded in without_skill run: %s" % sorted(names))
        leaked = sorted((names & set(user_names)) - {skill_name})
        if leaked: out.append("user-level skills visible: %s" % leaked)
        return out
    def parse(self, stdout):
        r, texts = new_result(), []
        for line in stdout.splitlines():
            try: ev = json.loads(line)
            except ValueError: continue
            if not isinstance(ev, dict): continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                r["init"] = {"skills": ev.get("skills"), "plugins": ev.get("plugins")}
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
    def sandbox_env(self, root):
        """HOME=<sandbox>/home hides $HOME/.agents/skills; CODEX_HOME=<sandbox>/codex_home hides $CODEX_HOME/skills
        (user + plugin skills) and config.toml. Auth: CODEX_API_KEY when set, else a symlink to the real
        $CODEX_HOME/auth.json (the secret is never copied or logged; after_run fails closed if Codex replaced it)."""
        home, ch = os.path.join(root, "home"), os.path.join(root, "codex_home")
        os.makedirs(home); os.makedirs(ch)
        if not os.environ.get("CODEX_API_KEY"):
            auth = os.path.join(real_codex_home(), "auth.json")
            if os.path.isfile(auth): os.symlink(auth, os.path.join(ch, "auth.json"))
        return dict(os.environ, HOME=home, CODEX_HOME=ch)
    def sandbox_problems(self, root, work, skill_name, with_skill):
        out = Engine.sandbox_problems(self, root, work, skill_name, with_skill)
        for label, d in (("sandbox HOME", os.path.join(root, "home", ".agents", "skills")),
                         ("sandbox CODEX_HOME", os.path.join(root, "codex_home", "skills")),
                         ("admin", os.path.join("/etc", "codex", "skills"))):
            hits = [n for n, p in skills_under(d, 4) if os.sep + ".system" + os.sep not in p + os.sep]
            if hits: out.append("%s skills folder %s has %s" % (label, d, sorted(hits)))
        return out
    def after_run(self, root):
        link = os.path.join(root, "codex_home", "auth.json")
        if os.path.lexists(link) and not os.path.islink(link):
            return ["codex replaced the auth.json link in the isolated CODEX_HOME %s (token refresh?); your real "
                    "auth.json may now be stale. The sandbox is kept, not deleted; check `codex login status`" % root]
        return []
    def command(self, prompt, with_skill, budget_usd=None):
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--sandbox", "workspace-write"]
        if self.model: cmd += ["-m", self.model]
        return cmd + self.extra_args + [prompt]
    def parse(self, stdout):
        r = new_result()
        for line in stdout.splitlines():
            try: ev = json.loads(line)
            except ValueError: continue
            if not isinstance(ev, dict): continue
            t = ev.get("type")
            if t == "item.completed" and isinstance(ev.get("item"), dict):
                it = ev["item"]
                if it.get("type") == "agent_message": r["final"] = it.get("text") or r["final"]
                elif it.get("type") == "command_execution":
                    r["tool_calls"].append({"tool": "command_execution", "input": {"command": it.get("command")}})
                else:
                    r["tool_calls"].append({"tool": it.get("type"), "input": it})
            elif t == "turn.completed":
                u = ev.get("usage") or {}
                r["input_tokens"] += int(u.get("input_tokens") or 0)
                r["cached_input_tokens"] += int(u.get("cached_input_tokens") or 0)
                # reasoning_output_tokens counted as output: upper bound for cost (billing rule not verified)
                r["output_tokens"] += int(u.get("output_tokens") or 0) + int(u.get("reasoning_output_tokens") or 0)
            elif t in ("turn.failed", "error"):
                r["error"] = json.dumps(ev.get("error") or ev.get("message") or t, ensure_ascii=False)[:500]
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
    """[(name, path)] of folders below root (depth <= max_depth) that hold a SKILL.md. Follows symlinked folders once;
    dangling links and unreadable folders are skipped, never raised."""
    out, seen = [], set()
    if not os.path.isdir(root): return out
    base = os.path.realpath(root).count(os.sep)
    for dp, dn, fn in os.walk(root, followlinks=True, onerror=lambda e: None):
        real = os.path.realpath(dp)
        if real in seen or real.count(os.sep) - base > max_depth: dn[:] = []; continue
        seen.add(real)
        if dp != root and os.path.isfile(os.path.join(dp, "SKILL.md")):
            out.append((os.path.basename(dp), dp)); dn[:] = []
        else:
            dn[:] = [d for d in dn if os.path.isdir(os.path.join(dp, d))]
    return out

def real_codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")

def claude_user_skills(home):
    """[(name, path)] of user-level Claude skills: ~/.claude/skills (incl. claude.ai synced/<id>/<name>) and plugins."""
    out = skills_under(os.path.join(home, ".claude", "skills"), 3)
    pc = os.path.join(home, ".claude", "plugins")
    for root, dirs, _ in os.walk(pc, onerror=lambda e: None) if os.path.isdir(pc) else []:
        if os.path.basename(root) == "skills":
            out += [(d, os.path.join(root, d)) for d in dirs if os.path.isfile(os.path.join(root, d, "SKILL.md"))]
            dirs[:] = []
        if root.count(os.sep) - pc.count(os.sep) > 6: dirs[:] = []
    return out

class Sandbox:
    """One fresh temp folder per run: <run_root>/skill-eval-<engine>-XXXX/{work, home, codex_home}. work holds the
    case's fixture files plus, for with_skill, only the skill under test. Removed after the run."""
    def __init__(self, engine, spec, case, skill_path, run_root):
        self.engine, self.run_root = engine, os.path.realpath(run_root)
        self.root = tempfile.mkdtemp(prefix="skill-eval-%s-" % engine.name, dir=self.run_root)
        self.work = os.path.join(self.root, "work"); os.makedirs(self.work)
        for f in case.get("files") or []:
            dst = os.path.join(self.work, f); os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(spec["skill_dir"], f), dst)
        self.name, self.with_skill = spec["skill_name"], bool(skill_path)
        if skill_path: engine.install_skill(self.work, skill_path, self.name)
        self.env = engine.sandbox_env(self.root)
    def problems(self):
        return self.engine.sandbox_problems(self.root, self.work, self.name, self.with_skill)
    def record(self):
        """What the run saw (names and paths only; no environment values)."""
        return {"sandbox": self.root, "with_skill": self.with_skill,
                "env_overrides": sorted(k for k in self.env if os.environ.get(k) != self.env[k])}
    def copy_back(self, dst):
        """Keeps the run's output files for static checks (file_exists), without the installed skill copy."""
        skills = os.path.realpath(os.path.join(self.work, self.engine.skills_rel))
        ign = lambda d, names: [self.name] if os.path.realpath(d) == skills else []
        shutil.copytree(self.work, dst, symlinks=True, ignore=ign)
    def cleanup(self):
        problems = self.engine.after_run(self.root)
        if not problems:
            shutil.rmtree(ensure_inside(self.run_root, self.root), ignore_errors=True)
        return problems

def isolated_run(eng, prompt, spec, case, skill_path, run_dir, run_root, cap, user_names, keep_workdir):
    """Runs one CLI call in a fresh Sandbox and returns (result, fatal problems). Raises EvalError before the call
    (fail closed, nothing spent) when the sandbox could expose another skill. Afterwards, a leak shown by the run's
    own init report, or Codex replacing its auth link, is returned as fatal: the caller charges the run, records it
    as an error and stops the iteration."""
    sb = Sandbox(eng, spec, case, skill_path, run_root)
    ran = False
    try:
        probs = sb.problems()
        if probs: raise EvalError("isolation (%s): %s" % (eng.name, " | ".join(probs)))
        ran = True
        res = eng.run(prompt, sb.work, with_skill=bool(skill_path), budget_usd=cap, env=sb.env)
        iso = eng.isolation_problems(res, bool(skill_path), spec["skill_name"], user_names)
        res["isolation"] = dict(sb.record(), problems=iso)
        if keep_workdir:
            wd = ensure_inside(run_dir, os.path.join(run_dir, "workdir"))
            if os.path.islink(wd): os.remove(wd)
            elif os.path.isdir(wd): shutil.rmtree(wd)
            sb.copy_back(wd)
    finally:
        after = sb.cleanup() if ran else (shutil.rmtree(ensure_inside(sb.run_root, sb.root), ignore_errors=True) or [])
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
    def __init__(self, model=DEFAULT_GRADER_MODEL, exec_fn=None, timeout=300, budget=None, prices=None):
        self.model, self.exec_fn, self.timeout, self.budget = model, exec_fn or live_exec, timeout, budget
        self.prices = prices or {}
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    def _call(self, prompt, schema):
        cap = None
        if self.budget is not None and self.budget.finite():
            cap = self.budget.claude_cap()
            if cap < 0.01:
                raise EvalError("budget exhausted ($%.2f of $%.2f) before grader call" % (self.budget.spent, self.budget.limit))
        cmd = ["claude", "-p", prompt, "--model", self.model, "--output-format", "json", "--json-schema",
               json.dumps(schema), "--tools", "", "--no-session-persistence"]
        if cap is not None: cmd += ["--max-budget-usd", "%.2f" % cap]
        rc, out, err = self.exec_fn(cmd, os.getcwd(), self.timeout)
        try: data = json.loads(out)
        except ValueError:
            if self.budget is not None and cap is not None: self.budget.charge(cap)  # usage unknown: charge the cap
            raise EvalError("grader returned non-JSON (exit %s): %s" % (rc, (err or out or "")[-300:]))
        if not isinstance(data, dict): data = {}
        u = data.get("usage") or {}
        tin = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
        tout = int(u.get("output_tokens") or 0)
        cost = float(data["total_cost_usd"]) if isinstance(data.get("total_cost_usd"), (int, float)) else None
        if cost is None and (tin or tout): cost = call_cost((tin, tout), price_for("grader", self.model, self.prices))
        if cost is None: cost = cap or 0.0
        self.usage["calls"] += 1
        self.usage["input_tokens"] += tin; self.usage["output_tokens"] += tout; self.usage["cost_usd"] += cost
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
        so = self._call(prompt, COMPARE_SCHEMA)
        w = so.get("winner")
        return (w if w in ("A", "B", "TIE") else "TIE"), str(so.get("reasoning") or "")

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

def estimate(n_cases, n_llm_cases, n_triggers, runs, engines, baseline, grader_model, tokens=None, prices=None,
             pairwise=True):
    """engines: [(engine_name, executor_model)]. One grader call per run that has llm items; one pairwise call per
    (case, run) when two configurations are compared; trigger queries run only in configurations that have a skill
    (with_skill, plus old_skill when baseline is old)."""
    tokens, prices = tokens or DEFAULT_TOKENS, prices or {}
    n_configs = len(configs_for(baseline))
    skill_configs = 2 if baseline == "old" else 1
    rows = []
    gprice = price_for("grader", grader_model, prices)
    for eng, model in engines:
        eprice = price_for(eng, model, prices)
        counts = {"exec": n_cases * n_configs * runs, "trigger": n_triggers * runs * skill_configs,
                  "grader": n_llm_cases * n_configs * runs, "pairwise": (n_cases * runs) if (pairwise and n_configs == 2) else 0}
        for kind, n in counts.items():
            price = eprice if kind in ("exec", "trigger") else gprice
            who = (model or "%s CLI default" % eng) if kind in ("exec", "trigger") else grader_model
            per = call_cost(tokens[kind], price)
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
    per = {k: call_cost(tokens[k], price_for("grader", grader_model, prices)) for k in ("grader", "pairwise")}
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
    lines.append("total calls %d, priced total $%.2f%s" % (est["calls"], est["total_usd"],
                 "" if not est["unpriced_models"] else "  + UNPRICED: %s (pass --price)" % ", ".join(est["unpriced_models"])))
    lines.append("assumed tokens per call (in,out): " + ", ".join("%s=%d,%d" % (k, v[0], v[1]) for k, v in est["tokens"].items()))
    return "\n".join(lines)

# ---------- workspace, approvals ----------
def default_workspace(spec):
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

AUTH_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

def preflight(spec, engines, run_root=None, home=None):
    """Read-only, blocks --live (fail closed). Runs execute in per-run sandboxes under run_root (README "Eval
    isolation"); this checks what the sandboxes cannot hide:
    - any .claude/skills (claude) or .agents/skills (codex) folder with skills in run_root or its ancestors
      (project skills are discovered from the cwd upward);
    - claude: a same-named copy in ~/.claude/skills (incl. synced/) or a plugin. --setting-sources project,local
      should hide user skills (docs: the "user" source loads ~/.claude/skills), but whether the CLI flag hides already
      downloaded claude.ai synced skills and plugin skills is not verified (추정), so a same-named copy still blocks;
    - claude: user settings.json supplying auth (apiKeyHelper / env token), which --setting-sources would drop;
    - codex: skills in /etc/codex/skills (admin scope, cannot be hidden), and no usable auth (CODEX_API_KEY or a
      file-based $CODEX_HOME/auth.json to link; its content is never read)."""
    home, name, out = home or os.path.expanduser("~"), spec["skill_name"], []
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
            for n_, p in claude_user_skills(home):
                if n_ == name:
                    out.append("claude: skill %r is also installed at %s; hiding it via --setting-sources is not "
                               "verified, so it could sit next to the copy under test (move/rename it)" % (name, p))
            try:
                st = rjson(os.path.join(home, ".claude", "settings.json"), {}) or {}
            except ValueError:
                st = {}
            env = st.get("env") if isinstance(st, dict) and isinstance(st.get("env"), dict) else {}
            if isinstance(st, dict) and ("apiKeyHelper" in st or any(k in env for k in AUTH_ENV_KEYS)):
                out.append("claude: ~/.claude/settings.json supplies auth (apiKeyHelper/env), but isolated runs use "
                           "--setting-sources project,local and would not see it; export the credential instead")
        if e == "codex":
            hits = skills_under(os.path.join("/etc", "codex", "skills"), 4)
            if hits:
                out.append("codex: /etc/codex/skills has %s (admin skills load in every run)" % sorted(n for n, _ in hits))
            if not os.environ.get("CODEX_API_KEY") and not os.path.isfile(os.path.join(real_codex_home(), "auth.json")):
                out.append("codex: no CODEX_API_KEY and no file-based %s/auth.json to reference from the isolated "
                           "CODEX_HOME (keyring auth cannot be isolated without changing it)" % real_codex_home())
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
        return None, 0.0, ("%s worst-case %s call $%.2f > remaining $%.2f (%s has no per-call cap)"
                      % (engine.name, kind, bound, budget.remaining(), engine.name))
    return None, bound, None

def run_cost(res, engine, prices, fallback=0.0):
    """Reported cost, else tokens x price; when the CLI reported neither, charge the call's cap/bound (fallback)."""
    if res.get("cost_usd") is not None: return res["cost_usd"]
    if res["input_tokens"] or res["output_tokens"]:
        c = call_cost((res["input_tokens"], res["output_tokens"]), price_for(engine.name, engine.model, prices))
        if c is not None: return c
    return fallback or 0.0

def write_run(run_dir, res, engine, config, prompt, triggered):
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
        "cached_input_tokens": res["cached_input_tokens"], "cost_usd": res.get("cost_usd")})

class _Fatal(Exception): pass

def run_iteration(spec, ws, engines, runs=DEFAULT_RUNS, baseline="none", old_skill=None, budget=None, prices=None,
                  iteration=None, log=print, tokens=None, run_root=None):
    """Executes every (engine, case, config, run) and every trigger query, each in its own Sandbox under run_root
    (default: the system temp folder). Returns the iteration number."""
    configs = configs_for(baseline)
    if baseline == "old" and not (old_skill and os.path.isfile(os.path.join(old_skill, "SKILL.md"))):
        raise EvalError("baseline old needs --old-skill (or `snapshot` first): %s" % old_skill)
    run_root = os.path.realpath(run_root or tempfile.gettempdir())
    n = iteration or ((iterations(ws) or [0])[-1] + 1)
    it_dir = os.path.join(ws, "iteration-%d" % n)
    wjson(os.path.join(it_dir, "plan.json"), {"skill_name": spec["skill_name"], "skill_path": spec["skill_dir"],
          "old_skill": old_skill, "configurations": configs, "runs_per_configuration": runs,
          "engines": {e.name: e.model for e in engines}, "evals_run": [c["id"] for c in spec["cases"]],
          "trigger_queries": len(spec["triggers"] or []), "started_at": now_iso(),
          "skill_sha256": skill_tree_hash(spec["skill_dir"]), "eval_set_sha256": gate_hash("eval_set", spec, ws),
          "old_skill_sha256": skill_tree_hash(old_skill) if baseline == "old" else None,
          "isolation": {"run_root": run_root, "per_run_sandbox": True}})
    budget, prices, tokens = budget or Budget(float("inf")), prices or {}, tokens or DEFAULT_TOKENS
    user_names = sorted({nm for nm, _ in claude_user_skills(os.path.expanduser("~"))}) if any(e.name == "claude" for e in engines) else []
    stopped = None

    def one(eng, kind, prompt, case, cfg, rd, keep):
        cap, fallback, stop = guard_call(eng, kind, budget, prices, tokens)
        if stop: return None, stop
        res, fatal = isolated_run(eng, prompt, spec, case, skill_path_for(cfg, spec, old_skill), ensure_inside(it_dir, rd),
                                  run_root, cap, user_names, keep)
        budget.charge(run_cost(res, eng, prices, fallback))
        write_run(rd, res, eng, cfg, prompt, eng.triggered(res, spec["skill_name"]))
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
    meta.update(finished_at=now_iso(), spent_usd=round(budget.spent, 4) if budget.limit != float("inf") else None,
                stopped=stopped)
    wjson(os.path.join(it_dir, "plan.json"), meta)
    if stopped: log("STOPPED: " + stopped)
    return n

# ---------- grading ----------
def static_check(chk, t, workdir):
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

def check_grader_model(it_dir, grader_model):
    """The grader must differ from the executor models recorded in the iteration's plan.json (not from CLI flags)."""
    plan = rjson(os.path.join(it_dir, "plan.json"))
    if not isinstance(plan, dict) or not isinstance(plan.get("engines"), dict):
        raise EvalError("%s has no plan.json with executor models; cannot check the grader differs" % it_dir)
    models = plan["engines"]
    same = [e for e, m in models.items() if m and m == grader_model]
    if same: raise EvalError("grader model %s equals the executor model of %s; use a different model" % (grader_model, same))
    if any(e == "claude" and not m for e, m in models.items()):
        raise EvalError("claude executor model unknown (CLI default) in plan.json; the grader cannot be shown to differ")

def grade_iteration(spec, ws, n, grader=None, seed=None, log=print):
    """Static checks first, then (with a grader) LLM yes/no items and blind pairwise comparison; human items are
    queued in human_review.json and merged back when a reviewer filled in "passed"."""
    it_dir = os.path.join(ws, "iteration-%d" % n)
    if not os.path.isdir(it_dir): raise EvalError("no %s" % it_dir)
    if grader is not None: check_grader_model(it_dir, grader.model)
    cases = {c["id"]: c for c in spec["cases"]}
    hr_path = os.path.join(it_dir, "human_review.json")
    old_hr = {x["key"]: x for x in (rjson(hr_path, {}) or {}).get("items", [])}
    queue = []
    for eng, ed, cfg, rd in walk_runs(it_dir):
        md = rjson(os.path.join(ed, "eval_metadata.json"), {})
        case = cases.get(md.get("eval_id"))
        t = rjson(os.path.join(rd, "outputs", "transcript.json"))
        if case is None or t is None: continue
        res, llm_items = {}, []
        for it in case["items"]:
            if it["layer"] == "static":
                ok, ev = static_check(it["check"], t, os.path.join(rd, "workdir"))
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
    wjson(hr_path, {"instructions": "Set \"passed\" to true/false (and optionally \"note\"), then re-run grade.",
                    "items": queue})
    if grader is not None: pairwise(spec, it_dir, grader, seed)
    log("graded %s (%d human item(s) queued)" % (it_dir, sum(1 for q in queue if not isinstance(q["passed"], bool))))

def pair_list(spec, it_dir):
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
                out.append((os.path.join(ep, ed), case, base, k, pa, pb)); k += 1
    return out

def grade_counts(spec, it_dir):
    """(LLM grader calls, pairwise calls) that `grade --live` would make for this iteration."""
    cases, n = {c["id"]: c for c in spec["cases"]}, 0
    for eng, ed, cfg, rd in walk_runs(it_dir):
        case = cases.get((rjson(os.path.join(ed, "eval_metadata.json"), {}) or {}).get("eval_id"))
        if case and os.path.isfile(os.path.join(rd, "outputs", "transcript.json")) \
                and any(it["layer"] == "llm" for it in case["items"]):
            n += 1
    return n, len(pair_list(spec, it_dir))

def pairwise(spec, it_dir, grader, seed=None):
    """Blind A/B per (engine, case, run k): with_skill run-k vs baseline run-k, order randomized and recorded."""
    seed = random.SystemRandom().randrange(2 ** 31) if seed is None else seed
    rng = random.Random(seed)
    for ed, case, base, k, pa, pb in pair_list(spec, it_dir):
        swap = rng.random() < 0.5
        a_cfg, b_cfg = (base, PRIMARY) if swap else (PRIMARY, base)
        ta, tb = rjson(pa if not swap else pb), rjson(pb if not swap else pa)
        w, why = grader.compare(case, ta, tb)
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
                          "spent_usd": plan.get("spent_usd"), "stopped": plan.get("stopped"),
                          # what was tested (recorded at run start) vs what is on disk now; register compares them
                          "skill_sha256": plan.get("skill_sha256"), "eval_set_sha256": plan.get("eval_set_sha256"),
                          "current_skill_sha256": skill_tree_hash(spec["skill_dir"]),
                          "current_eval_set_sha256": gate_hash("eval_set", spec, ws)},
             "runs": runs, "run_summary": summarize(runs), "per_engine": per_engine, "trigger": trig, "pairwise": pw,
             "static_failures": static_fail, "pressure": pressure, "completeness": completeness,
             "execution_errors": exec_errors,
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
         "- spent: %s%s" % ("n/a" if m.get("spent_usd") is None else "$%.2f" % m["spent_usd"],
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
    def __init__(self, instructions, model, budget, prices=None, exec_fn=None, timeout=900):
        self.instructions, self.model, self.budget, self.prices = instructions, model, budget, prices or {}
        self.exec_fn, self.timeout, self.calls = exec_fn or live_exec, timeout, 0
    def command(self, prompt, cap):
        cmd = ["claude", "-p", prompt, "--model", self.model, "--output-format", "json", "--no-session-persistence",
               "--permission-mode", "acceptEdits", "--tools", self.TOOLS, "--disable-slash-commands"]
        return cmd + (["--max-budget-usd", "%.2f" % cap] if cap is not None else [])
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
        rc, out, err = self.exec_fn(self.command(prompt, cap), skill_dir, self.timeout)
        self.calls += 1
        try: data = json.loads(out or "")
        except ValueError: data = {}
        data = data if isinstance(data, dict) else {}
        u = data.get("usage") or {}
        tin = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        cost = float(data["total_cost_usd"]) if isinstance(data.get("total_cost_usd"), (int, float)) else None
        if cost is None and (tin or u.get("output_tokens")):
            cost = call_cost((tin, int(u.get("output_tokens") or 0)), price_for("builder", self.model, self.prices))
        if cost is None: cost = cap or 0.0  # usage unknown: charge the cap
        if self.budget is not None: self.budget.charge(cost)
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
        grade_iteration(spec, ws, n, grader, seed, log=log)
        if golden: wjson(os.path.join(ws, "iteration-%d" % n, "calibration.json"), calibrate(golden, grader))
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
def make_engines(a):
    out = []
    for name in a.engines.split(","):
        name = name.strip()
        if name not in ENGINES: raise EvalError("unknown engine %r (claude|codex)" % name)
        model = a.claude_model if name == "claude" else a.codex_model
        extra = a.claude_arg if name == "claude" else a.codex_arg
        out.append(ENGINES[name](model=model, extra_args=extra or ()))
    return out

def plan_text(spec, problems, a, engines, ws, est):
    configs = configs_for(a.baseline)
    layers = {l: sum(1 for c in spec["cases"] for it in c["items"] if it["layer"] == l) for l in LAYERS}
    trig = spec["triggers"] or []
    L = ["skill: %s (%s)" % (spec["skill_name"], spec["skill_dir"]), "workspace: %s" % ws,
         "configurations: %s | runs: %d | engines: %s" % (", ".join(configs), a.runs,
             ", ".join("%s(%s)" % (e.name, e.model or "CLI default") for e in engines)),
         "grader: %s (yes/no rubric) + blind pairwise" % a.grader_model,
         "eval set: %d case(s), items static=%d llm=%d human=%d, pressure cases=%d; trigger queries: %d (+%d / -%d)" % (
             len(spec["cases"]), layers["static"], layers["llm"], layers["human"],
             sum(1 for c in spec["cases"] if c["pressure"]), len(trig), sum(q["should_trigger"] for q in trig),
             sum(not q["should_trigger"] for q in trig))]
    L += ["validation problems (%d):" % len(problems)] + ["  - " + p for p in problems]
    L += ["approval gates: requirements=%s, eval_set=%s" % (
        "approved" if approval_ok("requirements", spec, ws) else "NOT APPROVED",
        "approved" if approval_ok("eval_set", spec, ws) else "NOT APPROVED")]
    L += ["preflight problems (block --live):"] + ["  - " + w for w in preflight(spec, [e.name for e in engines], a.run_root)]
    if spec["cases"]:
        c = spec["cases"][0]
        L.append("sample commands (eval %s, run in <workspace>/iteration-N/<engine>/%s/<config>/run-k/workdir):" % (c["id"], eval_dir_name(c)))
        for e in engines:
            for cfg in configs:
                cmd = e.command(full_prompt(c)[:60] + "…", bool(skill_path_for(cfg, spec, a.old_skill or "<old>")))
                L.append("  [%s %s] %s" % (e.name, cfg, " ".join(json.dumps(x, ensure_ascii=False) if " " in x else x for x in cmd)))
    L += ["cost estimate:", format_estimate(est)]
    return "\n".join(L)

def live_estimate(a, spec, ws, n, engines, tokens, prices, iterations_left=None):
    """Cost of what a live command will really execute; never uses --cases/--llm-cases/--trigger-queries.
    loop: iterations_left x (one iteration + calibration) + (iterations_left - 1) builder calls."""
    if a.cmd == "grade":
        check_grader_model(os.path.join(ws, "iteration-%d" % n), a.grader_model)
        ng, npw = grade_counts(spec, os.path.join(ws, "iteration-%d" % n))
        return grader_estimate(ng, npw, a.grader_model, tokens, prices)
    if a.cmd == "calibrate":
        return grader_estimate(len(load_golden(a.golden)), 0, a.grader_model, tokens, prices)
    n_cases, n_llm, n_trig = spec_counts(spec, None)
    est = estimate(n_cases, n_llm, n_trig, a.runs, [(e.name, e.model) for e in engines], a.baseline, a.grader_model,
                   tokens, prices)
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

def check_live_gates(a, spec, problems, ws, n, engines, tokens, prices, budget_left, iterations_left=None):
    """Every --live gate; raises EvalError on the first failure. `loop` calls it again before each iteration with the
    reloaded eval set, the remaining iterations and the remaining budget."""
    if problems: raise EvalError("eval set has %d validation problem(s); run `validate`" % len(problems))
    for g in ("requirements", "eval_set"):
        if not approval_ok(g, spec, ws): raise EvalError("gate %s not approved (write %s/requirements.md, then `approve --gate %s`)" % (g, ws, g))
    live_est = live_estimate(a, spec, ws, n, engines, tokens, prices, iterations_left)
    if live_est["unpriced_models"]: raise EvalError("no price for %s; pass --price" % live_est["unpriced_models"])
    if a.cmd in ("run", "loop"):
        pf = preflight(spec, [e.name for e in engines], a.run_root)
        if pf: raise EvalError("preflight: " + " | ".join(pf))
        for e in engines:
            if e.has_call_cap: continue
            for kind in ("exec", "trigger"):
                b = call_bound(e.name, e.model, kind, prices, tokens)
                if b > budget_left:
                    raise EvalError("%s worst-case %s call bound $%.2f exceeds budget $%.2f (%s has no per-call cap)"
                                    % (e.name, kind, b, budget_left, e.name))
    if live_est["total_usd"] > budget_left:
        raise EvalError("estimate $%.2f exceeds budget $%.2f\n%s" % (live_est["total_usd"], budget_left, format_estimate(live_est)))
    return live_est

def spec_counts(spec, a):
    """Counts from the eval set; a (argparse namespace) may override them for estimate/plan only (a=None: no overrides)."""
    o = lambda k: getattr(a, k, None) if a is not None else None
    n_cases = o("cases") if o("cases") is not None else len(spec["cases"])
    n_llm = o("llm_cases") if o("llm_cases") is not None else sum(1 for c in spec["cases"] if any(i["layer"] == "llm" for i in c["items"]))
    n_trig = o("trigger_queries") if o("trigger_queries") is not None else len(spec["triggers"] or [])
    return n_cases, n_llm, n_trig

def main(argv=None):
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
            s.add_argument("--grader-model", default=DEFAULT_GRADER_MODEL)
            s.add_argument("--price", action="append", help="KEY=IN,OUT USD/MTok; KEY = engine name, model id or 'grader'")
            s.add_argument("--tokens", action="append", help="KIND=IN,OUT assumed tokens per call; KIND exec|trigger|grader|pairwise")
            s.add_argument("--cases", type=int); s.add_argument("--llm-cases", type=int); s.add_argument("--trigger-queries", type=int)
            s.add_argument("--live", action="store_true"); s.add_argument("--budget-usd", type=float)
            s.add_argument("--seed", type=int)
            s.add_argument("--run-root", help="folder for the per-run sandboxes (default: system temp folder)")
    for n in ("validate", "estimate", "plan", "run", "grade", "calibrate", "report", "loop"):
        s = sp.add_parser(n); common(s, n not in ("validate", "report"))
        if n in ("grade", "report", "calibrate"): s.add_argument("--iteration", type=int)
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
    a = p.parse_args(argv)
    try:
        return _dispatch(a)
    except EvalError as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 2
    finally:
        _LIVE["on"] = False

def _dispatch(a):
    spec, problems = load_eval_set(a.skill)
    ws = os.path.realpath(a.workspace) if a.workspace else default_workspace(spec)
    if a.cmd == "validate":
        print("\n".join(problems) if problems else "eval set OK: %d cases, %d trigger queries" % (len(spec["cases"]), len(spec["triggers"] or [])))
        return 1 if problems else 0
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
    engines = make_engines(a)
    if a.baseline == "old" and not a.old_skill and os.path.isdir(os.path.join(ws, "skill-snapshot")):
        a.old_skill = os.path.join(ws, "skill-snapshot")
    prices = parse_price_overrides(a.price)
    tokens = parse_token_overrides(a.tokens)
    n_cases, n_llm, n_trig = spec_counts(spec, a)
    est = estimate(n_cases, n_llm, n_trig, a.runs, [(e.name, e.model) for e in engines], a.baseline, a.grader_model, tokens, prices)
    if a.cmd == "estimate":
        print(format_estimate(est)); return 0
    live_cmds = ("run", "grade", "calibrate", "loop")
    if a.cmd == "plan" or (a.cmd in live_cmds and not a.live and a.cmd != "grade"):
        print(plan_text(spec, problems, a, engines, ws, est))
        print("\nDRY RUN: nothing was executed. Real runs need --live --budget-usd X, approved requirements/eval_set, "
              "no validation or preflight problems, and an estimate within budget.")
        return 0
    if a.cmd == "grade" and not a.live:
        n = a.iteration or (iterations(ws) or [None])[-1]
        if n is None: raise EvalError("no iterations in %s" % ws)
        grade_iteration(spec, ws, n, None); print("static + human queue only (dry run); LLM items not graded"); return 0
    # --- live: the same gates for run, loop, grade and calibrate ---
    if a.budget_usd is None or not math.isfinite(a.budget_usd) or a.budget_usd <= 0:
        raise EvalError("--live needs a finite --budget-usd > 0")
    n = None
    if a.cmd in ("grade", "calibrate"):
        n = a.iteration or (iterations(ws) or [None])[-1]
        if n is None or not os.path.isdir(os.path.join(ws, "iteration-%d" % n)): raise EvalError("no iterations in %s" % ws)
    check_live_gates(a, spec, problems, ws, n, engines, tokens, prices, a.budget_usd)
    budget = Budget(a.budget_usd)
    grader = LLMGrader(a.grader_model, budget=budget, prices=prices)
    _LIVE["on"] = True
    if a.cmd == "run":
        n = run_iteration(spec, ws, engines, a.runs, a.baseline, a.old_skill, budget, prices, tokens=tokens,
                          run_root=a.run_root)
        print("iteration-%d done, spent ~$%.2f" % (n, budget.spent)); return 0
    if a.cmd == "grade":
        grade_iteration(spec, ws, n, grader, a.seed)
        print("graded iteration-%d, spent ~$%.2f" % (n, budget.spent)); return 0
    if a.cmd == "calibrate":
        cal = calibrate(a.golden, grader)
        wjson(os.path.join(ws, "iteration-%d" % n, "calibration.json"), cal)
        print("agreement %.2f kappa %.2f (n=%d)" % (cal["agreement"], cal["kappa"], cal["n"])); return 0
    if a.cmd == "loop":
        builder = ClaudeBuilder(read_builder_prompt(a), a.builder_model, budget, prices) if a.builder_prompt else None
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
