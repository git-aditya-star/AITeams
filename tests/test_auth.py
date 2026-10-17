"""Master token on /api and /ws: bearer, cookie, open health, sign-in link, SPA sign-in page, token reload."""
import time

import pytest
from fastapi.testclient import TestClient

from aiteams.daemon import create_app
from tests.conftest import AUTH, TOKEN


@pytest.fixture
def client(home):
    app = create_app(7799, write_daemon_json=False)
    with TestClient(app) as c:  # no default headers: every test says how it authenticates
        yield c


def test_api_requires_token(client, repo):
    assert client.get("/api/projects").status_code == 401
    r = client.post("/api/projects/register", json={"path": str(repo)})
    assert r.status_code == 401 and r.json() == {"detail": "master token required"}
    assert client.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer \u00e9".encode("utf-8")}).status_code == 401
    assert client.get("/api/projects", headers=AUTH).status_code == 200
    client.cookies.set("aiteams_token", TOKEN)
    assert client.get("/api/projects").status_code == 200
    client.cookies.clear()
    pid = client.post("/api/projects/register", json={"path": str(repo)}, headers=AUTH).json()["id"]
    assert client.get(f"/api/p/{pid}/features").status_code == 401  # feat_api router too
    assert client.get(f"/api/p/{pid}/features", headers=AUTH).status_code == 200


def test_health_is_open(client):
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["version"] and h["sha"] is None and h["source_head"] is None


def test_signin_link_sets_cookie_once(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}, headers=AUTH).json()["id"]
    link = client.post("/api/auth/link", json={}, headers=AUTH).json()
    assert link["url"].startswith("http://127.0.0.1:7799/auth?t=") and link["code"] in link["url"]
    r = client.get("/auth", params={"t": link["code"], "next": f"/p/{pid}"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{pid}"
    assert r.cookies.get("aiteams_token") == TOKEN and "HttpOnly" in r.headers["set-cookie"]
    # the cookie now signs API calls (TestClient keeps the jar)
    assert client.get("/api/projects").status_code == 200
    # second use fails, unknown code fails, an outside `next` falls back to /
    again = client.get("/auth", params={"t": link["code"]})
    assert again.status_code == 200 and "link expired" in again.text
    assert "link expired" in client.get("/auth", params={"t": "bogus"}).text
    link = client.post("/api/auth/link", json={}, headers=AUTH).json()
    r = client.get("/auth", params={"t": link["code"], "next": "https://evil.example/x"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_signin_link_expires(client):
    link = client.post("/api/auth/link", json={}, headers=AUTH).json()
    client.app.state.app.auth_codes[link["code"]] = time.time() - 1
    assert "link expired" in client.get("/auth", params={"t": link["code"]}).text


def test_spa_route_needs_cookie(client, repo):
    r = client.get("/p/abc")
    assert r.status_code == 200 and "aiteams token --link" in r.text and "<title>AITeams</title>" in r.text
    assert "aiteams token --link" in client.get("/").text
    assert "aiteams token --link" in client.get("/p/abc", headers=AUTH).text  # a bearer header is not a browser session
    client.cookies.set("aiteams_token", TOKEN)
    assert "aiteams token --link" not in client.get("/p/abc").text  # index.html (or the 503 when the UI is not built)


def test_ws_auth(client, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}, headers=AUTH).json()["id"]
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/ws/p/{pid}") as ws:
            ws.receive_text()
    assert e.value.code == 4401
    with client.websocket_connect(f"/ws/p/{pid}?token={TOKEN}") as ws:
        assert ws.receive_json()["event"] == "hello"
    client.cookies.set("aiteams_token", TOKEN)
    with client.websocket_connect(f"/ws/p/{pid}") as ws:
        assert ws.receive_json()["event"] == "hello"
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/ws/p/nope?token={TOKEN}") as ws:
            ws.receive_text()
    assert e.value.code == 4404


def test_token_reload_reads_file_and_drops_clients(client, home, monkeypatch, repo):
    pid = client.post("/api/projects/register", json={"path": str(repo)}, headers=AUTH).json()["id"]
    monkeypatch.delenv("AITEAMS_TOKEN")
    (home / "token").write_text("rotated-token\n")
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect(f"/ws/p/{pid}?token={TOKEN}") as ws:
        assert ws.receive_json()["event"] == "hello"
        assert client.post("/api/token/reload", json={}, headers={"Authorization": "Bearer rotated-token"}).status_code == 401
        assert client.post("/api/token/reload", json={}, headers=AUTH).json() == {"ok": True}
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == 4401
    assert client.get("/api/projects", headers=AUTH).status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer rotated-token"}).status_code == 200
