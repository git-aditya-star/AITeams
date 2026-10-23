"""Every deny rule of the PreToolUse hook, plus an allowed Write inside the project."""
import json
import os
import subprocess
import sys

import pytest

from tests.conftest import ROOT

HOOK = os.path.join(ROOT, "aiteams", "hooks", "pretooluse.py")


def run_hook(repo, home, tool, tool_input):
    env = {**os.environ, "AITEAMS_PROJECT_PATH": str(repo), "AITEAMS_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    data = {"session_id": "s", "cwd": str(repo), "tool_name": tool, "tool_input": tool_input, "tool_use_id": "t"}
    out = subprocess.run([sys.executable, "-B", HOOK], input=json.dumps(data), capture_output=True, text=True,
                         env=env, cwd=str(repo), timeout=10)
    assert out.returncode == 0, out.stderr
    if not out.stdout.strip():
        return None
    return json.loads(out.stdout)["hookSpecificOutput"]


def denied(res):
    return res is not None and res["permissionDecision"] == "deny" and res["permissionDecisionReason"].startswith("AITEAMS_DENY")


@pytest.mark.parametrize("cmd", [
    "git commit -m x", "git commit --allow-empty -m test", "cd sub && git push origin main", "git reset --hard HEAD~1",
    "git clean -fd", "git -C . commit -m y",
])
def test_bash_git_denied(repo, home, cmd):
    assert denied(run_hook(repo, home, "Bash", {"command": cmd}))


def test_bash_rm_rf_outside_denied_inside_allowed(repo, home, tmp_path):
    assert denied(run_hook(repo, home, "Bash", {"command": "rm -rf %s" % (tmp_path / "elsewhere")}))
    assert denied(run_hook(repo, home, "Bash", {"command": "rm -fr ~/Documents"}))
    assert run_hook(repo, home, "Bash", {"command": "rm -rf build/"}) is None


def test_bash_claude_dir_and_state_db_denied(repo, home):
    assert denied(run_hook(repo, home, "Bash", {"command": "cat ~/.claude/settings.json"}))
    assert denied(run_hook(repo, home, "Bash", {"command": "ls $HOME/.claude"}))
    assert denied(run_hook(repo, home, "Bash", {"command": "sqlite3 .aiteams/state.db .tables"}))


def test_bash_kill_daemon_denied(repo, home):
    (home / "daemon.json").write_text(json.dumps({"pid": 4242, "port": 7700}))
    assert denied(run_hook(repo, home, "Bash", {"command": "kill 4242"}))
    assert denied(run_hook(repo, home, "Bash", {"command": "pkill -f aiteams"}))
    assert run_hook(repo, home, "Bash", {"command": "kill 999"}) is None


def test_bash_normal_allowed_and_logged(repo, home):
    assert run_hook(repo, home, "Bash", {"command": "git status && ls -la"}) is None
    log = (repo / ".aiteams" / "hooks.log").read_text().splitlines()
    assert any("git status" in l for l in log)


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_write_outside_project_denied(repo, home, tmp_path, tool):
    assert denied(run_hook(repo, home, tool, {"file_path": str(tmp_path / "other.txt")}))
    assert denied(run_hook(repo, home, tool, {"file_path": "../escape.txt"}))


def test_write_under_aiteams(repo, home):
    assert denied(run_hook(repo, home, "Write", {"file_path": ".aiteams/state.db"}))
    assert denied(run_hook(repo, home, "Write", {"file_path": str(repo / ".aiteams" / "prompts" / "x.md")}))
    assert run_hook(repo, home, "Write", {"file_path": ".aiteams/roles/qa.md"}) is None
    assert denied(run_hook(repo, home, "Write", {"file_path": ".aiteams/notes/handoff.md"}))  # retired in F-4


def test_notebook_edit_and_allowed_write(repo, home):
    assert denied(run_hook(repo, home, "NotebookEdit", {"notebook_path": "/tmp/x.ipynb"}))
    assert run_hook(repo, home, "Write", {"file_path": "src/app.py"}) is None
    assert run_hook(repo, home, "Write", {"file_path": str(repo / "DB_CHOICE.md")}) is None


def test_bad_input_never_crashes(repo, home):
    env = {**os.environ, "AITEAMS_PROJECT_PATH": str(repo), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, "-B", HOOK], input="not json", capture_output=True, text=True, env=env)
    assert out.returncode == 0 and out.stdout == ""


# ---- M1b: lock check against a stub daemon -------------------------------------
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Stub(BaseHTTPRequestHandler):
    calls = []
    response = {"ok": True}
    get_reply = None  # (status, raw body) for GET, else `response` as JSON

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        _Stub.calls.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        data = json.dumps(_Stub.response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        _Stub.calls.append((self.path, {k.lower(): v for k, v in self.headers.items()}, None))
        status, data = _Stub.get_reply if _Stub.get_reply else (200, json.dumps(_Stub.response).encode())
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub():
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    _Stub.calls.clear()
    _Stub.response = {"ok": True}
    _Stub.get_reply = None
    yield "http://127.0.0.1:%d" % srv.server_port
    srv.shutdown()


def run_hook_env(repo, home, tool, tool_input, extra_env):
    env = {**os.environ, "AITEAMS_PROJECT_PATH": str(repo), "AITEAMS_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1", **extra_env}
    data = {"session_id": "s", "cwd": str(repo), "tool_name": tool, "tool_input": tool_input, "tool_use_id": "t"}
    out = subprocess.run([sys.executable, "-B", HOOK], input=json.dumps(data), capture_output=True, text=True,
                         env=env, cwd=str(repo), timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["hookSpecificOutput"] if out.stdout.strip() else None


def test_lock_allow_and_deny(repo, home, stub):
    env = {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "turn-secret"}
    assert run_hook_env(repo, home, "Edit", {"file_path": str(repo / "src" / "app.py")}, env) is None
    path, headers, body = _Stub.calls[-1]
    assert path == "/internal/locks/acquire" and body == {"path": "src/app.py"}
    assert headers["x-aiteams-project"] == "p1" and headers["x-aiteams-turn"] == "7"
    assert headers["x-aiteams-token"] == "turn-secret"
    _Stub.response = {"ok": False, "holder": "backend", "since": 1_700_000_000}
    res = run_hook_env(repo, home, "Write", {"file_path": "src/app.py"}, env)
    assert denied(res) and "src/app.py is locked by backend since" in res["permissionDecisionReason"]
    # Bash is not lock-checked; writes outside the project are denied before any HTTP call
    n = len(_Stub.calls)
    assert run_hook_env(repo, home, "Bash", {"command": "sed -i '' s/a/b/ src/app.py"}, env) is None
    assert denied(run_hook_env(repo, home, "Write", {"file_path": "/tmp/x"}, env))
    assert len(_Stub.calls) == n


GATED = {"AITEAMS_ASK_PLUGINS": "team-skills"}  # F-23: what a turn's env carries when the Master marked team-skills


def test_ask_me_first_gate(repo, home, stub):
    """F-23: `<plugin>:<skill>` of a plugin in AITEAMS_ASK_PLUGINS asks the daemon (URL-encoded); everything else, and
    every skill while the list is empty, passes with no call."""
    env = {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "tok", **GATED}
    for skill in ("code-review", "project-notes:project-notes", "teamskills:x", "team-skills-extra:x", "team-skills"):
        assert run_hook_env(repo, home, "Skill", {"skill": skill}, env) is None, skill
    assert run_hook_env(repo, home, "Skill", {"skill": "update-config", "args": "team-skills:x"}, env) is None
    nothing_gated = {**env, "AITEAMS_ASK_PLUGINS": ""}
    assert run_hook_env(repo, home, "Skill", {"skill": "team-skills:local-preview"}, nothing_gated) is None  # empty list
    assert _Stub.calls == []
    _Stub.response = {"ok": False}
    for skill in ("team-skills:local-preview", "Team-Skills:local-preview", "  team-skills: local-preview "):
        res = run_hook_env(repo, home, "Skill", {"skill": skill}, env)
        assert denied(res) and "team-skills skills need" in res["permissionDecisionReason"], skill
        assert "'Use skill: team-skills:local-preview'" in res["permissionDecisionReason"], skill
    assert [c[0] for c in _Stub.calls] == ["/internal/skill-approval?skill=team-skills%3Alocal-preview"] * 3
    assert _Stub.calls[0][1]["x-aiteams-turn"] == "7" and _Stub.calls[0][1]["x-aiteams-token"] == "tok"
    _Stub.response = {"ok": True}
    assert run_hook_env(repo, home, "Skill", {"skill": "team-skills:local-preview"}, env) is None
    run_hook_env(repo, home, "Skill", {"skill": "team-skills:a b/c&d"}, env)
    assert _Stub.calls[-1][0] == "/internal/skill-approval?skill=team-skills%3Aa%20b%2Fc%26d"


def test_ask_me_first_gate_any_plugin_and_several(repo, home, stub):
    """F-23: any plugin can be gated; the env list is comma-separated and matched case-insensitively."""
    env = {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "tok",
           "AITEAMS_ASK_PLUGINS": "project-notes, Acme"}
    _Stub.response = {"ok": False}
    res = run_hook_env(repo, home, "Skill", {"skill": "acme:deploy"}, env)
    assert denied(res) and "'Use skill: acme:deploy'" in res["permissionDecisionReason"]
    assert denied(run_hook_env(repo, home, "Skill", {"skill": "project-notes:project-notes"}, env))
    assert run_hook_env(repo, home, "Skill", {"skill": "team-skills:local-preview"}, env) is None  # not in the list
    assert [c[0] for c in _Stub.calls] == ["/internal/skill-approval?skill=acme%3Adeploy",
                                           "/internal/skill-approval?skill=project-notes%3Aproject-notes"]


@pytest.mark.parametrize("reply", [(500, b'{"ok": true}'), (200, b"not json"), (200, b"{}"), (200, b'{"ok": "yes"}'),
                                   (200, b"[true]")])
def test_ask_me_first_gate_fails_closed_on_bad_answers(repo, home, stub, reply):
    _Stub.get_reply = reply
    env = {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "tok", **GATED}
    assert denied(run_hook_env(repo, home, "Skill", {"skill": "team-skills:local-preview"}, env))


@pytest.mark.parametrize("raw", [b"NOT-HTTP garbage\r\n\r\n",  # BadStatusLine
                                 b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n{\"ok\": true}"])  # IncompleteRead
def test_ask_me_first_gate_fails_closed_on_broken_http(repo, home, raw):
    """QA finding on #20: http.client errors are not OSError; the hook must deny, not crash (a crash runs the tool)."""
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(raw)
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    env = {"AITEAMS_DAEMON_URL": "http://127.0.0.1:%d" % srv.getsockname()[1], "AITEAMS_PROJECT": "p1",
           "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "tok", **GATED}
    try:
        res = run_hook_env(repo, home, "Skill", {"skill": "team-skills:local-preview"}, env)  # asserts exit 0
    finally:
        srv.close()
    assert denied(res) and "did not answer" in res["permissionDecisionReason"]


def test_ask_me_first_gate_fails_closed_without_daemon_but_not_outside_a_turn(repo, home):
    env = {"AITEAMS_DAEMON_URL": "http://127.0.0.1:1", "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", **GATED}
    res = run_hook_env(repo, home, "Skill", {"skill": "team-skills:local-preview"}, env)
    assert denied(res) and "did not answer" in res["permissionDecisionReason"]
    assert run_hook_env(repo, home, "Skill", {"skill": "team-skills:local-preview"}, GATED) is None  # no turn env


def test_lock_check_fails_open_without_daemon(repo, home):
    env = {"AITEAMS_DAEMON_URL": "http://127.0.0.1:1", "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7"}
    assert run_hook_env(repo, home, "Write", {"file_path": "src/app.py"}, env) is None
    assert any("lock check skipped" in l for l in (repo / ".aiteams" / "hooks.log").read_text().splitlines())


def test_tests_never_reach_the_live_daemon(repo, home):
    """F-2: the turn's daemon vars are stripped in conftest, so a hook run from tests makes no HTTP call."""
    from tests.conftest import TURN_ENV
    assert [k for k in TURN_ENV if k in os.environ] == []
    env = {**os.environ, "AITEAMS_PROJECT_PATH": str(repo), "AITEAMS_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    # run the hook with urlopen replaced by a tripwire: any daemon request exits 3
    script = ("import runpy, sys, urllib.request\n"
              "def trip(req, *a, **k):\n"
              "    sys.stderr.write('daemon contacted: ' + str(getattr(req, 'full_url', req))); sys.exit(3)\n"
              "urllib.request.urlopen = trip\n"
              "runpy.run_path(%r, run_name='__main__')\n" % HOOK)
    for tool, inp in (("Write", {"file_path": "src/app.py"}), ("Edit", {"file_path": ".aiteams/features/F-1/prd.md"})):
        data = {"session_id": "s", "cwd": str(repo), "tool_name": tool, "tool_input": inp, "tool_use_id": "t"}
        out = subprocess.run([sys.executable, "-B", "-c", script], input=json.dumps(data), capture_output=True,
                             text=True, env=env, cwd=str(repo), timeout=10)
        assert out.returncode == 0 and out.stdout == "", out.stderr


def test_project_notes_writable_without_lock_generated_files_denied(repo, home, stub):
    """F-4: topic notes skip the turn lock (Stop hook vs another role's lock); plugin-generated files are denied."""
    env = {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "t"}
    (repo / ".project-notes" / "sub").mkdir(parents=True)
    for tool, p in (("Write", ".project-notes/foo.md"), ("Edit", str(repo / ".project-notes" / "workflow.md")),
                    ("Write", "./.project-notes/../.project-notes/x.md"), ("Write", ".project-notes/index-notes.md"),
                    ("MultiEdit", ".project-notes/sub/deep.md")):
        assert run_hook_env(repo, home, tool, {"file_path": p}, env) is None, p
    assert _Stub.calls == []  # no lock request for notes
    for tool, p in (("Write", ".project-notes/INDEX.md"), ("Write", "./.project-notes/INDEX.md"),
                    ("Edit", ".project-notes/sub/../INDEX.md"), ("Write", str(repo / ".project-notes" / "INDEX.md")),
                    ("Edit", ".project-notes/index.md"), ("Write", ".Project-Notes/Index.md"),
                    ("Write", ".project-notes/.metrics/x"), ("Write", ".project-notes/.state/x"),
                    ("Write", ".project-notes/.backups/foo.md"), ("Write", ".project-notes/.METRICS/x"),
                    ("NotebookEdit", ".project-notes/.state/n.ipynb")):
        res = run_hook_env(repo, home, tool, {"file_path": p, "notebook_path": p}, env)
        assert denied(res) and "generated by the project-notes plugin" in res["permissionDecisionReason"], p
    assert _Stub.calls == []
    # an ordinary project file is still lock-checked
    assert run_hook_env(repo, home, "Write", {"file_path": "src/app.py"}, env) is None
    assert [c[0] for c in _Stub.calls] == ["/internal/locks/acquire"]


def test_project_notes_score_file_allowed_rest_of_metrics_denied(repo, home, stub):
    """F-19: the plugin's Stop hook asks for one Write of .metrics/pending-<session>.json with {id, score, comment};
    that file passes (no lock), a wrong tool or content on it is denied, and the rest of .metrics stays denied."""
    env = {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "t"}
    sid = "ca9ced07-1a75-4783-81b6-19669f1a4b35"
    rec = json.dumps({"id": sid + "-mvcugpqg", "score": 3, "comment": "pointed at the seed gotcha"})
    for p in (f".project-notes/.metrics/pending-{sid}.json", str(repo / ".project-notes" / ".metrics" / "pending-AB-1.json")):
        assert run_hook_env(repo, home, "Write", {"file_path": p, "content": rec}, env) is None, p
    assert _Stub.calls == []
    score = f".project-notes/.metrics/pending-{sid}.json"
    for tool, inp in (("Edit", {"file_path": score, "old_string": "3", "new_string": "9"}),
                      ("Write", {"file_path": score, "content": "not json"}),
                      ("Write", {"file_path": score, "content": "[1, 2]"}),
                      ("Write", {"file_path": score, "content": "{}"}),
                      ("Write", {"file_path": score, "content": json.dumps({"score": 3, "t": "turn"})})):
        res = run_hook_env(repo, home, tool, inp, env)
        assert denied(res) and "score file takes one Write" in res["permissionDecisionReason"], (tool, inp)
    for p in (".project-notes/.metrics/events.jsonl", ".project-notes/.metrics/data.js",
              ".project-notes/.metrics/dashboard.html", ".project-notes/.metrics/pending-x.jsonl",
              ".project-notes/.metrics/sub/pending-x.json", ".project-notes/.metrics/pending-../events.jsonl",
              ".project-notes/.state/pending-x.json", ".project-notes/.backups/pending-x.json",
              ".project-notes/pending-x.json/../INDEX.md"):
        res = run_hook_env(repo, home, "Write", {"file_path": p, "content": rec}, env)
        assert denied(res) and "generated by the project-notes plugin" in res["permissionDecisionReason"], p


def test_write_under_features_allowed(repo, home):
    assert run_hook(repo, home, "Write", {"file_path": ".aiteams/features/F-1/prd.md"}) is None
    assert denied(run_hook(repo, home, "Write", {"file_path": ".aiteams/prompts/x.md"}))


# ---- M2a: the install, the daemon's HTTP port and the token/runtime files are the Master's -----------------
@pytest.mark.parametrize("cmd,reason", [
    ("aiteams upgrade", "the Master runs aiteams upgrade"),
    ("cd /x && aiteams stop", "the Master runs aiteams upgrade"),
    ("aiteams token --link", "the Master runs aiteams upgrade"),
    ("echo $(aiteams token)", "the Master runs aiteams upgrade"),
    ("uv tool install .", "the Master runs aiteams upgrade"),
    ("ls; uv tool list", "the Master runs aiteams upgrade"),
    ("curl -s 127.0.0.1:7700/api/health", "MCP tools, not HTTP"),
    ("curl http://localhost:7700/api/projects", "MCP tools, not HTTP"),
    ('curl "$AITEAMS_DAEMON_URL/api/health"', "MCP tools, not HTTP"),
    ("curl ${AITEAMS_DAEMON_URL}/internal/messages", "MCP tools, not HTTP"),
    ("cat ~/.aiteams/token", "runtime and token are off limits"),
    ("cat $HOME/.aiteams/token", "runtime and token are off limits"),
    ("cat /Users/aditya/.aiteams/runtime.json", "runtime and token are off limits"),
    ("ls ~/.aiteams/runtime/", "runtime and token are off limits"),
])
def test_bash_m2a_denied(repo, home, cmd, reason):
    (home / "daemon.json").write_text(json.dumps({"pid": 4242, "port": 7700}))
    res = run_hook(repo, home, "Bash", {"command": cmd})
    assert denied(res) and reason in res["permissionDecisionReason"], (cmd, res)


@pytest.mark.parametrize("cmd", [
    'grep "aiteams upgrade" README.md', "rg 'uv tool' docs/", "curl https://example.com", "cat ~/.aiteams/settings.json",
    "grep -r AITEAMS_DAEMON README.md", "ls .aiteams/features",
])
def test_bash_m2a_allowed(repo, home, cmd):
    (home / "daemon.json").write_text(json.dumps({"pid": 4242, "port": 7700}))
    assert run_hook(repo, home, "Bash", {"command": cmd}) is None, cmd


def test_bash_port_from_daemon_json_or_default(repo, home):
    (home / "daemon.json").write_text(json.dumps({"pid": 4242, "port": 7811}))
    assert denied(run_hook(repo, home, "Bash", {"command": "curl 127.0.0.1:7811/api/health"}))
    assert run_hook(repo, home, "Bash", {"command": "curl 127.0.0.1:7700/api/health"}) is None
    (home / "daemon.json").unlink()
    assert denied(run_hook(repo, home, "Bash", {"command": "curl 127.0.0.1:7700/api/health"}))  # default port


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "~/.aiteams/token"}),
    ("Read", {"file_path": "$HOME/.aiteams/runtime.json"}),
    ("Grep", {"pattern": "sha", "path": "~/.aiteams/runtime"}),
    ("Glob", {"pattern": "~/.aiteams/runtime/**/*.py"}),
])
def test_read_tools_cannot_see_token_or_runtime(repo, home, tool, inp):
    # the hook resolves ~ against AITEAMS_HOME's real location too
    if "~/" in json.dumps(inp) or "$HOME" in json.dumps(inp):
        inp = {k: v.replace("~/.aiteams", str(home)).replace("$HOME/.aiteams", str(home)) for k, v in inp.items()}
    res = run_hook(repo, home, tool, inp)
    assert denied(res) and "runtime and token are off limits" in res["permissionDecisionReason"], (tool, inp)


def test_read_tools_normal_paths_allowed(repo, home):
    assert run_hook(repo, home, "Read", {"file_path": str(repo / "README.md")}) is None
    assert run_hook(repo, home, "Read", {"file_path": str(home / "settings.json")}) is None
    assert run_hook(repo, home, "Grep", {"pattern": "aiteams upgrade", "path": str(repo)}) is None
    assert run_hook(repo, home, "Glob", {"pattern": "**/*.md"}) is None


# ---- F-29 Part B: the Artifact tool is gated ----------------------------------------------------------------------
def artifact_env(stub):
    return {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7", "AITEAMS_TURN_TOKEN": "tok"}


@pytest.mark.parametrize("inp", [
    {"action": "delete", "url": "https://claude.ai/artifact/a"}, {"action": "list"}, {"action": "open", "url": "u"},
    {"action": "pin"}, {"action": "unpin"}, {"action": "quickstart"}, {"action": "DELETE"}, {"action": " Share "}])
def test_artifact_hook_denies_the_masters_actions_without_asking_the_daemon(repo, home, stub, inp):
    res = run_hook_env(repo, home, "Artifact", inp, artifact_env(stub))
    assert denied(res) and "the Master manages artifacts on claude.ai" in res["permissionDecisionReason"], inp
    assert _Stub.calls == []
    assert denied(run_hook(repo, home, "Artifact", inp))  # the static rules hold outside a role turn too


@pytest.mark.parametrize("inp,keys", [
    ({"action": "publish", "file_path": "a.html", "type_url": "https://x"}, ["type_url"]),
    ({"file_path": "a.html", "from_url": "https://x"}, ["from_url"]),
    ({"file_path": "a.html", "files": ["a.html", "b.html"]}, ["files"]), ({"file_paths": ["a.html"]}, ["file_paths"]),
    ({"file_path": "a.html", "paths": ["b.html"]}, ["paths"]), ({"file_path": "a.html", "root": "/"}, ["root"]),
    ({"file_path": "a.html", "path": "b.html"}, ["path"]), ({"file_path": "a.html", "asset_ids": ["x"]}, ["asset_ids"]),
    ({"file_path": "a.html", "asset": "x"}, ["asset"]), ({"file_path": "a.html", "out_dir": "/tmp"}, ["out_dir"]),
    ({"file_path": "a.html", "capabilities": ["data"]}, ["capabilities"]), ({"file_path": "a.html", "contract": {"a": 1}}, ["contract"]),
    ({"file_path": "a.html", "design_systems": ["x"]}, ["design_systems"]), ({"file_path": "a.html", "scope": "org"}, ["scope"]),
    ({"file_path": "a.html", "prompt": "p", "intent": "i"}, ["prompt", "intent"]),
    ({"file_path": "a.html", "force": True}, ["force"]), ({"file_path": "a.html", "overwrite_unread": True}, ["overwrite_unread"]),
    ({"file_path": "a.html", "unknown_new_param": 1}, ["unknown_new_param"]),
    ({"action": "read", "url": "https://claude.ai/artifact/a", "file_path": "a.html"}, ["file_path"]),
    ({"action": "read", "url": "https://claude.ai/artifact/a", "force": True}, ["force"])])
def test_artifact_hook_allows_only_the_listed_params_and_names_the_extra_key(repo, home, stub, inp, keys):
    res = run_hook_env(repo, home, "Artifact", inp, artifact_env(stub))
    assert denied(res) and f"remove {', '.join(keys)}." in res["permissionDecisionReason"], inp
    assert _Stub.calls == []
    assert denied(run_hook(repo, home, "Artifact", inp))  # the static rules hold outside a role turn too


def test_artifact_hook_ignores_empty_extra_params_and_passes_the_listed_ones(repo, home, stub):
    env = artifact_env(stub)
    _Stub.response = {"ok": True, "reason": ""}
    full = {"action": "publish", "file_path": "a.html", "url": "", "title": "T", "description": "d", "icon": "i", "label": "l"}
    assert run_hook_env(repo, home, "Artifact", full, env) is None
    assert run_hook_env(repo, home, "Artifact", {**full, "force": False, "paths": [], "root": "", "capabilities": None}, env) is None
    assert run_hook_env(repo, home, "Artifact", {"action": "read", "url": "https://claude.ai/artifact/a", "page": 2}, env) is None
    assert len(_Stub.calls) == 3


def test_artifact_hook_asks_the_daemon_for_a_publish_and_denies_with_its_reason(repo, home, stub):
    from urllib.parse import parse_qs, urlsplit
    env = artifact_env(stub)
    why = "the Master hasn't approved publishing x.html: request_approval(feature, 'Artifact: x.html', 'p'), end your turn"
    _Stub.response = {"ok": False, "reason": why}
    res = run_hook_env(repo, home, "Artifact", {"file_path": ".aiteams/features/F-1/x.html", "icon": "i"}, env)
    assert denied(res) and why in res["permissionDecisionReason"]
    path, headers, _ = _Stub.calls[-1]
    q = {k: v[0] for k, v in parse_qs(urlsplit(path).query, keep_blank_values=True).items()}
    assert urlsplit(path).path == "/internal/artifact-check"
    assert q == {"action": "publish", "path": str(repo.resolve() / ".aiteams/features/F-1/x.html"), "url": ""}  # made absolute
    assert headers["x-aiteams-turn"] == "7" and headers["x-aiteams-token"] == "tok" and headers["x-aiteams-project"] == "p1"
    _Stub.response = {"ok": True, "reason": ""}
    assert run_hook_env(repo, home, "Artifact", {"file_path": str(repo / ".aiteams/features/F-1/x.html")}, env) is None
    q = parse_qs(urlsplit(_Stub.calls[-1][0]).query)
    assert q["path"] == [str(repo / ".aiteams/features/F-1/x.html")]  # an absolute path goes as it is
    # an update passes the url, a read passes the action and the url
    run_hook_env(repo, home, "Artifact", {"file_path": "x.html", "url": "https://claude.ai/artifact/a"}, env)
    assert parse_qs(urlsplit(_Stub.calls[-1][0]).query)["url"] == ["https://claude.ai/artifact/a"]
    run_hook_env(repo, home, "Artifact", {"action": "read", "url": "https://claude.ai/artifact/a"}, env)
    q = parse_qs(urlsplit(_Stub.calls[-1][0]).query, keep_blank_values=True)
    assert q["action"] == ["read"] and q["url"] == ["https://claude.ai/artifact/a"] and q["path"] == [""]
    run_hook_env(repo, home, "Artifact", {}, env)  # no action means publish
    assert parse_qs(urlsplit(_Stub.calls[-1][0]).query)["action"] == ["publish"]


@pytest.mark.parametrize("reply", [(500, b'{"ok": true}'), (200, b"not json"), (200, b"{}"), (200, b'{"ok": "yes"}'),
                                   (200, b"[true]"), (200, b'{"ok": false}'), (200, b'{"ok": false, "reason": 5}')])
def test_artifact_hook_fails_closed_on_bad_answers(repo, home, stub, reply):
    _Stub.get_reply = reply
    res = run_hook_env(repo, home, "Artifact", {"file_path": "x.html"}, artifact_env(stub))
    assert denied(res) and res["permissionDecisionReason"].strip() != "AITEAMS_DENY:"


def test_artifact_hook_fails_closed_without_a_daemon_but_allows_a_publish_outside_a_turn(repo, home):
    env = {"AITEAMS_DAEMON_URL": "http://127.0.0.1:1", "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7"}
    res = run_hook_env(repo, home, "Artifact", {"file_path": "x.html"}, env)
    assert denied(res) and "did not answer" in res["permissionDecisionReason"]
    assert run_hook(repo, home, "Artifact", {"file_path": "x.html"}) is None  # no turn env: not a role turn
