"""Lab service: orchestrates simulation, analysis, persistence and module integrations."""
from __future__ import annotations
import os, re, threading
from collections import Counter
from decimal import Decimal
from . import scenarios as SC
from .findings import Collector, CAT, LEDGER_MAP, DISCLAIMER
from .gateway import Impl, HARDENED
from .integrations import LocalRepo, LocalPorts, guardian_signals
from .ledger import validate_ledger
from .money import contains_pan, digest, jsonable, canon
from .retry import analyze_policy, RetryPolicy
from .store import Store, now

SAFETY = {"synthetic_data_only": True, "live_payment_execution": False, "real_accounts_accessible": False, "decimal_only_money": True,
          "deterministic": True, "notice": "All transactions, ledgers and settlement files in this lab are SYNTHETIC simulation data. Findings are simulation results, not confirmed production incidents, fraud or financial loss. No regulatory certification is claimed."}


class Lab:
    def __init__(self, db_path: str | None = None):
        self.store = Store(db_path or os.environ.get("FORGEX_LAB_DB", ":memory:"))
        self.repo, self.ports = LocalRepo(), LocalPorts()
        self.lock, self._repo_cache = threading.RLock(), {}
        for c in SC.library_configs():
            c = dict(c)
            sid, builtin = c.pop("scenario_id"), c.pop("builtin")
            self.store.put_scenario(sid, c["name"], c["type"], c, builtin=True)

    # ------------------------------------------------------------ meta / scenarios
    def meta(self):
        return {"safety": SAFETY, "scenario_types": SC.public_specs(), "implementation_flags": Impl().to_dict(), "profiles": ["scenario_default", "hardened", "naive", "custom"],
                "naive_profile": SC.NAIVE.to_dict(), "hardened_profile": SC.HARDENED.to_dict(), "failure_types": {k: {"severity": v[0], "title": v[1]} for k, v in CAT.items()}}

    def create_scenario(self, cfg: dict) -> dict:
        cfg = dict(cfg)
        cfg.pop("scenario_id", None), cfg.pop("builtin", None)
        norm = SC.normalize_config(cfg)
        with self.lock:
            sid = f"SCN-USR-{sum(1 for s in self.store.scenarios() if not s['builtin']) + 1:03d}"
            norm["scenario_id"] = sid
            self.store.put_scenario(sid, norm["name"], norm["type"], {k: v for k, v in norm.items() if k != "scenario_id"})
        return self.store.scenario(sid)

    def scenarios(self):
        return self.store.scenarios()

    def scenario(self, sid):
        s = self.store.scenario(sid)
        if not s:
            raise KeyError(f"scenario {sid} not found")
        return s

    # ------------------------------------------------------------ run
    def _repo_index(self, path):
        if not path:
            return None
        if path not in self._repo_cache:
            self._repo_cache[path] = self.repo.scan(path)
        return self._repo_cache[path]

    def _cfg_from(self, config, scenario_id, overrides):
        if scenario_id:
            cfg = dict(self.scenario(scenario_id))
            for k in ("builtin", "created_at"):
                cfg.pop(k, None)
        elif config:
            cfg = dict(config)
        else:
            raise ValueError("provide scenario_id or config")
        o = overrides or {}
        for k in ("seed", "profile", "schedule", "repo_path", "abort_on_deadlock"):
            if o.get(k) is not None:
                cfg[k] = o[k]
        for k in ("params", "implementation"):
            if o.get(k):
                cfg[k] = {**(cfg.get(k) or {}), **o[k]}
        return cfg

    def run(self, config=None, scenario_id=None, overrides=None, triggered_by="manual", persist=True) -> dict:
        cfg = self._cfg_from(config, scenario_id, overrides)
        if cfg.get("scenario_id") is None and scenario_id:
            cfg["scenario_id"] = scenario_id
        ctx = SC.execute(cfg)
        cfg = ctx.cfg
        dg = SC.run_digest(ctx)
        with self.lock:
            run_id = self.store.next_run_id() if persist else "RUN-PREVIEW"
            findings = SC.analyze(ctx, run_id, self._repo_index(cfg.get("repo_path")))
            ev = self._evidence(run_id, ctx, findings)
            ids = [e["evidence_id"] for e in ev]
            for f in findings:
                f["evidence_refs"] = ids
            passed = sum(e["ok"] for e in ctx.exp)
            sev = Counter(f["severity"] for f in findings)
            summary = {"verdict": "FINDINGS_DETECTED" if findings else "CLEAN", "findings_total": len(findings), "by_severity": dict(sev),
                       "expectations_total": len(ctx.exp), "expectations_passed": passed, "ledger_valid": ctx.validation["valid"],
                       "failure_types": sorted({f["failure_type"] for f in findings}), "synthetic": True, "trace_events": len(ctx.trace.events)}
            result = jsonable({"implementation": ctx.impl.to_dict(), "steps": ctx.steps, "trace": ctx.trace.events, "payments": ctx.payments,
                               "journals": ctx.journals, "ledger_validation": ctx.validation, "expectations": ctx.exp, "observations": ctx.obs,
                               "charts": ctx.charts, "schedule": ctx.schedule, "detector_hits": ctx.hits, "synthetic": True, "disclaimer": DISCLAIMER})
            run = {"run_id": run_id, "scenario_id": cfg.get("scenario_id") or "ADHOC", "name": cfg["name"], "type": cfg["type"], "status": "completed",
                   "seed": cfg["seed"], "digest": dg, "config": cfg, "summary": summary, "result": result, "triggered_by": triggered_by}
            if persist:
                self.store.save_run(run, findings, ev)
        return {**{k: v for k, v in run.items() if k != "result"}, "created_at": now(), "findings": findings, **{k: result[k] for k in ("implementation", "steps", "expectations", "observations", "ledger_validation", "charts", "schedule")}}

    def _evidence(self, run_id, ctx, findings):
        items = [("trace", ctx.trace.events), ("ledger_journals", ctx.journals), ("ledger_validation", ctx.validation), ("payments", ctx.payments),
                 ("expectations", ctx.exp), ("observations", {"obs": ctx.obs, "schedule": ctx.schedule, "steps": ctx.steps})]
        out = []
        for i, (kind, payload) in enumerate(items, 1):
            p = jsonable(payload)
            out.append({"evidence_id": f"EV-{run_id}-{i:02d}", "kind": kind, "sha256": digest(p), "size": len(canon(p)), "payload": p})
        return out

    def get_run(self, run_id, full=False):
        r = self.store.run(run_id)
        if not r:
            raise KeyError(f"run {run_id} not found")
        res = r.pop("result")
        if not full:
            res = {k: v for k, v in res.items() if k not in ("trace", "journals", "payments", "detector_hits")}
        return {**r, "result": res, "findings": self.store.findings(run_id), "evidence": self.store.evidence(run_id)}

    def traces(self, run_id, actor=None, op=None, limit=1000, offset=0):
        r = self.store.run(run_id)
        if not r:
            raise KeyError(f"run {run_id} not found")
        ev = r["result"]["trace"]
        if actor:
            ev = [e for e in ev if e["actor"] == actor]
        if op:
            ev = [e for e in ev if e["op"] == op]
        return {"run_id": run_id, "total": len(ev), "synthetic": True, "schedule": r["result"].get("schedule"), "events": ev[offset:offset + limit],
                "payments": r["result"]["payments"], "steps": r["result"]["steps"]}

    def replay(self, run_id):
        r = self.store.run(run_id)
        if not r:
            raise KeyError(f"run {run_id} not found")
        again = self.run(config=r["config"], persist=False)
        return {"run_id": run_id, "reproduced": again["digest"] == r["digest"], "original_digest": r["digest"], "replay_digest": again["digest"],
                "same_failure_types": sorted({f["failure_type"] for f in again["findings"]}) == r["summary"]["failure_types"]}

    # ------------------------------------------------------------ compare / overview / report
    def compare(self, ids):
        if not 2 <= len(ids) <= 6:
            raise ValueError("provide 2-6 run ids")
        runs = [self.get_run(i) for i in ids]
        base = set(runs[0]["summary"]["failure_types"])
        rows = []
        for r in runs:
            ft = set(r["summary"]["failure_types"])
            rows.append({"run_id": r["run_id"], "scenario_id": r["scenario_id"], "name": r["name"], "digest": r["digest"], "seed": r["seed"], "summary": r["summary"],
                         "implementation": r["result"]["implementation"], "params": r["config"]["params"],
                         "resolved_vs_first": sorted(base - ft), "new_vs_first": sorted(ft - base), "failed_expectations": [e["id"] for e in r["result"]["expectations"] if not e["ok"]]})
        impl_keys = sorted({k for r in rows for k in r["implementation"] if len({str(x["implementation"].get(k)) for x in rows}) > 1})
        par_keys = sorted({k for r in rows for k in r["params"] if len({str(x["params"].get(k)) for x in rows}) > 1})
        return {"runs": rows, "differing_implementation_flags": impl_keys, "differing_params": par_keys, "synthetic": True}

    def overview(self):
        runs, total = self.store.runs(8)
        fs = self.store.findings()
        sev = Counter(f["severity"] for f in fs)
        ft = Counter(f["failure_type"] for f in fs)
        types_run = {r["type"] for r in self.store.q("SELECT DISTINCT type FROM runs")}
        return {"safety": SAFETY, "counts": {"scenarios": self.store.count("scenarios"), "runs": total, "findings": len(fs), "tasks": self.store.count("tasks")},
                "findings_by_severity": dict(sev), "top_failure_types": ft.most_common(8), "recent_runs": runs,
                "coverage": {"scenario_types_total": len(SC.SPECS), "scenario_types_run": len(types_run), "never_run": sorted(set(SC.SPECS) - types_run)}}

    def report(self, run_id):
        r = self.get_run(run_id, full=True)
        fs = r["findings"]
        md = [f"# Reliability report {run_id}", f"_{DISCLAIMER}_", "", f"- Scenario: {r['scenario_id']} - {r['name']} (type `{r['type']}`)", f"- Seed: {r['seed']}  Digest: `{r['digest'][:16]}`",
              f"- Verdict: **{r['summary']['verdict']}**  Findings: {len(fs)}  Ledger valid: {r['summary']['ledger_valid']}", ""]
        for f in fs:
            md += [f"## {f['finding_id']} {f['title']} ({f['severity']}, confidence {f['confidence']})", f"- Failure type: `{f['failure_type']}`", f"- Expected: {f['expected_result']}", f"- Actual (simulated): {f['actual_result']}",
                   f"- Root-cause hypothesis: {f['root_cause_hypothesis']}", f"- Recommended fix: {f['recommended_fix']}", "- Reproduction:"] + [f"  1. {s}" for s in f["reproduction_steps"]] + [""]
        return {"run_id": run_id, "generated_at": now(), "classification": "SIMULATED_RELIABILITY_ASSESSMENT", "disclaimer": DISCLAIMER, "run": {k: r[k] for k in ("run_id", "scenario_id", "name", "type", "seed", "digest", "summary", "config")},
                "implementation": r["result"]["implementation"], "expectations": r["result"]["expectations"], "findings": fs, "ledger_validation": r["result"]["ledger_validation"],
                "evidence_registry": r["evidence"], "limitations": ["Findings describe a simulation model of the payment flow, not the production system.", "Code references to the simulation model show where the modelled behaviour lives; repository references are static keyword matches.", "No regulatory certification is claimed."], "markdown": "\n".join(md)}

    # ------------------------------------------------------------ analyzers
    def ledger_validate(self, body: dict) -> dict:
        if "journals" in body:
            js = body["journals"]
            if not isinstance(js, list) or len(js) > 5000:
                raise ValueError("journals must be a list of at most 5000 entries")
            if contains_pan(js):
                raise ValueError("payload looks like it contains real card numbers; only synthetic data is accepted")
            source = "submitted"
        elif body.get("run_id"):
            js = self.get_run(body["run_id"], full=True)["result"]["journals"]
            source = f"run:{body['run_id']}"
        else:
            kind = body.get("sample", "defective")
            if kind not in ("clean", "defective"):
                raise ValueError("sample must be clean|defective")
            js = self._sample(kind)
            source = f"synthetic-sample:{kind}"
        v = validate_ledger(js, body.get("reported_balances"))
        fs = []
        for c in v["checks"]:
            if not c["passed"]:
                ft = LEDGER_MAP[c["invariant"]]
                meta = CAT[ft]
                fs.append({"failure_type": ft, "invariant": c["invariant"], "severity": meta[0], "title": meta[1], "violations": c["violations"][:20], "root_cause_hypothesis": meta[2],
                           "recommended_fix": meta[3], "classification": "SIMULATED_FINDING", "confidence": 0.95, "confidence_basis": "exact Decimal ledger validation"})
        return {"source": source, "validation": v, "findings": fs, "synthetic": True, "disclaimer": DISCLAIMER}

    def _sample(self, kind):
        if kind == "clean":
            return SC.execute({"type": "payment_lifecycle", "params": {"refund_amount": "25.00"}}).journals
        a = SC.execute({"type": "rounding_split"}).journals
        b = SC.execute({"type": "refund_reversal", "params": {"mode": "double"}}).journals
        off = len(a)
        b = [dict(j, journal_id=f"J{off + i + 1:05d}") for i, j in enumerate(b)]
        return a + b

    PROBES = [("same_request_repeated", "duplicate_request", {"duplicates": 3}, "Same request repeated"),
              ("same_key_different_payload", "key_payload_mismatch", {}, "Same key with different payload"),
              ("concurrent_same_key", "concurrent_same_key", {"actors": 4}, "Concurrent requests using the same key"),
              ("expired_record", "expired_key", {}, "Retry after the idempotency record expired"),
              ("retry_after_timeout", "timeout_retry", {}, "Retry after timeout (response lost)"),
              ("partial_failure", "partial_failure", {}, "Partial failure during processing, then retry")]

    def idempotency_analyze(self, body: dict) -> dict:
        base = {"hardened": HARDENED.to_dict(), "naive": SC.NAIVE.to_dict()}.get(body.get("profile") or "hardened")
        if base is None:
            raise ValueError("profile must be hardened|naive")
        impl = Impl.from_dict({**base, **(body.get("implementation") or {})})
        horizon = int(body.get("client_retry_horizon", 3600))
        if not 1 <= horizon <= 604800:
            raise ValueError("client_retry_horizon must be 1..604800 ticks")
        probes = []
        for pid, typ, params, label in self.PROBES:
            p = dict(params, **({"retry_after": horizon} if pid == "expired_record" else {}))
            cfg = {"type": typ, "profile": "custom", "implementation": impl.to_dict(), "params": p, "seed": int(body.get("seed", 1))}
            ctx = SC.execute(cfg)
            bad = [e for e in ctx.exp if not e["ok"]]
            ex = SC.explore(cfg, 25) if pid == "concurrent_same_key" else None
            safe = not bad and (ex is None or ex["failing_seeds"] == 0)
            probes.append({"probe": pid, "label": label, "scenario_type": typ, "safe": safe, "expected": "; ".join(e["expected"] for e in (bad or ctx.exp)[:1]),
                           "actual": "; ".join(e["actual"] for e in (bad or ctx.exp)[:1]) if not ex else f"{ex['failing_seeds']}/{ex['seeds_tried']} interleavings produced duplicates" + (f" (first failing seed {ex['first_failure']['seed']})" if ex["first_failure"] else ""),
                           "failure_types": sorted({e["ftype"] for e in bad}), "exploration": ex, "reproduce": {"config": ctx.cfg}, "trace_events": len(ctx.trace.events), "digest": SC.run_digest(ctx)})
        static = inspect_source(body["source_text"]) if body.get("source_text") else None
        if body.get("repo_path"):
            idx = self._repo_index(body["repo_path"])
            static = {"signals": idx["static_signals"], "idempotency_locations": idx["categories"]["idempotency"][:10], "tests": [t for t in idx["tests"] if "idempotency" in t["covers"]][:10]}
        n = sum(p["safe"] for p in probes)
        return {"implementation": impl.to_dict(), "probes": probes, "score": f"{n}/{len(probes)}", "safe_count": n, "total": len(probes),
                "verdict": "SAFE_IN_SIMULATION" if n == len(probes) else "UNSAFE_IN_SIMULATION", "static_inspection": static, "synthetic": True, "disclaimer": DISCLAIMER,
                "ttl_note": None if impl.ttl is None else f"TTL {impl.ttl} ticks must exceed the client retry horizon ({horizon})."}

    CONC = {"lost_update": "lost_update", "overdraft": "concurrent_payments", "concurrent_same_key": "concurrent_same_key", "deadlock": "deadlock", "state_race": "state_race"}

    def concurrency_simulate(self, body: dict) -> dict:
        sc = body.get("scenario")
        if sc not in self.CONC:
            raise ValueError(f"scenario must be one of {sorted(self.CONC)}")
        impl = dict(body.get("implementation") or {})
        mode = body.get("mode")
        if mode:
            if sc == "deadlock":
                impl["deadlock_ordering"] = mode in ("ordered", "safe")
            elif sc == "concurrent_same_key":
                impl["atomic_reserve"] = mode in ("atomic", "safe")
            else:
                if mode not in ("none", "lock", "optimistic"):
                    raise ValueError("mode must be none|lock|optimistic (deadlock: ordered|unordered, same-key: atomic|check_then_act)")
                impl["locking"] = mode
        params = dict(body.get("params") or {})
        if body.get("actors"):
            params["actors"] = body["actors"]
        cfg = {"type": self.CONC[sc], "seed": body.get("seed", 1), "profile": "scenario_default", "implementation": impl, "params": params, "schedule": body.get("schedule"),
               "abort_on_deadlock": bool(body.get("abort_on_deadlock", False)), "name": f"Concurrency lab: {sc}"}
        run = self.run(config=cfg, triggered_by="concurrency_api")
        ex = SC.explore({k: v for k, v in cfg.items() if k != "schedule"}, int(body["explore"])) if body.get("explore") else None
        tr = self.traces(run["run_id"])
        return {"run": run, "trace": tr["events"], "schedule": run["schedule"], "exploration": ex, "synthetic": True,
                "reproduce": {"seed": run["seed"], "schedule": run["schedule"], "note": "POST this schedule back to replay the exact interleaving"}}

    def retry_analyze(self, body: dict) -> dict:
        return analyze_policy(body.get("policy") or {}, body.get("semantics"))

    def storm(self, body: dict) -> dict:
        params = {k: v for k, v in (body.get("params") or body).items() if k in SC.SPECS["retry_storm"]["params"]}
        return self.run(config={"type": "retry_storm", "params": params, "seed": body.get("seed", 1), "profile": body.get("profile", "scenario_default"),
                                "implementation": body.get("implementation") or {}, "name": "Retry storm analysis"}, triggered_by="retry_api")

    def settlement_simulate(self, body: dict) -> dict:
        return self.run(config={"type": "settlement", "params": body.get("params") or {}, "seed": body.get("seed", 1), "profile": body.get("profile", "scenario_default"),
                                "implementation": body.get("implementation") or {}, "name": "Settlement simulation"}, triggered_by="settlement_api")

    # ------------------------------------------------------------ repository + modules
    def repo_scan(self, path):
        idx = self.repo.scan(path)
        self._repo_cache[path] = idx
        return idx

    def create_task(self, finding_id):
        f = self.store.finding(finding_id)
        if not f:
            raise KeyError(f"finding {finding_id} not found")
        ex = self.store.task(f"TASK-{finding_id}")
        if ex:
            return ex
        t = self.ports.create_task(f, self.store.run(f["run_id"])["config"])
        t["created_at"] = now()
        self.store.save_task(t)
        return t

    def tasks(self):
        return self.store.tasks()

    def validate_task(self, task_id):
        t = self.store.task(task_id)
        if not t:
            raise KeyError(f"task {task_id} not found")
        run = self.store.run(t["run_id"])
        ft = [f for f in self.store.findings(t["run_id"]) if f["finding_id"] == t["finding_id"]][0]["failure_type"]
        ov = {"implementation": t["validation_plan"]["fix_overrides"]}
        if ft in ("RETRY_STORM", "DEAD_LETTER_MISSING") or (ft == "RETRY_DUPLICATE_RISK" and not ov["implementation"]):
            ov["params"] = SC.SPECS["retry_storm"]["hardened_params"]
        if ft.startswith("SETTLEMENT") or ft.startswith("REFUND_SETTLEMENT"):
            v = {"validated": None, "reason": "Settlement anomalies originate in the processor feed; the simulated fix can only harden the importer (settlement_dedup/batch_atomic)."}
            if ft in ("SETTLEMENT_DUPLICATE_ENTRY", "SETTLEMENT_PARTIAL_FAILURE"):
                v = None
        else:
            v = None
        if v is None:
            if not ov["implementation"] and "params" not in ov:
                v = {"validated": None, "reason": "No automatic simulation fix is defined for this failure type."}
        if v is None:
            again = self.run(config=run["config"], overrides=ov, triggered_by=f"module05_validation:{task_id}")
            cleared = ft not in {f["failure_type"] for f in again["findings"]}
            v = {"validated": cleared, "validation_run": again["run_id"], "remaining_failure_types": again["summary"]["failure_types"], "applied_overrides": ov,
                 "scope": "simulation model only - repository regression tests must still be executed through Module 05"}
        t["validation_result"], t["status"] = v, ("validated_in_simulation" if v.get("validated") else "fix_insufficient" if v.get("validated") is False else t["status"])
        self.store.save_task(t)
        return t

    def monitor_spec(self, scenario_id, interval=60):
        self.scenario(scenario_id)
        return self.ports.monitor_spec(scenario_id, interval)

    def guardian(self, run_id):
        r = self.get_run(run_id)
        return guardian_signals(r, r["findings"])


def inspect_source(text: str) -> dict:
    if len(text) > 200_000:
        raise ValueError("source_text too large (max 200KB)")
    t = text.lower()
    chk = [("idempotency_key_handling", bool(re.search(r"idempot|idem_key", t)), "No idempotency key handling found"),
           ("unique_constraint_or_atomic_insert", bool(re.search(r"unique|on conflict|setnx|insert or ignore|\bnx\b", t)), "No unique constraint / atomic insert for the key"),
           ("payload_fingerprint", bool(re.search(r"hash|fingerprint|sha256|checksum", t)), "No request fingerprint comparison for key reuse"),
           ("record_expiry_policy", bool(re.search(r"ttl|expire|expiry|expires_at", t)), "No expiry policy visible (verify TTL exceeds client retry window)"),
           ("transaction_boundary", bool(re.search(r"transaction|atomic|commit|begin", t)), "No transaction boundary visible around state change + key record"),
           ("in_progress_recovery", bool(re.search(r"in_progress|lease|locked_at|processing", t)), "No in-progress / lease handling visible"),
           ("float_money", not re.search(r"\bfloat\b", t), "float appears in code that may handle money")]
    return {"checks": [{"check": c, "present": ok, "message": None if ok else m} for c, ok, m in chk], "basis": "static keyword heuristics - not proof", "confidence": 0.5}
