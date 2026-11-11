from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db
from .errors import DomainError
from .models import Task, Plan, History
from .module01 import get_m01
from .schemas import TaskCreate, PlanCreate, PlanUpdate, Decision, StepUpdate
from .services import lifecycle as L

router = APIRouter(prefix="/api/v1/engineering", tags=["engineering-planner"])

def actor(x_actor: str = Header(None)):
    if not x_actor or not (2 <= len(x_actor.strip()) <= 64):
        raise DomainError(401, "X-Actor header required (replace with FORGEX auth dependency on integration).", "unauthenticated")
    return x_actor.strip()

@router.get("/repositories")
def repositories(a: str = Depends(actor), m01=Depends(get_m01)):
    try:
        return {"items": m01.list_repositories(a)}
    except Exception as e:
        raise DomainError(502, f"Module 01 unavailable: {e}", "module01_unavailable")

@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate, a: str = Depends(actor), db: Session = Depends(get_db), m01=Depends(get_m01)):
    return L.task_out(L.create_task(db, body, a, m01))

@router.get("/tasks")
def list_tasks(repo_id: str | None = None, status: str | None = None, db: Session = Depends(get_db), a: str = Depends(actor)):
    q = select(Task).order_by(Task.created_at.desc())
    if repo_id: q = q.where(Task.repo_id == repo_id)
    if status: q = q.where(Task.status == status)
    return {"items": [{k: v for k, v in L.task_out(t).items() if k != "analysis"} for t in db.scalars(q).all()]}

@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db), a: str = Depends(actor)):
    t = L.get_task(db, task_id)
    plans = db.scalars(select(Plan).where(Plan.task_id == task_id).order_by(Plan.version.desc())).all()
    hist = db.scalars(select(History).where(History.task_id == task_id).order_by(History.id)).all()
    return {**L.task_out(t), "plans": [{"id": p.id, "version": p.version, "status": p.status, "risk_level": p.risk["level"]} for p in plans],
            "history": [L.hist_out(h) for h in hist]}

@router.post("/tasks/{task_id}/analyze")
def analyze(task_id: str, a: str = Depends(actor), db: Session = Depends(get_db), m01=Depends(get_m01)):
    return L.task_out(L.analyze_task(db, task_id, a, m01))

@router.post("/tasks/{task_id}/plans", status_code=201)
def create_plan(task_id: str, body: PlanCreate = PlanCreate(), a: str = Depends(actor), db: Session = Depends(get_db), m01=Depends(get_m01)):
    return L.plan_out(db, L.create_plan(db, task_id, a, m01, body.supersede))

@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, db: Session = Depends(get_db), a: str = Depends(actor)):
    return L.plan_out(db, L.get_plan(db, plan_id))

@router.patch("/plans/{plan_id}")
def update_plan(plan_id: str, body: PlanUpdate, a: str = Depends(actor), db: Session = Depends(get_db)):
    return L.plan_out(db, L.update_plan(db, plan_id, body, a))

@router.post("/plans/{plan_id}/approve")
def approve(plan_id: str, body: Decision = Decision(), a: str = Depends(actor), db: Session = Depends(get_db)):
    return L.plan_out(db, L.approve(db, plan_id, a, body.note))

@router.post("/plans/{plan_id}/reject")
def reject(plan_id: str, body: Decision = Decision(), a: str = Depends(actor), db: Session = Depends(get_db)):
    return L.plan_out(db, L.reject(db, plan_id, a, body.note))

@router.patch("/plans/{plan_id}/steps/{step_id}")
def step(plan_id: str, step_id: str, body: StepUpdate, a: str = Depends(actor), db: Session = Depends(get_db)):
    return L.plan_out(db, L.update_step(db, plan_id, step_id, body, a))

@router.get("/plans/{plan_id}/history")
def history(plan_id: str, db: Session = Depends(get_db), a: str = Depends(actor)):
    return {"items": L.plan_out(db, L.get_plan(db, plan_id))["history"]}

@router.get("/plans/{plan_id}/executable")
def executable(plan_id: str, a: str = Depends(actor), db: Session = Depends(get_db)):
    """Module 03 hand-off contract (approved plans only)."""
    return L.executable_contract(db, plan_id)
