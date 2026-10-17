"""In-memory daemon state: settings, open projects, scheduler, rate-limit pause."""
from __future__ import annotations

import asyncio
import os
import time

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
        self.hub = Hub()
        self.pending_questions: dict[int, asyncio.Future] = {}

    @property
    def paused(self) -> bool:
        return bool(self.db.get_kv("paused", False))

    def to_dict(self, state: "AppState | None" = None) -> dict:
        d = {"id": self.id, "name": self.name, "path": self.path, "is_git": self.is_git,
             "registered_at": self.registered_at}
        if state is not None:
            d["paused"] = self.paused
            d["rate_limited"] = state.rate_limited
        return d


class AppState:
    def __init__(self, port: int):
        self.port = port
        self.settings = config.load_settings()
        self.projects: dict[str, Project] = {}
        self.rate_limited: dict | None = None
        self.started_at = time.time()
        self.scheduler = None  # set by daemon
        self.request_shutdown = None  # callable set by cli/daemon

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
        now_limited = info.get("status") != "allowed"
        self.rate_limited = info if now_limited else None
        if was != now_limited:
            for p in self.projects.values():
                await p.hub.broadcast("rate_limit", {"info": info if now_limited else None})
