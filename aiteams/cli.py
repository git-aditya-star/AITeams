"""aiteams | serve | stop | status | upgrade | token"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

# `aiteams upgrade` replaces the venv this process imported from: import everything it needs up front.
from . import config, upgrade


def _headers(token: str | None = None) -> dict:
    return {"Content-Type": "application/json", "Authorization": "Bearer " + (token or config.master_token())}


def _get(url: str, timeout: float = 2.0, auth: bool = True):
    req = urllib.request.Request(url, headers=_headers() if auth else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url: str, body: dict, timeout: float = 10.0, token: str | None = None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=_headers(token), method="POST")
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
        return bool(_get(url + "/api/health", auth=False).get("ok"))
    except (OSError, ValueError):
        return False


def _health(url: str) -> dict:
    return _get(url + "/api/health", auth=False)


def start_daemon(exe: list[str], port: int) -> str:
    """Spawn `<exe> serve --daemon --port <port>` detached and wait for health."""
    config.home().mkdir(parents=True, exist_ok=True)
    log = open(config.home() / "daemon.log", "ab")
    subprocess.Popen([*exe, "serve", "--daemon", "--port", str(port)],
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
                     env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        time.sleep(0.1)
        if is_up(url):
            return url
    print("daemon did not come up; see", config.home() / "daemon.log", file=sys.stderr)
    sys.exit(1)


def ensure_daemon() -> str:
    url = daemon_url()
    if is_up(url):
        return url
    port = int(config.load_settings().get("port", 7700))
    exe = [sys.executable, "-m", "aiteams.cli"]
    if config.runtime_info().get("sha"):
        # A snapshot is installed: start it, not the (possibly activated dev venv) interpreter running this CLI.
        try:
            exe = [str(upgrade.tool_bin() / "aiteams")]
        except upgrade.UpgradeError:
            pass
    return start_daemon(exe, port)


def stop_daemon() -> bool:
    """POST /api/shutdown, wait for health and pid to go, SIGTERM fallback. True when it was running."""
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
                config.clear_daemon()
                return True
        print("daemon did not stop gracefully", file=sys.stderr)
    if d and d.get("pid"):
        try:
            os.kill(int(d["pid"]), signal.SIGTERM)
            print("sent SIGTERM to", d["pid"])
        except ProcessLookupError:
            config.clear_daemon()
            return False
        config.clear_daemon()
        return True
    return False


# ---- commands ----------------------------------------------------------------------------------
def cmd_default(_args) -> None:
    url = ensure_daemon()
    cwd = os.getcwd()
    res = _post(url + "/api/projects/register", {"path": cwd})
    page = f"{url}/p/{res['id']}"
    if not res.get("is_git"):
        print("warning: not a git repo:", cwd)
    print(("registered" if res.get("created") else "project") + f" {res['name']} -> {page}")
    link = _post(url + "/api/auth/link", {})
    webbrowser.open(link["url"] + f"&next=/p/{res['id']}")


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
    print("daemon stopped" if stop_daemon() else "daemon not running")


def cmd_status(_args) -> None:
    url = daemon_url()
    if not is_up(url):
        print("daemon: down")
        return
    h = _health(url)
    print(f"daemon: up  pid={h['pid']} port={h['port']} running_turns={h.get('running_turns', 0)}")
    if h.get("sha"):
        head = h.get("source_head")
        print(f"runtime: v{h.get('version')} {h['sha'][:7]} ({h.get('ref')}) from {h.get('source')}, "
              f"HEAD {head[:7] if head else '?'}" + ("  upgrade available" if head and head != h["sha"] else ""))
    else:
        print(f"runtime: v{h.get('version')} (editable dev install, no snapshot)")
    for p in _get(url + "/api/projects"):
        team = _get(f"{url}/api/p/{p['id']}/team")
        running = [r["role"] for r in team["roles"] if r["state"] == "running"]
        queued = [r["role"] for r in team["roles"] if r["state"] == "queued"]
        flag = "  schema_ahead" if p.get("schema_ahead") else ""
        print(f"  {p['id']}  {p['name']}  {p['path']}  running={running} queued={queued}{flag}")


def cmd_token(args) -> None:
    if args.rotate:
        old = config.master_token()
        new = config.write_token(secrets.token_urlsafe(32))
        url = daemon_url()
        if is_up(url):
            try:
                _post(url + "/api/token/reload", {}, token=old)
            except (OSError, ValueError) as e:
                print("warning: daemon did not reload the token:", e, file=sys.stderr)
        print(new)
        return
    if args.link:
        url = ensure_daemon()
        print(_post(url + "/api/auth/link", {})["url"] + "&next=/")
        return
    print(config.master_token())


def _source_repo(arg: str | None) -> str:
    if arg:
        src = os.path.realpath(os.path.expanduser(arg))
    elif config.runtime_info().get("source"):
        src = config.runtime_info()["source"]
    else:
        src = os.getcwd()
    if not upgrade.is_source_repo(src):
        print(f"{src} is not an AITeams checkout; run `aiteams upgrade` inside one or pass --src PATH", file=sys.stderr)
        sys.exit(2)
    return src


def cmd_upgrade(args) -> None:
    src = _source_repo(args.src)
    ref = args.ref or "HEAD"
    try:
        sha = upgrade.resolve_ref(src, ref)
        print(f"resolve   {ref} -> {sha[:7]} in {src}")
        if ref == "HEAD" and upgrade.dirty(src):
            print("warning: working tree has uncommitted changes; the snapshot is HEAD, not the tree")
        for line in upgrade.half_committed_groups(src):  # F-11: a warning only, never blocks
            print(line)
        snap = upgrade.snapshot(src, sha, config.runtime_dir())
        version = upgrade.snapshot_version(snap)
        print(f"snapshot  {snap}")
        # Before the stop, so a missing Node or a broken build costs no downtime.
        print(upgrade.build_ui(snap))
        print("stop      " + ("daemon stopped" if stop_daemon() else "daemon was not running"))
        print(f"install   uv tool install --reinstall --force {snap}")
        upgrade.install(snap)
        config.write_runtime_info(upgrade.runtime_record(sha, ref, src, version))
        print(f"record    {config.home() / 'runtime.json'}")
        exe = upgrade.tool_bin() / "aiteams"
    except upgrade.UpgradeError as e:
        print("upgrade aborted:", e, file=sys.stderr)
        sys.exit(1)
    port = int(config.load_settings().get("port", 7700))
    url = start_daemon([str(exe)], port)
    print(f"upgraded to {sha[:7]} ({version}), daemon pid {_health(url)['pid']}")


def _clean_pycache() -> None:
    # The tool shim runs without -B; drop the __pycache__ that slips in so the venv stays clean.
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
    u = sub.add_parser("upgrade", help="snapshot the source repo at REF (default HEAD) and run the daemon from it")
    u.add_argument("ref", nargs="?")
    u.add_argument("--src", help="AITeams checkout to snapshot (default: the one remembered in runtime.json, else cwd)")
    t = sub.add_parser("token", help="print the Master token")
    t.add_argument("--link", action="store_true", help="print a fresh single-use sign-in URL")
    t.add_argument("--rotate", action="store_true", help="write a new token and tell the daemon; open tabs sign out")
    args = ap.parse_args(argv)
    {"serve": cmd_serve, "stop": cmd_stop, "status": cmd_status, "upgrade": cmd_upgrade, "token": cmd_token,
     None: cmd_default}[args.cmd](args)


if __name__ == "__main__":
    main()
