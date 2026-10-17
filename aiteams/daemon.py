"""FastAPI app factory: lifespan (recover/shutdown), routers, static UI."""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api.features import feat_api
from .api.internal import internal
from .api.public import COOKIE, api, master_auth, open_api, signin_page, token_ok, wsr
from .orchestrator.scheduler import Scheduler
from .runtime import AppState

STATIC = Path(__file__).resolve().parent / "static"


def create_app(port: int, write_daemon_json: bool = True) -> FastAPI:
    state = AppState(port)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.load_projects()
        state.scheduler = Scheduler(state)
        await state.scheduler.recover()
        state.scheduler.start()
        if write_daemon_json:
            config.save_daemon(os.getpid(), port, time.time())
        try:
            yield
        finally:
            await state.scheduler.shutdown()
            for p in state.projects.values():
                p.db.close()
            if write_daemon_json:
                config.clear_daemon()

    app = FastAPI(title="AITeams", lifespan=lifespan)
    app.state.app = state
    app.include_router(open_api)
    app.include_router(api, dependencies=[Depends(master_auth)])
    app.include_router(feat_api, dependencies=[Depends(master_auth)])
    app.include_router(internal)
    app.include_router(wsr)

    index = STATIC / "index.html"
    if (STATIC / "assets").is_dir():  # hashed static files, nothing secret: public
        app.mount("/assets", StaticFiles(directory=str(STATIC / "assets")), name="assets")

    @app.get("/")
    @app.get("/p/{pid}")
    @app.get("/p/{pid}/{rest:path}")
    def spa(request: Request, pid: str = "", rest: str = ""):
        if not token_ok(state, request.cookies.get(COOKIE)):
            return signin_page()
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"error": "UI not built: run `cd ui && npm run build`, then `aiteams upgrade`"}, status_code=503)

    return app
