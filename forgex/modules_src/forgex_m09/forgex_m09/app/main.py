"""FORGEX Module 09 - Production Incident & Observability Intelligence Engine (FastAPI)."""
import time
from typing import Any
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import os
from . import analysis as A, incidents as I, ingest as G, rca, remediation as R, services as SV, slo, synthetic
from .config import FINTECH_RE, MAX_BODY
from .db import lj, one, q, x
from .security import audit, need, safe_text

app = FastAPI(title="FORGEX M09 - Observability & Incident Intelligence", version="1.0")
R_, W_, IN_, AD_ = need("read"), need("write"), need("ingest"), need("admin")

@app.middleware("http")
async def limit_body(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY: return JSONResponse({"detail": "request body too large"}, 413)
    return await call_next(request)

for exc, code in ((KeyError, 404), (ValueError, 422), (PermissionError, 409)):
    app.add_exception_handler(exc, lambda r, e, c=code: JSONResponse({"detail": str(e.args[0]) if e.args else "error"}, c))

def _src(source, hist):
    if source not in ("real", "imported"): raise HTTPException(422, "source must be 'real' or 'imported' (synthetic data only via the admin seed endpoint)")
    return source, bool(hist and source == "imported")
V = "/api/v1"

# ---------- ingestion ----------
def _ing(fn, kind):
    def ep(payload: Any = Body(...), source: str = "real", allow_historical: bool = False, actor: str = Depends(IN_)):
        s, h = _src(source, allow_historical); r = fn(payload, s, h); audit(actor, f"ingest.{kind}", "", {"accepted": r["accepted"], "rejected": r["rejected"], "source": s}); return r
    return ep
for kind, fn in (("logs", G.ingest_logs), ("metrics", G.ingest_metrics), ("traces", G.ingest_traces), ("events", G.ingest_events)):
    app.post(f"{V}/observability/ingest/{kind}")(_ing(fn, kind))

# ---------- query ----------
@app.get(V + "/observability/overview")
def overview(_=Depends(R_)):
    ref = A.ref_time(); inv = SV.inventory(30) if ref else []
    return {"reference_time": ref, "services": len(inv), "health": {h: sum(1 for s in inv if s["health"] == h) for h in ("healthy", "degraded", "down", "unknown")},
            "incidents": {s: (one("SELECT COUNT(*) c FROM incidents WHERE status=?", (s,)) or {"c": 0})["c"] for s in I.STATES},
            "data_sources": {t: {r["source"]: r["c"] for r in q(f"SELECT source, COUNT(*) c FROM {t} GROUP BY source")} for t in ("logs", "metrics", "spans", "events")},
            "recent_anomalies": len(A.detect(minutes=60)["anomalies"]) if ref else 0}

@app.get(V + "/observability/services")
def services_(_=Depends(R_)): return {"services": SV.inventory(30)}

@app.get(V + "/observability/services/{service_id}")
def service_detail(service_id: str, _=Depends(R_)):
    inv = next((s for s in SV.inventory(30) if s["id"] == service_id), None)
    if not inv: raise KeyError("service not found")
    g = SV.graph(); up, down = SV.adjacency(g)
    return {**inv, "upstream": sorted(up[service_id]), "downstream": sorted(down[service_id]), "slo": next((s for s in slo.compute()["services"] if s["service"] == service_id), None),
            "recent_deploys": [{**e, "payload": lj(e["payload"], {})} for e in q("SELECT ts,payload,source FROM events WHERE kind='deploy' AND service=? ORDER BY ts DESC LIMIT 5", (service_id,))],
            "recent_errors": q("SELECT ts,level,message,trace_id,source FROM logs WHERE service=? AND level IN ('ERROR','FATAL') ORDER BY ts DESC LIMIT 10", (service_id,))}

@app.get(V + "/observability/dependencies")
def dependencies(_=Depends(R_)):
    g = SV.graph(); return {**g, "critical_paths": SV.critical_paths(g), "changes_after_deploys": SV.dependency_changes(g),
                            "legend": "observed = seen in traces with real parent/child ids; inferred = declared in repository architecture (Module 01) only"}

@app.get(V + "/observability/anomalies")
def anomalies(since: float = None, until: float = None, minutes: int = Query(120, le=1440), _=Depends(R_)): return A.detect(since, until, minutes)

@app.post(V + "/observability/detect")
def detect(since: float = None, until: float = None, minutes: int = Query(120, le=1440), actor=Depends(W_)): return I.detect_and_open(actor, since, until, minutes)

@app.get(V + "/observability/logs")
def logs_(service: str = None, level: str = None, text: str = None, trace_id: str = None, limit: int = Query(100, le=500), _=Depends(R_)):
    w, a = ["1=1"], []
    for col, v in (("service", service), ("level", level and level.upper()), ("trace_id", trace_id)):
        if v: w.append(f"{col}=?"); a.append(v)
    if text: w.append("message LIKE ? ESCAPE '\\'"); a.append("%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    return {"logs": q(f"SELECT ts,service,level,message,trace_id,request_id,source FROM logs WHERE {' AND '.join(w)} ORDER BY ts DESC LIMIT ?", (*a, limit))}

@app.get(V + "/observability/metrics")
def metrics_(service: str = None, name: str = None, limit: int = Query(500, le=5000), _=Depends(R_)):
    w, a = ["1=1"], []
    for col, v in (("service", service), ("name", name)):
        if v: w.append(f"{col}=?"); a.append(v)
    return {"metrics": q(f"SELECT ts,service,name,value,labels,source FROM metrics WHERE {' AND '.join(w)} ORDER BY ts DESC LIMIT ?", (*a, limit))}

@app.get(V + "/observability/traces")
def traces_(service: str = None, errors_only: bool = False, limit: int = Query(50, le=200), _=Depends(R_)):
    h = "HAVING SUM(status='error')>0" if errors_only else ""
    w, a = ("WHERE service=?", [service]) if service else ("", [])
    return {"traces": q(f"SELECT trace_id, MIN(ts) ts, COUNT(*) spans, SUM(status='error') errors, MAX(dur_ms) max_ms, GROUP_CONCAT(DISTINCT service) services, MIN(source) source FROM spans {w} GROUP BY trace_id {h} ORDER BY ts DESC LIMIT ?", (*a, limit))}

@app.get(V + "/observability/traces/{trace_id}")
def trace_detail(trace_id: str, _=Depends(R_)):
    sp = q("SELECT ts,span_id,parent_id,service,name,dur_ms,status,attrs,source FROM spans WHERE trace_id=? ORDER BY ts", (trace_id,))
    if not sp: raise KeyError("trace not found")
    for s in sp: s["attrs"] = lj(s["attrs"], {})
    return {"trace_id": trace_id, "spans": sp, "logs": q("SELECT ts,service,level,message,source FROM logs WHERE trace_id=? ORDER BY ts", (trace_id,)), "correlation": "by identical trace_id only"}

@app.get(V + "/observability/slos")
def slos(since: float = None, until: float = None, hours: float = Query(24, le=720), _=Depends(R_)): return slo.compute(since, until, hours)

@app.get(V + "/observability/fintech")
def fintech(_=Depends(R_)):
    d = A.detect(minutes=120); inv = [s for s in SV.inventory(30) if s["critical"] and s["kind"] == "service"]
    pats = [a for a in d["anomalies"] if a["signal"] in rca.PATTERN_H or a["signal"] in ("timeout", "db_pool_exhausted")]
    mp = {"payment": "payment|checkout", "webhook": "webhook", "refund": "refund", "ledger": "ledger", "settlement": "settle", "reconciliation": "recon"}
    import re
    return {"categories": {k: {"services": [s for s in inv if re.search(rx, s["id"])], "findings": [a for a in pats if re.search(rx, a["service"])]} for k, rx in mp.items()},
            "integration": {"module_06": __import__("app.integrations", fromlist=["x"]).call(6, "transactions"), "module_07": __import__("app.integrations", fromlist=["x"]).call(7, "api_webhook_findings")},
            "safety": "read-only analysis; this system never initiates transactions or alters provider configuration"}

@app.post(V + "/observability/synthetic")
def seed(minutes: int = Query(60, ge=10, le=240), actor=Depends(AD_)):
    r = synthetic.generate(minutes); audit(actor, "synthetic.seed", "", r["ingested"]); return r

@app.delete(V + "/observability/synthetic")
def purge(actor=Depends(AD_)):
    n = {t: x(f"DELETE FROM {t} WHERE source='synthetic'").rowcount for t in ("logs", "metrics", "spans", "events")}; audit(actor, "synthetic.purge", "", n); return {"deleted": n}

# ---------- incidents ----------
@app.post(V + "/incidents", status_code=201)
def create_incident(body: dict = Body(...), actor=Depends(W_)):
    return I.create(body.get("title"), body.get("services"), actor, body.get("severity"), body.get("summary", ""), body.get("owner"), body.get("started_at"))

@app.get(V + "/incidents")
def list_incidents(status: str = None, severity: str = None, service: str = None, _=Depends(R_)): return {"incidents": I.listing(status, severity, service)}

@app.get(V + "/incidents/{iid}")
def get_incident(iid: str, _=Depends(R_)): return {**I.get(iid), "history": I.history(iid), "plans": R.plans(iid), "investigation": rca.stored(iid)}

@app.post(V + "/incidents/{iid}/investigate")
def investigate(iid: str, actor=Depends(W_)): return rca.investigate(iid, actor)

@app.get(V + "/incidents/{iid}/timeline")
def timeline(iid: str, _=Depends(R_)): return {"timeline": rca.full_timeline(iid)}

@app.get(V + "/incidents/{iid}/root-causes")
def root_causes(iid: str, _=Depends(R_)):
    s = rca.stored(iid)
    if not s: raise KeyError("no investigation yet - POST /investigate first")
    return {k: s[k] for k in ("generated_at", "confirmed_facts", "hypotheses", "limitations")}

@app.post(V + "/incidents/{iid}/create-plan")
def create_plan(iid: str, body: dict = Body(default={}), actor=Depends(W_)): return R.create_plan(iid, actor, (body or {}).get("hypothesis_id"))

@app.post(V + "/incidents/{iid}/plans/{pid}/approve")
def approve_plan(iid: str, pid: str, body: dict = Body(default={}), actor=Depends(W_)): return R.approve(iid, pid, actor, str((body or {}).get("note", "")))

@app.post(V + "/incidents/{iid}/status")
def set_status(iid: str, body: dict = Body(...), actor=Depends(W_)): return I.transition(iid, body.get("status"), actor, body.get("note", ""), body.get("pir"))

@app.patch(V + "/incidents/{iid}")
def patch_incident(iid: str, body: dict = Body(...), actor=Depends(W_)): return I.set_owner(iid, body.get("owner"), actor)

@app.post(V + "/incidents/{iid}/evidence")
def evidence(iid: str, body: dict = Body(...), actor=Depends(W_)): return I.add_evidence(iid, body.get("text", ""), actor, body.get("ref"))

@app.get(V + "/incidents/{iid}/report")
def report(iid: str, format: str = "json", _=Depends(R_)):
    inc = I.get(iid); inv = rca.stored(iid) or {}; hist = I.history(iid)
    dur = (inc["resolved_at"] - inc["started_at"]) if inc["resolved_at"] and inc["started_at"] else None
    rep = {"incident": inc, "duration_s": dur, "hypotheses": inv.get("hypotheses", []), "confirmed_facts": inv.get("confirmed_facts", []), "plans": R.plans(iid), "history": hist, "post_incident_review": inc["pir"]}
    if format != "md": return rep
    md = [f"# {inc['id']} - {inc['title']}", f"Severity **{inc['severity']}** | Status **{inc['status']}** | Owner {inc['owner'] or 'unassigned'} | Data source: {inc['source']}", f"Services: {', '.join(inc['services'])}", "", "## Confirmed facts"] + [f"- {f}" for f in rep["confirmed_facts"]] + ["", "## Hypotheses (unconfirmed)"]
    md += [f"- ({h['confidence']}) **{h['title']}** - {h['explanation']}" for h in rep["hypotheses"]] + ["", "## History"] + [f"- {time.strftime('%H:%M:%S', time.gmtime(h['ts']))} {h['kind']} by {h['actor']}" for h in hist]
    if inc["pir"]: md += ["", "## Post-incident review", f"{inc['pir'].get('summary')}", f"Root cause: {inc['pir'].get('root_cause')}", f"Impact: {inc['pir'].get('impact')}"]
    return PlainTextResponse("\n".join(md))

@app.get(V + "/audit")
def audit_log(limit: int = Query(100, le=500), _=Depends(AD_)): return {"audit": q("SELECT ts,actor,action,target,detail FROM audit ORDER BY id DESC LIMIT ?", (limit,))}

@app.get("/health")
def health(): return {"status": "ok", "module": 9}

_static = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_static):
    app.mount("/ui", StaticFiles(directory=_static, html=True), name="ui")
    @app.get("/", include_in_schema=False)
    def root(): return RedirectResponse("/ui/")
