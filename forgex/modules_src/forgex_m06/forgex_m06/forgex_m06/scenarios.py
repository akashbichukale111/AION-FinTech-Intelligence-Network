"""Scenario catalogue, config validation, deterministic execution and findings analysis."""
from __future__ import annotations
import re
from decimal import Decimal
from . import gateway as G
from . import settlement as S
from .detectors import detect
from .findings import Collector, CAT, LEDGER_MAP
from .gateway import Gateway, Impl, HARDENED, NAIVE, create_payment, capture, void, refund, reverse_capture, deliver_webhook, split_payment, LEASE_TICKS
from .ledger import validate_ledger
from .money import D, q, ZERO, MoneyError, contains_pan, digest, jsonable, scale
from .retry import RetryPolicy, simulate_storm, analyze_policy
from .sched import Scheduler, Trace, drive, Crash, ResponseLost

SPECS: dict[str, dict] = {}
FORBIDDEN_KEYS = re.compile(r"(live|live_mode|api_?key|secret|token|endpoint|url|host|account_number|iban|pan|card|card_number)", re.I)
TOP_KEYS = {"name", "type", "seed", "profile", "implementation", "params", "schedule", "scenario_id", "repo_path", "description", "abort_on_deadlock"}
MAX_AMOUNT = Decimal("1000000.00")


def spec(type_, title, category, desc, params, defect=None, hardened_params=None):
    def deco(fn):
        SPECS[type_] = {"type": type_, "title": title, "category": category, "description": desc, "params": params,
                        "defect": defect or {}, "hardened_params": hardened_params or {}, "fn": fn}
        return fn
    return deco


# ---------------------------------------------------------------- validation
def _coerce(name, sch, v):
    kind = sch[0]
    if kind == "int":
        if isinstance(v, bool) or not isinstance(v, int) or not (sch[2] <= v <= sch[3]):
            raise ValueError(f"param {name} must be an integer in [{sch[2]}, {sch[3]}]")
        return v
    if kind == "dec":
        d = D(v)
        if not (0 <= d <= MAX_AMOUNT) or scale(d) > 2:
            raise ValueError(f"param {name} must be a decimal amount 0..{MAX_AMOUNT} with at most 2 decimals")
        return str(q(d))
    if kind == "bool":
        if not isinstance(v, bool):
            raise ValueError(f"param {name} must be boolean")
        return v
    if kind == "enum":
        if v not in sch[2]:
            raise ValueError(f"param {name} must be one of {sch[2]}")
        return v
    raise ValueError("bad schema")


def public_specs():
    return [{"type": s["type"], "title": s["title"], "category": s["category"], "description": s["description"],
             "defect_profile": s["defect"], "params": {k: {"kind": v[0], "default": v[1], "range": list(v[2:])} for k, v in s["params"].items()}}
            for s in SPECS.values()]


def normalize_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("config must be an object")
    bad = set(cfg) - TOP_KEYS
    if bad:
        raise ValueError(f"unknown config field(s): {sorted(bad)}")
    if contains_pan(cfg):
        raise ValueError("config contains what looks like a real card number; only synthetic data is allowed")

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if path and FORBIDDEN_KEYS.fullmatch(str(k)) and path.split(".")[0] in ("params", "implementation"):
                    raise ValueError(f"field '{k}' is not allowed: the lab never connects to live systems or real accounts")
                walk(v, f"{path}.{k}" if path else k)
    walk(cfg)
    t = cfg.get("type")
    if t not in SPECS:
        raise ValueError(f"type must be one of {sorted(SPECS)}")
    seed = cfg.get("seed", 1)
    if isinstance(seed, bool) or not isinstance(seed, int) or not (0 <= seed < 2 ** 31):
        raise ValueError("seed must be an integer 0..2^31-1")
    prof = cfg.get("profile", "scenario_default")
    if prof not in ("scenario_default", "hardened", "naive", "custom"):
        raise ValueError("profile must be scenario_default|hardened|naive|custom")
    sp = SPECS[t]
    given = cfg.get("params") or {}
    unknown = set(given) - set(sp["params"])
    if unknown:
        raise ValueError(f"unknown param(s) for {t}: {sorted(unknown)}")
    params = {k: v[1] for k, v in sp["params"].items()}
    if prof == "hardened":
        params.update(sp["hardened_params"])
    params.update(given)
    params = {k: _coerce(k, sp["params"][k], v) for k, v in params.items()}
    sched = cfg.get("schedule")
    if sched is not None and (not isinstance(sched, list) or len(sched) > 2000 or not all(isinstance(x, str) and len(x) < 40 for x in sched)):
        raise ValueError("schedule must be a list of actor names")
    return {"name": str(cfg.get("name") or sp["title"])[:80], "type": t, "seed": seed, "profile": prof,
            "implementation": Impl.from_dict(cfg.get("implementation")).to_dict() if cfg.get("implementation") else {},
            "params": params, "schedule": sched, "scenario_id": cfg.get("scenario_id"), "repo_path": cfg.get("repo_path"),
            "abort_on_deadlock": bool(cfg.get("abort_on_deadlock", False)), "description": str(cfg.get("description") or "")[:300]}


def resolve_impl(cfg) -> Impl:
    sp = SPECS[cfg["type"]]
    base = {"hardened": HARDENED.to_dict(), "naive": NAIVE.to_dict(), "custom": HARDENED.to_dict()}.get(cfg["profile"]) or {**HARDENED.to_dict(), **sp["defect"]}
    return Impl.from_dict({**base, **cfg["implementation"]})


# ---------------------------------------------------------------- context
class Ctx:
    def __init__(self, cfg, impl, params):
        self.cfg, self.impl, self.p = cfg, impl, params
        self.trace = Trace()
        self.gw = Gateway(impl, self.trace, cfg["seed"])
        self.steps, self.exp, self.obs, self.schedule = [], [], {}, None
        self.extra_journals = None
        self.charts = {}

    def step(self, s):
        self.steps.append(s)

    def expect(self, eid, desc, expected, actual, ok, ftype, family=None, evidence=None, seqs=(), confidence=0.9):
        assert ftype in CAT, ftype
        self.exp.append({"id": eid, "description": desc, "expected": str(expected), "actual": str(actual), "ok": bool(ok),
                         "ftype": ftype, "family": family or ftype, "evidence": jsonable(evidence or {}),
                         "seqs": list(seqs), "confidence": confidence})

    def finish(self):
        self.payments = jsonable(self.gw.snapshot_payments())
        self.journals = self.extra_journals if self.extra_journals is not None else self.gw.ledger.snapshot()
        self.validation = validate_ledger(self.journals)
        self.hits = detect(self.gw, self.trace)


def req(p, key="IDEM-0001", order="ORD-1001", **kw):
    r = {"key": key, "merchant": "M-SYN-01", "customer": "C-SYN-0001", "order_ref": order, "amount": p.get("amount", "100.00"), "currency": "USD"}
    r.update(kw)
    return r


def captured_for(gw, order="ORD-1001"):
    return [p for p in gw.payments.values() if p["order_ref"] == order and p["captured"] > ZERO]


def try_drive(ctx, gen):
    try:
        return drive(gen), None
    except ResponseLost:
        return None, "timeout"
    except Crash as c:
        return None, f"crash:{c.args[0]}"
    except MoneyError as e:
        return {"status": 400, "error": str(e)}, None


def client_send(ctx, request, policy: RetryPolicy, new_key=False, actor="client"):
    gw, attempts, resp = ctx.gw, [], None
    for i in range(policy.max_attempts):
        r = dict(request)
        if new_key and i > 0:
            r["key"] = f"{request['key']}-retry{i}"
        ctx.step(f"attempt {i + 1}: POST /payments key={r['key']} amount={r['amount']} (t={gw.now})")
        resp, err = try_drive(ctx, create_payment(gw, actor, r))
        attempts.append({"attempt": i + 1, "key": r["key"], "status": resp["status"] if resp else None, "error": err})
        if resp and resp["status"] not in (409,) and resp["status"] < 500:
            break
        if i < policy.max_attempts - 1:
            d = policy.delay(i, gw.rng)
            gw.advance(d)
            ctx.step(f"client waits {d} ticks (backoff) then retries")
    return resp, attempts


def dup_ftype(impl, default):
    return "MISSING_IDEMPOTENCY_PROTECTION" if impl.idempotency == "none" else default


def single_order_expectation(ctx, eid, default_ftype, extra=None):
    ps = captured_for(ctx.gw)
    seqs = [s for p in ps for s in ctx.trace.refs(op="captured", payment_id=p["payment_id"])]
    ctx.expect(eid, "Exactly one payment is captured for the business order", "1 captured payment, 1 capture journal",
               f"{len(ps)} captured payment(s): {[p['payment_id'] for p in ps]}", len(ps) == 1,
               dup_ftype(ctx.impl, default_ftype), "duplicate_payment",
               {"payments": [p["payment_id"] for p in ps], "keys": sorted({str(p['key']) for p in ps}), **(extra or {})}, seqs)


# ---------------------------------------------------------------- scenario runners
AMT = ("dec", "100.00")


@spec("payment_lifecycle", "Payment lifecycle", "lifecycle", "Initiate -> authorize -> capture (or decline / auth only) with optional partial refund; baseline for ledger correctness.",
      {"amount": AMT, "outcome": ("enum", "captured", ["captured", "declined", "auth_only"]), "refund_amount": ("dec", "0.00")})
def run_lifecycle(ctx, p):
    ctx.step(f"POST /payments amount={p['amount']} outcome={p['outcome']}")
    resp, _ = try_drive(ctx, create_payment(ctx.gw, "client", req(p, decline=p["outcome"] == "declined", auth_only=p["outcome"] == "auth_only")))
    pid = resp.get("payment_id")
    if p["outcome"] == "captured" and D(p["refund_amount"]) > 0:
        ctx.step(f"POST /payments/{pid}/refunds amount={p['refund_amount']}")
        try_drive(ctx, refund(ctx.gw, "client", pid, p["refund_amount"], "RFD-0001"))
    want = {"captured": "captured", "declined": "failed", "auth_only": "authorized"}[p["outcome"]]
    if p["outcome"] == "captured" and D(p["refund_amount"]) > 0:
        want = "refunded" if D(p["refund_amount"]) >= D(p["amount"]) else "partially_refunded"
    st = ctx.gw.payments[pid]["state"]
    ctx.expect("lifecycle_state", "Payment ends in the expected lifecycle state", want, st, st == want, "PARTIAL_FAILURE_ORPHAN_STATE", "orphan_state", {"payment_id": pid})
    nj = len([j for j in ctx.gw.ledger.journals if j["kind"] == "capture"])
    ctx.expect("lifecycle_ledger", "Ledger has a capture journal iff the payment was captured", 1 if p["outcome"] == "captured" else 0, nj,
               nj == (1 if p["outcome"] == "captured" else 0), "PARTIAL_FAILURE_ORPHAN_STATE", "orphan_state")


@spec("duplicate_request", "Duplicate requests", "duplicates", "The same payment request is sent several times (double click / client retry) with the same idempotency key.",
      {"amount": AMT, "duplicates": ("int", 3, 2, 50), "with_key": ("bool", True)}, {"idempotency": "none"})
def run_dup_request(ctx, p):
    out = []
    for i in range(p["duplicates"]):
        r = req(p, key="IDEM-0001" if p["with_key"] else None)
        ctx.step(f"send #{i + 1}: POST /payments key={r['key']} amount={r['amount']}")
        resp, _ = try_drive(ctx, create_payment(ctx.gw, "client", r))
        out.append(resp)
    ctx.obs["responses"] = [{"status": r["status"], "payment_id": r.get("payment_id"), "replayed": r.get("replayed", False)} for r in out]
    single_order_expectation(ctx, "single_execution", "DUPLICATE_PAYMENT_EXECUTION", {"responses": ctx.obs["responses"]})


@spec("timeout_retry", "Timeout then retry", "retry", "Server commits but the response is lost (network timeout); the client retries.",
      {"amount": AMT, "timeouts": ("int", 1, 1, 5), "max_attempts": ("int", 4, 2, 10), "base_delay": ("int", 2, 0, 600),
       "new_key_per_attempt": ("bool", False)}, {"idempotency": "none"})
def run_timeout_retry(ctx, p):
    ctx.gw.fault("timeout_after_commit", p["timeouts"])
    pol = RetryPolicy(p["max_attempts"], p["base_delay"], 2, 600, "none")
    resp, att = client_send(ctx, req(p), pol, p["new_key_per_attempt"])
    ctx.obs["attempts"] = att
    single_order_expectation(ctx, "no_double_charge_on_retry", "DUPLICATE_PAYMENT_EXECUTION", {"attempts": att})
    ctx.expect("client_converges", "Client eventually receives a successful response", "HTTP 200", resp and resp["status"], bool(resp and resp["status"] == 200),
               "RETRY_DUPLICATE_RISK", "retry_converge", {"attempts": att}, confidence=0.8)


@spec("key_payload_mismatch", "Same key, different payload", "idempotency", "A key is reused with a different amount.",
      {"amount": AMT, "second_amount": ("dec", "250.00")}, {"idempotency": "key"})
def run_mismatch(ctx, p):
    ctx.step(f"POST /payments key=IDEM-0001 amount={p['amount']}")
    r1, _ = try_drive(ctx, create_payment(ctx.gw, "client", req(p)))
    ctx.step(f"POST /payments key=IDEM-0001 amount={p['second_amount']}  (same key, different payload)")
    r2, _ = try_drive(ctx, create_payment(ctx.gw, "client", req({"amount": p["second_amount"]})))
    ctx.obs["responses"] = [r1, r2]
    ok = r2["status"] == 422 and len(captured_for(ctx.gw)) == 1
    ft = "MISSING_IDEMPOTENCY_PROTECTION" if ctx.impl.idempotency == "none" else "IDEMPOTENCY_PAYLOAD_MISMATCH_ACCEPTED"
    ctx.expect("payload_mismatch_rejected", "Reused key with different payload is rejected (422) and nothing new executes", "HTTP 422, 1 payment",
               f"HTTP {r2['status']} amount={r2.get('amount')} replayed={r2.get('replayed', False)}, {len(captured_for(ctx.gw))} payment(s)", ok, ft, "idempotency_payload",
               {"first": r1, "second": r2}, ctx.trace.refs(op="idem_payload_mismatch_ignored"))


@spec("concurrent_same_key", "Concurrent same-key requests", "concurrency", "N workers submit the same idempotent request at the same time (seeded interleaving).",
      {"amount": AMT, "actors": ("int", 4, 2, 20)}, {"atomic_reserve": False})
def run_conc_key(ctx, p):
    sch = Scheduler(ctx.trace, ctx.cfg["seed"], ctx.cfg["schedule"])
    for i in range(p["actors"]):
        n = f"worker-{i + 1}"
        ctx.step(f"{n}: POST /payments key=IDEM-0001 amount={p['amount']} (concurrent)")
        sch.spawn(n, create_payment(ctx.gw, n, req(p)))
    sch.run()
    ctx.schedule = sch.used
    ctx.obs["actors"] = {n: (a["result"] and {"status": a["result"]["status"], "payment_id": a["result"].get("payment_id")}) or a["error"] for n, a in sch.a.items()}
    single_order_expectation(ctx, "single_execution_under_concurrency", "IDEMPOTENCY_CONCURRENT_RACE", {"actors": ctx.obs["actors"], "schedule_len": len(sch.used)})


@spec("expired_key", "Expired idempotency record", "idempotency", "Response is lost; the client retries after the idempotency record's TTL elapsed.",
      {"amount": AMT, "retry_after": ("int", 120, 1, 604800)}, {"ttl": 60})
def run_expired(ctx, p):
    ctx.gw.fault("timeout_after_commit")
    pol = RetryPolicy(2, p["retry_after"], 1, p["retry_after"], "none")
    resp, att = client_send(ctx, req(p), pol)
    ctx.obs["attempts"] = att
    ctx.obs["ttl"], ctx.obs["retry_after"] = ctx.impl.ttl, p["retry_after"]
    single_order_expectation(ctx, "no_duplicate_after_expiry", "IDEMPOTENCY_KEY_EXPIRY_DUPLICATE", {"ttl": ctx.impl.ttl, "retry_after": p["retry_after"]})


@spec("partial_failure", "Partial failure & recovery", "recovery", "Process crashes mid-request, then the client retries after the lease expires.",
      {"amount": AMT, "crash_point": ("enum", "after_authorize", ["after_authorize", "after_capture"])},
      {"atomic_ledger": False, "in_progress_policy": "reject"})
def run_partial(ctx, p):
    ctx.gw.fault(f"crash_{p['crash_point']}")
    ctx.step(f"POST /payments key=IDEM-0001 (server crashes {p['crash_point']})")
    _, err = try_drive(ctx, create_payment(ctx.gw, "client", req(p)))
    visible = {k: v["state"] for k, v in ctx.gw.payments.items()}
    ctx.obs["after_crash"] = {"error": err, "payments": visible, "journals": len(ctx.gw.ledger.journals)}
    ctx.expect("atomic_failure", "A crashed request leaves no partial state", "no payments / journals visible",
               f"payments={visible}, journals={len(ctx.gw.ledger.journals)}", not visible and not ctx.gw.ledger.journals,
               "PARTIAL_FAILURE_ORPHAN_STATE", "orphan_state", ctx.obs["after_crash"], ctx.trace.refs(op="crash"))
    ctx.gw.advance(LEASE_TICKS + 1)
    ctx.step(f"client retries after {LEASE_TICKS + 1} ticks with the same key")
    resp, err2 = try_drive(ctx, create_payment(ctx.gw, "client", req(p)))
    ps = captured_for(ctx.gw)
    nj = len([j for j in ctx.gw.ledger.journals if j["kind"] == "capture"])
    stuck = bool(resp and resp["status"] == 409)
    ok = bool(resp and resp["status"] == 200 and len(ps) == 1 and nj == 1)
    ft = "IDEMPOTENCY_STUCK_IN_PROGRESS" if stuck else dup_ftype(ctx.impl, "DUPLICATE_PAYMENT_EXECUTION")
    ctx.expect("recovery_after_crash", "Retry recovers to exactly one captured payment with one journal", "HTTP 200, 1 payment, 1 journal",
               f"HTTP {resp and resp['status']}, {len(ps)} payment(s), {nj} journal(s)", ok, ft, "idempotency_stuck" if stuck else "duplicate_payment",
               {"retry_response": resp}, ctx.trace.refs(op="idem_stuck") or ctx.trace.refs(op="captured"))


@spec("repeated_capture", "Repeated capture attempts", "duplicates", "Capture is called repeatedly for one authorization (retry / concurrent worker).",
      {"amount": AMT, "captures": ("int", 3, 2, 20)}, {"state_guard": False})
def run_repeated_capture(ctx, p):
    resp, _ = try_drive(ctx, create_payment(ctx.gw, "client", req(p, auth_only=True)))
    pid = resp["payment_id"]
    res = []
    for i in range(p["captures"]):
        ctx.step(f"POST /payments/{pid}/capture #{i + 1}")
        r, _ = try_drive(ctx, capture(ctx.gw, "client", pid))
        res.append(r["status"])
    pay = ctx.gw.payments[pid]
    nj = len([j for j in ctx.gw.ledger.journals if j["kind"] == "capture"])
    ctx.obs["capture_statuses"] = res
    ctx.expect("single_capture", "An authorization is captured exactly once", f"captured={pay['authorized']}, 1 journal",
               f"captured={pay['captured']}, {nj} journal(s), statuses={res}", pay["captured"] == pay["authorized"] and nj == 1,
               "REPEATED_CAPTURE", "repeated_capture", {"payment_id": pid, "statuses": res}, ctx.trace.refs(op="captured", payment_id=pid))


@spec("duplicate_refund", "Duplicate / excessive refunds", "duplicates", "The same refund request repeats, or distinct refunds exceed the captured amount.",
      {"amount": AMT, "refund_amount": ("dec", "40.00"), "refunds": ("int", 3, 2, 20), "distinct_keys": ("bool", False)}, {"refund_guard": False})
def run_dup_refund(ctx, p):
    resp, _ = try_drive(ctx, create_payment(ctx.gw, "client", req(p)))
    pid = resp["payment_id"]
    keys = []
    for i in range(p["refunds"]):
        k = f"RFD-{i + 1}" if p["distinct_keys"] else "RFD-0001"
        keys.append(k)
        ctx.step(f"POST /payments/{pid}/refunds key={k} amount={p['refund_amount']}")
        try_drive(ctx, refund(ctx.gw, "client", pid, p["refund_amount"], k))
    pay = ctx.gw.payments[pid]
    js = [j for j in ctx.gw.ledger.journals if j["kind"] == "refund"]
    uniq = len({j["txn_id"] for j in js})
    ctx.expect("refund_once_per_key", "No refund key is executed more than once", f"{uniq} refund journal(s) for {uniq} distinct key(s)", f"{len(js)} refund journal(s) for {uniq} distinct key(s)", len(js) == uniq,
               "DUPLICATE_REFUND_EXECUTION", "duplicate_refund", {"keys": keys, "journals": [j["journal_id"] for j in js]}, ctx.trace.refs(op="refunded", payment_id=pid))
    ctx.expect("refund_cap", "Cumulative refunds never exceed the captured amount", f"<= {pay['captured']}", pay["refunded"], pay["refunded"] <= pay["captured"],
               "OVER_REFUND", "over_refund", {"refunded": pay["refunded"], "captured": pay["captured"]}, ctx.trace.refs(op="refunded", payment_id=pid))


@spec("duplicate_webhook", "Duplicate webhook delivery", "duplicates", "At-least-once webhook delivery repeats the same event.",
      {"amount": AMT, "deliveries": ("int", 3, 2, 20)}, {"webhook_dedup": False})
def run_dup_webhook(ctx, p):
    resp, _ = try_drive(ctx, create_payment(ctx.gw, "client", req(p)))
    pid = resp["payment_id"]
    for i in range(p["deliveries"]):
        ctx.step(f"POST /webhooks event_id=EVT-0001 type=payment.settled (delivery #{i + 1})")
        try_drive(ctx, deliver_webhook(ctx.gw, "processor", {"event_id": "EVT-0001", "type": "payment.settled", "payment_id": pid, "amount": p["amount"]}))
    pay = ctx.gw.payments[pid]
    ctx.expect("webhook_once", "A webhook event is applied exactly once", f"settled={pay['captured']}", f"settled={pay['settled']}", pay["settled"] == pay["captured"],
               "DUPLICATE_WEBHOOK_PROCESSING", "duplicate_webhook", {"event_id": "EVT-0001", "effects": len(ctx.gw.webhook_effects)}, ctx.trace.refs(op="webhook_applied"))


@spec("refund_reversal", "Reversal correctness", "ledger", "Capture is reversed (optionally twice); the reversal must exactly mirror the original.",
      {"amount": AMT, "mode": ("enum", "single", ["single", "double"])}, {"reversal_correct": False})
def run_reversal(ctx, p):
    resp, _ = try_drive(ctx, create_payment(ctx.gw, "client", req(p)))
    pid = resp["payment_id"]
    for i in range(1 if p["mode"] == "single" else 2):
        ctx.step(f"POST /payments/{pid}/reverse #{i + 1}")
        try_drive(ctx, reverse_capture(ctx.gw, "client", pid))
    v = validate_ledger(ctx.gw.ledger.snapshot())
    chk = {c["invariant"]: c for c in v["checks"]}
    bal = {a: b for a, b in v["account_balances"].items()}
    ctx.expect("reversal_mirror", "Reversal is an exact mirror, balanced, and applied once", "mirror, balanced, once",
               "; ".join(chk["reversal_correctness"]["violations"] + chk["debit_credit_balance"]["violations"]) or "ok",
               chk["reversal_correctness"]["passed"] and chk["debit_credit_balance"]["passed"], "LEDGER_REVERSAL_INCORRECT", "ledger_reversal",
               {"violations": chk["reversal_correctness"]["violations"] + chk["debit_credit_balance"]["violations"]}, ctx.trace.refs(op="reversed"))
    nonzero = {a: b for a, b in bal.items() if D(b) != 0}
    ctx.expect("reversal_nets_to_zero", "After reversal every account nets to 0.00", "all balances 0.00", nonzero or "all 0.00", not nonzero,
               "LEDGER_RECONCILIATION_FAILURE", "ledger_reconcile", {"balances": bal})


@spec("rounding_split", "Rounding & precision (split)", "ledger", "A captured payment is split across N merchants; shares must sum exactly and keep 2-decimal precision.",
      {"amount": AMT, "parties": ("int", 3, 2, 9)}, {"rounding": "round_each"})
def run_rounding(ctx, p):
    resp, _ = try_drive(ctx, create_payment(ctx.gw, "client", req(p)))
    pid = resp["payment_id"]
    ctx.step(f"split {p['amount']} across {p['parties']} merchants (rounding={ctx.impl.rounding})")
    shares = split_payment(ctx.gw, "payout", pid, p["parties"])
    tot = sum(shares, ZERO)
    ctx.expect("split_sums", "Shares sum exactly to the captured amount", D(p["amount"]), tot, tot == D(p["amount"]), "LEDGER_IMBALANCE", "ledger_balance",
               {"shares": shares, "sum": tot, "difference": D(p["amount"]) - tot}, ctx.trace.refs(op="split_posted"))
    bad = [s for s in shares if scale(s) > 2]
    ctx.expect("split_precision", "Every share has at most 2 decimal places", "scale <= 2", f"{len(bad)} share(s) exceed 2 decimals", not bad, "LEDGER_ROUNDING_PRECISION",
               "ledger_precision", {"offending": bad[:3]}, ctx.trace.refs(op="split_posted"))


def _wallet(ctx, p, kind):
    w = {"bal": D(p["balance"]), "ver": 0}
    sch = Scheduler(ctx.trace, ctx.cfg["seed"], ctx.cfg["schedule"])
    mode, amt = ctx.impl.locking, D(p["amount"])
    wins = []

    def actor(name):
        t = ctx.trace
        if mode == "lock":
            yield ("lock", "wallet")
        for attempt in range(8):
            bal, ver = w["bal"], w["ver"]
            t.emit(name, "read_balance", balance=bal, version=ver)
            yield
            if kind == "debit":
                if bal < amt:
                    t.emit(name, "declined_insufficient", balance=bal)
                    break
                yield
            if mode == "optimistic" and w["ver"] != ver:
                t.emit(name, "version_conflict", expected=ver, actual=w["ver"])
                continue
            if kind == "debit":
                w["bal"] -= amt
                wins.append(name)
            else:
                w["bal"] = bal + amt  # read-modify-write using the value read earlier
            w["ver"] += 1
            t.emit(name, "write_balance", balance=w["bal"], version=w["ver"])
            break
        if mode == "lock":
            yield ("unlock", "wallet")
        return {"status": 200}
    for i in range(p["actors"]):
        n = f"txn-{i + 1}"
        ctx.step(f"{n}: {'debit' if kind == 'debit' else 'deposit'} {p['amount']} (concurrent, locking={mode})")
        sch.spawn(n, actor(n))
    sch.run()
    ctx.schedule = sch.used
    return w, wins


@spec("concurrent_payments", "Concurrent debits (overdraft race)", "concurrency", "N concurrent payments against one synthetic wallet: check-then-debit without isolation.",
      {"balance": ("dec", "100.00"), "amount": ("dec", "60.00"), "actors": ("int", 3, 2, 20)}, {"locking": "none"})
def run_overdraft(ctx, p):
    w, wins = _wallet(ctx, p, "debit")
    cap = int(D(p["balance"]) // D(p["amount"]))
    ctx.obs["wallet"] = {"final_balance": w["bal"], "successful_debits": len(wins), "max_allowed": cap}
    ctx.expect("no_overdraft", "Successful debits <= balance/amount and balance never negative", f"<= {cap} debits, balance >= 0",
               f"{len(wins)} debits, final balance {w['bal']}", len(wins) <= cap and w["bal"] >= 0, "OVERDRAFT_RACE", "overdraft",
               {"winners": wins, "final_balance": w["bal"]}, ctx.trace.refs(op="write_balance"))


@spec("lost_update", "Lost update on balance", "concurrency", "Concurrent read-modify-write deposits on one synthetic balance.",
      {"balance": ("dec", "100.00"), "amount": ("dec", "10.00"), "actors": ("int", 4, 2, 20)}, {"locking": "none"})
def run_lost_update(ctx, p):
    w, _ = _wallet(ctx, p, "credit")
    want = D(p["balance"]) + D(p["amount"]) * p["actors"]
    lost = int((want - w["bal"]) / D(p["amount"])) if D(p["amount"]) else 0
    ctx.obs["wallet"] = {"expected": want, "final_balance": w["bal"], "lost_updates": lost}
    ctx.expect("no_lost_updates", "Final balance equals initial + sum of all deposits", want, w["bal"], w["bal"] == want, "LOST_UPDATE", "lost_update",
               {"lost_updates": lost, "shortfall": want - w["bal"]}, ctx.trace.refs(op="write_balance"))


@spec("deadlock", "Deadlock indicators", "concurrency", "Opposite-direction transfers lock two accounts in different orders.",
      {"amount": ("dec", "10.00"), "actors": ("int", 4, 2, 10)}, {"deadlock_ordering": False})
def run_deadlock(ctx, p):
    sch = Scheduler(ctx.trace, ctx.cfg["seed"], ctx.cfg["schedule"], ctx.cfg["abort_on_deadlock"])
    bal = {"ACC-A": D("1000.00"), "ACC-B": D("1000.00")}
    amt = D(p["amount"])

    def transfer(name, src, dst):
        first, second = (sorted([src, dst]) if ctx.impl.deadlock_ordering else [src, dst])
        yield ("lock", first)
        yield
        yield ("lock", second)
        bal[src] -= amt
        bal[dst] += amt
        ctx.trace.emit(name, "transfer", src=src, dst=dst, amount=amt)
        yield ("unlock", second)
        yield ("unlock", first)
        return {"status": 200}
    for i in range(p["actors"]):
        n = f"txn-{i + 1}"
        s, d = ("ACC-A", "ACC-B") if i % 2 == 0 else ("ACC-B", "ACC-A")
        ctx.step(f"{n}: transfer {amt} {s} -> {d}, lock order {'global' if ctx.impl.deadlock_ordering else 'as requested'}")
        sch.spawn(n, transfer(n, s, d))
    sch.run()
    ctx.schedule = sch.used
    done = sum(1 for a in sch.a.values() if a["done"] and not a["error"])
    ctx.obs["deadlock"] = {"detected": sch.deadlocks_seen, "unresolved_cycle": sch.deadlock, "completed": done, "of": p["actors"], "balances": bal}
    ctx.expect("no_deadlock", "All transfers complete without a lock cycle", f"{p['actors']} completed, 0 deadlocks", f"{done} completed, {sch.deadlocks_seen} deadlock(s), cycle={sch.deadlock}",
               sch.deadlocks_seen == 0, "DEADLOCK", "deadlock", ctx.obs["deadlock"], ctx.trace.refs(op="deadlock_detected"))


@spec("state_race", "Capture vs void race", "concurrency", "Capture and void race on one authorization (inconsistent state transitions).",
      {"amount": AMT}, {"locking": "none"})
def run_state_race(ctx, p):
    resp, _ = try_drive(ctx, create_payment(ctx.gw, "client", req(p, auth_only=True)))
    pid = resp["payment_id"]
    sch = Scheduler(ctx.trace, ctx.cfg["seed"], ctx.cfg["schedule"])
    ctx.step(f"capture-worker: POST /payments/{pid}/capture (concurrent)")
    ctx.step(f"void-worker: POST /payments/{pid}/void (concurrent)")
    sch.spawn("capture-worker", capture(ctx.gw, "capture-worker", pid))
    sch.spawn("void-worker", void(ctx.gw, "void-worker", pid))
    sch.run()
    ctx.schedule = sch.used
    both = bool(ctx.trace.refs(op="captured", payment_id=pid) and ctx.trace.refs(op="voided", payment_id=pid))
    ctx.obs["final_state"] = ctx.gw.payments[pid]["state"]
    ctx.expect("single_winner", "Exactly one of capture / void wins", "one transition", f"capture={bool(ctx.trace.refs(op='captured', payment_id=pid))}, void={bool(ctx.trace.refs(op='voided', payment_id=pid))}, final={ctx.obs['final_state']}",
               not both, "INCONSISTENT_STATE_TRANSITION", "inconsistent_state", {"final_state": ctx.obs["final_state"]}, ctx.trace.refs(payment_id=pid))


@spec("retry_storm", "Retry storm & dead letters", "retry", "Many clients retry during an outage; measures amplification, duplicate effects and dead-letter handling.",
      {"clients": ("int", 200, 5, 2000), "outage": ("int", 20, 1, 200), "capacity": ("int", 20, 1, 1000), "max_attempts": ("int", 10, 1, 30),
       "base": ("int", 1, 0, 60), "factor": ("int", 1, 1, 4), "cap": ("int", 60, 1, 3600), "jitter": ("enum", "none", ["none", "full", "equal"]),
       "timeout": ("int", 3, 1, 60), "dead_letter": ("bool", False), "shed_expired": ("bool", False), "max_amplification": ("dec", "3.50")},
      {"idempotency": "none"}, {"max_attempts": 4, "base": 4, "factor": 2, "jitter": "full", "timeout": 5, "dead_letter": True, "shed_expired": True})
def run_storm(ctx, p):
    pol = RetryPolicy(p["max_attempts"], p["base"], p["factor"], p["cap"], p["jitter"], p["timeout"], p["dead_letter"])
    ctx.step(f"{p['clients']} clients, outage {p['outage']} ticks, capacity {p['capacity']}/tick, policy={pol}")
    r = simulate_storm(pol, p["clients"], (0, p["outage"]), p["capacity"], 300, ctx.cfg["seed"], ctx.impl.idempotency != "none", shed_expired=p["shed_expired"])
    ctx.obs["storm"] = {k: v for k, v in r.items() if k not in ("load_series", "queue_series")}
    ctx.charts["load_series"], ctx.charts["queue_series"] = r["load_series"], r["queue_series"]
    ctx.obs["policy_analysis"] = analyze_policy(pol.__dict__, {"operation": "create_payment", "has_idempotency_key": ctx.impl.idempotency != "none"})
    amp = Decimal(r["amplification"])
    ctx.expect("bounded_amplification", "Retries amplify load by at most the allowed factor and peak load stays near capacity", f"amplification <= {p['max_amplification']}, peak <= {p['capacity'] * 5}",
               f"amplification {amp}, peak {r['peak_load_per_tick']}/tick", amp <= D(p["max_amplification"]) and r["peak_load_per_tick"] <= p["capacity"] * 5,
               "RETRY_STORM", "retry_storm", ctx.obs["storm"], confidence=0.8)
    ctx.expect("no_duplicate_effects", "Retries never re-execute a committed operation", 0, r["duplicate_effects"], r["duplicate_effects"] == 0, "RETRY_DUPLICATE_RISK", "retry_dup",
               {"duplicate_effects": r["duplicate_effects"], "effects_for_abandoned_clients": r["effects_for_abandoned_clients"]}, confidence=0.8)
    ctx.expect("dead_letter_handling", "Requests that exhaust retries are parked in a dead-letter queue", "0 dropped", f"{r['abandoned_without_dlq']} dropped, {r['dead_lettered']} dead-lettered",
               r["abandoned_without_dlq"] == 0, "DEAD_LETTER_MISSING", "dlq", {"abandoned_without_dlq": r["abandoned_without_dlq"]}, confidence=0.8)


@spec("settlement", "Settlement & reconciliation", "settlement", "Synthetic processor settlement feed with injected anomalies; reconciled against the internal ledger.",
      {"payments": ("int", 40, 2, 500), "refunds": ("int", 5, 0, 100), "mismatch": ("int", 0, 0, 50), "delayed": ("int", 0, 0, 50), "missing": ("int", 0, 0, 50),
       "duplicate": ("int", 0, 0, 50), "refund_mismatch": ("int", 0, 0, 50), "partial_after": ("int", 0, 0, 1000), "cutoff": ("int", 1, 0, 30)},
      {"settlement_dedup": False, "batch_atomic": False})
def run_settlement(ctx, p):
    an = {k: p[k] for k in ("mismatch", "delayed", "missing", "duplicate", "refund_mismatch", "partial_after")}
    ctx.step(f"generate {p['payments']} synthetic captures + {p['refunds']} refunds; inject {an}; reconcile at cutoff day {p['cutoff']}")
    r = S.simulate(ctx.cfg["seed"], p["payments"], p["refunds"], an, p["cutoff"], ctx.impl.settlement_dedup, ctx.impl.batch_atomic)
    ctx.obs["settlement"] = {k: v for k, v in r.items() if k != "importer_ledger"}
    ctx.extra_journals = r["importer_ledger"]
    by = {}
    for d in r["reconciliation"]["discrepancies"]:
        by.setdefault(d["kind"], []).append(d)
    for kind, ds in by.items():
        ctx.expect(f"recon_{kind.lower()}", f"Reconciliation shows no {kind}", "0 discrepancies, variance 0.00", f"{len(ds)} discrepancy(ies); variance {r['reconciliation']['variance']}", False,
                   kind, kind.lower(), {"refs": [d["ref"] for d in ds[:10]], "examples": ds[:3], "variance": r["reconciliation"]["variance"], "classification": S.CLASSIFICATION}, confidence=0.85)
    imp = r["importer"]
    ctx.expect("importer_idempotent", "Settlement importer ignores duplicate entries", "0 duplicate postings", f"{len(r['injected'].get('duplicate', []))} duplicate line(s), applied={imp['applied']}, skipped={imp['skipped_duplicates']}",
               not (r["injected"].get("duplicate") and not ctx.impl.settlement_dedup), "SETTLEMENT_DUPLICATE_ENTRY", "settlement_dup", {"importer": imp}, confidence=0.85)
    ctx.expect("batch_atomic", "A failed settlement batch is not partially applied", "all-or-nothing", str(imp), not imp.get("partial_batch_applied"), "SETTLEMENT_PARTIAL_FAILURE", "settlement_partial",
               {"importer": imp, "failed_after_row": r["injected"].get("failed_after_row")}, confidence=0.85)


# ---------------------------------------------------------------- execute / analyze
def execute(cfg: dict) -> Ctx:
    cfg = normalize_config(cfg)
    ctx = Ctx(cfg, resolve_impl(cfg), cfg["params"])
    SPECS[cfg["type"]]["fn"](ctx, cfg["params"])
    ctx.finish()
    return ctx


def core(ctx: Ctx) -> dict:
    return jsonable({"trace": ctx.trace.events, "journals": ctx.journals, "payments": ctx.payments,
                     "expectations": [{k: e[k] for k in ("id", "ok", "actual")} for e in ctx.exp], "obs": ctx.obs, "schedule": ctx.schedule})


def run_digest(ctx: Ctx) -> str:
    return digest(core(ctx))


SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}
DUP_FAMILIES = {"duplicate_payment", "repeated_capture", "duplicate_refund", "duplicate_webhook", "over_refund", "settlement_dup", "settlement_duplicate_entry"}


def analyze(ctx: Ctx, run_id: str, repo_index=None) -> list[dict]:
    cfg = ctx.cfg
    col = Collector(run_id, cfg.get("scenario_id") or cfg["name"], ctx.steps, cfg, repo_index)
    for e in ctx.exp:
        if not e["ok"]:
            col.add(e["ftype"], e["expected"], e["actual"], e["evidence"], e["seqs"], e["confidence"], family=e["family"])
    for h in ctx.hits:
        ex = next((f for f in col.items.values() if f["family"] == h["family"]), None)
        if ex:
            ex["evidence"].append(h["evidence"])
            ex["trace_seq"] = sorted(set(ex["trace_seq"]) | set(h["trace_seq"]))
        else:
            col.add(h["ftype"], "condition does not occur", h["summary"], h["evidence"], h["trace_seq"], 0.85, "generic detector over simulated state and trace", family=h["family"])
    for c in ctx.validation["checks"]:
        if c["passed"]:
            continue
        ft = LEDGER_MAP[c["invariant"]]
        sym = next((f for f in col.items.values() if f["family"] in DUP_FAMILIES), None)
        if c["invariant"] == "transaction_uniqueness" and sym:
            sym["evidence"].append({"ledger_invariant": c["invariant"], "violations": c["violations"][:5]})
            continue
        col.add(ft, f"ledger invariant '{c['invariant']}' holds", "; ".join(c["violations"][:2]), {"invariant": c["invariant"], "violations": c["violations"][:10]},
                confidence=0.95, basis="exact Decimal ledger validation", family="ledger_" + c["invariant"])
    for f in col.items.values():  # an actual duplicate charge is critical regardless of its root-cause label
        if f["family"] == "duplicate_payment":
            f["severity"] = "critical"
    out = sorted(col.items.values(), key=lambda f: (SEV[f["severity"]], f["failure_type"]))
    for i, f in enumerate(out, 1):
        f["finding_id"] = f"{run_id}-F{i:02d}"
    return out


def explore(cfg: dict, seeds: int = 25) -> dict:
    """Run the same scenario over consecutive seeds to find failing interleavings."""
    base = normalize_config(cfg)
    fails = []
    for s in range(base["seed"], base["seed"] + seeds):
        ctx = execute({**base, "seed": s, "schedule": None})
        bad = [e["id"] for e in ctx.exp if not e["ok"]]
        if bad:
            fails.append({"seed": s, "failed": bad, "schedule": ctx.schedule})
    return {"seeds_tried": seeds, "first_seed": base["seed"], "failing_seeds": len(fails), "failure_rate": str((Decimal(len(fails)) / Decimal(seeds)).quantize(Decimal("0.01"))),
            "first_failure": fails[0] if fails else None, "reproduce_with": {"seed": fails[0]["seed"]} if fails else None}


# ---------------------------------------------------------------- built-in library
LIBRARY = [
    ("SCN-LIFE-CAPTURE", "Authorize, capture and partial refund", "payment_lifecycle", {"refund_amount": "25.00"}, 1),
    ("SCN-LIFE-DECLINE", "Issuer decline (failed payment)", "payment_lifecycle", {"outcome": "declined"}, 1),
    ("SCN-LIFE-AUTHONLY", "Authorization hold only", "payment_lifecycle", {"outcome": "auth_only"}, 1),
    ("SCN-DUP-REQUEST", "Duplicate request, no idempotency", "duplicate_request", {}, 1),
    ("SCN-TIMEOUT-RETRY", "Timeout after commit, naive retry", "timeout_retry", {}, 1),
    ("SCN-CLIENT-NEWKEY", "Client regenerates key on retry", "timeout_retry", {"new_key_per_attempt": True}, 1, {"idempotency": "key_payload"}),
    ("SCN-KEY-MISMATCH", "Same key, different payload", "key_payload_mismatch", {}, 1),
    ("SCN-CONC-KEY", "Concurrent requests, same key", "concurrent_same_key", {}, 3),
    ("SCN-KEY-EXPIRY", "Retry after idempotency TTL", "expired_key", {}, 1),
    ("SCN-PARTIAL-AUTH", "Crash after authorization", "partial_failure", {}, 1),
    ("SCN-PARTIAL-CAPTURE", "Crash after capture, before ledger", "partial_failure", {"crash_point": "after_capture"}, 1),
    ("SCN-REPEAT-CAPTURE", "Repeated capture attempts", "repeated_capture", {}, 1),
    ("SCN-DUP-REFUND", "Duplicate refund execution", "duplicate_refund", {}, 1),
    ("SCN-OVER-REFUND", "Distinct refunds exceed capture", "duplicate_refund", {"distinct_keys": True, "refund_amount": "60.00", "refunds": 2}, 1),
    ("SCN-DUP-WEBHOOK", "Duplicate webhook delivery", "duplicate_webhook", {}, 1),
    ("SCN-REVERSAL", "Incorrect reversal entry", "refund_reversal", {}, 1),
    ("SCN-REVERSAL-TWICE", "Capture reversed twice", "refund_reversal", {"mode": "double"}, 1),
    ("SCN-ROUNDING", "Split rounding drift (100.00 / 3)", "rounding_split", {}, 1),
    ("SCN-OVERDRAFT", "Concurrent debits overdraw wallet", "concurrent_payments", {}, 1),
    ("SCN-LOST-UPDATE", "Lost update on balance", "lost_update", {}, 1),
    ("SCN-DEADLOCK", "Opposite-order account locking", "deadlock", {}, 1),
    ("SCN-STATE-RACE", "Capture vs void race", "state_race", {}, 4),
    ("SCN-RETRY-STORM", "Retry storm during outage", "retry_storm", {}, 1),
    ("SCN-SETTLE-MISMATCH", "Settlement amount + refund sign mismatches", "settlement", {"mismatch": 3, "refund_mismatch": 2}, 1),
    ("SCN-SETTLE-DELAY", "Delayed settlement", "settlement", {"delayed": 4}, 1),
    ("SCN-SETTLE-MISSING-DUP", "Missing and duplicate settlement lines", "settlement", {"missing": 2, "duplicate": 2}, 1),
    ("SCN-SETTLE-PARTIAL", "Partial settlement batch failure", "settlement", {"partial_after": 25}, 1),
    ("SCN-SETTLE-CLEAN", "Clean settlement (control)", "settlement", {}, 1),
]


def library_configs():
    return [{"scenario_id": e[0], "name": e[1], "type": e[2], "params": e[3], "seed": e[4], "profile": "scenario_default",
             "implementation": e[5] if len(e) > 5 else {}, "builtin": True} for e in LIBRARY]
