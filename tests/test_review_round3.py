"""Codex P1 review round 2 (REQUEST_CHANGES), C5 fixes: G7 (the English scan of a skill reads every file a run
installs, and one table names what a run does not install), G9 (escapes in YAML flow collections and complex keys are
decoded strictly) and P2 (the registration decision is a library function, tested on a copy of the registered golden
csv-cleaner). At the C4 head 11 of these 12 tests ran red (/tmp/c5-owner/red.txt). The over-reach guard
test_literal_backslashes_in_block_style_stay_clean passed there by nature and must keep passing.
Mocked like tests/test_review_round2.py: subprocess is patched to fail for the whole module, no claude/codex/opencode/hermes
call, no network, register() and approve() are never called. ASCII only: non-English text is built from code points."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eva
import skill_eval as se
import test_eva as E
import test_skill_eval as T
import test_multi_tool_parity as P

KO = "".join(map(chr, (0xD55C, 0xAD6D, 0xC5B4)))
ESC = "\\uD55C\\uAD6D"
_patches = []

def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)

def tearDownModule():
    for pt in _patches: pt.stop()

def ids(r):
    return {c["id"] for c in r.failed}

def files_under(root):
    return sorted(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/") for dp, _, fn in os.walk(root) for f in fn)

def write_files(d, files):
    for rel, text in files.items():
        fp = os.path.join(d, *rel.split("/")); os.makedirs(os.path.dirname(fp), exist_ok=True); E.wtext(fp, text)

def append(fp, text):
    with open(fp, "a", encoding="utf-8") as f: f.write(text)

class TestInstalledFilesOutsideTheOldScanScope(P.CliBase):
    CASES = ("templates/x.md", "agents/extra.yaml", "NOTES.md")

    def korean_skill(self, rel):
        root = os.path.join(self.tmp, rel.replace("/", "-")); os.makedirs(root)
        return T.make_skill(root, evals=T.evals_doc(human=False), extra_files={rel: "note: %s\n" % KO})

    def test_validate_fails_and_names_the_file(self):
        for rel in self.CASES:
            with self.subTest(rel):
                rc, out, _ = self.cli("validate", self.korean_skill(rel))
                self.assertEqual(rc, 1, out); self.assertIn("english-only rule: %s:1 [Hangul]" % rel, out)

    def test_the_live_gates_refuse_before_the_estimate(self):
        for rel in self.CASES:
            spec, problems = se.load_eval_set(self.korean_skill(rel))
            for cmd in ("run", "loop"):
                with self.subTest(rel, cmd=cmd), mock.patch.object(se, "live_estimate") as est:
                    with self.assertRaises(se.EvalError) as cm:
                        se.check_live_gates(mock.Mock(cmd=cmd, strict_budget=True), spec, problems, self.ws, None, [],
                                            se.DEFAULT_TOKENS, {}, 100.0)
                    self.assertTrue(str(cm.exception).startswith("eval set has"), cm.exception)
                    est.assert_not_called()

class TestWhatARunDoesNotInstall(P.CliBase):
    SKIPPED = (".git/x", "references/.git/x", "FEEDBACK.md", "FEEDBACK.json")

    def test_install_hash_and_validation_leave_them_out(self):
        files = dict({rel: "%s\n" % KO for rel in self.SKIPPED}, **{"references/r.md": "ok\n"})
        d = T.make_skill(self.tmp, evals=T.evals_doc(human=False), extra_files=files)
        dst = se.ClaudeCodeEngine().install_skill(os.path.join(self.tmp, "w"), d, "branch-setup")
        self.assertEqual(files_under(dst), ["SKILL.md", "references/r.md"])
        before = se.skill_tree_hash(d)
        for rel in self.SKIPPED:
            append(os.path.join(d, rel), "x\n")
        self.assertEqual(se.skill_tree_hash(d), before)
        append(os.path.join(d, "references", "r.md"), "x\n")
        self.assertNotEqual(se.skill_tree_hash(d), before, "the hash sees an installed file")
        rc, out, _ = self.cli("validate", d)
        self.assertEqual(rc, 0, out)

class TestScanNamesExactlyTheInstalledFiles(E.TmpMixin, unittest.TestCase):
    def test_every_installed_file_is_named_by_the_scan_and_nothing_else(self):
        d = os.path.join(self.tmp, "word-count")
        line = "note: %s\n" % KO
        write_files(d, {
            "SKILL.md": E.GOOD_FM.format(name="word-count", extra="", body=KO + "\n"),
            "agents/openai.yaml": "interface:\n  display_name: %s\n" % KO, "agents/extra.yaml": line,
            "templates/x.md": line, "NOTES.md": line, "references/r.md": line, "references/deep/n.txt": line,
            "scripts/s.py": "# %s\n" % KO, "assets/a.json": json.dumps({"k": KO}),
            ".git/x": line, "references/.git/x": line, "FEEDBACK.md": line, "FEEDBACK.json": json.dumps({"k": KO}),
            "evals/evals.json": json.dumps({"skill_name": "word-count", "evals": [{"id": 1, "prompt": KO, "expected_output": "o"}]}),
            "scripts/__pycache__/m.txt": line, "notes-workspace/w.md": line, "references/evals/e.md": line})
        installed = files_under(se.ClaudeCodeEngine().install_skill(os.path.join(self.tmp, "w"), d, "word-count"))
        named = {f.split(":")[0] for cid, _, findings, _ in eva.english_scan(d).groups if cid != "english-evals"
                 for f in findings}
        self.assertEqual(set(installed), named)
        self.assertEqual(installed, ["NOTES.md", "SKILL.md", "agents/extra.yaml", "agents/openai.yaml", "assets/a.json",
                                     "references/deep/n.txt", "references/r.md", "scripts/s.py", "templates/x.md"])

FLOW_FIXTURES = (("flow mapping, value right after the colon", 'k: {"a":"%s"}\n' % ESC),
                 ("complex key", '? "%s"\n: v\n' % ESC),
                 ("multi-line flow mapping, escape on its own line", 'k: {\n  "a":"%s"\n}\n' % ESC))
LITERAL_FIXTURES = (("single-quoted block value", "pattern: '[\\uAC00-\\uD7A3]'\n"),
                    ("block scalar", "pattern: |\n  [\\uAC00-\\uD7A3]\n"),
                    ("flow sequence, then a single-quoted value", "tools: [read, write]\npattern: '[\\uAC00-\\uD7A3]'\n"))

class TestYamlFlowAndComplexKeys(E.SkillMixin, P.AgentBase, unittest.TestCase):
    def wrapper(self, text):
        d, _ = self.agent()
        E.wtext(os.path.join(d, "wrappers", "opencode", "extra.yaml"), text)
        return eva.check(d)

    def test_an_agent_wrapper_fails_and_names_the_file(self):
        for label, text in FLOW_FIXTURES:
            with self.subTest(label):
                r = self.wrapper(text)
                self.assertIn("english-wrappers", ids(r))
                self.assertIn("wrappers/opencode/extra.yaml:", P.row(r, "english-wrappers")["reason"])

    def test_a_skill_codex_metadata_file_fails(self):
        for label, text in FLOW_FIXTURES:
            with self.subTest(label):
                r = self.skill(files={"agents/openai.yaml": text})
                self.assertIn("english-tool-files", ids(r))
                self.assertIn("agents/openai.yaml:", P.row(r, "english-tool-files")["reason"])

    def test_literal_backslashes_in_block_style_stay_clean(self):
        for label, text in LITERAL_FIXTURES:
            with self.subTest(label):
                self.assertNotIn("english-wrappers", ids(self.wrapper(text)))
                self.assertNotIn("english-tool-files", ids(self.skill(files={"agents/openai.yaml": text})))

class TestRegistrationDecision(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.tmp)
        for name in ("csv-cleaner", "csv-cleaner-workspace"):
            shutil.copytree(os.path.join(T.ROOT, "golden", "skills", name), os.path.join(self.tmp, name))
        self.skill = os.path.join(self.tmp, "csv-cleaner")
        self.ws = os.path.join(self.tmp, "csv-cleaner-workspace")
        self.spec, _ = se.load_eval_set(self.skill)
        self.it = os.path.join(self.ws, "iteration-%d" % se.iterations(self.ws)[-1])
        T.install_test_approver(self)

    def decide(self):
        return se.registration_record(self.spec, self.ws)

    def test_the_unsigned_golden_registration_approval_refuses(self):
        with self.assertRaises(se.EvalError) as cm: self.decide()
        self.assertIn("registration needs `approve --gate registration` (unsigned", str(cm.exception))

    def snapshot(self):
        out = {}
        for rel in files_under(self.tmp):
            with open(os.path.join(self.tmp, rel), "rb") as f: out[rel] = f.read()
        return out

    def test_the_decision_reproduces_the_recorded_registration_and_writes_nothing(self):
        reg = se.rjson(os.path.join(self.ws, "registration.json"))
        signed = se.approve("registration", self.spec, self.ws)  # golden's own record is unsigned (H1)
        before = self.snapshot()
        rec = self.decide()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(rec["skill_path"], os.path.realpath(self.skill))
        drop = ("registered_at", "skill_path", "approval")
        self.assertEqual({k: v for k, v in rec.items() if k not in drop}, {k: v for k, v in reg.items() if k not in drop})
        self.assertTrue(se.rjson(os.path.join(self.it, "gate.json"))["needs_approval"])
        self.assertEqual(rec["approval"], signed)
        self.assertEqual(reg["approval"]["sha256"], signed["sha256"])

    def test_a_byte_change_to_an_installed_skill_file_refuses(self):
        append(os.path.join(self.skill, "scripts", "clean.py"), "\n")
        with self.assertRaises(se.EvalError): self.decide()

    def test_a_byte_change_to_the_eval_set_refuses(self):
        append(os.path.join(self.skill, "evals", "evals.json"), "\n")
        with self.assertRaises(se.EvalError): self.decide()

    def test_a_gate_file_that_disagrees_with_the_benchmark_refuses(self):
        fp = os.path.join(self.it, "gate.json")
        se.wjson(fp, dict(se.rjson(fp), edited=True))
        with self.assertRaises(se.EvalError): self.decide()

    def test_a_missing_registration_approval_refuses(self):
        fp = os.path.join(self.ws, "approvals.json")
        approvals = se.rjson(fp); del approvals["registration"]; se.wjson(fp, approvals)
        with self.assertRaises(se.EvalError): self.decide()

if __name__ == "__main__":
    unittest.main()
