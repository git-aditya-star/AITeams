#!/usr/bin/env python3
"""Minimal stdio MCP server for AITeams M0 probes. Stdlib only, newline-delimited JSON-RPC."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MESSAGES = os.environ.get("AITEAMS_MESSAGES") or os.path.join(HERE, "tmp", "messages.jsonl")
ROLE = os.environ.get("AITEAMS_ROLE", "unknown")

TOOLS = [
    {
        "name": "post_message",
        "description": "Post a message to an AITeams chat conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation": {"type": "string", "description": "Conversation id, e.g. 'F-1'"},
                "text": {"type": "string"},
                "mentions": {"type": "array", "items": {"type": "string"}, "default": []},
                "type": {"type": "string", "default": "Message"},
            },
            "required": ["conversation", "text"],
        },
    },
    {
        "name": "get_messages",
        "description": "Get the last N messages of an AITeams chat conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["conversation"],
        },
    },
]


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def reply(msg_id, result):
    send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def error(msg_id, code, message):
    send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def post_message(args):
    rec = {
        "ts": time.time(),
        "from": ROLE,
        "conversation": args["conversation"],
        "text": args["text"],
        "mentions": args.get("mentions") or [],
        "type": args.get("type") or "Message",
    }
    os.makedirs(os.path.dirname(MESSAGES), exist_ok=True)
    with open(MESSAGES, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return "posted to %s as %s" % (rec["conversation"], ROLE)


def get_messages(args):
    conv = args["conversation"]
    limit = int(args.get("limit") or 20)
    rows = []
    if os.path.exists(MESSAGES):
        with open(MESSAGES) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("conversation") == conv:
                    rows.append(rec)
    return json.dumps(rows[-limit:], indent=1)


HANDLERS = {"post_message": post_message, "get_messages": get_messages}


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
        if not isinstance(msg, dict) or "method" not in msg:
            continue  # a response or garbage; ignore
        method = msg["method"]
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            reply(msg_id, {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aiteams-chat", "version": "0.0.1"},
            })
        elif method.startswith("notifications/"):
            continue
        elif method == "ping":
            reply(msg_id, {})
        elif method == "tools/list":
            reply(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name")
            handler = HANDLERS.get(name)
            if handler is None:
                error(msg_id, -32602, "Unknown tool: %s" % name)
                continue
            try:
                text = handler(params.get("arguments") or {})
                reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
            except Exception as e:  # tool errors are results, not protocol errors
                reply(msg_id, {"content": [{"type": "text", "text": "error: %s" % e}], "isError": True})
        else:
            if msg_id is not None:
                error(msg_id, -32601, "Method not found: %s" % method)


if __name__ == "__main__":
    main()
