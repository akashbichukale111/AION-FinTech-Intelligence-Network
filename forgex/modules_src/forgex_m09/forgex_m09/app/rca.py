"""Unified investigation: timeline, error clusters, trace<->log links (only via real trace ids), impact graph, evidence-backed root-cause hypotheses."""
import hashlib, re, time
from collections import Counter, defaultdict
from . import analysis as A
from . import incidents as I
from . import services as SV
from .config import THRESHOLDS as T
from .db import j, lj, one, q, x
from .integrations import code_context

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX = re.compile(r"\b[0-9a-f]{12,}\b", re.I); _NUM = re.compile(r"\b\d+(\.\d+)?\b")
def template(m): return _NUM.sub("<n>", _HEX.sub("<hex>", _UUID.sub("<uuid>", m)))[:200]
def pct(v): return "n/a" if v is None else f"{v*100:.1f}%"

def _ctx(inc):
    w0, w1 = (inc["meta"].get("window") or [None, None])
    if not w0:
        ref = A.ref_time() or time.time(); w0, w1 = ref - 3600, ref + 1
    d = A.collect(w0 - 2700, w1)                      # 45 min extra history gives the statistics a baseline
    an, _ = A.analyze(d)
    an = [a for a in an if a["end"] >= w0 and a["start"] <= w1]
    inw = lambda r: w0 <= r["ts"] < w1
    win = {"logs": [l for l in d["logs"] if inw(l)], "spans": [s for s in d["spans"] if inw(s)], "events": [e for e in d["events"] if inw(e)], "metrics": [m for m in d["metrics"] if inw(m)]}
    pre = {"spans": [s for s in d["spans"] if s["ts"] < w0], "logs": [l for l in d["logs"] if l["ts"] < w0]}
    aff = sorted(set(inc["services"]) | {a["service"] for a in an})
    return {"w": (w0, w1), "all": d, "win": win, "pre": pre, "an": an, "aff": aff}

def error_clusters(c):
    cl = {}
    for l in c["win"]["logs"]:
        if l["level"] in ("ERROR", "FATAL", "WARN"):
            k = (l["service"], l["level"], template(l["message"])); e = cl.setdefault(k, {"service": k[0], "level": k[1], "template": k[2], "count": 0, "first": l["ts"], "last": l["ts"], "example": l["message"], "trace_ids": set()})
            e["count"] += 1; e["last"] = l["ts"]
            if l["trace_id"] and len(e["trace_ids"]) < 3: e["trace_ids"].add(l["trace_id"])
    out = sorted(cl.values(), key=lambda e: -e["count"])[:25]
    for e in out: e["trace_ids"] = sorted(e["trace_ids"])
    return out

def trace_links(c):
    logs = c["win"]["logs"]; with_tid = [l for l in logs if l["trace_id"]]
    bad = {}
    for s in c["win"]["spans"]:
        if A.span_failed(s): bad.setdefault(s["trace_id"], []).append(s)
    by_trace = defaultdict(list)
    for l in with_tid: by_trace[l["trace_id"]].append(l)
    links = [{"trace_id": t, "error_spans": [{"service": s["service"], "name": s["name"], "dur_ms": s["dur_ms"]} for s in sp[:6]],
              "logs": [{"ts": l["ts"], "service": l["service"], "level": l["level"], "message": l["message"]} for l in by_trace.get(t, [])[:8]]}
             for t, sp in list(bad.items())[:15]]
    linked = sum(1 for t in bad if t in by_trace)
    noid = Counter(l["service"] for l in logs if not l["trace_id"] and l["level"] in ("WARN", "ERROR", "FATAL"))
    return {"links": links, "coverage": {"error_traces": len(bad), "error_traces_with_logs": linked, "logs_without_trace_id": sum(noid.values()),
            "services_missing_trace_ids": dict(noid.most_common(5)),
            "note": "Logs/spans are linked only through identical trace_id. Records without ids are never linked by timing." }}

def timeline(c, iid=None):
    t = []
    for a in c["an"]: t.append({"ts": a["start"], "kind": "anomaly", "service": a["service"], "text": f"[{a['severity']}] {a['evidence']}", "source": "telemetry"})
    for e in c["win"]["events"]: t.append({"ts": e["ts"], "kind": e["kind"], "service": e["service"], "text": f"{e['kind']}: " + ", ".join(f"{k}={v}" for k, v in list(e["payload"].items())[:5]), "source": "telemetry"})
    for e in error_clusters(c)[:10]: t.append({"ts": e["first"], "kind": "error_cluster", "service": e["service"], "text": f"first of {e['count']}x '{e['template'][:90]}'", "source": "telemetry"})
    if iid:
        for h in I.history(iid): t.append({"ts": h["ts"], "kind": "incident:" + h["kind"], "service": None, "text": (h["detail"].get("text") or str(h["detail"]))[:300], "source": "incident"})
    return sorted(t, key=lambda e: e["ts"])[:600]

def impact(c, g):
    up, down = SV.adjacency(g); aff = set(c["aff"]); callers = set().union(*[up[s] for s in aff]) - aff if aff else set(); deps = set().union(*[down[s] for s in aff]) - aff if aff else set()
    nodes = [{"id": n, "role": "affected" if n in aff else "upstream-at-risk" if n in callers else "downstream-dependency" if n in deps else None} for n in sorted(aff | callers | deps)]
    ids = {n["id"] for n in nodes}
    return {"nodes": nodes, "edges": [e for e in g["edges"] if e["source"] in ids and e["target"] in ids]}

def _edge_stats(spans):
    idx = {(s["trace_id"], s["span_id"]): s for s in spans}; st = defaultdict(lambda: [0, 0])
    for s in spans:
        a = s["attrs"]; p = idx.get((s["trace_id"], s["parent_id"])) if s["parent_id"] else None
        tgt = ("db:" + str(a.get("db.name") or a["db.system"]).lower()) if a.get("db.system") else ("ext:" + str(a["peer.service"]).lower()) if a.get("peer.service") and a.get("peer.external") else (s["service"] if p and p["service"] != s["service"] else None)
        src = s["service"] if (a.get("db.system") or a.get("peer.external")) else (p["service"] if p else None)
        if tgt and src: st[(src, tgt)][0] += 1; st[(src, tgt)][1] += A.span_failed(s)
    return st

def _rate(spans, svc):
    r = [A.span_failed(s) for s in spans if s["service"] == svc and A.is_server(s)]; return (sum(r) / len(r), len(r)) if r else (None, 0)

PATTERN_H = {
    "webhook_delay": ("Webhook processing degraded", "Webhook consumers are retrying/delaying deliveries, typically from a slow consumer, saturated downstream, or backlog.", ["Backlog caused by an upstream outage (not a webhook-service defect)", "Provider-side delivery delay"], ["Check consumer lag and retry counts", "Inspect webhook handler latency and downstream dependencies", "Verify idempotency before replaying events"]),
    "duplicate_event": ("Duplicate event processing", "Repeated deliveries/retries are being processed more than once; idempotency keys may be missing, expired or failing.", ["Provider retry storm after earlier timeouts", "Consumer ack failures causing redelivery"], ["Audit idempotency-key checks in handlers", "Compare event ids processed vs unique ids", "Check ledger for double postings (read-only)"]),
    "ledger_error": ("Ledger service errors", "Ledger writes/validations are failing or imbalanced.", ["Ledger database saturation", "Schema/contract change in a recent deploy"], ["Inspect ledger error templates and DB health", "Run read-only reconciliation checks (Module 06 synthetic simulation first)"]),
    "settlement_delay": ("Settlement processing delay", "Settlement jobs are late or stuck.", ["Upstream payment failures reduced settleable volume", "Scheduler/queue backlog"], ["Check settlement job schedule and queue depth", "Verify provider settlement file availability"]),
    "reconciliation_degraded": ("Reconciliation degradation", "Reconciliation is lagging/mismatching.", ["Delayed ledger or provider data", "Clock/timezone cut-off drift"], ["Compare reconciliation inputs freshness", "Review mismatch categories (Module 03 AURA outputs if available)"]),
    "refund_failure": ("Refund service failures", "Refund requests are failing.", ["Provider API rejecting refunds", "Ledger dependency failure"], ["Inspect refund error templates", "Check provider status and credentials rotation (do not alter provider config automatically)"]),
    "capture_failure": ("Payment capture failures", "Capture calls are failing or being declined at the service boundary.", ["Provider outage/latency", "Timeouts masking successful captures (risk of duplicates)"], ["Correlate with provider edge error rate", "Check for orphaned authorizations before any retry"]),
    "authorization_failure": ("Payment authorization failures", "Authorization calls are failing.", ["Provider degradation", "Credential or configuration drift"], ["Compare decline vs error classes", "Check provider status page and recent config changes"]),
    "queue_backlog": ("Transaction/event queue backlog", "Messages are accumulating faster than consumers drain them.", ["Consumer crash loop", "Burst traffic"], ["Check consumer health and lag", "Scale or throttle only after confirming idempotency"]),
}

def hypotheses(inc, c, g):
    w0, w1 = c["w"]; an = c["an"]; aff = c["aff"]; up, down = SV.adjacency(g); out, facts = [], []
    near = set(aff) | set().union(*[up[s] | down[s] for s in aff]) if aff else set()
    inc_ev = lambda t, text, **k: {"type": t, "text": text, **k}
    def H(rule, target, title, expl, ev, conf, svcs, alts, steps):
        n = len({e["type"] for e in ev}); conf = min(0.9, max(0.1, conf + 0.05 * (n - 1)))
        out.append({"id": hashlib.sha1(f"{inc['id']}|{rule}|{target}".encode()).hexdigest()[:10], "rule": rule, "status": "hypothesis", "title": title, "explanation": expl, "evidence": ev,
                    "confidence": round(conf, 2), "confidence_label": "high" if conf >= .7 else "medium" if conf >= .45 else "low", "affected_services": sorted(set(svcs)),
                    "alternatives": alts, "next_steps": steps, "code_context": code_context(sorted(set(svcs))[0]) if svcs else None})
    for a in an:
        if a["signal"] in ("error_rate", "latency_p95", "availability"): facts.append(f"MEASURED: {a['service']} {a['evidence']}")
    facts.append(f"MEASURED: {sum(1 for l in c['win']['logs'] if l['level'] in ('ERROR','FATAL'))} ERROR/FATAL log lines and {sum(1 for s in c['win']['spans'] if A.span_failed(s))} failed spans in the incident window")
    # 1. deployment regression
    for e in (e for e in c["all"]["events"] if e["kind"] == "deploy"):
        first = min([a["start"] for a in an if a["service"] in near and a["signal"] in ("error_rate", "latency_p95") and e["ts"] <= a["start"] <= e["ts"] + T["deploy_window_s"]] or [None], key=lambda v: v or 1e18)
        if first is None or e["service"] not in near: continue
        facts.append(f"MEASURED: deploy {e['payload'].get('version','?')} of {e['service']} at ts={int(e['ts'])}")
        b = _rate([s for s in c["all"]["spans"] if e["ts"] - 900 <= s["ts"] < e["ts"]], e["service"]); a_ = _rate([s for s in c["all"]["spans"] if e["ts"] <= s["ts"] < e["ts"] + 900], e["service"])
        ev = [inc_ev("deploy", f"deploy {e['payload'].get('version','?')} ({e['payload'].get('commit','no commit id')}) on {e['service']}", ts=e["ts"]), inc_ev("anomaly", f"first related anomaly {int(first-e['ts'])}s after deploy")]
        conf = 0.5 if e["service"] in aff else 0.35
        if b[0] is not None and a_[0] is not None:
            ev.append(inc_ev("metric", f"{e['service']} server-span error rate {pct(b[0])} (n={b[1]}) in 15 min before -> {pct(a_[0])} (n={a_[1]}) in 15 min after"))
            if a_[0] >= max(0.05, 3 * b[0]): conf += 0.15
        ch = [d for d in SV.dependency_changes(g, c["all"]) if d["deploy"]["service"] == e["service"]]
        if ch: ev.append(inc_ev("dependency", "new dependency edges after deploy: " + ", ".join(ch[0]["new_edges"][:4]))); conf += 0.1
        H("deployment", e["service"], f"Regression introduced by deploy {e['payload'].get('version','?')} of {e['service']}", "Anomalies began shortly after a deployment to this (or an adjacent) service. Timing is suggestive, not conclusive.", ev, conf, [e["service"]] + aff,
          ["Coincident traffic or dependency failure unrelated to the deploy", "Config/feature-flag change not captured as a deploy event"], ["Diff the release against the previous version", "Check feature flags and config changes", "Evaluate rollback with the service owner (manual decision)"])
    # 2. failing dependency edges (deepest erroring edge = candidate)
    cur, base = _edge_stats(c["win"]["spans"]), _edge_stats(c["pre"]["spans"])
    bad = {k: v for k, v in cur.items() if v[0] >= 5 and v[1] / v[0] >= 0.2}
    for (s_, t_), (n, e_) in bad.items():
        if any(k[0] == t_ for k in bad if k != (s_, t_)): continue          # a deeper dependency fails -> that one is the candidate
        bn, be = base.get((s_, t_), (0, 0)); ev = [inc_ev("trace", f"{e_}/{n} calls {s_}->{t_} failed ({e_/n*100:.0f}%) in window" + (f"; baseline {be}/{bn}" if bn else "; no baseline calls"))]
        ta = [a for a in an if a["service"] == t_]
        if ta: ev.append(inc_ev("anomaly", f"{t_} anomaly: {ta[0]['evidence']}"))
        kind = "external provider" if t_.startswith("ext:") else "database" if t_.startswith("db:") else "dependency"
        conf = 0.4 + (0.15 if n >= 20 else 0) + (0.1 if bn and be / bn < 0.05 else 0)
        H("dependency", f"{s_}>{t_}", f"{kind.capitalize()} {t_} is failing for callers ({s_})", f"The deepest erroring edge in the observed call graph is {s_} -> {t_}; upstream errors are consistent with propagation from it.", ev, conf, [s_, t_] + aff,
          [f"Failure originates inside {s_} (e.g. its client/pool config) rather than {t_}", "Network path/DNS issue between the two"], [f"Check {t_} health, saturation and recent changes", f"Inspect {s_} client timeouts/retries/pool settings", "Review matching traces via trace-to-log links"])
    # 3. DB pool / resource / queue (signals on near services)
    for sig, title, steps in (("db_pool", "Database connection pool exhaustion", ["Compare pool size vs concurrency", "Look for leaked connections/long transactions", "Check recent pool config changes"]),
                              ("cpu", "CPU saturation", ["Profile hot paths", "Check autoscaling and noisy neighbours"]), ("memory", "Memory pressure", ["Check for leaks/GC pauses", "Compare against deploy times"]),
                              ("queue", "Queue backlog growth", ["Check consumer health and lag", "Inspect poison messages"])):
        for a in [a for a in an if a["signal"] == sig and a["service"] in near] + [a for a in an if sig == "db_pool" and a["signal"] == "db_pool_exhausted" and a["service"] in near]:
            ev = [inc_ev("metric", f"{a['service']}: {a['evidence']}", ts=a["start"])]
            lc = [x_ for x_ in an if x_["service"] == a["service"] and x_["signal"] in ("timeout", "db_pool_exhausted") and x_ is not a]
            ev += [inc_ev("log", f"{x_['service']}: {x_['evidence']}") for x_ in lc[:2]]
            if any(o["rule"] == "dependency" and any(s.startswith("db:") for s in o["affected_services"]) for o in out) and sig == "db_pool": ev.append(inc_ev("trace", "database edge is also erroring in traces"))
            H(sig, a["service"] + a["signal"], f"{title} on {a['service']}", f"{a['signal']} crossed its threshold ({a['method']}); this can cause timeouts and cascading errors to callers.", ev, 0.4 + (0.1 if a["severity"] == "high" else 0), [a["service"]] + aff,
              ["Symptom rather than cause (e.g. slow queries or retry storm upstream)", "Traffic surge"], steps)
    # 4. FinTech log-pattern hypotheses
    cnt = defaultdict(Counter)
    for a in an:
        if a["signal"] in PATTERN_H: cnt[a["signal"]][a["service"]] += int(a["peak"])
    for name, svs in cnt.items():
        t_, ex, alts, steps = PATTERN_H[name]; tot = sum(svs.values())
        ev = [inc_ev("log", f"{s}: log pattern '{name}' peak {n}/min") for s, n in svs.most_common(3)]
        ext = [k for k in bad if k[1].startswith("ext:")]
        if ext: ev.append(inc_ev("trace", f"external provider edge {ext[0][0]}->{ext[0][1]} also failing"))
        H("fintech:" + name, name, t_, ex, ev, 0.3 + min(tot / 30, .2) + (0.1 if ext else 0), list(svs) + aff, alts, steps)
    out.sort(key=lambda h: -h["confidence"])
    return out[:8], facts[:20]

def investigate(iid, actor):
    inc = I.get(iid); c = _ctx(inc); g = SV.graph(since=c["w"][0] - 3600, until=c["w"][1])
    hyps, facts = hypotheses(inc, c, g)
    tl = trace_links(c)
    body = {"generated_at": time.time(), "window": c["w"], "affected_services": c["aff"], "confirmed_facts": facts, "hypotheses": hyps,
            "error_clusters": error_clusters(c), "trace_links": tl, "impact": impact(c, g), "anomalies": c["an"],
            "related_incidents": [{"id": r["id"], "title": r["title"], "status": r["status"]} for r in I.listing() if r["id"] != iid and set(r["services"]) & set(c["aff"])][:10],
            "limitations": (["No hypothesis is supported by the available telemetry."] if not hyps else []) + ([f"{tl['coverage']['logs_without_trace_id']} WARN/ERROR logs lack trace_id - not linked."] if tl["coverage"]["logs_without_trace_id"] else [])}
    x("DELETE FROM hypotheses WHERE incident_id=?", (iid,)); x("INSERT INTO hypotheses(id,incident_id,generated_at,body) VALUES(?,?,?,?)", ("H-" + iid, iid, time.time(), j(body)))
    I.log(iid, "investigation", actor, {"hypotheses": len(hyps), "facts": len(facts)})
    if inc["status"] == "OPEN": I.transition(iid, "INVESTIGATING", actor, "investigation started")
    return body

def stored(iid):
    I.get(iid); r = one("SELECT body FROM hypotheses WHERE incident_id=?", (iid,)); return lj(r["body"]) if r else None

def full_timeline(iid):
    inc = I.get(iid); return timeline(_ctx(inc), iid)
