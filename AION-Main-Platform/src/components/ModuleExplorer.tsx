import { useState } from "react";
import { Modal } from "./ui";
import { AURA_MODULES, FORGEX_MODULES } from "../data/modules";
import type { Owner, PlatformModule } from "../types";

const LISTS: Record<Owner, PlatformModule[]> = { AURA: AURA_MODULES, FORGEX: FORGEX_MODULES };

export function ModuleExplorer({ initial, onClose }: { initial: Owner; onClose: () => void }) {
  const [tab, setTab] = useState<Owner>(initial);
  const [sel, setSel] = useState<PlatformModule | null>(null);
  const tone = tab === "AURA" ? "aura" : "forgex";
  const switchTab = (t: Owner) => { setTab(t); setSel(null); };

  return (
    <Modal title="Module explorer" onClose={onClose} wide>
      <div className="tabs" role="tablist">
        {(["AURA", "FORGEX"] as Owner[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab tab-${t.toLowerCase()} ${tab === t ? "on" : ""}`} onClick={() => switchTab(t)}>
            {t} <span>{LISTS[t].length} modules</span>
          </button>
        ))}
      </div>
      <div className="explorer">
        <ul className="mod-list" role="tabpanel">
          {LISTS[tab].map((m) => (
            <li key={m.n}>
              <button className={`mod-row ${sel?.n === m.n ? "on" : ""} ${tone}`} onClick={() => setSel(m)} aria-pressed={sel?.n === m.n}>
                <span className="mod-n">{m.n}</span>
                <span><b>{m.name}</b><small>{m.desc}</small></span>
              </button>
            </li>
          ))}
        </ul>
        <aside className={`mod-detail ${tone}`} aria-live="polite">
          {sel ? (
            <>
              <span className="mod-n big">{sel.n}</span>
              <h3>{sel.name}</h3>
              <p>{sel.desc}</p>
              <dl>
                <dt>Owned by</dt><dd>{sel.owner} workspace</dd>
                <dt>Availability</dt><dd>Defined in the platform plan. It becomes usable once the {sel.owner} workspace application is connected.</dd>
              </dl>
            </>
          ) : (
            <p className="empty">Select a module to see what it covers and which workspace owns it.</p>
          )}
        </aside>
      </div>
    </Modal>
  );
}
