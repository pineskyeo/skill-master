"""Claude Code `claude -p --output-format stream-json --verbose` stream -> normalized transcript (unit B3 D4).

Event shapes as recorded in the program runner's own sessions (tests/fixtures/streams/claude/SOURCE.md). Tool calls
of every agent in the stream count, a spawned subagent's too, so a write anywhere stays visible to no_writes."""
from engine.normalize import NormalizeError, checked, events, tokens

FORMAT = "claude-stream-json"
IGNORED_SYSTEM = ("hook_started", "hook_response", "thinking_tokens", "permission_denied", "api_retry",
                  "background_tasks_changed", "commands_changed", "informational", "notification", "task_notification",
                  "task_progress", "task_started", "task_updated")
IGNORED_TYPES = ("rate_limit_event", "tool_progress", "command_lifecycle")
ASSISTANT_BLOCKS = {"text": "extract", "tool_use": "extract", "thinking": "ignore", "redacted_thinking": "ignore"}
USER_BLOCKS = {"tool_result": "extract", "text": "ignore"}
RESULT_TEXT_BLOCKS = ("text",)


def _content_text(content, where):
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if not isinstance(content, list):
        raise NormalizeError("claude: %s content is neither a string nor a list of blocks" % where)
    parts = []
    for b in content:
        if not (isinstance(b, dict) and b.get("type") in RESULT_TEXT_BLOCKS and isinstance(b.get("text"), str)):
            raise NormalizeError("claude: %s holds a block that is not text (%r)" % (
                where, b.get("type") if isinstance(b, dict) else type(b).__name__))
        parts.append(b["text"])
    return "\n".join(parts)


def _blocks(ev, n):
    content = (ev.get("message") or {}).get("content") if isinstance(ev.get("message"), dict) else None
    if isinstance(content, str):
        return []
    if not isinstance(content, list):
        raise NormalizeError("claude: line %d: %s event has no content list" % (n, ev.get("type")))
    return content


def normalize(text, scenario_id, agent=None):
    init, result, calls, by_id, texts = None, None, [], {}, []
    for n, ev in events("claude", text):
        kind, sub = ev.get("type"), ev.get("subtype")
        if kind == "system" and sub == "init":
            if init is not None:
                raise NormalizeError("claude: line %d: a second system/init event" % n)
            init = ev
        elif (kind == "system" and sub in IGNORED_SYSTEM) or kind in IGNORED_TYPES:
            continue
        elif kind == "assistant":
            for b in _blocks(ev, n):
                bt = b.get("type") if isinstance(b, dict) else None
                if ASSISTANT_BLOCKS.get(bt) is None:
                    raise NormalizeError("claude: line %d: unknown assistant block type %r" % (n, bt))
                if bt == "text" and isinstance(b.get("text"), str):
                    texts.append(b["text"])
                elif bt == "tool_use":
                    name, cid = b.get("name"), b.get("id")
                    if not (isinstance(name, str) and name.strip() and isinstance(cid, str) and cid):
                        raise NormalizeError("claude: line %d: tool_use block without a name and an id" % n)
                    if cid in by_id:
                        raise NormalizeError("claude: line %d: tool_use id %s appears twice" % (n, cid))
                    call = {"tool": name, "input": b.get("input"), "id": cid}
                    calls.append(call)
                    by_id[cid] = call
        elif kind == "user":
            for b in _blocks(ev, n):
                bt = b.get("type") if isinstance(b, dict) else None
                if USER_BLOCKS.get(bt) is None:
                    raise NormalizeError("claude: line %d: unknown user block type %r" % (n, bt))
                if bt != "tool_result":
                    continue
                call = by_id.get(b.get("tool_use_id"))
                if call is None:
                    raise NormalizeError("claude: line %d: tool_result for an unknown tool_use id %r"
                                         % (n, b.get("tool_use_id")))
                if "result" in call:
                    raise NormalizeError("claude: line %d: a second tool_result for %s" % (n, call["id"]))
                call["result"] = _content_text(b.get("content"), "line %d tool_result" % n)
                if b.get("is_error") is True:
                    call["is_error"] = True
        elif kind == "result":
            if result is not None:
                raise NormalizeError("claude: line %d: a second result event" % n)
            result = ev
        else:
            raise NormalizeError("claude: line %d: unknown event type %r%s" % (
                n, kind, " subtype %r" % sub if kind == "system" else ""))
    if init is None:
        raise NormalizeError("claude: no system/init event")
    if result is None:
        raise NormalizeError("claude: no result event (truncated stream)")
    final = result.get("result") if isinstance(result.get("result"), str) else (texts[-1] if texts else "")
    usage = result.get("usage") or {}
    if not isinstance(usage, dict):
        raise NormalizeError("claude: result usage is not an object")
    cost = result.get("total_cost_usd")
    agents = init.get("agents")
    if agent is None:
        loaded = "unverified"
    else:
        loaded = "verified" if isinstance(agents, list) and agent in agents else "not-loaded"
    t = {"scenario": scenario_id, "final": final, "tool_calls": calls, "tool": "claude", "format": FORMAT,
         "usage": {"input_tokens": sum(tokens(usage.get(k), "usage." + k) for k in (
                       "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
                   "output_tokens": tokens(usage.get("output_tokens"), "usage.output_tokens")},
         "cost_usd": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else None,
         "is_error": result.get("is_error") is True, "agent_loaded": loaded}
    for key, value in (("workspace", init.get("cwd")), ("model", init.get("model")),
                       ("session_id", init.get("session_id"))):
        if isinstance(value, str) and value.strip():
            t[key] = value
    return checked(t)
