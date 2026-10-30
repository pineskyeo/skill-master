"""Codex `codex exec --json` events -> normalized transcript (unit B3 D4).

Event and item shapes as recorded in the program runner's own Codex reviews (tests/fixtures/streams/codex/SOURCE.md).
A command_execution item is the tool `shell`. A shell wrapper is read as the SCRIPT it runs, so the transcript
verifiers see the command that ran (a wrapped `rm` or `cat -n FILE` is otherwise hidden behind the shell path). The
one unwrapped form: an optional `env`, optional NAME=value assignments, sh, bash or zsh named by any path, short-flag
clusters of which only the last holds `c` (as its last letter), SCRIPT, then any positional arguments. Any other
command whose program is a shell raises NormalizeError (fail closed: a write it hides must never pass no_writes).
collab_tool_call is refused: a spawned agent's own tool calls are not in this stream (UNVERIFIED), so a write there
could pass no_writes. An `error` item is a CLI notice, not a tool call."""
import os, re, shlex
from engine.normalize import NormalizeError, checked, events, tokens

FORMAT = "codex-exec-json"
EVENTS = {"thread.started": "extract", "turn.started": "ignore", "item.started": "track", "item.completed": "extract",
          "turn.completed": "extract", "turn.failed": "extract", "error": "extract"}
ITEMS = {"command_execution": "shell", "file_change": "apply_patch", "agent_message": "text", "reasoning": "ignore",
         "error": "ignore"}
SHELLS = ("sh", "bash", "zsh")
OTHER_SHELLS = ("dash", "ksh", "mksh", "yash", "fish", "csh", "tcsh", "nu", "pwsh", "elvish", "xonsh")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
FLAGS_RE = re.compile(r"^-[A-Za-z]+$")


def _is_shell(word):
    return os.path.basename(word) in SHELLS + OTHER_SHELLS


def command_of(raw):
    """SCRIPT of a documented shell wrapper, the raw command when its program is not a shell, NormalizeError else."""
    try:
        argv = shlex.split(raw)
    except ValueError:
        if any(_is_shell(w) for w in raw.split()):
            raise NormalizeError("codex: shell command %r cannot be split into words" % raw[:200])
        return raw
    i = 1 if argv and os.path.basename(argv[0]) == "env" else 0
    while i < len(argv) and ASSIGNMENT_RE.match(argv[i]):
        i += 1
    rest = argv[i:]
    if not rest or not (_is_shell(rest[0]) or (i and rest[0].startswith("-") and any(map(_is_shell, rest)))):
        return raw
    flags, j = rest[1:], 0
    while j < len(flags) and FLAGS_RE.match(flags[j]) and "c" not in flags[j]:
        j += 1
    if (os.path.basename(rest[0]) in SHELLS and j + 1 < len(flags) and FLAGS_RE.match(flags[j])
            and flags[j].index("c") == len(flags[j]) - 1):
        return command_of(flags[j + 1])
    raise NormalizeError("codex: shell command %r is not the one documented wrapper form (env, NAME=value, sh|bash|zsh, "
                         "short flags ending in c, SCRIPT, arguments); refusing to grade what it ran" % raw[:200])


def normalize(text, scenario_id, agent=None):
    calls, texts, started, done = [], [], set(), set()
    session, terminal, failed, tin, tout = None, False, False, 0, 0
    for n, ev in events("codex", text):
        kind = ev.get("type")
        action = EVENTS.get(kind)
        if action is None:
            raise NormalizeError("codex: line %d: unknown event type %r" % (n, kind))
        item = ev.get("item")
        if kind in ("item.started", "item.completed"):
            if not (isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]):
                raise NormalizeError("codex: line %d: %s without an item id" % (n, kind))
            it = item.get("type")
            if it not in ITEMS:
                raise NormalizeError("codex: line %d: unknown item type %r%s" % (n, it, (
                    " (a spawned agent's own tool calls are not in the exec --json stream, UNVERIFIED; refusing to "
                    "grade it)" if it == "collab_tool_call" else "")))
        if kind == "thread.started":
            session = ev.get("thread_id")
        elif kind == "item.started":
            started.add(item["id"])
        elif kind == "item.completed":
            done.add(item["id"])
            it = ITEMS[item["type"]]
            if it == "text":
                if isinstance(item.get("text"), str):
                    texts.append(item["text"])
            elif it == "shell":
                raw = item.get("command")
                if not (isinstance(raw, str) and raw.strip()):
                    raise NormalizeError("codex: line %d: command_execution without a command" % n)
                out = item.get("aggregated_output")
                call = {"tool": "shell", "input": {"command": command_of(raw)},
                        "result": out if isinstance(out, str) else "", "id": item["id"]}
                if item.get("exit_code") not in (0, None) or item.get("status") == "failed":
                    call["is_error"] = True
                calls.append(call)
            elif it == "apply_patch":
                call = {"tool": "apply_patch", "input": {"changes": item.get("changes")}, "id": item["id"]}
                if item.get("status") == "failed":
                    call["is_error"] = True
                calls.append(call)
        elif kind == "turn.completed":
            terminal = True
            usage = ev.get("usage") or {}
            if not isinstance(usage, dict):
                raise NormalizeError("codex: line %d: usage is not an object" % n)
            tin += tokens(usage.get("input_tokens"), "usage.input_tokens")
            tout += tokens(usage.get("output_tokens"), "usage.output_tokens") + \
                tokens(usage.get("reasoning_output_tokens"), "usage.reasoning_output_tokens")
        elif kind in ("turn.failed", "error"):
            terminal = terminal or kind == "turn.failed"
            failed = True
    open_items = sorted(started - done)
    if open_items:
        raise NormalizeError("codex: item(s) %s started but never completed" % ", ".join(open_items))
    if not terminal:
        raise NormalizeError("codex: no turn.completed or turn.failed event (truncated stream)")
    t = {"scenario": scenario_id, "final": texts[-1] if texts else "", "tool_calls": calls, "tool": "codex",
         "format": FORMAT, "usage": {"input_tokens": tin, "output_tokens": tout}, "cost_usd": None,
         "is_error": failed, "agent_loaded": "unverified"}
    if isinstance(session, str) and session.strip():
        t["session_id"] = session
    return checked(t)
