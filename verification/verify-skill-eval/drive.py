#!/usr/bin/env python3
"""Drive eva.py and skill_eval.py the way a user does and keep the evidence.
A journey is a table of Step, Seed and Probe rows in JOURNEYS. Every child runs in a disposable scratch folder with an
empty HOME, a minimal environment and an audit hook that logs each process, socket, write and read, and refuses to start
a tool CLI. Standard library only, Python 3.9."""
import argparse, collections, contextlib, glob, hashlib, importlib.util, io, json, os, re, shlex, shutil, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.join(os.path.realpath(tempfile.gettempdir()), "verify-skill-eval")
SCRATCH, EVIDENCE = os.path.join(BASE, "scratch"), os.path.join(BASE, "evidence")
TOOL_CLIS = ("claude", "codex", "opencode", "hermes")
REFUSED_WORDS = ("approve", "approve-ui", "approver-session", "approver-check", "register", "machine-waiver",
                 "check-builtin-plugins")
REFUSED_FLAGS = ("--live", "--scope")
COPIES = (("bad", "bad"), ("golden", "golden"), ("skills", "skills"), ("tests/fixtures", "fixtures"),
          ("transcripts", "transcripts"))
SKILL = "fixtures/skills/csv-cleaner-en"
SKILL_WS = SKILL + "-workspace"
AGENT = "fixtures/agents/code-reviewer-v2"
AGENT_WS = AGENT + "-workspace"
DOCS = "skills/agent-scenarios/evals/files/doc-linker"
GOLDEN_SKILL = "golden/skills/csv-cleaner"
GOLDEN_IT = "golden/skills/csv-cleaner-workspace/iteration-4"
GOLDEN_AGENT = "golden/agents/code-reviewer"
REAL_SKILLS = ("agent-scenarios", "agent-spec", "unit-requirements")
RUN_DIR = AGENT_WS + "/iteration-1/claude/drill/new_agent/run-1"
VERIFIERS = (("tlc", "-help"), ("apalache-mc", "version"), ("alloy", "--version"), ("z3", "--version"), ("cvc5", "--version"),
             ("spin", "-V"), ("NuSMV", "-help"), ("cbmc", "--version"), ("dafny", "--version"), ("coqc", "--version"),
             ("lean", "--version"))
MODULES = ("z3", "cvc5", "hypothesis", "crosshair", "pysat")
JAVA_HOMES = ("/Library/Java/JavaVirtualMachines/*/Contents/Home", "~/Library/Java/JavaVirtualMachines/*/Contents/Home",
              "/opt/homebrew/opt/openjdk*", "/usr/local/opt/openjdk*", "~/.sdkman/candidates/java/current", "~/.jdks/*")
JAR_PATTERNS = {"TLA+": re.compile(r"(tla2tools.*\.jar|TLA\+ Toolbox\.app|tla\+?toolbox.*)$", re.I),
                "Alloy": re.compile(r"(org\.alloytools.*\.jar|alloy[-_.0-9a-z]*\.jar|Alloy.*\.app)$", re.I)}
JAR_ROOTS = (("~", 1), ("~/tools", 3), ("~/bin", 3), ("~/lib", 3), ("~/opt", 3), ("~/.local", 4), ("~/Downloads", 2),
             ("~/Documents", 2), ("~/Desktop", 2), ("~/Applications", 2), ("~/workspace", 2), ("~/.vscode/extensions", 3),
             ("~/.cursor/extensions", 3), ("/Applications", 1), ("/opt", 3), ("/opt/homebrew/Cellar", 4),
             ("/opt/homebrew/share", 3), ("/opt/homebrew/lib", 3), ("/usr/local/lib", 3), ("/usr/local/share", 3),
             ("/usr/local/Cellar", 4))
JAR_SKIP = ("Library", ".Trash", ".git", "node_modules", ".claude", ".ssh", ".gnupg", "__pycache__")
ACCOUNT_ENV = ("SKILL_EVAL_ACCOUNT_SWITCH", "CLAUDE_ACCOUNTS", "CLAUDE_AS_HOME")

Step = collections.namedtuple("Step", "tool args exits expect save")
Seed = collections.namedtuple("Seed", "kind path value")
Probe = collections.namedtuple("Probe", "kind paths")
Run = collections.namedtuple("Run", "scratch evidence audit_fd audit_path root", defaults=(ROOT,))

AUDIT_HOOK = r'''
import json, os, re, sys
fd = int(os.environ["VERIFY_AUDIT_FD"])
base = os.path.realpath(os.environ["VERIFY_SCRATCH"])
quiet = tuple(os.path.realpath(p) + os.sep for p in (base, os.environ["VERIFY_ROOT"], sys.prefix, sys.base_prefix)) + ("/dev/",)
tools = tuple(os.environ["VERIFY_TOOL_CLIS"].split(","))
WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# shutil.rmtree removes each entry through dir_fd, which real() cannot resolve, so its own event carries the true path.
PATHS = {"os.mkdir": (0,), "os.rmdir": (0,), "os.remove": (0,), "os.symlink": (1,), "os.link": (1,), "os.rename": (0, 1),
         "os.chmod": (0,), "os.truncate": (0,), "os.utime": (0,), "shutil.rmtree": (0,)}
GATED = ("approvals.json", "registration.json", "deprecations.json", "machine-waiver.json", "spend.jsonl")
def log(kind, detail):
    os.write(fd, (json.dumps({"kind": kind, "detail": detail}) + "\n").encode("utf-8"))
def real(path):
    try: return os.path.realpath(os.fsdecode(path))
    except ValueError: return repr(path)
def inside(path):
    p = real(path)
    return p == base or p.startswith(base + os.sep)
def write(event, path):
    log("write" if inside(path) else "write-outside", [event, real(path)])
def starts_tool(argv):
    return any(os.path.basename(w) in tools for a in argv for w in re.split(r"[\s;&|()]+", a))
def start(argv):
    log("process", argv)
    if starts_tool(argv):
        log("tool-cli-refused", argv)
        raise PermissionError("verify-skill-eval refuses to start " + argv[0])
def hook(event, args):
    if event in ("subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn"):
        path, raw = args[1:3] if event == "os.spawn" else args[:2]
        raw = raw or []
        argv = [os.fsdecode(a) if isinstance(a, (str, bytes)) else repr(a) for a in ([raw] if isinstance(raw, (str, bytes)) else raw)]
        if isinstance(path, (str, bytes)): argv.insert(0, os.fsdecode(path))
        start(argv)
    elif event == "os.system":
        start([os.fsdecode(args[0])])
    elif event in ("os.fork", "os.forkpty"):
        log("process", [event] + [repr(a) for a in args])
    elif event in ("socket.bind", "socket.connect", "socket.sendto", "socket.getaddrinfo"):
        log("socket", repr(args))
    elif event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.path.basename(os.fsdecode(args[0])) in GATED: log("gate-read", real(args[0]))
        if isinstance(args[2], int) and args[2] & WRITE: write(event, args[0])
        elif not real(args[0]).startswith(quiet): log("read-outside", real(args[0]))
    elif event in PATHS:
        for i in PATHS[event]:
            if isinstance(args[i], (str, bytes, os.PathLike)): write(event, args[i])
sys.addaudithook(hook)
log("loaded", os.getpid())
'''
AUDIT_KINDS = ("process", "socket", "write", "write-outside", "tool-cli-refused", "read-outside", "gate-read", "loaded")


def S(tool, args, exits, *expect, save=None):
    return Step(tool, shlex.split(args), tuple(exits), expect, save)


def put(path, text):
    return Seed("write", path, text)


def cp(src, path):
    return Seed("copy", path, src)


def rm(path):
    return Seed("remove", path, None)


def link(path, target):
    return Seed("link", path, target)


def has(*paths):
    return Probe("exists", paths)


def lacks(*paths):
    return Probe("absent", paths)


def same(a, b):
    return Probe("same-bytes", (a, b))


def holds(path, pattern):
    return Probe("holds", (path, pattern))


def silent():
    """The journey so far started nothing, opened no socket and wrote nothing but tempfile's writability probe, one file
    created and removed in $TMPDIR by tempfile.gettempdir() (run_probe)."""
    return Probe("silent", ())


WRAPPERS = (AGENT + "/wrappers/claude/code-reviewer-v2.md", AGENT + "/wrappers/codex/code-reviewer-v2.toml",
            AGENT + "/wrappers/opencode/code-reviewer-v2.md", AGENT + "/wrappers/hermes/SOUL.md")
AGENT_DESTS = ("../target/.claude/agents/code-reviewer-v2.md", "../target/.codex/agents/code-reviewer-v2.toml",
               "../target/.opencode/agents/code-reviewer-v2.md", "../target/.hermes/profiles/code-reviewer-v2/SOUL.md")
RUN_JSON = json.dumps({"scenario": "drill", "tool": "claude", "configuration": "new_agent", "run": 1, "state": "run"})
PLAN_JSON = json.dumps({"agent_name": "code-reviewer-v2", "engines": {"claude": "claude-opus-5-5"}, "runs_per_cell": 1})

JOURNEYS = {
    "static-checks": [
        S("eva", "check-all bad", [1], r"total=6 pass=0 review=0 fail=6", r"ruleset_sha256 [0-9a-f]{64}"),
        S("eva", "check-all golden", [1], r"total=5 pass=0 review=0 fail=5"),
        S("eva", "check-all no-such-folder", [1], r"no SKILL\.md/AGENT\.md units found"),
        S("eva", "check " + SKILL, [0], r"^\[PASS\] .*\(skill\) score=", r"ruleset_sha256 [0-9a-f]{64}"),
        S("eva", "check " + AGENT, [1], r"^\[FAIL\] .*\(agent\) score=", r"parity-permissions-hermes"),
        S("eva", "check " + SKILL + " --json", [0], r'"ruleset_sha256"'),
        S("eva", "check " + SKILL + " --rules no-such-bundle", [2], r"bundle error:"),
        S("eva", "check " + SKILL + " --rules {repo}/rules", [3], r"bundle-non-default"),
        S("eva", "schema-check {repo}/schemas/evals.schema.json " + SKILL + "/evals/evals.json --unit " + SKILL, [0],
          r"schema-check: valid"),
        S("eva", "schema-check {repo}/schemas/evals.schema.json " + SKILL + "/evals/evals.json", [1], r"no unit root given"),
        S("eva", "schema-check {repo}/schemas/evals.schema.json golden/registry.json", [1], r"^error "),
        S("eva", "schema-check {repo}/schemas/no-such.schema.json golden/registry.json", [2], r"cannot read"),
        silent(),
    ],
    "eval-dry-runs": [
        S("eval", "validate " + SKILL, [0], r"eval set OK: 6 cases, 20 trigger queries",
          r"multi-tool parity: claude=pass codex=pass opencode=pass hermes=pass"),
        S("eval", "validate bad/skills/no-io-skill", [1], r"evals\.json: missing evals/evals\.json"),
        S("eval", "estimate " + SKILL, [0], r"API-equivalent total \$\d"),
        S("eval", "plan " + SKILL, [0], r"DRY RUN: nothing was executed", r"workspace: \S+/" + SKILL_WS + "$"),
        S("eval", "plan " + SKILL + " --cases 1 --llm-cases 1 --trigger-queries 2", [0], r"DRY RUN: nothing was executed"),
        S("eval", "run " + SKILL, [0], r"DRY RUN: nothing was executed"),
        S("eva", "eval run " + SKILL, [0], r"DRY RUN: nothing was executed"),
        S("eval", "loop " + SKILL, [0], r"DRY RUN: nothing was executed"),
        S("eval", "calibrate " + SKILL + " --golden no-such.json", [0], r"DRY RUN: nothing was executed"),
        S("eval", "run " + SKILL + " --dry-run", [2], r"--dry-run checks the gates"),
        S("eval", "run " + SKILL + " --cases 3", [2], r"--cases"),
        S("eval", "run " + SKILL + " --engines opencode", [2], r"is not available"),
        S("eval", "grade " + SKILL, [2], r"no iterations in"),
        S("eval", "report " + SKILL, [2], r"no iterations in"),
        lacks(SKILL_WS, SKILL_WS + "/iteration-1", SKILL_WS + "/spend.jsonl"),
        silent(),
    ],
    "skill-recorded-iteration": [
        rm(GOLDEN_IT + "/comparison.md"),
        rm(GOLDEN_IT + "/gate.json"),
        S("eval", "report " + GOLDEN_SKILL, [3], r"iteration-4: NEEDS APPROVAL", r"stale: rules changed since this iteration ran",
          r"coverage-missing:hermes"),
        has(GOLDEN_IT + "/comparison.md", GOLDEN_IT + "/gate.json"),
        S("eval", "grade " + GOLDEN_SKILL + " --iteration 2", [0], r"static \+ human queue only \(dry run\); LLM items not graded"),
        S("eval", "pending --unit " + GOLDEN_SKILL, [0], r"pending approval\(s\); nothing was recorded"),
    ],
    "agent-lifecycle": [
        S("eval", "agent validate " + AGENT, [0], r"wrapper claude: missing \(eva\.py generate\)", r"^validate: OK"),
        S("eval", "agent plan " + AGENT, [0], r"cell overview claude: not-run: wrapper missing", r"DRY RUN: nothing was executed",
          r"workspace: \S+/" + AGENT_WS + "$"),
        S("eval", "agent run " + AGENT, [0], r"DRY RUN: nothing was executed"),
        S("eval", "agent loop " + AGENT, [0], r"^loop: max iterations \d+", r"DRY RUN: nothing was executed"),
        S("eval", "agent plan " + AGENT + " --engines opencode", [2], r"is not available"),
        S("eval", "agent grade " + AGENT, [2], r"no iterations in"),
        S("eval", "agent report " + AGENT, [2], r"no iterations in"),
        S("eval", "install " + AGENT + " --target {scratch}/target", [2], r"run `python3 eva\.py generate"),
        lacks(AGENT_WS, "../target/.claude"),
        silent(),
        S("eva", "generate " + AGENT, [0], r"generate: \S+: 4 written, 0 removed, 0 unchanged"),
        has(*WRAPPERS),
        S("eva", "generate " + AGENT, [0], r"generate: \S+: 0 written, 0 removed, 4 unchanged"),
        S("eva", "generate " + AGENT + " --check", [0], r""),
        S("eval", "agent validate " + AGENT, [0], r"wrapper claude: up to date", r"wrapper hermes: up to date"),
        S("eval", "agent plan " + AGENT, [0], r"cell overview claude: planned", r"cell drill claude: not-run: multi-turn unverified"),
        put(WRAPPERS[0], "tampered\n"),
        S("eva", "generate " + AGENT + " --check", [1], r"wrappers/claude/code-reviewer-v2\.md"),
        S("eval", "install " + AGENT + " --target {scratch}/target", [2], r"run `python3 eva\.py generate"),
        S("eva", "generate " + AGENT, [2], r"not written by eva\.py generate .*move it away and rerun"),
        rm(WRAPPERS[0]),
        S("eva", "generate " + AGENT, [0], r"1 written"),
        S("eva", "check " + AGENT + " --transcript transcripts/code-reviewer-drill.json", [3], r"^\[REVIEW\] ", r"criteria-needs-review"),
        S("eva", "check " + AGENT + " --transcript transcripts/code-reviewer-drill.json --rules {repo}/rules", [2], r"--transcript"),
        S("eval", "pending --unit " + AGENT, [0], r"gate requirements_eval_set status missing", r"gate rule_review status missing",
          r"2 pending approval\(s\); nothing was recorded"),
        S("eval", "install " + AGENT + " --target {scratch}/target", [0], r"hermes: config\.yaml UNVERIFIED, not written",
          r"install code-reviewer-v2 \(agent\) -> \S+: 4 create, 0 overwrite, 0 unchanged"),
        has(*AGENT_DESTS),
        same(WRAPPERS[0], AGENT_DESTS[0]),
        same(WRAPPERS[3], AGENT_DESTS[3]),
    ],
    "agent-recorded-iteration": [
        S("eva", "generate " + AGENT, [0], r"4 written"),
        put(AGENT_WS + "/iteration-1/plan.json", PLAN_JSON),
        put(RUN_DIR + "/run.json", RUN_JSON),
        cp("transcripts/code-reviewer-drill.json", RUN_DIR + "/transcript.json"),
        S("eval", "agent grade " + AGENT, [0], r"1 run\(s\) graded; llm criteria stay needs-review"),
        has(RUN_DIR + "/grading.json"),
        S("eval", "agent report " + AGENT, [3], r"NEEDS APPROVAL", r"coverage-missing:hermes", r"parity-permissions-hermes",
          r"criteria-ungraded:drill/claude/new_agent/run-1"),
        has(AGENT_WS + "/iteration-1/benchmark.json", AGENT_WS + "/iteration-1/gate.json", AGENT_WS + "/iteration-1/comparison.md"),
        S("eval", "pending --unit " + AGENT, [0], r"gate registration status", r"gate coverage_waiver:hermes status"),
        put(RUN_DIR + "/transcript.json", '{"changed": true}'),
        S("eval", "agent report " + AGENT, [3], r"run-ungraded:drill/claude/new_agent/run-1"),
    ],
    "golden-agents": [
        S("eva", "check " + GOLDEN_AGENT, [1], r"^\[FAIL\] ", r"agent-frontmatter-v2"),
        S("eva", "check golden/agents/research-assistant", [1], r"^\[FAIL\] ", r"agent-frontmatter-v2"),
        S("eval", "agent validate " + GOLDEN_AGENT, [1], r"^validate: FAIL"),
        S("eval", "agent validate golden/agents/research-assistant", [1], r"^validate: FAIL"),
        S("eval", "agent plan " + GOLDEN_AGENT, [0], r"scenarios: 0 valid", r"DRY RUN: nothing was executed"),
        S("eval", "agent run " + GOLDEN_AGENT, [0], r"scenarios: 0 valid", r"DRY RUN: nothing was executed"),
        S("eval", "agent loop " + GOLDEN_AGENT, [0], r"scenarios: 0 valid", r"DRY RUN: nothing was executed"),
        S("eva", "generate " + GOLDEN_AGENT, [2], r"refused: AGENT\.md v1", r"Migrate AGENT\.md to v2"),
        S("eval", "install " + GOLDEN_AGENT + " --target {scratch}/target", [2], r"run `python3 eva\.py generate", r"nothing was written"),
        S("eval", "pending --unit " + GOLDEN_AGENT, [0], r"gate requirements_eval_set status missing", r"nothing was recorded"),
        lacks("golden/agents/code-reviewer-workspace", "../target/.claude"),
    ],
    "agent-scenarios": [
        S("eva", "check-all fixtures/agents", [1], r"total=2 pass=0 review=0 fail=2"),
        S("eval", "agent validate fixtures/agents/research-assistant-v2", [0], r"scenarios present: pass", r"scenario ids: pass"),
        S("eva", "check fixtures/agents/research-assistant-v2 --transcript transcripts/research-followup-cited.json", [3],
          r"^\[REVIEW\] ", r"scenario-known"),
        S("eva", "generate " + DOCS, [0], r"4 written"),
        S("eval", "agent validate " + DOCS, [0], r"scenarios present: fail \(scenarios-present: 1 valid", r"^validate: OK"),
        S("eval", "agent plan " + DOCS, [0], r"fixture one-broken-link docs-basic: sha256 [0-9a-f]{64}, 2 file\(s\)",
          r"fixture file one-broken-link docs-basic/docs/guide\.md: sha256 [0-9a-f]{64}", r"DRY RUN: nothing was executed"),
        put(DOCS + "/fixtures/docs-basic/.claude/x", "x\n"),
        S("eval", "agent plan " + DOCS, [2], r"agent plan refused: fixture docs-basic of scenario one-broken-link: fixtures/docs-basic/\.claude is a sandbox install folder name"),
        rm(DOCS + "/fixtures/docs-basic/.claude"),
        link(DOCS + "/fixtures/docs-basic/out", "/etc/hosts"),
        S("eval", "agent plan " + DOCS, [2], r"agent plan refused: fixture docs-basic of scenario one-broken-link: fixtures/docs-basic/out is a symlink"),
        rm(DOCS + "/fixtures/docs-basic/out"),
        rm(DOCS + "/fixtures/docs-basic"),
        S("eval", "agent plan " + DOCS, [2], r"agent plan refused: fixture docs-basic of scenario one-broken-link: fixtures/docs-basic is not a folder of the agent"),
        S("eval", "agent run " + DOCS, [2], r"agent plan refused: fixture"),
        S("eval", "agent loop " + DOCS, [2], r"agent plan refused: fixture"),
        cp("fixtures/agents/code-reviewer-v2", "fixtures/agents/pinned"),
        put("fixtures/agents/pinned/AGENT.md", "---\nname: pinned\ndescription: Reviews a repository read-only. Use when the user asks for a code review.\n"
            "goal: Analyze the repository read-only and write a review with file and line evidence.\ncapabilities: [read, search, skill]\n"
            "model_policy: judgment\nsuccess_criteria:\n  - '{\"type\": \"no_writes\"}'\ntargets: [claude, codex, opencode, hermes]\n"
            "skills: [ghost@1.0.0]\n---\n# Pinned\n## Tools\n- search and read.\n## Memory & Handoff\nKeeps nothing.\n"),
        S("eval", "agent plan fixtures/agents/pinned", [2], r"pin ghost@1\.0\.0: REFUSED \(unregistered: no skill folder",
          r"agent plan refused: pin ghost@1\.0\.0 does not verify"),
    ],
    "real-skills": [
        S("eva", "check-all skills", [0], r"total=3 pass=3 review=0 fail=0"),
    ] + [S("eval", "validate skills/" + n, [0], r"eval set OK: \d+ cases, \d+ trigger queries",
           r"multi-tool parity: claude=pass codex=pass opencode=pass hermes=pass") for n in REAL_SKILLS]
      + [S("eval", "plan skills/" + n, [0], r"DRY RUN: nothing was executed") for n in REAL_SKILLS]
      + [S("eval", "pending --unit skills/" + n, [0], r"nothing was recorded") for n in REAL_SKILLS]
      + [lacks(*("skills/%s-workspace" % n for n in REAL_SKILLS)), silent()],
    "unit-lifecycle": [
        S("eva", "feedback " + SKILL, [0], r"-> FEEDBACK\.md, FEEDBACK\.json \(PASS 1\.00\)"),
        has(SKILL + "/FEEDBACK.md", SKILL + "/FEEDBACK.json"),
        S("eva", "index fixtures/skills", [0], r""),
        has("fixtures/skills/INDEX.md", "fixtures/skills/registry.json"),
        S("eval", "install " + SKILL + " --target {scratch}/target --tools claude --dry-run", [0], r"^dry run: install csv-cleaner-en \(skill\) -> "),
        lacks("../target/.claude"),
        S("eval", "install " + SKILL + " --target {scratch}/target --tools claude", [0], r"^install csv-cleaner-en \(skill\) -> \S+: \d+ create, 0 overwrite, 0 unchanged"),
        has("../target/.claude/skills/csv-cleaner-en/SKILL.md"),
        same(SKILL + "/SKILL.md", "../target/.claude/skills/csv-cleaner-en/SKILL.md"),
        lacks(SKILL_WS),
        S("eval", "install " + SKILL + " --target {scratch}/target --tools claude", [0], r"^no change$"),
        S("eval", "install " + SKILL + " --target {scratch}/home", [2], r"is the home folder"),
        S("eval", "install " + SKILL, [2], r"a project install needs --target"),
        S("eval", "install " + SKILL + " --target {scratch}/target --tools opencode,hermes", [0], r"UNVERIFIED destination"),
        put("../target/.claude/skills/csv-cleaner-en/SKILL.md", "other bytes\n"),
        S("eval", "install " + SKILL + " --target {scratch}/target --tools claude", [2], r"collision: \S+ exists with other bytes"),
        S("eval", "install " + SKILL + " --target {scratch}/target --tools claude --force", [0], r"^backup \S+/\.unit-install-backup/\d{8}T\d{6}Z"),
        same(SKILL + "/SKILL.md", "../target/.claude/skills/csv-cleaner-en/SKILL.md"),
        link(SKILL + "/scripts/out", "/etc/hosts"),
        S("eval", "install " + SKILL + " --target {scratch}/target --tools claude", [2], r"scripts/out is a symbolic link"),
        S("eval", "snapshot " + SKILL, [2], r"scripts/out"),
        rm(SKILL + "/scripts/out"),
        S("eval", "version bump " + SKILL + " patch --dry-run", [2], r"version bump needs a changelog entry"),
        put(SKILL + "/CHANGELOG.md", "## 1.0.1\n\nFixes the row count.\n"),
        S("eval", "version bump " + SKILL + " patch --dry-run", [0], r"dry run: would bump csv-cleaner-en 1\.0\.0 -> 1\.0\.1"),
        holds(SKILL + "/SKILL.md", r"version: 1\.0\.0"),
        S("eval", "version bump " + SKILL + " patch", [0], r"bumped csv-cleaner-en 1\.0\.0 -> 1\.0\.1"),
        holds(SKILL + "/SKILL.md", r"version: 1\.0\.1"),
        S("eval", "deprecate " + SKILL + " --version 1.0.1 --reason superseded", [0], r"an unsigned marker"),
        has(SKILL_WS + "/deprecations.json"),
        S("eval", "deprecate " + SKILL + " --version 1.0.1 --reason superseded", [0], r"^no change$"),
        S("eval", "deprecate " + SKILL + " --version 9.0.0 --reason superseded", [2], r"above the current version"),
        S("eval", "install " + SKILL + " --target {scratch}/target --tools claude --force", [0], r"warning: csv-cleaner-en 1\.0\.1 is deprecated: superseded"),
        S("eval", "drift . --today 2026-10-30", [3], r"tool claude: pinned 2\.1\.302", r"older ruleset: skill/csv-cleaner-en"),
        S("eval", "drift . --today 2026-10-30 --observed codex=0.0.1", [3], r"DRIFT"),
        S("eval", "hub build fixtures/skills --out {scratch}/site", [0], r"hub build: 1 units"),
        has("../site/index.html", "../site/registry.json", "../site/style.css"),
        S("eval", "hub build fixtures/skills --out {scratch}/site", [2], r"must be absent or an empty folder; nothing was written"),
        S("eval", "snapshot " + SKILL, [0], r"snapshot -> \S+/" + SKILL_WS + "/skill-snapshot"),
        has(SKILL_WS + "/skill-snapshot/SKILL.md"),
        same(SKILL + "/SKILL.md", SKILL_WS + "/skill-snapshot/SKILL.md"),
        S("eval", "snapshot " + SKILL, [2], r"skill-snapshot exists"),
        S("eval", "builder-check " + SKILL + " --workspace " + SKILL_WS, [0],
          r"writable SKILL\.md references/\*\* scripts/\*\* assets/\*\*", r"^fingerprint [0-9a-f]{64} \d+ entries", save="builder.txt"),
        S("eval", "builder-check " + SKILL + " --workspace " + SKILL_WS + " --compare {scratch}/builder.txt", [0], r"compare: 0 changed"),
        put(SKILL + "/evals/trigger_evals.json", "[]\n"),
        S("eval", "builder-check " + SKILL + " --workspace " + SKILL_WS + " --compare {scratch}/builder.txt", [1],
          r"changed evals/trigger_evals\.json", r"compare: 1 changed"),
        lacks(SKILL_WS + "/approvals.json", SKILL_WS + "/registration.json"),
    ],
}


def now():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def refused(args):
    """argparse takes an unambiguous prefix of a long flag and a --flag=value form, so both count."""
    for a in args:
        word = a.split("=", 1)[0]
        if word in REFUSED_WORDS or (len(word) > 2 and any(flag.startswith(word) for flag in REFUSED_FLAGS)): return a
    return None


def sha256(path):
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def repo_digest(root=ROOT):
    h = hashlib.sha256()
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d != ".git")
        for n in sorted(fn):
            p = os.path.join(dp, n)
            h.update(os.path.relpath(p, root).encode("utf-8") + b"\0" + sha256(p).encode("ascii") + b"\n")
    return h.hexdigest()


def child_env(scratch, audit_fd, root):
    return {"PATH": "/usr/bin:/bin", "HOME": os.path.join(scratch, "home"), "TMPDIR": os.path.join(scratch, "tmp"),
            "LANG": "en_US.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.path.join(scratch, "hook"),
            "VERIFY_AUDIT_FD": str(audit_fd), "VERIFY_SCRATCH": scratch, "VERIFY_ROOT": root,
            "VERIFY_TOOL_CLIS": ",".join(TOOL_CLIS)}


def make_hooked(scratch):
    for sub in ("home", "tmp", "target", "hook", "work"): os.makedirs(os.path.join(scratch, sub))
    with open(os.path.join(scratch, "hook", "sitecustomize.py"), "w", encoding="ascii") as f: f.write(AUDIT_HOOK)


def make_scratch(run_id, root=ROOT):
    scratch = os.path.join(SCRATCH, run_id)
    make_hooked(scratch)
    for src, dst in COPIES:
        shutil.copytree(os.path.join(root, *src.split("/")), os.path.join(scratch, "work", dst), symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    return scratch


def hooked_run(argv, cwd, scratch, audit_fd, root=ROOT, timeout=120):
    return subprocess.run(argv, cwd=cwd, env=child_env(scratch, audit_fd, root), capture_output=True, encoding="utf-8",
                          errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, pass_fds=(audit_fd,))


def tool_argv(tool, args, root=ROOT):
    return [sys.executable, os.path.join(root, "eva.py" if tool == "eva" else "skill_eval.py")] + list(args)


def audit_rows(path):
    with open(path, encoding="utf-8") as f: return [json.loads(x) for x in f if x.strip()]


def audit_counts(path):
    c = collections.Counter(r["kind"] for r in audit_rows(path))
    return {k: c[k] for k in AUDIT_KINDS}


def run_step(step, n, run):
    bad = refused(step.args)
    args = [a.format(repo=run.root, scratch=run.scratch) for a in step.args]
    bad = bad or refused(args)
    if bad: raise SystemExit("drive: refusing the step %s: %r is outside this skill" % (step.args, bad))
    before = audit_counts(run.audit_path)
    p = hooked_run(tool_argv(step.tool, args, run.root), os.path.join(run.scratch, "work"), run.scratch, run.audit_fd, run.root)
    after = audit_counts(run.audit_path)
    delta = {k: after[k] - before[k] for k in AUDIT_KINDS}
    text = p.stdout + p.stderr
    missing = [rx for rx in step.expect if rx and not re.search(rx, text, re.M)]
    ok = p.returncode in step.exits and not missing and delta["loaded"] >= 1
    shown = "%s %s" % ("eva.py" if step.tool == "eva" else "skill_eval.py", shlex.join(args))
    name = "%02d-%s.txt" % (n, re.sub(r"[^a-z0-9]+", "-", (step.tool + " " + " ".join(step.args[:2])).lower()).strip("-")[:48])
    with open(os.path.join(run.evidence, name), "w", encoding="utf-8") as f:
        f.write("$ %s\nexit %d\naudit %s\n--- stdout\n%s\n--- stderr\n%s\n" % (shown, p.returncode, json.dumps(delta), p.stdout, p.stderr))
    if step.save:
        with open(os.path.join(run.scratch, step.save), "w", encoding="utf-8") as f: f.write(p.stdout)
    return {"n": n, "command": shown, "exit": p.returncode, "allowed_exits": list(step.exits), "missing": missing, "ok": ok,
            "audit": delta, "output": name}


def run_seed(seed, n, run):
    path = os.path.normpath(os.path.join(run.scratch, "work", seed.path))
    if seed.kind == "remove":
        if os.path.isdir(path) and not os.path.islink(path): shutil.rmtree(path)
        else: os.unlink(path)
    else: os.makedirs(os.path.dirname(path), exist_ok=True)
    if seed.kind == "write":
        with open(path, "w", encoding="utf-8") as f: f.write(seed.value)
    elif seed.kind == "link": os.symlink(seed.value, path)
    elif seed.kind == "copy":
        src = os.path.join(run.scratch, "work", seed.value)
        if os.path.isdir(src): shutil.copytree(src, path, symlinks=True)
        else: shutil.copyfile(src, path)
    return {"n": n, "seed": seed.kind, "paths": [seed.path], "ok": True}


def run_probe(probe, n, run):
    paths = [os.path.normpath(os.path.join(run.scratch, "work", p)) for p in probe.paths]
    if probe.kind == "exists": bad = [p for p in paths if not os.path.lexists(p)]
    elif probe.kind == "absent": bad = [p for p in paths if os.path.lexists(p)]
    elif probe.kind == "holds":
        with open(paths[0], encoding="utf-8") as f: bad = [] if re.search(probe.paths[1], f.read()) else [probe.paths[1]]
    elif probe.kind == "silent":
        rows = audit_rows(run.audit_path)
        tmp = os.path.join(run.scratch, "tmp") + os.sep
        made = {r["detail"][1] for r in rows if r["kind"] == "write" and r["detail"][0] == "open"}
        gone = {r["detail"][1] for r in rows if r["kind"] == "write" and r["detail"][0] == "os.remove"}
        probes = {p for p in made & gone if p.startswith(tmp)}
        bad = [r for r in rows if r["kind"] in ("process", "socket", "write", "write-outside")
               and not (r["kind"] == "write" and r["detail"][1] in probes)]
    else: bad = [] if all(map(os.path.isfile, paths)) and sha256(paths[0]) == sha256(paths[1]) else paths
    return {"n": n, "probe": probe.kind, "paths": list(probe.paths), "problems": bad, "ok": not bad}


ROWS = {Step: run_step, Seed: run_seed, Probe: run_probe}


def audit_summary(path):
    rows = audit_rows(path)
    out = {k: [r["detail"] for r in rows if r["kind"] == k] for k in AUDIT_KINDS}
    out["read-outside"], out["gate-read"] = sorted(set(out["read-outside"])), sorted(set(out["gate-read"]))
    return out


def drive(name, root=ROOT):
    run_id = "%s-%s" % (now(), name)
    evidence = os.path.join(EVIDENCE, run_id)
    os.makedirs(evidence)
    before = repo_digest(root)
    scratch = make_scratch(run_id, root)
    audit_path = os.path.join(evidence, "audit.jsonl")
    audit_fd = os.open(audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    run, rows = Run(scratch, evidence, audit_fd, audit_path, root), []
    try:
        for n, item in enumerate(JOURNEYS[name], 1):
            rows.append(ROWS[type(item)](item, n, run))
    finally:
        os.close(audit_fd)
    audit = audit_summary(audit_path)
    after = repo_digest(root)
    side = {"processes": audit["process"], "sockets": audit["socket"], "writes_inside_scratch": len(audit["write"]),
            "writes_outside_scratch": audit["write-outside"], "tool_cli_refused": audit["tool-cli-refused"],
            "reads_outside": audit["read-outside"], "gate_files_opened": audit["gate-read"], "hook_loads": len(audit["loaded"]),
            "repo_unchanged": before == after, "repo_digest": after}
    ok = all(r["ok"] for r in rows) and side["repo_unchanged"] and not (
        audit["process"] or audit["socket"] or audit["write-outside"] or audit["tool-cli-refused"])
    verdict = {"journey": name, "ok": ok, "steps": rows, "side_effects": side, "scratch": scratch, "evidence": evidence}
    with open(os.path.join(evidence, "verdict.json"), "w", encoding="utf-8") as f: json.dump(verdict, f, indent=2)
    for r in rows:
        print("%s %s" % ("ok  " if r["ok"] else "FAIL", r.get("command") or "%s %s" % (r.get("probe") or r["seed"], " ".join(r["paths"]))))
        if not r["ok"]: print("     %s" % json.dumps({k: v for k, v in r.items() if k in ("exit", "allowed_exits", "missing", "problems")})[:600])
    print("side effects: hook_loads=%d processes=%d sockets=%d writes_inside=%d writes_outside_scratch=%d tool_cli_refused=%d "
          "reads_outside=%d repo_unchanged=%s" % (side["hook_loads"], len(audit["process"]), len(audit["socket"]), len(audit["write"]),
                                                  len(audit["write-outside"]), len(audit["tool-cli-refused"]),
                                                  len(audit["read-outside"]), side["repo_unchanged"]))
    print("%s %s evidence %s" % ("PASS" if ok else "FAIL", name, evidence))
    return 0 if ok else 1


def answers(argv):
    try:
        p = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as ex:
        return None, "%s: %s" % (type(ex).__name__, ex)
    return p.returncode, next((x.strip() for x in (p.stdout + p.stderr).splitlines() if x.strip()), "no output")


def expand(pattern):
    return sorted(glob.glob(os.path.expanduser(pattern)))


def java_inventory():
    """A file on PATH proves nothing on macOS, where /usr/bin/java is a stub that exits 1 without a JDK."""
    found = [shutil.which("java")] if shutil.which("java") else []
    if os.environ.get("JAVA_HOME"): found.append(os.path.join(os.environ["JAVA_HOME"], "bin", "java"))
    found += [os.path.join(h, "bin", "java") for pattern in JAVA_HOMES for h in expand(pattern)]
    lines, works = [], False
    for path in dict.fromkeys(p for p in found if os.path.exists(p)):
        code, first = answers([path, "-version"])
        works = works or code == 0
        lines.append("java candidate %s: exit %s: %s" % (path, code, first))
    return works, lines or ["java candidate: none on PATH, none under JAVA_HOME or the JDK folders"]


def jar_inventory(roots=JAR_ROOTS):
    found, seen, unreadable = {k: [] for k in JAR_PATTERNS}, 0, []

    def walk(path, depth):
        nonlocal seen
        try:
            entries = list(os.scandir(path))
        except FileNotFoundError:
            return
        except OSError:
            unreadable.append(path)
            return
        seen += 1
        for e in entries:
            for kind, rx in JAR_PATTERNS.items():
                if rx.fullmatch(e.name): found[kind].append(e.path)
            if depth > 1 and e.name not in JAR_SKIP and e.is_dir(follow_symlinks=False): walk(e.path, depth - 1)

    for root, depth in roots: walk(os.path.expanduser(root), depth)
    for entry in os.environ.get("CLASSPATH", "").split(":"):
        for kind, rx in JAR_PATTERNS.items():
            if entry and rx.fullmatch(os.path.basename(entry)) and os.path.exists(entry): found[kind].append(entry)
    return found, seen, unreadable


def doctor(_):
    print("python %s (%s)" % (sys.version.split()[0], sys.executable))
    print("repo %s" % ROOT)
    print("repo is a git clone: %s" % os.path.exists(os.path.join(ROOT, ".git")))
    print("scratch %s" % SCRATCH)
    print("evidence %s" % EVIDENCE)
    java_ok, java_lines = java_inventory()
    print("java runtime answered: %s" % ("yes" if java_ok else "no"))
    for line in java_lines: print("  " + line)
    jars, seen, unreadable = jar_inventory()
    for kind in sorted(jars): print("%s jars or apps: %s" % (kind, ", ".join(sorted(set(jars[kind]))) or "none"))
    print("jar search: %d folders read, %d unreadable%s" % (seen, len(unreadable), " (%s)" % ", ".join(unreadable) if unreadable else ""))
    tools = [(t, answers([t, flag])) for t, flag in VERIFIERS if shutil.which(t)]
    print("verification tools on PATH: %s" % (", ".join("%s (exit %s: %s)" % (t, a[0], a[1]) for t, a in tools) or "none"))
    print("verification modules: %s" % (", ".join(m for m in MODULES if importlib.util.find_spec(m)) or "none"))
    print("tool CLIs on PATH (never started): %s" % (", ".join(t for t in TOOL_CLIS if shutil.which(t)) or "none"))
    flags = [k for k in ACCOUNT_ENV if k in os.environ]
    print("account variables set: %s" % (", ".join(flags) or "none"))
    if flags: print("  tests/test_account_switch.py and test_auth_profiles.py read these and can fail without a code change")
    scratch = os.path.join(SCRATCH, "%s-doctor" % now())
    make_hooked(scratch)
    audit_path = os.path.join(scratch, "audit.jsonl")
    audit_fd = os.open(audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        p = hooked_run(tool_argv("eva", ["check", os.path.join(ROOT, "tests", *SKILL.split("/"))]), os.path.join(scratch, "work"),
                       scratch, audit_fd, timeout=60)
    finally:
        os.close(audit_fd)
    audit = audit_counts(audit_path)
    shutil.rmtree(scratch, True)
    print("eva.py check exit %d, %s" % (p.returncode, next(iter(re.findall(r"ruleset_sha256 [0-9a-f]{64}", p.stdout)), "NO ruleset line")))
    print("eva.py check audit: hook_loads=%d processes=%d sockets=%d writes_inside=%d writes_outside=%d reads_outside=%d"
          % (audit["loaded"], audit["process"], audit["socket"], audit["write"], audit["write-outside"], audit["read-outside"]))
    healthy = (sys.version_info >= (3, 9) and p.returncode == 0 and "ruleset_sha256" in p.stdout and audit["loaded"] >= 1
               and not (audit["process"] or audit["socket"] or audit["write"] or audit["write-outside"]))
    print("doctor: %s" % ("ready" if healthy else "NOT ready"))
    return 0 if healthy else 1


SELF_CHILD = r'''
import os, shutil, subprocess, sys
fake, outside = sys.argv[1], sys.argv[2]
for attempt in (lambda: subprocess.run([fake]), lambda: subprocess.run("%s --version" % fake, shell=True),
                lambda: os.system("%s --version" % fake), lambda: os.spawnv(os.P_WAIT, fake, [fake, "--version"])):
    try: attempt()
    except PermissionError: pass
with open(outside, "w") as f: f.write("x")
os.mkdir(outside + "-tree")
shutil.rmtree(outside + "-tree")
with open("/etc/hosts") as f: f.read()
sys.audit("socket.connect", None, ("127.0.0.1", 9))
subprocess.run(["/usr/bin/true"])
'''
ROGUE_EVA = r'''
import os, subprocess, sys
kind, outside = sys.argv[1], sys.argv[2]
if kind == "process": subprocess.run(["/usr/bin/true"])
if kind == "socket": sys.audit("socket.connect", None, ("127.0.0.1", 9))
if kind == "outside": open(outside, "w").close()
if kind == "repo":
    os.close(os.open("changed", os.O_WRONLY | os.O_CREAT, dir_fd=os.open(os.path.dirname(os.path.abspath(__file__)), os.O_RDONLY)))
'''
ROGUE_RULES = (("process", lambda side: side["processes"]), ("socket", lambda side: side["sockets"]),
               ("outside", lambda side: side["writes_outside_scratch"]), ("repo", lambda side: not side["repo_unchanged"]))


def rogue_drive(kind, root):
    """The real drive() over a copy whose eva.py does one side effect, so a FAIL can only come from the journey rule.
    dir_fd keeps the repo write out of the hook's outside-write count, which is the case only repo_digest can see."""
    name = "selftest-" + kind
    JOURNEYS[name] = [S("eva", kind + " {scratch}-outside", [0])]
    out = io.StringIO()
    with contextlib.redirect_stdout(out): code = drive(name, root)
    with open(os.path.join(out.getvalue().rsplit("evidence ", 1)[1].strip(), "verdict.json"), encoding="utf-8") as f: verdict = json.load(f)
    if os.path.exists(verdict["scratch"] + "-outside"): os.unlink(verdict["scratch"] + "-outside")
    shutil.rmtree(verdict["scratch"], True)
    return code, verdict


HIT, MISS = "REPRODUCED", "REFUTED"
Claim = collections.namedtuple("Claim", "id holds summary evidence reach counted", defaults=(True,))
FIXTURE = ("tests", "fixtures", "skills", "csv-cleaner-en")


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f: f.write(data)


class Lab:
    def __init__(self, root, name):
        self.root, self.name, self.n = root, name, 0
        sys.path.insert(0, root)
        import eva, skill_eval
        from engine import install, rule_runner
        if os.path.dirname(os.path.realpath(skill_eval.__file__)) != root:
            raise RuntimeError("imported %s, not the code under --root" % skill_eval.__file__)
        self.eva, self.se, self.rr, self.install = eva, skill_eval, rule_runner, install
        os.makedirs(SCRATCH, exist_ok=True)
        self.base = tempfile.mkdtemp(prefix="%s-%s-" % (now(), name), dir=SCRATCH)
        make_hooked(self.base)
        self.audit_path = os.path.join(self.base, "audit.jsonl")
        self.audit_fd = os.open(self.audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)

    def folder(self, *rel):
        self.n += 1
        return os.path.join(self.base, "v%02d" % self.n, *rel)

    def skill(self):
        d = self.folder("csv-cleaner-en")
        shutil.copytree(os.path.join(self.root, *FIXTURE), d)
        return d

    def tool(self, tool, *args):
        return hooked_run(tool_argv(tool, args, self.root), self.base, self.base, self.audit_fd, self.root)

    def verdict_of(self, d):
        p = self.tool("eva", "check", d)
        return "eva.py check exit %d %s" % (p.returncode, next(iter(re.findall(r"^\[(\w+)\]", p.stdout, re.M)), "no verdict"))


def repro_lines(lab, claims, unchanged, tail):
    audit = audit_counts(lab.audit_path)
    lines = ["code under test %s" % lab.root, "scratch %s" % lab.base]
    for c in claims:
        lines.append("%s %s: %s" % (HIT if c.holds else MISS, c.id, c.summary))
        lines += ["    " + x for x in c.evidence] + ["    reach: " + c.reach]
    lines += tail
    lines.append("child audit: hook_loads=%d processes=%d sockets=%d writes_outside=%d tool_cli_refused=%d; root unchanged: %s"
                 % (audit["loaded"], audit["process"], audit["socket"], audit["write-outside"], audit["tool-cli-refused"], unchanged))
    counted = [c for c in claims if c.counted]
    held = [c.id for c in counted if c.holds]
    lines.append("%s candidate %s: %d of %d defect sub-claims hold: %s" % (HIT if held else MISS, lab.name[-1].upper(), len(held), len(counted), ", ".join(held) or "none"))
    return lines


def repro_main(name, doc, run, tail=()):
    parser = argparse.ArgumentParser(description=doc.splitlines()[0])
    parser.add_argument("--root", default=ROOT, help="the code under test, default the clone")
    root = os.path.realpath(parser.parse_args().root)
    try:
        before = repo_digest(root)
        lab = Lab(root, name)
        claims = run(lab)
        os.close(lab.audit_fd)
        lines = repro_lines(lab, claims, before == repo_digest(root), tail)
    except Exception as ex:
        print("crash: %s: %s" % (type(ex).__name__, str(ex).replace(HIT, "<verdict>").replace(MISS, "<verdict>")), file=sys.stderr)
        return 2
    evidence = os.path.join(EVIDENCE, "%s-%s" % (now(), name))
    os.makedirs(evidence)
    with open(os.path.join(evidence, "report.txt"), "w", encoding="utf-8") as f: f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("evidence %s" % evidence)
    return 0


def selftest(_):
    results = []

    def check(label, passed):
        results.append(passed)
        print("%s %s" % ("ok  " if passed else "FAIL", label))

    sys.dont_write_bytecode = True
    sys.path.insert(0, ROOT)
    import skill_eval
    parser = skill_eval.build_parser()
    forms = (("run", "x"), ("grade", "x"), ("calibrate", "x", "--golden", "g"), ("loop", "x"), ("agent", "run", "x"),
             ("agent", "grade", "x"), ("agent", "loop", "x"))
    took = 0
    for form in forms:
        for n in range(3, len("--live") + 1):
            argv = list(form) + ["--live"[:n]]
            with contextlib.redirect_stderr(io.StringIO()):
                try: accepted = parser.parse_args(argv).live is True
                except SystemExit: accepted = False
            took += accepted
            if accepted and not refused(argv): check("refused() misses %s" % argv, False)
    check("argparse took %d prefix spellings of --live on the real parser, refused() caught each" % took, took >= len(forms))
    for spelled in (["--live=1"], ["--liv"], ["--li"], ["--l=1"], ["--scope", "user"], ["--scope=user"], ["--sc", "user"],
                    ["approve", "x"], ["register", "x"], ["machine-waiver", "show"], ["check-builtin-plugins"],
                    ["eval", "approve", "x"]):
        check("refused() catches %s" % spelled, refused(spelled) is not None)
    for allowed in (["--llm-cases", "1"], ["--dry-run"], ["--strict-budget"], ["run", "x"]):
        check("refused() passes %s" % allowed, refused(allowed) is None)

    scratch = os.path.join(SCRATCH, "%s-selftest" % now())
    make_hooked(scratch)
    work, audit_path = os.path.join(scratch, "work"), os.path.join(scratch, "audit.jsonl")
    audit_fd = os.open(audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    run = Run(scratch, scratch, audit_fd, audit_path)
    placed = 0
    absent = [rel for rows in JOURNEYS.values() for r in rows if isinstance(r, Probe) and r.kind == "absent" for rel in r.paths]
    for rel in absent:
        path, top = os.path.normpath(os.path.join(work, rel)), None
        while not os.path.lexists(path if top is None else os.path.dirname(top)):
            top = path if top is None else os.path.dirname(top)
        clean = run_probe(lacks(rel), 0, run)["ok"]
        os.makedirs(path)
        placed += clean and not run_probe(lacks(rel), 0, run)["ok"]
        shutil.rmtree(top or path)
    check("every lacks path flips to FAIL when the path exists (%d of %d)" % (placed, len(absent)), placed == len(absent))
    with open(os.path.join(work, "a"), "w") as f: f.write("one")
    with open(os.path.join(work, "b"), "w") as f: f.write("two")
    check("has fails on an absent path", not run_probe(has("nope"), 0, run)["ok"])
    check("same fails on other bytes", not run_probe(same("a", "b"), 0, run)["ok"])
    check("holds fails on other text", not run_probe(holds("a", "two"), 0, run)["ok"])
    step = lambda exits, *rx: Step("eva", ["--help"], tuple(exits), rx, None)
    check("a step fails on the wrong exit", not run_step(step([9], r"usage"), 1, run)["ok"])
    check("a step fails on a missing pattern", not run_step(step([0], r"no such text"), 2, run)["ok"])
    check("a step passes on the right exit and pattern", run_step(step([0], r"usage"), 3, run)["ok"])

    fake = os.path.join(scratch, "fake", TOOL_CLIS[0])
    os.makedirs(os.path.dirname(fake))
    mark = os.path.join(scratch, "fake", "started")
    with open(fake, "w") as f: f.write("#!/bin/sh\necho started > %s\n" % shlex.quote(mark))
    os.chmod(fake, 0o755)
    child = os.path.join(scratch, "child.py")
    with open(child, "w") as f: f.write(SELF_CHILD)
    outside = os.path.join(SCRATCH, os.path.basename(scratch) + "-outside")
    before = audit_counts(audit_path)
    hooked_run([sys.executable, child, fake, outside], work, scratch, audit_fd)
    after = audit_counts(audit_path)
    delta = {k: after[k] - before[k] for k in AUDIT_KINDS}
    check("the hook loads in a child", delta["loaded"] >= 1)
    check("the hook refuses a tool CLI in list, shell, os.system and os.spawnv form: %d refused, fake never started"
          % delta["tool-cli-refused"], delta["tool-cli-refused"] == 4 and not os.path.exists(mark))
    check("the hook logs a write outside scratch", delta["write-outside"] >= 1)
    check("the hook logs shutil.rmtree with its true path", any(
        r["kind"] == "write-outside" and r["detail"] == ["shutil.rmtree", outside + "-tree"] for r in audit_rows(audit_path)))
    check("the hook logs a read outside scratch and the repository", delta["read-outside"] >= 1)
    check("the hook logs a socket event (synthetic sys.audit, no network used)", delta["socket"] >= 1)
    check("the hook logs a process start", delta["process"] >= 3)
    check("silent fails once the audit holds a process", not run_probe(silent(), 0, run)["ok"])
    probe_log = os.path.join(scratch, "probe.jsonl")
    pair = [{"kind": "write", "detail": [e, os.path.join(scratch, "tmp", "p")]} for e in ("open", "os.remove")]
    with open(probe_log, "w") as f: f.write("".join(json.dumps(r) + "\n" for r in pair))
    paired = run_probe(silent(), 0, Run(scratch, scratch, -1, probe_log))["ok"]
    with open(probe_log, "w") as f: f.write(json.dumps(pair[0]) + "\n")
    check("silent passes a tempfile probe and fails a file left in $TMPDIR",
          paired and not run_probe(silent(), 0, Run(scratch, scratch, -1, probe_log))["ok"])
    os.unlink(os.path.join(scratch, "hook", "sitecustomize.py"))
    check("a step fails when the hook did not load", not run_step(step([0], r"usage"), 4, run)["ok"])
    os.close(audit_fd)
    shelf = os.path.join(scratch, "shelf")
    for rel in ("tools/tla/tla2tools.jar", "tools/org.alloytools.alloy.dist.jar", "tools/readme.txt"):
        write_bytes(os.path.join(shelf, *rel.split("/")), b"x")
    jars = jar_inventory(((shelf, 3),))[0]
    check("the jar search finds a TLA+ jar and an Alloy jar and nothing else: %d, %d" % (len(jars["TLA+"]), len(jars["Alloy"])),
          len(jars["TLA+"]) == 1 and len(jars["Alloy"]) == 1)
    check("the jar search misses a jar below its depth limit", not jar_inventory(((shelf, 1),))[0]["TLA+"])
    jdk = os.path.join(scratch, "jdk", "bin", "java")
    write_bytes(jdk, b"#!/bin/sh\necho 'openjdk 21'\nexit 0\n")
    os.chmod(jdk, 0o755)
    was_home = os.environ.get("JAVA_HOME")
    os.environ["JAVA_HOME"] = os.path.join(scratch, "jdk")
    java_ok, java_lines = java_inventory()
    if was_home is None: del os.environ["JAVA_HOME"]
    else: os.environ["JAVA_HOME"] = was_home
    check("java_inventory says yes when a candidate answers: %s" % java_lines[-1], java_ok)
    copy = os.path.join(scratch, "repo-copy")
    shutil.copytree(os.path.join(ROOT, "policy"), copy)
    was = repo_digest(copy)
    with open(os.path.join(copy, "extra"), "w") as f: f.write("x")
    check("repo_digest moves when a file appears (scratch copy of policy/)", repo_digest(copy) != was)
    rogue = os.path.join(scratch, "rogue")
    for src, _ in COPIES: shutil.copytree(os.path.join(ROOT, *src.split("/")), os.path.join(rogue, *src.split("/")), symlinks=True)
    write_bytes(os.path.join(rogue, "eva.py"), ROGUE_EVA.encode("ascii"))
    code, verdict = rogue_drive("none", rogue)
    check("the real drive() passes a journey with no side effect on the rogue copy", code == 0 and verdict["ok"])
    for kind, _ in ROGUE_RULES:
        code, verdict = rogue_drive(kind, rogue)
        fired = [k for k, rule in ROGUE_RULES if rule(verdict["side_effects"])]
        check("the real drive() fails a journey whose steps pass, side effect %s: exit %d, rules fired %s" % (kind, code, fired),
              code == 1 and not verdict["ok"] and all(r["ok"] for r in verdict["steps"]) and fired == [kind])
        if kind == "repo": os.unlink(os.path.join(rogue, "changed"))
    if os.path.exists(outside): os.unlink(outside)
    shutil.rmtree(scratch, True)
    print("selftest: %d checks, %d failed" % (len(results), results.count(False)))
    return 0 if all(results) else 1


FIXED_FRAMING = '''def sha256_labeled(pairs, marked=()):
    h = hashlib.sha256()
    def field(b): h.update(b"%d:" % len(b) + b)
    for label, p in pairs:
        field(label.encode())
        if os.path.isfile(p):
            field(b"file")
            with open(p, "rb") as f: field(f.read())
        else:
            field(b"missing")
    for label, marker in marked:
        field(label.encode()); field(b"marked"); field(marker)
    return h.hexdigest()
'''
FIXED_INSTALL = '''    import fnmatch
    ignored = ("evals", "__pycache__", "*-workspace", ".git", "FEEDBACK.md", "FEEDBACK.json")
    return sorted(x for x in out if not any(fnmatch.fnmatch(part, pat) for part in x[0].split("/") for pat in ignored))
'''


def fixed_copy(path):
    shutil.copytree(ROOT, path, ignore=shutil.ignore_patterns(".git", "verification", "__pycache__"))
    rx = re.compile(r"def sha256_labeled\(pairs, marked=\(\)\):.*?return h\.hexdigest\(\)\n", re.S)
    for rel in ("skill_eval.py", os.path.join("engine", "rule_runner.py")):
        with open(os.path.join(path, rel), encoding="utf-8") as f: text, n = rx.subn(lambda _: FIXED_FRAMING, f.read())
        if n != 1: raise SystemExit("flip: %s holds %d sha256_labeled definitions" % (rel, n))
        with open(os.path.join(path, rel), "w", encoding="utf-8") as f: f.write(text)
    rel = os.path.join("engine", "install.py")
    with open(os.path.join(path, rel), encoding="utf-8") as f: text = f.read()
    old = "    return sorted(out)\n\n\ndef declared_name"
    if text.count(old) != 1: raise SystemExit("flip: engine/install.py changed shape")
    with open(os.path.join(path, rel), "w", encoding="utf-8") as f: f.write(text.replace(old, FIXED_INSTALL + "\n\ndef declared_name"))


def flip(_):
    scratch = os.path.join(SCRATCH, "%s-flip" % now())
    fixed, empty = os.path.join(scratch, "fixed"), os.path.join(scratch, "empty")
    os.makedirs(empty)
    fixed_copy(fixed)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    results = []
    for script in ("repro_sha256_labeled.py", "repro_install_unhashed.py"):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
        for root, want in ((ROOT, HIT), (fixed, MISS)):
            p = subprocess.run([sys.executable, path, "--root", root], capture_output=True, encoding="utf-8", env=env, timeout=280)
            overall = next((x for x in p.stdout.splitlines() if re.match(r"(%s|%s) candidate " % (HIT, MISS), x)), "no overall line")
            ok = p.returncode == 0 and overall.startswith(want)
            results.append(ok)
            print("%s %s --root %s: exit %d: %s" % ("ok  " if ok else "FAIL", script, "clone" if root == ROOT else "fixed copy", p.returncode, overall[:150]))
            if root == fixed:
                for x in p.stdout.splitlines():
                    if x.startswith(HIT + " "): print("     still holds: %s" % x[:120])
        p = subprocess.run([sys.executable, path, "--root", empty], capture_output=True, encoding="utf-8", env=env, timeout=60)
        text = p.stdout + p.stderr
        ok = p.returncode != 0 and HIT not in text and MISS not in text
        results.append(ok)
        print("%s %s --root empty folder: exit %d: %s" % ("ok  " if ok else "FAIL", script, p.returncode, text.strip()[:120]))
    shutil.rmtree(scratch, True)
    print("flip: %d checks, %d failed" % (len(results), results.count(False)))
    return 0 if all(results) else 1


def cleanup(_):
    gone = sorted(os.listdir(SCRATCH)) if os.path.isdir(SCRATCH) else []
    shutil.rmtree(SCRATCH, True)
    left = os.path.exists(SCRATCH)
    print("removed %d scratch folder(s)%s; evidence stays in %s (%d folders)"
          % (len(gone), ", SCRATCH STILL EXISTS" if left else "", EVIDENCE, len(os.listdir(EVIDENCE)) if os.path.isdir(EVIDENCE) else 0))
    return 1 if left else 0


def run_all(_):
    return max(drive(name) for name in JOURNEYS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=doctor)
    sub.add_parser("all").set_defaults(fn=run_all)
    sub.add_parser("selftest").set_defaults(fn=selftest)
    sub.add_parser("flip").set_defaults(fn=flip)
    sub.add_parser("cleanup").set_defaults(fn=cleanup)
    p = sub.add_parser("journey")
    p.add_argument("name", choices=sorted(JOURNEYS))
    p.set_defaults(fn=lambda a: drive(a.name))
    a = parser.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
