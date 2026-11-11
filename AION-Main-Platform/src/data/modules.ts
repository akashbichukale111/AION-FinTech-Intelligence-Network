import type { PlatformModule } from "../types";

export const AURA_MODULES: PlatformModule[] = [
  { n: 1, name: "Unified Financial Data Intelligence", desc: "Normalizes payment, ledger and bank data into one evidence-ready financial model.", owner: "AURA" },
  { n: 2, name: "Intelligent Payment Operations & Routing", desc: "Analyzes payment flows and recommends routing and retry decisions.", owner: "AURA" },
  { n: 3, name: "Autonomous Financial Reconciliation", desc: "Matches transactions across sources and explains every unresolved break.", owner: "AURA" },
  { n: 4, name: "Fraud, Scam & Risk Intelligence", desc: "Scores transactions and accounts for fraud, scam and abuse signals.", owner: "AURA" },
  { n: 5, name: "Cash Flow & Treasury Intelligence", desc: "Forecasts liquidity and surfaces treasury exposure early.", owner: "AURA" },
  { n: 6, name: "Dispute & Chargeback Resolution Intelligence", desc: "Assembles evidence and recommends responses for disputes and chargebacks.", owner: "AURA" },
  { n: 7, name: "Intelligent Refunds & Financial Recovery", desc: "Guides refund decisions and recovery of failed or disputed funds.", owner: "AURA" },
  { n: 8, name: "Credit, Repayment & Early Warning Intelligence", desc: "Tracks repayment behavior and flags credit risk before it escalates.", owner: "AURA" },
  { n: 9, name: "Agentic Decision & Governance Engine", desc: "Routes agent decisions through policy checks, approvals and audit trails.", owner: "AURA" },
  { n: 10, name: "Financial Copilot & Executive Intelligence", desc: "Answers financial questions with sourced evidence for operators and executives.", owner: "AURA" },
];

export const FORGEX_MODULES: PlatformModule[] = [
  { n: 1, name: "Repository & Architecture Intelligence Engine", desc: "Maps repositories, dependencies and architecture so changes start from facts.", owner: "FORGEX" },
  { n: 2, name: "Autonomous Engineering Planner", desc: "Turns an engineering goal into a reviewable, step-by-step change plan.", owner: "FORGEX" },
  { n: 3, name: "Multi-Agent Autonomous Coding & Refactoring Engine", desc: "Coordinates agents that implement and refactor code under controlled scope.", owner: "FORGEX" },
  { n: 4, name: "NightShift Autonomous Engineering Engine", desc: "Runs scheduled, governed repair work while the team is offline.", owner: "FORGEX" },
  { n: 5, name: "Testing & Regression Intelligence Engine", desc: "Generates and selects tests, and detects regressions before release.", owner: "FORGEX" },
  { n: 6, name: "FinTech Transaction Reliability Lab", desc: "Exercises payment flows for correctness, idempotency and failure handling.", owner: "FORGEX" },
  { n: 7, name: "API, Webhook & Payment Guardian", desc: "Monitors API contracts and webhook delivery for payment-critical paths.", owner: "FORGEX" },
  { n: 8, name: "Security & Supply Chain Intelligence Engine", desc: "Finds code and dependency risks and proposes verified fixes.", owner: "FORGEX" },
  { n: 9, name: "Production Incident & Observability Intelligence Engine", desc: "Correlates signals during incidents and points to likely root causes.", owner: "FORGEX" },
  { n: 10, name: "Release Risk, Governance & Assurance Engine", desc: "Scores release risk and requires evidence and sign-off before shipping.", owner: "FORGEX" },
];
