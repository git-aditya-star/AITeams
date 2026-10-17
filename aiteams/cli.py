"""aiteams | serve | stop | status"""
from __future__ import annotations

import argparse
import atexit
import json
import shutil
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from . import config


def _get(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url: str, body: dict, timeout: float = 10.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def daemon_url() -> str | None:
    d = config.load_daemon()
    return f"http://127.0.0.1:{d['port']}" if d and d.get("port") else None


def is_up(url: str | None) -> bool:
    if not url:
        return False
    try:
        return bool(_get(url + "/api/health").get("ok"))
    except (OSError, ValueError):
        return False


def ensure_daemon() -> str:
    url = daemon_url()
    if is_up(url):
        return url
    port = int(config.load_settings().get("port", 7700))
    config.home().mkdir(parents=True, exist_ok=True)
    log = open(config.home() / "daemon.log", "ab")
    subprocess.Popen([sys.executable, "-m", "aiteams.cli", "serve", "--daemon", "--port", str(port)],
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
                     env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        time.sleep(0.1)
        if is_up(url):
            return url
    print("daemon did not come up; see", config.home() / "daemon.log", file=sys.stderr)
    sys.exit(1)


def cmd_default(_args) -> None:
    url = ensure_daemon()
    cwd = os.getcwd()
    res = _post(url + "/api/projects/register", {"path": cwd})
    page = f"{url}/p/{res['id']}"
    if not res.get("is_git"):
        print("warning: not a git repo:", cwd)
    print(("registered" if res.get("created") else "project") + f" {res['name']} -> {page}")
    webbrowser.open(page)


def cmd_serve(args) -> None:
    import uvicorn
    from .daemon import create_app

    port = int(args.port or config.load_settings().get("port", 7700))
    app = create_app(port)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", ws_ping_interval=20,
                         timeout_graceful_shutdown=5)
    server = uvicorn.Server(cfg)

    def request_shutdown():
        server.should_exit = True

    app.state.app.request_shutdown = request_shutdown
    server.run()


def cmd_stop(_args) -> None:
    url = daemon_url()
    d = config.load_daemon()
    if is_up(url):
        try:
            _post(url + "/api/shutdown", {})
        except (OSError, ValueError):
            pass
        # The daemon stops answering at once but SIGINTs running turns first; wait for the pid to exit.
        pid = int(d["pid"]) if d and d.get("pid") else 0
        for _ in range(450):
            time.sleep(0.1)
            if not is_up(url) and not _alive(pid):
                print("daemon stopped")
                config.clear_daemon()
                return
        print("daemon did not stop gracefully", file=sys.stderr)
    if d and d.get("pid"):
        try:
            os.kill(int(d["pid"]), signal.SIGTERM)
            print("sent SIGTERM to", d["pid"])
        except ProcessLookupError:
            print("daemon not running")
        config.clear_daemon()
    else:
        print("daemon not running")


def cmd_status(_args) -> None:
    url = daemon_url()
    if not is_up(url):
        print("daemon: down")
        return
    h = _get(url + "/api/health")
    print(f"daemon: up  pid={h['pid']} port={h['port']} running_turns={h.get('running_turns', 0)}")
    for p in _get(url + "/api/projects"):
        team = _get(f"{url}/api/p/{p['id']}/team")
        running = [r["role"] for r in team["roles"] if r["state"] == "running"]
        queued = [r["role"] for r in team["roles"] if r["state"] == "queued"]
        print(f"  {p['id']}  {p['name']}  {p['path']}  running={running} queued={queued}")


def _clean_pycache() -> None:
    # __init__ is compiled before it can set dont_write_bytecode; remove what slipped through.
    shutil.rmtree(os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__"), ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    atexit.register(_clean_pycache)
    ap = argparse.ArgumentParser(prog="aiteams")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int)
    s.add_argument("--daemon", action="store_true")
    sub.add_parser("stop")
    sub.add_parser("status")
    args = ap.parse_args(argv)
    {"serve": cmd_serve, "stop": cmd_stop, "status": cmd_status, None: cmd_default}[args.cmd](args)


if __name__ == "__main__":
    main()
