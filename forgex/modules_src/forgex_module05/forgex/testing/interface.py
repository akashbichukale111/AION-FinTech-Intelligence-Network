"""Public interface for other FORGEX modules."""
from . import service, store

def schedule_run(repository_id, **kw):
    """Module 04 (NightShift): start an authorized scheduled test run; returns the run doc (poll with get_run)."""
    return service.create_run({"repository_id": repository_id, "authorized": True, "include_fintech": kw.pop("include_fintech", False), **kw})

def get_run(run_id): return store.get("test_run", run_id)

def nightshift_report(run_id):
    r = store.get("test_run", run_id)
    if not r: return None
    g = r.get("quality_gate") or {}
    return {"run_id": run_id, "status": r["status"], "verdict": g.get("verdict"), "summary": r["summary"], "duration_seconds": r["duration_seconds"],
            "new_failures": (r.get("regression") or {}).get("newly_failing", []), "failure_groups": [{"category": f["category"], "count": f["count"], "cause": f["likely_cause"]} for f in r["failure_groups"]],
            "unknowns": g.get("unknowns", [])}

def release_assurance(run_id):
    """Module 10 (Release Risk & Assurance): stable, evidence-linked testing assessment."""
    r = store.get("test_run", run_id)
    if not r: return None
    g, reg = r.get("quality_gate") or {}, r.get("regression") or {}
    return {"schema": "forgex.testing.assurance/v1", "run_id": run_id, "repository_id": r["repository_id"], "commit_ref": r["commit_ref"],
            "verdict": g.get("verdict"), "complete": r["status"] in ("passed", "failed"), "critical_failures": g.get("critical_failures", []),
            "checks": [{"id": c["id"], "status": c["status"], "critical": c["critical"], "detail": c["detail"]} for c in g.get("checks", [])],
            "regression_risk": reg.get("risk_level"), "newly_failing_tests": reg.get("newly_failing", []),
            "coverage": ((r.get("coverage") or {}).get("total")), "fintech_failed": [s["id"] for s in (r.get("fintech") or {}).get("scenarios", []) if s["status"] != "passed"],
            "unknowns": g.get("unknowns", []), "evidence": r.get("evidence_refs", []), "environment": r.get("environment")}
