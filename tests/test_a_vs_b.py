"""F-10: only the owner sends an A vs B card (Lead, Product, or the feature's owner); others get a 403 that tells
them what to do instead (QA feature-10 #516: a non-owner posted it with its own side first)."""
import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app
from tests.conftest import AUTH, running_turn

CARD = [{"question": "Where does the CSV get built?", "header": "CSV", "multiSelect": False,
         "options": [{"label": "Server-side (Recommended)", "description": "Lead's pick"},
                     {"label": "Client-side", "description": "Frontend's case"}]}]


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]
    client.post(f"/api/p/{pid}/features", json={"text": "Export", "starter": "product"})
    return pid


def ask(client, pid, role, feature="F-1"):
    hdr = running_turn(client.app.state.app.projects[pid].db, role, pid=pid)[1]
    return client.post("/internal/questions", json={"questions": CARD, "feature": feature, "purpose": "a_vs_b",
                                                    "intro": "Both sides."}, headers=hdr)


def test_non_owner_gets_403_with_what_to_do(client, pid):
    db = client.app.state.app.projects[pid].db
    r = ask(client, pid, "frontend")
    assert r.status_code == 403
    assert r.json()["detail"] == ("only the owner (lead or product) sends an A vs B card; "
                                  "post the owner a Concern saying you still disagree")
    assert db.questions("pending") == [] and ask(client, pid, "frontend", None).status_code == 403


def test_lead_and_product_may_send_it(client, pid):
    for role in ("lead", "product"):
        r = ask(client, pid, role)
        assert r.status_code == 200, r.text
    assert ask(client, pid, "lead", None).status_code == 200  # no feature: Lead/Product still own engineering/scope


def test_feature_owner_may_send_it(client, pid):
    assert client.patch(f"/api/p/{pid}/features/1", json={"owner": "frontend"}).status_code == 200
    r = ask(client, pid, "frontend")
    assert r.status_code == 200, r.text
    r = ask(client, pid, "backend")
    assert r.status_code == 403 and "(lead, product or frontend)" in r.json()["detail"]
