"""F-40: every approval a role requests carries a three-part brief (What / If you approve / Trade-offs) and may point at one
doc; the daemon refuses a request that lacks a part, renders `summary` from the brief, and moves a feature to done when a
`Done:` approval is approved."""
import os

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, BRIEF, BRIEF_SUMMARY, TOKEN, running_turn

GOOD = {"what": "Approve the redesign.", "if_approved": "Backend builds it, then Frontend.", "tradeoffs": ["one migration", "a side panel"]}


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
def feat(client, pid, proj, repo):
    """F-1 (Lead 1's) with a docs folder holding a PRD, a design, a mock and a plain-text file."""
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    proj.db.x("UPDATE features SET lead='lead' WHERE id=1")
    d = repo / ".aiteams" / "features" / "F-1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "design.md").write_text("# Design\n")
    (d / "prd.md").write_text("# PRD\n")
    (d / "mock-x.html").write_text("<p>mock</p>")
    (d / "notes.txt").write_text("plain")
    return d


def hdr(proj, pid, role="product"):
    return running_turn(proj.db, role, pid=pid)[1]


def ask(client, headers, **body):
    return client.post("/internal/approvals", json=body, headers=headers)


def test_the_brief_is_stored_and_decoded_on_the_list_the_message_join_and_the_frame(client, pid, proj, feat):
    h = hdr(proj, pid)
    with client.websocket_connect(f"/ws/p/{pid}?token={TOKEN}") as ws:
        r = ask(client, h, feature="F-1", title="Scope lock: Export", **GOOD, doc="prd.md")
        assert r.status_code == 200, r.text
        frames = [ws.receive_json() for _ in range(4)]
    want = {**GOOD, "doc": "prd.md"}
    frame = next(f["data"] for f in frames if f["event"] == "approval")
    assert frame["brief"] == want and frame["summary"] == frame["summary"].strip()
    assert client.get(f"/api/p/{pid}/approvals").json()[0]["brief"] == want
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert m["approval"]["brief"] == want
    assert proj.db.approval(r.json()["id"])["brief"] == want
    assert client.get(f"/api/p/{pid}/inbox").json()["approvals"][0]["brief"] == want


def test_the_summary_and_the_chat_message_are_the_brief_rendered_as_markdown(client, pid, proj, feat):
    h = hdr(proj, pid)
    a = ask(client, h, feature="F-1", title="Scope lock: Export", summary="ignore me", **GOOD, doc="design.md").json()
    text = ("**What:** Approve the redesign.\n**If you approve:** Backend builds it, then Frontend.\n**Trade-offs:**\n"
            "- one migration\n- a side panel\n**Doc:** `.aiteams/features/F-1/design.md`")
    assert proj.db.approval(a["id"])["summary"] == text  # `summary` is ignored when a brief is given
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert m["text"] == "**Approval requested: Scope lock: Export**\n\n" + text
    b = ask(client, h, feature="F-1", title="Another", **BRIEF).json()
    assert proj.db.approval(b["id"])["summary"] == BRIEF_SUMMARY and proj.db.approval(b["id"])["brief"]["doc"] is None


@pytest.mark.parametrize("omit,named", [
    ({"what": None}, "what"), ({"what": ""}, "what"), ({"what": "   "}, "what"),
    ({"if_approved": None}, "if_approved"), ({"if_approved": " "}, "if_approved"),
    ({"tradeoffs": None}, "tradeoffs"), ({"tradeoffs": []}, "tradeoffs"),
    ({"what": None, "if_approved": None}, "what, if_approved"),
    ({"what": None, "if_approved": None, "tradeoffs": None}, "what, if_approved, tradeoffs"),
])
def test_a_missing_or_empty_part_is_refused_naming_it_and_nothing_is_filed(client, pid, proj, feat, omit, named):
    body = {**GOOD, **omit}
    r = ask(client, hdr(proj, pid), feature="F-1", title="Scope lock: x", **{k: v for k, v in body.items() if v is not None})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("brief needs What / If you approve / Trade-offs (1-3): missing " + named)
    assert proj.db.approvals("all") == []
    assert [m for m in client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json() if m["type"] == "Approval"] == []


def test_the_old_summary_only_call_is_refused(client, pid, proj, feat):
    r = ask(client, hdr(proj, pid), feature="F-1", title="Scope lock: x", summary="- csv only")
    assert r.status_code == 400 and "missing what, if_approved, tradeoffs" in r.json()["detail"]
    assert "summary is no longer used" in r.json()["detail"]
    assert proj.db.approvals("all") == []


def test_the_limits(client, pid, proj, feat):
    h = hdr(proj, pid)

    def go(**over):
        return ask(client, h, feature="F-1", title="Scope lock: x", **{**GOOD, **over})
    assert go(what="w" * 200, if_approved="i" * 300, tradeoffs=["t" * 200, "u", "v"]).status_code == 200  # the edges pass
    r = go(what="w" * 201)
    assert r.status_code == 400 and "what is too long: 201 characters, at most 200" in r.json()["detail"]
    r = go(what="two\nlines")
    assert r.status_code == 400 and "what must be one line" in r.json()["detail"]
    r = go(if_approved="i" * 301)
    assert r.status_code == 400 and "if_approved is too long: 301 characters, at most 300" in r.json()["detail"]
    assert go(if_approved="one line\nanother line").status_code == 200  # one or two lines
    r = go(tradeoffs=["a", "b", "c", "d"])
    assert r.status_code == 400 and "tradeoffs has 4 items, at most 3" in r.json()["detail"]
    r = go(tradeoffs=["a", "b" * 201])
    assert r.status_code == 400 and "tradeoffs item 2 is too long: 201 characters, at most 200" in r.json()["detail"]
    r = go(tradeoffs=["a", "  "])
    assert r.status_code == 400 and "tradeoffs item 2 is empty" in r.json()["detail"]
    assert go(tradeoffs=["none"]).status_code == 200
    assert len(proj.db.approvals("all")) == 3  # the three that passed; every refusal filed nothing


@pytest.mark.parametrize("doc", ["design.md", ".aiteams/features/F-1/design.md", "./.aiteams/features/F-1/design.md", "  design.md "])
def test_doc_is_a_bare_name_or_the_full_path_and_is_stored_as_the_bare_name(client, pid, proj, feat, repo, doc):
    a = ask(client, hdr(proj, pid), feature="F-1", title="Scope lock: x", **GOOD, doc=doc)
    assert a.status_code == 200, a.text
    assert proj.db.approval(a.json()["id"])["brief"]["doc"] == "design.md"


def test_doc_with_the_absolute_project_path_is_stored_as_the_bare_name(client, pid, proj, feat, repo):
    for base in (str(repo), os.path.realpath(repo)):
        a = ask(client, hdr(proj, pid), feature="F-1", title="Scope lock: x", **GOOD, doc=f"{base}/.aiteams/features/F-1/prd.md")
        assert a.status_code == 200, a.text
        assert proj.db.approval(a.json()["id"])["brief"]["doc"] == "prd.md"


def test_doc_may_be_a_mock(client, pid, proj, feat):
    a = ask(client, hdr(proj, pid), feature="F-1", title="Scope lock: x", **GOOD, doc="mock-x.html")
    assert a.status_code == 200 and proj.db.approval(a.json()["id"])["brief"]["doc"] == "mock-x.html"


@pytest.mark.parametrize("doc,why", [
    ("nope.md", "doesn't exist in F-1's docs folder"),
    (".aiteams/features/F-1/nope.md", "doesn't exist in F-1's docs folder"),
    (".aiteams/features/F-2/design.md", "must be a file in F-1's docs folder"),   # another feature's folder
    ("src/orders.py", "must be a file in F-1's docs folder"),
    ("../design.md", "must be a file in F-1's docs folder"),
    ("/etc/passwd", "must be a file in F-1's docs folder"),
    ("sub/design.md", "must be a file in F-1's docs folder"),
    ("..", "bad file name"),
    ("notes.txt", "doc must be a .md file, an .html page or an image"),
])
def test_a_bad_doc_is_a_400_and_nothing_is_filed(client, pid, proj, feat, doc, why):
    r = ask(client, hdr(proj, pid), feature="F-1", title="Scope lock: x", **GOOD, doc=doc)
    assert r.status_code == 400 and why in r.json()["detail"], r.json()
    assert proj.db.approvals("all") == []


def test_a_doc_that_is_a_symlink_is_refused(client, pid, proj, feat, repo):
    (repo / "secret.md").write_text("s")
    os.symlink(repo / "secret.md", feat / "linked.md")
    r = ask(client, hdr(proj, pid), feature="F-1", title="Scope lock: x", **GOOD, doc="linked.md")
    assert r.status_code == 400 and "symlinks are not served" in r.json()["detail"]


def test_a_doc_needs_a_feature(client, pid, proj, feat):
    h = hdr(proj, pid, "qa")
    r = ask(client, h, title="Skip flaky test?", **GOOD, doc="design.md")
    assert r.status_code == 400 and "doc needs a feature" in r.json()["detail"] and proj.db.approvals("all") == []
    ok = ask(client, h, title="Skip flaky test?", **GOOD)  # no feature and no doc is fine
    assert ok.status_code == 200 and proj.db.approval(ok.json()["id"])["brief"] == {**GOOD, "doc": None}
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "dm-qa"}).json()[-1]
    assert m["text"] == "**Approval requested: Skip flaky test?**\n\n" + proj.db.approval(ok.json()["id"])["summary"]


def test_a_lone_string_for_tradeoffs_is_a_422_on_the_route_but_the_tool_wraps_it(client, pid, proj, feat):
    r = client.post("/internal/approvals", json={"feature": "F-1", "title": "x", **GOOD, "tradeoffs": "none"}, headers=hdr(proj, pid))
    assert r.status_code == 422  # the MCP tool wraps a lone string into a list before it posts (tests/test_mcp_server.py)


def test_the_design_title_still_needs_the_features_lead(client, pid, proj, feat):
    r = ask(client, hdr(proj, pid, "lead-2"), feature="F-1", title="Design: x", **GOOD, doc="design.md")  # F-1 is Lead 1's
    assert r.status_code == 409 and proj.db.approvals("all") == []
    ok = ask(client, hdr(proj, pid, "lead"), feature="F-1", title="Design: x", **GOOD, doc="design.md")
    assert ok.status_code == 200 and client.get(f"/api/p/{pid}/features/1").json()["design"]["state"] == "pending"


def test_daemon_internal_calls_may_pass_no_brief_and_keep_their_summary(client, pid, proj, feat):
    st = client.app.state.app
    a = client.portal.call(workflow.request_approval, st, proj, "lead", None, "F-1", "Branch: F-1", "Proposed: work in a tree.")
    assert a["brief"] is None and a["summary"] == "Proposed: work in a tree."
    m = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert m["text"] == "**Approval requested: Branch: F-1**\n\nProposed: work in a tree." and m["approval"]["brief"] is None


def test_an_approval_row_filed_before_the_brief_reads_none_everywhere(client, pid, proj, feat):
    old = proj.db.create_approval(1, "product", None, "Scope lock: old", "- csv only")  # no brief: an old row
    assert proj.db.approval(old["id"])["brief"] is None
    assert client.get(f"/api/p/{pid}/approvals").json()[0]["brief"] is None
    assert client.get(f"/api/p/{pid}/inbox").json()["approvals"][0]["brief"] is None
    r = client.post(f"/api/p/{pid}/approvals/{old['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["brief"] is None and r.json()["summary"] == "- csv only"


def test_approving_a_done_request_moves_the_feature_to_done(client, pid, proj, feat):
    h = hdr(proj, pid, "qa")
    a = ask(client, h, feature="F-1", title="Done: Export", **GOOD, doc="design.md").json()["id"]
    assert client.get(f"/api/p/{pid}/features/1").json()["phase"] != "done"
    r = client.post(f"/api/p/{pid}/approvals/{a}/decide", json={"decision": "approve"})
    assert r.status_code == 200
    assert client.get(f"/api/p/{pid}/features/1").json()["phase"] == "done"
    last = client.get(f"/api/p/{pid}/messages", params={"conversation": "feature-1"}).json()[-1]
    assert "Approved: Done: Export (phase → done)" in last["text"]
    assert proj.db.feature(1)["done_at"] is not None


def test_done_titles_match_like_the_other_gates_trimmed_and_case_insensitive(client, pid, proj, feat):
    a = ask(client, hdr(proj, pid, "qa"), feature="F-1", title="  done: Export ", **GOOD).json()["id"]
    client.post(f"/api/p/{pid}/approvals/{a}/decide", json={"decision": "approve"})
    assert client.get(f"/api/p/{pid}/features/1").json()["phase"] == "done"


def test_an_explicit_set_phase_wins_over_the_done_default(client, pid, proj, feat):
    a = ask(client, hdr(proj, pid, "qa"), feature="F-1", title="Done: Export", **GOOD).json()["id"]
    r = client.post(f"/api/p/{pid}/approvals/{a}/decide", json={"decision": "approve", "set_phase": "qa"})
    assert r.status_code == 200 and client.get(f"/api/p/{pid}/features/1").json()["phase"] == "qa"


@pytest.mark.parametrize("decision", ["changes", "reject"])
def test_only_an_approval_of_a_done_request_moves_the_phase(client, pid, proj, feat, decision):
    a = ask(client, hdr(proj, pid, "qa"), feature="F-1", title="Done: Export", **GOOD).json()["id"]
    before = client.get(f"/api/p/{pid}/features/1").json()["phase"]
    client.post(f"/api/p/{pid}/approvals/{a}/decide", json={"decision": decision, "note": "not yet"})
    assert client.get(f"/api/p/{pid}/features/1").json()["phase"] == before


def test_other_titles_leave_the_phase_alone_and_a_done_without_a_feature_is_fine(client, pid, proj, feat):
    before = client.get(f"/api/p/{pid}/features/1").json()["phase"]
    a = ask(client, hdr(proj, pid), feature="F-1", title="Scope lock: Export", **GOOD).json()["id"]
    client.post(f"/api/p/{pid}/approvals/{a}/decide", json={"decision": "approve"})
    assert client.get(f"/api/p/{pid}/features/1").json()["phase"] == before
    b = ask(client, hdr(proj, pid, "qa"), title="Done: nothing", **GOOD).json()["id"]  # no feature: nothing to move
    assert client.post(f"/api/p/{pid}/approvals/{b}/decide", json={"decision": "approve"}).status_code == 200
