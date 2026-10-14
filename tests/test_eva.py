import json, os, shutil, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva

def p(*a): return os.path.join(ROOT, *a)

class TestParser(unittest.TestCase):
    def test_frontmatter(self):
        m, b = eva.parse_frontmatter("---\nname: a-b\nlist: [x, y]\nitems:\n  - one\n  - two\ndesc: >\n  hello\n  world\n---\nbody")
        self.assertEqual(m["name"], "a-b"); self.assertEqual(m["list"], ["x", "y"])
        self.assertEqual(m["items"], ["one", "two"]); self.assertEqual(m["desc"], "hello world"); self.assertEqual(b, "body")
    def test_nested_metadata(self):
        m, _ = eva.parse_frontmatter("---\nname: a\nmetadata:\n  version: 1.2.0\n---\n")
        self.assertEqual(m["metadata"]["version"], "1.2.0")
    def test_no_frontmatter(self):
        self.assertIsNone(eva.parse_frontmatter("# x")[0])

class TestGolden(unittest.TestCase):
    def test_all_golden_pass(self):
        for u in eva.find_units(p("golden")):
            r = eva.check(u)
            self.assertEqual(r.verdict, "PASS", (u, [c for c in r.checks if not c["passed"]]))

class TestBad(unittest.TestCase):
    def failed(self, path):
        r = eva.check(p(path)); self.assertEqual(r.verdict, "FAIL")
        return {c["id"] for c in r.checks if not c["passed"]}
    def test_do_everything(self):
        f = self.failed("bad/skills/DoEverything")
        self.assertTrue({"name-format", "single-purpose", "refs-exist", "description-when"} <= f)
    def test_no_io(self):
        self.assertTrue({"inputs-declared", "outputs-declared", "example-present"} <= self.failed("bad/skills/no-io-skill"))
    def test_no_frontmatter(self):
        self.assertIn("frontmatter", self.failed("bad/skills/wrong-folder-name"))
    def test_vague_agent(self):
        f = self.failed("bad/agents/vague-agent")
        self.assertTrue({"goal-present", "success-measurable", "tools-subset", "memory-section", "multi-turn-scenario"} <= f)
    def test_every_failure_has_fix(self):
        for c in eva.check(p("bad/agents/vague-agent")).checks:
            if not c["passed"]: self.assertTrue(c["fix"])

class TestCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        shutil.copytree(p("bad"), os.path.join(self.tmp, "bad"))
    def tearDown(self): shutil.rmtree(self.tmp)
    def test_feedback_files(self):
        d = os.path.join(self.tmp, "bad/skills/no-io-skill")
        self.assertEqual(eva.main(["feedback", d, "--round", "1"]), 1)
        self.assertIn("수정:", open(os.path.join(d, "FEEDBACK.md"), encoding="utf-8").read())
        self.assertEqual(json.loads(open(os.path.join(d, "FEEDBACK.json")).read())["verdict"], "FAIL")
    def test_index(self):
        eva.main(["index", self.tmp])
        reg = json.load(open(os.path.join(self.tmp, "registry.json")))
        self.assertEqual(len(reg), 4); self.assertTrue(os.path.exists(os.path.join(self.tmp, "INDEX.md")))
    def test_transcript(self):
        r = eva.grade_transcript(p("golden/agents/research-assistant"), p("transcripts/research-followup.json"))
        self.assertEqual(r.verdict, "PASS")
    def test_transcript_illegal_tool(self):
        t = os.path.join(self.tmp, "t.json")
        json.dump({"scenario": "basic", "tool_calls": ["web_search", "send_email"], "final": "x"}, open(t, "w"))
        r = eva.grade_transcript(p("golden/agents/research-assistant"), t)
        self.assertIn("only-declared-tools", {c["id"] for c in r.checks if not c["passed"]})

if __name__ == "__main__":
    unittest.main()
