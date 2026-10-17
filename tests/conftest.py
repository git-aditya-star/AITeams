import json
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
# A role running pytest inside its turn carries that turn's daemon URL and secret; subprocesses built from
# os.environ (hook, MCP server, fake claude) would then lock files and record edits on the live daemon.
TURN_ENV = ("AITEAMS_DAEMON_URL", "AITEAMS_PROJECT", "AITEAMS_ROLE", "AITEAMS_TURN", "AITEAMS_TURN_TOKEN",
            "AITEAMS_PROJECT_PATH", "AITEAMS_ASK_TIMEOUT_S")
for _k in TURN_ENV:
    os.environ.pop(_k, None)

TOKEN = "test-master-token"
AUTH = {"Authorization": "Bearer " + TOKEN}  # every TestClient sends it; /api needs the Master token since M2a


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated ~/.aiteams with the scheduler disabled (max_concurrent=0) and a fixed Master token."""
    h = tmp_path / "home"
    h.mkdir()
    (h / "settings.json").write_text(json.dumps({"max_concurrent": 0, "ask_timeout_s": 2}))
    monkeypatch.setenv("AITEAMS_HOME", str(h))
    monkeypatch.setenv("AITEAMS_TOKEN", TOKEN)
    return h


def running_turn(db, role, conv="general", kind="normal", wake_id=None, pid=None):
    """Create a running turn with a turn token; returns (turn, headers for /internal)."""
    t = db.create_turn(role, "sess", "master", conv, kind, token=f"tok-{role}-{db.q('SELECT COUNT(*) c FROM turns')[0]['c']}")
    t = db.update_turn(t["id"], state="running", wake_id=wake_id)
    return t, {"X-AITeams-Project": pid or "", "X-AITeams-Turn": str(t["id"]), "X-AITeams-Token": t["token"]}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    (r / "README.md").write_text("# demo\n")
    return r


@pytest.fixture
def db(repo):
    from aiteams.db import ProjectDB
    d = ProjectDB(str(repo))
    yield d
    d.close()


class FakeHub:
    def __init__(self):
        self.events = []

    async def broadcast(self, event, data):
        self.events.append((event, data))
