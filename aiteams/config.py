"""Global state under ~/.aiteams: daemon.json, settings.json, projects.json, token, runtime.json."""
from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from pathlib import Path

ROLES = ["manager", "lead", "lead-2", "product", "frontend", "backend", "qa"]
# F-22: two Leads. Lead 1 keeps the internal id `lead` (every table, DM and setting already uses it), Lead 2 is
# `lead-2`; people see "Lead 1" / "Lead 2". `@lead-1` is an alias for `lead`.
LEADS = ("lead", "lead-2")
ROLE_NAMES = {"lead": "Lead 1", "lead-2": "Lead 2", **{r: r.capitalize() for r in ROLES if r not in LEADS}}


def role_name(role: str) -> str:
    """What the team and the Master see: "Lead 1", "Lead 2", "Backend"."""
    return ROLE_NAMES.get(role) or str(role).capitalize()


def base_role(role: str) -> str:
    """The role whose prompt file an instance uses: lead-2 -> lead."""
    return role[:-2] if len(role) > 2 and role[-2] == "-" and role[-1].isdigit() else role


def canonical_role(name) -> str:
    """Lower-case id without a leading @, with the Lead 1 alias resolved: lead-1 -> lead."""
    r = str(name or "").strip().lstrip("@").lower()
    return "lead" if r == "lead-1" else r
# F-9: what the role settings dialog offers. `claude --help` lists fable as a model alias (2026-10-17).
# Part 2: (id, label) pairs, aliases first. Only IDs the probe accepted AND that answer as themselves
# (claude-opus-4-1 is accepted but runs Opus 5.5, so it is left out). See .aiteams/features/F-9/design.md.
MODELS: tuple[tuple[str, str], ...] = (
    ("opus", "Opus (latest)"), ("sonnet", "Sonnet (latest)"), ("haiku", "Haiku (latest)"), ("fable", "Fable (latest)"),
    ("claude-opus-5-5", "Opus 5.5"), ("claude-opus-5", "Opus 5"), ("claude-opus-4-5", "Opus 4.5"),
    ("claude-sonnet-5-5", "Sonnet 5.5"), ("claude-sonnet-5", "Sonnet 5"), ("claude-sonnet-4-5", "Sonnet 4.5"),
    ("claude-fable-5-1", "Fable 5.1"), ("claude-fable-5", "Fable 5"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5"),
)
MODEL_IDS = tuple(i for i, _ in MODELS)
MODEL_LABELS = dict(MODELS)
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Levels each model accepts (F-9 probe, see .aiteams/features/F-9/design.md). A model missing here accepts all.
EFFORT_SUPPORT: dict[str, tuple[str, ...]] = {m: EFFORTS for m in MODEL_IDS}

DEFAULT_SETTINGS = {
    "port": 7700,
    "default_model": "opus",
    "models": {},
    "default_effort": None,
    "efforts": {},
    "max_concurrent": 3,
    "ask_timeout_s": 600,
    "max_turns": 100,  # F-37: claude's --max-turns for a normal turn; turn_timeout_s is the real wall-clock guard
    "max_turn_continues": 2,  # F-37: how many capped turns in a row a role is re-woken automatically
    "turn_timeout_s": 1200,
    "stuck_turn_s": 1200,  # F-38: a turn running or a wake queued this long is flagged to the Manager (never stopped)
    "rotate_pct": 0.6,
    "stale_hours": 4,
    "fail_streak_rotate": 3,
    # F-23: one global plugin list for every role's turn, [{"id": "name@marketplace", "on": bool, "ask": bool}].
    # Empty by default (upgrade included): an unlisted plugin is left to Claude Code's own default.
    "plugins": [],
}
MAX_TURNS_ERROR = "max turns reached"  # the `error` of a turn that ended with claude's error_max_turns (state stays done)
PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$")


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
    s["plugins"] = list(s["plugins"]) if isinstance(s.get("plugins"), list) else []
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


def model_label(model: str) -> str:
    """Friendly label for an offered ID, else the raw ID (a saved ID that is no longer offered)."""
    return MODEL_LABELS.get(model, model)


def plugin_name(plugin_id: str) -> str:
    """`project-notes@project-notes` -> `project-notes`: the `<name>:` prefix of that plugin's skills."""
    return str(plugin_id).split("@", 1)[0]


def plugin_rows(settings: dict) -> list[dict]:
    """The usable rows of settings["plugins"], first one per id. A hand-edited settings.json (non-objects, bad ids)
    is skipped, so it can't crash a turn at spawn."""
    rows, seen = [], set()
    plugins = settings.get("plugins")
    for r in plugins if isinstance(plugins, list) else []:
        pid = r.get("id") if isinstance(r, dict) else None
        if not (isinstance(pid, str) and PLUGIN_ID_RE.match(pid)) or pid.lower() in seen:
            continue
        seen.add(pid.lower())
        rows.append({"id": pid, "on": r.get("on") is not False, "ask": r.get("ask") is True})
    return rows


def ask_plugin_names(settings: dict) -> list[str]:
    """Names (plugin_name) of the plugins marked "Ask me first", for AITEAMS_ASK_PLUGINS."""
    names = []
    for r in plugin_rows(settings):
        if r["ask"] and plugin_name(r["id"]) not in names:
            names.append(plugin_name(r["id"]))
    return names


def validate_plugins(rows) -> list[dict]:
    """F-23: check the `plugins` of a settings PUT; returns clean `{id, on, ask}` rows. Raises ValueError for the 400."""
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("plugins must be a list of {id, on, ask} objects")
    out, seen = [], set()
    for r in rows:
        pid = r.get("id")
        pid = pid.strip() if isinstance(pid, str) else pid
        if not (isinstance(pid, str) and PLUGIN_ID_RE.match(pid)):
            raise ValueError(f"plugin id {pid!r} must look like name@marketplace (letters, digits, . _ -)")
        if pid.lower() in seen:
            raise ValueError(f"plugin {pid} is listed twice")
        seen.add(pid.lower())
        on, ask = r.get("on", True), r.get("ask", False)
        if not isinstance(on, bool) or not isinstance(ask, bool):
            raise ValueError(f"plugin {pid}: on and ask must be true or false")
        out.append({"id": pid, "on": on, "ask": ask})
    return out


def validate_role_settings(body: dict, current: dict | None = None) -> dict:
    """Check the F-9 keys of a settings PUT; returns the body with inherit values ("" / None) removed from
    models/efforts. Raises ValueError with a message for the 400. Only keys in the body are checked, and a model
    equal to what `current` already stores for that key is accepted even if no longer offered (the dialog
    re-sends the whole map)."""
    out, current = dict(body), current or {}
    stored_models = current.get("models") if isinstance(current.get("models"), dict) else {}
    if "default_model" in out and out["default_model"] not in MODEL_IDS \
            and not (isinstance(out["default_model"], str) and out["default_model"] == current.get("default_model")):
        raise ValueError(f"default_model must be one of {', '.join(MODEL_IDS)}")
    if "default_effort" in out:
        if out["default_effort"] in ("", None):
            out["default_effort"] = None
        elif out["default_effort"] not in EFFORTS:
            raise ValueError(f"default_effort must be null or one of {', '.join(EFFORTS)}")
    for key, allowed in (("models", MODEL_IDS), ("efforts", EFFORTS)):
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
            if key == "models" and isinstance(v, str) and v == stored_models.get(role):
                clean[role] = v  # unchanged, possibly no longer offered: keep it
                continue
            if v not in allowed:
                raise ValueError(f"{key}.{role} must be one of {', '.join(allowed)}")
            clean[role] = v
        out[key] = clean
    if "plugins" in out:
        out["plugins"] = validate_plugins(out["plugins"])
    return out
