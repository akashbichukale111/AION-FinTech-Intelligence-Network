"""Orchestration: discovery -> isolated run -> parse -> failure analysis -> regression -> coverage -> quality gate -> history."""
from __future__ import annotations
import os, shlex, sys, threading, traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import coverage as cov, discovery, failures as fa, fintech, generation, quality_gate, regression, runner, store
from .integrations import git_changes, providers

POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("FORGEX_TESTING_WORKERS", "2")))
_LOCK = threading.Lock()

def _event(run, phase, msg):
    run["events"].append({"ts": store.now(), "phase": phase, "message": msg})
    store.put("test_run", run["id"], run, run["repository_id"])

def summarize(results):
    c = lambda s: sum(1 for r in results if r["status"] == s)
    return {"total": len(results), "passed": c("passed"), "failed": c("failed"), "errors": c("error"), "skipped": c("skipped")}

def final_status(cmds, results, framework):
    tests = [c for c in cmds if c["role"] == "test"]
    if any(c["timed_out"] for c in tests): return "timeout", "execution exceeded the configured timeout and was killed"
    if any(c["start_error"] for c in tests): return "error", "command could not start: " + next(c["start_error"] for c in tests if c["start_error"])
    s = summarize(results)
    if not results:
        if any(c["exit_code"] == 5 for c in tests) and framework == "pytest": return "incomplete", "pytest collected no tests"
        if any(c["exit_code"] not in (0, None) for c in tests): return "error", "no test results were produced and the command exited non-zero (collection/environment failure)"
        return "incomplete", "command exited 0 but no test results were parsed"
    if s["failed"] or s["errors"]: return "failed", f"{s['failed']} failed, {s['errors']} errored"
    if any(c["exit_code"] not in (0, None) for c in tests): return "error", "tests reported no failures but the command exited non-zero"
    return "passed", ""

def create_run(req: dict) -> dict:
    if req.get("authorized") is not True: raise PermissionError("test runs must be explicitly authorized (authorized=true)")
    token = os.environ.get("FORGEX_TESTING_TOKEN")
    if token and req.get("token") != token: raise PermissionError("invalid or missing authorization token")
    repo = providers.repository.resolve(req.get("repository_id"), req.get("repo_path"))
    iso = runner.choose_mode(req.get("isolation", "auto"), bool(req.get("allow_unisolated_network")))   # fail fast, before queueing
    rid = store.new_id("run")
    run = {"id": rid, "repository_id": repo.id, "commit_ref": req.get("commit_ref") or repo.commit_ref, "status": "queued", "status_reason": "",
           "created_at": store.now(), "started_at": None, "finished_at": None, "duration_seconds": None, "exit_code": None, "command": req.get("command"),
           "commands": [], "environment": {**iso, "python": sys.version.split()[0], "platform": sys.platform, "timeout_seconds": req.get("timeout_seconds", 300),
                                          "memory_mb": req.get("memory_mb", 1024)},
           "events": [], "summary": summarize([]), "results": [], "failure_groups": [], "regression": None, "coverage": None, "coverage_report": None,
           "changed_coverage": None, "coverage_compare": None, "fintech": None, "quality_gate": None, "base_run_id": req.get("base_run_id"),
           "evidence_refs": [], "request": {k: v for k, v in req.items() if k not in ("token", "changes")}}
    _event(run, "queued", f"Run authorized; isolation={iso['mode']}, network_isolated={iso['network_isolated']}")
    POOL.submit(_execute, rid, req)
    return run

def _execute(rid, req):
    run = store.get("test_run", rid)
    ws, final = None, None
    try:
        run["status"], run["started_at"] = "running", store.now()
        repo = providers.repository.resolve(run["repository_id"], req.get("repo_path"))
        iso = {k: run["environment"][k] for k in ("mode", "network_isolated")}
        _event(run, "workspace", "Creating isolated workspace copy")
        ws = providers.workspace.create(repo, req.get("change_set_id"))
        changes = list(req.get("changes") or ws.changes or [])
        if not changes and req.get("base_ref"): changes = git_changes(repo.path, req["base_ref"])
        extra = providers.plan.affected_files(req["plan_id"]) if req.get("plan_id") else []
        disc = discovery.discover(ws.path)
        known = set(disc["import_graph"].keys())
        fws = [f for f in disc["frameworks"] if any(t["framework"] == f["framework"] for t in disc["test_files"]) or f["language"] != "python"]
        wanted = req.get("framework")
        if wanted: fws = [f for f in fws if f["framework"].startswith(wanted)]
        _event(run, "discovery", f"{disc['totals']['tests']} tests in {disc['totals']['test_files']} files; frameworks: {', '.join(f['framework'] for f in fws) or 'none detected'}")
        tmo, mem = int(req.get("timeout_seconds", 300)), int(req.get("memory_mb", 1024))
        py = "python" if iso["mode"] == "docker" else sys.executable
        plan = []
        if req.get("command"):
            plan.append(("custom", [(shlex.split(req["command"]), "test")]))
        else:
            for f in fws:
                cmds = runner.default_commands(f["framework"], py, bool(req.get("collect_coverage", True)), iso["mode"])
                if cmds: plan.append((f["framework"], [(c, "post" if "coverage" in c and "xml" in c else "test") for c in cmds]))
        if not plan: raise RuntimeError("no runnable test framework detected; pass an explicit `command`")
        results, all_cmds, evid, outputs = [], [], [], ""
        per = []
        for fw, cmds in plan:
            fw_cmds = []
            for cmd, role in cmds:
                _event(run, "execute", f"[{fw}] {' '.join(cmd)}")
                c = runner.run_command(cmd, ws.path, iso, tmo, mem, f"{rid}_{len(all_cmds)}")
                c["role"], c["framework"] = role, fw
                fw_cmds.append(c); all_cmds.append(c)
                if role == "test" and (c["timed_out"] or c["start_error"]): break
            r, ev = runner.collect_results(ws.path, fw, "\n".join(c["stdout"] + c["stderr"] for c in fw_cmds))
            results += r; evid += ev
            per.append(final_status(fw_cmds, r, fw))
        rank = {"passed": 0, "incomplete": 1, "failed": 2, "error": 3, "timeout": 4}
        status, reason = max(per, key=lambda x: rank[x[0]])
        run.update(commands=[{k: v for k, v in c.items() if k not in ("stdout", "stderr")} for c in all_cmds], exit_code=next((c["exit_code"] for c in all_cmds if c["role"] == "test"), None),
                   command=run["command"] or " && ".join(c["command"] for c in all_cmds if c["role"] == "test"), results=results, summary=summarize(results),
                   status="running", status_reason="")
        final = (status, reason)
        store.put("run_logs", rid, {"commands": [{"command": c["command"], "stdout": c["stdout"], "stderr": c["stderr"]} for c in all_cmds]}, run["repository_id"])
        run["evidence_refs"] = [{"type": "logs", "ref": f"run_logs/{rid}"}] + [{"type": "report", "path": e} for e in evid]
        _event(run, "results", f"{run['summary']['total']} results: {run['summary']['passed']} passed, {run['summary']['failed'] + run['summary']['errors']} failing, {run['summary']['skipped']} skipped -> {status}")

        # coverage
        files = set(disc["import_graph"].keys())
        cvg, cpath, notes = cov.find_and_parse(ws.path, files) if req.get("collect_coverage", True) else (None, None, ["coverage collection disabled"])
        run["coverage"], run["coverage_report"] = cvg, {"path": cpath, "notes": notes}
        base = store.get("test_run", run["base_run_id"]) if run["base_run_id"] else None
        run["changed_coverage"] = cov.changed_uncovered(cvg, changes) if changes else None
        run["coverage_compare"] = cov.compare(base.get("coverage") if base else None, cvg) if base else None
        _event(run, "coverage", f"line coverage {cvg['total']['line_pct']}%" if cvg else "no coverage evidence: " + "; ".join(notes))

        # regression + failure intelligence
        changed_paths = {c["path"] for c in changes} | set(extra)
        hist = {}
        for h in store.listing("test_run", run["repository_id"], 15):
            if h["id"] != rid and h.get("commit_ref") == run["commit_ref"]:
                for r in h.get("results", []): hist.setdefault(r["name"], set()).add(r["status"])
        for r in results: hist.setdefault(r["name"], set()).add(r["status"])
        base_status = {r["name"]: r["status"] for r in base["results"]} if base else {}
        run["failure_groups"] = fa.analyze(results, files, changed_paths, base_status, {k: v for k, v in hist.items() if v & {"passed"} and v & {"failed", "error"}},
                                    {t["path"]: [x["path"] for x in t["sources"]] for t in disc["test_files"]})
        run["regression"] = regression.analyze(results, base["results"] if base else [], changes, disc, extra) if (changes or base or extra) else None
        # fintech simulations
        if req.get("include_fintech"):
            run["fintech"] = fintech.run_simulations("reference")
            store.put("fintech_sim", rid, run["fintech"], run["repository_id"])
            _event(run, "fintech", f"{run['fintech']['summary']['passed']}/{run['fintech']['summary']['total']} scenarios passed")
        security = req.get("security_findings")
        if security is None:
            try: security = providers.security.findings(run["repository_id"], run["commit_ref"]) or None
            except Exception: security = None
        run["quality_gate"] = quality_gate.evaluate({**run, "status": final[0], "status_reason": final[1]}, run["failure_groups"], run["regression"], cvg, run["changed_coverage"], run["fintech"], security,
                                                    req.get("critical_tests") or [], req.get("thresholds"))
        store.put("run_evidence", rid, {"reports": {e: open(os.path.join(ws.path, e), errors="replace").read(2_000_000) for e in evid if os.path.isfile(os.path.join(ws.path, e))}
                                          | ({cpath: open(os.path.join(ws.path, cpath), errors="replace").read(2_000_000)} if cpath else {})}, run["repository_id"])
        _event(run, "quality_gate", f"verdict: {run['quality_gate']['verdict']}")
    except Exception as e:
        run["status"], run["status_reason"] = "error", f"infrastructure failure: {type(e).__name__}: {e}"
        final = None
        run["events"].append({"ts": store.now(), "phase": "error", "message": run["status_reason"], "trace": traceback.format_exc()[-1500:]})
        if not run.get("quality_gate"):
            from . import quality_gate as qg
            run["quality_gate"] = qg.evaluate(run, [], None, None, None)
    finally:
        if final: run["status"], run["status_reason"] = final
        run["finished_at"] = store.now()
        run["duration_seconds"] = round(sum(c.get("duration_seconds", 0) for c in run["commands"]), 3)
        store.put("test_run", rid, run, run["repository_id"])
        if ws and ws.cleanup and not os.environ.get("FORGEX_KEEP_WORKSPACE"): ws.cleanup()

# ------------------------------------------------------------ history
def run_summary(r):
    return {k: r.get(k) for k in ("id", "repository_id", "commit_ref", "status", "status_reason", "command", "created_at", "finished_at", "duration_seconds", "summary", "base_run_id")} | \
           {"verdict": (r.get("quality_gate") or {}).get("verdict"), "line_pct": ((r.get("coverage") or {}).get("total") or {}).get("line_pct"), "environment": r.get("environment")}

def compare_runs(a, b):
    ra, rb = {x["name"]: x for x in a["results"]}, {x["name"]: x for x in b["results"]}
    rows, ch = [], {"new_failures": [], "fixed": [], "added": [], "removed": [], "slower": []}
    for n in sorted(set(ra) | set(rb)):
        x, y = ra.get(n), rb.get(n)
        if x and y:
            if x["status"] != y["status"]:
                rows.append({"name": n, "a": x["status"], "b": y["status"]})
                if y["status"] in ("failed", "error") and x["status"] == "passed": ch["new_failures"].append(n)
                if x["status"] in ("failed", "error") and y["status"] == "passed": ch["fixed"].append(n)
            if y["duration"] > max(0.5, 2 * x["duration"]): ch["slower"].append({"name": n, "a": x["duration"], "b": y["duration"]})
        elif y: ch["added"].append(n)
        else: ch["removed"].append(n)
    return {"a": run_summary(a), "b": run_summary(b), "status_changes": rows, **ch,
            "summary_delta": {k: b["summary"][k] - a["summary"][k] for k in a["summary"]},
            "coverage": cov.compare(a.get("coverage"), b.get("coverage")), "verdict": [(a.get("quality_gate") or {}).get("verdict"), (b.get("quality_gate") or {}).get("verdict")]}

# ------------------------------------------------------------ generation review
def generate_tests(req):
    repo = providers.repository.resolve(req.get("repository_id"), req.get("repo_path"))
    changes = list(req.get("changes") or [])
    if not changes and req.get("base_ref"): changes = git_changes(repo.path, req["base_ref"])
    if not changes and req.get("change_set_id"): changes = providers.workspace.create(repo, req["change_set_id"]).changes
    if not changes: raise ValueError("no changes to generate from: pass changes, base_ref (git) or change_set_id (Module 03)")
    disc = discovery.discover(repo.path)
    props, skipped = generation.generate(repo.id, repo.path, changes, disc["conventions"])
    for p in props: store.put("test_proposal", p["id"], p, repo.id)
    return {"proposals": props, "skipped": skipped}

def review_proposal(pid, decision, reviewer="", note=""):
    p = store.get("test_proposal", pid)
    if not p: raise KeyError(pid)
    if decision not in ("approve", "reject"): raise ValueError("decision must be approve or reject")
    if decision == "approve":
        if not p["validation"]["ok"]: raise ValueError("proposal failed validation: " + "; ".join(p["validation"]["issues"]))
        repo = providers.repository.resolve(p["repository_id"])
        target = (Path(repo.path) / p["test_path"]).resolve()
        if not str(target).startswith(str(Path(repo.path).resolve()) + os.sep): raise ValueError("test path escapes the repository")
        if target.exists(): raise ValueError(f"{p['test_path']} already exists; refusing to overwrite")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(p["code"])
        p["status"] = "applied"
    else:
        p["status"] = "rejected"
    p["reviews"].append({"decision": decision, "reviewer": reviewer, "note": note, "at": store.now()})
    return store.put("test_proposal", pid, p, p["repository_id"])
