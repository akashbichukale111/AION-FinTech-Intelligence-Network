import { useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { Bell, ChevronDown, Menu, X, User, Settings, LogOut, CircleAlert } from "lucide-react";
import { Logo, useDismiss } from "./ui";
import { PAYPAL_LABEL, usePayPalStatus } from "../usePayPal";

const NAV = [
  { to: "/", label: "Home", end: true },
  { to: "/aura", label: "AURA" },
  { to: "/forgex", label: "FORGEX" },
  { to: "/docs", label: "Docs" },
  { to: "/apis", label: "APIs" },
  { to: "/reports", label: "Reports" },
];

export function Navbar() {
  const [menu, setMenu] = useState(false);
  const [bell, setBell] = useState(false);
  const [profile, setProfile] = useState(false);
  const { state } = usePayPalStatus();
  const needsAttention = state !== "connected" && state !== "checking";
  const bellRef = useDismiss<HTMLDivElement>(bell, () => setBell(false));
  const profRef = useDismiss<HTMLDivElement>(profile, () => setProfile(false));

  return (
    <header className="nav">
      <div className="nav-inner">
        <Link to="/" className="brand" onClick={() => setMenu(false)}>
          <Logo />
          <span className="brand-text">
            <strong>AION</strong>
            <small>Autonomous AI Operations Network</small>
          </span>
        </Link>

        <nav className="nav-links" aria-label="Primary">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>{n.label}</NavLink>
          ))}
        </nav>

        <div className="nav-actions">
          <div className="pop" ref={bellRef}>
            <button className="icon-btn" aria-label="Notifications" aria-expanded={bell} onClick={() => { setBell(!bell); setProfile(false); }}>
              <Bell size={18} />
              {needsAttention && <span className="dot" aria-hidden="true" />}
            </button>
            {bell && (
              <div className="pop-panel" role="region" aria-label="Notifications">
                <h3>Notifications</h3>
                {needsAttention ? (
                  <Link to="/#paypal" className="note" onClick={() => setBell(false)}>
                    <CircleAlert size={16} />
                    <span><b>PayPal Sandbox: {PAYPAL_LABEL[state].toLowerCase()}</b><small>Open the PayPal integration panel to see what is missing.</small></span>
                  </Link>
                ) : (
                  <p className="empty">You are all caught up. Integration alerts will appear here.</p>
                )}
              </div>
            )}
          </div>

          <div className="pop" ref={profRef}>
            <button className="avatar-btn" aria-label="Profile menu" aria-expanded={profile} onClick={() => { setProfile(!profile); setBell(false); }}>
              <span className="avatar">G</span><ChevronDown size={14} />
            </button>
            {profile && (
              <div className="pop-panel" role="menu">
                <div className="who"><b>Guest session</b><small>Sign-in is not configured for this build.</small></div>
                <button role="menuitem" disabled><User size={15} />Profile</button>
                <button role="menuitem" disabled><Settings size={15} />Preferences</button>
                <button role="menuitem" disabled><LogOut size={15} />Sign out</button>
              </div>
            )}
          </div>

          <button className="icon-btn menu-btn" aria-label={menu ? "Close menu" : "Open menu"} aria-expanded={menu} onClick={() => setMenu(!menu)}>
            {menu ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      {menu && (
        <nav className="mobile-nav" aria-label="Mobile">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} onClick={() => setMenu(false)} className={({ isActive }) => (isActive ? "active" : "")}>{n.label}</NavLink>
          ))}
        </nav>
      )}
    </header>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap footer-grid">
        <div>
          <Link to="/" className="brand"><Logo /><span className="brand-text"><strong>AION</strong></span></Link>
          <p className="muted">The parent portal for two independent workspaces: AURA for financial intelligence and FORGEX for autonomous engineering.</p>
        </div>
        <div><h4>Workspaces</h4><Link to="/aura">AURA</Link><Link to="/forgex">FORGEX</Link></div>
        <div><h4>Platform</h4><Link to="/#architecture">Platform architecture</Link><Link to="/docs">Documentation</Link><Link to="/apis">API resources</Link></div>
        <div><h4>Trust</h4><Link to="/docs#governance">Security and governance</Link><Link to="/reports">Reports</Link></div>
      </div>
      <div className="wrap footer-base">© {new Date().getFullYear()} AION. AURA and FORGEX are separate products with separate systems.</div>
    </footer>
  );
}
