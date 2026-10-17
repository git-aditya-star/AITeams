#!/usr/bin/env python3
"""AITeams PreToolUse hook (stdlib only): deny commits/pushes, writes outside the project, daemon tampering."""
import json
import os
import re
import sys

PROJECT = os.path.realpath(os.environ.get("AITEAMS_PROJECT_PATH") or os.getcwd())
HOME_AITEAMS = os.environ.get("AITEAMS_HOME") or os.path.join(os.path.expanduser("~"), ".aiteams")
LOG_MAX = 200

GIT_RE = re.compile(r"\bgit\b[^|;&\n]*\b(commit|push|reset\s+--hard|clean\s+-f\w*)\b")
RM_RE = re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+(\S+)")
KILL_RE = re.compile(r"\b(kill|pkill)\b")


def log_input(raw):
    try:
        path = os.path.join(PROJECT, ".aiteams", "hooks.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = []
        if os.path.exists(path):
            with open(path) as f:
                lines = f.read().splitlines()
        lines.append(raw.strip().replace("\n", " "))
        with open(path, "w") as f:
            f.write("\n".join(lines[-LOG_MAX:]) + "\n")
    except OSError:
        pass


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "AITEAMS_DENY: " + reason}}))
    sys.exit(0)


def daemon_pid():
    try:
        with open(os.path.join(HOME_AITEAMS, "daemon.json")) as f:
            return int(json.load(f).get("pid") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def inside_project(path):
    rp = os.path.realpath(path)
    return rp == PROJECT or rp.startswith(PROJECT + os.sep)


def check_bash(cmd):
    if GIT_RE.search(cmd):
        deny("git commit/push/reset --hard/clean are not allowed for roles; the Master commits.")
    for m in RM_RE.finditer(cmd):
        target = os.path.expanduser(m.group(2).strip("'\""))
        if not os.path.isabs(target):
            target = os.path.join(os.getcwd(), target)
        if not inside_project(target):
            deny("rm -rf outside the project (%s) is not allowed." % m.group(2))
    low = cmd.replace("$HOME", "~")
    if re.search(r"(~|/Users/[^/\s]+|/home/[^/\s]+)/\.claude\b", low):
        deny("touching ~/.claude is not allowed.")
    if "state.db" in cmd:
        deny("the AITeams state database is off limits.")
    pid = daemon_pid()
    if pid and KILL_RE.search(cmd) and (re.search(r"\b%d\b" % pid, cmd) or "aiteams" in cmd):
        deny("killing the AITeams daemon is not allowed.")


def check_write(data, inp):
    p = inp.get("file_path") or inp.get("notebook_path") or ""
    if not p:
        return
    full = p if os.path.isabs(p) else os.path.join(data.get("cwd") or os.getcwd(), p)
    rp = os.path.realpath(full)
    if not inside_project(rp):
        deny("path %s is outside the project %s." % (p, PROJECT))
    rel = os.path.relpath(rp, PROJECT)
    if rel == ".aiteams" or rel.startswith(".aiteams" + os.sep):
        if not (rel.startswith(os.path.join(".aiteams", "roles") + os.sep)
                or rel.startswith(os.path.join(".aiteams", "notes") + os.sep)):
            deny("writing under .aiteams/ is only allowed in .aiteams/roles/ and .aiteams/notes/.")


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    log_input(raw)
    if not isinstance(data, dict):
        return
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    if tool == "Bash":
        check_bash(str(inp.get("command") or ""))
    elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        check_write(data, inp)


if __name__ == "__main__":
    main()
    sys.exit(0)
