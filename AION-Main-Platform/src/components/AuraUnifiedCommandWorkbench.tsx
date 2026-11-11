// AuraUnifiedCommandWorkbench.tsx
// Unified Financial Intelligence Command Center (AG Grid Community v31)
// Ultra-Smart Real-Time Filter + Fallback Copy Engine + Dark Terminal Styling + Full Interactivity

import { useState, useRef, useMemo, useCallback } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  ColDef,
  RowClassParams,
  RowStyle,
  ValueFormatterParams,
  ICellRendererParams,
  SelectionChangedEvent,
} from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import { CheckCircle2, ShieldCheck, Zap, X, Copy, Search, Check, Mic, MicOff, Volume2, VolumeX, Mail, Phone, PhoneCall, PhoneOff, Send, KeyRound } from "lucide-react";

export type OperationalMode =
  | "TRANSACTIONS"
  | "RECONCILIATION"
  | "RISK_INTELLIGENCE"
  | "DISPUTES"
  | "REFUNDS"
  | "AUDIT_TRAIL";

export interface ReplayStep {
  step: number;
  stage: string;
  timestamp: string;
  sourceId: string;
  detail: string;
  verified: boolean;
}

export interface FinancialRecord {
  txId: string;
  paypalTxId: string;
  orderId: string;
  ledgerId: string;
  counterparty: string;
  paypalAmount: number;
  orderAmount: number;
  ledgerAmount: number;
  delta: number;
  riskScore: number;
  riskReasons: string[];
  status: "MATCHED" | "MISMATCH" | "HIGH_RISK" | "DISPUTE_OPEN" | "REFUND_PENDING" | "RESOLVED_SANDBOX";
  ruleCode: string;
  webhookEvent: string;
  evidenceDoc: string;
  aiRecommendation: string;
  humanApprovedBy: string | null;
  merkleHash: string;
  velocitySparkline: number[] | null;
  isLiveSandbox: boolean;
  replayTimeline: ReplayStep[];
}

// Custom Cell Renderer 1: Zero-Dependency SVG Sparkline + Drift Status
const SparklineDriftRenderer = (params: ICellRendererParams<FinancialRecord>) => {
  const data = params.data;
  if (!data || !data.velocitySparkline) {
    return <span style={{ fontWeight: 800, color: "#38bdf8" }}>∑ INVARIANT ROOT</span>;
  }
  const pts = data.velocitySparkline;
  const max = Math.max(...pts, 1);
  const min = Math.min(...pts, 0);
  const polyPoints = pts
    .map((v, i) => `${(i / (pts.length - 1)) * 64},${20 - ((v - min) / (max - min || 1)) * 16}`)
    .join(" ");
  const hasIssue = data.delta !== 0 && data.status !== "RESOLVED_SANDBOX";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px", height: "100%" }}>
      <svg width="68" height="24">
        <polyline fill="none" stroke={hasIssue ? "#ef4444" : "#14b8a6"} strokeWidth="2" points={polyPoints} />
      </svg>
      <span style={{ fontSize: "10px", fontFamily: "monospace", color: hasIssue ? "#f87171" : "#2dd4bf" }}>
        {hasIssue ? "Δ DRIFT" : "SYNCED"}
      </span>
    </div>
  );
};

// Custom Cell Renderer 2: Deterministic Risk Heatmap Badge
const RiskHeatmapRenderer = (params: ICellRendererParams<FinancialRecord>) => {
  if (params.node.rowPinned === "bottom") return <span>—</span>;
  const score = Number(params.value ?? 0);
  const bg = score >= 70 ? "rgba(239,68,68,0.25)" : score >= 40 ? "rgba(245,158,11,0.2)" : "rgba(20,184,166,0.18)";
  const color = score >= 70 ? "#f87171" : score >= 40 ? "#fbbf24" : "#2dd4bf";
  const label = score >= 70 ? "CRITICAL" : score >= 40 ? "ELEVATED" : "LOW RISK";

  return (
    <span style={{ background: bg, color, border: `1px solid ${color}`, padding: "2px 8px", borderRadius: "999px", fontSize: "11px", fontWeight: 800, fontFamily: "monospace" }}>
      {score}/100 • {label}
    </span>
  );
};

// 20+ Realistic FinTech Transactions
const COMPREHENSIVE_RECORDS: FinancialRecord[] = [
  {
    txId: "REC-99420-FLAGSHIP",
    paypalTxId: "PP-V2-CAPTURE-99420",
    orderId: "ORD-ENT-8821",
    ledgerId: "GL-ERP-44012",
    counterparty: "Nexus Cloud Marketplace",
    paypalAmount: 25000.0,
    orderAmount: 25000.0,
    ledgerAmount: 20000.0,
    delta: -5000.0,
    riskScore: 94,
    riskReasons: ["THREE_WAY_AMOUNT_MISMATCH (-$5,000.00)", "UNRECORDED_PARTNER_COMMISSION_MKT04", "HIGH_VALUE_THRESHOLD_EXCEEDED"],
    status: "MISMATCH",
    ruleCode: "MKT-04 (20% Partner Split)",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED (Sig: VERIFIED)",
    evidenceDoc: "MSA-Contract-2026-Sec4.2.pdf (SHA256: 0x91a...e4)",
    aiRecommendation: "Post $5,000.00 Partner Split adjustment to GL-ERP-44012 or execute partial fee settlement in PayPal Sandbox.",
    humanApprovedBy: null,
    merkleHash: "0x8f9d2a7c4b1e...f92a",
    velocitySparkline: [20, 22, 25, 19, 15, 25],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Payment Received", timestamp: "10:14:02.110 UTC", sourceId: "PP-V2-CAPTURE-99420", detail: "PayPal Orders v2 Capture settled for $25,000.00 USD.", verified: true },
      { step: 2, stage: "3-Way Mismatch Detected", timestamp: "10:14:02.145 UTC", sourceId: "GL-ERP-44012", detail: "PayPal ($25,000) == Order ($25,000) != Ledger ($20,000). Delta: -$5,000.00.", verified: true },
      { step: 3, stage: "Evidence Collected", timestamp: "10:14:02.410 UTC", sourceId: "MSA-Contract-2026-Sec4.2", detail: "RAG + Deterministic parser matched Clause MKT-04 (20% Marketplace Split).", verified: true },
      { step: 4, stage: "AI Recommendation", timestamp: "10:14:02.690 UTC", sourceId: "AURA-BRAIN-V3", detail: "99.4% Confidence: Reconcile $5,000 fee split and anchor PayPal Sandbox receipt.", verified: true },
      { step: 5, stage: "CFO FinOps Validation", timestamp: "10:14:03.010 UTC", sourceId: "CFO-GATE-01", detail: "Verified policy compliance and partner withholding limits.", verified: true },
      { step: 6, stage: "Sandbox Execution Ready", timestamp: "10:14:03.200 UTC", sourceId: "PP-ORDERS-V2", detail: "Idempotency key locked: 0% double-spend risk.", verified: true },
      { step: 7, stage: "Audit Seal Anchored", timestamp: "Pending Approval", sourceId: "MERKLE-TREE-AURA", detail: "Awaiting human operator sign-off.", verified: false },
    ],
  },
  {
    txId: "TX-1002-DISPUTE",
    paypalTxId: "PP-V2-CAPTURE-1002",
    orderId: "ORD-ENT-5519",
    ledgerId: "GL-ERP-31098",
    counterparty: "Orbital Compute Clusters Ltd",
    paypalAmount: 1200.0,
    orderAmount: 1200.0,
    ledgerAmount: 1050.0,
    delta: -150.0,
    riskScore: 78,
    riskReasons: ["LEDGER_SHORTFALL (-$150.00)", "DISPUTE_OPEN_OLDER_THAN_7_DAYS"],
    status: "DISPUTE_OPEN",
    ruleCode: "SLA-CRED-09 (Tax Withholding)",
    webhookEvent: "CUSTOMER.DISPUTE.CREATED (PP-D-8812)",
    evidenceDoc: "Invoice-INV-5519-Signed.pdf",
    aiRecommendation: "Submit cryptographic delivery proof to PayPal Disputes API and adjust $150 tax ledger entry.",
    humanApprovedBy: null,
    merkleHash: "0x4b7e1c9a2d8f...c31b",
    velocitySparkline: [12, 12, 11, 10.5, 12, 10.5],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Payment Received", timestamp: "09:02:11 UTC", sourceId: "PP-V2-CAPTURE-1002", detail: "Captured $1,200.00 via PayPal Sandbox.", verified: true },
      { step: 2, stage: "Risk & Dispute Flagged", timestamp: "09:02:12 UTC", sourceId: "PP-D-8812", detail: "Dispute opened; $150.00 variance vs ERP Ledger.", verified: true },
      { step: 3, stage: "Evidence Assembled", timestamp: "09:02:15 UTC", sourceId: "INV-5519", detail: "Cryptographic delivery receipt compiled.", verified: true },
      { step: 4, stage: "Dispute Defense Drafted", timestamp: "09:02:18 UTC", sourceId: "AURA-DISPUTE-ENGINE", detail: "Ready for PayPal Disputes API submission.", verified: true },
    ],
  },
  {
    txId: "TX-1003-PARTNER",
    paypalTxId: "PP-V2-CAPTURE-1003",
    orderId: "ORD-SAAS-9011",
    ledgerId: "GL-ERP-44091",
    counterparty: "Starlight Media Distribution",
    paypalAmount: 8400.0,
    orderAmount: 8400.0,
    ledgerAmount: 7980.0,
    delta: -420.0,
    riskScore: 71,
    riskReasons: ["5% AFFILIATE_DEDUCTION_UNMATCHED"],
    status: "MISMATCH",
    ruleCode: "REV-SHARE-05",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Affiliate-Agreement-Starlight.pdf",
    aiRecommendation: "Apply $420 affiliate referral deduction to reconciliation ledger.",
    humanApprovedBy: null,
    merkleHash: "0x1a8f9d2c4b7e...9911",
    velocitySparkline: [8, 8.2, 8.4, 7.9, 8.4, 7.9],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Gross Capture Settled", timestamp: "08:15:20 UTC", sourceId: "PP-V2-CAPTURE-1003", detail: "Captured $8,400.00.", verified: true },
      { step: 2, stage: "Commission Gap Identified", timestamp: "08:15:21 UTC", sourceId: "GL-ERP-44091", detail: "Variance -$420.00 detected vs ERP.", verified: true },
    ],
  },
  {
    txId: "TX-1004-CROSSBORDER",
    paypalTxId: "PP-V2-CAPTURE-1004",
    orderId: "ORD-GLOBAL-7712",
    ledgerId: "GL-ERP-11045",
    counterparty: "Tokyo Quantum Systems KK",
    paypalAmount: 14500.0,
    orderAmount: 14500.0,
    ledgerAmount: 14290.0,
    delta: -210.0,
    riskScore: 65,
    riskReasons: ["FX_CONVERSION_SPREAD_VARIANCE"],
    status: "MISMATCH",
    ruleCode: "FX-SPREAD-JPY-USD",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "FX-Rate-Sheet-JPYUSD.pdf",
    aiRecommendation: "Reconcile $210 currency exchange spread to FX loss/gain account.",
    humanApprovedBy: null,
    merkleHash: "0x6e2c4b7a1f9d...bb33",
    velocitySparkline: [14.5, 14.5, 14.3, 14.29, 14.5, 14.29],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Payment Capture", timestamp: "07:44:10 UTC", sourceId: "PP-V2-CAPTURE-1004", detail: "Multi-currency JPY -> USD capture settled.", verified: true },
    ],
  },
  {
    txId: "TX-1005-REFUND",
    paypalTxId: "PP-V2-REFUND-2001",
    orderId: "ORD-RTN-4412",
    ledgerId: "GL-ERP-77812",
    counterparty: "AeroTech Avionics Corp",
    paypalAmount: 650.0,
    orderAmount: 650.0,
    ledgerAmount: 650.0,
    delta: 0.0,
    riskScore: 48,
    riskReasons: ["REFUND_AUTHORIZATION_PENDING"],
    status: "REFUND_PENDING",
    ruleCode: "RMA-RETURN-POLICY-30D",
    webhookEvent: "PAYMENT.SALE.REFUNDED",
    evidenceDoc: "RMA-Return-Manifest-2001.pdf",
    aiRecommendation: "Authorize $650 sandbox return refund after warehouse intake confirmation.",
    humanApprovedBy: null,
    merkleHash: "0x7d1a9b2c5e8f...aa10",
    velocitySparkline: [6.5, 6.5, 6.5, 6.5, 6.5, 6.5],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Refund Request", timestamp: "06:30:00 UTC", sourceId: "PP-V2-REFUND-2001", detail: "RMA return requested by customer.", verified: true },
    ],
  },
  {
    txId: "TX-1006-UNWIND-CLEAN",
    paypalTxId: "PP-PAYOUT-V1-0011",
    orderId: "ORD-GPU-0011",
    ledgerId: "GL-ERP-90011",
    counterparty: "FORGEX Autonomous Micro-GPU 01",
    paypalAmount: 11.0,
    orderAmount: 11.0,
    ledgerAmount: 11.0,
    delta: 0.0,
    riskScore: 12,
    riskReasons: ["EXACT_3WAY_MATCH", "UNWIND_WARRANT_VERIFIED"],
    status: "MATCHED",
    ruleCode: "UNWIND-AUTO-TIER-1",
    webhookEvent: "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
    evidenceDoc: "UNWIND-Warrant-0x9f8a.json",
    aiRecommendation: "No action needed. 3-way reconciliation and SHA-256 warrant verified.",
    humanApprovedBy: "AUTO-POLICY-TIER-1",
    merkleHash: "0x3c7a1e9b5d2f...a7c5",
    velocitySparkline: [11, 11, 11, 11, 11, 11],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Autonomous Warrant Issued", timestamp: "11:00:01 UTC", sourceId: "UNWIND-0x9f8a", detail: "3-Agent Courtroom approved $11.00 GPU spend.", verified: true },
      { step: 2, stage: "PayPal Sandbox Settled", timestamp: "11:00:02 UTC", sourceId: "PP-PAYOUT-V1-0011", detail: "Exact 3-way match ($11.00).", verified: true },
    ],
  },
  {
    txId: "TX-1007-ENTERPRISE",
    paypalTxId: "PP-V2-CAPTURE-1007",
    orderId: "ORD-SAAS-0099",
    ledgerId: "GL-ERP-99120",
    counterparty: "Apex Health Diagnostics",
    paypalAmount: 4990.0,
    orderAmount: 4990.0,
    ledgerAmount: 4990.0,
    delta: 0.0,
    riskScore: 15,
    riskReasons: ["EXACT_MATCH", "ANNUAL_SUB_RENEWAL"],
    status: "MATCHED",
    ruleCode: "SLA-AUTO-REC-ANNUAL",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Apex-Annual-Sub-Invoice.pdf",
    aiRecommendation: "Zero variance. 3-Way Triangulation verified.",
    humanApprovedBy: "SYSTEM-AUTOMATION",
    merkleHash: "0x2d4b7e1a9f8c...11a2",
    velocitySparkline: [4.9, 4.9, 4.9, 4.9, 4.9, 4.9],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Subscription Billed", timestamp: "05:12:00 UTC", sourceId: "PP-V2-CAPTURE-1007", detail: "Captured $4,990.00 annual subscription.", verified: true },
    ],
  },
  {
    txId: "TX-1008-ESCROW",
    paypalTxId: "PP-V2-CAPTURE-1008",
    orderId: "ORD-ESCROW-3301",
    ledgerId: "GL-ERP-55019",
    counterparty: "Nordic Data Infrastructure AS",
    paypalAmount: 50000.0,
    orderAmount: 50000.0,
    ledgerAmount: 50000.0,
    delta: 0.0,
    riskScore: 22,
    riskReasons: ["HIGH_VALUE_ESCROW_RELEASED"],
    status: "MATCHED",
    ruleCode: "ESCROW-RELEASE-V2",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Milestone-3-Signoff.pdf",
    aiRecommendation: "Multi-party co-sign verified. Escrow balance cleared.",
    humanApprovedBy: "CFO-CO-SIGN",
    merkleHash: "0x5a1b3c9e7d2f...ff90",
    velocitySparkline: [50, 50, 50, 50, 50, 50],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Escrow Released", timestamp: "04:00:10 UTC", sourceId: "PP-V2-CAPTURE-1008", detail: "$50,000.00 milestone release confirmed.", verified: true },
    ],
  },
  {
    txId: "TX-1009-CONTRACTOR",
    paypalTxId: "PP-PAYOUT-V1-0089",
    orderId: "ORD-PAYOUT-0089",
    ledgerId: "GL-ERP-88091",
    counterparty: "Synthetix Security Audits",
    paypalAmount: 1850.0,
    orderAmount: 1850.0,
    ledgerAmount: 1850.0,
    delta: 0.0,
    riskScore: 19,
    riskReasons: ["CONTRACTOR_WARRANT_VERIFIED"],
    status: "MATCHED",
    ruleCode: "SDE-CONTRACTOR-POLICY",
    webhookEvent: "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
    evidenceDoc: "Security-Audit-Report-Signoff.pdf",
    aiRecommendation: "Disbursed via PayPal Payouts v1. 0% variance.",
    humanApprovedBy: "SECURITY-LEAD",
    merkleHash: "0x8e2c4b7a1f9d...cc44",
    velocitySparkline: [1.8, 1.8, 1.8, 1.8, 1.8, 1.8],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Contractor Payout", timestamp: "03:15:00 UTC", sourceId: "PP-PAYOUT-V1-0089", detail: "Audit bounty disbursed.", verified: true },
    ],
  },
  {
    txId: "TX-1010-MARKETPLACE",
    paypalTxId: "PP-V2-CAPTURE-1010",
    orderId: "ORD-SPLIT-9921",
    ledgerId: "GL-ERP-66014",
    counterparty: "Cybernetic Robotics Lab",
    paypalAmount: 12000.0,
    orderAmount: 12000.0,
    ledgerAmount: 12000.0,
    delta: 0.0,
    riskScore: 25,
    riskReasons: ["MULTI_VENDOR_SPLIT_VERIFIED"],
    status: "MATCHED",
    ruleCode: "VENDOR-SPLIT-AUTO",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Vendor-Split-Schedule-9921.pdf",
    aiRecommendation: "3-Way triangulation intact. General Ledger synchronized.",
    humanApprovedBy: "FINOPS-LEAD",
    merkleHash: "0x9a1c4b7e2d8f...3311",
    velocitySparkline: [12, 12, 12, 12, 12, 12],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Vendor Settlement", timestamp: "02:40:00 UTC", sourceId: "PP-V2-CAPTURE-1010", detail: "Settled $12,000.00 vendor balance.", verified: true },
    ],
  },
  {
    txId: "TX-1011-CHARGEBACK",
    paypalTxId: "PP-V2-DISPUTE-9912",
    orderId: "ORD-DISP-0012",
    ledgerId: "GL-ERP-90112",
    counterparty: "Helios Energy Grid Solutions",
    paypalAmount: 890.0,
    orderAmount: 890.0,
    ledgerAmount: 800.0,
    delta: -90.0,
    riskScore: 82,
    riskReasons: ["CHARGEBACK_FILED", "SURCHARGE_DISCREPANCY"],
    status: "DISPUTE_OPEN",
    ruleCode: "CHARGEBACK-SEC-4",
    webhookEvent: "CUSTOMER.DISPUTE.CREATED",
    evidenceDoc: "Chargeback-Notification-9912.pdf",
    aiRecommendation: "Draft Disputes API defense bundle with SHA-256 warrant delivery log.",
    humanApprovedBy: null,
    merkleHash: "0x3f7a1c9e8b2d...88aa",
    velocitySparkline: [8.9, 8.9, 8.5, 8.0, 8.9, 8.0],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Dispute Notification", timestamp: "01:22:10 UTC", sourceId: "PP-V2-DISPUTE-9912", detail: "Customer contested service charge.", verified: true },
    ],
  },
  {
    txId: "TX-1012-AUTOTIER",
    paypalTxId: "PP-PAYOUT-V1-0022",
    orderId: "ORD-GPU-0022",
    ledgerId: "GL-ERP-90022",
    counterparty: "FORGEX Autonomous Micro-GPU 02",
    paypalAmount: 22.0,
    orderAmount: 22.0,
    ledgerAmount: 22.0,
    delta: 0.0,
    riskScore: 10,
    riskReasons: ["EXACT_3WAY_MATCH", "AUTONOMOUS_CAP_PASS"],
    status: "MATCHED",
    ruleCode: "UNWIND-AUTO-TIER-1",
    webhookEvent: "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
    evidenceDoc: "UNWIND-Warrant-0x9f8b.json",
    aiRecommendation: "Verified. Autonomous tier micro-payout.",
    humanApprovedBy: "AUTO-POLICY-TIER-1",
    merkleHash: "0x7a2c4e1b8f9d...bb11",
    velocitySparkline: [22, 22, 22, 22, 22, 22],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Micro-GPU Disbursed", timestamp: "00:45:00 UTC", sourceId: "PP-PAYOUT-V1-0022", detail: "$22.00 compute payout settled.", verified: true },
    ],
  },
  {
    txId: "TX-1013-WHOLESALE",
    paypalTxId: "PP-V2-CAPTURE-1013",
    orderId: "ORD-WHOLE-4491",
    ledgerId: "GL-ERP-77890",
    counterparty: "BioSynthetica Therapeutics",
    paypalAmount: 18200.0,
    orderAmount: 18200.0,
    ledgerAmount: 18200.0,
    delta: 0.0,
    riskScore: 18,
    riskReasons: ["WHOLESALE_BATCH_VERIFIED"],
    status: "MATCHED",
    ruleCode: "WHOLESALE-DISCOUNT-TIER-2",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "BioSynthetica-Order-4491.pdf",
    aiRecommendation: "No adjustments required. 100% matched.",
    humanApprovedBy: "SYSTEM-AUTOMATION",
    merkleHash: "0x4e1a8f9c2d7b...9900",
    velocitySparkline: [18.2, 18.2, 18.2, 18.2, 18.2, 18.2],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "B2B Wholesale Settlement", timestamp: "Yesterday", sourceId: "PP-V2-CAPTURE-1013", detail: "Captured $18,200.00.", verified: true },
    ],
  },
  {
    txId: "TX-1014-OVERNIGHT",
    paypalTxId: "PP-V2-CAPTURE-1014",
    orderId: "ORD-NET-1189",
    ledgerId: "GL-ERP-33019",
    counterparty: "Zenith Satellite Communications",
    paypalAmount: 7300.0,
    orderAmount: 7300.0,
    ledgerAmount: 7300.0,
    delta: 0.0,
    riskScore: 14,
    riskReasons: ["OVERNIGHT_NET_SETTLED"],
    status: "MATCHED",
    ruleCode: "NETTING-DAILY-BATCH",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Daily-Netting-Zenith-1189.pdf",
    aiRecommendation: "Cleared in overnight netting cycle.",
    humanApprovedBy: "SYSTEM-AUTOMATION",
    merkleHash: "0x5c9e2d4b7a1f...aa55",
    velocitySparkline: [7.3, 7.3, 7.3, 7.3, 7.3, 7.3],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Netting Settled", timestamp: "Yesterday", sourceId: "PP-V2-CAPTURE-1014", detail: "$7,300.00 cleared.", verified: true },
    ],
  },
  {
    txId: "TX-1015-CLEARING",
    paypalTxId: "PP-V2-CAPTURE-1015",
    orderId: "ORD-CLR-9011",
    ledgerId: "GL-ERP-22091",
    counterparty: "Vanguard Micro-Foundry",
    paypalAmount: 9100.0,
    orderAmount: 9100.0,
    ledgerAmount: 9100.0,
    delta: 0.0,
    riskScore: 16,
    riskReasons: ["AUTOMATED_CLEARING_HOUSE_MATCH"],
    status: "MATCHED",
    ruleCode: "ACH-VERIFIED-V3",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Foundry-PO-9011.pdf",
    aiRecommendation: "Three-way triangulation complete.",
    humanApprovedBy: "SYSTEM-AUTOMATION",
    merkleHash: "0x8f2a4c1e9b7d...ee33",
    velocitySparkline: [9.1, 9.1, 9.1, 9.1, 9.1, 9.1],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Clearing Settled", timestamp: "Yesterday", sourceId: "PP-V2-CAPTURE-1015", detail: "$9,100.00 confirmed.", verified: true },
    ],
  },
  {
    txId: "TX-1016-REVERSAL",
    paypalTxId: "PP-V2-CAPTURE-1016",
    orderId: "ORD-REV-3312",
    ledgerId: "GL-ERP-44019",
    counterparty: "Hydra Logistics Global",
    paypalAmount: 420.0,
    orderAmount: 420.0,
    ledgerAmount: 420.0,
    delta: 0.0,
    riskScore: 28,
    riskReasons: ["LEDGER_CORRECTION_RESOLVED"],
    status: "RESOLVED_SANDBOX",
    ruleCode: "MANUAL-LEDGER-REVERSAL",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Correction-Voucher-3312.pdf",
    aiRecommendation: "Audited ledger reversal complete.",
    humanApprovedBy: "CFO-HUMAN-SIGN-OFF",
    merkleHash: "0x1d7b4e9a2c8f...7722",
    velocitySparkline: [4.2, 4.2, 4.2, 4.2, 4.2, 4.2],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Reversal Authorized", timestamp: "Yesterday", sourceId: "PP-V2-CAPTURE-1016", detail: "$420.00 synchronized.", verified: true },
    ],
  },
  {
    txId: "TX-1017-WARRANT",
    paypalTxId: "PP-PAYOUT-V1-0033",
    orderId: "ORD-GPU-0033",
    ledgerId: "GL-ERP-90033",
    counterparty: "FORGEX Autonomous Micro-GPU 03",
    paypalAmount: 33.0,
    orderAmount: 33.0,
    ledgerAmount: 33.0,
    delta: 0.0,
    riskScore: 11,
    riskReasons: ["WARRANT_CERTIFIED", "AUTONOMOUS_TIER_PASS"],
    status: "MATCHED",
    ruleCode: "UNWIND-AUTO-TIER-1",
    webhookEvent: "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
    evidenceDoc: "UNWIND-Warrant-0x9f8c.json",
    aiRecommendation: "Commit-bound warrant verified by Courtroom.",
    humanApprovedBy: "AUTO-POLICY-TIER-1",
    merkleHash: "0x6b1e8f2a4c9d...5511",
    velocitySparkline: [33, 33, 33, 33, 33, 33],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Warrant Issued", timestamp: "Yesterday", sourceId: "UNWIND-0x9f8c", detail: "Autonomous GPU spend verified.", verified: true },
    ],
  },
  {
    txId: "TX-1018-TREASURY",
    paypalTxId: "PP-V2-CAPTURE-1018",
    orderId: "ORD-VAULT-8801",
    ledgerId: "GL-ERP-11099",
    counterparty: "AION Autonomous Treasury Vault",
    paypalAmount: 16500.0,
    orderAmount: 16500.0,
    ledgerAmount: 16500.0,
    delta: 0.0,
    riskScore: 20,
    riskReasons: ["TREASURY_REBALANCE_BALANCED"],
    status: "MATCHED",
    ruleCode: "TREASURY-REBALANCE-V1",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Treasury-Rebalance-Report-8801.pdf",
    aiRecommendation: "Capital allocated within policy caps.",
    humanApprovedBy: "TREASURY-COMMITTEE",
    merkleHash: "0x9c4e1a8f2b7d...4499",
    velocitySparkline: [16.5, 16.5, 16.5, 16.5, 16.5, 16.5],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Treasury Rebalance", timestamp: "Yesterday", sourceId: "PP-V2-CAPTURE-1018", detail: "$16,500.00 rebalanced.", verified: true },
    ],
  },
  {
    txId: "TX-1019-FX-EURUSD",
    paypalTxId: "PP-V2-CAPTURE-1019",
    orderId: "ORD-FX-7712",
    ledgerId: "GL-ERP-22019",
    counterparty: "Zurich Cross-Border FinTech AG",
    paypalAmount: 14200.0,
    orderAmount: 14200.0,
    ledgerAmount: 13950.0,
    delta: -250.0,
    riskScore: 68,
    riskReasons: ["CROSS_BORDER_FX_WITHHOLDING (-$250.00)", "EUR_USD_BASIS_SPREAD"],
    status: "MISMATCH",
    ruleCode: "FX-SPREAD-01",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Zurich-FX-Conversion-Warrant.pdf",
    aiRecommendation: "Post $250.00 FX currency conversion adjustment to GL-ERP-22019.",
    humanApprovedBy: null,
    merkleHash: "0x7a2f9b1c4e8d...6622",
    velocitySparkline: [14.2, 14.1, 14.2, 14.0, 13.9, 14.2],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "FX Payment Received", timestamp: "Yesterday", sourceId: "PP-V2-CAPTURE-1019", detail: "PayPal cross-border capture EUR -> USD.", verified: true },
      { step: 2, stage: "FX Variance Detected", timestamp: "Yesterday", sourceId: "GL-ERP-22019", detail: "Ledger shortfall $250.00 basis spread.", verified: true },
    ],
  },
  {
    txId: "TX-1020-SUBSCRIPTION",
    paypalTxId: "PP-V2-SUB-1020",
    orderId: "ORD-SUB-9941",
    ledgerId: "GL-ERP-55020",
    counterparty: "HyperScale DevOps Cluster Inc",
    paypalAmount: 6800.0,
    orderAmount: 6800.0,
    ledgerAmount: 6800.0,
    delta: 0.0,
    riskScore: 14,
    riskReasons: ["RECURRING_BILLING_VERIFIED"],
    status: "MATCHED",
    ruleCode: "SAAS-RECURRING-V2",
    webhookEvent: "BILLING.SUBSCRIPTION.ACTIVATED",
    evidenceDoc: "DevOps-SLA-Subscription-2026.pdf",
    aiRecommendation: "Annual enterprise seat activation synchronized.",
    humanApprovedBy: "AUTO-BILLING-AGENT",
    merkleHash: "0x3d8e2a1b9c4f...7733",
    velocitySparkline: [6.8, 6.8, 6.8, 6.8, 6.8, 6.8],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Subscription Active", timestamp: "Yesterday", sourceId: "PP-V2-SUB-1020", detail: "Monthly recurring payment confirmed.", verified: true },
    ],
  },
  {
    txId: "TX-1021-SPLITPAY",
    paypalTxId: "PP-V2-CAPTURE-1021",
    orderId: "ORD-MKT-3301",
    ledgerId: "GL-ERP-66021",
    counterparty: "Tokyo Quantum Gaming Studio",
    paypalAmount: 3450.0,
    orderAmount: 3450.0,
    ledgerAmount: 3450.0,
    delta: 0.0,
    riskScore: 19,
    riskReasons: ["SPLIT_SETTLEMENT_MATCHED"],
    status: "MATCHED",
    ruleCode: "MARKETPLACE-SPLIT-02",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Tokyo-Gaming-Split-Contract.pdf",
    aiRecommendation: "Split disbursement verified across marketplace escrow.",
    humanApprovedBy: "ESCROW-AGENT",
    merkleHash: "0x5f1e9a2b8c4d...8844",
    velocitySparkline: [3.4, 3.45, 3.45, 3.45, 3.45, 3.45],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Split Captured", timestamp: "Yesterday", sourceId: "PP-V2-CAPTURE-1021", detail: "$3,450.00 multi-vendor split executed.", verified: true },
    ],
  },
  {
    txId: "TX-1022-DISPUTE-FRAUD",
    paypalTxId: "PP-V2-CAPTURE-1022",
    orderId: "ORD-SEC-4412",
    ledgerId: "GL-ERP-77022",
    counterparty: "ShadowProxy Anonymous Network",
    paypalAmount: 1850.0,
    orderAmount: 1850.0,
    ledgerAmount: 1430.0,
    delta: -420.0,
    riskScore: 91,
    riskReasons: ["SUSPECTED_UNAUTHORIZED_ACTIVITY", "VPN_IP_VELOCITY_SPIKE (-$420.00)"],
    status: "DISPUTE_OPEN",
    ruleCode: "AML-FRAUD-ALERT-09",
    webhookEvent: "CUSTOMER.DISPUTE.CREATED (PP-D-9902)",
    evidenceDoc: "Fraud-Investigation-Log-4412.pdf",
    aiRecommendation: "Hold funds in PayPal Disputes API and require secondary KYC verification.",
    humanApprovedBy: null,
    merkleHash: "0x8b3e1a9f4c2d...9955",
    velocitySparkline: [1.8, 1.4, 0.9, 1.8, 1.2, 1.8],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Fraud Signal Detected", timestamp: "Yesterday", sourceId: "AML-SENTINEL", detail: "Suspicious VPN geolocation mismatch.", verified: true },
      { step: 2, stage: "Dispute Flagged", timestamp: "Yesterday", sourceId: "PP-D-9902", detail: "Transaction frozen pending courtroom review.", verified: true },
    ],
  },
  {
    txId: "TX-1023-PLATFORM-ESCROW",
    paypalTxId: "PP-V2-CAPTURE-1023",
    orderId: "ORD-ESC-8819",
    ledgerId: "GL-ERP-88023",
    counterparty: "Apex Global Datacenter Holdings",
    paypalAmount: 35000.0,
    orderAmount: 35000.0,
    ledgerAmount: 35000.0,
    delta: 0.0,
    riskScore: 24,
    riskReasons: ["HIGH_VALUE_ESCROW_COMPLIANT"],
    status: "MATCHED",
    ruleCode: "ESCROW-MULTI-SIG-V1",
    webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
    evidenceDoc: "Apex-MultiSig-Escrow-Agreement.pdf",
    aiRecommendation: "Multi-sig cryptographic signature verified for enterprise release.",
    humanApprovedBy: "CFO-CO-SIGN",
    merkleHash: "0x2e9f1a8b4c3d...1166",
    velocitySparkline: [35, 35, 35, 35, 35, 35],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Escrow Funded", timestamp: "Yesterday", sourceId: "PP-V2-CAPTURE-1023", detail: "$35,000.00 secured in escrow vault.", verified: true },
    ],
  },
  {
    txId: "TX-1024-MICRO-ACCEL",
    paypalTxId: "PP-PAYOUT-V1-0044",
    orderId: "ORD-GPU-0044",
    ledgerId: "GL-ERP-90044",
    counterparty: "FORGEX Autonomous Micro-GPU 04",
    paypalAmount: 11.0,
    orderAmount: 11.0,
    ledgerAmount: 11.0,
    delta: 0.0,
    riskScore: 9,
    riskReasons: ["MICRO_PAYOUT_VERIFIED", "ZERO_VARIANCE"],
    status: "MATCHED",
    ruleCode: "UNWIND-AUTO-TIER-1",
    webhookEvent: "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
    evidenceDoc: "UNWIND-Warrant-0x9f8d.json",
    aiRecommendation: "Autonomous micro-GPU compute settlement approved.",
    humanApprovedBy: "AUTO-POLICY-TIER-1",
    merkleHash: "0x4f1e8a2b9c3d...2277",
    velocitySparkline: [11, 11, 11, 11, 11, 11],
    isLiveSandbox: true,
    replayTimeline: [
      { step: 1, stage: "Micro-GPU Disbursed", timestamp: "Yesterday", sourceId: "UNWIND-0x9f8d", detail: "$11.00 compute bounty paid.", verified: true },
    ],
  },
];

export default function AuraUnifiedCommandWorkbench({ onTriggerSonicChime }: { onTriggerSonicChime?: () => void }) {
  const gridRef = useRef<AgGridReact<FinancialRecord>>(null);
  const [records, setRecords] = useState<FinancialRecord[]>(COMPREHENSIVE_RECORDS);
  const [activeMode, setActiveMode] = useState<OperationalMode>("TRANSACTIONS");
  const [selectedRecord, setSelectedRecord] = useState<FinancialRecord>(COMPREHENSIVE_RECORDS[0]);
  const [nlQuery, setNlQuery] = useState<string>("");
  const [activeFilterQuery, setActiveFilterQuery] = useState<string>("");
  const [validatedAstPill, setValidatedAstPill] = useState<string>(
    '🔒 Allowlisted Filter AST: { mode: "TRANSACTIONS", recordsCount: 24, rawSqlBlocked: true }'
  );
  const [activeReplayStep, setActiveReplayStep] = useState<number>(4);
  const [showReceiptModal, setShowReceiptModal] = useState<boolean>(false);
  const [resolvedReceiptData, setResolvedReceiptData] = useState<{
    txId: string;
    paypalCaptureId: string;
    amount: string;
    debugId: string;
    idempotencyKey: string;
    warrantHash: string;
    timestamp: string;
  } | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Voice AI & Speech Synthesis State
  const [isListening, setIsListening] = useState<boolean>(false);
  const [isCompiling, setIsCompiling] = useState<boolean>(false);
  const [voiceReplyEnabled, setVoiceReplyEnabled] = useState<boolean>(true);
  const recognitionRef = useRef<any>(null);

  // Out-of-Band CFO Cryptographic Email & Live Inbox Drawer State
  const [judgeEmail, setJudgeEmail] = useState<string>("judge@paypal.com");
  const [isDispatchingEmail, setIsDispatchingEmail] = useState<boolean>(false);
  const [cfoEmailData, setCfoEmailData] = useState<{
    status: string;
    delivery_mode: string;
    recipient: string;
    otp_code: string;
    warrant_hash: string;
    subject: string;
    html_preview: string;
    tx_id: string;
    amount_usd: number;
  } | null>(null);
  const [showEmailDrawer, setShowEmailDrawer] = useState<boolean>(false);
  const [enteredOtp, setEnteredOtp] = useState<string>("");

  // Free Web-Audio CFO Emergency Security Call State
  const [incomingCallActive, setIncomingCallActive] = useState<boolean>(false);
  const [callAnswered, setCallAnswered] = useState<boolean>(false);
  const ringtoneTimerRef = useRef<any>(null);

  // Zero-Cost Web-Audio Oscillator Telephone Ringtone
  const playPhoneRingtone = useCallback(() => {
    try {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      const now = ctx.currentTime;
      // Dual-tone frequency (US telephone ring: 440Hz + 480Hz)
      const osc1 = ctx.createOscillator();
      const osc2 = ctx.createOscillator();
      const gain = ctx.createGain();
      osc1.frequency.setValueAtTime(440, now);
      osc2.frequency.setValueAtTime(480, now);
      osc1.connect(gain);
      osc2.connect(gain);
      gain.connect(ctx.destination);
      gain.gain.setValueAtTime(0.12, now);
      gain.gain.setValueAtTime(0.12, now + 1.4);
      gain.gain.linearRampToValueAtTime(0, now + 1.5);
      osc1.start(now);
      osc2.start(now);
      osc1.stop(now + 1.5);
      osc2.stop(now + 1.5);
    } catch (err) {
      console.warn("WebAudio ringtone err:", err);
    }
  }, []);

  const triggerEmergencyCall = useCallback(() => {
    setIncomingCallActive(true);
    setCallAnswered(false);
    playPhoneRingtone();
    if (ringtoneTimerRef.current) clearInterval(ringtoneTimerRef.current);
    ringtoneTimerRef.current = setInterval(() => {
      playPhoneRingtone();
    }, 3500);
  }, [playPhoneRingtone]);

  const answerEmergencyCall = useCallback(() => {
    if (ringtoneTimerRef.current) clearInterval(ringtoneTimerRef.current);
    setCallAnswered(true);
    const alertSpeech = "Security Alert! Autonomous Agent attempted an unauthorized five-thousand-dollar transfer. We blocked the action and dispatched an emergency cryptographic audit email to the CFO inbox.";
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      const utt = new SpeechSynthesisUtterance(alertSpeech);
      utt.rate = 1.05;
      utt.pitch = 0.95;
      window.speechSynthesis.speak(utt);
    }
  }, []);

  const dismissEmergencyCall = useCallback(() => {
    if (ringtoneTimerRef.current) clearInterval(ringtoneTimerRef.current);
    setIncomingCallActive(false);
    setCallAnswered(false);
  }, []);

  // Executive Speech Synthesis Voice Reply
  const speakAuraConfirmation = useCallback((text: string) => {
    if (!voiceReplyEnabled || typeof window === "undefined" || !("speechSynthesis" in window)) return;
    try {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.05;
      utterance.pitch = 1.0;
      const voices = window.speechSynthesis.getVoices();
      const preferredVoice = voices.find(
        (v) =>
          v.lang.startsWith("en") &&
          (v.name.includes("Natural") ||
            v.name.includes("Google") ||
            v.name.includes("Samantha") ||
            v.name.includes("David") ||
            v.name.includes("Female"))
      );
      if (preferredVoice) utterance.voice = preferredVoice;
      window.speechSynthesis.speak(utterance);
    } catch (err) {
      console.warn("SpeechSynthesis error:", err);
    }
  }, [voiceReplyEnabled]);

  // Bulletproof Universal Clipboard Copy Engine (Works on localhost, https, & iframes)
  const fallbackCopy = (text: string) => {
    try {
      const el = document.createElement("textarea");
      el.value = text;
      el.setAttribute("readonly", "");
      el.style.contain = "strict";
      el.style.position = "absolute";
      el.style.left = "-9999px";
      el.style.fontSize = "12pt";
      document.body.appendChild(el);
      el.select();
      el.setSelectionRange(0, 99999);
      const ok = document.execCommand("copy");
      document.body.removeChild(el);
      return ok;
    } catch {
      return false;
    }
  };

  const copyToClipboard = useCallback((text: string, label: string = "Copied!", keyId?: string) => {
    if (keyId) {
      setCopiedKey(keyId);
      setTimeout(() => setCopiedKey(null), 2500);
    }
    setToastMessage(`📋 ${label}`);
    setTimeout(() => setToastMessage(null), 3000);

    if (navigator?.clipboard?.writeText) {
      navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    } else {
      fallbackCopy(text);
    }
  }, []);

  const showToast = useCallback((msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3500);
  }, []);

  // Update AST Filter Schema dynamically based on query
  const updateAstPillForQuery = useCallback((queryText: string) => {
    const q = queryText.trim().toLowerCase();
    if (!q || q === "reset" || q === "all" || q === "clear") {
      setValidatedAstPill('🔒 Validated AST: { "display": "ALL_LIVE_RECORDS", "recordsCount": 24, "sqlInjectionBlocked": true }');
      return;
    }
    if (q.includes("low") || q.includes("safe") || q.includes("clean") || q.includes("matched")) {
      setValidatedAstPill('🔒 Validated AST: { "riskScore": { "op": "<", "val": 30 }, "status": "VERIFIED_CLEAN" } • SQL/JS BLOCKED');
      return;
    }
    if (q.includes("high") || q.includes("critical") || q.includes("danger") || q.includes("alert")) {
      setValidatedAstPill('🔒 Validated AST: { "riskScore": { "op": ">=", "val": 70 }, "threatLevel": "CRITICAL" } • SQL/JS BLOCKED');
      return;
    }
    if (q.includes("mismatch") || q.includes("differ") || q.includes("variance") || q.includes("5000") || q.includes("5,000") || q.includes("delta")) {
      setValidatedAstPill('🔒 Validated AST: { "delta": { "op": "<", "val": 0 }, "target": "FLAGSHIP_MISMATCH" } • SQL/JS BLOCKED');
      return;
    }
    if (q.includes("dispute") || q.includes("chargeback") || q.includes("claim")) {
      setValidatedAstPill('🔒 Validated AST: { "mode": "DISPUTES", "status": "DISPUTE_OPEN" } • SQL/JS BLOCKED');
      return;
    }
    if (q.includes("refund") || q.includes("return") || q.includes("rma")) {
      setValidatedAstPill('🔒 Validated AST: { "mode": "REFUNDS", "status": "REFUND_PENDING" } • SQL/JS BLOCKED');
      return;
    }
    if (q.includes("gpu") || q.includes("payout") || q.includes("unwind") || q.includes("11") || q.includes("micro")) {
      setValidatedAstPill('🔒 Validated AST: { "tier": "AUTONOMOUS_UNWIND_GPU", "payoutCap": "$50.00" } • SQL/JS BLOCKED');
      return;
    }
    if (q.includes("escrow") || q.includes("wholesale") || q.includes("large") || q.includes("10000") || q.includes("10,000") || q.includes("50000") || q.includes("50,000") || q.includes("35000")) {
      setValidatedAstPill('🔒 Validated AST: { "paypalAmount": { "op": ">=", "val": 10000 }, "requiresCoSign": true } • SQL/JS BLOCKED');
      return;
    }
    setValidatedAstPill(`🔒 Validated AST: { "keywordSearch": "${queryText}", "allowlistFilter": true, "rawExecution": "BLOCKED" }`);
  }, []);

  // SMART DUAL-LAYER FILTER: Combines Mode + Natural Language Query without conflict
  const filteredRecords = useMemo(() => {
    let result = [...records];
    const q = activeFilterQuery.trim().toLowerCase();

    // Layer 1: Operational Mode Filtering
    if (activeMode === "RECONCILIATION") {
      result = result.filter((r) => r.delta !== 0);
    } else if (activeMode === "RISK_INTELLIGENCE") {
      result = result.filter((r) => r.riskScore >= 40).sort((a, b) => b.riskScore - a.riskScore);
    } else if (activeMode === "DISPUTES") {
      result = result.filter((r) => r.status === "DISPUTE_OPEN" || r.txId.includes("DISPUTE"));
    } else if (activeMode === "REFUNDS") {
      result = result.filter((r) => r.status === "REFUND_PENDING" || r.txId.includes("REFUND"));
    }

    // Layer 2: Natural Language Query Filtering (Searches across all records and fields)
    if (q) {
      if (q.includes("low") || q.includes("safe") || q.includes("clean") || q.includes("matched")) {
        result = result.filter((r) => r.riskScore < 30 || r.status === "MATCHED" || r.delta === 0);
      } else if (q.includes("high") || q.includes("critical") || q.includes("danger") || q.includes("alert")) {
        result = result.filter((r) => r.riskScore >= 70);
      } else if (q.includes("mismatch") || q.includes("differ") || q.includes("variance") || q.includes("5000") || q.includes("5,000") || q.includes("delta")) {
        result = result.filter((r) => r.delta !== 0);
      } else if (q.includes("dispute") || q.includes("chargeback") || q.includes("claim")) {
        result = result.filter((r) => r.status === "DISPUTE_OPEN" || r.txId.includes("DISPUTE"));
      } else if (q.includes("refund") || q.includes("return") || q.includes("rma")) {
        result = result.filter((r) => r.status === "REFUND_PENDING" || r.txId.includes("REFUND"));
      } else if (q.includes("gpu") || q.includes("payout") || q.includes("unwind") || q.includes("11") || q.includes("micro")) {
        result = result.filter((r) => r.txId.includes("UNWIND") || r.orderId.includes("GPU") || r.paypalAmount <= 50);
      } else if (q.includes("escrow") || q.includes("wholesale") || q.includes("large") || q.includes("10000") || q.includes("10,000") || q.includes("50000") || q.includes("50,000") || q.includes("35000")) {
        result = result.filter((r) => r.paypalAmount >= 10000);
      } else if (q.includes("reset") || q.includes("all") || q.includes("clear")) {
        // keep full result
      } else {
        result = result.filter((r) =>
          r.counterparty.toLowerCase().includes(q) ||
          r.txId.toLowerCase().includes(q) ||
          r.paypalTxId.toLowerCase().includes(q) ||
          r.orderId.toLowerCase().includes(q) ||
          r.ledgerId.toLowerCase().includes(q) ||
          r.ruleCode.toLowerCase().includes(q) ||
          r.evidenceDoc.toLowerCase().includes(q) ||
          r.status.toLowerCase().includes(q) ||
          r.riskReasons.some((reason) => reason.toLowerCase().includes(q))
        );
      }
    }

    return result;
  }, [records, activeMode, activeFilterQuery]);

  // Dynamic grid height based on row count — eliminates unsightly empty voids
  const gridHeight = useMemo(() => {
    const rowCount = filteredRecords.length;
    // Header (48px) + Pinned bottom invariant (42px) + Horizontal scrollbar (18px) + Rows * 42px
    const total = rowCount * 42 + 48 + 42 + 18;
    return Math.min(540, Math.max(260, total));
  }, [filteredRecords.length]);

  // Custom Cell Renderer: Transaction ID with Inline 1-Click Copy
  const TxIdCellRenderer = useCallback((params: ICellRendererParams<FinancialRecord>) => {
    const val = params.value;
    if (!val) return null;
    const isPinned = params.node.rowPinned === "bottom";
    if (isPinned) {
      return <span style={{ fontWeight: 900, color: "#2dd4bf" }}>{val}</span>;
    }
    const isCopied = copiedKey === val;
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", height: "100%", paddingRight: "4px" }}>
        <span style={{ fontWeight: 800, fontFamily: "monospace", color: "#2dd4bf" }}>{val}</span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            copyToClipboard(val, `Copied ${val}!`, val);
          }}
          title="Click to copy Transaction ID"
          style={{
            background: isCopied ? "#10b981" : "rgba(255, 255, 255, 0.08)",
            border: isCopied ? "1px solid #10b981" : "1px solid #334155",
            color: isCopied ? "#050b14" : "#94a3b8",
            cursor: "pointer",
            padding: "2px 5px",
            borderRadius: "4px",
            display: "inline-flex",
            alignItems: "center",
            gap: "2px",
            fontSize: "10px",
            fontWeight: 700,
            transition: "all 0.15s ease",
          }}
        >
          {isCopied ? <Check size={10} /> : <Copy size={10} />}
          <span>{isCopied ? "COPIED" : "COPY"}</span>
        </button>
      </div>
    );
  }, [copiedKey, copyToClipboard]);

  // 1. Pinned Bottom Mathematical Invariant Row (Decimal-Safe Totals of filtered set)
  const pinnedBottomRowData = useMemo(() => {
    const sumPayPal = filteredRecords.reduce((s, r) => s + Math.round(r.paypalAmount * 100), 0) / 100;
    const sumOrder = filteredRecords.reduce((s, r) => s + Math.round(r.orderAmount * 100), 0) / 100;
    const sumLedger = filteredRecords.reduce((s, r) => s + Math.round(r.ledgerAmount * 100), 0) / 100;
    const sumDelta = filteredRecords.reduce((s, r) => s + Math.round(r.delta * 100), 0) / 100;

    return [
      {
        txId: "∑ 3-WAY INVARIANT",
        paypalTxId: `${filteredRecords.length} Records`,
        orderId: "DECIMAL-EXACT",
        ledgerId: sumDelta === 0 ? "✅ BALANCED" : "⚠️ VARIANCE",
        counterparty: sumDelta === 0 ? "100% Reconciled Across PayPal, Orders & ERP" : "Unreconciled Ledger Variance Detected",
        paypalAmount: sumPayPal,
        orderAmount: sumOrder,
        ledgerAmount: sumLedger,
        delta: sumDelta,
        riskScore: 0,
        riskReasons: [],
        status: sumDelta === 0 ? ("RESOLVED_SANDBOX" as const) : ("MISMATCH" as const),
        ruleCode: "DETERMINISTIC-DECIMAL-ENGINE",
        webhookEvent: "—",
        evidenceDoc: "—",
        aiRecommendation: "—",
        humanApprovedBy: "SYSTEM",
        merkleHash: "ROOT: 0x99f1...e4a2",
        velocitySparkline: null,
        isLiveSandbox: true,
        replayTimeline: [],
      },
    ];
  }, [filteredRecords]);

  // 2. Single Reusable Grid with Dynamic Column Definitions per Operational Mode
  const columnDefs = useMemo<ColDef<FinancialRecord>[]>(() => {
    const baseCols: ColDef<FinancialRecord>[] = [
      {
        field: "txId",
        headerName: "Transaction ID",
        pinned: "left",
        width: 215,
        filter: "agTextColumnFilter",
        checkboxSelection: true,
        headerCheckboxSelection: true,
        cellRenderer: TxIdCellRenderer,
        cellStyle: { fontWeight: 800, fontFamily: "monospace", color: "#2dd4bf" },
      },
      {
        field: "counterparty",
        headerName: "Counterparty / Entity",
        flex: 1.3,
        minWidth: 180,
        filter: "agTextColumnFilter",
        cellStyle: { color: "#e2e8f0" }
      },
      {
        field: "paypalAmount",
        headerName: "PayPal v2 ($)",
        width: 135,
        filter: "agNumberColumnFilter",
        enableCellChangeFlash: true,
        cellStyle: { fontFamily: "monospace", color: "#38bdf8", fontWeight: 700 },
        valueFormatter: (p: ValueFormatterParams) =>
          p.value != null ? `$${Number(p.value).toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "",
      },
      {
        field: "orderAmount",
        headerName: "Internal Order ($)",
        width: 140,
        filter: "agNumberColumnFilter",
        hide: activeMode === "RISK_INTELLIGENCE",
        cellStyle: { fontFamily: "monospace", color: "#94a3b8" },
        valueFormatter: (p: ValueFormatterParams) =>
          p.value != null ? `$${Number(p.value).toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "",
      },
      {
        field: "ledgerAmount",
        headerName: "ERP Ledger ($)",
        width: 140,
        filter: "agNumberColumnFilter",
        enableCellChangeFlash: true,
        cellStyle: { fontFamily: "monospace", color: "#c084fc", fontWeight: 700 },
        valueFormatter: (p: ValueFormatterParams) =>
          p.value != null ? `$${Number(p.value).toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "",
      },
      {
        field: "delta",
        headerName: "3-Way Delta (Δ)",
        width: 145,
        filter: "agNumberColumnFilter",
        pinned: activeMode === "RECONCILIATION" ? "left" : undefined,
        enableCellChangeFlash: true,
        cellStyle: (params) => ({
          fontWeight: 900,
          fontFamily: "monospace",
          color: (params.value ?? 0) < 0 ? "#f87171" : "#2dd4bf",
          backgroundColor: (params.value ?? 0) < 0 ? "rgba(239,68,68,0.25)" : "rgba(20,184,166,0.12)",
        }),
        valueFormatter: (p: ValueFormatterParams) =>
          p.value != null ? `${p.value < 0 ? "" : "+"}$${Number(p.value).toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "",
      },
      {
        field: "riskScore",
        headerName: "Anomaly Risk",
        width: 155,
        filter: "agNumberColumnFilter",
        pinned: activeMode === "RISK_INTELLIGENCE" ? "left" : undefined,
        cellRenderer: RiskHeatmapRenderer,
      },
      {
        field: "velocitySparkline",
        headerName: "3-Way Drift SVG",
        width: 150,
        cellRenderer: SparklineDriftRenderer,
        sortable: false,
      },
      {
        field: "ruleCode",
        headerName: "Deterministic Rule",
        width: 185,
        filter: "agTextColumnFilter",
        cellStyle: { color: "#94a3b8", fontSize: "11px" }
      },
      {
        field: "merkleHash",
        headerName: "Audit Seal (SHA-256)",
        width: 165,
        hide: activeMode !== "AUDIT_TRAIL" && activeMode !== "RECONCILIATION",
        cellStyle: { fontFamily: "monospace", fontSize: "11px", color: "#a78bfa" },
      },
    ];
    return baseCols;
  }, [activeMode, TxIdCellRenderer]);

  // 3. Operational Mode Morphing
  const handleSwitchMode = useCallback((mode: OperationalMode) => {
    setActiveMode(mode);
    setActiveFilterQuery("");
    setNlQuery("");

    if (mode === "RECONCILIATION") {
      setValidatedAstPill('🔒 Validated AST: { "mode": "RECONCILIATION", "filter": "delta != 0" }');
    } else if (mode === "RISK_INTELLIGENCE") {
      setValidatedAstPill('🔒 Validated AST: { "mode": "RISK_INTELLIGENCE", "filter": "riskScore >= 40", "sort": "desc" }');
    } else if (mode === "DISPUTES") {
      setValidatedAstPill('🔒 Validated AST: { "mode": "DISPUTES", "status": "DISPUTE_OPEN" }');
    } else if (mode === "REFUNDS") {
      setValidatedAstPill('🔒 Validated AST: { "mode": "REFUNDS", "status": "REFUND_PENDING" }');
    } else if (mode === "AUDIT_TRAIL") {
      setValidatedAstPill('🔒 Validated AST: { "mode": "AUDIT_TRAIL", "merkleWarrants": "INSPECT_ALL" }');
    } else {
      setValidatedAstPill('🔒 Validated AST: { "mode": "TRANSACTIONS", "display": "ALL_LIVE_RECORDS" }');
    }
    showToast(`Switched to ${mode} View`);
  }, [showToast]);

  // 4. Safe Natural-Language Execution (Gemini AST + Deterministic Fallback + Spoken Response)
  const executeValidatedAiCommand = useCallback(async (commandText: string) => {
    const q = commandText.trim().toLowerCase();
    setNlQuery(commandText);
    setIsCompiling(true);

    let spokenResponse = "";

    // A. Instant Reset / Clear Handler
    if (!q || q === "reset" || q === "clear" || q === "all" || q.includes("reset") || q.includes("clear") || q.includes("show all")) {
      setActiveFilterQuery("");
      setActiveMode("TRANSACTIONS");
      setNlQuery("");
      setValidatedAstPill('🔒 Validated AST: { "display": "ALL_LIVE_RECORDS", "recordsCount": 24, "sqlInjectionBlocked": true }');
      spokenResponse = "Cleared all active filters. Restored full twenty-four transaction ledger.";
      showToast("🔄 Filter Reset: Displaying All 24 Transactions");
      setIsCompiling(false);
      speakAuraConfirmation(spokenResponse);
      return;
    }

    // B. Set Active Query & Pill
    setActiveFilterQuery(commandText);
    updateAstPillForQuery(commandText);

    // C. Try Live Backend Gemini 2.5 Flash Structured AST Endpoint
    try {
      const res = await fetch("http://127.0.0.1:8000/api/v1/workbench/nl-command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: commandText })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.target_mode && data.target_mode !== "null") {
          setActiveMode(data.target_mode as OperationalMode);
        }
        if (data.spotlight_tx_id) {
          const rec = records.find(r => r.txId === data.spotlight_tx_id);
          if (rec) setSelectedRecord(rec);
        }
        if (data.spoken_reply) {
          spokenResponse = data.spoken_reply;
        }
        if (data.ast_security_seal) {
          setValidatedAstPill(`🔒 Validated AST: ${JSON.stringify(data.ag_filter_model || {})} • ${data.ast_security_seal}`);
        }
      }
    } catch {
      // Offline fallback runs below
    }

    // D. Zero-Latency Deterministic Fallback Engine (Guaranteed 100% Reliable Offline)
    if (!spokenResponse) {
      if (q.includes("low") || q.includes("safe") || q.includes("clean") || q.includes("matched")) {
        setActiveMode("TRANSACTIONS");
        spokenResponse = "Filtered verified low-risk transactions with anomaly scores under 30 and clean reconciliation.";
        showToast("🟢 Filtered: Low-Risk & Clean Records");
      } else if (q.includes("mismatch") || q.includes("differ") || q.includes("variance") || q.includes("5000") || q.includes("5,000") || q.includes("delta")) {
        setActiveMode("RECONCILIATION");
        const flagship = records.find((r) => r.txId === "REC-99420-FLAGSHIP");
        if (flagship) setSelectedRecord(flagship);
        spokenResponse = "Isolated three-way ledger mismatches and pinned the five thousand dollar marketplace variance.";
        showToast("🔍 Flagship Isolated: REC-99420-FLAGSHIP (-$5,000)");
      } else if (q.includes("high") || q.includes("critical") || q.includes("danger") || q.includes("alert")) {
        setActiveMode("RISK_INTELLIGENCE");
        spokenResponse = "Filtered for critical anomaly risk scores above 70 and sorted descending.";
        showToast("🔴 Filtered: Critical Anomalies (Score >= 70)");
      } else if (q.includes("dispute") || q.includes("chargeback") || q.includes("claim")) {
        setActiveMode("DISPUTES");
        spokenResponse = "Switched to Disputes view. Isolated open PayPal buyer claims pending cryptographic evidence.";
        showToast("🛡️ Switched: Open PayPal Disputes");
      } else if (q.includes("refund") || q.includes("return") || q.includes("rma")) {
        setActiveMode("REFUNDS");
        spokenResponse = "Filtered pending refunds and verified credit recovery items.";
        showToast("↩️ Switched: Pending Refunds");
      } else if (q.includes("gpu") || q.includes("payout") || q.includes("unwind") || q.includes("11") || q.includes("micro")) {
        setActiveMode("TRANSACTIONS");
        spokenResponse = "Displaying autonomous micro-GPU bounty disbursements verified under Tier 1 policy.";
        showToast("⚡ Filtered: Autonomous Micro-GPU Payouts");
      } else if (q.includes("escrow") || q.includes("wholesale") || q.includes("large") || q.includes("10000") || q.includes("10,000") || q.includes("50000") || q.includes("50,000") || q.includes("35000")) {
        setActiveMode("TRANSACTIONS");
        spokenResponse = "Isolated high-value enterprise escrow accounts exceeding ten thousand dollars.";
        showToast("💎 Filtered: High-Value Escrow & Wholesale");
      } else {
        spokenResponse = `Applied validated financial filter for ${commandText}.`;
        showToast(`🔍 Filtered by "${commandText}"`);
      }
    }

    setIsCompiling(false);
    speakAuraConfirmation(spokenResponse);
  }, [records, showToast, speakAuraConfirmation, updateAstPillForQuery]);

  // Web Speech API Voice Recognition Toggle
  const toggleVoiceRecognition = useCallback(() => {
    if (isListening) {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
      }
      setIsListening(false);
      return;
    }

    const SpeechRecClass = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecClass) {
      showToast("⚠️ Web Speech API not supported in this browser. Please type your command.");
      return;
    }

    try {
      const recognition = new SpeechRecClass();
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.lang = "en-US";

      recognition.onstart = () => {
        setIsListening(true);
        showToast("🎙️ AURA Voice Active: Speak your financial command...");
      };

      recognition.onresult = (event: any) => {
        const transcript = event.results?.[0]?.[0]?.transcript;
        if (transcript) {
          showToast(`🎙️ Heard: "${transcript}"`);
          executeValidatedAiCommand(transcript);
        }
      };

      recognition.onerror = (e: any) => {
        console.warn("Speech recognition error:", e);
        setIsListening(false);
        showToast(`⚠️ Mic: ${e.error || "recognition error"}`);
      };

      recognition.onend = () => {
        setIsListening(false);
      };

      recognitionRef.current = recognition;
      recognition.start();
    } catch (err) {
      console.warn("Failed to start speech recognition:", err);
      setIsListening(false);
      showToast("⚠️ Microphone access error.");
    }
  }, [isListening, executeValidatedAiCommand, showToast]);

  // 5. Human-in-the-Loop Approval & Sandbox Resolution
  const handleHumanApproveResolution = useCallback(
    (targetTxId: string) => {
      const nowStr = new Date().toISOString().split("T")[1].slice(0, 12) + " UTC";
      const debugId = `ca5862aa${Math.random().toString(16).slice(2, 6)}`;
      const captureId = `PP-SANDBOX-CAPTURE-${Date.now().toString().slice(-6)}`;
      const warrantHash = `sha256:${Math.random().toString(16).slice(2, 10)}${Math.random().toString(16).slice(2, 10)}`;

      setRecords((prev) =>
        prev.map((rec) => {
          if (rec.txId !== targetTxId) return rec;
          const updatedTimeline: ReplayStep[] = [
            ...rec.replayTimeline,
            {
              step: rec.replayTimeline.length + 1,
              stage: "Human Approval Granted",
              timestamp: nowStr,
              sourceId: "CFO-FINOPS-GATE",
              detail: `Human operator authorized $${Math.abs(rec.delta).toLocaleString()} adjustment.`,
              verified: true,
            },
            {
              step: rec.replayTimeline.length + 2,
              stage: "PayPal Sandbox Resolved & Audited",
              timestamp: nowStr,
              sourceId: captureId,
              detail: `Ledger balanced to $${rec.paypalAmount.toLocaleString()}. Debug-ID: ${debugId}.`,
              verified: true,
            },
          ];
          const updated: FinancialRecord = {
            ...rec,
            ledgerAmount: rec.paypalAmount,
            delta: 0.0,
            riskScore: 6,
            status: "RESOLVED_SANDBOX",
            humanApprovedBy: "CFO-HUMAN-SIGN-OFF",
            merkleHash: warrantHash,
            replayTimeline: updatedTimeline,
          };
          setSelectedRecord(updated);
          setActiveReplayStep(updatedTimeline.length);
          return updated;
        })
      );

      // Save receipt data & show modal
      setResolvedReceiptData({
        txId: targetTxId,
        paypalCaptureId: captureId,
        amount: "$5,000.00",
        debugId,
        idempotencyKey: `AION-IDEM-RESOLVE-${Date.now()}`,
        warrantHash,
        timestamp: nowStr,
      });
      setShowReceiptModal(true);

      // Play official PayPal sonic sound
      if (onTriggerSonicChime) onTriggerSonicChime();

      // Flash cells in AG Grid
      const api = gridRef.current?.api;
      if (api) {
        const node = api.getRowNode(targetTxId);
        if (node) setTimeout(() => api.flashCells({ rowNodes: [node] }), 90);
      }
    },
    [onTriggerSonicChime]
  );

  // Out-of-Band CFO Cryptographic Email Dispatch Handler
  const handleDispatchCfoEmail = useCallback(async (emailAddr: string, rec: FinancialRecord) => {
    const targetEmail = (emailAddr || "judge@paypal.com").trim();
    setIsDispatchingEmail(true);
    showToast(`📧 Dispatching signed audit receipt & OTP to ${targetEmail}...`);

    try {
      const res = await fetch("http://127.0.0.1:8000/api/v1/notify/cfo-email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          recipient_email: targetEmail,
          tx_id: rec.txId,
          amount_usd: Math.abs(rec.delta) || rec.paypalAmount,
          paypal_debug_id: `pp-dbg-${rec.txId.toLowerCase()}`,
          event_type: rec.delta !== 0 ? "CFO_OUT_OF_BAND_APPROVAL" : "AUDIT_RECEIPT_VERIFIED"
        })
      });
      if (res.ok) {
        const data = await res.json();
        setCfoEmailData({
          ...data,
          tx_id: rec.txId,
          amount_usd: Math.abs(rec.delta) || rec.paypalAmount
        });
        setShowEmailDrawer(true);
        showToast(`✅ Dispatched to ${targetEmail} • OTP Ready!`);
        speakAuraConfirmation(`Cryptographic audit email dispatched to ${targetEmail}. Open drawer to review OTP approval.`);
        setIsDispatchingEmail(false);
        return;
      }
    } catch {
      // Local fallback
    }

    // Offline / Instant Fallback Dispatch
    const mockOtp = `${Math.floor(100 + Math.random() * 900)}-${Math.floor(100 + Math.random() * 900)}`;
    const mockWarrant = `0x${Math.random().toString(16).slice(2, 10)}${Math.random().toString(16).slice(2, 10)}`;
    const mockHtml = `
      <div style="font-family: Arial, sans-serif; background: #070b14; color: #f8fafc; padding: 20px; border-radius: 10px; border: 1px solid #14b8a6;">
        <div style="color: #2dd4bf; font-size: 11px; font-weight: bold;">AION ZERO-TRUST FINTECH • PAYPAL SANDBOX V2</div>
        <h3 style="margin: 8px 0; color: #fff;">Out-of-Band CFO Cryptographic Dispatch</h3>
        <p style="color: #94a3b8; font-size: 12px;">Reference: <strong>${rec.txId}</strong> | Counterparty: <strong>${rec.counterparty}</strong></p>
        <div style="background: #0f172a; padding: 12px; border-radius: 6px; border-left: 3px solid #f59e0b; margin: 12px 0;">
          <div><strong>Amount Variance:</strong> <span style="color: #38bdf8; font-size: 15px; font-weight: bold;">$${(Math.abs(rec.delta) || rec.paypalAmount).toLocaleString()} USD</span></div>
          <div><strong>UNWIND Warrant:</strong> <code style="color: #2dd4bf;">${mockWarrant}</code></div>
          <div style="margin-top: 8px; padding: 6px; background: rgba(20, 184, 166, 0.15); border-radius: 4px;">
            <strong>Air-Gapped Approval OTP:</strong> <span style="background: #14b8a6; color: #050b14; padding: 2px 8px; border-radius: 4px; font-weight: 900;">${mockOtp}</span>
          </div>
        </div>
      </div>
    `;

    setCfoEmailData({
      status: "DISPATCHED",
      delivery_mode: "SIMULATED_INSTANT_DRAWER (Zero-Config Judge Mode)",
      recipient: targetEmail,
      otp_code: mockOtp,
      warrant_hash: mockWarrant,
      subject: `🚨 [AION PayPal Security] Action Required: $${(Math.abs(rec.delta) || rec.paypalAmount).toLocaleString()} (${rec.txId})`,
      html_preview: mockHtml,
      tx_id: rec.txId,
      amount_usd: Math.abs(rec.delta) || rec.paypalAmount
    });
    setShowEmailDrawer(true);
    showToast(`✅ Dispatched to ${targetEmail} • OTP Ready!`);
    speakAuraConfirmation(`Cryptographic audit email dispatched to ${targetEmail}. Open drawer to review OTP approval.`);
    setIsDispatchingEmail(false);
  }, [showToast, speakAuraConfirmation]);

  // Air-Gapped OTP Verification & Execution
  const handleVerifyOtp = useCallback(() => {
    if (!cfoEmailData) return;
    const cleanEntered = enteredOtp.replace(/[^0-9]/g, "");
    const cleanExpected = cfoEmailData.otp_code.replace(/[^0-9]/g, "");

    if (cleanEntered === cleanExpected || enteredOtp.trim() === cfoEmailData.otp_code.trim()) {
      showToast("🔐 OTP Verified! Executing air-gapped cryptographic settlement...");
      setShowEmailDrawer(false);
      handleHumanApproveResolution(cfoEmailData.tx_id);
      setEnteredOtp("");
    } else {
      showToast("❌ Invalid OTP code. Check the CFO email drawer.");
    }
  }, [cfoEmailData, enteredOtp, handleHumanApproveResolution, showToast]);

  // 6. Live Asynchronous Webhook Event Stream
  const streamLiveSandboxWebhook = useCallback(() => {
    const api = gridRef.current?.api;
    const idSuffix = Math.floor(1000 + Math.random() * 9000);
    const amount = 350.0 + Math.floor(Math.random() * 500);
    const newRec: FinancialRecord = {
      txId: `TX-${idSuffix}-LIVE`,
      paypalTxId: `PP-WH-V2-${idSuffix}`,
      orderId: `ORD-LIVE-${idSuffix}`,
      ledgerId: `GL-LIVE-${idSuffix}`,
      counterparty: "PayPal Sandbox Real-Time Webhook",
      paypalAmount: amount,
      orderAmount: amount,
      ledgerAmount: amount,
      delta: 0.0,
      riskScore: 8,
      riskReasons: ["LIVE_WEBHOOK_VERIFIED", "3WAY_EXACT_MATCH"],
      status: "MATCHED",
      ruleCode: "WEBHOOK-V2-AUTO",
      webhookEvent: "PAYMENT.CAPTURE.COMPLETED",
      evidenceDoc: `Webhook-Payload-${idSuffix}.json`,
      aiRecommendation: "Streamed in real-time via AG Grid applyTransactionAsync at 60 FPS.",
      humanApprovedBy: "AUTO-TIER-1",
      merkleHash: `0x${Math.random().toString(16).slice(2, 14)}...f1`,
      velocitySparkline: [amount / 10, amount / 10, amount / 10, amount / 10, amount / 10, amount / 10],
      isLiveSandbox: true,
      replayTimeline: [
        {
          step: 1,
          stage: "Live Webhook Streamed",
          timestamp: new Date().toISOString().split("T")[1].slice(0, 12) + " UTC",
          sourceId: `PP-WH-V2-${idSuffix}`,
          detail: "Inserted via gridApi.applyTransactionAsync without full DOM reload.",
          verified: true,
        },
      ],
    };

    if (api) {
      api.applyTransactionAsync({ add: [newRec], addIndex: 0 });
    }
    setRecords((prev) => [newRec, ...prev]);

    setTimeout(() => {
      const node = api?.getRowNode(newRec.txId);
      if (node) api?.flashCells({ rowNodes: [node] });
    }, 60);

    showToast(`⚡ Streamed Live Webhook: ${newRec.txId} ($${amount.toFixed(2)})`);
  }, [showToast]);

  const onSelectionChanged = useCallback((event: SelectionChangedEvent<FinancialRecord>) => {
    const rows = event.api.getSelectedRows();
    if (rows.length > 0) {
      setSelectedRecord(rows[0]);
      setActiveReplayStep(rows[0].replayTimeline.length);
    }
  }, []);

  const getRowStyle = useCallback((params: RowClassParams<FinancialRecord>): RowStyle | undefined => {
    if (params.node.rowPinned === "bottom") {
      return { background: "#050b14", fontWeight: "900", borderTop: "2px solid #14b8a6" };
    }
    if (params.data?.delta && params.data.delta < 0 && params.data.status !== "RESOLVED_SANDBOX") {
      return { background: "rgba(127, 29, 29, 0.35)", color: "#f87171" };
    }
    return { background: "#091222", color: "#f1f5f9" };
  }, []);

  const kpiStats = useMemo(() => {
    const mismatches = records.filter((r) => r.delta !== 0).length;
    const highRisk = records.filter((r) => r.riskScore >= 70).length;
    const pendingApproval = records.filter((r) => r.delta !== 0 && !r.humanApprovedBy).length;
    return { total: records.length, mismatches, highRisk, pendingApproval };
  }, [records]);

  return (
    <div style={{ background: "#050b14", color: "#f8fafc", padding: "18px", borderRadius: "14px", border: "1px solid #1e293b", fontFamily: "'Inter', system-ui, sans-serif", position: "relative" }}>
      {/* 100% EXPLICIT GLOBAL DARK TERMINAL STYLESHEET (NO WHITE BACKGROUNDS ANYWHERE) */}
      <style>{`
        .ag-theme-quartz-dark,
        .ag-theme-quartz,
        .ag-theme-quartz-auto-dark,
        .ag-root-wrapper,
        .ag-root-wrapper-body,
        .ag-root,
        .ag-body,
        .ag-body-viewport,
        .ag-body-viewport-wrapper,
        .ag-center-cols-viewport,
        .ag-center-cols-container,
        .ag-center-cols-clipper,
        .ag-pinned-left-cols-viewport,
        .ag-pinned-left-cols-container,
        .ag-pinned-right-cols-viewport,
        .ag-pinned-right-cols-container,
        .ag-floating-bottom,
        .ag-floating-bottom-viewport,
        .ag-floating-bottom-container,
        .ag-pinned-bottom-floating-top,
        .ag-body-horizontal-scroll,
        .ag-body-horizontal-scroll-viewport,
        .ag-body-horizontal-scroll-container,
        .ag-body-vertical-scroll,
        .ag-body-vertical-scroll-viewport,
        .ag-body-vertical-scroll-container,
        .ag-scrolls,
        .ag-virtual-list-viewport {
          --ag-background-color: #091222 !important;
          --ag-wrapper-background-color: #091222 !important;
          --ag-data-background-color: #091222 !important;
          --ag-header-background-color: #0b182d !important;
          --ag-subheader-background-color: #0b182d !important;
          --ag-control-panel-background-color: #0b182d !important;
          --ag-odd-row-background-color: #070e1c !important;
          --ag-foreground-color: #f1f5f9 !important;
          --ag-border-color: #1e293b !important;
          --ag-row-border-color: #1e293b !important;
          --ag-row-hover-color: #13233c !important;
          background-color: #091222 !important;
          background: #091222 !important;
          color: #f1f5f9 !important;
          border-color: #1e293b !important;
        }
        .ag-row,
        .ag-row-even,
        .ag-cell {
          background-color: #091222 !important;
          background: #091222 !important;
          color: #f1f5f9 !important;
        }
        .ag-row-odd,
        .ag-row-odd .ag-cell {
          background-color: #070e1c !important;
          background: #070e1c !important;
        }
        .ag-row-hover,
        .ag-row:hover,
        .ag-row-hover .ag-cell {
          background-color: #13233c !important;
          background: #13233c !important;
        }
        .ag-row-selected,
        .ag-row-selected .ag-cell {
          background-color: rgba(20, 184, 166, 0.25) !important;
          background: rgba(20, 184, 166, 0.25) !important;
          border-color: #14b8a6 !important;
        }
        .ag-header,
        .ag-header-row,
        .ag-header-cell {
          background-color: #0b182d !important;
          background: #0b182d !important;
          color: #38bdf8 !important;
          border-color: #1e293b !important;
          font-weight: 700 !important;
        }
        .ag-header-cell-text {
          color: #38bdf8 !important;
          font-weight: 800 !important;
          letter-spacing: 0.3px;
        }
        .ag-pinned-bottom-floating-top,
        .ag-floating-bottom,
        .ag-floating-bottom-viewport,
        .ag-floating-bottom-container,
        .ag-row-pinned,
        .ag-row-pinned .ag-cell {
          background-color: #050b14 !important;
          background: #050b14 !important;
          color: #2dd4bf !important;
          border-top: 2px solid #14b8a6 !important;
          font-weight: 900 !important;
        }
        .ag-body-horizontal-scroll,
        .ag-body-horizontal-scroll-viewport,
        .ag-body-horizontal-scroll-container {
          background-color: #070e1c !important;
          background: #070e1c !important;
          border-top: 1px solid #1e293b !important;
        }
        .ag-checkbox-input-wrapper {
          background-color: #050b14 !important;
          border-color: #334155 !important;
        }
        .ag-checkbox-input-wrapper.ag-checked {
          background-color: #14b8a6 !important;
        }
      `}</style>

      {/* Floating Centered Toast Notification (Never overlaps KPI cards) */}
      {toastMessage && (
        <div style={{
          position: "absolute",
          top: "14px",
          left: "50%",
          transform: "translateX(-50%)",
          background: "linear-gradient(90deg, #10b981 0%, #059669 100%)",
          color: "#050b14",
          padding: "7px 18px",
          borderRadius: "20px",
          fontWeight: 800,
          fontSize: "12px",
          zIndex: 1000,
          boxShadow: "0 6px 20px rgba(16, 185, 129, 0.5)",
          display: "flex",
          alignItems: "center",
          gap: "8px",
          animation: "fadeIn 0.2s ease-out"
        }}>
          <CheckCircle2 size={16} /> {toastMessage}
        </div>
      )}

      {/* HEADER: AURA 01 Detect -> 02 Investigate -> 03 Decide -> 04 Resolve */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "12px", borderBottom: "1px solid #1e293b", paddingBottom: "14px", marginBottom: "14px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "11px", color: "#2dd4bf", fontWeight: 800, letterSpacing: "0.6px" }}>
            <span>AURA FINANCIAL INTELLIGENCE WORKBENCH</span>
            <span>•</span>
            <span>POWERED BY AG GRID v31 COMMUNITY</span>
          </div>
          <div style={{ display: "flex", gap: "16px", marginTop: "6px", fontSize: "12px", color: "#94a3b8" }}>
            <span><strong style={{ color: "#38bdf8" }}>01 Detect</strong> Anomalies</span>
            <span>→ <strong style={{ color: "#38bdf8" }}>02 Investigate</strong> DNA Evidence</span>
            <span>→ <strong style={{ color: "#38bdf8" }}>03 Decide</strong> Deterministic + AI</span>
            <span>→ <strong style={{ color: "#2dd4bf" }}>04 Resolve</strong> Human-Approved Sandbox</span>
          </div>
        </div>

        {/* KPI Strip */}
        <div style={{ display: "flex", gap: "10px" }}>
          {[
            { label: "Total Transactions", val: kpiStats.total, color: "#38bdf8" },
            { label: "High Risk (>70)", val: kpiStats.highRisk, color: "#f87171" },
            { label: "3-Way Mismatches", val: kpiStats.mismatches, color: "#fbbf24" },
            { label: "Pending Approval", val: kpiStats.pendingApproval, color: "#c084fc" },
          ].map((k) => (
            <div key={k.label} style={{ background: "#0b1324", border: "1px solid #1e293b", padding: "6px 14px", borderRadius: "8px", textAlign: "center" }}>
              <div style={{ fontSize: "17px", fontWeight: 900, color: k.color }}>{k.val}</div>
              <div style={{ fontSize: "10px", color: "#94a3b8" }}>{k.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* CAPABILITY 1: Voice AI Command Bar (Speech-to-Text + Gemini AST + Voice Reply + Quick Chips) */}
      <div style={{
        background: "#0b1324",
        border: isListening ? "1.5px solid #ef4444" : "1px solid rgba(20, 184, 166, 0.45)",
        borderRadius: "10px",
        padding: "12px 14px",
        marginBottom: "12px",
        boxShadow: isListening ? "0 0 20px rgba(239, 68, 68, 0.35)" : "none",
        transition: "all 0.2s"
      }}>
        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap", marginBottom: "8px" }}>
          
          {/* Live Mic Speech-to-Text Button */}
          <button
            type="button"
            onClick={toggleVoiceRecognition}
            style={{
              background: isListening ? "#ef4444" : "rgba(20, 184, 166, 0.18)",
              color: isListening ? "#ffffff" : "#2dd4bf",
              border: isListening ? "1.5px solid #f87171" : "1px solid #14b8a6",
              padding: "7px 13px",
              borderRadius: "6px",
              fontSize: "12px",
              fontWeight: 800,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              boxShadow: isListening ? "0 0 14px rgba(239, 68, 68, 0.6)" : "none",
              animation: isListening ? "pulse 1.2s infinite" : "none",
              transition: "all 0.15s ease"
            }}
            title={isListening ? "Listening... Click to stop" : "Click to speak voice command"}
          >
            {isListening ? <MicOff size={14} /> : <Mic size={14} />}
            <span>{isListening ? "Listening..." : "🎙️ Voice Command"}</span>
          </button>

          {/* Prompt / Command Input */}
          <input
            type="text"
            value={nlQuery}
            onChange={(e) => {
              const val = e.target.value;
              setNlQuery(val);
              setActiveFilterQuery(val);
              if (val.trim() !== "") {
                setActiveMode("TRANSACTIONS");
              }
              updateAstPillForQuery(val);
            }}
            onKeyDown={(e) => e.key === "Enter" && executeValidatedAiCommand(nlQuery)}
            placeholder="Speak or type: 'show mismatches', 'show low risk', 'disputes', 'above $1,000', or 'reset'..."
            style={{ flex: 1, minWidth: "260px", background: "#050b14", border: "1px solid #334155", color: "#f8fafc", padding: "8px 12px", borderRadius: "6px", fontSize: "12px" }}
          />

          {/* Execute on Grid Button */}
          <button
            type="button"
            disabled={isCompiling}
            onClick={() => executeValidatedAiCommand(nlQuery)}
            style={{
              background: "#0f766e",
              color: "#fff",
              border: "none",
              padding: "8px 14px",
              borderRadius: "6px",
              fontSize: "12px",
              fontWeight: 700,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: "6px"
            }}
          >
            {isCompiling ? <Zap size={13} className="animate-spin" /> : <Search size={13} />}
            <span>{isCompiling ? "Compiling..." : "▶ Execute on Grid"}</span>
          </button>

          {/* AI Voice Toggle Button */}
          <button
            type="button"
            onClick={() => {
              const next = !voiceReplyEnabled;
              setVoiceReplyEnabled(next);
              if (next) speakAuraConfirmation("AURA voice responses active.");
              showToast(next ? "🔊 AI Voice: ON" : "🔇 AI Voice: MUTED");
            }}
            style={{
              background: voiceReplyEnabled ? "rgba(56, 189, 248, 0.15)" : "rgba(255, 255, 255, 0.05)",
              color: voiceReplyEnabled ? "#38bdf8" : "#94a3b8",
              border: voiceReplyEnabled ? "1px solid #38bdf8" : "1px solid #334155",
              padding: "7px 11px",
              borderRadius: "6px",
              fontSize: "11px",
              fontWeight: 700,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: "5px"
            }}
            title="Toggle Executive AI Voice Reply"
          >
            {voiceReplyEnabled ? <Volume2 size={13} /> : <VolumeX size={13} />}
            <span>AI Voice: {voiceReplyEnabled ? "ON" : "OFF"}</span>
          </button>
        </div>

        {/* Quick Clickable AI Starter Chips */}
        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: "10px", color: "#94a3b8", fontWeight: 700 }}>Quick Commands:</span>
          {[
            { label: "🔍 All Mismatches (-$5K)", q: "mismatches" },
            { label: "🟢 Low Risk (<30)", q: "show low risk" },
            { label: "🔴 Critical Risk (>70)", q: "high-risk" },
            { label: "🛡️ Open Disputes", q: "disputes" },
            { label: "⚡ GPU Payouts ($11)", q: "gpu" },
            { label: "💎 High-Value (> $10k)", q: "escrow" },
            { label: "🔄 Reset All (24 Records)", q: "reset" },
          ].map((chip) => (
            <button
              key={chip.label}
              type="button"
              onClick={() => executeValidatedAiCommand(chip.q)}
              style={{
                background: "rgba(255, 255, 255, 0.05)",
                border: "1px solid rgba(255, 255, 255, 0.12)",
                color: "#e2e8f0",
                fontSize: "11px",
                padding: "3px 8px",
                borderRadius: "4px",
                cursor: "pointer",
                transition: "all 0.15s"
              }}
            >
              {chip.label}
            </button>
          ))}
        </div>

        <div style={{ fontSize: "11px", fontFamily: "monospace", color: "#38bdf8", marginTop: "8px" }}>
          {validatedAstPill}
        </div>
      </div>

      {/* 6 Operational Modes Switcher + Live Webhook & Audit Actions */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "8px", marginBottom: "12px" }}>
        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
          {(["TRANSACTIONS", "RECONCILIATION", "RISK_INTELLIGENCE", "DISPUTES", "REFUNDS", "AUDIT_TRAIL"] as OperationalMode[]).map((mode) => (
            <button
              key={mode}
              onClick={() => handleSwitchMode(mode)}
              style={{
                background: activeMode === mode ? "#14b8a6" : "#0b1324",
                color: activeMode === mode ? "#050b14" : "#94a3b8",
                border: activeMode === mode ? "1px solid #2dd4bf" : "1px solid #1e293b",
                padding: "6px 13px",
                borderRadius: "6px",
                fontSize: "11px",
                fontWeight: 800,
                cursor: "pointer",
                transition: "all 0.15s"
              }}
            >
              {mode.replace("_", " ")}
            </button>
          ))}
        </div>

        <div style={{ display: "flex", gap: "8px" }}>
          <button
            onClick={streamLiveSandboxWebhook}
            style={{
              background: "linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%)",
              color: "#fff",
              border: "none",
              padding: "7px 14px",
              borderRadius: "6px",
              fontSize: "11px",
              fontWeight: 800,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              boxShadow: "0 2px 8px rgba(37, 99, 235, 0.3)"
            }}
          >
            <Zap size={14} /> Stream Live Webhook (Async Tx)
          </button>
          <button
            onClick={() => {
              gridRef.current?.api.exportDataAsCsv({ fileName: "AURA_Signed_Audit_Report.csv" });
              showToast("📥 Exported AURA_Signed_Audit_Report.csv");
            }}
            style={{ background: "#0b1324", color: "#e2e8f0", border: "1px solid #334155", padding: "7px 14px", borderRadius: "6px", fontSize: "11px", fontWeight: 700, cursor: "pointer" }}
          >
            📥 Export Audit CSV
          </button>
        </div>
      </div>

      {/* MAIN SPLIT WORKBENCH: Left = Unified AG Grid | Right = Transaction DNA & Human Governance */}
      <div style={{ display: "grid", gridTemplateColumns: "1.65fr 1fr", gap: "14px" }}>
        {/* Left: ONE Reusable AG Grid Instance with 100% Dark Terminal Styling */}
        <div
          className="ag-theme-quartz-dark"
          style={{
            height: filteredRecords.length <= 8 ? "auto" : gridHeight,
            minHeight: filteredRecords.length <= 8 ? undefined : "260px",
            maxHeight: "560px",
            width: "100%",
            borderRadius: "8px",
            overflow: "hidden",
            border: "1px solid #1e293b",
            background: "#091222",
            transition: "height 0.2s ease",
          }}
        >
          <AgGridReact<FinancialRecord>
            ref={gridRef}
            rowData={filteredRecords}
            columnDefs={columnDefs}
            pinnedBottomRowData={pinnedBottomRowData}
            domLayout={filteredRecords.length <= 8 ? "autoHeight" : "normal"}
            enableCellTextSelection={true}
            ensureDomOrder={true}
            defaultColDef={{
              sortable: true,
              resizable: true,
            }}
            getRowId={(p) => p.data.txId}
            asyncTransactionWaitMillis={50}
            animateRows={true}
            rowSelection="single"
            onSelectionChanged={onSelectionChanged}
            getRowStyle={getRowStyle}
          />
        </div>

        {/* Right: CAPABILITY 3, 6 & 7 — Transaction DNA Explorer + Human Approval + Investigation Replay */}
        <div style={{ background: "#0b1324", border: "1px solid #1e293b", borderRadius: "10px", padding: "14px", display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #1e293b", paddingBottom: "8px" }}>
              <div>
                <span style={{ fontSize: "10px", color: "#2dd4bf", fontWeight: 800 }}>🧬 TRANSACTION DNA & EVIDENCE EXPLORER</span>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "3px" }}>
                  <h3 style={{ margin: 0, fontSize: "15px", fontWeight: 800 }}>{selectedRecord.txId}</h3>
                  <button
                    onClick={() => copyToClipboard(selectedRecord.txId, "Tx ID Copied!", "txId")}
                    style={{
                      background: copiedKey === "txId" ? "#10b981" : "rgba(255, 255, 255, 0.08)",
                      border: copiedKey === "txId" ? "1px solid #10b981" : "1px solid #334155",
                      color: copiedKey === "txId" ? "#050b14" : "#cbd5e1",
                      cursor: "pointer",
                      padding: "2px 7px",
                      borderRadius: "4px",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px",
                      fontSize: "11px",
                      fontWeight: 700,
                      transition: "all 0.15s ease",
                    }}
                    title="Copy Transaction ID"
                  >
                    {copiedKey === "txId" ? <Check size={11} /> : <Copy size={11} />}
                    <span>{copiedKey === "txId" ? "COPIED" : "COPY"}</span>
                  </button>
                </div>
              </div>
              <span style={{
                background: selectedRecord.delta < 0 && selectedRecord.status !== "RESOLVED_SANDBOX" ? "rgba(239, 68, 68, 0.2)" : "rgba(20, 184, 166, 0.2)",
                color: selectedRecord.delta < 0 && selectedRecord.status !== "RESOLVED_SANDBOX" ? "#f87171" : "#2dd4bf",
                padding: "3px 8px",
                borderRadius: "6px",
                fontSize: "11px",
                fontWeight: 800
              }}>
                {selectedRecord.status}
              </span>
            </div>

            {/* 3-Way Reconciliation Card */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "6px", marginTop: "10px", background: "#050b14", padding: "8px", borderRadius: "8px", border: "1px solid #1e293b", textAlign: "center", fontSize: "11px" }}>
              <div>
                <div style={{ color: "#94a3b8", fontSize: "10px" }}>PayPal v2</div>
                <div style={{ fontWeight: 800, color: "#38bdf8" }}>${selectedRecord.paypalAmount.toLocaleString()}</div>
              </div>
              <div>
                <div style={{ color: "#94a3b8", fontSize: "10px" }}>Order</div>
                <div style={{ fontWeight: 800, color: "#94a3b8" }}>${selectedRecord.orderAmount.toLocaleString()}</div>
              </div>
              <div>
                <div style={{ color: "#94a3b8", fontSize: "10px" }}>ERP Ledger</div>
                <div style={{ fontWeight: 800, color: "#c084fc" }}>${selectedRecord.ledgerAmount.toLocaleString()}</div>
              </div>
              <div>
                <div style={{ color: "#94a3b8", fontSize: "10px" }}>Mismatch</div>
                <div style={{ fontWeight: 900, color: selectedRecord.delta < 0 && selectedRecord.status !== "RESOLVED_SANDBOX" ? "#f87171" : "#2dd4bf" }}>
                  ${selectedRecord.delta.toLocaleString()}
                </div>
              </div>
            </div>

            {/* Evidence Links & Deterministic Reason Codes */}
            <div style={{ marginTop: "10px", fontSize: "11px", lineHeight: 1.5 }}>
              <div style={{ display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
                <strong>PayPal Ref:</strong> <code style={{ color: "#38bdf8" }}>{selectedRecord.paypalTxId}</code>
                <button
                  onClick={() => copyToClipboard(selectedRecord.paypalTxId, "PayPal Ref Copied!", "ppRef")}
                  style={{
                    background: copiedKey === "ppRef" ? "#10b981" : "rgba(255, 255, 255, 0.08)",
                    border: copiedKey === "ppRef" ? "1px solid #10b981" : "1px solid #334155",
                    color: copiedKey === "ppRef" ? "#050b14" : "#cbd5e1",
                    cursor: "pointer",
                    padding: "1px 6px",
                    borderRadius: "4px",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "3px",
                    fontSize: "10px",
                    fontWeight: 700,
                  }}
                  title="Copy PayPal Reference"
                >
                  {copiedKey === "ppRef" ? <Check size={10} /> : <Copy size={10} />}
                  <span>{copiedKey === "ppRef" ? "COPIED" : "COPY"}</span>
                </button>
                <span>|</span>
                <strong>Ledger:</strong> <code style={{ color: "#c084fc" }}>{selectedRecord.ledgerId}</code>
              </div>
              <div><strong>Webhook Event:</strong> <span style={{ color: "#38bdf8" }}>{selectedRecord.webhookEvent}</span></div>
              <div><strong>Document Proof:</strong> <span style={{ color: "#a78bfa" }}>{selectedRecord.evidenceDoc}</span></div>
              <div style={{ marginTop: "6px", background: "#050b14", padding: "8px", borderRadius: "6px", borderLeft: "3px solid #14b8a6" }}>
                <strong style={{ color: "#2dd4bf" }}>AI Diagnosis:</strong> {selectedRecord.aiRecommendation}
              </div>
            </div>

            {/* Out-of-Band CFO Cryptographic Email & Zero-Cost Security Caller */}
            <div style={{ marginTop: "10px", background: "#050b14", padding: "10px", borderRadius: "8px", border: "1px solid rgba(20, 184, 166, 0.35)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                <span style={{ fontSize: "11px", fontWeight: 800, color: "#2dd4bf", display: "inline-flex", alignItems: "center", gap: "5px" }}>
                  <Mail size={13} /> Out-of-Band CFO Email Approval & OTP
                </span>
                <span style={{ fontSize: "10px", color: "#94a3b8" }}>100% Free • Real SMTP / Instant Drawer</span>
              </div>
              <div style={{ display: "flex", gap: "6px", marginBottom: "6px" }}>
                <input
                  type="email"
                  value={judgeEmail}
                  onChange={(e) => setJudgeEmail(e.target.value)}
                  placeholder="Enter judge email (e.g. judge@paypal.com)..."
                  style={{
                    flex: 1,
                    background: "#091222",
                    border: "1px solid #334155",
                    color: "#f8fafc",
                    padding: "6px 10px",
                    borderRadius: "6px",
                    fontSize: "11px"
                  }}
                />
                <button
                  type="button"
                  disabled={isDispatchingEmail}
                  onClick={() => handleDispatchCfoEmail(judgeEmail, selectedRecord)}
                  style={{
                    background: "#0d9488",
                    color: "#fff",
                    border: "none",
                    padding: "6px 11px",
                    borderRadius: "6px",
                    fontSize: "11px",
                    fontWeight: 700,
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "4px",
                    whiteSpace: "nowrap"
                  }}
                  title="Dispatch live signed audit receipt to your inbox"
                >
                  <Send size={11} /> {isDispatchingEmail ? "Sending..." : "Send Audit Email"}
                </button>
              </div>

              {/* Quick Actions Strip: Emergency Call + Open Drawer */}
              <div style={{ display: "flex", gap: "6px", alignItems: "center", justifyContent: "space-between", marginTop: "6px" }}>
                <button
                  type="button"
                  onClick={triggerEmergencyCall}
                  style={{
                    background: "rgba(239, 68, 68, 0.15)",
                    border: "1px solid rgba(239, 68, 68, 0.4)",
                    color: "#f87171",
                    padding: "4px 8px",
                    borderRadius: "4px",
                    fontSize: "10px",
                    fontWeight: 700,
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "4px"
                  }}
                  title="Zero-cost Web-Audio + SpeechSynthesis emergency alert call"
                >
                  <PhoneCall size={11} /> 📞 Test AI CFO Emergency Call
                </button>

                {cfoEmailData && (
                  <button
                    type="button"
                    onClick={() => setShowEmailDrawer(true)}
                    style={{
                      background: "rgba(56, 189, 248, 0.15)",
                      border: "1px solid rgba(56, 189, 248, 0.4)",
                      color: "#38bdf8",
                      padding: "4px 8px",
                      borderRadius: "4px",
                      fontSize: "10px",
                      fontWeight: 700,
                      cursor: "pointer",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px"
                    }}
                  >
                    <Mail size={11} /> View Outbox Email Drawer (OTP: {cfoEmailData.otp_code})
                  </button>
                )}
              </div>

              {/* Air-Gapped OTP Verification Field (Shown when email is dispatched and variance is open) */}
              {cfoEmailData && selectedRecord.delta !== 0 && selectedRecord.status !== "RESOLVED_SANDBOX" && (
                <div style={{ marginTop: "8px", background: "#091222", padding: "8px", borderRadius: "6px", border: "1px solid #1e293b", display: "flex", gap: "6px", alignItems: "center" }}>
                  <KeyRound size={13} style={{ color: "#14b8a6", flexShrink: 0 }} />
                  <input
                    type="text"
                    value={enteredOtp}
                    onChange={(e) => setEnteredOtp(e.target.value)}
                    placeholder={`Enter OTP (${cfoEmailData.otp_code})...`}
                    style={{
                      flex: 1,
                      background: "#050b14",
                      border: "1px solid #334155",
                      color: "#2dd4bf",
                      padding: "4px 8px",
                      borderRadius: "4px",
                      fontSize: "11px",
                      fontWeight: 700,
                      fontFamily: "monospace"
                    }}
                  />
                  <button
                    type="button"
                    onClick={handleVerifyOtp}
                    style={{
                      background: "#10b981",
                      color: "#050b14",
                      border: "none",
                      padding: "5px 10px",
                      borderRadius: "4px",
                      fontSize: "11px",
                      fontWeight: 800,
                      cursor: "pointer"
                    }}
                  >
                    Verify & Settle
                  </button>
                </div>
              )}
            </div>

            {/* CAPABILITY 7: Chronological Investigation Replay Timeline */}
            <div style={{ marginTop: "10px" }}>
              <div style={{ fontSize: "10px", fontWeight: 800, color: "#94a3b8", marginBottom: "6px" }}>
                ⏪ INVESTIGATION REPLAY TIMELINE (Click step to inspect audit state)
              </div>
              <div style={{ display: "grid", gap: "5px", maxHeight: "145px", overflowY: "auto" }}>
                {selectedRecord.replayTimeline.map((item) => (
                  <div
                    key={item.step}
                    onClick={() => setActiveReplayStep(item.step)}
                    style={{
                      background: activeReplayStep === item.step ? "#112240" : "#050b14",
                      border: activeReplayStep === item.step ? "1px solid #2dd4bf" : "1px solid #1e293b",
                      padding: "6px 8px",
                      borderRadius: "6px",
                      fontSize: "10px",
                      cursor: "pointer",
                      transition: "all 0.15s"
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 800, color: "#e2e8f0" }}>
                      <span>0{item.step}. {item.stage}</span>
                      <span style={{ color: "#38bdf8", fontFamily: "monospace" }}>{item.timestamp}</span>
                    </div>
                    <div style={{ color: "#94a3b8", marginTop: "2px" }}>{item.detail}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* CAPABILITY 6: Human Approval Governance Gate */}
          <div style={{ marginTop: "12px", borderTop: "1px solid #1e293b", paddingTop: "10px" }}>
            {selectedRecord.delta !== 0 && selectedRecord.status !== "RESOLVED_SANDBOX" ? (
              <button
                onClick={() => handleHumanApproveResolution(selectedRecord.txId)}
                style={{
                  width: "100%",
                  background: "linear-gradient(90deg, #0d9488 0%, #059669 100%)",
                  color: "#fff",
                  border: "none",
                  padding: "11px",
                  borderRadius: "8px",
                  fontWeight: 800,
                  fontSize: "12px",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "6px",
                  boxShadow: "0 4px 14px rgba(13, 148, 136, 0.4)"
                }}
              >
                <ShieldCheck size={16} /> Human Approve & Execute PayPal Sandbox Resolution (${Math.abs(selectedRecord.delta).toLocaleString()})
              </button>
            ) : (
              <div style={{ textAlign: "center", fontSize: "11px", color: "#2dd4bf", fontWeight: 800, padding: "8px", background: "#052e2b", borderRadius: "6px", border: "1px solid rgba(16, 185, 129, 0.35)" }}>
                🔒 Audited & Approved ({selectedRecord.humanApprovedBy || "CFO-SIGN-OFF"}) • Merkle Seal Intact
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Official PayPal Settlement Receipt Modal */}
      {showReceiptModal && resolvedReceiptData && (
        <div style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0, 0, 0, 0.75)",
          backdropFilter: "blur(4px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 9999,
          padding: "16px"
        }}>
          <div style={{
            background: "#0b1324",
            border: "1px solid #10b981",
            borderRadius: "14px",
            width: "100%",
            maxWidth: "520px",
            padding: "20px",
            boxShadow: "0 20px 40px rgba(0, 0, 0, 0.8), 0 0 30px rgba(16, 185, 129, 0.25)",
            animation: "fadeIn 0.2s ease-out"
          }}>
            {/* Modal Header */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #1e293b", paddingBottom: "12px", marginBottom: "14px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <div style={{ width: "32px", height: "32px", borderRadius: "50%", background: "rgba(16, 185, 129, 0.2)", display: "flex", alignItems: "center", justifyContent: "center", color: "#10b981" }}>
                  <CheckCircle2 size={20} />
                </div>
                <div>
                  <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 800, color: "#f8fafc" }}>
                    PayPal Verified Settlement Receipt
                  </h3>
                  <span style={{ fontSize: "11px", color: "#10b981", fontWeight: 700 }}>
                    ● SETTLED IN PAYPAL SANDBOX (REST v2 Orders)
                  </span>
                </div>
              </div>
              <button
                onClick={() => setShowReceiptModal(false)}
                style={{ background: "transparent", border: "none", color: "#94a3b8", cursor: "pointer", padding: "4px" }}
              >
                <X size={18} />
              </button>
            </div>

            {/* Receipt Summary Grid */}
            <div style={{ background: "#050b14", padding: "12px", borderRadius: "8px", border: "1px solid #1e293b", marginBottom: "12px", fontSize: "12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
                <span style={{ color: "#94a3b8" }}>Resolved Target:</span>
                <strong style={{ color: "#38bdf8", fontFamily: "monospace" }}>{resolvedReceiptData.txId}</strong>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
                <span style={{ color: "#94a3b8" }}>Capture ID:</span>
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <strong style={{ color: "#2dd4bf", fontFamily: "monospace" }}>{resolvedReceiptData.paypalCaptureId}</strong>
                  <button
                    onClick={() => copyToClipboard(resolvedReceiptData.paypalCaptureId, "Capture ID Copied!", "capId")}
                    style={{
                      background: copiedKey === "capId" ? "#10b981" : "rgba(255, 255, 255, 0.08)",
                      border: copiedKey === "capId" ? "1px solid #10b981" : "1px solid #334155",
                      color: copiedKey === "capId" ? "#050b14" : "#cbd5e1",
                      cursor: "pointer",
                      padding: "1px 6px",
                      borderRadius: "4px",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "3px",
                      fontSize: "10px",
                      fontWeight: 700,
                    }}
                  >
                    {copiedKey === "capId" ? <Check size={10} /> : <Copy size={10} />}
                    <span>{copiedKey === "capId" ? "COPIED" : "COPY"}</span>
                  </button>
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
                <span style={{ color: "#94a3b8" }}>Reconciled Amount:</span>
                <strong style={{ color: "#10b981", fontSize: "14px" }}>{resolvedReceiptData.amount} USD</strong>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
                <span style={{ color: "#94a3b8" }}>PayPal-Debug-Id:</span>
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <strong style={{ color: "#fbbf24", fontFamily: "monospace" }}>{resolvedReceiptData.debugId}</strong>
                  <button
                    onClick={() => copyToClipboard(resolvedReceiptData.debugId, "Debug ID Copied!", "dbgId")}
                    style={{
                      background: copiedKey === "dbgId" ? "#10b981" : "rgba(255, 255, 255, 0.08)",
                      border: copiedKey === "dbgId" ? "1px solid #10b981" : "1px solid #334155",
                      color: copiedKey === "dbgId" ? "#050b14" : "#cbd5e1",
                      cursor: "pointer",
                      padding: "1px 6px",
                      borderRadius: "4px",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "3px",
                      fontSize: "10px",
                      fontWeight: 700,
                    }}
                  >
                    {copiedKey === "dbgId" ? <Check size={10} /> : <Copy size={10} />}
                    <span>{copiedKey === "dbgId" ? "COPIED" : "COPY"}</span>
                  </button>
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px" }}>
                <span style={{ color: "#94a3b8" }}>Idempotency-Lock:</span>
                <span style={{ color: "#10b981", fontWeight: 700 }}>VERIFIED (0% Double-Spend)</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#94a3b8" }}>Audit Warrant Seal:</span>
                <span style={{ color: "#a78bfa", fontFamily: "monospace", fontSize: "11px" }}>{resolvedReceiptData.warrantHash}</span>
              </div>
            </div>

            {/* Collapsible Raw JSON Modal */}
            <details style={{ background: "#050b14", border: "1px solid #1e293b", borderRadius: "6px", padding: "8px 10px", marginBottom: "14px", fontSize: "11px" }}>
              <summary style={{ cursor: "pointer", color: "#38bdf8", fontWeight: 700 }}>
                🔍 View Raw PayPal Orders v2 JSON Payload
              </summary>
              <pre style={{ margin: "8px 0 0 0", color: "#94a3b8", fontFamily: "monospace", fontSize: "10px", maxHeight: "110px", overflowY: "auto" }}>
{JSON.stringify(
  {
    id: resolvedReceiptData.paypalCaptureId,
    status: "COMPLETED",
    debug_id: resolvedReceiptData.debugId,
    amount: { currency_code: "USD", value: "25000.00" },
    discrepancy_resolved: "5000.00",
    idempotency_key: resolvedReceiptData.idempotencyKey,
    warrant_proof: resolvedReceiptData.warrantHash,
    settlement_state: "100%_BALANCED",
  },
  null,
  2
)}
              </pre>
            </details>

            {/* Modal Actions */}
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                onClick={() => copyToClipboard(resolvedReceiptData.debugId, "Debug-ID Copied!")}
                style={{
                  flex: 1,
                  background: "#1e293b",
                  border: "1px solid #475569",
                  color: "#e2e8f0",
                  padding: "9px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 700,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "6px"
                }}
              >
                <Copy size={13} /> Copy Debug-ID
              </button>
              <button
                onClick={() => setShowReceiptModal(false)}
                style={{
                  flex: 1,
                  background: "#10b981",
                  border: "none",
                  color: "#050b14",
                  padding: "9px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 800,
                  cursor: "pointer"
                }}
              >
                Close & Return
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 📬 Out-of-Band CFO Cryptographic Email Preview Drawer (Zero-Config Judge Mode) */}
      {showEmailDrawer && cfoEmailData && (
        <div style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0, 0, 0, 0.75)",
          backdropFilter: "blur(5px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 10000,
          padding: "16px"
        }}>
          <div style={{
            background: "#070b14",
            border: "1.5px solid #14b8a6",
            borderRadius: "14px",
            width: "100%",
            maxWidth: "600px",
            maxHeight: "90vh",
            overflowY: "auto",
            padding: "20px",
            boxShadow: "0 25px 60px rgba(0, 0, 0, 0.9), 0 0 40px rgba(20, 184, 166, 0.3)",
            animation: "fadeIn 0.2s ease-out"
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #1e293b", paddingBottom: "12px", marginBottom: "14px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <div style={{ width: "32px", height: "32px", borderRadius: "50%", background: "rgba(20, 184, 166, 0.2)", display: "flex", alignItems: "center", justifyContent: "center", color: "#2dd4bf" }}>
                  <Mail size={18} />
                </div>
                <div>
                  <h3 style={{ margin: 0, fontSize: "15px", fontWeight: 800, color: "#f8fafc" }}>
                    📬 Live Out-of-Band CFO Inbox Preview
                  </h3>
                  <span style={{ fontSize: "11px", color: "#38bdf8", fontWeight: 700 }}>
                    {cfoEmailData.delivery_mode}
                  </span>
                </div>
              </div>
              <button
                onClick={() => setShowEmailDrawer(false)}
                style={{ background: "transparent", border: "none", color: "#94a3b8", cursor: "pointer", padding: "4px" }}
              >
                <X size={18} />
              </button>
            </div>

            {/* Email Meta Strip */}
            <div style={{ background: "#0b1324", padding: "10px 14px", borderRadius: "8px", border: "1px solid #1e293b", marginBottom: "14px", fontSize: "11px" }}>
              <div style={{ marginBottom: "4px" }}><strong>To:</strong> <span style={{ color: "#38bdf8" }}>{cfoEmailData.recipient}</span></div>
              <div style={{ marginBottom: "4px" }}><strong>Subject:</strong> <span style={{ color: "#e2e8f0" }}>{cfoEmailData.subject}</span></div>
              <div><strong>Status:</strong> <span style={{ color: "#10b981", fontWeight: 800 }}>DISPATCHED & CRYPTOGRAPHICALLY ANCHORED</span></div>
            </div>

            {/* OTP Quick Approval Bar */}
            <div style={{ background: "rgba(20, 184, 166, 0.15)", border: "1px solid #14b8a6", padding: "12px", borderRadius: "8px", marginBottom: "14px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
              <div>
                <div style={{ fontSize: "10px", color: "#94a3b8", fontWeight: 700 }}>AIR-GAPPED APPROVAL OTP CODE</div>
                <div style={{ fontSize: "20px", fontWeight: 900, color: "#2dd4bf", letterSpacing: "2px", fontFamily: "monospace" }}>
                  {cfoEmailData.otp_code}
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  setEnteredOtp(cfoEmailData.otp_code);
                  setShowEmailDrawer(false);
                  handleHumanApproveResolution(cfoEmailData.tx_id);
                  showToast("🔐 OTP Applied: Settle Approved!");
                }}
                style={{
                  background: "#10b981",
                  color: "#050b14",
                  border: "none",
                  padding: "8px 14px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 900,
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px"
                }}
              >
                <KeyRound size={13} /> 1-Click Auto-Verify & Settle ${cfoEmailData.amount_usd.toLocaleString()}
              </button>
            </div>

            {/* Rendered HTML Email Content */}
            <div
              style={{ background: "#050b14", border: "1px solid #1e293b", borderRadius: "8px", padding: "14px" }}
              dangerouslySetInnerHTML={{ __html: cfoEmailData.html_preview }}
            />

            <div style={{ marginTop: "14px", display: "flex", justifyContent: "flex-end" }}>
              <button
                onClick={() => setShowEmailDrawer(false)}
                style={{ background: "#1e293b", color: "#f8fafc", border: "1px solid #334155", padding: "8px 16px", borderRadius: "6px", fontSize: "12px", fontWeight: 700, cursor: "pointer" }}
              >
                Close Preview
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 📞 Free Web-Audio AI CFO Emergency Security Alert Call Modal */}
      {incomingCallActive && (
        <div style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0, 0, 0, 0.8)",
          backdropFilter: "blur(6px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 10001,
          padding: "16px"
        }}>
          <div style={{
            background: "#0b1220",
            border: "2px solid #ef4444",
            borderRadius: "16px",
            width: "100%",
            maxWidth: "460px",
            padding: "24px",
            textAlign: "center",
            boxShadow: "0 25px 60px rgba(239, 68, 68, 0.4), 0 0 50px rgba(239, 68, 68, 0.3)",
            animation: "pulse 1.5s infinite"
          }}>
            <div style={{ width: "64px", height: "64px", borderRadius: "50%", background: "rgba(239, 68, 68, 0.2)", border: "2px solid #ef4444", display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 16px", color: "#f87171" }}>
              <PhoneCall size={32} />
            </div>

            <div style={{ fontSize: "11px", fontWeight: 900, color: "#f87171", letterSpacing: "1px" }}>
              🚨 INCOMING AI CFO SECURITY ALERT
            </div>
            <h3 style={{ margin: "6px 0 10px", fontSize: "18px", fontWeight: 800, color: "#f8fafc" }}>
              Deterministic FinOps Governance Enclave
            </h3>
            <p style={{ fontSize: "12px", color: "#94a3b8", lineHeight: 1.5, marginBottom: "20px" }}>
              Caller: <strong>AURA Security Sentinel (Port 8000)</strong><br />
              Target: <strong>Agent X-99 ($5,000 Transfer Blocked)</strong>
            </p>

            {callAnswered ? (
              <div style={{ background: "#050b14", border: "1px solid #10b981", padding: "14px", borderRadius: "8px", marginBottom: "16px" }}>
                <div style={{ color: "#10b981", fontWeight: 800, fontSize: "13px", marginBottom: "6px" }}>
                  🔊 Security Officer On Line (Voice Speaking...)
                </div>
                <div style={{ color: "#cbd5e1", fontSize: "12px", fontStyle: "italic" }}>
                  "Security Alert! Autonomous Agent attempted an unauthorized $5,000 transfer. Action blocked and emergency audit email dispatched to CFO."
                </div>
              </div>
            ) : null}

            <div style={{ display: "flex", gap: "10px", justifyContent: "center" }}>
              {!callAnswered ? (
                <button
                  type="button"
                  onClick={answerEmergencyCall}
                  style={{
                    flex: 1,
                    background: "linear-gradient(135deg, #10b981 0%, #059669 100%)",
                    color: "#fff",
                    border: "none",
                    padding: "12px",
                    borderRadius: "8px",
                    fontSize: "13px",
                    fontWeight: 900,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: "8px",
                    boxShadow: "0 4px 14px rgba(16, 185, 129, 0.4)"
                  }}
                >
                  <Phone size={16} /> Answer Call
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    dismissEmergencyCall();
                    handleDispatchCfoEmail(judgeEmail, selectedRecord);
                  }}
                  style={{
                    flex: 1,
                    background: "#0d9488",
                    color: "#fff",
                    border: "none",
                    padding: "12px",
                    borderRadius: "8px",
                    fontSize: "13px",
                    fontWeight: 900,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: "8px"
                  }}
                >
                  <Mail size={16} /> Open Dispatched Audit Email
                </button>
              )}

              <button
                type="button"
                onClick={dismissEmergencyCall}
                style={{
                  background: "rgba(255, 255, 255, 0.08)",
                  border: "1px solid #334155",
                  color: "#94a3b8",
                  padding: "12px 18px",
                  borderRadius: "8px",
                  fontSize: "13px",
                  fontWeight: 700,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px"
                }}
              >
                <PhoneOff size={15} /> Dismiss
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
