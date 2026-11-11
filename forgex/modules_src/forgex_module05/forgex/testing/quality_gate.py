"""G. Explainable quality gate. The verdict comes from checks, never from the score: any failed check fails the gate."""
from __future__ import annotations

DEFAULTS = {"min_line_pct": 70.0, "min_changed_line_pct": 80.0, "max_skipped_ratio": 0.2}

def evaluate(run, failures, regression, coverage, changed_cov, fintech=None, security=None, critical_tests=(), thresholds=None):
    th = {**DEFAULTS, **(thresholds or {})}
    s, checks = run["summary"], []
    def add(id_, status, title, detail, critical=False, evidence=None):
        checks.append({"id": id_, "status": status, "title": title, "detail": detail, "critical": critical, "evidence": evidence or []})

    # 1 execution completeness
    if run["status"] in ("timeout", "error", "incomplete", "cancelled"):
        add("execution", "fail", "Test run completed", f"Run ended as '{run['status']}': {run.get('status_reason', 'no reason recorded')}. Results are incomplete.", True)
    else:
        add("execution", "pass", "Test run completed", f"{s['total']} tests executed, exit code {run.get('exit_code')}.")
    # 2 test results
    if s["failed"] + s["errors"]:
        add("tests", "fail", "All executed tests pass", f"{s['failed']} failed, {s['errors']} errored of {s['total']}.", False,
            [f["tests"][:3] and f"{f['category']}: {f['tests'][0]}" for f in failures[:5]])
    elif s["total"]:
        add("tests", "pass", "All executed tests pass", f"{s['passed']} passed, {s['skipped']} skipped.")
    # 3 critical tests
    crit = [r for r in run.get("results", []) if any(c in r["name"] for c in critical_tests)] if critical_tests else []
    bad = [r["name"] for r in crit if r["status"] in ("failed", "error")]
    missing = [c for c in critical_tests if not any(c in r["name"] for r in run.get("results", []))]
    if bad: add("critical_tests", "fail", "Critical tests pass", f"{len(bad)} critical test(s) failing.", True, bad)
    elif missing: add("critical_tests", "fail", "Critical tests pass", "Critical tests were not executed.", True, missing)
    elif critical_tests: add("critical_tests", "pass", "Critical tests pass", f"{len(crit)} critical test(s) passed.")
    # 4 regressions
    if regression:
        if regression["newly_failing"]:
            add("regressions", "fail", "No previously-passing test now fails", f"{len(regression['newly_failing'])} regression(s).", True, regression["newly_failing"][:10])
        else:
            high = [x for x in regression["api_contract_changes"] + regression["migration_risks"] + regression["compatibility_risks"] if x["severity"] == "high"]
            med = [x for x in regression["api_contract_changes"] + regression["migration_risks"] + regression["compatibility_risks"] if x["severity"] == "medium"]
            if high: add("regressions", "fail", "No high-risk contract/migration/compatibility change", f"{len(high)} high-risk change(s) need explicit sign-off.", False, [f"{x['file']}: {x['detail']}" for x in high[:8]])
            elif med: add("regressions", "warn", "No medium-risk contract/migration change", f"{len(med)} medium-risk change(s).", False, [f"{x['file']}: {x['detail']}" for x in med[:8]])
            else: add("regressions", "pass" if regression["comparison_available"] or regression["changed_files"] else "unknown", "Regression analysis",
                      "No regressions or risky changes detected." if regression["comparison_available"] or regression["changed_files"] else "No baseline run or change set supplied.")
    else:
        add("regressions", "unknown", "Regression analysis", "No baseline run or change set supplied; regressions were not assessed.")
    # 5 coverage
    if not coverage:
        add("coverage", "unknown", "Coverage evidence", "No coverage report was produced; coverage is not assessed.")
    else:
        lp = coverage["total"]["line_pct"]
        add("coverage", "pass" if lp is not None and lp >= th["min_line_pct"] else "warn", "Line coverage",
            f"{lp}% lines" + (f", {coverage['total']['branch_pct']}% branches" if coverage["total"]["branch_supported"] else ", branch coverage not reported") + f" (threshold {th['min_line_pct']}%).")
        if changed_cov and changed_cov.get("available") and changed_cov.get("changed_line_pct") is not None:
            cp = changed_cov["changed_line_pct"]
            ct = changed_cov.get("critical_untested", [])
            st = "fail" if ct else "pass" if cp >= th["min_changed_line_pct"] else "warn"
            add("changed_coverage", st, "Changed code is covered", f"{cp}% of changed lines covered (threshold {th['min_changed_line_pct']}%)." + (f" {len(ct)} critical file(s) with untested changes." if ct else ""),
                bool(ct), [f"{c['path']}: lines {c['uncovered'][:10]}" for c in ct[:5]])
    # 6 security
    if security is None:
        add("security", "unknown", "Security findings", "No security module findings supplied.")
    else:
        hi = [f for f in security if str(f.get("severity", "")).lower() in ("critical", "high")]
        md = [f for f in security if str(f.get("severity", "")).lower() == "medium"]
        add("security", "fail" if hi else "warn" if md else "pass", "No critical/high security findings", f"{len(hi)} critical/high, {len(md)} medium finding(s).", bool(hi),
            [str(f.get("title") or f.get("id")) for f in hi[:5]])
    # 7 fintech
    if fintech:
        bad = [sc["name"] for sc in fintech["scenarios"] if sc["status"] != "passed"]
        add("fintech", "fail" if bad else "pass", "FinTech simulations", f"{len(fintech['scenarios']) - len(bad)}/{len(fintech['scenarios'])} scenarios passed.", bool(bad), bad)
    # 8 completeness
    if s["total"]:
        ratio = s["skipped"] / s["total"]
        add("completeness", "warn" if ratio > th["max_skipped_ratio"] else "pass", "Validation completeness",
            f"{s['skipped']} of {s['total']} tests skipped ({ratio:.0%}).")
    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] in ("warn", "unknown")]
    if fails: verdict = "fail"
    elif run["status"] != "passed": verdict = "incomplete"
    elif warns: verdict = "conditional"
    else: verdict = "pass"
    pts = {"pass": 1.0, "warn": 0.5, "unknown": 0.5, "fail": 0.0}
    score = round(100 * sum(pts[c["status"]] for c in checks) / len(checks)) if checks else None
    if fails and score is not None: score = min(score, 49)
    return {"verdict": verdict, "critical_failures": [c["id"] for c in fails if c["critical"]], "checks": checks, "score": score,
            "score_note": "Informational only. The verdict is decided by the checks; a failed check caps the score at 49 and can never be averaged away.",
            "thresholds": th, "unknowns": [c["id"] for c in checks if c["status"] == "unknown"]}
