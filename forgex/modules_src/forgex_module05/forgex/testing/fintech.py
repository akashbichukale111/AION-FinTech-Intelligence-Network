"""FinTech transaction testing. Deterministic, in-memory, synthetic data only. No network, no real money.
All amounts are Decimal; floats are rejected. `strict=False` builds a deliberately defective engine so the suite
itself can be proven to detect bugs."""
from __future__ import annotations
import hashlib, hmac, json, threading
from contextlib import nullcontext
from decimal import Decimal, ROUND_HALF_EVEN, InvalidOperation

CENT = Decimal("0.01")
T0 = 1_700_000_000

class PaymentError(Exception): pass
class IdempotencyConflict(PaymentError): pass
class DuplicatePayment(PaymentError): pass
class GatewayTimeout(PaymentError): pass
class InvalidAmount(PaymentError): pass
class RefundExceeded(PaymentError): pass
class SignatureInvalid(PaymentError): pass
class LedgerError(PaymentError): pass

def money(v) -> Decimal:
    if isinstance(v, (float, bool)): raise TypeError("float/bool is not allowed for money; use Decimal or str")
    try: d = v if isinstance(v, Decimal) else Decimal(v)
    except (InvalidOperation, TypeError, ValueError): raise InvalidAmount(f"not a decimal: {v!r}")
    if not d.is_finite(): raise InvalidAmount("non-finite amount")
    if d != d.quantize(CENT): raise InvalidAmount("more than 2 decimal places")
    return d

class Ledger:
    def __init__(self): self.entries, self.txns = [], {}
    def post(self, txn_id, lines):
        if txn_id in self.txns: raise LedgerError(f"duplicate txn {txn_id}")
        d = sum((money(l[1]) for l in lines), Decimal(0)); c = sum((money(l[2]) for l in lines), Decimal(0))
        if d != c or d <= 0: raise LedgerError(f"unbalanced txn {txn_id}: debit {d} credit {c}")
        self.txns[txn_id] = lines
        self.entries += [(txn_id, a, money(x), money(y)) for a, x, y in lines]
    def balance(self, account): return sum((e[2] - e[3] for e in self.entries if e[1] == account), Decimal(0))
    def balanced(self):
        return sum((e[2] for e in self.entries), Decimal(0)) == sum((e[3] for e in self.entries), Decimal(0)) and \
               all(sum((e[2] for e in self.entries if e[0] == t), Decimal(0)) == sum((e[3] for e in self.entries if e[0] == t), Decimal(0)) for t in self.txns)

class FakeGateway:
    """Idempotent by key. faults: scripted per-call, e.g. ['timeout_before', 'timeout_after']."""
    def __init__(self, faults=None):
        self.faults, self.calls, self.captures, self._l = list(faults or []), 0, {}, threading.Lock()
    def capture(self, key, order_id, amount):
        with self._l:
            self.calls += 1
            fault = self.faults.pop(0) if self.faults else None
            if fault == "timeout_before": raise GatewayTimeout("gateway timeout (request not processed)")
            cap = self.captures.get(key)
            if not cap or cap["voided"]:
                cap = self.captures[key] = {"id": f"cap_{len(self.captures) + 1:04d}", "order_id": order_id, "amount": amount, "voided": False}
            if fault == "timeout_after": raise GatewayTimeout("gateway timeout (request processed, response lost)")
            return cap
    def void(self, cap_id):
        for c in self.captures.values():
            if c["id"] == cap_id: c["voided"] = True
    def live_captures(self): return [c for c in self.captures.values() if not c["voided"]]

class PaymentEngine:
    def __init__(self, gateway=None, strict=True, secret=b"whsec_synthetic", clock=lambda: T0, fail_ledger_once=False):
        self.gateway, self.ledger, self.strict, self.secret, self.clock = gateway or FakeGateway(), Ledger(), strict, secret, clock
        self.lock, self.idem, self.payments, self.orders, self.refund_idem, self.events = threading.RLock(), {}, {}, {}, {}, set()
        self._fail_ledger_once = fail_ledger_once

    def charge(self, key, order_id, amount, currency="USD"):
        amount = money(amount)
        if amount <= 0: raise InvalidAmount("amount must be positive")
        fp = (order_id, str(amount), currency)
        with (self.lock if self.strict else nullcontext()):
            if self.strict:
                if key in self.idem:
                    if self.idem[key]["fp"] != fp: raise IdempotencyConflict("idempotency key reused with different parameters")
                    return self.idem[key]["payment"]
                if order_id in self.orders: raise DuplicatePayment(f"order {order_id} already paid")
            cap = self.gateway.capture(key if self.strict else f"{key}#{self.gateway.calls}", order_id, amount)
            pid = f"pay_{len(self.payments) + 1:04d}"
            try:
                if self._fail_ledger_once:
                    self._fail_ledger_once = False; raise LedgerError("injected ledger failure")
                self.ledger.post(f"txn_{pid}", [("gateway_clearing", amount, Decimal(0)), ("merchant_revenue", Decimal(0), amount)])
            except LedgerError:
                self.gateway.void(cap["id"]); raise
            p = {"id": pid, "order_id": order_id, "amount": amount, "currency": currency, "refunded": Decimal(0), "status": "captured", "capture_id": cap["id"]}
            self.payments[pid] = p; self.orders[order_id] = pid; self.idem[key] = {"fp": fp, "payment": p}
            return p

    def refund(self, payment_id, key, amount):
        amount = money(amount)
        with (self.lock if self.strict else nullcontext()):
            p = self.payments[payment_id]
            if self.strict:
                if amount <= 0: raise InvalidAmount("refund must be positive")
                if key in self.refund_idem:
                    if self.refund_idem[key][0] != (payment_id, amount): raise IdempotencyConflict("refund key reused")
                    return self.refund_idem[key][1]
                if amount > p["amount"] - p["refunded"]: raise RefundExceeded(f"refund {amount} exceeds remaining {p['amount'] - p['refunded']}")
            n = len(self.refund_idem) + 1
            self.ledger.post(f"txn_ref_{n:04d}", [("merchant_revenue", amount, Decimal(0)), ("gateway_clearing", Decimal(0), amount)])
            p["refunded"] += amount
            r = {"id": f"ref_{n:04d}", "payment_id": payment_id, "amount": amount}
            self.refund_idem[key] = ((payment_id, amount), r)
            return r

    @staticmethod
    def sign(secret, ts, body): return hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()

    def handle_webhook(self, body: bytes, signature: str, ts: int, tolerance=300):
        if self.strict:
            if abs(self.clock() - ts) > tolerance: raise SignatureInvalid("timestamp outside tolerance")
            if not hmac.compare_digest(self.sign(self.secret, ts, body), signature or ""): raise SignatureInvalid("bad signature")
        evt = json.loads(body, parse_float=Decimal)
        with (self.lock if self.strict else nullcontext()):
            if self.strict and evt["id"] in self.events: return "duplicate"
            self.events.add(evt["id"])
            if evt["type"] == "payment.confirmed":
                amt = money(evt["amount"])
                self.ledger.post(f"txn_evt_{evt['id']}_{len(self.ledger.txns)}", [("bank_settlement", amt, Decimal(0)), ("gateway_clearing", Decimal(0), amt)])
            return "processed"

def settle(amounts, fee_rate=Decimal("0.029"), fixed=Decimal("0.30")):
    rows = []
    for pid, gross in amounts:
        gross = money(gross); fee = (gross * fee_rate + fixed).quantize(CENT, ROUND_HALF_EVEN)
        rows.append({"payment_id": pid, "gross": gross, "fee": fee, "net": gross - fee})
    return rows

def reconcile(ledger_amounts: dict, processor_rows: list):
    seen, dup, mismatched, extra = set(), [], [], []
    for r in processor_rows:
        pid = r["payment_id"]
        if pid in seen: dup.append(pid); continue
        seen.add(pid)
        if pid not in ledger_amounts: extra.append(pid)
        elif money(r["gross"]) != ledger_amounts[pid]: mismatched.append({"payment_id": pid, "ledger": ledger_amounts[pid], "processor": money(r["gross"])})
    return {"missing_in_processor": sorted(set(ledger_amounts) - seen), "missing_in_ledger": sorted(extra), "amount_mismatch": mismatched, "duplicate_rows": dup}

# ---------------------------------------------------------------- scenarios
class _S:
    def __init__(self): self.checks = []
    def ok(self, desc, cond, detail=""): self.checks.append({"check": desc, "ok": bool(cond), "detail": str(detail)})
    def raises(self, desc, exc, fn):
        try: fn(); self.ok(desc, False, f"expected {exc.__name__}, nothing raised")
        except exc as e: self.ok(desc, True, f"{type(e).__name__}: {e}")
        except Exception as e: self.ok(desc, False, f"expected {exc.__name__}, got {type(e).__name__}: {e}")

def _sc1(strict):
    s, e = _S(), PaymentEngine(strict=strict)
    e.charge("k1", "order-1", "100.00")
    try: e.charge("k2", "order-1", "100.00"); dup = False
    except DuplicatePayment: dup = True
    s.ok("second payment for the same order with a new key is rejected", dup)
    s.ok("exactly one gateway capture and one ledger txn", len(e.gateway.live_captures()) == 1 and len(e.ledger.txns) == 1, f"captures={len(e.gateway.live_captures())} txns={len(e.ledger.txns)}")
    s.ok("revenue equals one payment", e.ledger.balance("merchant_revenue") == Decimal("-100.00"), e.ledger.balance("merchant_revenue"))
    return s
def _sc2(strict):
    s, e = _S(), PaymentEngine(strict=strict)
    a = e.charge("key-A", "o-1", "25.50"); b = e.charge("key-A", "o-1", "25.50")
    s.ok("same key + same body returns the same payment", a["id"] == b["id"])
    s.ok("replay creates no second ledger txn", len(e.ledger.txns) == 1)
    s.raises("same key + different amount is a conflict", IdempotencyConflict, lambda: e.charge("key-A", "o-1", "25.51"))
    s.raises("same key + different order is a conflict", IdempotencyConflict, lambda: e.charge("key-A", "o-2", "25.50"))
    return s
def _sc3(strict):
    s, e = _S(), PaymentEngine(strict=strict)
    res, errs = [], []
    def w():
        try: res.append(e.charge("same-key", "o-conc", "10.00")["id"])
        except PaymentError as x: errs.append(x)
    ts = [threading.Thread(target=w) for _ in range(24)]
    [t.start() for t in ts]; [t.join() for t in ts]
    s.ok("24 concurrent identical requests yield one payment id", len(set(res)) == 1 and len(res) + len(errs) == 24, f"ids={sorted(set(res))}")
    s.ok("exactly one live capture", len(e.gateway.live_captures()) == 1, len(e.gateway.live_captures()))
    s.ok("ledger holds one txn and is balanced", len(e.ledger.txns) == 1 and e.ledger.balanced())
    e2 = PaymentEngine(strict=strict)
    ts = [threading.Thread(target=lambda i=i: e2.charge(f"k{i}", f"o{i}", "1.10")) for i in range(30)]
    [t.start() for t in ts]; [t.join() for t in ts]
    s.ok("30 distinct concurrent payments total exactly 33.00", e2.ledger.balance("gateway_clearing") == Decimal("33.00"), e2.ledger.balance("gateway_clearing"))
    return s
def _sc4(strict):
    s, e = _S(), PaymentEngine(strict=strict, fail_ledger_once=True)
    s.raises("ledger failure after capture surfaces as an error", LedgerError, lambda: e.charge("k", "o-1", "40.00"))
    s.ok("captured funds were voided (compensation)", len(e.gateway.live_captures()) == 0, len(e.gateway.live_captures()))
    s.ok("no ledger entries or payments remain", not e.ledger.entries and not e.payments)
    p = e.charge("k", "o-1", "40.00")
    s.ok("retry after failure succeeds with a fresh capture", p["status"] == "captured" and len(e.gateway.live_captures()) == 1)
    return s
def _sc5(strict):
    s = _S()
    e = PaymentEngine(gateway=FakeGateway(["timeout_before"]), strict=strict)
    s.raises("timeout before processing raises GatewayTimeout", GatewayTimeout, lambda: e.charge("k1", "o1", "15.00"))
    s.ok("nothing recorded after timeout-before", not e.payments and not e.ledger.entries)
    p = e.charge("k1", "o1", "15.00"); s.ok("retry succeeds once", p["status"] == "captured" and len(e.ledger.txns) == 1)
    e2 = PaymentEngine(gateway=FakeGateway(["timeout_after"]), strict=strict)
    s.raises("timeout after processing raises GatewayTimeout", GatewayTimeout, lambda: e2.charge("k2", "o2", "15.00"))
    e2.charge("k2", "o2", "15.00")
    s.ok("retry after ambiguous timeout does not double-capture", len(e2.gateway.live_captures()) == 1, len(e2.gateway.live_captures()))
    s.ok("ledger charged exactly once", e2.ledger.balance("gateway_clearing") == Decimal("15.00"), e2.ledger.balance("gateway_clearing"))
    return s
def _body(eid, pid="pay_0001", amt="10.00"): return json.dumps({"id": eid, "type": "payment.confirmed", "payment_id": pid, "amount": amt}).encode()
def _sc6(strict):
    s, e = _S(), PaymentEngine(strict=strict); b = _body("evt_1"); sig = PaymentEngine.sign(e.secret, T0, b)
    r1, r2, r3 = e.handle_webhook(b, sig, T0), e.handle_webhook(b, sig, T0), e.handle_webhook(b, sig, T0)
    s.ok("first delivery processed, later deliveries reported duplicate", (r1, r2, r3) == ("processed", "duplicate", "duplicate"), (r1, r2, r3))
    s.ok("bank settlement credited once", e.ledger.balance("bank_settlement") == Decimal("10.00"), e.ledger.balance("bank_settlement"))
    return s
def _sc7(strict):
    s, e = _S(), PaymentEngine(strict=strict); b = _body("evt_2"); good = PaymentEngine.sign(e.secret, T0, b)
    s.raises("tampered body rejected", SignatureInvalid, lambda: e.handle_webhook(_body("evt_2", amt="10000.00"), good, T0))
    s.raises("wrong secret rejected", SignatureInvalid, lambda: e.handle_webhook(b, PaymentEngine.sign(b"other", T0, b), T0))
    s.raises("missing signature rejected", SignatureInvalid, lambda: e.handle_webhook(b, "", T0))
    s.raises("stale timestamp (replay) rejected", SignatureInvalid, lambda: e.handle_webhook(b, PaymentEngine.sign(e.secret, T0 - 3600, b), T0 - 3600))
    s.ok("valid signature accepted", e.handle_webhook(b, good, T0) == "processed")
    return s
def _sc8(strict):
    s, e = _S(), PaymentEngine(strict=strict); p = e.charge("k", "o", "100.00")
    s.raises("zero refund rejected", InvalidAmount, lambda: e.refund(p["id"], "r0", "0.00"))
    s.raises("negative refund rejected", InvalidAmount, lambda: e.refund(p["id"], "r1", "-5.00"))
    s.raises("sub-cent precision rejected", InvalidAmount, lambda: e.refund(p["id"], "r2", "0.001"))
    s.raises("float amount rejected", TypeError, lambda: e.refund(p["id"], "r3", 10.5))
    e.refund(p["id"], "r4", "60.00")
    s.raises("cumulative over-refund rejected", RefundExceeded, lambda: e.refund(p["id"], "r5", "40.01"))
    e.refund(p["id"], "r6", "40.00")
    s.ok("exact remaining amount refunds fully", p["refunded"] == Decimal("100.00"), p["refunded"])
    s.ok("revenue nets to zero after full refund", e.ledger.balance("merchant_revenue") == Decimal("0.00"), e.ledger.balance("merchant_revenue"))
    return s
def _sc9(strict):
    s, e = _S(), PaymentEngine(strict=strict)
    for i, amt in enumerate(["0.01", "19.99", "1234567.89", "0.10"]):
        p = e.charge(f"k{i}", f"o{i}", amt)
    e.refund(p["id"], "rf", "0.05")
    s.ok("every transaction balances (debits == credits)", e.ledger.balanced())
    s.ok("sum of balances across accounts is zero", sum((e.ledger.balance(a) for a in {x[1] for x in e.ledger.entries}), Decimal(0)) == 0)
    s.ok("no float or non-Decimal values in the ledger", all(isinstance(x[2], Decimal) and isinstance(x[3], Decimal) for x in e.ledger.entries))
    s.raises("unbalanced posting refused", LedgerError, lambda: e.ledger.post("bad", [("a", Decimal("1.00"), Decimal(0)), ("b", Decimal(0), Decimal("0.99"))]))
    s.raises("duplicate txn id refused", LedgerError, lambda: e.ledger.post("txn_pay_0001", [("a", Decimal("1.00"), Decimal(0)), ("b", Decimal(0), Decimal("1.00"))]))
    return s
def _sc10(strict):
    s = _S()
    rows = settle([("p1", "0.20"), ("p2", "0.60"), ("p3", "99.99")], fee_rate=Decimal("0.025"), fixed=Decimal("0"))
    s.ok("half-even rounding at exact ties (0.005 -> 0.00, 0.015 -> 0.02)", [r["fee"] for r in rows[:2]] == [Decimal("0.00"), Decimal("0.02")], [str(r["fee"]) for r in rows])
    s.ok("gross == net + fee for every row", all(r["gross"] == r["net"] + r["fee"] for r in rows))
    ledger = {"p1": Decimal("10.00"), "p2": Decimal("20.00"), "p3": Decimal("30.00"), "p4": Decimal("5.00")}
    rec = reconcile(ledger, [{"payment_id": "p1", "gross": "10.00"}, {"payment_id": "p2", "gross": "19.99"}, {"payment_id": "p3", "gross": "30.00"},
                             {"payment_id": "p3", "gross": "30.00"}, {"payment_id": "p9", "gross": "1.00"}])
    s.ok("detects amount mismatch", [m["payment_id"] for m in rec["amount_mismatch"]] == ["p2"], rec["amount_mismatch"])
    s.ok("detects payment missing from processor report", rec["missing_in_processor"] == ["p4"], rec["missing_in_processor"])
    s.ok("detects processor row unknown to the ledger", rec["missing_in_ledger"] == ["p9"], rec["missing_in_ledger"])
    s.ok("detects duplicate processor rows", rec["duplicate_rows"] == ["p3"], rec["duplicate_rows"])
    return s

SCENARIOS = [("duplicate_payment_prevention", "Duplicate payment prevention", _sc1), ("idempotency_key_consistency", "Idempotency key consistency", _sc2),
             ("concurrent_transactions", "Concurrent transaction handling", _sc3), ("partial_payment_failure", "Partial payment failure", _sc4),
             ("timeout_and_retry", "Payment timeout and retry", _sc5), ("webhook_duplicate_delivery", "Webhook duplicate delivery", _sc6),
             ("webhook_signature_validation", "Webhook signature validation", _sc7), ("refund_amount_boundaries", "Refund amount boundaries", _sc8),
             ("ledger_consistency", "Ledger debit-credit consistency", _sc9), ("settlement_reconciliation", "Settlement and reconciliation edge cases", _sc10)]

def run_simulations(mode="reference", only=None):
    strict = mode != "defective"
    out = []
    for sid, name, fn in SCENARIOS:
        if only and sid not in only: continue
        try:
            sc = fn(strict); status = "passed" if all(c["ok"] for c in sc.checks) else "failed"; checks = sc.checks; err = None
        except Exception as ex:   # a crash is a failure, never a pass
            status, checks, err = "failed", [], f"{type(ex).__name__}: {ex}"
        out.append({"id": sid, "name": name, "status": status, "checks": checks, "error": err})
    return {"mode": mode, "synthetic_data_only": True, "currency_type": "decimal.Decimal", "scenarios": out,
            "summary": {"total": len(out), "passed": sum(s["status"] == "passed" for s in out), "failed": sum(s["status"] != "passed" for s in out)}}
