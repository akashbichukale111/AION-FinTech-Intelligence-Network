"""Deterministic SYNTHETIC telemetry for local demos. Goes through the normal validated ingest path, always tagged source='synthetic'.
Scenario: payment-service deploy shrinks DB pool -> pool exhaustion -> timeouts -> errors cascade to checkout/gateway; webhook backlog + duplicate events."""
import random, time
from . import ingest as I

def hx(r, n): return "".join(r.choice("0123456789abcdef") for _ in range(n))

def generate(minutes=60, seed=7):
    r, now = random.Random(seed), time.time(); t0 = now - minutes * 60 - 30; inc0, inc1 = t0 + minutes * 60 * 0.75, t0 + minutes * 60 * 0.97
    logs, spans, mets, evs = [], [], [], []
    def sp(tid, sid, pid, svc, name, ts, dur, err=False, **a): spans.append({"trace_id": tid, "span_id": sid, "parent_id": pid, "service": svc, "name": name, "start": ts, "duration_ms": dur, "status": "error" if err else "ok", "attrs": a})
    t = t0
    while t < t0 + minutes * 60:
        bad = inc0 <= t <= inc1; tid = hx(r, 32); ids = [hx(r, 16) for _ in range(7)]
        pfail = r.random() < (0.55 if bad else 0.004); xfail = r.random() < 0.002
        dbd = r.uniform(4000, 6000) if bad and pfail else r.uniform(6, 14); extd = r.uniform(90, 160)
        payd = (dbd + extd + 40) if not pfail else dbd + 30; ledf = r.random() < 0.002
        sp(tid, ids[3], ids[2], "payment-service", "pg.query", t + .09, dbd, pfail, **{"span.kind": "client", "db.system": "postgresql", "db.name": "payments-db"})
        if not pfail: sp(tid, ids[4], ids[2], "payment-service", "provider.authorize", t + .1, extd, xfail, **{"span.kind": "client", "peer.service": "stripe-sim", "peer.external": True})
        if not pfail:
            sp(tid, ids[5], ids[2], "ledger-service", "ledger.post", t + .3, 25 + r.random() * 10, ledf, **{"span.kind": "server"})
            sp(tid, ids[6], ids[5], "ledger-service", "pg.query", t + .31, 8, False, **{"span.kind": "client", "db.system": "postgresql", "db.name": "ledger-db"})
        sp(tid, ids[2], ids[1], "payment-service", "POST /payments", t + .08, payd, pfail or xfail, **{"http.status_code": 500 if pfail else 200})
        sp(tid, ids[1], ids[0], "checkout-service", "POST /checkout", t + .05, payd + 60, pfail or xfail, **{"http.status_code": 502 if pfail else 200})
        sp(tid, ids[0], None, "api-gateway", "POST /api/pay", t, payd + 100, pfail or xfail, **{"http.status_code": 504 if pfail else 200})
        if pfail:
            logs += [{"ts": t + 5, "service": "payment-service", "level": "ERROR", "message": f"could not obtain connection from pool (pool exhausted) for user ravi{int(t)%50}@example.com card 4111111111111111 token=abc12345 password=hunter2", "trace_id": tid},
                     {"ts": t + 5.1, "service": "payment-service", "level": "WARN", "message": "timeout calling payments-db after 5000ms", "trace_id": tid},
                     {"ts": t + 5.2, "service": "checkout-service", "level": "ERROR", "message": "payment authorization failed: upstream 500", "trace_id": tid}]
        elif r.random() < 0.05: logs.append({"ts": t + .5, "service": "api-gateway", "level": "INFO", "message": "request completed 200", "trace_id": tid})
        t += 5
    for s in range(int(minutes * 4)):          # metrics every 15s
        ts = t0 + s * 15; bad = inc0 <= ts <= inc1; prog = min(1, max(0, (ts - inc0) / 300)) if bad else 0
        for svc in ("api-gateway", "checkout-service", "payment-service", "ledger-service", "webhook-service", "settlement-service"):
            mets += [{"service": svc, "name": "cpu_percent", "value": round(35 + r.random() * 8 + (25 * prog if svc == "payment-service" else 0), 1), "ts": ts},
                     {"service": svc, "name": "memory_percent", "value": round(55 + r.random() * 5, 1), "ts": ts}, {"service": svc, "name": "up", "value": 1, "ts": ts}]
        mets += [{"service": "payment-service", "name": "db_pool_in_use", "value": 20 if bad else r.randint(5, 10), "ts": ts, "labels": {"pool": "payments-db"}},
                 {"service": "payment-service", "name": "db_pool_max", "value": 20, "ts": ts, "labels": {"pool": "payments-db"}},
                 {"service": "webhook-service", "name": "queue_depth", "value": int(60 + r.random() * 80 + (3500 * prog if bad else 0)), "ts": ts}]
    for k in range(int(minutes * 3)):          # webhook/settlement logs WITHOUT trace ids (shows missing-correlation handling)
        ts = t0 + k * 20
        if inc0 <= ts <= inc1 + 120:
            logs += [{"ts": ts, "service": "webhook-service", "level": "WARN", "message": f"webhook delivery delayed, retry {r.randint(1,5)} queue backlog growing"},
                     {"ts": ts + 1, "service": "webhook-service", "level": "INFO", "message": f"duplicate event evt_{r.randint(1000,9999)} already processed"}]
            if k % 4 == 0: logs.append({"ts": ts + 2, "service": "settlement-service", "level": "WARN", "message": "settlement batch delayed: upstream payment failures"})
        elif r.random() < .05: logs.append({"ts": ts, "service": "webhook-service", "level": "INFO", "message": "webhook delivered"})
    evs = [{"kind": "deploy", "service": "checkout-service", "ts": t0 + 600, "payload": {"version": "5.3.1", "commit": "9d1e2f0", "environment": "synthetic"}},
           {"kind": "deploy", "service": "payment-service", "ts": inc0 - 60, "payload": {"version": "2.14.0", "commit": "a1b2c3d", "environment": "synthetic", "note": "reduces db pool size (synthetic scenario)"}}]
    res = {}
    for name, items, fn in (("logs", logs, I.ingest_logs), ("traces", spans, I.ingest_traces), ("metrics", mets, I.ingest_metrics), ("events", evs, I.ingest_events)):
        tot = {"accepted": 0, "duplicates": 0, "rejected": 0}
        for i in range(0, len(items), 4000):
            chunk = items[i:i + 4000]; rr = fn({"spans": chunk} if name == "traces" else {"records": chunk}, source="synthetic")
            for k in tot: tot[k] += rr[k]
        res[name] = tot
    return {"scenario": "payment-service db-pool exhaustion after deploy 2.14.0", "ingested": res, "label": "SYNTHETIC - not production telemetry"}
