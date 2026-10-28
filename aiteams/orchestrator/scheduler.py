"""Global scheduler: one running turn per role, max_concurrent across projects, Master-first, pause."""
from __future__ import annotations

import asyncio
import time

from .. import stalls
from . import lifecycle
from .claude_proc import kill_orphan
from .turn import TurnRunner


class Scheduler:
    def __init__(self, state):
        self.state = state
        self.running: dict[tuple[str, str], TurnRunner] = {}
        self.stopping = False
        self.flagged: set = set()  # F-38: (project id, "turn"|"wake", id) of the long turns already reported
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def notify(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while not self.stopping:
            try:
                self._tick()
                await self._flag_long()
            except Exception as e:  # keep the loop alive
                print("scheduler tick error:", e)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), 5.0)
            except asyncio.TimeoutError:
                pass

    def _tick(self) -> None:
        if self.stopping or self.state.rate_limit_active():
            return
        cap = int(self.state.settings.get("max_concurrent", 3))
        candidates = []
        for proj in self.state.projects.values():
            if proj.paused or proj.schema_ahead:
                continue
            per_role: dict[str, dict] = {}
            for w in proj.db.queued_wakes():  # master-first, then FIFO
                if (proj.id, w["role"]) in self.running:
                    continue
                cur = per_role.get(w["role"])
                # A pending handoff turn goes first for its role; everything else waits for the fresh session.
                if cur is None or (w["kind"] == "handoff" and cur["kind"] != "handoff"):
                    per_role[w["role"]] = w
            for w in per_role.values():
                candidates.append((0 if w["origin"] == "master" else 1, w["created_at"], proj, w))
        candidates.sort(key=lambda c: (c[0], c[1]))
        started_roles = set()
        for _, _, proj, w in candidates:
            if len(self.running) >= cap:
                break
            key = (proj.id, w["role"])
            if key in self.running or key in started_roles:
                continue
            started_roles.add(key)
            # Mark started here (same loop slice) so a concurrent post cannot merge into a wake we already took.
            proj.db.set_wake_state(w["id"], "started")
            w = proj.db.wake(w["id"])
            runner = TurnRunner(self.state, proj, w)
            self.running[key] = runner
            runner.task = asyncio.create_task(self._run(key, runner))

    async def _flag_long(self) -> None:
        """F-38: report (once each) a turn running, or a wake queued, past `stuck_turn_s`. Never stops anything. It runs
        beside _tick (every 5 s at most); a paused or rate-limited project is waiting by design, so it isn't flagged."""
        if self.stopping or self.state.rate_limit_active():
            return
        for proj in list(self.state.projects.values()):
            if not (proj.paused or proj.schema_ahead):
                await stalls.flag_long_turns(self.state, proj, self.flagged)

    async def _run(self, key, runner: TurnRunner) -> None:
        try:
            await runner.run()
        except Exception as e:
            try:
                if runner.turn:
                    runner.turn = runner.db.update_turn(runner.turn["id"], state="failed", ended_at=time.time(),
                                                        error=f"runner crashed: {e!r}")
                    await runner.project.hub.broadcast("turn_state", runner.turn)
            except Exception:
                pass
        finally:
            self.running.pop(key, None)
            await self.release_locks(runner.project, runner.role)
            if runner.turn:
                runner.project.delivered.pop(runner.turn["id"], None)  # F-16: the watermark lives for one turn
            try:
                await lifecycle.after_turn(self.state, runner.project, runner)
            except Exception as e:
                print("after_turn error:", e)
            if runner.interrupt_reason == "shutdown":
                await self.requeue_once(runner.project, runner.wake, runner.role)
            elif not self.stopping:
                try:  # F-38: the last turn of an idle project just ended; anything still waiting on a role climbs a rung
                    await stalls.check_idle(self.state, runner.project)
                except Exception as e:
                    print("stalls error:", e)
            self.notify()

    def stop(self, pid: str, role: str, reason: str, note: str | None = None) -> bool:
        """SIGINT the role's running turn (Master Stop / Manager interrupt). False when idle."""
        runner = self.running.get((pid, role))
        if runner is None:
            return False
        runner.interrupt(reason, note)
        return True

    # ---- recovery / shutdown ----------------------------------------------
    async def release_locks(self, project, role: str) -> None:
        """Locks live for one turn: drop the role's locks and tell the UI."""
        try:
            if project.db.release_locks(role):
                await project.hub.broadcast("locks", {"locks": project.db.locks()})
        except Exception:
            pass

    async def requeue_once(self, project, wake: dict | None, role: str) -> None:
        db = project.db
        if wake and wake.get("kind", "normal") != "normal":
            if wake["kind"] == "handoff" and db.role_state(role)["rotate_handoff"]:
                db.add_wake(role, wake["conversation_id"], [], "system", kind="handoff")
            elif wake["kind"] in ("standup", "standup_compile", "brainstorm", "brainstorm_compile"):
                db.add_wake(role, wake["conversation_id"], [], "system", kind=wake["kind"], ref=wake.get("ref"))
        elif wake and wake.get("origin") != "system":
            db.add_wake(role, wake["conversation_id"], wake["message_ids"], "system")
        else:
            msg = db.add_message("dm-" + role, "system", "System",
                                 f"A turn for {role} was interrupted twice; its wake was not re-queued.", [], None)
            await project.hub.broadcast("message", msg)

    async def recover(self) -> None:
        """On daemon start: running turns -> failed, orphan pids killed, wakes re-queued once."""
        for proj in self.state.projects.values():
            if proj.schema_ahead:
                continue
            db = proj.db
            for t in [t for t in db.turns(limit=500) if t["state"] in ("running", "queued")]:
                if t.get("pid"):
                    await asyncio.to_thread(kill_orphan, int(t["pid"]))
                db.update_turn(t["id"], state="failed", ended_at=time.time(),
                               error=(t.get("error") or "") + " daemon restarted while running")
                wake = db.wake(t["wake_id"]) if t.get("wake_id") else None
                await self.release_locks(proj, t["role"])
                await self.requeue_once(proj, wake, t["role"])
            for role in [r["role"] for r in db.q("SELECT role FROM role_state WHERE rotate_handoff=1")]:
                if not db.queued_wake(role, "handoff"):
                    db.add_wake(role, "dm-" + role, [], "system", kind="handoff")
            try:  # F-38: once at daemon start, for a project that came up idle
                await stalls.check_idle(self.state, proj)
            except Exception as e:
                print("stalls error:", e)
        self.notify()

    async def shutdown(self) -> None:
        """SIGINT every running turn, wait for their result (<= 15 s); runners re-queue their wakes."""
        self.stopping = True
        runners = list(self.running.values())
        for r in runners:
            r.interrupt("shutdown")
        tasks = [r.task for r in runners if r.task]
        if tasks:
            await asyncio.wait(tasks, timeout=30)
        if self._task:
            self._task.cancel()
