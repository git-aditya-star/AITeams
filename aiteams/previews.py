"""F-50: the one resolver for a file a message or card names (an html page or an image), so the Master sees it in place.

A reference is one of four forms: a docs path (`.aiteams/features/F-n/<name>`), a bare name (the context feature's docs
folder), an absolute or `~` path, or a relative path (the context feature's worktree first, then the project folder). The
docs forms go through `workflow.mock_path` unchanged (F-29). Every other path is checked here against the same four types
and size cap, `..` / NUL / `.git`, and must really live under the project or its worktrees folder (a symlink that leaves
them is refused). `resolve` returns (real path, kind) or raises the HTTPException to answer."""
from __future__ import annotations

import os
import re
import stat

from fastapi import HTTPException

from . import branches, workflow

DOCS_REF = re.compile(r"^(?:\./)?\.aiteams/features/F-(\d+)/([^/]+)$")
MAX_REF = 1024


def _checked(ref: str) -> None:
    """The refusals that need no file: a bad reference, a type that isn't served, `..`, NUL and `.git`."""
    if not isinstance(ref, str) or not ref.strip():
        raise HTTPException(400, "path required")
    if len(ref) > MAX_REF:
        raise HTTPException(400, "path too long")
    if "\x00" in ref:
        raise HTTPException(400, "bad path")
    if workflow.mock_kind(ref) is None:
        raise HTTPException(400, "only html, png, jpg, jpeg and svg files are served")
    segments = [s for s in re.split(r"[\\/]+", ref) if s]
    if ".." in segments or any(s.lower() == ".git" for s in segments):  # .git: any case, the folders may be case-insensitive
        raise HTTPException(400, "bad path")


def _roots(project_path: str) -> list[str]:
    return [os.path.realpath(project_path), os.path.realpath(branches.worktrees_root(project_path))]


def _file(path: str, roots: list[str]) -> str:
    """The real path of `path` when it is a servable file inside a root; the HTTPException otherwise."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        raise HTTPException(400, "bad path")
    root = next((r for r in roots if real.startswith(r + os.sep)), None)
    if root is None:
        raise HTTPException(400, "the file is outside the project and its worktrees")
    if any(s.lower() == ".git" for s in real[len(root) + 1:].split(os.sep)):
        raise HTTPException(400, "bad path")
    if workflow.mock_kind(real) is None:  # a symlink named x.png must not serve some other kind of file
        raise HTTPException(400, "only html, png, jpg, jpeg and svg files are served")
    try:
        stt = os.stat(real)
    except OSError:
        raise HTTPException(404, "no such file")
    if not stat.S_ISREG(stt.st_mode):
        raise HTTPException(404, "no such file")
    if stt.st_size > workflow.MOCK_MAX_BYTES:
        raise HTTPException(413, f"the file is over {workflow.MOCK_MAX_BYTES // (1024 * 1024)} MB")
    return real


def resolve(proj, ref: str, feature_id: int | None) -> tuple[str, str]:
    """(path to serve, kind `html` | `image`) for `ref`, named in the context of feature `feature_id` (None: no feature)."""
    _checked(ref)
    m = DOCS_REF.match(ref)
    if m:
        feat = proj.db.feature(int(m.group(1)))
        if feat is None:
            raise HTTPException(404, "unknown feature")
        p = workflow.mock_path(proj.path, feat, m.group(2))
        return p, workflow.mock_kind(p)
    if "/" not in ref:  # a bare name: only in the feature the reference is named in
        feat = proj.db.feature(feature_id) if feature_id is not None else None
        if feat is None:
            raise HTTPException(404, "a bare file name needs the feature it is named in")
        p = workflow.mock_path(proj.path, feat, ref)
        return p, workflow.mock_kind(p)
    roots = _roots(proj.path)
    if ref == "~" or ref.startswith("~/"):
        ref = os.path.expanduser(ref)
    if os.path.isabs(ref):
        real = _file(ref, roots)
    else:
        tree = branches.tree_for(proj.db, feature_id) if feature_id is not None else None
        real = None
        for base in ([tree] if tree else []) + [proj.path]:
            try:
                real = _file(os.path.join(base, ref), roots)
                break
            except HTTPException as e:
                if e.status_code != 404:
                    raise
                missing = e
        if real is None:
            raise missing
    return real, workflow.mock_kind(real)
