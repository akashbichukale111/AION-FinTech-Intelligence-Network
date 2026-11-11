import json, os, tempfile
from datetime import datetime, timezone
D = tempfile.mkdtemp()
os.environ["FORGEX_M01_SCAN_DIR"] = D
os.environ["FORGEX_DB_URL"] = f"sqlite:///{D}/t.db"
from fastapi.testclient import TestClient
from app.main import app

SCAN = {"repo_id": "payments-api", "scan_id": "scan-1", "scanned_at": datetime.now(timezone.utc).isoformat(),
  "files": [
   {"path": "app/payments/service.py", "language": "python", "imports": ["app.db.models", "app.payments.gateway"], "symbols": ["create_payment", "charge_card", "retry_charge"]},
   {"path": "app/payments/gateway.py", "language": "python", "symbols": ["submit_charge", "timeout"]},
   {"path": "app/api/routes.py", "language": "python", "imports": ["app.payments.service"], "symbols": ["post_payment"]},
   {"path": "app/db/models.py", "kind": "model", "symbols": ["Payment", "IdempotencyKey"]},
   {"path": "migrations/0001_init.sql", "kind": "migration"},
   {"path": "tests/test_payments.py", "is_test": True, "imports": ["app.payments.service"]}],
  "apis": [{"method": "POST", "path": "/payments", "file": "app/api/routes.py"}],
  "configs": [{"path": "config/settings.py", "keys": ["GATEWAY_TIMEOUT"]}], "dependencies": []}
(open(f"{D}/payments-api.json", "w")).write(json.dumps(SCAN))
json.dump({**SCAN, "repo_id": "secret", "allowed_users": ["bob"]}, open(f"{D}/secret.json", "w"))
c = TestClient(app); H = {"X-Actor": "alice"}
TASK = {"title": "Customers charged twice on retry", "description": "Duplicate payment: after a gateway timeout the retry creates a second charge for the same order.",
        "type": "fintech_reliability", "priority": "high", "repo_id": "payments-api", "logs": "WARN timeout in app/payments/gateway.py retry_charge attempt 2"}

def test_full_flow():
    assert c.post("/api/v1/engineering/tasks", json=TASK).status_code == 401
    assert c.post("/api/v1/engineering/tasks", json={**TASK, "repo_id": "secret"}, headers=H).status_code == 403
    assert c.post("/api/v1/engineering/tasks", json={**TASK, "repo_id": "nope"}, headers=H).status_code == 424
    assert c.post("/api/v1/engineering/tasks", json={**TASK, "title": "x"}, headers=H).status_code == 422
    r = c.post("/api/v1/engineering/tasks", json=TASK, headers=H); assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert c.post("/api/v1/engineering/tasks", json=TASK, headers=H).status_code == 409
    assert c.post(f"/api/v1/engineering/tasks/{tid}/plans", headers=H).status_code == 409  # not analyzed
    a = c.post(f"/api/v1/engineering/tasks/{tid}/analyze", headers=H).json()["analysis"]
    assert a["hypotheses"] and all(h["status"] == "hypothesis_unverified" for h in a["hypotheses"])
    paths = [f["path"] for f in a["impact"]["files_to_change"]]
    assert "app/payments/service.py" in paths and "app/payments/gateway.py" in paths
    assert "app/api/routes.py" in paths or any(d["path"] == "app/api/routes.py" for d in a["impact"]["dependent_modules"])
    assert any(d["path"] == "app/payments/service.py" for d in a["context"]["relevant"])
    assert a["impact"]["affected_apis"][0]["path"] == "/payments"
    assert a["impact"]["database_schema"]["files"] and a["risk"]["factors"] and a["classification"]["domain_templates"]
    ids = {e["id"] for e in a["evidence"]}
    assert all(e in ids for h in a["hypotheses"] for e in h["evidence_ids"])
    p = c.post(f"/api/v1/engineering/tasks/{tid}/plans", headers=H); assert p.status_code == 201, p.text
    p = p.json(); pid = p["id"]
    assert p["status"] == "draft" and len(p["steps"]) >= 5
    assert c.post(f"/api/v1/engineering/tasks/{tid}/plans", headers=H).status_code == 409  # duplicate plan
    s1 = p["steps"][0]["id"]
    assert c.patch(f"/api/v1/engineering/plans/{pid}/steps/{s1}", json={"status": "in_progress"}, headers=H).status_code == 409  # unapproved
    assert c.get(f"/api/v1/engineering/plans/{pid}/executable", headers=H).status_code == 409
    assert c.post(f"/api/v1/engineering/plans/{pid}/approve", json={"note": "ok ok ok ok ok"}, headers={"X-Actor": "ai:planner"}).status_code == 403
    r = c.post(f"/api/v1/engineering/plans/{pid}/approve", json={"note": "Reviewed risk, acceptable for staging"}, headers={"X-Actor": "bob-lead"})
    assert r.status_code == 200 and r.json()["status"] == "approved", r.text
    s2 = p["steps"][1]["id"]
    assert c.patch(f"/api/v1/engineering/plans/{pid}/steps/{s2}", json={"status": "in_progress"}, headers=H).status_code == 409  # unmet dep
    for st in p["steps"]:
        for s in ("in_progress", "done"):
            r = c.patch(f"/api/v1/engineering/plans/{pid}/steps/{st['id']}", json={"status": s}, headers=H); assert r.status_code == 200, r.text
    f = c.get(f"/api/v1/engineering/plans/{pid}", headers=H).json()
    assert f["status"] == "completed" and {h["event"] for h in f["history"]} >= {"plan_created", "plan_approved", "step_updated"}
    assert c.get(f"/api/v1/engineering/tasks/{tid}", headers=H).json()["status"] == "done"
    assert c.get(f"/api/v1/engineering/plans/{pid}/executable", headers=H).status_code == 409  # completed plans are not executable

def test_reject_and_supersede():
    t = dict(TASK, title="Retry storm on gateway timeout")
    tid = c.post("/api/v1/engineering/tasks", json=t, headers=H).json()["id"]
    c.post(f"/api/v1/engineering/tasks/{tid}/analyze", headers=H)
    pid = c.post(f"/api/v1/engineering/tasks/{tid}/plans", headers=H).json()["id"]
    assert c.post(f"/api/v1/engineering/plans/{pid}/reject", json={}, headers=H).status_code == 422
    assert c.post(f"/api/v1/engineering/plans/{pid}/reject", json={"note": "Too broad"}, headers=H).json()["status"] == "rejected"
    p2 = c.post(f"/api/v1/engineering/tasks/{tid}/plans", headers=H).json(); assert p2["version"] == 2
    p3 = c.post(f"/api/v1/engineering/tasks/{tid}/plans", json={"supersede": True}, headers=H).json(); assert p3["version"] == 3
    assert c.get(f"/api/v1/engineering/plans/{p2['id']}", headers=H).json()["status"] == "superseded"
    assert c.patch(f"/api/v1/engineering/plans/{p3['id']}", json={"notes": "n"}, headers=H).status_code == 200

def test_repos_and_stale():
    assert [r["repo_id"] for r in c.get("/api/v1/engineering/repositories", headers=H).json()["items"]] == ["payments-api"]
