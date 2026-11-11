"""
AURA Financial Platform - Realistic High-Fidelity Seed Data
Populates all 10 modules with rich financial data so the platform is immediately demo-ready.
"""
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.models.m01_financial_data import DataSource, ImportBatch, FinancialRecord, DataQualityReport
from app.models.m02_payment_ops import PaymentAttempt, ProviderPerformanceMetric, RoutingRecommendation
from app.models.m03_reconciliation import ReconRun, ReconMatch, ReconException
from app.models.m04_risk_intelligence import RiskAssessment, RiskAlert
from app.models.m05_treasury import AccountBalance, CashFlowSnapshot, TreasuryForecast
from app.models.m06_disputes import DisputeCase, DisputeEvidenceItem
from app.models.m07_refunds import RefundRequest, RefundRecoveryCase
from app.models.m08_credit import BorrowerProfile, CreditFacility, RepaymentSchedule
from app.models.m09_governance import GovernanceAgent, GovernancePolicy, ApprovalRequest, GovernanceAuditLog
from app.models.m10_copilot import CopilotConversation, CopilotMessage, ExecutiveBriefing
from app.core.types import RiskLevel, ReconStatus, RefundStatus, DisputeStatus, GovernanceActionType, ProviderName, FinancialRecordType, RecordStatus


def seed_initial_data(db: Session):
    # Check if already seeded
    if db.query(FinancialRecord).first():
        return

    print("Seeding AURA Financial Platform with realistic multi-module financial records...")

    now = datetime.now(timezone.utc)

    # -------------------------------------------------------------------------
    # MODULE 01: Financial Data & Batches
    # -------------------------------------------------------------------------
    source = DataSource(
        source_name="PAYPAL_SANDBOX_STREAM",
        provider=ProviderName.PAYPAL.value,
        source_type="REST_API",
        status="ACTIVE",
        sync_frequency="REALTIME",
        last_sync_at=now
    )
    db.add(source)

    batch = ImportBatch(
        batch_id="batch_paypal_initial_01",
        source_name="PAYPAL_SANDBOX_STREAM",
        provider_name=ProviderName.PAYPAL.value,
        total_records=8,
        valid_records=8,
        failed_records=0,
        quality_score=Decimal("99.8"),
        status="COMPLETED"
    )
    db.add(batch)
    db.flush()

    tx_samples = [
        ("pp_tx_9018274619", Decimal("1450.00"), "USD", FinancialRecordType.PAYMENT.value, "alex.morgan@techcorp.io", "Alex Morgan", "Enterprise Cloud License - Annual"),
        ("pp_tx_8819284711", Decimal("320.50"), "USD", FinancialRecordType.PAYMENT.value, "sarah.connor@cyberdyne.com", "Sarah Connor", "Developer Pro Subscription Q3"),
        ("pp_tx_7719204822", Decimal("89.99"), "USD", FinancialRecordType.PAYMENT.value, "david.beck@gmail.com", "David Beck", "SaaS Starter Seat Tier"),
        ("pp_tx_6619284733", Decimal("3400.00"), "USD", FinancialRecordType.PAYMENT.value, "finance@acme-holdings.com", "Acme Finance Team", "Custom AI Engine Infrastructure Fee"),
        ("pp_tx_5519284744", Decimal("125.00"), "USD", FinancialRecordType.REFUND.value, "elena.rostova@temp-mail.org", "Elena Rostova", "Refund for duplicated seat request"),
        ("pp_tx_4419284755", Decimal("650.00"), "USD", FinancialRecordType.PAYMENT.value, "marcus.vance@vance-legal.com", "Marcus Vance", "Legal Copilot Premium License"),
        ("pp_tx_3319284766", Decimal("18.50"), "USD", FinancialRecordType.FEE.value, "system@paypal.com", "PayPal Merchant Services", "Standard Processing Fee 2.9% + $0.30"),
        ("pp_tx_2219284777", Decimal("2200.00"), "USD", FinancialRecordType.PAYMENT.value, "billing@nordic-ventures.no", "Nordic Ventures AS", "Treasury & Reconciliation Node Cluster")
    ]

    records = []
    for idx, (tx_id, amt, curr, rtype, email, name, desc) in enumerate(tx_samples):
        rec = FinancialRecord(
            record_id=f"rec_seed_{idx+1:03d}",
            batch_id=batch.batch_id,
            provider_name=ProviderName.PAYPAL.value,
            provider_tx_id=tx_id,
            amount=amt,
            currency=curr,
            record_type=rtype,
            status=RecordStatus.SETTLED.value,
            payer_id=f"payer_pp_{idx+100}",
            payer_email=email,
            payer_name=name,
            description=desc,
            transaction_time=now - timedelta(hours=idx*4 + 2)
        )
        db.add(rec)
        records.append(rec)
    db.flush()

    q_report = DataQualityReport(
        report_id="dqr_initial_batch_01",
        batch_id=batch.batch_id,
        completeness_score=Decimal("100.0"),
        validity_score=Decimal("99.8"),
        uniqueness_score=Decimal("100.0"),
        overall_quality_score=Decimal("99.9"),
        anomaly_count=0
    )
    db.add(q_report)

    # -------------------------------------------------------------------------
    # MODULE 02: Payment Operations & Provider Performance
    # -------------------------------------------------------------------------
    perf = ProviderPerformanceMetric(
        provider_name=ProviderName.PAYPAL.value,
        window_period="LAST_30_DAYS",
        total_volume=Decimal("8231.99"),
        total_count=len(tx_samples),
        success_count=len(tx_samples) - 1,
        failure_count=1,
        success_rate=Decimal("99.4"),
        avg_latency_ms=172,
        health_status="OPTIMAL"
    )
    db.add(perf)

    for rec in records:
        attempt = PaymentAttempt(
            attempt_id=f"att_{rec.record_id}",
            order_id=rec.provider_tx_id,
            capture_id=f"cap_{rec.provider_tx_id[6:]}",
            amount=rec.amount,
            currency=rec.currency,
            provider=ProviderName.PAYPAL.value,
            status="CAPTURED" if rec.record_type != "REFUND" else "REFUNDED",
            payer_email=rec.payer_email,
            latency_ms=168,
            created_at=rec.transaction_time
        )
        db.add(attempt)

    routing_rec = RoutingRecommendation(
        recommendation_id="route_rec_paypal_usd",
        currency="USD",
        recommended_provider=ProviderName.PAYPAL.value,
        expected_success_rate=Decimal("99.4"),
        estimated_cost_basis=Decimal("0.024"),
        rationale="PayPal Sandbox maintains lowest authorization latency (172ms) and highest settlement reliability."
    )
    db.add(routing_rec)
    db.flush()

    # -------------------------------------------------------------------------
    # MODULE 03: Autonomous Reconciliation
    # -------------------------------------------------------------------------
    recon_run = ReconRun(
        run_id="run_seeded_golden_01",
        scope="PAYPAL_SETTLEMENT_VS_LEDGER",
        status="COMPLETED",
        total_records_analyzed=len(records),
        matched_count=len(records) - 1,
        exception_count=1,
        matched_amount=Decimal("8106.99"),
        unmatched_amount=Decimal("125.00"),
        match_rate=Decimal("99.1")
    )
    db.add(recon_run)
    db.flush()

    for rec in records[:-1]:
        match = ReconMatch(
            match_id=f"match_{rec.record_id}",
            run_id=recon_run.run_id,
            internal_record_id=rec.record_id,
            provider_tx_id=rec.provider_tx_id,
            matched_amount=rec.amount,
            currency=rec.currency,
            confidence_score=Decimal("100.0"),
            match_type=ReconStatus.EXACT_MATCH.value
        )
        db.add(match)

    recon_exc = ReconException(
        exception_id="exc_seeded_001",
        run_id=recon_run.run_id,
        record_id=records[-1].record_id,
        provider_tx_id=records[-1].provider_tx_id,
        exception_type=ReconStatus.FEE_DISCREPANCY.value,
        discrepancy_amount=Decimal("2.40"),
        currency="USD",
        status="OPEN",
        resolution_notes="Partner merchant fee adjustment difference under automatic investigation."
    )
    db.add(recon_exc)
    db.flush()

    # -------------------------------------------------------------------------
    # MODULE 04: Fraud & Risk Intelligence
    # -------------------------------------------------------------------------
    risk1 = RiskAssessment(
        assessment_id="risk_assess_clean_01",
        transaction_id=records[0].record_id,
        amount=records[0].amount,
        currency="USD",
        risk_score=8,
        risk_level=RiskLevel.LOW.value,
        fraud_probability=Decimal("0.01"),
        explanation="Standard verified corporate buyer with clean velocity and established domain reputation."
    )
    db.add(risk1)

    risk2 = RiskAssessment(
        assessment_id="risk_assess_suspicious_02",
        transaction_id=records[4].record_id,
        amount=records[4].amount,
        currency="USD",
        risk_score=72,
        risk_level=RiskLevel.HIGH.value,
        fraud_probability=Decimal("0.72"),
        velocity_anomaly=True,
        triggered_rules=["DISPOSABLE_EMAIL_DOMAIN", "HIGH_REFUND_REQUEST_VELOCITY"],
        explanation="Disposable email provider detected combined with rapid refund request within 2 hours of checkout."
    )
    db.add(risk2)
    db.flush()

    risk_alert = RiskAlert(
        alert_id="alt_suspicious_refund_01",
        assessment_id=risk2.assessment_id,
        severity=RiskLevel.HIGH.value,
        alert_title="Rapid Refund Velocity on Disposable Domain",
        entity_ref="elena.rostova@temp-mail.org",
        status="UNRESOLVED"
    )
    db.add(risk_alert)

    # -------------------------------------------------------------------------
    # MODULE 05: Treasury & Cash Flow
    # -------------------------------------------------------------------------
    treasury_acc = AccountBalance(
        balance_id="bal_paypal_corp_01",
        account_name="PayPal Corporate Sandbox Settlement Wallet",
        provider="PAYPAL",
        currency="USD",
        current_balance=Decimal("128450.00"),
        available_balance=Decimal("123950.00"),
        pending_settlement=Decimal("4500.00"),
        reserved_amount=Decimal("0.00")
    )
    db.add(treasury_acc)

    cf_snap = CashFlowSnapshot(
        snapshot_id="cf_snap_daily_01",
        total_inflow=Decimal("8231.99"),
        total_outflow=Decimal("143.50"),
        net_cash_flow=Decimal("8088.49"),
        inflow_by_provider={"PAYPAL": "8231.99"},
        outflow_by_category={"REFUNDS": "125.00", "FEES": "18.50"}
    )
    db.add(cf_snap)

    forecast = TreasuryForecast(
        forecast_id="fc_runway_30d_01",
        forecast_horizon_days=30,
        predicted_runway_days=210,
        predicted_closing_balance=Decimal("152000.00"),
        confidence_interval_low=Decimal("141000.00"),
        confidence_interval_high=Decimal("165000.00"),
        liquidity_health_score=96,
        recommendations=[
            "PayPal cash inflow velocity currently outpaces burn by 4.2x.",
            "Surplus operating liquidity available for short-term Treasury yield allocation."
        ]
    )
    db.add(forecast)

    # -------------------------------------------------------------------------
    # MODULE 06: Disputes & Chargebacks
    # -------------------------------------------------------------------------
    dispute = DisputeCase(
        dispute_id="disp_case_pp_001",
        provider_dispute_id="PP-D-9928172",
        provider=ProviderName.PAYPAL.value,
        transaction_id=records[1].provider_tx_id,
        payer_email="sarah.connor@cyberdyne.com",
        disputed_amount=Decimal("320.50"),
        currency="USD",
        reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        lifecycle_stage="INQUIRY",
        status=DisputeStatus.EVIDENCE_REQUIRED.value,
        response_deadline=now + timedelta(days=7),
        win_probability=Decimal("84.5"),
        recommended_action="CONTEST_WITH_EVIDENCE"
    )
    db.add(dispute)
    db.flush()

    evidence = DisputeEvidenceItem(
        evidence_id="evi_signed_slip_001",
        dispute_id=dispute.dispute_id,
        evidence_type="PROOF_OF_FULFILLMENT",
        document_name="license_activation_audit_log_signed.pdf",
        tracking_number="ACT_KEY_9921_CYBER",
        carrier_name="Digital Provisioning Gateway",
        notes="SaaS activation token was acknowledged by user's verified enterprise email with 14 successful logins.",
        status="VALIDATED"
    )
    db.add(evidence)

    # -------------------------------------------------------------------------
    # MODULE 07: Intelligent Refunds & Recovery
    # -------------------------------------------------------------------------
    refund = RefundRequest(
        request_id="ref_req_001",
        transaction_id=records[0].provider_tx_id,
        amount=Decimal("450.00"),
        currency="USD",
        reason="Partial seat downgrade requested by account administrator",
        status=RefundStatus.APPROVAL_PENDING.value,
        is_eligible=True,
        duplicate_guard_passed=True,
        dispute_shield_passed=True,
        requires_approval=True
    )
    db.add(refund)

    # -------------------------------------------------------------------------
    # MODULE 08: Credit, Repayment & Early Warning
    # -------------------------------------------------------------------------
    borrower = BorrowerProfile(
        borrower_id="bor_techcorp_01",
        borrower_ref="REF_TECHCORP_US",
        legal_name="TechCorp Global Software Inc.",
        risk_rating="AA",
        total_exposure=Decimal("150000.00"),
        available_credit=Decimal("50000.00"),
        default_probability=Decimal("0.008"),
        early_warning_status="NORMAL"
    )
    db.add(borrower)
    db.flush()

    facility = CreditFacility(
        facility_id="fac_techcorp_rev_01",
        borrower_id=borrower.borrower_id,
        facility_type="REVOLVING_LINE",
        credit_limit=Decimal("200000.00"),
        utilized_amount=Decimal("150000.00"),
        currency="USD",
        interest_rate_pct=Decimal("7.25"),
        status="ACTIVE"
    )
    db.add(facility)
    db.flush()

    schedule = RepaymentSchedule(
        schedule_id="sch_inst_01",
        facility_id=facility.facility_id,
        installment_number=1,
        due_date=now + timedelta(days=14),
        amount_due=Decimal("12500.00"),
        principal_due=Decimal("10000.00"),
        interest_due=Decimal("2500.00"),
        status="SCHEDULED"
    )
    db.add(schedule)

    # -------------------------------------------------------------------------
    # MODULE 09: Governance Engine
    # -------------------------------------------------------------------------
    agents = [
        ("agent_recon_sentinel", "Autonomous Reconciliation Sentinel", "RECON", "ACTIVE", "FULL", 142, 142),
        ("agent_fraud_quarantine", "Fraud Velocity Quarantine Agent", "RISK", "ACTIVE", "SEMI_AUTONOMOUS", 48, 42),
        ("agent_refund_guard", "Refund Integrity & Duplicate Shield", "REFUNDS", "ACTIVE", "SEMI_AUTONOMOUS", 64, 59),
        ("agent_paypal_settlement", "PayPal Settlement & Webhook Worker", "PAYPAL", "ACTIVE", "FULL", 312, 312)
    ]
    for aid, aname, adom, astatus, aauto, aprop, aappr in agents:
        agent = GovernanceAgent(
            agent_id=aid,
            agent_name=aname,
            domain=adom,
            status=astatus,
            autonomy_level=aauto,
            total_actions_proposed=aprop,
            total_actions_approved=aappr
        )
        db.add(agent)

    gov_policy = GovernancePolicy(
        policy_id="pol_refund_threshold_01",
        policy_name="High-Value PayPal Refund Authorization Policy",
        action_type=GovernanceActionType.EXECUTE_PAYPAL_REFUND.value,
        requires_approval_above_amount=Decimal("250.00"),
        required_roles=["operator", "admin"],
        is_active=True,
        description="Mandatory secondary human sign-off on PayPal refunds exceeding $250.00 USD."
    )
    db.add(gov_policy)

    approval_req = ApprovalRequest(
        request_id="app_req_seed_001",
        agent_id="agent_refund_guard",
        action_type=GovernanceActionType.EXECUTE_PAYPAL_REFUND.value,
        target_entity_type="REFUND_REQUEST",
        target_entity_id=refund.request_id,
        amount=refund.amount,
        currency="USD",
        status="PENDING",
        urgency="MEDIUM",
        requested_by="Refund Integrity Sentinel Agent",
        payload={"capture_id": records[0].provider_tx_id, "amount": str(refund.amount)}
    )
    db.add(approval_req)

    audit = GovernanceAuditLog(
        log_id="aud_init_system_01",
        event_type="PLATFORM_INITIALIZATION_AND_POLICY_SYNC",
        actor_id="AURA_SYSTEM_ORCHESTRATOR",
        actor_type="SYSTEM",
        module_source="M09_GOVERNANCE",
        details={"version": "2.0.0", "modules_active": 10, "paypal_adapter": "ONLINE"}
    )
    db.add(audit)

    # -------------------------------------------------------------------------
    # MODULE 10: Financial Copilot & Executive Intelligence
    # -------------------------------------------------------------------------
    briefing = ExecutiveBriefing(
        briefing_id="brf_morning_executive_pulse",
        title="AURA Executive Financial Intelligence Briefing",
        net_inflow_today=Decimal("8088.49"),
        paypal_settlement_volume=Decimal("8231.99"),
        disputes_requiring_action=1,
        unresolved_risk_alerts=1,
        reconciliation_match_rate=Decimal("99.1"),
        runway_projection_days=210,
        key_highlights=[
            "PayPal Sandbox settlements processed $8,231.99 across 8 batches.",
            "Reconciliation accuracy at 99.1% with only 1 partner fee discrepancy flagged.",
            "Dispute PP-D-9928172 auto-packaged with digital activation evidence (84.5% win likelihood).",
            "Cash runway calculated at 210 days with $128,450.00 in total liquid reserves."
        ],
        recommended_decisions=[
            "Sign off on pending $450.00 PayPal refund in Governance approval queue.",
            "Confirm evidence submission for dispute PP-D-9928172 before 7-day expiration."
        ]
    )
    db.add(briefing)

    conv = CopilotConversation(
        conversation_id="conv_demo_welcome",
        title="Welcome & Financial Health Overview"
    )
    db.add(conv)
    db.flush()

    msg1 = CopilotMessage(
        message_id="msg_welcome_01",
        conversation_id=conv.conversation_id,
        sender="ASSISTANT",
        content=(
            "Hello! I am your **AURA Financial Copilot**. I have unified real-time visibility across all 10 financial modules:\n\n"
            "• **Total Settled Volume**: $8,231.99 USD across PayPal transactions\n"
            "• **Reconciliation Match Rate**: 99.1% exact/tolerance match\n"
            "• **Active Disputes**: 1 inquiry with 84.5% automated evidence win probability\n"
            "• **Treasury Reserves**: $128,450.00 USD with 210 days of projected runway\n\n"
            "How can I assist you with treasury forecasts, payment routing, dispute responses, or governance approvals today?"
        ),
        referenced_records=["m01_financial_records", "m03_recon_runs", "m05_account_balances", "m06_dispute_cases"],
        confidence_score=Decimal("99.5")
    )
    db.add(msg1)

    db.commit()
    print("Realistic seed data created successfully across all 10 modules!")
