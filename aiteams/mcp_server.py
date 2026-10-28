#!/usr/bin/env python3
"""AITeams stdio MCP server (stdlib only; spawned by `claude`, talks to the daemon over HTTP).

Tools: post_message, get_messages, list_conversations, list_features, get_feature, create_feature, set_feature,
ask_master, withdraw_request, start_brainstorm (Product/Manager), request_approval, request_commit, review_commit,
lock_file, unlock_file, list_locks, write_handoff, get_team_status,
run_standup, save_standup, interrupt, checkpoint (Manager only), permission (the --permission-prompt-tool).
"""
import json
import os
import sys
import urllib.error
import urllib.request

DAEMON = os.environ.get("AITEAMS_DAEMON_URL", "http://127.0.0.1:7700").rstrip("/")
PROJECT = os.environ.get("AITEAMS_PROJECT", "")
TURN = os.environ.get("AITEAMS_TURN", "")
TURN_TOKEN = os.environ.get("AITEAMS_TURN_TOKEN", "")
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
         "type": {"type": "string", "enum": ["Message", "Question", "Decision", "Concern", "Finding", "Handoff", "Blocker", "Standup"],
                  "default": "Message"},
         "mentions": {"type": "array", "items": {"type": "string"}, "default": []}},
         "required": ["conversation", "text"]}},
    {"name": "get_messages",
     "description": "Read earlier messages of a conversation (newest last). Use `before` (a message id) to page back.",
     "inputSchema": {"type": "object", "properties": {
         "conversation": {"type": "string"}, "before": {"type": ["integer", "null"]},
         "limit": {"type": "integer", "default": 20}}, "required": ["conversation"]}},
    {"name": "react",
     "description": "Toggle an emoji reaction on a message (calling it again removes it). Never wakes anyone and "
                    "decides nothing; use it instead of a thanks/OK reply.",
     "inputSchema": {"type": "object", "properties": {
         "message_id": {"type": "integer"},
         "emoji": {"type": "string", "enum": ["\U0001F44D", "\u2764\ufe0f", "\U0001F602", "\U0001F389",
                                              "\U0001F440", "\U0001F680", "\u2705", "\u274C"]}},
         "required": ["message_id", "emoji"]}},
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
     "description": "Change a feature's phase (any direction, it is a label), owner, title or Lead. Posts a System "
                    "line. `lead` (lead-1 or lead-2) is the Manager's: it names the one Lead who acts in the feature.",
     "inputSchema": {"type": "object", "properties": {
         "feature": {"type": "string"},
         "phase": {"type": "string", "enum": ["intake", "spec", "design", "build", "qa", "done"]},
         "owner": {"type": ["string", "null"]}, "title": {"type": "string"},
         "lead": {"type": "string", "enum": ["lead-1", "lead-2"]}}, "required": ["feature"]}},
    {"name": "ask_master",
     "description": "Ask the Master 1-6 multiple-choice questions as a clickable card (2-4 options each; the UI "
                    "always adds a free-text Other, so don't). Non-blocking: end your turn after posting; the "
                    "Master's answer arrives as a Decision that wakes you; to pull the card back unanswered, use "
                    "withdraw_request. Put your recommended option first with "
                    "' (Recommended)' in its label. purpose: ask | interview | concern (needs a voice on the "
                    "feature) | a_vs_b (the owner only: Lead, Product or the feature's owner) | brainstorm (Product only, in a brainstorm compile turn). `intro` is a short "
                    "lead-in (your honest take, or both sides' case). Multi-select labels can't contain commas.",
     "inputSchema": {"type": "object", "properties": {
         "questions": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "object", "properties": {
             "question": {"type": "string"}, "header": {"type": "string", "description": "<= 12 chars"},
             "multiSelect": {"type": "boolean", "default": False},
             "options": {"type": "array", "minItems": 2, "maxItems": 4, "items": {"type": "object", "properties": {
                 "label": {"type": "string"}, "description": {"type": "string"}}, "required": ["label"]}}},
             "required": ["question", "header", "options"]}},
         "feature": {"type": ["string", "null"]}, "intro": {"type": "string", "default": ""},
         "purpose": {"type": "string", "enum": ["ask", "interview", "concern", "a_vs_b", "brainstorm"],
                     "default": "ask"}},
         "required": ["questions"]}},
    {"name": "withdraw_request",
     "description": "Pull back a request the Master hasn't decided yet, instead of asking them to reject it: an "
                    "approval (kind 'approval'), a commit request in lead_review or master_review (kind 'commit') or a "
                    "question card (kind 'card'). Allowed for the role that sent it and for the feature's Lead. "
                    "`reason` is one line (no line breaks, max 200 characters) and is shown in chat. It leaves the "
                    "Master's Inbox and stays as 'withdrawn'. Refused when the request is already decided, when its "
                    "commit is being committed right now, or when another open commit request requires it (withdraw "
                    "or re-file that one first).",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["approval", "commit", "card"]}, "id": {"type": "integer"},
         "reason": {"type": "string"}}, "required": ["kind", "id", "reason"]}},
    {"name": "start_brainstorm",
     "description": "Product or Manager only: start a brainstorm round on a feature. Every role with a voice on it "
                    "posts one idea in the feature conversation; then Product compiles them into one card for the "
                    "Master. One active round per feature.",
     "inputSchema": {"type": "object", "properties": {"feature": {"type": "string"}, "topic": {"type": "string"}},
                     "required": ["feature", "topic"]}},
    {"name": "request_approval",
     "description": "Ask the Master for a decision that is theirs (scope lock, scope change, Design, Done, a skill, an "
                    "artifact, a risky change). It lands in their Inbox as a card with three parts, and the daemon "
                    "refuses the request if one is missing: `what` is one line (at most 200 characters) saying what you "
                    "ask them to decide; `if_approved` (at most 300) says what happens next if they say yes; `tradeoffs` "
                    "lists 1 to 3 short items (each at most 200; [\"none\"] is fine) with the costs and risks. `doc` is "
                    "optional: a file in the feature's docs folder (`design.md`, or `.aiteams/features/F-n/design.md`) "
                    "that opens beside the card, so don't repeat it in the brief. End your turn afterwards. `feature` "
                    "may be null (then there is no `doc`).",
     "inputSchema": {"type": "object", "properties": {
         "feature": {"type": ["string", "null"]}, "title": {"type": "string"}, "what": {"type": "string"},
         "if_approved": {"type": "string"},
         "tradeoffs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
         "doc": {"type": ["string", "null"]}},
         "required": ["title", "what", "if_approved", "tradeoffs"]}},
    {"name": "propose_branch",
     "description": "Lead only, for your own feature: propose how it works on git, as a Branch card in the Master's "
                    "Inbox (end your turn afterwards). mode 'new' (a new branch in its own worktree next to the project; "
                    "`branch` defaults to feat/f<n>-<slug>, `base` to origin/main, else main), 'current' (the branch the "
                    "main folder has checked out) or 'shared' (another feature's live worktree: `shared_from` = F-n). "
                    "`why` is one sentence for the Master. The Master may edit the name and base; commit requests are "
                    "refused until the card is approved. A branch that looks shared (main, dev, release...) is only "
                    "warned about, never refused.",
     "inputSchema": {"type": "object", "properties": {
         "feature": {"type": "string"}, "mode": {"type": "string", "enum": ["new", "current", "shared"], "default": "new"},
         "branch": {"type": "string"}, "base": {"type": "string"}, "shared_from": {"type": "string"},
         "why": {"type": "string"}}, "required": ["feature"]}},
    {"name": "request_pr",
     "description": "Lead only, for your own feature, after QA passes: ask the Master to open a pull request for the "
                    "feature's branch, as a PR card in their Inbox (end your turn afterwards). `destination` is the branch "
                    "it goes to (default: the one the feature started from, else main); `title` and `body` are the PR's. "
                    "The card lists the commits the branch has that the destination lacks; the Master may edit all three. "
                    "On approval AITeams pushes the branch to origin and opens the PR on GitHub with `gh`; the Master "
                    "merges it there, and the feature's worktree is removed once it is merged. Refused when the feature "
                    "has no confirmed branch, the branch has no new commits, or it already has an open PR.",
     "inputSchema": {"type": "object", "properties": {
         "feature": {"type": "string"}, "destination": {"type": "string"}, "title": {"type": "string"},
         "body": {"type": "string"}}, "required": ["feature", "title"]}},
    {"name": "request_commit",
     "description": "Request a commit of exactly these files (paths relative to the project root) with a message "
                    "'<type>(<area>): <subject>' (Conventional Commits: feat|fix|docs|test|refactor|chore, no feature id) "
                    "+ body (what, why, how tested). The Lead reviews, then the Master commits. "
                    "Files locked by another role are refused. `requires`: list the open requests this one can't "
                    "land without (e.g. the backend half of a UI change); the Master then commits them together.",
     "inputSchema": {"type": "object", "properties": {
         "feature": {"type": ["string", "null"]}, "files": {"type": "array", "items": {"type": "string"}},
         "message": {"type": "string"}, "requires": {"type": "array", "items": {"type": "integer"}}},
         "required": ["files", "message"]}},
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
    {"name": "write_handoff",
     "description": "Save a handoff note for your future self (what you were doing, exact state, decisions/gotchas, "
                    "next steps, open questions). Called once in a handoff turn; may also be called after finishing a "
                    "big piece of work. Saved to .aiteams/handoffs/<role>/ and posted in your DM.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "get_team_status",
     "description": "One line per role: state, context %, session number, what they are on, pending rotation.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "run_standup",
     "description": "Manager only: start a standup. Roles active since the last one are asked for Done/Next/Blocked "
                    "in #standup; you are woken afterwards to compile the report. Returns 'nothing changed' when idle.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "save_standup",
     "description": "Manager only, during a standup compile turn: save the report (Markdown) and post it to #standup.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "interrupt",
     "description": "Manager only: SIGINT a role's running turn (its wake is dropped; a System line tells the role). "
                    "A role may run in several sessions (a main one and one per feature, e.g. 'F-36'): without `lane` every "
                    "running session of the role is stopped, with `lane` only that one ('' = the main session). "
                    "Use only for clearly wrong or runaway work.",
     "inputSchema": {"type": "object", "properties": {"role": {"type": "string"}, "reason": {"type": "string"},
                                                     "lane": {"type": "string"}},
                     "required": ["role", "reason"]}},
    {"name": "checkpoint",
     "description": "Manager only: retire a role's session. With handoff=true (default) the role first writes a handoff; "
                    "handoff=false restarts it with only the chat digest (use when its memory is the problem).",
     "inputSchema": {"type": "object", "properties": {"role": {"type": "string"}, "reason": {"type": "string"},
                                                     "handoff": {"type": "boolean", "default": True},
                                                     "lane": {"type": "string", "description": "the session: a feature key "
                                                               "such as 'F-36', or '' (default) for the role's main session"}},
                     "required": ["role", "reason"]}},
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
        "Content-Type": "application/json", "X-AITeams-Project": PROJECT, "X-AITeams-Turn": TURN,
        "X-AITeams-Token": TURN_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail")
        except Exception:
            detail = str(e)
        if isinstance(detail, dict) and "guard" in detail:  # F-16: the refusal is the news block itself
            raise RuntimeError(detail["guard"])
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


def tool_react(a):
    res = http("POST", "/internal/reactions", {"message_id": int(a.get("message_id") or 0),
                                               "emoji": str(a.get("emoji") or "")})
    return "%s %s %s #%s" % ("added" if res.get("added") else "removed", a.get("emoji"),
                             "to" if res.get("added") else "from", res.get("message_id"))


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
    body = {k: a[k] for k in ("phase", "owner", "title", "lead") if k in a}
    return http("PATCH", "/internal/features/" + urllib.request.quote(str(a.get("feature") or "")), body)["header"]


def tool_ask_master(a):
    res = http("POST", "/internal/questions", {"questions": a.get("questions") or [], "feature": _feat(a),
                                               "intro": str(a.get("intro") or ""),
                                               "purpose": str(a.get("purpose") or "ask")})
    return "card #%s posted in %s; end your turn, the Master's answer will wake you" % (res["id"],
                                                                                      res["conversation_id"])


def tool_withdraw_request(a):
    res = http("POST", "/internal/withdraw", {"kind": str(a.get("kind") or ""), "id": int(a.get("id") or 0),
                                              "reason": str(a.get("reason") or "")})
    return "%s #%s withdrawn: %s" % (res["kind"], res["id"], res["note"])


def tool_start_brainstorm(a):
    res = http("POST", "/internal/brainstorm", {"feature": str(a.get("feature") or ""), "topic": str(a.get("topic") or "")})
    return "brainstorm #%s on %s started; waiting for %s" % (res["id"], res["feature"], ", ".join(res["roles"]))


def tool_request_approval(a):
    tradeoffs = a.get("tradeoffs")
    if isinstance(tradeoffs, str):  # a lone string is one trade-off, not a refusal
        tradeoffs = [tradeoffs]
    res = http("POST", "/internal/approvals", {"feature": _feat(a), "title": str(a.get("title") or ""),
                                               "what": a.get("what"), "if_approved": a.get("if_approved"),
                                               "tradeoffs": tradeoffs, "doc": a.get("doc")})
    return "approval #%s pending; end your turn, the Master will answer in the Inbox" % res["id"]


def tool_propose_branch(a):
    res = http("POST", "/internal/branches", {"feature": _feat(a), "mode": str(a.get("mode") or "new"),
                                              "branch": a.get("branch"), "base": a.get("base"),
                                              "shared_from": a.get("shared_from"), "why": str(a.get("why") or "")})
    return "Branch card #%s pending; end your turn, the Master will answer in the Inbox" % res["id"]


def tool_request_pr(a):
    res = http("POST", "/internal/prs", {"feature": _feat(a), "destination": a.get("destination"),
                                         "title": str(a.get("title") or ""), "body": str(a.get("body") or "")})
    return "PR card #%s pending; end your turn, the Master will answer in the Inbox" % res["id"]


def tool_request_commit(a):
    res = http("POST", "/internal/commits", {"feature": _feat(a), "files": [str(f) for f in (a.get("files") or [])],
                                             "message": str(a.get("message") or ""),
                                             "requires": [int(n) for n in (a.get("requires") or [])]})
    req = res.get("requires") or []
    return "commit #%s %s (%d files)%s" % (res["id"], res["state"], len(res["files"]),
                                           "; requires " + ", ".join("#%s" % n for n in req) if req else "")


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


def tool_write_handoff(a):
    res = http("POST", "/internal/handoff", {"text": str(a.get("text") or "")})
    return "saved %s" % res["path"]


def tool_get_team_status(_a):
    return "\n".join(http("GET", "/internal/team").get("team") or [])


def tool_run_standup(_a):
    res = http("POST", "/internal/standup")
    if res.get("status") == "nothing_changed":
        return "nothing changed since the last standup; nobody was woken"
    return "standup #%s started; waiting for %s. You will be woken to compile the report." % (res["id"], ", ".join(res["roles"]))


def tool_save_standup(a):
    res = http("POST", "/internal/standup/save", {"text": str(a.get("text") or "")})
    return "saved %s and posted to #standup" % res["path"]


def tool_interrupt(a):
    body = {"role": str(a.get("role") or ""), "reason": str(a.get("reason") or "")}
    if a.get("lane") is not None:  # F-41: left out = every running session of the role
        body["lane"] = str(a["lane"])
    res = http("POST", "/internal/interrupt", body)
    return "interrupted %s" % a.get("role") if res.get("ok") else "%s is not running" % a.get("role")


def tool_checkpoint(a):
    http("POST", "/internal/checkpoint", {"role": str(a.get("role") or ""), "reason": str(a.get("reason") or ""),
                                          "handoff": bool(a.get("handoff", True)), "lane": str(a.get("lane") or "")})
    return "checkpoint requested for %s%s" % (a.get("role"), "" if a.get("handoff", True) else " (no handoff)")


HANDLERS = {"post_message": tool_post_message, "get_messages": tool_get_messages, "react": tool_react,
            "write_handoff": tool_write_handoff, "get_team_status": tool_get_team_status,
            "run_standup": tool_run_standup, "save_standup": tool_save_standup,
            "interrupt": tool_interrupt, "checkpoint": tool_checkpoint,
            "list_conversations": tool_list_conversations, "permission": tool_permission,
            "list_features": tool_list_features, "get_feature": tool_get_feature,
            "create_feature": tool_create_feature, "set_feature": tool_set_feature,
            "request_approval": tool_request_approval, "propose_branch": tool_propose_branch, "request_pr": tool_request_pr,
            "ask_master": tool_ask_master,
            "withdraw_request": tool_withdraw_request, "start_brainstorm": tool_start_brainstorm,
            "request_commit": tool_request_commit,
            "review_commit": tool_review_commit, "lock_file": tool_lock_file,
            "unlock_file": tool_unlock_file, "list_locks": tool_list_locks}


def news(shown=None):
    """F-16: the messages posted since this turn last looked, to append to a tool result. Never fails the tool."""
    q = "/internal/new_messages" + ("?shown=" + urllib.request.quote(str(shown)) if shown else "")
    try:
        text = (http("GET", q, timeout=5.0) or {}).get("text") or ""
    except Exception:
        return ""
    return "\n\n" + text if text else ""


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
        args = args if isinstance(args, dict) else {}
        try:
            out = handler(args)
            text = out if isinstance(out, str) else json.dumps(out)
            if name != "permission":
                text += news(args.get("conversation") if name == "get_messages" else None)
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
