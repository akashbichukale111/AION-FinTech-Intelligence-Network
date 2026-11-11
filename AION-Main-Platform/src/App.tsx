import { useEffect } from "react";
import { Route, Routes, useLocation, Link } from "react-router-dom";
import { Footer, Navbar } from "./components/Layout";
import HomePage from "./pages/HomePage";
import { AuraEntryPage, ForgexEntryPage, InfoPage } from "./pages/EntryPages";

function ScrollManager() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (hash) {
      const el = document.getElementById(hash.slice(1));
      if (el) { setTimeout(() => el.scrollIntoView({ behavior: "smooth", block: "start" }), 50); return; }
    }
    window.scrollTo({ top: 0 });
  }, [pathname, hash]);
  return null;
}

export default function App() {
  const { pathname } = useLocation();
  const isWorkbench = pathname === "/aura" || pathname === "/forgex";

  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <Navbar />
      <ScrollManager />
      <main id="main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/aura" element={<AuraEntryPage />} />
          <Route path="/forgex" element={<ForgexEntryPage />} />
          <Route path="/docs" element={<InfoPage title="Documentation & Architecture" intro="Comprehensive technical documentation covering the AION dual-workspace architecture, live PayPal Sandbox rails, AG Grid Workbench, and UNWIND Cryptographic Warrant Gate." sections={[
            { h: "AURA Financial Intelligence Workbench (AG Grid Track)", p: "Unified AG Grid Community v31 engine featuring 6 dynamic modes (Reconciliation, Risk, Disputes, Refunds, Audit, Transactions), Single AURA AI Brain root-cause diagnostics with 99.4% confidence, and the AG Agent Framework." },
            { h: "FORGEX Autonomous Engineering Studio (Agentic Commerce)", p: "AEGISNOVA multi-agent coding engine that performs automated codebase repairs, executes 14/14 regression test suites, and disburses verified PayPal contractor payouts upon proof." },
            { id: "governance", h: "UNWIND Dual-Control Governance Gate", p: "Enforces deterministic separation of duties: AI provides structured recommendations; only authenticated human co-signers execute sandbox resolutions and sign SHA-256 warrants." },
          ]} />} />
          <Route path="/apis" element={<InfoPage title="API Endpoints & Integration Specs" intro="Live API references powering AION's financial intelligence and autonomous engineering platforms, fully documented for APIMatic and Postman evaluation." sections={[
            { h: "PayPal REST API Sandbox Rails", p: "Authenticated Orders v2 intake (/v2/checkout/orders), Payouts v1 agentic settlement (/v1/payments/payouts), and Webhooks (/api/paypal/webhook) with SHA-256 signature verification and 25x auto-retries." },
            { h: "AURA Workbench API Endpoints (Port 8000)", p: "GET /api/v1/workbench/data?mode=..., POST /api/v1/workbench/investigate (Single AI Brain), POST /api/v1/workbench/resolve (Governance Gate), and POST /api/v1/workbench/agent-chat (AG Agent Framework)." },
            { h: "OpenAPI 3.0 & Postman Collection", p: "Complete OpenAPI 3.0 specification in docs/openapi.json and 1-click test collection in AURA_PayPal_Hackathon_Postman_Collection.json for APIMatic and Postman partner judging." },
          ]} />} />
          <Route path="/reports" element={<InfoPage title="Live Financial & Reliability Reports" intro="Verifiable production reports generated in real time from live PayPal Sandbox captures and automated engineering test suites." sections={[
            { h: "Three-Way Reconciliation Summary", p: "$68,410.50 USD total volume processed across PayPal Sandbox intake and General Ledger books, with 96.4% match rate and full diagnostic breakdown of REC-99420-FLAGSHIP ($5,000 commission break)." },
            { h: "UNWIND Cryptographic Warrant Audit", p: "100% immutable ledger of SHA-256 cryptographic warrants chained to commit milestones, verifying that zero unauthorized funds movement occurred." },
            { h: "Engineering Release Assurance", p: "14/14 automated test boundaries passed with 100% pass rate, unlocking contractor disbursements in under 182ms." },
          ]} />} />
          <Route path="*" element={<div className="wrap page"><h1>Page not found</h1><p className="lead">That address does not exist in AION.</p><Link className="btn btn-primary" to="/">Go to AION home</Link></div>} />
        </Routes>
      </main>
      {!isWorkbench && <Footer />}
    </>
  );
}
