"""Service inventory, dependency graph (observed from traces; declared from Module 01 labelled `inferred`), critical paths, deploy-time dependency changes."""
import re
from collections import defaultdict
from . import analysis as A
from .config import FINTECH_RE, THRESHOLDS as T
from .db import q
from .integrations import architecture

def _node_kind(n): return "database" if n.startswith("db:") else "external" if n.startswith("ext:") else "queue" if n.startswith("queue:") else "service"

def graph(since=None, until=None, data=None):
    ref = A.ref_time()
    if data is None:
        until = until or ((ref + 1) if ref else 0); since = since or (until - 24 * 3600)
        data = A.collect(since, until)
    idx = {(s["trace_id"], s["span_id"]): s for s in data["spans"]}
    edges = {}
    def bump(a, b, kind, s):
        e = edges.setdefault((a, b, kind), {"source": a, "target": b, "kind": kind, "calls": 0, "errors": 0, "first_seen": s["ts"], "last_seen": s["ts"], "inferred": False})
        e["calls"] += 1; e["errors"] += A.span_failed(s); e["first_seen"] = min(e["first_seen"], s["ts"]); e["last_seen"] = max(e["last_seen"], s["ts"])
    for s in data["spans"]:
        a = s["attrs"]
        if a.get("db.system"): bump(s["service"], "db:" + str(a.get("db.name") or a["db.system"]).lower(), "database", s)
        elif a.get("messaging.system"): bump(s["service"], "queue:" + str(a.get("messaging.destination") or a["messaging.system"]).lower(), "queue", s)
        elif a.get("peer.service") and a.get("peer.external"): bump(s["service"], "ext:" + str(a["peer.service"]).lower(), "external", s)
        p = idx.get((s["trace_id"], s["parent_id"])) if s["parent_id"] else None
        if p and p["service"] != s["service"]: bump(p["service"], s["service"], "api", s)   # trace correlation ids required: never guessed
    for e in edges.values(): e["evidence"] = f"observed in {e['calls']} spans"
    arch = architecture(); declared = 0
    if arch.get("available"):
        for d in arch["data"].get("edges", []):
            k = (d.get("source"), d.get("target"), d.get("kind", "api"))
            if not (k[0] and k[1]): continue
            if k in edges: edges[k]["declared_in_repo"] = True
            else:
                edges[k] = {"source": k[0], "target": k[1], "kind": k[2], "calls": 0, "errors": 0, "first_seen": None, "last_seen": None,
                            "inferred": True, "evidence": "declared in repository architecture (Module 01); NOT observed in telemetry"}; declared += 1
    names = {r["service"] for t in ("logs", "metrics", "spans", "events") for r in q(f"SELECT DISTINCT service FROM {t}")}
    for e in edges.values(): names |= {e["source"], e["target"]}
    nodes = [{"id": n, "kind": _node_kind(n), "critical": bool(re.search(FINTECH_RE, n))} for n in sorted(names)]
    return {"nodes": nodes, "edges": sorted(edges.values(), key=lambda e: (e["source"], e["target"])),
            "notes": [] if arch.get("available") else [f"repository architecture unavailable: {arch.get('reason')}"], "declared_only": declared}

def adjacency(g):
    down, up = defaultdict(set), defaultdict(set)
    for e in g["edges"]: down[e["source"]].add(e["target"]); up[e["target"]].add(e["source"])
    return up, down

def critical_paths(g, limit=10):
    up, down = adjacency(g); crit = {n["id"] for n in g["nodes"] if n["critical"] and n["kind"] == "service"}
    entries = [n["id"] for n in g["nodes"] if n["kind"] == "service" and not up[n["id"]] and down[n["id"]]]
    out = []
    def dfs(n, path):
        if len(out) >= limit or len(path) > 8: return
        if n in crit and len(path) > 1: out.append(list(path))
        for m in sorted(down[n]):
            if m not in path: dfs(m, path + [m])
    for e in entries: dfs(e, [e])
    return out

def dependency_changes(g, data=None):
    deploys = [e for e in (data or A.collect(0, 4e9))["events"] if e["kind"] == "deploy"]
    firsts = [e["first_seen"] for e in g["edges"] if e["first_seen"]]
    if not firsts: return []
    base, out = min(firsts), []
    for d in deploys:
        new = [f"{e['source']}->{e['target']}" for e in g["edges"] if e["first_seen"] and d["ts"] <= e["first_seen"] <= d["ts"] + T["deploy_window_s"] and e["first_seen"] - base > 600]
        if new: out.append({"deploy": {"service": d["service"], "ts": d["ts"], "version": d["payload"].get("version")}, "new_edges": new})
    return out

def inventory(minutes=30):
    since, until = A.window(None, None, minutes)
    d = A.collect(since, until); an, _ = A.analyze(d); g = graph()
    S, meth, _ = A.build_series(d); active = defaultdict(list)
    for a in an:
        if a["end"] >= until - 2 * A.B: active[a["service"]].append(a)
    out = []
    for n in g["nodes"]:
        sv = n["id"]; er = S[sv].get("error_rate", {}) if sv in S else {}
        lat = S[sv].get("latency_p95", {}) if sv in S else {}
        tot = sum(v[1] for v in er.values())
        rate = (sum(v[0] * v[1] for v in er.values()) / tot) if tot else None
        sev = [a["severity"] for a in active[sv]]
        health = "unknown" if not (er or lat or sv in active) else ("down" if any(a["signal"] == "availability" for a in active[sv]) else "degraded" if active[sv] else "healthy")
        out.append({**n, "health": health, "error_rate": rate, "requests": tot, "latency_p95_ms": max((v[0] for v in lat.values()), default=None),
                    "active_anomalies": [{"signal": a["signal"], "severity": a["severity"]} for a in active[sv]],
                    "error_rate_method": meth[sv].get("error_rate") if sv in meth else None, "window_minutes": minutes})
    return out
