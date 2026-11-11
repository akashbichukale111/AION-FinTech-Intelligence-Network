"""Remediation hand-off. Builds an evidence-backed engineering plan and (if configured) forwards it to Modules 02/03/05/08.
HARD RULE: this module has no deploy capability. Plans stop at PENDING_APPROVAL; approval only releases work to engineers."""
import hashlib, time
from .db import j, lj, one, q, x
from . import incidents as I
from .integrations import call, code_context
from .security import audit

def create_plan(iid, actor, hypothesis_id=None):
    inc = I.get(iid); hyps = lj((one("SELECT body FROM hypotheses WHERE incident_id=?", (iid,)) or {}).get("body"), {}).get("hypotheses", [])
    if not hyps: raise ValueError("run /investigate first - a plan needs root-cause hypotheses")
    h = next((x_ for x_ in hyps if x_["id"] == hypothesis_id), hyps[0]) if hypothesis_id else hyps[0]
    code = {s: code_context(s) for s in h["affected_services"][:3]}
    task = {"title": f"[{inc['id']}] {h['title']}", "severity": inc["severity"], "services": h["affected_services"], "hypothesis": h["explanation"],
            "confidence": h["confidence"], "evidence": [e["text"] for e in h["evidence"][:15]], "next_steps": h["next_steps"],
            "acceptance": ["root cause confirmed or refuted with telemetry", "fix validated by regression tests (Module 05)", "security checks pass (Module 08)", "human approval recorded"]}
    steps = [
        {"n": 1, "module": 2, "action": "create engineering task", "payload": task},
        {"n": 2, "module": 1, "action": "locate relevant code in repository architecture", "result": code},
        {"n": 3, "module": 2, "action": "repair plan: " + "; ".join(h["next_steps"][:4])},
        {"n": 4, "module": 3, "action": "prepare change in ISOLATED workspace (no production access)"},
        {"n": 5, "module": 5, "action": "run tests + regression validation"},
        {"n": 6, "module": 8, "action": "run security / supply-chain checks"},
        {"n": 7, "module": 0, "action": "HUMAN APPROVAL gate - mandatory; no automatic deployment exists in this system"}]
    integ = {}
    for key, mod, purpose, pl in (("m02", 2, "create_task", task), ("m03", 3, "prepare_change", {"task": task, "dry_run": True, "isolated": True})):
        integ[key] = call(mod, purpose, pl)
    for s in steps:
        m = {2: integ["m02"], 3: integ["m03"]}.get(s["module"])
        s["status"] = ("dispatched" if m and m.get("available") else "not_dispatched") if m else "pending"
        if m and not m.get("available"): s["note"] = m.get("reason")
    pid = "PLN-" + hashlib.sha1(f"{iid}{time.time()}".encode()).hexdigest()[:8].upper()
    body = {"id": pid, "incident_id": iid, "hypothesis_id": h["id"], "auto_deploy": False, "steps": steps, "integrations": integ,
            "disclaimer": "Hypothesis-based plan. Nothing has been changed in production."}
    x("INSERT INTO plans(id,incident_id,created_at,status,body) VALUES(?,?,?,?,?)", (pid, iid, time.time(), "PENDING_APPROVAL", j(body)))
    I.log(iid, "plan_created", actor, {"plan": pid, "hypothesis": h["id"]}); audit(actor, "plan.create", pid, {"incident": iid})
    return plans(iid)[0]

def plans(iid):
    I.get(iid); out = q("SELECT id,created_at,status,body FROM plans WHERE incident_id=? ORDER BY created_at DESC", (iid,))
    for p in out: p["body"] = lj(p["body"], {})
    return out

def approve(iid, pid, actor, note=""):
    p = one("SELECT status FROM plans WHERE id=? AND incident_id=?", (pid, iid))
    if not p: raise KeyError(pid)
    if p["status"] != "PENDING_APPROVAL": raise PermissionError(f"plan already {p['status']}")
    x("UPDATE plans SET status='APPROVED_FOR_ENGINEERING' WHERE id=?", (pid,))
    I.log(iid, "plan_approved", actor, {"plan": pid, "note": note[:500], "deploys_nothing": True}); audit(actor, "plan.approve", pid, {})
    return plans(iid)[0]
