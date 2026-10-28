import json
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
# A role running pytest inside its turn carries that turn's daemon URL and secret; subprocesses built from
# os.environ (hook, MCP server, fake claude) would then lock files and record edits on the live daemon.
TURN_ENV = ("AITEAMS_DAEMON_URL", "AITEAMS_PROJECT", "AITEAMS_ROLE", "AITEAMS_TURN", "AITEAMS_TURN_TOKEN",
            "AITEAMS_PROJECT_PATH", "AITEAMS_ASK_TIMEOUT_S", "AITEAMS_ASK_PLUGINS",
            # F-29: every role turn now carries the Artifact switch; tests that check "switch not set" must not inherit it
            "CLAUDE_CODE_ARTIFACT", "CLAUDE_CODE_ARTIFACT_AUTO_OPEN")
for _k in TURN_ENV:
    os.environ.pop(_k, None)

TOKEN = "test-master-token"
AUTH = {"Authorization": "Bearer " + TOKEN}  # every TestClient sends it; /api needs the Master token since M2a
# F-40: /internal/approvals refuses a request without the three-part brief; most tests only need a valid one
BRIEF = {"what": "Approve the plan.", "if_approved": "The team builds it.", "tradeoffs": ["none"]}
BRIEF_SUMMARY = "**What:** Approve the plan.\n**If you approve:** The team builds it.\n**Trade-offs:**\n- none"


@pytest.fixture(autouse=True)
def design_gate_off(monkeypatch):
    """F-21: migration 8 gates every feature created afterwards behind an approved `Design:`; most tests file commit
    requests without one, so the gate starts in the far future. Modules that test the gate override this fixture."""
    monkeypatch.setattr("aiteams.db.design_gate_start", lambda: 4e9)


@pytest.fixture(autouse=True)
def branch_gate_off(monkeypatch):
    """F-39: migration 13 gates every feature created afterwards behind an approved `Branch:` card. A separate fixture, so a
    module that overrides design_gate_off keeps this one; tests/test_branches.py overrides this to test the gate."""
    monkeypatch.setattr("aiteams.db.branch_gate_start", lambda: 4e9)


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
