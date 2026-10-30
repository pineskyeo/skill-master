"""The static hub site (unit B5): renders the unit views skill_eval.unit_view builds into index.html, one page per
unit, style.css and registry.json, scans every output file (English only, ASCII only, no character reference but the
five the escaper writes, no JSON or CSS escape, no local path, no URL, no script or style, relative links to files of
the site), and writes the site only when the scan found nothing. Standard library only; no import of eva or skill_eval; no process, socket or git."""
import os, posixpath, re

from engine import approvals, registry
from engine.primitives import script_denylist

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hub_templates")
TEMPLATE_FILES = ("index.html", "unit.html", "style.css")
TOOLS = ("claude", "opencode", "hermes")
_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
PLACEHOLDER_RE = re.compile(r"\{\{([a-z_]+)\}\}")
LINK_RE = re.compile(r"""\b(?:href|src|srcset|action|formaction|poster)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""",
                     re.I)
TAG_RE = re.compile(r"<\s*/?\s*([A-Za-z][A-Za-z0-9-]*)([^>]*)>")
EVENT_OR_STYLE_RE = re.compile(r"""[\s/"'](?:on[a-z]+|style|http-equiv)\s*=""", re.I)
REFERENCE_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|#39);)")
LOCAL_PATHS = ("/Users/", "/home/", "/private/", "/var/folders/", "/Volumes/")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SAFE_PATH_RE = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
SKILL_DIRS = {"claude": ".claude/skills", "opencode": ".opencode/skills", "hermes": ".hermes/skills"}
SKILL_NOTES = {"claude": "Claude Code reads project skills from .claude/skills."}
AGENT_COPIES = {"claude": (".claude/agents", "%s.md")}
AGENT_NOTES = {"claude": "Claude Code reads project agents from .claude/agents."}
WRAPPER_FILES = {"claude": "wrappers/claude/%s.md", "opencode": "wrappers/opencode/%s.md",
                 "hermes": "wrappers/hermes/SOUL.md"}
UNVERIFIED = "UNVERIFIED: no %s engine here (gates.md G33)"
TONES = {"pass": "ok", "PASS": "ok", "run": "ok", "registered": "ok", "fail": "bad", "FAIL": "bad", "invalid": "bad",
         "review": "warn", "REVIEW": "warn", "stale": "warn", "unsigned": "warn", "pending": "warn"}


class HubError(Exception):
    pass


def esc(text):
    """HTML text with only the five references &amp; &lt; &gt; &quot; &#39; written."""
    return "".join(_ESC.get(c, c) for c in text)


def shown(value, what):
    """value as page text: withheld when it holds non-English script or any other non-ASCII character."""
    text = value if isinstance(value, str) else "" if value is None else str(value)
    if script_denylist.english_text_findings(text, what): return "(%s withheld: not English)" % what
    if not text.isascii(): return "(%s withheld: not ASCII)" % what
    return text


def slug(path):
    return re.sub(r"[^a-z0-9-]", "-", path.lower().replace("/", "--"))


def badge(key, text, status):
    words = str(status).split()
    tone = TONES.get(words[0].rstrip(":") if words else "", "muted")
    return '<span class="badge %s" data-badge="%s">%s</span>' % (tone, key, esc(text))


def badges(view, labels):
    """[(key, text, status)] of a unit, every text built from the view's records and check results."""
    out = [("registration", shown(view["registration_label"], "registration"), view["registration"]),
           ("verdict", "%s %.2f" % (view["verdict"], view["score"]), view["verdict"]),
           ("english", "English %s" % view["english"], view["english"]),
           ("version", "version %s" % (shown(view["version"], "version") or "none"), ""),
           ("scripts", "scripts" if view["scripts"] else "no scripts", "")]
    out += [("parity-" + t, "%s parity: %s" % (labels[t], view["parity"][t]), view["parity"][t]) for t in TOOLS]
    out += [("coverage-" + t, "%s coverage: %s" % (labels[t], shown(view["coverage"][t], "coverage")), view["coverage"][t])
            for t in TOOLS]
    return out


def snippet(view, tool, labels):
    """(note, lines) of one tool's project-scope copy block; <clone> is the reader's git clone of the repository."""
    name, path = view["name"], view["path"]
    note = UNVERIFIED % labels[tool] if tool not in SKILL_NOTES else (SKILL_NOTES if view["kind"] == "skill" else
                                                                      AGENT_NOTES)[tool]
    if not (isinstance(name, str) and SAFE_NAME_RE.fullmatch(name) and SAFE_PATH_RE.fullmatch(path)
            and ".." not in path.split("/")):
        return note, ["no snippet: the unit name or path is not a plain ASCII path"]
    if view["kind"] == "skill":
        return note, ["mkdir -p %s" % SKILL_DIRS[tool], "cp -R <clone>/%s %s/%s" % (path, SKILL_DIRS[tool], name)]
    wrapper = "%s/%s" % (path, WRAPPER_FILES[tool].replace("%s", name))
    if not view["wrappers"].get(tool):
        return note, ["no generated wrapper: run python3 eva.py generate %s" % path]
    if tool in AGENT_COPIES:
        dest, file = AGENT_COPIES[tool]
        return note, ["mkdir -p %s" % dest, "cp <clone>/%s %s/%s" % (wrapper, dest, file % name)]
    return note, ["wrapper file: <clone>/%s (no destination is claimed)" % wrapper]


def fill(template, values, where):
    missing = [k for k in PLACEHOLDER_RE.findall(template) if k not in values]
    if missing: raise HubError("template %s has an unknown placeholder {{%s}}" % (where, missing[0]))
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def load_templates(folder):
    folder = folder or TEMPLATES
    out = {}
    for name in TEMPLATE_FILES:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f: out[name] = f.read()
        except (OSError, UnicodeDecodeError) as ex:
            raise HubError("template %s: %s" % (name, ex))
    return out


def machine_waiver_html(view):
    mw = view.get("machine_waiver")
    rec = mw.get("record") if isinstance(mw, dict) else None
    if not isinstance(rec, dict): return "<li>none</li>"
    return ('<li data-machine-waiver="%s">machine waiver <code>%s</code> by %s, tools %s, expires %s: %s</li>'
            % (esc(str(mw.get("sha256"))), esc(str(mw.get("sha256"))), esc(shown(approvals.decided_by(rec), "principal")),
               esc(",".join(str(t) for t in rec.get("tools") or [])), esc(str(rec.get("expires"))),
               esc(shown(rec.get("reason"), "reason"))))


def findings_html(view):
    """The named-findings section of a registered agent's page; empty for every other unit, so their pages keep their
    bytes."""
    findings = view.get("findings")
    if findings is None: return ""
    items = "\n".join("<li>%s</li>" % esc(shown(f, "finding")) for f in findings) or "<li>none</li>"
    return '\n<h2>Findings the registration approval named</h2>\n<ul class="findings">\n%s\n</ul>' % items


def unit_page(view, labels, template):
    name = shown(view["name"], "name")
    items = "\n".join("<li>%s</li>" % badge(*b) for b in badges(view, labels))
    hashes = "\n".join('<li data-hash="%s"><code>%s</code> %s</li>' % (k, v, k) for k, v in sorted(view["hashes"].items()))
    approvals = "\n".join(["<li>%s by %s%s</li>" % (esc(a["gate"]), esc(shown(a["principal"], "principal")),
                                                     ": " + esc(shown(a["reason"], "reason")) if a["reason"] else "")
                           for a in view["approvals"]] +
                          ([machine_waiver_html(view)] if view.get("machine_waiver") else [])) or "<li>none</li>"
    blocks = []
    for tool in TOOLS:
        note, lines = snippet(view, tool, labels)
        blocks.append('<div class="snippet" data-tool="%s">\n<h3>%s</h3>\n<p class="note">%s</p>\n<pre><code>%s</code></pre>\n'
                      "</div>" % (tool, esc(labels[tool]), esc(note), esc("\n".join(lines))))
    n = view["coverage_iteration"]
    return fill(template, {
        "name": esc(name), "kind": esc(view["kind"]), "path": esc(shown(view["path"], "path")),
        "description": esc(shown(view["description"], "description")), "badges": items, "hashes": hashes,
        "approvals": approvals, "findings": findings_html(view), "snippets": "\n".join(blocks),
        "coverage_note": esc("Coverage per tool is recomputed from the runs of iteration-%d." % n if n else
                             "Coverage per tool: no eval iteration was read.")}, "unit.html")


def index_row(view, labels, page):
    cells = [esc(view["kind"]), '<a href="%s">%s</a>' % (page, esc(shown(view["name"], "name"))),
             esc(shown(view["version"], "version"))]
    by = {b[0]: b for b in badges(view, labels)}
    cells += [badge(*by[k]) for k in ("registration", "verdict", "english")]
    cells += [" ".join(badge(*by[p + t]) for t in TOOLS) for p in ("parity-", "coverage-")]
    cells.append(badge(*by["scripts"]))
    return '<tr class="unit">%s</tr>' % "".join("<td>%s</td>" % c for c in cells)


def render(views, registry_text, labels, templates=None):
    """{relative path: text} of the whole site. HubError when two units map to one page or a template is unusable."""
    t = load_templates(templates)
    views = registry.ordered(views, lambda v: v["kind"], lambda v: v["name"], lambda v: v["version"], lambda v: v["path"])
    files, rows, owner = {}, [], {}
    for v in views:
        rel = "units/%s.html" % slug(v["path"])
        if rel in owner: raise HubError("two units map to the page %s: %s and %s" % (rel, owner[rel], v["path"]))
        owner[rel] = v["path"]
        files[rel] = unit_page(v, labels, t["unit.html"])
        rows.append(index_row(v, labels, rel))
    registered = sum(v["registration"] == "registered" for v in views)
    files["index.html"] = fill(t["index.html"], {"rows": "\n".join(rows), "summary": esc(
        "%d units, %d registered." % (len(views), registered))}, "index.html")
    files["style.css"] = t["style.css"]
    files["registry.json"] = registry_text
    return files


def _links(text):
    return [next(g for g in m.groups() if g is not None) for m in LINK_RE.finditer(text)]


def scan(files, local=()):
    """One finding per problem of the rendered site; [] when every file may be written."""
    out = []
    local = list(LOCAL_PATHS) + ["://"] + sorted(p for p in local if len(p) > 1)
    for rel in sorted(files):
        text = files[rel]
        if rel.endswith(".json"):
            viol, review = script_denylist.english_json_findings(text, rel)
            out += viol + review
            refuse = local + ["\\u"]
        else:
            out += script_denylist.english_text_findings(text, rel)
            refuse = local + ["//"] + (["\\", "url(", "@import"] if rel.endswith(".css") else [])
        out += ["%s: holds %r" % (rel, s) for s in refuse if s in text]
        bad = next((i for i, c in enumerate(text) if not (" " <= c <= "~" or c in "\n\t")), None)
        if bad is not None:
            out.append("%s:%d: character U+%04X is not printable ASCII" % (rel, text.count("\n", 0, bad) + 1, ord(text[bad])))
        if not rel.endswith(".html"): continue
        m = REFERENCE_RE.search(text)
        if m: out.append("%s:%d: character reference %r (only &amp; &lt; &gt; &quot; &#39; are written)" % (
            rel, text.count("\n", 0, m.start()) + 1, text[m.start():m.start() + 10]))
        for m in TAG_RE.finditer(text):
            if m.group(1).lower() in ("script", "style") or EVENT_OR_STYLE_RE.search(m.group(2)):
                out.append("%s: a %s element or a script or style attribute is not allowed" % (rel, m.group(1).lower()))
        for link in _links(text):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), link))
            if not link or link.startswith(("/", "#")) or ":" in link or "\\" in link or target not in files:
                out.append("%s: link %r is not a relative link to a file of the site" % (rel, link))
    return out


def out_problem(out):
    if os.path.islink(out) or os.path.exists(out) and not (os.path.isdir(out) and not os.listdir(out)):
        return "hub build: --out %s must be absent or an empty folder; nothing was written" % out
    return None


def write(out, files):
    problem = out_problem(out)
    if problem: raise HubError(problem)
    try:
        for rel in sorted(files):
            fp = os.path.join(out, *rel.split("/"))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="ascii", newline="\n") as f: f.write(files[rel])
    except OSError as ex:
        raise HubError("hub build: cannot write %s: %s" % (out, ex))
