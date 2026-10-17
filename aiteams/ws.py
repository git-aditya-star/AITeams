"""Per-project websocket hub: fan-out of {event, data} frames."""
from __future__ import annotations

import json

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, event: str, data) -> None:
        if not self.clients:
            return
        frame = json.dumps({"event": event, "data": data}, default=str)
        for ws in list(self.clients):
            try:
                await ws.send_text(frame)
            except Exception:
                self.disconnect(ws)
