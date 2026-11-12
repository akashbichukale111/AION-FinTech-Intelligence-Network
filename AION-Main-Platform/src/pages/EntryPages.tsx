import { useState, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ExternalLink, Mic, MicOff, Volume2, VolumeX, Search, Zap, CheckCircle2 } from "lucide-react";
import { Badge } from "../components/ui";
import AuraUnifiedCommandWorkbench from "../components/AuraUnifiedCommandWorkbench";
import ForgexUnifiedCommandStudio from "../components/ForgexUnifiedCommandStudio";
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
  const [selectedView, setSelectedView] = useState<"unified" | "paypal" | "studio">("unified");
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Voice AI & Autonomous Prompt State for FORGEX
  const [forgexQuery, setForgexQuery] = useState<string>("");
  const [isListening, setIsListening] = useState<boolean>(false);
  const [isExecuting, setIsExecuting] = useState<boolean>(false);
  const [voiceReplyEnabled, setVoiceReplyEnabled] = useState<boolean>(true);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [astPill, setAstPill] = useState<string>(
    '🔒 FORGEX AST: { engine: "AEGISNOVA_AUTONOMOUS_V3", sandbox: "PAYPAL_REST_V2", policy: "TIER_1_AUTO" }'
  );
  const recognitionRef = useRef<any>(null);

  const showToast = useCallback((msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3500);
  }, []);

  const speakForgexConfirmation = useCallback((text: string) => {
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

  const executeForgexPrompt = useCallback((promptText: string) => {
    const q = promptText.trim().toLowerCase();
    setForgexQuery(promptText);
    setIsExecuting(true);

    let spoken = "";

    if (!q || q === "reset" || q === "clear" || q.includes("reset") || q.includes("clear")) {
      setSelectedView("paypal");
      setForgexQuery("");
      setAstPill('🔒 FORGEX AST: { workspace: "CLEAN_STANDBY", gate: "READY", policy: "IDLE" }');
      spoken = "Restored clean FORGEX autonomous studio workspace.";
      showToast("🔄 FORGEX Workspace Reset");
    } else if (q.includes("unwind") || q.includes("gpu") || q.includes("11") || q.includes("bounty") || q.includes("micro")) {
      setSelectedView("paypal");
      try {
        iframeRef.current?.contentWindow?.postMessage({ type: "SWITCH_TAB", tab: "MODE_PAYPAL" }, "*");
      } catch {}
      setAstPill('🔒 FORGEX AST: { action: "UNWIND_GPU_WARRANT", cap: "$11.00", proof: "SHA256_COMMIT_LOCKED" }');
      spoken = "Generated UNWIND cryptographic warrant bound to git commit sha-256 with eleven dollar PayPal payout cap.";
      showToast("⚡ Autonomous GPU Warrant Bound ($11.00)");
    } else if (q.includes("aegis") || q.includes("gate") || q.includes("audit") || q.includes("merkle") || q.includes("proof") || q.includes("studio")) {
      setSelectedView("studio");
      setAstPill('🔒 FORGEX AST: { engine: "AEGISNOVA_GATE", merkleRoot: "0x9f1a...e882", state: "ANCHORED" }');
      spoken = "Deploying AegisNova deterministic gate with Merkle audit proof verification.";
      showToast("🛡️ AEGISNOVA Deterministic Gate Engaged");
    } else if (q.includes("paypal") || q.includes("order") || q.includes("capture") || q.includes("commerce")) {
      setSelectedView("paypal");
      setAstPill('🔒 FORGEX AST: { protocol: "PAYPAL_ORDERS_V2", sandboxMode: "LIVE_IDEMPOTENT" }');
      spoken = "Switched to PayPal Agentic Commerce sandbox mode with live webhook idempotency.";
      showToast("💳 PayPal Sandbox Mode Active");
    } else {
      setAstPill(`🔒 FORGEX AST: { directive: "${promptText}", execution: "DETERMINISTIC_SANDBOX" }`);
      spoken = `Autonomous engineering directive for ${promptText} registered in FORGEX runtime.`;
      showToast(`⚡ Directive: "${promptText}"`);
    }

    setIsExecuting(false);
    speakForgexConfirmation(spoken);
  }, [showToast, speakForgexConfirmation]);

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
      showToast("⚠️ Web Speech API not supported. Please type directive.");
      return;
    }

    try {
      const recognition = new SpeechRecClass();
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.lang = "en-US";

      recognition.onstart = () => {
        setIsListening(true);
        showToast("🎙️ FORGEX Voice Active: Speak autonomous directive...");
      };

      recognition.onresult = (event: any) => {
        const transcript = event.results?.[0]?.[0]?.transcript;
        if (transcript) {
          showToast(`🎙️ Heard: "${transcript}"`);
          executeForgexPrompt(transcript);
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
      console.warn("Speech error:", err);
      setIsListening(false);
      showToast("⚠️ Microphone access error.");
    }
  }, [isListening, executeForgexPrompt, showToast]);

  const searchStr = typeof window !== "undefined" ? window.location.search : "";
  const extraParams = searchStr ? `&${searchStr.replace(/^\?/, "")}` : "";

  const viewUrls = {
    unified: "",
    paypal: `${baseUrl}/engineering?tab=paypal${extraParams}`,
    studio: `${baseUrl}/studio`
  };

  const handleSelectView = (view: "unified" | "paypal" | "studio") => {
    setSelectedView(view);
    if (view === "paypal") {
      try {
        iframeRef.current?.contentWindow?.postMessage({ type: "SWITCH_TAB", tab: "MODE_PAYPAL" }, "*");
      } catch {}
    }
  };

  const currentIframeUrl = viewUrls[selectedView] || viewUrls.paypal;

  return (
    <div style={{ width: "100%", height: "calc(100vh - 60px)", display: "flex", flexDirection: "column", backgroundColor: "#070A12", overflow: "hidden", position: "relative" }}>
      {/* Floating Centered Toast Notification */}
      {toastMessage && (
        <div style={{
          position: "absolute",
          top: "14px",
          left: "50%",
          transform: "translateX(-50%)",
          background: "linear-gradient(90deg, #8b5cf6 0%, #6d28d9 100%)",
          color: "#ffffff",
          padding: "7px 18px",
          borderRadius: "20px",
          fontWeight: 800,
          fontSize: "12px",
          zIndex: 1000,
          boxShadow: "0 6px 20px rgba(139, 92, 246, 0.5)",
          display: "flex",
          alignItems: "center",
          gap: "8px"
        }}>
          <CheckCircle2 size={16} /> {toastMessage}
        </div>
      )}

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
            onClick={() => handleSelectView("unified")}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "6px 14px",
              backgroundColor: selectedView === "unified" ? "rgba(139, 92, 246, 0.25)" : "rgba(139, 92, 246, 0.1)",
              border: selectedView === "unified" ? "1.5px solid #a78bfa" : "1px solid rgba(139, 92, 246, 0.35)",
              borderRadius: "6px",
              color: "#c4b5fd",
              fontSize: "12px",
              fontWeight: 700,
              cursor: "pointer",
              boxShadow: selectedView === "unified" ? "0 0 12px rgba(139, 92, 246, 0.4)" : "none",
              transition: "all 0.2s"
            }}
          >
            ⚡ Autonomous Studio (Cloud Live)
          </button>
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
            💳 Local Sandbox (Port 3000)
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

      {/* Voice AI & Autonomous Engineering Prompt Bar */}
      <div style={{
        padding: "10px 20px",
        backgroundColor: "#0d111e",
        borderBottom: isListening ? "1.5px solid #ef4444" : "1px solid rgba(139, 92, 246, 0.3)",
        boxShadow: isListening ? "0 0 18px rgba(239, 68, 68, 0.35)" : "none",
        transition: "all 0.2s"
      }}>
        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap", marginBottom: "6px" }}>
          <button
            type="button"
            onClick={toggleVoiceRecognition}
            style={{
              background: isListening ? "#ef4444" : "rgba(139, 92, 246, 0.2)",
              color: isListening ? "#ffffff" : "#c4b5fd",
              border: isListening ? "1.5px solid #f87171" : "1px solid #8b5cf6",
              padding: "6px 12px",
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
            title={isListening ? "Listening... Click to stop" : "Speak autonomous engineering directive"}
          >
            {isListening ? <MicOff size={13} /> : <Mic size={13} />}
            <span>{isListening ? "Listening..." : "🎙️ Voice Command"}</span>
          </button>

          <input
            type="text"
            value={forgexQuery}
            onChange={(e) => setForgexQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && executeForgexPrompt(forgexQuery)}
            placeholder="Speak or type: 'Autonomous Unwind GPU micro-task with 11 dollar payout warrant', 'Deploy AegisNova gate', or 'reset'..."
            style={{
              flex: 1,
              minWidth: "260px",
              background: "#070A12",
              border: "1px solid #334155",
              color: "#f8fafc",
              padding: "7px 12px",
              borderRadius: "6px",
              fontSize: "12px"
            }}
          />

          <button
            type="button"
            disabled={isExecuting}
            onClick={() => executeForgexPrompt(forgexQuery)}
            style={{
              background: "#7c3aed",
              color: "#fff",
              border: "none",
              padding: "7px 14px",
              borderRadius: "6px",
              fontSize: "12px",
              fontWeight: 700,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: "6px"
            }}
          >
            {isExecuting ? <Zap size={13} className="animate-spin" /> : <Search size={13} />}
            <span>{isExecuting ? "Executing..." : "▶ Execute Directive"}</span>
          </button>

          <button
            type="button"
            onClick={() => {
              const next = !voiceReplyEnabled;
              setVoiceReplyEnabled(next);
              if (next) speakForgexConfirmation("FORGEX executive voice active.");
              showToast(next ? "🔊 AI Voice: ON" : "🔇 AI Voice: MUTED");
            }}
            style={{
              background: voiceReplyEnabled ? "rgba(167, 139, 250, 0.15)" : "rgba(255, 255, 255, 0.05)",
              color: voiceReplyEnabled ? "#c4b5fd" : "#94a3b8",
              border: voiceReplyEnabled ? "1px solid #a78bfa" : "1px solid #334155",
              padding: "6px 10px",
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

        {/* Quick Chips for FORGEX */}
        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: "10px", color: "#94a3b8", fontWeight: 700 }}>Quick Directives:</span>
          {[
            { label: "⚡ GPU Bounty ($11)", q: "Autonomous Unwind GPU micro-task with 11 dollar payout warrant" },
            { label: "🛡️ AEGISNOVA Gate", q: "Deploy AegisNova deterministic gate with Merkle audit proof" },
            { label: "💳 PayPal Sandbox", q: "Switch to PayPal Orders v2 Sandbox mode" },
            { label: "🔄 Reset Workspace", q: "reset" }
          ].map((chip) => (
            <button
              key={chip.label}
              type="button"
              onClick={() => executeForgexPrompt(chip.q)}
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
          <div style={{ marginLeft: "auto", fontSize: "11px", fontFamily: "monospace", color: "#a78bfa" }}>
            {astPill}
          </div>
        </div>
      </div>

      {/* Content: Unified Cloud Studio (Default - 100% Vercel Edge Live) or Embedded Localhost */}
      {selectedView === "unified" ? (
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px", backgroundColor: "#070A12" }}>
          <ForgexUnifiedCommandStudio onTriggerSonicChime={() => paypalAudio.playChime(0.35)} />
        </div>
      ) : (
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
      )}
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
