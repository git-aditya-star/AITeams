"""F-29: mocks (html and images) in a feature's docs folder: docs_list kinds and order, and the raw-file endpoint with
its refusals, size cap and sandbox headers."""
import os

import pytest
from fastapi.testclient import TestClient

from aiteams import workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH, TOKEN

CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(32))
HTML = b"<!doctype html><title>mock</title><p id=x>hello</p><script>document.title='ran'</script>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>alert(1)</script></svg>'


@pytest.fixture
def client(home):
    with TestClient(create_app(7799, write_daemon_json=False), headers=AUTH) as c:
        yield c


@pytest.fixture
def pid(client, repo):
    return client.post("/api/projects/register", json={"path": str(repo)}).json()["id"]


@pytest.fixture
def docs(client, pid, repo):
    """F-1 with its docs folder; returns the folder."""
    client.post(f"/api/p/{pid}/features", json={"text": "Thing", "starter": "product"})
    d = repo / ".aiteams" / "features" / "F-1"
    d.mkdir(parents=True, exist_ok=True)
    return d


def url(pid, name, fid=1):
    return f"/api/p/{pid}/features/{fid}/files/{name}"


def listing(client, pid):
    return client.get(f"/api/p/{pid}/features/1/docs").json()


# ---- docs_list ------------------------------------------------------------------------------------------------------
def test_docs_list_gives_every_entry_a_kind_md_first_then_mocks_sorted(client, pid, docs):
    for name, data in (("zeta.md", b"z"), ("alpha.md", b"a"), ("mock-b.html", HTML), ("a.png", PNG), ("pic.JPG", PNG),
                       ("photo.jpeg", PNG), ("flow.svg", SVG), ("notes.txt", b"x"), ("script.py", b"x"), ("noext", b"x")):
        (docs / name).write_bytes(data)
    (docs / "sub").mkdir()
    (docs / "sub" / "inner.png").write_bytes(PNG)  # no subfolders
    (docs / "folder.png").mkdir()  # a directory with a mock-looking name is not a mock
    got = [(d["name"], d["kind"]) for d in listing(client, pid)]
    assert got == [("alpha.md", "md"), ("zeta.md", "md"), ("a.png", "image"), ("flow.svg", "image"),
                   ("mock-b.html", "html"), ("photo.jpeg", "image"), ("pic.JPG", "image")]
    sizes = {d["name"]: d["size"] for d in listing(client, pid)}
    assert sizes["mock-b.html"] == len(HTML) and sizes["a.png"] == len(PNG) and sizes["alpha.md"] == 1


def test_docs_list_mocks_use_the_editor_and_time_like_docs_and_symlinks_are_not_listed(client, pid, docs):
    proj = client.app.state.app.projects[pid]
    (docs / "mock-a.html").write_bytes(HTML)
    proj.db.touch_edit(".aiteams/features/F-1/mock-a.html", "frontend")
    (docs / "real.png").write_bytes(PNG)
    os.symlink(docs / "real.png", docs / "link.png")
    os.symlink("/etc/hosts", docs / "hosts.html")
    by_name = {d["name"]: d for d in listing(client, pid)}
    assert set(by_name) == {"mock-a.html", "real.png"}
    assert by_name["mock-a.html"]["editor"] == "frontend" and by_name["real.png"]["editor"] is None
    assert by_name["real.png"]["updated_at"] > 0


def test_docs_list_is_empty_without_a_folder_and_the_role_header_lists_mocks(client, pid, repo, docs):
    (docs / "mock-a.html").write_bytes(HTML)
    (docs / "design.md").write_text("x")
    proj = client.app.state.app.projects[pid]
    header = workflow.feature_header(proj.db, str(repo), proj.db.feature(1))
    assert "design.md (unknown" in header and "mock-a.html (unknown" in header
    assert header.index("design.md") < header.index("mock-a.html")  # docs first, then mocks
    proj.db.create_feature("Other")
    assert client.get(f"/api/p/{pid}/features/2/docs").json() == []


def test_the_text_docs_endpoint_stays_md_only(client, pid, docs):
    (docs / "mock-a.html").write_bytes(HTML)
    (docs / "prd.md").write_text("# PRD\n")
    assert client.get(f"/api/p/{pid}/features/1/docs/mock-a.html").status_code == 400
    assert client.get(f"/api/p/{pid}/features/1/docs/prd.md").json()["text"] == "# PRD\n"


# ---- the raw endpoint -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name, data, ctype", [
    ("mock-a.html", HTML, "text/html; charset=utf-8"), ("a.png", PNG, "image/png"), ("b.jpg", PNG, "image/jpeg"),
    ("c.jpeg", PNG, "image/jpeg"), ("d.svg", SVG, "image/svg+xml"), ("UPPER.PNG", PNG, "image/png")])
def test_raw_file_bytes_content_type_and_sandbox_headers(client, pid, docs, name, data, ctype):
    (docs / name).write_bytes(data)
    r = client.get(url(pid, name))
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == ctype
    assert r.headers["content-security-policy"] == CSP  # no allow-same-origin: an opaque origin even in its own tab
    assert "allow-same-origin" not in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-cache"


def test_raw_file_reflects_a_changed_file_and_is_served_per_feature(client, pid, repo, docs):
    (docs / "m.html").write_bytes(b"one")
    assert client.get(url(pid, "m.html")).content == b"one"
    (docs / "m.html").write_bytes(b"two!")
    assert client.get(url(pid, "m.html")).content == b"two!"
    client.post(f"/api/p/{pid}/features", json={"text": "Second", "starter": "product"})
    other = repo / ".aiteams" / "features" / "F-2"
    other.mkdir(parents=True, exist_ok=True)
    (other / "m.html").write_bytes(b"feature two")
    assert client.get(url(pid, "m.html", 2)).content == b"feature two"
    assert client.get(url(pid, "m.html", 1)).content == b"two!"
    assert client.get(url(pid, "m.html", 99)).status_code == 404  # unknown feature


@pytest.mark.parametrize("name", [
    "..%2Fx.html", "%2e%2e%2fx.html", "a/b.png", "a%2Fb.png", "sub%2F..%2Fx.png", "x.py", "x.md", "x.txt", "x", "x.html.py",
    "x.png.exe", "..%5Cx.png", "a%5Cb.png", "x%00.png", "%2e%2e", "x.html%20", "x.png."])
def test_raw_file_refuses_paths_and_other_extensions_with_400(client, pid, docs, name):
    (docs / "x.html").write_bytes(HTML)
    (docs.parent.parent / "x.html").write_bytes(b"outside the feature folder")  # what a traversal would reach
    r = client.get(url(pid, name))
    assert r.status_code in (400, 404), (name, r.status_code)
    assert b"outside" not in r.content and r.content != HTML


@pytest.mark.parametrize("name", ["..%2Fx.html", "a/b.png", "x.py", "x.md", "a%2Fb.png", "x"])
def test_the_main_refusals_are_400s(client, pid, docs, name):
    assert client.get(url(pid, name)).status_code == 400


@pytest.mark.parametrize("name", [".", "..", "", "a/b.png", "../x.html", "a\\b.png", "x\x00.png"])
def test_mock_path_refuses_a_name_with_a_path_in_it(repo, name):
    """The HTTP client normalises `.` and `..` away before sending, so they are checked on the resolver itself."""
    with pytest.raises(workflow.HTTPException) as e:
        workflow.mock_path(str(repo), {"key": "F-1"}, name)
    assert e.value.status_code == 400


def test_missing_file_and_a_directory_named_like_a_mock_are_404(client, pid, docs):
    assert client.get(url(pid, "nope.html")).status_code == 404
    (docs / "folder.png").mkdir()
    assert client.get(url(pid, "folder.png")).status_code == 404


def test_symlinks_and_a_symlinked_folder_are_refused(client, pid, repo, docs, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"top secret")
    os.symlink(secret, docs / "leak.png")
    os.symlink(docs / "gone.png", docs / "dangling.png")
    (docs / "real.png").write_bytes(PNG)
    os.symlink(docs / "real.png", docs / "inside.png")  # even a link that stays inside the folder is refused
    for name in ("leak.png", "inside.png"):
        r = client.get(url(pid, name))
        assert r.status_code == 400 and b"top secret" not in r.content, name
    assert client.get(url(pid, "dangling.png")).status_code == 400  # a dangling link is still a link
    assert client.get(url(pid, "real.png")).content == PNG
    # the whole docs folder replaced by a link to somewhere else
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "m.html").write_bytes(b"from elsewhere")
    client.post(f"/api/p/{pid}/features", json={"text": "Linked", "starter": "product"})
    folder = repo / ".aiteams" / "features" / "F-2"
    if folder.is_dir() and not folder.is_symlink():
        folder.rmdir()
    os.symlink(elsewhere, folder)
    r = client.get(url(pid, "m.html", 2))
    assert r.status_code == 400 and r.content != b"from elsewhere"


def test_size_cap_is_413_just_over_10_mb_and_200_at_the_cap(client, pid, docs):
    cap = 10 * 1024 * 1024
    assert workflow.MOCK_MAX_BYTES == cap
    (docs / "ok.png").write_bytes(b"x" * cap)
    (docs / "big.png").write_bytes(b"x" * (cap + 1))
    assert client.get(url(pid, "ok.png")).status_code == 200
    r = client.get(url(pid, "big.png"))
    assert r.status_code == 413 and r.json()["detail"] == "the file is over 10 MB"


def test_the_raw_endpoint_needs_the_master_token_by_bearer_or_cookie(client, pid, docs):
    (docs / "m.html").write_bytes(HTML)
    assert client.get(url(pid, "m.html"), headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(url(pid, "m.html"), headers={"Authorization": ""}).status_code == 401  # no credentials at all
    client.cookies.set("aiteams_token", "nope")
    assert client.get(url(pid, "m.html"), headers={"Authorization": ""}).status_code == 401
    client.cookies.set("aiteams_token", TOKEN)  # what the browser sends for the iframe / img / new-tab request
    r = client.get(url(pid, "m.html"), headers={"Authorization": ""})
    assert r.status_code == 200 and r.content == HTML and r.headers["content-security-policy"] == CSP
    client.cookies.clear()
    assert client.get(url(pid, "nope.png"), headers={"Authorization": "Bearer nope"}).status_code == 401  # auth before 404


def test_refusals_carry_no_file_content_and_the_listing_matches_what_is_served(client, pid, docs):
    for name, data in (("a.png", PNG), ("m.html", HTML), ("f.svg", SVG)):
        (docs / name).write_bytes(data)
    for d in listing(client, pid):
        if d["kind"] != "md":
            r = client.get(url(pid, d["name"]))
            assert r.status_code == 200 and len(r.content) == d["size"], d["name"]
