"""FastAPI surface: /api/v1/fintech-lab/*  (mount `router` into the FORGEX app, or run `create_app()` standalone)."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from .service import Lab

_lab: Lab | None = None


def get_lab() -> Lab:
    global _lab
    if _lab is None:
        _lab = Lab()
    return _lab


def set_lab(lab: Lab):
    global _lab
    _lab = lab


router = APIRouter(prefix="/api/v1/fintech-lab", tags=["fintech-lab"])


class RunRequest(BaseModel):
    scenario_id: str | None = None
    config: dict[str, Any] | None = None
    seed: int | None = None
    profile: str | None = None
    params: dict[str, Any] | None = None
    implementation: dict[str, Any] | None = None
    schedule: list[str] | None = None
    repo_path: str | None = None
    triggered_by: str = Field("manual", pattern=r"^[a-z0-9_:\-\.]{1,60}$")


def guard(fn):
    try:
        return fn()
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))


@router.get("/meta")
def meta():
    return get_lab().meta()


@router.get("/overview")
def overview():
    return get_lab().overview()


@router.post("/scenarios", status_code=201)
def create_scenario(body: dict[str, Any]):
    return guard(lambda: get_lab().create_scenario(body))


@router.get("/scenarios")
def list_scenarios(type: str | None = None):
    items = get_lab().scenarios()
    items = [s for s in items if not type or s["type"] == type]
    return {"items": items, "total": len(items), "synthetic": True}


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str):
    return guard(lambda: get_lab().scenario(scenario_id))


@router.post("/runs", status_code=201)
def start_run(body: RunRequest):
    ov = {k: getattr(body, k) for k in ("seed", "profile", "params", "implementation", "schedule", "repo_path")}
    return guard(lambda: get_lab().run(config=body.config, scenario_id=body.scenario_id, overrides=ov, triggered_by=body.triggered_by))


@router.get("/runs")
def list_runs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), scenario_id: str | None = None):
    items, total = get_lab().store.runs(limit, offset, scenario_id)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/runs/compare")
def compare(ids: str):
    return guard(lambda: get_lab().compare([i for i in ids.split(",") if i]))


@router.get("/runs/{run_id}")
def get_run(run_id: str, full: bool = False):
    return guard(lambda: get_lab().get_run(run_id, full))


@router.get("/runs/{run_id}/findings")
def run_findings(run_id: str, severity: str | None = None):
    def f():
        fs = get_lab().get_run(run_id)["findings"]
        fs = [x for x in fs if not severity or x["severity"] == severity]
        return {"run_id": run_id, "items": fs, "total": len(fs), "synthetic": True}
    return guard(f)


@router.get("/runs/{run_id}/traces")
def run_traces(run_id: str, actor: str | None = None, op: str | None = None, limit: int = Query(1000, ge=1, le=5000), offset: int = Query(0, ge=0)):
    return guard(lambda: get_lab().traces(run_id, actor, op, limit, offset))


@router.get("/runs/{run_id}/evidence")
def run_evidence(run_id: str, evidence_id: str | None = None):
    def f():
        lab = get_lab()
        if evidence_id:
            e = lab.store.evidence_one(evidence_id)
            if not e or e["run_id"] != run_id:
                raise KeyError("evidence not found")
            return e
        lab.get_run(run_id)
        return {"run_id": run_id, "items": lab.store.evidence(run_id)}
    return guard(f)


@router.post("/runs/{run_id}/replay")
def replay(run_id: str):
    return guard(lambda: get_lab().replay(run_id))


@router.get("/reliability-report/{run_id}")
def report(run_id: str):
    return guard(lambda: get_lab().report(run_id))


@router.post("/ledger/validate")
def ledger_validate(body: dict[str, Any]):
    return guard(lambda: get_lab().ledger_validate(body))


@router.post("/idempotency/analyze")
def idempotency(body: dict[str, Any]):
    return guard(lambda: get_lab().idempotency_analyze(body))


@router.post("/concurrency/simulate")
def concurrency(body: dict[str, Any]):
    return guard(lambda: get_lab().concurrency_simulate(body))


@router.post("/retry/analyze")
def retry_analyze(body: dict[str, Any]):
    return guard(lambda: get_lab().retry_analyze(body))


@router.post("/retry/storm")
def retry_storm(body: dict[str, Any]):
    return guard(lambda: get_lab().storm(body))


@router.post("/settlement/simulate")
def settlement(body: dict[str, Any]):
    return guard(lambda: get_lab().settlement_simulate(body))


@router.post("/repository/scan")
def repo_scan(body: dict[str, Any]):
    return guard(lambda: get_lab().repo_scan(str(body.get("path", ""))))


@router.get("/findings")
def all_findings(severity: str | None = None, failure_type: str | None = None):
    fs = get_lab().store.findings()
    fs = [f for f in fs if (not severity or f["severity"] == severity) and (not failure_type or f["failure_type"] == failure_type)]
    return {"items": fs, "total": len(fs), "synthetic": True}


@router.post("/findings/{finding_id}/tasks", status_code=201)
def make_task(finding_id: str):
    return guard(lambda: get_lab().create_task(finding_id))


@router.get("/tasks")
def tasks():
    t = get_lab().tasks()
    return {"items": t, "total": len(t)}


@router.post("/tasks/{task_id}/validate")
def validate_task(task_id: str):
    return guard(lambda: get_lab().validate_task(task_id))


@router.get("/monitoring/spec/{scenario_id}")
def monitor(scenario_id: str, interval_minutes: int = Query(60, ge=5, le=10080)):
    return guard(lambda: get_lab().monitor_spec(scenario_id, interval_minutes))


@router.get("/guardian/signals/{run_id}")
def guardian(run_id: str):
    return guard(lambda: get_lab().guardian(run_id))


STATIC = Path(__file__).parent / "static" / "index.html"


def create_app(lab: Lab | None = None) -> FastAPI:
    if lab:
        set_lab(lab)
    app = FastAPI(title="FORGEX Module 06 - FinTech Transaction Reliability Lab", version="1.0.0")
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    @app.get("/fintech-lab", include_in_schema=False)
    def ui():
        return FileResponse(STATIC)

    @app.exception_handler(Exception)
    def boom(_, exc):
        return JSONResponse({"detail": f"internal error: {type(exc).__name__}"}, status_code=500)
    return app


app = create_app()
