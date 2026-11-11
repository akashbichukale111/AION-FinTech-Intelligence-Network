from ..templates_fintech import FINTECH, GENERIC_IMPL

def build(task, A):
    tpls = [t for t in FINTECH if t["key"] in A["_tpl_keys"]]
    imp = A["impact"]
    changes = [f["path"] for f in imp["files_to_change"]]
    tests = imp["tests"]["existing"]
    steps = []
    def add(title, desc, files, deps, kind):
        code = f"S{len(steps)+1}"
        steps.append({"code": code, "title": title, "description": desc, "files": files, "depends_on": deps, "kind": kind})
        return code
    s = add("Reproduce and pin current behaviour", "Write a failing automated test (simulated/supplied data only, never real funds) that reproduces the issue.", tests, [], "test")
    hyp_txt = " | ".join(f"[{h['id']}] " + "; ".join(h["investigation"][:2]) for h in A["hypotheses"] if h.get("investigation")) or "Review the affected files manually."
    s = add("Verify root-cause hypotheses", "Run each investigation and record the outcome. Do not code the fix until a hypothesis is verified. " + hyp_txt, changes, [s], "investigate")
    impl = [x for t in tpls for x in t["steps"]] or GENERIC_IMPL[task.type]
    for title, desc in impl:
        s = add(title, desc + " (Apply only to the verified cause.)", changes, [s], "implement")
    s = add("Add and extend tests", "Implement: " + "; ".join(imp["tests"]["required"][:5]), tests or ["(new test file)"], [s], "test")
    if imp["security"]["considerations"]:
        s = add("Security review", "Review: " + "; ".join(imp["security"]["considerations"][:4]), imp["security"]["files"], [s], "review")
    add("Run regression suite and staged rollout", "Run the full suite; deploy behind a flag/staging first; monitor. Requires human sign-off before production.", [], [s], "release")
    rollback = list(dict.fromkeys([r for t in tpls for r in t["rollback"]] +
        ["Revert the change set (single revertable commit/PR)", "Disable the feature flag if used"] +
        (["Provide a tested downgrade for every migration"] if imp["database_schema"]["migration_files"] else [])))
    accept = list(dict.fromkeys([a for t in tpls for a in t["acceptance"]] +
        ["The reported issue no longer reproduces; the reproducing test passes", "All new and existing tests pass",
         "A hypothesis is documented as verified before the fix is merged", "Only files in the approved list changed (else re-approval)"] +
        (["All monetary calculations use Decimal; no float arithmetic"] if imp["money_flow"] else [])))
    solution = (f"Recommended approach: {tpls[0]['name']} template — " + "; ".join(t for t, _ in impl)) if tpls else "Recommended approach: " + "; ".join(t for t, _ in impl)
    return {
        "problem_understanding": f"[{task.type}/{task.priority}] {task.title}: {task.description}",
        "facts": [f["statement"] + f" ({f['evidence_id']})" for f in A["facts"]],
        "hypotheses": [{"id": h["id"], "title": h["title"], "confidence": h["confidence"], "status": h["status"], "evidence_ids": h["evidence_ids"]} for h in A["hypotheses"]],
        "recommendations": {"proposed_solution": solution, "notes": ""},
        "files_and_modules": {"to_change": changes, "dependents": [d["path"] for d in imp["dependent_modules"]],
                              "apis": [f"{a['method']} {a['path']}" for a in imp["affected_apis"]], "database": imp["database_schema"]["files"]},
        "step_dependencies": {s_["code"]: s_["depends_on"] for s_ in steps},
        "required_tests": imp["tests"]["required"], "security_considerations": imp["security"]["considerations"],
        "risk_summary": {"total": A["risk"]["total"], "level": A["risk"]["level"]},
        "rollback_strategy": rollback, "acceptance_criteria": accept, "missing_context": A["missing_context"],
        "governance": "Human approval required before any code execution. Hypotheses are unverified until step S2 is completed.",
    }, steps
