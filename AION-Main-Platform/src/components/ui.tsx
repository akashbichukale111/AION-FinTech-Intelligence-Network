import { useEffect, useRef, type ReactNode } from "react";

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="aion-g" x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="#7c8cff" /><stop offset="1" stopColor="#9b6bff" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="#0d1330" />
      <path d="M16 5l9 5v12l-9 5-9-5V10z" fill="none" stroke="url(#aion-g)" strokeWidth="2" />
      <path d="M11.5 20.5L16 11l4.5 9.5M13 17.5h6" fill="none" stroke="#e6e9ff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "aura" | "forgex" | "neutral" | "warn" | "ok" | "err"; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function useDismiss<T extends HTMLElement>(open: boolean, close: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) close(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open, close]);
  return ref;
}

export function Modal({ title, onClose, wide, children }: { title: string; onClose: () => void; wide?: boolean; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; };
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close dialog" autoFocus>×</button>
        </div>
        {children}
      </div>
    </div>
  );
}
