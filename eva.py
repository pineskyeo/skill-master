#!/usr/bin/env python3
"""Eva (에바): SKILL.md skill evaluator (agent checks are slated to move to a separate "agent master").
stdlib only, Python 3.9+."""
import argparse, json, os, posixpath, re, shlex, sys, unicodedata

PASS_THRESHOLD = 0.8
CATEGORY_FLOOR = 0.5  # every category with applicable checks needs >=1 pass and pass rate >= this
SKILL_MAX_BYTES = 20000  # house proxy for the spec's "< 5000 tokens" body budget (tokens are not counted)
SKILL_MAX_LINES = 500    # spec: "Keep your main SKILL.md under 500 lines" -> lines must be < 500
NAME_MAX = 64
# agentskills.io/specification: the only frontmatter fields (skills-ref and skill-creator quick_validate reject others)
SKILL_FIELDS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")
# Host-specific allowance (not in the agentskills.io spec): fields documented in the Claude Code skills frontmatter
# reference (https://code.claude.com/docs/en/skills). Other hosts' validators (skills-ref, quick_validate) reject them.
HOST_SKILL_FIELDS = ("argument-hint", "disable-model-invocation")
EVAL_LAYERS = ("static", "llm", "human")  # optional evals[].layer: how the case is graded
RESERVED_NAME_WORDS = ("anthropic", "claude")  # Claude skill authoring best practices: name "cannot contain"
COMPATIBILITY_MAX = 500
REF_TOC_LINES = 100  # Claude best practices: reference files longer than 100 lines get a table of contents
MIN_EVALS = 3        # Claude best practices checklist: "At least three evaluations created"
DESCRIPTION_MAX = 1024  # spec + best practices: description "Max 1024 characters"
DESCRIPTION_MIN = 20    # house rule: the spec only requires non-empty
# Spec: description "Should describe ... when to use it". Only an English trigger clause outside quotes/backticks
# counts, followed by a substantive condition (DESC_WHEN_MIN_WORDS words, not only vague ones):
#   - a sentence that starts with 'Use' / 'Used' / 'Invoke' / 'Trigger' (optionally 'this|the|it [skill|tool]', an '-ly' adverb,
#     'for|to <task>') and then 'when'/'whenever' ('Use when ...', 'Use this skill when ...', 'Used when ...');
#   - 'this skill' right after a use verb ('... use|using|invoke|trigger|read this skill when ...') or as the
#     sentence subject of 'modal/be + use verb' ('This skill should be used|can be used|must be invoked|is used when ...');
#   - a sentence 'Read this skill before|when ...'.
# Not counted: a 'when' after any other words ('Counts words when files change', 'files loaded when',
# 'hooks called when', 'Applies to X when', '..., used when', 'This skill counts words|is loaded|applies when',
# 'Skip|Avoid this skill when', 'Config for this skill is used when'), negated clauses ('Do not use when', "Don't use
# this skill when", 'Never use when', 'Cannot use this skill when', 'Avoid using this skill when'), 'Use this.',
# Korean conditionals ('...할 때 사용한다') and quoted text. A '.', '!' or '?' directly followed by a capital letter
# ('needed.The user ...') ends the condition but does not start a new trigger sentence.
_DESC_USE_VERB = r"(?:used|invoked|triggered|read)"
DESC_USE_START_RE = re.compile(
    r"(?:use|used|invoke|trigger)(?:\s+(?:this|the|it))?(?:\s+(?:skill|tool))?(?:\s+[A-Za-z]+ly)?(?:\s+(?:for|to)\b[^;]{1,150}?)?"
    r"\s+(?:when|whenever)\b([^;]*)", re.I)
DESC_THIS_SKILL_OBJ_RE = re.compile(
    r"\b(?:use|using|invoke|invoking|trigger|triggering|read|reading)\s+this\s+skill\s+(?:when|whenever)\b([^;]*)", re.I)
DESC_THIS_SKILL_SUBJ_RE = re.compile(
    r"this\s+skill\s+(?:(?:should|must|can|may|will|could|might|shall)\s+be|is)\s+%s\s+(?:when|whenever)\b([^;]*)"
    % _DESC_USE_VERB, re.I)
DESC_READ_RE = re.compile(r"read\s+this\s+skill\s+(?:before|when|whenever)\b([^;]*)", re.I)
DESC_NEGATION_RE = re.compile(r"\b(?:not|never|cannot|avoid(?:s|ing)?|skip(?:s|ping)?|without)\b|n['’]t\b", re.I)
DESC_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
DESC_TIGHT_STOP_RE = re.compile(r"[.!?](?=[A-Z])")  # 'needed.The user': sentence end with no space after it
DESC_WHEN_MIN_WORDS = 2
DESC_WHEN_VAGUE = {"a", "an", "the", "it", "is", "are", "be", "this", "that", "there", "you", "user", "users", "someone",
                   "needed", "need", "necessary", "appropriate", "required", "relevant", "useful", "helpful", "asked",
                   "requested", "wanted", "desired", "possible", "applicable", "suitable", "something", "anything",
                   "so", "as", "if", "or", "and", "in", "to", "of", "for"}

def desc_when_clause(desc):
    """The condition text of the first substantive English trigger clause in desc ('the user wants a word count'
    from 'Use when the user wants a word count.'), else None. Quoted text is blanked first (_unquoted)."""
    for sent in DESC_SENTENCE_RE.split(_unquoted(desc)):
        sent = sent.strip().lstrip("-*•(").strip()
        for m in (DESC_USE_START_RE.match(sent), DESC_READ_RE.match(sent), DESC_THIS_SKILL_SUBJ_RE.match(sent),
                  DESC_THIS_SKILL_OBJ_RE.search(sent)):
            if m is None or DESC_NEGATION_RE.search(sent[:m.start(1)]):
                continue
            cond = " ".join(DESC_TIGHT_STOP_RE.split(m.group(1))[0].split()).rstrip(".!?")
            words = re.findall(r"[A-Za-z][A-Za-z'-]*", cond)
            if len(words) >= DESC_WHEN_MIN_WORDS and any(w.lower() not in DESC_WHEN_VAGUE for w in words):
                return cond
    return None

# Best practices: "Always write in third person" (avoid "I can help you...", "You can use this...").
# Explicit pronouns only, any letter case; quoted text ("fix my code", `my-app`) is the user's words, not the
# description's voice, and is skipped (_unquoted). Korean often drops the subject, so pronoun-less first/second
# person is not detected.
_ROMAN_I_BEFORE = r"(?:phase|part|type|chapter|level|stage|class|tier|section|volume|vol\.?|appendix|war|step|grade|act|book|round|schedule|article|title|figure|table)"
DESC_PERSON_RE = re.compile(
    r"(?<![\w/.(-])(?-i:I)(?![\w/)-]|\.\w)"     # 'I', "I'm" (not 'I/O', 'I-II', '(I)')
    r"|(?<![\w/.-])(?-i:i)(?=\s|['’])"          # lowercase pronoun 'i ' / "i'm" (not 'i.e.')
    r"|(?<![\w/.-])(?:me|my|mine|we|our|ours|you|your|yours|yourself|yourselves)(?![\w/-])"
    r"|(?<![\w/.-])(?-i:us|Us|uS)(?![\w/-])"    # all-caps 'US' is the country/acronym
    r"|(?<!\w)(?:저는|제가|저희|나는|내가|우리는|우리가|우리의|당신|너는|네가|여러분)", re.I)
_ROMAN_I_RE = re.compile(r"\b%s\s+I(?![\w/-])" % _ROMAN_I_BEFORE, re.I)
_QUOTED_RE = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”|‘[^’\n]*’|`[^`\n]*`|(?<![\w])'[^'\n]*'(?![\w])")

def _unquoted(text):
    """text with quoted spans ("...", '...', “...”, ‘...’, `...`) blanked out; apostrophes ("it's") are not quotes."""
    return _QUOTED_RE.sub(lambda m: " " * len(m.group(0)), text)

def desc_person_words(desc):
    """First/second-person words in desc outside quotes, Roman numeral 'Phase I' style excluded."""
    text = _unquoted(desc)
    roman = {m.end() - 1 for m in _ROMAN_I_RE.finditer(text)}
    return sorted(set(m.group(0) for m in DESC_PERSON_RE.finditer(text) if m.start() not in roman))

# single-purpose (house heuristic, scored, not required): count the distinct English task verbs that open a
# coordinated item ('Summarizes PDFs, cleans CSVs and sends emails' -> summarize, clean, send). An item starts at the
# text start, after . ; : ! ? , ( ) or a joiner (and, or, then, also, plus, to, as well as, &), so swapping ';' for ','
# or 'and' does not hide the list. Verbs come from TASK_VERBS in base, -s/-es/-ies or -ing form. Words that are often
# nouns (AMBIGUOUS_TASK_VERBS: 'report', 'schedule', 'format') count only as '-ing' or right after 'to'.
# Not counted: quoted text, sentences that are an English trigger clause (desc_when_clause: the 'Use when ...'
# condition lists request phrasings, not the skill's tasks) or exclude uses ('Do NOT use for ...'), a verb followed by 'this' ('Read this skill'), hyphenated
# compounds ('find-and-replace'), verbs outside the lexicon, and Korean verbs (only the phrases in MULTI_PURPOSE_RE).
TASK_VERBS = """add aggregate analyze annotate archive audit automate backup benchmark build bundle calculate chart check
clean classify code combine commit compare compile compose compress compute configure convert copy count crawl create crop
debug decrypt delete deploy design detect diff draft draw edit email encrypt enrich estimate evaluate export extract fetch
fill filter find fix forecast format generate grade import index inspect install label lint list load manage map merge
migrate minify modify monitor move normalize notify optimize order organize parse patch plan plot post predict print process
profile proofread publish query rank read rebase record refactor release remove rename render reorder repair replace report
resize restore review rewrite rotate run sanitize scan scrape schedule score search send share sign simulate sort split
summarize synthesize tag test track train transcribe transform translate trim update upload validate verify visualize
watermark write""".split()
AMBIGUOUS_TASK_VERBS = set("""report email chart plan index list record order tag label post profile map design draft query
patch release schedule test code score print format process review track search update export import run rank estimate
forecast benchmark""".split())
SINGLE_PURPOSE_MAX_VERBS = 2  # 'Removes duplicate rows and trims whitespace' (2) passes; 3+ distinct task verbs fail
MULTI_PURPOSE_RE = re.compile(r"\b(?:and also|as well as|plus)\b|그리고 또|뿐만 아니라", re.I)

def _verb_forms(v):
    f = {v, v + "s", v + "es", v + "ing"}
    if v.endswith("e"): f.add(v[:-1] + "ing")
    if v.endswith("y"): f.add(v[:-1] + "ies")
    if re.search(r"[^aeiou][aeiou][bdgmnprt]$", v): f.add(v + v[-1] + "ing")  # split -> splitting
    return f

TASK_VERB_FORMS = {f: v for v in TASK_VERBS for f in _verb_forms(v)}
TASK_TOKEN_RE = re.compile(r"[A-Za-z]+(?:[-/][A-Za-z]+)*|[.;:!?,()&]")
# 'Do NOT use for PDFs, spreadsheets, or coding': a sentence that excludes uses lists what the skill does not do
TASK_EXCLUSION_RE = re.compile(r"\b(?:do\s+not|don['’]t|never|not)\s+(?:use|trigger|invoke)\b|\bnot\s+for\b", re.I)
TASK_JOINERS = {"and", "or", "then", "also", "plus", "to"}

def desc_task_verbs(desc):
    """Distinct task verbs (base form, in order) that open a coordinated item outside trigger sentences."""
    out = []
    for sent in DESC_SENTENCE_RE.split(_unquoted(desc)):
        if desc_when_clause(sent) is not None or TASK_EXCLUSION_RE.search(sent):
            continue
        toks = TASK_TOKEN_RE.findall(re.sub(r"\bas\s+well\s+as\b", " and ", sent, flags=re.I))
        start, after_to = True, False
        for i, t in enumerate(toks):
            w = t.lower()
            if not w[0].isalpha():
                start, after_to = True, False; continue
            v = TASK_VERB_FORMS.get(w)
            nxt = toks[i + 1].lower() if i + 1 < len(toks) else ""
            if start and v and nxt != "this" and v not in out and \
                    (v not in AMBIGUOUS_TASK_VERBS or w.endswith("ing") or after_to):
                out.append(v)
            start, after_to = w in TASK_JOINERS, w == "to"
    return out

# ---------- frontmatter (minimal YAML subset) ----------
def _quoted_end(v):
    """Index of the quote closing the quoted scalar that starts at v[0], or -1 if unterminated.
    YAML: in "..." a backslash escapes the next character; in '...' a quote is escaped by doubling it ('')."""
    q, i = v[0], 1
    while i < len(v):
        if q == '"' and v[i] == "\\":
            i += 2; continue
        if v[i] == q:
            if q == "'" and v[i + 1:i + 2] == "'":
                i += 2; continue
            return i
        i += 1
    return -1

def _strip_comment(v):
    """Drop a YAML comment: a '#' at the start of the value or preceded by whitespace (outside quotes)
    starts a comment, so 'k: #x' is an empty (null) value, as in YAML."""
    v = v.strip()
    if v and v[0] in "\"'":
        end = _quoted_end(v)
        if end > 0 and re.match(r"^\s+#", v[end + 1:]):
            return v[:end + 1]
        return v
    if v.startswith("#"):
        return ""
    m = re.search(r"\s#", v)
    return v[:m.start()].rstrip() if m else v

# YAML 1.1 double-quoted escapes (PyYAML ScalarScanner.ESCAPE_REPLACEMENTS / ESCAPE_CODES)
_DQ_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
               "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
               "L": " ", "P": " "}
_DQ_HEX = {"x": 2, "u": 4, "U": 8}

def _unquote(v):
    """(value, error) of a complete quoted scalar v (as returned by _strip_comment)."""
    if v[0] == "'":
        return v[1:-1].replace("''", "'"), None
    out, i, s = [], 0, v[1:-1]
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c); i += 1; continue
        e = s[i + 1:i + 2]
        if e in _DQ_ESCAPES:
            out.append(_DQ_ESCAPES[e]); i += 2
        elif e in _DQ_HEX and re.match(r"^[0-9A-Fa-f]{%d}$" % _DQ_HEX[e], s[i + 2:i + 2 + _DQ_HEX[e]]):
            code = int(s[i + 2:i + 2 + _DQ_HEX[e]], 16)
            if code > 0x10FFFF:  # chr() would raise (PyYAML itself crashes with OverflowError/ValueError here)
                return None, "escape '\\%s%s' is beyond U+10FFFF" % (e, s[i + 2:i + 2 + _DQ_HEX[e]])
            out.append(chr(code)); i += 2 + _DQ_HEX[e]
        else:
            return None, "invalid escape '\\%s' in double-quoted value" % e
    return "".join(out), None

def _scalar(v):
    """Python value of one scalar: None for empty/comment-only, unquoted string, inline list, or plain string."""
    v = _strip_comment(v)
    if not v:
        return None
    if v[0] in "\"'" and _quoted_end(v) == len(v) - 1:
        return _unquote(v)[0]
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    return v

def _plain_scalar_error(v):
    """Why an unquoted value is not a valid YAML plain scalar for this subset (None if fine).
    A real YAML loader would reject or reinterpret these, so the frontmatter must quote them."""
    v = _strip_comment(v)
    if not v:
        return None
    if v[0] in "\"'":
        end = _quoted_end(v)
        if end < 0:
            return "unterminated %s-quoted value (multi-line quoted values are not supported here)" % v[0]
        if end != len(v) - 1:
            return "unexpected text after the closing %s quote" % v[0]
        return _unquote(v)[1]
    if v.startswith("[") and v.endswith("]"):
        return None
    if ": " in v or v.endswith(":"):
        return "':' inside an unquoted value is a YAML mapping indicator; quote the value"
    if v[0] in "@`":
        return "'%s' is a reserved YAML indicator; quote the value" % v[0]
    if v[0] in "&*!%{|>" or v == "-" or v.startswith("- ") or v.startswith("["):
        return "value starts with YAML syntax '%s' not supported here; quote the value" % v[0]
    return None

def _block_scalar(style, lines, eof=False):
    """Value of a '>'/'|' block scalar (style '>', '|', '>-', '|-') from its raw lines, as PyYAML scans it
    (ScalarScanner.scan_block_scalar): indentation from the first non-empty line, '#' lines are content,
    folding joins adjacent non-indented lines with a space, clip chomping keeps one final newline, '-' strips it.
    eof: the last line ends the frontmatter text. skill-creator's quick_validate loads the text between '---\n' and
    '\n---', so a block that is the last line has no line break and clip adds none. Returns (value, error)."""
    no_break = eof and bool(lines) and bool(lines[-1].strip())
    while lines and not lines[-1].strip():
        lines = lines[:-1]  # trailing empty lines only matter for '+' (keep), which is not supported
    first = next((l for l in lines if l.strip()), None)
    if first is None:
        return "", None
    indent = len(first) - len(first.lstrip(" "))
    chunks, breaks, line_break, prev_text = [], [], "", None
    for ln in lines:
        if len(ln) - len(ln.lstrip(" ")) >= indent and ln.strip():
            text = ln[indent:]
            if prev_text is not None:
                if style[0] == ">" and prev_text[:1] not in (" ", "\t") and text[:1] not in (" ", "\t"):
                    if not breaks: chunks.append(" ")
                else:
                    chunks.append(line_break)
            chunks.extend(breaks); breaks = []
            chunks.append(text); line_break = "\n"; prev_text = text
        elif not ln.strip():
            if prev_text is None: chunks.append("\n")  # leading empty lines are kept
            else: breaks.append("\n")
        else:
            return None, "line less indented than the block scalar's first line"
    if not style.endswith("-") and not no_break:
        chunks.append(line_break)
    return "".join(chunks), None

def parse_frontmatter_ex(text):
    """Returns (meta dict or None, body, error). Supports scalars, inline lists, '- item' lists,
    one level of 'key: value' nesting, '>'/'|' blocks. Opening and closing lines must be exactly '---';
    every top-level line must be 'key: value' (a mapping) with unique keys. Indentation must use spaces
    (YAML forbids tabs) and unquoted values must be valid YAML plain scalars (see _plain_scalar_error).
    ' #' starts a comment, as in YAML; inside a '>'/'|' block an indented '#' line is content."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None, text, "first line must be exactly '---'"
    end = None
    for i in range(1, len(lines)):
        if lines[i] == "---":
            end = i; break
    if end is None:
        return None, text, "closing '---' line not found"
    meta, key, block, block_lines, block_start, closed = {}, None, None, [], 0, False
    def err(n, msg): return None, text, "frontmatter line %d: %s" % (n, msg)
    def finish_block(eof=False):
        v, e = _block_scalar(block, block_lines, eof)
        if e: return err(block_start, e)
        meta[key] = v
        return None
    for n, ln in enumerate(lines[1:end], 2):
        if block is not None and (not ln.strip() or ln[0] == " "):
            block_lines.append(ln); continue  # block content, '#' lines included
        if block is not None:
            bad = finish_block()
            if bad: return bad
            block, closed = None, True  # an indented line after this would continue nothing
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[0] in " \t":
            if key is None:
                return err(n, "indented line without a parent key")
            if closed:
                return err(n, "indented line after the block scalar of '%s' ended" % key)
            if "\t" in ln[:len(ln) - len(ln.lstrip())]:
                return err(n, "tab in indentation (YAML allows spaces only)")
            s = ln.strip()
            if meta[key] is not None and not isinstance(meta[key], (list, dict)):
                return err(n, "key '%s' already has a scalar value; nested lines not allowed" % key)
            elif s.startswith("- "):
                if isinstance(meta[key], dict):
                    return err(n, "key '%s' mixes '- item' list and 'key: value' mapping" % key)
                pe = _plain_scalar_error(s[2:])
                if pe: return err(n, pe)
                if meta[key] is None: meta[key] = []
                meta[key].append(_scalar(s[2:]))
            elif re.match(r"^[A-Za-z_][\w-]*:\s+\S", s):
                if isinstance(meta[key], list):
                    return err(n, "key '%s' mixes '- item' list and 'key: value' mapping" % key)
                if meta[key] is None: meta[key] = {}
                k2, v2 = s.split(":", 1)
                if k2.strip() in meta[key]:
                    return err(n, "duplicate key '%s.%s'" % (key, k2.strip()))
                pe = _plain_scalar_error(v2)
                if pe: return err(n, pe)
                meta[key][k2.strip()] = _scalar(v2)
            else:
                return err(n, "expected '- item' or 'key: value' under '%s'" % key)
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$", ln)
        if not m:
            return err(n, "expected 'key: value' (frontmatter must be a mapping)")
        key, val = m.group(1), _strip_comment(m.group(2) or "")
        if key in meta:
            return err(n, "duplicate key '%s'" % key)
        closed = False
        if val in (">", "|", ">-", "|-"):
            meta[key] = ""; block, block_lines, block_start = val, [], n
        elif val == "":
            meta[key] = None
        else:
            pe = _plain_scalar_error(val)
            if pe: return err(n, pe)
            meta[key] = _scalar(val)
    if block is not None:
        bad = finish_block(eof=True)
        if bad: return bad
    return meta, "\n".join(lines[end + 1:]), None

# YAML 1.1 implicit types (what PyYAML's safe_load resolves): an unquoted value matching these is
# loaded as bool/null/int/float/date, not a string.
_YAML_TYPED_RE = re.compile(
    r"^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF"
    r"|~|null|Null|NULL"
    r"|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0[0-7_]+|[-+]?0x[0-9a-fA-F_]+|[-+]?0b[01_]+"
    r"|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?"
    r"|[-+]?[0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9_]+(?:[eE][-+][0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
    r"|\d{4}-\d\d-\d\d|\d{4}-\d\d?-\d\d?(?:[Tt]|[ \t]+)\d\d?:\d\d:\d\d.*)$")

def yaml_typed_keys(text):
    """Dotted keys ('name', 'metadata.version') whose unquoted value a YAML loader types as non-string."""
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return []
    out, parent, block = [], None, False
    for ln in lines[1:lines.index("---", 1)]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[0] == " ":
            m = re.match(r"^\s+(?:-\s+(.*)|([A-Za-z_][\w-]*):\s+(.*))$", ln)
            if not block and parent and m:
                v = m.group(1) if m.group(1) is not None else m.group(3)
                if _YAML_TYPED_RE.match(_strip_comment(v)):
                    out.append(parent + ("[]" if m.group(1) is not None else "." + m.group(2)))
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$", ln)
        if m:
            parent, v = m.group(1), _strip_comment(m.group(2) or "")
            block = v in (">", "|", ">-", "|-")
            if v and _YAML_TYPED_RE.match(v):
                out.append(parent)
    return out

def parse_frontmatter(text):
    """Returns (meta dict or None, body)."""
    meta, body, _ = parse_frontmatter_ex(text)
    return meta, body

def read_text(fp):
    """Returns (text, error) without raising on missing/undecodable files."""
    try:
        with open(fp, encoding="utf-8") as f:
            return f.read(), None
    except (OSError, UnicodeDecodeError) as e:
        return None, "cannot read %s: %s" % (os.path.basename(fp), e)

def as_list(v):
    if v is None or v == "": return []
    return v if isinstance(v, list) else [v]

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# CommonMark ATX heading: 0-3 spaces, 1-6 '#', then a space/tab or end of line; optional closing '#' sequence
ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")

def _split_fences(text):
    """(lines outside, lines inside) fenced code blocks (``` or ~~~, CommonMark closing rules); fence lines dropped."""
    prose, code, fence = [], [], None
    for ln in text.splitlines():
        m = FENCE_RE.match(ln)
        if m:
            mark, rest = m.group(1), m.group(2)
            if fence is None: fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence) and not rest.strip(): fence = None
            continue
        (prose if fence is None else code).append(ln)
    return prose, code

def _unfenced(text):
    """text with fenced code blocks removed."""
    return "\n".join(_split_fences(text)[0])

def _fenced(text):
    """Lines inside fenced code blocks."""
    return _split_fences(text)[1]

def headings(text):
    """[(level, heading text)] of ATX headings outside fenced code."""
    out = []
    for ln in _unfenced(text).splitlines():
        m = ATX_RE.match(ln)
        if m: out.append((len(m.group(1)), (m.group(2) or "").strip()))
    return out

def has_section(body, *words):
    """True if a markdown heading (outside fenced code blocks) contains any of words."""
    return any(w.lower() in t.lower() for _, t in headings(body) for w in words)

class Report:
    """checks: status "pass"/"fail" (passed True/False), "n/a" (absent optional item) or "needs-review"
    (criterion a machine cannot check; a human or model grader must). n/a and needs-review have passed None and are
    excluded from score and category floor; any needs-review item turns an otherwise passing verdict into REVIEW."""
    def __init__(self, path, kind):
        self.path, self.kind, self.checks = path, kind, []
    def add(self, cid, category, ok, reason, fix="", must=False):
        self.checks.append({"id": cid, "category": category, "status": "pass" if ok else "fail", "passed": bool(ok),
                            "must": must or cid in CRITICAL, "reason": reason, "fix": "" if ok else fix})
    def na(self, cid, category, reason, must=False):
        self.checks.append({"id": cid, "category": category, "status": "n/a", "passed": None,
                            "must": must or cid in CRITICAL, "reason": "N/A: " + reason, "fix": ""})
    def review(self, cid, category, reason, fix):
        self.checks.append({"id": cid, "category": category, "status": "needs-review", "passed": None,
                            "must": True, "reason": "needs-review: " + reason, "fix": fix})
    @property
    def needs_review(self):
        return [c for c in self.checks if c["status"] == "needs-review"]
    @property
    def applicable(self):
        return [c for c in self.checks if c["passed"] is not None]
    @property
    def failed(self):
        return [c for c in self.checks if c["passed"] is False]
    @property
    def score(self):
        app = self.applicable
        return round(sum(c["passed"] for c in app) / len(app), 3) if app else 0.0
    @property
    def critical_failed(self):
        """Failed checks that fail the unit regardless of score (CRITICAL + present optional checks + outcomes)."""
        return [c for c in self.failed if c["must"]]
    @property
    def categories(self):
        out = {}
        for c in self.applicable:
            s = out.setdefault(c["category"], {"passed": 0, "total": 0})
            s["passed"] += c["passed"]; s["total"] += 1
        return out
    @property
    def category_failed(self):
        """Categories with zero passes or a pass rate below CATEGORY_FLOOR."""
        return sorted(k for k, s in self.categories.items()
                      if s["passed"] == 0 or s["passed"] / s["total"] < CATEGORY_FLOOR)
    @property
    def verdict(self):
        """FAIL if any machine check fails; else REVIEW if a needs-review item remains; else PASS."""
        ok = self.applicable and self.score >= PASS_THRESHOLD and not self.critical_failed and not self.category_failed
        if not ok: return "FAIL"
        return "REVIEW" if self.needs_review else "PASS"
    def to_dict(self):
        return {"path": self.path, "kind": self.kind, "score": self.score,
                "threshold": PASS_THRESHOLD, "category_floor": CATEGORY_FLOOR,
                "categories": self.categories, "category_failed": self.category_failed,
                "needs_review": [c["id"] for c in self.needs_review],
                "verdict": self.verdict, "checks": self.checks}

EXIT_CODES = {"PASS": 0, "FAIL": 1, "REVIEW": 3}

CRITICAL = {"frontmatter", "name-format", "name-matches-folder", "description-present",
            "goal-present", "tools-declared"}

def detect_kind(path):
    if os.path.isfile(os.path.join(path, "SKILL.md")): return "skill"
    if os.path.isfile(os.path.join(path, "AGENT.md")): return "agent"
    return None

def name_errors(name):
    """Name rules exactly as skills-ref validator._validate_name: NFKC-normalized, 1..64 chars, lowercase,
    Unicode letters/digits (str.isalnum) and '-' only, no leading/trailing '-', no '--'."""
    if not name.strip():
        return ["must be a non-empty string"]
    e = []
    if len(name) > NAME_MAX: e.append("exceeds %d characters (%d)" % (NAME_MAX, len(name)))
    if name != name.lower(): e.append("must be lowercase")
    if name.startswith("-") or name.endswith("-"): e.append("cannot start or end with '-'")
    if "--" in name: e.append("cannot contain '--'")
    if not all(c.isalnum() or c == "-" for c in name): e.append("only letters, digits and '-' allowed")
    return e

def _str_field(meta, key):
    """meta[key] if it is a string, else "" (an empty 'key:' is None, which must not read as the text 'None';
    non-string values are reported by field-types)."""
    v = meta.get(key)
    return v.strip() if isinstance(v, str) else ""

def _common_name(r, meta, path):
    name = unicodedata.normalize("NFKC", _str_field(meta, "name"))
    ne = name_errors(name)
    r.add("name-format", "single-task", not ne,
          "name '%s' (skills-ref rules: NFKC, lowercase letters/digits/'-', <=%d)%s" % (name, NAME_MAX, (": %s" % ne) if ne else ""),
          "name을 소문자·숫자·하이픈(예: pdf-summarizer)으로, %d자 이하로 바꾸세요." % NAME_MAX)
    folder = unicodedata.normalize("NFKC", os.path.basename(os.path.realpath(path)))  # '.', 'x/', symlinks
    r.add("name-matches-folder", "structure", name == folder,
          "name '%s' vs folder '%s'" % (name, folder), "frontmatter name을 폴더 이름 '%s'과 같게 맞추세요." % folder)
    desc = _str_field(meta, "description")
    r.add("description-present", "single-task", bool(desc), "description present",
          "무엇을 하는지와 언제 쓰는지를 담은 description을 추가하세요.")
    return name, desc

# ---------- skills (agentskills.io specification + Claude skill authoring best practices) ----------
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
MD_LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^)]*[\"'])?\s*\)")

# Spec bundled directories (agentskills.io "Directory structure"); a path into one of them is a file reference
# whether written as a link, in inline code, or as plain text ("Run scripts/x.py").
BUNDLED_REF_RE = re.compile(r"(?<![\w./\\-])((?:scripts|references|assets)[/\\][^\s`'\"()<>\[\]{}|,;]+)")
GLOB_CHARS = set("*?")
HANGUL = "ㄱ-ㆎ가-힣"
# Korean particle/ending glued to a path that ends in a Latin letter, digit, ')' or closing '**'
# ('scripts/clean.py를', '...py에서', '**scripts/x.py**를')
KO_PARTICLE_RE = re.compile(r"(?<=[A-Za-z0-9)\]*])[%s]+$" % HANGUL)
# Inside fenced code only run commands are references, checked in every command of a line (split on &&, ||, ;, |):
# an interpreter followed by a bundled-dir path ('python scripts/x.py', '$ bash assets/setup.sh', 'cd d && sh scripts/b.sh'),
# or direct execution of a script ('./scripts/x.sh', 'scripts/x.py args'). A bare 'scripts/x.py' is a file listing,
# not a run command, when it is indented, carries a '# note', or the block draws a tree (├ └ │).
RUNNER = r"(?:sudo\s+)?(?:python[0-9.]*|bash|sh|zsh|node|deno\s+run|ruby|perl|Rscript|php|pwsh|uv\s+run(?:\s+python[0-9.]*)?)"
_ARG = r"[^\s;&|<>'\"`]+"
_ENV = r"(?:[A-Za-z_]\w*=\S*\s+)*"  # 'LANG=C python scripts/x.py'
FENCED_RUN_RE = re.compile(r"^(?:\$\s+)?%s%s\s+(?:-\S+\s+)*(?:\./)?((?:scripts|references|assets)/%s)" % (_ENV, RUNNER, _ARG))
FENCED_EXEC_RE = re.compile(r"^(?:\$\s+)?%s(\./)?(scripts/%s)" % (_ENV, _ARG))
CMD_SPLIT_RE = re.compile(r"&&|\|\||;|\|")
TREE_CHARS = set("├└│")

def _fenced_run_refs(code_lines):
    """Bundled-dir paths that fenced code lines run (see FENCED_RUN_RE / FENCED_EXEC_RE)."""
    out, tree = [], any(TREE_CHARS & set(ln) for ln in code_lines)
    for ln in code_lines:
        m = re.search(r"(^|\s)#", ln)  # shell comment
        cmd, note = (ln[:m.start()], True) if m else (ln, False)
        for i, seg in enumerate(CMD_SPLIT_RE.split(cmd)):
            indented = i == 0 and seg[:1] in (" ", "\t")
            seg = seg.strip()
            r = FENCED_RUN_RE.match(seg)
            if r:
                out.append(r.group(1)); continue
            e = FENCED_EXEC_RE.match(seg)
            if e and (e.group(1) or not (indented or tree or (note and seg == e.group(0)))):
                out.append(e.group(2))
    return out

def _clean_bundled(t, stars=0):
    """Bundled-dir path with trailing prose removed (anchor, punctuation, Korean particle, and up to `stars` closing
    '*' of the emphasis the path was opened with, as in '**scripts/x.py**'); None if what is left is a directory
    mention or a glob ('assets/icon*', 'scripts/*.py')."""
    t = t.split("#", 1)[0]
    prev = None
    while t != prev:
        prev = t
        t = KO_PARTICLE_RE.sub("", t.rstrip(".:!?"))
        k = len(t) - len(t.rstrip("*"))
        if 0 < k <= stars:
            t = t[:-k]
    if not t or t[-1] in "/\\" or GLOB_CHARS & set(t):
        return None
    return t

def local_refs(text):
    """Local file references in markdown: every relative markdown link target and bundled-dir path (scripts/,
    references/, assets/) in inline code, **bold** or plain text outside fenced code, plus run commands inside
    fenced code ('python scripts/x.py'). URLs, #anchors, directory mentions ('scripts/') and glob patterns
    ('assets/*.png') are not file references."""
    prose = _unfenced(text)
    refs = set()
    for t in MD_LINK_RE.findall(prose):
        t = t.split("#", 1)[0].split("?", 1)[0]
        if t and not SCHEME_RE.match(t):
            refs.add(t)
    found = [(m.group(1), len(prose[:m.start()]) - len(prose[:m.start()].rstrip("*")))
             for m in BUNDLED_REF_RE.finditer(prose)]
    found += [(t, 0) for t in _fenced_run_refs(_fenced(text))]
    for t, stars in found:
        t = _clean_bundled(t, stars)
        if t: refs.add(t)
    return refs

def _ref_problem(ref):
    """Why a reference is not a portable relative path inside the skill root (None if fine)."""
    if "\\" in ref: return "backslash (use forward slashes)"
    if ref.startswith("/") or ref.startswith("~"): return "absolute path (use a path relative to the skill root)"
    norm = posixpath.normpath(ref)
    if norm == ".." or norm.startswith("../"): return "points outside the skill directory"
    return None

def git_root(path):
    """Nearest enclosing git work tree root of path (a directory holding '.git', dir or worktree file), or None."""
    d = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(d, ".git")): return d
        up = os.path.dirname(d)
        if up == d: return None
        d = up

def repo_root_ref(path, ref):
    """True if ref, missing from the skill folder, is a file relative to the enclosing git repo root and stays inside
    it (after resolving symlinks). Refs with a _ref_problem ('..', absolute, backslash) never qualify."""
    root = git_root(path)
    if root is None or _ref_problem(ref): return False
    target = os.path.realpath(os.path.join(root, ref))
    return os.path.commonpath([root, target]) == root and os.path.exists(target)

def _toc_first(text):
    """True if the first heading after the title (outside fences) is a table of contents."""
    heads = headings(text)
    if heads and heads[0][0] == 1:
        heads = heads[1:]
    return bool(heads) and bool(re.search(r"contents|목차|\btoc\b", heads[0][1], re.I))

# skill-creator quick_validate loads with yaml.safe_load and rejects these when not str. skills-ref (strictyaml)
# reads every scalar as a string, so other fields (metadata values included) are not forced to be quoted.
PYYAML_STR_FIELDS = ("name", "description", "compatibility")

def _field_type_errors(meta, typed):
    """Spec types: name/description/license/compatibility/allowed-tools are strings, metadata maps string->string.
    typed: keys whose unquoted value YAML 1.1 (PyYAML) would load as bool/null/number/date (yaml_typed_keys)."""
    e = []
    for k in ("name", "description", "license", "compatibility", "allowed-tools"):
        if k not in meta: continue
        if not isinstance(meta[k], str) or not meta[k].strip():
            e.append("%s: non-empty string required, got %s" % (k, "empty" if meta[k] in (None, "") else type(meta[k]).__name__))
        elif k in typed and k in PYYAML_STR_FIELDS:
            e.append("%s: PyYAML (skill-creator quick_validate) loads '%s' as non-string; quote it" % (k, meta[k]))
    if "metadata" in meta:
        md = meta["metadata"]
        if not isinstance(md, dict):
            e.append("metadata: mapping of string keys to string values required, got %s" %
                     ("empty" if md in (None, "") else type(md).__name__))
        else:
            for k, v in md.items():
                if not isinstance(v, str):
                    e.append("metadata.%s: string value required, got %s" % (k, type(v).__name__))
    if "argument-hint" in meta and not _nonempty_str(meta["argument-hint"]):
        e.append("argument-hint: non-empty string required")
    if "disable-model-invocation" in meta:
        v = meta["disable-model-invocation"]
        if not (isinstance(v, str) and v.lower() in ("true", "false") and "disable-model-invocation" in typed):
            e.append("disable-model-invocation: unquoted boolean true/false required, got %r" % (v,))
    return e

def evals_errors(path, name):
    """Validate evals/evals.json against the skill-creator / agentskills.io format. Returns (n_cases, errors).
    Format: {"skill_name": <frontmatter name>, "evals": [{"id": int (unique), "prompt": str, "expected_output": str,
    "files": [str] (optional, relative to skill root, must exist), "assertions"|"expectations": [str] (optional),
    "layer": "static"|"llm"|"human" (optional; exact lowercase, no layer is required to be present)}]}"""
    fp = os.path.join(path, "evals", "evals.json")
    if not os.path.isfile(fp):
        return 0, ["missing evals/evals.json"]
    raw, err = read_text(fp)
    if err: return 0, [err]
    try:
        data = json.loads(raw)
    except ValueError as ex:
        return 0, ["invalid JSON: %s" % ex]
    if not isinstance(data, dict):
        return 0, ['top level must be an object {"skill_name": ..., "evals": [...]}, got %s' % type(data).__name__]
    e = []
    if data.get("skill_name") != name:
        e.append("skill_name %r must equal frontmatter name %r" % (data.get("skill_name"), name))
    items = data.get("evals")
    if not (isinstance(items, list) and items):
        return 0, e + ["evals: non-empty list required"]
    ids = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            e.append("evals[%d]: object required" % i); continue
        if not (isinstance(it.get("id"), int) and not isinstance(it.get("id"), bool)):
            e.append("evals[%d].id: integer required" % i)
        else:
            ids.append(it["id"])
        for k in ("prompt", "expected_output"):
            if not _nonempty_str(it.get(k)):
                e.append("evals[%d].%s: non-empty string required" % (i, k))
        if "files" in it:
            fs = it["files"]
            if not (isinstance(fs, list) and all(_nonempty_str(f) for f in fs)):
                e.append("evals[%d].files: list of paths required" % i)
            else:
                for f in fs:
                    prob = _ref_problem(f)
                    if prob: e.append("evals[%d].files %s: %s" % (i, f, prob))
                    elif not os.path.isfile(os.path.join(path, f)): e.append("evals[%d].files %s: not found" % (i, f))
        for k in ("assertions", "expectations"):
            if k in it and not _str_list(it[k]):
                e.append("evals[%d].%s: non-empty list of strings required" % (i, k))
        if "layer" in it and it["layer"] not in EVAL_LAYERS:
            e.append("evals[%d].layer: must be one of %s (lowercase), got %r" % (i, "|".join(EVAL_LAYERS), it["layer"]))
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup: e.append("duplicate ids %s" % dup)
    return len(items), e

def check_skill(path):
    r = Report(path, "skill")
    fp = os.path.join(path, "SKILL.md")
    raw, rerr = read_text(fp)
    meta, body, ferr = parse_frontmatter_ex(raw) if raw is not None else (None, "", rerr)
    raw = raw or ""
    r.add("frontmatter", "structure", meta is not None, "YAML frontmatter between --- lines" + (": %s" % ferr if ferr else ""),
          "SKILL.md 첫 줄을 정확히 '---'로 시작하고, 'key: value' 매핑(name, description)으로 된 frontmatter를 '---'로 닫으세요.")
    meta = meta or {}
    typed = set(yaml_typed_keys(raw)) if meta else set()
    extra = sorted(k for k in meta if k not in SKILL_FIELDS + HOST_SKILL_FIELDS)
    host = sorted(k for k in meta if k in HOST_SKILL_FIELDS)
    r.add("frontmatter-fields", "structure", not extra,
          "frontmatter fields within spec %s" % (SKILL_FIELDS,) +
          (" + Claude Code host-specific %s" % host if host else "") + (": unexpected %s" % extra if extra else ""),
          "spec 밖 필드 %s를 frontmatter에서 빼세요. 입력·출력은 본문 '## Inputs'/'## Outputs' 섹션에, "
          "그 밖의 값은 metadata: 아래 문자열로 두세요." % extra, must=True)
    te = _field_type_errors(meta, typed)
    r.add("field-types", "structure", not te, "field types per spec" + (": %s" % te if te else ""),
          "spec 타입에 맞추세요: name·description·license·compatibility·allowed-tools는 문자열(allowed-tools는 공백 구분), "
          "metadata는 '키: 문자열' 매핑. name·description·compatibility가 숫자·날짜·yes/no처럼 보이면 따옴표로 감싸세요.", must=True)
    name, desc = _common_name(r, meta, path)
    bad_words = [w for w in RESERVED_NAME_WORDS if w in name.lower()]
    r.add("name-reserved", "single-task", not bad_words,
          "name has no reserved words %s" % (RESERVED_NAME_WORDS,) + (": contains %s" % bad_words if bad_words else ""),
          "name에서 예약어 %s를 빼세요." % bad_words, must=True)
    r.add("description-length", "single-task", 1 <= len(desc) <= DESCRIPTION_MAX,
          "description length %d (1..%d)" % (len(desc), DESCRIPTION_MAX),
          "description을 %d자 이하로 줄이세요(자세한 내용은 본문으로 옮기세요)." % DESCRIPTION_MAX, must=True)
    r.add("description-min-length", "single-task", len(desc) >= DESCRIPTION_MIN,
          "description length %d (>= %d, house rule)" % (len(desc), DESCRIPTION_MIN),
          "무엇을 하는지와 언제 쓰는지가 드러나게 description을 %d자 이상으로 쓰세요." % DESCRIPTION_MIN)
    r.add("description-no-xml", "single-task", "<" not in desc and ">" not in desc,
          "description has no XML tags / angle brackets", "description에서 '<', '>'(XML 태그)를 빼세요.", must=True)
    when = desc_when_clause(desc)
    r.add("description-when", "single-task", when is not None,
          "description has an English trigger clause outside quotes (sentence-initial 'Use when ...', 'Used when ...', "
          "'Use this skill when ...', 'Use for/to ... when ...', 'Invoke when ...', 'Trigger when ...', "
          "'... use this skill when ...', 'This skill should be used when ...', 'Read this skill before ...') with a concrete condition" + (": %s" % when if when else ""),
          "description에 영어 사용 조건 문장(예: 'Use when the user asks to summarize a PDF.')을 문장 첫머리에 넣으세요. "
          "'when'/'before' 뒤에 구체적인 조건(%d단어 이상)을 쓰세요. 'Use this.', 수동형('files loaded when', "
          "'Applies to X when', 'This skill is loaded when'), 부정형('Do not use when', 'Skip this skill when'), 따옴표 안 문장, 한국어 조건문('…할 때 사용한다')만으로는 "
          "인정하지 않습니다." % DESC_WHEN_MIN_WORDS, must=True)
    pov = desc_person_words(desc)
    r.add("description-pov", "single-task", not pov,
          "description has no first/second-person wording" + (": %s" % pov if pov else ""),
          "description을 3인칭으로 쓰세요(예: 'I can help you...'/'You can use this...' 대신 'Processes Excel files...'). "
          "1·2인칭 표현 %s를 빼세요." % pov, must=True)
    multi = sorted({m.group(0).lower() for m in MULTI_PURPOSE_RE.finditer(desc)})
    verbs = desc_task_verbs(desc)
    r.add("single-purpose", "single-task", not multi and len(verbs) <= SINGLE_PURPOSE_MAX_VERBS,
          "description focuses on one purpose (distinct task verbs %d, max %d; add-on phrases %d)%s" % (
              len(verbs), SINGLE_PURPOSE_MAX_VERBS, len(multi),
              ": task verbs %s%s" % (verbs, ", phrases %s" % multi if multi else "") if verbs or multi else ""),
          "여러 일을 나열하지 말고 스킬 하나에 작업 하나만 남기세요(나머지는 별도 스킬로 분리). 찾은 작업 동사: %s" % verbs)
    if "compatibility" in meta:
        comp = meta["compatibility"] if isinstance(meta["compatibility"], str) else ""
        r.add("compatibility-length", "structure", 1 <= len(comp.strip()) <= COMPATIBILITY_MAX,
              "compatibility length %d (1..%d)" % (len(comp.strip()), COMPATIBILITY_MAX),
              "compatibility를 1~%d자 문자열로 줄이세요(환경 요구사항이 없으면 필드를 빼세요)." % COMPATIBILITY_MAX, must=True)
    else:
        r.na("compatibility-length", "structure", "no compatibility (optional)", must=True)
    r.add("inputs-declared", "io", has_section(body, "input", "입력"),
          "inputs declared (## Inputs section)", "본문에 '## Inputs' 섹션을 추가하세요(frontmatter inputs:는 spec 밖 필드입니다).")
    r.add("outputs-declared", "io", has_section(body, "output", "출력"),
          "outputs declared (## Outputs section)", "본문에 '## Outputs' 섹션을 추가하세요(frontmatter outputs:는 spec 밖 필드입니다).")
    r.add("example-present", "io", has_section(body, "example", "예시"),
          "has ## Example section", "입력→출력 예시를 담은 '## Example' 섹션을 추가하세요.")
    nbytes, nlines = len(raw.encode("utf-8")), len(raw.splitlines())
    r.add("size-limit", "structure", nbytes <= SKILL_MAX_BYTES and nlines < SKILL_MAX_LINES,
          "SKILL.md %d bytes / %d lines (<=%d bytes, <%d lines)" % (nbytes, nlines, SKILL_MAX_BYTES, SKILL_MAX_LINES),
          "긴 내용은 references/로 옮기고 SKILL.md에서 링크하세요.")
    refs = local_refs(body)
    probs = sorted("%s (%s)" % (x, _ref_problem(x)) for x in refs if _ref_problem(x))
    r.add("refs-relative", "structure", not probs,
          "refs are forward-slash paths relative to the skill root" + (": %s" % probs if probs else ""),
          "참조를 스킬 폴더 기준 상대경로·슬래시(/)로 고치세요(예: references/guide.md): %s" % ", ".join(probs))
    absent = sorted(x for x in refs if not os.path.exists(os.path.join(path, x)))
    repo_refs = [x for x in absent if repo_root_ref(path, x)]
    missing = [x for x in absent if x not in repo_refs]
    if missing or not repo_refs:
        r.add("refs-exist", "structure", not missing,
              "referenced files exist" + (": missing %s" % missing if missing else "") +
              ("; repo-root-relative reference, not bundled: %s" % repo_refs if repo_refs else ""),
              "없는 파일을 만들거나 참조를 지우세요: %s" % ", ".join(missing))
    else:
        r.review("refs-exist", "structure",
                 "repo-root-relative reference, not bundled: %s (found from the enclosing git repo root, not the skill "
                 "folder; works only when the skill runs inside that repo)" % repo_refs,
                 "스킬을 리포 밖에서도 쓰려면 파일을 스킬 폴더(scripts/ 등)에 넣으세요. 리포 안에서만 쓰는 스킬이면 "
                 "본문에 '리포 루트 기준 경로'라고 밝히고 사람이 확인하세요: %s" % ", ".join(repo_refs))
    direct = {posixpath.normpath(x) for x in refs if not _ref_problem(x)}
    md_refs = sorted(x for x in direct if x.lower().endswith(".md") and os.path.isfile(os.path.join(path, x)))
    nested = []
    for x in md_refs:
        text, _ = read_text(os.path.join(path, x))
        for t in local_refs(text or ""):
            if _ref_problem(t): continue
            cand = posixpath.normpath(posixpath.join(posixpath.dirname(x), t))
            if not os.path.exists(os.path.join(path, cand)):
                cand = posixpath.normpath(t)
            if cand not in direct and cand != x and cand != "SKILL.md":
                nested.append("%s -> %s" % (x, t))
    r.add("refs-one-level", "structure", not nested,
          "no nested reference chains (files linked from SKILL.md link nothing SKILL.md doesn't)" +
          (": %s" % nested if nested else ""),
          "참조 파일이 다른 파일을 다시 참조하지 않게 하세요. 필요한 파일은 SKILL.md에서 직접 링크하세요: %s" % ", ".join(nested))
    deep = sorted(x for x in direct if x.count("/") > 1)
    r.add("refs-path-depth", "structure", not deep, "refs at most one directory deep" + (": %s" % deep if deep else ""),
          "참조 파일은 scripts/x, references/x 처럼 한 단계 폴더 안에 두세요.")
    long_refs = []
    for x in md_refs:
        text, _ = read_text(os.path.join(path, x))
        if text is not None and len(text.splitlines()) > REF_TOC_LINES:
            long_refs.append((x, _toc_first(text)))
    if long_refs:
        no_toc = [x for x, ok in long_refs if not ok]
        r.add("reference-toc", "structure", not no_toc,
              "reference files over %d lines start with a table of contents" % REF_TOC_LINES +
              (": missing in %s" % no_toc if no_toc else ""),
              "%d줄이 넘는 참조 파일 맨 위(제목 바로 다음)에 '## Contents' 목차를 넣으세요: %s" % (REF_TOC_LINES, ", ".join(no_toc)))
    else:
        r.na("reference-toc", "structure", "no referenced .md file over %d lines" % REF_TOC_LINES)
    md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    ver = md.get("version")
    if ver is not None:
        r.add("version-format", "structure", bool(re.match(r"^\d+\.\d+\.\d+$", str(ver))),
              "version '%s' semver (metadata.version)" % ver, "metadata.version에 \"1.0.0\" 같은 SemVer를 쓰세요.", must=True)
    else:
        r.na("version-format", "structure", "no metadata.version (optional)", must=True)
    n, ee = evals_errors(path, name)
    r.add("evals-valid", "evaluation", not ee, "evals/evals.json format" + (": %s" % ee if ee else " valid"),
          "evals/evals.json을 {\"skill_name\": \"%s\", \"evals\": [{\"id\": 1, \"prompt\": ..., \"expected_output\": ..., "
          "\"files\"(선택), \"assertions\"(선택)}]} 형식으로 쓰세요." % name, must=True)
    r.add("evals-count", "evaluation", n >= MIN_EVALS, "%d eval cases (need >= %d)" % (n, MIN_EVALS),
          "실제 사용자 요청처럼 쓴 테스트 케이스를 %d개 이상 두세요(경계 상황 1개 이상 포함)." % MIN_EVALS, must=True)
    return r

# ---------- agents (slated to move to the separate "agent master"; kept here until then) ----------
def _nonempty_str(v):
    return isinstance(v, str) and bool(v.strip())

def _str_list(v):
    """Non-empty list of non-empty strings."""
    return isinstance(v, list) and bool(v) and all(_nonempty_str(x) for x in v)

def _strs(v):
    """String items of v if it is a list, else [] (safe access for malformed input)."""
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []

def scenario_errors(s):
    """Shape errors of one scenario. Required: id, turns, expected_tools, must_include, expected_outcome."""
    if not isinstance(s, dict):
        return ["scenario must be a JSON object, got %s" % type(s).__name__]
    e = []
    if not _nonempty_str(s.get("id")):
        e.append("id: non-empty string required")
    t = s.get("turns")
    if not (isinstance(t, list) and t and all(isinstance(x, dict) and _nonempty_str(x.get("user")) for x in t)):
        e.append('turns: non-empty list of {"user": "<text>"} required')
    for k in ("expected_tools", "must_include"):
        if not _str_list(s.get(k)):
            e.append("%s: non-empty list of strings required" % k)
    for k, required in (("expected_outcome", True), ("success_criteria", False)):
        if k not in s and not required:
            continue
        v = s.get(k)
        if not (_nonempty_str(v) or _str_list(v)):
            e.append("%s: non-empty string or list of strings required" % k)
    return e

def load_scenarios(path):
    """Returns [(filename, data or None, errors)]; data is None when the JSON does not parse
    or the scenarios/ directory cannot be listed (e.g. PermissionError)."""
    d = os.path.join(path, "scenarios")
    out = []
    if os.path.isdir(d):
        try:
            names = os.listdir(d)
        except OSError as e:
            return [("scenarios/", None, ["cannot list scenarios/: %s" % e])]
        for f in sorted(names):
            if f.endswith(".json"):
                raw, err = read_text(os.path.join(d, f))  # OSError / UnicodeDecodeError -> error, not a crash
                if err:
                    out.append((f, None, [err])); continue
                try:
                    data = json.loads(raw)
                except ValueError as e:
                    out.append((f, None, ["invalid JSON: %s" % e])); continue
                out.append((f, data, scenario_errors(data)))
    return out

def check_agent(path):
    r = Report(path, "agent")
    raw, rerr = read_text(os.path.join(path, "AGENT.md"))
    meta, body, ferr = parse_frontmatter_ex(raw) if raw is not None else (None, "", rerr)
    r.add("frontmatter", "structure", meta is not None, "YAML frontmatter" + (": %s" % ferr if ferr else ""),
          "AGENT.md 첫 줄을 정확히 '---'로 시작하고, 'key: value' 매핑(name, description, goal, tools, success_criteria)으로 된 frontmatter를 '---'로 닫으세요.")
    meta = meta or {}
    _common_name(r, meta, path)
    goal = _str_field(meta, "goal")
    r.add("goal-present", "goal", len(goal) >= 15, "goal stated (>=15 chars)", "에이전트가 끝내야 할 목표를 goal에 한 문장으로 쓰세요.")
    crit = [str(c) for c in as_list(meta.get("success_criteria"))]
    # same checkers as transcript grading: measurable iff an outcome checker recognizes the criterion.
    # At least one must be machine-checkable; the rest stay as criteria and are graded needs-review per transcript.
    unmeasurable = [c for c in crit if outcome_evidence(c, "") is None]
    r.add("success-measurable", "goal", len(crit) > len(unmeasurable),
          "%d/%d success criteria machine-checkable%s" % (len(crit) - len(unmeasurable), len(crit),
          ("; needs-review (human/model grading) at transcript time: %s" % unmeasurable) if unmeasurable else ""),
          "자동 확인 가능한 success_criteria를 1개 이상 쓰세요(예: '출처 링크 3개 이상', '800자 이하', '비교표', "
          "'파일:줄 근거 2개 이상', '파일 변경 0건'). 자동 확인이 안 되는 기준은 지우지 말고 함께 두세요.", must=True)
    scen = load_scenarios(path)
    invalid = ["%s (%s)" % (f, "; ".join(e)) for f, s, e in scen if e]
    valid = [(f, s) for f, s, e in scen if not e]
    r.add("scenarios-present", "goal", len(valid) >= 2,
          "%d valid scenarios in scenarios/*.json" % len(valid),
          "scenarios/ 폴더에 유효한 JSON 시나리오를 2개 이상 넣으세요.")
    r.add("scenarios-valid", "goal", bool(scen) and not invalid,
          "scenario shape valid" + (": %s" % invalid if invalid else ""),
          "각 시나리오를 JSON 객체로 쓰고 id(문자열), turns([{\"user\": ...}]), expected_tools, must_include(문자열 목록), "
          "expected_outcome을 채우세요.", must=True)
    objs = [(f, s) for f, s, e in scen if isinstance(s, dict)]
    noexp = [f for f, s in objs if not s.get("expected_outcome")]
    r.add("scenarios-expected", "goal", bool(objs) and not noexp,
          "all scenarios have expected_outcome" + (": missing in %s" % noexp if noexp else ""),
          "각 시나리오에 expected_outcome을 넣으세요.")
    tools = [str(t) for t in as_list(meta.get("tools"))]
    r.add("tools-declared", "tool-use", bool(tools), "tools declared: %s" % tools, "frontmatter tools: 에 쓸 도구 목록을 선언하세요.")
    undeclared = sorted({t for f, s in objs for t in _strs(s.get("expected_tools")) if t not in tools})
    r.add("tools-subset", "tool-use", not undeclared,
          "scenario expected_tools within declared" + (": undeclared %s" % undeclared if undeclared else ""),
          "tools에 %s를 선언하거나 시나리오에서 빼세요." % undeclared)
    r.add("tools-documented", "tool-use", has_section(body, "tool", "도구"),
          "## Tools section explains usage", "도구별로 언제 쓰는지 설명하는 '## Tools' 섹션을 추가하세요.")
    r.add("memory-section", "context", has_section(body, "memory", "handoff", "context", "기억", "인수인계"),
          "memory/handoff section", "'## Memory & Handoff' 섹션에 무엇을 기억하고 어떻게 넘기는지 쓰세요.")
    multi = [f for f, s in objs if isinstance(s.get("turns"), list) and len(s["turns"]) >= 2]
    r.add("multi-turn-scenario", "context", bool(multi),
          "multi-turn scenarios: %s" % multi, "turns가 2개 이상이고 앞 턴 정보를 참조하는 시나리오를 추가하세요.")
    return r

def check(path):
    kind = detect_kind(path)
    if kind == "skill": return check_skill(path)
    if kind == "agent": return check_agent(path)
    r = Report(path, "unknown")
    r.add("frontmatter", "structure", False, "no SKILL.md or AGENT.md", "폴더에 SKILL.md 또는 AGENT.md를 넣으세요.")
    return r

def find_units(root):
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if not d.startswith("."))
        if "SKILL.md" in fn or "AGENT.md" in fn:
            out.append(dp); dn[:] = []
    return out

# ---------- transcript grading (agent side; slated for split-out with the agent checks) ----------
def load_transcript(path):
    """Parse + validate a transcript. Returns (run, errors); run is None when errors.
    Format: {"scenario": str, "final": str, "workspace": str (optional),
             "tool_calls": [ "<tool>" | {"tool": str, "input": str|object (optional), "result": str (optional)} ],
             "tool_results": [str, ...] (optional)}
    run = {"scenario", "final", "workspace", "calls": [{"tool", "input", "result"}], "results": [str]}"""
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError) as e:
        return None, ["cannot load transcript JSON: %s" % e]
    if not isinstance(t, dict):
        return None, ["transcript must be a JSON object, got %s" % type(t).__name__]
    e = []
    if not _nonempty_str(t.get("scenario")): e.append("scenario: non-empty string required")
    if not isinstance(t.get("final"), str): e.append("final: string required")
    if "workspace" in t and not _nonempty_str(t["workspace"]): e.append("workspace: non-empty string required")
    calls = []
    tc = t.get("tool_calls")
    if not isinstance(tc, list):
        e.append("tool_calls: list required")
        tc = []
    for i, c in enumerate(tc):
        if isinstance(c, str) and c.strip():
            calls.append({"tool": c, "input": None, "result": None})
        elif isinstance(c, dict) and _nonempty_str(c.get("tool")):
            if "result" in c and not isinstance(c["result"], str):
                e.append("tool_calls[%d].result: string required" % i)
            unknown = sorted(set(c) - {"tool", "input", "result"})
            if unknown:
                e.append("tool_calls[%d]: unknown fields %s" % (i, unknown))
            calls.append({"tool": c["tool"], "input": c.get("input"), "result": c.get("result")})
        else:
            e.append('tool_calls[%d]: tool name string or {"tool": str, "input", "result"} object required' % i)
    tr = t.get("tool_results", [])
    if not (isinstance(tr, list) and all(isinstance(x, str) for x in tr)):
        e.append("tool_results: list of strings required"); tr = []
    if e:
        return None, e
    results = [c["result"] for c in calls if c["result"]] + list(tr)
    return {"scenario": t["scenario"], "final": t["final"], "workspace": t.get("workspace"),
            "calls": calls, "results": results}, []

def grade_transcript(agent_path, transcript_path):
    """Grade one run transcript against the agent's declaration and scenario (see load_transcript for format)."""
    r = Report(transcript_path, "transcript")
    run, terr = load_transcript(transcript_path)
    r.add("transcript-valid", "structure", run is not None, "transcript shape" + (": %s" % terr if terr else " valid"),
          "transcript를 {\"scenario\": str, \"tool_calls\": [...], \"final\": str} 형식으로 고치세요 (LOOP.md 참고).", must=True)
    if run is None: return r
    raw, rerr = read_text(os.path.join(agent_path, "AGENT.md"))
    meta, _, ferr = parse_frontmatter_ex(raw) if raw is not None else (None, "", rerr)
    r.add("agent-valid", "structure", meta is not None, "agent AGENT.md frontmatter" + (": %s" % ferr if ferr else " valid"),
          "채점할 에이전트 폴더의 AGENT.md frontmatter를 먼저 고치세요.", must=True)
    if meta is None: return r
    tools = [str(x) for x in as_list(meta.get("tools"))]
    scen = load_scenarios(agent_path)
    sid = run["scenario"]
    match = [(f, s, e) for f, s, e in scen if isinstance(s, dict) and s.get("id") == sid]
    s = match[0][1] if match and not match[0][2] else None
    reason = "scenario '%s' exists" % sid if s is not None else (
        "scenario '%s' invalid: %s" % (sid, match[0][2]) if match else "scenario '%s' not found" % sid)
    r.add("scenario-known", "goal", s is not None, reason, "transcript의 scenario를 유효한 시나리오 id와 맞추세요.", must=True)
    if s is None: return r
    names = [c["tool"] for c in run["calls"]]
    illegal = sorted(set(names) - set(tools))
    r.add("only-declared-tools", "tool-use", not illegal, "illegal tools: %s" % illegal,
          "선언되지 않은 도구 %s 사용을 멈추세요." % illegal, must=True)
    miss = [x for x in s["expected_tools"] if x not in names]
    r.add("expected-tools-called", "tool-use", not miss, "missing calls: %s" % miss,
          "필요한 도구 %s를 호출하세요." % miss, must=True)
    viol = answer_lookup_violations(run["calls"], run["workspace"])
    r.add("no-answer-lookup", "tool-use", not viol,
          "%d tool calls inspected%s" % (len(run["calls"]), (": answer lookup %s" % viol) if viol else ", no answer lookup"),
          "워크스페이스 밖 탐색(.., /, ~, 워크스페이스 밖 절대경로)이나 git 이력 뒤지기(git log --all, reflog, fsck 등)를 멈추고 "
          "정답을 찾아보지 말고 주어진 워크스페이스 안의 근거로 직접 푸세요.", must=True)
    final = run["final"]
    lack = [k for k in s["must_include"] if k.lower() not in final.lower()]
    r.add("outcome-keywords", "goal", not lack, "final answer missing: %s" % lack,
          "최종 답에 %s를 포함하세요." % lack, must=True)
    # outcome: every machine-checkable criterion must hold on the final answer (must-pass)
    sources = [("criteria", c) for c in as_list(meta.get("success_criteria"))]
    sources += [("criteria", c) for c in as_list(s.get("success_criteria"))]
    sources += [("expected", c) for c in as_list(s.get("expected_outcome"))]
    seen, checked = {}, 0
    for origin, crit in sources:
        crit = str(crit)
        ev = outcome_evidence(crit, final, run)
        base = "%s-%s" % (origin, ev[0] if ev else "needs-review")
        seen[base] = seen.get(base, 0) + 1
        cid = base if seen[base] == 1 else "%s-%d" % (base, seen[base])
        if ev is None:
            r.review(cid, "goal", "not machine-checkable, grade by human/model: '%s'" % crit,
                     "사람 또는 모델 채점자가 최종 답이 '%s'를 만족하는지 판정하세요." % crit)
            continue
        checked += 1
        _, ok, got, fix = ev
        r.add(cid, "goal", ok, "'%s' -> %s" % (crit, got), fix, must=True)
    r.add("outcome-evidence", "goal", checked > 0, "%d checkable outcome criteria" % checked,
          "expected_outcome/success_criteria에 확인 가능한 조건(예: '출처 3개 이상', '800자 이하', '비교표')을 넣으세요.", must=True)
    return r

# ---------- answer-lookup integrity (no-answer-lookup) ----------
PATH_KEYS = {"path", "paths", "file", "file_path", "filepath", "filename", "dir", "directory", "cwd", "root", "target"}
CMD_KEYS = {"command", "cmd", "script", "args"}
GIT_HISTORY_RE = re.compile(
    r"\bgit\b[^;&|\n]*?\b(?:reflog|fsck|cat-file|rev-list\b[^;&|\n]*--all|"
    r"(?:log|shortlog|whatchanged)\b[^;&|\n]*(?:--all|--reflog|--walk-reflogs|\s-g\b)|"
    r"[^;&|\n]*--(?:lost-found|unreachable))")

def _strings(v):
    if isinstance(v, str): yield v
    elif isinstance(v, list):
        for x in v: yield from _strings(x)
    elif isinstance(v, dict):
        for x in v.values(): yield from _strings(x)

def _call_inputs(inp):
    """Split a tool input into (path-like strings, command strings)."""
    if isinstance(inp, str):
        return [], [inp]
    paths, cmds = [], []
    if isinstance(inp, dict):
        for k, v in inp.items():
            if k in PATH_KEYS: paths += list(_strings(v))
            elif k in CMD_KEYS: cmds += list(_strings(v))
    elif isinstance(inp, list):
        cmds += list(_strings(inp))
    return paths, cmds

def _outside(token, workspace=None):
    """True if a path token points outside the workspace (.., ~, filesystem root, foreign absolute path)."""
    tok = token.strip().strip("'\"")
    if tok.startswith("-") and "=" in tok:
        tok = tok.split("=", 1)[1]
    if not tok: return False
    if tok == "~" or tok.startswith("~/"): return True
    if tok.startswith("/"):
        if tok.rstrip("/") == "": return True
        if workspace:
            ws = posixpath.normpath(workspace)
            norm = posixpath.normpath(tok)
            return not (norm == ws or norm.startswith(ws.rstrip("/") + "/"))
        return False
    norm = posixpath.normpath(tok)
    return norm == ".." or norm.startswith("../")

def answer_lookup_violations(calls, workspace=None):
    """List of '<tool>: <offending input>' for calls that look for answers instead of solving the task:
    searching outside the workspace or mining git history."""
    out = []
    for c in calls:
        paths, cmds = _call_inputs(c["input"])
        for p in paths:
            if _outside(p, workspace):
                out.append("%s: path %s" % (c["tool"], p))
        for cmd in cmds:
            if GIT_HISTORY_RE.search(cmd):
                out.append("%s: git history mining '%s'" % (c["tool"], cmd))
            try:
                toks = shlex.split(cmd)
            except ValueError:
                toks = cmd.split()
            bad = [t for t in toks if _outside(t, workspace)]
            if bad:
                out.append("%s: outside workspace %s in '%s'" % (c["tool"], bad, cmd))
    return out

# ---------- outcome checkers ----------
URL_RE = re.compile(r"https?://[^\s<>()\[\]|\"'`]+")
TABLE_RE = re.compile(r"^[ \t]*\|?.*\|.*\n[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$", re.M)
FILELINE_RE = re.compile(r"(?<![\w/.:-])((?:[\w.-]+/)*[\w-][\w.-]*\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?(?![\d])")
# Line-numbered tool output that proves a line was read: cat -n / nl / Read-tool style "N<tab|→|:>text"
# (cat -n '%6d\t', Read tool 'N→'), attributed to the one file that call read (_read_call). Only a result that is
# line-numbered throughout counts (see _numbered_lines); single-file grep -n output is parsed in _grep_lines.
NUMBERED_LINE_RE = re.compile(r"^[ \t]*(\d+)(\t|→|:)")
GREP_LINE_RE = re.compile(r"^(\d+)[:-]")
FILELINE_CRIT_RE = re.compile(r"파일\s*[:·/]\s*줄|file\s*:\s*line|file[- ]line", re.I)
NO_WRITE_CRIT_RE = re.compile(r"(?:파일\s*)?(?:변경|수정|쓰기)\s*0\s*건|읽기\s*전용|read[- ]only|"
                              r"no\s+(?:file\s+)?(?:writes?|edits?|changes|modifications)|zero\s+(?:file\s+)?(?:writes|edits|changes)", re.I)
WRITE_WORDS = {"write", "edit", "editor", "multiedit", "create", "delete", "remove", "rm", "move", "mv", "rename",
               "patch", "apply", "replace", "insert", "append", "save", "commit", "push", "mkdir", "touch", "update"}
WRITE_CMD_RE = re.compile(r"(?:^|[;&|]\s*|\bsudo\s+)(?:rm|mv|cp|touch|mkdir|rmdir|tee|truncate|chmod|chown|ln|dd)\b|"
                          r"\bsed\s+(?:-\w*\s+)*-i|(?<![0-9&>])>>?\s*(?!&|/dev/null)\S|"
                          r"\bgit\s+(?:commit|push|add|rm|mv|reset|checkout|restore|apply|stash|merge|rebase|cherry-pick)\b")

def _norm_url(u):
    return u.rstrip(".,;:").rstrip("/")

def _tool_words(name):
    return {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)}

def write_calls(calls):
    """Tool calls that write/edit files: write-like tool names or write-like shell commands."""
    out = []
    for c in calls:
        if _tool_words(c["tool"]) & WRITE_WORDS:
            out.append(c["tool"]); continue
        _, cmds = _call_inputs(c["input"])
        hit = [cmd for cmd in cmds if WRITE_CMD_RE.search(cmd)]
        if hit:
            out.append("%s '%s'" % (c["tool"], hit[0]))
    return out

def _norm_path(p, cwd=None, workspace=None):
    """Canonical form of a file path for exact comparison: quotes and './' dropped, normalized, joined to cwd when
    relative, and made relative to the workspace when it lies inside it. No suffix matching: 'README.md' and
    'docs/README.md' are different files."""
    p = p.strip().strip("'\"")
    if cwd and not p.startswith("/"):
        p = posixpath.join(cwd.strip().strip("'\""), p)
    p = posixpath.normpath(p)
    if workspace and p.startswith("/"):
        ws = posixpath.normpath(workspace)
        if p.startswith(ws.rstrip("/") + "/"):
            p = posixpath.relpath(p, ws)
    return p[2:] if p.startswith("./") else p

SHELL_META_RE = re.compile(r"[|;&<>`$()\n]")
# Tool names (split into words by _tool_words) that read one file and return its lines: 'Read', 'read_file',
# 'view_file', 'open_file', 'cat'. Search/list/stat/count/write tools ('grep', 'search', 'wc', 'stat', 'write') are not.
READ_TOOL_WORDS = {"read", "view", "open", "cat"}
NOT_READ_TOOL_WORDS = WRITE_WORDS | {"search", "grep", "rg", "find", "glob", "list", "ls", "stat", "wc", "count",
                                     "head", "tail", "diff", "fetch", "web", "url", "http"}
# Tools whose input is a shell command; the command itself must then be a recognized single-file read.
SHELL_TOOL_WORDS = {"bash", "shell", "sh", "zsh", "terminal", "exec", "execute", "run", "command", "cmd", "console"}
CWD_KEYS = {"cwd"}
_GREP_FLAG_LETTERS = set("niwFExhHvsI")  # options that keep 'N:line' output for one file honest

def _numbered_read_cmd(cmd):
    """(kind, file) for a shell command that prints one file's lines with their real line numbers, else None.
    kind 'numbered': 'cat -n FILE' (not -b/-s, which skip or squeeze lines), 'nl -ba FILE' (optionally -w N).
    kind 'grep': 'grep -n [-i -w -F -E -x -v -h -H -s -A N -B N -C N] [-e] PATTERN FILE' on exactly one file
    (output 'N:line', context 'N-line', with -H 'FILE:N:line'). Pipes, redirects, several files, recursion and any
    other command (echo, printf, wc -l, head, ...) are not attributable."""
    if SHELL_META_RE.search(cmd): return None
    try: toks = shlex.split(cmd)
    except ValueError: return None
    if not toks: return None
    prog, args = os.path.basename(toks[0]), toks[1:]
    if prog == "grep":
        letters, pos, i, has_n, pattern = set(), [], 0, False, None
        while i < len(args):
            a = args[i]
            if a in ("-e", "--regexp") and i + 1 < len(args):
                if pattern is not None: return None
                pattern = args[i + 1]; i += 2; continue
            if a in ("-A", "-B", "-C") and i + 1 < len(args) and args[i + 1].isdigit():
                i += 2; continue
            if re.match(r"^-[ABC]\d+$", a):
                i += 1; continue
            if a in ("--line-number",):
                has_n = True; i += 1; continue
            if a.startswith("-") and a != "-" and not a.startswith("--"):
                letters |= set(a[1:]); i += 1; continue
            if a.startswith("--"):
                return None
            pos.append(a); i += 1
        if letters - _GREP_FLAG_LETTERS or not (has_n or "n" in letters):
            return None
        if pattern is None:
            if not pos: return None
            pos = pos[1:]
        return ("grep", pos[0]) if len(pos) == 1 and pos[0] != "-" else None
    files = [t for t in args if not t.startswith("-") or t == "-"]
    flags = [t for t in args if t.startswith("-") and t != "-"]
    if prog == "cat":
        letters = "".join(f[1:] for f in flags if not f.startswith("--"))
        longs = [f for f in flags if f.startswith("--")]
        ok = ("n" in letters or "--number" in longs) and not set(letters) - {"n"} and not set(longs) - {"--number"}
    elif prog == "nl":
        if "a" in files and "-b" in flags:  # 'nl -b a FILE'
            files.remove("a"); flags = ["-ba" if f == "-b" else f for f in flags]
        if "-w" in flags and files and files[0].isdigit():  # 'nl -w 3 FILE'
            files.pop(0); flags.remove("-w")
        ok = "-ba" in flags and all(f == "-ba" or re.match(r"^-w\d+$", f) for f in flags)
    else:
        ok = False
    return ("numbered", files[0]) if ok and len(files) == 1 and files[0] != "-" else None

def _read_call(call, workspace=None):
    """(kind, file as printed, canonical file) when a tool call read exactly one file, else None.
    Read-tool style: a read tool (READ_TOOL_WORDS, no NOT_READ_TOOL_WORDS) with exactly one path-key value and no
    command. Shell style: a shell tool (SHELL_TOOL_WORDS) whose one command is _numbered_read_cmd. An optional
    'cwd' key resolves relative paths. Anything else (echo/printf output, wc/stat/search/write tools with a path key,
    several paths) reads no attributable file."""
    inp, words = call["input"], _tool_words(call["tool"])
    cwd = None
    if isinstance(inp, dict):
        cw = [v for k, v in inp.items() if k in CWD_KEYS]
        if cw:
            if len(cw) != 1 or not _nonempty_str(cw[0]): return None
            cwd = cw[0]
        inp = {k: v for k, v in inp.items() if k not in CWD_KEYS}
    paths, cmds = _call_inputs(inp)
    paths = [x for x in paths if x.strip()]
    if words & NOT_READ_TOOL_WORDS:
        return None
    if paths and not cmds and words & READ_TOOL_WORDS:
        if len(paths) != 1: return None
        return "numbered", paths[0], _norm_path(paths[0], cwd, workspace)
    if cmds and not paths and words & SHELL_TOOL_WORDS and len(cmds) == 1:
        rc = _numbered_read_cmd(cmds[0])
        if rc: return rc[0], rc[1], _norm_path(rc[1], cwd, workspace)
    return None

def lines_seen(run):
    """{line number: {canonical file paths}} for lines a tool call showed with their real line numbers.
    A line counts only from the result of a call that read exactly that one file (_read_call) and only when the whole
    result has that call's line-numbered shape: 'N<sep>line' for a read tool / 'cat -n' / 'nl -ba' (_numbered_lines),
    'N:line' or 'N-line' (or 'FILE:N:line' for that same FILE) for single-file 'grep -n' (_grep_lines). Top-level tool_results and path-prefixed lines from any other call ('echo src/a.py:42:x', multi-file
    grep) are not attributable to a read and do not count."""
    seen = {}
    if not run: return seen
    ws = run.get("workspace")
    for c in run["calls"]:
        if not c["result"]: continue
        rc = _read_call(c, ws)
        if rc is None: continue
        kind, printed, target = rc
        nums = _grep_lines(c["result"], printed) if kind == "grep" else _numbered_lines(c["result"], printed, target, ws)
        for ln in nums:
            seen.setdefault(ln, set()).add(target)
    return seen

def _result_lines(result):
    """Lines of a tool result without the trailing empty lines a final newline leaves."""
    lines = [ln.rstrip("\r") for ln in result.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines

def _numbered_lines(result, printed, target, ws=None):
    """Line numbers of a read result that is line-numbered throughout: every line is 'N<sep>text' with one separator
    (tab, '→' or ':') for the whole result and consecutive numbers N, N+1, ... Raw file content ('200 OK\\n404 Not
    Found', a '42 x' line) yields []. A 'wc -l' style 'N path' line is a count, not line N, and yields no number."""
    parsed = [(NUMBERED_LINE_RE.match(ln), ln) for ln in _result_lines(result)]
    if not parsed or any(m is None for m, _ in parsed): return []
    if len({m.group(2) for m, _ in parsed}) != 1: return []
    nums = [int(m.group(1)) for m, _ in parsed]
    if any(b != a + 1 for a, b in zip(nums, nums[1:])): return []
    out = []
    for n, (m, ln) in zip(nums, parsed):
        rest = ln[m.end():].strip()
        if rest and (_norm_path(rest) == _norm_path(printed) or _norm_path(rest, None, ws) == target):
            continue
        out.append(n)
    return out

def _grep_lines(result, printed):
    """Line numbers of single-file 'grep -n' output: every line is 'N:text' (match) or 'N-text' (context),
    optionally prefixed 'FILE:' / 'FILE-' for the read FILE (-H), or a '--' group separator, with increasing numbers.
    Any other line means the result is not that grep's output and yields []."""
    out = []
    for line in _result_lines(result):
        if line == "--": continue
        for pre in (printed + ":", printed + "-"):
            if line.startswith(pre) and re.match(r"^\d+%s" % re.escape(pre[-1]), line[len(pre):]):
                line = line[len(pre):]; break
        m = GREP_LINE_RE.match(line)
        if not m or (out and int(m.group(1)) <= out[-1]): return []
        out.append(int(m.group(1)))
    return out

def outcome_evidence(crit, final, run=None):
    """Map one criterion to checkable evidence on the final answer (and the run's tool calls/results).
    run: normalized transcript from load_transcript (None = no tool calls, no tool results).
    Returns (kind, ok, observed, fix) or None if the criterion is not machine-checkable."""
    calls = run["calls"] if run else []
    results = run["results"] if run else []
    if NO_WRITE_CRIT_RE.search(crit):
        w = write_calls(calls)
        return ("no-writes", not w, "%d write/edit tool calls%s" % (len(w), (": %s" % w) if w else ""),
                "파일을 바꾸는 도구(write/edit 등) 호출 %s를 없애고 읽기 전용으로 작업하세요." % w)
    n = _min_count(crit)
    if n is not None and FILELINE_CRIT_RE.search(crit):
        seen, ws = lines_seen(run), (run or {}).get("workspace")
        cites = sorted({(f, int(a), int(b) if b else int(a)) for f, a, b in FILELINE_RE.findall(final)})
        shown = lambda f, ln: _norm_path(f, None, ws) in seen.get(ln, ())
        # a range 'a-b' needs a <= b and every line a..b shown (all() stops at the first unseen line)
        ok_cites = [c for c in cites if c[1] <= c[2] and all(shown(c[0], ln) for ln in range(c[1], c[2] + 1))]
        bad = [("%s:%d" % c[:2]) + ("-%d" % c[2] if c[2] != c[1] else "") for c in cites if c not in ok_cites]
        return ("file-lines", len(ok_cites) >= n,
                "%d file:line citations whose lines appear in captured read results (need >= %d)%s" % (len(ok_cites), n,
                ("; cited lines never shown by a tool: %s" % bad) if bad else ""),
                "그 파일 하나만 읽은 도구 호출(Read 도구, cat -n FILE, nl -ba FILE, grep -n PATTERN FILE)의 결과에 줄 번호와 "
                "함께 나온 줄만 '경로:줄'(예: src/app.py:42)로 인용하세요. 범위 '경로:시작-끝'은 시작 <= 끝이고 그 사이 모든 줄이 "
                "나왔어야 합니다. 경로는 읽은 경로와 정확히 같아야 합니다. "
                "%d개 이상 필요 (현재 확인된 것 %d개). 없는 줄·읽지 않은 줄은 세지 않습니다." % (n, len(ok_cites)))
    if n is not None and re.search(r"출처|인용|링크|source|citation|cite|reference|url|link", crit, re.I):
        urls = sorted(set(_norm_url(u) for u in URL_RE.findall(final)))
        fetched = set(_norm_url(u) for res in results for u in URL_RE.findall(res))
        ok_urls = [u for u in urls if u in fetched]
        bad = [u for u in urls if u not in fetched]
        return ("sources", len(ok_urls) >= n,
                "%d verified unique URLs (need >= %d)%s" % (len(ok_urls), n,
                ("; not found in tool results: %s" % bad) if bad else ""),
                "도구 결과(tool result)에 실제로 나온 출처 URL을 %d개 이상 인용하세요 (현재 확인된 것 %d개). "
                "지어낸 URL은 세지 않습니다." % (n, len(ok_urls)))
    mx = _max_chars(crit)
    if mx is not None:
        return ("length", len(final) <= mx, "%d chars (max %d)" % (len(final), mx),
                "최종 답을 %d자 이하로 줄이세요 (현재 %d자)." % (mx, len(final)))
    if re.search(r"비교표|표로|표 형식|\btable\b", crit, re.I):
        ok = bool(TABLE_RE.search(final))
        return ("table", ok, "markdown table %s" % ("found" if ok else "not found"),
                "최종 답에 헤더와 구분선(|---|)이 있는 마크다운 표를 넣으세요.")
    return None

def _min_count(text):
    m = (re.search(r"(\d+)\s*(?:개|건|곳)?\s*이상", text) or re.search(r"at\s+least\s+(\d+)", text, re.I)
         or re.search(r">=\s*(\d+)", text) or re.search(r"(\d+)\s*\+", text))
    return int(m.group(1)) if m else None

def _max_chars(text):
    m = (re.search(r"(\d+)\s*자\s*(?:이하|이내)", text)
         or re.search(r"(?:at\s+most|no\s+more\s+than|<=|under)\s*(\d+)\s*(?:chars?|characters)", text, re.I))
    return int(m.group(1)) if m else None

# ---------- feedback / index ----------
def feedback_md(r, rnd=None):
    d = r.to_dict()
    lines = ["# Eva Feedback: %s" % os.path.basename(os.path.normpath(r.path)), "",
             "- kind: %s / score: %.0f%% / verdict: **%s**%s" % (d["kind"], d["score"] * 100, d["verdict"],
             (" / round %s" % rnd) if rnd else ""), ""]
    fails = r.failed
    if d["category_failed"]:
        lines += ["## 카테고리 하한 미달 (각 카테고리 통과율 %.0f%% 이상, 1개 이상 통과 필요)" % (CATEGORY_FLOOR * 100), ""]
        for k in d["category_failed"]:
            lines.append("- %s: %d/%d 통과" % (k, d["categories"][k]["passed"], d["categories"][k]["total"]))
        lines.append("")
    if d["needs_review"]:
        lines += ["## 사람/모델 채점 필요 (needs-review, 자동 판정 불가)", ""]
        for c in r.needs_review:
            lines.append("- `%s`: %s" % (c["id"], c["reason"]))
        lines.append("")
    if not fails:
        lines.append("자동 검사는 모두 통과했습니다." if d["needs_review"] else "모든 검사를 통과했습니다. 수정할 것이 없습니다.")
    else:
        lines += ["## 고쳐야 할 것 (중요도 순)", ""]
        fails.sort(key=lambda c: not c["must"])
        for i, c in enumerate(fails, 1):
            tag = " [필수]" if c["must"] else ""
            lines += ["%d. `%s`%s (%s)" % (i, c["id"], tag, c["category"]),
                      "   - 이유: %s" % c["reason"], "   - 수정: %s" % c["fix"]]
    lines += ["", "다 고친 뒤 `python3 eva.py check %s`로 다시 제출하세요." % r.path]
    return "\n".join(lines) + "\n"

def build_index(root):
    reg = []
    for u in find_units(root):
        kind = detect_kind(u)
        raw, _ = read_text(os.path.join(u, "SKILL.md" if kind == "skill" else "AGENT.md"))
        meta = (parse_frontmatter(raw)[0] if raw is not None else None) or {}
        rep = check(u)
        reg.append({"kind": kind, "name": meta.get("name", ""), "version": (meta.get("metadata") or {}).get("version", meta.get("version", "")) if isinstance(meta.get("metadata", {}), dict) else meta.get("version", ""),
                    "description": meta.get("description", ""), "path": os.path.relpath(u, root),
                    "verdict": rep.verdict, "score": rep.score})
    md = ["# INDEX", "", "| kind | name | version | verdict | score | path | description |", "|---|---|---|---|---|---|---|"]
    for e in reg:
        md.append("| %s | %s | %s | %s | %.2f | %s | %s |" % (e["kind"], e["name"], e["version"], e["verdict"],
                  e["score"], e["path"], str(e["description"]).replace("|", "/")[:120]))
    return reg, "\n".join(md) + "\n"

def print_report(r):
    d = r.to_dict()
    print("[%s] %s (%s) score=%.2f threshold=%.2f" % (d["verdict"], r.path, d["kind"], d["score"], PASS_THRESHOLD))
    for c in d["checks"]:
        mark = {"pass": "✓", "fail": "✗", "n/a": "-", "needs-review": "?"}[c["status"]]
        print("  %s %-22s %-11s %s" % (mark, c["id"], c["category"], c["reason"]))
    if d["category_failed"]:
        print("  category below floor %.2f: %s" % (CATEGORY_FLOOR, d["category_failed"]))

def main(argv=None):
    p = argparse.ArgumentParser(prog="eva", description="Eva skill/agent evaluator")
    sp = p.add_subparsers(dest="cmd", required=True)
    for n in ("check", "check-all", "feedback", "index"):
        s = sp.add_parser(n); s.add_argument("path"); s.add_argument("--json", action="store_true")
        if n == "check": s.add_argument("--transcript")
        if n == "feedback": s.add_argument("--round", type=int)
    a = p.parse_args(argv)
    if a.cmd == "check":
        r = grade_transcript(a.path, a.transcript) if a.transcript else check(a.path)
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2)) if a.json else print_report(r)
        return EXIT_CODES[r.verdict]
    if a.cmd == "check-all":
        units = find_units(a.path)
        if not units:
            print("no SKILL.md/AGENT.md units found under %s" % a.path, file=sys.stderr)
            return 1
        reps = [check(u) for u in units]
        if a.json:
            print(json.dumps([r.to_dict() for r in reps], ensure_ascii=False, indent=2))
        else:
            for r in reps: print("[%s] %.2f %s" % (r.verdict, r.score, r.path))
            print("total=%d pass=%d review=%d fail=%d" % (len(reps), sum(r.verdict == "PASS" for r in reps),
                  sum(r.verdict == "REVIEW" for r in reps), sum(r.verdict == "FAIL" for r in reps)))
        verdicts = {r.verdict for r in reps}
        return EXIT_CODES["FAIL"] if "FAIL" in verdicts else EXIT_CODES["REVIEW"] if "REVIEW" in verdicts else 0
    if a.cmd == "feedback":
        r = check(a.path)
        open(os.path.join(a.path, "FEEDBACK.md"), "w", encoding="utf-8").write(feedback_md(r, a.round))
        json.dump(r.to_dict(), open(os.path.join(a.path, "FEEDBACK.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("%s -> FEEDBACK.md, FEEDBACK.json (%s %.2f)" % (a.path, r.verdict, r.score))
        return EXIT_CODES[r.verdict]
    if a.cmd == "index":
        reg, md = build_index(a.path)
        open(os.path.join(a.path, "INDEX.md"), "w", encoding="utf-8").write(md)
        json.dump(reg, open(os.path.join(a.path, "registry.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("indexed %d units -> INDEX.md, registry.json" % len(reg))
        return 0

if __name__ == "__main__":
    sys.exit(main())
