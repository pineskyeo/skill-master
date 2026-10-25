"""H3: engine/spend.py, the per-workspace spend ledger <workspace>/spend.jsonl.

Every paid call (exec, trigger, grader, pairwise, calibrate, builder) gets an intent row before it starts and a result
row after it; API-key mode admits a call only when spend so far plus its worst-case bound fits the hard cap; a crashed
call's bound counts at the next live start. Lanes 6 and 7 run here as Python calls with a stub CLI that reports
total_cost_usd; nothing real is executed (subprocess is patched for the whole module) and the only key is the
throwaway value of tests/test_auth_profiles.py. This file is ASCII only."""
import json, os, re, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, evals_doc, read, write, wjson, rjson, claude_stream, FakeClaude, FakeGraderExec
from test_auth_profiles import Profiled, KEY, SHA, CLAUDE_REASONS
from engine import approvals as A, spend

TINY = ("--tokens", "exec=100,10", "--tokens", "trigger=100,10")
PRICE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
COMMON = ("v", "type", "call_id", "run_id", "pid", "at", "command", "kind", "engine", "model", "profile", "billing",
          "iteration", "cap_usd", "remaining_usd")
INTENT = COMMON + ("decision", "reason", "spent_before_usd", "call_cap_usd", "bound_usd")
RESULT = COMMON + ("outcome", "reported_cost_usd", "input_tokens", "output_tokens", "computed_cost_usd", "price",
                   "price_source", "price_date", "charged_usd", "charged_source", "spent_after_usd", "overshoot_usd")
RECOVERED = COMMON + ("recovers", "counted_usd")
FIELDS = {"intent": INTENT, "result": RESULT, "recovered": RECOVERED}
# A fake engine with a per-call cap that is not in spend.API_KEY_REFUSED: the ledger's API-key mechanics run on it.
CAPPED = "capped-stub"

_patches = []


def capped_stand_in():
    """Lets the stub Claude stand in for a capped engine in API-key mode (R19-f1 refuses Claude there)."""
    return mock.patch.object(spend, "API_KEY_REFUSED", ())


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


class Crash(BaseException):
    """The harness dies mid-call (a kill, a KeyboardInterrupt): no result row can follow."""


class StubClaude:
    """Stub `claude -p` of lanes 6 and 7: reports total_cost_usd and no usage; crash_on=k raises Crash on call k."""
    def __init__(self, cost=0.02, crash_on=None, api_key_source="ANTHROPIC_API_KEY"):
        self.calls, self.cost, self.crash_on, self.api_key_source = [], cost, crash_on, api_key_source
    def __call__(self, cmd, cwd, timeout, env=None):
        self.calls.append(cmd)
        if self.crash_on == len(self.calls): raise Crash("killed mid-call")
        skills = [] if "--disable-slash-commands" in cmd else ["branch-setup"]
        return 0, claude_stream("feat/x", usage=(0, 0, 0), cost=self.cost, skills=skills,
                                api_key_source=self.api_key_source), ""


class Ledgered(Profiled):
    def setUp(self):
        Profiled.setUp(self)
        self.d = self.approved_skill(evals=evals_doc(human=False))
        self.path = os.path.join(self.ws, "spend.jsonl")

    def rows(self):
        rows, torn = spend.read_rows(self.path)
        self.assertIsNone(torn)
        for r in rows:
            self.assertEqual(tuple(r), FIELDS[r["type"]], r)
        return rows

    def company_run(self, stub, budget="0.05", *extra):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch.object(se, "live_exec", stub):
            return self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "company", "--budget-usd",
                            budget, "--engines", "claude", "--runs", "1", *(TINY + extra))

    def ledger(self, billing="api_key", cap=0.05, command="run", ws=None):
        led = spend.Ledger(ws or self.ws, command, "company" if billing == "api_key" else "personal-mac", billing, cap)
        led.open([])
        self.addCleanup(led.close)
        return led

    def loop_run(self, builder_effect):
        """A mocked `loop --live` under personal-mac: exec, trigger, grader, pairwise, calibrate and one builder call."""
        bp, gp = os.path.join(self.tmp, "builder.md"), os.path.join(self.tmp, "golden.json")
        write(bp, "Fix the skill.")
        wjson(gp, {"examples": [{"prompt": "p", "output": "feat/x", "item": "names a branch", "label": True}]})
        fake, fg = FakeClaude(regress=True), FakeGraderExec()
        def fake_live(cmd, cwd, timeout, env=None):
            if "--json-schema" in cmd: return fg(cmd, cwd, timeout)
            if "--tools" in cmd:
                builder_effect()
                return 0, json.dumps({"type": "result", "total_cost_usd": 0.5, "usage": {}}), ""
            return fake(cmd, cwd, timeout, env)
        with mock.patch.object(se, "live_exec", fake_live):
            rc, out, err = self.cli("loop", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac",
                                    "--budget-usd", "200", "--engines", "claude", "--runs", "1", "--builder-prompt", bp,
                                    "--golden", gp, "--max-iterations", "2", "--run-root", self.runs)
        self.assertIn(rc, (0, 3), err)
        return json.loads(out.strip().splitlines()[-1])


class TestCapAndPairs(Ledgered):
    def test_a_cap_refusal_writes_a_refused_intent_and_nothing_starts(self):
        led = self.ledger()
        t = led.admit("exec", CAPPED, "m", 1, 0.04, 0.02, 0.02, True)
        self.assertEqual([(r["type"], r["decision"], r["reason"]) for r in self.rows()], [("intent", "refused", t.reason)])
        self.assertIn("exceeds the hard cap $0.05", t.reason)
        with self.assertRaises(spend.SpendError): t.launch()
        seen = []
        with mock.patch.dict(se._LIVE, {}), mock.patch.object(subprocess, "run", lambda cmd, **kw: seen.append(cmd)):
            se.enable_live(A.Grant("live", "run", "u", SHA, {}))
            se._LIVE["ledger"] = led
            with self.assertRaises(se.LiveDisabled): se.live_exec([CAPPED, "-p", "x"], self.tmp, 1)
        self.assertEqual(seen, [])

    def test_the_admission_rule_is_explicit(self):
        led = self.ledger()
        admit = lambda spent, cap, has=True, refusal=None: led.admit("exec", CAPPED, "m", 1, spent, cap, cap, has, refusal)
        self.assertEqual(admit(0.03, 0.02).decision, "allowed")  # 0.03 + 0.02 <= 0.05
        t = admit(0.04, 0.02)
        self.assertEqual((t.decision, t.reason), ("refused", "spend so far $0.04 + worst-case bound $0.02 exceeds the "
                                                             "hard cap $0.05"))
        self.assertIn("below $0.01", admit(0.0, 0.005).reason)
        self.assertIn("below $0.01", admit(0.0, None).reason)
        self.assertEqual(admit(0.0, 0.02, refusal="budget exhausted").reason, "budget exhausted")
        self.assertIn("no enforceable per-call cap in API-key mode", admit(0.0, 0.02, has=False).reason)
        sub = self.ledger("subscription", cap=0.05, command="grade", ws=os.path.join(self.tmp, "ws2"))
        self.assertEqual(sub.admit("exec", "claude", "m", 1, 0.04, 0.02, 0.02, True, None).decision, "allowed")
        self.assertEqual(sub.admit("exec", "codex", None, 1, 0.0, None, 0.02, False, "x").reason, "x")
        rows = spend.read_rows(self.path)[0]
        self.assertEqual([r["decision"] for r in rows], ["refused"] * 5)

    def test_lane_6_the_run_is_refused_before_any_call(self):
        stub = StubClaude(cost=0.02)
        rc, out, err = self.company_run(stub)
        self.assertEqual((rc, err.strip()), (2, "error: " + CLAUDE_REASONS["exec"]))
        self.assertEqual(stub.calls, [])
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "iteration-1")))
        self.assertNotIn(KEY, out + err)

    def test_lane_6_mechanics_two_pairs_one_refused_intent_and_actual_spend(self):
        """spend.API_KEY_REFUSED is patched to () so the stub Claude stands in for a capped engine: the run, its rows,
        report and the exit 3 of a stopped API-key run have no route but the CLI."""
        stub = StubClaude(cost=0.02)
        with capped_stand_in():
            rc, out, err = self.company_run(stub)
        self.assertEqual(rc, 3, err)
        self.assertEqual(len(stub.calls), 2)
        rows = self.rows()
        self.assertEqual([(r["type"], r["decision"] if r["type"] == "intent" else r["outcome"]) for r in rows],
                         [("intent", "allowed"), ("result", "ok"), ("intent", "allowed"), ("result", "ok"),
                          ("intent", "refused")])
        run_id = rows[0]["run_id"]
        for i, r in enumerate(rows):
            self.assertEqual((r["v"], r["run_id"], r["command"], r["kind"], r["engine"], r["model"], r["profile"],
                              r["billing"], r["iteration"], r["cap_usd"], r["pid"]),
                             (1, run_id, "run", "exec", "claude", "claude-opus-5-5", "company", "api_key", 1, 0.05,
                              os.getpid()))
        self.assertEqual([r["call_id"] for r in rows], ["%s:%d" % (run_id, k) for k in (1, 1, 2, 2, 3)])
        i1, r1, i2, r2, refused = rows
        self.assertEqual((i1["call_cap_usd"], i1["bound_usd"], i1["spent_before_usd"], i1["remaining_usd"]),
                         (0.02, 0.02, 0.0, 0.05))
        self.assertEqual((i2["call_cap_usd"], i2["spent_before_usd"]), (0.01, 0.02))
        self.assertEqual((r1["reported_cost_usd"], r1["charged_usd"], r1["charged_source"], r1["overshoot_usd"],
                          r1["spent_after_usd"], r1["remaining_usd"]), (0.02, 0.02, "cli_reported", 0.0, 0.02, 0.03))
        self.assertEqual((r2["reported_cost_usd"], r2["overshoot_usd"], r2["spent_after_usd"]), (0.02, 0.01, 0.04))
        self.assertEqual((r1["price"], r1["price_source"], r1["price_date"], r1["computed_cost_usd"],
                          r1["input_tokens"], r1["output_tokens"]), ([4.0, 20.0], PRICE_URL, "2026-10-15", None, 0, 0))
        self.assertIn("overshot its per-call cap", refused["reason"])
        plan = rjson(os.path.join(self.ws, "iteration-1", "plan.json"))
        self.assertEqual(plan["stopped"], refused["reason"])
        self.assertNotIn(KEY, out + err + read(self.path))
        rc, out, _ = self.cli("report", self.d, "--workspace", self.ws)
        md = read(os.path.join(self.ws, "iteration-1", "comparison.md"))
        header = next(x for x in md.splitlines() if x.startswith("- API-equivalent cost: "))
        self.assertTrue(header.endswith("; actual spend: $0.04 (CLI-reported, API-key mode, auth profile company); hard "
                                        "cap: $0.05 (--budget-usd of run)"), header)
        self.assertIn("| actual spend | $0.04 |", md.splitlines())
        self.assertIn("| hard cap | $0.05 (run) |", md.splitlines())
        cost = rjson(os.path.join(self.ws, "iteration-1", "benchmark.json"))["cost"]
        self.assertEqual((cost["billing"], cost["actual_spend_usd"], cost["actual_spend_basis"], cost["unreported_calls"],
                          cost["unreconciled_calls"], cost["caps"]),
                         ("api_key", 0.04, "cli_reported", 0, 0, [{"run_id": run_id, "command": "run", "cap_usd": 0.05}]))

    def test_one_intent_and_result_pair_for_each_paid_call_kind(self):
        self.assertEqual(self.loop_run(lambda: None)["status"], "needs-approval")
        rows = self.rows()
        intents = {r["call_id"]: r for r in rows if r["type"] == "intent"}
        results = [r for r in rows if r["type"] == "result"]
        self.assertTrue(intents and all(r["decision"] == "allowed" for r in intents.values()))
        self.assertEqual(sorted(r["call_id"] for r in results), sorted(intents))
        for r in results:
            self.assertEqual((r["kind"], r["engine"], r["iteration"]), (intents[r["call_id"]]["kind"],
                             intents[r["call_id"]]["engine"], intents[r["call_id"]]["iteration"]))
        self.assertEqual({r["kind"] for r in results}, {"exec", "trigger", "grader", "pairwise", "calibrate", "builder"})
        self.assertEqual({r["iteration"] for r in results if r["kind"] == "builder"}, {1})
        self.assertEqual({(r["profile"], r["billing"]) for r in rows}, {("personal-mac", "subscription")})
        cost = rjson(os.path.join(self.ws, "iteration-1", "benchmark.json"))["cost"]
        self.assertEqual((cost["billing"], cost["actual_spend_usd"]), ("subscription", None))
        self.assertIn("actual spend: not metered (subscription auth)",
                      read(os.path.join(self.ws, "iteration-1", "comparison.md")))


class TestCrashRecovery(Ledgered):
    def test_lane_7_a_crashed_call_counts_once_at_the_next_start(self):
        """spend.API_KEY_REFUSED is patched to () so the stub Claude stands in for a capped engine: recovery at a live
        start has no route but the CLI."""
        pt = capped_stand_in(); pt.start(); self.addCleanup(pt.stop)
        with self.assertRaises(Crash): self.company_run(StubClaude(crash_on=1))
        self.assertFalse(se._LIVE["on"])
        rows = self.rows()
        self.assertEqual([(r["type"], r["decision"]) for r in rows], [("intent", "allowed")])
        crashed = rows[0]
        self.assertEqual([r["call_id"] for r in spend.preview(self.ws)], [crashed["call_id"]])
        line = "spend ledger: 1 dangling intent(s) from crashed calls, $0.02 counted as spent at the next live start"
        rc, out, _ = self.cli("plan", self.d, "--workspace", self.ws, "--auth-profile", "company", "--budget-usd", "0.05")
        self.assertIn(line, out.splitlines())
        self.assertEqual(len(self.rows()), 1)  # plan writes nothing
        shown = []
        T.install_test_approver(self, write=shown.append)
        stub = StubClaude()
        rc, _, err = self.company_run(stub)
        self.assertEqual(rc, 3, err)
        self.assertIn(line, shown)
        second = [r for r in self.rows() if r["run_id"] != crashed["run_id"]]
        rec = second[0]
        self.assertEqual((rec["type"], rec["recovers"], rec["counted_usd"], rec["kind"], rec["engine"], rec["model"],
                          rec["iteration"], rec["command"], rec["profile"], rec["remaining_usd"]),
                         ("recovered", crashed["call_id"], 0.02, "exec", "claude", "claude-opus-5-5", None, "run", "company",
                          0.03))
        self.assertEqual((second[1]["type"], second[1]["spent_before_usd"], second[1]["call_cap_usd"]),
                         ("intent", 0.02, 0.01))
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(spend.preview(self.ws), [])
        stub3 = StubClaude()
        rc, _, err = self.company_run(stub3)
        self.assertEqual(rc, 3, err)
        third = [r for r in self.rows() if r["run_id"] not in (crashed["run_id"], second[0]["run_id"])]
        self.assertEqual([r for r in third if r["type"] == "recovered"], [])
        self.assertEqual((third[0]["type"], third[0]["spent_before_usd"]), ("intent", 0.0))
        self.assertEqual(len(stub3.calls), 2)

    def test_an_exception_between_intent_and_launch_gives_not_launched(self):
        led = self.ledger()
        t = led.admit("grader", CAPPED, "m", 1, 0.0, 0.02, 0.02, True)
        t.launch()
        t.not_launched(0.0)
        t.not_launched(0.0)
        rows = self.rows()
        self.assertEqual([(r["type"], r.get("outcome"), r.get("charged_usd")) for r in rows],
                         [("intent", None, None), ("result", "not_launched", 0.0)])
        self.assertEqual(spend.preview(self.ws), [])


class TestLedgerMechanics(Ledgered):
    def written(self):
        led = self.ledger()
        t = led.admit("exec", CAPPED, "m", 1, 0.0, 0.02, 0.02, True)
        t.launch()
        led.close()
        return t

    def test_a_torn_last_line_is_ignored_and_reported(self):
        t = self.written()
        good = read(self.path)
        for tail in ('{"v": 1, "type": "res', json.dumps({"v": 1})):
            write(self.path, good + tail)
            rows, torn = spend.read_rows(self.path)
            self.assertEqual(len(rows), 1); self.assertIn("line 2", torn)
            self.assertEqual([r["call_id"] for r in spend.preview(self.ws)], [t.call_id])
        led = spend.Ledger(self.ws, "run", "company", "api_key", 0.05)
        self.assertEqual(led.open([t.call_id]), 0.02)
        led.close()
        rows, torn = spend.read_rows(self.path)
        self.assertIsNone(torn)
        self.assertEqual([r["type"] for r in rows], ["intent", "recovered"])

    def test_an_invalid_row_elsewhere_fails_closed(self):
        self.written()
        good = read(self.path)
        for bad, needle in (("not json\n", "line 1"), (json.dumps({"v": 1, "type": "intent"}) + "\n", "line 1")):
            write(self.path, bad + good)
            with self.assertRaises(spend.SpendError) as cm: spend.read_rows(self.path)
            self.assertIn(needle, str(cm.exception))
        rc, _, err = self.cli("plan", self.d, "--workspace", self.ws, "--auth-profile", "company")
        self.assertEqual(rc, 2); self.assertIn("spend.jsonl line 1", err)

    def test_each_row_is_one_write_and_one_fsync(self):
        fresh = os.path.join(self.tmp, "fresh-ws")
        real_fsync, real_write = os.fsync, os.write
        with mock.patch.object(spend.os, "fsync", side_effect=real_fsync) as fs, \
                mock.patch.object(spend.os, "write", side_effect=real_write) as wr:
            led = spend.Ledger(fresh, "run", "company", "api_key", 0.05)
            led.open([])
            self.addCleanup(led.close)
            self.assertEqual(fs.call_count, 1)  # the new file's folder
            fs.reset_mock(); wr.reset_mock()
            t = led.admit("exec", CAPPED, "m", 1, 0.0, 0.02, 0.02, True)
            t.launch()
            t.result("ok", 0.01, 0, 0, None, None, None, None, 0.01, "cli_reported", 0.01)
            led.admit("exec", CAPPED, "m", 1, 0.06, 0.02, 0.02, True)
        self.assertEqual((fs.call_count, wr.call_count), (3, 3))
        self.assertEqual(oct(os.stat(os.path.join(fresh, "spend.jsonl")).st_mode & 0o777), "0o600")

    def test_a_second_live_command_on_the_ledger_is_refused(self):
        """spend.API_KEY_REFUSED is patched to () for the CLI start so the stub Claude stands in for a capped engine: the
        lock refusal at a live start has no route but the CLI."""
        led = self.ledger()
        other = spend.Ledger(self.ws, "run", "company", "api_key", 0.05)
        with self.assertRaises(spend.SpendError) as cm: other.open([])
        self.assertEqual(str(cm.exception), "another live command is using %s" % self.path)
        stub = StubClaude()
        with capped_stand_in():
            rc, _, err = self.company_run(stub)
        self.assertEqual(rc, 2); self.assertIn("another live command is using %s" % os.path.realpath(self.path), err)
        self.assertEqual(stub.calls, [])
        self.assertFalse(se._LIVE["on"])
        led.close()
        with self.assertRaises(spend.SpendError) as cm: other.open(["%s:1" % SHA])
        self.assertIn("changed since the preview", str(cm.exception))

    def test_live_exec_refuses_without_an_open_intent(self):
        led = self.ledger()
        seen = []
        def fake_run(cmd, **kw):
            seen.append(cmd); return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.dict(se._LIVE, {}), mock.patch.object(subprocess, "run", fake_run):
            se.enable_live(A.Grant("live", "run", "u", SHA, {}))
            se._LIVE["ledger"] = led
            with self.assertRaises(se.LiveDisabled) as cm: se.live_exec(["claude", "-p", "x"], self.tmp, 1)
            self.assertIn("no open spend intent", str(cm.exception))
            led.admit("exec", CAPPED, "m", 1, 0.0, 0.02, 0.02, True).launch()
            se.live_exec(["claude", "-p", "x"], self.tmp, 1)
            with self.assertRaises(se.LiveDisabled): se.live_exec(["claude", "-p", "x"], self.tmp, 1)
            se._LIVE["ledger"] = None
            with self.assertRaises(se.LiveDisabled): se.live_exec(["claude", "-p", "x"], self.tmp, 1)
        self.assertEqual(len(seen), 1)


class TestBuilderBracket(Ledgered):
    def test_ledger_rows_of_a_builder_call_are_harness_writes(self):
        r = self.loop_run(lambda: None)
        self.assertEqual(r["status"], "needs-approval")
        self.assertTrue(any(x["kind"] == "builder" for x in self.rows()))

    def test_a_builder_write_to_the_ledger_is_caught(self):
        def append():
            last = read(self.path).splitlines()[-1]
            with open(self.path, "a") as f: f.write(last + "\n")
        r = self.loop_run(append)
        self.assertEqual(r["status"], "builder-wrote-outside")
        self.assertEqual([p.rsplit("/", 1)[-1] for p in r["problems"]], ["spend.jsonl"])


class TestReport(Ledgered):
    def test_cost_summary_counts_reported_costs_and_unreconciled_intents(self):
        it_dir = os.path.join(self.ws, "iteration-1"); os.makedirs(it_dir)
        co = self.ledger()
        t = co.admit("exec", CAPPED, "m", 1, 0.0, 0.02, 0.02, True); t.launch()
        t.result("ok", 0.015, 100, 10, 0.0006, (4.0, 20.0), PRICE_URL, "2026-10-15", 0.015, "cli_reported", 0.015)
        t = co.admit("builder", CAPPED, "m", 1, 0.015, 0.01, 0.01, True); t.launch()
        co.admit("grader", CAPPED, "m", 1, 0.0156, 0.01, 0.01, True).launch()
        co.admit("exec", CAPPED, "m", 2, 0.0, 0.02, 0.02, True).launch()
        t.result("ok", None, 100, 10, 0.0006, (4.0, 20.0), PRICE_URL, "2026-10-15", 0.0006, "tokens_x_price", 0.0156)
        co.close()
        c = se.cost_summary(it_dir)
        self.assertEqual((c["billing"], c["actual_spend_usd"], c["unreported_calls"], c["unreconciled_calls"],
                          c["unreconciled_bound_usd"], c["auth_profile"]), ("api_key", 0.015, 1, 1, 0.01, "company"))
        sub = spend.Ledger(self.ws, "grade", "personal-mac", "subscription", 5.0); sub.open(sorted(
            r["call_id"] for r in spend.preview(self.ws)))
        t = sub.admit("grader", "claude", "m", 1, 0.0, 2.5, 2.5, True); t.launch()
        t.result("ok", 0.05, 100, 10, 0.0003, (2.0, 10.0), PRICE_URL, "2026-10-15", 0.05, "cli_reported", 0.05)
        sub.close()
        c = se.cost_summary(it_dir)
        self.assertEqual((c["billing"], c["actual_spend_usd"]), ("mixed", 0.015))
        self.assertIn("API-key", c["actual_spend_note"])
        self.assertEqual([x["command"] for x in c["caps"]], ["run"])

    def test_actual_spend_stays_null_under_subscription(self):
        it_dir = os.path.join(self.ws, "iteration-1"); os.makedirs(it_dir)
        a5 = se.cost_summary(it_dir)
        sub = self.ledger("subscription", 5.0)
        t = sub.admit("exec", "claude", "m", 1, 0.0, 2.5, 2.5, True); t.launch()
        t.result("ok", 0.05, 100, 10, 0.0006, (4.0, 20.0), PRICE_URL, "2026-10-15", 0.05, "cli_reported", 0.05)
        c = se.cost_summary(it_dir)
        self.assertEqual(c, a5)
        self.assertEqual((c["billing"], c["actual_spend_usd"]), ("subscription", None))

    def test_a_company_iteration_with_only_a_dangling_intent_reports_api_key(self):
        """spend.API_KEY_REFUSED is patched to () so the stub Claude stands in for a capped engine: a company iteration
        to report on has no route but the CLI."""
        with capped_stand_in(), self.assertRaises(Crash): self.company_run(StubClaude(crash_on=1))
        intent = self.rows()[0]
        c = se.cost_summary(os.path.join(self.ws, "iteration-1"), self.ws)
        self.assertEqual((c["billing"], c["actual_spend_usd"], c["actual_spend_basis"], c["unreported_calls"],
                          c["unreconciled_calls"], c["unreconciled_bound_usd"], c["caps"], c["auth_profile"]),
                         ("api_key", 0.0, "cli_reported", 0, 1, 0.02,
                          [{"run_id": intent["run_id"], "command": "run", "cap_usd": 0.05}], "company"))


class UsageOnlyClaude(StubClaude):
    """Stub `claude -p` that reports usage tokens and no total_cost_usd (null)."""
    def __init__(self, tin):
        StubClaude.__init__(self, cost=None)
        self.tin = tin
    def __call__(self, cmd, cwd, timeout, env=None):
        self.calls.append(cmd)
        skills = [] if "--disable-slash-commands" in cmd else ["branch-setup"]
        return 0, claude_stream("feat/x", usage=(self.tin, 0, 0), cost=None, skills=skills,
                                api_key_source="ANTHROPIC_API_KEY"), ""


UNREPORTED = "reported no valid total_cost_usd in API-key mode"


class TestUnreportedCost(Ledgered):
    """The CLI cases patch spend.API_KEY_REFUSED to () so the stub Claude stands in for a capped engine: the charge of an
    unreported call in a run and the exit 3 of the stopped run have no route but the CLI."""
    def company_run(self, *a):
        with capped_stand_in():
            return Ledgered.company_run(self, *a)

    def test_an_unreported_call_above_its_cap_halts_before_the_hard_cap_is_passed(self):
        stub = UsageOnlyClaude(7500)  # $0.03 at the shipped price, above the $0.02 per-call cap
        rc, _, err = self.company_run(stub)
        self.assertEqual(rc, 3, err)
        self.assertEqual(len(stub.calls), 1)
        rows = self.rows()
        self.assertEqual([(r["type"], r["decision"] if r["type"] == "intent" else r["outcome"]) for r in rows],
                         [("intent", "allowed"), ("result", "ok"), ("intent", "refused")])
        res, refused = rows[1], rows[2]
        self.assertEqual((res["reported_cost_usd"], res["computed_cost_usd"], res["charged_usd"], res["spent_after_usd"]),
                         (None, 0.03, 0.03, 0.03))
        self.assertIn(UNREPORTED, refused["reason"])
        self.assertLessEqual(refused["spent_before_usd"], 0.05)

    def test_an_unreported_call_below_its_cap_is_charged_the_cap_and_halts(self):
        stub = UsageOnlyClaude(5000)
        rc, _, err = self.company_run(stub, "0.05", "--price", "claude=0.0001,0.0001")
        self.assertEqual(rc, 3, err)
        self.assertEqual(len(stub.calls), 1)
        rows = self.rows()
        res, refused = rows[1], rows[2]
        self.assertEqual((res["reported_cost_usd"], res["charged_usd"], res["charged_source"], res["spent_after_usd"],
                          res["remaining_usd"]), (None, 0.02, "cap_charged_unreported", 0.02, 0.03))
        self.assertEqual((refused["decision"], refused["spent_before_usd"]), ("refused", 0.02))
        self.assertIn(UNREPORTED, refused["reason"])

    def test_every_invalid_reported_cost_is_charged_the_call_cap_and_halts(self):
        for i, rep in enumerate((None, -0.01, float("nan"), float("inf"), "0.02", True)):
            led = self.ledger(ws=os.path.join(self.tmp, "ws-%d" % i))
            t = led.admit("grader", CAPPED, "m", 1, 0.0, 0.02, 0.02, True); t.launch()
            t.result("ok", rep, 100, 10, 0.0006, (4.0, 20.0), PRICE_URL, "2026-10-15", 0.0006, "tokens_x_price", 0.0006)
            row = spend.read_rows(led.path)[0][-1]
            self.assertEqual((row["reported_cost_usd"], row["charged_usd"], row["charged_source"], row["spent_after_usd"]),
                             (None, 0.02, "cap_charged_unreported", 0.02), rep)
            self.assertIn(UNREPORTED, led.halt or "", rep)
            for kind in spend.KINDS:
                self.assertEqual(led.admit(kind, CAPPED, "m", 1, 0.02, 0.01, 0.01, True).reason, led.halt)

    def test_the_budget_is_charged_the_cap_of_an_unreported_call(self):
        self.activate("company")
        b = se.Budget(1.0)
        b.ledger = spend.Ledger(self.ws, "grade", "company", "api_key", 1.0); b.ledger.open([])
        self.addCleanup(b.ledger.close)
        fg = FakeGraderExec(report_cost=False)
        g = se.LLMGrader("claude-sonnet-5-5", exec_fn=fg, budget=b)
        import test_round10
        with self.assertRaises(spend.SpendError) as cm:
            g.grade(test_round10.CASE, {"final": "feat/x", "tool_calls": []}, test_round10.ITEMS)
        self.assertEqual((str(cm.exception), fg.calls, self.rows()), (CLAUDE_REASONS["grader"], [], []))
        t = b.ledger.admit("grader", CAPPED, "m", 1, b.spent, 0.5, 0.5, True); t.launch()
        b.charge(0.0003)
        se.spend_settle(t, b, "ok", None, 100, 10, ((2.0, 10.0), PRICE_URL, "2026-10-15"), 0.0003, "tokens_x_price")
        self.assertEqual(round(b.spent, 6), 0.5)
        self.assertIn(UNREPORTED, b.ledger.halt)

    def test_subscription_and_not_launched_results_are_unchanged(self):
        sub = self.ledger("subscription", 5.0)
        t = sub.admit("exec", "claude", "m", 1, 0.0, 2.5, 2.5, True); t.launch()
        t.result("ok", None, 100, 10, 0.0006, (4.0, 20.0), PRICE_URL, "2026-10-15", 0.0006, "tokens_x_price", 0.0006)
        self.assertEqual((sub.halt, spend.read_rows(sub.path)[0][-1]["charged_usd"]), (None, 0.0006))
        led = self.ledger(ws=os.path.join(self.tmp, "ws-nl"))
        t = led.admit("exec", CAPPED, "m", 1, 0.0, 0.02, 0.02, True); t.launch()
        t.not_launched(0.0)
        self.assertEqual((led.halt, spend.read_rows(led.path)[0][-1]["charged_usd"]), (None, 0.0))


class TestCompanyAdmission(Ledgered):
    def company_budget(self, command, cap):
        self.activate("company")
        b = se.Budget(cap)
        b.ledger = spend.Ledger(self.ws, command, "company", "api_key", cap); b.ledger.open([])
        self.addCleanup(b.ledger.close)
        return b

    def test_an_exact_fit_is_admitted_and_one_millionth_over_is_refused(self):
        led = self.ledger(cap=0.05)
        self.assertEqual(led.admit("exec", CAPPED, "m", 1, 0.04, 0.01, 0.01, True).decision, "allowed")
        t = led.admit("exec", CAPPED, "m", 1, 0.04, 0.010001, 0.010001, True)
        self.assertEqual((t.decision, t.reason), ("refused", "spend so far $0.04 + worst-case bound $0.010001 exceeds "
                                                             "the hard cap $0.05"))

    def test_a_result_row_follows_a_paid_call_whose_run_cannot_be_recorded(self):
        def boom(*a, **k): raise OSError("disk full")
        with mock.patch.object(se, "write_run", boom), mock.patch.object(se, "live_exec", StubClaude(api_key_source="none")):
            self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd",
                     "0.05", "--engines", "claude", "--runs", "1", *TINY)
        rows = self.rows()
        self.assertEqual([(r["type"], r["decision"] if r["type"] == "intent" else r["outcome"]) for r in rows],
                         [("intent", "allowed"), ("result", "ok")])
        self.assertEqual(spend.preview(self.ws), [])

    def test_a_budget_without_a_ledger_refuses_every_call_kind_under_api_key(self):
        self.activate("company")
        for kind in spend.KINDS:
            self.assertEqual(se.spend_admit(se.Budget(1.0), kind, "claude", "m", 1, 0.5, 0.5, True, None),
                             (None, se.NEEDS_LEDGER), kind)
        fc = StubClaude()
        n = se.run_iteration(se.load_eval_set(self.d)[0], self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=fc)],
                             runs=1, budget=se.Budget(1.0), log=T.quiet)
        self.assertEqual(fc.calls, [])
        self.assertEqual(rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"], se.NEEDS_LEDGER)
        fg = FakeGraderExec()
        import test_round10
        with self.assertRaises(se.EvalError) as cm:
            se.LLMGrader("claude-sonnet-5-5", exec_fn=fg, budget=se.Budget(1.0)).grade(
                test_round10.CASE, {"final": "x", "tool_calls": []}, test_round10.ITEMS)
        self.assertEqual((str(cm.exception), fg.calls), (se.NEEDS_LEDGER, []))
        calls = []
        bld = se.ClaudeBuilder("Fix it.", "claude-sonnet-5-5", se.Budget(1.0), {}, skill_name="branch-setup",
                               exec_fn=lambda *a, **k: calls.append(a) or (0, "{}", ""))
        with self.assertRaises(se.EvalError) as cm: bld(os.path.join(self.ws, "iteration-%d" % n), self.d)
        self.assertEqual((str(cm.exception), calls), (se.NEEDS_LEDGER, []))
        self.assertFalse(os.path.exists(self.path))

    def test_a_company_builder_call_is_refused_before_it_starts(self):
        b = self.company_budget("loop", 1.0)
        calls = []
        it = os.path.join(self.ws, "iteration-1"); os.makedirs(it)
        bld = se.ClaudeBuilder("Fix it.", "claude-sonnet-5-5", b, {}, skill_name="branch-setup",
                               exec_fn=lambda *a, **k: calls.append(a) or (0, "{}", ""))
        with self.assertRaises(spend.SpendError) as cm: bld(it, self.d)
        self.assertEqual((str(cm.exception), calls, self.rows(), b.spent), (CLAUDE_REASONS["builder"], [], [], 0.0))

    def test_an_overshoot_is_recorded_and_halts_the_command(self):
        led = self.ledger(cap=1.0, command="loop")
        t = led.admit("builder", CAPPED, "m", 1, 0.0, 0.5, 0.5, True); t.launch()
        t.result("ok", 0.6, 0, 0, None, None, None, None, 0.6, "cli_reported", 0.6)
        self.assertIn("overshot its per-call cap $0.50 by $0.10", led.halt)
        refused = led.admit("builder", CAPPED, "m", 1, 0.6, 0.2, 0.2, True)
        rows = self.rows()
        self.assertEqual([(r["type"], r["kind"], r["profile"], r["billing"]) for r in rows],
                         [(t, "builder", "company", "api_key") for t in ("intent", "result", "intent")])
        self.assertEqual(rows[0]["call_id"], rows[1]["call_id"])
        self.assertEqual((rows[0]["call_cap_usd"], rows[1]["reported_cost_usd"], rows[1]["overshoot_usd"]), (0.5, 0.6, 0.1))
        self.assertEqual((rows[2]["decision"], rows[2]["reason"]), ("refused", led.halt))
        self.assertEqual(refused.reason, led.halt)

    def test_a_company_grade_is_refused_before_any_grader_call(self):
        se.run_iteration(se.load_eval_set(self.d)[0], self.ws,
                         [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude())], runs=1, log=T.quiet)
        fg = FakeGraderExec(cost=0.01)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), \
                mock.patch.object(se, "live_exec", lambda cmd, cwd, timeout, env=None: fg(cmd, cwd, timeout)):
            rc, _, err = self.cli("grade", self.d, "--workspace", self.ws, "--live", "--auth-profile", "company",
                                  "--budget-usd", "5", "--tokens", "grader=100,10", "--tokens", "pairwise=100,10")
        self.assertEqual((rc, err.strip()), (2, "error: " + CLAUDE_REASONS["grader"]))
        self.assertEqual(fg.calls, [])
        self.assertFalse(os.path.exists(self.path))


GRADER_TINY = ("--tokens", "grader=100,10", "--tokens", "pairwise=100,10")


def halting_cost(cmd, how):
    """The total_cost_usd a halting call reports: one cent above its --max-budget-usd ("over") or none ("unreported")."""
    return None if how == "unreported" else float(cmd[cmd.index("--max-budget-usd") + 1]) + 0.01


class LastCallHalts(StubClaude):
    """Stub `claude -p` that reports `cost` and 100 input tokens per call, except call number `last`, which halts the
    command by halting_cost."""
    def __init__(self, last, how, cost=0.001):
        StubClaude.__init__(self, cost=cost)
        self.last, self.how = last, how
    def __call__(self, cmd, cwd, timeout, env=None):
        self.calls.append(cmd)
        cost = halting_cost(cmd, self.how) if len(self.calls) == self.last else self.cost
        skills = [] if "--disable-slash-commands" in cmd else ["branch-setup"]
        return 0, claude_stream("feat/x", usage=(100, 0, 0), cost=cost, skills=skills,
                                api_key_source="ANTHROPIC_API_KEY"), ""


class GraderLastCallHalts:
    """FakeGraderExec whose call number `last` halts the command by halting_cost."""
    def __init__(self, last, how):
        self.fake, self.last, self.how = FakeGraderExec(), last, how
    def __call__(self, cmd, cwd, timeout, env=None):
        rc, out, err = self.fake(cmd, cwd, timeout)
        if len(self.fake.calls) != self.last: return rc, out, err
        data = json.loads(out)
        data["total_cost_usd"] = halting_cost(cmd, self.how)
        if data["total_cost_usd"] is None: del data["total_cost_usd"]
        return rc, json.dumps(data), err


class CompanyClaude(FakeClaude):
    """FakeClaude whose init event names the listed key variable as apiKeySource, as a company run requires."""
    def __call__(self, cmd, cwd, timeout, env=None):
        rc, out, err = FakeClaude.__call__(self, cmd, cwd, timeout, env)
        return rc, out.replace('"apiKeySource": "none"', '"apiKeySource": "ANTHROPIC_API_KEY"'), err


def overshoot_halt(intent, result):
    return "stopped: call %s overshot its per-call cap $%s by $%s (reported $%s)" % (
        result["call_id"], spend.money(intent["call_cap_usd"]), spend.money(result["overshoot_usd"]),
        spend.money(result["reported_cost_usd"]))


def unreported_halt(intent, result):
    return ("stopped: call %s reported no valid total_cost_usd in API-key mode (its real cost is unknown); charged $%s, "
            "at least its per-call cap $%s" % (result["call_id"], spend.money(result["charged_usd"]),
                                                spend.money(intent["call_cap_usd"])))


class TestLastCallHalt(Ledgered):
    """R17-f1: a halt set by the last paid call of a command stops that command. spend.API_KEY_REFUSED is patched to ()
    so the stub Claude stands in for a capped engine: each command's stop and exit status have no route but the CLI."""
    def setUp(self):
        Ledgered.setUp(self)
        pt = capped_stand_in(); pt.start(); self.addCleanup(pt.stop)

    def company_cli(self, live, cmd, *extra):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch.object(se, "live_exec", live):
            return self.cli(cmd, self.d, "--workspace", self.ws, "--live", "--auth-profile", "company", *extra)

    def last_pair(self):
        """The intent and result rows of the last call; no row follows its result."""
        rows = self.rows()
        intent, result = rows[-2], rows[-1]
        self.assertEqual((intent["type"], intent["decision"], result["type"], result["call_id"]),
                         ("intent", "allowed", "result", intent["call_id"]))
        return intent, result

    def subscription_iteration(self):
        """Iteration 1 of the approved skill, run without a profile; returns its grader plus pairwise call count."""
        se.run_iteration(se.load_eval_set(self.d)[0], self.ws,
                         [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude())], runs=1, log=T.quiet)
        return sum(se.grade_counts(se.load_eval_set(self.d)[0], os.path.join(self.ws, "iteration-1")))

    def test_f1_run_only_exec_call_overshoot_stops_the_iteration(self):
        self.activate("company")
        b = se.Budget(0.05)
        b.ledger = spend.Ledger(self.ws, "run", "company", "api_key", 0.05); b.ledger.open([])
        self.addCleanup(b.ledger.close)
        spec = se.load_eval_set(self.d)[0]
        spec["cases"], spec["triggers"] = spec["cases"][:1], []
        stub, logged = LastCallHalts(2, "over"), []
        n = se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=stub)], runs=1, budget=b,
                             log=logged.append)
        self.assertEqual(len(stub.calls), 2)
        intent, result = self.last_pair()
        self.assertEqual([r["type"] for r in self.rows()], ["intent", "result", "intent", "result"])
        self.assertEqual((result["kind"], result["reported_cost_usd"], result["charged_usd"], result["overshoot_usd"]),
                         ("exec", round(intent["call_cap_usd"] + 0.01, 6), round(intent["call_cap_usd"] + 0.01, 6), 0.01))
        halt = overshoot_halt(intent, result)
        self.assertEqual(rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"], halt)
        self.assertIn("STOPPED: " + halt, logged)
        _, gate = se.report_iteration(spec, self.ws, n)
        self.assertIn("incomplete: run stopped early (%s)" % halt, gate["problems"])

    def test_f1_run_last_trigger_call_unreported_exits_3(self):
        stub = LastCallHalts(2 * 2 + 20, "unreported")  # two cases x two configurations, then 20 trigger queries
        rc, out, err = self.company_run(stub, "1")
        self.assertEqual(len(stub.calls), stub.last)
        intent, result = self.last_pair()
        self.assertEqual((result["kind"], result["reported_cost_usd"], result["charged_source"], result["charged_usd"]),
                         ("trigger", None, "cap_charged_unreported", intent["call_cap_usd"]))
        halt = unreported_halt(intent, result)
        self.assertEqual(rjson(os.path.join(self.ws, "iteration-1", "plan.json"))["stopped"], halt)
        self.assertIn("STOPPED: " + halt, out.splitlines())
        self.assertEqual(rc, 3, err)
        rc, out, _ = self.cli("report", self.d, "--workspace", self.ws)
        self.assertIn("incomplete: run stopped early (%s)" % halt, out)

    def test_f1_grade_last_pairwise_call_unreported_exits_2(self):
        fg = GraderLastCallHalts(self.subscription_iteration(), "unreported")
        rc, out, err = self.company_cli(fg, "grade", "--budget-usd", "5", "--engines", "claude", *GRADER_TINY)
        self.assertEqual(len(fg.fake.calls), fg.last)
        intent, result = self.last_pair()
        self.assertEqual((result["kind"], result["reported_cost_usd"], result["charged_source"], result["charged_usd"]),
                         ("pairwise", None, "cap_charged_unreported", intent["call_cap_usd"]))
        self.assertEqual(rc, 2, err)
        self.assertIn("error: " + unreported_halt(intent, result), err)
        self.assertNotIn("graded iteration-", out)

    def test_f1_calibrate_last_call_overshoot_exits_2_without_calibration_json(self):
        self.subscription_iteration()
        gp = os.path.join(self.tmp, "golden.json")
        wjson(gp, {"examples": [{"prompt": "p", "output": "feat/x", "item": "names a branch", "label": True}] * 2})
        fg = GraderLastCallHalts(2, "over")
        rc, out, err = self.company_cli(fg, "calibrate", "--budget-usd", "5", "--engines", "claude", "--golden", gp)
        self.assertEqual(len(fg.fake.calls), fg.last)
        intent, result = self.last_pair()
        self.assertEqual((result["kind"], result["charged_usd"], result["overshoot_usd"]),
                         ("calibrate", round(intent["call_cap_usd"] + 0.01, 6), 0.01))
        self.assertEqual(rc, 2, err)
        self.assertIn("error: " + overshoot_halt(intent, result), err)
        self.assertNotIn("agreement", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "iteration-1", "calibration.json")))

    def test_f1_loop_last_pairwise_call_overshoot_is_not_clean(self):
        exe, fg = CompanyClaude(), GraderLastCallHalts(2 + 2, "over")  # two grader calls, then two pairwise calls
        def fake_live(cmd, cwd, timeout, env=None):
            return fg(cmd, cwd, timeout) if "--json-schema" in cmd else exe(cmd, cwd, timeout, env)
        rc, out, err = self.company_cli(fake_live, "loop", "--budget-usd", "20", "--engines", "claude", "--runs", "1",
                                        "--max-iterations", "1", "--run-root", self.runs, *(TINY + GRADER_TINY))
        self.assertEqual(len(fg.fake.calls), fg.last, err)
        intent, result = self.last_pair()
        self.assertEqual((result["kind"], result["overshoot_usd"]), ("pairwise", 0.01))
        self.assertEqual(json.loads(out.strip().splitlines()[-1]),
                         {"status": "run-stopped", "iteration": 1, "error": overshoot_halt(intent, result)})
        self.assertEqual(rc, 3, err)


class TestRecoveredAndDryRun(Ledgered):
    """spend.API_KEY_REFUSED is patched to () so the stub Claude stands in for a capped engine: recovery at a live start
    and the dry-run remainder line have no route but the CLI."""
    def setUp(self):
        Ledgered.setUp(self)
        pt = capped_stand_in(); pt.start(); self.addCleanup(pt.stop)

    def test_f2_a_recovered_crash_is_no_longer_unreconciled(self):
        with self.assertRaises(Crash): self.company_run(StubClaude(crash_on=1))
        self.company_run(StubClaude())
        self.assertEqual(len([r for r in self.rows() if r["type"] == "recovered"]), 1)
        c = se.cost_summary(os.path.join(self.ws, "iteration-1"), self.ws)
        self.assertEqual((c["billing"], c["unreconciled_calls"], c["unreconciled_bound_usd"]), ("api_key", 0, 0.0))

    def dry_run(self, budget):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}):
            rc, out, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--dry-run", "--auth-profile",
                                    "company", "--budget-usd", budget, "--engines", "claude", "--runs", "1", *TINY)
        return rc, next(x for x in out.splitlines() if x.startswith("budget: API-equivalent estimate "))

    def test_f4_the_dry_run_budget_line_compares_with_the_remainder(self):
        _, line = self.dry_run("1")
        est = re.match(r"budget: API-equivalent estimate \$([0-9.]+) ", line).group(1)
        self.assertEqual(line, "budget: API-equivalent estimate $%s vs --budget-usd $1.00 (fits)" % est)
        with self.assertRaises(Crash): self.company_run(StubClaude(crash_on=1), "1")
        bound = spend.preview(self.ws)[0]["bound_usd"]
        self.assertLessEqual(float(est), bound)
        rc, line = self.dry_run(repr(bound))
        self.assertEqual(line, "budget: API-equivalent estimate $%s vs $0.00 left of --budget-usd $%.2f after the "
                               "dangling intents (EXCEEDS)" % (est, bound))
        self.assertEqual(rc, 3)


BAD_BOUND = "intent row: bound_usd must be null or a finite number >= 0"


class TestMalformedRows(Ledgered):
    """R18-f2: a ledger row whose summed amount is negative, not finite or not a number, or whose id or decision is
    malformed, fails closed: nothing is signed or started and no remainder above --budget-usd is ever used."""
    def plant_dangling_bound(self, value, n):
        led = self.ledger(ws=os.path.join(self.tmp, "src-%d" % n))
        led.admit("exec", CAPPED, "claude-opus-5-5", 1, 0.0, 0.02, 0.02, True).launch()
        led.close()
        row = spend.read_rows(led.path)[0][0]
        row["bound_usd"] = value
        write(self.path, json.dumps(row) + "\n")
        with open(self.path, "rb") as f: return f.read()

    def assert_start_refused(self, value, n=0):
        planted = self.plant_dangling_bound(value, n)
        shown, lefts = [], []
        T.install_test_approver(self, write=shown.append)
        real = se.check_live_gates
        def gates(*a, **kw):
            lefts.append(a[8]); return real(*a, **kw)
        stub = StubClaude()
        with mock.patch.object(se, "check_live_gates", gates):
            rc, _, err = self.company_run(stub)
        self.assertEqual(lefts, [], "a budget remainder was computed from the planted bound")
        self.assertEqual((stub.calls, shown), ([], []))
        self.assertEqual((rc, err.strip()), (2, "error: %s line 1: %s" % (os.path.realpath(self.path), BAD_BOUND)))
        with open(self.path, "rb") as f: self.assertEqual(f.read(), planted)
        with self.assertRaises(spend.SpendError) as cm: spend.preview(self.ws)
        self.assertEqual(str(cm.exception), "%s line 1: %s" % (self.path, BAD_BOUND))

    def test_f2_bound_negative(self):
        self.assert_start_refused(-1.0)

    def test_f2_bound_nonfinite(self):
        for n, value in enumerate((float("nan"), float("inf"))):
            with self.subTest(value=value):
                self.assert_start_refused(value, n)

    def test_f2_bound_not_number(self):
        for n, value in enumerate(("0.02", True)):
            with self.subTest(value=value):
                self.assert_start_refused(value, n)

    def valid_rows(self):
        led = self.ledger()
        t = led.admit("exec", CAPPED, "m", 1, 0.0, 0.02, 0.02, True); t.launch()
        t.result("ok", 0.01, 0, 0, None, None, None, None, 0.01, "cli_reported", 0.01)
        led.admit("exec", CAPPED, "m", 1, 0.01, 0.02, 0.02, True).launch()
        led.close()
        rec = spend.Ledger(self.ws, "run", "company", "api_key", 0.05)
        rec.open([r["call_id"] for r in spend.preview(self.ws)]); rec.close()
        rows = self.rows()
        self.assertEqual([r["type"] for r in rows], ["intent", "result", "intent", "recovered"])
        return rows

    def assert_row_refused(self, line, field, value, problem):
        rows = self.valid_rows()
        rows[line - 1][field] = value
        write(self.path, "".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaises(spend.SpendError) as cm: spend.read_rows(self.path)
        self.assertEqual(str(cm.exception), "%s line %d: %s" % (self.path, line, problem))

    def test_f2_other_field_counted_usd_negative(self):
        self.assert_row_refused(4, "counted_usd", -0.02, "recovered row: counted_usd must be null or a finite number >= 0")

    def test_f2_other_field_reported_cost_negative(self):
        self.assert_row_refused(2, "reported_cost_usd", -0.01,
                                "result row: reported_cost_usd must be null or a finite number >= 0")

    def test_f2_other_field_call_id_not_a_string(self):
        self.assert_row_refused(1, "call_id", 7, "intent row: call_id must be a non-empty string")

    def test_f2_other_field_decision_unknown(self):
        self.assert_row_refused(3, "decision", "maybe", 'intent row: decision must be "allowed" or "refused"')

    def test_f2_pin_negative_remaining_after_an_overshoot_reads(self):
        led = self.ledger(cap=0.05)
        t = led.admit("exec", CAPPED, "m", 1, 0.0, 0.02, 0.02, True); t.launch()
        t.result("ok", 0.07, 0, 0, None, None, None, None, 0.07, "cli_reported", 0.07)
        led.close()
        rows = self.rows()
        self.assertEqual((rows[1]["remaining_usd"], rows[1]["overshoot_usd"]), (-0.02, 0.05))
        self.assertEqual(spend.preview(self.ws), [])


class TestClaudeRefusedInApiKeyMode(Ledgered):
    """R19-f1 option (c): under API-key billing the ledger refuses every Claude call kind before it writes any row."""
    def test_c4_guard_admit(self):
        led = self.ledger()
        b = se.Budget(0.05); b.ledger = led
        for kind in spend.KINDS:
            with self.assertRaises(spend.SpendError, msg=kind) as cm:
                led.admit(kind, "claude", "m", 1, 0.0, 0.02, 0.02, True)
            self.assertEqual(str(cm.exception), CLAUDE_REASONS[kind])
            with self.assertRaises(spend.SpendError, msg=kind) as cm:
                se.spend_admit(b, kind, "claude", "m", 1, 0.02, 0.02, True, None)
            self.assertEqual(str(cm.exception), CLAUDE_REASONS[kind])
        self.assertEqual(spend.read_rows(self.path), ([], None))
        t = led.admit("exec", "capped-stub", "m", 1, 0.0, 0.02, 0.02, True)
        self.assertEqual((t.decision, t.call_id), ("allowed", led.run_id + ":1"))

    def test_c4_pin_personal_cap(self):
        stub = StubClaude(api_key_source="none")
        with mock.patch.object(se, "live_exec", stub):
            rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac",
                                  "--budget-usd", "0.05", "--engines", "claude", "--runs", "1", *TINY)
        self.assertEqual(rc, 0, err)
        self.assertTrue(stub.calls)
        self.assertEqual(stub.calls[0][stub.calls[0].index("--max-budget-usd") + 1], "0.02")
        self.assertTrue(all("--max-budget-usd" in c for c in stub.calls))
        self.assertIn("budget exhausted", rjson(os.path.join(self.ws, "iteration-1", "plan.json"))["stopped"])
        se.AUTH["profile"] = None
        stub = StubClaude(api_key_source="none")
        spec = se.load_eval_set(self.d)[0]
        spec["cases"], spec["triggers"] = spec["cases"][:1], []
        se.run_iteration(spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=stub)], runs=1,
                         budget=se.Budget(1.0), log=T.quiet)
        self.assertEqual([c[c.index("--max-budget-usd") + 1] for c in stub.calls[:1]], ["0.50"])


if __name__ == "__main__":
    unittest.main()
