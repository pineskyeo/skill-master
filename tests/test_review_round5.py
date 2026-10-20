"""Codex P1 review round 4 (REQUEST_CHANGES), C7 fix. A test module whose docstring says the file is ASCII holds only
ASCII bytes, and eva.py and skill_eval.py hold no invisible format character (Unicode category Cf, such as U+FEFF or a
zero-width space). At the C6 head both tests ran red (/tmp/c7-owner/red.txt): literal non-English text in
tests/test_english_rule.py and tests/test_multi_tool_parity.py, and a literal U+FEFF at eva.py:1279. The fix spells each
of those characters as an escape inside its string literal, so every parsed value stays the same.
Reads source files only: no subprocess, no claude/codex/opencode/hermes call, no network. ASCII only: characters are
named by code point."""
import ast, glob, os, re, unicodedata, unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
CLAIM = re.compile(r"ASCII only|stays ASCII")
MUST_CLAIM = {"test_english_rule.py", "test_multi_tool_parity.py", "test_review_round1.py", "test_review_round2.py",
              "test_review_round3.py", "test_review_round4.py", "test_review_round5.py"}

def claims_ascii(path):
    with open(path, "rb") as f:
        doc = ast.get_docstring(ast.parse(f.read())) or ""
    return bool(CLAIM.search(" ".join(doc.split())))

def non_ascii_lines(path):
    with open(path, "rb") as f:
        return [n for n, ln in enumerate(f.read().split(b"\n"), 1) if any(b > 127 for b in ln)]

class TestAsciiClaim(unittest.TestCase):
    def test_a_test_file_that_claims_ascii_holds_only_ascii_bytes(self):
        claiming = {os.path.basename(p): p for p in sorted(glob.glob(os.path.join(TESTS, "*.py"))) if claims_ascii(p)}
        self.assertLessEqual(MUST_CLAIM, set(claiming), sorted(claiming))
        bad = ["tests/%s:%d" % (name, n) for name, p in sorted(claiming.items()) for n in non_ascii_lines(p)]
        self.assertEqual(bad, [])

class TestFormatCharacters(unittest.TestCase):
    def test_product_code_holds_no_format_character(self):
        bad = []
        for rel in ("eva.py", "skill_eval.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                for n, ln in enumerate(f.read().split("\n"), 1):
                    bad += ["%s:%d U+%04X" % (rel, n, ord(c)) for c in ln if unicodedata.category(c) == "Cf"]
        self.assertEqual(bad, [])

if __name__ == "__main__":
    unittest.main()
