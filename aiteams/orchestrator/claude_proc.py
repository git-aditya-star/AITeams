"""Spawn one `claude -p` turn: flags, env, stream-json stdin/stdout, SIGINT/SIGKILL escalation."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
from pathlib import Path

from ..config import plugin_rows

PKG = Path(__file__).resolve().parent.parent
HOOK = str(PKG / "hooks" / "pretooluse.py")
MCP_SERVER = str(PKG / "mcp_server.py")
HOOK_TOOLS = "Bash|Edit|Write|MultiEdit|NotebookEdit|Skill|Artifact"  # F-36: reads are open, so no hook on Read|Grep|Glob


TREE_ENV = ("AITEAMS_TREE", "AITEAMS_TREE_FEATURE", "AITEAMS_WORKTREES_ROOT")  # F-39 B2: where a feature's work lives


def child_env(daemon_url: str, project_id: str, project_path: str, role: str, turn_id: int,
              ask_timeout_s: int = 600, turn_token: str = "", ask_plugins: list[str] | None = None,
              tree: str | None = None, tree_feature: str | None = None, worktrees_root: str | None = None) -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("AITEAMS_TOKEN", None)  # the Master token never reaches a role
    for k in TREE_ENV:  # never inherited: a turn on no feature (or on one without a tree) is exactly today's turn
        env.pop(k, None)
    env.update({
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "30000",
        "MCP_TOOL_TIMEOUT": "700000",
        "AITEAMS_DAEMON_URL": daemon_url,
        "AITEAMS_PROJECT": project_id,
        "AITEAMS_ROLE": role,
        "AITEAMS_TURN": str(turn_id),
        "AITEAMS_TURN_TOKEN": turn_token,
        "AITEAMS_PROJECT_PATH": project_path,
        "AITEAMS_ASK_TIMEOUT_S": str(int(ask_timeout_s)),
        # F-29 B: the real Artifact tool is off in headless runs unless this (undocumented) switch is set; the hook gates
        # every call, and the page never opens a browser on the Master's machine
        "CLAUDE_CODE_ARTIFACT": "1",
        "CLAUDE_CODE_ARTIFACT_AUTO_OPEN": "0",
        # F-23: plugins marked "Ask me first" (the hook gates their skills); read once, so a change applies next turn
        "AITEAMS_ASK_PLUGINS": ",".join(ask_plugins or []),
    })
    # F-39 B2: every turn knows where worktrees live (the hook locks a file there by its absolute path); a turn whose focus
    # feature has a live tree also knows which one (the hook denies main-folder edits for it, build_cmd adds the directory)
    if worktrees_root:
        env["AITEAMS_WORKTREES_ROOT"] = worktrees_root
    if tree:
        env["AITEAMS_TREE"], env["AITEAMS_TREE_FEATURE"] = tree, tree_feature or ""
    return env


def aiteams_env(env: dict) -> dict:
    keys = ("AITEAMS_DAEMON_URL", "AITEAMS_PROJECT", "AITEAMS_ROLE", "AITEAMS_TURN", "AITEAMS_TURN_TOKEN",
            "AITEAMS_PROJECT_PATH", "AITEAMS_ASK_TIMEOUT_S", "PYTHONDONTWRITEBYTECODE")
    return {k: env[k] for k in keys if k in env}


def python_cmd() -> str:
    # The hook and MCP server are stdlib-only; run them with the daemon's interpreter (3.12).
    return sys.executable


def build_cmd(settings: dict, role: str, env: dict, system_prompt_file: str, session_id: str,
              resume: bool, model: str, effort: str | None = None) -> list[str]:
    py = python_cmd()
    hook_settings: dict = {"hooks": {"PreToolUse": [{"matcher": HOOK_TOOLS, "hooks": [
        {"type": "command", "command": f"{py} -B {HOOK}"}]}]}}
    # F-23: exactly the Master's list (on = true, off = false); no key at all when it is empty, so Claude Code's own
    # defaults apply. User-scope plugins still resolve under --setting-sources local (probed for F-4 and F-5).
    rows = plugin_rows(settings)
    if rows:
        hook_settings["enabledPlugins"] = {r["id"]: r["on"] for r in rows}
    mcp = {"mcpServers": {"aiteams": {"command": py, "args": ["-B", MCP_SERVER], "env": aiteams_env(env)}}}
    cmd = ["claude", "-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose",
           "--include-partial-messages", "--include-hook-events",
           "--setting-sources", "local",
           "--permission-mode", "default", "--permission-prompt-tool", "mcp__aiteams__permission",
           "--settings", json.dumps(hook_settings),
           "--mcp-config", json.dumps(mcp), "--strict-mcp-config",
           "--append-system-prompt-file", system_prompt_file, "--system-prompt-snapshot", "off",
           "--disallowedTools", "SendMessage", "ListAgents", "ArtifactComments", "ArtifactData",
           # F-39 B2: the cwd stays the main folder (a changed cwd would lose the resumed session); the feature's tree is added
           *(["--add-dir", env["AITEAMS_TREE"]] if env.get("AITEAMS_TREE") else []),
           "--model", model, *(["--effort", effort] if effort else []),
           "--max-turns", str(settings.get("max_turns", 40))]
    cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
    return cmd


def display_argv(cmd: list[str]) -> list[str]:
    """argv for the `aiteams_cmd` event: the long JSON values of --settings / --mcp-config become "<json>"."""
    return ["<json>" if i and cmd[i - 1] in ("--settings", "--mcp-config") else a for i, a in enumerate(cmd)]


class ClaudeProc:
    """One turn = one process. Events arrive via `events()`; `stderr` collects stderr lines."""

    def __init__(self, cmd: list[str], cwd: str, env: dict):
        self.cmd, self.cwd, self.env = cmd, cwd, env
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr: list[str] = []
        self.saw_init = False
        self.interrupted = False
        self._err_task: asyncio.Task | None = None
        self._caffeinate: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd, cwd=self.cwd, env=self.env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        self._err_task = asyncio.create_task(self._read_err())
        await self._keep_awake()

    async def _keep_awake(self) -> None:
        """F-13: on macOS, `caffeinate -i -w <claude pid>` holds off idle sleep until claude exits. Best effort."""
        exe = shutil.which("caffeinate") if sys.platform == "darwin" else None
        if not exe or not self.proc:
            return
        dn = asyncio.subprocess.DEVNULL
        try:
            self._caffeinate = await asyncio.create_subprocess_exec(exe, "-i", "-w", str(self.proc.pid),
                                                                    stdin=dn, stdout=dn, stderr=dn)
        except (OSError, ValueError):
            self._caffeinate = None

    async def _reap_caffeinate(self) -> None:
        c = self._caffeinate
        if c is None:
            return
        try:
            await asyncio.wait_for(c.wait(), 2)
        except asyncio.TimeoutError:
            try:
                c.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(c.wait(), 2)
            except asyncio.TimeoutError:
                pass

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    async def _read_err(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            self.stderr.append(line.decode("utf-8", "replace").rstrip())
            if len(self.stderr) > 200:
                del self.stderr[0]

    def send_user(self, text: str) -> None:
        assert self.proc and self.proc.stdin
        msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())

    async def events(self):
        """Yield parsed JSON events until EOF. Unparseable lines become {"type": "_raw"}."""
        assert self.proc and self.proc.stdout
        while True:
            try:
                line = await self.proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                yield {"type": "_raw", "text": "<line too long>"}
                continue
            if not line:
                return
            s = line.decode("utf-8", "replace").strip()
            if not s:
                continue
            try:
                ev = json.loads(s)
            except ValueError:
                ev = {"type": "_raw", "text": s[:500]}
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                self.saw_init = True
            yield ev

    def close_stdin(self) -> None:
        try:
            if self.proc and self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass

    def interrupt(self) -> None:
        self.interrupted = True
        self._signal(signal.SIGINT)

    def _signal(self, sig) -> None:
        try:
            if self.proc and self.proc.returncode is None:
                self.proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass

    async def finish(self, grace: float = 15.0) -> int | None:
        """Close stdin, wait `grace` s, then SIGINT (10 s), then SIGKILL."""
        if not self.proc:
            return None
        self.close_stdin()
        try:
            await asyncio.wait_for(self.proc.wait(), grace)
        except asyncio.TimeoutError:
            self._signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except asyncio.TimeoutError:
                self._signal(signal.SIGKILL)
                try:
                    await asyncio.wait_for(self.proc.wait(), 5)
                except asyncio.TimeoutError:
                    pass
        if self._err_task:
            try:
                await asyncio.wait_for(self._err_task, 2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        await self._reap_caffeinate()
        return self.proc.returncode


def kill_orphan(pid: int) -> bool:
    """SIGINT then SIGKILL a pid if it looks like a claude process. Returns True if it existed."""
    import subprocess
    import time
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0 or "claude" not in out.stdout:
        return False
    try:
        os.kill(pid, signal.SIGINT)
        for _ in range(50):
            time.sleep(0.1)
            os.kill(pid, 0)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        return False
    return True
