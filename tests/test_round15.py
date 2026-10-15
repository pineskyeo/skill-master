"""Round-15 tests: every Claude launch also passes skillOverrides "off" for every known bundled skill (static 2.1.300
list + a scan of the installed binary's registerBundledSkill calls, kill-switch survivors 'design' and 'doctor'
included), never for the skill under test; the child env sets DISABLE_DOCTOR_COMMAND=1; a skill named like a bundled
skill is refused before launch; the strict init check is unchanged. Everything is mocked: subprocess is patched to
fail for the whole module."""
import io, json, os, subprocess, sys, unittest
from contextlib import redirect_stdout
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, claude_stream
from test_round3_isolation import Base

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

STATIC = set(se.CLAUDE_BUNDLED_SKILL_NAMES)

def settings_of(cmd):
    return json.loads(cmd[cmd.index("--settings") + 1])

# A miniature of the 2.1.300 bundle layout: chunks separated by "// @bun @bytecode", registerBundledSkill exported
# under a minified alias, names given as literals, chunk-local identifiers, identifiers assigned in another chunk,
# an ambiguous identifier and a template literal.
FAKE_BUNDLE = b"".join([
    b"\x00// @bun @bytecode\nfunction Zs(e){let l=Er();if(e.survivesBundledKillSwitch)l.add(e)}",
    b"export{Zs as registerBundledSkill,xSo as registerBundledSkillSessionReset};\n",
    b"\x00// @bun @bytecode\nfunction Wo(){Zs({name:\"doctor\",aliases:[\"checkup\"],isEnabled:()=>!a.DISABLE_DOCTOR_COMMAND,"
    b"survivesBundledKillSwitch:!0,getPromptForCommand(){}})}",
    b"Zs({name:\"plain-one\",isEnabled:()=>!0,async getPromptForCommand(){return[]}}),survivesBundledKillSwitch:!0;\n",
    b"\x00// @bun @bytecode\nimport{Ppt}from\"/$bunfs/root/chunk-a.js\";function lt(){Zs({name:Ppt,description:()=>1,"
    b"survivesBundledKillSwitch:!0,policyGate:()=>1,getPromptForCommand:(r)=>ws(r)})}",
    b"var Lcl=\"chunk-local\";function q(){Zs({name:Lcl,userInvocable:!0,getPromptForCommand(){}})}",
    b"function amb(){Zs({name:e,isEnabled:Vs,getPromptForCommand(){}})}",
    b"function tpl(){for(let{kind:e}of Qn)Zs({name:`artifact-${e}`,getPromptForCommand(){}})}",
    b"function m(){xZs({name:\"not-a-call\"});o.Zs({name:\"member-call\"})}\n",
    b"\x00// @bun @bytecode\nvar Ppt=\"design\",e=\"one\";",
    b"\x00// @bun @bytecode\nvar e=\"two\";",
])

class CacheBase(Base):
    def setUp(self):
        super().setUp()
        self._saved = (dict(se._BUILTIN_SCAN), dict(se._BUNDLED_SCAN))
    def tearDown(self):
        for cache, saved in zip((se._BUILTIN_SCAN, se._BUNDLED_SCAN), self._saved):
            cache.clear(); cache.update(saved)
        super().tearDown()
    def fake_binary(self, data):
        p = os.path.join(self.tmp, "claude-bin")
        with open(p, "wb") as f: f.write(data)
        return p
    def seed(self, data=b""):
        """Both caches filled from a fake binary (static lists plus whatever data registers)."""
        p = self.fake_binary(data)
        se.claude_builtin_plugins(p, refresh=True); return se.claude_bundled_skills(p, refresh=True)

class TestBundledDiscovery(CacheBase):
    def test_static_list_has_the_kill_switch_survivors_and_the_template_names(self):
        self.assertEqual(set(se.CLAUDE_BUNDLED_KILL_SWITCH_SURVIVORS), {"design", "doctor"})
        self.assertTrue({"design", "doctor", "slides", "doc", "artifact-report", "claude-api"} <= STATIC)
        self.assertNotIn("csv-cleaner", STATIC)

    def test_scan_resolves_literals_and_identifiers_and_finds_survivors(self):
        names, surv = se.scan_bundled_skill_names(self.fake_binary(FAKE_BUNDLE))
        self.assertEqual(names, {"doctor", "plain-one", "design", "chunk-local"})
        # survivor flag is read inside the call's object only (plain-one's trailing flag is after getPromptForCommand)
        self.assertEqual(surv, {"doctor", "design"})

    def test_scan_without_the_export_or_of_an_empty_file_finds_nothing(self):
        self.assertEqual(se.scan_bundled_skill_names(self.fake_binary(b"")), (set(), set()))
        self.assertEqual(se.scan_bundled_skill_names(self.fake_binary(FAKE_BUNDLE.replace(b" as registerBundledSkill,",
                                                                                          b" as other,"))), (set(), set()))

    def test_scan_results_are_added_to_the_static_list(self):
        info = self.seed(FAKE_BUNDLE)
        self.assertEqual(set(info["names"]), STATIC | {"plain-one", "chunk-local"})
        self.assertEqual(info["survivors"], ["design", "doctor"]); self.assertIsNone(info["error"])

    def test_missing_binary_falls_back_to_the_static_list(self):
        info = se.claude_bundled_skills(os.path.join(self.tmp, "nope"), refresh=True)
        self.assertEqual(set(info["names"]), STATIC); self.assertIn("FileNotFoundError", info["error"])
        with mock.patch.object(se.shutil, "which", return_value=None):
            info = se.claude_bundled_skills(refresh=True)
        self.assertEqual(set(info["names"]), STATIC); self.assertEqual(info["error"], "claude not found on PATH")

    def test_scan_is_cached(self):
        self.seed(FAKE_BUNDLE)
        first = se.claude_settings_json("x")
        with mock.patch.object(se, "scan_bundled_skill_names", side_effect=AssertionError("rescanned")):
            self.assertEqual(se.claude_settings_json("x"), first)

class TestSkillOverrides(CacheBase):
    def test_every_bundled_skill_is_off_except_the_skill_under_test(self):
        self.seed(FAKE_BUNDLE)
        st = se.claude_settings("csv-cleaner")
        self.assertIs(st["disableBundledSkills"], True)
        self.assertEqual(st["skillOverrides"], {n: "off" for n in STATIC | {"plain-one", "chunk-local"}})
        self.assertEqual(st["skillOverrides"]["design"], "off"); self.assertEqual(st["skillOverrides"]["doctor"], "off")
        self.assertNotIn("csv-cleaner", st["skillOverrides"])
        self.assertFalse(any(st["enabledPlugins"].values()))  # round 14 unchanged
        # a target that shares a bundled name is never overridden (it is refused before launch, see below)
        self.assertNotIn("plain-one", se.claude_settings("plain-one")["skillOverrides"])
        self.assertIn("plain-one", se.claude_settings()["skillOverrides"])

    def test_executor_with_and_without_skill_never_override_the_target(self):
        self.seed()
        e = se.ClaudeCodeEngine("claude-sonnet-5-5")
        for with_skill in (True, False):
            ov = settings_of(e.command("p", with_skill, 1.0, skill_name="design"))["skillOverrides"]
            self.assertNotIn("design", ov); self.assertEqual(ov["doctor"], "off")

    def test_run_iteration_launches_exclude_the_spec_skill_and_set_survivors_off(self):
        self.seed()
        spec, _ = self.spec()
        se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=T.quiet)
        self.assertTrue(self.fc.calls)
        for cmd, _ in self.fc.calls:  # executor and trigger runs
            ov = settings_of(cmd)["skillOverrides"]
            self.assertNotIn(spec["skill_name"], ov)
            self.assertEqual(set(ov), STATIC); self.assertTrue(all(v == "off" for v in ov.values()))

    def test_grader_and_builder_set_every_bundled_skill_off(self):
        self.seed()
        g = se.LLMGrader("claude-sonnet-5-5").command("p", {"type": "object"}, 1.0)
        self.assertEqual(set(settings_of(g)["skillOverrides"]), STATIC)
        b = se.ClaudeBuilder("fix", "claude-opus-5-5", se.Budget(1.0), skill_name="doc").command("p", 1.0)
        self.assertEqual(set(settings_of(b)["skillOverrides"]), STATIC - {"doc"})

    def test_launch_claude_checks_the_settings_for_the_same_skill_name(self):
        self.seed()
        calls = []
        fx = lambda *a, **k: calls.append(a) or (0, "", "")
        cmd = ["claude", "-p", "x"] + se.claude_isolation_args("doc")
        with self.assertRaises(se.EvalError):  # settings built for "doc" do not match a launch for no/another skill
            se.launch_claude(fx, cmd, self.tmp, 5)
        old14 = ["claude", "-p", "x", "--setting-sources", "project,local", "--strict-mcp-config", "--settings",
                 json.dumps({k: v for k, v in se.claude_settings().items() if k != "skillOverrides"}, sort_keys=True)]
        with self.assertRaises(se.EvalError):  # round-14 settings without skillOverrides are refused
            se.launch_claude(fx, old14, self.tmp, 5)
        self.assertEqual(calls, [])
        se.launch_claude(fx, cmd, self.tmp, 5, skill_name="doc")
        self.assertEqual(len(calls), 1)

    def test_live_builder_passes_the_spec_skill_name(self):
        self.seed()
        made = []
        real = se.ClaudeBuilder
        def spy(*a, **k):
            made.append(k.get("skill_name")); return real(*a, **k)
        d = os.path.join(self.tmp, "builder.md"); T.write(d, "Fix it.")
        with mock.patch.object(se, "ClaudeBuilder", spy), mock.patch.object(se, "loop", return_value={"status": "clean"}), \
                mock.patch.object(se, "check_live_gates", return_value=None), \
                mock.patch.object(se, "make_grader", return_value=mock.Mock()):
            spec, _ = self.spec()
            self.cli("loop", spec["skill_dir"], "--workspace", self.ws, "--live", "--budget-usd", "5", "--engines",
                     "claude", "--runs", "1", "--builder-prompt", d, "--run-root", self.runs)
        self.assertEqual(made, [spec["skill_name"]])

class TestDoctorEnvAndCollision(CacheBase):
    def test_child_env_disables_doctor(self):
        self.assertEqual(se.CLAUDE_CHILD_ENV["DISABLE_DOCTOR_COMMAND"], "1")
        self.assertEqual(se.claude_child_env()["DISABLE_DOCTOR_COMMAND"], "1")
        self.assertEqual(se.ClaudeCodeEngine("claude-sonnet-5-5").sandbox_env(self.tmp)["DISABLE_DOCTOR_COMMAND"], "1")

    def test_a_skill_named_like_a_bundled_skill_is_refused_before_launch(self):
        self.seed()
        e = se.ClaudeCodeEngine("claude-sonnet-5-5")
        work = os.path.join(self.tmp, "w"); os.makedirs(work)
        probs = e.sandbox_problems(self.tmp, work, "design", False)
        self.assertTrue(any("'design' is also a bundled skill" in p for p in probs), probs)
        self.assertFalse(any("bundled skill" in p for p in e.sandbox_problems(self.tmp, work, "csv-cleaner", False)))
        self.assertFalse(any("bundled skill" in p for p in e.sandbox_problems(self.tmp, work, None, False)))

class TestInitCheckUnchanged(Base):
    def check(self, **kw):
        e = se.ClaudeCodeEngine("claude-sonnet-5-5")
        return e.isolation_problems(e.parse(claude_stream("x", **kw)), True, "csv-cleaner", [])

    def test_design_or_doctor_still_loaded_stops_the_run(self):
        for extra in (["design"], ["doctor"], ["design", "doctor"]):
            probs = self.check(skills=["csv-cleaner"] + extra)
            self.assertEqual(len(probs), 1, extra)
        self.assertEqual(self.check(skills=["csv-cleaner"]), [])

class TestDryRunPrint(CacheBase):
    def test_dry_run_shows_the_overrides_and_the_doctor_env(self):
        self.seed()
        spec, _ = self.spec()
        a = mock.Mock(argv=["run", "x", "--live", "--dry-run"], grader_engine="claude", budget_usd=5.0)
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.object(se, "check_live_gates", return_value={"total_usd": 1.0}), \
                mock.patch.object(se, "plan_text", return_value=""):
            se.live_dry_run(a, spec, [], self.ws, None, [se.ClaudeCodeEngine("claude-sonnet-5-5")], {}, {})
        out = buf.getvalue()
        self.assertIn('"skillOverrides": {', out); self.assertIn('"doctor": "off"', out)
        self.assertIn("kill-switch survivors: design, doctor", out); self.assertIn("DISABLE_DOCTOR_COMMAND=1", out)
        self.assertIn("bundled skills set off: %d, never %r" % (len(STATIC), spec["skill_name"]), out)

if __name__ == "__main__":
    unittest.main()
