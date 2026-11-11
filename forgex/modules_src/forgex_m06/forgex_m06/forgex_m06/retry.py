"""Retry & recovery intelligence: static policy analysis + deterministic retry-storm simulation."""
from __future__ import annotations
import random
from dataclasses import dataclass, asdict
from decimal import Decimal


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base: int = 1
    factor: int = 2
    cap: int = 60
    jitter: str = "none"   # none | full | equal
    timeout: int = 3
    dead_letter: bool = True

    def delay(self, i: int, rng: random.Random) -> int:
        d = min(self.cap, self.base * (self.factor ** i))
        if self.jitter == "full":
            return rng.randint(0, d)
        if self.jitter == "equal":
            return d // 2 + rng.randint(0, d - d // 2)
        return d

    def worst_case_wait(self) -> int:
        return sum(min(self.cap, self.base * self.factor ** i) for i in range(max(0, self.max_attempts - 1)))

    @classmethod
    def from_dict(cls, d):
        p = cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})
        if not (1 <= p.max_attempts <= 100 and 0 <= p.base <= 3600 and 1 <= p.factor <= 10 and 0 <= p.cap <= 86400
                and 1 <= p.timeout <= 3600 and p.jitter in ("none", "full", "equal")):
            raise ValueError("retry policy out of allowed range")
        return p


SEMANTICS = {
    "create_payment": {"mutating": True, "needs_key": True},
    "capture": {"mutating": True, "needs_key": True},
    "refund": {"mutating": True, "needs_key": True},
    "webhook_delivery": {"mutating": True, "needs_key": False},
    "read": {"mutating": False, "needs_key": False},
}


def analyze_policy(policy: dict, semantics: dict | None = None) -> dict:
    p = RetryPolicy.from_dict(policy)
    s = {"operation": "create_payment", "has_idempotency_key": False, "deadline": 120, **(semantics or {})}
    op = SEMANTICS.get(s["operation"])
    if op is None:
        raise ValueError(f"operation must be one of {sorted(SEMANTICS)}")
    issues = []

    def add(code, sev, msg):
        issues.append({"code": code, "severity": sev, "message": msg})

    if p.max_attempts > 6:
        add("RETRY_LIMIT_TOO_HIGH", "medium", f"{p.max_attempts} attempts multiplies load by up to {p.max_attempts}x during an outage.")
    if p.factor <= 1 or p.base == 0:
        add("NO_BACKOFF", "high", "Constant/zero delay between attempts creates a retry storm under failure.")
    if p.jitter == "none" and p.max_attempts > 1:
        add("NO_JITTER", "medium", "Without jitter, clients retry in lock-step (thundering herd).")
    if p.cap < p.base * p.factor and p.factor > 1:
        add("CAP_BELOW_BACKOFF", "low", "Delay cap is below the first backoff step.")
    if p.worst_case_wait() + p.timeout * p.max_attempts > int(s["deadline"]):
        add("EXCEEDS_DEADLINE", "medium", f"Worst-case retry window {p.worst_case_wait() + p.timeout * p.max_attempts} exceeds deadline {s['deadline']}.")
    if op["mutating"] and op["needs_key"] and not s["has_idempotency_key"] and p.max_attempts > 1:
        add("DUPLICATE_EXECUTION_RISK", "critical", "Mutating operation retried with no idempotency key: a timeout after commit will re-execute it.")
    if op["mutating"] and not p.dead_letter:
        add("NO_DEAD_LETTER", "high", "Exhausted retries are dropped instead of parked for review/recovery.")
    rec = recommend(s["operation"], s["has_idempotency_key"])
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda i: order[i["severity"]])
    return {"policy": asdict(p), "semantics": s, "worst_case_wait": p.worst_case_wait(), "issues": issues,
            "safe": not any(i["severity"] in ("critical", "high") for i in issues), "recommendation": rec,
            "simulated": True}


def recommend(operation: str, has_key: bool) -> dict:
    if operation == "read":
        return {"policy": asdict(RetryPolicy(5, 1, 2, 30, "full", 3, False)), "notes": ["Reads are safe to retry; use exponential backoff with full jitter."]}
    if operation == "webhook_delivery":
        return {"policy": asdict(RetryPolicy(8, 5, 3, 3600, "equal", 10, True)),
                "notes": ["At-least-once delivery: the RECEIVER must dedupe by event_id.", "Park exhausted deliveries in a dead-letter queue and alert."]}
    notes = ["Retry only timeouts, 5xx and 409-in-progress; never retry other 4xx.", "Reuse the SAME idempotency key on every attempt.",
             "Bound attempts and total deadline; park exhausted requests in a dead-letter queue with the key + payload fingerprint.",
             "Before giving up, query the operation status by key/order reference instead of re-sending blindly."]
    if not has_key:
        notes.insert(0, "Do NOT auto-retry this mutation until an idempotency key is added; reconcile by status lookup instead.")
        return {"policy": asdict(RetryPolicy(1, 0, 1, 0, "none", 5, True)), "notes": notes}
    return {"policy": asdict(RetryPolicy(4, 1, 2, 30, "full", 5, True)), "notes": notes}


def simulate_storm(policy: RetryPolicy, clients=200, outage=(0, 20), capacity=20, horizon=200, seed=0,
                   server_idempotent=False, spread=5, shed_expired=False) -> dict:
    rng = random.Random(seed)
    cl = [{"id": i, "attempt": 0, "next": rng.randint(0, spread - 1), "wait": None, "done": False, "dead": False,
           "effects": 0} for i in range(clients)]
    queue, load, qlen, dlq, zombies, total, shed = [], [], [], [], 0, 0, 0
    for tick in range(horizon):
        sent = 0
        for c in cl:
            if c["done"] or c["dead"]:
                continue
            if c["wait"] is not None and tick > c["wait"]:
                c["wait"] = None
                if c["attempt"] >= policy.max_attempts:
                    c["dead"] = True
                    if policy.dead_letter:
                        dlq.append(c["id"])
                else:
                    c["next"] = tick + policy.delay(c["attempt"] - 1, rng)
            if c["wait"] is None and c["next"] is not None and tick >= c["next"] and not c["dead"]:
                c["attempt"] += 1
                c["wait"], c["next"] = tick + policy.timeout, None
                queue.append((c["id"], tick + policy.timeout))
                sent += 1
        total += sent
        if not (outage[0] <= tick < outage[1]):
            done_n = 0
            while queue and done_n < capacity:
                cid, deadline = queue.pop(0)
                c = cl[cid]
                if shed_expired and deadline < tick:
                    shed += 1  # deadline propagation: expired work is dropped, not executed
                    continue
                done_n += 1
                if not (server_idempotent and c["effects"] > 0):
                    c["effects"] += 1
                if deadline >= tick and not c["done"] and not c["dead"]:
                    c["done"], c["wait"] = True, None
                else:
                    zombies += 1
        load.append(sent)
        qlen.append(len(queue))
        if all(c["done"] or c["dead"] for c in cl) and not queue:
            break
    ok = sum(c["done"] for c in cl)
    dead = [c for c in cl if c["dead"]]
    dup = sum(max(0, c["effects"] - 1) for c in cl)
    orphan_effects = sum(1 for c in dead if c["effects"] > 0)
    return {"clients": clients, "total_requests": total,
            "amplification": str((Decimal(total) / Decimal(clients)).quantize(Decimal("0.01"))),
            "peak_load_per_tick": max(load) if load else 0, "capacity_per_tick": capacity,
            "peak_queue": max(qlen) if qlen else 0, "succeeded": ok, "dead_lettered": len(dlq),
            "abandoned_without_dlq": 0 if policy.dead_letter else len(dead),
            "duplicate_effects": dup, "effects_for_abandoned_clients": orphan_effects, "late_processed_requests": zombies, "shed_requests": shed,
            "ticks_simulated": len(load), "load_series": load, "queue_series": qlen,
            "dead_letter_ids": dlq[:50], "simulated": True}
