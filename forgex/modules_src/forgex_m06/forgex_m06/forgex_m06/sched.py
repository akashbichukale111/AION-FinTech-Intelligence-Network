"""Deterministic trace + cooperative scheduler (seeded interleavings, locks, deadlock detection)."""
from __future__ import annotations
import random
from .money import jsonable


class Crash(Exception):
    """Simulated process failure in the middle of a request."""


class ResponseLost(Exception):
    """Simulated network timeout: server committed, client never saw the response."""


class DeadlockVictim(Exception):
    pass


class Trace:
    def __init__(self):
        self.events: list[dict] = []
        self.seq = 0

    def emit(self, actor, op, **data):
        self.seq += 1
        self.events.append({"seq": self.seq, "actor": actor, "op": op, **jsonable(data)})
        return self.seq

    def refs(self, **match) -> list[int]:
        return [e["seq"] for e in self.events if all(str(e.get(k)) == str(v) for k, v in match.items())]


def drive(gen):
    """Run a generator to completion single-threaded (no contention)."""
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


class Scheduler:
    """Cooperative scheduler. Actors are generators yielding None (preemption point),
    ('lock', res) or ('unlock', res). Every choice is recorded so a run can be replayed exactly."""

    def __init__(self, trace: Trace, seed: int = 0, schedule=None, abort_on_deadlock=False):
        self.trace, self.rng = trace, random.Random(seed)
        self.fixed, self.abort = list(schedule or []), abort_on_deadlock
        self.a: dict[str, dict] = {}
        self.locks: dict[str, str] = {}
        self.used: list[str] = []
        self.deadlock: list[str] | None = None
        self.deadlocks_seen = 0

    def spawn(self, name, gen):
        self.a[name] = {"gen": gen, "pending": None, "done": False, "result": None, "error": None}

    def _runnable(self, n):
        a = self.a[n]
        if a["done"]:
            return False
        p = a["pending"]
        return not (p and self.locks.get(p) not in (None, n))

    def _cycle(self):
        edges = {n: self.locks.get(a["pending"]) for n, a in self.a.items() if not a["done"] and a["pending"]}
        for s in edges:
            seen, cur = [s], edges.get(s)
            while cur is not None and cur in edges:
                if cur in seen:
                    return seen[seen.index(cur):]
                seen.append(cur)
                cur = edges[cur]
        return None

    def _pick(self, ready):
        while self.fixed:
            c = self.fixed.pop(0)
            if c in ready:
                return c
        return self.rng.choice(sorted(ready))

    def _release(self, n):
        for r in [r for r, o in self.locks.items() if o == n]:
            del self.locks[r]

    def _finish(self, n, result=None, error=None):
        a = self.a[n]
        a.update(done=True, result=result, error=error, pending=None)
        self._release(n)

    def _kill(self, n):
        a = self.a[n]
        try:
            a["gen"].throw(DeadlockVictim("aborted as deadlock victim"))
        except (DeadlockVictim, StopIteration):
            pass
        except Exception:
            pass
        self.trace.emit("scheduler", "deadlock_victim_aborted", victim=n)
        self._finish(n, error="DeadlockVictim")

    def run(self, max_steps=20000):
        steps = 0
        while steps < max_steps:
            live = [n for n, a in self.a.items() if not a["done"]]
            if not live:
                break
            ready = [n for n in live if self._runnable(n)]
            if not ready:
                cyc = self._cycle() or live
                self.deadlocks_seen += 1
                self.trace.emit("scheduler", "deadlock_detected", cycle=cyc,
                                waits={n: self.a[n]["pending"] for n in live if self.a[n]["pending"]},
                                holders=dict(self.locks))
                if self.abort:
                    self._kill(sorted(cyc)[0])
                    continue
                self.deadlock = cyc
                break
            n = self._pick(ready)
            steps += 1
            self.used.append(n)
            self._step(n)
        return self

    def _step(self, n):
        a = self.a[n]
        if a["pending"]:
            res = a["pending"]
            self.locks[res] = n
            a["pending"] = None
            self.trace.emit(n, "lock_acquired", resource=res)
            return
        try:
            req = next(a["gen"])
        except StopIteration as e:
            self._finish(n, result=e.value)
            return
        except Exception as e:  # noqa: BLE001 - simulated failures surface as actor errors
            self.trace.emit(n, "actor_error", error=f"{type(e).__name__}: {e}")
            self._finish(n, error=f"{type(e).__name__}: {e}")
            return
        if isinstance(req, tuple):
            kind, res = req
            if kind == "lock":
                holder = self.locks.get(res)
                if holder in (None, n):
                    self.locks[res] = n
                    self.trace.emit(n, "lock_acquired", resource=res)
                else:
                    a["pending"] = res
                    self.trace.emit(n, "lock_wait", resource=res, holder=holder)
            elif kind == "unlock" and self.locks.get(res) == n:
                del self.locks[res]
                self.trace.emit(n, "lock_released", resource=res)
