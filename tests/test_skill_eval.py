"""skill_eval.py tests. Every engine and grader here is mocked: subprocess is patched to fail for the whole module,
so no claude/codex CLI and no model is ever called."""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva, skill_eval as se

_patches = []

def _forbidden(*a, **k):
    raise AssertionError("subprocess must not be called in skill_eval tests: %r" % (a[:1],))

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, _forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False)

def rjson(path):
    with open(path, encoding="utf-8") as f: return json.load(f)

SKILL_MD = ("---\nname: branch-setup\ndescription: Creates a git worktree and branch for new work. Use when the user "
            "starts a new task and wants a fresh branch.\n---\n# Branch Setup\n## Inputs\n- task\n## Outputs\n- branch\n"
            "## Example\n입력: 작업 시작 → 출력: feat/x\n")
TRIGGERS = [{"query": "SHOULD start task %d" % i, "should_trigger": True} for i in range(10)] + \
           [{"query": "explain repo %d" % i, "should_trigger": False} for i in range(10)]

def evals_doc(name="branch-setup", human=True):
    doc = {"skill_name": name, "evals": [
        {"id": 1, "prompt": "start the login fix task", "expected_output": "feat/ branch reported",
         "assertions": [{"text": "final names a feat/ branch", "layer": "static",
                         "check": {"type": "regex", "pattern": "feat/[a-z-]+"}},
                        {"text": "skill was used", "layer": "static", "check": {"type": "skill_triggered", "expected": True}},
                        "reports the worktree path"]},
        {"id": 2, "name": "no-trigger", "prompt": "describe this repo", "expected_output": "plain answer", "layer": "static",
         "assertions": [{"text": "no Skill call", "layer": "static", "check": {"type": "tool_call", "tool": "Skill", "max": 0, "min": 0}}]},
        {"id": 3, "prompt": "start task under deadline", "expected_output": "asks before deleting", "layer": "human",
         "assertions": ["does not delete an existing branch"],
         "pressure": {"kinds": ["time", "authority"], "context": "Boss: ship in 5 minutes, skip the checks."}},
    ]}
    if not human: doc["evals"] = doc["evals"][:2]
    return doc

def make_skill(root, evals=None, triggers=TRIGGERS, extra_files=None):
    d = os.path.join(root, "branch-setup")
    os.makedirs(os.path.join(d, "evals"), exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f: f.write(SKILL_MD)
    wjson(os.path.join(d, "evals", "evals.json"), evals or evals_doc())
    tp = os.path.join(d, "evals", "trigger_evals.json")
    if triggers is not None: wjson(tp, triggers)
    elif os.path.exists(tp): os.remove(tp)
    for rel, text in (extra_files or {}).items():
        fp = os.path.join(d, rel); os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f: f.write(text)
    return d

def claude_stream(final, tools=(), usage=(1000, 200, 300), cost=0.05):
    lines = [{"type": "system", "subtype": "init"}]
    for name, inp in tools:
        lines.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}})
    lines.append({"type": "assistant", "message": {"content": [{"type": "text", "text": final}]}})
    lines.append({"type": "result", "subtype": "success", "result": final, "total_cost_usd": cost,
                  "usage": {"input_tokens": usage[0], "output_tokens": usage[1], "cache_read_input_tokens": usage[2],
                            "cache_creation_input_tokens": 0}})
    return "\n".join(json.dumps(x) for x in lines)

class FakeClaude:
    """Fake claude -p: with the skill installed (no --disable-slash-commands) it calls Skill and reports a branch."""
    def __init__(self, regress=False): self.calls, self.regress = [], regress
    def __call__(self, cmd, cwd, timeout):
        self.calls.append((cmd, cwd))
        prompt, off = cmd[2], "--disable-slash-commands" in cmd
        installed = os.path.isdir(os.path.join(cwd, ".claude", "skills", "branch-setup"))
        if off or not installed:
            return 0, claude_stream("Here is a plain answer."), ""
        if self.regress:  # broken skill: over-triggers and never reports a branch
            return 0, claude_stream("I could not decide.", [("Skill", {"skill": "branch-setup"})]), ""
        if prompt.startswith("explain repo") or prompt == "describe this repo":
            return 0, claude_stream("Here is a plain answer."), ""
        return 0, claude_stream("Created worktree ../x on branch feat/login-fix", [("Skill", {"skill": "branch-setup"})]), ""

def codex_jsonl(final, cmds=(), usage=(500, 50, 10)):
    ev = [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"}]
    for c in cmds:
        ev.append({"type": "item.completed", "item": {"id": "i", "type": "command_execution", "command": c}})
    ev.append({"type": "item.completed", "item": {"id": "j", "type": "agent_message", "text": final}})
    ev.append({"type": "turn.completed", "usage": {"input_tokens": usage[0], "cached_input_tokens": 100,
                                                   "output_tokens": usage[1], "reasoning_output_tokens": usage[2]}})
    return "\n".join(json.dumps(x) for x in ev)

class FakeCodex:
    def __init__(self): self.calls = []
    def __call__(self, cmd, cwd, timeout):
        self.calls.append((cmd, cwd))
        prompt = cmd[-1]
        installed = os.path.isdir(os.path.join(cwd, ".agents", "skills", "branch-setup"))
        if installed and not (prompt.startswith("explain repo") or prompt == "describe this repo"):
            return 0, codex_jsonl("branch feat/login-fix ready", ["cat .agents/skills/branch-setup/SKILL.md"]), ""
        return 0, codex_jsonl("plain answer"), ""

class FakeGraderExec:
    """Fake `claude -p --json-schema` grader: yes when the run's final message names a feat/ branch."""
    def __init__(self, compare_pick=None, cost=0.001, report_cost=True):
        self.calls, self.compare_pick, self.cost, self.report_cost = [], compare_pick, cost, report_cost
    def __call__(self, cmd, cwd, timeout):
        self.calls.append(cmd)
        prompt, schema = cmd[2], json.loads(cmd[cmd.index("--json-schema") + 1])
        if "winner" in schema["properties"]:
            a = prompt.split("=== Run A ===")[1].split("=== Run B ===")[0]
            so = {"winner": self.compare_pick or ("A" if "feat/" in a else "B"), "reasoning": "branch reported"}
        else:
            final = prompt.split("Final message:\n")[1].split("\n\nTool calls")[0]
            ids = [l.split("]")[0][3:] for l in prompt.split("Rubric items:\n")[1].splitlines() if l.startswith("- [")]
            so = {"items": [{"id": i, "answer": "yes" if "feat/" in final else "no", "evidence": final[:40]} for i in ids]}
        out = {"type": "result", "result": "", "structured_output": so, "usage": {"input_tokens": 100, "output_tokens": 10}}
        if self.report_cost: out["total_cost_usd"] = self.cost
        return 0, json.dumps(out), ""

def quiet(*a, **k): pass

def read(path):
    with open(path, encoding="utf-8") as f: return f.read()

def write(path, text):
    with open(path, "w", encoding="utf-8") as f: f.write(text)

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.ws = os.path.join(self.tmp, "ws")
        self.home = os.path.join(self.tmp, "home"); os.makedirs(self.home)
        env = mock.patch.dict(os.environ, {"HOME": self.home, "CODEX_HOME": os.path.join(self.home, ".codex")})
        env.start(); self.addCleanup(env.stop)  # preflight looks only at a fake home
    def tearDown(self): shutil.rmtree(self.tmp)
    def spec(self, **kw):
        spec, problems = se.load_eval_set(make_skill(self.tmp, **kw))
        return spec, problems
    def engines(self, regress=False):
        self.fc, self.fx = FakeClaude(regress), FakeCodex()
        return [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=self.fc), se.CodexEngine(exec_fn=self.fx)]
    def grader(self, **kw):
        self.fg = FakeGraderExec(**kw)
        return se.LLMGrader("claude-sonnet-5-5", exec_fn=self.fg)

class TestNoLiveCalls(Base):
    def test_live_exec_refuses_without_live_mode(self):
        with self.assertRaises(se.LiveDisabled): se.live_exec(["claude", "-p", "x"], self.tmp, 1)
        with self.assertRaises(se.LiveDisabled): se.ClaudeCodeEngine("m").run("x", self.tmp, True)
        with self.assertRaises(se.LiveDisabled): se.LLMGrader().grade({"prompt": "p", "expected_output": "e"}, {}, [{"id": "1", "text": "t"}])
    def test_subprocess_is_patched(self):
        with self.assertRaises(AssertionError): subprocess.run(["true"])

class TestEvalSet(Base):
    def test_valid_set(self):
        spec, problems = self.spec()
        self.assertEqual(problems, [])
        self.assertEqual([c["id"] for c in spec["cases"]], [1, 2, 3])
        self.assertEqual([i["layer"] for i in spec["cases"][0]["items"]], ["static", "static", "llm"])
        self.assertEqual(spec["cases"][2]["items"][0]["layer"], "human")
        self.assertEqual(spec["cases"][1]["name"], "no-trigger")
        self.assertTrue(se.full_prompt(spec["cases"][2]).startswith("Boss: ship in 5 minutes"))
        self.assertEqual(len(spec["triggers"]), 20)
    def test_static_string_needs_check(self):
        ev = evals_doc(); ev["evals"][1]["assertions"] = ["Skill 호출이 0회다"]
        _, problems = self.spec(evals=ev)
        self.assertTrue(any("static layer needs a machine check" in p for p in problems), problems)
    def test_bad_checks_and_pressure(self):
        ev = evals_doc()
        ev["evals"][0]["assertions"] = [{"text": "a", "layer": "static", "check": {"type": "nope"}},
                                        {"text": "b", "layer": "static", "check": {"type": "regex", "pattern": "("}},
                                        {"text": "c", "layer": "static", "check": {"type": "file_exists", "path": "../x"}},
                                        {"text": "d", "layer": "static", "check": {"type": "tool_call", "tool": "Bash", "min": -1}}]
        ev["evals"][2]["pressure"] = {"kinds": []}
        _, problems = self.spec(evals=ev)
        joined = "\n".join(problems)
        for frag in ("check.type", "check.pattern", "check.path", "check.min", "pressure"):
            self.assertIn(frag, joined)
    def test_trigger_counts(self):
        _, problems = self.spec(triggers=TRIGGERS[:9] + TRIGGERS[10:])
        self.assertTrue(any("need 10 should-trigger + 10 should-not, got 9 + 10" in p for p in problems), problems)
        _, problems = self.spec(triggers=None)
        self.assertTrue(any("missing evals/trigger_evals.json" in p for p in problems))
        _, problems = self.spec(triggers=[{"query": "x"}])
        self.assertTrue(any("should_trigger" in p for p in problems))
    def test_eva_check_accepts_layered_objects(self):
        d = make_skill(self.tmp)
        n, err = eva.evals_errors(d, "branch-setup")
        self.assertEqual((n, err), (3, []))
        ev = evals_doc(); ev["evals"][0]["assertions"] = [{"text": "x", "layer": "LLM"}]
        wjson(os.path.join(d, "evals", "evals.json"), ev)
        self.assertTrue(any("assertions" in e for e in eva.evals_errors(d, "branch-setup")[1]))

class TestEngines(unittest.TestCase):
    def test_claude_command_and_parse(self):
        e = se.ClaudeCodeEngine("claude-opus-5-5", extra_args=["--permission-mode", "acceptEdits"])
        on, off = e.command("hi", True, 1.5), e.command("hi", False)
        self.assertEqual(on[:3], ["claude", "-p", "hi"])
        self.assertIn("stream-json", on); self.assertIn("--no-session-persistence", on)
        self.assertEqual(on[on.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(on[on.index("--max-budget-usd") + 1], "1.50")
        self.assertNotIn("--disable-slash-commands", on); self.assertIn("--disable-slash-commands", off)
        self.assertEqual(on[-2:], ["--permission-mode", "acceptEdits"])
        r = e.parse(claude_stream("done feat/x", [("Skill", {"skill": "plugin:branch-setup"})], usage=(10, 5, 100), cost=0.2))
        self.assertEqual(r["final"], "done feat/x")
        self.assertEqual((r["input_tokens"], r["cached_input_tokens"], r["output_tokens"], r["cost_usd"]), (110, 100, 5, 0.2))
        self.assertTrue(e.triggered(r, "branch-setup"))
        self.assertFalse(e.triggered(r, "other"))
        read = e.parse(claude_stream("x", [("Read", {"file_path": "/w/.claude/skills/branch-setup/SKILL.md"})]))
        self.assertTrue(e.triggered(read, "branch-setup"))
        self.assertEqual(e.parse("garbage\n{\"type\":\"result\",\"is_error\":true,\"subtype\":\"error_max_turns\"}")["error"], "error_max_turns")
    def test_codex_command_and_parse(self):
        e = se.CodexEngine()
        cmd = e.command("do it", True)
        self.assertEqual(cmd[:3], ["codex", "exec", "--json"]); self.assertEqual(cmd[-1], "do it"); self.assertNotIn("-m", cmd)
        self.assertEqual(se.CodexEngine("gpt-x").command("p", True)[-3:], ["-m", "gpt-x", "p"])
        r = e.parse(codex_jsonl("ok", ["cat .agents/skills/branch-setup/SKILL.md"]) + "\n" + codex_jsonl("ok2", usage=(1, 1, 1)))
        self.assertEqual(r["final"], "ok2")
        self.assertEqual((r["input_tokens"], r["output_tokens"], r["cached_input_tokens"]), (501, 62, 200))
        self.assertTrue(e.triggered(r, "branch-setup"))
        self.assertIn("boom", e.parse(json.dumps({"type": "turn.failed", "error": {"message": "boom"}}))["error"])

class TestEstimate(unittest.TestCase):
    def test_pilot_counts(self):
        est = se.estimate(3, 1, 20, 3, [("claude", "claude-opus-5-5"), ("codex", None)], "none", "claude-sonnet-5-5")
        calls = {(r["engine"], r["kind"]): r["calls"] for r in est["rows"]}
        self.assertEqual(calls[("claude", "exec")], 18)      # 3 cases x 2 configs x 3 runs
        self.assertEqual(calls[("claude", "trigger")], 60)   # 20 queries x 3 runs x with_skill only
        self.assertEqual(calls[("claude", "grader")], 6)     # 1 llm case x 2 configs x 3 runs
        self.assertEqual(calls[("claude", "pairwise")], 9)   # 3 cases x 3 runs
        self.assertEqual(est["calls"], 2 * (18 + 60 + 6 + 9))
        self.assertEqual(est["unpriced_models"], ["codex CLI default"])
        opus_exec = next(r for r in est["rows"] if r["engine"] == "claude" and r["kind"] == "exec")
        self.assertAlmostEqual(opus_exec["usd"], 18 * (60000 * 4 + 4000 * 20) / 1e6)
        priced = se.estimate(3, 1, 20, 3, [("codex", None)], "none", "claude-sonnet-5-5", prices={"codex": (5.0, 30.0)})
        self.assertEqual(priced["unpriced_models"], [])
        old = se.estimate(3, 1, 20, 3, [("claude", "claude-opus-5-5")], "old", "claude-sonnet-5-5")
        self.assertEqual(next(r["calls"] for r in old["rows"] if r["kind"] == "trigger"), 120)
        self.assertIn("UNPRICED", se.format_estimate(est))
    def test_overrides(self):
        self.assertEqual(se.parse_price_overrides(["codex=5,30"]), {"codex": (5.0, 30.0)})
        self.assertEqual(se.parse_token_overrides(["exec=1,2"])["exec"], (1, 2))
        with self.assertRaises(se.EvalError): se.parse_price_overrides(["codex=5"])
        for bad in ("codex=0,0", "codex=-1,5", "codex=nan,1", "codex=inf,1", "=1,1"):
            with self.assertRaises(se.EvalError, msg=bad): se.parse_price_overrides([bad])
        for bad in ("exec=0,1", "exec=1,0", "grader=-5,10"):
            with self.assertRaises(se.EvalError, msg=bad): se.parse_token_overrides([bad])
        with self.assertRaises(se.EvalError): se.parse_token_overrides(["bogus=1,2"])

class TestRunAndGrade(Base):
    def run_one(self, runs=3, regress=False, **kw):
        spec, problems = self.spec(**kw)
        self.assertEqual(problems, [])
        n = se.run_iteration(spec, self.ws, self.engines(regress), runs=runs, log=quiet)
        return spec, n

    def test_layout_and_timing(self):
        spec, n = self.run_one(runs=2)
        it = os.path.join(self.ws, "iteration-1")
        rd = os.path.join(it, "claude", "eval-1-start-the-login-fix-task", "with_skill", "run-2")
        t = rjson(os.path.join(rd, "timing.json"))
        self.assertEqual(t["total_tokens"], 1500); self.assertEqual(t["cost_usd"], 0.05)
        self.assertIn("duration_ms", t); self.assertIn("total_duration_seconds", t)
        self.assertTrue(os.path.isdir(os.path.join(rd, "workdir", ".claude", "skills", "branch-setup")))
        self.assertFalse(os.path.exists(os.path.join(rd, "workdir", ".claude", "skills", "branch-setup", "evals")))
        off = os.path.join(it, "claude", "eval-1-start-the-login-fix-task", "without_skill", "run-1", "workdir")
        self.assertFalse(os.path.exists(os.path.join(off, ".claude")))
        self.assertTrue(rjson(os.path.join(rd, "outputs", "transcript.json"))["triggered"])
        self.assertEqual(rjson(os.path.join(it, "claude", "eval-1-start-the-login-fix-task", "eval_metadata.json"))["eval_id"], 1)
        self.assertTrue(os.path.isdir(os.path.join(it, "codex", "eval-2-no-trigger", "without_skill", "run-2")))
        tr = rjson(os.path.join(it, "claude", "trigger", "with_skill", "results.json"))
        self.assertEqual(tr["summary"], {"total": 20, "passed": 20, "failed": 0})
        self.assertEqual(tr["results"][0]["runs"], 2)
        self.assertFalse(os.path.exists(os.path.join(it, "claude", "trigger", "without_skill")))
        # 3 cases x 2 configs x 2 runs + 20 queries x 2 runs, per engine
        self.assertEqual(len(self.fc.calls), 12 + 40); self.assertEqual(len(self.fx.calls), 12 + 40)
        self.assertEqual(rjson(os.path.join(it, "plan.json"))["runs_per_configuration"], 2)

    def test_dry_grade_static_llm_human(self):
        spec, n = self.run_one(runs=1)
        se.grade_iteration(spec, self.ws, n, None, log=quiet)
        it = os.path.join(self.ws, "iteration-1")
        g = rjson(os.path.join(it, "claude", "eval-1-start-the-login-fix-task", "with_skill", "run-1", "grading.json"))
        by = {e["id"]: e for e in g["expectations"]}
        self.assertTrue(by["1.0"]["passed"]); self.assertTrue(by["1.1"]["passed"])
        self.assertIsNone(by["1.2"]["passed"]); self.assertEqual(by["1.2"]["status"], "not-graded")
        self.assertEqual(g["summary"], {"passed": 2, "failed": 0, "total": 2, "pass_rate": 1.0, "pending": 1})
        for k in ("text", "passed", "evidence"): self.assertIn(k, by["1.0"])  # skill-creator viewer fields
        gb = rjson(os.path.join(it, "claude", "eval-1-start-the-login-fix-task", "without_skill", "run-1", "grading.json"))
        self.assertEqual(gb["summary"]["passed"], 0)
        hr = rjson(os.path.join(it, "human_review.json"))
        self.assertEqual(len(hr["items"]), 4)  # case 3 x 2 configs x 1 run x 2 engines
        hr["items"][0]["passed"] = False; hr["items"][0]["note"] = "deleted the branch"
        wjson(os.path.join(it, "human_review.json"), hr)
        se.grade_iteration(spec, self.ws, n, None, log=quiet)
        hr2 = rjson(os.path.join(it, "human_review.json"))
        self.assertIs(hr2["items"][0]["passed"], False)
        g3 = rjson(os.path.join(self.ws, hr2["items"][0]["run_dir"], "grading.json"))
        self.assertEqual(g3["expectations"][0]["evidence"], "deleted the branch")
        self.assertEqual(g3["summary"]["total"], 1)

    def test_llm_grader_and_pairwise(self):
        spec, n = self.run_one(runs=3)
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=7, log=quiet)
        it = os.path.join(self.ws, "iteration-1")
        ed = os.path.join(it, "claude", "eval-1-start-the-login-fix-task")
        g = rjson(os.path.join(ed, "with_skill", "run-1", "grading.json"))
        self.assertTrue(g["expectations"][2]["passed"]); self.assertEqual(g["expectations"][2]["grader"], "claude-sonnet-5-5")
        comps = [rjson(os.path.join(ed, "comparison-%d.json" % k)) for k in (1, 2, 3)]
        self.assertTrue(all(c["winner_configuration"] == "with_skill" for c in comps))
        self.assertTrue(all(c["seed"] == 7 for c in comps))
        orders = {c["mapping"]["A"] for c in comps}
        ed2 = os.path.join(it, "claude", "eval-2-no-trigger")
        orders |= {rjson(os.path.join(ed2, "comparison-%d.json" % k))["mapping"]["A"] for k in (1, 2, 3)}
        self.assertEqual(orders, {"with_skill", "without_skill"})  # order is randomized
        prompts = [c[2] for c in self.fg.calls if "Run A" in c[2]]
        self.assertTrue(all("with_skill" not in p and "without_skill" not in p for p in prompts))  # blind
        self.assertTrue(all("--tools" in c and c[c.index("--tools") + 1] == "" for c in self.fg.calls))

    def test_grader_respects_budget(self):
        spec, n = self.run_one(runs=1)
        fg = FakeGraderExec(cost=0.01)
        g = se.LLMGrader("claude-sonnet-5-5", exec_fn=fg, budget=se.Budget(0.045))
        with self.assertRaises(se.EvalError) as cm:
            se.grade_iteration(spec, self.ws, n, g, seed=1, log=quiet)
        self.assertIn("budget exhausted", str(cm.exception))
        # caps = floor(remaining x 0.5): 0.02, 0.01, 0.01, then 0.0075 -> 0.00 refused
        self.assertEqual(g.usage["calls"], 3)
        self.assertEqual([c[c.index("--max-budget-usd") + 1] for c in fg.calls], ["0.02", "0.01", "0.01"])
        self.assertAlmostEqual(g.budget.spent, 0.03)

    def test_pairwise_seed_is_reproducible(self):
        spec, n = self.run_one(runs=3)
        it = os.path.join(self.ws, "iteration-1")
        def maps():
            se.pairwise(spec, it, self.grader(), seed=123)
            return [rjson(os.path.join(it, "claude", "eval-1-start-the-login-fix-task", "comparison-%d.json" % k))["mapping"]["A"]
                    for k in (1, 2, 3)]
        self.assertEqual(maps(), maps())

    def test_grader_must_differ_from_executor(self):
        spec, n = self.run_one(runs=1)
        with self.assertRaises(se.EvalError):
            se.grade_iteration(spec, self.ws, n, se.LLMGrader("claude-opus-5-5", exec_fn=FakeGraderExec()), log=quiet)
        n2 = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine(None, exec_fn=FakeClaude())], runs=1, log=quiet)
        with self.assertRaises(se.EvalError) as cm:
            se.grade_iteration(spec, self.ws, n2, self.grader(), log=quiet)
        self.assertIn("plan.json", str(cm.exception))

    def test_benchmark_report_gate_clean(self):
        spec, n = self.run_one(runs=3)
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=1, log=quiet)
        _, gate = se.report_iteration(spec, self.ws, n)
        self.assertEqual(gate["problems"], ["ungraded: 12 human item(s) pending review (fill human_review.json, re-run grade)"])
        self.assertTrue(gate["human_pending_only"])
        hp = os.path.join(self.ws, "iteration-1", "human_review.json")
        hr = rjson(hp)
        for x in hr["items"]: x["passed"] = True
        wjson(hp, hr)
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=1, log=quiet)
        bench, gate = se.report_iteration(spec, self.ws, n)
        self.assertEqual(bench["completeness"]["claude"], {"expected_runs": 18, "graded_runs": 18, "expected_trigger_runs": 60,
                                                           "trigger_runs": 60, "trigger_errors": 0})
        it = os.path.join(self.ws, "iteration-1")
        b = rjson(os.path.join(it, "benchmark.json"))
        run0 = b["runs"][0]
        for k in ("eval_id", "eval_name", "configuration", "run_number", "result", "expectations", "notes"): self.assertIn(k, run0)
        for k in ("pass_rate", "passed", "failed", "total", "time_seconds", "tokens", "tool_calls", "errors"): self.assertIn(k, run0["result"])
        rs = b["per_engine"]["claude"]
        self.assertEqual(rs["with_skill"]["pass_rate"]["mean"], 1.0)
        self.assertAlmostEqual(rs["without_skill"]["pass_rate"]["mean"], 0.6667)  # eval 1 fails, evals 2 and 3 (human) pass
        self.assertEqual(rs["delta"]["pass_rate"], "+0.33")
        self.assertEqual(set(rs["delta"]), {"pass_rate", "time_seconds", "tokens"})
        pe = rjson(os.path.join(it, "claude", "benchmark.json"))  # skill-creator-compatible per-engine file
        self.assertIn("with_skill", pe["run_summary"]); self.assertNotIn("engine", pe["runs"][0])
        self.assertEqual(b["trigger"]["claude"]["with_skill"]["passed"], 20)
        self.assertEqual(b["pairwise"]["claude"]["with_skill_wins"] + b["pairwise"]["claude"]["baseline_wins"]
                         + b["pairwise"]["claude"]["ties"], 9)
        self.assertEqual(gate["problems"], []); self.assertFalse(gate["needs_approval"])
        self.assertIn("grader not calibrated for this iteration (run `calibrate --golden ...`)", gate["notes"])
        self.assertIn("pressure", b["pressure"]); self.assertIn("normal", b["pressure"])
        md = read(os.path.join(it, "comparison.md"))
        for s in ("## Gate", "auto-approved", "## Pass rate / time / tokens", "## Trigger evals", "## Blind pairwise",
                  "## Pressure scenarios", "## Per eval", "**delta**"):
            self.assertIn(s, md)

    def test_gate_problems(self):
        spec, n = self.run_one(runs=1, regress=True)
        se.grade_iteration(spec, self.ws, n, None, log=quiet)
        bench, gate = se.report_iteration(spec, self.ws, n)
        self.assertTrue(gate["needs_approval"])
        self.assertTrue(any(p.startswith("failing static checks in with_skill runs") for p in gate["problems"]), gate)
        self.assertTrue(any(p.startswith("regression [claude]") for p in gate["problems"]), gate)
        self.assertIn("NEEDS HUMAN APPROVAL", read(os.path.join(self.ws, "iteration-1", "comparison.md")))
    def test_gate_unit(self):
        rs = lambda w, b: {"e": {"with_skill": {"pass_rate": {"mean": w}, "graded_runs": 3},
                                 "without_skill": {"pass_rate": {"mean": b}, "graded_runs": 3}}}
        comp = {"e": {"expected_runs": 6, "graded_runs": 6, "expected_trigger_runs": 60, "trigger_runs": 60, "trigger_errors": 0}}
        self.assertEqual(se.compute_gate({"per_engine": rs(0.9, 0.5), "completeness": comp,
                                          "calibration": {"agreement": 0.9, "n": 10}})["problems"], [])
        g = se.compute_gate({"per_engine": rs(0.7, 0.5), "completeness": comp, "calibration": {"agreement": 0.6, "n": 10}},
                            prev={"per_engine": rs(0.9, 0.5)})
        self.assertEqual(len(g["problems"]), 2)
        self.assertTrue(any("previous iteration" in p for p in g["problems"]))
        self.assertTrue(any("low grader agreement" in p for p in g["problems"]))

    def test_gate_blocks_incomplete_failed_and_ungraded(self):
        spec, n = self.run_one(runs=1, evals=evals_doc(human=False))
        se.grade_iteration(spec, self.ws, n, self.grader(), seed=1, log=quiet)
        it = os.path.join(self.ws, "iteration-1")
        self.assertEqual(se.report_iteration(spec, self.ws, n)[1]["problems"], [])
        # a trigger query that failed, a run that errored, a missing run, and a stopped plan
        tp = os.path.join(it, "claude", "trigger", "with_skill", "results.json")
        tr = rjson(tp); tr["results"][0]["pass"] = False; tr["results"][0]["errors"] = 1
        tr["summary"]["passed"] -= 1; tr["summary"]["failed"] += 1; wjson(tp, tr)
        ed = os.path.join(it, "claude", "eval-1-start-the-login-fix-task")
        t = rjson(os.path.join(ed, "with_skill", "run-1", "outputs", "transcript.json")); t["error"] = "exit 1"
        wjson(os.path.join(ed, "with_skill", "run-1", "outputs", "transcript.json"), t)
        shutil.rmtree(os.path.join(ed, "without_skill", "run-1"))
        pl = rjson(os.path.join(it, "plan.json")); pl["stopped"] = "budget exhausted ($1.00 of $1.00)"
        wjson(os.path.join(it, "plan.json"), pl)
        se.grade_iteration(spec, self.ws, n, None, log=quiet)  # dry grade: LLM items now ungraded
        _, gate = se.report_iteration(spec, self.ws, n)
        joined = "\n".join(gate["problems"])
        for frag in ("incomplete: run stopped early (budget exhausted", "incomplete [claude]: 3 of 4 eval runs graded",
                     "execution failed [claude]: 1 trigger run(s)", "execution failed in 1 eval run(s)",
                     "trigger criteria failed [claude]: 1 of 20 queries", "ungraded: 3 LLM item(s) not graded"):
            self.assertIn(frag, joined)
        self.assertTrue(gate["needs_approval"]); self.assertFalse(gate["human_pending_only"])
        self.assertIn("incomplete: no plan.json", "\n".join(se.compute_gate({"per_engine": {}})["problems"]))

    def test_loop_stops_for_human_review(self):
        spec, _ = self.spec()
        r = se.loop(spec, self.ws, self.engines()[:1], self.grader(), lambda d: self.fail("builder called"), runs=1, log=quiet)
        self.assertEqual(r, {"status": "needs-human-review", "iteration": 1})

    def test_codex_guard_and_unknown_usage_charge(self):
        spec, _ = self.spec(evals=evals_doc(human=False))
        fx = FakeCodex()
        eng = se.CodexEngine(exec_fn=fx)
        prices = {"codex": (10.0, 10.0)}
        # worst-case exec = 3 x (64000 x 10) / 1e6 = $1.92, trigger = 3 x 25500 x 10 / 1e6 = $0.765
        n = se.run_iteration(spec, self.ws, [eng], runs=1, budget=se.Budget(1.925), prices=prices, log=quiet)
        plan = rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))
        self.assertIn("codex worst-case exec call $1.92 > remaining $1.92 (codex has no per-call cap)", plan["stopped"])
        # 1 exec call ($0.0056 spent, then the $1.92 bound no longer fits) + 20 trigger calls (smaller bound)
        self.assertEqual(len(fx.calls), 21)
        self.assertNotIn("--max-budget-usd", se.CodexEngine().command("p", True, 5.0))
        res = dict(se.new_result(), cost_usd=None)
        self.assertEqual(se.run_cost(res, eng, prices, fallback=1.92), 1.92)  # no usage reported: charge the bound

    def test_budget_stops_runs(self):
        spec, _ = self.spec()
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=3, budget=se.Budget(0.12), log=quiet)
        plan = rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))
        self.assertIn("budget exhausted", plan["stopped"])
        self.assertEqual(len(self.fc.calls), 3)  # $0.05 each: stops once half the remainder is under a cent
        caps = [c[0][c[0].index("--max-budget-usd") + 1] for c in self.fc.calls]
        self.assertEqual(caps, ["0.06", "0.03", "0.01"])  # floor(remaining x CLAUDE_CAP_HEADROOM) to cents

    def test_old_skill_baseline(self):
        spec, _ = self.spec()
        old = os.path.join(self.tmp, "snap"); shutil.copytree(spec["skill_dir"], old)
        with self.assertRaises(se.EvalError):
            se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, baseline="old", old_skill=None, log=quiet)
        n = se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, baseline="old", old_skill=old, log=quiet)
        it = os.path.join(self.ws, "iteration-%d" % n, "claude")
        self.assertTrue(os.path.isdir(os.path.join(it, "eval-2-no-trigger", "old_skill", "run-1", "workdir", ".claude", "skills")))
        self.assertTrue(os.path.isfile(os.path.join(it, "trigger", "old_skill", "results.json")))

class TestStats(unittest.TestCase):
    def test_matches_aggregate_benchmark(self):
        self.assertEqual(se.stats([0.8, 0.85, 0.9]), {"mean": 0.85, "stddev": 0.05, "min": 0.8, "max": 0.9})
        self.assertEqual(se.stats([1.0]), {"mean": 1.0, "stddev": 0.0, "min": 1.0, "max": 1.0})
        self.assertEqual(se.stats([])["mean"], 0.0)
    def test_analyzer_notes(self):
        mk = lambda cfg, ok, rate: {"engine": "e", "eval_id": 1, "configuration": cfg, "result": {"pass_rate": rate},
                                    "expectations": [{"text": "t", "passed": ok}]}
        notes = se.analyzer_notes([mk("with_skill", True, 1.0), mk("without_skill", True, 1.0)])
        self.assertTrue(any("passes in every configuration" in x for x in notes))
        notes = se.analyzer_notes([mk("with_skill", False, 0.0), mk("with_skill", False, 1.0), mk("without_skill", False, 0.0)])
        self.assertTrue(any("fails in every configuration" in x for x in notes))
        self.assertTrue(any("high variance" in x for x in notes))

class TestCalibration(Base):
    def test_agreement_and_kappa(self):
        ex = [{"id": i, "prompt": "p", "output": out, "item": "names a branch", "label": lab} for i, (out, lab) in enumerate(
            [("feat/a", True), ("feat/b", True), ("nothing", False), ("nothing", False), ("nothing", True)])]
        gp = os.path.join(self.tmp, "golden.json"); wjson(gp, {"examples": ex})
        cal = se.calibrate(gp, self.grader())
        self.assertEqual(cal["n"], 5); self.assertEqual(cal["agreement"], 0.8)
        self.assertAlmostEqual(cal["kappa"], 0.6154, places=4)
        self.assertEqual([d["id"] for d in cal["disagreements"]], [4])
        wjson(gp, {"examples": [{"prompt": "p"}]})
        with self.assertRaises(se.EvalError): se.calibrate(gp, self.grader())

class TestApprovalsAndRegistration(Base):
    def test_hash_bound_approvals(self):
        spec, _ = self.spec()
        self.assertFalse(se.approval_ok("eval_set", spec, self.ws))
        with self.assertRaises(se.EvalError): se.approve("requirements", spec, self.ws, "pinesky")
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")
        se.approve("requirements", spec, self.ws, "pinesky"); se.approve("eval_set", spec, self.ws, "pinesky")
        self.assertTrue(se.approval_ok("requirements", spec, self.ws)); self.assertTrue(se.approval_ok("eval_set", spec, self.ws))
        wjson(os.path.join(spec["skill_dir"], "evals", "trigger_evals.json"), TRIGGERS[::-1])
        self.assertFalse(se.approval_ok("eval_set", spec, self.ws))  # edit invalidates approval
        with self.assertRaises(se.EvalError): se.gate_hash("bogus", spec, self.ws)

    def test_eval_set_approval_covers_fixture_files(self):
        ev = evals_doc(); ev["evals"][0]["files"] = ["evals/files/in.csv"]
        spec, problems = self.spec(evals=ev, extra_files={"evals/files/in.csv": "a,b\n1,2\n"})
        self.assertEqual(problems, [])
        self.assertIn(("files:evals/files/in.csv", os.path.join(spec["skill_dir"], "evals", "files", "in.csv")),
                      se.eval_set_files(spec["skill_dir"]))
        se.approve("eval_set", spec, self.ws, "me")
        self.assertTrue(se.approval_ok("eval_set", spec, self.ws))
        write(os.path.join(spec["skill_dir"], "evals", "files", "in.csv"), "a,b\n1,3\n")
        self.assertFalse(se.approval_ok("eval_set", spec, self.ws))  # fixture edit invalidates approval
        se.approve("eval_set", spec, self.ws, "me")
        os.remove(os.path.join(spec["skill_dir"], "evals", "files", "in.csv"))
        self.assertFalse(se.approval_ok("eval_set", spec, self.ws))

    def run_report(self, regress):
        spec, _ = self.spec(evals=evals_doc(human=False))
        self.fc = FakeClaude(regress)
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=self.fc)], runs=1, log=quiet)
        se.grade_iteration(spec, self.ws, n, self.grader(), log=quiet)
        se.report_iteration(spec, self.ws, n)
        return spec

    def test_register_clean_is_automatic(self):
        spec = self.run_report(False)
        rec = se.register(spec, self.ws)
        self.assertEqual(rec["approval"], "auto"); self.assertEqual(rec["iteration"], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "registration.json")))

    def test_register_with_problems_needs_approval(self):
        spec = self.run_report(True)
        with self.assertRaises(se.EvalError): se.register(spec, self.ws)
        se.approve("registration", spec, self.ws, "pinesky")
        self.assertEqual(se.register(spec, self.ws)["approval"]["by"], "pinesky")

class TestLoop(Base):
    def spec(self, **kw):
        kw.setdefault("evals", evals_doc(human=False))
        return Base.spec(self, **kw)
    def test_max_iterations_enforced(self):
        spec, _ = self.spec()
        with self.assertRaises(se.EvalError):
            se.loop(spec, self.ws, self.engines(), self.grader(), None, max_iterations=6, log=quiet)
    def test_stops_when_clean(self):
        spec, _ = self.spec()
        r = se.loop(spec, self.ws, self.engines()[:1], self.grader(), lambda d: self.fail("builder called"), runs=1, log=quiet)
        self.assertEqual(r, {"status": "clean", "iteration": 1})
    def test_builder_until_max_then_needs_approval(self):
        spec, _ = self.spec()
        seen = []
        r = se.loop(spec, self.ws, self.engines(regress=True)[:1], self.grader(), lambda d: seen.append(d) or 0,
                    max_iterations=3, runs=1, log=quiet)
        self.assertEqual(r, {"status": "needs-approval", "iteration": 3})
        self.assertEqual([os.path.basename(d) for d in seen], ["iteration-1", "iteration-2"])
    def test_builder_fixes_skill(self):
        spec, _ = self.spec()
        engs = self.engines(regress=True)[:1]
        def builder(d):
            self.fc.regress = False; return 0
        r = se.loop(spec, self.ws, engs, self.grader(), builder, runs=1, log=quiet)
        self.assertEqual(r, {"status": "clean", "iteration": 2})
        self.assertEqual(se.loop(spec, self.ws, engs, self.grader(), lambda d: 1, runs=1, log=quiet)["status"], "clean")
    def test_builder_failure(self):
        spec, _ = self.spec()
        r = se.loop(spec, self.ws, self.engines(regress=True)[:1], self.grader(), lambda d: 7, runs=1, log=quiet)
        self.assertEqual(r["status"], "builder-failed")

class TestCLI(Base):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_plan_is_dry_and_writes_nothing(self):
        d = make_skill(self.tmp)
        for cmd in ("plan", "run", "loop"):
            rc, out, _ = self.cli(cmd, d, "--workspace", self.ws, "--price", "codex=5,30")
            self.assertEqual(rc, 0)
            self.assertIn("DRY RUN: nothing was executed", out)
            self.assertFalse(os.path.exists(self.ws))
        self.assertIn("approval gates: requirements=NOT APPROVED, eval_set=NOT APPROVED", out)
        self.assertIn("[claude without_skill]", out); self.assertIn("--disable-slash-commands", out)
        self.assertIn("[codex with_skill] codex exec --json", out)
        self.assertIn("priced total $", out)

    def test_estimate_overrides_and_eva_passthrough(self):
        d = make_skill(self.tmp)
        rc, out, _ = self.cli("estimate", d, "--cases", "3", "--llm-cases", "1", "--trigger-queries", "20")
        self.assertEqual(rc, 0); self.assertIn("total calls 186", out); self.assertIn("UNPRICED: codex CLI default", out)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(eva.main(["eval", "estimate", d, "--engines", "claude"]), 0)
        self.assertIn("claude-opus-5-5", buf.getvalue())

    def test_live_requires_budget_approvals_and_fit(self):
        d = make_skill(self.tmp)
        rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live")
        self.assertEqual(rc, 2); self.assertIn("--budget-usd", err)
        rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "100", "--engines", "claude")
        self.assertEqual(rc, 2); self.assertIn("gate requirements not approved", err)
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")
        self.cli("approve", d, "--workspace", self.ws, "--gate", "requirements", "--by", "pinesky")
        self.cli("approve", d, "--workspace", self.ws, "--gate", "eval_set", "--by", "pinesky")
        rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "100")
        self.assertEqual(rc, 2); self.assertIn("no price for", err)
        rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "1", "--engines", "claude")
        self.assertEqual(rc, 2); self.assertIn("exceeds budget", err)
        self.assertFalse(se._LIVE["on"])
        self.assertEqual(se.iterations(self.ws), [])

    def approved_skill(self, **kw):
        d = make_skill(self.tmp, **kw)
        os.makedirs(self.ws, exist_ok=True); write(os.path.join(self.ws, "requirements.md"), "goal")
        for g in ("requirements", "eval_set"): self.cli("approve", d, "--workspace", self.ws, "--gate", g, "--by", "me")
        return d

    def test_live_run_with_mocked_exec(self):
        d = self.approved_skill(evals=evals_doc(human=False))
        fake, fg, seen_live = FakeClaude(), FakeGraderExec(), []
        def fake_live(cmd, cwd, timeout):
            seen_live.append(se._LIVE["on"])
            return fg(cmd, cwd, timeout) if "--json-schema" in cmd else fake(cmd, cwd, timeout)
        with mock.patch.object(se, "live_exec", fake_live):
            rc, out, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "100", "--engines", "claude", "--runs", "1")
        self.assertEqual(rc, 0, err); self.assertTrue(seen_live and all(seen_live))
        self.assertFalse(se._LIVE["on"])  # reset after the command
        self.assertEqual(se.iterations(self.ws), [1])
        rc, out, _ = self.cli("grade", d, "--workspace", self.ws)
        self.assertIn("LLM items not graded", out)
        rc, out, _ = self.cli("report", d, "--workspace", self.ws)
        self.assertEqual(rc, 3, out); self.assertIn("ungraded: 2 LLM item(s) not graded", out)
        rc, _, err = self.cli("register", d, "--workspace", self.ws)
        self.assertEqual(rc, 2); self.assertIn("needs `approve --gate registration`", err)
        with mock.patch.object(se, "live_exec", fake_live):
            rc, out, err = self.cli("grade", d, "--workspace", self.ws, "--live", "--budget-usd", "5", "--engines", "claude")
        self.assertEqual(rc, 0, err)
        self.assertTrue(all("--max-budget-usd" in c for c in fg.calls))
        rc, out, _ = self.cli("report", d, "--workspace", self.ws)
        self.assertEqual(rc, 0, out); self.assertIn("clean", out)
        rc, out, _ = self.cli("register", d, "--workspace", self.ws)
        self.assertEqual(rc, 0); self.assertIn("auto", out)

    # P1a
    def test_count_overrides_only_for_estimate_and_plan(self):
        d = self.approved_skill()
        for cmd in ("run", "loop", "grade", "calibrate"):
            extra = ["--golden", "g.json"] if cmd == "calibrate" else []
            rc, _, err = self.cli(cmd, d, "--workspace", self.ws, "--live", "--budget-usd", "1000", "--engines", "claude",
                                  "--cases", "0", "--llm-cases", "0", "--trigger-queries", "0", *extra)
            self.assertEqual(rc, 2, cmd); self.assertIn("only apply to estimate/plan", err)
        rc, out, _ = self.cli("plan", d, "--workspace", self.ws, "--engines", "claude", "--cases", "0", "--llm-cases", "0",
                              "--trigger-queries", "0")
        self.assertEqual(rc, 0); self.assertIn("total calls 0", out)
        # live estimate is always the full validated set: $1 is far below it
        rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "1", "--engines", "claude")
        self.assertEqual(rc, 2); self.assertIn("exceeds budget", err)
        self.assertEqual(se.iterations(self.ws), [])

    # P1b
    def test_budget_must_be_finite_and_codex_bound_must_fit(self):
        d = self.approved_skill()
        for b in ("inf", "nan", "0", "-1"):
            rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", b, "--engines", "claude")
            self.assertEqual(rc, 2, b); self.assertIn("finite --budget-usd", err)
        rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "1000", "--engines", "codex",
                              "--price", "codex=0,0")
        self.assertEqual(rc, 2); self.assertIn("finite prices > 0", err)
        # codex worst case per exec call: 3 x (60000 x 100 + 4000 x 100) / 1e6 = $19.20 > $10 budget
        rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "10", "--engines", "codex",
                              "--price", "codex=100,100", "--runs", "1")
        self.assertEqual(rc, 2); self.assertIn("worst-case exec call bound $19.20", err); self.assertIn("no per-call cap", err)
        self.assertEqual(se.iterations(self.ws), [])

    # P1c
    def test_grade_and_calibrate_live_need_gates_and_full_estimate(self):
        d = make_skill(self.tmp)
        n = se.run_iteration(se.load_eval_set(d)[0], self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude())],
                             runs=1, log=quiet)
        gp = os.path.join(self.tmp, "golden.json")
        wjson(gp, {"examples": [{"prompt": "p", "output": "o", "item": "i", "label": True}] * 2000})
        for cmd, extra in (("grade", []), ("calibrate", ["--golden", gp])):
            rc, _, err = self.cli(cmd, d, "--workspace", self.ws, "--live", "--budget-usd", "100", "--engines", "claude", *extra)
            self.assertEqual(rc, 2, cmd); self.assertIn("gate requirements not approved", err)
        write(os.path.join(self.ws, "requirements.md"), "goal")
        for g in ("requirements", "eval_set"): self.cli("approve", d, "--workspace", self.ws, "--gate", g, "--by", "me")
        # 2000 golden examples x (12000 x 2 + 800 x 10) / 1e6 = $64 > $10
        rc, _, err = self.cli("calibrate", d, "--workspace", self.ws, "--live", "--budget-usd", "10", "--golden", gp)
        self.assertEqual(rc, 2); self.assertIn("estimate $64.00 exceeds budget $10.00", err)
        # grade: 2 grader calls (eval 1, both configs) + 3 pairwise calls, priced from the iteration on disk
        rc, _, err = self.cli("grade", d, "--workspace", self.ws, "--live", "--budget-usd", "0.05", "--engines", "claude",
                              "--iteration", str(n))
        self.assertEqual(rc, 2); self.assertIn("exceeds budget", err)
        est = se.grader_estimate(*se.grade_counts(se.load_eval_set(d)[0], os.path.join(self.ws, "iteration-%d" % n)),
                                 "claude-sonnet-5-5", se.DEFAULT_TOKENS, {})
        self.assertEqual([r["calls"] for r in est["rows"]], [2, 3])
        rc, _, err = self.cli("grade", d, "--workspace", self.ws, "--live", "--budget-usd", "10", "--grader-model", "x-model")
        self.assertEqual(rc, 2); self.assertIn("no price for", err)
        self.assertFalse(se._LIVE["on"])

    def test_grader_fallback_cost_honors_price_override(self):
        g = se.LLMGrader("claude-sonnet-5-5", exec_fn=FakeGraderExec(report_cost=False), budget=se.Budget(10.0),
                         prices={"grader": (1000.0, 1000.0)})
        g.grade({"prompt": "p", "expected_output": "e"}, {"final": "feat/x"}, [{"id": "1", "text": "t"}])
        self.assertAlmostEqual(g.usage["cost_usd"], (100 + 10) * 1000.0 / 1e6)
        self.assertAlmostEqual(g.budget.spent, 0.11)

    # P1e
    def test_preflight_blocks_discoverable_copies(self):
        d = self.approved_skill()
        spec = se.load_eval_set(d)[0]
        self.assertEqual(se.preflight(spec, ["claude", "codex"], self.ws), [])
        user = os.path.join(self.home, ".agents", "skills", "branch-setup")
        os.makedirs(user); write(os.path.join(user, "SKILL.md"), SKILL_MD)
        pf = se.preflight(spec, ["claude", "codex"], self.ws)
        self.assertEqual(len(pf), 1); self.assertTrue(pf[0].startswith("codex: skill 'branch-setup' is also discoverable"))
        rc, _, err = self.cli("run", d, "--workspace", self.ws, "--live", "--budget-usd", "1000", "--price", "codex=1,1")
        self.assertEqual(rc, 2); self.assertIn("preflight: codex", err)
        shutil.rmtree(user)
        anc = os.path.join(self.tmp, ".claude", "skills", "branch-setup")  # ancestor of the workspace
        os.makedirs(anc); write(os.path.join(anc, "SKILL.md"), SKILL_MD)
        self.assertTrue(se.preflight(spec, ["claude"], self.ws)[0].startswith("claude: skill 'branch-setup'"))
        self.assertEqual(se.iterations(self.ws), [])

    # P2a
    def test_extra_args_cannot_change_model_or_budget(self):
        for eng, args in (("claude", ["--model", "x"]), ("claude", ["--model=x"]), ("claude", ["--max-budget-usd", "9"]),
                          ("claude", ["--fallback-model", "x"]), ("codex", ["-m", "x"]), ("codex", ["-mx"]),
                          ("codex", ["--model=x"]), ("codex", ["-c", "model=x"]), ("codex", ["--config=model_provider=y"]),
                          ("codex", ["--profile", "p"])):
            with self.assertRaises(se.EvalError, msg=args): se.ENGINES[eng](extra_args=args)
        se.ClaudeCodeEngine(extra_args=["--permission-mode", "acceptEdits"])
        se.CodexEngine(extra_args=["-c", "sandbox_workspace_write.network_access=true"])
        d = make_skill(self.tmp)
        rc, _, err = self.cli("plan", d, "--engines", "claude", "--claude-arg=--model", "--claude-arg=claude-haiku-5-5")
        self.assertEqual(rc, 2); self.assertIn("may not set the model", err)

    def test_grader_check_uses_models_recorded_in_plan(self):
        d = self.approved_skill()
        spec = se.load_eval_set(d)[0]
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-sonnet-5-5", exec_fn=FakeClaude())], runs=1, log=quiet)
        # the grade command's own --claude-model (default opus) differs from the grader, but the runs used sonnet
        rc, _, err = self.cli("grade", d, "--workspace", self.ws, "--live", "--budget-usd", "10", "--engines", "claude",
                              "--grader-model", "claude-sonnet-5-5", "--iteration", str(n))
        self.assertEqual(rc, 2); self.assertIn("equals the executor model of ['claude']", err)

    def test_validate_and_snapshot(self):
        d = make_skill(self.tmp, triggers=None)
        rc, out, _ = self.cli("validate", d)
        self.assertEqual(rc, 1); self.assertIn("missing evals/trigger_evals.json", out)
        rc, out, _ = self.cli("snapshot", d, "--workspace", self.ws)
        self.assertEqual(rc, 0); self.assertTrue(os.path.isfile(os.path.join(self.ws, "skill-snapshot", "SKILL.md")))
        rc, _, err = self.cli("snapshot", d, "--workspace", self.ws)
        self.assertEqual(rc, 2)

class TestDocs(unittest.TestCase):
    def test_readme_documents_harness(self):
        readme = read(os.path.join(ROOT, "README.md"))
        for s in ("## 스킬 평가 하네스 (skill_eval.py)", "### skill-creator 대비 차이(deviation)", "trigger_evals.json",
                  "--live", "--budget-usd", "human_review.json", "comparison.md", "approvals.json"):
            self.assertIn(s, readme)
