"""Round-14 tests: every Claude launch (executor, trigger, grader, builder) passes --settings with enabledPlugins
setting every known "<name>@builtin" id to false (static list + a scan of the installed binary), the strict init check
is unchanged (any plugin or extra skill still stops the run), and the free `check-builtin-plugins` command reports
the listed enabled states. Everything is mocked: subprocess is patched to fail for the whole module."""
import io, json, os, subprocess, sys, unittest
from contextlib import redirect_stdout
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, claude_stream, FakeGraderExec
from test_round3_isolation import Base

_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

STATIC_IDS = {n + "@builtin" for n in se.CLAUDE_BUILTIN_PLUGIN_NAMES}

def settings_of(cmd):
    return json.loads(cmd[cmd.index("--settings") + 1])

class CacheBase(Base):
    def setUp(self):
        super().setUp()
        self._saved = dict(se._BUILTIN_SCAN)
    def tearDown(self):
        se._BUILTIN_SCAN.clear(); se._BUILTIN_SCAN.update(self._saved)
        super().tearDown()
    def fake_binary(self, data):
        p = os.path.join(self.tmp, "claude-bin")
        with open(p, "wb") as f: f.write(data)
        return p

class TestDiscovery(CacheBase):
    def test_static_list_has_the_names_seen_in_2_1_300(self):
        for n in ("agents-md", "telemetry", "plugin-authoring", "sec-default", "you-should-know"):
            self.assertIn("cc-plugin-" + n, se.CLAUDE_BUILTIN_PLUGIN_NAMES)

    def test_scan_finds_names_across_chunk_boundaries(self):
        data = b"\x00" * 13 + b"cc-plugin-agents-md@builtin\x00junk cc-plugin-new-thing\x00" + b"x" * 7 + b"cc-plugin-diff"
        p = self.fake_binary(data)
        for chunk in (5, 16, 1 << 20):
            self.assertEqual(se.scan_builtin_plugin_names(p, chunk=chunk),
                             {"cc-plugin-agents-md", "cc-plugin-new-thing", "cc-plugin-diff"}, chunk)

    def test_discovered_names_are_added_to_the_static_list(self):
        info = se.claude_builtin_plugins(self.fake_binary(b"..cc-plugin-brand-new.."), refresh=True)
        self.assertEqual(set(info["ids"]), STATIC_IDS | {"cc-plugin-brand-new@builtin"})
        self.assertIsNone(info["error"])

    def test_unreadable_or_missing_binary_falls_back_to_static_list(self):
        info = se.claude_builtin_plugins(os.path.join(self.tmp, "nope"), refresh=True)
        self.assertEqual(set(info["ids"]), STATIC_IDS); self.assertIn("FileNotFoundError", info["error"])
        with mock.patch.object(se.shutil, "which", return_value=None):
            info = se.claude_builtin_plugins(refresh=True)
        self.assertEqual(set(info["ids"]), STATIC_IDS); self.assertEqual(info["error"], "claude not found on PATH")

    def test_scan_is_cached_so_every_launch_gets_the_same_settings(self):
        se.claude_builtin_plugins(self.fake_binary(b"cc-plugin-once"), refresh=True)
        first = se.claude_settings_json()
        with mock.patch.object(se, "scan_builtin_plugin_names", side_effect=AssertionError("rescanned")):
            self.assertEqual(se.claude_settings_json(), first)
            self.assertEqual(se.claude_isolation_args()[-1], first)

class TestLaunchSettings(CacheBase):
    def test_settings_disable_bundled_skills_and_every_builtin_plugin(self):
        se.claude_builtin_plugins(self.fake_binary(b"cc-plugin-extra"), refresh=True)
        st = se.claude_settings()
        self.assertIs(st["disableBundledSkills"], True)
        self.assertEqual(set(st["enabledPlugins"]), STATIC_IDS | {"cc-plugin-extra@builtin"})
        self.assertTrue(all(v is False for v in st["enabledPlugins"].values()))

    def test_executor_trigger_grader_and_builder_all_disable_builtin_plugins(self):
        e = se.ClaudeCodeEngine("claude-sonnet-5-5")
        cmds = [e.command("p", True, 1.0), e.command("p", False, 1.0),
                se.LLMGrader("claude-sonnet-5-5").command("p", {"type": "object"}, 1.0),
                se.ClaudeBuilder("fix", "claude-opus-5-5", se.Budget(1.0)).command("p", 1.0)]
        for cmd in cmds:
            st = settings_of(cmd)
            self.assertTrue(STATIC_IDS <= set(st["enabledPlugins"]), cmd[:3])
            self.assertFalse(any(st["enabledPlugins"].values()))

    def test_run_iteration_launches_carry_the_plugin_map(self):
        spec, _ = self.spec()
        se.run_iteration(spec, self.ws, self.engines()[:1], runs=1, log=T.quiet)
        self.assertTrue(self.fc.calls)
        for cmd, _ in self.fc.calls:  # executor and trigger runs
            self.assertTrue(STATIC_IDS <= set(settings_of(cmd)["enabledPlugins"]))

    def test_launch_claude_refuses_the_old_settings_without_plugin_map(self):
        old = ["--setting-sources", "project,local", "--strict-mcp-config", "--settings",
               json.dumps({"disableBundledSkills": True})]
        calls = []
        with self.assertRaises(se.EvalError):
            se.launch_claude(lambda *a, **k: calls.append(a) or (0, "", ""), ["claude", "-p", "x"] + old, self.tmp, 5)
        self.assertEqual(calls, [])

class TestInitCheckUnchanged(Base):
    def check(self, **kw):
        e = se.ClaudeCodeEngine("claude-sonnet-5-5")
        return e.isolation_problems(e.parse(claude_stream("x", **kw)), True, "csv-cleaner", [])

    def test_a_builtin_plugin_that_still_loads_stops_the_run_with_its_full_id(self):
        plugins = [{"name": "cc-plugin-%s" % n, "path": "builtin", "source": "cc-plugin-%s@builtin" % n}
                   for n in ("agents-md", "telemetry", "sec-default")]
        probs = self.check(skills=["csv-cleaner"], plugins=plugins)
        self.assertEqual(len(probs), 1)
        self.assertIn("cc-plugin-sec-default@builtin", probs[0])  # third id no longer cut off at 200 chars

    def test_design_doctor_or_plugin_skills_still_stop_the_run(self):
        probs = self.check(skills=["csv-cleaner", "design", "doctor", "plugin-authoring"])
        self.assertEqual(len(probs), 1)
        self.assertIn("['csv-cleaner', 'design', 'doctor', 'plugin-authoring']", probs[0])

    def test_exact_skill_and_no_plugins_passes(self):
        self.assertEqual(self.check(skills=["csv-cleaner"]), [])

class P:
    def __init__(self, stdout): self.stdout, self.returncode = stdout, 0

class TestFreeCheckCommand(CacheBase):
    def setUp(self):
        super().setUp()
        se.claude_builtin_plugins(self.fake_binary(b""), refresh=True)  # static ids only

    def listing(self, enabled=False, skip=()):
        return json.dumps([{"id": i, "enabled": enabled} for i in sorted(STATIC_IDS) if i not in skip] +
                          [{"id": "csv@local", "enabled": True}])

    def test_command_is_plugin_management_only(self):
        cmd = se.builtin_plugin_check_command()
        self.assertEqual(cmd[0], "claude"); self.assertNotIn("-p", cmd); self.assertNotIn("--print", cmd)
        self.assertEqual(cmd[-3:], ["plugin", "list", "--json"])
        self.assertEqual(cmd[cmd.index("--settings") + 1], se.claude_settings_json())

    def test_all_disabled_passes(self):
        seen = []
        rc, lines = se.builtin_plugin_check(lambda c: seen.append(c) or P(self.listing()))
        self.assertEqual(rc, 0, lines); self.assertEqual(seen, [se.builtin_plugin_check_command()])

    def test_enabled_or_unlisted_builtin_fails(self):
        rc, lines = se.builtin_plugin_check(lambda c: P(json.dumps(
            [{"id": i, "enabled": i == "cc-plugin-sec-default@builtin"} for i in sorted(STATIC_IDS)])))
        self.assertEqual(rc, 1); self.assertIn("NOT disabled: cc-plugin-sec-default@builtin", lines)
        rc, lines = se.builtin_plugin_check(lambda c: P(self.listing(skip={"cc-plugin-tips@builtin"})))
        self.assertEqual(rc, 1); self.assertTrue(any("not listed" in l and "cc-plugin-tips@builtin" in l for l in lines))

    def test_unparseable_or_empty_output_cannot_confirm(self):
        for out in ("not json", "[]", "{}"):
            rc, lines = se.builtin_plugin_check(lambda c: P(out))
            self.assertEqual(rc, 1, out)
        def boom(c): raise FileNotFoundError("claude")
        rc, lines = se.builtin_plugin_check(boom)
        self.assertEqual(rc, 1); self.assertIn("cannot confirm: FileNotFoundError", lines)

    def test_cli_subcommand_needs_no_skill_and_returns_the_check_code(self):
        buf = io.StringIO()
        with mock.patch.object(se, "builtin_plugin_check", return_value=(1, ["a", "b"])), redirect_stdout(buf):
            self.assertEqual(se.main(["check-builtin-plugins"]), 1)
        self.assertEqual(buf.getvalue(), "a\nb\n")

if __name__ == "__main__":
    unittest.main()
