"""Unit lifecycle (unit B6, plan.md:1370): the SemVer bump with its changelog rule, the deprecation marker and drift.
Standard library only; no import of eva or skill_eval (skill_eval passes the frontmatter reader, the ruleset hash, the
recorded hashes and the rule dates in). Nothing here starts a process or opens a socket."""
import copy, datetime, re
from engine import registry

TOOLS = ("claude", "codex", "opencode", "hermes")
PARTS = ("major", "minor", "patch")
DEPRECATIONS_FORMAT = 1
_VERSION_RE = re.compile(r"^(?P<lead>\s*version:[ \t]*)(?P<q>['\"]?)(?P<v>[^'\"\s#]+)(?P=q)(?P<tail>[ \t]*(#.*)?)$")


class LifecycleError(Exception):
    pass


def _english(line):
    return all(" " <= c <= "~" or c == "\t" for c in line)


def next_version(version, part):
    """The next release of a SemVer version: part major, minor or patch is raised, the lower parts set to 0, the
    prerelease and the build cleared."""
    p = registry.parse_semver(version)
    if p is None: raise LifecycleError("version %r is not SemVer 2.0.0 (MAJOR.MINOR.PATCH)" % (version,))
    if part not in PARTS: raise LifecycleError("part %r is not one of %s" % (part, ", ".join(PARTS)))
    major, minor, patch = p[:3]
    if part == "major": return "%d.0.0" % (major + 1)
    if part == "minor": return "%d.%d.0" % (major, minor + 1)
    return "%d.%d.%d" % (major, minor, patch + 1)


def changelog_problem(text, version):
    """None when text holds a line exactly '## <version>' followed, before the next '## ' heading, by a non-blank line,
    and that entry is English (printable ASCII); else why not."""
    if not isinstance(text, str) or not text.strip(): return "CHANGELOG.md is missing or empty"
    lines = text.splitlines()
    head = "## " + version
    if head not in lines: return "no line %r" % head
    i = lines.index(head)
    body = []
    for ln in lines[i + 1:]:
        if ln.startswith("## "): break
        body.append(ln)
    if not any(ln.strip() for ln in body): return "no line of text under %r" % head
    if not all(_english(ln) for ln in body): return "the %r entry is not English (printable ASCII only)" % head
    return None


def version_line(text, kind):
    """(index, match) of the frontmatter line that holds the unit version: for a skill metadata.version, else the
    top-level version; for an agent the top-level version. LifecycleError when there is no such single line."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != "---": raise LifecycleError("no frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r") == "---"), None)
    if end is None: raise LifecycleError("the frontmatter is not closed")
    hits = []
    if kind == "skill":
        meta = [i for i in range(1, end) if re.match(r"metadata:\s*$", lines[i])]
        j = meta[0] + 1 if meta else end
        while j < end and (not lines[j].strip() or lines[j][:1] in " \t"):
            if re.match(r"[ \t]+version:", lines[j]): hits.append(j)
            j += 1
    if not hits: hits = [i for i in range(1, end) if lines[i].startswith("version:")]
    if len(hits) != 1: raise LifecycleError("no single version line in the frontmatter")
    m = _VERSION_RE.match(lines[hits[0]].rstrip("\r"))
    if not m: raise LifecycleError("the version line %r is not a plain scalar" % lines[hits[0]])
    return hits[0], m


def set_version(text, kind, new, read_version):
    """text with only the version line's value set to new (its quotes and comment kept); read_version(text) is the
    caller's frontmatter reader, which must then give new."""
    i, m = version_line(text, kind)
    lines = text.split("\n")
    cr = "\r" if lines[i].endswith("\r") else ""
    lines[i] = "%s%s%s%s%s%s" % (m.group("lead"), m.group("q"), new, m.group("q"), m.group("tail") or "", cr)
    out = "\n".join(lines)
    if read_version(out) != new: raise LifecycleError("the rewritten version line does not read back as %s" % new)
    return out


def reason_problem(reason):
    if not isinstance(reason, str) or not reason.strip(): return "the reason is empty"
    if not all(" " <= c <= "~" for c in reason): return "the reason holds a character that is not printable ASCII"
    if reason != reason.strip(): return "the reason has leading or trailing spaces"
    return None


def deprecate(data, version, reason, current, at):
    """(deprecations data, changed): data with version marked deprecated for reason. The same version and reason
    again is no change; a version that is not SemVer or above current, and a bad reason, raise LifecycleError."""
    if registry.parse_semver(version) is None: raise LifecycleError("--version %r is not SemVer 2.0.0" % (version,))
    if registry.parse_semver(current) is None:
        raise LifecycleError("the unit's current version %r is not SemVer 2.0.0" % (current,))
    if registry.semver_key(version) > registry.semver_key(current):
        raise LifecycleError("version %s is above the current version %s" % (version, current))
    problem = reason_problem(reason)
    if problem: raise LifecycleError(problem)
    if data is None: data = {"format": DEPRECATIONS_FORMAT, "versions": {}}
    if not (isinstance(data, dict) and data.get("format") == DEPRECATIONS_FORMAT and isinstance(data.get("versions"), dict)):
        raise LifecycleError("deprecations.json is not format %d" % DEPRECATIONS_FORMAT)
    old = data["versions"].get(version)
    if isinstance(old, dict) and old.get("reason") == reason: return data, False
    out = copy.deepcopy(data)
    out["versions"][version] = {"reason": reason, "at": at}
    return out, True


def deprecated_reason(data, version):
    entry = data.get("versions", {}).get(version) if isinstance(data, dict) and isinstance(data.get("versions"), dict) \
        else None
    return entry.get("reason") if isinstance(entry, dict) else None


def parse_day(text):
    try:
        return datetime.date.fromisoformat(text) if isinstance(text, str) and len(text) == 10 else None
    except ValueError:
        return None


def drift_report(pins, observed, units, current, rules, today, max_days):
    """(lines, listed count). pins: {tool: {version, verified, source} or None}; observed: {tool: version}; units:
    [(kind, name, recorded ruleset_sha256 or None)]; current: the ruleset_sha256 now; rules: [(file, id, verified date
    or None)]; today: YYYY-MM-DD; max_days: the verified-age limit."""
    day = parse_day(today)
    if day is None: raise LifecycleError("--today %r is not a real date YYYY-MM-DD" % (today,))
    lines, listed = ["tools:"], 0
    for tool in TOOLS:
        pin, obs = pins.get(tool), observed.get(tool)
        pinned = ("pinned %s (verified %s, %s)" % (pin["version"], pin["verified"], pin["source"]) if pin else
                  "no pinned version (not installed when the versions were recorded)")
        if obs is None:
            state = "observed unknown (drift starts no tool binary; pass --observed %s=<version>)" % tool
        elif pin and obs == pin["version"]:
            state = "observed %s, same" % obs
        else:
            state, listed = "observed %s, DRIFT" % obs, listed + 1
        lines.append("tool %s: %s, %s" % (tool, pinned, state))
    lines.append("units:")
    for kind, name, recorded in units:
        if recorded != current:
            listed += 1
            lines.append("older ruleset: %s/%s recorded %s current %s" % (kind, name, recorded[:12] if recorded else "none",
                                                                          current[:12]))
    lines.append("rules:")
    for fname, rid, verified in rules:
        if verified is None:
            lines.append("rule %s (%s): unverified (source.verified is null)" % (rid, fname))
        elif parse_day(verified) is None:
            lines.append("rule %s (%s): verified %r is not a date" % (rid, fname, verified))
        elif (day - parse_day(verified)).days > max_days:
            lines.append("rule %s (%s): verified %s, older than %d days before %s" % (rid, fname, verified, max_days,
                                                                                     today))
        else:
            continue
        listed += 1
    lines.append("drift: %d listed" % listed if listed else "drift: nothing listed")
    return lines, listed
