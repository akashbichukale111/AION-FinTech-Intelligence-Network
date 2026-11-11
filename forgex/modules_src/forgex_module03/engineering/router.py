from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from . import store, service as S

store.init()
router = APIRouter(prefix="/api/v1/engineering", tags=["engineering"])

def _err(e: S.EngError): return JSONResponse(status_code=e.http, content={"error": {"code": e.code, "message": e.msg}})
def _wrap(fn):
    try: return fn()
    except S.EngError as e: return _err(e)

class CreateIn(BaseModel): plan_id: str
class ApproveIn(BaseModel): approver: str
class RejectIn(BaseModel): reason: str = ""; reviewer: str = "unknown"
class RollbackIn(BaseModel): requested_by: str = "unknown"

@router.post("/executions", status_code=201)
def create(b: CreateIn): return _wrap(lambda: S.public(S.create(b.plan_id)))
@router.get("/executions")
def list_(): return store.list_all()
@router.get("/executions/{eid}")
def get(eid: str): return _wrap(lambda: S.public(S.get(eid)))
@router.post("/executions/{eid}/start", status_code=202)
def start(eid: str): return _wrap(lambda: S.public(S.start(eid), False))
@router.post("/executions/{eid}/cancel")
def cancel(eid: str): return _wrap(lambda: S.public(S.cancel(eid), False))
@router.get("/executions/{eid}/diff")
def diff(eid: str, format: str = "json"):
    def f():
        d = S.diff(eid); return PlainTextResponse(d["patch"], headers={"X-Patch-SHA256": d["sha256"]}) if format == "patch" else d
    return _wrap(f)
@router.get("/executions/{eid}/review")
def review(eid: str):
    def f():
        ex = S.get(eid); return {"tests": ex["tests"], "code_review": ex["code_review"], "security_review": ex["security_review"], "limitations": ex["limitations"], "analysis": ex["analysis"]}
    return _wrap(f)
@router.post("/executions/{eid}/approve")
def approve(eid: str, b: ApproveIn): return _wrap(lambda: S.public(S.approve(eid, b.approver), False))
@router.post("/executions/{eid}/reject")
def reject(eid: str, b: RejectIn): return _wrap(lambda: S.public(S.reject(eid, b.reason, b.reviewer), False))
@router.post("/executions/{eid}/rollback")
def rollback(eid: str, b: RollbackIn): return _wrap(lambda: S.public(S.rollback(eid, b.requested_by), False))
