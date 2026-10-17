#!/usr/bin/env python3
"""M0 runtime probes for AITeams (throwaway harness, see SPEC.md). Run: python3 -B spikes/m0/run_probes.py"""
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
M0 = os.path.join(ROOT, "spikes", "m0")
TMP = os.path.join(M0, "tmp")
REPO = os.path.join(TMP, "repo")
MESSAGES = os.path.join(TMP, "messages.jsonl")
HOOK_LOG = os.path.join(TMP, "hook_input.json")
HOOK = os.path.join(M0, "hooks", "deny_git.py")
MCP_SERVER = os.path.join(M0, "mcp_chat_server.py")
RESULTS = os.path.join(M0, "RESULTS.md")
PROBE_TIMEOUT = 120

HOOK_SETTINGS = {"hooks": {"PreToolUse": [{"matcher": "Bash|Edit|Write", "hooks": [
    {"type": "command", "command": "python3 -B %s" % HOOK}]}]}}

# Flags that worked for "our settings + MCP" (P2 decides; P3/P8 reuse). Default per spec.
P2_FLAGS = {"safe_mode": True, "extra_flags": []}


def make_env(role):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.update({
        "AITEAMS_ROLE": role,
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
        "AITEAMS_PROBE": "1",
        "AITEAMS_ALLOWED_DIR": REPO,
        "AITEAMS_HOOK_LOG": HOOK_LOG,
        "AITEAMS_MESSAGES": MESSAGES,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def mcp_config(role):
    return {"mcpServers": {"aiteams": {
        "command": "python3", "args": ["-B", MCP_SERVER],
        "env": {"AITEAMS_ROLE": role, "AITEAMS_MESSAGES": MESSAGES, "PYTHONDONTWRITEBYTECODE": "1"}}}}


def build_cmd(role, session_id=None, resume=None, extra_flags=(), settings=None, mcp=True,
              bypass=True, safe_mode=True, model="haiku", system_prompt=None):
    cmd = ["claude", "-p", "--output-format", "stream-json", "--input-format", "stream-json",
           "--verbose", "--include-partial-messages", "--include-hook-events"]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    if safe_mode:
        cmd.append("--safe-mode")
    if bypass:
        cmd.append("--dangerously-skip-permissions")
    if model:
        cmd += ["--model", model]
    if session_id:
        cmd += ["--session-id", session_id]
    if resume:
        cmd += ["--resume", resume]
    if settings is not None:
        cmd += ["--settings", json.dumps(settings)]
    if mcp:
        cmd += ["--mcp-config", json.dumps(mcp_config(role)), "--strict-mcp-config"]
    cmd += list(extra_flags)
    cmd += ["--disallowedTools", "SendMessage", "ListAgents", "--max-turns", "15"]
    return cmd


def shell_quote(cmd):
    out = []
    for a in cmd:
        out.append("'" + a.replace("'", "'\\''") + "'" if re.search(r"[\s'\"{}]", a) else a)
    return " ".join(out)


class Session:
    """One `claude -p` process with stream-json stdin/stdout; may carry several turns."""

    def __init__(self, role, **kw):
        self.role = role
        self.cmd = build_cmd(role, **kw)
        self.events = []
        self.stderr = []
        self.q = queue.Queue()
        self.proc = subprocess.Popen(self.cmd, cwd=REPO, env=make_env(role), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read_out(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                ev = {"type": "_raw", "text": line[:500]}
            self.q.put(ev)
        self.q.put(None)

    def _read_err(self):
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def send(self, text):
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self.stderr.append("stdin write failed: %s" % e)

    def wait_result(self, timeout=PROBE_TIMEOUT):
        """Collect events until a `result` event. Returns (turn_events, result_or_None, status)."""
        deadline = time.time() + timeout
        turn = []
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return turn, None, "timeout"
            try:
                ev = self.q.get(timeout=remaining)
            except queue.Empty:
                return turn, None, "timeout"
            if ev is None:
                return turn, None, "eof"
            self.events.append(ev)
            turn.append(ev)
            if ev.get("type") == "result":
                return turn, ev, "ok"

    def drain(self, timeout=0.5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ev = self.q.get(timeout=max(0.01, deadline - time.time()))
            except queue.Empty:
                break
            if ev is None:
                break
            self.events.append(ev)

    def close_stdin(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def finish(self, timeout=15):
        """Close stdin, wait for exit; escalate SIGINT -> SIGKILL on hang. Returns returncode."""
        self.close_stdin()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill_hard()
        self.drain(1.0)
        return self.proc.returncode

    def kill_hard(self):
        try:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except OSError:
            pass


def spawn(role, prompt, timeout=PROBE_TIMEOUT, **kw):
    """One-shot: start process, send prompt, wait for result, close. Returns a run dict."""
    s = Session(role, **kw)
    s.send(prompt)
    turn, result, status = s.wait_result(timeout)
    if status == "timeout":
        s.kill_hard()
        status = "timeout-killed"
    rc = s.finish()
    return {"cmd": shell_quote(s.cmd), "events": s.events, "result": result, "status": status,
            "returncode": rc, "stderr": s.stderr, "text": result_text(result), "session": s}


def result_text(result):
    if not result:
        return ""
    t = result.get("result")
    if isinstance(t, str):
        return t
    return json.dumps(result.get("result") or result.get("errors") or result.get("subtype"))


def init_event(events):
    for ev in events:
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            return ev
    return {}


def hook_events(events):
    return [ev for ev in events if "hook" in str(ev.get("type", "")).lower()
            or "hook" in str(ev.get("subtype", "")).lower()]


def hook_names(events):
    names = {}
    for ev in hook_events(events):
        n = ev.get("hook_name") or ev.get("subtype")
        names[n] = names.get(n, 0) + 1
    return names


LANG_Q = ("Does your context include a document that starts with the heading '# Global preferences' and has a "
          "section 'Response language (ask first, every chat)'? Answer with only YES or NO.")


def leak_check(label, **kw):
    """Ask the P1 question under the given flags; returns (leaks_user_claude_md, run)."""
    run = spawn("probe", LANG_Q, timeout=90, **kw)
    text = run["text"]
    leak = re.search(r"\bYES\b", text) is not None
    return leak, run


def trunc(s, n=220):
    s = str(s).replace("\n", "\\n")
    return s if len(s) <= n else s[:n] + "…"


def flatten(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", json.dumps(b)) if isinstance(b, dict) else str(b) for b in content)
    return json.dumps(content)


def excerpt(events, max_lines=40):
    lines = []
    for ev in events:
        t = ev.get("type")
        if t == "stream_event":
            continue
        if t == "system":
            if ev.get("subtype") == "init":
                lines.append("system/init: model=%s permissionMode=%s apiKeySource=%s mcp_servers=%s tools=%d" % (
                    ev.get("model"), ev.get("permissionMode"), ev.get("apiKeySource"),
                    json.dumps(ev.get("mcp_servers")), len(ev.get("tools") or [])))
            else:
                rest = {k: v for k, v in ev.items() if k not in ("type", "subtype", "session_id", "uuid")}
                lines.append("system/%s: %s" % (ev.get("subtype"), trunc(json.dumps(rest))))
        elif t == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text":
                    lines.append("assistant.text: " + trunc(b.get("text")))
                elif b.get("type") == "tool_use":
                    lines.append("assistant.tool_use %s: %s" % (b.get("name"), trunc(json.dumps(b.get("input")))))
        elif t == "user":
            content = (ev.get("message") or {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tag = " [error]" if b.get("is_error") else ""
                        lines.append("user.tool_result%s: %s" % (tag, trunc(flatten(b.get("content")))))
        elif t == "result":
            lines.append("result: subtype=%s is_error=%s num_turns=%s session_id=%s total_cost_usd=%s" % (
                ev.get("subtype"), ev.get("is_error"), ev.get("num_turns"), ev.get("session_id"),
                ev.get("total_cost_usd")))
            lines.append("result.text: " + trunc(result_text(ev), 400))
        else:
            lines.append("%s: %s" % (t, trunc(json.dumps(ev))))
    if len(lines) > max_lines:
        half = max_lines // 2
        lines = lines[:half] + ["... (%d lines omitted)" % (len(lines) - max_lines)] + lines[-half:]
    return lines


def read_messages(conversation=None):
    rows = []
    if os.path.exists(MESSAGES):
        with open(MESSAGES) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if conversation is None or rec.get("conversation") == conversation:
                        rows.append(rec)
    return rows


def git_count():
    out = subprocess.run(["git", "-C", REPO, "rev-list", "--count", "HEAD"], capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def run_block(run, title="Run"):
    md = ["**%s command:**" % title, "```", run["cmd"], "```",
          "status=%s returncode=%s" % (run["status"], run["returncode"]), "", "```"]
    md += excerpt(run["events"])
    md.append("```")
    if run["stderr"]:
        md += ["stderr (tail):", "```"] + [trunc(l) for l in run["stderr"][-8:]] + ["```"]
    return md


# ---------------------------------------------------------------- probes

def p1():
    prompt = ("Answer two questions on separate lines. 1) Do your instructions contain any rule about "
              "asking which language (Hinglish/English) to respond in? Answer YES or NO. 2) Reply with the word OK.")
    run = spawn("probe", prompt, safe_mode=True, mcp=False)
    init = init_event(run["events"])
    tools = init.get("tools") or []
    mcp_tools = [t for t in tools if t.startswith("mcp__")]
    hooks = hook_events(run["events"])
    text = run["text"]
    hn = hook_names(run["events"])
    has_no = re.search(r"\bNO\b", text) is not None and re.search(r"\bYES\b", text) is None
    has_ok = re.search(r"\bOK\b", text) is not None
    ok = bool(run["result"]) and has_no and has_ok and not mcp_tools and not hooks
    only_policy = bool(hn) and all(str(k).startswith("SessionStart") for k in hn)
    notes = ("--safe-mode; CLAUDE.md language rule visible=%s; apiKeySource=%s permissionMode=%s; mcp__ tools=%d; "
             "hook events=%d %s%s; user Stop hook (notify.sh) event seen=%s"
             % (not has_no, init.get("apiKeySource"), init.get("permissionMode"), len(mcp_tools), len(hooks),
                json.dumps(hn), " (all org-policy SessionStart hooks from ~/.claude/remote-settings.json; not user hooks)" if only_policy else "",
                any("Stop" in str(k) for k in hn)))
    extra = ["macOS notification: not observable by the harness. notify.sh is a Stop hook in ~/.claude/settings.json; "
             "with --include-hook-events a Stop hook would emit hook_started/hook_response, and none appeared (%s), "
             "so it did not run. Ask the user only if they saw a 'Claude is done' banner during the probes."
             % ("no Stop events" if not any("Stop" in str(k) for k in hn) else "Stop events SEEN"),
             "init.tools (%d): %s" % (len(tools), ", ".join(tools)),
             "init.mcp_servers: %s" % json.dumps(init.get("mcp_servers")),
             "init keys: %s" % ", ".join(sorted(init.keys()))]
    return {"id": "P1", "title": "safe-mode isolation (same login)", "ok": ok, "notes": notes,
            "md": extra + [""] + run_block(run), "answer": text}


def p2():
    global P2_FLAGS
    prompt = ("Call the tool mcp__aiteams__post_message with conversation 'probe', text 'hello from P2'. "
              "Then run the bash command `git commit --allow-empty -m probe` in the current directory. "
              "Report what happened.")

    def attempt(label, safe_mode, extra_flags):
        before = git_count()
        n_before = len(read_messages("probe"))
        kw = dict(safe_mode=safe_mode, extra_flags=extra_flags, settings=HOOK_SETTINGS, mcp=True)
        run = spawn("probe", prompt, **kw)
        after = git_count()
        init = init_event(run["events"])
        has_mcp_tool = "mcp__aiteams__post_message" in (init.get("tools") or [])
        posted = any(m.get("text") == "hello from P2" for m in read_messages("probe")[n_before:])
        denied = "AITEAMS_DENY" in json.dumps(run["events"])
        info = {"label": label, "kw": kw, "run": run, "has_mcp_tool": has_mcp_tool, "mcp_servers": init.get("mcp_servers"),
                "posted": posted, "denied": denied, "commits_before": before, "commits_after": after,
                "hooks": hook_names(run["events"]), "ok": posted and denied and after == before, "leak": None, "leak_run": None}
        if info["ok"]:
            info["leak"], info["leak_run"] = leak_check(label, **kw)
        return info

    def block(a):
        out = ["**%s**" % a["label"],
               "mcp tool in init=%s mcp_servers=%s posted=%s denied=%s commits %d->%d hooks=%s; user CLAUDE.md leaks=%s" % (
                   a["has_mcp_tool"], json.dumps(a["mcp_servers"]), a["posted"], a["denied"], a["commits_before"],
                   a["commits_after"], json.dumps(a["hooks"]), a["leak"]), ""] + run_block(a["run"], a["label"])
        if a["leak_run"]:
            out += ["", "Isolation check (asked whether the user's '# Global preferences' CLAUDE.md is in context): answer=%s" % trunc(a["leak_run"]["text"], 100),
                    "```"] + excerpt(a["leak_run"]["events"], 10) + ["```"]
        return out

    combos = [("Attempt 1: --safe-mode + --settings + --mcp-config", True, []),
              ("Attempt 2 (fallback): no --safe-mode, --setting-sources project", False, ["--setting-sources", "project"]),
              ("Attempt 3: no --safe-mode, --setting-sources local", False, ["--setting-sources", "local"])]
    md, notes_parts, chosen, functional = [], [], None, None
    for label, sm, ef in combos:
        a = attempt(label, sm, ef)
        md += block(a) + [""]
        notes_parts.append("[%s] mcp=%s deny=%s posted=%s leak=%s" % (label.split(":")[0], a["has_mcp_tool"], a["denied"], a["posted"], a["leak"]))
        if a["ok"] and functional is None:
            functional = a
        if a["ok"] and a["leak"] is False:
            chosen = a
            break
    if chosen is None and functional is not None:
        chosen = functional
        base = functional["kw"]
        for label, kw in [("--system-prompt replaces default", dict(base, system_prompt="You are a coding agent in a headless harness. Follow the user's instructions exactly.")),
                          ("--bare", dict(base, extra_flags=list(base["extra_flags"]) + ["--bare"]))]:
            vleak, vr = leak_check(label, **kw)
            md += ["**Variant: %s** leak=%s rc=%s answer=%s" % (label, vleak, vr["returncode"], trunc(vr["text"], 80)), "```"] + excerpt(vr["events"], 8) + ["```", ""]
            notes_parts.append("variant[%s]: leak=%s rc=%s" % (label, vleak, vr["returncode"]))
    if chosen is not None:
        P2_FLAGS = {"safe_mode": chosen["kw"]["safe_mode"], "extra_flags": list(chosen["kw"]["extra_flags"])}
        notes_parts.append("CHOSEN for P3/P8: %s (leak=%s)" % (chosen["label"].split(":")[-1].strip(), chosen["leak"]))
    else:
        notes_parts.append("no combination works; remaining option CLAUDE_CONFIG_DIR + setup-token (not tried)")
    return {"id": "P2", "title": "our settings and MCP survive safe-mode", "ok": chosen is not None, "notes": "; ".join(notes_parts),
            "md": md, "answer": chosen["run"]["text"] if chosen else ""}


def p3():
    outside = os.path.join(TMP, "outside.txt")
    notes_md = os.path.join(REPO, "notes.md")
    prompt = ("Create the file `%s` with content X using the Write tool, then create `notes.md` in the current "
              "directory with content Y." % outside)
    run = spawn("probe", prompt, settings=HOOK_SETTINGS, mcp=True, bypass=True, **P2_FLAGS)
    outside_exists = os.path.exists(outside)
    notes_exists = os.path.exists(notes_md)
    ok = (not outside_exists) and notes_exists
    hook_in = []
    if os.path.exists(HOOK_LOG):
        with open(HOOK_LOG) as f:
            hook_in = [l.strip() for l in f if l.strip()]
    sample = ""
    for l in hook_in:
        if '"Write"' in l and "outside.txt" in l:
            sample = l
    sample = sample or (hook_in[-1] if hook_in else "(none)")
    try:
        sample = json.dumps(json.loads(sample), indent=1)
    except ValueError:
        pass
    notes = "outside.txt exists=%s repo/notes.md exists=%s; hook stdin records=%d; flags=%s" % (
        outside_exists, notes_exists, len(hook_in), "--safe-mode" if P2_FLAGS["safe_mode"] else " ".join(P2_FLAGS["extra_flags"]) or "no isolation flag")
    md = ["Hook stdin JSON seen (one record, from tmp/hook_input.json):", "```json", sample, "```", ""] + run_block(run)
    return {"id": "P3", "title": "hooks still deny under bypass; path deny", "ok": ok, "notes": notes,
            "md": md, "answer": run["text"]}


def p4():
    prompt = ("Publish a minimal HTML page titled 'AITeams M0 probe' with the text 'hello' as an artifact "
              "and reply with its URL.")
    url_re = re.compile(r"https?://claude\.ai/(?:code/)?artifact/[\w-]+")

    def attempt(model):
        run = spawn("probe", prompt, safe_mode=True, mcp=False, model=model)
        tools = init_event(run["events"]).get("tools") or []
        art_tools = [t for t in tools if re.search(r"artifact|publish", t, re.I)]
        hay = [run["text"]]
        for ev in run["events"]:
            if ev.get("type") == "user":
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        hay.append(flatten(b.get("content")))
        m = url_re.search("\n".join(hay))
        return run, art_tools, (m.group(0) if m else None)

    md = []
    run, art_tools, url = attempt("haiku")
    md += ["**Attempt 1 (haiku):** artifact-like tools in init: %s; URL: %s" % (art_tools or "none", url), ""]
    md += run_block(run, "Attempt 1")
    notes = "haiku: tools=%s url=%s" % (",".join(art_tools) or "none", url)
    if not url:
        run2, art_tools2, url2 = attempt(None)
        md += ["", "**Attempt 2 (default model):** artifact-like tools in init: %s; URL: %s" % (art_tools2 or "none", url2), ""]
        md += run_block(run2, "Attempt 2")
        notes += "; default model: tools=%s url=%s" % (",".join(art_tools2) or "none", url2)
        if url2:
            url, run = url2, run2
    if url:
        notes += "; DELETE LATER: %s" % url
    stray = []
    for ev in run["events"]:
        for b in (ev.get("message") or {}).get("content") or [] if ev.get("type") == "assistant" else []:
            if b.get("type") == "tool_use" and b.get("name") == "Write":
                fp = (b.get("input") or {}).get("file_path") or ""
                if fp and not os.path.realpath(fp).startswith(REPO) and os.path.exists(fp):
                    os.remove(fp)
                    stray.append(fp)
    if stray:
        notes += "; removed stray files written outside the temp repo: %s" % ", ".join(stray)
    return {"id": "P4", "title": "Artifact tool available headless", "ok": bool(url), "notes": notes,
            "md": md, "answer": run["text"]}


P5_STATE = {}


def p5():
    sid = str(uuid.uuid4())
    md = []
    # (a) two turns in one process
    s = Session("probe", session_id=sid, safe_mode=True, mcp=False)
    s.send("Remember the codeword PINEAPPLE. Reply OK.")
    t1, r1, st1 = s.wait_result()
    t2 = r2 = None
    st2 = "skipped"
    if r1:
        s.send("What is the codeword?")
        t2, r2, st2 = s.wait_result()
    if st1 == "timeout" or st2 == "timeout":
        s.kill_hard()
    rc = s.finish()
    a_ok = bool(r1 and r2) and "PINEAPPLE" in result_text(r2).upper()
    P5_STATE["events"] = s.events
    P5_STATE["result"] = r2 or r1
    md += ["**(a) two turns, one process** session_id=%s; turn1 status=%s turn2 status=%s rc=%s; "
           "result.session_id turn1=%s turn2=%s" % (sid, st1, st2, rc, (r1 or {}).get("session_id"), (r2 or {}).get("session_id")),
           "```", shell_quote(s.cmd), "```", "```"] + excerpt(s.events) + ["```"]
    if s.stderr:
        md += ["stderr:", "```"] + [trunc(l) for l in s.stderr[-6:]] + ["```"]
    # (b) resume in a new process
    rb = spawn("probe", "What is the codeword?", resume=sid, safe_mode=True, mcp=False)
    b_ok = "PINEAPPLE" in rb["text"].upper()
    md += ["", "**(b) --resume in a new process** result.session_id=%s" % (rb["result"] or {}).get("session_id"), ""]
    md += run_block(rb, "(b)")
    # (c) does --session-id need to be fresh? reuse the same id
    rc_run = spawn("probe", "Reply OK.", timeout=60, session_id=sid, safe_mode=True, mcp=False)
    md += ["", "**(c) reuse of the same --session-id (expect refusal)** status=%s rc=%s result=%s" % (
        rc_run["status"], rc_run["returncode"], trunc(rc_run["text"], 200)), "```"]
    md += [trunc(l) for l in rc_run["stderr"][-6:]] or ["(no stderr)"]
    md += ["```"]
    notes = "(a) multi-turn=%s (b) --resume + stream-json=%s; result.session_id==uuid: %s; reusing --session-id: %s" % (
        a_ok, b_ok, (r1 or {}).get("session_id") == sid,
        "refused (rc=%s)" % rc_run["returncode"] if not rc_run["result"] or rc_run["result"].get("is_error") else "accepted")
    return {"id": "P5", "title": "resume with stream-json input; multiple turns per process", "ok": a_ok and b_ok,
            "notes": notes, "md": md, "answer": "(a) %s | (b) %s" % (trunc(result_text(r2), 200), trunc(rb["text"], 200))}


def p6():
    prompt = "Run `sleep 2` with Bash 30 times, one call at a time, saying 'tick N' after each."
    md = []

    def interrupted_run(sig, label):
        sid = str(uuid.uuid4())
        s = Session("probe", session_id=sid, safe_mode=True, mcp=False)
        s.send(prompt)
        time.sleep(6)
        s.proc.send_signal(sig)
        t0 = time.time()
        turn, result, status = s.wait_result(timeout=10)
        exited = None
        try:
            s.proc.wait(timeout=max(0.1, 10 - (time.time() - t0)))
            exited = "exited on signal alone in %.1fs" % (time.time() - t0)
        except subprocess.TimeoutExpired:
            s.close_stdin()
            try:
                s.proc.wait(timeout=5)
                exited = "exited only after stdin close (%.1fs after signal)" % (time.time() - t0)
            except subprocess.TimeoutExpired:
                s.kill_hard()
                exited = "did not exit; killed"
        s.drain(1.0)
        rc = s.proc.returncode
        ticks = len([e for e in s.events if e.get("type") == "assistant"
                     and any(b.get("type") == "tool_use" for b in (e.get("message") or {}).get("content") or [])])
        last = s.events[-1].get("type") if s.events else None
        md.extend(["**%s** session_id=%s; result event after signal=%s (status=%s); %s; exit code=%s; "
                   "last stream event type=%s; tool calls seen=%d" % (
                       label, sid, bool(result), status, exited, rc, last, ticks),
                   "```", shell_quote(s.cmd), "```", "```"] + excerpt(s.events, 24) + ["```"])
        if s.stderr:
            md.extend(["stderr:", "```"] + [trunc(l) for l in s.stderr[-6:]] + ["```"])
        rr = spawn("probe", "How many ticks did you complete? Answer with just the number.", resume=sid,
                   safe_mode=True, mcp=False)
        md.extend(["", "resume after %s: status=%s is_error=%s text=%s" % (
            label, rr["status"], (rr["result"] or {}).get("is_error"), trunc(rr["text"], 200)), "```"]
                  + excerpt(rr["events"], 12) + ["```"])
        if rr["stderr"]:
            md.extend(["stderr:", "```"] + [trunc(l) for l in rr["stderr"][-4:]] + ["```"])
        return {"result": result, "exited": exited, "rc": rc, "last": last, "ticks": ticks,
                "resume_ok": bool(rr["result"]) and not (rr["result"] or {}).get("is_error"),
                "resume_number": re.search(r"\b\d+\b", rr["text"]) is not None, "resume_text": rr["text"]}

    a = interrupted_run(signal.SIGINT, "SIGINT after 6 s")
    md.append("")
    b = interrupted_run(signal.SIGTERM, "SIGTERM after 6 s (second run)")
    exit_ok = a["exited"] is not None and a["exited"].startswith("exited on signal alone")
    ok = exit_ok and (a["result"] is not None or a["last"] in ("result", "assistant", "user")) and a["resume_number"]
    notes = "SIGINT: %s, rc=%s, result event=%s, resume answered number=%s | SIGTERM: %s, rc=%s, result event=%s, resume ok=%s" % (
        a["exited"], a["rc"], a["result"] is not None, a["resume_number"],
        b["exited"], b["rc"], b["result"] is not None, b["resume_ok"])
    return {"id": "P6", "title": "interrupt semantics", "ok": ok, "notes": notes, "md": md,
            "answer": "SIGINT resume: %s | SIGTERM resume: %s" % (trunc(a["resume_text"], 150), trunc(b["resume_text"], 150))}


def p7():
    events = P5_STATE.get("events") or []
    result = P5_STATE.get("result")
    last_assistant = None
    for ev in events:
        if ev.get("type") == "assistant":
            last_assistant = ev
    usage = ((last_assistant or {}).get("message") or {}).get("usage") or {}
    need = ["input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]
    have = {k: usage.get(k) for k in need}
    ok = all(usage.get(k) is not None for k in need)
    ctx = sum(int(usage.get(k) or 0) for k in need) + int(usage.get("output_tokens") or 0)
    md = ["Last assistant `message.usage`:", "```json", json.dumps(usage, indent=1), "```",
          "Context-size estimate (input + cache_read + cache_creation + output) = %d tokens" % ctx, "",
          "result fields: usage=%s modelUsage=%s total_cost_usd=%s num_turns=%s" % (
              "present" if (result or {}).get("usage") else "missing",
              "present" if (result or {}).get("modelUsage") else "missing",
              (result or {}).get("total_cost_usd"), (result or {}).get("num_turns")),
          "", "Full `result` event JSON (from P5):", "```json", json.dumps(result, indent=1), "```"]
    notes = "last assistant usage: %s; result.num_turns=%s total_cost_usd=%s" % (
        json.dumps(have), (result or {}).get("num_turns"), (result or {}).get("total_cost_usd"))
    return {"id": "P7", "title": "context meter fields", "ok": ok and result is not None, "notes": notes, "md": md,
            "answer": "ctx estimate %d tokens" % ctx}


def p8():
    md = []
    sid_a = str(uuid.uuid4())
    flags = dict(P2_FLAGS)
    # 1. manager
    r1 = spawn("manager", "You are the Manager. Post a message to conversation 'F-1' with mentions ['backend'] "
               "asking Backend to list the files in the repo. Use the post_message tool. Then stop.",
               session_id=sid_a, settings=HOOK_SETTINGS, mcp=True, **flags)
    md += ["**1. manager (session A=%s)**" % sid_a, ""] + run_block(r1, "1")
    msgs = [m for m in read_messages("F-1") if m.get("from") == "manager" and "backend" in (m.get("mentions") or [])]
    if not msgs:
        notes = "manager never posted an F-1 message mentioning backend"
        return {"id": "P8", "title": "two-role exchange through MCP", "ok": False, "notes": notes, "md": md,
                "answer": r1["text"]}
    ask = msgs[-1]["text"]
    # 2. backend
    r2 = spawn("backend", "New message in F-1 from manager: %s. Do what it asks, then reply with post_message "
               "to 'F-1' mentioning ['manager']." % ask, settings=HOOK_SETTINGS, mcp=True, **flags)
    md += ["", "**2. backend (session B=%s)**" % (r2["result"] or {}).get("session_id"), ""] + run_block(r2, "2")
    replies = [m for m in read_messages("F-1") if m.get("from") == "backend"]
    if not replies:
        notes = "backend never posted a reply to F-1"
        return {"id": "P8", "title": "two-role exchange through MCP", "ok": False, "notes": notes, "md": md,
                "answer": r2["text"]}
    reply = replies[-1]["text"]
    # 3. manager again via --resume
    r3 = spawn("manager", "New message in F-1 from backend: %s. Acknowledge it by posting a short reply to 'F-1' "
               "with post_message mentioning ['backend'], then stop." % reply,
               resume=sid_a, settings=HOOK_SETTINGS, mcp=True, **flags)
    md += ["", "**3. manager resumed (--resume A)**", ""] + run_block(r3, "3")
    f1 = read_messages("F-1")
    order = [m.get("from") for m in f1]
    files_in_repo = [fn for fn in os.listdir(REPO) if not fn.startswith(".")]
    backend_idx = next((i for i, m in enumerate(f1) if m.get("from") == "backend"), None)
    acks = [m for i, m in enumerate(f1) if backend_idx is not None and i > backend_idx and m.get("from") == "manager"]
    ack_text = " ".join(m.get("text", "") for m in acks).lower()
    acknowledges = bool(acks) and bool(r3["result"]) and not (r3["result"] or {}).get("is_error") and any(
        fn.lower() in ack_text for fn in files_in_repo)
    ok = len(f1) == 3 and order == ["manager", "backend", "manager"] and acknowledges
    md += ["", "messages.jsonl (conversation F-1):", "```json"] + [json.dumps(m) for m in f1] + ["```"]
    notes = "F-1 messages=%d order=%s manager's 3rd message names a repo file=%s" % (len(f1), "→".join(order), acknowledges)
    return {"id": "P8", "title": "two-role exchange through MCP", "ok": ok, "notes": notes, "md": md,
            "answer": r3["text"]}


CONSEQUENCES = {
    "P1": ("`--safe-mode` isolates role agents from the user's ~/.claude config on the same login (Runtime / isolation).",
           "the hook events are org-policy hooks from ~/.claude/remote-settings.json (PostHog telemetry) that no flag disables, "
           "and safe-mode also drops --settings/--mcp-config (P2 attempt 1) — so use the combination P2 chose; "
           "last resort CLAUDE_CONFIG_DIR + `claude setup-token` (needs a login)."),
    "P2": ("Our `--settings` hooks and `--mcp-config` chat server can be combined with an isolation flag (Orchestrator architecture).",
           "Run without `--safe-mode` and pass `--setting-sources project` (tried in-run if attempt 1 failed); else CLAUDE_CONFIG_DIR."),
    "P3": ("PreToolUse hooks enforce no-commit and path confinement even under `--dangerously-skip-permissions` (agents never commit; file locks).",
           "Drop bypass and route permissions via `--permission-prompt-tool`, or enforce in the orchestrator (git pre-commit hook in the working copy)."),
    "P4": ("Every role has full Claude Code power incl. the Artifact tool headless (Agent prompts and team-rules).",
           "Artifacts only from the Master's interactive session; roles write HTML files the UI serves."),
    "P5": ("Spawn-per-turn `claude -p --resume <id>` with stream-json stdin/stdout (Orchestrator architecture).",
           "Use `--input-format text` with the prompt as argv, or keep one long-lived process per role (previously rejected)."),
    "P6": ("Master can interrupt a turn with SIGINT and the session stays resumable (Master messages / interrupt).",
           "No interrupt: queue Master messages until the turn ends (already the default); SIGTERM loses the turn."),
    "P7": ("Context meter from the last assistant step's usage fields drives rotation at 60% (Rotation).",
           "Estimate context from the transcript JSONL size under ~/.claude/projects, or from result.usage."),
    "P8": ("The whole loop works: MCP chat tools + @mention wake + `--resume` (Orchestrator is a dumb pipe).",
           "Debug individual pieces (P2/P5) first; if MCP tool calls are unreliable on haiku, use sonnet for roles."),
}


def write_results(rows, started, notes_extra):
    lines = ["# M0 runtime probe results", "",
             "Generated %s by `spikes/m0/run_probes.py` (claude %s, python %s). Total wall time %.0f s." % (
                 time.strftime("%Y-%m-%d %H:%M:%S"), CLAUDE_VERSION, sys.version.split()[0], time.time() - started),
             "", "| Probe | Result | Notes (flags that worked, fallback used) |", "|---|---|---|"]
    for r in rows:
        lines.append("| %s %s | %s | %s |" % (r["id"], r["title"], "PASS" if r["ok"] else "FAIL",
                                              r["notes"].replace("|", "\\|")))
    lines += ["", "Common flags on every spawn: `claude -p --output-format stream-json --input-format stream-json "
              "--verbose --include-partial-messages --include-hook-events [--safe-mode] --dangerously-skip-permissions --model haiku "
              "--disallowedTools SendMessage ListAgents --max-turns 15`; env minus ANTHROPIC_API_KEY, plus "
              "AITEAMS_ROLE, ENABLE_CLAUDEAI_MCP_SERVERS=false; cwd `spikes/m0/tmp/repo`.",
              "", "stream-json user message shape that worked: "
              "`{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"...\"}]}}` + newline.",
              ""] + notes_extra
    for r in rows:
        lines += ["", "### %s — %s: %s" % (r["id"], r["title"], "PASS" if r["ok"] else "FAIL"), ""]
        lines += r["md"]
        lines += ["", "**Final answer text:** %s" % trunc(r.get("answer", ""), 600)]
    lines += ["", "## Design consequences", ""]
    for r in rows:
        assumption, fallback = CONSEQUENCES[r["id"]]
        if r["ok"]:
            lines.append("- %s PASS — holds: %s Observed: %s" % (r["id"], assumption, r["notes"]))
        else:
            lines.append("- %s FAIL — breaks: %s Fallback: %s Observed: %s" % (r["id"], assumption, fallback, r["notes"]))
    with open(RESULTS, "w") as f:
        f.write("\n".join(lines) + "\n")


CLAUDE_VERSION = "?"


def setup():
    global CLAUDE_VERSION
    if os.path.exists(TMP):
        shutil.rmtree(TMP)
    os.makedirs(REPO)
    CLAUDE_VERSION = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "init", "-q", REPO], check=True)
    subprocess.run(["git", "-C", REPO, "config", "user.email", "m0@aiteams.local"], check=True)
    subprocess.run(["git", "-C", REPO, "config", "user.name", "m0"], check=True)
    with open(os.path.join(REPO, "README.md"), "w") as f:
        f.write("# m0 temp repo\n")
    with open(os.path.join(REPO, "hello.py"), "w") as f:
        f.write("print('hello')\n")
    subprocess.run(["git", "-C", REPO, "add", "."], check=True)
    subprocess.run(["git", "-C", REPO, "commit", "-q", "-m", "init"], check=True)


def cleanup():
    if os.path.exists(TMP):
        shutil.rmtree(TMP)
    for dirpath, dirnames, _ in os.walk(M0):
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d))


def main():
    started = time.time()
    setup()
    rows = []
    try:
        run_all(rows)
    except BaseException:
        cleanup()
        raise
    finish(rows, started)


def run_all(rows):
    for fn in (p1, p2, p3, p4, p5, p6, p7, p8):
        t0 = time.time()
        pid = fn.__name__.upper()
        print("running %s ..." % pid, flush=True)
        try:
            r = fn()
        except Exception as e:  # a harness bug must still produce a row, never a missing one
            import traceback
            r = {"id": pid, "title": "harness error", "ok": False, "notes": "harness exception: %r" % e,
                 "md": ["```", traceback.format_exc(), "```"], "answer": ""}
        r["notes"] += " (%.0f s)" % (time.time() - t0)
        rows.append(r)
        print("  %s %s — %s" % (r["id"], "PASS" if r["ok"] else "FAIL", r["notes"]), flush=True)


def finish(rows, started):
    notes_extra = ["Timeouts: the spec's 120 s applies per `claude` process (spawn/turn), with SIGINT→SIGKILL "
                   "escalation; probes that need several processes (P2, P5, P6, P8) take longer in total."]
    try:
        write_results(rows, started, notes_extra)
    finally:
        cleanup()
    print()
    print("| Probe | Result | Notes |")
    print("|---|---|---|")
    for r in rows:
        print("| %s %s | %s | %s |" % (r["id"], r["title"], "PASS" if r["ok"] else "FAIL", r["notes"]))
    print("\nwrote %s" % RESULTS)


if __name__ == "__main__":
    main()
