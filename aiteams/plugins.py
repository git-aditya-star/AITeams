"""F-23: the "missing plugin" check. Each turn's `system/init` event lists the plugins Claude Code loaded; a row the Master
switched On that is not in it is flagged (badge in Role settings, one toast per flip). It never blocks or fails a turn."""
from __future__ import annotations

import time

from .config import plugin_name, plugin_rows


def loaded_plugins(init: dict) -> set[str] | None:
    """Lower-cased names and ids Claude Code reports in init.plugins ([{name, path, source: "name@marketplace"}], probed
    2026-10-18); None when the event carries no plugin list at all (nothing to compare, so nothing is flagged)."""
    plugins = init.get("plugins")
    if not isinstance(plugins, list):
        return None
    out: set[str] = set()
    for p in plugins:
        for v in ((p.get("name"), p.get("source")) if isinstance(p, dict) else (p,)):
            if isinstance(v, str) and v:
                out.add(v.lower())
    return out


async def check_plugins(st, proj, role: str, init: dict) -> list[str]:
    """Compare the On rows with `init`, update st.plugin_status (global, last turn wins) and broadcast `plugin_missing
    {id, role}` on the project's hub once per flip to missing. Returns the ids flagged by this call."""
    loaded = loaded_plugins(init)
    if loaded is None:
        return []
    on = [r["id"] for r in plugin_rows(st.settings) if r["on"]]
    now, flagged = time.time(), []
    for pid in list(st.plugin_status):
        if pid not in on:
            del st.plugin_status[pid]  # switched off or removed: a later flip toasts again
    for pid in on:
        missing = pid.lower() not in loaded and plugin_name(pid).lower() not in loaded
        was = st.plugin_status.get(pid)
        st.plugin_status[pid] = {"missing": missing, "checked_at": now, "role": role}
        if missing and not (was and was["missing"]):
            flagged.append(pid)
            await proj.hub.broadcast("plugin_missing", {"id": pid, "role": role})
    return flagged


def plugins_view(st) -> dict:
    """GET /api/settings/plugins: the saved rows plus the last check's status per On row."""
    return {"rows": plugin_rows(st.settings), "status": dict(st.plugin_status)}
