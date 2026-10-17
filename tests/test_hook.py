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
    assert run_hook(repo, home, "Write", {"file_path": ".aiteams/notes/handoff.md"}) is None


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

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        _Stub.calls.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        data = json.dumps(_Stub.response).encode()
        self.send_response(200)
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
    env = {"AITEAMS_DAEMON_URL": stub, "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7"}
    assert run_hook_env(repo, home, "Edit", {"file_path": str(repo / "src" / "app.py")}, env) is None
    path, headers, body = _Stub.calls[-1]
    assert path == "/internal/locks/acquire" and body == {"path": "src/app.py"}
    assert headers["x-aiteams-project"] == "p1" and headers["x-aiteams-turn"] == "7"
    _Stub.response = {"ok": False, "holder": "backend", "since": 1_700_000_000}
    res = run_hook_env(repo, home, "Write", {"file_path": "src/app.py"}, env)
    assert denied(res) and "src/app.py is locked by backend since" in res["permissionDecisionReason"]
    # Bash is not lock-checked; writes outside the project are denied before any HTTP call
    n = len(_Stub.calls)
    assert run_hook_env(repo, home, "Bash", {"command": "sed -i '' s/a/b/ src/app.py"}, env) is None
    assert denied(run_hook_env(repo, home, "Write", {"file_path": "/tmp/x"}, env))
    assert len(_Stub.calls) == n


def test_lock_check_fails_open_without_daemon(repo, home):
    env = {"AITEAMS_DAEMON_URL": "http://127.0.0.1:1", "AITEAMS_PROJECT": "p1", "AITEAMS_TURN": "7"}
    assert run_hook_env(repo, home, "Write", {"file_path": "src/app.py"}, env) is None
    assert any("lock check skipped" in l for l in (repo / ".aiteams" / "hooks.log").read_text().splitlines())


def test_write_under_features_allowed(repo, home):
    assert run_hook(repo, home, "Write", {"file_path": ".aiteams/features/F-1/prd.md"}) is None
    assert denied(run_hook(repo, home, "Write", {"file_path": ".aiteams/prompts/x.md"}))
