"""The engine files outside eva.py hold English text only, and translating them changed no behavior.
The scan reads source files only: it never imports, runs or execs loop.py or claude_bash_gate.py, and calls no
subprocess, no claude/codex/opencode/hermes binary and no network.
Contract: the meta-test reports non-English text (as eva.non_english_hits classifies it) in the five engine files that
is written literally anywhere in a file, or that appears in the value of any Python string or bytes literal (adjacent
literals joined as Python joins them, joined bytes read as UTF-8 with replacement) or in the full text of LOOP.md once
these reader decodings are applied again and again until the text stops changing (at most DECODE_PASSES passes):
backslash escapes (u with four hex digits, U with eight, N with a name, the JavaScript braced form), HTML character
references with or without the semicolon as html.unescape reads them, and percent-encoded UTF-8 (runs of % plus two hex
digits, read as UTF-8 with replacement); after each pass every high surrogate followed by a low surrogate is joined into
one character, whatever spelled them. Out of the contract: text built by running code (for example chr,
String.fromCharCode, atob, unescape, decodeURIComponent, base64, bytes.decode, a + join of literals) and CSS escapes.
This file stays ASCII only: Hangul in the test data is written as escapes or chr() calls."""
import ast, codecs, contextlib, html, io, os, re, subprocess, sys, tempfile, tokenize, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva, skill_eval as se

PY_FILES = ("skill_eval.py", "label_blind.py", "loop.py", "claude_bash_gate.py")
MD_FILES = ("LOOP.md",)
GAPS_IN_A_STRING_GROUP = (tokenize.NL, tokenize.COMMENT)
SLUG_PATTERN = 'r"[^0-9A-Za-z\\uAC00-\\uD7A3]+"'
EVAL_DIR_PATTERN = 'r"^[0-9A-Za-z\\uAC00-\\uD7A3._-]+$"'
# skill_eval.slug and eval_dir_name match Korean eval names with these two tokens; neither is shown to a reader or a user.
DECODE_EXEMPT_TOKENS = {"skill_eval.py": (SLUG_PATTERN, EVAL_DIR_PATTERN)}
ESCAPE = re.compile(r"\\(?:u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|N\{[^}\n]*\})|\\u\{([0-9A-Fa-f]+)\}"
                    r"|&(?:#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[0-9A-Za-z]{1,32};?)|(?:%[0-9A-Fa-f]{2})+")
DECODE_PASSES = 4
SURROGATE_PAIR = re.compile("[%s-%s][%s-%s]" % (chr(0xD800), chr(0xDBFF), chr(0xDC00), chr(0xDFFF)))
H = chr(0xD55C)
BS = chr(92)


def char_lines(text, first, last):
    """The source line of each character of text, counted from first and capped at last."""
    lines, line = [], first
    for c in text:
        lines.append(min(line, last))
        line += c == "\n"
    return lines


def hits(text, lines):
    return {(lines[i], script) for i, _, script in eva.non_english_hits(text)}


def decode_one(match):
    text = match.group()
    try:
        if text.startswith("&"):
            return html.unescape(text)
        if text.startswith("%"):
            return bytes.fromhex(text.replace("%", "")).decode("utf-8", "replace")
        if match.group(1):
            return chr(int(match.group(1), 16))
        return text.encode("ascii").decode("unicode_escape")
    except (UnicodeError, ValueError, OverflowError):
        return text


def joined_pairs(text, lines):
    """text with every high surrogate followed by a low surrogate joined into one character on the high's line."""
    out, out_lines, at = [], [], 0
    for m in SURROGATE_PAIR.finditer(text):
        out += [text[at:m.start()], m.group().encode("utf-16", "surrogatepass").decode("utf-16")]
        out_lines += lines[at:m.start() + 1]
        at = m.end()
    return "".join(out) + text[at:], out_lines + lines[at:]


def decoded(text, lines):
    """text as a later reader of it (re, json, a browser, a Markdown renderer) shows it, with its lines; a decoded
    escape sits on the line where it starts."""
    out, out_lines, at = [], [], 0
    m = ESCAPE.search(text)
    while m:
        value = decode_one(m)
        if value == m.group():
            # a candidate that does not decode stays plain text, so an escape it spans is matched on its own
            m = ESCAPE.search(text, m.start() + 1)
            continue
        out += [text[at:m.start()], value]
        out_lines += lines[at:m.start()] + [lines[m.start()]] * len(value)
        at = m.end()
        m = ESCAPE.search(text, at)
    return "".join(out) + text[at:], out_lines + lines[at:]


def read(text, lines):
    """Applies decoded until the text stops changing, at most DECODE_PASSES times: one reader's output is the next
    reader's input, as when an HTML attribute decodes to a JavaScript escape."""
    for _ in range(DECODE_PASSES):
        new_text, new_lines = joined_pairs(*decoded(text, lines))
        if new_text == text:
            break
        text, lines = new_text, new_lines
    return text, lines


def string_groups(tokens):
    """Runs of STRING tokens that Python joins into one string: only NL and COMMENT tokens sit between them."""
    group = []
    for tok in tokens:
        if tok.type == tokenize.STRING:
            group.append(tok)
        elif tok.type not in GAPS_IN_A_STRING_GROUP and group:
            yield group
            group = []


def reader_text(tok):
    """A STRING token's text as its reader gets it: a raw body as written, bytes as Python builds them (the group
    decodes them), any other value as Python builds it."""
    prefix = re.match(r"[A-Za-z]*", tok.string).group().lower()
    body = tok.string[len(prefix):]
    if "b" in prefix:
        return ast.literal_eval(prefix + body)
    if "r" in prefix:
        quote = 3 if body[:3] in ('"""', "'''") else 1
        return body[quote:-quote]
    return ast.literal_eval(prefix.replace("f", "") + body)


def string_findings(name, path):
    found = set()
    with open(path, "rb") as f:
        for group in string_groups(tokenize.tokenize(f.readline)):
            parts, lines = [], []
            for tok in group:
                if tok.string not in DECODE_EXEMPT_TOKENS.get(name, ()):
                    part = reader_text(tok)
                    parts.append(part)
                    as_text = part.decode("latin-1") if isinstance(part, bytes) else part
                    lines += char_lines(as_text, tok.start[0], tok.end[0])
            if parts and isinstance(parts[0], bytes):
                text, lines = utf_8_text(b"".join(parts), lines)
            else:
                text = "".join(parts)
            found |= hits(*read(text, lines))
    return found


def utf_8_text(data, byte_lines):
    """data read as UTF-8 with replacement, each character on the line of the byte it starts at."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    text, lines, start = "", [], 0
    for i in range(len(data)):
        part = decoder.decode(data[i:i + 1])
        text, lines = text + part, lines + [byte_lines[start]] * len(part)
        start = i + 1 - len(decoder.getstate()[0])
    part = decoder.decode(b"", True)
    return text + part, lines + byte_lines[start:start + 1] * len(part)


def file_findings(root, name):
    path = os.path.join(root, name)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    lines = char_lines(text, 1, text.count("\n") + 1)
    found = hits(text, lines)
    return found | (string_findings(name, path) if name.endswith(".py") else hits(*read(text, lines)))


def engine_findings(root):
    return ["%s:%d %s" % (name, line, script)
            for name in PY_FILES + MD_FILES for line, script in sorted(file_findings(root, name))]


def planted_findings(name, source):
    with tempfile.TemporaryDirectory() as d:
        for each in PY_FILES + MD_FILES:
            with open(os.path.join(d, each), "w", encoding="utf-8") as f:
                f.write(source if each == name else "")
        return engine_findings(d)


class TestEngineIsEnglish(unittest.TestCase):
    def test_engine_strings_comments_and_loop_doc_hold_no_non_english_script(self):
        found = engine_findings(ROOT)
        self.assertEqual(found, [], "non-English text in the engine files:\n" + "\n".join(found))

    def test_an_escaped_message_is_still_caught(self):
        source = 'x = 1\nMSG = "\\ud55c\\uae00"\nPAT = r"[\\uAC00-\\uD7A3]"\n'
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 Hangul", "label_blind.py:3 Hangul"])

    def test_an_escaped_message_in_an_f_string_is_caught(self):
        source = 'x = 1\nMSG = f"\\ud55c {x}"\n'
        self.assertEqual(planted_findings("skill_eval.py", source), ["skill_eval.py:2 Hangul"])

    def test_an_escaped_word_in_a_raw_page_is_caught_on_its_own_line(self):
        source = 'x = 1\nPAGE = r"""<p>\n<b>\\ud55c</b>\n"""\n'
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:3 Hangul"])

    def test_an_escape_left_in_a_plain_string_for_a_browser_is_caught(self):
        source = 'x = 1\nPAGE = "<b>\\\\ud55c</b>"\n'
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 Hangul"])

    def test_long_and_named_escapes_in_a_raw_string_are_caught(self):
        source = 'x = 1\nA = r"\\U0000d55c"\nB = r"\\N{HANGUL SYLLABLE HAN}"\n'
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 Hangul", "label_blind.py:3 Hangul"])

    def test_browser_escapes_in_a_raw_page_are_caught(self):
        source = 'x = 1\nPAGE = r"""<b>&#xD55C;</b>\n<i>&#54620;</i>\n<script>alert("\\u{d55c}")</script>"""\n'
        self.assertEqual(planted_findings("label_blind.py", source),
                         ["label_blind.py:2 Hangul", "label_blind.py:3 Hangul", "label_blind.py:4 Hangul"])

    def test_a_surrogate_pair_in_a_raw_string_is_caught(self):
        source = 'x = 1\nPAGE = r"""<b>\\ud840\\udc00</b>"""\n'
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 CJK"])

    def test_the_korean_name_matcher_range_is_not_reported(self):
        source = 'x = 1\nPAT = r"[^0-9A-Za-z\\uAC00-\\uD7A3]+"\n'
        self.assertEqual(planted_findings("skill_eval.py", source), [])

    def test_hangul_next_to_the_matcher_range_is_still_reported(self):
        source = 'x = 1\nPAT = r"[^0-9A-Za-z\\uAC00-\\uD7A3]+\\ud55c"\n'
        self.assertEqual(planted_findings("skill_eval.py", source), ["skill_eval.py:2 Hangul"])

    def test_a_hangul_comment_is_reported_in_each_engine_file(self):
        for name in ("skill_eval.py", "label_blind.py", "loop.py", "claude_bash_gate.py", "LOOP.md"):
            with self.subTest(name=name):
                comment = "<!-- %s -->" % H if name.endswith(".md") else "# " + H
                self.assertEqual(planted_findings(name, "x = 1\n" + comment + "\n"), ["%s:2 Hangul" % name])

    def test_a_hangul_comment_and_a_hangul_identifier_are_both_reported(self):
        source = "x = 1\n# " + H + "\n" + H + " = 2\n"
        self.assertEqual(planted_findings("skill_eval.py", source), ["skill_eval.py:2 Hangul", "skill_eval.py:3 Hangul"])

    def test_numeric_entities_without_a_semicolon_in_a_raw_page_are_reported(self):
        source = 'x = 1\nPAGE = r"""<b>&#xD55C</b>\n<i>&#54620</i>"""\n'
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 Hangul", "label_blind.py:3 Hangul"])

    def test_numeric_and_named_entities_in_loop_md_are_reported(self):
        source = "# Title\n\nSee &#xD55C; and &#54620\nthen &dcy;\n"
        self.assertEqual(planted_findings("LOOP.md", source), ["LOOP.md:3 Hangul", "LOOP.md:4 Cyrillic"])

    def test_an_escape_split_across_adjacent_literals_is_reported(self):
        source = 'x = 1\nPAGE = r"<b>&#x" r"D55C;</b>"\nJS = (r"alert(\'\\u"  # split\n      r"d55c\')")\n'
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 Hangul", "label_blind.py:3 Hangul"])

    def test_an_escape_glued_to_an_ampersand_in_a_raw_page_is_reported(self):
        source = ('x = 1\nPAGE = r"""<script>alert(\'R&D\\ud55c\')\nmsg = \'a=1&b=\\ud55c\'\n'
                  'alert(\'R&D\\N{HANGUL SYLLABLE HAN}\')\nalert(\'R&D\\u{d55c}\')</script>"""\n')
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:%d Hangul" % n for n in (2, 3, 4, 5)])

    def test_an_escape_glued_to_a_reference_without_its_semicolon_is_reported(self):
        source = 'x = 1\nPAGE = r"""<p>&copy\\ud55c</p>\n<p>AT&lt\\u{d55c}</p>"""\n'
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 Hangul", "label_blind.py:3 Hangul"])

    def test_an_escape_inside_a_named_escape_that_does_not_decode_is_reported(self):
        source = ('x = 1\nPAGE = r"""<script>alert(\'\\N{x\\ud55c}\')</script>\n<p>\\N{x&#xD55C;}</p>\n'
                  '<p>\\N{x\\N{HANGUL SYLLABLE HAN}}</p>\n<i>\\N{x\\u{d55c}}</i>"""\n')
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:%d Hangul" % n for n in (2, 3, 4, 5)])

    def test_bytes_literals_are_read_as_utf_8_and_decoded(self):
        source = ('x = 1\nOUT = b"' + BS + 'xed' + BS + 'x95' + BS + 'x9c"\n'
                  'DATA = b\'"' + BS + BS + 'ud55c"\'\n'
                  'PAGE = b"<p>&#xD55C;</p>"\n'
                  'JS = rb\'<script>alert("' + BS + 'u{d55c}")</script>\'\n')
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:%d Hangul" % n for n in (2, 3, 4, 5)])

    def test_a_reference_that_decodes_to_a_backslash_before_an_escape_is_reported(self):
        source = ('x = 1\nPAGE = r"""<button onclick="alert(\'&#92;ud55c\')">a</button>\n'
                  '<button onclick="alert(\'&bsol;ud55c\')">b</button>\n'
                  '<button onclick="alert(\'&#x5C;u{d55c}\')">c</button>\n'
                  '<button onclick="alert(\'&#92;u&#100;55c\')">d</button>"""\n')
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:%d Hangul" % n for n in (2, 3, 4, 5)])

    def test_percent_encoded_utf_8_in_a_url_is_reported(self):
        source = ('x = 1\nPAGE = r"""<a href="javascript:alert(%27%ED%95%9C%27)">a</a>\n'
                  '<iframe src="data:text/plain;charset=utf-8,%ED%95%9C"></iframe>"""\n')
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 Hangul", "label_blind.py:3 Hangul"])
        self.assertEqual(planted_findings("LOOP.md", "# T\n\n[docs](https://example.com/%ED%95%9C)\n"), ["LOOP.md:3 Hangul"])

    def test_percent_format_strings_are_not_reported(self):
        source = 'x = 1\nMSG = "%s: %d of %5.2f%% (%02x) %-10s %(name)s %ED"\nOLD = b"%c%e9"\n'
        self.assertEqual(planted_findings("skill_eval.py", source), [])

    def test_the_matcher_pattern_outside_skill_eval_is_reported(self):
        source = "x = 1\nPAT = " + SLUG_PATTERN + "\n"
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 Hangul"])

    def test_the_matcher_range_in_another_skill_eval_string_is_reported(self):
        source = ('x = 1\nPAGE = r"""<script>alert(\'' + SLUG_PATTERN + '\')</script>"""\n'
                  'HELP = r"names may hold \\uAC00-\\uD7A3"\n')
        self.assertEqual(planted_findings("skill_eval.py", source), ["skill_eval.py:2 Hangul", "skill_eval.py:3 Hangul"])

    def test_the_two_matcher_patterns_in_skill_eval_are_not_reported(self):
        source = "x = 1\nA = re.sub(%s, '-', t)\nB = re.match(%s, d)\n" % (SLUG_PATTERN, EVAL_DIR_PATTERN)
        self.assertEqual(planted_findings("skill_eval.py", source), [])

    def test_utf_8_split_across_adjacent_bytes_literals_is_reported(self):
        ed, x95, x9c = BS + "xed", BS + "x95", BS + "x9c"
        source = ('x = 1\nA = (b"' + ed + x95 + '" b"' + x9c + '")\nB = (b"' + ed + '" b"' + x95 + x9c + '")\n'
                  'C = (b"' + ed + '"  # a\n     b"' + x95 + '"  # b\n     b"' + x9c + '")\n')
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:%d Hangul" % n for n in (2, 3, 4)])

    def test_a_surrogate_pair_is_joined_whatever_spells_its_halves(self):
        high, low = BS + "u{D840}", BS + "u{DC00}"
        source = ('x = 1\nA = r"""<p>' + high + low + '</p>"""\nB = r"""<p>' + BS + 'uD840' + low + '</p>"""\n'
                  'C = r"""<p>' + high + BS + 'uDC00</p>"""\nD = r\'' + high + '\' r\'' + low + '\'\n'
                  'E = r"""<p>&#92;u{D840}&#92;u{DC00}</p>"""\nF = rb\'<script>' + high + low + '</script>\'\n'
                  'G = "' + BS + 'ud840' + BS + 'udc00"\n')
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:%d CJK" % n for n in range(2, 9)])
        self.assertEqual(planted_findings("LOOP.md", "# T\n\n" + high + low + "\n"), ["LOOP.md:3 CJK"])

    def test_a_legacy_reference_glued_to_three_percent_layers_decodes_within_the_pass_cap(self):
        source = 'x = 1\nPAGE = r"""<p>&copy%25255Cud55c</p>"""\n'
        self.assertEqual(planted_findings("label_blind.py", source), ["label_blind.py:2 Hangul"])


KO = "\ud55c\uad6d\uc5b4 \ud14d\uc2a4\ud2b8"
JAMO = "\u3131"


class TestKoreanNamesUnchanged(unittest.TestCase):
    def test_slug_keeps_hangul_syllables_and_lowercases_the_rest(self):
        self.assertEqual(se.slug(KO), "\ud55c\uad6d\uc5b4-\ud14d\uc2a4\ud2b8")
        self.assertEqual(se.slug("Clean CSV: \ud55c\uae00 Test!"), "clean-csv-\ud55c\uae00-test")
        self.assertEqual(se.slug("\uac00 \ud7a3"), "\uac00-\ud7a3")

    def test_slug_replaces_characters_outside_the_syllable_range(self):
        self.assertEqual(se.slug("a" + JAMO + "b"), "a-b")

    def test_eval_dir_name_accepts_a_korean_case_name_and_rejects_jamo(self):
        self.assertEqual(se.eval_dir_name({"id": 3, "name": "\ud55c\uae00-\ucf00\uc774\uc2a4"}),
                         "eval-3-\ud55c\uae00-\ucf00\uc774\uc2a4")
        with self.assertRaises(se.EvalError):
            se.eval_dir_name({"id": 1, "name": "a" + JAMO})


def _no_subprocess(*a, **k):
    raise AssertionError("this test must not start a process")


class TestExitCodesUnchanged(unittest.TestCase):
    def test_validate_of_a_missing_folder_exits_1_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "no-such-skill")
            out = io.StringIO()
            with mock.patch.object(subprocess, "run", _no_subprocess), \
                    mock.patch.object(subprocess, "Popen", _no_subprocess), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                rc = se.main(["validate", missing])
            self.assertEqual(rc, 1, out.getvalue())
            self.assertEqual(os.listdir(d), [])
            self.assertEqual(eva.non_english_hits(out.getvalue()), [])


if __name__ == "__main__":
    unittest.main()
