"""Deterministic risk, independent verification, FinTech assurance, rollback, gates, notes. No LLM involved."""
import json, re, os, urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from db import q, q1, now, canon, sha
import governance as gov

SEV = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
SEVN = {v: k for k, v in SEV.items()}
KIND_MODULE = {"architecture": "M01", "plan": "M02", "patch": "M03", "nightshift": "M04", "tests": "M05", "fintech": "M06", "api": "M07",
               "security": "M08", "incidents": "M09", "config": "M10", "migration": "M10", "rollback": "M10"}
EXEMPT_SELF = {"patch", "plan", "nightshift"}   # descriptive of the change itself; verified independently (hash, plan diff)
COMMIT_FREE = {"incidents"}

def parse_diff(diff):
    files, add, rem, lastm = [], 0, 0, None
    for ln in diff.splitlines():
        if ln.startswith("--- "): lastm = ln[4:].strip()
        elif ln.startswith("+++ "):
            p = ln[4:].strip()
            if p == "/dev/null" and lastm: p = lastm
            p = p[2:] if p[:2] in ("a/", "b/") else p
            if p != "/dev/null" and p not in files: files.append(p)
        elif ln.startswith("+"): add += 1
        elif ln.startswith("-"): rem += 1
    return files, add, rem

def load_rel(rid):
    r = q1("SELECT * FROM releases WHERE id=?", (rid,))
    if not r: return None
    r["changed_files"] = json.loads(r["changed_files"] or "[]"); r["is_financial"] = bool(r["is_financial"]); return r

class Ctx:
    def __init__(s, rel, pol):
        s.rel, s.pol, s.rows = rel, pol, {}
        for r in q("SELECT * FROM evidence WHERE release_id=? ORDER BY collected_at, rowid", (rel["id"],)): s.rows[r["kind"]] = r
    def get(s, kind):
        r = s.rows.get(kind)
        if not r: return None, None, "missing"
        p = json.loads(r["payload"])
        if sha(canon(p)) != r["content_hash"]: return None, r["id"], "tampered (content hash mismatch)"
        if kind not in COMMIT_FREE and r["commit_sha"] != s.rel["commit_sha"]:
            return None, r["id"], "bound to a different/no commit" if not r["commit_sha"] else "stale (different commit)"
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(r["collected_at"])).total_seconds() / 3600
        if age > s.pol["evidence_max_age_hours"]: return None, r["id"], f"stale ({age:.0f}h old)"
        if kind not in EXEMPT_SELF and s.rel["agent_id"] and r["collector"] == s.rel["agent_id"]: return None, r["id"], "self-reported by the coding agent (not independent)"
        return p, r["id"], None

def _num(v, d=0):
    try: return float(v)
    except Exception: return d

# ---------------- FinTech assurance (Decimal only) ----------------
FIN_TESTS = ["duplicate_payment_prevention", "idempotency", "ledger_invariant", "refund_correctness", "webhook_signature_validation",
             "retry_safety", "settlement_consistency", "concurrency", "payment_api_compatibility"]

def D(v):
    if isinstance(v, float): raise ValueError("float used for money")
    try: return Decimal(str(v))
    except InvalidOperation: raise ValueError(f"invalid amount {v!r}")

def fintech_assurance(ctx):
    p, ref, prob = ctx.get("fintech")
    if prob: return {"status": "NOT_VERIFIED", "reason": f"fintech evidence {prob}", "checks": [], "evidence_refs": [ref] if ref else []}
    checks = []
    def add(n, s, d): checks.append({"check": n, "status": s, "detail": d})
    if p.get("synthetic") is not True:
        add("synthetic_only", "FAILED", "evidence is not marked synthetic; real-money validation is never accepted"); 
    else: add("synthetic_only", "VERIFIED", "synthetic transactions only")
    tests = p.get("tests", {})
    for n in FIN_TESTS:
        t = tests.get(n)
        if not t or not t.get("executed") or not t.get("run_id"): add(n, "NOT_VERIFIED", "no execution evidence (executed + run_id required)")
        elif t.get("passed") is True: add(n, "VERIFIED", f"run {t['run_id']} passed")
        else: add(n, "FAILED", f"run {t['run_id']} failed")
    def override(n, ok, msg):  # independent Decimal recomputation overrides any claimed result
        for c in checks:
            if c["check"] == n:
                if not ok: c["status"], c["detail"] = "FAILED", msg
                elif c["status"] == "VERIFIED": c["detail"] += "; Decimal recomputation agrees"
    try:
        by = {}
        for e in p.get("ledger_entries", []):
            by.setdefault(e["tx"], [Decimal(0), Decimal(0)])[0 if e["direction"] == "debit" else 1] += D(e["amount"])
        bad = [t for t, (d, c) in by.items() if d != c]
        if by: override("ledger_invariant", not bad, f"debit != credit for tx {bad}")
        for r in p.get("refunds", []):
            total = sum((D(a) for a in r["refunds"]), Decimal(0))
            if any(D(a) <= 0 for a in r["refunds"]) or total > D(r["payment"]): override("refund_correctness", False, f"refunds {total} exceed/invalid vs payment {r['payment']}")
        for r in p.get("payments", []):
            if int(r.get("applied", 1)) > 1: override("duplicate_payment_prevention", False, f"idempotency key {r.get('idempotency_key')} applied {r['applied']}x")
        st = p.get("settlement")
        if st: override("settlement_consistency", D(st["expected_total"]) == D(st["settled_total"]), f"expected {st['expected_total']} != settled {st['settled_total']}")
    except (ValueError, KeyError, TypeError) as e:
        add("decimal_recomputation", "FAILED", f"malformed monetary evidence: {e}")
    mig, mref, mprob = ctx.get("migration")
    if mig and mig.get("has_migration") and (not mig.get("reversible") or not mig.get("reconciliation_passed")):
        add("financial_migration_safety", "FAILED", "financial data migration is not reversible or not reconciled")
    st = "FAILED" if any(c["status"] == "FAILED" for c in checks) else "NOT_VERIFIED" if any(c["status"] == "NOT_VERIFIED" for c in checks) else "VERIFIED"
    return {"status": st, "checks": checks, "evidence_refs": [ref], "note": "Assurance only; Module 10 never authorizes payments, refunds or transfers."}

# ---------------- Rollback readiness ----------------
def rollback_readiness(ctx):
    rel = ctx.rel; checks = []
    rb, rref, rprob = ctx.get("rollback"); mg, mref, mprob = ctx.get("migration")
    def add(n, s, d, ref=None): checks.append({"check": n, "status": s, "detail": d, "evidence_refs": [ref] if ref else []})
    if rprob:
        add("rollback_evidence", "NOT_VERIFIED", f"rollback evidence {rprob}", rref)
    else:
        add("previous_version", "VERIFIED" if rb.get("previous_version") and rb.get("previous_commit") else "NOT_VERIFIED", f"previous {rb.get('previous_version')}@{rb.get('previous_commit')}", rref)
        add("artifact_available", "VERIFIED" if rb.get("artifact_ref") and rb.get("artifact_available") else "NOT_VERIFIED", f"artifact {rb.get('artifact_ref')}", rref)
        add("config_snapshot", "VERIFIED" if rb.get("config_snapshot_ref") else "NOT_VERIFIED", f"config snapshot {rb.get('config_snapshot_ref')}", rref)
        add("rollback_tested", "VERIFIED" if rb.get("tested") else "NOT_VERIFIED", "rollback exercised in non-prod" if rb.get("tested") else "rollback never exercised", rref)
    if mprob and mg is None: add("migration_reversibility", "NOT_VERIFIED", f"migration evidence {mprob}", mref)
    elif mg and mg.get("applicable") is False or (mg and not mg.get("has_migration")): add("migration_reversibility", "NOT_APPLICABLE", "no migration", mref)
    elif mg: add("migration_reversibility", "VERIFIED" if mg.get("reversible") and mg.get("down_script") and mg.get("backup_verified") else "FAILED", "reversible with down script and verified backup" if mg.get("reversible") else "migration not reversible", mref)
    st = "NOT_READY" if any(c["status"] == "FAILED" for c in checks) else "NOT_VERIFIED" if any(c["status"] == "NOT_VERIFIED" for c in checks) else "READY"
    plan = [f"1. Freeze further releases; announce rollback of {rel['version']} ({rel['commit_sha'][:10]}).",
            f"2. Redeploy previous artifact {rb.get('artifact_ref') if rb else '<unknown>'} (version {rb.get('previous_version') if rb else '<unknown>'}).",
            f"3. Restore configuration snapshot {rb.get('config_snapshot_ref') if rb else '<unknown>'}."]
    if mg and mg.get("has_migration"): plan.append(f"4. Run down migration {mg.get('down_script')} after confirming backup {'verified' if mg.get('backup_verified') else 'NOT verified'}.")
    plan += ["5. Re-run smoke tests and synthetic transaction checks." if rel["is_financial"] else "5. Re-run smoke tests.", "6. Record outcome via POST /releases/{id}/outcome."]
    return {"status": st, "checks": checks, "rollback_plan": plan, "generated_from_evidence": rref}

# ---------------- Risk engine ----------------
def risk(ctx):
    rel, pol, out = ctx.rel, ctx.pol, []
    def R(cat, sev, conf, refs, factors, impact, mit): out.append(dict(category=cat, severity=sev, confidence=round(conf, 2), evidence_refs=[r for r in refs if r], contributing_factors=factors, potential_impact=impact, recommended_mitigation=mit))
    def U(cat, kind, prob, ref): R(cat, "UNKNOWN", 0.0, [ref], [f"{kind} evidence {prob}"], "Cannot be assessed; missing evidence is NOT treated as success.", f"Provide fresh, independent {kind} evidence bound to commit {rel['commit_sha'][:10]}.")
    # 1 complexity
    ch, loc = rel["changed_files"], rel["lines_added"] + rel["lines_removed"]
    if not ch: R("change_complexity", "UNKNOWN", 0, [], ["no changed files / diff recorded"], "Change size unknown.", "Attach the diff.")
    else:
        s = "HIGH" if len(ch) > 40 or loc > 1500 else "MEDIUM" if len(ch) > 15 or loc > 500 else "LOW"
        R("change_complexity", s, 1.0 if rel["diff_sha256"] else 0.6, [], [f"{len(ch)} files", f"{loc} changed lines", "diff hash verified" if rel["diff_sha256"] else "no diff supplied; stats self-declared"], "Larger changes raise defect probability and review burden.", "Split the release or add focused review.")
    # 2 critical service impact
    p, ref, pr = ctx.get("architecture")
    if pr: U("critical_service_impact", "architecture", pr, ref)
    else:
        cs = p.get("critical_services_affected", []); hot = [c for c in cs if re.search(r"payment|ledger|settle|auth|billing|wallet", c, re.I)]
        R("critical_service_impact", "HIGH" if hot or len(cs) >= 2 else "MEDIUM" if cs else "LOW", 0.9, [ref], [f"affected critical services: {cs}", f"blast radius: {p.get('blast_radius')}"], "Outage or defect in critical services.", "Canary rollout, extra monitoring on affected services.")
    # 3 regression / tests
    t, ref, pr = ctx.get("tests")
    if pr: U("regression_risk", "tests", pr, ref)
    else:
        rg = t.get("regression")
        if t.get("failed", 0) > 0 or t.get("exit_code", 1) != 0: R("regression_risk", "CRITICAL", 1.0, [ref], [f"{t.get('failed')} failing tests, exit_code={t.get('exit_code')}"], "Known broken behaviour would ship.", "Fix failing tests before release.")
        elif not rg or not rg.get("executed"): R("regression_risk", "UNKNOWN", 0.2, [ref], ["regression suite not executed"], "Regressions undetected.", "Run the critical regression suite.")
        elif rg.get("failed", 0) > 0: R("regression_risk", "CRITICAL", 1.0, [ref], [f"{rg['failed']} critical regression failures"], "Existing behaviour broken.", "Fix regressions.")
        else: R("regression_risk", "LOW", 0.9, [ref], [f"{t.get('passed')} passed, regression {rg.get('total')} ok"], "Low.", "None.")
        # 10 validation completeness (coverage)
        cov = t.get("coverage")
        if cov is None: R("test_coverage", "UNKNOWN", 0.3, [ref], ["coverage not reported"], "Untested paths unknown.", "Report coverage.")
        else: R("test_coverage", "MEDIUM" if _num(cov) < pol["min_coverage"] else "LOW", 0.9, [ref], [f"coverage {cov}% (min {pol['min_coverage']}%)"], "Untested code paths.", "Add tests for changed code.")
    # 4/5 security
    sc, ref, pr = ctx.get("security")
    if pr or not sc.get("scan_executed"):
        why = pr or "scan_executed is false"; U("security_severity", "security", why, ref); U("dependency_vulnerability", "security", why, ref)
    else:
        fo = [f for f in sc.get("findings", []) if f.get("status", "open") == "open"]; worst = max([SEV.get(f["severity"].upper(), 2) for f in fo], default=0)
        R("security_severity", SEVN[worst], 0.9, [ref], [f"{len(fo)} open findings"] + [f"{f.get('id')}:{f['severity']}" for f in fo[:10]], "Exploitable weakness shipped.", "Remediate open findings.")
        dv = [d for d in sc.get("dependency_vulns", []) if not d.get("fixed")]; worst = max([SEV.get(d["severity"].upper(), 2) for d in dv], default=0)
        R("dependency_vulnerability", SEVN[worst], 0.9, [ref], [f"{len(dv)} unfixed vulnerable dependencies"] + [f"{d.get('package')}:{d['severity']}" for d in dv[:10]], "Supply-chain exposure.", "Upgrade or pin patched versions.")
    # 6 API
    a, ref, pr = ctx.get("api")
    if pr: U("api_compatibility", "api", pr, ref)
    elif a.get("applicable") is False: R("api_compatibility", "NONE", 0.8, [ref], ["API surface unchanged"], "None.", "None.")
    elif not a.get("checked"): R("api_compatibility", "UNKNOWN", 0.2, [ref], ["compatibility check not executed"], "Consumers may break.", "Run contract checks.")
    else:
        b = a.get("breaking_changes", [])
        R("api_compatibility", ("CRITICAL" if rel["is_financial"] else "HIGH") if b else "LOW", 0.9, [ref], [f"breaking: {b}"] if b else ["no breaking changes"], "Client/webhook consumers break.", "Version the API or provide compatibility layer.")
    # 7 data integrity
    m, ref, pr = ctx.get("migration")
    if pr: U("data_integrity", "migration", pr, ref)
    elif m.get("applicable") is False or not m.get("has_migration"): R("data_integrity", "NONE", 0.8, [ref], ["no database migration"], "None.", "None.")
    else:
        s = ("CRITICAL" if rel["is_financial"] else "HIGH") if (m.get("destructive") and not m.get("reversible")) else "HIGH" if not m.get("reversible") else "MEDIUM" if not m.get("backup_verified") else "LOW"
        R("data_integrity", s, 0.9, [ref], [f"reversible={m.get('reversible')}", f"destructive={m.get('destructive')}", f"backup_verified={m.get('backup_verified')}"], "Data loss/corruption.", "Provide down migration and verified backup.")
    # 8 financial
    fin = None
    if rel["is_financial"]:
        fin = fintech_assurance(ctx)
        if fin["status"] == "FAILED": R("financial_transaction", "CRITICAL", 1.0, fin["evidence_refs"], [f"{c['check']}: {c['detail']}" for c in fin["checks"] if c["status"] == "FAILED"], "Money could be duplicated, lost or mis-settled.", "Fix failing invariants and re-run synthetic validation.")
        elif fin["status"] == "NOT_VERIFIED": R("financial_transaction", "UNKNOWN", 0.2, fin["evidence_refs"], [fin.get("reason") or f"{c['check']}: {c['detail']}" for c in fin["checks"] if c["status"] == "NOT_VERIFIED"][:9], "Financial correctness unproven.", "Run required synthetic transaction validation.")
        else: R("financial_transaction", "LOW", 0.95, fin["evidence_refs"], ["all financial invariants verified"], "Low.", "None.")
    # 9 incidents
    i, ref, pr = ctx.get("incidents")
    if pr: U("incident_history", "incidents", pr, ref)
    else:
        arch = ctx.get("architecture")[0]; svc = set((arch or {}).get("critical_services_affected", []) + (arch or {}).get("affected_services", []))
        inc = [x for x in i.get("incidents", []) if not svc or x.get("service") in svc]
        sev1 = [x for x in inc if str(x.get("severity", "")).lower() in ("sev1", "critical", "p1")]
        R("incident_history", "HIGH" if sev1 else "MEDIUM" if inc else "NONE", 0.9 if arch else 0.5, [ref], [f"{len(inc)} relevant incidents in window", f"{len(sev1)} critical"], "Repeat failure in fragile services.", "Add targeted monitoring, extra review.")
    # 10b completeness
    req = ["plan", "patch", "tests", "security", "api", "config", "rollback", "architecture", "incidents"] + (["fintech"] if rel["is_financial"] else [])
    miss = [k for k in req if ctx.get(k)[2]]; ratio = (len(req) - len(miss)) / len(req)
    R("validation_completeness", "HIGH" if ratio < 0.5 else "MEDIUM" if miss else "LOW", ratio, [], [f"{len(req)-len(miss)}/{len(req)} evidence kinds usable"] + [f"unusable: {m}" for m in miss], "Unvalidated areas ship unseen.", "Collect the missing/stale evidence.")
    # 11 rollback
    rb = rollback_readiness(ctx)
    R("rollback_capability", "HIGH" if rb["status"] == "NOT_READY" else "UNKNOWN" if rb["status"] == "NOT_VERIFIED" else "LOW", 0.9 if rb["status"] != "NOT_VERIFIED" else 0.2, [rb["generated_from_evidence"]], [f"{c['check']}: {c['status']}" for c in rb["checks"]], "Slow recovery if release fails.", "Prepare and test rollback.")
    return out

def summarize_risk(items):
    known = [i for i in items if i["severity"] != "UNKNOWN"]; worst = max([SEV[i["severity"]] for i in known], default=-1)
    unk = [i["category"] for i in items if i["severity"] == "UNKNOWN"]
    return {"overall_severity": SEVN.get(worst, "UNKNOWN"), "worst_is_max_not_average": True, "critical_risks": [i["category"] for i in items if i["severity"] == "CRITICAL"],
            "high_risks": [i["category"] for i in items if i["severity"] == "HIGH"], "unknown_categories": unk,
            "assurance_confidence": round(sum(i["confidence"] for i in items) / max(len(items), 1), 2)}

# ---------------- Independent verification ----------------
def verify(ctx):
    rel, pol, res = ctx.rel, ctx.pol, []
    def add(n, s, d, refs=()): res.append({"check": n, "status": s, "detail": d, "evidence_refs": [r for r in refs if r]})
    def get(kind, n):
        p, ref, pr = ctx.get(kind)
        if pr: add(n, "NOT_VERIFIED", f"{kind} evidence {pr}", [ref]); return None, ref
        if p.get("applicable") is False: add(n, "NOT_APPLICABLE", p.get("reason", "declared not applicable by evidence provider"), [ref]); return None, ref
        return p, ref
    pl, ref = get("plan", "plan_completion")
    if pl:
        done = pl.get("steps_done", 0) == pl.get("steps_total", -1) and pl.get("steps_total", 0) > 0
        add("plan_completion", "VERIFIED" if done and pl.get("approved_by") else "FAILED", f"{pl.get('steps_done')}/{pl.get('steps_total')} steps; plan approved_by={pl.get('approved_by')}", [ref])
        ch = set(rel["changed_files"]); appr = set(pl.get("approved_files", [])); exp = set(pl.get("expected_files", []))
        if not ch: add("plan_compliance", "NOT_VERIFIED", "no changed files recorded"); add("unintended_changes", "NOT_VERIFIED", "no changed files recorded")
        else:
            miss = sorted(exp - ch); extra = sorted(ch - appr)
            add("plan_compliance", "FAILED" if miss else "VERIFIED", f"expected files missing from change: {miss}" if miss else "all expected files changed", [ref])
            add("unintended_changes", "FAILED" if extra else "VERIFIED", f"files changed outside approved plan: {extra}" if extra else "no unplanned file changes", [ref])
    else:
        for n in ("plan_compliance", "unintended_changes"): add(n, "NOT_VERIFIED", "plan evidence unavailable")
    pa, ref = get("patch", "patch_integrity")
    if pa:
        if not rel["diff_sha256"]: add("patch_integrity", "NOT_VERIFIED", "no diff stored with candidate; cannot independently hash", [ref])
        elif pa.get("diff_sha256") != rel["diff_sha256"]: add("patch_integrity", "FAILED", "agent-reported patch hash differs from independently hashed diff", [ref])
        elif sorted(pa.get("changed_files", rel["changed_files"])) != sorted(rel["changed_files"]): add("patch_integrity", "FAILED", "patch file list differs from diff", [ref])
        else: add("patch_integrity", "VERIFIED", "diff hash matches patch evidence", [ref])
    t, ref = get("tests", "test_execution")
    if t:
        proof = t.get("executed") is True and t.get("command") and t.get("run_id") and "exit_code" in t and t.get("total", 0) > 0
        if not proof: add("test_execution", "NOT_VERIFIED", "no execution proof (executed, command, run_id, exit_code, total>0 required)", [ref])
        elif t["exit_code"] != 0 or t.get("failed", 0) > 0: add("test_execution", "FAILED", f"exit_code={t['exit_code']} failed={t.get('failed')}", [ref])
        elif t.get("coverage") is not None and _num(t["coverage"]) < pol["min_coverage"]: add("test_execution", "FAILED", f"coverage {t['coverage']}% < {pol['min_coverage']}%", [ref])
        else: add("test_execution", "VERIFIED", f"run {t['run_id']}: {t.get('passed')}/{t['total']} passed", [ref])
        rg = t.get("regression")
        if not rg or not rg.get("executed"): add("regression", "NOT_VERIFIED", "critical regression suite not executed", [ref])
        else: add("regression", "FAILED" if rg.get("failed", 0) else "VERIFIED", f"{rg.get('failed', 0)}/{rg.get('total')} critical regression failures", [ref])
    else:
        add("regression", "NOT_VERIFIED", "tests evidence unavailable")
    s, ref = get("security", "security")
    if s:
        if not s.get("scan_executed"): add("security", "NOT_VERIFIED", "security scan not executed", [ref])
        else:
            thr = SEV[pol["block_security_severity"]]
            bad = [f for f in s.get("findings", []) if f.get("status", "open") == "open" and SEV.get(f["severity"].upper(), 2) >= thr] + [d for d in s.get("dependency_vulns", []) if not d.get("fixed") and SEV.get(d["severity"].upper(), 2) >= thr]
            add("security", "FAILED" if bad else "VERIFIED", f"{len(bad)} open findings >= {pol['block_security_severity']}" if bad else "no blocking security findings", [ref])
    f = fintech_assurance(ctx) if rel["is_financial"] else None
    if f: add("financial_invariants", {"VERIFIED": "VERIFIED", "FAILED": "FAILED"}.get(f["status"], "NOT_VERIFIED"), f"fintech assurance {f['status']}", f["evidence_refs"])
    else: add("financial_invariants", "NOT_APPLICABLE", "release not marked financial")
    a, ref = get("api", "api_compat")
    if a:
        if not a.get("checked"): add("api_compat", "NOT_VERIFIED", "compatibility check not executed", [ref])
        elif a.get("breaking_changes"): add("api_compat", "FAILED", f"breaking changes: {a['breaking_changes']}", [ref])
        elif a.get("webhooks_changed") and not a.get("webhook_signature_validation"): add("api_compat", "FAILED", "webhooks changed without signature validation evidence", [ref])
        else: add("api_compat", "VERIFIED", "no breaking API/webhook changes", [ref])
    c, ref = get("config", "config")
    if c:
        if c.get("validated") is not True: add("config", "FAILED" if c.get("errors") else "NOT_VERIFIED", f"errors: {c.get('errors')}" if c.get("errors") else "config not validated", [ref])
        elif c.get("secrets_scan_clean") is False: add("config", "FAILED", "secrets detected in configuration", [ref])
        else: add("config", "VERIFIED", "configuration validated", [ref])
    rb = rollback_readiness(ctx)
    add("rollback_artifacts", {"READY": "VERIFIED", "NOT_READY": "FAILED"}.get(rb["status"], "NOT_VERIFIED"), f"rollback {rb['status']}", [rb["generated_from_evidence"]])
    st = {r["check"]: r["status"] for r in res}
    need = ["plan_completion", "test_execution", "patch_integrity"]
    tc = "FAILED" if any(st.get(n) == "FAILED" for n in need) else "VERIFIED" if all(st.get(n) == "VERIFIED" for n in need) else "NOT_VERIFIED"
    res.insert(0, {"check": "task_completion", "status": tc, "detail": "derived from plan completion + executed tests + patch integrity; the agent's own completion claim is ignored", "evidence_refs": []})
    summ = {k: sum(1 for r in res if r["status"] == k) for k in ("VERIFIED", "FAILED", "NOT_VERIFIED", "NOT_APPLICABLE")}
    return {"checks": res, "summary": summ, "fintech": f, "rollback": rb}

# ---------------- Gates ----------------
GATES = [("plan_completion", "Engineering plan completion", ["plan_completion", "plan_compliance", "unintended_changes", "patch_integrity"]), ("code_review", "Code review approval", None),
         ("required_tests", "Required tests passed", ["test_execution"]), ("critical_regression", "Critical regression checks passed", ["regression"]),
         ("security", "Security requirements satisfied", ["security"]), ("financial_invariants", "Financial invariants validated", ["financial_invariants"]),
         ("api_compatibility", "API compatibility checked", ["api_compat"]), ("configuration", "Configuration validated", ["config"]),
         ("human_approvals", "Required human approvals obtained", None), ("rollback", "Rollback readiness established", ["rollback_artifacts"])]

def evaluate(rel, persist=False):
    pol = gov.active_policy(); ctx = Ctx(rel, pol)
    risks = risk(ctx); rs = summarize_risk(risks); ver = verify(ctx); vs = {c["check"]: c for c in ver["checks"]}
    eff = rs["overall_severity"]
    if eff == "UNKNOWN" or rs["unknown_categories"]: eff = SEVN[max(SEV.get(eff, 0), SEV[pol["unknown_risk_as"]])]
    req = gov.approval_requirement(rel, pol, eff); gates = []
    for i, (key, name, checks) in enumerate(GATES, 1):
        g = {"id": i, "key": key, "name": name, "mandatory": key in pol["mandatory_gates"], "checks": [], "evidence_refs": []}
        if key == "code_review":
            rej = gov.valid_approvals(rel, "code_review", "reject") or [a for a in q("SELECT * FROM approvals WHERE release_id=? AND cycle=? AND kind='code_review' AND decision='reject'", (rel["id"], rel["cycle"]))]
            ok = gov.valid_approvals(rel, "code_review")
            g["status"] = "FAIL" if rej else "PASS" if len(ok) >= pol["code_review"]["min"] else "NOT_VERIFIED"
            g["detail"] = f"{len(ok)}/{pol['code_review']['min']} valid human code-review approvals" + (" ; rejected" if rej else "")
        elif key == "human_approvals":
            ok = gov.valid_approvals(rel, "release"); have = {a["role"] for a in ok}; miss = sorted(set(req["required_roles"]) - have)
            sat = len(ok) >= req["min_approvers"] and not miss
            g["status"] = "FAIL" if rel["status"] == "REJECTED" else "PASS" if sat else "PENDING"
            g["detail"] = f"{len(ok)}/{req['min_approvers']} approvers; missing roles: {miss}; tier {req['risk_tier']}"; g["requirement"] = req
        else:
            cs = [vs[c] for c in checks]; g["checks"] = cs; g["evidence_refs"] = sorted({r for c in cs for r in c["evidence_refs"]})
            sts = {c["status"] for c in cs}
            g["status"] = "FAIL" if "FAILED" in sts else "NOT_VERIFIED" if "NOT_VERIFIED" in sts else "NOT_APPLICABLE" if sts == {"NOT_APPLICABLE"} else "PASS"
            g["detail"] = "; ".join(f"{c['check']}={c['status']}" for c in cs)
        gates.append(g)
    mand = [g for g in gates if g["mandatory"]]; blockers = [f"gate {g['key']} failed" for g in mand if g["status"] == "FAIL"]
    if rs["critical_risks"]: blockers += [f"critical risk: {c}" for c in rs["critical_risks"]]
    warnings = [f"{r['category']} risk {r['severity']}" for r in risks if r["severity"] != "UNKNOWN" and SEV[r["severity"]] >= SEV[pol["warning_risk_at"]]]
    warnings += [f"{c} could not be assessed (missing/stale evidence)" for c in rs["unknown_categories"]] + [f"warning gate {g['key']} = {g['status']}" for g in gates if g["key"] in pol["warning_gates"] and g["status"] not in ("PASS", "NOT_APPLICABLE")]
    pending = any(g["status"] == "PENDING" for g in mand)
    status = "BLOCKED" if blockers else "NOT_VERIFIED" if any(g["status"] == "NOT_VERIFIED" for g in mand) else "PENDING_APPROVAL" if pending else "READY_WITH_WARNINGS" if warnings else "READY"
    out = {"release_id": rel["id"], "status": status, "blockers": blockers, "warnings": warnings, "gates": gates, "risk_summary": rs, "risks": risks, "verification": ver,
           "approval_requirement": req, "policy_version": pol["version"], "evaluated_at": now(), "release_state": rel["status"]}
    if persist:
        from db import x, nid
        x("INSERT INTO assessments VALUES(?,?,?,?,?,?)", (nid("risk"), rel["id"], now(), rs["overall_severity"], rs["assurance_confidence"], json.dumps({"summary": rs, "risks": risks})))
        x("INSERT INTO verifications VALUES(?,?,?,?)", (nid("ver"), rel["id"], now(), json.dumps(ver)))
        x("INSERT INTO gate_evals VALUES(?,?,?,?,?,?)", (nid("gate"), rel["id"], now(), status, pol["version"], json.dumps({"gates": gates, "blockers": blockers, "warnings": warnings})))
    return out

# ---------------- Release notes ----------------
def release_notes(rel, ev):
    ch = rel["changed_files"]
    return "\n".join([f"# Release {rel['version']} — {rel['title']}", f"Commit `{rel['commit_sha']}` (base `{rel['base_sha'] or 'n/a'}`) · repo `{rel['repo'] or 'n/a'}` · author {rel['author']}",
        f"Gate status: **{ev['status']}** · risk: **{ev['risk_summary']['overall_severity']}** · assurance confidence {ev['risk_summary']['assurance_confidence']}", "", rel["description"] or "", "",
        f"## Change summary\n{len(ch)} files, +{rel['lines_added']} / -{rel['lines_removed']} lines", *[f"- `{f}`" for f in ch[:60]], "",
        "## Warnings", *([f"- {w}" for w in ev["warnings"]] or ["- none"]), "", "## Blockers", *([f"- {b}" for b in ev["blockers"]] or ["- none"]),
        "", f"Deployment artifact: {rel['artifact_ref'] or 'not recorded'}", "_Generated deterministically from stored evidence._"])

# ---------------- Adapters (configurable; Module 10 does not re-implement Modules 01-09) ----------------
def collect_from_modules(rel, actor_id="adapter"):
    """FORGEX_MODULE_URLS = {"tests":"http://m05/api/v1/evidence?commit={commit}", ...}; each must return the payload JSON for that evidence kind."""
    import db
    cfg = json.loads(os.environ.get("FORGEX_MODULE_URLS", "{}")); got, errs = [], []
    for kind, url in cfg.items():
        try:
            with urllib.request.urlopen(url.format(commit=rel["commit_sha"], release=rel["id"]), timeout=10) as r: payload = json.loads(r.read())
            cs = payload.pop("commit_sha", rel["commit_sha"]) if isinstance(payload, dict) else rel["commit_sha"]
            eid = db.nid("ev"); db.x("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, rel["id"], KIND_MODULE.get(kind, "M??"), kind, json.dumps(payload), url, f"adapter:{kind}", cs, sha(canon(payload)), now()))
            got.append(kind)
        except Exception as e: errs.append({"kind": kind, "error": str(e)[:200]})
    return {"collected": got, "errors": errs, "configured": list(cfg)}

class GitHubProvider:
    """PR import. Read-only; never merges or deploys."""
    def fetch(self, repo, number):
        api = os.environ.get("FORGEX_GITHUB_API", "https://api.github.com"); tok = os.environ.get("FORGEX_GITHUB_TOKEN")
        def get(path, accept):
            req = urllib.request.Request(f"{api}/repos/{repo}/pulls/{number}{path}", headers={"Accept": accept, **({"Authorization": f"Bearer {tok}"} if tok else {})})
            with urllib.request.urlopen(req, timeout=20) as r: return r.read().decode()
        meta = json.loads(get("", "application/vnd.github+json")); diff = get("", "application/vnd.github.v3.diff")
        return {"commit_sha": meta["head"]["sha"], "base_sha": meta["base"]["sha"], "title": meta["title"], "description": meta.get("body") or "", "diff": diff}
