import re
from ..util import tokens

DB_RE = re.compile(r"(model|schema|migration|repositor|dao|ledger|database|\bdb\b|/db/)", re.I)
SEC_RE = re.compile(r"(auth|crypto|token|secret|permission|webhook|signature|jwt|session|password)", re.I)
CFG_KINDS = {"config"}

def analyze(task_type, scan, ctx, tpls, ev, max_changes=8):
    kind_of = {f["path"]: f["kind"] for f in scan["files"]}
    changes = [r for r in ctx["relevant"] if r["kind"] != "config"][:max_changes]
    cpaths = [c["path"] for c in changes]
    scope = set(cpaths) | {d["path"] for d in ctx["dependents"]}
    db_files = sorted(p for p in scope if kind_of.get(p) in ("model", "migration", "schema") or DB_RE.search(p))
    sec_files = sorted(p for p in scope if SEC_RE.search(p))
    migrations = [p for p in db_files if kind_of.get(p) in ("migration", "schema") or "migration" in p.lower()]
    tpl_sec = [s for t in tpls for s in t["security"]]
    tpl_tests = [s for t in tpls for s in t["tests"]]
    money = any(t["money"] for t in tpls)
    apis = [{"method": a.get("method"), "path": a.get("path"), "file": a.get("file"), "direct": a.get("file") in cpaths,
             "evidence_id": a["evidence_id"]} for a in ctx["apis"]]
    regress = [f"{d['path']} imports {', '.join(d['depends_on'])}; re-run its tests and review call sites." for d in ctx["dependents"]]
    regress += [f"API {a['method']} {a['path']} may change behaviour." for a in apis]
    required = list(dict.fromkeys(tpl_tests + ["Regression test reproducing the reported issue (must fail before the fix)",
                                               "Unit tests for each changed module"]))
    if money: required.append("Decimal-precision assertions for all monetary calculations")
    existing_tests = [t["path"] for t in ctx["tests"]]
    return {
        "files_to_change": [{"path": c["path"], "evidence_id": c["evidence_id"], "reason": "; ".join(c["reasons"])} for c in changes],
        "dependent_modules": ctx["dependents"], "affected_apis": apis,
        "database_schema": {"files": db_files, "migration_files": migrations,
                            "implication": ("Schema/migration files in scope; plan a reversible migration." if migrations else
                                            "Data-layer files in scope; check transactional behaviour." if db_files else "No data-layer files identified in scope.")},
        "security": {"files": sec_files, "considerations": tpl_sec + (["Security-sensitive files in scope: " + ", ".join(sec_files)] if sec_files else []) +
                     (["Security-typed task: threat-model the change."] if task_type == "security" else [])},
        "configuration": ctx["configs"], "packages": ctx["packages"],
        "tests": {"existing": existing_tests, "required": required,
                  "coverage_gap": "No existing tests linked to the affected files." if not existing_tests else None},
        "regression_risks": regress, "money_flow": money,
        "graph": {"changed": cpaths, "dependents": [d["path"] for d in ctx["dependents"]],
                  "apis": [{"label": f"{a['method']} {a['path']}", "file": a["file"]} for a in apis], "edges": ctx["edges"]},
    }
