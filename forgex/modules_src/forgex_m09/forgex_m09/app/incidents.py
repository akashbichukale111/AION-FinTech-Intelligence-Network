"""Incident lifecycle manager + detection promotion (anomalies -> candidate incidents)."""
import hashlib, re, time, uuid
from collections import defaultdict
from . import analysis as A
from . import services as SV
from .config import FINTECH_RE, THRESHOLDS as T
from .db import j, lj, one, q, x
from .ingest import SVC_RE
from .security import audit, safe_text

STATES = ["OPEN", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "RESOLVED", "CLOSED"]
TRANS = {"OPEN": {"INVESTIGATING", "RESOLVED"}, "INVESTIGATING": {"IDENTIFIED", "RESOLVED"}, "IDENTIFIED": {"MITIGATING", "INVESTIGATING", "RESOLVED"},
         "MITIGATING": {"RESOLVED", "IDENTIFIED"}, "RESOLVED": {"CLOSED", "INVESTIGATING"}, "CLOSED": set()}
ACTIVE = ("OPEN", "INVESTIGATING", "IDENTIFIED", "MITIGATING")

def log(iid, kind, actor, detail):
    x("INSERT INTO incident_log(incident_id,ts,kind,actor,detail) VALUES(?,?,?,?,?)", (iid, time.time(), kind, actor, j(detail)))
    x("UPDATE incidents SET updated_at=? WHERE id=?", (time.time(), iid))

def _row(r):
    if not r: return None
    r["services"] = lj(r["services"], []); r["meta"] = lj(r["meta"], {}); r["pir"] = lj(r["pir"])
    return r

def get(iid):
    r = _row(one("SELECT * FROM incidents WHERE id=?", (iid,)))
    if not r: raise KeyError(iid)
    return r

def listing(status=None, severity=None, service=None, limit=100):
    rows = [_row(r) for r in q("SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?", (min(limit, 500),))]
    return [r for r in rows if (not status or r["status"] == status) and (not severity or r["severity"] == severity) and (not service or service in r["services"])]

def classify(anoms, services):
    """Severity rubric (documented): critical FinTech service +2, high anomaly +2 / medium +1, availability +1, >=3 services +1. >=5 SEV1, >=3 SEV2, >=2 SEV3, else SEV4."""
    s = 0
    if any(re.search(FINTECH_RE, v) for v in services): s += 2
    sev = [a["severity"] for a in anoms]; s += 2 if "high" in sev else (1 if "medium" in sev else 0)
    s += any(a["signal"] == "availability" for a in anoms) + (len(services) >= 3)
    return "SEV1" if s >= 5 else "SEV2" if s >= 3 else "SEV3" if s >= 2 else "SEV4"

def _sources(since, until):
    s = set()
    for t in ("logs", "spans", "metrics", "events"):
        s |= {r["source"] for r in q(f"SELECT DISTINCT source FROM {t} WHERE ts>=? AND ts<?", (since, until))}
    return "+".join(sorted(s)) or "none"

def create(title, services, actor, severity=None, summary="", owner=None, started_at=None, origin="manual", anomalies=None, window=None):
    services = sorted({s.strip().lower() for s in services or []})
    if not title or not services or not all(SVC_RE.match(s) for s in services): raise ValueError("title and valid service ids are required")
    anomalies = anomalies or []
    if severity and severity not in ("SEV1", "SEV2", "SEV3", "SEV4"): raise ValueError("severity must be SEV1..SEV4")
    iid, now = "INC-" + uuid.uuid4().hex[:8].upper(), time.time()
    started = started_at or (min(a["start"] for a in anomalies) if anomalies else None)
    w = window or ([started - 600, max([a["end"] for a in anomalies] or [started + 3600]) + 600] if started else None)
    fp = hashlib.sha1("|".join(services + sorted({a["signal"] for a in anomalies})).encode()).hexdigest()[:16]
    meta = {"window": w, "anomalies": anomalies[:50]}
    x("INSERT INTO incidents(id,title,severity,status,services,owner,created_at,updated_at,started_at,origin,source,fingerprint,summary,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (iid, safe_text(title, 200), severity or classify(anomalies, services), "OPEN", j(services), owner, now, now, started, origin,
       _sources(*w) if w else "none", fp, safe_text(summary, 2000), j(meta)))
    log(iid, "created", actor, {"origin": origin, "anomalies": [a["evidence"] for a in anomalies[:10]]})
    audit(actor, "incident.create", iid, {"services": services})
    return get(iid)

def transition(iid, new, actor, note="", pir=None):
    inc = get(iid); new = (new or "").upper()
    if new not in STATES: raise ValueError(f"unknown status; use one of {STATES}")
    if new not in TRANS[inc["status"]]: raise PermissionError(f"invalid transition {inc['status']} -> {new}; allowed: {sorted(TRANS[inc['status']])}")
    now = time.time()
    if new == "CLOSED":
        if not isinstance(pir, dict) or not all(pir.get(k) for k in ("summary", "root_cause", "impact")): raise ValueError("closing requires a post-incident review with summary, root_cause and impact (action_items optional)")
        x("UPDATE incidents SET pir=?, closed_at=? WHERE id=?", (j({**{k: safe_text(str(v), 4000) if not isinstance(v, list) else [safe_text(str(i), 500) for i in v[:30]] for k, v in pir.items()}, "recorded_at": now, "by": actor}), now, iid))
    if new == "RESOLVED": x("UPDATE incidents SET resolved_at=? WHERE id=?", (now, iid))
    if new == "INVESTIGATING" and inc["status"] == "RESOLVED": x("UPDATE incidents SET resolved_at=NULL WHERE id=?", (iid,))
    x("UPDATE incidents SET status=? WHERE id=?", (new, iid))
    log(iid, "status_change", actor, {"from": inc["status"], "to": new, "note": safe_text(note, 1000)}); audit(actor, "incident.status", iid, {"to": new})
    return get(iid)

def set_owner(iid, owner, actor):
    get(iid); owner = safe_text(owner or "", 80) or None
    x("UPDATE incidents SET owner=? WHERE id=?", (owner, iid)); log(iid, "owner_change", actor, {"owner": owner}); audit(actor, "incident.owner", iid, {"owner": owner})
    return get(iid)

def add_evidence(iid, text, actor, ref=None):
    get(iid); log(iid, "evidence", actor, {"text": safe_text(text, 2000), "ref": ref})
    return {"ok": True}

def history(iid):
    get(iid)
    out = q("SELECT ts,kind,actor,detail FROM incident_log WHERE incident_id=? ORDER BY ts,id", (iid,))
    for r in out: r["detail"] = lj(r["detail"], {})
    return out

def detect_and_open(actor, since=None, until=None, minutes=120):
    """Group correlated anomalies (same/adjacent service in the observed graph, starts within link_gap_s) and promote only qualifying groups."""
    res = A.detect(since, until, minutes); an = [a for a in res["anomalies"] if a["signal"] != "deploy_regression"]
    dep = [a for a in res["anomalies"] if a["signal"] == "deploy_regression"]
    g = SV.graph(since=res["window"]["since"] - 3600, until=res["window"]["until"]); up, down = SV.adjacency(g)
    par = list(range(len(an)))
    def f(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    for i, a in enumerate(an):
        for k in range(i):
            b = an[k]
            near = a["service"] == b["service"] or b["service"] in up[a["service"]] | down[a["service"]]
            if near and abs(a["start"] - b["start"]) <= T["link_gap_s"]: par[f(i)] = f(k)
    groups = defaultdict(list)
    for i, a in enumerate(an): groups[f(i)].append(a)
    created, updated, skipped = [], [], []
    for grp in groups.values():
        grp = grp + [d for d in dep if d["service"] in {a["service"] for a in grp}]
        svs = sorted({a["service"] for a in grp}); sigs = {a["signal"] for a in grp}
        core = {"error_rate", "availability", "latency_p95", "deploy_regression"} & sigs
        qualifies = ("high" in {a["severity"] for a in grp} and bool(core)) or len(sigs) >= 3 or (len(sigs) >= 2 and len(svs) >= 2 and bool(core))
        if not qualifies: skipped.append({"services": svs, "signals": sorted(sigs), "reason": "anomaly only - did not meet incident criteria"}); continue
        start = min(a["start"] for a in grp); end = max(a["end"] for a in grp)
        ex = next((i for i in listing() if i["status"] in ACTIVE and set(i["services"]) & set(svs) and abs((i["started_at"] or 0) - start) <= 1800), None)
        if ex:
            log(ex["id"], "anomaly_attached", actor, {"anomalies": [a["evidence"] for a in grp][:10]}); updated.append(ex["id"]); continue
        crit = next((s for s in svs if re.search(FINTECH_RE, s)), svs[0])
        title = f"{'/'.join(sorted(sigs - {'deploy_regression'})[:3])} on {crit}" + (f" (+{len(svs)-1} services)" if len(svs) > 1 else "")
        i = create(title, svs, actor, origin="auto-detected", anomalies=grp, window=[start - 600, end + 600],
                   summary="Auto-detected from correlated anomalies. Status OPEN = unconfirmed until a human investigates.")
        created.append(i["id"])
    audit(actor, "incident.detect", "", {"created": created, "updated": updated})
    return {"created": created, "updated": updated, "anomalies_total": len(res["anomalies"]), "not_promoted": skipped, "notes": res["notes"], "coverage": res["coverage"]}
