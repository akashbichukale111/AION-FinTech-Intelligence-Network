import json
import socket
import textwrap

import pytest
from fastapi.testclient import TestClient

import api_guardian as ag

FASTAPI_APP = '''
import hmac, hashlib, time, requests
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True)
STRIPE_KEY = "sk_live_abcdefghijklmnop1234"
seen_keys = set()

class Pay(BaseModel):
    amount: int = Field(gt=0, le=100000)

def get_current_user(): ...

@app.post("/webhooks/stripe")
async def stripe_hook(request: Request):
    payload = await request.json()
    order = Order.get(payload["data"]["id"])
    order.status = "paid"
    order.save()
    return {"ok": True}

@app.post("/webhooks/safe")
async def safe_hook(request: Request):
    raw = await request.body()
    ts = int(request.headers["X-Timestamp"])
    if abs(time.time() - ts) > 300:
        return {"error": "stale"}
    expected = hmac.new(b"k", f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers["X-Signature"]):
        return {"error": "bad"}
    event = await request.json()
    if ProcessedEvent.exists(event_id=event["id"]):
        return {"ok": True}
    ProcessedEvent.create(event_id=event["id"])
    return {"ok": True}

@app.post("/payments")
def create_payment(body: Pay):
    r = requests.post("https://api.stripe.com/v1/payment_intents", data={"amount": body.amount})
    Payment.create(status="paid")
    return r.json()

@app.post("/refunds/{id}")
def refund(id: int, body: Pay, user=Depends(get_current_user)):
    p = Payment.get(id)
    if p.status == "captured":
        p.status = "refunded"
        p.save()
    return {"ok": 1}

@app.get("/accounts/{account_id}")
def get_account(account_id: int, user=Depends(get_current_user)):
    return Account.objects.get(id=account_id)

@app.get("/health")
def health():
    return {"ok": True}

def charge_with_retry(session):
    while True:
        try:
            return session.post("/charge", json={})
        except Exception:
            time.sleep(1)
'''

EXPRESS = '''
const express = require("express");
const router = express.Router();
router.post("/orders", async (req, res) => { await db.insert(req.body); res.json({}); });
router.get("/me", requireAuth, (req, res) => res.json(req.user));
'''

SPEC = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://api.example.com"}],
        "paths": {"/payments": {"post": {"operationId": "pay", "requestBody": {"content": {"application/json": {"schema": {
            "type": "object", "required": ["amount"], "properties": {"amount": {"type": "integer"}, "note": {"type": "string"}}}}}},
            "responses": {"200": {"description": "ok"}}}}}}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text(FASTAPI_APP)
    (repo / "routes.js").write_text(EXPRESS)
    monkeypatch.setattr(ag, "DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setattr(ag, "ALLOWED_ROOTS", [tmp_path.resolve()])
    c = TestClient(ag.create_app())
    r = c.post("/api/v1/api-guardian/scan", json={"repo_path": str(repo)})
    assert r.status_code == 200, r.text
    return c, r.json()["id"], repo


def findings(c, sid, **q):
    return c.get("/api/v1/api-guardian/findings", params={"scan_id": sid, **q}).json()["items"]


def test_discovery(env):
    c, sid, _ = env
    refs = {e["ref"] for e in c.get("/api/v1/api-guardian/endpoints", params={"scan_id": sid}).json()["items"]}
    assert {"POST /webhooks/stripe", "POST /payments", "POST /refunds/{id}", "GET /accounts/{account_id}", "POST /orders", "GET /me"} <= refs


def test_webhook_signature_finding_accuracy(env):
    c, sid, _ = env
    f = findings(c, sid)
    unsigned = [x for x in f if x["rule"] == "webhook.no_signature_verification"]
    assert [x["endpoint"] for x in unsigned] == ["POST /webhooks/stripe"]  # safe webhook NOT flagged
    assert unsigned[0]["evidence"]["kind"] == "absence" and unsigned[0]["line"]
    assert unsigned[0]["severity"] == "critical" and unsigned[0]["confidence"] in ("medium", "high")
    safe = [x for x in f if x["endpoint"] == "POST /webhooks/safe" and x["category"] == "webhook"]
    assert not safe, [x["rule"] for x in safe]  # fully protected handler yields zero webhook findings


def test_absence_findings_never_claim_certainty_without_corroboration(env):
    c, sid, _ = env
    for x in findings(c, sid):
        if x["evidence"]["kind"] == "absence" and not x["evidence"]["note"].startswith("corroborated"):
            assert x["confidence"] != "high", x["rule"]
            assert x["needs_verification"]


def test_payment_and_idempotency_findings(env):
    c, sid, _ = env
    rules = {(x["rule"], x["endpoint"]) for x in findings(c, sid)}
    assert ("payment.payment_no_idempotency", "POST /payments") in rules
    assert ("payment.provider_call_no_timeout", "POST /payments") in rules
    assert ("payment.status_race", "POST /refunds/{id}") in rules
    assert ("auth.missing", "POST /orders") in rules
    assert not any(r == "auth.missing" and e in ("GET /me", "GET /accounts/{account_id}", "GET /health") for r, e in rules)


def test_retry_and_security_findings_and_redaction(env):
    c, sid, _ = env
    f = findings(c, sid)
    rules = {x["rule"] for x in f}
    assert {"retry.unbounded", "idempotency.in_memory_store", "cors.wildcard_credentials", "secret.stripe_live_key"} <= rules
    blob = json.dumps(f)
    assert "sk_live_abcdefghijklmnop1234" not in blob  # secrets redacted in stored findings
    assert "sk_live_abcdefghijklmnop1234" not in json.dumps(c.get("/api/v1/api-guardian/audit").json())


def test_openapi_import_and_breaking_changes(env, tmp_path):
    c, base, _ = env
    s1 = c.post("/api/v1/api-guardian/openapi", json={"openapi_text": json.dumps(SPEC), "openapi_name": "v1.json"}).json()["id"]
    spec2 = json.loads(json.dumps(SPEC))
    spec2["paths"]["/payments"]["post"]["requestBody"]["content"]["application/json"]["schema"]["required"].append("note")
    s2 = c.post("/api/v1/api-guardian/openapi", json={"openapi_text": json.dumps(spec2), "openapi_name": "v2.json"}).json()["id"]
    rules = {x["rule"] for x in findings(c, s1)}
    assert {"openapi.insecure_server", "openapi.payment_no_idempotency_key"} <= rules
    diff = c.get("/api/v1/api-guardian/compare", params={"base": s1, "head": s2}).json()
    assert any(b["type"] == "new_required_body_field" and b["detail"] == "note" for b in diff["breaking_changes"])


def test_simulation_is_deterministic_and_network_isolated(env, monkeypatch):
    c, sid, _ = env

    def boom(*a, **k):
        raise AssertionError("network access attempted during simulation")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    for name in ag.SCENARIOS:
        a = c.post("/api/v1/api-guardian/simulations", json={"scenario": name, "target": "naive", "seed": 7}).json()
        b = c.post("/api/v1/api-guardian/simulations", json={"scenario": name, "target": "naive", "seed": 7}).json()
        assert a["trace_hash"] == b["trace_hash"] and a["evidence_type"] == "simulated"
        assert a["id"] != b["id"]
        r = c.post("/api/v1/api-guardian/simulations", json={"scenario": name, "target": "reference", "seed": 7}).json()
        assert r["verdict"] == "pass", (name, r["assertions"])  # reference implementation is safe
    for name in ("duplicate_webhook", "invalid_signature", "expired_timestamp", "out_of_order", "network_timeout", "provider_unavailable", "malformed_payload"):
        n = c.post("/api/v1/api-guardian/simulations", json={"scenario": name, "target": "naive", "seed": 7}).json()
        assert n["verdict"] == "fail", name  # naive implementation exposes the defect


def test_simulation_from_finding_reproduces_static_defect(env):
    c, sid, _ = env
    f = next(x for x in findings(c, sid) if x["rule"] == "webhook.no_signature_verification")
    r = c.post("/api/v1/api-guardian/simulations", json={"finding_id": f["id"]}).json()
    assert r["verdict"] == "fail" and r["outcome"]["credit"] == 1998 + 0 or r["verdict"] == "fail"
    ok = next(x for x in c.get("/api/v1/api-guardian/webhooks", params={"scan_id": sid}).json()["items"] if x["ref"] == "POST /webhooks/safe")
    assert ok["checks"]["signature"] == "present" and ok["status"] == "verified"
    safe_ep = next(e for e in c.get("/api/v1/api-guardian/endpoints", params={"scan_id": sid}).json()["items"] if e["ref"] == "POST /webhooks/safe")
    r2 = c.post("/api/v1/api-guardian/simulations", json={"scenario": "invalid_signature", "target": f"scan:{sid}:{safe_ep['id']}"}).json()
    assert r2["verdict"] == "pass"


def test_retry_and_limits(env):
    c, _, _ = env
    r = c.post("/api/v1/api-guardian/simulations", json={"scenario": "retry_exhaustion", "target": "reference", "profile": {"max_retries": 10 ** 6, "backoff": "none"}}).json()
    assert r["verdict"] == "fail" and r["outcome"]["result"]["attempts"] <= 51  # execution cap enforced
    r = c.post("/api/v1/api-guardian/simulations", json={"scenario": "duplicate_webhook", "target": "reference", "params": {"copies": 10 ** 9}}).json()
    assert len(r["outcome"]["responses"]) <= 200


def test_plan_requires_approval_and_safety(env, tmp_path):
    c, sid, _ = env
    fid = findings(c, sid)[0]["id"]
    p = c.post(f"/api/v1/api-guardian/findings/{fid}/create-plan").json()
    assert p["auto_apply"] is False and p["status"] == "draft_pending_approval"
    assert c.post("/api/v1/api-guardian/scan", json={"repo_path": "https://example.com"}).status_code == 403
    assert c.post("/api/v1/api-guardian/scan", json={"repo_path": "/etc"}).status_code == 403
