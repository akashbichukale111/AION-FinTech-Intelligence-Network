import json
from db import q, q1, x, now, canon

ALL_GATES = ["plan_completion","code_review","required_tests","critical_regression","security","financial_invariants","api_compatibility","configuration","human_approvals","rollback"]
ROLES = ["viewer","engineer","ci","reviewer","release_manager","security_officer","finance_controller","admin","agent"]
DEFAULT_POLICY = {
  "name": "default", "evidence_max_age_hours": 72, "min_coverage": 60.0, "block_security_severity": "HIGH", "approval_ttl_hours": 48,
  "code_review": {"min": 1, "roles": ["reviewer", "release_manager"]},
  "release_approval": {
    "roles": ["release_manager", "security_officer", "finance_controller"],
    "by_risk": {"NONE": {"min": 1, "required_roles": []}, "LOW": {"min": 1, "required_roles": []},
                "MEDIUM": {"min": 1, "required_roles": ["release_manager"]},
                "HIGH": {"min": 2, "required_roles": ["release_manager", "security_officer"]},
                "CRITICAL": {"min": 2, "required_roles": ["release_manager", "security_officer"]}},
    "financial_extra_roles": ["finance_controller"]},
  "unknown_risk_as": "HIGH", "warning_risk_at": "MEDIUM",
  "sod": {"reviewer_cannot_release_approve": True},
  "mandatory_gates": ALL_GATES, "warning_gates": [],
  "permissions": {
    "view": ROLES, "create": ["engineer", "release_manager", "admin", "agent"],
    "evidence": ["engineer", "ci", "release_manager", "security_officer", "finance_controller", "admin", "agent"],
    "assess": ["engineer", "ci", "reviewer", "release_manager", "security_officer", "finance_controller", "admin"],
    "submit": ["engineer", "release_manager", "admin"], "outcome": ["release_manager", "admin"], "admin": ["admin"]},
  "human_only_actions": ["approve", "reject", "submit", "outcome", "admin"],
}

def active_policy():
    r = q1("SELECT * FROM policies WHERE active=1 ORDER BY version DESC LIMIT 1")
    p = json.loads(r["body"]); p["version"] = r["version"]; return p

def seed_policy():
    if not q1("SELECT 1 x FROM policies"):
        x("INSERT INTO policies(version,body,created_by,created_at,active) VALUES(1,?,?,?,1)", (json.dumps(DEFAULT_POLICY), "system", now()))

def new_policy_version(body, actor):
    cur = q1("SELECT MAX(version) v FROM policies")["v"] or 0
    merged = {**DEFAULT_POLICY, **body}
    x("UPDATE policies SET active=0"); x("INSERT INTO policies(version,body,created_by,created_at,active) VALUES(?,?,?,?,1)", (cur + 1, json.dumps(merged), actor, now()))
    return cur + 1

def can(user, action, pol):
    if user["kind"] == "agent" and action in pol["human_only_actions"]: return False
    return user["role"] in pol["permissions"].get(action, [])

def forbidden_actors(rel):
    return {a for a in (rel["author"], rel["created_by"], rel["agent_id"], rel["submitted_by"]) if a}

def valid_approvals(rel, kind, decision="approve"):
    out, seen = [], set()
    for a in q("SELECT * FROM approvals WHERE release_id=? AND cycle=? AND kind=? AND decision=? ORDER BY created_at", (rel["id"], rel["cycle"], kind, decision)):
        if a["commit_sha"] != rel["commit_sha"] or a["expires_at"] < now() or a["actor"] in seen: continue
        seen.add(a["actor"]); out.append(a)
    return out

def approval_requirement(rel, pol, eff_sev):
    req = pol["release_approval"]["by_risk"][eff_sev]
    roles = set(req["required_roles"]) | (set(pol["release_approval"]["financial_extra_roles"]) if rel["is_financial"] else set())
    return {"min_approvers": max(req["min"], len(roles)), "required_roles": sorted(roles), "risk_tier": eff_sev}
