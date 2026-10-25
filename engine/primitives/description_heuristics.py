"""The description_heuristics op (moved from eva.py, unit A4): the English trigger clause, first- and second-person
words and the single-purpose verb count of a SKILL.md or AGENT.md description. Tokenising, quote stripping and sentence
splitting are code; every word list and regex comes from a data file (rules/data/description.json), compiled once per
distinct file content. No import of eva or skill_eval."""
import os, re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DATA = os.path.join(ROOT, "rules", "data", "description.json")
_LOADED = {}


class DataError(ValueError):
    pass


def _verb_forms(v):
    f = {v, v + "s", v + "es", v + "ing"}
    if v.endswith("e"): f.add(v[:-1] + "ing")
    if v.endswith("y"): f.add(v[:-1] + "ies")
    if re.search(r"[^aeiou][aeiou][bdgmnprt]$", v): f.add(v + v[-1] + "ing")  # split -> splitting
    return f


def _text(v):
    return isinstance(v, str)


def _regex(v):
    try:
        return isinstance(v, str) and re.compile(v) is not None
    except re.error:
        return False


def _words(v):
    return isinstance(v, list) and all(isinstance(w, str) and w for w in v)


def _count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _heading_words(v):
    return isinstance(v, dict) and all(_words(w) and w for w in v.values())


# Every key of a description data file and the shape its value must have; a value of another shape fails the bundle load.
REGEX = (_regex, "a regex string that compiles")
WORDS = (_words, "a list of non-empty strings")
COUNT = (_count, "an integer >= 0")
SHAPES = {
    "DESC_USE_VERB": REGEX, "DESC_USE_START_RE": REGEX, "DESC_THIS_SKILL_OBJ_RE": REGEX, "DESC_THIS_SKILL_SUBJ_RE": REGEX,
    "DESC_READ_RE": REGEX, "DESC_NEGATION_RE": REGEX, "DESC_SENTENCE_RE": REGEX, "DESC_TIGHT_STOP_RE": REGEX,
    "DESC_LIST_MARKERS": (_text, "a string of characters"), "DESC_WORD_RE": REGEX, "DESC_WHEN_MIN_WORDS": COUNT,
    "DESC_WHEN_VAGUE": WORDS, "ROMAN_I_BEFORE": REGEX, "DESC_PERSON_RE": REGEX, "QUOTED_RE": REGEX, "TASK_VERBS": WORDS,
    "AMBIGUOUS_TASK_VERBS": WORDS, "TASK_TOKEN_RE": REGEX, "TASK_EXCLUSION_RE": REGEX, "TASK_JOINERS": WORDS,
    "TASK_JOINER_PHRASE_RE": REGEX, "SINGLE_PURPOSE_MAX_VERBS": COUNT, "MULTI_PURPOSE_RE": REGEX,
    "HEADING_WORDS": (_heading_words, "an object of non-empty lists of non-empty strings"),
}


class Data:
    """The compiled word lists and regexes of one data file."""
    def __init__(self, doc):
        for key, (ok, shape) in SHAPES.items():
            if key not in doc:
                raise DataError("not a description data file: %s is missing" % key)
            if not ok(doc[key]):
                raise DataError("not a description data file: %s must be %s" % (key, shape))
        try:
            self.use_start = re.compile(doc["DESC_USE_START_RE"], re.I)
            self.this_skill_obj = re.compile(doc["DESC_THIS_SKILL_OBJ_RE"], re.I)
            self.this_skill_subj = re.compile(doc["DESC_THIS_SKILL_SUBJ_RE"], re.I)
            self.read = re.compile(doc["DESC_READ_RE"], re.I)
            self.negation = re.compile(doc["DESC_NEGATION_RE"], re.I)
            self.sentence = re.compile(doc["DESC_SENTENCE_RE"])
            self.tight_stop = re.compile(doc["DESC_TIGHT_STOP_RE"])
            self.list_markers = doc["DESC_LIST_MARKERS"]
            self.word = re.compile(doc["DESC_WORD_RE"])
            self.joiner_phrase = re.compile(doc["TASK_JOINER_PHRASE_RE"], re.I)
            self.when_min_words = int(doc["DESC_WHEN_MIN_WORDS"])
            self.when_vague = frozenset(doc["DESC_WHEN_VAGUE"])
            self.person = re.compile(doc["DESC_PERSON_RE"], re.I)
            self.roman_i = re.compile(r"\b%s\s+I(?![\w/-])" % doc["ROMAN_I_BEFORE"], re.I)
            self.quoted = re.compile(doc["QUOTED_RE"])
            self.verb_forms = {f: v for v in doc["TASK_VERBS"] for f in _verb_forms(v)}
            self.ambiguous = frozenset(doc["AMBIGUOUS_TASK_VERBS"])
            self.token = re.compile(doc["TASK_TOKEN_RE"])
            self.exclusion = re.compile(doc["TASK_EXCLUSION_RE"], re.I)
            self.joiners = frozenset(doc["TASK_JOINERS"])
            self.max_verbs = int(doc["SINGLE_PURPOSE_MAX_VERBS"])
            self.multi_purpose = re.compile(doc["MULTI_PURPOSE_RE"], re.I)
            self.heading_words = {k: list(v) for k, v in doc["HEADING_WORDS"].items()}
        except (KeyError, TypeError, ValueError, AttributeError, re.error) as e:
            raise DataError("not a description data file: %s: %s" % (type(e).__name__, e)) from None


def data_for(path):
    """The Data of the file at path, one object per distinct file content (strict JSON: engine.schema.loads)."""
    from engine import schema
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise DataError("%s: cannot read: %s" % (os.path.basename(path), e.strerror or e)) from None
    if raw not in _LOADED:
        try:
            doc = schema.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, schema.JsonError) as e:
            raise DataError("%s: %s" % (os.path.basename(path), e)) from None
        if not isinstance(doc, dict):
            raise DataError("%s: not a JSON object" % os.path.basename(path))
        _LOADED[raw] = Data(doc)
    return _LOADED[raw]


def default():
    return data_for(DEFAULT_DATA)


def unquoted(text, data=None):
    """text with quoted spans ("...", '...', \u201c...\u201d, \u2018...\u2019, `...`) blanked out; apostrophes ("it's") are
    not quotes."""
    d = data or default()
    return d.quoted.sub(lambda m: " " * len(m.group(0)), text)


def when_clause(desc, data=None):
    """The condition text of the first substantive English trigger clause in desc ('the user wants a word count'
    from 'Use when the user wants a word count.'), else None. Quoted text is blanked first (unquoted)."""
    d = data or default()
    for sent in d.sentence.split(unquoted(desc, d)):
        sent = sent.strip().lstrip(d.list_markers).strip()
        for m in (d.use_start.match(sent), d.read.match(sent), d.this_skill_subj.match(sent), d.this_skill_obj.search(sent)):
            if m is None or d.negation.search(sent[:m.start(1)]):
                continue
            cond = " ".join(d.tight_stop.split(m.group(1))[0].split()).rstrip(".!?")
            words = d.word.findall(cond)
            if len(words) >= d.when_min_words and any(w.lower() not in d.when_vague for w in words):
                return cond
    return None


def person_words(desc, data=None):
    """First/second-person words in desc outside quotes, Roman numeral 'Phase I' style excluded."""
    d = data or default()
    text = unquoted(desc, d)
    roman = {m.end() - 1 for m in d.roman_i.finditer(text)}
    return sorted(set(m.group(0) for m in d.person.finditer(text) if m.start() not in roman))


def task_verbs(desc, data=None):
    """Distinct task verbs (base form, in order) that open a coordinated item outside trigger sentences."""
    d = data or default()
    out = []
    for sent in d.sentence.split(unquoted(desc, d)):
        if when_clause(sent, d) is not None or d.exclusion.search(sent):
            continue
        toks = d.token.findall(d.joiner_phrase.sub(" and ", sent))
        start, after_to = True, False
        for i, t in enumerate(toks):
            w = t.lower()
            if not w[0].isalpha():
                start, after_to = True, False; continue
            v = d.verb_forms.get(w)
            nxt = toks[i + 1].lower() if i + 1 < len(toks) else ""
            if start and v and nxt != "this" and v not in out and \
                    (v not in d.ambiguous or w.endswith("ing") or after_to):
                out.append(v)
            start, after_to = w in d.joiners, w == "to"
    return out


def multi_phrases(desc, data=None):
    """Sorted distinct add-on phrases ('and also', 'as well as', 'plus') in desc, lower-cased."""
    d = data or default()
    return sorted({m.group(0).lower() for m in d.multi_purpose.finditer(desc)})


def description(meta):
    """The description as eva.py _str_field read it: the stripped string, else ''."""
    v = meta.get("description")
    return v.strip() if isinstance(v, str) else ""


class DescriptionCheck:
    """{op: description_heuristics, data, measure: present|length|no_xml|when|pov|single_purpose, min, max, chars}.
    Facts carry the reason parts eva.py formatted, so the rule messages render the A3 reasons."""
    MEASURES = ("present", "length", "no_xml", "when", "pov", "single_purpose")

    def __init__(self, node, folder):
        self.data = data_for(os.path.join(folder, *node["data"].split("/")))
        self.measure, self.node = node["measure"], node
        if self.measure not in self.MEASURES:
            raise ValueError("description_heuristics: unknown measure '%s'" % self.measure)

    def __call__(self, unit):
        from engine.primitives.heading_present import main_file
        desc, d, n = description(main_file(unit).meta_or_empty), self.data, self.node
        if self.measure == "present":
            return self.verdict(bool(desc), {})
        if self.measure == "length":
            lo, hi = n.get("min", 0), n.get("max")
            return self.verdict(lo <= len(desc) and (hi is None or len(desc) <= hi),
                                {"length": len(desc), "min": lo, "max": hi})
        if self.measure == "no_xml":
            return self.verdict(not any(c in desc for c in n["chars"]), {})
        if self.measure == "when":
            when = when_clause(desc, d)
            return self.verdict(when is not None, {"clause": ": %s" % when if when else "",
                                                   "min_words": d.when_min_words})
        if self.measure == "pov":
            pov = person_words(desc, d)
            return self.verdict(not pov, {"detail": ": %s" % pov if pov else "", "words": "%s" % pov})
        multi, verbs = multi_phrases(desc, d), task_verbs(desc, d)
        detail = ": task verbs %s%s" % (verbs, ", phrases %s" % multi if multi else "") if verbs or multi else ""
        return self.verdict(not multi and len(verbs) <= d.max_verbs,
                            {"count": len(verbs), "max": d.max_verbs, "phrases": len(multi), "detail": detail,
                             "verbs": "%s" % verbs})

    @staticmethod
    def verdict(ok, facts):
        return ("pass" if ok else "fail"), facts, None, False
