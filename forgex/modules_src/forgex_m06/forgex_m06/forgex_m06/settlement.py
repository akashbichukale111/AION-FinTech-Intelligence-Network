"""Synthetic settlement feed generation + reconciliation. Everything here is SIMULATED data."""
from __future__ import annotations
import random
from decimal import Decimal
from .money import D, q, ZERO, FEE_RATE, jsonable
from .ledger import Ledger, validate_ledger

CLASSIFICATION = "SIMULATION_FINDING_NOT_A_FINANCIAL_DISCREPANCY"


def build_internal(rng: random.Random, n: int, refunds: int) -> list[dict]:
    rows = []
    for i in range(n):
        amt = q(Decimal(rng.randint(500, 250000)) / Decimal(100))
        net = amt - q(amt * FEE_RATE)
        rows.append({"ref": f"PAY-{i + 1:04d}", "type": "capture", "amount": net, "gross": amt, "row": len(rows)})
    for i in range(min(refunds, n)):
        gross = q(rows[i]["gross"] / Decimal(4))
        net = gross - q(gross * FEE_RATE)
        rows.append({"ref": f"REF-{i + 1:04d}", "type": "refund", "amount": -net, "gross": gross, "row": len(rows)})
    return rows


def build_feed(rng, internal, a: dict, cutoff: int) -> tuple[list[dict], dict]:
    lines = [{"ref": r["ref"], "type": r["type"], "amount": r["amount"], "settle_day": 1} for r in internal]
    idx = list(range(len(lines)))
    rng.shuffle(idx)
    inj = {k: [] for k in ("mismatch", "delayed", "missing", "duplicate", "refund_mismatch")}
    refund_idx = [i for i in idx if lines[i]["type"] == "refund"]
    cap_idx = [i for i in idx if lines[i]["type"] == "capture"]
    take = lambda pool, k: [pool.pop() for _ in range(min(k, len(pool)))]
    for i in take(cap_idx, a.get("mismatch", 0)):
        lines[i]["amount"] += Decimal("0.01") * rng.randint(1, 3)
        inj["mismatch"].append(lines[i]["ref"])
    for i in take(cap_idx, a.get("delayed", 0)):
        lines[i]["settle_day"] = cutoff + rng.randint(1, 4)
        inj["delayed"].append(lines[i]["ref"])
    for i in take(refund_idx, a.get("refund_mismatch", 0)):
        lines[i]["amount"] = -lines[i]["amount"]  # sign flipped: refund settled as credit
        inj["refund_mismatch"].append(lines[i]["ref"])
    drop = set()
    for i in take(cap_idx, a.get("missing", 0)):
        drop.add(i)
        inj["missing"].append(lines[i]["ref"])
    extra = []
    for i in take(cap_idx, a.get("duplicate", 0)):
        extra.append(dict(lines[i]))
        inj["duplicate"].append(lines[i]["ref"])
    feed = [l for i, l in enumerate(lines) if i not in drop] + extra
    for i, l in enumerate(feed):
        l["row"] = i
    partial = a.get("partial_after", 0)
    failed_after = None
    if partial and partial < len(feed):
        failed_after = partial
        inj["partial_batch"] = [l["ref"] for l in feed[partial:]]
        feed = feed[:partial]
    return feed, {k: v for k, v in inj.items() if v} | ({"failed_after_row": failed_after} if failed_after else {})


def reconcile(internal, feed, cutoff, failed_after=None) -> dict:
    by = {}
    for l in feed:
        by.setdefault(l["ref"], []).append(l)
    disc = []
    exp_total = sum((r["amount"] for r in internal), ZERO)
    got_total = sum((l["amount"] for l in feed if l["settle_day"] <= cutoff), ZERO)
    known = {r["ref"] for r in internal}
    for r in internal:
        ls = by.get(r["ref"], [])
        if not ls:
            kind = "SETTLEMENT_PARTIAL_FAILURE" if failed_after is not None else "SETTLEMENT_MISSING_RECORD"
            disc.append({"ref": r["ref"], "kind": kind, "expected": r["amount"], "actual": None})
            continue
        if len(ls) > 1:
            disc.append({"ref": r["ref"], "kind": "SETTLEMENT_DUPLICATE_ENTRY", "expected": r["amount"],
                         "actual": sum((l["amount"] for l in ls), ZERO), "entries": len(ls)})
        l = ls[0]
        if l["settle_day"] > cutoff:
            disc.append({"ref": r["ref"], "kind": "SETTLEMENT_DELAYED", "expected": r["amount"], "actual": l["amount"],
                         "settle_day": l["settle_day"], "cutoff": cutoff})
        elif l["amount"] != r["amount"]:
            kind = "REFUND_SETTLEMENT_INCONSISTENT" if r["type"] == "refund" else "SETTLEMENT_MISMATCH"
            disc.append({"ref": r["ref"], "kind": kind, "expected": r["amount"], "actual": l["amount"],
                         "delta": l["amount"] - r["amount"]})
    disc += [{"ref": ref, "kind": "SETTLEMENT_UNEXPECTED_RECORD", "expected": None, "actual": ls[0]["amount"]}
             for ref, ls in by.items() if ref not in known]
    return {"expected_total": exp_total, "settled_total": got_total, "variance": got_total - exp_total,
            "discrepancies": disc, "classification": CLASSIFICATION, "simulated": True}


def import_feed(feed, dedup: bool, atomic: bool, failed_after) -> tuple[Ledger, dict]:
    """Model of the settlement importer: posts one balanced journal per feed line."""
    led, seen, applied, skipped = Ledger(), set(), 0, 0
    batch_rejected = failed_after is not None and atomic
    if batch_rejected:
        return led, {"applied": 0, "skipped_duplicates": 0, "batch_rejected": True}
    for l in feed:
        key = (l["ref"], l["type"])
        if dedup and key in seen:
            skipped += 1
            continue
        seen.add(key)
        a = l["amount"]
        led.post(f"{l['ref']}:settle", "settlement_import",
                 [("bank_cash", a, ZERO), ("settlement_receivable", ZERO, a)] if a > 0 else
                 [("bank_cash", ZERO, -a), ("settlement_receivable", -a, ZERO)])
        applied += 1
    return led, {"applied": applied, "skipped_duplicates": skipped, "batch_rejected": False,
                 "partial_batch_applied": failed_after is not None and not atomic}


def simulate(seed: int, n: int, refunds: int, anomalies: dict, cutoff: int, dedup: bool, atomic: bool) -> dict:
    rng = random.Random(seed)
    internal = build_internal(rng, n, refunds)
    feed, injected = build_feed(rng, internal, anomalies, cutoff)
    rec = reconcile(internal, feed, cutoff, injected.get("failed_after_row"))
    led, imp = import_feed(feed, dedup, atomic, injected.get("failed_after_row"))
    val = validate_ledger(led.journals)
    return jsonable({"internal_count": len(internal), "feed_count": len(feed), "injected": injected,
                     "reconciliation": rec, "importer": imp, "importer_ledger_validation": val,
                     "importer_ledger": led.journals, "synthetic": True})
