"""Duplicate-transaction & state-integrity detectors. They work on gateway STATE + TRACE only,
never on knowledge of which scenario ran."""
from __future__ import annotations
from collections import defaultdict
from .money import ZERO, jsonable
from .sched import Trace

CAPTURED = ("captured", "partially_refunded", "refunded", "reversed")


def detect(gw, trace: Trace) -> list[dict]:
    hits = []
    pays = gw.payments

    def hit(family, ftype, summary, evidence, seqs):
        hits.append({"family": family, "ftype": ftype, "summary": summary, "evidence": jsonable(evidence), "trace_seq": sorted(set(seqs))})

    # duplicate payment execution: >1 captured payments for same business order
    groups = defaultdict(list)
    for p in pays.values():
        if p["captured"] > ZERO and p.get("order_ref"):
            groups[(p["merchant"], p["customer"], p["order_ref"])].append(p)
    for (m, c, o), ps in groups.items():
        if len(ps) > 1:
            keys = sorted({str(p["key"]) for p in ps})
            seqs = [s for p in ps for s in trace.refs(op="captured", payment_id=p["payment_id"])]
            cause = ("no idempotency key honoured (idempotency=none or keyless request)" if gw.impl.idempotency == "none" or None in {p["key"] for p in ps}
                     else "same key produced several payments (race / expired record)" if len(keys) == 1
                     else "client generated a new key per attempt")
            hit("duplicate_payment", "DUPLICATE_PAYMENT_EXECUTION",
                f"order {o} captured {len(ps)} times (total {sum((p['captured'] for p in ps), ZERO)})",
                {"order_ref": o, "payments": [p["payment_id"] for p in ps], "keys": keys,
                 "total_captured": sum((p["captured"] for p in ps), ZERO), "probable_cause": cause}, seqs)

    # repeated capture on one authorization
    cap_j = defaultdict(list)
    for j in gw.ledger.journals:
        if j["kind"] == "capture" and j["status"] == "committed":
            cap_j[j["txn_id"]].append(j["journal_id"])
    for pid, js in cap_j.items():
        p = pays.get(pid)
        if p and (len(js) > 1 or p["captured"] > p["authorized"]):
            hit("repeated_capture", "REPEATED_CAPTURE",
                f"{pid}: captured {p['captured']} against authorization {p['authorized']} ({len(js)} capture journals)",
                {"payment_id": pid, "authorized": p["authorized"], "captured": p["captured"], "journals": js},
                trace.refs(op="captured", payment_id=pid))

    # duplicate / excessive refunds
    rj = defaultdict(list)
    for j in gw.ledger.journals:
        if j["kind"] == "refund":
            rj[j["txn_id"]].append(j["journal_id"])
    for tid, js in rj.items():
        if len(js) > 1:
            pid = tid.split(":")[0]
            hit("duplicate_refund", "DUPLICATE_REFUND_EXECUTION", f"refund {tid} executed {len(js)} times",
                {"refund": tid, "journals": js}, trace.refs(op="refunded", payment_id=pid))
    for pid, p in pays.items():
        if p["refunded"] > p["captured"] > ZERO:
            hit("over_refund", "OVER_REFUND", f"{pid}: refunded {p['refunded']} exceeds captured {p['captured']}",
                {"payment_id": pid, "refunded": p["refunded"], "captured": p["captured"]},
                trace.refs(op="refunded", payment_id=pid))

    # duplicate webhook effects
    by_ev = defaultdict(list)
    for e in gw.webhook_effects:
        by_ev[e["event_id"]].append(e)
    for eid, es in by_ev.items():
        if len(es) > 1:
            pid = es[0]["payment_id"]
            hit("duplicate_webhook", "DUPLICATE_WEBHOOK_PROCESSING",
                f"webhook {eid} applied {len(es)} times (settled {pays[pid]['settled']} vs captured {pays[pid]['captured']})",
                {"event_id": eid, "payment_id": pid, "journals": [e["journal_id"] for e in es]}, trace.refs(op="webhook_applied", event_id=eid))

    # keyless request that reached execution
    keyless = [e for e in trace.events if e["op"] == "request" and not e.get("key")]
    if keyless:
        hit("idempotency", "MISSING_IDEMPOTENCY_PROTECTION", f"{len(keyless)} payment request(s) carried no idempotency key",
            {"requests": len(keyless)}, [e["seq"] for e in keyless])

    # orphan / partial state
    jids = {j["txn_id"] for j in gw.ledger.journals if j["kind"] == "capture"}
    for pid, p in pays.items():
        if p["state"] == "captured" and pid not in jids:
            hit("orphan_state", "PARTIAL_FAILURE_ORPHAN_STATE", f"{pid} is captured but has no ledger journal",
                {"payment_id": pid, "state": p["state"]}, trace.refs(payment_id=pid))
        elif p["state"] == "authorized" and not p.get("auth_only"):
            hit("orphan_state", "PARTIAL_FAILURE_ORPHAN_STATE", f"{pid} left authorized (hold never captured or released)",
                {"payment_id": pid, "state": p["state"]}, trace.refs(payment_id=pid))
    for j in gw.ledger.journals:
        if j["kind"] == "capture" and j["txn_id"] not in pays:
            hit("orphan_state", "PARTIAL_FAILURE_ORPHAN_STATE", f"journal {j['journal_id']} references unknown payment",
                {"journal_id": j["journal_id"]}, [])

    # inconsistent transitions
    for pid, p in pays.items():
        if trace.refs(op="voided", payment_id=pid) and (trace.refs(op="captured", payment_id=pid) or pid in cap_j):
            hit("inconsistent_state", "INCONSISTENT_STATE_TRANSITION",
                f"{pid} was both captured and voided (final state {p['state']})",
                {"payment_id": pid, "final_state": p["state"], "capture_journals": cap_j.get(pid, [])},
                trace.refs(op="captured", payment_id=pid) + trace.refs(op="voided", payment_id=pid))

    # stuck idempotency records
    stuck = trace.refs(op="idem_stuck")
    if stuck:
        keys = sorted({e["key"] for e in trace.events if e["op"] == "idem_stuck"})
        hit("idempotency_stuck", "IDEMPOTENCY_STUCK_IN_PROGRESS", f"key(s) {keys} stuck in-progress after crash",
            {"keys": keys}, stuck)
    return hits
