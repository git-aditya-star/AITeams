"""F-9: role model/effort settings: effort_for, PUT validation, options, persistence, team status."""
import pytest
from fastapi.testclient import TestClient

from aiteams import config
from aiteams.daemon import create_app
from tests.conftest import AUTH


def test_effort_for_role_then_default_then_none(monkeypatch):
    s = {"default_model": "opus", "models": {}, "default_effort": None, "efforts": {}}
    assert config.effort_for(s, "qa") is None
    s["default_effort"] = "high"
    assert config.effort_for(s, "qa") == "high"
    s["efforts"] = {"qa": "low"}
    assert config.effort_for(s, "qa") == "low" and config.effort_for(s, "lead") == "high"
    s["efforts"] = {"qa": "turbo"}  # garbage in the file never reaches the CLI
    assert config.effort_for(s, "qa") is None
    s["efforts"] = {"qa": "max"}
    monkeypatch.setitem(config.EFFORT_SUPPORT, "opus", ("low", "medium"))
    assert config.effort_for(s, "qa") is None  # unsupported by the role's model: the flag is dropped
    s["models"] = {"qa": "sonnet"}
    assert config.effort_for(s, "qa") == "max"


@pytest.mark.parametrize("bad", [
    {"models": {"qa": 5}, "efforts": {"qa": ["low"]}, "default_effort": {"x": 1}},
    {"models": "opus", "efforts": "low", "default_model": 7, "default_effort": 3},
    {"models": {"qa": None}, "efforts": {"qa": {"a": 1}}, "default_model": ["opus"]},
])
def test_hand_edited_garbage_never_crashes(home, bad):
    """QA A7: garbage in settings.json resolves to opus / no effort at load and at turn start."""
    import json
    (home / "settings.json").write_text(json.dumps({"max_concurrent": 0, **bad}))
    s = config.load_settings()
    assert config.model_for(s, "qa") == "opus" and config.effort_for(s, "qa") is None
    assert config.model_for(bad, "qa") == "opus" and config.effort_for(bad, "qa") is None  # raw, unloaded


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


def test_options(client):
    o = client.get("/api/settings/options").json()
    assert o == {"roles": config.ROLES, "models": ["opus", "sonnet", "haiku", "fable"],
                 "efforts": ["low", "medium", "high", "xhigh", "max"],
                 "effort_support": {m: list(config.EFFORTS) for m in config.MODELS}}
    bad = {"Authorization": "Bearer nope"}  # QA B8: all of /api/settings* needs the Master token
    assert client.get("/api/settings/options", headers=bad).status_code == 401
    assert client.get("/api/settings", headers=bad).status_code == 401
    assert client.put("/api/settings", json={"efforts": {"qa": "low"}}, headers=bad).status_code == 401
    assert config.load_settings()["efforts"] == {}


@pytest.mark.parametrize("body", [
    {"default_model": "gpt-5"}, {"default_model": ""}, {"default_effort": "turbo"},
    {"models": {"qa": "claude-opus-5-5"}}, {"models": {"intern": "opus"}}, {"models": ["opus"]},
    {"efforts": {"qa": "LOW"}}, {"efforts": {"nobody": "low"}},
])
def test_put_rejects_bad_values_and_leaves_file(client, home, body):
    before = config.load_settings()
    r = client.put("/api/settings", json=body)
    assert r.status_code == 400, body
    assert config.load_settings() == before


def test_put_sets_inherits_and_survives_restart(client, home):
    r = client.put("/api/settings", json={"models": {"qa": "sonnet", "lead": ""},
                                          "efforts": {"qa": "low", "lead": None}, "default_effort": "high"})
    assert r.status_code == 200, r.text
    assert r.json()["models"] == {"qa": "sonnet"} and r.json()["efforts"] == {"qa": "low"}
    assert config.load_settings()["efforts"] == {"qa": "low"}  # a fresh load = a daemon restart
    assert config.load_settings()["max_concurrent"] == 0  # QA B2: a partial PUT keeps unrelated keys
    assert client.get("/api/settings").json()["default_effort"] == "high"
    # empty/null means inherit: the role's key is removed
    r = client.put("/api/settings", json={"efforts": {"qa": ""}, "default_effort": None}).json()
    assert r["efforts"] == {} and r["default_effort"] is None
    assert config.load_settings()["efforts"] == {}


def test_team_status_has_effort(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    client.put("/api/settings", json={"models": {"qa": "sonnet"}, "efforts": {"qa": "low"}})
    team = {r["role"]: r for r in client.get(f"/api/p/{pid}/team").json()["roles"]}
    assert (team["qa"]["model"], team["qa"]["effort"]) == ("sonnet", "low")
    assert team["lead"]["effort"] is None
