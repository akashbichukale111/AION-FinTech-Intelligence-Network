import re
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from ..errors import DomainError
from ..models import Task, Plan, Step, History
from ..util import iso, utcnow
from ..module01 import Module01Client
from . import analysis, planner

OPEN_PLAN = ("draft", "approved", "in_progress")
AI_ACTORS = ("ai:", "agent:", "llm", "system", "bot")

def log(db, task_id, plan_id, event, actor, note=None, data=None):
    db.add(History(task_id=task_id, plan_id=plan_id, event=event, actor=actor, note=note, data=data))

def _norm(s): return re.sub(r"\W+", " ", s.lower()).strip()

def _scan(m01: Module01Client, repo_id, actor):
    try:
        scan = m01.get_scan(repo_id, actor)
    except PermissionError as e:
        raise DomainError(403, str(e), "repo_forbidden")
    except Exception as e:
        raise DomainError(502, f"Module 01 unavailable: {e}", "module01_unavailable")
    if scan is None:
        raise DomainError(424, f"No Module 01 scan found for repository '{repo_id}'. Scan it in Module 01 first.", "scan_missing")
    return scan

# ---- serialization
def task_out(t):
    return {"id": t.id, "title": t.title, "description": t.description, "type": t.type, "priority": t.priority, "repo_id": t.repo_id,
            "status": t.status, "logs": t.logs, "created_by": t.created_by, "created_at": iso(t.created_at), "updated_at": iso(t.updated_at),
            "analysis": {k: v for k, v in t.analysis.items() if not k.startswith("_")} if t.analysis else None}

def step_out(s):
    return {"id": s.id, "code": s.code, "position": s.position, "title": s.title, "description": s.description, "kind": s.kind,
            "files": s.files, "depends_on": s.depends_on, "status": s.status, "note": s.note, "updated_by": s.updated_by}

def hist_out(h):
    return {"id": h.id, "event": h.event, "actor": h.actor, "note": h.note, "data": h.data, "at": iso(h.at), "plan_id": h.plan_id}

def plan_out(db, p):
    steps = db.scalars(select(Step).where(Step.plan_id == p.id).order_by(Step.position)).all()
    hist = db.scalars(select(History).where(History.plan_id == p.id).order_by(History.id)).all()
    return {"id": p.id, "task_id": p.task_id, "version": p.version, "status": p.status, "title": p.title, "content": p.content, "risk": p.risk,
            "evidence": p.evidence, "scan_id": p.scan_id, "created_by": p.created_by, "created_at": iso(p.created_at),
            "decided_by": p.decided_by, "decided_at": iso(p.decided_at), "decision_note": p.decision_note,
            "steps": [step_out(s) for s in steps], "history": [hist_out(h) for h in hist]}

def get_task(db, tid):
    t = db.get(Task, tid)
    if not t: raise DomainError(404, "Task not found", "not_found")
    return t

def get_plan(db, pid):
    p = db.get(Plan, pid)
    if not p: raise DomainError(404, "Plan not found", "not_found")
    return p

# ---- tasks
def create_task(db: Session, data, actor, m01):
    _scan(m01, data.repo_id, actor)  # repository access + scan existence check
    dup = db.scalars(select(Task).where(Task.repo_id == data.repo_id, Task.status.notin_(["done", "rejected", "closed"]))).all()
    if any(_norm(t.title) == _norm(data.title) for t in dup):
        raise DomainError(409, "An open task with the same title already exists for this repository.", "duplicate_task")
    t = Task(**data.model_dump(), created_by=actor)
    db.add(t); db.flush()
    log(db, t.id, None, "task_created", actor, data={"type": t.type, "repo_id": t.repo_id})
    db.commit()
    return t

def analyze_task(db, tid, actor, m01):
    t = get_task(db, tid)
    scan = _scan(m01, t.repo_id, actor)
    t.analysis = analysis.run(t, scan)
    if t.status in ("new", "analyzed", "planned"): t.status = "analyzed"
    log(db, t.id, None, "task_analyzed", actor, data={"scan_id": scan["scan_id"], "risk": t.analysis["risk"]["level"]})
    db.commit()
    return t

# ---- plans
def create_plan(db, tid, actor, m01, supersede=False):
    t = get_task(db, tid)
    if not t.analysis: raise DomainError(409, "Analyze the task before creating a plan.", "not_analyzed")
    scan = _scan(m01, t.repo_id, actor)
    if scan["scan_id"] != t.analysis["scan"]["scan_id"]:
        raise DomainError(409, "Repository was re-scanned since the analysis. Re-run analysis first.", "analysis_outdated")
    open_plans = db.scalars(select(Plan).where(Plan.task_id == tid, Plan.status.in_(OPEN_PLAN))).all()
    if open_plans:
        if not supersede or any(p.status == "in_progress" for p in open_plans):
            raise DomainError(409, "An open plan already exists for this task (set supersede=true to replace a draft/approved plan; in-progress plans cannot be replaced).", "open_plan_exists")
        for p in open_plans:
            p.status = "superseded"; log(db, tid, p.id, "plan_superseded", actor)
    A = t.analysis
    content, steps = planner.build(t, A)
    ver = (db.scalar(select(func.max(Plan.version)).where(Plan.task_id == tid)) or 0) + 1
    p = Plan(task_id=tid, version=ver, title=f"Plan v{ver}: {t.title}"[:240], content=content, risk=A["risk"], evidence=A["evidence"],
             scan_id=scan["scan_id"], created_by=actor)
    db.add(p); db.flush()
    for i, s in enumerate(steps):
        db.add(Step(id=f"{p.id}_{s['code']}", plan_id=p.id, position=i + 1, **s))
    t.status = "planned"
    log(db, tid, p.id, "plan_created", actor, data={"version": ver, "risk": A["risk"]["level"], "steps": len(steps)})
    db.commit()
    return p

def update_plan(db, pid, data, actor):
    p = get_plan(db, pid)
    if p.status != "draft": raise DomainError(409, "Only draft plans can be edited.", "not_editable")
    c, changed = dict(p.content), {}
    rec = dict(c["recommendations"])
    if data.title: p.title = data.title; changed["title"] = data.title
    if data.proposed_solution is not None: rec["proposed_solution"] = data.proposed_solution; changed["proposed_solution"] = True
    if data.notes is not None: rec["notes"] = data.notes; changed["notes"] = True
    c["recommendations"] = rec; p.content = c
    log(db, p.task_id, p.id, "plan_updated", actor, data=changed)
    db.commit(); return p

def _decide(db, pid, actor, note, approve):
    p = get_plan(db, pid)
    if actor.lower().startswith(AI_ACTORS): raise DomainError(403, "Only a human may approve or reject plans.", "human_required")
    if p.status != "draft": raise DomainError(409, f"Plan is '{p.status}'; only drafts can be decided.", "bad_state")
    if approve and p.risk["level"] in ("high", "critical") and len((note or "").strip()) < 10:
        raise DomainError(422, "High/critical risk plans need an approval note (min 10 chars) acknowledging the risk.", "note_required")
    if not approve and len((note or "").strip()) < 3: raise DomainError(422, "A rejection reason is required.", "note_required")
    if approve and p.created_by == actor and p.risk["level"] == "critical":
        raise DomainError(403, "Critical-risk plans need an approver other than the creator.", "four_eyes")
    p.status, p.decided_by, p.decided_at, p.decision_note = ("approved" if approve else "rejected"), actor, utcnow(), note
    t = get_task(db, p.task_id); t.status = "approved" if approve else "rejected"
    log(db, p.task_id, p.id, "plan_approved" if approve else "plan_rejected", actor, note)
    db.commit(); return p

def approve(db, pid, actor, note): return _decide(db, pid, actor, note, True)
def reject(db, pid, actor, note): return _decide(db, pid, actor, note, False)

def update_step(db, pid, sid, data, actor):
    p = get_plan(db, pid)
    if p.status not in ("approved", "in_progress"):
        raise DomainError(409, "Plan must be approved before steps can be executed or tracked.", "not_approved")
    steps = db.scalars(select(Step).where(Step.plan_id == pid)).all()
    s = next((x for x in steps if x.id == sid or x.code == sid), None)
    if not s: raise DomainError(404, "Step not found", "not_found")
    ok = {"pending": {"in_progress", "skipped"}, "in_progress": {"done", "blocked"}, "blocked": {"in_progress", "skipped"}, "done": set(), "skipped": set()}
    if data.status != s.status and data.status not in ok[s.status]:
        raise DomainError(409, f"Invalid transition {s.status} -> {data.status}", "bad_transition")
    if data.status in ("in_progress", "done"):
        state = {x.code: x.status for x in steps}
        unmet = [d for d in s.depends_on if state.get(d) not in ("done", "skipped")]
        if unmet: raise DomainError(409, f"Unmet dependencies: {', '.join(unmet)}", "unmet_dependencies")
    old, s.status, s.note, s.updated_by = s.status, data.status, data.note or s.note, actor
    sts = [x.status for x in steps]
    if all(x in ("done", "skipped") for x in sts): p.status = "completed"
    elif any(x != "pending" for x in sts): p.status = "in_progress"
    get_task(db, p.task_id).status = {"completed": "done", "in_progress": "in_progress"}.get(p.status, "approved")
    log(db, p.task_id, p.id, "step_updated", actor, data.note, {"step": s.code, "from": old, "to": data.status})
    db.commit(); return p

def executable_contract(db, pid):
    """Interface for Module 03 Coding Agents: only APPROVED/in-progress plans are exposed."""
    p = get_plan(db, pid)
    if p.status not in ("approved", "in_progress"):
        raise DomainError(409, "Plan is not approved; it cannot be executed.", "not_approved")
    o = plan_out(db, p); t = get_task(db, p.task_id)
    return {"contract_version": "1", "plan_id": p.id, "task_id": t.id, "repo_id": t.repo_id, "scan_id": p.scan_id,
            "approved_by": p.decided_by, "approved_at": o["decided_at"], "risk": p.risk,
            "allowed_files": o["content"]["files_and_modules"]["to_change"], "steps": o["steps"],
            "acceptance_criteria": o["content"]["acceptance_criteria"], "rollback_strategy": o["content"]["rollback_strategy"],
            "constraints": ["Do not touch files outside allowed_files without re-approval", "Use Decimal for money", "Never access real funds"]}
