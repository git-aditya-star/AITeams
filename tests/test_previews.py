"""F-50: the preview resolver (docs path, bare name, absolute, `~`, relative to the tree and to the project), each refusal,
and the two routes: GET /preview serves with the mock's sandbox headers, POST /preview/resolve says what can be shown."""
import os
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from aiteams import branches, previews, workflow
from aiteams.daemon import create_app
from tests.conftest import AUTH

CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(32))
TREE_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(32, 64))
HTML = b"<!doctype html><title>mock</title><p>hello</p>"


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
def feats(client, pid):
    """F-1 and F-2, each with a docs folder."""
    for t in ("One", "Two"):
        client.post(f"/api/p/{pid}/features", json={"text": t, "starter": "product"})


def write(path, data=PNG):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def tree(proj, repo, feats):
    """F-1 on its own worktree folder under the project's worktrees root; returns the folder."""
    t = os.path.join(branches.worktrees_root(str(repo)), "f1")
    os.makedirs(t)
    proj.db.set_feature_branch(1, mode="new", branch="feat/x", base="main", tree=t)
    return t


def docs(repo, n=1):
    return repo / ".aiteams" / "features" / f"F-{n}"


def get(client, pid, ref, feature=None, **kw):
    q = f"path={quote(ref, safe='')}" + (f"&feature={feature}" if feature is not None else "")
    return client.get(f"/api/p/{pid}/preview?{q}", **kw)


def refused(proj, ref, status, feature=None):
    with pytest.raises(HTTPException) as e:
        previews.resolve(proj, ref, feature)
    assert e.value.status_code == status, e.value.detail


# ---- the four forms -------------------------------------------------------------------------------------------------
def test_a_docs_path_serves_that_features_file_whatever_the_context(client, pid, repo, feats):
    write(docs(repo, 2) / "mock-b.html", HTML)
    r = get(client, pid, ".aiteams/features/F-2/mock-b.html", feature=1)  # named in F-1, lives in F-2's docs
    assert r.status_code == 200 and r.content == HTML
    assert r.headers["content-type"] == "text/html; charset=utf-8" and r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert get(client, pid, "./.aiteams/features/F-2/mock-b.html").content == HTML  # a leading ./ and no context at all


def test_a_bare_name_is_the_context_features_docs_file_and_needs_a_feature(client, pid, repo, feats):
    write(docs(repo, 1) / "shot.png")
    r = get(client, pid, "shot.png", feature=1)
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["content-security-policy"] == CSP
    assert get(client, pid, "shot.png", feature=2).status_code == 404  # F-2's docs have no such file
    assert get(client, pid, "shot.png").status_code == 404  # no context feature: nothing
    assert get(client, pid, "shot.png", feature=99).status_code == 404


def test_an_absolute_path_and_a_tilde_path_in_the_tree_or_the_project(client, pid, repo, tree, monkeypatch):
    shot = write(Path(tree) / "ui" / "test-results" / "shots" / "a.png", TREE_PNG)
    assert get(client, pid, str(shot)).content == TREE_PNG  # absolute, no context feature needed
    monkeypatch.setenv("HOME", os.path.dirname(os.path.dirname(os.path.realpath(tree))))
    tilde = "~/" + os.path.relpath(os.path.realpath(shot), os.environ["HOME"])
    assert get(client, pid, tilde).content == TREE_PNG
    write(repo / "qa" / "shots" / "p.png", PNG)
    assert get(client, pid, str(repo / "qa" / "shots" / "p.png")).content == PNG


def test_a_relative_path_tries_the_tree_first_then_the_project(client, pid, repo, tree):
    write(repo / "ui" / "test-results" / "shots" / "both.png", PNG)
    write(Path(tree) / "ui" / "test-results" / "shots" / "both.png", TREE_PNG)
    write(repo / "qa" / "shots" / "only-project.png", PNG)
    rel = "ui/test-results/shots/both.png"
    assert get(client, pid, rel, feature=1).content == TREE_PNG  # the tree's copy wins
    assert get(client, pid, rel, feature=2).content == PNG  # F-2 has no tree: the project's copy
    assert get(client, pid, rel).content == PNG  # nor does a message with no feature
    assert get(client, pid, "qa/shots/only-project.png", feature=1).content == PNG  # not in the tree: falls back
    assert get(client, pid, "qa/shots/missing.png", feature=1).status_code == 404


def test_the_resolve_route_says_which_refs_can_be_shown(client, pid, repo, tree):
    write(docs(repo, 1) / "mock-a.html", HTML)
    write(docs(repo, 1) / "shot.png")
    write(Path(tree) / "ui" / "test-results" / "shots" / "t.png", TREE_PNG)
    refs = [".aiteams/features/F-1/mock-a.html", "shot.png", "ui/test-results/shots/t.png", "nope.png", "notes.txt",
            "../x.png", "/etc/passwd.png", ".aiteams/features/F-1/nope.html"]
    r = client.post(f"/api/p/{pid}/preview/resolve", json={"feature": 1, "refs": refs})
    assert r.status_code == 200
    got = r.json()
    assert [g["ref"] for g in got] == refs
    assert [g["kind"] for g in got] == ["html", "image", "image", None, None, None, None, None]
    assert [g["url"] is None for g in got] == [False, False, False, True, True, True, True, True]
    for g in got[:3]:  # the url fetches exactly what was resolved
        assert client.get(g["url"]).status_code == 200
    assert client.get(got[2]["url"]).content == TREE_PNG
    assert client.post(f"/api/p/{pid}/preview/resolve", json={"refs": ["shot.png"]}).json() == [
        {"ref": "shot.png", "kind": None, "url": None}]  # no feature: a bare name doesn't resolve, and that is no error
    assert client.post(f"/api/p/{pid}/preview/resolve", json={"feature": None, "refs": []}).json() == []
    assert client.post(f"/api/p/{pid}/preview/resolve", json={"feature": 1, "refs": ["a.png"] * 21}).status_code == 422


# ---- refusals ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ref", ["notes.txt", "qa/run.py", "qa/shot.png.bak", "qa/noext", "/etc/hosts", "qa/.png"])
def test_a_type_that_is_not_served_is_400(proj, repo, feats, ref):
    write(repo / "qa" / "run.py", b"x")
    write(repo / "qa" / "shot.png.bak", b"x")
    refused(proj, ref, 400, 1)


@pytest.mark.parametrize("ref", ["../x.png", "qa/../../x.png", "qa/./../x.png", "qa\\..\\x.png", "..", ".git/x.png",
                                 "qa/.git/x.png", "qa/.GIT/x.png", "a\x00.png", "qa/a\x00b.png", ""])
def test_dotdot_nul_and_dot_git_are_400(proj, repo, feats, ref):
    refused(proj, ref, 400, 1)


def test_a_path_outside_the_project_and_its_worktrees_is_400_even_when_the_file_exists(proj, repo, tmp_path, feats):
    out = write(tmp_path / "elsewhere" / "secret.png")
    refused(proj, str(out), 400, 1)
    sibling = write(tmp_path / "repo2" / "x.png")  # a folder that merely starts with the project's name
    refused(proj, str(sibling), 400, 1)
    refused(proj, "/nowhere/at/all.png", 400, 1)
    refused(proj, str(repo), 400, 1)  # the folder itself


def test_a_symlink_that_leaves_the_project_is_400_a_file_or_a_folder(proj, repo, tmp_path, feats):
    out = write(tmp_path / "elsewhere" / "secret.png")
    os.symlink(out, repo / "link.png")
    os.symlink(out.parent, repo / "linkdir")
    refused(proj, "./link.png", 400, 1)  # (a bare `link.png` would be a docs name)
    os.makedirs(repo / "sub")
    os.symlink(out, repo / "sub" / "link.png")
    refused(proj, "sub/link.png", 400, 1)
    refused(proj, "linkdir/secret.png", 400, 1)
    refused(proj, str(repo / "linkdir" / "secret.png"), 400, 1)


def test_a_symlink_inside_the_project_is_fine_but_only_to_a_served_type(client, pid, proj, repo, feats):
    real = write(repo / "qa" / "real.png", PNG)
    os.symlink(real, repo / "qa" / "alias.png")
    r = get(client, pid, "qa/alias.png", feature=1)
    assert r.status_code == 200 and r.content == PNG
    (repo / "qa" / "notes.txt").write_text("secret")
    os.symlink(repo / "qa" / "notes.txt", repo / "qa" / "looks-like.png")  # named like an image, is a text file
    refused(proj, "qa/looks-like.png", 400, 1)
    write(repo / ".git" / "x.png")
    os.symlink(repo / ".git" / "x.png", repo / "qa" / "into-git.png")  # a link into .git: the real path has the segment
    refused(proj, "qa/into-git.png", 400, 1)


def test_not_a_file_is_404_and_a_big_one_is_413(client, pid, proj, repo, feats, monkeypatch):
    os.makedirs(repo / "qa" / "shots.png")
    refused(proj, "qa/shots.png", 404, 1)
    refused(proj, "qa/missing.png", 404, 1)
    refused(proj, ".aiteams/features/F-1/missing.png", 404, 1)
    big = write(repo / "qa" / "big.png", b"x" * 20)
    monkeypatch.setattr(workflow, "MOCK_MAX_BYTES", 10)
    refused(proj, "qa/big.png", 413, 1)
    assert get(client, pid, "qa/big.png", feature=1).status_code == 413
    refused(proj, str(big), 413, 1)
    write(docs(repo) / "big.png", b"x" * 20)
    refused(proj, "big.png", 413, 1)
    refused(proj, ".aiteams/features/F-1/big.png", 413, 1)


def test_the_docs_forms_keep_the_docs_refusals(proj, repo, tmp_path, feats):
    out = write(tmp_path / "elsewhere" / "secret.png")
    os.symlink(out, docs(repo) / "link.png")
    refused(proj, "link.png", 400, 1)  # mock_path: symlinks in the docs folder are not served
    refused(proj, ".aiteams/features/F-1/link.png", 400, 1)
    refused(proj, ".aiteams/features/F-99/a.png", 404, 1)  # no such feature
    refused(proj, "shot.png", 404, None)


def test_a_path_that_is_too_long_is_400(proj, feats):
    refused(proj, "a/" * 600 + "x.png", 400, 1)


def test_the_routes_need_the_master_token(client, pid, repo, feats):
    write(docs(repo) / "shot.png")
    anon = TestClient(client.app)  # no `with`: no second lifespan, which would close the project's db
    assert anon.get(f"/api/p/{pid}/preview?path=shot.png&feature=1").status_code == 401
    assert anon.post(f"/api/p/{pid}/preview/resolve", json={"feature": 1, "refs": ["shot.png"]}).status_code == 401
    assert get(client, pid, "shot.png", feature=1).status_code == 200
