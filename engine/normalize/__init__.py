"""One normalizer per tool behind one registry (unit B3 D4): normalize(tool, text, scenario_id, agent=None) turns a
tool's raw run stream into a transcript of schemas/transcript.schema.json, or raises NormalizeError. Every event and
item type a normalizer reads is in its own table with its action; anything else raises, so a stream it cannot read is
never graded."""
import importlib, json, os

TOOLS = ("claude", "codex", "opencode", "hermes")
SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "schemas",
                      "transcript.schema.json")
_COMPILED = {}


class NormalizeError(ValueError):
    pass


def normalize(tool, text, scenario_id, agent=None):
    if tool not in TOOLS:
        raise NormalizeError("no normalizer for tool %r (tools: %s)" % (tool, ", ".join(TOOLS)))
    return importlib.import_module("engine.normalize." + tool).normalize(text, scenario_id, agent=agent)


def events(tool, text):
    """The JSON objects of a JSON-lines stream; a line that is not a JSON object raises."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError as e:
            raise NormalizeError("%s: line %d is not JSON (%s)" % (tool, n, e))
        if not isinstance(ev, dict):
            raise NormalizeError("%s: line %d is not a JSON object" % (tool, n))
        out.append((n, ev))
    return out


def tokens(value, what):
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NormalizeError("%s must be a non-negative integer, got %r" % (what, value))
    return value


def checked(transcript):
    """The transcript, after it validates against schemas/transcript.schema.json."""
    from engine import schema
    if "s" not in _COMPILED:
        _COMPILED["s"] = schema.compile_schema(schema.load(SCHEMA))
    errs = _COMPILED["s"].validate(transcript)
    if errs:
        raise NormalizeError("%s: the normalized transcript does not match transcript.schema.json: %s"
                             % (transcript.get("tool"), "; ".join(schema.format_error(e) for e in errs[:3])))
    return transcript
