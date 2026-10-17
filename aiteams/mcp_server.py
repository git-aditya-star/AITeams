#!/usr/bin/env python3
"""AITeams stdio MCP server (stdlib only; spawned by `claude`, talks to the daemon over HTTP).

Tools: post_message, get_messages, list_conversations, list_features, get_feature, create_feature, set_feature,
request_approval, request_commit, review_commit, lock_file, unlock_file, list_locks,
permission (the --permission-prompt-tool).
"""
import json
import os
import sys
import urllib.error
import urllib.request

DAEMON = os.environ.get("AITEAMS_DAEMON_URL", "http://127.0.0.1:7700").rstrip("/")
PROJECT = os.environ.get("AITEAMS_PROJECT", "")
TURN = os.environ.get("AITEAMS_TURN", "")
ROLE = os.environ.get("AITEAMS_ROLE", "unknown")
ASK_TIMEOUT = float(os.environ.get("AITEAMS_ASK_TIMEOUT_S", "600"))

TOOLS = [
    {"name": "post_message",
     "description": "Post a message to a team conversation as yourself (%s). `conversation` is an id "
                    "(e.g. 'general', 'dm-manager') or an exact title. Keep it short and typed; "
                    "@mention only who must act." % ROLE,
     "inputSchema": {"type": "object", "properties": {
         "conversation": {"type": "string"},
         "text": {"type": "string"},
         "type": {"type": "string", "enum": ["Message", "Question", "Decision", "Concern", "Finding", "Handoff", "Blocker"],
                  "default": "Message"},
         "mentions": {"type": "array", "items": {"type": "string"}, "default": []}},
         "required": ["conversation", "text"]}},
    {"name": "get_messages",
     "description": "Read earlier messages of a conversation (newest last). Use `before` (a message id) to page back.",
     "inputSchema": {"type": "object", "properties": {
         "conversation": {"type": "string"}, "before": {"type": ["integer", "null"]},
         "limit": {"type": "integer", "default": 20}}, "required": ["conversation"]}},
    {"name": "list_conversations", "description": "List conversations: ids, titles, kinds.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_features",
     "description": "List features (F-<n>, phase, owner, title, conversation id). include_history adds done/archived.",
     "inputSchema": {"type": "object", "properties": {"include_history": {"type": "boolean", "default": False}}}},
    {"name": "get_feature",
     "description": "Feature header: phase, owner, docs (with last editor and time), open approvals, commits, locks.",
     "inputSchema": {"type": "object", "properties": {"feature": {"type": "string"}}, "required": ["feature"]}},
    {"name": "create_feature",
     "description": "Create a feature (its conversation and docs folder). Returns F-<n>. Then @mention the role that "
                    "should start, in its conversation.",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "phase": {"type": "string", "enum": ["intake", "spec", "design", "build", "qa", "done"], "default": "intake"},
         "owner": {"type": ["string", "null"]}}, "required": ["title"]}},
    {"name": "set_feature",
     "description": "Change a feature's phase (any direction, it is a label), owner or title. Posts a System line.",
     "inputSchema": {"type": "object", "properties": {
         "feature": {"type": "string"},
         "phase": {"type": "string", "enum": ["intake", "spec", "design", "build", "qa", "done"]},
         "owner": {"type": ["string", "null"]}, "title": {"type": "string"}}, "required": ["feature"]}},
    {"name": "request_approval",
     "description": "Ask the Master for a decision that is theirs (scope lock, scope change, Done, risky change). "
                    "It lands in their Inbox; end your turn afterwards. `feature` may be null.",
     "inputSchema": {"type": "object", "properties": {
         "feature": {"type": ["string", "null"]}, "title": {"type": "string"}, "summary": {"type": "string"}},
         "required": ["title", "summary"]}},
    {"name": "request_commit",
     "description": "Request a commit of exactly these files (paths relative to the project root) with a message "
                    "'F-<n>: <subject>' + body (what, why, how tested). The Lead reviews, then the Master commits. "
                    "Files locked by another role are refused.",
     "inputSchema": {"type": "object", "properties": {
         "feature": {"type": ["string", "null"]}, "files": {"type": "array", "items": {"type": "string"}},
         "message": {"type": "string"}}, "required": ["files", "message"]}},
    {"name": "review_commit",
     "description": "Lead only: approve or reject a commit request after reading its diff and running the tests.",
     "inputSchema": {"type": "object", "properties": {
         "commit": {"type": "integer"}, "decision": {"type": "string", "enum": ["approve", "reject"]},
         "note": {"type": "string", "default": ""}}, "required": ["commit", "decision"]}},
    {"name": "lock_file",
     "description": "Lock a file for the rest of your turn (edits lock automatically; use this to pre-claim).",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "unlock_file",
     "description": "Release your lock on a file. Lead/Manager may pass force=true to break another role's lock.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string"}, "force": {"type": "boolean", "default": False}}, "required": ["path"]}},
    {"name": "list_locks", "description": "Files locked right now and by whom.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "permission",
     "description": "Permission prompt tool used by Claude Code (internal).",
     "inputSchema": {"type": "object", "properties": {
         "tool_name": {"type": "string"}, "input": {"type": "object"}, "tool_use_id": {"type": "string"}},
         "required": ["tool_name", "input"]}},
]


def http(method, path, body=None, timeout=30.0):
    url = DAEMON + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "X-AITeams-Project": PROJECT, "X-AITeams-Turn": TURN})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail")
        except Exception:
            detail = str(e)
        raise RuntimeError("daemon %s %s -> %s: %s" % (method, path, e.code, detail))
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError("daemon unreachable (%s): %s" % (url, e))


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def reply(msg_id, result):
    send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def error(msg_id, code, message):
    send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def tool_post_message(a):
    res = http("POST", "/internal/messages", {
        "conversation": str(a.get("conversation") or ""), "text": str(a.get("text") or ""),
        "type": a.get("type") or "Message", "mentions": list(a.get("mentions") or [])})
    return "posted #%s" % res.get("id")


def tool_get_messages(a):
    q = "/internal/messages?conversation=%s&limit=%d" % (urllib.request.quote(str(a.get("conversation") or "")),
                                                         int(a.get("limit") or 20))
    if a.get("before"):
        q += "&before=%d" % int(a["before"])
    return json.dumps(http("GET", q), indent=1)


def tool_list_conversations(_a):
    return json.dumps(http("GET", "/internal/conversations"), indent=1)


def tool_permission(a):
    tool = a.get("tool_name") or ""
    inp = a.get("input") if isinstance(a.get("input"), dict) else {}
    if tool != "AskUserQuestion":
        return {"behavior": "allow", "updatedInput": inp}
    try:
        return http("POST", "/internal/permission",
                    {"tool_name": tool, "input": inp, "tool_use_id": a.get("tool_use_id")}, timeout=ASK_TIMEOUT + 60)
    except RuntimeError as e:
        return {"behavior": "deny", "message": "AITeams could not reach the Master (%s). Post the question with "
                                                "post_message(type='Question') and end your turn." % e}


def _feat(a):
    f = a.get("feature")
    return None if f in (None, "", "null") else str(f)


def tool_list_features(a):
    hist = "1" if a.get("include_history") else "0"
    res = http("GET", "/internal/features?history=" + hist)
    return "\n".join(res.get("features") or []) or "no features yet"


def tool_get_feature(a):
    return http("GET", "/internal/features/" + urllib.request.quote(str(a.get("feature") or "")))["header"]


def tool_create_feature(a):
    res = http("POST", "/internal/features", {"title": str(a.get("title") or ""), "phase": a.get("phase") or "intake",
                                              "owner": a.get("owner") or None})
    return "created %s (conversation id %s)" % (res["key"], res["conversation_id"])


def tool_set_feature(a):
    body = {k: a[k] for k in ("phase", "owner", "title") if k in a}
    return http("PATCH", "/internal/features/" + urllib.request.quote(str(a.get("feature") or "")), body)["header"]


def tool_request_approval(a):
    res = http("POST", "/internal/approvals", {"feature": _feat(a), "title": str(a.get("title") or ""),
                                               "summary": str(a.get("summary") or "")})
    return "approval #%s pending; end your turn, the Master will answer in the Inbox" % res["id"]


def tool_request_commit(a):
    res = http("POST", "/internal/commits", {"feature": _feat(a), "files": [str(f) for f in (a.get("files") or [])],
                                             "message": str(a.get("message") or "")})
    return "commit #%s %s (%d files)" % (res["id"], res["state"], len(res["files"]))


def tool_review_commit(a):
    res = http("POST", "/internal/commits/%d/review" % int(a.get("commit") or 0),
               {"decision": str(a.get("decision") or ""), "note": str(a.get("note") or "")})
    return "commit #%s is now %s" % (res["id"], res["state"])


def tool_lock_file(a):
    res = http("POST", "/internal/locks/acquire", {"path": str(a.get("path") or "")})
    if res.get("ok"):
        return "locked %s for this turn" % res["path"]
    return "%s is locked by %s" % (res.get("path"), res.get("holder"))


def tool_unlock_file(a):
    res = http("POST", "/internal/locks/release", {"path": str(a.get("path") or ""), "force": bool(a.get("force"))})
    return ("released %s" if res.get("released") else "%s was not locked") % res["path"]


def tool_list_locks(_a):
    locks = http("GET", "/internal/locks")
    return "\n".join("%s (%s)" % (l["path"], l["role"]) for l in locks) or "no locks"


HANDLERS = {"post_message": tool_post_message, "get_messages": tool_get_messages,
            "list_conversations": tool_list_conversations, "permission": tool_permission,
            "list_features": tool_list_features, "get_feature": tool_get_feature,
            "create_feature": tool_create_feature, "set_feature": tool_set_feature,
            "request_approval": tool_request_approval, "request_commit": tool_request_commit,
            "review_commit": tool_review_commit, "lock_file": tool_lock_file,
            "unlock_file": tool_unlock_file, "list_locks": tool_list_locks}


def handle(msg):
    if not isinstance(msg, dict) or "method" not in msg:
        return
    method, msg_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        reply(msg_id, {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                       "capabilities": {"tools": {}}, "serverInfo": {"name": "aiteams", "version": "0.1.0"}})
    elif method.startswith("notifications/"):
        return
    elif method == "ping":
        reply(msg_id, {})
    elif method == "tools/list":
        reply(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            error(msg_id, -32602, "Unknown tool: %s" % name)
            return
        args = params.get("arguments")
        try:
            out = handler(args if isinstance(args, dict) else {})
            text = out if isinstance(out, str) else json.dumps(out)
            reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:
            reply(msg_id, {"content": [{"type": "text", "text": "error: %s" % e}], "isError": True})
    elif msg_id is not None:
        error(msg_id, -32601, "Method not found: %s" % method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            error(None, -32700, "Parse error")
            continue
        try:
            handle(msg)
        except Exception as e:
            error(msg.get("id") if isinstance(msg, dict) else None, -32603, "Internal error: %s" % e)


if __name__ == "__main__":
    main()
