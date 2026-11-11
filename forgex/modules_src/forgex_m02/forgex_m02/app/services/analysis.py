from ..templates_fintech import match_templates
from ..llm import get_provider, sanitize
from ..util import iso, utcnow
from . import context, rca, impact, risk

def run(task, scan):
    ev = context.Evidence(task.repo_id, scan["scan_id"])
    text, logs = f"{task.title}\n{task.description}", task.logs or ""
    ev.add("task_text", task.id, "Task title/description supplied by the user")
    if logs: ev.add("log", task.id, "Logs supplied with the task")
    tpls = match_templates(text, logs)
    signals = {s for t in tpls for s in t["signals"]}
    ctx = context.retrieve(scan, text, logs, signals, ev)
    hyps = rca.build(task.type, text, logs, tpls, scan, ctx, ev)
    imp = impact.analyze(task.type, scan, ctx, tpls, ev)
    rk = risk.assess(task.type, imp, tpls)
    fresh = context.freshness(scan)
    facts = [{"id": f"F{i+1}", "statement": f"{r['path']} is relevant ({'; '.join(r['reasons'])}).", "evidence_id": r["evidence_id"]}
             for i, r in enumerate(ctx["relevant"])]
    facts += [{"id": f"F{len(facts)+i+1}", "statement": f"{d['path']} depends on {', '.join(d['depends_on'])}.", "evidence_id": d["evidence_id"]}
              for i, d in enumerate(ctx["dependents"])]
    provider = get_provider()
    llm = sanitize(provider.propose_hypotheses({"title": task.title, "description": task.description, "type": task.type}, facts),
                   {e["id"] for e in ev.items})
    for i, h in enumerate(llm): h["id"] = f"llm.{i+1}"
    missing = []
    if not ctx["relevant"]: missing.append("No files in the Module 01 scan matched this task; add file paths, symbols or logs to the description.")
    if not ctx["tests"]: missing.append("No existing tests linked to the relevant files.")
    if not logs and task.type in ("bug", "fintech_reliability", "performance"): missing.append("No logs/stack traces supplied; hypotheses rely on structure only.")
    if not scan["apis"]: missing.append("Module 01 scan contains no API inventory; affected APIs cannot be determined.")
    if fresh["warning"]: missing.append(fresh["warning"])
    return {
        "analyzed_at": iso(utcnow()), "scan": {"repo_id": task.repo_id, "scan_id": scan["scan_id"], "scanned_at": scan.get("scanned_at"), **fresh},
        "classification": {"declared_type": task.type, "priority": task.priority,
                           "domain_templates": [{"key": t["key"], "name": t["name"], "matched_keywords": t["matched_keywords"]} for t in tpls]},
        "facts": facts, "hypotheses": hyps + llm, "context": {k: ctx[k] for k in ("relevant", "tests", "dependents", "apis", "configs", "packages")},
        "impact": imp, "risk": rk, "missing_context": missing, "evidence": ev.items,
        "llm_provider": provider.name, "_tpl_keys": [t["key"] for t in tpls],
    }
