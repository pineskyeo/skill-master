#!/usr/bin/env python3
"""PreToolUse gate for file edits and the CSV skill's one permitted Bash invocation, and in edit-allowlist mode
(argv: edit-allowlist <skill folder realpath>) or agent-allowlist mode (argv: agent-allowlist <agent folder realpath>)
the loop builder's writable set (engine/writable.py)."""
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import sys


def decision(kind, reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": kind,
                                   "permissionDecisionReason": reason}}


def carries_command(value):
    """Detect a command field even if a future tool nests it in its input."""
    if isinstance(value, dict):
        return "command" in value or any(carries_command(v) for v in value.values())
    if isinstance(value, list):
        return any(carries_command(v) for v in value)
    return False


EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Glob", "Grep"}
EDIT_PATH_KEYS = {"file_path", "notebook_path"}


def edit_paths(value):
    """Find every file target, including targets in a future tool's nested input."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in EDIT_PATH_KEYS:
                yield item
            else:
                yield from edit_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from edit_paths(item)


def under_skill_tree(path, root):
    relative = os.path.relpath(path, root)
    parts = relative.split(os.sep)
    return any(parts[i:i + 2] == [".claude", "skills"] for i in range(len(parts) - 1))


def check_edit(payload, root, paths, skill_name, script_relative):
    if not isinstance(root, str) or not os.path.isabs(root) or os.path.realpath(root) != root:
        return "invalid workdir"
    if payload.get("cwd") != root or os.getcwd() != root:
        return "file edits must run from the original workdir"
    if not paths:
        return "file edit has no target path"
    for requested in paths:
        if not isinstance(requested, str) or not requested or "\x00" in requested:
            return "invalid file edit path"
        requested_path = os.path.normpath(requested if os.path.isabs(requested) else os.path.join(root, requested))
        target = os.path.realpath(requested_path)
        try:
            relative = os.path.relpath(target, root)
        except ValueError:
            return "file edit resolves outside the workdir"
        if relative == ".." or relative.startswith(".." + os.sep) or relative == ".":
            return "file edit resolves outside the workdir or targets its root"
        if under_skill_tree(requested_path, root) or under_skill_tree(target, root):
            return "installed skills are read-only during evaluation"
        # A hard link has no symlink to resolve; still protect the harness hook and installed script.
        protected = (os.path.realpath(__file__),
                     os.path.join(root, ".claude", "skills", skill_name, script_relative))
        if any(os.path.exists(target) and os.path.exists(p) and os.path.samefile(target, p) for p in protected):
            return "protected harness or installed skill file"
    return None


def regular_path(root, relative, existing, allow_hidden=False):
    if (not relative or relative.startswith("/") or "\\" in relative or
            any(part in ("", ".", "..") or (part.startswith(".") and not allow_hidden)
                for part in relative.split("/"))):
        return False
    path = root
    parts = relative.split("/")
    for part in parts[:-1]:
        path = os.path.join(path, part)
        try: mode = os.lstat(path).st_mode
        except OSError: return False
        if not stat.S_ISDIR(mode): return False
    path = os.path.join(path, parts[-1])
    try: mode = os.lstat(path).st_mode
    except FileNotFoundError: return not existing
    except OSError: return False
    return stat.S_ISREG(mode) if existing else False


def check(payload, root, skill_name, script_relative, script_hash, python3):
    command = payload.get("tool_input", {}).get("command") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "PreToolUse" or payload.get("tool_name") != "Bash":
        return "unexpected hook event"
    if not isinstance(command, str) or not re.fullmatch(r"[A-Za-z0-9_./ -]+", command):
        return "shell syntax or characters outside the permitted command"
    try: words = shlex.split(command)
    except ValueError: return "invalid shell command"
    script = ".claude/skills/%s/%s" % (skill_name, script_relative)
    if len(words) != 5 or words[:3] != ["python3", "-I", script]:
        return "only the installed clean.py invocation with two paths is permitted"
    if not isinstance(root, str) or not os.path.isabs(root) or os.path.islink(root):
        return "invalid workdir"
    if os.path.realpath(root) != root or payload.get("cwd") != root or os.getcwd() != root:
        return "Bash must run from the original workdir"
    if any(k in os.environ for k in ("BASH_ENV", "ENV", "BASHOPTS", "SHELLOPTS")) or any(
            k.startswith("BASH_FUNC_") for k in os.environ):
        return "shell startup or function overrides are not permitted"
    resolved_python = shutil.which("python3")
    if not resolved_python or os.path.realpath(resolved_python) != python3 or python3.startswith(root + os.sep):
        return "python3 does not resolve to the harness-approved interpreter"
    if not regular_path(root, script, True, allow_hidden=True): return "installed script is missing or aliased"
    with open(os.path.join(root, script), "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != script_hash:
            return "installed script differs from the harness copy"
    src, dst = words[3:]
    if not regular_path(root, src, True): return "input must be a regular workdir file without aliases"
    if not regular_path(root, dst, False): return "output must be a new workdir file without aliases"
    if src == dst: return "input and output must differ"
    return None


COMMAND_TOOLS = ("Bash", "Monitor", "PowerShell")


def builder_target_problem(writable, requested, cwd, root, mode):
    if not isinstance(requested, str) or not requested or "\x00" in requested:
        return "invalid file edit path"
    if not os.path.isabs(requested):
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or not os.path.samefile(cwd, root):
            return "relative file edit path outside the %s folder" % writable.MODES[mode].unit
        requested = os.path.join(root, requested)
    resolved = os.path.realpath(requested)
    for path in (os.path.normpath(requested), resolved):
        if not writable.is_writable(writable.under(root, path), mode):
            return "outside the builder's writable set (%s): %s" % (writable.MODES[mode].shown, requested)
    try: st = os.lstat(resolved)
    except FileNotFoundError: return None
    if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
        return "file has other hard links: %s" % requested
    return None


def builder_decision(payload, mode, root):
    """Edit-allowlist (skill) and agent-allowlist (agent) mode for the loop builders: None keeps reads in the normal
    permission flow."""
    from engine import writable
    if mode not in writable.MODES:
        return "deny", "unknown gate mode"
    if not isinstance(root, str) or not os.path.isabs(root) or os.path.realpath(root) != root or not os.path.isdir(root):
        return "deny", "invalid %s root" % writable.MODES[mode].unit
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "PreToolUse":
        return "deny", "unexpected hook event"
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input")
    if tool in COMMAND_TOOLS or carries_command(tool_input):
        return "deny", "the builder may not run commands"
    if tool in READ_TOOLS:
        return None
    if tool not in EDIT_TOOLS:
        return "deny", "tool not permitted for the builder: %s" % (tool,)
    paths = list(edit_paths(tool_input))
    if not paths:
        return "deny", "file edit has no target path"
    for requested in paths:
        problem = builder_target_problem(writable, requested, payload.get("cwd"), root, mode)
        if problem:
            return "deny", problem
    return "allow", "edit inside the builder's writable set"


def builder_main(mode, root):
    try:
        verdict = builder_decision(json.load(sys.stdin), mode, root)
    except Exception as ex:
        verdict = ("deny", "gate validation failed: %s" % type(ex).__name__)
    if verdict:
        print(json.dumps(decision(*verdict)))


def main():
    if len(sys.argv) == 3:
        return builder_main(*sys.argv[1:])
    try:
        skill_name, script_relative, script_hash, python3 = sys.argv[1:]
        if len(sys.argv) != 5: raise ValueError("gate configuration")
        root = os.environ.get("SKILL_EVAL_WORKDIR")
        payload = json.load(sys.stdin)
        if (isinstance(payload, dict) and payload.get("hook_event_name") == "PreToolUse" and
                payload.get("tool_name") not in ("Bash", "Monitor", "PowerShell") and
                not carries_command(payload.get("tool_input"))):
            tool_name = payload.get("tool_name")
            paths = list(edit_paths(payload.get("tool_input")))
            if tool_name in READ_TOOLS or (tool_name not in EDIT_TOOLS and not paths):
                return  # non-editing tools keep their normal permission flow
            problem = check_edit(payload, root, paths, skill_name, script_relative)
            if problem:
                print(json.dumps(decision("deny", problem)))
            return  # allowed edits still go through Claude's normal permission checks
        problem = check(payload, root, skill_name, script_relative, script_hash, python3)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as ex:
        problem = "gate validation failed: %s" % type(ex).__name__
    print(json.dumps(decision("deny" if problem else "allow", problem or "validated CSV cleaner invocation")))


if __name__ == "__main__": main()
