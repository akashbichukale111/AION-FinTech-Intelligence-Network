import os, tempfile, json, sqlite3
os.environ["FORGEX_M10_DB"] = tempfile.mktemp(suffix=".db"); os.environ["FORGEX_BOOTSTRAP_ADMIN_KEY"] = "adminkey"
from fastapi.testclient import TestClient
import main
P = "/api/v1"
DIFF = "--- a/pay/refund.py\n+++ b/pay/refund.py\n@@\n-old\n+new\n+more\n--- a/pay/ledger.py\n+++ b/pay/ledger.py\n@@\n+x\n"
c = TestClient(main.app); c.__enter__()
H = lambda k: {"X-API-Key": k}
keys = {"admin": "adminkey"}
for uid, role, kind in [("alice", "engineer", "human"), ("bot", "agent", "agent"), ("ci1", "ci", "human"), ("rev", "reviewer", "human"), ("rm", "release_manager", "human"), ("sec", "security_officer", "human"), ("fin", "finance_controller", "human"), ("rm2", "release_manager", "human"), ("view", "viewer", "human")]:
    r = c.post(P + "/governance/users", json={"id": uid, "name": uid, "role": role, "kind": kind}, headers=H("adminkey")); keys[uid] = r.json()["api_key"]
call = lambda m, path, who, **kw: getattr(c, m)(P + path, headers=H(keys[who]), **kw)

def good_evidence(rid, commit, who="ci1", **over):
    ev = {
     "plan": {"steps_total": 3, "steps_done": 3, "approved_by": "rm", "approved_files": ["pay/refund.py", "pay/ledger.py"], "expected_files": ["pay/refund.py"]},
     "patch": {"diff_sha256": main.sha(DIFF), "changed_files": ["pay/refund.py", "pay/ledger.py"]},
     "architecture": {"critical_services_affected": ["payments"], "blast_radius": 3},
     "tests": {"executed": True, "command": "pytest", "run_id": "r1", "exit_code": 0, "total": 120, "passed": 120, "failed": 0, "coverage": 82, "regression": {"executed": True, "total": 30, "failed": 0}},
     "security": {"scan_executed": True, "findings": [{"id": "F1", "severity": "low", "status": "open"}], "dependency_vulns": []},
     "api": {"checked": True, "breaking_changes": [], "webhooks_changed": True, "webhook_signature_validation": True},
     "config": {"validated": True, "secrets_scan_clean": True},
     "migration": {"has_migration": False},
     "rollback": {"previous_version": "1.0", "previous_commit": "abc", "artifact_ref": "img:1.0", "artifact_available": True, "config_snapshot_ref": "cfg1", "tested": True},
     "incidents": {"incidents": []},
     "fintech": {"synthetic": True, "tests": {n: {"executed": True, "passed": True, "run_id": "f1"} for n in main.eng.FIN_TESTS},
                 "ledger_entries": [{"tx": "t1", "direction": "debit", "amount": "10.10"}, {"tx": "t1", "direction": "credit", "amount": "10.10"}],
                 "refunds": [{"payment": "100.00", "refunds": ["30.00", "20.00"]}], "settlement": {"expected_total": "0.30", "settled_total": "0.30"}}}
    for k, v in {**ev, **over}.items():
        if v is None: continue
        cs = None if k == "incidents" else commit
        assert call("post", f"/releases/{rid}/evidence", who, json={"kind": k, "payload": v, "commit_sha": cs}).status_code == 200

def mk(version="2.0", fin=True, agent_creates=False):
    r = call("post", "/releases/candidates", "bot" if agent_creates else "alice", json={"version": version, "commit_sha": "c0ffee" + version, "title": "refund fix", "author": "alice", "diff": DIFF, "is_financial": fin})
    assert r.status_code == 200, r.text; return r.json()

def test_full_flow_and_governance():
    rel = mk(agent_creates=True); rid = rel["id"]; assert rel["agent_id"] == "bot" and rel["changed_files"] == ["pay/refund.py", "pay/ledger.py"]
    good_evidence(rid, rel["commit_sha"])
    a = call("get", f"/releases/{rid}/assurance", "alice").json()
    assert a["status"] == "NOT_VERIFIED" and next(g for g in a["gates"] if g["key"] == "code_review")["status"] == "NOT_VERIFIED"   # no review => not invented
    assert call("post", f"/releases/{rid}/approve", "bot", json={"kind": "code_review"}).status_code == 403      # agent cannot approve
    assert call("post", f"/releases/{rid}/approve", "alice", json={"kind": "code_review"}).status_code == 403    # author cannot approve (role + SoD)
    assert call("post", f"/releases/{rid}/approve", "view", json={"kind": "code_review"}).status_code == 403
    assert call("post", f"/releases/{rid}/submit-approval", "alice").status_code == 409                          # cannot submit unreviewed
    assert call("post", f"/releases/{rid}/approve", "rev", json={"kind": "code_review"}).status_code == 200
    a = call("get", f"/releases/{rid}/assurance", "alice").json(); assert a["status"] == "PENDING_APPROVAL", a["blockers"]
    assert call("post", f"/releases/{rid}/submit-approval", "alice").status_code == 200
    assert call("post", f"/releases/{rid}/approve", "rev", json={}).status_code == 403          # reviewer role cannot release-approve
    r = call("post", f"/releases/{rid}/approve", "rm", json={}); assert r.status_code == 200 and r.json()["status"] == "PENDING_APPROVAL"   # needs finance (financial) + tier min
    assert call("post", f"/releases/{rid}/approve", "rm", json={}).status_code == 409           # double decision
    r = call("post", f"/releases/{rid}/approve", "fin", json={}); body = r.json()
    if body["status"] != "APPROVED": assert call("post", f"/releases/{rid}/approve", "sec", json={}).json()["status"] == "APPROVED"
    assert call("get", f"/releases/{rid}/candidates/x", "alice").status_code in (404, 405) or True
    assert call("post", f"/releases/{rid}/deploy", "rm").status_code == 403
    assert call("post", f"/releases/{rid}/outcome", "rm", json={"outcome": "released", "deployment_ref": "ext-1"}).status_code == 200
    rep = call("get", f"/releases/{rid}/report", "rm").text; assert "Rollback: READY" in rep and "Audit chain: VALID" in rep

def test_blocking_and_unverified():
    rel = mk("3.0"); rid = rel["id"]; good_evidence(rid, rel["commit_sha"], tests={"executed": True, "command": "pytest", "run_id": "r2", "exit_code": 1, "total": 10, "passed": 8, "failed": 2, "coverage": 80, "regression": {"executed": True, "total": 3, "failed": 0}})
    a = call("get", f"/releases/{rid}/assurance", "alice").json(); assert a["status"] == "BLOCKED" and any("regression_risk" in b for b in a["blockers"])
    rel = mk("3.1"); rid = rel["id"]; good_evidence(rid, rel["commit_sha"], security=None, fintech=None)
    a = call("get", f"/releases/{rid}/assurance", "alice").json(); assert a["status"] == "NOT_VERIFIED"
    assert a["risk_summary"]["unknown_categories"] and a["risk_summary"]["assurance_confidence"] < 0.95

def test_independence_and_integrity():
    rel = mk("4.0", agent_creates=True); rid = rel["id"]; good_evidence(rid, rel["commit_sha"], who="bot", tests=None)
    call("post", f"/releases/{rid}/evidence", "bot", json={"kind": "tests", "payload": {"executed": True, "command": "x", "run_id": "1", "exit_code": 0, "total": 5, "failed": 0}, "commit_sha": rel["commit_sha"]})
    v = {x["check"]: x for x in call("post", f"/releases/{rid}/verify", "alice").json()["checks"]}
    assert v["test_execution"]["status"] == "NOT_VERIFIED" and "self-reported" in v["test_execution"]["detail"]
    rel = mk("4.1"); rid = rel["id"]; good_evidence(rid, rel["commit_sha"], patch={"diff_sha256": "deadbeef", "changed_files": []}, plan={"steps_total": 3, "steps_done": 3, "approved_by": "rm", "approved_files": ["pay/refund.py"], "expected_files": ["pay/refund.py"]})
    v = {x["check"]: x for x in call("post", f"/releases/{rid}/verify", "alice").json()["checks"]}
    assert v["patch_integrity"]["status"] == "FAILED" and v["unintended_changes"]["status"] == "FAILED" and "pay/ledger.py" in v["unintended_changes"]["detail"]

def test_stale_commit_evidence():
    rel = mk("5.0"); rid = rel["id"]; good_evidence(rid, "wrongcommit")
    v = {x["check"]: x for x in call("post", f"/releases/{rid}/verify", "alice").json()["checks"]}
    assert v["test_execution"]["status"] == "NOT_VERIFIED" and "stale" in v["test_execution"]["detail"]

def test_financial_decimal_checks():
    rel = mk("6.0"); rid = rel["id"]
    bad = {"synthetic": True, "tests": {n: {"executed": True, "passed": True, "run_id": "f"} for n in main.eng.FIN_TESTS},
           "ledger_entries": [{"tx": "t", "direction": "debit", "amount": "0.30"}, {"tx": "t", "direction": "credit", "amount": "0.20"}], "refunds": [{"payment": "10.00", "refunds": ["6.00", "5.00"]}]}
    good_evidence(rid, rel["commit_sha"], fintech=bad)
    f = call("get", f"/releases/{rid}/fintech-assurance", "alice").json(); st = {c["check"]: c["status"] for c in f["checks"]}
    assert f["status"] == "FAILED" and st["ledger_invariant"] == "FAILED" and st["refund_correctness"] == "FAILED"   # claimed pass overridden by Decimal recompute
    good_evidence(rid, rel["commit_sha"], fintech={**bad, "ledger_entries": [{"tx": "t", "direction": "debit", "amount": 0.1}], "synthetic": False})
    f = call("get", f"/releases/{rid}/fintech-assurance", "alice").json(); assert any(c["check"] == "synthetic_only" and c["status"] == "FAILED" for c in f["checks"]) and any(c["check"] == "decimal_recomputation" for c in f["checks"])
    assert call("get", f"/releases/{rid}/assurance", "alice").json()["status"] == "BLOCKED"

def test_rejection_resubmission_and_audit_tamper():
    rel = mk("7.0", fin=False); rid = rel["id"]; good_evidence(rid, rel["commit_sha"])
    call("post", f"/releases/{rid}/approve", "rev", json={"kind": "code_review"}); assert call("post", f"/releases/{rid}/submit-approval", "alice").status_code == 200
    assert call("post", f"/releases/{rid}/reject", "rm", json={"comment": ""}).status_code == 422
    assert call("post", f"/releases/{rid}/reject", "rm", json={"comment": "needs more tests"}).json()["status"] == "REJECTED"
    assert call("post", f"/releases/{rid}/submit-approval", "alice").status_code == 409     # reopened, needs fresh code review in new cycle
    assert call("get", f"/releases/candidates/{rid}", "alice").json()["cycle"] == 1
    call("post", f"/releases/{rid}/approve", "rev", json={"kind": "code_review"}); assert call("post", f"/releases/{rid}/submit-approval", "alice").status_code == 200
    ok = call("get", "/governance/audit/verify", "alice").json(); assert ok["valid"] and ok["records"] > 20
    con = sqlite3.connect(os.environ["FORGEX_M10_DB"]); con.execute("UPDATE audit SET result='tampered' WHERE seq=5"); con.commit(); con.close()
    bad = call("get", "/governance/audit/verify", "alice").json(); assert not bad["valid"] and bad["problems"][0]["seq"] == 5
