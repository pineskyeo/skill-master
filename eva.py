#!/usr/bin/env python3
"""Eva (에바): skill/agent evaluator. stdlib only, Python 3.9+."""
import argparse, json, os, re, sys

PASS_THRESHOLD = 0.8
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

def parse_frontmatter(text):
    """Returns (meta dict, body). Supports scalars, inline lists, '- item' lists, '>'/'|' blocks."""
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i; break
    if end is None:
        return None, text
    meta, key, block = {}, None, None
    for ln in lines[1:end]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[0] in " \t" and key:
            s = ln.strip()
            if block is not None:
                meta[key] = (meta[key] + " " + s).strip()
            elif s.startswith("- "):
                if not isinstance(meta.get(key), list): meta[key] = []
                meta[key].append(_scalar(s[2:]))
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", ln)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        block = None
        if val in (">", "|", ">-", "|-"):
            meta[key] = ""; block = val
        elif val == "":
            meta[key] = []
        else:
            meta[key] = _scalar(val)
    return meta, "\n".join(lines[end + 1:])

def as_list(v):
    if v is None or v == "": return []
    return v if isinstance(v, list) else [v]

def has_section(body, *words):
    for ln in body.splitlines():
        if ln.startswith("#") and any(w.lower() in ln.lower() for w in words):
            return True
    return False

class Report:
    def __init__(self, path, kind):
        self.path, self.kind, self.checks = path, kind, []
    def add(self, cid, category, ok, reason, fix=""):
        self.checks.append({"id": cid, "category": category, "passed": bool(ok),
                            "reason": reason, "fix": "" if ok else fix})
    @property
    def score(self):
        return round(sum(c["passed"] for c in self.checks) / len(self.checks), 3) if self.checks else 0.0
    @property
    def critical_failed(self):
        return [c for c in self.checks if not c["passed"] and c["id"] in CRITICAL]
    @property
    def verdict(self):
        return "PASS" if self.score >= PASS_THRESHOLD and not self.critical_failed else "FAIL"
    def to_dict(self):
        return {"path": self.path, "kind": self.kind, "score": self.score,
                "threshold": PASS_THRESHOLD, "verdict": self.verdict, "checks": self.checks}

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
    raw = open(fp, encoding="utf-8").read()
    meta, body = parse_frontmatter(raw)
    r.add("frontmatter", "structure", meta is not None, "YAML frontmatter between --- lines",
          "SKILL.md 맨 위에 ---로 감싼 frontmatter(name, description)를 넣으세요.")
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
    if meta.get("version") is not None:
        r.add("version-format", "structure", bool(re.match(r"^\d+\.\d+\.\d+$", str(meta["version"]))),
              "version semver", "version을 1.0.0 같은 semver로 쓰세요.")
    return r

# ---------- agents ----------
def load_scenarios(path):
    d = os.path.join(path, "scenarios")
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                try:
                    out.append((f, json.load(open(os.path.join(d, f), encoding="utf-8"))))
                except ValueError as e:
                    out.append((f, {"_error": str(e)}))
    return out

def check_agent(path):
    r = Report(path, "agent")
    raw = open(os.path.join(path, "AGENT.md"), encoding="utf-8").read()
    meta, body = parse_frontmatter(raw)
    r.add("frontmatter", "structure", meta is not None, "YAML frontmatter",
          "AGENT.md 맨 위에 frontmatter(name, description, goal, tools, success_criteria)를 넣으세요.")
    meta = meta or {}
    _common_name(r, meta, path)
    goal = str(meta.get("goal", "")).strip()
    r.add("goal-present", "goal", len(goal) >= 15, "goal stated (>=15 chars)", "에이전트가 끝내야 할 목표를 goal에 한 문장으로 쓰세요.")
    crit = as_list(meta.get("success_criteria"))
    measurable = [c for c in crit if re.search(r"\d|must|모든|반드시|within|이내|이상|이하|==|<|>", str(c), re.I)]
    r.add("success-measurable", "goal", len(crit) >= 1 and len(measurable) == len(crit),
          "%d/%d success criteria measurable" % (len(measurable), len(crit)),
          "success_criteria마다 숫자나 must 같은 확인 가능한 조건을 넣으세요(예: '3개 이상 출처 인용').")
    scen = load_scenarios(path)
    bad = [f for f, s in scen if "_error" in s]
    r.add("scenarios-present", "goal", len(scen) >= 2 and not bad,
          "%d scenarios in scenarios/*.json%s" % (len(scen), (", invalid: %s" % bad) if bad else ""),
          "scenarios/ 폴더에 유효한 JSON 시나리오를 2개 이상 넣으세요.")
    noexp = [f for f, s in scen if "_error" not in s and not s.get("expected_outcome")]
    r.add("scenarios-expected", "goal", scen and not noexp,
          "all scenarios have expected_outcome" + (": missing in %s" % noexp if noexp else ""),
          "각 시나리오에 expected_outcome을 넣으세요.")
    tools = [str(t) for t in as_list(meta.get("tools"))]
    r.add("tools-declared", "tool-use", bool(tools), "tools declared: %s" % tools, "frontmatter tools: 에 쓸 도구 목록을 선언하세요.")
    undeclared = sorted({t for f, s in scen for t in s.get("expected_tools", []) if t not in tools})
    r.add("tools-subset", "tool-use", not undeclared,
          "scenario expected_tools within declared" + (": undeclared %s" % undeclared if undeclared else ""),
          "tools에 %s를 선언하거나 시나리오에서 빼세요." % undeclared)
    r.add("tools-documented", "tool-use", has_section(body, "tool", "도구"),
          "## Tools section explains usage", "도구별로 언제 쓰는지 설명하는 '## Tools' 섹션을 추가하세요.")
    r.add("memory-section", "context", has_section(body, "memory", "handoff", "context", "기억", "인수인계"),
          "memory/handoff section", "'## Memory & Handoff' 섹션에 무엇을 기억하고 어떻게 넘기는지 쓰세요.")
    multi = [f for f, s in scen if len(s.get("turns", [])) >= 2]
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
def grade_transcript(agent_path, transcript_path):
    """transcript JSON: {"scenario": id, "tool_calls": [...], "final": "..."}"""
    t = json.load(open(transcript_path, encoding="utf-8"))
    meta, _ = parse_frontmatter(open(os.path.join(agent_path, "AGENT.md"), encoding="utf-8").read())
    tools = [str(x) for x in as_list((meta or {}).get("tools"))]
    scen = dict((s.get("id", f), s) for f, s in load_scenarios(agent_path))
    s = scen.get(t.get("scenario"))
    r = Report(transcript_path, "transcript")
    r.add("scenario-known", "goal", s is not None, "scenario '%s' exists" % t.get("scenario"), "transcript의 scenario를 시나리오 id와 맞추세요.")
    if s is None: return r
    calls = t.get("tool_calls", [])
    illegal = sorted(set(calls) - set(tools))
    r.add("only-declared-tools", "tool-use", not illegal, "illegal tools: %s" % illegal, "선언되지 않은 도구 %s 사용을 멈추세요." % illegal)
    miss = [x for x in s.get("expected_tools", []) if x not in calls]
    r.add("expected-tools-called", "tool-use", not miss, "missing calls: %s" % miss, "필요한 도구 %s를 호출하세요." % miss)
    final = t.get("final", "").lower()
    kws = [k.lower() for k in s.get("must_include", [])]
    lack = [k for k in kws if k not in final]
    r.add("outcome-keywords", "goal", not lack, "final answer missing: %s" % lack, "최종 답에 %s를 포함하세요." % lack)
    return r

# ---------- feedback / index ----------
def feedback_md(r, rnd=None):
    d = r.to_dict()
    lines = ["# Eva Feedback: %s" % os.path.basename(os.path.normpath(r.path)), "",
             "- kind: %s / score: %.0f%% / verdict: **%s**%s" % (d["kind"], d["score"] * 100, d["verdict"],
             (" / round %s" % rnd) if rnd else ""), ""]
    fails = [c for c in d["checks"] if not c["passed"]]
    if not fails:
        lines.append("모든 검사를 통과했습니다. 수정할 것이 없습니다.")
    else:
        lines += ["## 고쳐야 할 것 (중요도 순)", ""]
        fails.sort(key=lambda c: c["id"] not in CRITICAL)
        for i, c in enumerate(fails, 1):
            tag = " [필수]" if c["id"] in CRITICAL else ""
            lines += ["%d. `%s`%s (%s)" % (i, c["id"], tag, c["category"]),
                      "   - 이유: %s" % c["reason"], "   - 수정: %s" % c["fix"]]
    lines += ["", "다 고친 뒤 `python3 eva.py check %s`로 다시 제출하세요." % r.path]
    return "\n".join(lines) + "\n"

def build_index(root):
    reg = []
    for u in find_units(root):
        kind = detect_kind(u)
        meta, _ = parse_frontmatter(open(os.path.join(u, "SKILL.md" if kind == "skill" else "AGENT.md"), encoding="utf-8").read())
        meta = meta or {}
        rep = check(u)
        reg.append({"kind": kind, "name": meta.get("name", ""), "version": meta.get("version", ""),
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
        print("  %s %-22s %-11s %s" % ("✓" if c["passed"] else "✗", c["id"], c["category"], c["reason"]))

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
        reps = [check(u) for u in find_units(a.path)]
        if a.json:
            print(json.dumps([r.to_dict() for r in reps], ensure_ascii=False, indent=2))
        else:
            for r in reps: print("[%s] %.2f %s" % (r.verdict, r.score, r.path))
            print("total=%d pass=%d fail=%d" % (len(reps), sum(r.verdict == "PASS" for r in reps), sum(r.verdict == "FAIL" for r in reps)))
        return 0
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
