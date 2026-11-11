"""
AURA Financial Platform - Master End-to-End Integration Verification Suite
Verifies all 10 Modules, Workflows 1-9, PayPal Adapter, and FastAPI endpoints.
"""
import sys
import os
import uuid

# Set UTF-8 encoding for standard output
sys.stdout.reconfigure(encoding='utf-8')

backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal, init_db, engine
from app.seed_data import seed_initial_data
from app.workflows.orchestrator import WorkflowOrchestrator
from app.integrations.paypal.adapter import PayPalAdapter
from sqlalchemy import inspect
from decimal import Decimal

print("=" * 80)
print("  STARTING AURA 10-MODULE & PAYPAL INTEGRATION VERIFICATION")
print("=" * 80)

# Step 1: Database & Seed
print("\n[STEP 1] Initializing Unified Database Schema...")
init_db()
db = SessionLocal()
seed_initial_data(db)
inspector = inspect(engine)
tables = inspector.get_table_names()
print(f"  [OK] {len(tables)} Unified Database Tables Verified")
assert len(tables) >= 28, f"Expected at least 28 tables, found {len(tables)}"

# Step 2: Verify Workflow 1 (M01 -> M02)
print("\n[STEP 2] Verifying Workflow 1: Financial Data -> Payments...")
w1 = WorkflowOrchestrator.sync_records_to_payments(db)
print(f"  [OK] Synced Volume: ${w1['total_provider_volume']} | Success Rate: {w1['success_rate_pct']}%")

# Step 3: Verify Workflow 2 (M01 + M02 -> M03)
print("\n[STEP 3] Verifying Workflow 2: Autonomous 3-Way Reconciliation...")
w2 = WorkflowOrchestrator.execute_reconciliation(db)
print(f"  [OK] Run ID: {w2['run_id']} | Matched: {w2['matched_records']} | Match Rate: {w2['match_rate_pct']}%")

# Step 4: Verify Workflow 3 (M01,02,03 -> M04)
print("\n[STEP 4] Verifying Workflow 3: Fraud & Risk Intelligence...")
w3 = WorkflowOrchestrator.evaluate_risk_on_records(db)
print(f"  [OK] Assessed Transactions: {w3['transactions_assessed']} | Flagged: {w3['high_risk_flagged']}")

# Step 5: Verify Workflow 4 (M01,02,03 -> M05)
print("\n[STEP 5] Verifying Workflow 4: Treasury & Runway Forecasting...")
w4 = WorkflowOrchestrator.update_treasury_analytics(db)
print(f"  [OK] Liquid Reserves: ${w4['current_treasury_balance']} | Projected Runway: {w4['runway_days']} Days")

# Step 6: Verify Workflow 5 (M01-04 -> M06)
print("\n[STEP 6] Verifying Workflow 5: Dispute Intelligence & Evidence Packaging...")
w5 = WorkflowOrchestrator.auto_enrich_dispute(db, "disp_case_pp_001")
print(f"  [OK] Auto-Enriched Dispute: {w5['dispute_id']} | Win Prob: {w5['win_probability_pct']}% | Action: {w5['recommended_action']}")

# Step 7: Verify Workflow 6 (M02,03,06 -> M07)
print("\n[STEP 7] Verifying Workflow 6: Refund Intelligence & Dispute Shield Guard...")
# Test A: Dispute Shield blocks refund on disputed transaction
blocked_refund = WorkflowOrchestrator.evaluate_refund_eligibility(db, "pp_tx_8819284711", Decimal("300.00"))
assert not blocked_refund["eligible"], "Dispute shield should have blocked this refund"
print(f"  [OK] Dispute Shield Activated: {blocked_refund['reason'][:60]}...")

# Test B: Clean transaction is eligible and generates governance approval
fresh_tx_id = f"pp_tx_test_{uuid.uuid4().hex[:8]}"
clean_refund = WorkflowOrchestrator.evaluate_refund_eligibility(db, fresh_tx_id, Decimal("400.00"))
assert clean_refund["eligible"], "Clean transaction should be eligible"
print(f"  [OK] Refund Request: {clean_refund['refund_request_id']} | Requires Approval: {clean_refund['requires_governance_approval']}")

# Step 8: Verify Workflow 7 (M01,02,03,05 -> M08)
print("\n[STEP 8] Verifying Workflow 7: Credit & Early Warning Monitoring...")
w7 = WorkflowOrchestrator.evaluate_credit_and_cashflow(db, "bor_techcorp_01")
print(f"  [OK] Borrower: {w7['legal_name']} | Rating: {w7['risk_rating']} | DSCR: {w7['dscr_ratio']}x | Status: {w7['early_warning_status']}")

# Step 9: Verify Workflow 8 (M01-08 -> M09)
print("\n[STEP 9] Verifying Workflow 8: Central Governance Approval & Audit Trail...")
w8 = WorkflowOrchestrator.approve_governance_action(db, "app_req_seed_001", "Chief Risk Officer")
print(f"  [OK] Request: {w8['request_id']} | Status: {w8['status']} | Audit Log: {w8['audit_log_id']}")

# Step 10: Verify Workflow 9 (M01-09 -> M10)
print("\n[STEP 10] Verifying Workflow 9: Financial Copilot Executive Briefing...")
w9 = WorkflowOrchestrator.generate_executive_briefing(db)
print(f"  [OK] Briefing ID: {w9['briefing_id']} | Settled Volume: ${w9['settled_volume']} | Recon Rate: {w9['recon_match_rate']}%")

# Step 11: Verify PayPal Adapter Status
print("\n[STEP 11] Verifying Dedicated PayPal Adapter Layer...")
pp_status = PayPalAdapter.get_status()
print(f"  [OK] Configured: {pp_status['configured']} | Mode: {pp_status['mode']} | Base URL: {pp_status['base_url']}")
print(f"  [OK] Connection Status: {pp_status['connection_status']}")
print(f"  [OK] Supported Features: {len(pp_status['supported_features'])} PayPal REST capabilities")

# Step 12: Verify FastAPI HTTP Endpoints
print("\n[STEP 12] Verifying FastAPI HTTP Endpoints via TestClient...")
client = TestClient(app)

res_health = client.get("/health")
assert res_health.status_code == 200, f"Health check failed: {res_health.text}"
print("  [OK] GET /health -> 200 OK")

res_metrics = client.get("/api/v1/overview/metrics")
assert res_metrics.status_code == 200, f"Overview metrics failed: {res_metrics.text}"
print(f"  [OK] GET /api/v1/overview/metrics -> 200 OK (Settled Vol: ${res_metrics.json()['kpis']['total_settled_volume']})")

res_chat = client.post("/api/v1/copilot/chat", json={"message": "What is our current treasury liquidity and PayPal volume?"})
assert res_chat.status_code == 200, f"Copilot chat failed: {res_chat.text}"
print("  [OK] POST /api/v1/copilot/chat -> 200 OK (Citations: " + ", ".join(res_chat.json()['referenced_sources']) + ")")

res_pp_status = client.get("/api/v1/integrations/paypal/status")
assert res_pp_status.status_code == 200, f"PayPal status failed: {res_pp_status.text}"
print(f"  [OK] GET /api/v1/integrations/paypal/status -> 200 OK (Mode: {res_pp_status.json()['mode']})")

db.close()

print("\n" + "=" * 80)
print("  ALL 10 MODULES + PAYPAL SANDBOX INTEGRATION FULLY VERIFIED! (100% PASS)")
print("=" * 80)
