"""Agent evaluation per tool (unit B3): the scenario x tool cell table, the launch and continuation tables, the run
records of an iteration, grading records, the benchmark and the report text. skill_eval.py owns the CLI, the engines,
the live gate and the spend path, and passes in what it needs from eva (engine modules never import eva or skill_eval).

Iteration layout: <workspace>/iteration-N/<tool>/<scenario id>/<configuration>/run-k/ holding stream.jsonl (the raw
stream), transcript.json (the normalized transcript, only for a run whose agent load passed), run.json and, after
`agent grade`, grading.json. configuration is new_agent, or old_agent for the previous version (--old-agent)."""
import hashlib, json, os, re
from engine import generate, registry
from engine.primitives import transcript as transcript_primitive

TOOLS = ("claude", "codex", "opencode", "hermes")
CONFIGS = ("new_agent", "old_agent")
MULTI_TURN_VERIFIED = {t: False for t in TOOLS}
WRAPPERS = {"claude": "wrappers/claude/{name}.md", "codex": "wrappers/codex/{name}.toml",
            "opencode": "wrappers/opencode/{name}.md", "hermes": "wrappers/hermes/SOUL.md"}
INSTALL = {"claude": ".claude/agents/{name}.md", "codex": ".codex/agents/{name}.toml"}
LAUNCH = {
    "claude": {"argv": ("claude", "-p", "{prompt}", "--agent", "{name}", "--output-format", "stream-json", "--verbose",
                        "--no-session-persistence"),
               "prompt": "{prompt}", "verified": True,
               "source": "claude -p --agent <name> (inputs/design.md:52); project agents in .claude/agents/ "
                         "(inputs/design.md:46)"},
    "codex": {"argv": ("codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--sandbox",
                       "workspace-write"),
              "prompt": "Use the {name} agent for this request: {prompt}", "verified": False,
              "source": "house choice: codex exec has no --agent flag and a top-level start as a custom "
                        "agent is not recorded (inputs/design.md:67, :118); a project agent in .codex/agents/ is "
                        "invoked by asking (inputs/design.md:62, :65)"},
}
PLANNED, RUN = "planned", "run"
NOT_AVAILABLE = "not-run: engine not available"
NOT_SELECTED = "not-run: engine not selected"
NOT_TARGET = "not-run: not in targets"
WRAPPER_MISSING = "not-run: wrapper missing (eva.py generate)"
WRAPPER_STALE = "not-run: wrapper out of date (eva.py generate)"
MULTI_TURN = "not-run: multi-turn unverified"
AGENT_NOT_LOADED = "error: agent not loaded"
RESERVED_NAME = "error: reserved agent name"
DUPLICATE_ID = "error: duplicate scenario id"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# The built-in agents a recorded Claude system/init event lists (program runner capture runs/p1/clj21.jsonl:5,
# compared case-insensitively); a plugin agent's name holds a colon. An agent under test with such a name is always in
# init `agents`, so the D8 evidence could not tell that the wrapper under test was loaded.
RESERVED_CLAUDE_AGENTS = ("claude", "Explore", "general-purpose", "Plan", "statusline-setup")


def error_state(reason):
    return "error: " + " ".join(str(reason).split())[:300]


def reserved_agent_name(name):
    return ":" in name or name.lower() in {n.lower() for n in RESERVED_CLAUDE_AGENTS}


def duplicate_ids(scens):
    ids = [s["id"] for s in scens]
    return sorted({i for i in ids if ids.count(i) > 1}, key=str)


def scenarios(loaded):
    """[(file, data, errors)] of eva.load_scenarios -> the valid scenarios as {"file", "id", "turns", "users"}."""
    out = []
    for f, s, errs in loaded:
        if errs or not isinstance(s, dict):
            continue
        users = [t["user"] for t in s["turns"]]
        out.append({"file": f, "id": s["id"], "turns": len(users), "users": users, "data": s})
    return out


def wrapper_states(agent_dir):
    """{tool: None (up to date) | "missing" | "stale" | "refused: <why>"} from engine/generate.py outcome()."""
    try:
        out = generate.outcome(agent_dir)
    except Exception as e:
        return {t: "refused: %s: %s" % (type(e).__name__, e) for t in TOOLS}
    if out.refusal:
        return {t: "refused: " + out.refusal.splitlines()[0] for t in TOOLS}
    states = {t: None for t in TOOLS}
    for rel, problem, _ in out.found:
        parts = rel.split("/")
        if len(parts) >= 2 and parts[0] == "wrappers" and parts[1] in states:
            states[parts[1]] = "missing" if problem == "missing" and states[parts[1]] is None else "stale"
    return states


def wrapper_label(state, rel):
    if state is None:
        return "up to date (%s)" % rel
    if state == "missing":
        return "missing (eva.py generate)"
    if state == "stale":
        return "out of date (eva.py generate)"
    return state.replace("refused: ", "refused (", 1) + ")"


def cell_table(scens, targets, available, selected, wrappers, tool_errors=None):
    """One cell per scenario and tool, in TOOLS order, each with exactly one state. tool_errors: {tool: error state}
    of agent-level checks that keep every cell of a selected tool from running."""
    out = []
    dup = set(duplicate_ids(scens))
    tool_errors = tool_errors or {}
    for s in scens:
        for t in TOOLS:
            w = wrappers.get(t)
            if s["id"] in dup:
                state = DUPLICATE_ID
            elif s["turns"] > 1 and not MULTI_TURN_VERIFIED[t]:
                state = MULTI_TURN
            elif t not in available:
                state = NOT_AVAILABLE
            elif t not in targets:
                state = NOT_TARGET
            elif t not in selected:
                state = NOT_SELECTED
            elif t in tool_errors:
                state = tool_errors[t]
            elif w is not None:
                state = WRAPPER_STALE if w == "stale" else WRAPPER_MISSING
            elif not SAFE_ID_RE.match(str(s["id"])):
                state = error_state("scenario id %r is not a safe path component" % (s["id"],))
            else:
                state = PLANNED
            out.append({"scenario": s["id"], "file": s["file"], "tool": t, "state": state})
    return out


def launch_argv(tool, name, prompt, extra=()):
    spec = LAUNCH[tool]
    text = spec["prompt"].format(name=name, prompt=prompt)
    argv = [text if a == "{prompt}" else a.replace("{name}", name) for a in spec["argv"]] + list(extra)
    if "{prompt}" not in spec["argv"]:
        argv.append(text)
    return argv


def prompt_of(scenario):
    return scenario["users"][0]


def install_rel(tool, name):
    return INSTALL[tool].format(name=name)


def wrapper_rel(tool, name):
    return WRAPPERS[tool].format(name=name)


def parse_pin(text):
    """(name, version) of an AGENT.md skills entry "<name>@<semver>", None for anything else."""
    if not isinstance(text, str) or text.count("@") != 1:
        return None
    name, version = text.split("@")
    if not registry.safe_name(name) or registry.parse_semver(version) is None:
        return None
    return name, version


def pins_of(meta):
    """The AGENT.md skills entries of a valid frontmatter, in order ([] without the key)."""
    pins = (meta or {}).get("skills")
    return list(pins) if isinstance(pins, list) else []


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    try:
        with open(path, "rb") as f:
            return sha256_bytes(f.read())
    except OSError:
        return None


def rjson(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def run_dir(it_dir, tool, sid, config, k):
    return os.path.join(it_dir, tool, sid, config, "run-%d" % k)


def iter_runs(it_dir):
    """Every run.json of an iteration, sorted, each with its folder under "dir"."""
    out = []
    for tool in TOOLS:
        td = os.path.join(it_dir, tool)
        if not os.path.isdir(td):
            continue
        for sid in sorted(os.listdir(td)):
            for config in CONFIGS:
                cd = os.path.join(td, sid, config)
                if not os.path.isdir(cd):
                    continue
                for rd in sorted(os.listdir(cd), key=lambda x: (len(x), x)):
                    rec = rjson(os.path.join(cd, rd, "run.json"))
                    if re.match(r"run-\d+$", rd) and isinstance(rec, dict):
                        out.append(dict(rec, dir=os.path.join(cd, rd)))
    return out


# ---------- grading ----------
def llm_items(agent_dir, scenario):
    """[{"id", "text"}] of the llm criteria, with the row ids eva.grade_transcript gives them (origin-kind, then -2,
    -3 for repeats, counted over every criterion in its order)."""
    agent = transcript_primitive.read_agent(agent_dir)
    meta = agent.meta or {}
    sources = [("criteria", c) for c in meta.get("success_criteria") or []]
    sources += [("criteria", c) for c in scenario.get("success_criteria", [])]
    sources += [("expected", c) for c in scenario.get("expected_outcome", [])]
    seen, out = {}, []
    for origin, crit in sources:
        if not isinstance(crit, dict):
            continue
        base = "%s-%s" % (origin, transcript_primitive.KINDS.get(crit.get("type"), crit.get("type")))
        seen[base] = seen.get(base, 0) + 1
        cid = base if seen[base] == 1 else "%s-%d" % (base, seen[base])
        if crit.get("type") == "llm":
            out.append({"id": cid, "text": crit.get("text", "")})
    return out


def grader_case(scenario):
    s = scenario["data"]
    expected = "must include %s; expected tools %s; expected outcome %s" % (
        json.dumps(s.get("must_include", [])), json.dumps(s.get("expected_tools", [])),
        json.dumps(s.get("expected_outcome", [])))
    return {"prompt": "\n".join(scenario["users"]), "expected_output": expected}


def grading_record(rec, rows, transcript_sha, grader=None):
    """grading.json of one run: the rows, the counts, and the grader keys in every case (none/null/null when no
    grader ran for this file)."""
    applicable = [r for r in rows if r["passed"] is not None]
    passed = sum(1 for r in applicable if r["passed"])
    return {"scenario": rec["scenario"], "tool": rec["tool"], "configuration": rec["configuration"], "run": rec["run"],
            "transcript_sha256": transcript_sha,
            "grader_engine": grader.engine if grader else "none",
            "grader_model": (grader.model or "cli-default") if grader else None,
            "grader_effort": (grader.effort or "cli-default") if grader else None,
            "passed": passed, "applicable": len(applicable),
            "needs_review": sum(1 for r in rows if r["status"] == "needs-review"),
            "failed": [r["id"] for r in rows if r["passed"] is False],
            "pass_rate": round(passed / len(applicable), 4) if applicable else None, "rows": rows}


def apply_verdicts(rows, verdicts, label):
    """The rows with each llm needs-review row the grader answered set to pass or fail."""
    out = []
    for r in rows:
        v = verdicts.get(r["id"]) if r["status"] == "needs-review" else None
        if v is None or v[0] is None:
            out.append(r)
            continue
        ok, evidence = v
        out.append(dict(r, status="pass" if ok else "fail", passed=bool(ok),
                        reason="grader %s: %s: %s" % (label, "yes" if ok else "no", evidence[:300]),
                        fix="" if ok else r["fix"]))
    return out


def grade_runs(it_dir, agent_dir, scens, grade_fn, grader=None, log=print, signed=None):
    """grading.json for every run whose state is run and that holds a transcript. grade_fn(agent_dir, path) -> rows
    (eva.grade_transcript). grader: None (llm rows stay needs-review) or an object with grade(case, transcript, items)
    and engine, model, effort, label(). signed: the run folders the human signed for the grader; any other run gets
    its rows with no grader. Both configurations are graded against agent_dir, the version under test, so the
    previous version faces the same scenarios and criteria."""
    by_id = {s["id"]: s for s in scens}
    done = []
    for rec in iter_runs(it_dir):
        tp = os.path.join(rec["dir"], "transcript.json")
        if rec.get("state") != RUN or not os.path.isfile(tp):
            continue
        with open(tp, "rb") as f:
            sha = sha256_bytes(f.read())
        rows = grade_fn(agent_dir, tp)
        used = None
        items = [it for it in llm_items(agent_dir, by_id[rec["scenario"]]["data"])
                 if any(r["id"] == it["id"] and r["status"] == "needs-review" for r in rows)] \
            if grader is not None and rec["scenario"] in by_id and (signed is None or rec["dir"] in signed) else []
        if items:
            with open(tp, encoding="utf-8") as f:
                t = json.load(f)
            rows = apply_verdicts(rows, grader.grade(grader_case(by_id[rec["scenario"]]), t, items), grader.label())
            used = grader
        g = grading_record(rec, rows, sha, used)
        wjson(os.path.join(rec["dir"], "grading.json"), g)
        done.append(g)
        log("[%s] %s %s run-%d: pass_rate %s, %d needs-review%s" % (
            rec["tool"], rec["scenario"], rec["configuration"], rec["run"],
            "-" if g["pass_rate"] is None else "%.2f" % g["pass_rate"], g["needs_review"],
            ", grader %s" % used.label() if used else ""))
    return done


# ---------- benchmark, gate, report ----------
def collect(it_dir):
    """Run records with their grading. grading is None unless the run's state is run and grading.json grades the
    transcript.json that is on disk now (same sha256), so a stale or misplaced grading never counts as graded."""
    out = []
    for rec in iter_runs(it_dir):
        g = rjson(os.path.join(rec["dir"], "grading.json"))
        ok = isinstance(g, dict) and rec.get("state") == RUN and \
            g.get("transcript_sha256") == file_sha256(os.path.join(rec["dir"], "transcript.json"))
        out.append(dict(rec, grading=g if ok else None))
    return out


def _mean(xs):
    return round(sum(xs) / len(xs), 4) if xs else None


def benchmark(plan, runs, coverage):
    cells = {}
    for r in runs:
        key = (r["scenario"], r["tool"], r["configuration"])
        c = cells.setdefault(key, {"scenario": key[0], "tool": key[1], "configuration": key[2], "runs": 0,
                                   "graded_runs": 0, "pass_rates": []})
        c["runs"] += 1
        g = r["grading"]
        if g is not None and g.get("pass_rate") is not None:
            c["graded_runs"] += 1
            c["pass_rates"].append(g["pass_rate"])
    summary = [dict({k: v for k, v in c.items() if k != "pass_rates"}, pass_rate=_mean(c["pass_rates"]))
               for _, c in sorted(cells.items())]
    rate = {(c["scenario"], c["tool"], c["configuration"]): c["pass_rate"] for c in summary}
    baseline = []
    for sid, tool, config in sorted(rate):
        if config != "old_agent":
            continue
        new, old = rate.get((sid, tool, "new_agent")), rate[(sid, tool, config)]
        baseline.append({"scenario": sid, "tool": tool, "new_agent": new, "old_agent": old,
                         "delta": None if new is None or old is None else round(new - old, 4)})
    return {"metadata": {k: v for k, v in plan.items() if k != "cells"}, "cells": plan.get("cells", []),
            "summary": summary, "baseline": baseline, "tool_coverage": coverage,
            "runs": [dict({k: v for k, v in r.items() if k not in ("dir", "grading")},
                          graded=r["grading"] is not None,
                          pass_rate=r["grading"]["pass_rate"] if r["grading"] else None) for r in runs]}


def gate(plan, runs, coverage_missing):
    """Gate problems: the lines given by the caller (the G1 coverage-missing lines and the parity FAIL rows), then the
    run-level problems. Fail closed:
    an unverified Codex agent load, a pinned run whose skill load is not verified, an errored run, an ungraded run, an
    llm criterion without a grader verdict and a failed row each keep the gate from reading clean."""
    problems = list(coverage_missing)
    if plan.get("stopped"):
        problems.append("stopped: %s" % plan["stopped"])
    unverified = [r for r in runs if r["tool"] == "codex" and r.get("state") == RUN
                  and r.get("agent_loaded") != "verified"]
    if unverified:
        problems.append("agent-load-unverified:codex: %d Codex run(s) have no evidence that the agent was loaded (no "
                        "recorded top-level Codex agent start; UNVERIFIED until an operator capture verifies it)"
                        % len(unverified))
    pinned = {}
    for r in runs:
        iso = r.get("isolation") if isinstance(r.get("isolation"), dict) else {}
        pins = iso.get("pinned_skills") or (plan.get("pins") if r.get("configuration") == "new_agent" else None)
        if r.get("state") == RUN and pins and iso.get("skills_loaded") != "verified":
            pinned[r["tool"]] = pinned.get(r["tool"], 0) + 1
    for tool in TOOLS:
        if tool in pinned:
            problems.append("skills-load-unverified:%s: %d pinned run(s) without an init event that lists exactly the "
                            "pinned skills (run.json isolation.skills_loaded is not verified); the pinned skill load "
                            "is UNVERIFIED, so these runs do not test the pins" % (tool, pinned[tool]))
    for r in runs:
        where = "%s/%s/%s/run-%d" % (r["scenario"], r["tool"], r["configuration"], r["run"])
        g = r["grading"]
        if r.get("state") != RUN:
            problems.append("run-error:%s: %s" % (where, r.get("state")))
        elif g is None:
            problems.append("run-ungraded:%s: run `agent grade`" % where)
        else:
            if g.get("needs_review"):
                problems.append("criteria-ungraded:%s: %d llm criterion(s) without a grader verdict (agent grade "
                                "--live)" % (where, g["needs_review"]))
            if g.get("failed"):
                problems.append("criteria-failed:%s: %s" % (where, ", ".join(g["failed"])))
    return {"problems": problems, "needs_approval": bool(problems)}


def comparison_md(bench, gate_, n, coverage_rows):
    m = bench["metadata"]
    L = ["# %s agent, iteration %d" % (m.get("agent_name"), n), "",
         "- engines: %s, runs per cell: %s" % (", ".join("%s(%s)" % (k, v or "CLI default")
                                                          for k, v in (m.get("engines") or {}).items()),
                                                m.get("runs_per_cell")),
         "- baseline: %s" % ("previous version %s" % m["old_agent"] if m.get("old_agent") else
                             "none (pass --old-agent DIR to compare with the previous version)"),
         "", "## Gate", "", "**%s**" % ("NEEDS HUMAN APPROVAL" if gate_["needs_approval"] else "clean (no problems)")]
    L += ["- problem: " + p for p in gate_["problems"]]
    L += ["", "## Cells (scenario x tool)", "", "| scenario | tool | state |", "|---|---|---|"]
    L += ["| %s | %s | %s |" % (c["scenario"], c["tool"], c["state"]) for c in bench["cells"]]
    L += ["", "## Pass rate", "", "| scenario | tool | configuration | pass_rate | graded runs |", "|---|---|---|---|---|"]
    L += ["| %s | %s | %s | %s | %d/%d |" % (s["scenario"], s["tool"], s["configuration"],
                                            "-" if s["pass_rate"] is None else "%.2f" % s["pass_rate"],
                                            s["graded_runs"], s["runs"]) for s in bench["summary"]]
    if bench["baseline"]:
        L += ["", "## Baseline: previous version", "", "| scenario | tool | new_agent | old_agent | delta |",
              "|---|---|---|---|---|"]
        L += ["| %s | %s | %s | %s | %s |" % (b["scenario"], b["tool"], b["new_agent"], b["old_agent"], b["delta"])
              for b in bench["baseline"]]
    L += ["", "## Tool coverage", "", "| tool | dynamic eval | graded runs | registration |", "|---|---|---|---|"]
    L += ["| %s | %s | %d | %s |" % row for row in coverage_rows]
    return "\n".join(L) + "\n"
