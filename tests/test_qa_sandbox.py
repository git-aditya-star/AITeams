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


def test_fire_reaction(sandbox):
    from aiteams.db import EMOJI
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        m = c.post("/qa/fire", json={"kind": "message", "text": "react to me"}, headers=auth).json()
        with c.websocket_connect("/ws/p/demo?token=sb-token") as ws:
            r = c.post("/qa/fire", json={"kind": "reaction", "role": "qa", "emoji": EMOJI[3]}, headers=auth).json()
            frame = next(f for f in (ws.receive_json() for _ in range(5)) if f["event"] != "hello")
        assert r["message"] == m["message"] and r["added"] is True
        assert frame == {"event": "reaction", "data": {"message_id": m["message"],
                                                       "reactions": [{"emoji": EMOJI[3], "roles": ["qa"]}]}}
        msgs = c.get("/api/p/demo/messages", params={"conversation": r["conversation"]}, headers=auth).json()
        assert msgs[-1]["reactions"] == [{"emoji": EMOJI[3], "roles": ["qa"]}]
        r = c.post("/qa/fire", json={"kind": "reaction", "message_id": m["message"], "emoji": EMOJI[3], "role": "qa"},
                   headers=auth).json()
        assert r["added"] is False and r["reactions"] == []


def test_fire_card(sandbox):
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        r = c.post("/qa/fire", json={"kind": "card", "role": "product"}, headers=auth).json()
        q = next(x for x in c.get("/api/p/demo/inbox", headers=auth).json()["questions"] if x["id"] == r["question"])
        assert (q["kind"], q["purpose"], len(q["payload"]["questions"])) == ("card", "interview", 2)
        assert q["payload"]["questions"][1]["multiSelect"] is True
        msgs = c.get("/api/p/demo/messages", params={"conversation": r["conversation"]}, headers=auth).json()
        assert msgs[-1]["ref"] == f"question:{r['question']}" and msgs[-1]["question"]["id"] == r["question"]
        w = c.post("/qa/fire", json={"kind": "card_withdraw", "question": r["question"]}, headers=auth).json()
        assert w["state"] == "withdrawn"


def test_fire_commit_group(sandbox):
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        r = c.post("/qa/fire", json={"kind": "commit_group", "lead_review": True}, headers=auth).json()
        g = next(g for g in c.get("/api/p/demo/inbox", headers=auth).json()["groups"] if g["id"] == r["group"])
        assert [m["id"] for m in g["members"]] == r["commits"] and g["ready"] is False
        r = c.post("/qa/fire", json={"kind": "commit_group"}, headers=auth).json()
        g = next(g for g in c.get("/api/p/demo/inbox", headers=auth).json()["groups"] if g["id"] == r["group"])
        assert g["ready"] is True and g["files"] == 2
        d = c.post(f"/api/p/demo/commits/{r['group']}/decide", json={"decision": "approve", "group": True},
                   headers=auth).json()
        assert [m["state"] for m in d["group"]] == ["committed", "committed"]


def test_fire_brainstorm(sandbox):
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        r = c.post("/qa/fire", json={"kind": "brainstorm", "text": "Big files?"}, headers=auth).json()
        assert r["brainstorm"] and "product" in r["roles"] and "lead" in r["roles"]
        msgs = c.get("/api/p/demo/messages", params={"conversation": r["conversation"]}, headers=auth).json()
        assert msgs[-1]["id"] == r["message"] and msgs[-1]["brainstorm"]["state"] == "collecting"
        assert msgs[-1]["text"].startswith(f"Brainstorm #{r['brainstorm']} on Big files?")
        fid = c.get("/api/p/demo/features", headers=auth).json()
        fid = next(f["id"] for f in fid if f["conversation_id"] == r["conversation"])
        rounds = c.get(f"/api/p/demo/features/{fid}/brainstorms", headers=auth).json()
        assert [b["id"] for b in rounds] == [r["brainstorm"]]
        again = c.post(f"/api/p/demo/features/{fid}/brainstorm", json={"topic": "x"}, headers=auth)
        assert again.status_code == 409


def test_seed_gives_the_dashboard_pictures_data(sandbox):
    """F-18: the backdated chat spreads over several hours, F-3's committed request is counted, and the shipped
    timeline holds the two done features (F-3 newest, with its summary)."""
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    with TestClient(app) as c:
        d = c.get("/api/p/demo/dashboard", headers={"Authorization": "Bearer sb-token"}).json()
        busy = [i for i, b in enumerate(d["activity_hourly"]) if b["messages"]]
        assert len(busy) >= 4 and busy[-1] == 23  # 9h, 5h, 2h ago and now
        assert sum(b["commits"] for b in d["activity_hourly"]) == 1
        tl = d["shipped"]["timeline"]
        assert [f["key"] for f in tl] == ["F-3", "F-2"] and tl[0]["summary"]


def test_seed_f1_has_an_approved_design(sandbox):
    """F-21: F-1's seeded commit request needs an approved `Design:` approval; the PRD approval stays #1 and the
    design one is already resolved, so the Inbox still shows one approval."""
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        f1 = c.get("/api/p/demo/features/1", headers=auth).json()
        assert f1["design"] == {"state": "approved", "approval_id": 2, "gated": True}
        approvals = c.get("/api/p/demo/inbox", headers=auth).json()["approvals"]
        assert [a["id"] for a in approvals] == [1]


def test_seed_has_lead_1_and_lead_2_features(sandbox):
    """F-22: F-1..F-3 belong to Lead 1 (as migration 9 leaves existing features), the active F-4 to Lead 2; the seed
    adds it last, so the ids and order the other specs rely on don't move."""
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        feats = {f["key"]: f for f in c.get("/api/p/demo/features", params={"history": 1}, headers=auth).json()}
        assert {k: f["lead"] for k, f in feats.items()} == {"F-1": "lead", "F-2": "lead", "F-3": "lead", "F-4": "lead-2"}
        assert feats["F-4"]["phase"] == "build" and feats["F-4"]["title"] == "Order export schedule"
        dash = c.get("/api/p/demo/dashboard", headers=auth).json()
        assert [f["key"] for f in dash["features"]] == ["F-4", "F-1"]  # active, newest first
        assert [f["key"] for f in dash["shipped"]["timeline"]] == ["F-3", "F-2"]
        inbox = c.get("/api/p/demo/inbox", headers=auth).json()
        assert [x["id"] for x in inbox["commits"]] == [1] and [a["id"] for a in inbox["approvals"]] == [1]


def test_fire_message_as_lead_2(sandbox):
    """F-22: qa.fire('message', {role: 'lead-2'}) posts as Lead 2 (the e2e specs rely on it)."""
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        r = c.post("/qa/fire", json={"kind": "message", "role": "lead-2", "text": "from Lead 2"}, headers=auth).json()
        msgs = c.get("/api/p/demo/messages", params={"conversation": r["conversation"]}, headers=auth).json()
        assert msgs[-1]["author"] == "lead-2" and msgs[-1]["text"] == "from Lead 2"


def test_fire_plugin_missing_flags_a_row_and_reset_clears_it(sandbox):
    """F-23: qa.fire('plugin_missing') adds an On row that no turn loaded: a WS frame, GET /api/settings/plugins status,
    one toast per flip; {loaded: true} clears it; reset drops the row and the status."""
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    notes = "project-notes@project-notes"
    with TestClient(app) as c:
        assert c.get("/api/settings/plugins", headers=auth).json() == {"rows": [], "status": {}}
        with c.websocket_connect("/ws/p/demo?token=sb-token") as ws:
            r = c.post("/qa/fire", json={"kind": "plugin_missing", "role": "frontend"}, headers=auth).json()
            assert r["plugin"] == notes and r["flagged"] == [notes]
            frames = [ws.receive_json() for _ in range(2)]  # the socket's hello, then the event
        assert frames[1] == {"event": "plugin_missing", "data": {"id": notes, "role": "frontend"}}
        got = c.get("/api/settings/plugins", headers=auth).json()
        assert got["rows"] == [{"id": notes, "on": True, "ask": False}]
        assert got["status"][notes]["missing"] is True and got["status"][notes]["role"] == "frontend"
        assert c.post("/qa/fire", json={"kind": "plugin_missing"}, headers=auth).json()["flagged"] == []  # once per flip
        assert c.post("/qa/fire", json={"kind": "plugin_missing", "loaded": True}, headers=auth).json()["flagged"] == []
        assert c.get("/api/settings/plugins", headers=auth).json()["status"][notes]["missing"] is False
        c.post("/qa/fire", json={"kind": "plugin_missing", "id": "acme@market"}, headers=auth)
        assert [r["id"] for r in c.get("/api/settings/plugins", headers=auth).json()["rows"]] == [notes, "acme@market"]
        assert c.post("/qa/reset", headers=auth).json() == {"ok": True}
        assert c.get("/api/settings/plugins", headers=auth).json() == {"rows": [], "status": {}}


def test_seed_has_a_changes_requested_and_a_rejected_approval_on_f3(sandbox):
    """F-25: two resolved approvals on F-3, created last with no message, so History's Approvals tab and the badge shots
    have data and no seeded id moved (PRD #1 and the approved Design #2 keep their ids)."""
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        rows = {a["id"]: a for a in c.get("/api/p/demo/approvals", params={"state": "all"}, headers=auth).json()}
        assert sorted(rows) == [1, 2, 3, 4]
        assert (rows[1]["state"], rows[2]["state"]) == ("pending", "approved") and rows[2]["title"].startswith("Design:")
        f3 = c.app.state.app.projects["demo"].db.feature("F-3")["id"]
        assert (rows[3]["state"], rows[3]["feature_id"]) == ("changes_requested", f3) and rows[3]["note"]
        assert (rows[4]["state"], rows[4]["feature_id"]) == ("rejected", f3) and rows[4]["note"]
        assert all(rows[i]["message_id"] is None and rows[i]["resolved_at"] for i in (3, 4))
        assert [r["id"] for r in c.get("/api/p/demo/approvals", params={"state": "rejected"}, headers=auth).json()] == [4]
        assert len(c.get("/api/p/demo/inbox", headers=auth).json()["approvals"]) == 1  # still only the PRD card
        new = c.post("/qa/fire", json={"kind": "approval"}, headers=auth).json()["approval"]
        assert new == 5  # fired approvals follow the seeded ones


def test_seed_has_mocks_a_diagram_and_the_two_chat_messages_that_show_them(sandbox):
    """F-29: F-1's docs hold a mock page, a generated PNG and an SVG, design.md ends with a mermaid block, and the last two
    F-1 messages link the mock and carry a diagram. The seeded ids before them are unchanged."""
    import struct
    mod, work, _ = sandbox
    app = mod.build_app(8790, work, "demo")
    auth = {"Authorization": "Bearer sb-token"}
    with TestClient(app) as c:
        docs = c.get("/api/p/demo/features/1/docs", headers=auth).json()
        assert [(d["name"], d["kind"]) for d in docs] == [
            ("design.md", "md"), ("prd.md", "md"), ("flow.svg", "image"), ("mock-export.html", "html"), ("shot.png", "image")]
        html = c.get("/api/p/demo/features/1/files/mock-export.html", headers=auth)
        assert html.status_code == 200 and "sandbox" in html.headers["content-security-policy"]
        # the probe is offline (no CDN) and writes its verdict into its own body
        assert "http" not in html.text.replace("http://www.w3.org", "") and "parent.document" in html.text
        assert "fetch('/api/projects')" in html.text and "parent: blocked" in html.text and "fetch: blocked" in html.text
        png = c.get("/api/p/demo/features/1/files/shot.png", headers=auth).content
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (160, 100)
        assert c.get("/api/p/demo/features/1/files/flow.svg", headers=auth).text.startswith("<svg")
        design = c.get("/api/p/demo/features/1/docs/design.md", headers=auth).json()["text"]
        assert design.startswith("# F-1 design") and "| piece | owner | notes |" in design
        assert design.rstrip().endswith("```") and "```mermaid\nflowchart LR" in design
        msgs = c.get("/api/p/demo/messages", params={"conversation": "feature-1"}, headers=auth).json()
        link, flow = msgs[-2], msgs[-1]
        assert link["author"] == "frontend" and link["mentions"] == []
        assert ".aiteams/features/F-1/mock-export.html" in link["text"] and ".aiteams/features/F-1/shot.png" in link["text"]
        assert flow["author"] == "backend" and "```mermaid\nsequenceDiagram" in flow["text"] and flow["mentions"] == []
        assert msgs[-3]["id"] < link["id"] < flow["id"]
        # nothing earlier moved: PRD approval is #1, the approved design #2, the first commit request #1
        inbox = c.get("/api/p/demo/inbox", headers=auth).json()
        assert [a["id"] for a in inbox["approvals"]] == [1] and [x["id"] for x in inbox["commits"]] == [1]
        assert c.post("/qa/fire", json={"kind": "message"}, headers=auth).json()["conversation"] == "feature-1"
