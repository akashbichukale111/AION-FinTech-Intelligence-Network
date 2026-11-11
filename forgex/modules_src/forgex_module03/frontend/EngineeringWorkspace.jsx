import { useEffect, useState, useCallback } from "react";
const API = "/api/v1/engineering";
const j = async (url, opt) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opt });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d?.error?.message || r.statusText);
  return d;
};
const post = (u, b = {}) => j(u, { method: "POST", body: JSON.stringify(b) });
const col = { idle: "#64748b", running: "#3b82f6", done: "#22c55e", failed: "#ef4444", blocked: "#f59e0b" };
const sev = { critical: "#ef4444", high: "#f97316", medium: "#eab308", low: "#64748b" };

export default function EngineeringWorkspace() {
  const [list, setList] = useState([]);
  const [sel, setSel] = useState(null);
  const [ex, setEx] = useState(null);
  const [planId, setPlanId] = useState("");
  const [err, setErr] = useState("");
  const [file, setFile] = useState(null);

  const loadList = useCallback(() => j(API + "/executions").then(setList).catch(e => setErr(e.message)), []);
  const loadOne = useCallback(id => j(`${API}/executions/${id}`).then(d => { setEx(d); setFile(f => f ?? d.changes?.[0]?.file); }).catch(e => setErr(e.message)), []);
  useEffect(() => { loadList(); }, [loadList]);
  useEffect(() => {
    if (!sel) return;
    loadOne(sel);
    const t = setInterval(() => { loadOne(sel); loadList(); }, 1500);
    return () => clearInterval(t);
  }, [sel, loadOne, loadList]);

  const act = (path, body) => async () => {
    setErr("");
    try { await post(`${API}/executions/${sel}/${path}`, body); await loadOne(sel); loadList(); } catch (e) { setErr(e.message); }
  };
  const create = async () => {
    setErr("");
    try { const d = await post(API + "/executions", { plan_id: planId }); setSel(d.id); setFile(null); loadList(); } catch (e) { setErr(e.message); }
  };
  const ch = ex?.changes?.find(c => c.file === file);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", height: "100vh", background: "#0b1020", color: "#e2e8f0", fontFamily: "Inter,system-ui,sans-serif" }}>
      <aside style={{ borderRight: "1px solid #1e293b", padding: 16, overflow: "auto" }}>
        <h3 style={{ margin: 0 }}>FORGEX · Engineering</h3>
        <p style={{ fontSize: 12, color: "#94a3b8" }}>Executes approved Module 02 plans only</p>
        <input value={planId} onChange={e => setPlanId(e.target.value)} placeholder="Approved plan ID" style={inp} />
        <button onClick={create} disabled={!planId} style={btn("#3b82f6")}>Create execution</button>
        <h4>Executions</h4>
        {list.map(e => (
          <div key={e.id} onClick={() => { setSel(e.id); setFile(null); }} style={{ padding: 8, borderRadius: 8, cursor: "pointer", marginBottom: 6, background: sel === e.id ? "#1e293b" : "transparent" }}>
            <div style={{ fontSize: 13 }}>{e.id} · plan {e.plan_id}</div>
            <div style={{ fontSize: 11, color: "#94a3b8" }}>{e.status}</div>
          </div>
        ))}
      </aside>
      <main style={{ padding: 20, overflow: "auto" }}>
        {err && <div style={{ background: "#7f1d1d", padding: 10, borderRadius: 8, marginBottom: 12 }}>{err}</div>}
        {!ex ? <p style={{ color: "#94a3b8" }}>Select or create an execution.</p> : <>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
            <h2 style={{ margin: 0 }}>Execution {ex.id}</h2><span style={pill("#334155")}>{ex.status}</span>
            {ex.current_step && <span style={pill("#1d4ed8")}>step {ex.current_step}</span>}
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              {ex.status === "created" && <button style={btn("#22c55e")} onClick={act("start")}>Start</button>}
              {["created", "running"].includes(ex.status) && <button style={btn("#64748b")} onClick={act("cancel")}>Cancel</button>}
              {ex.status === "awaiting_approval" && <>
                <button style={btn("#22c55e")} onClick={act("approve", { approver: "akash" })}>Approve & apply</button>
                <button style={btn("#ef4444")} onClick={act("reject", { reason: "Rejected in UI", reviewer: "akash" })}>Reject</button></>}
              {ex.status === "approved" && <button style={btn("#f59e0b")} onClick={act("rollback", { requested_by: "akash" })}>Rollback</button>}
              {ex.changes?.length > 0 && <a style={{ ...btn("#334155"), textDecoration: "none" }} href={`${API}/executions/${ex.id}/diff?format=patch`} download={`${ex.id}.patch`}>Export patch</a>}
            </div>
          </div>
          {ex.error && <div style={{ background: "#7f1d1d", padding: 10, borderRadius: 8, marginBottom: 12 }}>{ex.error.code}: {ex.error.message}</div>}
          <Section title="Agents">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(210px,1fr))", gap: 10 }}>
              {Object.values(ex.agents).map(a => (
                <div key={a.role} style={card}><b style={{ fontSize: 13 }}>{a.role.replace(/_/g, " ")}</b>
                  <div style={{ fontSize: 11, color: "#94a3b8" }}>{a.description}</div>
                  <span style={pill(col[a.status])}>{a.status}</span> <span style={{ fontSize: 11, color: "#64748b" }}>{a.provider}</span></div>))}
            </div>
            {!ex.providers.llm.available && <p style={{ fontSize: 12, color: "#f59e0b" }}>LLM provider unavailable: {ex.providers.llm.reason}. Only deterministic plan operations run.</p>}
          </Section>
          <Section title="Timeline / logs">
            <div style={{ ...card, maxHeight: 180, overflow: "auto", fontFamily: "monospace", fontSize: 12 }}>
              {ex.audit.map((a, i) => <div key={i}>{new Date(a.ts * 1000).toLocaleTimeString()} [{a.agent}] {a.event} {a.detail}</div>)}
            </div>
          </Section>
          {ex.changes?.length > 0 && <Section title={`Diff (${ex.changes.length} files, sha ${ex.patch_sha256?.slice(0, 10)})`}>
            <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", gap: 10 }}>
              <div>{ex.changes.map(c => <div key={c.file} onClick={() => setFile(c.file)} style={{ ...card, cursor: "pointer", marginBottom: 6, borderColor: file === c.file ? "#3b82f6" : "#1e293b" }}>
                <div style={{ fontSize: 12 }}>{c.file}</div><div style={{ fontSize: 11, color: "#94a3b8" }}>{c.status} +{c.additions} −{c.deletions} · {c.agent} · step {c.step_id}</div></div>)}</div>
              <pre style={{ ...card, overflow: "auto", margin: 0, fontSize: 12 }}>
                {ch && <div style={{ color: "#94a3b8", marginBottom: 6 }}>{ch.explanation}</div>}
                {ch?.diff.split("\n").map((l, i) => <div key={i} style={{ color: l.startsWith("+") ? "#4ade80" : l.startsWith("-") ? "#f87171" : l.startsWith("@@") ? "#60a5fa" : "#cbd5e1" }}>{l}</div>)}
              </pre>
            </div>
          </Section>}
          {ex.tests && <Section title="Tests">
            <pre style={{ ...card, fontSize: 12, whiteSpace: "pre-wrap" }}><b style={{ color: ex.tests.passed ? "#4ade80" : "#f87171" }}>{ex.tests.passed ? "PASSED" : "FAILED"}</b> · {ex.tests.command}{"\n"}{ex.tests.output}</pre></Section>}
          {(ex.code_review?.length > 0 || ex.security_review?.length > 0 || ex.status === "awaiting_approval") && <Section title="Review findings">
            {[...ex.code_review.map(f => ({ ...f, kind: "code" })), ...ex.security_review.map(f => ({ ...f, kind: "security" }))].map((f, i) =>
              <div key={i} style={{ ...card, marginBottom: 6 }}><span style={pill(sev[f.severity])}>{f.severity}</span> <b>{f.kind}</b> · {f.file} — {f.msg}</div>)}
            {!ex.code_review.length && !ex.security_review.length && <p style={{ color: "#4ade80" }}>No findings.</p>}
            {ex.analysis?.injection_findings?.length > 0 && <p style={{ color: "#f59e0b" }}>{ex.analysis.injection_findings.length} prompt-injection-like strings found in repo and ignored.</p>}
          </Section>}
          {ex.limitations?.length > 0 && <Section title="Known limitations"><ul>{ex.limitations.map((l, i) => <li key={i} style={{ fontSize: 13 }}>{l}</li>)}</ul></Section>}
        </>}
      </main>
    </div>
  );
}
const Section = ({ title, children }) => <section style={{ marginBottom: 20 }}><h4 style={{ margin: "0 0 8px" }}>{title}</h4>{children}</section>;
const card = { background: "#111827", border: "1px solid #1e293b", borderRadius: 10, padding: 10 };
const inp = { width: "100%", padding: 8, borderRadius: 8, border: "1px solid #334155", background: "#0f172a", color: "#e2e8f0", boxSizing: "border-box", marginBottom: 8 };
const btn = bg => ({ background: bg, color: "#fff", border: 0, borderRadius: 8, padding: "8px 12px", cursor: "pointer", fontSize: 13 });
const pill = bg => ({ background: bg, color: "#fff", borderRadius: 999, padding: "2px 8px", fontSize: 11 });
