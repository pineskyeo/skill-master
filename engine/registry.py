"""The unit registry (units B5, B6): SemVer 2.0.0 parse and order, the default skill and agent workspaces, the format-2
registration record rules, the verify-now state classifier over injected callables, and the registry document with its
canonical text. No import of eva or skill_eval (the callers pass the hashes and the approver in)."""
import json, os, re

REGISTRATION_FORMAT = 2
REGISTRY_FORMAT = 1
NOT_A_UNIT = "neither SKILL.md nor AGENT.md is a regular file"
NO_WORKSPACE = "the SKILL.md name is not a safe path component"
NO_AGENT_WORKSPACE = "the agent name (the AGENT.md v2 name, else the folder name) is not a safe path component"
SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ID = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(%s(?:\.%s)*))?"
                       r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?" % (_ID, _ID))
RECORD_HASHES = ("benchmark_sha256", "skill_sha256", "eval_set_sha256", "skill_md_sha256", "ruleset_sha256")
SCHEMA_NAME = "schemas/registration.schema.json"


def parse_semver(text):
    """(major, minor, patch, prerelease identifiers) of a SemVer 2.0.0 string, None for anything else."""
    m = SEMVER_RE.fullmatch(text) if isinstance(text, str) else None
    if not m: return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), tuple(m.group(4).split(".")) if m.group(4) else ()


def semver_key(text):
    """SemVer 2.0.0 section 11 precedence as a sort key: build metadata ignored, a prerelease below its release,
    numeric identifiers compared numerically and below alphanumeric ones, a longer identifier list above its prefix."""
    major, minor, patch, pre = parse_semver(text)
    return major, minor, patch, not pre, tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre)


def safe_name(name):
    """A skill name is one safe path component: it names the install folder and the workspace."""
    return isinstance(name, str) and bool(SAFE_NAME_RE.match(name)) and ".." not in name


def unit_name(unit_dir, meta):
    """The SKILL.md name, else the folder name."""
    return meta.get("name") if isinstance(meta.get("name"), str) else os.path.basename(os.path.normpath(unit_dir))


def unit_workspace(unit_dir, meta):
    """The default workspace <name>-workspace next to the skill folder (skill-creator convention), None when the name
    is not a safe path component."""
    name = unit_name(unit_dir, meta)
    return os.path.join(os.path.dirname(unit_dir), name + "-workspace") if safe_name(name) else None


def agent_name(agent_dir, info):
    """The agent name of the agent commands: the AGENT.md v2 name when info (engine/primitives/transcript.read_agent of
    the folder) is valid, else the name of the realpath folder."""
    return info.meta["name"] if info.valid else os.path.basename(os.path.realpath(agent_dir))


def agent_workspace(agent_dir, name=None):
    """The default agent workspace of the agent commands, the record command and the index: <name>-workspace next to
    the realpath agent folder, None when the name is not a safe path component. name: agent_name, read here when None."""
    if name is None:
        from engine.primitives import transcript
        name = agent_name(agent_dir, transcript.read_agent(os.path.realpath(agent_dir)))
    return os.path.join(os.path.dirname(os.path.realpath(agent_dir)), name + "-workspace") if safe_name(name) else None


def version_of(meta):
    """The unit version: SKILL.md metadata.version, else version."""
    md = meta.get("metadata")
    if isinstance(md, dict) and "version" in md: return md["version"]
    return meta.get("version", "")


def ordered(items, kind, name, version, path):
    """items sorted by kind, name, then version descending (a version that is not SemVer last), then path."""
    def by_version(x):
        v = version(x)
        return (True, semver_key(v)) if parse_semver(v) else (False, ())
    out = sorted(items, key=lambda x: str(path(x)))
    out.sort(key=by_version, reverse=True)
    out.sort(key=lambda x: (str(kind(x)), str(name(x))))
    return out


def posix_rel(path, start):
    """path relative to start with forward slashes, both resolved first."""
    return os.path.relpath(os.path.realpath(path), os.path.realpath(start)).replace(os.sep, "/")


def bump_problem(rec, old):
    """Why rec may not replace old (the registration.json already in the workspace), or None. Only a readable format-2
    record with a SemVer version sets a bound: rec's version must be higher, or equal with the same skill tree."""
    if not (isinstance(old, dict) and old.get("registration_format") == REGISTRATION_FORMAT
            and parse_semver(old.get("version"))):
        return None
    new, was = semver_key(rec["version"]), semver_key(old["version"])
    if new < was:
        return "version %s is lower than the registered version %s" % (rec["version"], old["version"])
    if new == was and old.get("skill_sha256") != rec["skill_sha256"]:
        return ("version %s is already registered for another skill tree; bump the version in %s" % (
            rec["version"], "AGENT.md version" if rec.get("kind") == "agent" else "SKILL.md metadata.version"))
    return None


def early_state(kind, ws):
    """The verify-now steps that read no hash and no signature (B5 D4 steps 1 to 4, up to the format): (state, reason,
    record) when one decides, else (None, None, record) for a format-2 record that classify must verify. ws is None when
    the unit name is not a safe path component."""
    if kind not in ("skill", "agent"): return "unregistered", NOT_A_UNIT, None
    if ws is None: return "unregistered", NO_AGENT_WORKSPACE if kind == "agent" else NO_WORKSPACE, None
    fp = os.path.join(ws, "registration.json")
    if not os.path.isfile(fp): return "unregistered", "no registration.json", None
    try:
        with open(fp, encoding="utf-8") as f: rec = json.load(f)
    except (OSError, ValueError, RecursionError):
        return "invalid", "registration.json is not readable JSON", None
    if not isinstance(rec, dict): return "invalid", "registration.json is not a JSON object", None
    path = rec.get("skill_path")
    if isinstance(path, str) and (os.path.isabs(path) or path.startswith("~")): return "stale", "legacy-absolute", rec
    if rec.get("registration_format") != REGISTRATION_FORMAT: return "invalid", "not registration format 2", rec
    return None, None, rec


def classify(kind, ws, unit_dir, schema_errors, signature, current, approval):
    """(state, reason, record) of the registration.json in ws for unit_dir, first match wins (B5 D4). schema_errors(rec):
    the registration schema's errors; signature(rec): the approvals.Verdict of the register signature; current(rec):
    (field, value) pairs recomputed from the files; approval(gate, copy): the approvals.Verdict of a relied-on record."""
    from engine import approvals
    state, reason, rec = early_state(kind, ws)
    if state is not None: return state, reason, rec
    path = rec.get("skill_path")
    if schema_errors(rec): return "invalid", "does not match " + SCHEMA_NAME, rec
    if os.path.realpath(os.path.join(os.path.realpath(ws), path)) != os.path.realpath(unit_dir):
        return "stale", "moved", rec
    if "signed" not in rec: return "unsigned", "no register signature", rec
    v = signature(rec)
    if v.status == "stale" and v.reason == approvals.RULES_CHANGED: return "stale", "rules changed", rec
    if v.status == "stale": return "invalid", "the record does not match its signature", rec
    if v.status != "approved": return "invalid", "the register signature does not verify", rec
    for field, value in current(rec):
        if rec[field] != value: return "stale", "%s changed" % field, rec
    for gate in sorted(rec["approvals"]):
        if approval(gate, rec["approvals"][gate]).status != "approved":
            return "stale", "approval %s no longer verifies" % gate, rec
    return "registered", "", rec


def label(state, reason, rec):
    """The registration column of INDEX.md."""
    if state == "registered": return "registered %s" % rec["version"]
    return "%s (%s)" % (state, reason) if state in ("stale", "invalid") else state


def entry(root, row):
    """The registry entry of a registered build_index row; paths relative to root. registered_by and each principal
    are approvals.decided_by of the record (recorded_by, or the principal of a record signed before unit N1)."""
    from engine import approvals
    rec = row["record"]
    out = {"kind": rec["kind"], "name": rec["skill_name"], "version": rec["version"],
           "path": row["path"].replace(os.sep, "/"),
           # an agent workspace sits next to the realpath agent folder, so it is compared with the realpath root
           "workspace": posix_rel(row["workspace"], root) if rec["kind"] == "agent" else
                        os.path.relpath(row["workspace"], root).replace(os.sep, "/"),
           "iteration": rec["iteration"], "registered_at": rec["registered_at"],
           "registered_by": approvals.decided_by(rec["signed"]),
           "approvals": [dict({"gate": g, "principal": approvals.decided_by(a)},
                              **({"reason": a["reason"]} if "reason" in a else {}))
                         for g, a in sorted(rec["approvals"].items())]}
    out.update((k, rec[k]) for k in RECORD_HASHES)
    return out


def document(root, rows):
    """The registry document of build_index rows: an entry for each registered row only."""
    units = [entry(root, r) for r in rows if r["registration"] == "registered"]
    return {"registry_format": REGISTRY_FORMAT,
            "units": ordered(units, lambda e: e["kind"], lambda e: e["name"], lambda e: e["version"], lambda e: e["path"])}


def document_text(doc):
    """Canonical registry.json text: sorted keys, ASCII escapes, two-space indent, a trailing newline."""
    return json.dumps(doc, sort_keys=True, ensure_ascii=True, indent=2) + "\n"
