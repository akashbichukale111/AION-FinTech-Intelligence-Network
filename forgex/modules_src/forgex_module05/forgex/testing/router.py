"""REST API. Prefix /api/v1/testing (adapt to FORGEX's router conventions when mounting)."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import discovery, fintech, runner, service, store
from .integrations import providers

router = APIRouter(prefix="/api/v1/testing", tags=["testing"])

class DiscoverReq(BaseModel):
    repository_id: Optional[str] = None
    repo_path: Optional[str] = None

class GenerateReq(DiscoverReq):
    base_ref: Optional[str] = None
    change_set_id: Optional[str] = None
    changes: Optional[list[dict]] = None

class RunReq(GenerateReq):
    authorized: bool = False
    token: Optional[str] = None
    commit_ref: Optional[str] = None
    command: Optional[str] = None
    framework: Optional[str] = None
    timeout_seconds: int = Field(300, ge=5, le=3600)
    memory_mb: int = Field(1024, ge=128, le=16384)
    isolation: str = "auto"
    allow_unisolated_network: bool = False
    collect_coverage: bool = True
    include_fintech: bool = False
    base_run_id: Optional[str] = None
    plan_id: Optional[str] = None
    critical_tests: list[str] = []
    security_findings: Optional[list[dict]] = None
    thresholds: Optional[dict] = None

class ReviewReq(BaseModel):
    decision: str
    reviewer: str = ""
    note: str = ""

class FintechReq(BaseModel):
    mode: str = "reference"          # "defective" runs a deliberately buggy engine to prove the suite catches bugs
    scenarios: Optional[list[str]] = None
    repository_id: Optional[str] = None

def _run(run_id):
    r = store.get("test_run", run_id)
    if not r: raise HTTPException(404, "run not found")
    return r

def _guard(fn, *a):
    try: return fn(*a)
    except PermissionError as e: raise HTTPException(403, str(e))
    except runner.IsolationUnavailable as e: raise HTTPException(409, str(e))
    except (ValueError, KeyError) as e: raise HTTPException(422, str(e))

@router.get("/environment")
def environment():
    return {"isolation": runner.detect_isolation(), "workers": service.POOL._max_workers}

@router.post("/discover")
def discover(req: DiscoverReq):
    repo = _guard(providers.repository.resolve, req.repository_id, req.repo_path)
    result = discovery.discover(repo.path)
    result.update(repository_id=repo.id, commit_ref=repo.commit_ref, scanned_at=store.now())
    store.put("inventory", repo.id, result, repo.id)
    return result

@router.get("/repositories/{repository_id}/tests")
def repository_tests(repository_id: str):
    inv = store.get("inventory", repository_id)
    if not inv: raise HTTPException(404, "not discovered yet; POST /discover first")
    return {k: v for k, v in inv.items() if k != "import_graph"}

@router.post("/generate")
def generate(req: GenerateReq):
    return _guard(service.generate_tests, req.model_dump())

@router.get("/generate")
def list_proposals(repository_id: Optional[str] = None):
    return store.listing("test_proposal", repository_id)

@router.post("/generate/{proposal_id}/review")
def review(proposal_id: str, req: ReviewReq):
    return _guard(service.review_proposal, proposal_id, req.decision, req.reviewer, req.note)

@router.post("/runs", status_code=202)
def start_run(req: RunReq):
    return _guard(service.create_run, req.model_dump())

@router.get("/runs")
def list_runs(repository_id: Optional[str] = None, limit: int = 50):
    return [service.run_summary(r) for r in store.listing("test_run", repository_id, limit)]

@router.get("/runs/compare")
def compare(a: str, b: str):
    return service.compare_runs(_run(a), _run(b))

@router.get("/runs/{run_id}")
def get_run(run_id: str):
    r = _run(run_id)
    return {k: v for k, v in r.items() if k not in ("coverage",)} | {"coverage_total": (r.get("coverage") or {}).get("total")}

@router.get("/runs/{run_id}/logs")
def logs(run_id: str):
    _run(run_id); return store.get("run_logs", run_id) or {"commands": []}

@router.get("/runs/{run_id}/failures")
def failures(run_id: str):
    r = _run(run_id)
    bad = [x for x in r["results"] if x["status"] in ("failed", "error")]
    return {"run_id": run_id, "status": r["status"], "status_reason": r["status_reason"], "groups": r["failure_groups"], "failures": bad}

@router.get("/runs/{run_id}/coverage")
def coverage(run_id: str):
    r = _run(run_id)
    return {"run_id": run_id, "available": bool(r["coverage"]), "report": r["coverage_report"], "coverage": r["coverage"],
            "changed_code": r["changed_coverage"], "comparison": r["coverage_compare"]}

@router.get("/runs/{run_id}/regressions")
def regressions(run_id: str):
    r = _run(run_id)
    return {"run_id": run_id, "available": bool(r["regression"]), "regression": r["regression"],
            "note": None if r["regression"] else "no change set, plan or baseline run was supplied"}

@router.get("/runs/{run_id}/quality-gate")
def quality_gate(run_id: str):
    r = _run(run_id)
    return r["quality_gate"] or {"verdict": "pending", "checks": []}

@router.get("/runs/{run_id}/assurance")
def assurance(run_id: str):
    from .interface import release_assurance
    _run(run_id); return release_assurance(run_id)

@router.post("/fintech-simulations")
def fintech_simulations(req: FintechReq):
    if req.mode not in ("reference", "defective"): raise HTTPException(422, "mode must be reference or defective")
    out = fintech.run_simulations(req.mode, req.scenarios)
    sid = store.new_id("sim"); out["id"] = sid; out["created_at"] = store.now()
    store.put("fintech_sim", sid, out, req.repository_id)
    return out

@router.get("/fintech-simulations")
def list_sims(repository_id: Optional[str] = None):
    return [{k: v for k, v in s.items() if k != "scenarios"} for s in store.listing("fintech_sim", repository_id, 30) if "id" in s]

@router.get("/ui", response_class=HTMLResponse)
def ui():
    return (Path(__file__).parent / "ui.html").read_text()
