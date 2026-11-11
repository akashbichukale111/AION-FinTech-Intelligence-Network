import json, os, secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import db, governance as gov, engine as eng
from db import q, q1, x, now, nid, sha, canon

app = FastAPI(title="FORGEX Module 10 — Release Risk, Governance & Assurance")
P = "/api/v1"
DEPLOY_ENABLED = False  # deployment integration must be separately authorized; no deploy code exists in this module

@app.on_event("startup")
def startup():
    db.init(); gov.seed_policy()
    if not q1("SELECT 1 x FROM users"):
        key = os.environ.get("FORGEX_BOOTSTRAP_ADMIN_KEY") or secrets.token_urlsafe(24)
        x("INSERT INTO users VALUES('admin','Bootstrap Admin','admin','human',?,1)", (sha(key),))
        if not os.environ.get("FORGEX_BOOTSTRAP_ADMIN_KEY"): print(f"[M10] one-time bootstrap admin key (shown once): {key}")

def auth(x_api_key: Optional[str] = Header(None)):
    u = q1("SELECT * FROM users WHERE key_hash=? AND active=1", (sha(x_api_key),)) if x_api_key else None
    if not u: raise HTTPException(401, "invalid or missing X-API-Key")
    return u

def need(user, action, rid=None):
    pol = gov.active_policy()
    if not gov.can(user, action, pol):
        db.audit(user, f"{action}_denied", rid, result="denied", details={"role": user["role"], "kind": user["kind"]}, policy_version=pol["version"])
        raise HTTPException(403, f"role '{user['role']}' ({user['kind']}) may not perform '{action}'")
    return pol

def rel_or_404(rid):
    r = eng.load_rel(rid)
    if not r: raise HTTPException(404, "release not found")
    return r

def set_state(user, rel, new, action, **kw):
    x("UPDATE releases SET status=? WHERE id=?", (new, rel["id"]))
    db.audit(user, action, rel["id"], prev_state=rel["status"], new_state=new, agent_id=rel["agent_id"], **kw)

# ---------- models ----------
class CandidateIn(BaseModel):
    version: str; commit_sha: str; title: str; author: str
    base_sha: Optional[str] = None; description: str = ""; repo: Optional[str] = None; is_financial: bool = False
    diff: Optional[str] = None; changed_files: list[str] = []; lines_added: int = 0; lines_removed: int = 0
    agent_id: Optional[str] = None; artifact_ref: Optional[str] = None; pr: Optional[dict] = None
class EvidenceIn(BaseModel):
    kind: str; payload: dict; source_ref: Optional[str] = None; commit_sha: Optional[str] = None
class DecisionIn(BaseModel):
    kind: str = "release"; comment: str = ""
class OutcomeIn(BaseModel):
    outcome: str; notes: str = ""; deployment_ref: Optional[str] = None
class PolicyIn(BaseModel):
    body: dict
class UserIn(BaseModel):
    id: str; name: str; role: str; kind: str = "human"

# ---------- candidates ----------
@app.post(P + "/releases/candidates")
def create_candidate(b: CandidateIn, user=Depends(auth)):
    pol = need(user, "create")
    d = b.dict()
    if b.pr:
        try: d.update({k: v for k, v in eng.GitHubProvider().fetch(b.pr["repo"], b.pr["number"]).items() if k in ("commit_sha", "base_sha", "diff") or not d.get(k)}); d["repo"] = d["repo"] or b.pr["repo"]
        except Exception as e: raise HTTPException(502, f"GitHub provider error: {e}")
    files, add, rem, dh = d["changed_files"], d["lines_added"], d["lines_removed"], None
    if d["diff"]: files, add, rem = eng.parse_diff(d["diff"]); dh = sha(d["diff"])
    agent = user["id"] if user["kind"] == "agent" else d["agent_id"]
    if q1("SELECT 1 x FROM users WHERE id=? AND kind='human'", (d["author"],)) is None: raise HTTPException(422, "author must be a registered human user id")
    rid = nid("rel")
    x("INSERT INTO releases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rid, d["version"], d["commit_sha"], d["base_sha"], d["title"], d["description"], d["repo"], d["author"], agent, user["id"], None, now(), "DRAFT",
        int(d["is_financial"]), dh, json.dumps(files), add, rem, 0, json.dumps(b.pr) if b.pr else None, d["artifact_ref"], None, pol["version"]))
    db.audit(user, "release_candidate_created", rid, new_state="DRAFT", result="ok", details={"commit": d["commit_sha"], "diff_sha256": dh, "files": len(files)}, policy_version=pol["version"], agent_id=agent)
    return eng.load_rel(rid)

@app.get(P + "/releases/candidates")
def list_candidates(user=Depends(auth)):
    need(user, "view"); rows = q("SELECT id,version,commit_sha,title,author,status,is_financial,created_at,outcome FROM releases ORDER BY created_at DESC")
    for r in rows:
        g = q1("SELECT status FROM gate_evals WHERE release_id=? ORDER BY created_at DESC LIMIT 1", (r["id"],)); r["gate_status"] = g["status"] if g else "NOT_VERIFIED"
    return rows

@app.get(P + "/releases/candidates/{rid}")
def get_candidate(rid: str, user=Depends(auth)):
    need(user, "view"); r = rel_or_404(rid)
    ev = q("SELECT id,module,kind,source_ref,collector,commit_sha,collected_at FROM evidence WHERE release_id=? ORDER BY collected_at", (rid,))
    return {**r, "evidence": ev, "approvals": q("SELECT * FROM approvals WHERE release_id=? ORDER BY created_at", (rid,))}

# ---------- evidence ----------
@app.post(P + "/releases/{rid}/evidence")
def add_evidence(rid: str, b: EvidenceIn, user=Depends(auth)):
    need(user, "evidence", rid); rel_or_404(rid)
    if b.kind not in eng.KIND_MODULE: raise HTTPException(422, f"kind must be one of {sorted(eng.KIND_MODULE)}")
    eid = nid("ev"); x("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, rid, eng.KIND_MODULE[b.kind], b.kind, json.dumps(b.payload), b.source_ref, user["id"], b.commit_sha, sha(canon(b.payload)), now()))
    db.audit(user, "evidence_recorded", rid, evidence_refs=[eid], result="ok", details={"kind": b.kind, "commit": b.commit_sha, "collector": user["id"]})
    return {"id": eid}

@app.get(P + "/releases/{rid}/evidence")
def list_evidence(rid: str, user=Depends(auth)):
    need(user, "view"); rel = rel_or_404(rid); ctx = eng.Ctx(rel, gov.active_policy()); out = []
    for k, r in ctx.rows.items():
        _, _, prob = ctx.get(k); out.append({**{c: r[c] for c in ("id", "module", "kind", "collector", "commit_sha", "collected_at", "source_ref")}, "usable": prob is None, "problem": prob, "payload": json.loads(r["payload"])})
    return out

@app.post(P + "/releases/{rid}/collect")
def collect(rid: str, user=Depends(auth)):
    need(user, "evidence", rid); rel = rel_or_404(rid); res = eng.collect_from_modules(rel)
    db.audit(user, "evidence_collect_from_modules", rid, result="ok" if not res["errors"] else "partial", details=res); return res

# ---------- assessment ----------
@app.post(P + "/releases/{rid}/assess-risk")
def assess_risk(rid: str, user=Depends(auth)):
    pol = need(user, "assess", rid); rel = rel_or_404(rid); ev = eng.evaluate(rel, persist=True)
    db.audit(user, "risk_assessed", rid, result=ev["risk_summary"]["overall_severity"], details=ev["risk_summary"], policy_version=pol["version"], agent_id=rel["agent_id"])
    return {"summary": ev["risk_summary"], "risks": ev["risks"]}

@app.post(P + "/releases/{rid}/verify")
def verify(rid: str, user=Depends(auth)):
    pol = need(user, "assess", rid); rel = rel_or_404(rid); ev = eng.evaluate(rel, persist=True)
    db.audit(user, "independent_verification", rid, result=canon(ev["verification"]["summary"]), policy_version=pol["version"], agent_id=rel["agent_id"],
             evidence_refs=sorted({r for c in ev["verification"]["checks"] for r in c["evidence_refs"]}))
    return ev["verification"]

@app.get(P + "/releases/{rid}/gates")
def gates(rid: str, user=Depends(auth)):
    need(user, "view"); ev = eng.evaluate(rel_or_404(rid), persist=True); return {k: ev[k] for k in ("release_id", "status", "blockers", "warnings", "gates", "approval_requirement", "policy_version", "evaluated_at")}

@app.get(P + "/releases/{rid}/assurance")
def assurance(rid: str, user=Depends(auth)):
    need(user, "view"); return eng.evaluate(rel_or_404(rid), persist=True)

@app.get(P + "/releases/{rid}/fintech-assurance")
def fintech(rid: str, user=Depends(auth)):
    need(user, "view"); rel = rel_or_404(rid)
    if not rel["is_financial"]: return {"status": "NOT_APPLICABLE", "checks": []}
    return eng.fintech_assurance(eng.Ctx(rel, gov.active_policy()))

@app.get(P + "/releases/{rid}/rollback-readiness")
def rollback(rid: str, user=Depends(auth)):
    need(user, "view"); rel = rel_or_404(rid); r = eng.rollback_readiness(eng.Ctx(rel, gov.active_policy()))
    db.audit(user, "rollback_readiness_checked", rid, result=r["status"], details={"checks": [(c["check"], c["status"]) for c in r["checks"]]}); return r

# ---------- approval workflow ----------
@app.post(P + "/releases/{rid}/submit-approval")
def submit(rid: str, user=Depends(auth)):
    pol = need(user, "submit", rid); rel = rel_or_404(rid)
    if rel["status"] not in ("DRAFT", "REJECTED"): raise HTTPException(409, f"cannot submit from state {rel['status']}")
    if rel["status"] == "REJECTED":  # resubmission starts a fresh approval cycle; old approvals no longer count
        x("UPDATE releases SET cycle=cycle+1,status='DRAFT' WHERE id=?", (rid,)); db.audit(user, "release_reopened", rid, prev_state="REJECTED", new_state="DRAFT", details={"cycle": rel["cycle"] + 1}); rel = eng.load_rel(rid)
    ev = eng.evaluate(rel, persist=True)
    if ev["status"] != "PENDING_APPROVAL":
        db.audit(user, "submit_refused", rid, result=ev["status"], details={"blockers": ev["blockers"], "gates": {g["key"]: g["status"] for g in ev["gates"]}}, policy_version=pol["version"])
        raise HTTPException(409, {"message": f"cannot request approval while status is {ev['status']}", "blockers": ev["blockers"], "gates": {g["key"]: g["status"] for g in ev["gates"]}})
    x("UPDATE releases SET submitted_by=? WHERE id=?", (user["id"], rid)); rel = eng.load_rel(rid)
    set_state(user, rel, "PENDING_APPROVAL", "submitted_for_approval", result="ok", policy_version=pol["version"], details={"requirement": ev["approval_requirement"]})
    return {"status": "PENDING_APPROVAL", "requirement": ev["approval_requirement"]}

def decide(rid, b, user, decision):
    pol = gov.active_policy(); rel = rel_or_404(rid)
    act = decision
    def deny(why, code=403):
        db.audit(user, f"{act}_denied", rid, result="denied", decision=decision, details={"reason": why, "kind": b.kind}, policy_version=pol["version"], agent_id=rel["agent_id"]); raise HTTPException(code, why)
    if user["kind"] != "human": deny("AI agents can never approve or reject")
    roles = pol["code_review"]["roles"] if b.kind == "code_review" else pol["release_approval"]["roles"]
    if b.kind not in ("code_review", "release"): raise HTTPException(422, "kind must be code_review or release")
    if user["role"] not in roles: deny(f"role {user['role']} cannot give {b.kind} {decision}")
    if user["id"] in gov.forbidden_actors(rel): deny("separation of duties: author/creator/submitter/agent owner cannot approve or reject this release")
    if b.kind == "release" and rel["status"] != "PENDING_APPROVAL": deny(f"release not pending approval (state {rel['status']})", 409)
    if b.kind == "code_review" and rel["status"] not in ("DRAFT", "PENDING_APPROVAL"): deny(f"code review not accepted in state {rel['status']}", 409)
    if b.kind == "release" and pol["sod"]["reviewer_cannot_release_approve"] and user["id"] in {a["actor"] for a in gov.valid_approvals(rel, "code_review")}: deny("separation of duties: code reviewer cannot also give release approval")
    if decision == "reject" and not b.comment.strip(): raise HTTPException(422, "rejection requires a comment")
    if q1("SELECT 1 x FROM approvals WHERE release_id=? AND cycle=? AND kind=? AND actor=?", (rid, rel["cycle"], b.kind, user["id"])): deny("already decided in this cycle", 409)
    if decision == "approve" and b.kind == "release":
        ev = eng.evaluate(rel)
        if ev["status"] in ("BLOCKED", "NOT_VERIFIED"): deny(f"cannot approve while release is {ev['status']}: {ev['blockers'] or 'unverified gates'}", 409)
    aid = nid("apr"); exp = (datetime.now(timezone.utc) + timedelta(hours=pol["approval_ttl_hours"])).isoformat()
    x("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (aid, rid, rel["cycle"], b.kind, decision, user["id"], user["role"], rel["commit_sha"], pol["version"], b.comment, now(), exp))
    db.audit(user, f"{b.kind}_{decision}", rid, prev_state=rel["status"], new_state=rel["status"], decision=decision, result="recorded", policy_version=pol["version"], details={"approval": aid, "comment": b.comment, "expires_at": exp}, agent_id=rel["agent_id"])
    rel = eng.load_rel(rid)
    if decision == "reject": set_state(user, rel, "REJECTED", "release_rejected", decision="reject", policy_version=pol["version"], details={"by": user["id"], "kind": b.kind}); return {"status": "REJECTED"}
    if b.kind == "release":
        ev = eng.evaluate(rel, persist=True)
        if next(g for g in ev["gates"] if g["key"] == "human_approvals")["status"] == "PASS" and ev["status"] in ("READY", "READY_WITH_WARNINGS"):
            set_state(user, rel, "APPROVED", "release_approved", decision="approve", policy_version=pol["version"], result=ev["status"]); return {"status": "APPROVED", "gate_status": ev["status"]}
        return {"status": rel["status"], "gate_status": ev["status"], "detail": next(g for g in ev["gates"] if g["key"] == "human_approvals")["detail"]}
    return {"status": rel["status"], "recorded": "code_review approval"}

@app.post(P + "/releases/{rid}/approve")
def approve(rid: str, b: DecisionIn, user=Depends(auth)): return decide(rid, b, user, "approve")
@app.post(P + "/releases/{rid}/reject")
def reject(rid: str, b: DecisionIn, user=Depends(auth)): return decide(rid, b, user, "reject")

@app.post(P + "/releases/{rid}/outcome")
def outcome(rid: str, b: OutcomeIn, user=Depends(auth)):
    pol = need(user, "outcome", rid); rel = rel_or_404(rid)
    if b.outcome not in ("released", "rolled_back", "failed"): raise HTTPException(422, "outcome must be released|rolled_back|failed")
    if b.outcome == "released" and rel["status"] != "APPROVED": raise HTTPException(409, "only an APPROVED release can be recorded as released")
    x("UPDATE releases SET outcome=? WHERE id=?", (b.outcome, rid)); set_state(user, rel, b.outcome.upper(), "release_outcome_recorded", result=b.outcome, details={"notes": b.notes, "deployment_ref": b.deployment_ref, "note": "recorded by human; this module does not deploy"}, policy_version=pol["version"])
    return {"status": b.outcome.upper()}

@app.post(P + "/releases/{rid}/deploy")
def deploy(rid: str, user=Depends(auth)):
    db.audit(user, "deploy_attempt_blocked", rid, result="disabled")
    raise HTTPException(403, "Deployment is disabled. It requires a separately authorized deployment integration that is not configured.")

# ---------- reports / history ----------
@app.get(P + "/releases/{rid}/release-notes", response_class=PlainTextResponse)
def notes(rid: str, user=Depends(auth)):
    need(user, "view"); rel = rel_or_404(rid); return eng.release_notes(rel, eng.evaluate(rel))

@app.get(P + "/releases/{rid}/report", response_class=PlainTextResponse)
def report(rid: str, user=Depends(auth)):
    need(user, "view"); rel = rel_or_404(rid); ev = eng.evaluate(rel, persist=True); L = [eng.release_notes(rel, ev), "", "## Risk assessment"]
    for r in ev["risks"]: L.append(f"- **{r['category']}**: {r['severity']} (confidence {r['confidence']}) — {'; '.join(r['contributing_factors'])} → _{r['recommended_mitigation']}_")
    L += ["", "## Independent verification"] + [f"- {c['check']}: **{c['status']}** — {c['detail']}" for c in ev["verification"]["checks"]]
    L += ["", "## Gates"] + [f"- {g['id']}. {g['name']}: **{g['status']}**{' (mandatory)' if g['mandatory'] else ''} — {g['detail']}" for g in ev["gates"]]
    L += ["", "## Approvals"] + [f"- {a['kind']} {a['decision']} by {a['actor']} ({a['role']}) cycle {a['cycle']} expires {a['expires_at']}" for a in q("SELECT * FROM approvals WHERE release_id=?", (rid,))]
    rb = ev["verification"]["rollback"]; L += ["", f"## Rollback: {rb['status']}"] + rb["rollback_plan"]
    av = db.audit_verify(); L += ["", f"## Audit chain: {'VALID' if av['valid'] else 'BROKEN'} ({av['records']} records, head {av['head_hash'][:16]}…)", av["limitation"]]
    return "\n".join(L)

@app.get(P + "/releases/queue")
def queue(user=Depends(auth)):
    need(user, "view"); return [r for r in list_candidates(user) if r["status"] == "PENDING_APPROVAL"]

@app.get(P + "/releases/history")
def history(user=Depends(auth)):
    need(user, "view"); return q("SELECT id,version,title,status,outcome,created_at FROM releases WHERE status NOT IN ('DRAFT') ORDER BY created_at DESC")

# ---------- governance ----------
@app.get(P + "/me")
def me(user=Depends(auth)): return {k: user[k] for k in ("id", "name", "role", "kind")}
@app.get(P + "/governance/policies")
def policies(user=Depends(auth)): need(user, "view"); return [{**r, "body": json.loads(r["body"])} for r in q("SELECT * FROM policies ORDER BY version DESC")]
@app.post(P + "/governance/policies")
def new_policy(b: PolicyIn, user=Depends(auth)):
    need(user, "admin"); v = gov.new_policy_version(b.body, user["id"]); db.audit(user, "policy_version_created", result="ok", policy_version=v, details={"changed_keys": list(b.body)}); return {"version": v}
@app.get(P + "/governance/audit")
def audit_list(release_id: Optional[str] = None, limit: int = 200, user=Depends(auth)):
    need(user, "view"); return q("SELECT * FROM audit WHERE (?1 IS NULL OR release_id=?1) ORDER BY seq DESC LIMIT ?2", (release_id, limit))
@app.get(P + "/governance/audit/verify")
def audit_check(user=Depends(auth)): need(user, "view"); return db.audit_verify()
@app.post(P + "/governance/users")
def add_user(b: UserIn, user=Depends(auth)):
    need(user, "admin")
    if b.role not in gov.ROLES or b.kind not in ("human", "agent"): raise HTTPException(422, "bad role/kind")
    if b.kind == "agent" and b.role != "agent": raise HTTPException(422, "agents must have role 'agent'")
    key = secrets.token_urlsafe(24); x("INSERT INTO users VALUES(?,?,?,?,?,1)", (b.id, b.name, b.role, b.kind, sha(key)))
    db.audit(user, "user_created", result="ok", details={"id": b.id, "role": b.role, "kind": b.kind}); return {"id": b.id, "api_key": key, "note": "shown once"}
@app.get(P + "/governance/users")
def users(user=Depends(auth)): need(user, "admin"); return q("SELECT id,name,role,kind,active FROM users")

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
@app.get("/center")
def center(): return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))
