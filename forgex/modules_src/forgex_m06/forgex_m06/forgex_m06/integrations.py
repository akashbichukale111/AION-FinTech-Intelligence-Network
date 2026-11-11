"""Integration contracts with FORGEX Modules 01-05 and the Module 07 interface.
Modules 01-05 were not present in this workspace, so each contract has (a) a typed port and (b) a local
implementation that persists/produces the exact payload the module should receive. If FORGEX_M02_URL
is set, tasks are additionally POSTed there. Nothing here changes a production system."""
from __future__ import annotations
import json, os, textwrap, urllib.request
from typing import Protocol
from . import code_scan


class RepoIntelligencePort(Protocol):          # Module 01
    def scan(self, path: str) -> dict: ...


class TaskPort(Protocol):                      # Module 02
    def create_task(self, finding: dict) -> dict: ...


class PatchPort(Protocol):                     # Module 03
    def prepare(self, task: dict) -> dict: ...


class MonitorPort(Protocol):                   # Module 04
    def spec(self, scenario_id: str, interval_minutes: int) -> dict: ...


class ValidationPort(Protocol):                # Module 05
    def request(self, task: dict) -> dict: ...


class GuardianPort(Protocol):                  # Module 07 (interface only - not implemented here)
    def signals(self, run_id: str) -> dict: ...


class LocalRepo:
    def __init__(self):
        roots = os.environ.get("FORGEX_REPO_ROOTS")
        self.roots = [r for r in roots.split(os.pathsep) if r] if roots else None

    def scan(self, path):
        return code_scan.scan(path, self.roots)


PLAN = {
    "idempotency": ["Add/verify an Idempotency-Key header on every mutating endpoint", "Persist (key, request fingerprint, state, response) with a UNIQUE constraint on key", "Reserve the key atomically BEFORE executing; replay stored response on repeats; 422 on payload mismatch", "Give in-progress records a lease and a recovery path"],
    "ledger": ["Validate sum(debits)==sum(credits) before commit", "Quantize and allocate money with Decimal (largest remainder)", "Post journals + state change in one DB transaction; UNIQUE(txn_id, kind)"],
    "default": ["Reproduce with the stored scenario and seed", "Implement the recommended fix behind a feature flag", "Add a regression test that replays the scenario against the fixed implementation"],
}


def repair_plan(f):
    fam = "idempotency" if "IDEMP" in f["failure_type"] or "DUPLICATE" in f["failure_type"] else "ledger" if f["failure_type"].startswith("LEDGER") else "default"
    files = sorted({r["path"] for r in f["affected_code_refs"] if r["source"] == "repository"})
    return {"summary": f["recommended_fix"], "steps": PLAN[fam] + PLAN["default"], "candidate_files": files,
            "simulation_model_refs": [r for r in f["affected_code_refs"] if r["source"] == "simulation_model"],
            "acceptance_criteria": [f"Replaying scenario {f['scenario_id']} (seed {f['reproduction_steps'][0].split('seed')[-1][:6].strip(' \":}')}) reports no {f['failure_type']} finding", "Decimal ledger invariants hold for the replay", "No change is auto-applied to production; human approval required"]}


def regression_test_source(f, cfg) -> str:
    return textwrap.dedent(f'''\
        # Auto-generated regression spec for {f["failure_type"]} ({f["finding_id"]}) - SYNTHETIC simulation replay.
        from forgex_m06.service import Lab

        def test_{f["failure_type"].lower()}_fixed():
            lab = Lab()
            cfg = {json.dumps({k: cfg[k] for k in ("type", "seed", "params", "profile")}, indent=4)}
            cfg["implementation"] = {json.dumps(f["fix_overrides"])}
            run = lab.run(config=cfg)
            assert "{f["failure_type"]}" not in {{x["failure_type"] for x in run["findings"]}}
        ''')


class LocalPorts:
    def create_task(self, f, cfg):
        t = {"task_id": f"TASK-{f['finding_id']}", "finding_id": f["finding_id"], "run_id": f["run_id"], "title": f"[{f['severity'].upper()}] {f['title']} ({f['failure_type']})",
             "priority": {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}[f["severity"]], "status": "draft", "classification": "SIMULATED_FINDING",
             "repair_plan": repair_plan(f),
             "patch_request": {"target": "module_03", "mode": "isolated_worktree", "auto_apply": False, "requires_human_approval": True, "production_changes": False},
             "validation_plan": {"target": "module_05", "scenario": f["scenario_id"], "seed": cfg["seed"], "fix_overrides": f["fix_overrides"], "regression_test": regression_test_source(f, cfg)},
             "module_sync": {"module_02": {"synced": False, "reason": "FORGEX_M02_URL not configured; stored locally with the contract payload"}}}
        url = os.environ.get("FORGEX_M02_URL")
        if url:
            try:
                rq = urllib.request.Request(url, data=json.dumps(t, default=str).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(rq, timeout=5) as r:
                    t["module_sync"]["module_02"] = {"synced": True, "status": r.status}
            except Exception as e:  # noqa: BLE001
                t["module_sync"]["module_02"] = {"synced": False, "reason": f"{type(e).__name__}: {e}"}
        return t

    @staticmethod
    def monitor_spec(scenario_id, interval):
        return {"target": "module_04", "monitor_type": "fintech_reliability_replay", "scenario_id": scenario_id, "interval_minutes": interval,
                "action": {"method": "POST", "path": "/api/v1/fintech-lab/runs", "body": {"scenario_id": scenario_id, "triggered_by": "module_04"}},
                "alert_when": "findings.critical > 0 OR run.digest changes for an unchanged config", "synthetic_only": True}


def guardian_signals(run, findings) -> dict:
    """Stable contract for Module 07 (API, Webhook & Payment Guardian)."""
    fam = {"DUPLICATE_WEBHOOK_PROCESSING": "webhook", "IDEMPOTENCY": "api", "RETRY": "api", "SETTLEMENT": "settlement", "LEDGER": "ledger"}
    out = []
    for f in findings:
        ft = f["failure_type"]
        surface = next((v for k, v in fam.items() if k in ft), "payment")
        out.append({"signal_id": f["finding_id"], "surface": surface, "failure_type": ft, "severity": f["severity"], "confidence": f["confidence"],
                    "recommended_guard": f["fix_overrides"], "evidence_run": run["run_id"], "simulated": True})
    return {"contract_version": "1.0", "run_id": run["run_id"], "digest": run["digest"], "simulated": True, "signals": out,
            "guard_hints": sorted({"require_idempotency_key" if "IDEMP" in s["failure_type"] or "DUPLICATE" in s["failure_type"] else "dedupe_webhook_event_id" if "WEBHOOK" in s["failure_type"] else "bounded_retry_with_jitter" if "RETRY" in s["failure_type"] else "atomic_ledger_posting" for s in out})}
