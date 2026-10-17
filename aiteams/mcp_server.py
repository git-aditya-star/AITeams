#!/usr/bin/env python3
"""AITeams stdio MCP server (stdlib only; spawned by `claude`, talks to the daemon over HTTP).

Tools: post_message, get_messages, list_conversations, permission (the --permission-prompt-tool).
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
         "type": {"type": "string", "enum": ["Message", "Question", "Decision", "Concern", "Finding", "Handoff"],
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


HANDLERS = {"post_message": tool_post_message, "get_messages": tool_get_messages,
            "list_conversations": tool_list_conversations, "permission": tool_permission}


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
