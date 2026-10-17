"""Receipt transitions (queued -> seen -> acked) with WS fan-out."""
from __future__ import annotations

from ..db import ProjectDB


async def mark(db: ProjectDB, hub, message_ids: list[int], role: str, state: str) -> None:
    for mid in message_ids:
        if db.set_receipt(mid, role, state):
            await hub.broadcast("receipt", {"message_id": mid, "role": role, "state": state})
