"""The engine files, README.md and every file under references/ hold English text only, and translating them changed no
behavior. The scan reads source files only: it never imports, runs or execs loop.py or claude_bash_gate.py, and calls no
subprocess, no claude/codex/opencode/hermes binary and no network.
Contract: the meta-test reports non-English text (as eva.non_english_hits classifies it) in the engine files, README.md
and every file under references/ found at test time that is written literally anywhere in a file, or that appears in
the value of any Python string or bytes literal (adjacent literals joined as Python joins them, joined bytes read as
UTF-8 with replacement) or in the full text of each other file once
these reader decodings are applied again and again until the text stops changing (at most DECODE_PASSES passes):
backslash escapes (u with four hex digits, U with eight, N with a name, the JavaScript braced form), HTML character
references with or without the semicolon as html.unescape reads them, and percent-encoded UTF-8 (runs of % plus two hex
digits, read as UTF-8 with replacement); after each pass every high surrogate followed by a low surrogate is joined into
one character, whatever spelled them. Out of the contract: text built by running code (for example chr,
String.fromCharCode, atob, unescape, decodeURIComponent, base64, bytes.decode, a + join of literals) and CSS escapes.
This file stays ASCII only: Hangul in the test data is written as escapes or chr() calls."""
import ast, codecs, contextlib, html, io, os, pathlib, re, shutil, subprocess, sys, tempfile, tokenize, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import eva, skill_eval as se

PY_FILES = ("eva.py", "skill_eval.py", "label_blind.py", "loop.py", "claude_bash_gate.py")
MD_FILES = ("LOOP.md", "README.md")
NEW_REFERENCES = ("references/engine-cli.md", "references/eval-set-format.md", "references/rules.md")
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
    path = os.path.join(root, *name.split("/"))
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError as e:
        raise AssertionError("%s is not UTF-8 text, so the English scan cannot read it: %s" % (name, e))
    lines = char_lines(text, 1, text.count("\n") + 1)
    found = hits(text, lines)
    return found | (string_findings(name, path) if name.endswith(".py") else hits(*read(text, lines)))


def scanned_files(root):
    """The engine files, LOOP.md, README.md and every file under root/references, in any folder and of any name, at call
    time."""
    found = []
    for dp, _, fn in os.walk(os.path.join(root, "references")):
        found += [os.path.relpath(os.path.join(dp, n), root).replace(os.sep, "/") for n in fn]
    return PY_FILES + MD_FILES + tuple(sorted(found))


def engine_findings(root):
    return ["%s:%d %s" % (name, line, script)
            for name in scanned_files(root) for line, script in sorted(file_findings(root, name))]


def planted_findings(name, source):
    with tempfile.TemporaryDirectory() as d:
        for each in sorted(set(PY_FILES + MD_FILES + (name,))):
            os.makedirs(os.path.dirname(os.path.join(d, *each.split("/"))), exist_ok=True)
            with open(os.path.join(d, *each.split("/")), "w", encoding="utf-8") as f:
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
        for name in ("eva.py", "skill_eval.py", "label_blind.py", "loop.py", "claude_bash_gate.py", "LOOP.md", "README.md",
                     "references/any-new-guide.md"):
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


def walked_references(root):
    return tuple(sorted(p.relative_to(root).as_posix() for p in pathlib.Path(root, "references").rglob("*") if p.is_file()))


def plant_tree(d, plants):
    for each in PY_FILES + MD_FILES:
        with open(os.path.join(d, each), "w", encoding="utf-8") as f:
            f.write("")
    for name, data in plants.items():
        path = os.path.join(d, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


class TestScanScope(unittest.TestCase):
    def test_the_scan_covers_eva_the_readme_and_every_reference_found_now(self):
        self.assertIn("eva.py", PY_FILES)
        names = scanned_files(ROOT)
        self.assertEqual(names, PY_FILES + ("LOOP.md", "README.md") + walked_references(ROOT))
        for ref in NEW_REFERENCES:
            self.assertIn(ref, names)

    def test_hangul_in_any_file_under_references_is_reported_whatever_its_folder_name_or_case(self):
        plants = {n: ("# T\n\n" + H + "\n").encode("utf-8") for n in (
            "references/sub/x.md", "references/sub/deeper/page.html", "references/UPPER.MD", "references/notes.txt",
            "references/data.json", "references/guide.markdown", "references/page.mdx", "references/NOTES")}
        with tempfile.TemporaryDirectory() as d:
            plant_tree(d, plants)
            self.assertEqual(scanned_files(d), PY_FILES + MD_FILES + walked_references(d))
            self.assertEqual(engine_findings(d), ["%s:3 Hangul" % n for n in sorted(plants)])

    def test_a_file_under_references_that_is_not_utf_8_fails_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            plant_tree(d, {"references/a.md": b"# A\n", "references/img/logo.png": b"\x89PNG\r\n\x1a\n\xff\xfe"})
            with self.assertRaisesRegex(AssertionError, "references/img/logo.png"):
                engine_findings(d)

    def test_a_hangul_line_planted_in_a_copy_of_the_readme_is_the_only_finding(self):
        with tempfile.TemporaryDirectory() as d:
            for each in PY_FILES + ("LOOP.md",):
                with open(os.path.join(d, each), "w", encoding="utf-8") as f:
                    f.write("")
            with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
                lines = f.read().split("\n")
            with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines[:2] + ["Planted: " + H] + lines[2:]))
            self.assertEqual(engine_findings(d), ["README.md:3 Hangul"])

    def test_a_reference_added_later_is_scanned_without_a_code_edit(self):
        self.assertEqual(planted_findings("references/added-later.md", "# Guide\n\nSee " + H + ".\n"),
                         ["references/added-later.md:3 Hangul"])


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
INLINE_LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
REFERENCE_LINK = re.compile(r"^ {0,3}\[[^\]\n]+\]:\s*<?(\S+?)>?(?:\s|$)")
HTML_LINK = re.compile(r"<[A-Za-z][^>]*?\s(?:href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))", re.I)
URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def doc_files(root):
    refs = os.path.join(root, "references")
    return ["README.md"] + sorted("references/" + n for n in os.listdir(refs) if n.endswith(".md"))


def relative_links(text):
    """(line, target) of every Markdown link and HTML href or src outside fenced code and code spans whose target is not
    a URL or an in-page anchor; the target loses its #fragment."""
    out, fence = [], None
    for n, line in enumerate(text.split("\n"), 1):
        m = FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence): fence = None
            continue
        if m:
            fence = m.group(1); continue
        line = CODE_SPAN.sub("", line)
        targets = INLINE_LINK.findall(line) + REFERENCE_LINK.findall(line) + ["".join(t) for t in HTML_LINK.findall(line)]
        out += [(n, t.split("#")[0]) for t in targets if not URL_SCHEME.match(t) and not t.startswith("#")]
    return out


def doc_links(root, name):
    with open(os.path.join(root, *name.split("/")), encoding="utf-8") as f:
        return relative_links(f.read())


def resolves(root, base, target):
    """True when target, relative to the folder base, names an existing path inside root with the exact case of every
    component (this volume ignores case, a case-sensitive host does not). A root-relative target never resolves."""
    if target.startswith("/"):
        return False
    rel = os.path.relpath(os.path.normpath(os.path.join(base, *target.split("/"))), root)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return False
    here = root
    for part in [] if rel == os.curdir else rel.split(os.sep):
        if not os.path.isdir(here) or part not in os.listdir(here):
            return False
        here = os.path.join(here, part)
    return True


def broken_links(root):
    found = []
    for name in doc_files(root):
        base = os.path.dirname(os.path.join(root, *name.split("/")))
        found += ["%s:%d %s" % (name, n, t) for n, t in doc_links(root, name) if not resolves(root, base, t)]
    return found


class TestDocLinks(unittest.TestCase):
    def test_every_relative_link_in_the_readme_and_the_references_resolves(self):
        self.assertEqual(broken_links(ROOT), [])
        self.assertGreater(sum(len(doc_links(ROOT, n)) for n in doc_files(ROOT)), 10)

    def test_the_readme_links_the_three_new_references(self):
        targets = [t for _, t in doc_links(ROOT, "README.md")]
        for ref in NEW_REFERENCES:
            self.assertIn(ref, targets)

    def test_a_planted_broken_link_is_reported_and_urls_anchors_and_code_are_not(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "references"))
            with open(os.path.join(d, "LOOP.md"), "w", encoding="utf-8") as f:
                f.write("# Loop\n")
            with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
                f.write("# R\n\n[ok](LOOP.md) [web](https://example.com/x.md) [top](#r) `[c](gone.md)`\n"
                        "```\n[fenced](gone.md)\n```\n[gone](references/missing.md#part)\n")
            with open(os.path.join(d, "references", "a.md"), "w", encoding="utf-8") as f:
                f.write("# A\n\n[up](../LOOP.md) [loop](../LOOP.md#loop)\n[ref]: ../nowhere.md\n")
            self.assertEqual(broken_links(d), ["README.md:7 references/missing.md", "references/a.md:4 ../nowhere.md"])

    def test_a_case_only_match_an_escaping_or_root_relative_link_html_links_and_other_titles_are_checked(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "repo")
            os.makedirs(os.path.join(root, "references"))
            for path, text in ((os.path.join(d, "outside.md"), "# Outside\n"), (os.path.join(root, "LOOP.md"), "# Loop\n"),
                               (os.path.join(root, "references", "a.md"), "# A\n\n[up](../LOOP.md 'Loop')\n")):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as f:
                f.write("# R\n\n[case](loop.md) [ok](LOOP.md)\n[out](../outside.md)\n[root](/LOOP.md)\n"
                        "<a href=\"gone-a.md\">a</a> <img alt=\"x\" src='gone.png'> <a href=\"LOOP.md\">ok</a>\n"
                        "[sq](gone-sq.md 'Title') [ok](LOOP.md 'T')\n[pt](gone-pt.md (Title)) [ok](LOOP.md (T))\n")
            self.assertEqual(broken_links(root), ["README.md:3 loop.md", "README.md:4 ../outside.md", "README.md:5 /LOOP.md",
                                                  "README.md:6 gone-a.md", "README.md:6 gone.png", "README.md:7 gone-sq.md",
                                                  "README.md:8 gone-pt.md"])


SENTENCE_BREAK = re.compile(r"(?<=\.)\s+(?=[A-Z`\[])")
NO_RUN_CLAIM = re.compile(r"(?i)\bexecutes? nothing\b|\b(?:no|nothing)\b[^.]*\bCLI\b")
UNQUALIFIED_NO_RUN = re.compile(r"(?i)\bexecutes? nothing\b|\bno\s+CLI\b")
PLUGIN_LISTING = "check-builtin-plugins"


def doc_sentences(root, name):
    with open(os.path.join(root, *name.split("/")), encoding="utf-8") as f:
        return [(n, s) for n, line in enumerate(f.read().split("\n"), 1) for s in SENTENCE_BREAK.split(line) if s.strip()]


def no_run_claims(root, name):
    """(line, sentence) of every sentence that bounds what runs by `--live` and says no CLI runs or nothing executes.
    A sentence about the plugin listing is that bound's exception, not a claim."""
    return [(n, s) for n, s in doc_sentences(root, name)
            if "`--live`" in s and NO_RUN_CLAIM.search(s) and PLUGIN_LISTING not in s]


def names_the_plugin_listing_exception(root, name):
    return any(PLUGIN_LISTING in s and "`claude`" in s and "without `--live`" in s for _, s in doc_sentences(root, name))


class TestLiveBoundaryDocs(unittest.TestCase):
    def test_a_doc_that_bounds_cli_starts_by_live_names_the_plugin_listing_as_the_exception(self):
        self.assertNotIn("live", vars(se.build_parser().parse_args([PLUGIN_LISTING])))
        self.assertEqual(se.builtin_plugin_check_command()[0], "claude")
        claimed = [n for n in doc_files(ROOT) if no_run_claims(ROOT, n)]
        self.assertTrue({"README.md", "references/engine-cli.md"} <= set(claimed), claimed)
        self.assertEqual([n for n in claimed if not names_the_plugin_listing_exception(ROOT, n)], [])

    def test_a_no_run_claim_names_the_tool_clis_because_the_plan_starts_ssh_keygen(self):
        self.assertEqual(["%s:%d" % (name, n) for name in doc_files(ROOT) for n, s in no_run_claims(ROOT, name)
                          if UNQUALIFIED_NO_RUN.search(s)], [])


EVA_SUBCOMMANDS = ("check", "check-all", "feedback", "index", "schema-check", "generate")
FEEDBACK_HEADINGS = ["# Eva Feedback: DoEverything",
                     "## Categories below the floor (each category needs a pass rate of 50% or more and at least one pass)",
                     "## What to fix (most important first)"]


def eva_output(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            rc = eva.main(argv)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue()


class TestEvaPrintsEnglish(unittest.TestCase):
    def test_the_help_of_eva_and_of_every_subcommand_has_no_non_english_line(self):
        for argv in [["--help"]] + [[sub, "--help"] for sub in EVA_SUBCOMMANDS]:
            with self.subTest(argv=argv):
                rc, text = eva_output(argv)
                self.assertEqual(rc, 0, text)
                self.assertIn("usage: eva", text)
                self.assertEqual(eva.non_english_hits(text), [])

    def test_a_folder_without_a_unit_gets_an_english_fix(self):
        with tempfile.TemporaryDirectory() as d:
            rep = eva.check(d)
        self.assertEqual([(c["id"], c["reason"], c["fix"]) for c in rep.checks if c["id"] == "frontmatter"],
                         [("frontmatter", "no SKILL.md or AGENT.md", "Put a SKILL.md or an AGENT.md in the folder.")])

    def test_feedback_on_a_copy_of_do_everything_writes_english_headings(self):
        with tempfile.TemporaryDirectory() as d:
            unit = os.path.join(d, "DoEverything")
            shutil.copytree(os.path.join(ROOT, "bad", "skills", "DoEverything"), unit)
            rc, _ = eva_output(["feedback", unit])
            with open(os.path.join(unit, "FEEDBACK.md"), encoding="utf-8") as f:
                headings = [l for l in f.read().splitlines() if l.startswith("#")]
        self.assertEqual(rc, 1)
        self.assertEqual(headings, FEEDBACK_HEADINGS)
        self.assertEqual([h for h in headings if eva.non_english_hits(h)], [])


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
