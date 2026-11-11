"""Orchestration: approved plan -> isolated workspace -> agents -> diff -> review -> human approval."""
import threading, time, uuid
from pathlib import Path
from . import store, workspace as W, agents as A

class EngError(Exception):
    def __init__(self, code, msg, http=400): self.code, self.msg, self.http = code, msg, http

_cancel = set()

def load_plan(plan_id):
    """Module 02 adapter. Wire to your real planning service (one line). Expected shape:
    {id, status:'approved', repository_path, steps:[{id,title,kind?,files?,evidence?,ops?}]}"""
    try:
        from app.planning.service import get_plan  # <-- adjust import to your Module 02
    except Exception as e:
        raise EngError("module02_unavailable", f"Module 02 adapter not wired: {e}", 501)
    p = get_plan(plan_id)
    if not p: raise EngError("plan_not_found", "Plan not found", 404)
    return p if isinstance(p, dict) else p.dict()

def load_evidence(repo):
    """Module 01 adapter (optional). Return repo intelligence dict for the lead agent; {} if unavailable."""
    try:
        from app.repository.service import get_repo_evidence  # <-- adjust to your Module 01
        return get_repo_evidence(repo) or {}
    except Exception: return {}

def _agents(): return {r: {"role": r, "description": d, "status": "idle", "provider": A.get_provider().name if r not in ("code_review", "security_review", "test_engineering") else "deterministic"} for r, d in A.ROLES}

def _set(ex, role, status, note=""):
    ex["agents"][role]["status"] = status; store.audit(ex["id"], role, status, note); store.save(ex)

def create(plan_id):
    plan = load_plan(plan_id)
    if str(plan.get("status", "")).lower() != "approved": raise EngError("plan_not_approved", "Only approved plans can be executed", 409)
    repo = plan.get("repository_path") or plan.get("repo")
    if not repo or not Path(repo).is_dir(): raise EngError("repo_invalid", "Plan repository path is invalid", 422)
    ex = {"id": uuid.uuid4().hex[:12], "plan_id": plan_id, "repo": str(Path(repo).resolve()), "status": "created", "created": time.time(),
          "plan": plan, "agents": _agents(), "current_step": None, "changes": [], "tests": None, "code_review": [], "security_review": [],
          "analysis": None, "evidence": {}, "limitations": [], "patch_sha256": None, "error": None,
          "providers": {"deterministic": {"available": True}, "llm": {"available": A.LLMProvider.available, "reason": A.LLMProvider.reason}}}
    store.save(ex); store.audit(ex["id"], "system", "created", f"plan={plan_id}"); return ex

def start(eid):
    ex = get(eid)
    if ex["status"] != "created": raise EngError("bad_state", f"Cannot start from {ex['status']}", 409)
    ex["status"] = "running"; store.save(ex)
    threading.Thread(target=_run, args=(eid,), daemon=True).start(); return ex

def _fail(ex, code, msg):
    ex["status"] = "failed"; ex["error"] = {"code": code, "message": msg}; store.audit(ex["id"], "system", "failed", msg); store.save(ex)

def _run(eid):
    ex = store.get(eid); plan = ex["plan"]
    try:
        _set(ex, "engineering_lead", "running", "validating plan, loading Module 01 evidence")
        ex["evidence"] = load_evidence(ex["repo"])
        ws, manifest = W.create(ex["repo"], eid); ex["workspace"], ex["manifest"] = ws, manifest
        files = sorted({f for s in plan["steps"] for f in (s.get("files") or [o["file"] for o in s.get("ops", [])])})
        ex["declared_files"] = files; _set(ex, "engineering_lead", "done", f"workspace ready, {len(manifest)} files copied (secrets excluded)")

        _set(ex, "code_analysis", "running"); ex["analysis"] = A.analyse(ws, files)
        _set(ex, "code_analysis", "done", f"{len(ex['analysis']['injection_findings'])} injection-like strings flagged and ignored")

        prov, meta = A.get_provider(), {}
        for step in plan["steps"]:
            if eid in _cancel: ex["status"] = "cancelled"; store.audit(eid, "system", "cancelled"); store.save(ex); return
            ex["current_step"] = step.get("id"); role = "refactoring" if step.get("kind") == "refactor" else "implementation"
            _set(ex, role, "running", f"step {step.get('id')}: {step.get('title')}")
            if not step.get("ops"):
                ex["limitations"].append(f"Step {step.get('id')} has no executable ops and no LLM provider is available")
                _set(ex, role, "blocked", "insufficient evidence/provider - stopping"); return _fail(ex, "step_not_executable", f"Step {step.get('id')} needs an LLM provider or explicit ops")
            for f in prov.apply_step(ws, step):
                meta[f] = {"step_id": step.get("id"), "agent": role, "explanation": step.get("title"), "evidence": step.get("evidence", [])}
            _set(ex, role, "done")

        _set(ex, "test_engineering", "running"); ex["tests"] = A.run_tests(ws)
        _set(ex, "test_engineering", "done" if ex["tests"]["passed"] else "failed", ex["tests"]["command"])

        changes = W.compute_changes(ex["repo"], ws, manifest, meta)
        for c in changes: c["_abs"] = str(Path(ws) / c["file"])
        _set(ex, "code_review", "running"); ex["code_review"] = A.code_review(changes, files); _set(ex, "code_review", "done", f"{len(ex['code_review'])} findings")
        _set(ex, "security_review", "running"); ex["security_review"] = A.security_review(changes); _set(ex, "security_review", "done", f"{len(ex['security_review'])} findings")
        for c in changes: c.pop("_abs")
        ex["changes"] = changes; ex["patch_sha256"] = W.export_patch(changes)[1]
        ex["limitations"].append("Tests run in a restricted subprocess, not a container; use Docker/gVisor for untrusted repos")
        ex["status"] = "awaiting_approval"; ex["current_step"] = None; store.audit(eid, "system", "awaiting_approval", f"{len(changes)} files changed"); store.save(ex)
    except Exception as e:
        _fail(ex, "execution_error", f"{type(e).__name__}: {e}")

def get(eid):
    ex = store.get(eid)
    if not ex: raise EngError("not_found", "Execution not found", 404)
    return ex

def public(ex, with_log=True):
    d = {k: v for k, v in ex.items() if k not in ("manifest", "workspace")}
    for c in d.get("changes", []): c.pop("_abs", None)
    if with_log: d["audit"] = store.audit_log(ex["id"])
    return d

def cancel(eid):
    ex = get(eid)
    if ex["status"] not in ("created", "running"): raise EngError("bad_state", f"Cannot cancel from {ex['status']}", 409)
    _cancel.add(eid)
    if ex["status"] == "created": ex["status"] = "cancelled"; store.save(ex)
    return ex

def diff(eid):
    ex = get(eid); text, h = W.export_patch(ex["changes"]); return {"patch": text, "sha256": h, "files": [c["file"] for c in ex["changes"]]}

def approve(eid, approver):
    ex = get(eid)
    if ex["status"] != "awaiting_approval": raise EngError("bad_state", f"Cannot approve from {ex['status']}", 409)
    if not approver: raise EngError("approver_required", "approver is required", 422)
    text, h = W.export_patch(ex["changes"])
    if h != ex["patch_sha256"]: raise EngError("patch_integrity", "Patch hash mismatch; refusing to apply", 409)
    if any(f["severity"] == "critical" for f in ex["security_review"]): raise EngError("security_block", "Critical security finding blocks approval", 409)
    if ex["tests"] and not ex["tests"]["passed"]: raise EngError("tests_failed", "Tests failed; fix before approving", 409)
    try: W.apply_to_repo(ex["repo"], ex["workspace"], ex["manifest"], ex["changes"], eid)
    except RuntimeError as e: raise EngError("conflict", str(e), 409)
    ex["status"] = "approved"; store.audit(eid, "human", "approved", approver); store.save(ex); return ex

def reject(eid, reason, who):
    ex = get(eid)
    if ex["status"] != "awaiting_approval": raise EngError("bad_state", f"Cannot reject from {ex['status']}", 409)
    ex["status"] = "rejected"; store.audit(eid, "human", "rejected", f"{who}: {reason}"); store.save(ex); return ex

def rollback(eid, who):
    ex = get(eid)
    if ex["status"] != "approved": raise EngError("bad_state", "Only approved (applied) executions can be rolled back", 409)
    try: W.rollback(ex["repo"], ex["changes"], eid)
    except RuntimeError as e: raise EngError("rollback_failed", str(e), 409)
    ex["status"] = "rolled_back"; store.audit(eid, "human", "rolled_back", who); store.save(ex); return ex
