"""F-29 Part B: the Artifact gate (daemon rules, one approval per page, pinned content), the capture of a publish result,
the visible failure when the tool is missing, and how artifacts show in the docs list, the messages and the turn setup."""
import asyncio
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from aiteams.orchestrator import turn as turn_mod
from aiteams.orchestrator.claude_proc import HOOK_TOOLS, aiteams_env, build_cmd, child_env
from aiteams.runtime import AppState, Project
from tests.conftest import AUTH, FakeHub, running_turn
from tests.test_claude_proc import INIT, RESULT, fake_cmd, fake_env

URL = "https://claude.ai/artifact/AbC123"
URL2 = "https://claude.ai/artifact/ZzZ999"
HTML = "<!doctype html><title>Export  orders &amp; more</title><p>hi</p>"
NAME = "artifact-x.html"
REL = ".aiteams/features/F-1/" + NAME


@pytest.fixture
def client(home):
    with TestClient(create_app(7799, write_daemon_json=False), headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


@pytest.fixture
def proj(client, pid):
    return client.app.state.app.projects[pid]


@pytest.fixture
def docs(client, pid, proj, repo):
    """F-1 (Lead 1's, so nothing claims it mid-test) with its docs folder and one page in it."""
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    proj.db.x("UPDATE features SET lead='lead' WHERE id=1")
    d = repo / ".aiteams" / "features" / "F-1"
    d.mkdir(parents=True, exist_ok=True)
    (d / NAME).write_text(HTML)
    return d


def turn(proj, pid, role="frontend"):
    return running_turn(proj.db, role, pid=pid)


def abs_path(docs, name=NAME):
    return str(docs / name)


def check(client, hdr, path=None, url=None, action="publish"):
    params = {"action": action, **({"path": path} if path is not None else {}), **({"url": url} if url else {})}
    r = client.get("/internal/artifact-check", params=params, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


def approve(client, pid, hdr, name=NAME, decision="approve", feature="F-1"):
    aid = client.post("/internal/approvals", json={"feature": feature, "title": f"Artifact: {name}", "summary": REL},
                      headers=hdr).json()["id"]
    r = client.post(f"/api/p/{pid}/approvals/{aid}/decide", json={"decision": decision})
    assert r.status_code == 200, r.text
    return aid


def publish(proj, role="frontend", inp=None, path=REL, url=URL, version=1, turn_id=None):
    st = AppState(7799)  # record_artifact only needs the hub and the db, both on the project
    return asyncio.run(workflow.record_artifact(st, proj, role, turn_id, inp or {"file_path": path}, path, url, version))


# ---- a new page: the Master's approval, pinned to the page -------------------------------------------------------
def test_a_new_page_needs_an_approved_artifact_request_and_gets_a_pending_row(client, pid, proj, docs):
    t1, h1 = turn(proj, pid)
    r = check(client, h1, abs_path(docs))
    assert r["ok"] is False and f"request_approval(feature, 'Artifact: {NAME}', '{REL}')" in r["reason"]
    assert proj.db.q("SELECT * FROM artifacts") == []
    # pending, changes requested and rejected requests don't open it
    pending = client.post("/internal/approvals", json={"feature": "F-1", "title": f"Artifact: {NAME}", "summary": REL},
                          headers=h1).json()["id"]
    assert check(client, h1, abs_path(docs))["ok"] is False
    client.post(f"/api/p/{pid}/approvals/{pending}/decide", json={"decision": "changes", "note": "darker"})
    approve(client, pid, h1, decision="reject")
    assert check(client, h1, abs_path(docs))["ok"] is False
    # the approval pinned the page's content when it was requested
    aid = approve(client, pid, h1)
    assert proj.db.get_kv(f"artifact_sha:{aid}") == hashlib.sha256(HTML.encode()).hexdigest()
    # approved: allowed, and a pending row (no url yet) records who and which turn
    assert check(client, h1, abs_path(docs)) == {"ok": True, "reason": ""}
    row = proj.db.artifact_by_path(1, REL)
    assert (row["url"], row["role"], row["approval_id"], row["turn_id"], row["version"]) == (None, "frontend", aid, t1["id"], 1)
    assert proj.db.artifacts(1) == []  # a pending row is not a page yet
    # one approval, one artifact: a second new publish while one is pending is refused, for any role
    t2, h2 = turn(proj, pid, "product")
    r = check(client, h2, abs_path(docs))
    assert r["ok"] is False and "already in progress" in r["reason"]
    # its turn ended without a capture: the row is stale and the page can be tried again
    proj.db.update_turn(t1["id"], state="failed")
    assert check(client, h2, abs_path(docs))["ok"] is True
    rows = proj.db.q("SELECT * FROM artifacts")
    assert len(rows) == 1 and rows[0]["role"] == "product" and rows[0]["turn_id"] == t2["id"]


def test_the_path_may_be_relative_to_the_project_and_the_title_match_ignores_case_and_spacing(client, pid, proj, docs):
    _, h = turn(proj, pid)
    client.post("/internal/approvals", json={"feature": "F-1", "title": f"  ARTIFACT:    {NAME} ", "summary": "s"}, headers=h)
    aid = proj.db.approvals("pending")[0]["id"]
    client.post(f"/api/p/{pid}/approvals/{aid}/decide", json={"decision": "approve"})
    assert proj.db.get_kv(f"artifact_sha:{aid}") == hashlib.sha256(HTML.encode()).hexdigest()  # pinned despite the spacing
    assert check(client, h, REL) == {"ok": True, "reason": ""}  # a relative path is resolved against the project


def test_a_page_changed_after_the_approval_is_refused_until_it_is_approved_again(client, pid, proj, docs):
    _, h = turn(proj, pid)
    approve(client, pid, h)
    (docs / NAME).write_text(HTML + "<script>fetch('https://example.com/steal')</script>")
    r = check(client, h, abs_path(docs))
    assert r["ok"] is False and "changed after the Master approved it" in r["reason"] and "request_approval" in r["reason"]
    assert proj.db.q("SELECT * FROM artifacts") == []
    approve(client, pid, h)  # asks again with the page as it is now: a new pin
    assert check(client, h, abs_path(docs))["ok"] is True


def test_an_approval_for_another_page_feature_or_without_a_pinned_file_does_not_count(client, pid, proj, docs):
    _, h = turn(proj, pid)
    (docs / "artifact-other.html").write_text("<title>o</title>")
    approve(client, pid, h, "artifact-other.html")
    assert check(client, h, abs_path(docs))["ok"] is False  # another page's approval
    client.post(f"/api/p/{pid}/features", json={"text": "Second", "starter": "product"})
    proj.db.x("UPDATE features SET lead='lead' WHERE id=2")
    approve(client, pid, h, NAME, feature="F-2")  # the same title in another feature
    assert check(client, h, abs_path(docs))["ok"] is False
    aid = client.post("/internal/approvals", json={"feature": "F-1", "title": f"Artifact: {NAME}", "summary": "s"},
                      headers=h).json()["id"]
    client.post(f"/api/p/{pid}/approvals/{aid}/decide", json={"decision": "approve"})
    proj.db.x("DELETE FROM kv WHERE key=?", f"artifact_sha:{aid}")  # an approval made without a pin can't be matched
    r = check(client, h, abs_path(docs))
    assert r["ok"] is False and "pinned no page" in r["reason"]


def test_requesting_an_artifact_approval_pins_the_page_and_other_titles_pin_nothing(client, pid, proj, docs):
    _, h = turn(proj, pid)

    def req(title):
        return client.post("/internal/approvals", json={"feature": "F-1", "title": title, "summary": "s"}, headers=h).json()["id"]
    a = req(f"Artifact: {NAME}")
    assert proj.db.get_kv(f"artifact_sha:{a}") == hashlib.sha256(HTML.encode()).hexdigest()
    b = req("Artifact: nope.html")  # no such file: pinned as nothing
    assert proj.db.one("SELECT value FROM kv WHERE key=?", f"artifact_sha:{b}")["value"] == "null"
    for title in ("Artifact: ../x.html", "Artifact: sub/x.html", "Artifact:", "Design: Export", "Use skill: a:b"):
        assert proj.db.one("SELECT value FROM kv WHERE key=?", f"artifact_sha:{req(title)}") is None, title
    c = client.post("/internal/approvals", json={"title": f"Artifact: {NAME}", "summary": "s"}, headers=h).json()["id"]
    assert proj.db.one("SELECT value FROM kv WHERE key=?", f"artifact_sha:{c}") is None  # no feature, no page


# ---- after the first publish: updates by the recorded url only ----------------------------------------------------
def test_after_the_capture_only_updates_by_the_recorded_url_pass(client, pid, proj, docs):
    t, h = turn(proj, pid)
    approve(client, pid, h)
    assert check(client, h, abs_path(docs))["ok"] is True
    row = publish(proj, turn_id=t["id"])  # the daemon read the "Published ... (Version 1" line
    assert (row["url"], row["version"], row["title"]) == (URL, 1, "Export orders & more")
    assert len(proj.db.q("SELECT * FROM artifacts")) == 1  # the pending row was filled, not duplicated
    r = check(client, h, abs_path(docs))
    assert r["ok"] is False and URL in r["reason"] and "pass url" in r["reason"] and "new approval" in r["reason"]
    assert check(client, h, abs_path(docs), URL) == {"ok": True, "reason": ""}  # an update: no new approval
    r = check(client, h, abs_path(docs), URL2)
    assert r["ok"] is False and f"pass url: {URL}" in r["reason"]
    (docs / "artifact-y.html").write_text("<title>y</title>")
    other = check(client, h, abs_path(docs, "artifact-y.html"), URL)  # the url of a different page
    assert other["ok"] is False and "Artifact: artifact-y.html" in other["reason"]
    # the update lands: same row, version 2, a second card
    row2 = publish(proj, url=URL, version=2, turn_id=t["id"])
    assert row2["id"] == row["id"] and row2["version"] == 2
    msgs = [m for m in proj.db.messages("feature-1") if m["ref"] == f"artifact:{row['id']}"]
    assert [m["text"] for m in msgs] == [f"Published Export orders & more\n\n{URL}",
                                         f"Updated Export orders & more (v2)\n\n{URL}"]
    assert all(m["author"] == "frontend" and m["mentions"] == [] and m["artifact"]["version"] == 2 for m in msgs)
    assert proj.db.queued_wakes() == [w for w in proj.db.queued_wakes() if all(m["id"] not in w["message_ids"] for m in msgs)]


def test_an_update_by_url_with_no_recorded_page_points_at_the_approval(client, pid, proj, docs):
    _, h = turn(proj, pid)
    r = check(client, h, abs_path(docs), URL)
    assert r["ok"] is False and f"Artifact: {NAME}" in r["reason"]


def test_a_page_is_one_artifact_even_when_a_second_approval_exists(client, pid, proj, docs):
    t, h = turn(proj, pid)
    approve(client, pid, h)
    assert check(client, h, abs_path(docs))["ok"] is True
    publish(proj, turn_id=t["id"])
    approve(client, pid, h)  # the Master approves the same page again
    assert check(client, h, abs_path(docs))["ok"] is False  # still only an update by url can go through


# ---- the paths the gate refuses ------------------------------------------------------------------------------------
def test_paths_that_are_not_a_page_in_a_feature_docs_folder_are_refused(client, pid, proj, docs, repo, tmp_path):
    _, h = turn(proj, pid)
    approve(client, pid, h)
    outside = tmp_path / "x.html"
    outside.write_text("<title>o</title>")
    (docs / "notes.md").write_text("# n")
    (docs / "page.txt").write_text("x")
    (docs / "sub").mkdir()
    (docs / "sub" / "a.html").write_text("<title>s</title>")
    (docs / "dir.html").mkdir()
    os.symlink(docs / NAME, docs / "inside.html")
    os.symlink(outside, docs / "leak.html")
    (repo / ".aiteams" / "features" / "F-9").mkdir()
    (repo / ".aiteams" / "features" / "F-9" / "a.html").write_text("<title>9</title>")
    (repo / "README.html").write_text("<title>r</title>")
    bad = [str(outside), str(repo / "README.html"), str(docs / "notes.md"), str(docs / "page.txt"), str(docs / "sub" / "a.html"),
           str(docs / "dir.html"), str(docs / "inside.html"), str(docs / "leak.html"), str(docs / "missing.html"),
           str(repo / ".aiteams" / "features" / "F-9" / "a.html"), str(docs / ".." / ".." / ".." / "README.html"),
           str(docs) + "/", "", "   ", ".aiteams/features/F-1/../F-2/x.html"]
    for path in bad:
        r = check(client, h, path)
        assert r["ok"] is False and isinstance(r["reason"], str) and r["reason"], path
    assert check(client, h)["ok"] is False  # no path at all
    assert proj.db.q("SELECT * FROM artifacts") == []  # nothing was allowed, so nothing is pending
    assert check(client, h, abs_path(docs))["ok"] is True  # while the real page still is


def test_a_symlinked_docs_folder_is_refused(client, pid, proj, docs, repo, tmp_path):
    _, h = turn(proj, pid)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / NAME).write_text(HTML)
    client.post(f"/api/p/{pid}/features", json={"text": "Linked", "starter": "product"})
    proj.db.x("UPDATE features SET lead='lead' WHERE id=2")
    folder = repo / ".aiteams" / "features" / "F-2"
    if folder.is_dir() and not folder.is_symlink():
        folder.rmdir()
    os.symlink(elsewhere, folder)
    approve(client, pid, h, feature="F-2")
    assert check(client, h, str(folder / NAME))["ok"] is False


# ---- read and the actions that are the Master's ---------------------------------------------------------------------
def test_read_is_allowed_only_for_a_recorded_url(client, pid, proj, docs):
    t, h = turn(proj, pid)
    assert check(client, h, action="read", url=URL)["ok"] is False  # nothing recorded yet
    approve(client, pid, h)
    check(client, h, abs_path(docs))
    publish(proj, turn_id=t["id"])
    assert check(client, h, action="read", url=URL) == {"ok": True, "reason": ""}
    assert check(client, h, action="read", url=URL2)["ok"] is False
    assert check(client, h, action="read")["ok"] is False
    assert check(client, h, action="READ", url=URL)["ok"] is True


@pytest.mark.parametrize("action", ["delete", "list", "open", "pin", "unpin", "quickstart", "DELETE", " Delete ", "share",
                                    "unknown"])
def test_every_other_action_is_the_masters(client, pid, proj, docs, action):
    _, h = turn(proj, pid)
    approve(client, pid, h)
    r = check(client, h, abs_path(docs), action=action)
    assert r["ok"] is False and "the Master manages artifacts on claude.ai" in r["reason"]
    assert proj.db.q("SELECT * FROM artifacts") == []


def test_static_denial_is_an_allowlist_and_the_permission_route_agrees(client, pid, proj, docs):
    ok = {"file_path": REL, "icon": "x", "description": "d"}
    assert workflow.artifact_static_denial(ok) is None and workflow.artifact_static_denial({"action": "read"}) is None
    full = {"action": "publish", "file_path": REL, "url": URL, "title": "T", "description": "d", "icon": "i", "label": "l"}
    assert workflow.artifact_static_denial(full) is None
    assert workflow.artifact_static_denial({"action": "read", "url": URL, "page": 2}) is None
    for key, val in (("type_url", "https://x"), ("from_url", "https://x"), ("files", ["a"]), ("file_paths", ["a"]),
                     ("paths", ["a"]), ("root", "/"), ("path", "b.html"), ("asset", "x"), ("asset_ids", ["x"]),
                     ("out_dir", "/tmp"), ("capabilities", ["data"]), ("design_systems", ["x"]), ("contract", {"a": 1}),
                     ("scope", "org"), ("prompt", "p"), ("intent", "i"), ("force", True), ("overwrite_unread", True),
                     ("something_new", "x")):
        why = workflow.artifact_static_denial({**ok, key: val})
        assert why and f"remove {key}." in why, key
        assert workflow.artifact_static_denial({**ok, key: type(val)() if not isinstance(val, bool) else False}) is None  # empty = unused
    assert "remove paths, root." in workflow.artifact_static_denial({**ok, "paths": ["a"], "root": "/"})
    assert "only url, page" in workflow.artifact_static_denial({"action": "read", "url": URL, "file_path": REL})
    assert workflow.artifact_static_denial({"action": "delete"}) == workflow.ARTIFACT_DENIED
    _, h = turn(proj, pid)

    def perm(inp, tool="Artifact"):
        return client.post("/internal/permission", json={"tool_name": tool, "input": inp, "tool_use_id": "t1"}, headers=h).json()
    assert perm({"action": "delete", "url": URL})["behavior"] == "deny"
    assert perm({"action": "pin", "url": URL})["behavior"] == "deny"
    assert perm({**ok, "files": ["a", "b"]})["behavior"] == "deny"
    for key, val in (("paths", ["a"]), ("root", "/"), ("asset_ids", ["x"]), ("capabilities", ["data"]), ("force", True)):
        r = perm({**ok, key: val})
        assert r["behavior"] == "deny" and f"remove {key}." in r["message"]
    assert "claude.ai" in perm({"action": "delete"})["message"]
    assert perm(ok) == {"behavior": "allow", "updatedInput": ok}  # the hook already gated a publish; this route only refuses
    assert perm({"action": "delete"}, tool="Write")["behavior"] == "allow"  # other tools are untouched


def test_the_artifact_check_needs_a_turn_token(client, pid, proj, docs):
    assert client.get("/internal/artifact-check", params={"path": abs_path(docs)}).status_code == 403
    _, h = turn(proj, pid)
    assert client.get("/internal/artifact-check", params={"path": abs_path(docs)},
                      headers={**h, "X-AITeams-Token": "wrong"}).status_code == 403


# ---- the capture ----------------------------------------------------------------------------------------------------
def run_events(db, monkeypatch, events, setup, env=None):
    """A real TurnRunner turn of `backend` with a fake claude that prints `events`; setup(st, proj) runs first."""
    monkeypatch.setattr(turn_mod, "build_cmd", lambda *a, **k: fake_cmd())
    monkeypatch.setattr(turn_mod, "child_env", lambda *a, **k: fake_env(events, **(env or {})))
    st = AppState(7799)
    proj = Project({"id": "p1", "path": db.project_path, "name": "demo", "registered_at": 0})
    proj.hub = FakeHub()
    st.projects["p1"] = proj
    setup(st, proj)
    m = proj.db.add_message("dm-backend", "master", "Message", "hi", [], None)
    wake = proj.db.add_wake("backend", "dm-backend", [m["id"]], "master")
    t = asyncio.run(turn_mod.TurnRunner(st, proj, wake).run())
    return st, proj, t


def call(inp, tid="tu1"):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "Artifact", "input": inp}]}}


def result(text, tid="tu1"):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def feature_with_page(repo):
    def setup(st, proj):
        proj.db.create_feature("Export")
        d = repo / ".aiteams" / "features" / "F-1"
        d.mkdir(parents=True, exist_ok=True)
        (d / NAME).write_text(HTML)
    return setup


def test_a_turn_records_the_published_page_and_posts_the_link_card(db, repo, home, monkeypatch):
    page = str(repo / REL)
    line = f"Published {page} at {URL} (Version 1, version id 8f3a). The page is private; you cannot change sharing."
    st, proj, t = run_events(db, monkeypatch, [INIT, call({"file_path": page, "icon": "i", "description": "d"}), result(line),
                                               RESULT], feature_with_page(repo))
    assert t["state"] == "done"
    [row] = proj.db.q("SELECT * FROM artifacts")
    assert (row["feature_id"], row["path"], row["url"], row["title"], row["version"], row["role"], row["turn_id"]) == (
        1, REL, URL, "Export orders & more", 1, "backend", t["id"])
    [card] = [m for m in proj.db.messages("feature-1") if m["ref"]]
    assert card["author"] == "backend" and card["ref"] == f"artifact:{row['id']}" and card["mentions"] == []
    assert card["text"] == f"Published Export orders & more\n\n{URL}" and card["artifact"]["url"] == URL
    assert ("message", proj.db.messages("feature-1")[-1]) in proj.hub.events or any(e == "message" for e, _ in proj.hub.events)
    assert "system" in [e["type"] for e in proj.db.turn_events(t["id"])]  # the events are stored as before


def test_the_result_may_be_a_list_of_text_blocks_and_an_update_bumps_the_same_row(db, repo, home, monkeypatch):
    page = str(repo / REL)

    def setup(st, proj):
        feature_with_page(repo)(st, proj)
        proj.db.create_artifact(1, REL, "frontend", None, None, URL, "Old title", 1)
    blocks = [{"type": "text", "text": f"Published {page} at {URL} (Version 2, version id 9b)"},
              {"type": "text", "text": "more"}]
    st, proj, t = run_events(db, monkeypatch, [INIT, call({"file_path": page, "url": URL}), result(blocks), RESULT], setup)
    [row] = proj.db.q("SELECT * FROM artifacts")
    assert (row["version"], row["title"], row["role"], row["turn_id"]) == (2, "Export orders & more", "backend", t["id"])
    [card] = [m for m in proj.db.messages("feature-1") if m["ref"]]
    assert card["text"] == f"Updated Export orders & more (v2)\n\n{URL}"


def test_results_that_are_not_a_publish_of_one_of_our_pages_record_nothing(db, repo, home, monkeypatch):
    page = str(repo / REL)
    good = f"Published {page} at {URL} (Version 1, version id 1)"
    outside = f"Published /tmp/elsewhere/x.html at {URL2} (Version 1, version id 2)"
    events = [INIT,
              call({"file_path": page}, "a"), result("Error: the Artifact tool failed", "a"),        # an error text
              call({"file_path": page}, "b"), result(good, "zzz"),                                   # another call's id
              {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c", "content": good}]}},  # no call
              call({"file_path": "/tmp/elsewhere/x.html"}, "d"), result(outside, "d"),               # not a feature docs page
              {"type": "user", "message": {"content": "plain string content"}},
              {"type": "user", "message": {"content": [None, "x", {"type": "text"}]}},
              RESULT]
    st, proj, t = run_events(db, monkeypatch, events, feature_with_page(repo))
    assert t["state"] == "done" and t["error"] is None
    assert proj.db.q("SELECT * FROM artifacts") == [] and [m for m in proj.db.messages("feature-1") if m["ref"]] == []


def test_a_failing_capture_never_fails_the_turn(db, repo, home, monkeypatch):
    page = str(repo / REL)

    async def boom(*a, **k):
        raise RuntimeError("capture blew up")
    monkeypatch.setattr(workflow, "record_artifact", boom)
    line = f"Published {page} at {URL} (Version 1, version id 1)"
    st, proj, t = run_events(db, monkeypatch, [INIT, call({"file_path": page}), result(line), RESULT], feature_with_page(repo))
    assert t["state"] == "done" and t["error"] is None


def test_a_turn_that_ends_without_a_capture_gives_its_pending_row_back(db, repo, home, monkeypatch):
    holder = {}

    def setup(st, proj):
        feature_with_page(repo)(st, proj)
        tid = proj.db.one("SELECT COALESCE(MAX(id), 0) + 1 AS n FROM turns")["n"]  # the id the runner will take
        holder["row"] = proj.db.create_artifact(1, REL, "backend", None, tid)
        holder["other"] = proj.db.create_artifact(1, REL + "2", "backend", None, tid + 50)  # some other turn's row
    st, proj, t = run_events(db, monkeypatch, [INIT, RESULT], setup)
    assert t["id"] == holder["row"]["turn_id"]
    assert proj.db.artifact(holder["row"]["id"]) is None and proj.db.artifact(holder["other"]["id"]) is not None
    assert proj.db.drop_pending_artifacts(12345) == 0


def test_pending_rows_have_no_page_in_the_docs_list(client, pid, proj, docs):
    proj.db.create_artifact(1, REL, "frontend", None, 1)
    assert [d["kind"] for d in client.get(f"/api/p/{pid}/features/1/docs").json()] == ["html"]


# ---- the tool is missing: a visible line, once ------------------------------------------------------------------------
def general_system_lines(proj):
    return [m["text"] for m in proj.db.messages("general") if m["author"] == "system"]


def test_a_missing_artifact_tool_says_so_once_per_daemon_run(db, repo, home, monkeypatch):
    on = {"CLAUDE_CODE_ARTIFACT": "1"}
    st, proj, t = run_events(db, monkeypatch, [{**INIT, "tools": ["Bash", "Read"]}, RESULT], lambda s, p: None, on)
    assert general_system_lines(proj) == [workflow.ARTIFACT_MISSING] and st.artifact_missing_notified is True
    assert "Local mocks still work" in workflow.ARTIFACT_MISSING and "unavailable" in workflow.ARTIFACT_MISSING
    m = proj.db.add_message("dm-backend", "master", "Message", "again", [], None)
    again = asyncio.run(turn_mod.TurnRunner(st, proj, proj.db.add_wake("backend", "dm-backend", [m["id"]], "master")).run())
    assert again["state"] == "done" and len(general_system_lines(proj)) == 1  # not repeated
    assert ("message", proj.db.messages("general")[-1]) in proj.hub.events or any(e == "message" for e, _ in proj.hub.events)


def test_no_line_when_the_tool_is_there_or_the_switch_is_not_set_or_init_lists_no_tools(db, repo, home, monkeypatch):
    on = {"CLAUDE_CODE_ARTIFACT": "1"}
    _, proj, _ = run_events(db, monkeypatch, [{**INIT, "tools": ["Bash", "Artifact"]}, RESULT], lambda s, p: None, on)
    assert general_system_lines(proj) == []
    proj.db.close()
    _, proj, _ = run_events(db, monkeypatch, [{**INIT, "tools": ["Bash"]}, RESULT], lambda s, p: None, {})  # no switch
    assert general_system_lines(proj) == []
    proj.db.close()
    no_tools = {k: v for k, v in INIT.items() if k != "tools"}
    _, proj, _ = run_events(db, monkeypatch, [no_tools, RESULT], lambda s, p: None, on)
    assert general_system_lines(proj) == []


# ---- the turn's setup ---------------------------------------------------------------------------------------------------
def test_role_turns_get_the_artifact_switch_and_never_the_comment_or_data_tools():
    env = child_env("http://127.0.0.1:7700", "p1", "/tmp/p", "backend", 7, 600, "tok")
    assert env["CLAUDE_CODE_ARTIFACT"] == "1" and env["CLAUDE_CODE_ARTIFACT_AUTO_OPEN"] == "0"
    assert "CLAUDE_CODE_ARTIFACT" not in aiteams_env(env)  # the MCP server doesn't need it
    cmd = build_cmd({"max_turns": 40}, "backend", env, "/tmp/sys.md", "s1", False, "opus")
    banned = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--model")]
    assert {"SendMessage", "ListAgents", "ArtifactComments", "ArtifactData"} <= set(banned) and "Artifact" not in banned
    assert "Artifact" in HOOK_TOOLS.split("|")  # every Artifact call goes through the hook


def test_the_daemons_own_artifact_switch_is_not_inherited(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ARTIFACT", "0")
    monkeypatch.setenv("CLAUDE_CODE_ARTIFACT_AUTO_OPEN", "1")
    env = child_env("http://127.0.0.1:7700", "p1", "/tmp/p", "qa", 7, 600, "tok")
    assert env["CLAUDE_CODE_ARTIFACT"] == "1" and env["CLAUDE_CODE_ARTIFACT_AUTO_OPEN"] == "0"


# ---- docs list, messages, role header --------------------------------------------------------------------------------
def test_published_artifacts_come_last_in_the_docs_list_and_the_role_header(client, pid, proj, docs, repo):
    (docs / "prd.md").write_text("# prd")
    (docs / "mock-a.html").write_text("<title>m</title>")
    (docs / "shot.png").write_bytes(b"\x89PNG")
    proj.db.create_artifact(1, REL, "frontend", None, 1)  # pending: not listed
    a = proj.db.create_artifact(1, REL, "frontend", None, None, URL, "Export orders", 3)
    listed = client.get(f"/api/p/{pid}/features/1/docs").json()
    assert [(d["kind"], d["name"]) for d in listed] == [
        ("md", "prd.md"), ("html", NAME), ("html", "mock-a.html"), ("image", "shot.png"), ("artifact", "Export orders")]
    art = listed[-1]
    assert (art["url"], art["version"], art["path"], art["editor"], art["id"]) == (URL, 3, REL, "frontend", a["id"])
    assert art["size"] == len(HTML) and art["updated_at"] > 0
    header = workflow.feature_header(proj.db, str(repo), proj.db.feature(1), with_sizes=True)
    assert "Export orders (frontend," in header and header.index("shot.png") < header.index("Export orders (")
    (docs / NAME).unlink()  # the source file is gone: the artifact is still listed, with no size
    assert client.get(f"/api/p/{pid}/features/1/docs").json()[-1]["size"] == 0


def test_artifacts_of_other_features_are_not_listed_and_the_message_join_carries_the_row(client, pid, proj, docs):
    client.post(f"/api/p/{pid}/features", json={"text": "Second", "starter": "product"})
    proj.db.create_artifact(2, ".aiteams/features/F-2/artifact-z.html", "qa", None, None, URL2, "Z", 1)
    assert [d["kind"] for d in client.get(f"/api/p/{pid}/features/1/docs").json()] == ["html"]
    a = proj.db.create_artifact(1, REL, "frontend", None, None, URL, "Export orders", 2)
    msg = proj.db.add_message("feature-1", "frontend", "Message", f"Updated Export orders (v2)\n\n{URL}", [], None, f"artifact:{a['id']}")
    got = [m for m in client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json() if m["id"] == msg["id"]][0]
    assert got["artifact"]["url"] == URL and got["artifact"]["version"] == 2 and got["approval"] is None
    plain = proj.db.add_message("feature-1", "frontend", "Message", "no ref", [], None)
    assert proj.db.message(plain["id"])["artifact"] is None
    bad = proj.db.add_message("feature-1", "frontend", "Message", "x", [], None, "artifact:9999")
    assert proj.db.message(bad["id"])["artifact"] is None  # a card whose row is gone joins to nothing
