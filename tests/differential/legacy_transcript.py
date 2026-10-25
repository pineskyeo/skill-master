"""The differential oracle of unit B1 (brief B1 D10): the A4 bodies of grade_transcript, outcome_evidence, _min_count,
_max_chars, FILELINE_CRIT_RE and NO_WRITE_CRIT_RE (the prose recognition with its source-word and table-word regexes),
and the A4 capability tables that the legacy parity bodies kept in eva.py read (CAPABILITY_BY_TOOL, NEUTRAL_TOOLS,
OPENCODE_PERMISSION_GRANTS, TOOL_ALIASES, tool_keys, capabilities). Each is the A4 eva.py source segment byte for byte,
apart from eva. qualification and \\uXXXX escapes for non-ASCII; tests/test_differential.py pins each against its A4
sha256. Only tests/test_differential.py and the A3 structure oracle next to this file import it, and a tripwire proves
no CLI path reaches it. A6 deletes it with the rest of the oracle."""
import os, re
import eva

CAPABILITY_BY_TOOL = {}
for _cap, _names in (("read", "read_file read grep glob list_dir ls find search view cat"),
                     ("write", "write_file edit_file write edit multiedit create_file delete_file notebookedit apply_patch"),
                     ("shell", "bash shell terminal exec run_command"),
                     ("web_fetch", "web_fetch webfetch"), ("web_search", "web_search websearch"),
                     ("delegate", "task"), ("skill", "skill")):
    for _n in _names.split():
        CAPABILITY_BY_TOOL[_n] = _cap

NEUTRAL_TOOLS = ("todowrite", "todo_write")

OPENCODE_PERMISSION_GRANTS = {"read": "read", "edit": "write", "glob": "read", "grep": "read", "list": "read", "bash": "shell",
                              "task": "delegate", "external_directory": None, "webfetch": "web_fetch",
                              "websearch": "web_search", "lsp": "read", "skill": "skill"}

TOOL_ALIASES = {"read": "read", "read_file": "read", "write": "write", "write_file": "write", "edit": "edit",
                "edit_file": "edit", "bash": "shell", "shell": "shell", "websearch": "web_search", "web_search": "web_search",
                "webfetch": "web_fetch", "web_fetch": "web_fetch"}

def tool_keys(names):
    """Set of tool identities of a list of tool names (TOOL_ALIASES, lower case; neutral tools dropped)."""
    keys = {n.strip().lower() for n in names}
    return {TOOL_ALIASES.get(k, k) for k in keys if k not in NEUTRAL_TOOLS}

def capabilities(names):
    """(set of capabilities, unmapped names) of a list of tool names, by CAPABILITY_BY_TOOL (case-insensitive)."""
    caps, bad = set(), []
    for n in names:
        k = n.strip().lower()
        if k in NEUTRAL_TOOLS: continue
        if k in CAPABILITY_BY_TOOL: caps.add(CAPABILITY_BY_TOOL[k])
        else: bad.append(n)
    return caps, bad

def grade_transcript(agent_path, transcript_path):
    """Grade one run transcript against the agent's declaration and scenario (see eva.load_transcript for format)."""
    r = eva.Report(transcript_path, "transcript")
    run, terr = eva.load_transcript(transcript_path)
    r.add("transcript-valid", "structure", run is not None, "transcript shape" + (": %s" % terr if terr else " valid"),
          "transcript\ub97c {\"scenario\": str, \"tool_calls\": [...], \"final\": str} \ud615\uc2dd\uc73c\ub85c \uace0\uce58\uc138\uc694 (LOOP.md \ucc38\uace0).", must=True)
    if run is None: return r
    raw, rerr = eva.read_text(os.path.join(agent_path, "AGENT.md"))
    meta, _, ferr = eva.parse_frontmatter_ex(raw) if raw is not None else (None, "", rerr)
    r.add("agent-valid", "structure", meta is not None, "agent AGENT.md frontmatter" + (": %s" % ferr if ferr else " valid"),
          "\ucc44\uc810\ud560 \uc5d0\uc774\uc804\ud2b8 \ud3f4\ub354\uc758 AGENT.md frontmatter\ub97c \uba3c\uc800 \uace0\uce58\uc138\uc694.", must=True)
    if meta is None: return r
    tools = [str(x) for x in eva.as_list(meta.get("tools"))]
    scen = eva.load_scenarios(agent_path)
    sid = run["scenario"]
    match = [(f, s, e) for f, s, e in scen if isinstance(s, dict) and s.get("id") == sid]
    s = match[0][1] if match and not match[0][2] else None
    reason = "scenario '%s' exists" % sid if s is not None else (
        "scenario '%s' invalid: %s" % (sid, match[0][2]) if match else "scenario '%s' not found" % sid)
    r.add("scenario-known", "goal", s is not None, reason, "transcript\uc758 scenario\ub97c \uc720\ud6a8\ud55c \uc2dc\ub098\ub9ac\uc624 id\uc640 \ub9de\ucd94\uc138\uc694.", must=True)
    if s is None: return r
    names = [c["tool"] for c in run["calls"]]
    illegal = sorted(set(names) - set(tools))
    r.add("only-declared-tools", "tool-use", not illegal, "illegal tools: %s" % illegal,
          "\uc120\uc5b8\ub418\uc9c0 \uc54a\uc740 \ub3c4\uad6c %s \uc0ac\uc6a9\uc744 \uba48\ucd94\uc138\uc694." % illegal, must=True)
    miss = [x for x in s["expected_tools"] if x not in names]
    r.add("expected-tools-called", "tool-use", not miss, "missing calls: %s" % miss,
          "\ud544\uc694\ud55c \ub3c4\uad6c %s\ub97c \ud638\ucd9c\ud558\uc138\uc694." % miss, must=True)
    viol = eva.answer_lookup_violations(run["calls"], run["workspace"])
    r.add("no-answer-lookup", "tool-use", not viol,
          "%d tool calls inspected%s" % (len(run["calls"]), (": answer lookup %s" % viol) if viol else ", no answer lookup"),
          "\uc6cc\ud06c\uc2a4\ud398\uc774\uc2a4 \ubc16 \ud0d0\uc0c9(.., /, ~, \uc6cc\ud06c\uc2a4\ud398\uc774\uc2a4 \ubc16 \uc808\ub300\uacbd\ub85c)\uc774\ub098 git \uc774\ub825 \ub4a4\uc9c0\uae30(git log --all, reflog, fsck \ub4f1)\ub97c \uba48\ucd94\uace0 "
          "\uc815\ub2f5\uc744 \ucc3e\uc544\ubcf4\uc9c0 \ub9d0\uace0 \uc8fc\uc5b4\uc9c4 \uc6cc\ud06c\uc2a4\ud398\uc774\uc2a4 \uc548\uc758 \uadfc\uac70\ub85c \uc9c1\uc811 \ud478\uc138\uc694.", must=True)
    final = run["final"]
    lack = [k for k in s["must_include"] if k.lower() not in final.lower()]
    r.add("outcome-keywords", "goal", not lack, "final answer missing: %s" % lack,
          "\ucd5c\uc885 \ub2f5\uc5d0 %s\ub97c \ud3ec\ud568\ud558\uc138\uc694." % lack, must=True)
    # outcome: every machine-checkable criterion must hold on the final answer (must-pass)
    sources = [("criteria", c) for c in eva.as_list(meta.get("success_criteria"))]
    sources += [("criteria", c) for c in eva.as_list(s.get("success_criteria"))]
    sources += [("expected", c) for c in eva.as_list(s.get("expected_outcome"))]
    seen, checked = {}, 0
    for origin, crit in sources:
        crit = str(crit)
        ev = outcome_evidence(crit, final, run)
        base = "%s-%s" % (origin, ev[0] if ev else "needs-review")
        seen[base] = seen.get(base, 0) + 1
        cid = base if seen[base] == 1 else "%s-%d" % (base, seen[base])
        if ev is None:
            r.review(cid, "goal", "not machine-checkable, grade by human/model: '%s'" % crit,
                     "\uc0ac\ub78c \ub610\ub294 \ubaa8\ub378 \ucc44\uc810\uc790\uac00 \ucd5c\uc885 \ub2f5\uc774 '%s'\ub97c \ub9cc\uc871\ud558\ub294\uc9c0 \ud310\uc815\ud558\uc138\uc694." % crit)
            continue
        checked += 1
        _, ok, got, fix = ev
        r.add(cid, "goal", ok, "'%s' -> %s" % (crit, got), fix, must=True)
    r.add("outcome-evidence", "goal", checked > 0, "%d checkable outcome criteria" % checked,
          "expected_outcome/success_criteria\uc5d0 \ud655\uc778 \uac00\ub2a5\ud55c \uc870\uac74(\uc608: '\ucd9c\ucc98 3\uac1c \uc774\uc0c1', '800\uc790 \uc774\ud558', '\ube44\uad50\ud45c')\uc744 \ub123\uc73c\uc138\uc694.", must=True)
    return r

FILELINE_CRIT_RE = re.compile(r"\ud30c\uc77c\s*[:\u00b7/]\s*\uc904|file\s*:\s*line|file[- ]line", re.I)

NO_WRITE_CRIT_RE = re.compile(r"(?:\ud30c\uc77c\s*)?(?:\ubcc0\uacbd|\uc218\uc815|\uc4f0\uae30)\s*0\s*\uac74|\uc77d\uae30\s*\uc804\uc6a9|read[- ]only|"
                              r"no\s+(?:file\s+)?(?:writes?|edits?|changes|modifications)|zero\s+(?:file\s+)?(?:writes|edits|changes)", re.I)

def outcome_evidence(crit, final, run=None):
    """Map one criterion to checkable evidence on the final answer (and the run's tool calls/results).
    run: normalized transcript from eva.load_transcript (None = no tool calls, no tool results).
    Returns (kind, ok, observed, fix) or None if the criterion is not machine-checkable."""
    calls = run["calls"] if run else []
    results = run["results"] if run else []
    if NO_WRITE_CRIT_RE.search(crit):
        w = eva.write_calls(calls)
        return ("no-writes", not w, "%d write/edit tool calls%s" % (len(w), (": %s" % w) if w else ""),
                "\ud30c\uc77c\uc744 \ubc14\uafb8\ub294 \ub3c4\uad6c(write/edit \ub4f1) \ud638\ucd9c %s\ub97c \uc5c6\uc560\uace0 \uc77d\uae30 \uc804\uc6a9\uc73c\ub85c \uc791\uc5c5\ud558\uc138\uc694." % w)
    n = _min_count(crit)
    if n is not None and FILELINE_CRIT_RE.search(crit):
        seen, ws = eva.lines_seen(run), (run or {}).get("workspace")
        cites = sorted({(f, int(a), int(b) if b else int(a)) for f, a, b in eva.FILELINE_RE.findall(final)})
        shown = lambda f, ln: eva._norm_path(f, None, ws) in seen.get(ln, ())
        # a range 'a-b' needs a <= b and every line a..b shown (all() stops at the first unseen line)
        ok_cites = [c for c in cites if c[1] <= c[2] and all(shown(c[0], ln) for ln in range(c[1], c[2] + 1))]
        bad = [("%s:%d" % c[:2]) + ("-%d" % c[2] if c[2] != c[1] else "") for c in cites if c not in ok_cites]
        return ("file-lines", len(ok_cites) >= n,
                "%d file:line citations whose lines appear in captured read results (need >= %d)%s" % (len(ok_cites), n,
                ("; cited lines never shown by a tool: %s" % bad) if bad else ""),
                "\uadf8 \ud30c\uc77c \ud558\ub098\ub9cc \uc77d\uc740 \ub3c4\uad6c \ud638\ucd9c(Read \ub3c4\uad6c, cat -n FILE, nl -ba FILE, grep -n PATTERN FILE)\uc758 \uacb0\uacfc\uc5d0 \uc904 \ubc88\ud638\uc640 "
                "\ud568\uaed8 \ub098\uc628 \uc904\ub9cc '\uacbd\ub85c:\uc904'(\uc608: src/app.py:42)\ub85c \uc778\uc6a9\ud558\uc138\uc694. \ubc94\uc704 '\uacbd\ub85c:\uc2dc\uc791-\ub05d'\uc740 \uc2dc\uc791 <= \ub05d\uc774\uace0 \uadf8 \uc0ac\uc774 \ubaa8\ub4e0 \uc904\uc774 "
                "\ub098\uc654\uc5b4\uc57c \ud569\ub2c8\ub2e4. \uacbd\ub85c\ub294 \uc77d\uc740 \uacbd\ub85c\uc640 \uc815\ud655\ud788 \uac19\uc544\uc57c \ud569\ub2c8\ub2e4. "
                "%d\uac1c \uc774\uc0c1 \ud544\uc694 (\ud604\uc7ac \ud655\uc778\ub41c \uac83 %d\uac1c). \uc5c6\ub294 \uc904\u00b7\uc77d\uc9c0 \uc54a\uc740 \uc904\uc740 \uc138\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4." % (n, len(ok_cites)))
    if n is not None and re.search(r"\ucd9c\ucc98|\uc778\uc6a9|\ub9c1\ud06c|source|citation|cite|reference|url|link", crit, re.I):
        urls = sorted(set(eva._norm_url(u) for u in eva.URL_RE.findall(final)))
        fetched = set(eva._norm_url(u) for res in results for u in eva.URL_RE.findall(res))
        ok_urls = [u for u in urls if u in fetched]
        bad = [u for u in urls if u not in fetched]
        return ("sources", len(ok_urls) >= n,
                "%d verified unique URLs (need >= %d)%s" % (len(ok_urls), n,
                ("; not found in tool results: %s" % bad) if bad else ""),
                "\ub3c4\uad6c \uacb0\uacfc(tool result)\uc5d0 \uc2e4\uc81c\ub85c \ub098\uc628 \ucd9c\ucc98 URL\uc744 %d\uac1c \uc774\uc0c1 \uc778\uc6a9\ud558\uc138\uc694 (\ud604\uc7ac \ud655\uc778\ub41c \uac83 %d\uac1c). "
                "\uc9c0\uc5b4\ub0b8 URL\uc740 \uc138\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4." % (n, len(ok_urls)))
    mx = _max_chars(crit)
    if mx is not None:
        return ("length", len(final) <= mx, "%d chars (max %d)" % (len(final), mx),
                "\ucd5c\uc885 \ub2f5\uc744 %d\uc790 \uc774\ud558\ub85c \uc904\uc774\uc138\uc694 (\ud604\uc7ac %d\uc790)." % (mx, len(final)))
    if re.search(r"\ube44\uad50\ud45c|\ud45c\ub85c|\ud45c \ud615\uc2dd|\btable\b", crit, re.I):
        ok = bool(eva.TABLE_RE.search(final))
        return ("table", ok, "markdown table %s" % ("found" if ok else "not found"),
                "\ucd5c\uc885 \ub2f5\uc5d0 \ud5e4\ub354\uc640 \uad6c\ubd84\uc120(|---|)\uc774 \uc788\ub294 \ub9c8\ud06c\ub2e4\uc6b4 \ud45c\ub97c \ub123\uc73c\uc138\uc694.")
    return None

def _min_count(text):
    m = (re.search(r"(\d+)\s*(?:\uac1c|\uac74|\uacf3)?\s*\uc774\uc0c1", text) or re.search(r"at\s+least\s+(\d+)", text, re.I)
         or re.search(r">=\s*(\d+)", text) or re.search(r"(\d+)\s*\+", text))
    return int(m.group(1)) if m else None

def _max_chars(text):
    m = (re.search(r"(\d+)\s*\uc790\s*(?:\uc774\ud558|\uc774\ub0b4)", text)
         or re.search(r"(?:at\s+most|no\s+more\s+than|<=|under)\s*(\d+)\s*(?:chars?|characters)", text, re.I))
    return int(m.group(1)) if m else None
