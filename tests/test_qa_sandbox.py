"""scripts/qa_sandbox.py: seeded `demo` project behind the token, /qa/fire -> hub frame, isolated from the real home."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("qa_sandbox", ROOT / "scripts" / "qa_sandbox.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake_home = tmp_path / "user"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # what a role's turn carries; isolate_env must drop all of it
    for k, v in {"AITEAMS_TURN_TOKEN": "live-turn-secret", "AITEAMS_DAEMON_URL": "http://127.0.0.1:7700",
                 "AITEAMS_TURN": "7", "AITEAMS_HOME": str(fake_home / ".aiteams"), "AITEAMS_TOKEN": "x"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    work = tmp_path / "work"
    mod.isolate_env(work / "home", "sb-token")
    (work / "home").mkdir(parents=True)
    (work / "home" / "settings.json").write_text('{"max_concurrent": 0}')
    yield mod, work, fake_home


def test_env_is_isolated(sandbox):
    _, work, _ = sandbox
    assert "AITEAMS_TURN_TOKEN" not in os.environ and "AITEAMS_DAEMON_URL" not in os.environ
    assert "AITEAMS_TURN" not in os.environ
    assert os.environ["AITEAMS_HOME"] == str(work / "home") and os.environ["AITEAMS_TOKEN"] == "sb-token"


def test_seeded_app_and_fire(sandbox):
    mod, work, fake_home = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        assert c.get("/api/projects").status_code == 401
        projects = c.get("/api/projects", headers=auth).json()
        assert [p["id"] for p in projects] == ["demo"]
        inbox = c.get("/api/p/demo/inbox", headers=auth).json()
        assert len(inbox["questions"]) == 1 and len(inbox["approvals"]) == 1 and len(inbox["commits"]) == 1
        diff = c.get(f"/api/p/demo/commits/{inbox['commits'][0]['id']}/diff", headers=auth).json()
        assert [(f["path"], f["status"], f["binary"]) for f in diff["files"]] == [
            ("src/orders.py", "modified", False), ("src/export_csv.py", "added", False), ("assets/logo.png", "modified", True)]
        assert c.post("/qa/fire", json={"kind": "approval"}).status_code == 401
        assert c.post("/qa/fire", json={"kind": "nope"}, headers=auth).status_code == 400
        with c.websocket_connect("/ws/p/demo?token=sb-token") as ws:
            r = c.post("/qa/fire", json={"kind": "approval", "text": "Sign off the CSV columns"}, headers=auth).json()
            assert r["conversation"] == "feature-1"
            frames = [ws.receive_json() for _ in range(3)]
        approval = next(f for f in frames if f["event"] == "approval")
        assert approval["data"]["title"] == "Sign off the CSV columns" and approval["data"]["state"] == "pending"
        assert c.post("/qa/fire", json={"kind": "committed"}, headers=auth).json()["state"] == "committed"
        assert c.post("/qa/reset", headers=auth).json() == {"ok": True}
        assert len(c.get("/api/p/demo/inbox", headers=auth).json()["approvals"]) == 1
    assert not (fake_home / ".aiteams").exists()
