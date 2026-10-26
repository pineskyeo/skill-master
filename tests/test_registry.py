"""B5: a format-2 registration records the unit, eval set, ruleset and benchmark hashes, the approvals it relied on, a
SemVer version and a path relative to its own folder; `registration_state` verifies it now; `eva.py index` writes
registry.json only from registrations that verify and lists every unit in INDEX.md with its registration state.

Signing uses only the throwaway test key of tests/test_skill_eval.py under /tmp; subprocess is patched for the whole
module and only that key's runner starts /usr/bin/ssh-keygen. register is reached only as a Python call on temp
fixtures with fake engines. Every record fixture is built here in a temp folder. ASCII only."""
import contextlib, importlib, inspect, io, json, os, random, shutil, subprocess, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_coverage_gate as C
import test_ruleset_binding as RB
import test_eva as E
from test_skill_eval import se, eva, evals_doc, make_skill, write, wjson, rjson, read
from engine import schema as S

ROOT = T.ROOT
REGISTRATION_SCHEMA = os.path.join(ROOT, "schemas", "registration.schema.json")
REGISTRY_SCHEMA = os.path.join(ROOT, "schemas", "registry.schema.json")
BODY = T.SKILL_MD[T.SKILL_MD.index("# Branch Setup"):]
DESCRIPTION = ("Creates a git worktree and branch for new work. Use when the user starts a new task and wants a fresh "
               "branch.")
HEADER = "| kind | name | version | verdict | score | english | parity | registration | path | description |"
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def skill_text(version, body=BODY):
    meta = "metadata:\n  version: %s\n" % version if version is not None else ""
    return "---\nname: branch-setup\ndescription: %s\n%s---\n%s" % (DESCRIPTION, meta, body)


def errors(schema_path, instance):
    return S.compile_schema(S.load(schema_path)).validate(instance)


def registry():
    return importlib.import_module("engine.registry")


class Reg(C.Cov):
    """C.Cov (temp home, temp ruleset root, the test approver typing a waiver reason) plus a skill at
    repo/skills/branch-setup with SKILL.md metadata.version 1.0.0 and its default workspace."""
    def setUp(self):
        C.Cov.setUp(self)
        self.repo = os.path.join(self.tmp, "repo")
        self.d = make_skill(os.path.join(self.repo, "skills"), evals=evals_doc(human=False))
        self.set_version("1.0.0")
        self.spec = se.load_eval_set(self.d)[0]
        self.ws = se.default_workspace(self.spec)
        os.makedirs(self.ws); write(os.path.join(self.ws, "requirements.md"), "goal")
        self.approvals = os.path.join(self.ws, "approvals.json")
        self.reg = os.path.join(self.ws, "registration.json")

    def set_version(self, version, body=BODY):
        write(os.path.join(self.d, "SKILL.md"), skill_text(version, body))

    def iteration(self, regress=False):
        n = self.run_both(self.engines(regress=regress))
        self.waive("opencode"); self.waive("hermes")
        self.report(n)
        return n

    def registered(self):
        self.iteration()
        return se.register(self.spec, self.ws)

    def state(self, ws=None, unit=None):
        return se.registration_state(unit or self.d, ws or self.ws)

    def index(self, root=None):
        root = root or self.repo
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = eva.main(["index", root])
        return rc, out.getvalue(), read(os.path.join(root, "registry.json")), read(os.path.join(root, "INDEX.md"))

    def refused(self):
        del self.events[:]
        with self.assertRaises(se.EvalError) as cm: se.register(self.spec, self.ws)
        self.assertEqual(self.asked("challenge"), [], "refused before the challenge")
        return str(cm.exception)


class TestSemver(unittest.TestCase):
    def test_parse_accepts_semver_and_refuses_everything_else(self):
        R = registry()
        good = {"1.0.0": (1, 0, 0, ()), "0.0.0": (0, 0, 0, ()), "10.20.30": (10, 20, 30, ()),
                "1.2.3-alpha.1": (1, 2, 3, ("alpha", "1")), "1.0.0-0.3.7": (1, 0, 0, ("0", "3", "7")),
                "1.0.0-x-y-z.--": (1, 0, 0, ("x-y-z", "--")), "1.0.0+build.5": (1, 0, 0, ()),
                "1.0.0-rc.1+exp.sha.5114f85": (1, 0, 0, ("rc", "1"))}
        for text, want in good.items():
            self.assertEqual(R.parse_semver(text), want, text)
        for text in ("1.0", "1", "v1.0.0", "01.0.0", "1.01.0", "1.0.00", "1.0.0-01", "1.0.0-", "1.0.0+", "1.0.0-a..b",
                     "1.0.0+a..b", " 1.0.0", "1.0.0 ", "1.0.0\n", "1.0.0-" + chr(0xE9), chr(0xFF11) + ".0.0", "", None, 1,
                     1.0):
            self.assertIsNone(R.parse_semver(text), repr(text))

    def test_order_follows_semver_section_11(self):
        R = registry()
        want = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11",
                "1.0.0-rc.1", "1.0.0", "1.0.1", "1.9.0", "1.10.0", "1.11.0", "2.0.0", "2.1.0", "2.1.1"]
        shuffled = list(want); random.Random(5).shuffle(shuffled)
        self.assertEqual(sorted(shuffled, key=R.semver_key), want)
        self.assertEqual(R.semver_key("1.0.0+a"), R.semver_key("1.0.0+b"))
        self.assertLess(R.semver_key("1.0.0-2"), R.semver_key("1.0.0-10"))
        self.assertLess(R.semver_key("1.0.0-10"), R.semver_key("1.0.0-a"))

    def test_the_version_is_metadata_version_then_version(self):
        R = registry()
        self.assertEqual(R.version_of({"metadata": {"version": "1.2.0"}, "version": "9.9.9"}), "1.2.0")
        self.assertEqual(R.version_of({"version": "2.0.0"}), "2.0.0")
        self.assertEqual(R.version_of({"metadata": "x", "version": "2.0.0"}), "2.0.0")
        self.assertEqual(R.version_of({}), "")

    def test_the_registry_sorts_by_kind_name_then_version_descending(self):
        R = registry()
        items = [("skill", "b", "1.0.0"), ("skill", "a", "1.0.0"), ("skill", "a", "1.10.0"), ("skill", "a", "1.2.0"),
                 ("agent", "z", ""), ("skill", "a", "not-semver"), ("skill", "a", "2.0.0-rc.1")]
        got = R.ordered(items, kind=lambda x: x[0], name=lambda x: x[1], version=lambda x: x[2], path=lambda x: "")
        self.assertEqual(got, [("agent", "z", ""), ("skill", "a", "2.0.0-rc.1"), ("skill", "a", "1.10.0"),
                               ("skill", "a", "1.2.0"), ("skill", "a", "1.0.0"), ("skill", "a", "not-semver"),
                               ("skill", "b", "1.0.0")])


class TestRecord(Reg):
    def test_a_verifying_registration_records_every_field_and_reads_registered(self):
        rec = self.registered()
        self.assertEqual(rjson(self.reg), rec)
        self.assertEqual((rec["registration_format"], rec["kind"], rec["skill_name"], rec["version"], rec["skill_path"]),
                         (2, "skill", "branch-setup", "1.0.0", "../branch-setup"))
        data = rjson(self.approvals)
        self.assertEqual(rec["approvals"], {g: data[g] for g in ("coverage_waiver:opencode", "coverage_waiver:hermes")})
        self.assertEqual(rec["approval"], "auto")
        self.assertEqual(rec["ruleset_sha256"], se.ruleset_sha256())
        self.assertEqual(rec["benchmark_sha256"], se.gate_hash("iteration-1", self.spec, self.ws))
        self.assertEqual(rec["eval_set_sha256"], se.gate_hash("eval_set", self.spec, self.ws))
        self.assertEqual(rec["skill_sha256"], se.skill_tree_hash(self.d))
        self.assertEqual(errors(REGISTRATION_SCHEMA, rec), [])
        self.assertEqual(set(se.REGISTRATION_SIGNED), set(rec) - {"signed"}, "every field but the signature is signed")
        self.assertEqual(self.state(), ("registered", "", rec))

    def test_every_signed_field_edit_breaks_the_registration(self):
        rec = self.registered()
        edits = {"registration_format": 3, "kind": "agent", "skill_name": "other-skill", "version": "1.0.1",
                 "skill_path": "../branch-setup/.", "iteration": 2, "benchmark_sha256": "0" * 64,
                 "skill_sha256": "0" * 64, "eval_set_sha256": "0" * 64, "skill_md_sha256": "0" * 64,
                 "ruleset_sha256": "0" * 64, "approvals": {}, "approval": dict(rec["signed"]),
                 "registered_at": "2000-01-01T00:00:00Z"}
        self.assertEqual(set(edits), set(se.REGISTRATION_SIGNED))
        for field, value in edits.items():
            with self.subTest(field):
                wjson(self.reg, dict(rec, **{field: value}))
                self.assertEqual(self.state()[0], "invalid", self.state()[:2])
        wjson(self.reg, rec)
        self.assertEqual(self.state()[0], "registered")

    def test_a_tampered_hash_reads_invalid_and_never_reaches_the_registry(self):
        rec = self.registered()
        wjson(self.reg, dict(rec, skill_sha256="0" * 64))
        self.assertEqual(self.state()[:2], ("invalid", "the record does not match its signature"))
        rc, out, text, md = self.index()
        self.assertEqual(json.loads(text)["units"], [])
        self.assertIn("| invalid (the record does not match its signature) |", md)

    def test_an_unsigned_registration_approval_refuses_and_a_signed_one_is_recorded(self):
        self.iteration(regress=True)
        data = rjson(self.approvals)
        data["registration"] = {"sha256": se.gate_hash("registration", self.spec, self.ws), "by": "pinesky",
                                "at": C.AT}
        wjson(self.approvals, data)
        msg = self.refused()
        self.assertIn("needs `approve --gate registration`", msg); self.assertIn("unsigned", msg)
        self.assertFalse(os.path.exists(self.reg))
        signed = se.approve("registration", self.spec, self.ws)
        rec = se.register(self.spec, self.ws)
        self.assertEqual(rec["approval"], signed)
        self.assertEqual(rec["approvals"]["registration"], signed)
        self.assertEqual(sorted(rec["approvals"]), ["coverage_waiver:hermes", "coverage_waiver:opencode", "registration"])
        self.assertEqual(self.state()[0], "registered")

    def test_a_record_without_a_signature_reads_unsigned(self):
        rec = self.registered()
        wjson(self.reg, {k: v for k, v in rec.items() if k != "signed"})
        self.assertEqual(self.state()[:2], ("unsigned", "no register signature"))

    def test_the_golden_legacy_record_reads_stale_legacy_absolute_and_is_not_in_the_registry(self):
        g = os.path.join(self.tmp, "g")
        for name in ("csv-cleaner", "csv-cleaner-workspace"):
            shutil.copytree(E.p("golden", "skills", name), os.path.join(g, "skills", name))
        state, reason, rec = se.registration_state(os.path.join(g, "skills", "csv-cleaner"))
        self.assertEqual((state, reason), ("stale", "legacy-absolute"))
        self.assertTrue(os.path.isabs(rec["skill_path"]))
        rc, out, text, md = self.index(g)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(text), {"registry_format": 1, "units": []})
        row = next(x for x in md.splitlines() if "| csv-cleaner |" in x)
        self.assertIn("| stale (legacy-absolute) |", row)


class TestStates(Reg):
    def test_no_file_unreadable_and_not_format_2(self):
        self.assertEqual(self.state()[:2], ("unregistered", "no registration.json"))
        write(self.reg, "{")
        self.assertEqual(self.state()[:2], ("invalid", "registration.json is not readable JSON"))
        write(self.reg, "[]")
        self.assertEqual(self.state()[:2], ("invalid", "registration.json is not a JSON object"))
        wjson(self.reg, {"skill_name": "branch-setup", "skill_path": "../branch-setup", "iteration": 1})
        self.assertEqual(self.state()[:2], ("invalid", "not registration format 2"))
        rec = self.registered()
        wjson(self.reg, dict(rec, extra=1))
        self.assertEqual(self.state()[:2], ("invalid", "does not match schemas/registration.schema.json"))

    def test_an_agent_is_unregistered(self):
        state, reason, rec = se.registration_state(E.p("golden", "agents", "code-reviewer"))
        self.assertEqual((state, reason, rec), ("unregistered", "no registration.json", None))

    def test_a_moved_workspace_reads_moved_and_a_copy_of_the_whole_tree_still_verifies(self):
        self.registered()
        other = os.path.join(self.tmp, "elsewhere", "ws")
        shutil.copytree(self.ws, other)
        self.assertEqual(self.state(ws=other)[:2], ("stale", "moved"))
        clone = os.path.join(self.tmp, "clone")
        shutil.copytree(self.repo, clone)
        self.assertEqual(se.registration_state(os.path.join(clone, "skills", "branch-setup"))[:2], ("registered", ""))

    def test_a_rule_change_reads_stale_rules_changed(self):
        self.registered()
        RB.change_rule_message(self.root)
        self.assertEqual(self.state()[:2], ("stale", "rules changed"))

    def test_a_changed_file_reads_stale_and_names_the_first_changed_field(self):
        self.registered()
        notes = os.path.join(self.d, "notes.md")
        write(notes, "more\n")
        self.assertEqual(self.state()[:2], ("stale", "skill_sha256 changed"))
        os.remove(notes)
        self.assertEqual(self.state()[0], "registered")
        tp = os.path.join(self.d, "evals", "trigger_evals.json")
        before = read(tp)
        write(tp, before + "\n")
        self.assertEqual(self.state()[:2], ("stale", "eval_set_sha256 changed"))
        write(tp, before)
        bp = os.path.join(self.ws, "iteration-1", "benchmark.json")
        before = read(bp)
        write(bp, before + "\n")
        self.assertEqual(self.state()[:2], ("stale", "benchmark_sha256 changed"))
        write(bp, before)
        self.assertEqual(self.state()[0], "registered")

    def test_a_relied_on_approval_that_no_longer_verifies_reads_stale_and_names_the_gate(self):
        self.iteration(regress=True)
        se.approve("registration", self.spec, self.ws)
        se.register(self.spec, self.ws)
        self.assertEqual(self.state()[0], "registered")
        os.makedirs(os.path.join(self.ws, "iteration-2"))
        write(os.path.join(self.ws, "iteration-2", "benchmark.json"), "{}\n")
        self.assertEqual(self.state()[:2], ("stale", "approval registration no longer verifies"))

    def test_the_verify_now_function_reads_only_records_and_hashes(self):
        for src in (inspect.getsource(se.registration_state), read(os.path.join(ROOT, "engine", "registry.py"))):
            for word in ("eva.", "check_skill", "check_agent", "english_", "tool_coverage", "coverage", "parity",
                         "opencode", "hermes", "load_eval_set"):
                self.assertNotIn(word, src.replace("eva.detect_kind", "").replace("eva.read_text", ""), word)
        self.registered()
        boom = AssertionError("not a record or hash read")
        with mock.patch.object(eva, "check", side_effect=boom), mock.patch.object(eva, "parity_status", side_effect=boom), \
                mock.patch.object(se, "runs_coverage", side_effect=boom), \
                mock.patch.object(se, "load_eval_set", side_effect=boom):
            self.assertEqual(self.state()[0], "registered")


class TestVersionRefusals(Reg):
    def test_a_missing_or_malformed_version_refuses_before_signing(self):
        for version in (None, "1.0", "v1.0.0", "01.0.0"):
            with self.subTest(version):
                self.set_version(version)
                self.iteration()
                msg = self.refused()
                self.assertIn("version", msg)
                self.assertFalse(os.path.exists(self.reg))

    def test_a_lower_or_unbumped_version_refuses_and_a_bump_registers(self):
        first = self.registered()
        self.set_version("0.9.0")
        self.iteration()
        self.assertIn("lower than the registered version 1.0.0", self.refused())
        self.set_version("1.0.0", BODY + "\nMore.\n")
        self.iteration()
        self.assertIn("bump the version", self.refused())
        self.assertEqual(rjson(self.reg), first)
        self.set_version("1.0.1", BODY + "\nMore.\n")
        self.iteration()
        rec = se.register(self.spec, self.ws)
        self.assertEqual(rec["version"], "1.0.1")
        again = se.register(self.spec, self.ws)
        self.assertEqual((again["version"], self.state()[0]), ("1.0.1", "registered"))

    def test_a_record_its_schema_refuses_is_never_signed(self):
        self.iteration()
        os.rename(os.path.join(self.ws, "iteration-1"), os.path.join(self.ws, "iteration-0"))
        self.assertIn("schemas/registration.schema.json", self.refused())
        self.assertFalse(os.path.exists(self.reg))

    def test_a_legacy_or_unreadable_old_record_sets_no_bound(self):
        self.iteration()
        shutil.copyfile(E.p("golden", "skills", "csv-cleaner-workspace", "registration.json"), self.reg)
        self.assertEqual(se.register(self.spec, self.ws)["version"], "1.0.0")
        write(self.reg, "{")
        self.assertEqual(se.register(self.spec, self.ws)["version"], "1.0.0")


class TestSchemas(Reg):
    def test_the_registration_schema_is_closed_and_refuses_absolute_paths_and_bad_versions(self):
        rec = self.registered()
        self.assertEqual(errors(REGISTRATION_SCHEMA, rec), [])
        self.assertEqual(errors(REGISTRATION_SCHEMA, {k: v for k, v in rec.items() if k != "signed"}), [])
        for bad in (dict(rec, skill_path="/abs/branch-setup"), dict(rec, skill_path="~/branch-setup"),
                    dict(rec, version="v1.0.0"), dict(rec, version="1.0"), dict(rec, registration_format=1),
                    dict(rec, kind="tool"), dict(rec, extra=1), dict(rec, approvals={"eval_set": rec["signed"]}),
                    dict(rec, approvals={"coverage_waiver:hermes": rec["signed"]}),
                    {k: v for k, v in rec.items() if k != "approvals"},
                    {k: v for k, v in rec.items() if k != "version"}):
            self.assertTrue(errors(REGISTRATION_SCHEMA, bad), bad)

    def test_the_registry_schema_validates_the_index_output_and_is_closed(self):
        self.registered()
        rc, out, text, md = self.index()
        doc = json.loads(text)
        self.assertEqual(errors(REGISTRY_SCHEMA, doc), [])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(eva.main(["schema-check", REGISTRY_SCHEMA, os.path.join(self.repo, "registry.json")]), 0)
        self.assertIn("schema-check: valid", out.getvalue())
        entry = doc["units"][0]
        for bad in (dict(doc, extra=1), dict(doc, registry_format=2), {"registry_format": 1},
                    dict(doc, units=[dict(entry, extra=1)]), dict(doc, units=[dict(entry, path="/abs")]),
                    dict(doc, units=[dict(entry, version="1.0")])):
            self.assertTrue(errors(REGISTRY_SCHEMA, bad), bad)

    def test_the_registry_schema_takes_a_skill_or_an_agent_entry_and_no_other_kind(self):
        self.registered()
        doc = json.loads(self.index()[2])
        entry = doc["units"][0]
        self.assertEqual(entry["kind"], "skill")
        self.assertEqual(errors(REGISTRY_SCHEMA, doc), [])
        self.assertEqual(errors(REGISTRY_SCHEMA, dict(doc, units=[dict(entry, kind="agent")])), [])
        for kind in ("tool", "Skill", "", None, 1):
            self.assertTrue(errors(REGISTRY_SCHEMA, dict(doc, units=[dict(entry, kind=kind)])), kind)

    def test_no_text_claims_that_agents_have_no_registration(self):
        for rel in ("engine/registry.py", "skill_eval.py", "eva.py"):
            text = read(os.path.join(ROOT, *rel.split("/")))
            for claim in ("agents have no registration", "no agent registration", "waivers are not read yet"):
                self.assertFalse(claim in text, (rel, claim))


class TestIndex(Reg):
    def test_index_writes_only_verifying_registrations_and_lists_every_unit(self):
        rec = self.registered()
        plain = os.path.join(self.repo, "skills", "plain-skill")
        os.makedirs(plain)
        write(os.path.join(plain, "SKILL.md"), E.GOOD_FM.format(name="plain-skill", extra="", body="# Plain\n"))
        shutil.copytree(E.p("golden", "agents", "code-reviewer"), os.path.join(self.repo, "agents", "code-reviewer"))
        rc, out, text, md = self.index()
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip().splitlines()[-1], "indexed 3 units -> INDEX.md, registry.json (1 registered)")
        doc = json.loads(text)
        self.assertEqual(text, json.dumps(doc, sort_keys=True, ensure_ascii=True, indent=2) + "\n")
        self.assertEqual(doc["units"], [{
            "kind": "skill", "name": "branch-setup", "version": "1.0.0", "path": "skills/branch-setup",
            "workspace": "skills/branch-setup-workspace", "iteration": 1,
            "benchmark_sha256": rec["benchmark_sha256"], "skill_sha256": rec["skill_sha256"],
            "eval_set_sha256": rec["eval_set_sha256"], "skill_md_sha256": rec["skill_md_sha256"],
            "ruleset_sha256": rec["ruleset_sha256"], "registered_at": rec["registered_at"],
            "registered_by": T.TEST_PRINCIPAL,
            "approvals": [{"gate": "coverage_waiver:hermes", "principal": T.TEST_PRINCIPAL, "reason": C.REASON},
                          {"gate": "coverage_waiver:opencode", "principal": T.TEST_PRINCIPAL, "reason": C.REASON}]}])
        lines = md.splitlines()
        self.assertIn(HEADER, lines)
        rows = {ln.split(" | ")[1]: ln for ln in lines if ln.startswith("| skill |") or ln.startswith("| agent |")}
        self.assertEqual(sorted(rows), ["branch-setup", "code-reviewer", "plain-skill"])
        self.assertIn("| registered 1.0.0 |", rows["branch-setup"])
        self.assertIn("| unregistered |", rows["plain-skill"]); self.assertIn("| unregistered |", rows["code-reviewer"])
        write(os.path.join(self.d, "notes.md"), "more\n")
        rc, out, text, md = self.index()
        self.assertEqual(json.loads(text)["units"], [])
        self.assertIn("| stale (skill_sha256 changed) |", md)
        self.assertTrue(out.strip().endswith("(0 registered)"), out)


if __name__ == "__main__":
    unittest.main()
