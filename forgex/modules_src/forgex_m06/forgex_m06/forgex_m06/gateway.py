"""SIMULATED payment gateway model with switchable implementation defects.
This is a model under test - it never talks to any network, processor or real account."""
from __future__ import annotations
import random
from collections import Counter
from dataclasses import dataclass, asdict, fields
from .money import D, q, ZERO, FEE_RATE, allocate, digest, MoneyError
from .ledger import Ledger
from .sched import Trace, Crash, ResponseLost

LEASE_TICKS = 30


@dataclass
class Impl:
    idempotency: str = "key_payload"      # none | key | key_payload
    ttl: int | None = None                # idempotency record TTL in ticks (None = never expires)
    atomic_reserve: bool = True           # reserve key atomically before executing
    in_progress_policy: str = "resume"    # resume | reject  (what to do with a crashed in-progress record)
    atomic_ledger: bool = True            # whole request commits or rolls back together
    state_guard: bool = True              # enforce payment state machine (capture/void/reverse)
    refund_guard: bool = True             # refund idempotency key + cumulative cap
    webhook_dedup: bool = True            # dedupe webhooks by event_id
    reversal_correct: bool = True         # reversal journal is an exact mirror
    rounding: str = "allocate"            # allocate | round_each | none
    locking: str = "lock"                 # none | lock | optimistic
    deadlock_ordering: bool = True        # acquire locks in a global order
    settlement_dedup: bool = True         # settlement importer ignores duplicate entries
    batch_atomic: bool = True             # settlement batch import is all-or-nothing

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None):
        d = d or {}
        names = {f.name for f in fields(cls)}
        bad = set(d) - names
        if bad:
            raise ValueError(f"unknown implementation flag(s): {sorted(bad)}")
        impl = cls(**d)
        enums = {"idempotency": {"none", "key", "key_payload"}, "in_progress_policy": {"resume", "reject"},
                 "rounding": {"allocate", "round_each", "none"}, "locking": {"none", "lock", "optimistic"}}
        for k, ok in enums.items():
            if getattr(impl, k) not in ok:
                raise ValueError(f"{k} must be one of {sorted(ok)}")
        if impl.ttl is not None and (not isinstance(impl.ttl, int) or impl.ttl < 0):
            raise ValueError("ttl must be a non-negative integer or null")
        return impl


HARDENED = Impl()
NAIVE = Impl(idempotency="none", atomic_reserve=False, in_progress_policy="reject", atomic_ledger=False,
             state_guard=False, refund_guard=False, webhook_dedup=False, reversal_correct=False,
             rounding="round_each", locking="none", deadlock_ordering=False, settlement_dedup=False,
             batch_atomic=False)


def R(status, **kw):
    return {"status": status, **kw}


class Gateway:
    def __init__(self, impl: Impl, trace: Trace, seed: int = 0):
        self.impl, self.trace, self.rng = impl, trace, random.Random(seed)
        self.ledger = Ledger()
        self.payments: dict[str, dict] = {}
        self.idem: dict[str, dict] = {}
        self.refund_keys: dict[str, dict] = {}
        self.reversed: set[str] = set()
        self.webhooks_seen: set[str] = set()
        self.webhook_effects: list[dict] = []
        self.faults: Counter = Counter()
        self.now = 0
        self.n = 0

    def fault(self, name, count=1):
        self.faults[name] += count

    def take(self, name):
        if self.faults[name] > 0:
            self.faults[name] -= 1
            return True
        return False

    def next_pid(self):
        self.n += 1
        return f"PAY-{self.n:04d}"

    def advance(self, ticks):
        self.now += ticks
        self.trace.emit("clock", "advance", ticks=ticks, now=self.now)

    def snapshot_payments(self):
        return {k: dict(v) for k, v in sorted(self.payments.items())}


def post_capture(gw: Gateway, p: dict, amount):
    fee = q(amount * FEE_RATE)
    net = amount - fee
    j = gw.ledger.post(p["payment_id"], "capture", [("customer_funds", amount, ZERO), ("merchant_payable", ZERO, net),
                                                    ("fee_revenue", ZERO, fee)])
    p["journal"] = p.get("journal") or j["journal_id"]
    return j


# ---------------------------------------------------------------- create payment (idempotent entry point)
def create_payment(gw: Gateway, actor: str, req: dict):
    t, im = gw.trace, gw.impl
    key = req.get("key")
    fp = digest({k: v for k, v in req.items() if k != "key"}, 16)
    t.emit(actor, "request", key=key, amount=req.get("amount"), order_ref=req.get("order_ref"), fp=fp)
    use = im.idempotency != "none" and bool(key)
    if use:
        yield
        rec = gw.idem.get(key)
        if rec and im.ttl is not None and gw.now - rec["created"] > im.ttl:
            t.emit(actor, "idem_record_expired", key=key, age=gw.now - rec["created"], ttl=im.ttl)
            del gw.idem[key]
            rec = None
        if rec:
            if rec["fp"] != fp:
                if im.idempotency == "key_payload":
                    t.emit(actor, "idem_payload_mismatch_rejected", key=key)
                    return R(422, error="idempotency_key_reused_with_different_payload")
                t.emit(actor, "idem_payload_mismatch_ignored", key=key)
            if rec["state"] == "done":
                t.emit(actor, "idem_replay", key=key, payment_id=rec["response"].get("payment_id"))
                return dict(rec["response"], replayed=True)
            if gw.now <= rec["lease_until"]:
                t.emit(actor, "idem_in_progress_rejected", key=key)
                return R(409, error="request_in_progress")
            if im.in_progress_policy == "reject":
                t.emit(actor, "idem_stuck", key=key, note="stale in-progress record is never recovered")
                return R(409, error="request_in_progress_stuck")
            t.emit(actor, "idem_resume", key=key, payment_id=rec.get("payment_id"))
            rec["lease_until"] = gw.now + LEASE_TICKS
            return (yield from _finish(gw, actor, req, key, fp, rec.get("payment_id")))
        if im.atomic_reserve:
            gw.idem[key] = {"fp": fp, "state": "in_progress", "created": gw.now, "lease_until": gw.now + LEASE_TICKS,
                            "payment_id": None}
            t.emit(actor, "idem_reserved", key=key)
    yield
    return (yield from _finish(gw, actor, req, key, fp, None))


def _finish(gw, actor, req, key, fp, pid):
    t, im = gw.trace, gw.impl
    try:
        resp = yield from _execute(gw, actor, req, key, pid)
    except Crash:
        if im.atomic_ledger and key and im.idempotency != "none" and gw.idem.get(key, {}).get("state") == "in_progress":
            del gw.idem[key]
            t.emit(actor, "rollback", key=key, note="reservation rolled back with the transaction")
        raise
    if key and im.idempotency != "none":
        rec = gw.idem.setdefault(key, {"fp": fp, "created": gw.now, "lease_until": gw.now})
        rec.update(state="done", response=resp, payment_id=resp.get("payment_id"))
        t.emit(actor, "idem_stored", key=key, payment_id=resp.get("payment_id"))
    if gw.take("timeout_after_commit"):
        t.emit(actor, "response_lost", key=key, payment_id=resp.get("payment_id"))
        raise ResponseLost()
    return resp


def _execute(gw: Gateway, actor, req, key, pid=None):
    t, im = gw.trace, gw.impl
    atomic = im.atomic_ledger
    amt = D(req["amount"])
    if amt <= 0:
        raise MoneyError("amount must be positive")
    existing = gw.payments.get(pid) if pid else None
    pid = pid or gw.next_pid()
    base = existing or {"payment_id": pid, "state": "initiated", "amount": amt, "authorized": ZERO, "captured": ZERO,
                        "refunded": ZERO, "settled": ZERO, "merchant": req.get("merchant"),
                        "customer": req.get("customer"), "order_ref": req.get("order_ref"),
                        "currency": req.get("currency", "USD"), "key": key, "journal": None,
                        "auth_only": bool(req.get("auth_only")), "synthetic": True}
    p = dict(base) if atomic else base
    if not atomic:
        gw.payments[pid] = p
        if key and key in gw.idem:
            gw.idem[key]["payment_id"] = pid
    t.emit(actor, "execute_start", payment_id=pid, key=key)
    if p["state"] == "initiated":
        if req.get("decline"):
            p["state"], p["failure"] = "failed", "issuer_declined"
            t.emit(actor, "declined", payment_id=pid)
        else:
            p["state"], p["authorized"] = "authorized", amt
            t.emit(actor, "authorized", payment_id=pid, amount=amt)
            yield
            if gw.take("crash_after_authorize"):
                t.emit(actor, "crash", point="after_authorize", payment_id=pid)
                raise Crash("after_authorize")
    if p["state"] == "authorized" and not p["auth_only"]:
        p["state"], p["captured"] = "captured", p["authorized"]
        t.emit(actor, "captured", payment_id=pid, amount=p["captured"])
        if gw.take("crash_after_capture"):
            t.emit(actor, "crash", point="after_capture", payment_id=pid)
            raise Crash("after_capture")
    if p["state"] == "captured" and not p["journal"]:
        j = post_capture(gw, p, p["captured"])
        t.emit(actor, "ledger_posted", payment_id=pid, journal_id=j["journal_id"])
    if atomic:
        gw.payments[pid] = p
    t.emit(actor, "execute_done", payment_id=pid, state=p["state"])
    return R(402 if p["state"] == "failed" else 200, payment_id=pid, state=p["state"], amount=amt)


# ---------------------------------------------------------------- state transitions
def _plock(gw, pid):
    return gw.impl.locking == "lock"


def capture(gw: Gateway, actor, pid, amount=None):
    t, im = gw.trace, gw.impl
    p = gw.payments.get(pid)
    if not p:
        return R(404, error="unknown payment")
    if _plock(gw, pid):
        yield ("lock", f"pay:{pid}")
    st = p["state"]
    amt = D(amount) if amount is not None else p["authorized"]
    t.emit(actor, "capture_request", payment_id=pid, state_seen=st, amount=amt)
    yield
    if im.state_guard and st != "authorized":
        t.emit(actor, "capture_rejected", payment_id=pid, state=st)
        res = R(409, error=f"capture not allowed in state {st}", state=st)
    else:
        p["state"] = "captured"
        p["captured"] += amt
        j = post_capture(gw, p, amt)
        t.emit(actor, "captured", payment_id=pid, amount=amt, total_captured=p["captured"], journal_id=j["journal_id"])
        res = R(200, payment_id=pid, state="captured", captured=p["captured"])
    if _plock(gw, pid):
        yield ("unlock", f"pay:{pid}")
    return res


def void(gw: Gateway, actor, pid):
    t, im = gw.trace, gw.impl
    p = gw.payments.get(pid)
    if not p:
        return R(404, error="unknown payment")
    if _plock(gw, pid):
        yield ("lock", f"pay:{pid}")
    st = p["state"]
    t.emit(actor, "void_request", payment_id=pid, state_seen=st)
    yield
    if im.state_guard and st != "authorized":
        t.emit(actor, "void_rejected", payment_id=pid, state=st)
        res = R(409, error=f"void not allowed in state {st}", state=st)
    else:
        p["state"] = "voided"
        t.emit(actor, "voided", payment_id=pid)
        res = R(200, payment_id=pid, state="voided")
    if _plock(gw, pid):
        yield ("unlock", f"pay:{pid}")
    return res


def refund(gw: Gateway, actor, pid, amount, refund_key):
    t, im = gw.trace, gw.impl
    p = gw.payments.get(pid)
    if not p:
        return R(404, error="unknown payment")
    amt = D(amount)
    if _plock(gw, pid):
        yield ("lock", f"pay:{pid}")
    t.emit(actor, "refund_request", payment_id=pid, refund_key=refund_key, amount=amt)
    yield
    res = None
    if im.refund_guard:
        if refund_key in gw.refund_keys:
            t.emit(actor, "refund_replay", refund_key=refund_key)
            res = dict(gw.refund_keys[refund_key], replayed=True)
        elif p["state"] not in ("captured", "partially_refunded", "refunded"):
            res = R(409, error=f"refund not allowed in state {p['state']}")
        elif p["refunded"] + amt > p["captured"]:
            t.emit(actor, "refund_rejected_over_cap", payment_id=pid, refunded=p["refunded"], amount=amt,
                   captured=p["captured"])
            res = R(422, error="refund exceeds captured amount")
    if res is None:
        yield
        fee = q(amt * FEE_RATE)
        j = gw.ledger.post(f"{pid}:refund:{refund_key}", "refund",
                           [("merchant_payable", amt - fee, ZERO), ("fee_revenue", fee, ZERO), ("customer_funds", ZERO, amt)])
        p["refunded"] += amt
        p["state"] = "refunded" if p["refunded"] >= p["captured"] else "partially_refunded"
        res = R(200, payment_id=pid, refunded=p["refunded"], state=p["state"], journal_id=j["journal_id"])
        gw.refund_keys[refund_key] = res
        t.emit(actor, "refunded", payment_id=pid, refund_key=refund_key, amount=amt, total_refunded=p["refunded"],
               journal_id=j["journal_id"])
    if _plock(gw, pid):
        yield ("unlock", f"pay:{pid}")
    return res


def reverse_capture(gw: Gateway, actor, pid):
    t, im = gw.trace, gw.impl
    p = gw.payments.get(pid)
    jid = p and p.get("journal")
    if not jid:
        return R(409, error="nothing to reverse")
    yield
    if im.state_guard and jid in gw.reversed:
        t.emit(actor, "reversal_rejected", payment_id=pid, journal_id=jid)
        return R(409, error="already reversed")
    orig = next(j for j in gw.ledger.journals if j["journal_id"] == jid)
    lines = [(l["account"], l["credit"], l["debit"]) for l in orig["lines"]]
    if not im.reversal_correct:
        lines = lines[:-1]  # defect: fee leg dropped from the mirror entry
    j = gw.ledger.post(pid, "reversal", lines, reverses=jid)
    gw.reversed.add(jid)
    p["state"] = "reversed"
    t.emit(actor, "reversed", payment_id=pid, journal_id=j["journal_id"], reverses=jid)
    return R(200, payment_id=pid, state="reversed", journal_id=j["journal_id"])


def deliver_webhook(gw: Gateway, actor, ev: dict):
    t, im = gw.trace, gw.impl
    eid, pid = ev["event_id"], ev["payment_id"]
    t.emit(actor, "webhook_received", event_id=eid, type=ev.get("type"), payment_id=pid)
    yield
    if im.webhook_dedup:
        if eid in gw.webhooks_seen:
            t.emit(actor, "webhook_duplicate_ignored", event_id=eid)
            return R(200, duplicate=True)
        gw.webhooks_seen.add(eid)
    p = gw.payments.get(pid)
    if not p or p["state"] not in ("captured", "partially_refunded", "refunded"):
        return R(409, error="payment not settleable")
    amt = D(ev["amount"])
    j = gw.ledger.post(f"{pid}:settlement", "settlement",
                       [("settlement_receivable", amt, ZERO), ("customer_funds", ZERO, amt)])
    p["settled"] += amt
    gw.webhook_effects.append({"event_id": eid, "payment_id": pid, "amount": amt, "journal_id": j["journal_id"]})
    t.emit(actor, "webhook_applied", event_id=eid, payment_id=pid, amount=amt, journal_id=j["journal_id"])
    return R(200, applied=True)


def split_payment(gw: Gateway, actor, pid, parties: int):
    """Marketplace split of a captured payment across N merchants (rounding model under test)."""
    p = gw.payments[pid]
    amt = p["captured"]
    mode = gw.impl.rounding
    if mode == "allocate":
        shares = allocate(amt, parties)
    elif mode == "round_each":
        shares = [q(amt / parties)] * parties
    else:
        shares = [amt / parties] * parties  # unquantized Decimal division
    j = gw.ledger.post(f"{pid}:split", "split", [("customer_funds_split", amt, ZERO)] +
                       [(f"merchant_payable:{i + 1}", ZERO, s) for i, s in enumerate(shares)])
    gw.trace.emit(actor, "split_posted", payment_id=pid, shares=shares, journal_id=j["journal_id"], rounding=mode)
    return shares
