"""Metrics analyzer + statistical anomaly engine (robust z-score / MAD + configurable thresholds) + pattern detector.
An anomaly is NOT an incident: promotion happens in incidents.detect_and_open()."""
import hashlib, json, math, re, statistics, time
from collections import defaultdict
from .config import THRESHOLDS as T
from .db import lj, q

B = int(T["bucket_s"])
PATTERNS = {  # name -> (regex, applies_to_all_levels)
    "timeout": (r"time(d)?[ -]?out|deadline exceeded|ETIMEDOUT", False),
    "db_pool_exhausted": (r"connection pool|too many connections|pool exhausted|could not (obtain|acquire) connection", False),
    "http_5xx": (r"\b(HTTP )?5\d\d\b.*(error|fail|unavailable)", False),
    "capture_failure": (r"capture.*(fail|declin|error)", False),
    "authorization_failure": (r"authori[sz]ation.*(fail|declin|error)", False),
    "webhook_delay": (r"webhook.*(delay|late|backlog|retry|fail|timeout)", False),
    "duplicate_event": (r"duplicate (event|webhook|delivery)|already processed|idempotency", True),
    "refund_failure": (r"refund.*(fail|error|reject)", False),
    "ledger_error": (r"ledger.*(error|imbalance|mismatch|fail)|unbalanced", False),
    "settlement_delay": (r"settlement.*(delay|late|stuck|fail|overdue)", False),
    "reconciliation_degraded": (r"reconcil.*(fail|lag|mismatch|degrad|slow)", False),
    "queue_backlog": (r"(queue|backlog).*(grow|backlog|lag|depth|full)|consumer lag", False),
}
_PRX = {k: re.compile(v[0], re.I) for k, v in PATTERNS.items()}
GAUGES = {"cpu_percent": "cpu", "process_cpu_percent": "cpu", "memory_percent": "memory", "mem_percent": "memory",
          "queue_depth": "queue", "queue_length": "queue", "db_pool_utilization": "db_pool", "latency_p95_ms": "latency_p95", "up": "up"}
SIGNALS = {  # signal -> (threshold key, z-test?)
    "error_rate": ("error_rate", True), "latency_p95": ("latency_p95_ms", True), "cpu": ("cpu", True),
    "memory": ("memory", True), "db_pool": ("db_pool", True), "queue": ("queue", True),
}

def ref_time():
    r = q("SELECT MAX(m) m FROM (SELECT MAX(ts) m FROM logs UNION ALL SELECT MAX(ts) FROM spans UNION ALL SELECT MAX(ts) FROM metrics UNION ALL SELECT MAX(ts) FROM events)")
    return r[0]["m"]

def window(since=None, until=None, minutes=60):
    ref = ref_time()
    until = float(until) if until else ((ref + 1) if ref else time.time())
    return (float(since) if since else until - minutes * 60), until

def collect(since, until):
    spans = q("SELECT ts,trace_id,span_id,parent_id,service,name,dur_ms,status,attrs,source FROM spans WHERE ts>=? AND ts<?", (since, until))
    for s in spans: s["attrs"] = lj(s["attrs"], {})
    metrics = q("SELECT ts,service,name,value,labels FROM metrics WHERE ts>=? AND ts<? ORDER BY ts", (since, until))
    for m in metrics: m["labels"] = lj(m["labels"], {})
    events = q("SELECT ts,kind,service,payload,source FROM events WHERE ts>=? AND ts<? ORDER BY ts", (since, until))
    for e in events: e["payload"] = lj(e["payload"], {})
    return {"logs": q("SELECT ts,service,level,message,trace_id,source FROM logs WHERE ts>=? AND ts<? ORDER BY ts", (since, until)),
            "spans": spans, "metrics": metrics, "events": events, "since": since, "until": until}

def is_server(s):
    a = s["attrs"]; return not (a.get("db.system") or a.get("peer.service") or a.get("messaging.system") or a.get("span.kind") == "client")

def span_failed(s):
    c = s["attrs"].get("http.status_code")
    return s["status"] == "error" or (isinstance(c, (int, float)) and c >= 500)

def pctl(v, p):
    v = sorted(v); return v[min(len(v) - 1, math.ceil(p * len(v)) - 1)] if v else None

def build_series(data):
    """-> S[service][signal] = {bucket: (value, n)}, meth[service][signal] = method, pat[service][pattern] = {bucket: count}"""
    S, meth, pat = defaultdict(lambda: defaultdict(dict)), defaultdict(dict), defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    req = defaultdict(lambda: defaultdict(lambda: [[], 0]))
    for s in data["spans"]:
        if not is_server(s): continue
        r = req[s["service"]][int(s["ts"] // B) * B]; r[0].append(s["dur_ms"]); r[1] += span_failed(s)
    for sv, bs in req.items():
        for b, (d, e) in bs.items():
            S[sv]["error_rate"][b] = (e / len(d), len(d)); S[sv]["latency_p95"][b] = (pctl(d, .95), len(d))
        meth[sv]["error_rate"] = meth[sv]["latency_p95"] = "server_spans"
    # counters (http_requests_total) for services without spans
    ctr, last, gauge = defaultdict(list), {}, defaultdict(dict)
    for m in data["metrics"]:
        sv, n = m["service"], m["name"]
        if n == "http_requests_total":
            st = str(m["labels"].get("status", m["labels"].get("code", "")))
            ctr[(sv, json.dumps({k: v for k, v in m["labels"].items() if k not in ("status", "code")}, sort_keys=True), st)].append((m["ts"], m["value"]))
        elif n in GAUGES: gauge[(sv, GAUGES[n])][int(m["ts"] // B) * B] = m["value"]
        elif n in ("db_pool_in_use", "db_pool_max"): last[(sv, n, int(m["ts"] // B) * B)] = m["value"]
    tot, err = defaultdict(float), defaultdict(float)
    for (sv, _, st), pts in ctr.items():
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            d = max(v1 - v0, 0); b = int(t1 // B) * B; tot[(sv, b)] += d; err[(sv, b)] += d if st.startswith("5") else 0
    for (sv, b), n in tot.items():
        if n > 0 and "error_rate" not in meth[sv]:
            S[sv]["error_rate"][b] = (err[(sv, b)] / n, int(n)); meth[sv]["error_rate"] = "counter_delta"
    for (sv, n, b), v in list(last.items()):
        if n == "db_pool_in_use" and (sv, "db_pool_max", b) in last and last[(sv, "db_pool_max", b)] > 0: gauge[(sv, "db_pool")][b] = v / last[(sv, "db_pool_max", b)]
    for (sv, sig), bs in gauge.items():
        for b, v in bs.items(): S[sv][sig][b] = (v, 1)
        meth[sv].setdefault(sig, "gauge")
    # logs: error ratio fallback + pattern counts
    lg = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for l in data["logs"]:
        b = int(l["ts"] // B) * B; r = lg[l["service"]][b]; r[0] += 1; r[1] += l["level"] in ("ERROR", "FATAL")
        for k, rx in _PRX.items():
            if (l["level"] in ("WARN", "ERROR", "FATAL") or PATTERNS[k][1]) and rx.search(l["message"]): pat[l["service"]][k][b] += 1
    for sv, bs in lg.items():
        if "error_rate" not in meth[sv]:
            for b, (n, e) in bs.items(): S[sv]["error_rate"][b] = (e / n, n)
            meth[sv]["error_rate"] = "log_ratio"
    return S, meth, pat

def find_runs(points, thr, z, min_n=0):
    """points [(bucket, value, n)] -> (runs, baseline, method). Anomalous = value>=thr AND robust z>=z (needs >=min_points samples)."""
    points = sorted(points); vals = [p[1] for p in points]
    if len(vals) >= T["min_points"]:
        med = statistics.median(vals); mad = statistics.median([abs(v - med) for v in vals]) * 1.4826
        scale = max(mad, abs(med) * 0.1, abs(thr) * 0.05, 1e-9); method = "robust_z"
        flagged = [(b, v, (v - med) / scale) for b, v, n in points if n >= min_n and v >= thr and (v - med) / scale >= z]
    else:
        method = "threshold_only"; flagged = [(b, v, None) for b, v, n in points if n >= min_n and v >= thr]
    fb = {f[0] for f in flagged}; rest = [v for b, v, _ in points if b not in fb]
    base = statistics.median(rest) if rest else (statistics.median(vals) if vals else None)
    runs = []
    for b, v, sc in flagged:
        if runs and b - runs[-1]["last"] <= 2 * B:
            r = runs[-1]; r["last"] = b; r["peak"] = max(r["peak"], v); r["n"] += 1; r["score"] = max(r["score"] or 0, sc or 0) or None
        else: runs.append({"first": b, "last": b, "peak": v, "n": 1, "score": sc})
    return runs, base, method

def _fmt(sig, v):
    if v is None: return "n/a"
    return f"{v*100:.1f}%" if sig in ("error_rate", "db_pool") else (f"{v:.0f} ms" if sig == "latency_p95" else f"{v:.1f}")

def _mk(service, signal, run, base, method, thr, ev):
    ratio = run["peak"] / thr if thr else 1
    sev = "high" if ratio >= 3 or run["n"] >= 5 else ("medium" if ratio >= 1.5 or run["n"] >= 3 else "low")
    aid = hashlib.sha1(f"{service}|{signal}|{run['first']}".encode()).hexdigest()[:12]
    return {"id": aid, "service": service, "signal": signal, "start": run["first"], "end": run["last"] + B, "peak": run["peak"], "baseline": base,
            "buckets": run["n"], "score": run["score"], "method": method, "severity": sev, "status": "anomaly (unconfirmed)",
            "evidence": ev or f"{signal} peaked at {_fmt(signal, run['peak'])} vs baseline {_fmt(signal, base)} over {run['n']} bucket(s) [{method}]"}

def analyze(data):
    S, meth, pat = build_series(data)
    out, notes = [], []
    for sv, sigs in S.items():
        for sig, (tk, zt) in SIGNALS.items():
            if sig not in sigs: continue
            thr = T[tk]; pts = [(b, v, n) for b, (v, n) in sigs[sig].items() if v is not None]
            if sig == "error_rate": pts = [p for p in pts if round(p[1] * p[2]) >= T["min_errors"] or p[1] < thr]   # one failed request is noise, not an anomaly
            runs, base, method = find_runs(pts, thr, T["z"] if zt else 0, T["min_requests"] if sig == "error_rate" else 0)
            for r in runs: out.append(_mk(sv, sig, r, base, method, thr, None))
            if method == "threshold_only" and pts: notes.append(f"{sv}/{sig}: only {len(pts)} samples (<{T['min_points']}); threshold-only detection")
        if "up" in sigs:
            pts = [(b, 1 - v, 1) for b, (v, _) in sigs["up"].items()]
            for r in find_runs(pts, 0.5, 0)[0]: out.append(_mk(sv, "availability", r, 0, "threshold_only", 0.5, f"'up' metric reported 0 for {r['n']} bucket(s)"))
        if "error_rate" in sigs and meth[sv].get("error_rate") == "log_ratio": notes.append(f"{sv}: no spans/counters; error rate approximated from log-line ratio")
    for sv, ps in pat.items():
        for name, bs in ps.items():
            pts = [(b, c, c) for b, c in bs.items()]
            for r in find_runs(pts, T["pattern_count"], 0)[0]:
                tot = sum(c for b, c in bs.items() if r["first"] <= b <= r["last"])
                out.append(_mk(sv, name, r, 0, "pattern_count", T["pattern_count"], f"log pattern '{name}' matched {tot} WARN/ERROR lines (peak {r['peak']:.0f}/min)"))
    for e in data["events"]:   # health events
        if e["kind"] == "health" and str(e["payload"].get("status", "")).lower() in ("down", "unhealthy", "unavailable"):
            out.append({"id": hashlib.sha1(f"{e['service']}|health|{e['ts']}".encode()).hexdigest()[:12], "service": e["service"], "signal": "availability",
                        "start": e["ts"], "end": e["ts"] + B, "peak": 1, "baseline": 0, "buckets": 1, "score": None, "method": "health_event", "severity": "high",
                        "status": "anomaly (unconfirmed)", "evidence": f"health event reported {e['service']} as {e['payload'].get('status')}"})
    # deployment-related regressions
    for e in (x for x in data["events"] if x["kind"] == "deploy"):
        for a in [a for a in out if a["service"] == e["service"] and a["signal"] in ("error_rate", "latency_p95")]:
            if e["ts"] <= a["start"] <= e["ts"] + T["deploy_window_s"]:
                out.append({**a, "id": hashlib.sha1(f"{a['id']}|deploy".encode()).hexdigest()[:12], "signal": "deploy_regression",
                            "evidence": f"{a['signal']} anomaly began {int(a['start']-e['ts'])}s after deploy {e['payload'].get('version','?')} of {e['service']}; correlation, not proof of causation"})
    if not data["spans"] and not data["logs"] and not data["metrics"]: notes.append("no telemetry in the analysed window")
    return sorted(out, key=lambda a: a["start"]), notes

def detect(since=None, until=None, minutes=120):
    since, until = window(since, until, minutes)
    d = collect(since, until); an, notes = analyze(d)
    return {"window": {"since": since, "until": until}, "anomalies": an, "notes": sorted(set(notes))[:30],
            "coverage": {k: len(d[k]) for k in ("logs", "spans", "metrics", "events")}}
