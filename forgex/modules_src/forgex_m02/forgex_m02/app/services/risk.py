"""Deterministic, explainable risk scoring (rules v1). No AI-generated scores."""
from decimal import Decimal as D, ROUND_HALF_UP

RULES_VERSION = "risk-rules-v1"
WEIGHTS = {"complexity": 20, "repository_impact": 15, "dependency": 10, "security": 15, "data_integrity": 20, "regression": 10, "reversibility": 10}

def _bucket(n, steps):
    for limit, score in steps:
        if n <= limit:
            return score
    return steps[-1][1]

def assess(task_type, impact, tpls):
    n_ch, n_dep = len(impact["files_to_change"]), len(impact["dependent_modules"])
    n_api, n_cfg, n_pkg = len(impact["affected_apis"]), len(impact["configuration"]), len(impact["packages"])
    db, mig = impact["database_schema"]["files"], impact["database_schema"]["migration_files"]
    sec = bool(impact["security"]["files"]) or task_type == "security"
    tpl_sec, money = any(t["security"] for t in tpls), impact["money_flow"]
    n_tests = len(impact["tests"]["existing"])
    F = []
    def add(name, score, why, ev=()):
        F.append({"name": name, "weight": WEIGHTS[name], "score": score, "explanation": why, "evidence_ids": list(ev)})
    s = _bucket(n_ch, [(0, 5), (2, 15), (5, 40), (8, 65), (999, 85)]) + (10 if task_type in ("feature", "refactor") else 0)
    add("complexity", min(s, 100), f"{n_ch} file(s) to change; task type '{task_type}'.", [f["evidence_id"] for f in impact["files_to_change"]])
    s = min(_bucket(n_dep, [(0, 10), (3, 35), (8, 60), (999, 85)]) + 5 * n_api, 100)
    add("repository_impact", s, f"{n_dep} dependent module(s), {n_api} affected API(s).", [d["evidence_id"] for d in impact["dependent_modules"]])
    s = min(10 + 10 * n_cfg + 10 * n_pkg, 100)
    add("dependency", s, f"{n_cfg} related config file(s), {n_pkg} related package(s).")
    s = 80 if (task_type == "security" or sec and tpl_sec) else 50 if (sec or tpl_sec) else 10
    add("security", s, "Security-typed task or security-sensitive files in scope." if s >= 50 else "No security-sensitive scope detected.")
    s = 85 if (money and db) else 70 if db else 40 if money else 10
    add("data_integrity", s, f"Monetary flow: {money}; data-layer files in scope: {len(db)}.")
    s = 90 if n_tests == 0 else 55 if n_tests < n_ch else 20
    add("regression", s, f"{n_tests} existing linked test file(s) for {n_ch} changed file(s).")
    s = 85 if mig else 55 if db else 15
    add("reversibility", s, "Schema/migration changes are hard to reverse." if mig else "Data-layer changes need careful rollback." if db else "Code-only change; easy to revert.")
    total = (sum(D(f["weight"]) * D(f["score"]) for f in F) / D(100)).quantize(D("0.1"), ROUND_HALF_UP)
    level = "low" if total < 30 else "medium" if total < 55 else "high" if total < 75 else "critical"
    return {"rules_version": RULES_VERSION, "total": str(total), "level": level, "factors": F,
            "requires_senior_review": level in ("high", "critical"),
            "method": "Weighted average of rule-based factor scores (0-100); thresholds: <30 low, <55 medium, <75 high, else critical."}
