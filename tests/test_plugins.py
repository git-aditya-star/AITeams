"""F-23: the plugin list in settings (validation, persistence), what a turn sends (enabledPlugins, AITEAMS_ASK_PLUGINS)
and the missing-plugin check. The hook gate is in test_hook.py, the approval endpoint in test_skill_approval.py."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from aiteams import config, plugins
from aiteams.daemon import create_app
from aiteams.orchestrator import turn as turn_mod
from aiteams.orchestrator.claude_proc import aiteams_env, build_cmd, child_env
from aiteams.runtime import AppState, Project
from tests.conftest import AUTH, FakeHub
from tests.test_claude_proc import ASSISTANT, INIT, RESULT, fake_cmd, fake_env

NOTES, SKILLS = "project-notes@project-notes", "team-skills@example"
# The real init.plugins shape, probed 2026-10-18 (claude -p, --setting-sources local + enabledPlugins).
LOADED = [{"name": "project-notes", "path": "/x/project-notes/0.3.0", "source": NOTES, "version": "0.3.0"},
          {"name": "team-skills", "path": "/x/team-skills/b685", "source": SKILLS},
          {"name": "cc-plugin-sec-default", "path": "builtin", "source": "cc-plugin-sec-default@builtin"}]


@pytest.fixture
def client(home):
    with TestClient(create_app(7799, write_daemon_json=False), headers=AUTH) as c:
        yield c


def row(pid, on=True, ask=False):
    return {"id": pid, "on": on, "ask": ask}


# ---- settings ---------------------------------------------------------------------------------------------------
def test_default_is_empty_and_not_shared(home):
    assert config.DEFAULT_SETTINGS["plugins"] == [] and config.load_settings()["plugins"] == []
    config.load_settings()["plugins"].append(row(NOTES))
    assert config.DEFAULT_SETTINGS["plugins"] == [] and config.load_settings()["plugins"] == []


@pytest.mark.parametrize("bad", ["x", {"id": NOTES}, 5, None, {"a": 1}])
def test_load_settings_coerces_a_non_list_to_empty(home, bad):
    (home / "settings.json").write_text(json.dumps({"plugins": bad}))
    assert config.load_settings()["plugins"] == []


def test_plugin_name_and_rows_skip_hand_edited_garbage():
    assert config.plugin_name(NOTES) == "project-notes" and config.plugin_name("plain") == "plain"
    s = {"plugins": ["x", 5, {"id": 7}, {"id": "no-at-sign"}, {"id": "a@b", "on": "yes"}, row(NOTES, False, True),
                     row(NOTES.upper()), {"id": "c@d"}, {"id": "e@f", "ask": 1}]}
    assert config.plugin_rows(s) == [row("a@b"), row(NOTES, False, True), row("c@d"), row("e@f")]
    assert config.plugin_rows({"plugins": "x"}) == [] and config.plugin_rows({}) == []
    assert config.ask_plugin_names({"plugins": [row(NOTES, True, True), row(SKILLS, False, True), row("acme@m"),
                                                row("team-skills@other", True, True)]}) == ["project-notes", "team-skills"]


def test_validate_plugins_accepts_and_normalises():
    assert config.validate_plugins([]) == []
    assert config.validate_plugins([row(NOTES, True, False), {"id": " a.b-c_d@m.k-1 ", "on": False, "ask": True},
                                    {"id": "bare@m"}]) == [row(NOTES), row("a.b-c_d@m.k-1", False, True), row("bare@m")]


@pytest.mark.parametrize("bad, msg", [
    ("x", "list"), ({"id": NOTES}, "list"), ([NOTES], "list"), (None, "list"), ([[row(NOTES)]], "list"),
    ([{"on": True}], "name@marketplace"), ([{"id": 5}], "name@marketplace"), ([{"id": ""}], "name@marketplace"),
    ([{"id": "noat"}], "name@marketplace"), ([{"id": "a@b@c"}], "name@marketplace"), ([{"id": "a b@c"}], "name@marketplace"),
    ([{"id": "@m"}], "name@marketplace"), ([{"id": "a@"}], "name@marketplace"), ([{"id": "a/b@c"}], "name@marketplace"),
    ([row(NOTES), row(NOTES, False)], "twice"), ([row("A@b"), row("a@B")], "twice"),
    ([{"id": NOTES, "on": "yes"}], "true or false"), ([{"id": NOTES, "on": 1}], "true or false"),
    ([{"id": NOTES, "ask": None}], "true or false"), ([{"id": NOTES, "ask": "no"}], "true or false"),
])
def test_validate_plugins_rejects(bad, msg):
    with pytest.raises(ValueError, match=msg):
        config.validate_plugins(bad)


def test_put_saves_plugins_and_keeps_them_across_other_puts(client, home):
    rows = [row(NOTES), row(SKILLS, True, True), row("cc-plugin-telemetry@builtin", False)]
    r = client.put("/api/settings", json={"plugins": rows})
    assert r.status_code == 200 and r.json()["plugins"] == rows
    assert config.load_settings()["plugins"] == rows  # a fresh load = a daemon restart
    client.put("/api/settings", json={"default_effort": "high"})  # a partial PUT keeps the list
    assert config.load_settings()["plugins"] == rows
    assert client.app.state.app.settings["plugins"] == rows  # the next turn reads the live settings
    assert client.put("/api/settings", json={"plugins": []}).status_code == 200
    assert config.load_settings()["plugins"] == []


@pytest.mark.parametrize("body", [
    {"plugins": "x"}, {"plugins": [{"id": "nope"}]}, {"plugins": [row(NOTES), row(NOTES)]},
    {"plugins": [{"id": NOTES, "on": "true"}]}, {"plugins": [row(NOTES)], "models": {"qa": "gpt-5"}},
])
def test_put_rejects_bad_plugins_with_a_sentence_and_leaves_the_file(client, home, body):
    client.put("/api/settings", json={"plugins": [row(SKILLS)]})
    before = config.load_settings()
    r = client.put("/api/settings", json=body)
    assert r.status_code == 400 and isinstance(r.json()["detail"], str) and r.json()["detail"]
    assert config.load_settings() == before


# ---- what a turn sends -------------------------------------------------------------------------------------------
def settings_of(cmd):
    return json.loads(cmd[cmd.index("--settings") + 1])


def cmd_for(settings):
    env = child_env("http://127.0.0.1:7700", "p1", "/tmp/p", "backend", 7, 600, "turn-secret")
    return build_cmd({"max_turns": 40, **settings}, "backend", env, "/tmp/sys.md", "s1", False, "opus")


def test_build_cmd_sends_nothing_for_an_empty_list():
    for s in ({}, {"plugins": []}, {"plugins": "garbage"}, {"plugins": ["x", {"id": "bad"}]}):
        assert "enabledPlugins" not in settings_of(cmd_for(s)), s


def test_build_cmd_sends_exactly_the_list():
    s = settings_of(cmd_for({"plugins": [row(NOTES), row(SKILLS, False, True), row("cc-plugin-telemetry@builtin", False)]}))
    assert s["enabledPlugins"] == {NOTES: True, SKILLS: False, "cc-plugin-telemetry@builtin": False}  # unlisted: not sent
    assert "PreToolUse" in s["hooks"]


def test_child_env_carries_the_ask_list_and_never_inherits_it(monkeypatch):
    monkeypatch.setenv("AITEAMS_ASK_PLUGINS", "inherited")
    base = ("http://127.0.0.1:7700", "p1", "/tmp/p", "backend", 7, 600, "tok")
    assert child_env(*base)["AITEAMS_ASK_PLUGINS"] == ""
    assert child_env(*base, ["team-skills", "project-notes"])["AITEAMS_ASK_PLUGINS"] == "team-skills,project-notes"
    assert "AITEAMS_ASK_PLUGINS" not in aiteams_env(child_env(*base, ["team-skills"]))  # the MCP server doesn't need it


# ---- the missing check --------------------------------------------------------------------------------------------
class Proj:
    def __init__(self):
        self.hub = FakeHub()


def check(st, proj, role, listed):
    return asyncio.run(plugins.check_plugins(st, proj, role, {"type": "system", "subtype": "init", "plugins": listed}))


def test_loaded_plugins_reads_name_and_source_of_the_real_shape():
    assert plugins.loaded_plugins({"plugins": LOADED}) == {
        "project-notes", NOTES, "team-skills", SKILLS, "cc-plugin-sec-default", "cc-plugin-sec-default@builtin"}
    assert plugins.loaded_plugins({"plugins": ["Acme", {"name": 5}, None]}) == {"acme"}  # bare names, garbage skipped
    assert plugins.loaded_plugins({}) is None and plugins.loaded_plugins({"plugins": "x"}) is None


def test_flip_to_missing_broadcasts_once_and_clears_when_it_loads(home):
    st, proj = AppState(7799), Proj()
    st.settings["plugins"] = [row(NOTES), row(SKILLS), row("acme@market"), row("off@market", False)]
    assert check(st, proj, "frontend", LOADED) == ["acme@market"]  # off rows are never checked
    assert proj.hub.events == [("plugin_missing", {"id": "acme@market", "role": "frontend"})]
    s = st.plugin_status
    assert set(s) == {NOTES, SKILLS, "acme@market"} and s["acme@market"]["missing"] is True and s["acme@market"]["role"] == "frontend"
    assert s[NOTES]["missing"] is False and isinstance(s[NOTES]["checked_at"], float)
    # still missing in later turns (any role): no second toast, the status follows the last turn
    assert check(st, proj, "qa", LOADED) == [] and len(proj.hub.events) == 1 and st.plugin_status["acme@market"]["role"] == "qa"
    # it loads: cleared; missing again later: a new toast
    assert check(st, proj, "qa", LOADED + [{"name": "acme", "source": "acme@market"}]) == []
    assert st.plugin_status["acme@market"]["missing"] is False and len(proj.hub.events) == 1
    assert check(st, proj, "backend", LOADED) == ["acme@market"]
    assert proj.hub.events[-1] == ("plugin_missing", {"id": "acme@market", "role": "backend"}) and len(proj.hub.events) == 2


def test_match_by_name_or_full_id_and_switching_off_or_removing_resets_the_toast(home):
    st, proj = AppState(7799), Proj()
    st.settings["plugins"] = [row("project-notes@elsewhere")]
    assert check(st, proj, "qa", LOADED) == []  # the name matches what loaded
    st.settings["plugins"] = [row("zzz@market")]
    assert check(st, proj, "qa", []) == ["zzz@market"] and set(st.plugin_status) == {"zzz@market"}
    st.settings["plugins"] = [row("zzz@market", on=False)]  # switched off: status gone, nothing flagged
    assert check(st, proj, "qa", []) == [] and st.plugin_status == {}
    st.settings["plugins"] = [row("zzz@market")]  # switched on again and still missing: a fresh toast
    assert check(st, proj, "qa", []) == ["zzz@market"] and len(proj.hub.events) == 2
    st.settings["plugins"] = []  # removed
    assert check(st, proj, "qa", []) == [] and st.plugin_status == {}


def test_an_init_without_a_plugin_list_flags_nothing(home):
    st, proj = AppState(7799), Proj()
    st.settings["plugins"] = [row(NOTES)]
    assert asyncio.run(plugins.check_plugins(st, proj, "qa", {"type": "system", "subtype": "init"})) == []
    assert st.plugin_status == {} and proj.hub.events == []
    assert check(st, proj, "qa", LOADED) == [] and st.plugin_status[NOTES]["missing"] is False  # no row, no flag
    st.settings["plugins"] = []
    assert check(st, proj, "qa", []) == [] and st.plugin_status == {}  # an empty list sends and checks nothing


def test_plugins_view_is_rows_plus_status(home):
    st, proj = AppState(7799), Proj()
    assert plugins.plugins_view(st) == {"rows": [], "status": {}}
    st.settings["plugins"] = [row(NOTES, True, True), row("acme@market")]
    check(st, proj, "qa", LOADED)
    v = plugins.plugins_view(st)
    assert v["rows"] == [row(NOTES, True, True), row("acme@market")]
    assert set(v["status"]) == {NOTES, "acme@market"} and v["status"]["acme@market"]["missing"] is True
    assert set(v["status"]["acme@market"]) == {"missing", "checked_at", "role"}


# ---- a real TurnRunner turn --------------------------------------------------------------------------------------
def run_turn(db, monkeypatch, plugin_rows, events):
    captured = []

    def fake_child_env(*a, **k):
        captured.append((a, k))
        return fake_env(events)
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", fake_child_env)
    st = AppState(7799)
    st.settings["plugins"] = plugin_rows
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    m = proj.db.add_message("dm-backend", "master", "Message", "hi", [], None)
    wake = proj.db.add_wake("backend", "dm-backend", [m["id"]], "master")
    t = asyncio.run(turn_mod.TurnRunner(st, proj, wake).run())
    return st, proj, t, captured


def test_turn_flags_a_missing_plugin_and_reads_the_ask_list_at_start(db, home, monkeypatch):
    init = {**INIT, "plugins": LOADED}
    st, proj, t, captured = run_turn(db, monkeypatch, [row(NOTES, True, True), row("acme@market", True, True)],
                                     [init, ASSISTANT, RESULT])
    assert t["state"] == "done" and t["error"] is None  # never blocks the turn
    assert [e for e in proj.hub.events if e[0] == "plugin_missing"] == [
        ("plugin_missing", {"id": "acme@market", "role": "backend"})]
    assert st.plugin_status["acme@market"]["missing"] is True and st.plugin_status[NOTES]["missing"] is False
    (args, _), = captured
    assert args[-1] == ["project-notes", "acme"]  # AITEAMS_ASK_PLUGINS comes from the settings at turn start
    assert "system" in [e["type"] for e in proj.db.turn_events(t["id"])]  # the init event is still stored
    proj.db.close()


def test_turn_with_no_plugin_list_or_no_init_plugins_is_quiet(db, home, monkeypatch):
    st, proj, t, captured = run_turn(db, monkeypatch, [], [{**INIT, "plugins": LOADED}, ASSISTANT, RESULT])
    assert t["state"] == "done" and st.plugin_status == {} and captured[0][0][-1] == []
    proj.db.close()
    st, proj, t, _ = run_turn(db, monkeypatch, [row("acme@market")], [INIT, ASSISTANT, RESULT])  # INIT has no plugins key
    assert t["state"] == "done" and st.plugin_status == {} and not [e for e in proj.hub.events if e[0] == "plugin_missing"]
    proj.db.close()


def test_a_failing_check_never_fails_the_turn(db, home, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("check blew up")
    monkeypatch.setattr(turn_mod, "check_plugins", boom)
    st, proj, t, _ = run_turn(db, monkeypatch, [row("acme@market")], [{**INIT, "plugins": []}, ASSISTANT, RESULT])
    assert t["state"] == "done" and t["error"] is None
    proj.db.close()


def test_get_plugin_settings_needs_the_master_token_and_returns_rows_and_status(client, home):
    assert client.get("/api/settings/plugins", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/settings/plugins").json() == {"rows": [], "status": {}}
    rows = [row(NOTES, True, True), row("acme@market", False)]
    client.put("/api/settings", json={"plugins": rows})
    st = client.app.state.app
    st.plugin_status[NOTES] = {"missing": True, "checked_at": 1.5, "role": "qa"}
    assert client.get("/api/settings/plugins").json() == {
        "rows": rows, "status": {NOTES: {"missing": True, "checked_at": 1.5, "role": "qa"}}}
