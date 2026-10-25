"""The heading_present op and the markdown helpers it rests on (moved from eva.py, unit A4): ATX headings outside fenced
code, and the main file of a unit (SKILL.md or AGENT.md) read and split as eva.py check_skill and check_agent read it.
No import of eva or skill_eval."""
import os, re
from engine.loaders import parse_frontmatter_ex

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# CommonMark ATX heading: 0-3 spaces, 1-6 '#', then a space/tab or end of line; optional closing '#' sequence
ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
MAIN_FILE = {"skill": "SKILL.md", "agent": "AGENT.md"}


def split_fences(text):
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


def unfenced(text):
    """text with fenced code blocks removed."""
    return "\n".join(split_fences(text)[0])


def fenced(text):
    """Lines inside fenced code blocks."""
    return split_fences(text)[1]


def headings(text):
    """[(level, heading text)] of ATX headings outside fenced code."""
    out = []
    for ln in unfenced(text).splitlines():
        m = ATX_RE.match(ln)
        if m: out.append((len(m.group(1)), (m.group(2) or "").strip()))
    return out


def has_section(body, *words):
    """True if a markdown heading (outside fenced code blocks) contains any of words."""
    return any(w.lower() in t.lower() for _, t in headings(body) for w in words)


def read_text(fp):
    """Returns (text, error) without raising on missing/undecodable files."""
    try:
        with open(fp, encoding="utf-8") as f:
            return f.read(), None
    except (OSError, UnicodeDecodeError) as e:
        return None, "cannot read %s: %s" % (os.path.basename(fp), e)


class MainFile:
    """The unit's SKILL.md or AGENT.md: raw text ('' when unreadable), meta (None when the frontmatter does not parse),
    body, the read or parse error, and meta_or_empty."""
    def __init__(self, path, kind):
        raw, rerr = read_text(os.path.join(path, MAIN_FILE[kind]))
        self.meta, self.body, self.error = parse_frontmatter_ex(raw) if raw is not None else (None, "", rerr)
        self.raw = raw or ""
        self.meta_or_empty = self.meta or {}


def main_file(unit):
    """The MainFile of a rule runner unit, read once per Bundle.apply."""
    if "main_file" not in unit.memo:
        unit.memo["main_file"] = MainFile(unit.path, unit.kind)
    return unit.memo["main_file"]


class HeadingCheck:
    """{op: heading_present, data: data/<file>.json, words: <key of HEADING_WORDS>}: pass when a heading of the main
    file body, outside fenced code, contains one of the words (case-insensitive substring)."""
    def __init__(self, node, folder):
        from engine.primitives import description_heuristics
        data = description_heuristics.data_for(os.path.join(folder, *node["data"].split("/")))
        if node["words"] not in data.heading_words:
            raise ValueError("heading_present: %s has no HEADING_WORDS entry '%s'" % (node["data"], node["words"]))
        self.words = tuple(data.heading_words[node["words"]])

    def __call__(self, unit):
        return ("pass" if has_section(main_file(unit).body, *self.words) else "fail"), {}, None, False
