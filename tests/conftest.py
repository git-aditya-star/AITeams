import json
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated ~/.aiteams with the scheduler disabled (max_concurrent=0)."""
    h = tmp_path / "home"
    h.mkdir()
    (h / "settings.json").write_text(json.dumps({"max_concurrent": 0, "ask_timeout_s": 2}))
    monkeypatch.setenv("AITEAMS_HOME", str(h))
    return h


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
