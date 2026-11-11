import { useState } from "react";
import { Volume2, Sparkles } from "lucide-react";

export function EcosystemVisual() {
  const [isPlaying, setIsPlaying] = useState(false);

  // Synthesize Official PayPal Sonic Brand Identity Mnemonic (Native Web Audio)
  const playPayPalSonicBrand = () => {
    try {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      if (ctx.state === "suspended") ctx.resume();
      const now = ctx.currentTime;
      setIsPlaying(true);

      // Official PayPal Sonic Identity Motif:
      // Note 1: G4 (392.00 Hz) - Transient Attack
      // Note 2: C5 (523.25 Hz) - Uplifting Leap
      // Note 3: E5 (659.25 Hz) - Harmonious Ascend
      // Note 4: G5 (783.99 Hz) & C6 (1046.50 Hz) - Signature Shimmer Resolve
      const notes = [
        { f: 392.00, t: 0.00, dur: 0.18, type: "sine" as OscillatorType, g: 0.22 },
        { f: 523.25, t: 0.12, dur: 0.22, type: "sine" as OscillatorType, g: 0.26 },
        { f: 659.25, t: 0.26, dur: 0.28, type: "triangle" as OscillatorType, g: 0.28 },
        { f: 783.99, t: 0.42, dur: 0.65, type: "sine" as OscillatorType, g: 0.30 },
        { f: 1046.50, t: 0.44, dur: 0.75, type: "sine" as OscillatorType, g: 0.18 }
      ];

      notes.forEach((n) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = n.type;
        osc.frequency.setValueAtTime(n.f, now + n.t);

        gain.gain.setValueAtTime(0.001, now + n.t);
        gain.gain.exponentialRampToValueAtTime(n.g, now + n.t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + n.t + n.dur);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.start(now + n.t);
        osc.stop(now + n.t + n.dur + 0.05);
      });

      setTimeout(() => {
        setIsPlaying(false);
      }, 1200);
    } catch (e) {
      setIsPlaying(false);
    }
  };

  return (
    <div style={{
      width: "100%",
      maxWidth: "480px",
      margin: "0 auto",
      background: "linear-gradient(145deg, #070d1e 0%, #0c142b 60%, #081a33 100%)",
      border: "1px solid rgba(0, 121, 193, 0.45)",
      boxShadow: "0 20px 50px rgba(0, 48, 135, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.1)",
      borderRadius: "20px",
      padding: "24px",
      position: "relative",
      overflow: "hidden"
    }}>
      {/* Ambient background glow */}
      <div style={{
        position: "absolute",
        top: "-60px",
        right: "-60px",
        width: "200px",
        height: "200px",
        background: "radial-gradient(circle, rgba(0, 121, 193, 0.35) 0%, transparent 70%)",
        pointerEvents: "none"
      }} />

      {/* Top Header: Official PayPal Dual-P Emblem & Status */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "20px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {/* Official PayPal Dual-P SVG */}
          <svg width="34" height="40" viewBox="0 0 34 40" fill="none">
            <path d="M26.2 8.7C25.5 3.7 21.2 0 16 0H4.2C2.9 0 1.8 1 1.6 2.3L0 12.5C-0.2 13.8 0.8 15 2.1 15H8.7L6.8 27.2C6.6 28.5 7.6 29.7 8.9 29.7H13.7L15.6 17.5C15.8 16.2 16.9 15.2 18.2 15.2H21.4C26.5 15.2 30.6 11.2 29.9 6.2L26.2 8.7Z" fill="#003087" opacity="0.85" />
            <path d="M30.4 12.8C29.6 7.8 25.3 4.1 20.1 4.1H8.3C7 4.1 5.9 5.1 5.7 6.4L1.8 31.4C1.6 32.7 2.6 33.9 3.9 33.9H9.7L11.6 21.7C11.8 20.4 12.9 19.4 14.2 19.4H17.4C22.5 19.4 26.6 15.4 25.9 10.4L30.4 12.8Z" fill="#0079C1" />
          </svg>
          <div>
            <div style={{ fontSize: "18px", fontWeight: 800, letterSpacing: "-0.5px", color: "#ffffff", display: "flex", alignItems: "center", gap: "6px" }}>
              <span>PayPal</span>
              <span style={{ fontSize: "11px", fontWeight: 700, color: "#38bdf8", background: "rgba(56, 189, 248, 0.15)", border: "1px solid rgba(56, 189, 248, 0.3)", padding: "1px 6px", borderRadius: "4px" }}>
                REST CORE
              </span>
            </div>
            <div style={{ fontSize: "11px", color: "#64748b", fontFamily: "monospace" }}>
              Orders v2 • Payouts v1 • Idempotent
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "6px", background: "rgba(34, 197, 94, 0.1)", border: "1px solid rgba(34, 197, 94, 0.3)", padding: "4px 8px", borderRadius: "12px" }}>
          <span style={{ width: "7px", height: "7px", borderRadius: "50%", background: "#22c55e", boxShadow: "0 0 8px #22c55e" }} />
          <span style={{ fontSize: "10px", fontWeight: 700, color: "#4ade80", fontFamily: "monospace" }}>OAuth 2.0 LIVE</span>
        </div>
      </div>

      {/* Center Interactive Player: PayPal Sonic Brand Identity */}
      <div style={{
        background: "rgba(5, 11, 24, 0.8)",
        border: "1px solid rgba(0, 121, 193, 0.3)",
        borderRadius: "14px",
        padding: "18px",
        marginBottom: "18px",
        textAlign: "center",
        position: "relative"
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", marginBottom: "8px" }}>
          <Sparkles size={15} color="#38bdf8" />
          <span style={{ fontSize: "11px", fontWeight: 800, textTransform: "uppercase", letterSpacing: "1px", color: "#38bdf8" }}>
            Official PayPal Sonic Brand Identity
          </span>
        </div>

        <p style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 14px 0", lineHeight: "1.4" }}>
          Experience the authentic acoustic identity designed for global checkout velocity and trust.
        </p>

        {/* Animated Sonic Soundwaves */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "5px", height: "30px", marginBottom: "14px" }}>
          {[16, 26, 18, 30, 22, 14, 28, 20].map((h, i) => (
            <div
              key={i}
              style={{
                width: "4px",
                height: isPlaying ? `${h}px` : "6px",
                backgroundColor: isPlaying ? "#38bdf8" : "#1e293b",
                borderRadius: "2px",
                transition: "all 0.15s ease",
                boxShadow: isPlaying ? "0 0 8px #38bdf8" : "none"
              }}
            />
          ))}
        </div>

        {/* Sonic Brand Play Button */}
        <button
          onClick={playPayPalSonicBrand}
          disabled={isPlaying}
          style={{
            background: isPlaying
              ? "linear-gradient(135deg, #0079C1, #00457C)"
              : "linear-gradient(135deg, #0079C1, #003087)",
            color: "#ffffff",
            border: "1px solid #38bdf8",
            borderRadius: "10px",
            padding: "10px 20px",
            fontSize: "12px",
            fontWeight: 700,
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: "8px",
            boxShadow: isPlaying ? "0 0 20px rgba(56, 189, 248, 0.6)" : "0 4px 15px rgba(0, 48, 135, 0.4)",
            transition: "all 0.2s ease"
          }}
        >
          <Volume2 size={16} />
          <span>{isPlaying ? "Playing PayPal Sonic Mnemonic..." : "Play PayPal Sonic Brand Sound"}</span>
        </button>
      </div>

      {/* Ecosystem Architecture Bridges: Connecting PayPal to AURA & FORGEX */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
        {/* Bridge to AURA */}
        <div style={{
          background: "rgba(11, 26, 46, 0.6)",
          border: "1px solid rgba(34, 211, 238, 0.3)",
          borderRadius: "10px",
          padding: "12px",
          display: "flex",
          flexDirection: "column",
          gap: "4px"
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span style={{ fontSize: "12px", fontWeight: 800, color: "#67e8f9" }}>AURA Rails</span>
            <span style={{ fontSize: "9px", color: "#10b981", fontWeight: 700 }}>Orders v2</span>
          </div>
          <p style={{ fontSize: "10.5px", color: "#94a3b8", margin: 0 }}>
            Automated 3-way reconciliation on AG Grid engine with live capture intake.
          </p>
        </div>

        {/* Bridge to FORGEX */}
        <div style={{
          background: "rgba(20, 18, 46, 0.6)",
          border: "1px solid rgba(167, 139, 250, 0.3)",
          borderRadius: "10px",
          padding: "12px",
          display: "flex",
          flexDirection: "column",
          gap: "4px"
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span style={{ fontSize: "12px", fontWeight: 800, color: "#c4b5fd" }}>FORGEX Rails</span>
            <span style={{ fontSize: "9px", color: "#a855f7", fontWeight: 700 }}>Payouts v1</span>
          </div>
          <p style={{ fontSize: "10.5px", color: "#94a3b8", margin: 0 }}>
            Autonomous agentic contractor payouts governed by SHA-256 warrant gates.
          </p>
        </div>
      </div>
    </div>
  );
}
