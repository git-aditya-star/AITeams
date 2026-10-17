"""Global state under ~/.aiteams: daemon.json, settings.json, projects.json, token, runtime.json."""
from __future__ import annotations

import json
import os
import secrets
import uuid
from pathlib import Path

ROLES = ["manager", "lead", "product", "frontend", "backend", "qa"]
# F-9: what the role settings dialog offers. `claude --help` lists fable as a model alias (2026-10-17).
MODELS = ("opus", "sonnet", "haiku", "fable")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Levels each model accepts (F-9 probe, see .aiteams/features/F-9/design.md). A model missing here accepts all.
EFFORT_SUPPORT: dict[str, tuple[str, ...]] = {m: EFFORTS for m in MODELS}

DEFAULT_SETTINGS = {
    "port": 7700,
    "default_model": "opus",
    "models": {},
    "default_effort": None,
    "efforts": {},
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
    for k in ("models", "efforts"):
        if not isinstance(s.get(k), dict):
            s[k] = {}
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
    """The role's model, else default_model, else opus. Hand-edited garbage (non-strings) is skipped, so a bad
    settings.json can't crash a turn at spawn."""
    models = settings.get("models")
    for v in ((models.get(role) if isinstance(models, dict) else None), settings.get("default_model")):
        if isinstance(v, str) and v:
            return v
    return "opus"


def effort_for(settings: dict, role: str) -> str | None:
    """The role's effort, else the default; None (no --effort) if unset, unknown, or not supported by its model."""
    efforts = settings.get("efforts")
    level = (efforts.get(role) if isinstance(efforts, dict) else None) or settings.get("default_effort")
    if not isinstance(level, str) or level not in EFFORTS:
        return None
    return level if level in EFFORT_SUPPORT.get(model_for(settings, role), EFFORTS) else None


def validate_role_settings(body: dict) -> dict:
    """Check the F-9 keys of a settings PUT; returns the body with inherit values ("" / None) removed from
    models/efforts. Raises ValueError with a message for the 400."""
    out = dict(body)
    if "default_model" in out and out["default_model"] not in MODELS:
        raise ValueError(f"default_model must be one of {', '.join(MODELS)}")
    if "default_effort" in out:
        if out["default_effort"] in ("", None):
            out["default_effort"] = None
        elif out["default_effort"] not in EFFORTS:
            raise ValueError(f"default_effort must be null or one of {', '.join(EFFORTS)}")
    for key, allowed in (("models", MODELS), ("efforts", EFFORTS)):
        if key not in out:
            continue
        if not isinstance(out[key], dict):
            raise ValueError(f"{key} must be an object of role -> value")
        clean = {}
        for role, v in out[key].items():
            if role not in ROLES:
                raise ValueError(f"{key}: unknown role {role!r}")
            if v in ("", None):
                continue  # inherit
            if v not in allowed:
                raise ValueError(f"{key}.{role} must be one of {', '.join(allowed)}")
            clean[role] = v
        out[key] = clean
    return out
