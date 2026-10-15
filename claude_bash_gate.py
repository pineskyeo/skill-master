#!/usr/bin/env python3
"""Fail-closed PreToolUse gate for the CSV skill's one permitted Bash invocation."""
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


def main():
    try:
        skill_name, script_relative, script_hash, python3 = sys.argv[1:]
        if len(sys.argv) != 5: raise ValueError("gate configuration")
        root = os.environ.get("SKILL_EVAL_WORKDIR")
        payload = json.load(sys.stdin)
        if (isinstance(payload, dict) and payload.get("hook_event_name") == "PreToolUse" and
                payload.get("tool_name") not in ("Bash", "Monitor", "PowerShell") and
                not carries_command(payload.get("tool_input"))):
            return  # no decision: ordinary tools follow their normal permission flow
        problem = check(payload, root, skill_name, script_relative, script_hash, python3)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as ex:
        problem = "gate validation failed: %s" % type(ex).__name__
    print(json.dumps(decision("deny" if problem else "allow", problem or "validated CSV cleaner invocation")))


if __name__ == "__main__": main()
