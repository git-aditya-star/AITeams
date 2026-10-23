"""In-memory daemon state: settings, token, open projects, scheduler, rate-limit pause, runtime info."""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from importlib.metadata import PackageNotFoundError, version as pkg_version

from . import config
from .db import ProjectDB, ensure_git_exclude
from .ws import Hub


class Project:
    def __init__(self, meta: dict):
        self.id: str = meta["id"]
        self.path: str = meta["path"]
        self.name: str = meta["name"]
        self.registered_at: float = meta.get("registered_at") or 0.0
        self.is_git = ensure_git_exclude(self.path)
        self.db = ProjectDB(self.path)
        self.schema_ahead = self.db.schema_ahead  # DB written by a newer AITeams: read-only, never scheduled
        if self.schema_ahead:
            print(f"project {self.id} ({self.path}): database is from a newer AITeams; loaded read-only")
        else:
            # F-12: no held AskUserQuestion survives a restart. Nothing to broadcast: the hub is new, and clients
            # load questions and the inbox when they connect.
            self.db.expire_orphaned_blocking_questions()
        self.hub = Hub()
        self.pending_questions: dict[int, asyncio.Future] = {}
        # F-16: running turn id -> highest message id it has been shown (prompt, then new_messages / the guard)
        self.delivered: dict[int, int] = {}

    @property
    def paused(self) -> bool:
        return bool(self.db.get_kv("paused", False))

    def to_dict(self, state: "AppState | None" = None) -> dict:
        d = {"id": self.id, "name": self.name, "path": self.path, "is_git": self.is_git,
             "registered_at": self.registered_at, "schema_ahead": self.schema_ahead}
        if state is not None:
            d["paused"] = self.paused
            d["rate_limited"] = state.rate_limited
        return d


class AppState:
    def __init__(self, port: int):
        self.port = port
        self.settings = config.load_settings()
        self.token = config.master_token()
        self.auth_codes: dict[str, float] = {}  # single-use sign-in codes -> expiry
        self.projects: dict[str, Project] = {}
        self.rate_limited: dict | None = None
        self.artifact_missing_notified = False  # F-29 B: the "artifacts unavailable" System line, once per daemon run
        self.plugin_status: dict[str, dict] = {}  # F-23: {plugin id: {missing, checked_at, role}}, last turn wins
        self.started_at = time.time()
        self.scheduler = None  # set by daemon
        self.request_shutdown = None  # callable set by cli/daemon
        self._head_cache: tuple[float, str | None] = (0.0, None)

    def runtime(self) -> dict:
        """Version surface for /api/health: installed version, snapshot sha/ref/source, source HEAD (cached 30 s)."""
        try:
            ver = pkg_version("aiteams")
        except PackageNotFoundError:
            ver = None
        info = config.runtime_info()
        out = {"version": ver, "sha": info.get("sha"), "ref": info.get("ref"), "installed_at": info.get("installed_at"),
               "source": info.get("source"), "source_head": None}
        if info.get("source"):
            ts, head = self._head_cache
            if time.time() - ts > 30:
                try:
                    r = subprocess.run(["git", "-C", info["source"], "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
                    head = r.stdout.strip() if r.returncode == 0 else None
                except (OSError, subprocess.SubprocessError):
                    head = None
                self._head_cache = (time.time(), head)
            out["source_head"] = head
        return out

    @property
    def daemon_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def load_projects(self) -> None:
        for meta in config.load_projects():
            if os.path.isdir(meta["path"]):
                try:
                    self.projects[meta["id"]] = Project(meta)
                except Exception:
                    pass

    def register(self, path: str) -> tuple[Project, bool]:
        meta, created = config.register_project(path, time.time())
        proj = self.projects.get(meta["id"])
        if proj is None:
            proj = Project(meta)
            self.projects[proj.id] = proj
        return proj, created

    def rate_limit_active(self) -> bool:
        info = self.rate_limited
        if not info:
            return False
        resets = info.get("resetsAt")
        if isinstance(resets, (int, float)) and time.time() > resets:
            self.rate_limited = None
            return False
        return True

    async def set_rate_limit(self, info: dict) -> None:
        was = self.rate_limit_active()
        # Claude Code reports allowed | allowed_warning (window nearly used) | rejected; only rejected holds turns.
        now_limited = str(info.get("status") or "").split("_")[0] != "allowed"
        self.rate_limited = info if now_limited else None
        if was != now_limited:
            for p in self.projects.values():
                await p.hub.broadcast("rate_limit", {"info": info if now_limited else None})
