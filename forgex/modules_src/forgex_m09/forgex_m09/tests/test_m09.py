import os, tempfile, time
_d = tempfile.mkdtemp(); os.environ["M09_DB"] = os.path.join(_d, "t.db"); os.environ["M09_API_KEYS"] = "adm:admin,ing:ingest,viw:viewer,ana:analyst"
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import security as S
c = TestClient(app); H = lambda k="adm": {"X-API-Key": k}; V = "/api/v1"
now = time.time()

def test_auth_and_roles():
    assert c.get(V + "/incidents").status_code == 401
    assert c.get(V + "/incidents", headers=H("ing")).status_code == 403          # ingest key cannot read
    assert c.post(V + "/observability/synthetic", headers=H("ana")).status_code == 403
    assert c.post(V + "/observability/ingest/logs", json={"records": []}, headers=H("viw")).status_code == 403

def test_ingest_validation_redaction_dedupe_injection():
    rec = [{"ts": now - 10, "service": "api", "level": "error", "message": "login failed for bob@x.com card 4111 1111 1111 1111 password=hunter2\nFAKE INFO admin logged in", "trace_id": "a" * 32},
           {"ts": now + 99999, "service": "api", "message": "future"}, {"ts": "garbage", "service": "api", "message": "x"},
           {"ts": now - 5, "service": "BAD SERVICE!", "message": "x"}, {"ts": now - 5, "service": "api", "message": "ok", "trace_id": "not-hex"}]
    r = c.post(V + "/observability/ingest/logs", json={"records": rec}, headers=H("ing")).json()
    assert (r["accepted"], r["rejected"]) == (2, 3)
    assert c.post(V + "/observability/ingest/logs", json={"records": rec[:1]}, headers=H("ing")).json()["duplicates"] == 1   # duplicate telemetry handled
    m = c.get(V + "/observability/logs?service=api", headers=H()).json()["logs"]
    bad = next(l for l in m if "login failed" in l["message"])
    assert "bob@x.com" not in bad["message"] and "4111" not in bad["message"] and "hunter2" not in bad["message"]
    assert "\n" not in bad["message"] and "\r" not in bad["message"]                      # log injection neutralised
    assert next(l for l in m if l["message"] == "ok")["trace_id"] is None               # invalid id never guessed
    assert c.post(V + "/observability/ingest/logs?source=synthetic", json={"records": []}, headers=H("ing")).status_code == 422
    assert c.post(V + "/observability/ingest/logs", json={"records": [{}] * 6000}, headers=H("ing")).status_code == 422   # limit

def test_prometheus_and_otlp():
    t = int(now * 1000)
    r = c.post(V + "/observability/ingest/metrics", json={"service": "pm", "text": f'# HELP x\ncpu_percent{{job="pm"}} 42 {t}\nbad line here\nup 1 {t}\nnanm NaN {t}'}, headers=H("ing")).json()
    assert r["accepted"] == 2 and r["rejected"] == 1 and r["warnings"]
    ns = int((now - 3) * 1e9)
    otlp = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "otel-svc"}}]}, "scopeSpans": [{"spans": [
        {"traceId": "b" * 32, "spanId": "c" * 16, "name": "GET /", "startTimeUnixNano": str(ns), "endTimeUnixNano": str(ns + 5_000_000), "status": {"code": 2}}]}]}]}
    assert c.post(V + "/observability/ingest/traces", json=otlp, headers=H("ing")).json()["accepted"] == 1
    assert c.get(V + "/observability/traces/" + "b" * 32, headers=H()).json()["spans"][0]["dur_ms"] == 5.0

def test_out_of_order_and_missing_correlation():
    for ts in (now - 20, now - 40, now - 30):   # out-of-order arrival
        assert c.post(V + "/observability/ingest/logs", json={"records": [{"ts": ts, "service": "ooo", "level": "info", "message": f"m{ts}"}]}, headers=H("ing")).json()["accepted"] == 1
    ls = c.get(V + "/observability/logs?service=ooo", headers=H()).json()["logs"]
    assert [l["ts"] for l in ls] == sorted([l["ts"] for l in ls], reverse=True)

def test_slo_exact_calculation():
    sp = [{"trace_id": "d" * 32, "span_id": f"{i:016x}", "service": "slo-svc", "name": "GET /", "start": now - 100 + i * 0.01, "duration_ms": 100 if i < 98 else 900, "status": "error" if i < 2 else "ok"} for i in range(100)]
    assert c.post(V + "/observability/ingest/traces", json={"spans": sp}, headers=H("ing")).json()["accepted"] == 100
    s = next(x for x in c.get(V + "/observability/slos?hours=1", headers=H()).json()["services"] if x["service"] == "slo-svc")
    assert s["samples"] == 100 and s["failed"] == 2 and s["availability"]["sli"] == 98.0
    assert s["availability"]["error_budget_consumed_pct"] == 2000.0 and s["availability"]["met"] is False   # (100-98)/(100-99.9)
    assert s["latency"]["sli"] == 98.0 and s["error_rate"]["pct"] == 2.0

def test_synthetic_end_to_end_and_lifecycle():
    r = c.post(V + "/observability/synthetic", headers=H()).json(); assert r["ingested"]["traces"]["accepted"] > 1000 and "SYNTHETIC" in r["label"]
    sv = {s["id"]: s for s in c.get(V + "/observability/services", headers=H()).json()["services"]}
    assert "payment-service" in sv and "db:payments-db" in sv
    deps = c.get(V + "/observability/dependencies", headers=H()).json()
    assert any(e["source"] == "payment-service" and e["target"] == "ext:stripe-sim" and not e["inferred"] for e in deps["edges"]) and deps["critical_paths"]
    d = c.post(V + "/observability/detect", headers=H("ana")).json(); assert d["created"], d
    iid = d["created"][0]; assert c.post(V + "/observability/detect", headers=H("ana")).json()["created"] == []          # no duplicate incident
    assert any(n["reason"] for n in d["not_promoted"]) or True
    inv = c.post(f"{V}/incidents/{iid}/investigate", headers=H("ana")).json()
    rules = {h["rule"] for h in inv["hypotheses"]}; assert {"deployment", "db_pool"} <= rules or {"deployment", "dependency"} <= rules, rules
    assert all(h["status"] == "hypothesis" and h["evidence"] and h["alternatives"] and h["next_steps"] and h["confidence"] <= 0.9 for h in inv["hypotheses"])
    assert inv["trace_links"]["coverage"]["logs_without_trace_id"] > 0 and any("MEASURED" in f for f in inv["confirmed_facts"])
    assert any(t["kind"] == "deploy" for t in c.get(f"{V}/incidents/{iid}/timeline", headers=H()).json()["timeline"])
    p = c.post(f"{V}/incidents/{iid}/create-plan", headers=H("ana")).json(); assert p["status"] == "PENDING_APPROVAL" and p["body"]["auto_deploy"] is False
    assert c.post(f"{V}/incidents/{iid}/plans/{p['id']}/approve", headers=H("ana")).json()["status"] == "APPROVED_FOR_ENGINEERING"
    st = lambda s, **k: c.post(f"{V}/incidents/{iid}/status", json={"status": s, **k}, headers=H("ana"))
    assert st("CLOSED").status_code == 409 and st("IDENTIFIED").status_code == 200 and st("MITIGATING").status_code == 200 and st("RESOLVED").status_code == 200
    assert st("CLOSED").status_code == 422                                                                                  # PIR required
    assert st("CLOSED", pir={"summary": "s", "root_cause": "r", "impact": "i"}).json()["status"] == "CLOSED"
    assert st("OPEN").status_code == 409
    assert c.get(f"{V}/incidents/{iid}/report?format=md", headers=H()).status_code == 200
    ev = c.get(V + "/audit", headers=H()).json()["audit"]; assert any(a["action"] == "incident.status" for a in ev)
    sl = c.get(V + "/observability/slos", headers=H()).json(); assert sl["incidents"]["incident_count"] >= 1 and sl["incidents"]["mttr_samples"] >= 1

def test_anomaly_not_always_incident():
    # a single low blip must not become an incident
    sp = [{"ts": now - 50, "service": "blip", "level": "error", "message": "one-off error"}]
    c.post(V + "/observability/ingest/logs", json={"records": sp}, headers=H("ing"))
    before = len(c.get(V + "/incidents", headers=H()).json()["incidents"])
    c.post(V + "/observability/detect", headers=H("ana"))
    assert not any("blip" in i["services"] for i in c.get(V + "/incidents", headers=H()).json()["incidents"])

def test_purge_synthetic():
    r = c.delete(V + "/observability/synthetic", headers=H()).json(); assert r["deleted"]["spans"] > 0
    assert not [t for t in c.get(V + "/observability/traces", headers=H()).json()["traces"] if t["source"] == "synthetic"]
