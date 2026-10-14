#!/usr/bin/env python3
"""Eva (에바): skill/agent evaluator. stdlib only, Python 3.9+."""
import argparse, json, os, posixpath, re, shlex, sys

PASS_THRESHOLD = 0.8
CATEGORY_FLOOR = 0.5  # every category with applicable checks needs >=1 pass and pass rate >= this
SKILL_MAX_BYTES = 20000
SKILL_MAX_LINES = 500
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# ---------- frontmatter (minimal YAML subset) ----------
def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    return v

def parse_frontmatter_ex(text):
    """Returns (meta dict or None, body, error). Supports scalars, inline lists, '- item' lists,
    one level of 'key: value' nesting, '>'/'|' blocks. Opening and closing lines must be exactly '---';
    every top-level line must be 'key: value' (a mapping) with unique keys."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None, text, "first line must be exactly '---'"
    end = None
    for i in range(1, len(lines)):
        if lines[i] == "---":
            end = i; break
    if end is None:
        return None, text, "closing '---' line not found"
    meta, key, block = {}, None, None
    def err(n, msg): return None, text, "frontmatter line %d: %s" % (n, msg)
    for n, ln in enumerate(lines[1:end], 2):
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[0] in " \t":
            if key is None:
                return err(n, "indented line without a parent key")
            s = ln.strip()
            if block is not None:
                meta[key] = (meta[key] + " " + s).strip()
            elif meta[key] is not None and not isinstance(meta[key], (list, dict)):
                return err(n, "key '%s' already has a scalar value; nested lines not allowed" % key)
            elif s.startswith("- "):
                if isinstance(meta[key], dict):
                    return err(n, "key '%s' mixes '- item' list and 'key: value' mapping" % key)
                if meta[key] is None: meta[key] = []
                meta[key].append(_scalar(s[2:]))
            elif re.match(r"^[A-Za-z_][\w-]*:\s*\S", s):
                if isinstance(meta[key], list):
                    return err(n, "key '%s' mixes '- item' list and 'key: value' mapping" % key)
                if meta[key] is None: meta[key] = {}
                k2, v2 = s.split(":", 1)
                if k2.strip() in meta[key]:
                    return err(n, "duplicate key '%s.%s'" % (key, k2.strip()))
                meta[key][k2.strip()] = _scalar(v2)
            else:
                return err(n, "expected '- item' or 'key: value' under '%s'" % key)
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$", ln)
        if not m:
            return err(n, "expected 'key: value' (frontmatter must be a mapping)")
        key, val = m.group(1), (m.group(2) or "").strip()
        if key in meta:
            return err(n, "duplicate key '%s'" % key)
        block = None
        if val in (">", "|", ">-", "|-"):
            meta[key] = ""; block = val
        elif val == "":
            meta[key] = None
        else:
            meta[key] = _scalar(val)
    return meta, "\n".join(lines[end + 1:]), None

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

def has_section(body, *words):
    """True if a markdown heading (outside fenced code blocks) contains any of words."""
    fence = None
    for ln in body.splitlines():
        m = FENCE_RE.match(ln)
        if m:
            mark, rest = m.group(1), m.group(2)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence) and not rest.strip():
                fence = None
            continue
        if fence is None and ln.startswith("#") and any(w.lower() in ln.lower() for w in words):
            return True
    return False

class Report:
    """checks: passed True/False, or None for N/A (excluded from score and category floor)."""
    def __init__(self, path, kind):
        self.path, self.kind, self.checks = path, kind, []
    def add(self, cid, category, ok, reason, fix="", must=False):
        self.checks.append({"id": cid, "category": category, "passed": bool(ok),
                            "must": must or cid in CRITICAL, "reason": reason, "fix": "" if ok else fix})
    def na(self, cid, category, reason, must=False):
        self.checks.append({"id": cid, "category": category, "passed": None,
                            "must": must or cid in CRITICAL, "reason": "N/A: " + reason, "fix": ""})
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
        ok = self.applicable and self.score >= PASS_THRESHOLD and not self.critical_failed and not self.category_failed
        return "PASS" if ok else "FAIL"
    def to_dict(self):
        return {"path": self.path, "kind": self.kind, "score": self.score,
                "threshold": PASS_THRESHOLD, "category_floor": CATEGORY_FLOOR,
                "categories": self.categories, "category_failed": self.category_failed,
                "verdict": self.verdict, "checks": self.checks}

CRITICAL = {"frontmatter", "name-format", "name-matches-folder", "description-present",
            "goal-present", "tools-declared"}

def detect_kind(path):
    if os.path.isfile(os.path.join(path, "SKILL.md")): return "skill"
    if os.path.isfile(os.path.join(path, "AGENT.md")): return "agent"
    return None

def _common_name(r, meta, path):
    name = str(meta.get("name", ""))
    r.add("name-format", "single-task", bool(NAME_RE.match(name)) and len(name) <= 64,
          "name '%s' kebab-case, <=64" % name, "name을 소문자 kebab-case(예: pdf-summarizer)로, 64자 이하로 바꾸세요.")
    folder = os.path.basename(os.path.normpath(path))
    r.add("name-matches-folder", "structure", name == folder,
          "name '%s' vs folder '%s'" % (name, folder), "frontmatter name을 폴더 이름 '%s'과 같게 맞추세요." % folder)
    desc = str(meta.get("description", "")).strip()
    r.add("description-present", "single-task", bool(desc), "description present",
          "무엇을 하는지와 언제 쓰는지를 담은 description을 추가하세요.")
    return name, desc

# ---------- skills ----------
def check_skill(path):
    r = Report(path, "skill")
    fp = os.path.join(path, "SKILL.md")
    raw, rerr = read_text(fp)
    meta, body, ferr = parse_frontmatter_ex(raw) if raw is not None else (None, "", rerr)
    raw = raw or ""
    r.add("frontmatter", "structure", meta is not None, "YAML frontmatter between --- lines" + (": %s" % ferr if ferr else ""),
          "SKILL.md 첫 줄을 정확히 '---'로 시작하고, 'key: value' 매핑(name, description)으로 된 frontmatter를 '---'로 닫으세요.")
    meta = meta or {}
    name, desc = _common_name(r, meta, path)
    r.add("description-length", "single-task", 20 <= len(desc) <= 1024,
          "description length %d (20..1024)" % len(desc), "description을 20~1024자로 조정하세요.")
    r.add("description-when", "single-task",
          bool(re.search(r"\b(use when|when|use for|use this)\b|할 때|때 사용|경우", desc, re.I)),
          "description says WHEN to use", "description에 'Use when ...'(언제 쓰는지) 문장을 추가하세요.")
    multi = len(re.findall(r"\b(and also|as well as|plus)\b|그리고 또|뿐만 아니라", desc, re.I))
    r.add("single-purpose", "single-task", multi == 0 and desc.count(";") < 2,
          "description focuses on one purpose", "여러 일을 나열하지 말고 스킬 하나에 작업 하나만 남기세요(나머지는 별도 스킬로 분리).")
    r.add("inputs-declared", "io", bool(as_list(meta.get("inputs"))) or has_section(body, "input", "입력"),
          "inputs declared (frontmatter or ## Inputs)", "frontmatter에 inputs: 목록 또는 '## Inputs' 섹션을 추가하세요.")
    r.add("outputs-declared", "io", bool(as_list(meta.get("outputs"))) or has_section(body, "output", "출력"),
          "outputs declared (frontmatter or ## Outputs)", "frontmatter에 outputs: 목록 또는 '## Outputs' 섹션을 추가하세요.")
    r.add("example-present", "io", has_section(body, "example", "예시"),
          "has ## Example section", "입력→출력 예시를 담은 '## Example' 섹션을 추가하세요.")
    nbytes, nlines = len(raw.encode("utf-8")), raw.count("\n") + 1
    r.add("size-limit", "structure", nbytes <= SKILL_MAX_BYTES and nlines <= SKILL_MAX_LINES,
          "SKILL.md %d bytes / %d lines (<=%d / %d)" % (nbytes, nlines, SKILL_MAX_BYTES, SKILL_MAX_LINES),
          "긴 내용은 references/로 옮기고 SKILL.md에서 링크하세요.")
    refs = set(re.findall(r"\]\(((?:scripts|references|assets)/[^)\s#]+)\)", body))
    refs |= set(re.findall(r"`((?:scripts|references|assets)/[^`\s]+)`", body))
    missing = sorted(x for x in refs if not os.path.exists(os.path.join(path, x)))
    r.add("refs-exist", "structure", not missing,
          "referenced files exist" + (": missing %s" % missing if missing else ""),
          "없는 파일을 만들거나 참조를 지우세요: %s" % ", ".join(missing))
    md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    ver = md.get("version", meta.get("version"))
    if ver is not None:
        r.add("version-format", "structure", bool(re.match(r"^\d+\.\d+\.\d+$", str(ver))),
              "version '%s' semver (metadata.version)" % ver, "metadata.version에 1.0.0 같은 SemVer를 쓰세요.", must=True)
    else:
        r.na("version-format", "structure", "no metadata.version (optional)", must=True)
    deep = sorted(x for x in refs if x.count("/") > 1)
    r.add("refs-one-level", "structure", not deep, "refs one level deep" + (": %s" % deep if deep else ""),
          "참조는 scripts/x 처럼 한 단계 깊이로만 두세요.")
    ev = os.path.join(path, "evals", "evals.json")
    if os.path.exists(ev):
        try:
            data = json.load(open(ev, encoding="utf-8")); items = data.get("evals", data) if isinstance(data, dict) else data
            ok = isinstance(items, list) and items and all(isinstance(e, dict) and e.get("prompt") and e.get("expected_output") for e in items)
        except ValueError:
            ok = False
        r.add("evals-valid", "io", ok, "evals/evals.json has prompt+expected_output items",
              "evals.json 각 항목에 prompt와 expected_output을 넣으세요.", must=True)
    else:
        r.na("evals-valid", "io", "no evals/evals.json (optional)", must=True)
    return r

# ---------- agents ----------
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
    """Returns [(filename, data or None, errors)]; data is None when the JSON does not parse."""
    d = os.path.join(path, "scenarios")
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        data = json.load(fh)
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
    goal = str(meta.get("goal", "")).strip()
    r.add("goal-present", "goal", len(goal) >= 15, "goal stated (>=15 chars)", "에이전트가 끝내야 할 목표를 goal에 한 문장으로 쓰세요.")
    crit = [str(c) for c in as_list(meta.get("success_criteria"))]
    # same checkers as transcript grading: measurable iff an outcome checker recognizes the criterion
    unmeasurable = [c for c in crit if outcome_evidence(c, "") is None]
    r.add("success-measurable", "goal", len(crit) >= 1 and not unmeasurable,
          "%d/%d success criteria machine-checkable%s" % (len(crit) - len(unmeasurable), len(crit),
          (": not checkable %s" % unmeasurable) if unmeasurable else ""),
          "success_criteria마다 자동 확인 가능한 조건을 쓰세요(예: '출처 링크 3개 이상', '800자 이하', '비교표', "
          "'파일:줄 근거 2개 이상', '파일 변경 0건').")
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

# ---------- transcript grading ----------
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
    viol = bypass_violations(run["calls"], run["workspace"])
    r.add("no-bypass", "tool-use", not viol,
          "%d tool calls inspected%s" % (len(run["calls"]), (": bypass %s" % viol) if viol else ", no bypass"),
          "워크스페이스 밖 탐색(.., /, ~, 워크스페이스 밖 절대경로)이나 git 이력 뒤지기(git log --all, reflog, fsck 등)를 멈추고 "
          "주어진 워크스페이스 안의 근거로 정석대로 푸세요.", must=True)
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
        base = "%s-%s" % (origin, ev[0] if ev else "unchecked")
        seen[base] = seen.get(base, 0) + 1
        cid = base if seen[base] == 1 else "%s-%d" % (base, seen[base])
        if ev is None:
            r.na(cid, "goal", "not machine-checkable: '%s'" % crit)
            continue
        checked += 1
        _, ok, got, fix = ev
        r.add(cid, "goal", ok, "'%s' -> %s" % (crit, got), fix, must=True)
    r.add("outcome-evidence", "goal", checked > 0, "%d checkable outcome criteria" % checked,
          "expected_outcome/success_criteria에 확인 가능한 조건(예: '출처 3개 이상', '800자 이하', '비교표')을 넣으세요.", must=True)
    return r

# ---------- no-bypass (우회 금지) ----------
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

def bypass_violations(calls, workspace=None):
    """List of '<tool>: <offending input>' for calls that search outside the workspace or mine git history."""
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
FILELINE_RE = re.compile(r"(?<![\w/.:-])((?:[\w.-]+/)*[\w-][\w.-]*\.[A-Za-z0-9]+):(\d+)(?:-\d+)?(?![\d])")
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
        seen_text = "\n".join(list(_strings([c["input"] for c in calls])) + results)
        cites = sorted(set(FILELINE_RE.findall(final)))
        opened = lambda f: re.search(r"(?<![\w.-])" + re.escape(f) + r"(?![\w-]|\.\w)", seen_text) is not None
        ok_cites = [c for c in cites if opened(c[0])]
        bad = ["%s:%s" % c for c in cites if not opened(c[0])]
        return ("file-lines", len(ok_cites) >= n,
                "%d file:line citations to files opened via tools (need >= %d)%s" % (len(ok_cites), n,
                ("; files never opened: %s" % bad) if bad else ""),
                "도구로 실제 읽은 파일의 '경로:줄'(예: src/app.py:42) 근거를 %d개 이상 다세요 (현재 %d개)." % (n, len(ok_cites)))
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
    if not fails:
        lines.append("모든 검사를 통과했습니다. 수정할 것이 없습니다.")
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
        mark = "-" if c["passed"] is None else ("✓" if c["passed"] else "✗")
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
        return 0 if r.verdict == "PASS" else 1
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
            print("total=%d pass=%d fail=%d" % (len(reps), sum(r.verdict == "PASS" for r in reps), sum(r.verdict == "FAIL" for r in reps)))
        return 1 if any(r.verdict == "FAIL" for r in reps) else 0
    if a.cmd == "feedback":
        r = check(a.path)
        open(os.path.join(a.path, "FEEDBACK.md"), "w", encoding="utf-8").write(feedback_md(r, a.round))
        json.dump(r.to_dict(), open(os.path.join(a.path, "FEEDBACK.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("%s -> FEEDBACK.md, FEEDBACK.json (%s %.2f)" % (a.path, r.verdict, r.score))
        return 0 if r.verdict == "PASS" else 1
    if a.cmd == "index":
        reg, md = build_index(a.path)
        open(os.path.join(a.path, "INDEX.md"), "w", encoding="utf-8").write(md)
        json.dump(reg, open(os.path.join(a.path, "registry.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("indexed %d units -> INDEX.md, registry.json" % len(reg))
        return 0

if __name__ == "__main__":
    sys.exit(main())
