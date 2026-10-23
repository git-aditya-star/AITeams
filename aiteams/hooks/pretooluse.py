#!/usr/bin/env python3
"""AITeams PreToolUse hook (stdlib only): deny commits/pushes, writes outside the project, daemon tampering."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROJECT = os.path.realpath(os.environ.get("AITEAMS_PROJECT_PATH") or os.getcwd())
HOME_AITEAMS = os.environ.get("AITEAMS_HOME") or os.path.join(os.path.expanduser("~"), ".aiteams")
LOG_MAX = 200

GIT_RE = re.compile(r"\bgit\b[^|;&\n]*\b(commit|push|reset\s+--hard|clean\s+-f\w*)\b")
RM_RE = re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+(\S+)")
KILL_RE = re.compile(r"\b(kill|pkill)\b")
# Command position only, so `grep "aiteams upgrade" README.md` still works.
ADMIN_RE = re.compile(r"(^|[;&|(]\s*|\$\(\s*)(aiteams\s+(upgrade|stop|serve|token)|uv\s+tool)\b", re.M)
DAEMON_URL_RE = re.compile(r"\$\{?AITEAMS_DAEMON_URL")
RUNTIME_RE = re.compile(r"(~|\$\{?HOME\}?|/Users/[^/\s]+|/home/[^/\s]+)/\.aiteams/(token|runtime)\b")


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


def daemon_info():
    try:
        with open(os.path.join(HOME_AITEAMS, "daemon.json")) as f:
            d = json.load(f)
        return int(d.get("pid") or 0), int(d.get("port") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0, 0


def daemon_port(port):
    if port:
        return port
    m = re.search(r":(\d+)", os.environ.get("AITEAMS_DAEMON_URL") or "")
    return int(m.group(1)) if m else 7700


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
    pid, port = daemon_info()
    if pid and KILL_RE.search(cmd) and (re.search(r"\b%d\b" % pid, cmd) or "aiteams" in cmd):
        deny("killing the AITeams daemon is not allowed.")
    if ADMIN_RE.search(cmd):
        deny("the Master runs aiteams upgrade/stop/serve/token and changes the install; roles do not.")
    port = daemon_port(port)
    if re.search(r"(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]):%d\b" % port, cmd) or DAEMON_URL_RE.search(cmd):
        deny("roles talk to AITeams through its MCP tools, not HTTP.")
    if RUNTIME_RE.search(cmd):
        deny("the AITeams runtime and token are off limits.")


NOTES = ".project-notes"
NOTES_GENERATED_DIRS = (".metrics", ".state", ".backups")
# F-19: the plugin's Stop hook asks for a whole-file Write of .metrics/pending-<session>.json holding
# {"id", "score", "comment"}; the plugin folds it into events.jsonl itself. That one file is the only way in.
NOTES_SCORE_RE = re.compile(r"pending-[a-z0-9-]+\.json")
NOTES_SCORE_KEYS = {"id", "score", "comment"}


def notes_score_write(tool, low, inp):
    """True for the plugin's score file: Write of .project-notes/.metrics/pending-<id>.json with a score record.
    A wrong tool or content on that path is denied with what's expected; any other path returns False."""
    d, name = os.path.split(low)
    if d != os.path.join(NOTES, ".metrics") or not NOTES_SCORE_RE.fullmatch(name):
        return False
    try:
        rec = json.loads(inp.get("content") or "") if tool == "Write" else None
    except ValueError:
        rec = None
    if not (isinstance(rec, dict) and rec and set(rec) <= NOTES_SCORE_KEYS):
        deny("the project-notes score file takes one Write of a JSON object with only id, score and comment.")
    return True


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
                or rel.startswith(os.path.join(".aiteams", "features") + os.sep)):
            deny("writing under .aiteams/ is only allowed in .aiteams/roles/ and .aiteams/features/.")
    low = rel.lower()  # macOS file systems are case-insensitive: index.md is INDEX.md
    if low == NOTES or low.startswith(NOTES + os.sep):
        if notes_score_write(data.get("tool_name"), low, inp):
            return
        if low == os.path.join(NOTES, "index.md") or any(low.startswith(os.path.join(NOTES, d) + os.sep)
                                                         for d in NOTES_GENERATED_DIRS):
            deny("%s is generated by the project-notes plugin; edit the topic note instead." % rel)
        # Notes are never locked: the plugin's Stop hook makes a role refresh a stale note before its turn ends,
        # and a lock held by another running role would wedge that turn until --max-turns.
        return
    check_lock(rel)


def check_lock(rel):
    """Ask the daemon to lock the file for this role; deny when another role holds it. Fail open."""
    url, project, turn = (os.environ.get("AITEAMS_DAEMON_URL"), os.environ.get("AITEAMS_PROJECT"),
                          os.environ.get("AITEAMS_TURN"))
    if not (url and project and turn):
        return
    req = urllib.request.Request(url.rstrip("/") + "/internal/locks/acquire", data=json.dumps({"path": rel}).encode(),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "X-AITeams-Project": project, "X-AITeams-Turn": turn,
                                                         "X-AITeams-Token": os.environ.get("AITEAMS_TURN_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read().decode() or "{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        log_input("lock check skipped (daemon unreachable): %s" % e)
        return
    if isinstance(res, dict) and res.get("ok") is False:
        since = time.strftime("%H:%M", time.localtime(float(res.get("since") or 0)))
        deny("%s is locked by %s since %s; ask them in chat, or ask @lead/@manager to unlock_file(path, force=true)."
             % (rel, res.get("holder"), since))


def check_skill(inp):
    """Skills of a plugin marked "Ask me first" (F-23, AITEAMS_ASK_PLUGINS) need the Master's approval: ask the daemon;
    fail closed on any error during a turn. Every other skill passes with no call."""
    skill = str(inp.get("skill") or "").strip()
    plugin, sep, name = skill.partition(":")
    plugin, name = plugin.strip().lower(), name.strip()
    gated = {n.strip().lower() for n in os.environ.get("AITEAMS_ASK_PLUGINS", "").split(",") if n.strip()}
    if not (sep and plugin in gated):
        return
    url, project, turn = (os.environ.get("AITEAMS_DAEMON_URL"), os.environ.get("AITEAMS_PROJECT"),
                          os.environ.get("AITEAMS_TURN"))
    if not (url and project and turn):
        return  # not a role turn (tests, the Master's own session)
    full = "%s:%s" % (plugin, name)
    reason = ("%s skills need the Master's approval first: request_approval(feature, 'Use skill: %s', why), "
              "end your turn, and use it after the approving Decision." % (plugin, full))
    req = urllib.request.Request(url.rstrip("/") + "/internal/skill-approval?skill=" + urllib.parse.quote(full, safe=""),
                                 headers={"X-AITeams-Project": project, "X-AITeams-Turn": turn,
                                          "X-AITeams-Token": os.environ.get("AITEAMS_TURN_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read().decode() or "{}")
    except Exception as e:  # anything (incl. http.client.BadStatusLine/IncompleteRead) must deny, not crash open
        log_input("skill check failed closed: %r" % e)
        deny(reason + " (approval check failed: the daemon did not answer)")
    if not (isinstance(res, dict) and res.get("ok") is True):
        deny(reason)


ARTIFACT_DENIED = ("the Master manages artifacts on claude.ai: you can publish a page they approved, update it by its url, "
                   "or read a recorded page back. Delete, list, open, pin and sharing are theirs.")
ARTIFACT_PARAMS = {"publish": ("file_path", "url", "title", "description", "icon", "label"), "read": ("url", "page")}  # allowlist


def check_artifact(inp):
    """F-29 Part B: the real Artifact tool is gated. Only `publish` and `read`, only their allowlisted params; the daemon
    decides the rest (a new page needs the Master's approved `Artifact: <file>`, an update must pass the recorded url, a
    read only a recorded url). Fails closed on any error during a turn, like check_skill."""
    action = str(inp.get("action") or "publish").strip().lower()
    allowed = ARTIFACT_PARAMS.get(action)
    if allowed is None:
        deny(ARTIFACT_DENIED)
    extra = [k for k, v in inp.items() if v and k != "action" and k not in allowed]
    if extra:
        deny("the Artifact %s may pass only %s: remove %s." % (action, ", ".join(allowed), ", ".join(extra)))
    url, project, turn = (os.environ.get("AITEAMS_DAEMON_URL"), os.environ.get("AITEAMS_PROJECT"),
                          os.environ.get("AITEAMS_TURN"))
    if not (url and project and turn):
        return  # not a role turn (tests, the Master's own session)
    path = inp.get("file_path")
    path = (path if os.path.isabs(path) else os.path.join(PROJECT, path)) if isinstance(path, str) and path else ""
    query = urllib.parse.urlencode({"action": action, "path": path, "url": str(inp.get("url") or "")})
    req = urllib.request.Request(url.rstrip("/") + "/internal/artifact-check?" + query,
                                 headers={"X-AITeams-Project": project, "X-AITeams-Turn": turn,
                                          "X-AITeams-Token": os.environ.get("AITEAMS_TURN_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read().decode() or "{}")
    except Exception as e:  # anything must deny, not crash open
        log_input("artifact check failed closed: %r" % e)
        deny("the Artifact check failed: the daemon did not answer. Try again, or ask the Master.")
    if not (isinstance(res, dict) and res.get("ok") is True):
        reason = res.get("reason") if isinstance(res, dict) else None
        deny(reason if isinstance(reason, str) and reason else ARTIFACT_DENIED)


def check_read(inp):
    """Read/Grep/Glob: the token and runtime files are off limits (Bash has the same rule)."""
    for key in ("file_path", "path", "pattern"):
        v = inp.get(key)
        if not isinstance(v, str) or not v:
            continue
        rp = os.path.realpath(os.path.expanduser(v))
        base = os.path.realpath(HOME_AITEAMS)
        if rp == os.path.join(base, "token") or rp.startswith(os.path.join(base, "runtime")) or RUNTIME_RE.search(v):
            deny("the AITeams runtime and token are off limits.")


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
    elif tool in ("Read", "Grep", "Glob"):
        check_read(inp)
    elif tool == "Skill":
        check_skill(inp)
    elif tool == "Artifact":
        check_artifact(inp)


if __name__ == "__main__":
    main()
    sys.exit(0)
