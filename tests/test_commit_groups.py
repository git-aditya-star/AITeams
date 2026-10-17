"""F-11 R1: commit groups — `requires` links requests; the Inbox shows a group as one item; the Master commits a group
in dependency order, all or nothing; a member alone is refused; a re-file takes its predecessor's place."""
import subprocess

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    return pid


def db_of(client, pid):
    return client.app.state.app.projects[pid].db


def hdr(client, pid, role):
    return running_turn(db_of(client, pid), role, pid=pid)[1]


def request(client, pid, repo, role, name, requires=(), approve=True):
    (repo / name).write_text(f"{name}\n")
    r = client.post("/internal/commits", json={"feature": "F-1", "files": [name], "message": f"add {name}",
                                               "requires": list(requires)}, headers=hdr(client, pid, role))
    assert r.status_code == 200, r.text
    c = r.json()
    if approve and role != "lead":
        client.post(f"/internal/commits/{c['id']}/review", json={"decision": "approve"}, headers=hdr(client, pid, "lead"))
    return c


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout.strip()


def decide(client, pid, cid, **body):
    return client.post(f"/api/p/{pid}/commits/{cid}/decide", json={"decision": "approve", **body})


def test_requires_validation_and_card_line(client, pid, repo):
    db = db_of(client, pid)
    base = request(client, pid, repo, "backend", "api.py")
    for bad in ([99], ["x"]):
        r = client.post("/internal/commits", json={"files": ["api.py"], "message": "m", "requires": bad},
                        headers=hdr(client, pid, "frontend"))
        assert r.status_code in (400, 422), r.text
    ui = request(client, pid, repo, "frontend", "ui.ts", [base["id"]])
    assert ui["requires"] == [base["id"]] and db.commit(ui["id"])["requires"] == [base["id"]]
    assert db.commit(base["id"])["requires"] == []  # NULL reads as []
    card = db.message(db.commit(ui["id"])["message_id"])
    assert f"Requires #{base['id']}" in card["text"]
    # a superseded request can't be required
    request(client, pid, repo, "qa", "q.py", approve=False)
    (repo / "q2.py").write_text("x\n")
    new = client.post("/internal/commits", json={"feature": "F-1", "files": ["q.py", "q2.py"], "message": "q"},
                      headers=hdr(client, pid, "qa")).json()
    old_id = new["id"] - 1
    r = client.post("/internal/commits", json={"files": ["api.py"], "message": "m", "requires": [old_id]},
                    headers=hdr(client, pid, "frontend"))
    assert r.status_code == 400 and f"#{old_id}" in r.json()["detail"]


def test_group_order_inbox_item_and_group_commit(client, pid, repo):
    db = db_of(client, pid)
    base = request(client, pid, repo, "backend", "api.py")
    ui = request(client, pid, repo, "frontend", "ui.ts", [base["id"]], approve=False)
    lone = request(client, pid, repo, "qa", "lone.py")
    assert [m["id"] for m in workflow.commit_group(db, ui["id"])] == [base["id"], ui["id"]]
    assert [m["id"] for m in workflow.commit_group(db, lone["id"])] == [lone["id"]]
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert [g["id"] for g in inbox["groups"]] == [base["id"]]
    g = inbox["groups"][0]
    assert ([m["id"] for m in g["members"]], g["files"], g["ready"], g["feature"]) == ([base["id"], ui["id"]], 2, False, "F-1")
    assert [c["id"] for c in inbox["commits"]] == [lone["id"]] and inbox["count"] == 2  # group = one item
    # a member alone (either end) is refused while the other is open; the group needs every member in master_review
    for cid in (base["id"], ui["id"]):
        r = decide(client, pid, cid)
        assert r.status_code == 409 and "commit group" in r.json()["detail"], r.text
    r = decide(client, pid, base["id"], group=True)
    assert r.status_code == 409 and r.json()["detail"]["detail"] == f"#{ui['id']} is lead_review"
    assert {x["id"]: x["state"] for x in r.json()["detail"]["group"]} == {base["id"]: "master_review", ui["id"]: "lead_review"}
    client.post(f"/internal/commits/{ui['id']}/review", json={"decision": "approve"}, headers=hdr(client, pid, "lead"))
    assert client.get(f"/api/p/{pid}/inbox").json()["groups"][0]["ready"] is True
    db.x("DELETE FROM wake_queue")
    r = decide(client, pid, ui["id"], group=True)  # any member id commits the group
    assert r.status_code == 200, r.text
    assert [x["state"] for x in r.json()["group"]] == ["committed", "committed"]
    assert git(repo, "log", "-2", "--format=%s").splitlines() == [f"F-1: add ui.ts", f"F-1: add api.py"]
    msgs = db.messages("feature-1", limit=5)
    assert msgs[-1]["text"] == f"Group #{base['id']} + #{ui['id']} committed." and msgs[-1]["author"] == "system"
    assert [m["mentions"] for m in msgs[-3:-1]] == [["backend", "lead"], ["frontend", "lead"]]
    assert sorted({w["role"] for w in db.queued_wakes()}) == ["backend", "frontend", "lead"]
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert inbox["groups"] == [] and inbox["count"] == 1
    assert decide(client, pid, lone["id"]).status_code == 200  # singles unchanged


def test_stale_member_commits_nothing_then_force(client, pid, repo):
    base = request(client, pid, repo, "backend", "api.py")
    ui = request(client, pid, repo, "frontend", "ui.ts", [base["id"]])
    head = git(repo, "rev-parse", "HEAD")
    (repo / "ui.ts").write_text("edited after review\n")
    r = decide(client, pid, base["id"], group=True)
    assert r.status_code == 409 and r.json()["detail"] == {"stale": {str(ui["id"]): ["ui.ts"]}}
    assert git(repo, "rev-parse", "HEAD") == head
    assert decide(client, pid, base["id"], group=True, force=True).status_code == 200
    assert git(repo, "show", "HEAD:ui.ts") == "edited after review"


def test_mid_group_failure_rolls_back(client, pid, repo):
    db = db_of(client, pid)
    (repo / "wip.py").write_text("someone else's work\n")
    base = request(client, pid, repo, "backend", "api.py")
    ui = request(client, pid, repo, "frontend", "ui.ts", [base["id"]])
    head = git(repo, "rev-parse", "HEAD")
    (repo / "ui.ts").unlink()  # git add of a vanished, untracked path fails on the second member
    r = decide(client, pid, base["id"], group=True, force=True)
    assert r.status_code == 200
    assert {x["id"]: x["state"] for x in r.json()["group"]} == {base["id"]: "master_review", ui["id"]: "failed"}
    assert git(repo, "rev-parse", "HEAD") == head  # the first member's commit was undone
    assert git(repo, "diff", "--cached", "--name-only") == ""  # nothing left staged
    assert (repo / "api.py").read_text() == "api.py\n" and (repo / "wip.py").exists()  # working tree untouched
    last = db.messages("feature-1", limit=1)[-1]
    assert last["author"] == "master" and last["mentions"] == ["backend", "lead", "frontend"]
    assert f"Commit group #{base['id']} + #{ui['id']} failed at #{ui['id']}; nothing was committed" in last["text"]


def test_refile_takes_the_predecessors_place(client, pid, repo):
    db = db_of(client, pid)
    base = request(client, pid, repo, "backend", "api.py")
    ui = request(client, pid, repo, "frontend", "ui.ts", [base["id"]])
    # a pending predecessor: the backend re-files; the UI half now requires the new request
    base2 = request(client, pid, repo, "backend", "api.py")
    assert db.commit(base["id"])["note"] == f"superseded by #{base2['id']}"
    assert db.commit(ui["id"])["requires"] == [base2["id"]]
    assert [m["id"] for m in workflow.commit_group(db, ui["id"])] == [base2["id"], ui["id"]]
    # a rejected predecessor that the UI half still requires: the re-file repoints it too
    client.post(f"/api/p/{pid}/commits/{base2['id']}/decide", json={"decision": "reject", "note": "add tests"})
    r = decide(client, pid, ui["id"])  # its base is rejected: blocked until one of them re-files (Lead #605)
    assert r.status_code == 409 and r.json()["detail"] == (
        f"#{ui['id']} requires #{base2['id']}, which was rejected: re-file #{base2['id']}, or re-file #{ui['id']} without it")
    base3 = request(client, pid, repo, "backend", "api.py")
    assert db.commit(base2["id"])["note"] == f"superseded by #{base3['id']}"
    assert db.commit(ui["id"])["requires"] == [base3["id"]]
    # the UI half re-files: it inherits what its predecessor required
    ui2 = request(client, pid, repo, "frontend", "ui.ts")
    assert ui2["requires"] == [base3["id"]] and db.commit(ui["id"])["note"] == f"superseded by #{ui2['id']}"
    assert decide(client, pid, base3["id"], group=True).status_code == 200
    assert [db.commit(i)["state"] for i in (base3["id"], ui2["id"])] == ["committed", "committed"]


def test_reject_one_member_and_committed_requires(client, pid, repo):
    db = db_of(client, pid)
    base = request(client, pid, repo, "backend", "api.py")
    assert decide(client, pid, base["id"]).status_code == 200  # alone: no group yet
    ui = request(client, pid, repo, "frontend", "ui.ts", [base["id"]])  # requiring a committed request is fine
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert inbox["groups"][0]["ready"] is True and inbox["groups"][0]["files"] == 1
    assert decide(client, pid, ui["id"]).status_code == 200  # the rest is committed: it lands alone
    # rejecting one member rejects only it; a rejected dependent releases its base (Lead #605)
    a = request(client, pid, repo, "backend", "a.py")
    b = request(client, pid, repo, "frontend", "b.ts", [a["id"]])
    r = client.post(f"/api/p/{pid}/commits/{b['id']}/decide", json={"decision": "reject", "note": "no"}).json()
    assert r["state"] == "rejected" and db.commit(a["id"])["state"] == "master_review"
    assert [m["id"] for m in workflow.commit_group(db, a["id"])] == [a["id"]]
    inbox = client.get(f"/api/p/{pid}/inbox").json()
    assert inbox["groups"] == [] and a["id"] in [c["id"] for c in inbox["commits"]]
    assert decide(client, pid, a["id"]).status_code == 200
    # the UI half re-files later: it just requires the committed base
    b2 = request(client, pid, repo, "frontend", "b.ts", [a["id"]])
    assert decide(client, pid, b2["id"]).status_code == 200


def test_rejected_requirement_blocks_alone_and_in_a_group(client, pid, repo):
    db = db_of(client, pid)
    a = request(client, pid, repo, "backend", "a.py")
    b = request(client, pid, repo, "frontend", "b.ts", [a["id"]])
    c = request(client, pid, repo, "qa", "c.py", [b["id"]])
    client.post(f"/api/p/{pid}/commits/{a['id']}/decide", json={"decision": "reject", "note": "redo"})
    msg = f"#{b['id']} requires #{a['id']}, which was rejected: re-file #{a['id']}, or re-file #{b['id']} without it"
    assert [m["id"] for m in workflow.commit_group(db, c["id"])] == [b["id"], c["id"]]
    for cid, grp in ((b["id"], False), (b["id"], True), (c["id"], True)):
        r = decide(client, pid, cid, group=grp)
        assert r.status_code == 409 and r.json()["detail"] == msg, (cid, grp, r.text)
    g = client.get(f"/api/p/{pid}/inbox").json()["groups"][0]
    assert (g["ready"], g["blocked"]) == (False, msg)
    assert db.commit(b["id"])["state"] == "master_review"
    # the review script's view of the group: open members in order, their files
    r = client.get(f"/internal/commits/{c['id']}/files", headers=hdr(client, pid, "lead")).json()
    assert r == {"ids": [b["id"], c["id"]], "files": ["b.ts", "c.py"]}
    r = client.get(f"/internal/commits/{a['id']}/files", headers=hdr(client, pid, "lead")).json()
    assert r == {"ids": [a["id"]], "files": ["a.py"]}  # a rejected request alone
    assert client.get("/internal/commits/999/files", headers=hdr(client, pid, "lead")).status_code == 404
