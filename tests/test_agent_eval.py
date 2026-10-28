"""Unit B3: agent evaluation per tool (skill_eval.py agent, engine/agent_eval.py, engine/normalize/). Every engine and
grader here is faked: subprocess is patched to fail for the whole module, and approvals are recorded by the test
approver of tests/test_skill_eval.py, which starts no process."""
import contextlib, hashlib, io, json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import eva, skill_eval as se
import test_skill_eval as T

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
AGENT_FIXTURE = os.path.join(FIXTURES, "agents", "code-reviewer-v2")
CLAUDE_STREAM = os.path.join(FIXTURES, "streams", "claude", "overview.jsonl")
CODEX_STREAM = os.path.join(FIXTURES, "streams", "codex", "overview.jsonl")
NAME = "code-reviewer-v2"
TOOLS = ("claude", "codex", "opencode", "hermes")
MULTI = "not-run: multi-turn unverified"
NOT_AVAILABLE = "not-run: engine not available"
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden)
        pt.start()
        _patches.append(pt)


def tearDownModule():
    for pt in _patches:
        pt.stop()


def read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def rjson(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = se.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def generated_copy(parent, edit=None, name=NAME):
    dst = os.path.join(parent, name)
    shutil.copytree(AGENT_FIXTURE, dst)
    if name != NAME:
        p = os.path.join(dst, "AGENT.md")
        text = read(p).replace("name: %s" % NAME, "name: %s" % name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    if edit:
        edit(dst)
    with contextlib.redirect_stdout(io.StringIO()):
        assert eva.main(["generate", dst]) == 0
    return dst


REQUIREMENTS = "Review the repository read-only and cite file:line evidence for every finding.\n"


def open_gates(agent_dir, ws):
    """What an agent live start needs (K1): <ws>/requirements.md, a current requirements_eval_set approval and a current
    rule_review decision, each recorded by the case's test approver when it does not verify yet."""
    os.makedirs(ws, exist_ok=True)
    if not os.path.isfile(os.path.join(ws, "requirements.md")):
        with open(os.path.join(ws, "requirements.md"), "w", encoding="utf-8") as f:
            f.write(REQUIREMENTS)
    spec = se.agent_spec(se.Agent(agent_dir))
    for gate in ("requirements_eval_set", "rule_review"):
        if gate == "rule_review" and not se.rule_review_items(agent_dir):
            continue
        if se.approval_verdict(gate, spec, ws).status != "approved":
            se.approve(gate, spec, ws)


def normalize(tool, text, sid="overview", agent=None):
    from engine import normalize as N
    return N.normalize(tool, text, sid, agent=agent)


def norm_error():
    from engine import normalize as N
    return N.NormalizeError


def lines(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


CLAUDE_INIT = {"type": "system", "subtype": "init", "cwd": "/w", "session_id": "s", "model": "m", "agents": [NAME],
               "skills": [], "plugins": [], "apiKeySource": "none"}
CLAUDE_RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "done", "session_id": "s",
                 "total_cost_usd": 0.01, "usage": {"input_tokens": 1, "output_tokens": 1}}
CODEX_HEAD = [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"}]
CODEX_TAIL = [{"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]


def codex_cmd(command, out="", code=0, iid="i"):
    return {"type": "item.completed", "item": {"id": iid, "type": "command_execution", "command": command,
                                              "aggregated_output": out, "exit_code": code, "status": "completed"}}


class TestNormalize(unittest.TestCase):
    def check_valid(self, t):
        self.assertEqual(eva.shape_schema("transcript.schema.json").problems(t), [])
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, "transcript.json")
        wjson(p, t)
        run, errs = eva.load_transcript(p)
        self.assertEqual(errs, [])
        return run

    def test_claude_fixture_normalizes_and_validates(self):
        t = normalize("claude", read(CLAUDE_STREAM), agent=NAME)
        self.check_valid(t)
        self.assertEqual((t["tool"], t["format"], t["scenario"]), ("claude", "claude-stream-json", "overview"))
        self.assertEqual(t["agent_loaded"], "verified")
        self.assertEqual([c["tool"] for c in t["tool_calls"]], ["Glob", "Read"])
        self.assertEqual(t["tool_calls"][1]["input"], {"file_path": "/sandbox/work/README.md"})
        self.assertTrue(t["tool_calls"][1]["result"].startswith("1\t# Demo"))
        self.assertIn("README.md:3", t["final"])
        self.assertEqual(t["workspace"], "/sandbox/work")
        self.assertEqual(t["usage"], {"input_tokens": 1500, "output_tokens": 150})
        self.assertEqual((t["cost_usd"], t["is_error"], t["model"]), (0.05, False, "claude-opus-5-5"))

    def test_codex_fixture_normalizes_and_validates(self):
        t = normalize("codex", read(CODEX_STREAM))
        self.check_valid(t)
        self.assertEqual((t["tool"], t["format"], t["agent_loaded"]), ("codex", "codex-exec-json", "unverified"))
        self.assertEqual([(c["tool"], c["input"]) for c in t["tool_calls"]],
                         [("shell", {"command": "ls"}), ("shell", {"command": "cat -n README.md"})])
        self.assertTrue(t["tool_calls"][1]["result"].startswith("     1\t# Demo"))
        self.assertTrue(t["final"].startswith("The repository is small."))
        self.assertEqual(t["usage"], {"input_tokens": 5200, "output_tokens": 184})
        self.assertIsNone(t["cost_usd"])

    def test_codex_command_execution_reads_as_shell_and_a_write_is_kept(self):
        text = lines(*CODEX_HEAD + [codex_cmd("/bin/zsh -lc 'rm -rf build'", "", 1),
                                    codex_cmd("git status", "clean", 0, "j")] + CODEX_TAIL)
        t = normalize("codex", text)
        self.check_valid(t)
        self.assertEqual(t["tool_calls"][0], {"tool": "shell", "input": {"command": "rm -rf build"}, "result": "",
                                              "id": "i", "is_error": True})
        self.assertEqual(t["tool_calls"][1]["input"], {"command": "git status"})
        from engine.primitives import transcript as P
        run = {"calls": [{"tool": c["tool"], "input": c["input"], "result": c["result"]} for c in t["tool_calls"]]}
        self.assertEqual(P.write_calls(run["calls"]), ["shell 'rm -rf build'"])

    def test_codex_shell_wrappers_are_unwrapped_or_refused(self):
        from engine.primitives import transcript as P
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        unwrapped = ["/bin/zsh -lc 'rm -rf build'", "/bin/bash -c 'rm -rf build' sh", "/bin/zsh -e -lc 'rm -rf build'",
                     "bash -xc 'rm -rf build'", "/usr/bin/env bash -c 'rm -rf build'",
                     "env CI=1 LANG=C /bin/sh -c 'rm -rf build' sh x", "CI=1 zsh -c 'rm -rf build'",
                     "bash -lc \"zsh -c 'rm -rf build'\""]
        for raw in unwrapped:
            with self.subTest(unwrapped=raw):
                t = normalize("codex", lines(*CODEX_HEAD + [codex_cmd(raw)] + CODEX_TAIL))
                self.assertEqual(t["tool_calls"][0]["input"], {"command": "rm -rf build"})
                p = os.path.join(d, "transcript.json")
                wjson(p, t)
                rows = {r["id"]: r["status"] for r in eva.grade_transcript(AGENT_FIXTURE, p).checks}
                self.assertEqual((rows["criteria-no-writes"], rows["expected-no-writes"]), ("fail", "fail"))
        refused = ["/bin/bash --noprofile -c 'rm -rf build'", "bash -o pipefail -c 'rm -rf build'", "bash build.sh",
                   "bash -cx 'rm -rf build'", "zsh -c", "env -i bash -c 'rm -rf build'", "bash", "bash -c 'rm -rf build",
                   "bash -lc \"bash --noprofile -c 'rm -rf build'\"", "/usr/local/bin/dash -c 'rm -rf build'"]
        for raw in refused:
            with self.subTest(refused=raw):
                with self.assertRaises(norm_error()) as cm:
                    normalize("codex", lines(*CODEX_HEAD + [codex_cmd(raw)] + CODEX_TAIL))
                self.assertIn("shell", str(cm.exception))
        for raw in ("git status", "/usr/bin/env python3 tool.py", "ls -la", "cat -n 'a b.txt'"):
            with self.subTest(kept=raw):
                t = normalize("codex", lines(*CODEX_HEAD + [codex_cmd(raw)] + CODEX_TAIL))
                self.assertEqual(t["tool_calls"][0]["input"], {"command": raw})
        self.assertEqual(P.write_calls([{"tool": "shell", "input": {"command": "git status"}}]), [])

    def test_a_codex_error_event_marks_the_transcript_failed(self):
        err = {"type": "error", "message": "stream disconnected"}
        t = normalize("codex", lines(*CODEX_HEAD + [err, codex_cmd("ls")] + CODEX_TAIL))
        self.assertIs(t["is_error"], True)
        self.assertIs(normalize("codex", lines(*CODEX_HEAD + [codex_cmd("ls")] + CODEX_TAIL))["is_error"], False)

    def test_reserved_agent_names(self):
        from engine import agent_eval as AE
        for name in ("claude", "Explore", "explore", "general-purpose", "GENERAL-PURPOSE", "Plan", "plan",
                     "statusline-setup", "pstack:poteto-agent", "codex:codex-rescue"):
            self.assertTrue(AE.reserved_agent_name(name), name)
        for name in (NAME, "planner", "claude-helper", "explorer"):
            self.assertFalse(AE.reserved_agent_name(name), name)

    def test_codex_file_change_is_an_apply_patch_call(self):
        item = {"type": "item.completed", "item": {"id": "f", "type": "file_change", "changes": [{"path": "a.py"}],
                                                   "status": "completed"}}
        t = normalize("codex", lines(*CODEX_HEAD + [item] + CODEX_TAIL))
        self.assertEqual(t["tool_calls"][0]["tool"], "apply_patch")
        from engine.primitives import transcript as P
        self.assertEqual(P.write_calls([{"tool": "apply_patch", "input": t["tool_calls"][0]["input"]}]), ["apply_patch"])
        notice = {"type": "item.completed", "item": {"id": "e", "type": "error", "message": "code mode is off"}}
        self.assertEqual(normalize("codex", lines(*CODEX_HEAD + [notice] + CODEX_TAIL))["tool_calls"], [])

    def test_unknown_types_are_refused(self):
        E = norm_error()
        claude_bad = [
            [CLAUDE_INIT, {"type": "mystery"}, CLAUDE_RESULT],
            [CLAUDE_INIT, {"type": "system", "subtype": "mystery"}, CLAUDE_RESULT],
            [CLAUDE_INIT, {"type": "assistant", "message": {"content": [{"type": "server_tool_use", "id": "x"}]}},
             CLAUDE_RESULT],
            [CLAUDE_INIT, {"type": "user", "message": {"content": [{"type": "image"}]}}, CLAUDE_RESULT],
            [CLAUDE_INIT, {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "nope",
                                                                    "content": "x"}]}}, CLAUDE_RESULT],
            [CLAUDE_INIT],
            [CLAUDE_RESULT],
        ]
        for events in claude_bad:
            with self.subTest(claude=events[1:2]), self.assertRaises(E):
                normalize("claude", lines(*events))
        with self.assertRaises(E):
            normalize("claude", "not json\n")
        codex_bad = [
            CODEX_HEAD + [{"type": "item.updated", "item": {"id": "i", "type": "todo_list"}}] + CODEX_TAIL,
            CODEX_HEAD + [{"type": "item.completed", "item": {"id": "i", "type": "web_search"}}] + CODEX_TAIL,
            CODEX_HEAD + [{"type": "item.completed", "item": {"id": "i", "type": "collab_tool_call", "tool": "spawn"}}]
            + CODEX_TAIL,
            CODEX_HEAD + [{"type": "item.started", "item": {"id": "i", "type": "command_execution",
                                                            "command": "rm x"}}] + CODEX_TAIL,
            CODEX_HEAD + [codex_cmd("ls")],
            CODEX_HEAD + [{"type": "mystery"}] + CODEX_TAIL,
        ]
        for events in codex_bad:
            with self.subTest(codex=events[2:3]), self.assertRaises(E):
                normalize("codex", lines(*events))

    def test_claude_tool_result_is_attached_and_errors_kept(self):
        events = [CLAUDE_INIT,
                  {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Write",
                                                                  "input": {"file_path": "x.txt"}}]}},
                  {"type": "system", "subtype": "permission_denied", "tool_name": "Write", "tool_use_id": "a"},
                  {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a",
                                                            "content": [{"type": "text", "text": "denied"}],
                                                            "is_error": True}]}},
                  CLAUDE_RESULT]
        t = normalize("claude", lines(*events), agent=NAME)
        self.assertEqual(t["tool_calls"], [{"tool": "Write", "input": {"file_path": "x.txt"}, "id": "a",
                                            "result": "denied", "is_error": True}])

    def test_agent_load_evidence(self):
        no_agent = dict(CLAUDE_INIT, agents=["general-purpose"])
        self.assertEqual(normalize("claude", lines(no_agent, CLAUDE_RESULT), agent=NAME)["agent_loaded"], "not-loaded")
        self.assertEqual(normalize("claude", lines(CLAUDE_INIT, CLAUDE_RESULT))["agent_loaded"], "unverified")
        self.assertEqual(normalize("codex", read(CODEX_STREAM), agent=NAME)["agent_loaded"], "unverified")

    def test_opencode_and_hermes_refuse(self):
        E = norm_error()
        for tool in ("opencode", "hermes"):
            with self.assertRaises(E) as cm:
                normalize(tool, read(CLAUDE_STREAM))
            self.assertIn("engine not available", str(cm.exception))
        with self.assertRaises(E):
            normalize("cursor", "")


OLD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Agent run transcript (eva.py check --transcript)",
    "description": "{\"scenario\": str, \"final\": str, \"workspace\": str (optional), \"tool_calls\": [\"<tool>\" | "
                   "{\"tool\": str, \"input\": any (optional), \"result\": str (optional)}], \"tool_results\": [str] "
                   "(optional)}. A $defs entry with a description is a message rule. Fix: write the transcript as "
                   "{\"scenario\": str, \"tool_calls\": [...], \"final\": str} (see LOOP.md).",
    "$comment": "source: house (eva.py load_transcript, transcript v1); severity: fail (the transcript-valid row of "
                "grade_transcript, must; no bundle rule, brief D2: a transcript is not a unit); protects: claude, codex, "
                "opencode, hermes.",
    "$ref": "#/$defs/transcript",
    "$defs": {
        "nonblank": {"type": "string", "pattern": "\\S"},
        "transcript": {
            "if": {"$ref": "#/$defs/transcript-object"},
            "then": {"allOf": [{"$ref": "#/$defs/scenario"}, {"$ref": "#/$defs/final"}, {"$ref": "#/$defs/workspace"},
                               {"$ref": "#/$defs/tool-calls"}, {"$ref": "#/$defs/tool-results"}]},
            "else": {"$ref": "#/$defs/transcript-object"}},
        "transcript-object": {"description": "transcript must be a JSON object, got %(type)s", "type": "object"},
        "scenario": {"description": "scenario: non-empty string required", "required": ["scenario"],
                     "properties": {"scenario": {"$ref": "#/$defs/nonblank"}}},
        "final": {"description": "final: string required", "required": ["final"],
                  "properties": {"final": {"type": "string"}}},
        "workspace": {"description": "workspace: non-empty string required",
                      "properties": {"workspace": {"$ref": "#/$defs/nonblank"}}},
        "tool-calls": {"if": {"$ref": "#/$defs/tool-calls-list"},
                       "then": {"properties": {"tool_calls": {"items": {"$ref": "#/$defs/tool-call"}}}},
                       "else": {"$ref": "#/$defs/tool-calls-list"}},
        "tool-calls-list": {"description": "tool_calls: list required", "required": ["tool_calls"],
                            "properties": {"tool_calls": {"type": "array"}}},
        "tool-call": {"if": {"$ref": "#/$defs/tool-call-shape"},
                      "then": {"if": {"type": "object"}, "then": {"allOf": [{"$ref": "#/$defs/tool-call-result"},
                                                                            {"$ref": "#/$defs/tool-call-fields"}]}},
                      "else": {"$ref": "#/$defs/tool-call-shape"}},
        "tool-call-shape": {
            "description": "tool_calls[%(i)s]: tool name string or {\"tool\": str, \"input\", \"result\"} object required",
            "anyOf": [{"$ref": "#/$defs/nonblank"},
                      {"type": "object", "required": ["tool"], "properties": {"tool": {"$ref": "#/$defs/nonblank"}}}]},
        "tool-call-result": {"description": "tool_calls[%(i)s].result: string required",
                             "properties": {"result": {"type": "string"}}},
        "tool-call-fields": {"description": "tool_calls[%(i)s]: unknown fields %(extra)s",
                             "properties": {"tool": True, "input": True, "result": True},
                             "additionalProperties": {"not": {}}},
        "tool-results": {"description": "tool_results: list of strings required",
                         "properties": {"tool_results": {"type": "array", "items": {"type": "string"}}}}}}
TRANSCRIPT_AGENTS = {"code-reviewer-drill.json": "code-reviewer-v2", "research-followup-cited.json": "research-assistant-v2",
                     "research-followup.json": "research-assistant-v2",
                     "bad/code-reviewer-answer-lookup.json": "code-reviewer-v2",
                     "bad/research-fabricated-urls.json": "research-assistant-v2"}
V1_VARIANTS = {"extra-top-keys": {"note": 1}, "workspace-blank": {"workspace": ""}, "final-number": {"final": 1},
               "tool-call-unknown-fields": {"tool_calls": [{"tool": "t", "zeta": 1}]},
               "tool-call-result-number": {"tool_calls": [{"tool": "t", "result": 1}]},
               "tool-results-null": {"tool_results": None}, "not-object": None}


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def old_shape(self):
        d = os.path.join(self.tmp, "schemas")
        wjson(os.path.join(d, "transcript.schema.json"), OLD_SCHEMA)
        with mock.patch.object(eva, "SCHEMA_DIR", d):
            return eva.ShapeSchema("transcript.schema.json")

    def both(self, agent, path):
        new = eva.grade_transcript(agent, path).checks
        with mock.patch.dict(eva._SHAPES, {"transcript.schema.json": self.old_shape()}):
            old = eva.grade_transcript(agent, path).checks
            old_load = eva.load_transcript(path)
        return old, new, old_load

    def test_v1_transcripts_keep_their_check_rows(self):
        base = rjson(os.path.join(ROOT, "transcripts", "code-reviewer-drill.json"))
        cases = [(os.path.join(ROOT, "transcripts", *rel.split("/")), os.path.join(FIXTURES, "agents", a))
                 for rel, a in TRANSCRIPT_AGENTS.items()]
        for name, change in V1_VARIANTS.items():
            p = os.path.join(self.tmp, name + ".json")
            wjson(p, [1] if change is None else dict(base, **change))
            cases.append((p, AGENT_FIXTURE))
        for path, agent in cases:
            old, new, old_load = self.both(agent, path)
            with self.subTest(transcript=os.path.basename(path)):
                self.assertEqual(new, old)
                self.assertEqual(eva.load_transcript(path), old_load)

    def test_normalized_fields_validate_and_bad_values_are_refused(self):
        base = rjson(os.path.join(ROOT, "transcripts", "code-reviewer-drill.json"))
        shape = eva.shape_schema("transcript.schema.json")
        good = dict(base, tool="claude", format="claude-stream-json", model="m", session_id="s",
                    turns=[{"user": "u", "final": "f"}], usage={"input_tokens": 0, "output_tokens": 3}, cost_usd=None,
                    is_error=False, agent_loaded="verified",
                    tool_calls=[{"tool": "Read", "input": {}, "result": "r", "id": "a", "is_error": False}])
        self.assertEqual(shape.problems(good), [])
        self.assertEqual(shape.problems(dict(good, cost_usd=0.5)), [])
        bad = {"tool": "cursor", "format": " ", "usage": {"input_tokens": -1, "output_tokens": 0},
               "cost_usd": -0.1, "is_error": "no", "agent_loaded": "maybe", "turns": [{"user": "u"}],
               "model": 5, "session_id": ""}
        for key, value in bad.items():
            with self.subTest(field=key):
                self.assertNotEqual(shape.problems(dict(good, **{key: value})), [])
        for key, value in (("id", 1), ("is_error", "x"), ("extra", 1)):
            with self.subTest(call_field=key):
                call = dict(good["tool_calls"][0], **{key: value})
                self.assertNotEqual(shape.problems(dict(good, tool_calls=[call])), [])
        self.assertNotEqual(shape.problems(dict(good, usage={"input_tokens": 1, "output_tokens": 1, "x": 1})), [])


class AgentBase(T.Base):
    def setUp(self):
        T.Base.setUp(self)
        self.agent = generated_copy(os.path.join(self.tmp, "agents"))
        self.ws = os.path.join(self.tmp, "agent-ws")

    def wrapper(self, agent=None, tool="claude"):
        rel = {"claude": "wrappers/claude/%s.md", "codex": "wrappers/codex/%s.toml"}[tool] % NAME
        return read(os.path.join(agent or self.agent, *rel.split("/")), "rb")

    def live(self, cmd, *extra, fake=None, gates=True):
        fake = fake or FakeAgentExec()
        if gates and cmd == "run":
            open_gates(self.agent, self.ws)
        runs = ("--runs", "1") if cmd == "run" else ()
        with mock.patch.object(se, "live_exec", fake):
            rc, out, err = cli("agent", cmd, self.agent, "--workspace", self.ws, "--live", "--auth-profile",
                               "personal-mac", "--budget-usd", "1", *(runs + extra))
        return rc, out, err, fake

    def run_dir(self, n=1, tool="claude", sid="overview", config="new_agent", k=1):
        return os.path.join(self.ws, "iteration-%d" % n, tool, sid, config, "run-%d" % k)

    def after_signing(self, change):
        real = se.APPROVER["it"]

        class Signer(object):
            def __getattr__(self, k):
                return getattr(real, k)

            def sign(self, kind, gate, prepare, budget_usd=None, reason=None):
                grant = real.sign(kind, gate, prepare, budget_usd=budget_usd, reason=reason)
                if kind == "live":
                    change()
                return grant
        return mock.patch.dict(se.APPROVER, {"it": Signer()})


class FakeAgentExec:
    """Fake claude/codex: records what the sandbox held at launch and answers with a fixture stream; a
    `claude -p --json-schema` call is the grader and gives `answer` to every rubric item."""
    def __init__(self, claude=None, codex=None, answer="yes"):
        self.claude = read(CLAUDE_STREAM) if claude is None else claude
        self.codex = read(CODEX_STREAM) if codex is None else codex
        self.answer = answer
        self.calls, self.grader_calls = [], []

    def __call__(self, cmd, cwd, timeout, env=None):
        if cmd[0] == "claude" and "--json-schema" in cmd:
            self.grader_calls.append(cmd)
            ids = [l.split("]")[0][3:] for l in cmd[2].split("Rubric items:\n")[1].splitlines() if l.startswith("- [")]
            so = {"items": [{"id": i, "answer": self.answer, "evidence": "cites README.md:1"} for i in ids]}
            return 0, json.dumps({"type": "result", "structured_output": so, "total_cost_usd": 0.001,
                                  "usage": {"input_tokens": 100, "output_tokens": 10}}), ""
        files = {}
        for dp, dn, fn in os.walk(cwd):
            for n in fn:
                p = os.path.join(dp, n)
                files[os.path.relpath(p, cwd).replace(os.sep, "/")] = read(p, "rb")
        self.calls.append({"cmd": cmd, "cwd": cwd, "files": files})
        if cmd[0] == "claude":
            return 0, self.claude, ""
        if cmd[0] == "codex":
            return 0, self.codex, ""
        raise AssertionError("unexpected launch %r" % (cmd[:1],))


NOT_LOADED_STREAM = read(CLAUDE_STREAM).replace('"Plan","code-reviewer-v2"]', '"Plan"]')


class NotLoadedExec(FakeAgentExec):
    """Answers the agent calls numbered in calls (1-based), or every call that installs wrapper, with a Claude stream
    whose init does not list the agent."""
    def __init__(self, calls=(), wrapper=None, **kw):
        FakeAgentExec.__init__(self, **kw)
        self.bad, self.bad_wrapper = set(calls), wrapper

    def __call__(self, cmd, cwd, timeout, env=None):
        rc, out, err = FakeAgentExec.__call__(self, cmd, cwd, timeout, env)
        if "--json-schema" in cmd:
            return rc, out, err
        installed = self.calls[-1]["files"].get(".claude/agents/%s.md" % NAME)
        if len(self.calls) in self.bad or (self.bad_wrapper is not None and installed == self.bad_wrapper):
            return rc, NOT_LOADED_STREAM, err
        return rc, out, err


class TestValidatePlan(AgentBase):
    def test_validate_passes_on_a_generated_copy(self):
        rc, out, err = cli("agent", "validate", self.agent)
        self.assertEqual(rc, 0, out + err)
        for prefix in ("AGENT.md v2: pass", "scenario schema: pass", "tool subset: pass", "tools declared: pass",
                       "wrapper claude: up to date", "wrapper codex: up to date", "wrapper opencode: up to date",
                       "wrapper hermes: up to date"):
            self.assertTrue(any(l.startswith(prefix) for l in out.splitlines()), (prefix, out))

    def test_validate_reports_a_missing_wrapper_without_failing(self):
        bare = os.path.join(self.tmp, "bare", NAME)
        shutil.copytree(AGENT_FIXTURE, bare)
        rc, out, _ = cli("agent", "validate", bare)
        self.assertEqual(rc, 0, out)
        self.assertIn("wrapper claude: missing (eva.py generate)", out)

    def test_validate_fails_on_an_undeclared_expected_tool(self):
        p = os.path.join(self.agent, "scenarios", "01-overview.json")
        doc = rjson(p)
        doc["expected_tools"].append("shell")
        wjson(p, doc)
        rc, out, _ = cli("agent", "validate", self.agent)
        self.assertEqual(rc, 1, out)
        self.assertTrue(any(l.startswith("tool subset: fail") for l in out.splitlines()), out)

    def test_plan_lists_two_scenarios_against_four_tools(self):
        with mock.patch.object(se, "live_exec", FakeAgentExec()) as fake:
            rc, out, err = cli("agent", "plan", self.agent, "--workspace", self.ws, "--runs", "1")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(fake.calls, [])
        self.assertFalse(os.path.exists(self.ws))
        cells = [l for l in out.splitlines() if l.startswith("cell ")]
        self.assertEqual(len(cells), 8, out)
        self.assertIn("cell overview claude: planned (est $0.32 for 1 exec call)", cells)
        rc, out, _ = cli("agent", "plan", self.agent, "--workspace", self.ws, "--old-agent", self.agent)
        self.assertIn("cell overview claude: planned (est $1.92 for 6 exec calls)", out)
        self.assertIn("cell overview codex: planned (est unpriced: pass --price)", cells)
        self.assertIn("cell overview opencode: %s" % NOT_AVAILABLE, cells)
        self.assertIn("cell overview hermes: %s" % NOT_AVAILABLE, cells)
        for tool in TOOLS:
            self.assertIn("cell drill %s: %s" % (tool, MULTI), cells)
        self.assertIn("tool coverage (dynamic): claude=planned, codex=planned, opencode=%s, hermes=%s"
                      % (NOT_AVAILABLE, NOT_AVAILABLE), out)
        self.assertIn("coverage_required", out)
        self.assertIn("multi-turn: non-interactive continuation is UNVERIFIED for claude, codex, opencode, hermes", out)

    def test_cell_states_follow_targets_selection_and_wrappers(self):
        from engine import agent_eval as AE
        scen = [{"file": "01-a.json", "id": "a", "turns": 1}, {"file": "02-b.json", "id": "b", "turns": 2}]
        cells = AE.cell_table(scen, targets=["claude", "opencode"], available=("claude", "codex"), selected=["claude"],
                              wrappers={"claude": None, "codex": None, "opencode": None, "hermes": None})
        self.assertEqual([(c["scenario"], c["tool"], c["state"]) for c in cells], [
            ("a", "claude", "planned"), ("a", "codex", "not-run: not in targets"),
            ("a", "opencode", NOT_AVAILABLE), ("a", "hermes", NOT_AVAILABLE),
            ("b", "claude", MULTI), ("b", "codex", MULTI), ("b", "opencode", MULTI), ("b", "hermes", MULTI)])
        rc, out, _ = cli("agent", "plan", self.agent, "--workspace", self.ws, "--engines", "claude")
        self.assertEqual(rc, 0, out)
        self.assertIn("cell overview codex: not-run: engine not selected", out)
        with open(os.path.join(self.agent, "wrappers", "claude", NAME + ".md"), "a", encoding="utf-8") as f:
            f.write("extra\n")
        os.remove(os.path.join(self.agent, "wrappers", "codex", NAME + ".toml"))
        rc, out, _ = cli("agent", "plan", self.agent, "--workspace", self.ws)
        self.assertIn("cell overview claude: not-run: wrapper out of date (eva.py generate)", out)
        self.assertIn("cell overview codex: not-run: wrapper missing (eva.py generate)", out)

    def test_run_without_live_is_a_dry_run(self):
        with mock.patch.object(se, "live_exec", FakeAgentExec()) as fake:
            rc, out, _ = cli("agent", "run", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 0)
        self.assertIn("DRY RUN: nothing was executed", out)
        self.assertEqual(fake.calls, [])
        self.assertFalse(os.path.exists(self.ws))

    def test_tables(self):
        from engine import agent_eval as AE
        self.assertEqual(AE.MULTI_TURN_VERIFIED, {t: False for t in TOOLS})
        self.assertTrue(AE.LAUNCH["claude"]["verified"])
        self.assertFalse(AE.LAUNCH["codex"]["verified"])
        self.assertEqual(set(AE.LAUNCH), {"claude", "codex"})
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            se.build_parser().parse_args(["agent", "run", self.agent, "--baseline", "none"])


class TestLiveRefusals(AgentBase):
    def refused(self, *argv, profile="personal-mac", budget="1"):
        fake = FakeAgentExec()
        args = ["agent", "run", self.agent, "--workspace", self.ws, "--live", "--runs", "1"]
        if profile: args += ["--auth-profile", profile]
        if budget: args += ["--budget-usd", budget]
        with mock.patch.object(se, "live_exec", fake):
            rc, out, err = cli(*(args + list(argv)))
        self.assertEqual(rc, 2, out + err)
        self.assertEqual((fake.calls, fake.grader_calls), ([], []))
        self.assertFalse(os.path.exists(self.ws))
        self.assertFalse(se._LIVE["on"])
        return err

    def test_refusals_before_recording(self):
        self.assertIn(se.NO_BUDGET, self.refused("--engines", "claude", budget=None))
        self.assertIn(se.PROFILE_REQUIRED, self.refused("--engines", "claude", profile=None))
        self.assertIn("refused in API-key mode", self.refused("--engines", "claude", profile="company"))
        self.assertIn("--strict-budget (default)", self.refused("--engines", "codex", "--price", "codex=1,10"))
        self.assertIn("exceeds budget", self.refused("--engines", "claude", budget="0.1"))

    def test_extra_args_may_not_pick_another_agent(self):
        self.assertIn("may not choose an agent", self.refused("--engines", "claude", "--claude-arg=--agent=other"))

    def test_refused_when_a_wrapper_is_stale(self):
        with open(os.path.join(self.agent, "wrappers", "claude", NAME + ".md"), "a", encoding="utf-8") as f:
            f.write("extra\n")
        err = self.refused("--engines", "claude,codex", "--no-strict-budget", "--price", "codex=1,10")
        self.assertIn("%s wrapper claude: out of date (eva.py generate)" % NAME, err)
        self.assertNotIn("no runnable cell", err)

    def plan_cells(self, *extra):
        rc, out, err = cli("agent", "plan", self.agent, "--workspace", self.ws, *extra)
        self.assertEqual(rc, 0, out + err)
        return {tuple(l[len("cell "):].split(": ", 1)[0].split(" ")): l.split(": ", 1)[1]
                for l in out.splitlines() if l.startswith("cell ")}, out

    def test_a_reserved_agent_name_fails_validate_plan_and_run(self):
        for name in ("general-purpose", "explore"):
            with self.subTest(name=name):
                self.agent = generated_copy(os.path.join(self.tmp, "reserved-" + name), name=name)
                rc, out, _ = cli("agent", "validate", self.agent)
                self.assertEqual(rc, 1, out)
                self.assertTrue(any(l.startswith("agent name: fail") for l in out.splitlines()), out)
                cells, _ = self.plan_cells()
                self.assertEqual(cells[("overview", "claude")], "error: reserved agent name")
                self.assertTrue(cells[("overview", "codex")].startswith("planned"), cells)
                self.assertEqual(cells[("drill", "claude")], MULTI)
                self.assertIn("reserved agent name", self.refused("--engines", "claude"))
                self.assertIn("reserved agent name", self.refused("--engines", "codex", "--no-strict-budget",
                                                                  "--price", "codex=1,10"))

    def test_a_same_named_user_scope_agent_refuses_the_run(self):
        cfg = os.path.join(self.tmp, "claude-config")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            self.assertEqual(self.plan_cells()[0][("overview", "claude")].split(" ")[0], "planned")
            planted = os.path.join(cfg, "agents", NAME + ".md")
            os.makedirs(os.path.dirname(planted))
            with open(planted, "w", encoding="utf-8") as f:
                f.write("---\nname: %s\ndescription: another agent\n---\nWrite files freely.\n" % NAME)
            cells, _ = self.plan_cells()
            self.assertTrue(cells[("overview", "claude")].startswith("error: "), cells)
            self.assertIn(planted, cells[("overview", "claude")])
            self.assertTrue(cells[("overview", "codex")].startswith("planned"), cells)
            self.assertIn(planted, self.refused("--engines", "claude"))
            self.assertEqual(read(planted), "---\nname: %s\ndescription: another agent\n---\nWrite files freely.\n" % NAME)
        toml = os.path.join(self.home, ".codex", "agents", NAME + ".toml")
        os.makedirs(os.path.dirname(toml))
        with open(toml, "w", encoding="utf-8") as f:
            f.write('name = "%s"\n' % NAME)
        cells, _ = self.plan_cells()
        self.assertIn(toml, cells[("overview", "codex")])
        self.assertIn(toml, self.refused("--engines", "codex", "--no-strict-budget", "--price", "codex=1,10"))

    def test_duplicate_scenario_ids_fail_validate_plan_and_run(self):
        def edit(d):
            p = os.path.join(d, "scenarios", "02-drill.json")
            doc = rjson(p)
            doc["id"] = "overview"
            wjson(p, doc)
        self.agent = generated_copy(os.path.join(self.tmp, "dup"), edit)
        rc, out, _ = cli("agent", "validate", self.agent)
        self.assertEqual(rc, 1, out)
        line = [l for l in out.splitlines() if l.startswith("scenario ids: ")]
        self.assertEqual(len(line), 1, out)
        self.assertTrue(line[0].startswith("scenario ids: fail"), line)
        self.assertIn("'overview'", line[0])
        rc, out, err = cli("agent", "plan", self.agent, "--workspace", self.ws)
        cells = [l for l in out.splitlines() if l.startswith("cell ")]
        self.assertEqual(cells, ["cell overview %s: error: duplicate scenario id" % t for t in TOOLS] * 2)
        self.assertIn("duplicate scenario id", self.refused("--engines", "claude"))

    def test_an_input_changed_after_signing_is_refused(self):
        old = generated_copy(os.path.join(self.tmp, "old"))
        scenario = os.path.join(old, "scenarios", "01-overview.json")

        def change():
            with open(scenario, "a", encoding="utf-8") as f:
                f.write("\n")
        with self.after_signing(change):
            rc, out, err, fake = self.live("run", "--engines", "claude", "--old-agent", old)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("inputs changed after signing", err)
        self.assertEqual(fake.calls, [])
        self.assertFalse(se._LIVE["on"])

    def test_a_check_that_fails_after_signing_is_refused(self):
        planted = os.path.join(self.home, ".claude", "agents", NAME + ".md")

        def plant():
            os.makedirs(os.path.dirname(planted))
            with open(planted, "w", encoding="utf-8") as f:
                f.write("---\nname: %s\ndescription: another agent\n---\n" % NAME)
        with self.after_signing(plant):
            rc, out, err, fake = self.live("run", "--engines", "claude")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("refused after signing", err)
        self.assertIn(planted, err)
        self.assertEqual(fake.calls, [])
        self.assertFalse(se._LIVE["on"])


class TestLiveRun(AgentBase):
    def test_signed_run_installs_exactly_the_wrapper_and_keeps_the_stream(self):
        rc, out, err, fake = self.live("run", "--engines", "claude")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["files"], {".claude/agents/%s.md" % NAME: self.wrapper()})
        self.assertEqual(call["cmd"][:5], ["claude", "-p", "Give me an overview of this repository", "--agent", NAME])
        rd = self.run_dir()
        self.assertEqual(read(os.path.join(rd, "stream.jsonl"), "rb"), read(CLAUDE_STREAM, "rb"))
        run, errs = eva.load_transcript(os.path.join(rd, "transcript.json"))
        self.assertEqual(errs, [])
        t = rjson(os.path.join(rd, "transcript.json"))
        self.assertEqual((t["tool"], t["agent_loaded"]), ("claude", "verified"))
        self.assertEqual(t["turns"], [{"user": "Give me an overview of this repository", "final": t["final"]}])
        self.assertEqual(rjson(os.path.join(rd, "run.json"))["state"], "run")
        plan = rjson(os.path.join(self.ws, "iteration-1", "plan.json"))
        states = {(c["scenario"], c["tool"]): c["state"] for c in plan["cells"]}
        self.assertEqual(states[("overview", "claude")], "run")
        self.assertEqual(states[("overview", "codex")], "not-run: engine not selected")
        for tool in TOOLS:
            self.assertEqual(states[("drill", tool)], MULTI)
        self.assertFalse(os.listdir(self.runs))

    def test_an_agent_without_pins_or_fixture_keeps_the_n1_launch_and_records(self):
        """Unit K2: no pins and no fixture keep slash commands off, the plan text, plan.json keys and the isolation
        record of the N1 head; the transcript gains the work-folder manifest (the wrapper is not listed)."""
        rc, out, _ = cli("agent", "plan", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 0, out)
        self.assertFalse([l for l in out.splitlines() if l.startswith(("pin ", "fixture ", "agent plan refused"))], out)
        rc, out, err, fake = self.live("run", "--engines", "claude")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("--disable-slash-commands", fake.calls[0]["cmd"])
        plan = rjson(os.path.join(self.ws, "iteration-1", "plan.json"))
        self.assertFalse({"pins", "fixtures"} & set(plan))
        run = rjson(os.path.join(self.run_dir(), "run.json"))
        self.assertNotIn("fixture", run)
        self.assertEqual(set(run["isolation"]), {"sandbox", "installed", "auth", "env_overrides", "env_removed",
                                                 "problems"})
        self.assertEqual(rjson(os.path.join(self.run_dir(), "transcript.json"))["work_manifest"], {})

    def test_a_claude_stream_without_the_agent_is_an_error_and_not_graded(self):
        text = read(CLAUDE_STREAM).replace('"Plan","code-reviewer-v2"]', '"Plan"]')
        rc, out, err, _ = self.live("run", "--engines", "claude", fake=FakeAgentExec(claude=text))
        self.assertEqual(rc, 0, out + err)
        rd = self.run_dir()
        self.assertEqual(rjson(os.path.join(rd, "run.json"))["state"], "error: agent not loaded")
        self.assertFalse(os.path.exists(os.path.join(rd, "transcript.json")))
        self.assertTrue(os.path.exists(os.path.join(rd, "stream.jsonl")))
        rc, out, _ = cli("agent", "grade", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(os.path.join(rd, "grading.json")))
        rc, out, _ = cli("agent", "report", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 3)
        gate = rjson(os.path.join(self.ws, "iteration-1", "gate.json"))
        self.assertIn(T.coverage_missing("claude", se.NOT_GRADED), gate["problems"])

    def test_codex_run_reads_unverified_and_the_report_says_so(self):
        rc, out, err, fake = self.live("run", "--engines", "codex", "--no-strict-budget", "--price", "codex=1,10")
        self.assertEqual(rc, 0, out + err)
        call = fake.calls[0]
        self.assertEqual(call["cmd"][:3], ["codex", "exec", "--json"])
        self.assertIn(NAME, call["cmd"][-1])
        self.assertIn("Give me an overview of this repository", call["cmd"][-1])
        self.assertEqual(list(call["files"]), [".codex/agents/%s.toml" % NAME])
        self.assertEqual(call["files"][".codex/agents/%s.toml" % NAME], self.wrapper(tool="codex"))
        t = rjson(os.path.join(self.run_dir(tool="codex"), "transcript.json"))
        self.assertEqual(t["agent_loaded"], "unverified")
        self.assertEqual(cli("agent", "grade", self.agent, "--workspace", self.ws)[0], 0)
        rc, out, _ = cli("agent", "report", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 3)
        gate = rjson(os.path.join(self.ws, "iteration-1", "gate.json"))
        self.assertTrue(any(p.startswith("agent-load-unverified:codex") for p in gate["problems"]), gate)
        self.assertIn("agent-load-unverified:codex", out)

    def test_grade_rows_equal_eva_check_transcript(self):
        self.assertEqual(self.live("run", "--engines", "claude")[0], 0)
        rc, out, err = cli("agent", "grade", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 0, out + err)
        rd = self.run_dir()
        tp = os.path.join(rd, "transcript.json")
        g = rjson(os.path.join(rd, "grading.json"))
        self.assertEqual(g["rows"], eva.grade_transcript(self.agent, tp).checks)
        self.assertEqual((g["grader_engine"], g["grader_model"], g["grader_effort"]), ("none", None, None))
        self.assertEqual(g["transcript_sha256"], hashlib.sha256(read(tp, "rb")).hexdigest())
        self.assertEqual((g["scenario"], g["tool"], g["configuration"], g["run"]), ("overview", "claude", "new_agent", 1))
        llm = [r for r in g["rows"] if r["status"] == "needs-review"]
        self.assertEqual([r["id"] for r in llm], ["criteria-needs-review"])
        self.assertTrue(all(r["status"] == "pass" for r in g["rows"] if r["status"] != "needs-review"), g["rows"])

    def test_live_grade_records_the_grader(self):
        self.assertEqual(self.live("run", "--engines", "claude")[0], 0)
        for extra, effort in (((), "cli-default"), (("--grader-effort", "low"), "low")):
            rc, out, err, fake = self.live("grade", *extra)
            self.assertEqual(rc, 0, out + err)
            self.assertEqual(len(fake.grader_calls), 1)
            g = rjson(os.path.join(self.run_dir(), "grading.json"))
            self.assertEqual((g["grader_engine"], g["grader_model"], g["grader_effort"]),
                             ("claude", se.DEFAULT_GRADER_MODEL, effort))
            row = [r for r in g["rows"] if r["id"] == "criteria-needs-review"][0]
            self.assertEqual((row["status"], row["passed"]), ("pass", True))
            typed = eva.grade_transcript(self.agent, os.path.join(self.run_dir(), "transcript.json")).checks
            self.assertEqual([r for r in g["rows"] if r["id"] != "criteria-needs-review"],
                             [r for r in typed if r["id"] != "criteria-needs-review"])

    def test_live_grade_needs_budget_and_profile(self):
        self.assertEqual(self.live("run", "--engines", "claude")[0], 0)
        with mock.patch.object(se, "live_exec", FakeAgentExec()) as fake:
            rc, _, err = cli("agent", "grade", self.agent, "--workspace", self.ws, "--live")
            self.assertEqual(rc, 2)
            self.assertIn(se.NO_BUDGET, err)
            rc, _, err = cli("agent", "grade", self.agent, "--workspace", self.ws, "--live", "--budget-usd", "1")
            self.assertEqual(rc, 2)
            self.assertIn(se.PROFILE_REQUIRED, err)
        self.assertEqual(fake.grader_calls, [])

    def test_report_coverage_record(self):
        self.assertEqual(self.live("run", "--engines", "claude")[0], 0)
        wjson(os.path.join(self.ws, "approvals.json"), {"coverage_waiver:opencode": {"sha256": "0" * 64}})
        rc, out, _ = cli("agent", "report", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 3)
        gate = rjson(os.path.join(self.ws, "iteration-1", "gate.json"))
        self.assertIn(T.coverage_missing("claude", se.PENDING), gate["problems"])
        self.assertEqual(cli("agent", "grade", self.agent, "--workspace", self.ws)[0], 0)
        rc, out, _ = cli("agent", "report", self.agent, "--workspace", self.ws)
        self.assertEqual(rc, 3)
        it = os.path.join(self.ws, "iteration-1")
        bench, gate = rjson(os.path.join(it, "benchmark.json")), rjson(os.path.join(it, "gate.json"))
        self.assertEqual(bench["tool_coverage"]["claude"]["status"], "run")
        self.assertEqual(bench["tool_coverage"]["claude"]["graded_runs"], 1)
        for tool, status, waiver in (("codex", se.NOT_SELECTED, "missing"), ("opencode", NOT_AVAILABLE, "unsigned"),
                                     ("hermes", NOT_AVAILABLE, "missing")):
            self.assertIn(T.coverage_missing(tool, status, waiver), gate["problems"])
        self.assertFalse(any(p.startswith("coverage-missing:claude") for p in gate["problems"]))
        self.assertTrue(os.path.isfile(os.path.join(it, "comparison.md")))
        self.assertIn("coverage-missing:hermes", out)

    def test_a_grading_of_other_transcript_bytes_does_not_count(self):
        self.assertEqual(self.live("run", "--engines", "claude")[0], 0)
        self.assertEqual(cli("agent", "grade", self.agent, "--workspace", self.ws)[0], 0)
        tp = os.path.join(self.run_dir(), "transcript.json")
        t = rjson(tp)
        t["final"] += " README.md:4"
        wjson(tp, t)
        self.assertEqual(cli("agent", "report", self.agent, "--workspace", self.ws)[0], 3)
        gate = rjson(os.path.join(self.ws, "iteration-1", "gate.json"))
        self.assertIn(T.coverage_missing("claude", se.PENDING), gate["problems"])

    def test_previous_version_baseline(self):
        def edit(d):
            p = os.path.join(d, "AGENT.md")
            text = read(p).replace("Use when the user asks for a code review.", "Use when the user asks for a review.")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        old = generated_copy(os.path.join(self.tmp, "old"), edit)
        rc, out, err, fake = self.live("run", "--engines", "claude", "--old-agent", old)
        self.assertEqual(rc, 0, out + err)
        wrappers = [c["files"][".claude/agents/%s.md" % NAME] for c in fake.calls]
        self.assertEqual(sorted(wrappers), sorted([self.wrapper(), self.wrapper(old)]))
        self.assertNotEqual(self.wrapper(), self.wrapper(old))
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir(config="old_agent"), "transcript.json")))
        self.assertEqual(cli("agent", "grade", self.agent, "--workspace", self.ws)[0], 0)
        cli("agent", "report", self.agent, "--workspace", self.ws)
        it = os.path.join(self.ws, "iteration-1")
        bench = rjson(os.path.join(it, "benchmark.json"))
        self.assertEqual([(b["scenario"], b["tool"]) for b in bench["baseline"]], [("overview", "claude")])
        b = bench["baseline"][0]
        self.assertEqual(b["new_agent"], b["old_agent"])
        self.assertEqual(b["delta"], 0.0)
        self.assertIn("previous version", read(os.path.join(it, "comparison.md")))

    def test_live_grade_sends_only_the_signed_runs_to_the_grader(self):
        rc, out, err, _ = self.live("run", "--engines", "claude", "--runs", "2", fake=NotLoadedExec(calls=(2,)))
        self.assertEqual(rc, 0, out + err)
        r1, r2 = self.run_dir(k=1), self.run_dir(k=2)
        self.assertEqual(rjson(os.path.join(r2, "run.json"))["state"], "error: agent not loaded")

        def flip():
            shutil.copyfile(os.path.join(r1, "transcript.json"), os.path.join(r2, "transcript.json"))
            rec = rjson(os.path.join(r2, "run.json"))
            rec["state"] = "run"
            wjson(os.path.join(r2, "run.json"), rec)
        with self.after_signing(flip):
            rc, out, err, fake = self.live("grade")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(fake.grader_calls), 1)
        self.assertEqual(rjson(os.path.join(r1, "grading.json"))["grader_engine"], "claude")
        g2 = os.path.join(r2, "grading.json")
        if os.path.exists(g2):
            g = rjson(g2)
            self.assertEqual((g["grader_engine"], g["grader_model"], g["grader_effort"]), ("none", None, None))
            row = [r for r in g["rows"] if r["id"] == "criteria-needs-review"][0]
            self.assertEqual((row["status"], row["passed"]), ("needs-review", None))

    def test_previous_version_runs_do_not_count_as_coverage(self):
        def edit(d):
            p = os.path.join(d, "AGENT.md")
            text = read(p).replace("Use when the user asks for a code review.", "Use when the user asks for a review.")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        old = generated_copy(os.path.join(self.tmp, "old"), edit)
        fake = NotLoadedExec(wrapper=self.wrapper())
        rc, out, err, _ = self.live("run", "--engines", "claude", "--old-agent", old, fake=fake)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(rjson(os.path.join(self.run_dir(), "run.json"))["state"], "error: agent not loaded")
        self.assertEqual(rjson(os.path.join(self.run_dir(config="old_agent"), "run.json"))["state"], "run")
        self.assertEqual(cli("agent", "grade", self.agent, "--workspace", self.ws)[0], 0)
        self.assertEqual(cli("agent", "report", self.agent, "--workspace", self.ws)[0], 3)
        it = os.path.join(self.ws, "iteration-1")
        bench, gate = rjson(os.path.join(it, "benchmark.json")), rjson(os.path.join(it, "gate.json"))
        self.assertEqual((bench["tool_coverage"]["claude"]["status"], bench["tool_coverage"]["claude"]["graded_runs"]),
                         (se.NOT_GRADED, 0))
        self.assertIn(T.coverage_missing("claude", se.NOT_GRADED), gate["problems"])
        self.assertEqual(gate["coverage"]["claude"], "coverage-missing")
        b = bench["baseline"]
        self.assertEqual([(x["scenario"], x["tool"], x["new_agent"]) for x in b], [("overview", "claude", None)])
        self.assertIsNotNone(b[0]["old_agent"])

    def test_a_stream_that_reports_is_error_is_an_error_cell(self):
        claude = read(CLAUDE_STREAM).replace('"is_error":false', '"is_error":true')
        codex = read(CODEX_STREAM).replace('{"type":"turn.completed"',
                                           '{"type":"error","message":"stream disconnected"}\n{"type":"turn.completed"')
        self.assertNotEqual(codex, read(CODEX_STREAM))
        rc, out, err, _ = self.live("run", "--engines", "claude,codex", "--no-strict-budget", "--price", "codex=1,10",
                                    fake=FakeAgentExec(claude=claude, codex=codex))
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(cli("agent", "grade", self.agent, "--workspace", self.ws)[0], 0)
        self.assertEqual(cli("agent", "report", self.agent, "--workspace", self.ws)[0], 3)
        gate = rjson(os.path.join(self.ws, "iteration-1", "gate.json"))
        for tool in ("claude", "codex"):
            rd = self.run_dir(tool=tool)
            state = rjson(os.path.join(rd, "run.json"))["state"]
            self.assertTrue(state.startswith("error: "), (tool, state))
            self.assertFalse(os.path.exists(os.path.join(rd, "grading.json")), tool)
            self.assertIn("run-error:overview/%s/new_agent/run-1: %s" % (tool, state), gate["problems"])
            self.assertIn(T.coverage_missing(tool, se.NOT_GRADED), gate["problems"])
        agent = se.Agent(self.agent)
        scenario = [s for s in agent.scenarios if s["id"] == "overview"][0]
        for tool, stream in (("claude", claude), ("codex", codex)):
            rd = os.path.join(self.tmp, "unflagged", tool)
            eng = mock.Mock(model=None, effort=None)
            eng.name = tool
            res = {"stream": stream, "workdir": "/sandbox/work", "input_tokens": 1, "output_tokens": 1}
            self.assertEqual(se.record_agent_run(rd, res, eng, scenario, "new_agent", 1, agent, (0.0, "test")),
                             "error: the stream reports is_error", tool)

    def test_an_llm_row_without_a_grader_verdict_stays_needs_review(self):
        self.assertEqual(self.live("run", "--engines", "claude")[0], 0)
        rc, out, err, fake = self.live("grade", fake=FakeAgentExec(answer="maybe"))
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(fake.grader_calls), 1)
        g = rjson(os.path.join(self.run_dir(), "grading.json"))
        row = [r for r in g["rows"] if r["id"] == "criteria-needs-review"][0]
        self.assertEqual((row["status"], row["passed"]), ("needs-review", None))
        self.assertEqual(g["needs_review"], 1)
        self.assertNotIn("criteria-needs-review", g["failed"])
        self.assertEqual(cli("agent", "report", self.agent, "--workspace", self.ws)[0], 3)
        gate = rjson(os.path.join(self.ws, "iteration-1", "gate.json"))
        self.assertTrue(any(p.startswith("criteria-ungraded:overview/claude/new_agent/run-1") for p in gate["problems"]))

    def test_multi_turn_scenarios_never_run(self):
        rc, out, err, fake = self.live("run", "--engines", "claude,codex", "--no-strict-budget", "--price",
                                       "codex=1,10")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(fake.calls), 2)
        self.assertTrue(all("Tell me the overview" not in " ".join(c["cmd"]) for c in fake.calls))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "iteration-1", "claude", "drill")))


if __name__ == "__main__":
    unittest.main()
