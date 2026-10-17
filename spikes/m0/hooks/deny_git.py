#!/usr/bin/env python3
"""PreToolUse hook: deny `git commit|push` in Bash, deny Edit/Write outside the allowed dir."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWED = os.path.realpath(os.environ.get("AITEAMS_ALLOWED_DIR") or os.path.join(HERE, "..", "tmp", "repo"))
LOG = os.environ.get("AITEAMS_HOOK_LOG") or os.path.join(HERE, "..", "tmp", "hook_input.json")

raw = sys.stdin.read()
try:
    data = json.loads(raw)
except ValueError:
    data = {}
try:
    with open(LOG, "a") as f:
        f.write(raw.strip() + "\n")
except OSError:
    pass


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "AITEAMS_DENY: " + reason,
    }}))
    sys.exit(0)


tool = data.get("tool_name", "")
inp = data.get("tool_input") or {}
if tool == "Bash":
    cmd = inp.get("command", "")
    if re.search(r"\bgit\b[^|;&]*\b(commit|push)\b", cmd):
        deny("git commit/push is not allowed for agents; the Master commits.")
elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
    p = inp.get("file_path") or inp.get("notebook_path") or ""
    if p:
        full = p if os.path.isabs(p) else os.path.join(data.get("cwd") or os.getcwd(), p)
        rp = os.path.realpath(full)
        if not (rp == ALLOWED or rp.startswith(ALLOWED + os.sep)):
            deny("path %s is outside the allowed dir %s" % (p, ALLOWED))
sys.exit(0)
