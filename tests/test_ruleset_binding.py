"""A5: the rule bundle hash (ruleset_sha256) is bound into every signed approval, plan.json, benchmark.json and
registration.json; the live and post-builder gates run in the order policy/gates.json declares; the compute_gate
thresholds and messages come from policy/gate.rules.json.

Signing uses only the throwaway test key of tests/test_skill_eval.py, made under /tmp. subprocess is patched for the
whole module; only that key runner starts /usr/bin/ssh-keygen. Every fixture lives in a temp folder, and the ruleset
root (skill_eval.RULESET_ROOT) is a temp copy of the bundle files, except where a test reads the tree it runs in.
approve, register and the live start are reached only as Python calls. This file is ASCII only."""
import contextlib, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, eva, evals_doc, make_skill, write, wjson, rjson, quiet, FakeClaude
from engine import approvals as A, rule_runner as RR, schema as S

ROOT = T.ROOT
BUNDLE_DIRS = ("rules", "schemas", "targets", "policy")
SHA = "ab" * 32
OTHER = "cd" * 32
AT = "2026-10-20T09:41:00Z"
RULES = "rules changed since approval"
RULES_LABEL = "NOT APPROVED (stale: rules changed since approval)"
UNSIGNED_RULES_LABEL = "NOT APPROVED (unsigned; stale: rules changed since approval)"
NO_BUDGET = "fail: --live needs a finite --budget-usd > 0"
LIVE_HEADER = "live gates (policy/gates.json, checked as for run --live, nothing executed):"
REGISTRATION_SCHEMA = os.path.join(ROOT, "schemas", "registration.schema.json")

_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def copy_bundle(dst):
    for d in BUNDLE_DIRS:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dst, d), ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def flip_byte(path, marker):
    """Changes the one byte right after the first marker."""
    with open(path, "rb") as f: data = f.read()
    i = data.index(marker) + len(marker)
    with open(path, "wb") as f: f.write(data[:i] + (b"X" if data[i:i + 1] != b"X" else b"Y") + data[i + 1:])


def change_rule_message(root):
    flip_byte(os.path.join(root, "rules", "skill-structure.rules.json"), b'"message": "')


def append(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f: f.write(text)


def file_sha(path):
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def declared(root=ROOT):
    doc = rjson(os.path.join(root, "policy", "gates.json"))
    return [e["id"] for e in doc["live"]], [e["id"] for e in doc["post_builder"]]


def schema_errors(instance):
    return S.compile_schema(S.load(REGISTRATION_SCHEMA)).validate(instance)


class Bound(T.Base):
    """T.Base (temp home, fake auth status, the test approver) plus a temp ruleset root and a skill with a workspace."""
    def setUp(self):
        T.Base.setUp(self)
        self.root = copy_bundle(os.path.join(self.tmp, "bundle"))
        pt = mock.patch.object(se, "RULESET_ROOT", self.root); pt.start(); self.addCleanup(pt.stop)
        self.d = make_skill(self.tmp, evals=evals_doc(human=False))
        self.spec = se.load_eval_set(self.d)[0]
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")
        self.approvals = os.path.join(self.ws, "approvals.json")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def plan(self, *extra):
        rc, out, err = self.cli("plan", self.d, "--workspace", self.ws, "--engines", "claude", *extra)
        self.assertEqual(rc, 0, err)
        return out.splitlines()

    def line(self, lines, prefix):
        return next(x for x in lines if x.startswith(prefix))

    def states(self, lines):
        i = lines.index(LIVE_HEADER)
        out = []
        for x in lines[i + 1:]:
            m = re.match(r"  (\d+)\. ([a-z-]+): (.*)$", x)
            if not m: break
            out.append((int(m.group(1)), m.group(2), m.group(3)))
        return out

    def report(self, regress=False, drop_ruleset=False):
        n = se.run_iteration(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude(regress))],
                             runs=1, log=quiet)
        if drop_ruleset:
            pp = os.path.join(self.ws, "iteration-%d" % n, "plan.json")
            plan = rjson(pp); plan.pop("ruleset_sha256"); wjson(pp, plan)
        se.grade_iteration(self.spec, self.ws, n, self.grader(), log=quiet)
        bench, gate = se.report_iteration(self.spec, self.ws, n)
        return n, bench, gate


class TestBundleHash(Bound):
    def test_the_hash_is_the_bundle_pairs_of_the_ruleset_root_computed_on_every_call(self):
        want = RR.sha256_labeled(RR.ruleset_pairs(os.path.join(self.root, "rules"), self.root))
        self.assertEqual(se.ruleset_sha256(), want)
        change_rule_message(self.root)
        self.assertNotEqual(se.ruleset_sha256(), want)

    def test_every_bundle_file_moves_the_hash(self):
        seen = {se.ruleset_sha256()}
        for rel in ("rules/data/description.json", "schemas/rules.schema.json", "targets/claude.json",
                    "policy/eval-policy.json", "policy/gates.json", "policy/gate.rules.json"):
            append(os.path.join(self.root, *rel.split("/")), " ")
            h = se.ruleset_sha256()
            self.assertNotIn(h, seen, rel)
            seen.add(h)

    def test_no_off_bundle_file_moves_the_hash(self):
        base = se.ruleset_sha256()
        for rel in ("policy/pricing.json", "policy/models.json", "policy/auth-profiles.json", "other/x.json"):
            append(os.path.join(self.root, *rel.split("/")), "\n")
            self.assertEqual(se.ruleset_sha256(), base, rel)

    def test_a_link_in_a_hashed_tree_stops_the_command(self):
        target = os.path.join(self.tmp, "t.json"); write(target, "{}")
        os.symlink(target, os.path.join(self.root, "targets", "t.json"))
        with self.assertRaises(se.EvalError) as cm: se.ruleset_sha256()
        self.assertIn("targets/t.json", str(cm.exception))

    def test_the_plan_line_equals_the_hash_eva_check_prints(self):
        """Lane 9 in process: the default bundle of the tree the test runs in."""
        with mock.patch.object(se, "RULESET_ROOT", ROOT):
            ruleset = self.line(self.plan(), "ruleset ")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            eva.main(["check", self.d, "--json"])
        self.assertEqual(ruleset, "ruleset " + json.loads(out.getvalue())["ruleset_sha256"])
        self.assertEqual(ruleset, "ruleset " + RR.default_bundle().sha256)


class TestStaleAfterRuleChange(Bound):
    """Stands in for operator lane 2 (plan.md:860): an approval signed before a one-byte rule change."""
    def test_an_approval_signed_before_a_one_byte_rule_message_change_is_stale(self):
        rec = se.approve("eval_set", self.spec, self.ws)
        self.assertEqual(rec["ruleset_sha256"], se.ruleset_sha256())
        self.assertTrue(se.approval_ok("eval_set", self.spec, self.ws))
        lines = self.plan()
        before = self.line(lines, "ruleset ")
        self.assertTrue(self.line(lines, "approval gates: ").endswith("eval_set=approved by test-approver"))
        change_rule_message(self.root)
        v = se.approval_verdict("eval_set", self.spec, self.ws)
        self.assertEqual(v, A.Verdict("stale", T.TEST_PRINCIPAL, RULES))
        self.assertFalse(se.approval_ok("eval_set", self.spec, self.ws))
        self.assertEqual(se.approval_label(v), RULES_LABEL)
        lines = self.plan()
        self.assertTrue(self.line(lines, "approval gates: ").endswith("eval_set=" + RULES_LABEL), lines)
        self.assertEqual(self.line(lines, "ruleset "), "ruleset " + se.ruleset_sha256())
        self.assertNotEqual(self.line(lines, "ruleset "), before)

    def test_a_gated_file_change_keeps_the_plain_stale_label(self):
        se.approve("eval_set", self.spec, self.ws)
        wjson(os.path.join(self.d, "evals", "trigger_evals.json"), T.TRIGGERS[::-1])
        v = se.approval_verdict("eval_set", self.spec, self.ws)
        self.assertEqual((v.status, v.reason), ("stale", "the gated files changed since signing"))
        self.assertEqual(se.approval_label(v), "NOT APPROVED (stale)")

    def test_the_live_gate_refuses_a_rules_stale_approval(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        change_rule_message(self.root)
        rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "100", "--engines",
                              "claude", "--runs", "1")
        self.assertEqual(rc, 2, err)
        self.assertIn("gate requirements not approved (stale)", err)
        self.assertEqual(se.iterations(self.ws), [])


class TestPricingIsNotTheBundle(Bound):
    """Lane 3 (plan.md:861) in process: a change outside the bundle keeps the approval valid."""
    def approved(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        return se.ruleset_sha256()

    def assert_still_approved(self, before):
        self.assertEqual(se.ruleset_sha256(), before)
        self.assertTrue(se.approval_ok("eval_set", self.spec, self.ws))
        self.assertEqual(self.line(self.plan(), "approval gates: "),
                         "approval gates: requirements=approved by test-approver, eval_set=approved by test-approver")

    def test_one_price_change_keeps_the_approval_valid(self):
        before = self.approved()
        fp = os.path.join(self.root, "policy", "pricing.json")
        doc = rjson(fp); doc["values"]["PRICES"]["value"]["claude-opus-5-5"]["input"] = 5.0; wjson(fp, doc)
        self.assert_still_approved(before)

    def test_models_auth_profiles_and_the_signers_file_keep_it_valid(self):
        k = T.shared_keys()
        signers = os.path.join(tempfile.mkdtemp(dir=k["dir"]), "allowed_signers")
        shutil.copy(k["signers"], signers)
        T.install_test_approver(self, signers=signers)
        before = self.approved()
        append(os.path.join(self.root, "policy", "models.json"), "\n")
        ap = os.path.join(self.root, "policy", "auth-profiles.json"); doc = rjson(ap)
        doc["profiles"]["company"]["label"] += " (edited)"; wjson(ap, doc)
        append(signers, "# a comment line\n")
        self.assert_still_approved(before)


class TestSignedPayload(Bound):
    def test_payload_bytes_carry_the_ruleset_line_after_sha256(self):
        self.assertEqual(A.payload("live", "run", "branch-setup", SHA, OTHER, AT, budget_usd=0.01),
                         ("skill-master-approval v1\nkind live\ngate run\nunit branch-setup\nsha256 %s\nruleset_sha256 "
                          "%s\nbudget_usd 0.01\nat %s\n" % (SHA, OTHER, AT)).encode("ascii"))
        self.assertEqual(A.payload("approve", "eval_set", "u", SHA, OTHER, AT),
                         ("skill-master-approval v1\nkind approve\ngate eval_set\nunit u\nsha256 %s\nruleset_sha256 "
                          "%s\nat %s\n" % (SHA, OTHER, AT)).encode("ascii"))

    def test_every_kind_refuses_a_missing_or_malformed_ruleset(self):
        for kind, budget in (("approve", None), ("register", None), ("live", 1.0)):
            for bad in (None, "", "AB" * 32, SHA + "\n", SHA[:-1], 5):
                with self.subTest(kind=kind, bad=bad), self.assertRaises(ValueError):
                    A.payload(kind, "g", "u", SHA, bad, AT, budget_usd=budget)

    def test_the_record_carries_the_ruleset_and_a_swapped_one_fails_the_verify(self):
        rec = se.approve("eval_set", self.spec, self.ws)
        ap, h, now = se.APPROVER["it"], se.gate_hash("eval_set", self.spec, self.ws), se.ruleset_sha256()
        self.assertEqual(set(rec), {"format", "method", "sha256", "ruleset_sha256", "principal", "at", "signature"})
        self.assertEqual(ap.verdict(rec, "approve", "eval_set", "branch-setup", h, now).status, "approved")
        v = ap.verdict(dict(rec, ruleset_sha256=OTHER), "approve", "eval_set", "branch-setup", h, OTHER)
        self.assertEqual(v.status, "invalid", v)
        self.assertIn("signature does not verify", v.reason)

    def test_a_present_but_malformed_ruleset_reads_invalid(self):
        rec = se.approve("eval_set", self.spec, self.ws)
        h = se.gate_hash("eval_set", self.spec, self.ws)
        for bad in ("AB" * 32, SHA + "\n", 5, None, ""):
            with self.subTest(bad=bad):
                v = se.APPROVER["it"].verdict(dict(rec, ruleset_sha256=bad), "approve", "eval_set", "branch-setup", h,
                                              se.ruleset_sha256())
                self.assertEqual((v.status, v.reason), ("invalid", "field ruleset_sha256 is missing or malformed"))

    def test_an_h1_shape_signed_record_reads_stale_and_starts_no_ssh_keygen(self):
        k, calls = T.shared_keys(), []
        T.install_test_approver(self, run=T.keygen_runner(k["dir"], calls))
        h = se.gate_hash("eval_set", self.spec, self.ws)
        h1 = ("skill-master-approval v1\nkind approve\ngate eval_set\nunit branch-setup\nsha256 %s\nat %s\n"
              % (h, AT)).encode("ascii")
        sig = A.sign_payload(T.keygen_runner(k["dir"]), k["key"], h1, passphrase=T.TEST_PASSPHRASE)
        self.assertIsNone(se.APPROVER["it"].verify(h1, sig, T.TEST_PRINCIPAL), "control: the H1 signature is real")
        del calls[:]
        wjson(self.approvals, {"eval_set": {"format": 1, "method": "ssh-sig", "sha256": h,
                                            "principal": T.TEST_PRINCIPAL, "at": AT, "signature": sig}})
        v = se.approval_verdict("eval_set", self.spec, self.ws)
        self.assertEqual(v, A.Verdict("stale", T.TEST_PRINCIPAL, RULES))
        self.assertEqual(se.approval_label(v), RULES_LABEL)
        self.assertEqual(calls, [])

    def test_an_unsigned_legacy_record_reads_unsigned_and_stale(self):
        h = se.gate_hash("eval_set", self.spec, self.ws)
        wjson(self.approvals, {"eval_set": {"sha256": h, "by": "pinesky", "at": AT}})
        v = se.approval_verdict("eval_set", self.spec, self.ws)
        self.assertEqual((v.status, v.principal), ("unsigned", None))
        self.assertEqual(se.approval_label(v), UNSIGNED_RULES_LABEL)
        self.assertTrue(self.line(self.plan(), "approval gates: ").endswith("eval_set=" + UNSIGNED_RULES_LABEL))
        wjson(self.approvals, {"eval_set": {"sha256": h, "by": "pinesky", "at": AT,
                                            "ruleset_sha256": se.ruleset_sha256()}})
        v = se.approval_verdict("eval_set", self.spec, self.ws)
        self.assertEqual((v.status, se.approval_label(v)), ("unsigned", "NOT APPROVED (unsigned)"))

    def test_the_verdict_order(self):
        rec = se.approve("eval_set", self.spec, self.ws)
        ap, h, now = se.APPROVER["it"], se.gate_hash("eval_set", self.spec, self.ws), se.ruleset_sha256()
        verdict = lambda r, sha, rules, ap=ap: ap.verdict(r, "approve", "eval_set", "branch-setup", sha, rules)
        no_rules = {k: v for k, v in rec.items() if k != "ruleset_sha256"}
        missing = T.gate_approver(signers=os.path.join(self.tmp, "no-signers"))
        cases = ((verdict(None, h, now), ("missing", "no record")),
                 (verdict(dict(rec, ruleset_sha256="x"), OTHER, OTHER), ("invalid", "field ruleset_sha256 is missing or "
                                                                                    "malformed")),
                 (missing.verdict(rec, "approve", "eval_set", "branch-setup", OTHER, OTHER),
                  ("invalid", "approvers file missing: %s" % os.path.join(self.tmp, "no-signers"))),
                 (verdict(rec, OTHER, OTHER), ("stale", "the gated files changed since signing")),
                 (verdict(rec, None, OTHER), ("stale", "the gated files are missing")),
                 (verdict(rec, h, OTHER), ("stale", RULES)),
                 (verdict(no_rules, h, now), ("stale", RULES)),
                 (verdict(rec, h, now), ("approved", "")))
        for got, want in cases:
            self.assertEqual((got.status, got.reason), want)

    def test_approve_register_and_the_live_start_show_the_ruleset_they_sign(self):
        shown = []
        T.install_test_approver(self, write=shown.append)
        se.approve("requirements", self.spec, self.ws); se.approve("eval_set", self.spec, self.ws)
        self.assertIn("ruleset_sha256 " + se.ruleset_sha256(), shown)
        T.waive_coverage(self.spec, self.ws)
        self.report()
        del shown[:]
        rec = se.register(self.spec, self.ws)
        self.assertIn("register ruleset_sha256=" + rec["ruleset_sha256"], shown)
        del shown[:]
        T.install_test_approver(self, write=shown.append, read_line=lambda p: "\n")
        with mock.patch.object(se, "live_exec", T._forbidden):
            rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac", "--budget-usd", "100", "--engines",
                                  "claude", "--runs", "1")
        self.assertEqual(rc, 2, err); self.assertIn("challenge mismatch", err)
        self.assertIn("ruleset_sha256 " + se.ruleset_sha256(), shown)


class TestRecordedHashes(Bound):
    def test_plan_json_records_the_ruleset_and_the_off_bundle_hashes(self):
        n = se.run_iteration(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude())], runs=1,
                             log=quiet)
        plan = rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))
        pol = os.path.join(self.root, "policy")
        self.assertEqual(plan["ruleset_sha256"], se.ruleset_sha256())
        self.assertEqual(plan["policy_sha256"], {"pricing": file_sha(os.path.join(pol, "pricing.json")),
                                                 "models": file_sha(os.path.join(pol, "models.json")),
                                                 "auth_profiles": file_sha(os.path.join(pol, "auth-profiles.json")),
                                                 "allowed_signers": file_sha(T.shared_keys()["signers"])})
        write(os.path.join(pol, "auth-profiles.json"), "{}\n")
        n = se.run_iteration(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude())], runs=1,
                             log=quiet)
        plan2 = rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))
        self.assertEqual(plan2["policy_sha256"]["auth_profiles"], file_sha(os.path.join(pol, "auth-profiles.json")))
        self.assertEqual(plan2["ruleset_sha256"], plan["ruleset_sha256"])

    def test_benchmark_records_both_hashes_and_the_gate_lists_a_rule_change(self):
        """Stands in for operator lane 5 (plan.md:863): report after the rules changed."""
        n, bench, gate = self.report()
        m = bench["metadata"]
        self.assertEqual((m["ruleset_sha256"], m["current_ruleset_sha256"]), (se.ruleset_sha256(), se.ruleset_sha256()))
        self.assertEqual(gate["problems"], T.CLAUDE_ONLY_MISSING)
        self.assertEqual(rjson(os.path.join(self.ws, "iteration-%d" % n, "benchmark.json"))["metadata"]["ruleset_sha256"],
                         se.ruleset_sha256())
        change_rule_message(self.root)
        bench, gate = se.report_iteration(self.spec, self.ws, n)
        self.assertNotEqual(bench["metadata"]["current_ruleset_sha256"], bench["metadata"]["ruleset_sha256"])
        self.assertEqual(gate["problems"], ["stale: rules changed since this iteration ran"] + T.CLAUDE_ONLY_MISSING)
        self.assertTrue(gate["needs_approval"])
        self.assertIn("- problem: stale: rules changed since this iteration ran",
                      T.read(os.path.join(self.ws, "iteration-%d" % n, "comparison.md")))

    def test_a_plan_without_a_ruleset_is_flagged_unrecorded(self):
        n, bench, gate = self.report(drop_ruleset=True)
        self.assertIsNone(bench["metadata"]["ruleset_sha256"])
        self.assertEqual(gate["problems"],
                         ["stale: rules changed since this iteration ran (plan.json records no ruleset_sha256)"]
                         + T.CLAUDE_ONLY_MISSING)

    def test_human_pending_only_keeps_its_b1_result(self):
        bench = dict(B1_FIXTURES["human-only"][0])
        bench["metadata"] = dict(bench["metadata"], ruleset_sha256=SHA, current_ruleset_sha256=OTHER)
        g = se.compute_gate(bench)
        self.assertEqual(g["problems"], ["stale: rules changed since this iteration ran",
                                         "ungraded: 3 human item(s) pending review (fill human_review.json, re-run grade)"]
                         + T.NO_COVERAGE_MISSING)
        self.assertTrue(g["human_pending_only"])
        only_rules = {"per_engine": {}, "completeness": {}, "metadata": dict(bench["metadata"], skill_sha256="a",
                                                                              current_skill_sha256="a")}
        g = se.compute_gate(only_rules)
        self.assertEqual((g["problems"], g["human_pending_only"]),
                         (["stale: rules changed since this iteration ran"] + T.NO_COVERAGE_MISSING, False))


class TestRegistration(Bound):
    def report(self, **kw):
        T.waive_coverage(self.spec, self.ws)
        return Bound.report(self, **kw)

    def test_the_record_binds_the_ruleset_and_matches_the_schema(self):
        self.report()
        rec = se.register(self.spec, self.ws)
        self.assertEqual(rec["ruleset_sha256"], se.ruleset_sha256())
        self.assertIn("ruleset_sha256", se.REGISTRATION_SIGNED)
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "approved")
        self.assertEqual(schema_errors(rec), [])
        self.assertEqual(schema_errors(se.registration_record(self.spec, self.ws)), [])
        without = {k: v for k, v in rec.items() if k != "ruleset_sha256"}
        self.assertTrue(any(kw == "required" and "ruleset_sha256" in msg for _, kw, msg in schema_errors(without)),
                        schema_errors(without))
        self.assertTrue(schema_errors(dict(rec, extra=1)))
        self.assertTrue(schema_errors(dict(rec, ruleset_sha256="AB" * 32)))
        self.assertTrue(schema_errors(dict(rec, iteration=0)))

    def test_a_signed_registration_approval_matches_the_schema(self):
        self.report(regress=True)
        se.approve("registration", self.spec, self.ws)
        rec = se.register(self.spec, self.ws)
        self.assertEqual(rec["approval"]["ruleset_sha256"], se.ruleset_sha256())
        self.assertEqual(schema_errors(rec), [])
        self.assertTrue(schema_errors(dict(rec, approval={k: v for k, v in rec["approval"].items()
                                                          if k != "ruleset_sha256"})))

    def test_the_eva_schema_check_refuses_a_record_without_the_ruleset(self):
        self.report()
        rec = se.register(self.spec, self.ws)
        bad = os.path.join(self.tmp, "registration-no-ruleset.json")
        wjson(bad, {k: v for k, v in rec.items() if k != "ruleset_sha256"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = eva.main(["schema-check", REGISTRATION_SCHEMA, bad])
        self.assertEqual(rc, 1, out.getvalue()); self.assertIn("ruleset_sha256", out.getvalue())

    def test_a_moved_ruleset_refuses_registration_and_names_both_hashes(self):
        self.report()
        old = se.ruleset_sha256()
        change_rule_message(self.root)
        new = se.ruleset_sha256()
        with self.assertRaises(se.EvalError) as cm: se.registration_record(self.spec, self.ws)
        self.assertIn(T.coverage_missing("hermes", T.NOT_AVAILABLE, "stale (%s)" % RULES), str(cm.exception))
        with mock.patch.object(se, "COVERAGE_REQUIRED", ("claude",)), self.assertRaises(se.EvalError) as cm:
            se.registration_record(self.spec, self.ws)
        self.assertIn(old[:12], str(cm.exception)); self.assertIn(new[:12], str(cm.exception))
        with self.assertRaises(se.EvalError): se.register(self.spec, self.ws)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "registration.json")))

    def test_an_iteration_without_a_recorded_ruleset_refuses(self):
        self.report(drop_ruleset=True)
        se.approve("registration", self.spec, self.ws)
        with self.assertRaises(se.EvalError) as cm: se.registration_record(self.spec, self.ws)
        self.assertIn("<unrecorded>", str(cm.exception)); self.assertIn(se.ruleset_sha256()[:12], str(cm.exception))

    def test_a_registration_signed_before_a_rule_change_reads_stale(self):
        self.report()
        se.register(self.spec, self.ws)
        change_rule_message(self.root)
        v = se.registration_verdict(self.spec, self.ws)
        self.assertEqual((v.status, v.reason), ("stale", RULES))

    def test_a_swapped_top_level_ruleset_breaks_the_register_signature(self):
        self.report()
        se.register(self.spec, self.ws)
        self.assertEqual(se.registration_verdict(self.spec, self.ws).status, "approved")
        fp = os.path.join(self.ws, "registration.json")
        rec = rjson(fp); rec["ruleset_sha256"] = OTHER; wjson(fp, rec)
        v = se.registration_verdict(self.spec, self.ws)
        self.assertNotEqual(v.status, "approved", v)

    def test_the_schema_refuses_an_unknown_key_in_the_signed_object(self):
        self.report()
        rec = se.register(self.spec, self.ws)
        self.assertEqual(schema_errors(rec), [])
        errors = schema_errors(dict(rec, signed=dict(rec["signed"], extra=1)))
        self.assertTrue(any(kw == "additionalProperties" for _, kw, _ in errors), errors)


def rates(w, b, n=3):
    return {"with_skill": {"pass_rate": {"mean": w}, "graded_runs": n},
            "without_skill": {"pass_rate": {"mean": b}, "graded_runs": n}}


def comp(graded, expected, trig, etrig, errors, cmp_, ecmp):
    return {"expected_runs": expected, "graded_runs": graded, "expected_trigger_runs": etrig, "trigger_runs": trig,
            "trigger_errors": errors, "expected_comparisons": ecmp, "comparisons": cmp_}


COVERAGE = {"claude": {"status": "run"}, "codex": {"status": "not-run: engine not selected"},
            "opencode": {"status": "not-run: engine not available"}}
B1_FIXTURES = {
    "everything": ({"per_engine": {"codex": rates(0.5, 0.75, 2), "claude": rates(0.25, 0.5)},
                    "calibration": {"agreement": 0.5, "n": 4},
                    "tool_coverage": COVERAGE,
                    "static_failures": [1, 2, 3],
                    "metadata": {"stopped": "budget exhausted ($1.00 of $1.00)", "skill_sha256": "a",
                                 "current_skill_sha256": "b", "eval_set_sha256": "c", "current_eval_set_sha256": "d"},
                    "completeness": {"codex": comp(1, 4, 10, 20, 2, 0, 2), "claude": comp(2, 6, 5, 60, 1, 1, 3)},
                    "execution_errors": [1, 2],
                    "trigger": {"codex": {"with_skill": {"failed": 2, "total": 20}},
                                "claude": {"with_skill": {"failed": 1, "total": 20}}},
                    "llm_not_graded": 2, "human_pending": 1},
                   {"per_engine": {"claude": {"with_skill": {"pass_rate": {"mean": 0.75}, "graded_runs": 3}},
                                   "codex": {"with_skill": {"pass_rate": {"mean": 0.875}, "graded_runs": 2}}}}),
    "no-plan": ({"per_engine": {}}, None),
    "unbound": ({"per_engine": {}, "completeness": {}, "metadata": {"skill_sha256": "a"}}, None),
    "human-only": ({"per_engine": {"claude": rates(0.75, 0.5)}, "calibration": {"agreement": 0.9, "n": 10},
                    "completeness": {}, "metadata": {"skill_sha256": "a", "current_skill_sha256": "a",
                                                     "eval_set_sha256": "e", "current_eval_set_sha256": "e"},
                    "human_pending": 3}, None),
    "clean": ({"per_engine": {"claude": rates(0.75, 0.5)}, "calibration": {"agreement": 0.9, "n": 10},
               "completeness": {}, "metadata": {"skill_sha256": "a", "current_skill_sha256": "a",
                                                "eval_set_sha256": "e", "current_eval_set_sha256": "e"}}, None),
}
NOT_CALIBRATED = "grader not calibrated for this iteration (run `calibrate --golden ...`)"
# compute_gate at the B1 export on B1_FIXTURES (/tmp/a5-owner/gen_b1_gate.py, /tmp/a5-owner/b1-gate-messages.txt).
B1_GATES = {
    "clean": {"human_pending_only": False, "needs_approval": False, "notes": [], "problems": []},
    "everything": {"human_pending_only": False, "needs_approval": True, "notes": [
        "per-tool dynamic coverage: run on claude; NOT run on codex (engine not selected), opencode (engine not "
        "available) (not-run is never counted as passed; adding the OpenCode and Hermes engines is a follow-up)"],
        "problems": [
            "regression [codex]: with_skill pass_rate 0.50 < without_skill 0.75",
            "regression [codex]: with_skill pass_rate 0.50 < previous iteration 0.88",
            "regression [claude]: with_skill pass_rate 0.25 < without_skill 0.50",
            "regression [claude]: with_skill pass_rate 0.25 < previous iteration 0.75",
            "low grader agreement: 0.50 < 0.80 on 4 golden examples",
            "failing static checks in with_skill runs: 3",
            "incomplete: run stopped early (budget exhausted ($1.00 of $1.00))",
            "incomplete [claude]: 2 of 6 eval runs graded",
            "incomplete [claude]: 5 of 60 trigger runs",
            "execution failed [claude]: 1 trigger run(s) with errors",
            "incomplete [claude]: 1 of 3 blind pairwise comparisons (run `grade --live`)",
            "incomplete [codex]: 1 of 4 eval runs graded",
            "incomplete [codex]: 10 of 20 trigger runs",
            "execution failed [codex]: 2 trigger run(s) with errors",
            "incomplete [codex]: 0 of 2 blind pairwise comparisons (run `grade --live`)",
            "stale: the skill changed after this iteration ran (re-run before trusting this report)",
            "stale: the eval set changed after this iteration ran",
            "execution failed in 2 eval run(s)",
            "trigger criteria failed [claude]: 1 of 20 queries",
            "trigger criteria failed [codex]: 2 of 20 queries",
            "ungraded: 2 LLM item(s) not graded (run `grade --live`)",
            "ungraded: 1 human item(s) pending review (fill human_review.json, re-run grade)"]},
    "human-only": {"human_pending_only": True, "needs_approval": True, "notes": [], "problems": [
        "ungraded: 3 human item(s) pending review (fill human_review.json, re-run grade)"]},
    "no-plan": {"human_pending_only": False, "needs_approval": True, "notes": [NOT_CALIBRATED],
                "problems": ["incomplete: no plan.json, expected run counts unknown"]},
    "unbound": {"human_pending_only": False, "needs_approval": True, "notes": [NOT_CALIBRATED],
                "problems": ["unbound: plan.json records no skill/eval-set hash; re-run the iteration"]},
}
PREDICATE_IDS = ["regression-baseline", "regression-previous", "low-agreement", "not-calibrated",
                 "static-failures", "run-stopped", "no-plan", "eval-runs-incomplete", "trigger-runs-incomplete",
                 "trigger-errors", "comparisons-incomplete", "unbound", "stale-skill", "stale-eval-set", "stale-rules",
                 "stale-rules-unrecorded", "execution-errors", "trigger-failed", "llm-ungraded", "human-pending",
                 "coverage-missing", "coverage-waived", "coverage-not-required"]
G1_COVERAGE = dict({name: T.NO_COVERAGE_MISSING for name in B1_FIXTURES}, everything=[
    T.coverage_missing("claude", T.NO_COVERAGE_ENTRY), T.coverage_missing("codex", T.NOT_SELECTED),
    T.coverage_missing("opencode", T.NOT_AVAILABLE), T.coverage_missing("hermes", T.NO_COVERAGE_ENTRY)])


class TestGateRules(unittest.TestCase):
    def test_every_b1_message_renders_byte_for_byte(self):
        for name, (bench, prev) in B1_FIXTURES.items():
            with self.subTest(name):
                want = dict(B1_GATES[name], problems=B1_GATES[name]["problems"] + G1_COVERAGE[name], needs_approval=True,
                            notes=[x for x in B1_GATES[name]["notes"] if not x.startswith("per-tool dynamic coverage")],
                            coverage_only=name == "clean", coverage={t: "coverage-missing" for t in eva.TOOLS})
                self.assertEqual(se.compute_gate(bench, prev), want)

    def test_the_file_holds_one_template_per_predicate_and_the_two_thresholds(self):
        doc = rjson(os.path.join(ROOT, "policy", "gate.rules.json"))
        self.assertEqual(sorted(doc["messages"]), sorted(PREDICATE_IDS))
        self.assertEqual([row[0] for row in se.GATE_PREDICATES], PREDICATE_IDS)
        for pid, entry in doc["messages"].items():
            self.assertTrue(entry["source"].strip(), pid)
            self.assertEqual(se.GATE_MESSAGES[pid], entry["template"])
        self.assertEqual({k: (v["value"], v["type"]) for k, v in doc["values"].items()},
                         {"AGREEMENT_MIN": (0.8, "float"), "REGRESSION_TOLERANCE": (0.0, "float")})
        self.assertEqual((se.AGREEMENT_MIN, se.REGRESSION_TOLERANCE), (0.8, 0.0))
        ep = rjson(os.path.join(ROOT, "policy", "eval-policy.json"))["values"]
        self.assertNotIn("AGREEMENT_MIN", ep); self.assertNotIn("REGRESSION_TOLERANCE", ep)
        with open(os.path.join(ROOT, "policy", "gate.rules.json"), "rb") as f: self.assertTrue(f.read().isascii())

    def refusal(self, edit=None, text=None, remove=False):
        """The PolicyError text the import-time loaders raise on an edited copy of policy/gate.rules.json."""
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        for name in os.listdir(os.path.join(ROOT, "policy")):
            shutil.copy(os.path.join(ROOT, "policy", name), d)
        fp = os.path.join(d, "gate.rules.json")
        if remove: os.remove(fp)
        elif text is not None: write(fp, text)
        elif edit is not None:
            doc = rjson(fp); edit(doc); wjson(fp, doc)
        with mock.patch.object(se, "POLICY_DIR", d):
            with self.assertRaises(se.PolicyError) as cm:
                se.load_all_policy(); se.load_gate_messages()
        return str(cm.exception)

    def test_the_loaders_accept_the_file_as_shipped(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        for name in os.listdir(os.path.join(ROOT, "policy")):
            shutil.copy(os.path.join(ROOT, "policy", name), d)
        with mock.patch.object(se, "POLICY_DIR", d):
            self.assertEqual(se.load_all_policy()["AGREEMENT_MIN"], 0.8)
            self.assertEqual(se.load_gate_messages(), se.GATE_MESSAGES)

    def test_the_loaders_refuse_a_broken_file(self):
        def set_template(pid, value):
            return lambda doc: doc["messages"][pid].update(template=value)
        def set_value(name, value):
            return lambda doc: doc["values"][name].update(value=value)
        cases = (("missing file", dict(remove=True), "policy/gate.rules.json: "),
                 ("duplicate key", dict(text='{"values": {}, "values": {}}'), "policy/gate.rules.json: "),
                 ("missing id", dict(edit=lambda doc: doc["messages"].pop("human-pending")),
                  "policy/gate.rules.json: messages must name exactly"),
                 ("unknown id", dict(edit=lambda doc: doc["messages"].update(frobnicate={"template": "x", "source": "s"})),
                  "policy/gate.rules.json: messages must name exactly"),
                 ("unsupplied field", dict(edit=set_template("static-failures", "failing static checks: %(engine)s")),
                  "policy/gate.rules.json: messages.static-failures: "),
                 ("positional field", dict(edit=set_template("execution-errors", "execution failed in %d eval run(s)")),
                  "policy/gate.rules.json: messages.execution-errors: "),
                 ("empty template", dict(edit=set_template("no-plan", "")), "policy/gate.rules.json: messages.no-plan: "),
                 ("no source", dict(edit=lambda doc: doc["messages"]["unbound"].pop("source")),
                  "policy/gate.rules.json: messages.unbound: "),
                 ("agreement below zero", dict(edit=set_value("AGREEMENT_MIN", -0.5)),
                  "policy/gate.rules.json: AGREEMENT_MIN: must be a finite number from 0 to 1, got -0.5"),
                 ("tolerance above one", dict(edit=set_value("REGRESSION_TOLERANCE", 1.5)),
                  "policy/gate.rules.json: REGRESSION_TOLERANCE: must be a finite number from 0 to 1, got 1.5"),
                 ("threshold missing", dict(edit=lambda doc: doc["values"].pop("AGREEMENT_MIN")),
                  "policy/gate.rules.json: values must name exactly AGREEMENT_MIN, REGRESSION_TOLERANCE"))
        for label, kw, want in cases:
            with self.subTest(label):
                self.assertIn(want, self.refusal(**kw))

    def test_an_unsupplied_field_is_named(self):
        msg = self.refusal(edit=lambda doc: doc["messages"]["static-failures"].update(template="x %(engine)s"))
        self.assertIn("engine", msg)


class TestGateOrder(T.Base):
    """Lane 6 (plan.md:864): the traced run order equals policy/gates.json as read from the tree the test runs in, so a
    swap of two entries in that file turns these tests red."""
    def setUp(self):
        T.Base.setUp(self)
        self.d = make_skill(self.tmp, evals=evals_doc(human=False))
        self.spec, self.problems = se.load_eval_set(self.d)
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")

    def traced(self, name, trace):
        def record(gid, fn):
            return lambda *a, **k: trace.append(gid) or fn(*a, **k)
        return mock.patch.object(se, name, tuple((gid, record(gid, fn)) for gid, fn in getattr(se, name)))

    def live_args(self, budget="100"):
        a = se.build_parser().parse_args(["run", self.d, "--workspace", self.ws, "--engines", "claude", "--live",
                                          "--budget-usd", budget])
        a.grader_model, a.argv = se.DEFAULT_GRADER_MODEL, []
        return a

    def check(self, report):
        a = self.live_args()
        return se.check_live_gates(a, self.spec, self.problems, self.ws, None, se.make_engines(a, self.spec),
                                   se.parse_token_overrides(None), se.parse_price_overrides(None), 100.0, report=report)

    def test_check_live_gates_runs_the_declared_live_gates_in_order_in_report_mode(self):
        trace, report = [], []
        with self.traced("LIVE_GATES", trace): self.check(report)
        self.assertEqual(trace, declared()[0])
        self.assertEqual(len(trace), 11)
        self.assertTrue(report, "control: the unapproved fixture fails some gates and every gate still ran")

    def test_check_live_gates_runs_every_declared_gate_when_all_pass(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        trace = []
        with self.traced("LIVE_GATES", trace): self.check(None)
        self.assertEqual(trace, declared()[0])

    def test_the_loop_runs_the_declared_post_builder_gates_in_order(self):
        trace, calls = [], []
        with self.traced("POST_BUILDER_GATES", trace):
            r = se.loop(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude(True))],
                        self.grader(), lambda it, sd: calls.append(it) or 0, max_iterations=2, runs=1, log=quiet)
        self.assertEqual(r, {"status": "needs-approval", "iteration": 2})
        self.assertEqual(len(calls), 1)
        self.assertEqual(trace, declared()[1])
        self.assertEqual(len(trace), 7)


class TestGateDeclaration(Bound):
    def swap(self, key, i, j):
        fp = os.path.join(self.root, "policy", "gates.json")
        doc = rjson(fp); doc[key][i], doc[key][j] = doc[key][j], doc[key][i]; wjson(fp, doc)

    def test_a_swapped_live_gate_refuses_check_live_gates_and_plan_prints_it(self):
        self.swap("live", 0, 1)
        a = se.build_parser().parse_args(["run", self.d, "--workspace", self.ws, "--engines", "claude"])
        a.grader_model = se.DEFAULT_GRADER_MODEL
        for report in (None, []):
            with self.assertRaises(se.EvalError) as cm:
                se.check_live_gates(a, self.spec, [], self.ws, None, se.make_engines(a, self.spec),
                                    se.parse_token_overrides(None), se.parse_price_overrides(None), 100.0, report=report)
            self.assertIn("policy/gates.json", str(cm.exception)); self.assertIn("strict-budget", str(cm.exception))
        lines = self.plan()
        i = lines.index(LIVE_HEADER)
        self.assertTrue(lines[i + 1].startswith("gate declaration mismatch (check_live_gates and loop refuse): "
                                                "policy/gates.json"), lines[i + 1])

    def test_while_the_declaration_mismatches_every_plan_gate_line_fails_with_it(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        fp = os.path.join(self.root, "policy", "gates.json")
        shipped = T.read(fp)
        for key, i, j in (("live", 0, 1), ("post_builder", 2, 3)):
            with self.subTest(key):
                write(fp, shipped)
                self.swap(key, i, j)
                problem = se.gate_declaration_problem()
                self.assertIn(key, problem)
                lines = self.plan("--budget-usd", "100")
                k = lines.index(LIVE_HEADER)
                self.assertEqual(lines[k + 1], "gate declaration mismatch (check_live_gates and loop refuse): " + problem)
                states = self.states([LIVE_HEADER] + lines[k + 2:])
                self.assertEqual([(n, gid) for n, gid, _ in states], list(enumerate((g for g, _ in se.LIVE_GATES), 1)))
                self.assertEqual({s for _, _, s in states}, {"fail: " + problem.splitlines()[0]})
                self.assertEqual(lines[k + 2 + len(states)], "post-builder gates (loop): " +
                                 ", ".join(gid for gid, _ in se.POST_BUILDER_GATES))
        write(fp, shipped)
        self.assertIsNone(se.gate_declaration_problem())
        self.assertNotIn("fail: policy/gates.json", "\n".join(self.plan("--budget-usd", "100")))

    def test_a_swapped_post_builder_gate_refuses_the_loop(self):
        self.swap("post_builder", 2, 3)
        with self.assertRaises(se.EvalError) as cm:
            se.loop(self.spec, self.ws, [], None, lambda it, sd: self.fail("builder called"), runs=1, log=quiet)
        self.assertIn("builder-non-english", str(cm.exception))
        self.assertEqual(se.iterations(self.ws), [])

    def test_a_malformed_file_refuses(self):
        for text in ("{", '{"live": [], "post_builder": []}', '{"live": "x", "post_builder": []}'):
            with self.subTest(text):
                write(os.path.join(self.root, "policy", "gates.json"), text)
                self.assertIn("policy/gates.json", se.gate_declaration_problem())


class TestPlanLines(Bound):
    def test_plan_prints_the_ruleset_and_every_declared_gate_with_its_state(self):
        lines = self.plan()
        i = lines.index(self.line(lines, "approval gates: "))
        self.assertEqual(lines[i + 1], "ruleset " + se.ruleset_sha256())
        self.assertEqual(lines[i + 2], LIVE_HEADER)
        live, post = declared(self.root)
        states = self.states(lines)
        self.assertEqual([(n, gid) for n, gid, _ in states], list(enumerate(live, 1)))
        self.assertEqual(lines[i + 3 + len(live)], "post-builder gates (loop): " + ", ".join(post))
        st = {gid: s for _, gid, s in states}
        self.assertEqual(st["subscription-auth"], "skipped: " + se.AUTH_STATUS_SKIPPED)
        self.assertEqual((st["per-call-bound"], st["estimate-within-budget"]), (NO_BUDGET, NO_BUDGET))
        self.assertTrue(st["approvals"].startswith("fail: gate requirements not approved (write "), st["approvals"])
        for gid in ("eval-set-valid", "strict-budget", "codex-grader-not-under-test", "grader-not-model-under-test",
                    "rule-review", "priced-models"):
            self.assertEqual(st[gid], "pass", gid)
        self.assertEqual(self.status_calls, [])

    def test_with_a_budget_the_states_match_check_live_gates_in_report_mode(self):
        for g in ("requirements", "eval_set"): se.approve(g, self.spec, self.ws)
        st = {gid: s for _, gid, s in self.states(self.plan("--budget-usd", "100"))}
        self.assertEqual(st.pop("subscription-auth"), "skipped: " + se.AUTH_STATUS_SKIPPED)
        self.assertEqual(set(st.values()), {"pass"}, st)
        st = {gid: s for _, gid, s in self.states(self.plan("--budget-usd", "0.01"))}
        a = se.build_parser().parse_args(["run", self.d, "--workspace", self.ws, "--engines", "claude", "--budget-usd",
                                          "0.01"])
        a.grader_model = se.DEFAULT_GRADER_MODEL
        report = []
        se.check_live_gates(a, self.spec, [], self.ws, None, se.make_engines(a, self.spec), se.parse_token_overrides(None),
                            se.parse_price_overrides(None), 0.01, report=report)
        fails = [r.splitlines()[0] for r in report if r not in (se.AUTH_STATUS_SKIPPED, se.CODEX_AUTH_SKIPPED)]
        self.assertEqual([s[len("fail: "):] for s in st.values() if s.startswith("fail: ")], fails)
        self.assertTrue(st["estimate-within-budget"].startswith("fail: estimate $"), st)
        self.assertNotIn("\n", st["estimate-within-budget"])
        self.assertEqual(self.status_calls, [])

    def test_an_unknown_state_is_never_pass(self):
        def boom(g): raise se.EvalError("cannot price this")
        table = tuple((gid, boom if gid == "priced-models" else fn) for gid, fn in se.LIVE_GATES)
        with mock.patch.object(se, "LIVE_GATES", table):
            st = {gid: s for _, gid, s in self.states(self.plan("--budget-usd", "100"))}
        self.assertEqual(st["priced-models"], "fail: cannot price this")

    def test_preflight_runs_once_per_plan(self):
        calls = []
        real = se.preflight
        with mock.patch.object(se, "preflight", lambda *a, **k: calls.append(a) or real(*a, **k)):
            self.plan("--budget-usd", "100")
        self.assertEqual(len(calls), 1)


class TestLoopRulesChanged(Bound):
    """Lane 7 (plan.md:865): a builder call that moves ruleset_sha256 stops the loop with rules-changed."""
    def loop(self, builder, **kw):
        kw.setdefault("max_iterations", 3)
        return se.loop(self.spec, self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=FakeClaude(True))],
                       self.grader(), builder, runs=1, log=quiet, **kw)

    def spies(self, after):
        def spy(name, fn):
            return lambda *a, **k: after.append(name) or fn(*a, **k)
        return [mock.patch.object(eva, "english_violations", spy("english", eva.english_violations)),
                mock.patch.object(eva, "parity_violations", spy("parity", eva.parity_violations)),
                mock.patch.object(se, "rule_review_problems", spy("review", se.rule_review_problems)),
                mock.patch.object(se, "load_eval_set", spy("eval-set", se.load_eval_set))]

    def test_a_builder_that_changes_one_byte_of_the_bundle_stops_the_loop(self):
        old, calls, after = se.ruleset_sha256(), [], []
        def builder(it, sd):
            calls.append(it); change_rule_message(self.root); return 0
        with contextlib.ExitStack() as stack:
            for pt in self.spies(after): stack.enter_context(pt)
            r = self.loop(builder)
        new = se.ruleset_sha256()
        self.assertNotEqual(old, new)
        self.assertEqual(r, {"status": "rules-changed", "iteration": 1,
                             "problems": ["ruleset_sha256 %s -> %s" % (old[:12], new[:12])], "returncode": 0})
        self.assertEqual(len(calls), 1)
        self.assertEqual(se.iterations(self.ws), [1])
        self.assertEqual(after, [])

    def test_a_builder_that_raises_after_moving_the_hash_is_reported_not_raised(self):
        def crash(it, sd):
            change_rule_message(self.root); raise RuntimeError("boom")
        r = self.loop(crash)
        self.assertEqual(r["status"], "rules-changed"); self.assertIn("RuntimeError: boom", r["error"])
        self.assertNotIn("returncode", r)

    def test_a_failed_builder_that_moved_the_hash_keeps_its_error(self):
        def failed(it, sd):
            change_rule_message(self.root); raise se.EvalError("builder output is not a JSON object")
        r = self.loop(failed)
        self.assertEqual((r["status"], r["error"]), ("rules-changed", "builder output is not a JSON object"))

    def plant_link(self):
        target = os.path.join(self.tmp, "t.json"); write(target, "{}")
        os.symlink(target, os.path.join(self.root, "targets", "t.json"))

    def hash_error(self):
        with self.assertRaises(se.EvalError) as cm: se.ruleset_sha256()
        return str(cm.exception).splitlines()[0]

    def test_a_builder_that_leaves_the_bundle_unhashable_stops_the_loop(self):
        old, calls, after = se.ruleset_sha256(), [], []
        def builder(it, sd):
            calls.append(it); self.plant_link(); return 0
        with contextlib.ExitStack() as stack:
            for pt in self.spies(after): stack.enter_context(pt)
            r = self.loop(builder)
        self.assertEqual(r, {"status": "rules-changed", "iteration": 1,
                             "problems": ["ruleset_sha256 %s -> %s" % (old[:12], self.hash_error())], "returncode": 0})
        self.assertIn("targets/t.json", r["problems"][0])
        self.assertEqual(len(calls), 1)
        self.assertEqual(after, [])

    def test_a_builder_that_raises_after_leaving_the_bundle_unhashable_is_reported_not_raised(self):
        old = se.ruleset_sha256()
        def crash(it, sd):
            self.plant_link(); raise RuntimeError("boom")
        r = self.loop(crash)
        self.assertEqual((r["status"], r["problems"]),
                         ("rules-changed", ["ruleset_sha256 %s -> %s" % (old[:12], self.hash_error())]))
        self.assertIn("RuntimeError: boom", r["error"]); self.assertNotIn("returncode", r)

    def test_a_failed_builder_that_left_the_bundle_unhashable_keeps_its_error(self):
        def failed(it, sd):
            self.plant_link(); raise se.EvalError("builder output is not a JSON object")
        r = self.loop(failed)
        self.assertEqual((r["status"], r["error"]), ("rules-changed", "builder output is not a JSON object"))
        self.assertNotIn("returncode", r)

    def test_an_unchanged_bundle_does_not_stop_the_loop(self):
        self.assertEqual(self.loop(lambda it, sd: 0, max_iterations=2), {"status": "needs-approval", "iteration": 2})

    def test_an_outside_write_is_reported_before_the_rule_change(self):
        def both(it, sd):
            append(os.path.join(sd, "evals", "evals.json"), " "); change_rule_message(self.root); return 0
        r = self.loop(both)
        self.assertEqual((r["status"], r["problems"]), ("builder-wrote-outside", ["evals/evals.json"]))


if __name__ == "__main__":
    unittest.main()
