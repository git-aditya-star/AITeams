"""Global state under ~/.aiteams: daemon.json, settings.json, projects.json, token, runtime.json."""
from __future__ import annotations

import json
import os
import secrets
import uuid
from pathlib import Path

ROLES = ["manager", "lead", "product", "frontend", "backend", "qa"]

DEFAULT_SETTINGS = {
    "port": 7700,
    "default_model": "opus",
    "models": {},
    "max_concurrent": 3,
    "ask_timeout_s": 600,
    "max_turns": 40,
    "turn_timeout_s": 1200,
    "rotate_pct": 0.6,
    "stale_hours": 4,
    "fail_streak_rotate": 3,
}


def home() -> Path:
    return Path(os.environ.get("AITEAMS_HOME") or Path.home() / ".aiteams")


def _read_json(path: Path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(_read_json(home() / "settings.json", {}) or {})
    if not isinstance(s.get("models"), dict):
        s["models"] = {}
    return s


def save_settings(s: dict) -> dict:
    merged = load_settings()
    merged.update({k: v for k, v in s.items() if k in DEFAULT_SETTINGS})
    _write_json(home() / "settings.json", merged)
    return merged


def load_daemon() -> dict | None:
    return _read_json(home() / "daemon.json", None)


def save_daemon(pid: int, port: int, started_at: float) -> None:
    _write_json(home() / "daemon.json", {"pid": pid, "port": port, "started_at": started_at})


def clear_daemon() -> None:
    try:
        (home() / "daemon.json").unlink()
    except OSError:
        pass


def load_projects() -> list[dict]:
    data = _read_json(home() / "projects.json", [])
    return data if isinstance(data, list) else []


def save_projects(projects: list[dict]) -> None:
    _write_json(home() / "projects.json", projects)


def register_project(path: str, registered_at: float) -> tuple[dict, bool]:
    """Idempotent by resolved path. Returns (project, created)."""
    rp = os.path.realpath(path)
    projects = load_projects()
    for p in projects:
        if p["path"] == rp:
            return p, False
    proj = {"id": uuid.uuid4().hex[:8], "path": rp, "name": os.path.basename(rp) or rp,
            "registered_at": registered_at}
    projects.append(proj)
    save_projects(projects)
    return proj, True


def token_path() -> Path:
    return home() / "token"


def master_token() -> str:
    """The Master's secret for /api and /ws. Created on first use (mode 0600). AITEAMS_TOKEN overrides (tests)."""
    env = os.environ.get("AITEAMS_TOKEN")
    if env:
        return env
    try:
        tok = token_path().read_text().strip()
        if tok:
            return tok
    except OSError:
        pass
    return write_token(secrets.token_urlsafe(32))


def write_token(tok: str) -> str:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as f:
        f.write(tok + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return tok


def runtime_dir() -> Path:
    return home() / "runtime"


def runtime_info() -> dict:
    """~/.aiteams/runtime.json written by `aiteams upgrade`; {} for a dev (editable) install."""
    data = _read_json(home() / "runtime.json", {})
    return data if isinstance(data, dict) else {}


def write_runtime_info(info: dict) -> None:
    _write_json(home() / "runtime.json", info)


def model_for(settings: dict, role: str) -> str:
    return (settings.get("models") or {}).get(role) or settings.get("default_model") or "opus"
