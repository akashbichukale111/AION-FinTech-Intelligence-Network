import { useState, useRef } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { Badge } from "../components/ui";
import AuraUnifiedCommandWorkbench from "../components/AuraUnifiedCommandWorkbench";
import { paypalAudio } from "../utils/paypalAudio";

interface Check { label: string; value: string; ok: boolean }

function Status({ checks }: { checks: Check[] }) {
  return (
    <ul className="checks">
      {checks.map((c) => (
        <li key={c.label}><span>{c.label}</span><Badge tone={c.ok ? "ok" : "neutral"}>{c.value}</Badge></li>
      ))}
    </ul>
  );
}

interface EntryProps {
  tone: "aura" | "forgex"; name: string; fullName: string; blurb: string;
  url?: string; envVar: string; checks: Check[]; needs: string[]; roadmap?: string[];
  embedUrl?: string;
}

export function Entry({ tone, name, fullName, blurb, url, envVar, checks, needs, roadmap, embedUrl }: EntryProps) {
  return (
    <div className={`wrap page entry ws-${tone}`}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <Link to="/" className="back" style={{ margin: 0 }}><ArrowLeft size={15} /> Back to AION Portal</Link>
        {url && (
          <a className={`btn btn-${tone}`} href={url} target="_blank" rel="noopener noreferrer" style={{ fontSize: "13px", padding: "6px 14px" }}>
            Open in Standalone Window <ExternalLink size={14} />
          </a>
        )}
      </div>

      <Badge tone={tone}>{name} workspace</Badge>
      <h1>{name}</h1>
      <p className="ws-full">{fullName}</p>
      <p className="lead">{blurb}</p>

      {embedUrl && (
        <section className="panel" style={{ padding: "12px", marginTop: "20px", marginBottom: "30px", border: "1px solid rgba(56, 189, 248, 0.3)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", borderBottom: "1px solid rgba(255,255,255,0.08)", marginBottom: "12px" }}>
            <span style={{ fontWeight: 600, color: "#38bdf8", fontSize: "14px" }}>
              ⚡ AURA Live Embedded Financial Operating System (All 10 Modules)
            </span>
            <span style={{ fontSize: "12px", color: "#94a3b8" }}>Port 8000 Active</span>
          </div>
          <iframe
            src={embedUrl}
            title="AURA Financial Workspace"
            style={{
              width: "100%",
              height: "850px",
              border: "none",
              borderRadius: "8px",
              backgroundColor: "#030712"
            }}
          />
        </section>
      )}

      <div className="entry-grid">
        <section className="panel">
          <h2>Integration status</h2>
          <Status checks={checks} />
        </section>
        <section className="panel">
          <h2>Architecture & Verification</h2>
          <ol className="steps">
            {needs.map((n) => <li key={n}>{n}</li>)}
            <li>Environment Variable: <code>{envVar}</code></li>
          </ol>
        </section>
      </div>
      {roadmap && (
        <section className="panel" id="roadmap">
          <h2>Engineering roadmap</h2>
          <p className="muted">Delivered milestones across the unified platform.</p>
          <ol className="steps">{roadmap.map((r) => <li key={r}>{r}</li>)}</ol>
        </section>
      )}
    </div>
  );
}

export function AuraEntryPage() {
  const url = (import.meta.env.VITE_AURA_URL as string | undefined) || "http://127.0.0.1:8000";
  const [selectedView, setSelectedView] = useState<"unified" | "legacy">("unified");

  return (
    <div style={{ width: "100%", height: "calc(100vh - 60px)", display: "flex", flexDirection: "column", backgroundColor: "#030712", overflow: "hidden" }}>
      {/* Top Workspace Header */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 20px",
        backgroundColor: "#0b1220",
        borderBottom: "1px solid rgba(56, 189, 248, 0.2)",
        flexShrink: 0,
        zIndex: 40
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <Link
            to="/"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "8px",
              padding: "6px 14px",
              backgroundColor: "rgba(56, 189, 248, 0.1)",
              border: "1px solid rgba(56, 189, 248, 0.3)",
              borderRadius: "6px",
              color: "#38bdf8",
              fontSize: "13px",
              fontWeight: 600,
              textDecoration: "none"
            }}
          >
            <ArrowLeft size={15} /> Back to AION Portal
          </Link>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontWeight: 700, fontSize: "15px", color: "#f8fafc" }}>
              AURA Financial Intelligence Workbench
            </span>
            <Badge tone="aura">AG Grid Financial Workbench ($5,000 Track)</Badge>
            <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#10b981", fontWeight: 600 }}>
              <span style={{ width: "7px", height: "7px", borderRadius: "50%", backgroundColor: "#10b981" }}></span>
              PayPal Sandbox Connected
            </span>
          </div>
        </div>

        {/* Dual-Mode View Switcher & Standalone Tab */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <button
            type="button"
            onClick={() => setSelectedView("unified")}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "6px 14px",
              backgroundColor: selectedView === "unified" ? "rgba(20, 184, 166, 0.25)" : "rgba(20, 184, 166, 0.08)",
              border: selectedView === "unified" ? "1.5px solid #14b8a6" : "1px solid rgba(20, 184, 166, 0.3)",
              borderRadius: "6px",
              color: selectedView === "unified" ? "#2dd4bf" : "#94a3b8",
              fontSize: "12px",
              fontWeight: 700,
              cursor: "pointer",
              boxShadow: selectedView === "unified" ? "0 0 12px rgba(20, 184, 166, 0.35)" : "none",
              transition: "all 0.2s"
            }}
            title="Switch to Native 60FPS AG Grid v31 Command Center"
          >
            💎 Unified Command Center (AG Grid v31)
          </button>
          <button
            type="button"
            onClick={() => setSelectedView("legacy")}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "6px 12px",
              backgroundColor: selectedView === "legacy" ? "rgba(56, 189, 248, 0.25)" : "rgba(255, 255, 255, 0.05)",
              border: selectedView === "legacy" ? "1.5px solid #38bdf8" : "1px solid rgba(255, 255, 255, 0.1)",
              borderRadius: "6px",
              color: selectedView === "legacy" ? "#38bdf8" : "#94a3b8",
              fontSize: "12px",
              fontWeight: 600,
              cursor: "pointer",
              transition: "all 0.2s"
            }}
            title="Switch to Full 10-Module FastAPI Operating System"
          >
            🌐 Full OS (Port 8000)
          </button>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "6px 12px",
              backgroundColor: "rgba(255, 255, 255, 0.05)",
              border: "1px solid rgba(255, 255, 255, 0.1)",
              borderRadius: "6px",
              color: "#94a3b8",
              fontSize: "12px",
              textDecoration: "none"
            }}
          >
            Standalone Tab <ExternalLink size={13} />
          </a>
        </div>
      </div>

      {/* Content: Unified AG Grid v31 Workbench or Full Embedded OS */}
      {selectedView === "unified" ? (
        <div style={{ flex: 1, overflowY: "auto", padding: "16px", backgroundColor: "#030712" }}>
          <AuraUnifiedCommandWorkbench onTriggerSonicChime={() => paypalAudio.playChime(0.35)} />
        </div>
      ) : (
        <iframe
          src={url}
          title="AURA Financial Operating System"
          style={{
            width: "100%",
            height: "100%",
            flex: 1,
            border: "none",
            backgroundColor: "#030712"
          }}
        />
      )}
    </div>
  );
}

export function ForgexEntryPage() {
  const baseUrl = (import.meta.env.VITE_FORGEX_URL as string | undefined) || "http://localhost:3000";
  const [selectedView, setSelectedView] = useState<"paypal" | "studio">("paypal");
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const searchStr = typeof window !== "undefined" ? window.location.search : "";
  const extraParams = searchStr ? `&${searchStr.replace(/^\?/, "")}` : "";

  const viewUrls = {
    paypal: `${baseUrl}/engineering?tab=paypal${extraParams}`,
    studio: `${baseUrl}/studio`
  };

  const handleSelectView = (view: "paypal" | "studio") => {
    setSelectedView(view);
    if (view === "paypal") {
      try {
        iframeRef.current?.contentWindow?.postMessage({ type: "SWITCH_TAB", tab: "MODE_PAYPAL" }, "*");
      } catch {}
    }
  };

  const currentIframeUrl = viewUrls[selectedView];

  return (
    <div style={{ width: "100%", height: "calc(100vh - 60px)", display: "flex", flexDirection: "column", backgroundColor: "#070A12", overflow: "hidden" }}>
      {/* Top Workspace Header */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 20px",
        backgroundColor: "#070A12",
        borderBottom: "1px solid rgba(139, 92, 246, 0.3)",
        flexShrink: 0,
        zIndex: 40
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <Link
            to="/"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "8px",
              padding: "6px 14px",
              backgroundColor: "rgba(139, 92, 246, 0.12)",
              border: "1px solid rgba(139, 92, 246, 0.35)",
              borderRadius: "6px",
              color: "#a78bfa",
              fontSize: "13px",
              fontWeight: 600,
              textDecoration: "none"
            }}
          >
            <ArrowLeft size={15} /> Back to AION Portal
          </Link>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontWeight: 700, fontSize: "15px", color: "#f8fafc" }}>
              FORGEX Autonomous Engineering Platform
            </span>
            <Badge tone="forgex">Agentic Commerce & AEGISNOVA Studio</Badge>
            <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#10b981", fontWeight: 600 }}>
              <span style={{ width: "7px", height: "7px", borderRadius: "50%", backgroundColor: "#10b981" }}></span>
              PayPal Sandbox Live (Port 3000)
            </span>
          </div>
        </div>

        {/* Quick View Switcher & External Links */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <button
            type="button"
            onClick={() => handleSelectView("paypal")}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "6px 14px",
              backgroundColor: selectedView === "paypal" ? "rgba(245, 158, 11, 0.25)" : "rgba(245, 158, 11, 0.1)",
              border: selectedView === "paypal" ? "1.5px solid #f59e0b" : "1px solid rgba(245, 158, 11, 0.4)",
              borderRadius: "6px",
              color: "#fbbf24",
              fontSize: "12px",
              fontWeight: 700,
              cursor: "pointer",
              boxShadow: selectedView === "paypal" ? "0 0 12px rgba(245, 158, 11, 0.4)" : "none",
              transition: "all 0.2s"
            }}
          >
            💳 PayPal Agentic Commerce & Evidence Ledger (Live)
          </button>
          <button
            type="button"
            onClick={() => handleSelectView("studio")}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "6px 12px",
              backgroundColor: selectedView === "studio" ? "rgba(139, 92, 246, 0.25)" : "rgba(139, 92, 246, 0.12)",
              border: selectedView === "studio" ? "1.5px solid #a78bfa" : "1px solid rgba(139, 92, 246, 0.35)",
              borderRadius: "6px",
              color: "#c4b5fd",
              fontSize: "12px",
              fontWeight: 600,
              cursor: "pointer",
              transition: "all 0.2s"
            }}
          >
            ⚡ AEGISNOVA Autonomous Studio
          </button>
          <a
            href={currentIframeUrl}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "6px 12px",
              backgroundColor: "rgba(255, 255, 255, 0.05)",
              border: "1px solid rgba(255, 255, 255, 0.1)",
              borderRadius: "6px",
              color: "#94a3b8",
              fontSize: "12px",
              textDecoration: "none"
            }}
          >
            Standalone Window <ExternalLink size={13} />
          </a>
        </div>
      </div>

      {/* Full-Suite Live Embedded FORGEX Operating System */}
      <iframe
        ref={iframeRef}
        src={currentIframeUrl}
        title="FORGEX Autonomous Engineering & FinTech Reliability Platform"
        allow="payment; camera; microphone; clipboard-read; clipboard-write; display-capture"
        style={{
          width: "100%",
          height: "100%",
          flex: 1,
          border: "none",
          backgroundColor: "#070A12"
        }}
      />
    </div>
  );
}

interface InfoProps { title: string; intro: string; sections: { id?: string; h: string; p: string }[] }

export function InfoPage({ title, intro, sections }: InfoProps) {
  return (
    <div className="wrap page">
      <Link to="/" className="back"><ArrowLeft size={15} /> Back to AION</Link>
      <Badge tone="warn">Coming soon</Badge>
      <h1>{title}</h1>
      <p className="lead">{intro}</p>
      <div className="entry-grid">
        {sections.map((s) => <section key={s.h} id={s.id} className="panel"><h2>{s.h}</h2><p className="muted">{s.p}</p></section>)}
      </div>
    </div>
  );
}
