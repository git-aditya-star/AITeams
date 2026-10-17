"""F-11 commit groups as a pure graph walk over commit dicts (stdlib only: `aiteams upgrade` uses it on a read-only
copy of a project DB, the daemon on ProjectDB rows). A dict needs `id`, `state`, `note` and `requires` (list of ids)."""
from __future__ import annotations

from typing import Callable

PENDING = ("lead_review", "master_review")


def superseded(c: dict) -> bool:
    return c["state"] == "rejected" and (c.get("note") or "").startswith("superseded by #")


def component(start: dict, linked: list[dict], get: Callable[[int], dict | None]) -> list[dict]:
    """The requests linked to `start` through `requires` (either direction), in dependency order (a request after
    everything it requires; ties by id). Rejected requests are left out (a rejected dependent releases its base; a
    rejected requirement blocks through `rejected_requirements`); a rejected `start` is a group of one.
    `linked` = every request with a non-empty `requires`; `get(id)` loads any other request."""
    if start["state"] == "rejected":
        return [start]
    nbrs: dict[int, set[int]] = {}
    for d in linked:
        for n in d["requires"]:
            nbrs.setdefault(d["id"], set()).add(n)
            nbrs.setdefault(n, set()).add(d["id"])
    members, todo = {start["id"]: start}, [start["id"]]
    while todo:
        for n in nbrs.get(todo.pop(), ()):
            if n not in members:
                r = get(n)
                if r is not None and r["state"] != "rejected":
                    members[n] = r
                    todo.append(n)
    out, done = [], set()
    while len(out) < len(members):
        ready = [m for i, m in sorted(members.items()) if i not in done
                 and all(n in done or n not in members for n in m["requires"])]
        nxt = ready[0] if ready else next(m for i, m in sorted(members.items()) if i not in done)  # no cycles; never hang
        out.append(nxt)
        done.add(nxt["id"])
    return out


def rejected_requirements(group: list[dict], get: Callable[[int], dict | None]) -> list[tuple[dict, int]]:
    """(member, required id) for each not-yet-committed member that requires a rejected, not superseded request."""
    out = []
    for m in group:
        if m["state"] == "committed":
            continue
        for n in m["requires"]:
            r = get(n)
            if r is not None and r["state"] == "rejected" and not superseded(r):
                out.append((m, n))
    return out
