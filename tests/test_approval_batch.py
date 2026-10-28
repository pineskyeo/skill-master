"""S1 method 1: `pending` lists every missing or stale approval of the given units and signs nothing; `approve --batch`
runs presence and approver-check once, asks one challenge and one passphrase, and signs each item as its own record
through _approve_with and store_approval, the format a single approve writes.

Every case runs in the temp HOME of tests/test_skill_eval.py Base and signs with its throwaway test key; subprocess is
patched for the whole module and only that key runner starts /usr/bin/ssh-keygen. ASCII only."""
import contextlib, hashlib, io, json, os, re, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, evals_doc, make_skill, write, wjson, rjson
from engine import approvals as A, spend

COMBINED = "requirements_eval_set"
FIELDS = ("unit", "kind", "unit_dir", "workspace", "gate", "status", "summary", "sha256", "ruleset_sha256", "spend_usd",
          "estimate_usd")
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def state(path):
    if not os.path.exists(path): return None
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def make_unit(root, name, requirements="goal"):
    d = make_skill(os.path.join(root, name), evals=evals_doc(name=name, human=False))
    write(os.path.join(d, "SKILL.md"), T.SKILL_MD.replace("name: branch-setup", "name: " + name))
    ws = os.path.join(root, name + "-ws"); os.makedirs(ws)
    write(os.path.join(ws, "requirements.md"), requirements)
    return d, ws


def result_row(charged):
    row = {k: None for k in spend.FIELDS["result"]}
    row.update(v=spend.VERSION, type="result", call_id="c-%s" % charged, charged_usd=charged)
    return json.dumps(row) + "\n"


def payload_lines(rec, gate, unit):
    return [x for x in A.payload("approve", gate, unit, rec["sha256"], rec["ruleset_sha256"], rec["at"]).decode()
            .splitlines() if not x.startswith("at ")]


class Batch(T.Base):
    def setUp(self):
        T.Base.setUp(self)
        self.shown, self.asked, self.secrets = [], [], []
        self.install()
        self.units = {n: make_unit(self.tmp, n) for n in ("alpha", "beta", "gamma")}

    def install(self, **kw):
        def read(prompt):
            self.asked.append(prompt)
            return T.type_challenge(prompt)
        def secret(prompt):
            self.secrets.append(prompt)
            return T.TEST_PASSPHRASE
        args = dict(read_line=read, write=self.shown.append, passphrase=None, read_secret=secret)
        args.update(kw)
        T.install_test_approver(self, **args)

    def spec(self, name):
        return se.load_eval_set(self.units[name][0])[0]

    def wsp(self, name):
        return self.units[name][1]

    def argv(self, names):
        out = []
        for n in names: out += ["--unit", self.units[n][0], "--workspace", self.units[n][1]]
        return out

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def files(self):
        return {n: state(os.path.join(ws, "approvals.json")) for n, (d, ws) in self.units.items()}


class TestPending(Batch):
    def test_a_missing_a_stale_and_a_current_approval(self):
        se.approve(COMBINED, self.spec("beta"), self.wsp("beta"))
        write(os.path.join(self.wsp("beta"), "requirements.md"), "goal, edited")
        se.approve(COMBINED, self.spec("gamma"), self.wsp("gamma"))
        with open(os.path.join(self.wsp("alpha"), "spend.jsonl"), "w") as f: f.write(result_row(0.25) + result_row(0.5))
        before = self.files()
        items = se.pending_items(se.unit_pairs([self.units[n][0] for n in ("alpha", "beta", "gamma")],
                                                [self.units[n][1] for n in ("alpha", "beta", "gamma")]))
        self.assertEqual([(i["unit"], i["gate"], i["status"]) for i in items],
                         [("alpha", COMBINED, "missing"), ("beta", COMBINED, "stale")])
        for i in items:
            for f in FIELDS: self.assertIn(f, i)
            spec = self.spec(i["unit"])
            self.assertEqual(i["sha256"], se.gate_hash(COMBINED, spec, i["workspace"]))
            self.assertEqual(i["ruleset_sha256"], se.ruleset_sha256())
            self.assertTrue(isinstance(i["estimate_usd"], float) and i["estimate_usd"] > 0, i["estimate_usd"])
            self.assertTrue(i["summary"])
        self.assertEqual((items[0]["spend_usd"], items[1]["spend_usd"]), (0.75, 0.0))
        self.assertEqual(self.files(), before)

    def test_the_cli_lists_every_field_and_signs_nothing(self):
        before = self.files()
        rc, out, err = self.cli("pending", *self.argv(("alpha", "beta", "gamma")))
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(re.findall(r"^unit (alpha|beta|gamma) gate requirements_eval_set ", out, re.M)), 3, out)
        for word in ("status missing", "sha256 ", "ruleset_sha256 " + se.ruleset_sha256(), "spend so far $0.00",
                     "plan estimate $", "summary "):
            self.assertIn(word, out)
        self.assertIn("3 pending approval(s); nothing was signed", out)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.asked, []); self.assertEqual(self.secrets, [])

    def test_pending_escapes_agent_written_text(self):
        write(os.path.join(self.wsp("alpha"), "requirements.md"), "goal \x1b[2J\r")
        rc, out, err = self.cli("pending", *self.argv(("alpha",)))
        self.assertEqual(rc, 0, err)
        self.assertNotIn("\x1b", out); self.assertNotIn("\r", out)

    def test_workspace_pairs(self):
        d, ws = self.units["alpha"]
        self.assertEqual(se.unit_pairs([d], [ws])[0][1], os.path.realpath(ws))
        with self.assertRaises(se.EvalError): se.unit_pairs([d, d], [ws])
        with self.assertRaises(se.EvalError): se.unit_pairs([], [])
        self.assertEqual(se.unit_pairs([d], [])[0][1], se.default_workspace(se.load_eval_set(d)[0]))


RAN = {"status": "run", "graded_runs": 1}
NOT_RUN = {"status": "not-run: engine not available", "graded_runs": 0}


class TestPendingKinds(Batch):
    """One case per gate kind pending_gates lists (the combined gate is TestPending's), each with its row fields."""
    def setUp(self):
        Batch.setUp(self)
        se.approve(COMBINED, self.spec("alpha"), self.wsp("alpha"))

    def alpha(self):
        return se.pending_items(se.unit_pairs([self.units["alpha"][0]], [self.wsp("alpha")]))

    def assert_row(self, item, unit, kind, gate, ws, spec):
        for f in FIELDS + ("needs_reason", "review"): self.assertIn(f, item)
        self.assertEqual((item["unit"], item["kind"], item["gate"], item["status"], item["workspace"]),
                         (unit, kind, gate, "missing", os.path.realpath(ws)))
        self.assertEqual(item["sha256"], se.gate_hash(gate, spec, ws))
        self.assertTrue(item["sha256"])
        self.assertEqual(item["ruleset_sha256"], se.ruleset_sha256())
        self.assertEqual(item["needs_reason"], gate in A.WAIVER_GATES)
        self.assertTrue(item["summary"]); self.assertTrue(item["review"])

    def iteration(self, needs_approval, coverage, problems=("p1: a gate problem",)):
        it = os.path.join(self.wsp("alpha"), "iteration-1"); os.makedirs(it)
        gate = {"needs_approval": needs_approval, "problems": list(problems)}
        wjson(os.path.join(it, "gate.json"), gate)
        wjson(os.path.join(it, "benchmark.json"), {"tool_coverage": coverage, "gate": gate})

    def test_a_rule_review_item(self):
        os.makedirs(os.path.join(self.units["alpha"][0], "agents"))
        write(os.path.join(self.units["alpha"][0], "agents", "openai.yaml"), "interface:\n  display_name: X\n")
        items = self.alpha()
        self.assertEqual([i["gate"] for i in items], ["rule_review"])
        self.assert_row(items[0], "alpha", "skill", "rule_review", self.wsp("alpha"), self.spec("alpha"))
        findings = se.rule_review_items(self.units["alpha"][0])
        self.assertTrue(findings)
        self.assertEqual(items[0]["summary"], "%d rule-review finding(s)" % len(findings))
        self.assertEqual(items[0]["review"], [("finding", x) for x in findings])

    def test_a_registration_item(self):
        self.iteration(True, {t: RAN for t in A.COVERAGE_TOOLS})
        items = self.alpha()
        self.assertEqual([i["gate"] for i in items], ["registration"])
        self.assert_row(items[0], "alpha", "skill", "registration", self.wsp("alpha"), self.spec("alpha"))
        self.assertEqual(items[0]["summary"], "iteration-1 gate problems: p1: a gate problem")
        self.assertEqual(items[0]["review"], [("problem", "p1: a gate problem")])

    def test_a_coverage_waiver_item(self):
        self.iteration(False, {"claude": RAN, "codex": RAN, "opencode": NOT_RUN, "hermes": RAN})
        items = self.alpha()
        self.assertEqual([i["gate"] for i in items], ["coverage_waiver:opencode"])
        self.assert_row(items[0], "alpha", "skill", "coverage_waiver:opencode", self.wsp("alpha"), self.spec("alpha"))
        self.assertEqual(items[0]["summary"], "tool opencode did not run in iteration-1 (not-run: engine not available)")
        self.assertIn("coverage-missing:opencode", items[0]["review"][0][1])

    def test_an_agent_unit_lists_the_live_gates_registration_and_coverage_waivers(self):
        import test_install as I
        agent = I.agent_copy(os.path.join(self.tmp, "agents"))
        ws = os.path.join(self.tmp, "agents", I.NAME + "-workspace")
        I.fake_iteration(agent, ws, self.runs)
        write(os.path.join(ws, "requirements.md"), "review the repository")
        rc, out, err = self.cli("agent", "report", agent, "--workspace", ws)
        self.assertEqual(rc, 3, out + err)
        items = se.pending_items(se.unit_pairs([agent], [ws]))
        gates = [COMBINED, "rule_review", "registration", "coverage_waiver:codex", "coverage_waiver:opencode",
                 "coverage_waiver:hermes"]
        self.assertEqual([i["gate"] for i in items], gates)
        spec = se.agent_spec(se.Agent(agent))
        for item, gate in zip(items, gates):
            self.assert_row(item, I.NAME, "agent", gate, ws, spec)
            self.assertIsNone(item["estimate_usd"])


class TestBatchSigning(Batch):
    def test_the_passphrase_is_read_once_and_each_item_is_its_own_record(self):
        rc, out, err = self.cli("approve", "--batch", *self.argv(("alpha", "beta", "gamma")))
        self.assertEqual(rc, 0, err + "".join(self.shown))
        self.assertEqual(len(self.secrets), 1)
        self.assertEqual(len([p for p in self.asked if "to sign" in p]), 1)
        for n in ("alpha", "beta", "gamma"):
            recs = rjson(os.path.join(self.wsp(n), "approvals.json"))
            self.assertEqual(sorted(recs), [COMBINED])
            self.assertEqual(se.approval_verdict(COMBINED, self.spec(n), self.wsp(n)).status, "approved")
        self.assertEqual(len(re.findall(r"^signed \S+ requirements_eval_set", out, re.M)), 3, out)

    def test_a_batch_record_verifies_exactly_like_a_single_record(self):
        twin = make_unit(os.path.join(self.tmp, "twin"), "alpha")
        single = se.approve(COMBINED, self.spec("alpha"), self.wsp("alpha"))
        rc, out, err = self.cli("approve", "--batch", "--unit", twin[0], "--workspace", twin[1])
        self.assertEqual(rc, 0, err)
        batch = rjson(os.path.join(twin[1], "approvals.json"))[COMBINED]
        self.assertEqual(sorted(single), sorted(batch))
        self.assertEqual({k: v for k, v in single.items() if k not in ("at", "signature")},
                         {k: v for k, v in batch.items() if k not in ("at", "signature")})
        self.assertEqual(payload_lines(single, COMBINED, "alpha"), payload_lines(batch, COMBINED, "alpha"))
        self.assertEqual(se.approval_verdict(COMBINED, se.load_eval_set(twin[0])[0], twin[1]).status, "approved")

    def test_a_challenge_mismatch_signs_nothing(self):
        self.install(read_line=lambda p: "WRONG1\n")
        before = self.files()
        rc, out, err = self.cli("approve", "--batch", *self.argv(("alpha", "beta")))
        self.assertEqual(rc, 2)
        self.assertIn("approve --batch: challenge mismatch: nothing was signed", err)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.secrets, [])

    def test_a_batch_refused_at_presence(self):
        before = self.files()
        with mock.patch.object(A, "is_tty", lambda fd: fd != 0):
            rc, out, err = self.cli("approve", "--batch", *self.argv(("alpha",)))
        self.assertEqual(rc, 2)
        self.assertEqual(err.strip(), "approve --batch needs a human at an interactive terminal (stdin is not a TTY)")
        self.assertEqual((self.files(), self.asked, self.secrets), (before, [], []))

    def test_an_item_that_changed_after_the_list_is_refused_and_the_others_are_signed(self):
        real = se.pending_items
        def edited(units):
            items = real(units)
            write(os.path.join(self.wsp("beta"), "requirements.md"), "goal, edited after the list")
            return items
        with mock.patch.object(se, "pending_items", edited):
            rc, out, err = self.cli("approve", "--batch", *self.argv(("alpha", "beta")))
        self.assertEqual(rc, 2)
        self.assertIn("not confirmed in this sitting or changed since the list was shown; nothing was signed", out + err)
        self.assertEqual(sorted(rjson(os.path.join(self.wsp("alpha"), "approvals.json"))), [COMBINED])
        self.assertIsNone(state(os.path.join(self.wsp("beta"), "approvals.json")))

    def test_the_batch_and_the_single_forms_parse_and_mixing_them_is_refused(self):
        for argv in (["approve", "--batch"], ["approve", self.units["alpha"][0], "--batch", "--unit", "x"],
                     ["approve", self.units["alpha"][0]], ["approve", "--gate", "eval_set"]):
            with self.subTest(argv):
                rc, out, err = self.cli(*argv)
                self.assertEqual(rc, 2, err)
                self.assertIn("approve: give SKILL and --gate, or --batch with --unit", err)


class TestSitting(Batch):
    def test_a_sitting_signs_only_what_was_confirmed_and_only_once(self):
        ap = se.APPROVER["it"]
        s = ap.open_sitting("approve --batch")
        spec, ws = self.spec("alpha"), self.wsp("alpha")
        with self.assertRaises(A.Refused):
            se._approve_with(COMBINED, lambda: (spec, ws), sitting=s)
        other = A.cli_approver()
        with self.assertRaises(A.Refused):
            other.sign("approve", COMBINED, lambda: None, sitting=s)
        with self.assertRaises(A.Refused):
            ap.sign("register", "registration", lambda: None, sitting=s)
        self.assertIsNone(state(os.path.join(ws, "approvals.json")))


if __name__ == "__main__":
    unittest.main()
