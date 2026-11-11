"""SLO engine - every figure is computed from stored telemetry; missing data is reported, never invented."""
from collections import defaultdict
from . import analysis as A
from .config import slo_for
from .db import lj, q

def _incident_stats(since, until):
    rows = q("SELECT services,started_at,created_at,resolved_at FROM incidents WHERE created_at>=? AND created_at<?", (since, until))
    mttd = [r["created_at"] - r["started_at"] for r in rows if r["started_at"] and r["created_at"] >= r["started_at"]]
    mttr = [r["resolved_at"] - r["created_at"] for r in rows if r["resolved_at"]]
    f = lambda v: round(sum(v) / len(v), 1) if v else None
    return {"incident_count": len(rows), "mttd_s": f(mttd), "mttd_samples": len(mttd), "mttr_s": f(mttr), "mttr_samples": len(mttr),
            "note": "MTTD = incident creation wall-clock minus first anomalous bucket; meaningful only for live ingestion. MTTR = resolution minus creation."}

def compute(since=None, until=None, hours=24):
    ref = A.ref_time()
    if ref is None: return {"period": None, "services": [], "missing_data": ["no telemetry ingested"], "incidents": _incident_stats(0, 4e9)}
    until = float(until) if until else ref + 1; since = float(since) if since else until - hours * 3600
    d = A.collect(since, until); per, missing = defaultdict(lambda: {"n": 0, "err": 0, "fast": 0}), []
    cfg = {}
    for s in d["spans"]:
        if not A.is_server(s): continue
        c = cfg.setdefault(s["service"], slo_for(s["service"])); p = per[s["service"]]
        p["n"] += 1; p["err"] += A.span_failed(s); p["fast"] += s["dur_ms"] <= c["latency_ms"]
    S, meth, _ = A.build_series(d); res = []
    for sv in sorted(set(per) | {k for k, v in meth.items() if "error_rate" in v}):
        c = cfg.get(sv) or slo_for(sv); gaps = []
        if sv in per and per[sv]["n"]:
            p = per[sv]; n, err = p["n"], p["err"]; src = "server_spans"; lat_sli = p["fast"] / n * 100
        else:
            ers = S[sv]["error_rate"]; n = sum(v[1] for v in ers.values()); err = sum(v[0] * v[1] for v in ers.values()); src = meth[sv]["error_rate"]
            lat_sli = None; gaps.append("no spans: latency SLO not measurable"); gaps.append(f"availability from {src} (approximation)") if src == "log_ratio" else None
        if not n: res.append({"service": sv, "status": "no_data", "missing_data": ["no request samples"]}); continue
        avail = (1 - err / n) * 100; er_pct = err / n * 100
        def budget(sli, target): return None if target >= 100 else round(max(0.0, (100 - sli)) / (100 - target) * 100, 1)
        res.append({"service": sv, "samples": n, "failed": round(err), "sli_source": src, "targets": c,
                    "availability": {"sli": round(avail, 4), "target": c["availability"], "met": avail >= c["availability"], "error_budget_consumed_pct": budget(avail, c["availability"])},
                    "latency": None if lat_sli is None else {"sli": round(lat_sli, 4), "threshold_ms": c["latency_ms"], "target": c["latency_target"], "met": lat_sli >= c["latency_target"], "error_budget_consumed_pct": budget(lat_sli, c["latency_target"])},
                    "error_rate": {"pct": round(er_pct, 4), "target_max_pct": c["error_rate_pct"], "met": er_pct <= c["error_rate_pct"], "budget_consumed_pct": round(er_pct / c["error_rate_pct"] * 100, 1) if c["error_rate_pct"] else None},
                    "missing_data": gaps, "status": "met" if avail >= c["availability"] and er_pct <= c["error_rate_pct"] else "breached"})
    first = min([x["ts"] for x in d["spans"]] + [x["ts"] for x in d["logs"]] + [x["ts"] for x in d["metrics"]], default=None)
    if not d["spans"]: missing.append("no traces ingested: latency SLOs unavailable")
    return {"period": {"since": since, "until": until, "first_sample": first, "hours_requested": round((until - since) / 3600, 2),
                       "note": "SLO windows are as long as ingested data allows; a 30-day SLO needs 30 days of telemetry"},
            "services": res, "missing_data": missing, "incidents": _incident_stats(since, until)}
