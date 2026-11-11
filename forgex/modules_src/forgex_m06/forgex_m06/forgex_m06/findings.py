"""Reliability findings engine: turns failed expectations, detector hits and ledger violations into
evidence-backed findings. Every finding is a SIMULATED result, never a production incident."""
from __future__ import annotations
import inspect
from . import gateway, retry, settlement, sched
from .money import jsonable

DISCLAIMER = "SIMULATED finding on synthetic data - not a confirmed production incident, fraud or financial loss."

# ftype: (severity, family, title, root-cause hypothesis, fix, fix_overrides, code symbols, repo categories)
CAT = {
 "DUPLICATE_PAYMENT_EXECUTION": ("critical", "Duplicate payment execution", "Payment creation is not idempotent for the same business order, so a retry/duplicate request executes a second charge.", "Require an Idempotency-Key on every mutating call; reserve the key atomically before execution and replay the stored response for repeats.", {"idempotency": "key_payload", "atomic_reserve": True}, ["create_payment"], ["idempotency", "payment_service"]),
 "MISSING_IDEMPOTENCY_PROTECTION": ("high", "Missing idempotency protection", "The endpoint ignores or lacks idempotency keys; every repeat is treated as a brand-new request.", "Add idempotency-key handling (unique constraint on key + request fingerprint) in front of the payment handler.", {"idempotency": "key_payload", "atomic_reserve": True}, ["create_payment"], ["idempotency", "payment_service"]),
 "IDEMPOTENCY_PAYLOAD_MISMATCH_ACCEPTED": ("high", "Key reuse with different payload accepted", "Key is matched without comparing a request fingerprint, so a different payload silently receives the first response (or executes again).", "Store a canonical hash of the payload with the key and return 422 when the same key arrives with a different payload.", {"idempotency": "key_payload"}, ["create_payment"], ["idempotency"]),
 "IDEMPOTENCY_KEY_EXPIRY_DUPLICATE": ("high", "Idempotency record expired before retry window ended", "Idempotency TTL is shorter than the client's retry horizon, so a late retry looks new and executes again.", "Keep keys longer than the maximum client retry window (24h+), or never expire records for financial mutations.", {"ttl": None}, ["create_payment"], ["idempotency"]),
 "IDEMPOTENCY_CONCURRENT_RACE": ("critical", "Concurrent requests with the same key both executed", "Check-then-act on the key: the key is only written after execution, so concurrent requests pass the check together.", "Reserve the key with an atomic insert (unique index / SETNX) BEFORE executing; losers get 409 or the replayed response.", {"atomic_reserve": True}, ["create_payment"], ["idempotency"]),
 "IDEMPOTENCY_STUCK_IN_PROGRESS": ("medium", "Crashed request leaves key stuck in-progress", "In-progress records have no lease/recovery, so retries receive 409 forever after a crash.", "Give in-progress records a lease; after expiry resume from the persisted payment state or roll back.", {"in_progress_policy": "resume"}, ["create_payment"], ["idempotency"]),
 "PARTIAL_FAILURE_ORPHAN_STATE": ("high", "Partial failure leaves orphaned state", "Authorize/capture/ledger writes are not in one transaction, so a crash mid-request leaves holds or captures with no journal.", "Wrap state change + ledger posting (+ idempotency record) in a single DB transaction, or use an outbox with a recovery sweeper.", {"atomic_ledger": True}, ["_execute"], ["payment_service", "transaction_handler"]),
 "REPEATED_CAPTURE": ("critical", "Repeated capture on one authorization", "Capture does not enforce the authorized -> captured transition, so each call captures and posts again.", "Enforce the state machine under a row lock (SELECT ... FOR UPDATE) and make capture idempotent per authorization.", {"state_guard": True, "locking": "lock"}, ["capture"], ["payment_service", "ledger"]),
 "DUPLICATE_REFUND_EXECUTION": ("critical", "Duplicate refund execution", "Refund requests carry no idempotency key that the service honours.", "Persist refund keys with a unique constraint; replay the original refund response on repeats.", {"refund_guard": True}, ["refund"], ["payment_service", "ledger"]),
 "OVER_REFUND": ("high", "Refunds exceed captured amount", "No cumulative refunded <= captured check.", "Check cumulative refunds against captured amount inside the same transaction as the refund write.", {"refund_guard": True}, ["refund"], ["payment_service", "ledger"]),
 "DUPLICATE_WEBHOOK_PROCESSING": ("high", "Duplicate webhook processing", "Webhook handler is not deduplicated on event_id; at-least-once delivery applies the effect repeatedly.", "Insert event_id into a unique table first (same transaction as the effect); ignore conflicts.", {"webhook_dedup": True}, ["deliver_webhook"], ["webhook"]),
 "LEDGER_IMBALANCE": ("critical", "Unbalanced journal entry", "A posting path creates debit and credit legs that do not sum to the same amount (rounding, dropped leg).", "Validate sum(debits)==sum(credits) before commit; allocate remainders with largest-remainder; add DB constraint/trigger.", {"rounding": "allocate", "reversal_correct": True}, ["split_payment", "reverse_capture", "post_capture"], ["ledger"]),
 "LEDGER_JOURNAL_INCONSISTENT": ("high", "Malformed journal entry", "Journal lines are negative, zero-value, two-sided or missing an account.", "Validate journal structure before commit; add CHECK constraints.", {}, ["post_capture"], ["ledger"]),
 "LEDGER_ROUNDING_PRECISION": ("high", "Money not quantized to currency precision", "Division results are stored without quantizing/allocating to 2 decimal places.", "Use Decimal with explicit quantize + allocate remainders; reject amounts with excess precision.", {"rounding": "allocate"}, ["split_payment"], ["ledger"]),
 "LEDGER_DUPLICATE_POSTING": ("critical", "Identical journal posted more than once", "Posting is not unique per business transaction.", "Unique constraint on (txn_id, kind) for journals.", {}, ["post_capture"], ["ledger"]),
 "LEDGER_REVERSAL_INCORRECT": ("high", "Reversal is not an exact mirror / reversed twice", "Reversal builds its own lines instead of mirroring the original and is not guarded against repeats.", "Generate reversals by swapping debit/credit of the original journal; enforce single reversal per journal.", {"reversal_correct": True, "state_guard": True}, ["reverse_capture"], ["ledger"]),
 "LEDGER_RECONCILIATION_FAILURE": ("high", "Account balances do not reconcile", "Journals changed balances inconsistently.", "Recompute balances from journals and alert on drift.", {}, ["post_capture"], ["ledger"]),
 "LEDGER_NON_ATOMIC": ("high", "Partial journal visible", "Journal written outside an atomic transaction.", "Commit journal and payment state together.", {"atomic_ledger": True}, ["_execute"], ["ledger"]),
 "LOST_UPDATE": ("critical", "Lost update on shared balance", "Read-modify-write without lock or version check.", "Use row locks, atomic UPDATE ... SET bal = bal + :x, or optimistic version checks with retry.", {"locking": "lock"}, [], ["ledger", "db_model"]),
 "OVERDRAFT_RACE": ("critical", "Concurrent debits overdraw balance", "Balance check and debit are separate steps (write skew / check-then-act).", "Do check+debit atomically (conditional UPDATE ... WHERE balance >= x) or serializable isolation.", {"locking": "lock"}, [], ["ledger", "db_model"]),
 "DEADLOCK": ("high", "Deadlock between concurrent transfers", "Transactions acquire account locks in different orders.", "Acquire locks in a global order (e.g. by account id); add lock timeouts and bounded retry on deadlock victims.", {"deadlock_ordering": True}, [], ["ledger", "transaction_handler"]),
 "INCONSISTENT_STATE_TRANSITION": ("high", "Conflicting state transitions both succeeded", "Capture and void each validate state from a stale read without locking.", "Row lock / compare-and-swap on state version so only one transition wins.", {"locking": "lock", "state_guard": True}, ["capture", "void"], ["payment_service", "transaction_handler"]),
 "RETRY_STORM": ("high", "Retry storm amplifies load", "Clients retry with constant/low delay and no jitter, multiplying load during an outage.", "Exponential backoff + full jitter, retry budget, circuit breaker, bounded attempts.", {}, ["simulate_storm"], ["retry"]),
 "RETRY_DUPLICATE_RISK": ("critical", "Retries re-execute committed operations", "Timeouts after commit are retried against a non-idempotent endpoint.", "Idempotency key + status lookup before retry.", {"idempotency": "key_payload", "atomic_reserve": True}, ["simulate_storm"], ["retry", "idempotency"]),
 "DEAD_LETTER_MISSING": ("high", "Exhausted retries are dropped", "No dead-letter queue: work that exhausted retries disappears.", "Park exhausted requests (key, fingerprint, last error) in a DLQ with alerting and a replay tool.", {}, ["simulate_storm"], ["retry"]),
 "SETTLEMENT_MISMATCH": ("high", "Settlement amount differs from ledger", "Fee/rounding differences between internal ledger and processor file.", "Reconcile per transaction with tolerance rules; alert on variance; store processor reference ids.", {}, ["reconcile"], ["ledger"]),
 "SETTLEMENT_DELAYED": ("medium", "Settlement later than expected cutoff", "Settlement arrives after the cutoff and is not tracked as in-transit.", "Track expected settlement date; age in-transit items and alert.", {}, ["reconcile"], ["ledger"]),
 "SETTLEMENT_MISSING_RECORD": ("high", "Captured payment absent from settlement", "Missing records are not detected until manual review.", "Daily reconciliation with exceptions queue.", {}, ["reconcile"], ["ledger"]),
 "SETTLEMENT_DUPLICATE_ENTRY": ("critical", "Duplicate settlement entry", "Importer is not idempotent on (reference, type).", "Unique constraint on processor reference; dedup on import.", {"settlement_dedup": True}, ["import_feed"], ["ledger", "webhook"]),
 "SETTLEMENT_PARTIAL_FAILURE": ("high", "Settlement batch partially applied", "Batch import is not all-or-nothing and does not record a resume point.", "Import in a single transaction or checkpoint by row and resume.", {"batch_atomic": True}, ["import_feed"], ["ledger"]),
 "REFUND_SETTLEMENT_INCONSISTENT": ("high", "Refund settles with wrong sign/amount", "Refund legs are not netted consistently with capture legs.", "Reconcile refunds separately with explicit sign convention.", {}, ["reconcile"], ["ledger"]),
 "SETTLEMENT_UNEXPECTED_RECORD": ("medium", "Settlement line with no internal transaction", "Processor feed contains references unknown internally.", "Route unknown references to an exceptions queue.", {}, ["reconcile"], ["ledger"]),
}
LEDGER_MAP = {"debit_credit_balance": "LEDGER_IMBALANCE", "journal_consistency": "LEDGER_JOURNAL_INCONSISTENT",
              "transaction_uniqueness": "LEDGER_DUPLICATE_POSTING", "account_balance_reconciliation": "LEDGER_RECONCILIATION_FAILURE",
              "reversal_correctness": "LEDGER_REVERSAL_INCORRECT", "rounding_and_precision": "LEDGER_ROUNDING_PRECISION",
              "atomic_transaction_behavior": "LEDGER_NON_ATOMIC"}
MODS = {"create_payment": gateway, "_execute": gateway, "capture": gateway, "void": gateway, "refund": gateway,
        "deliver_webhook": gateway, "split_payment": gateway, "reverse_capture": gateway, "post_capture": gateway,
        "simulate_storm": retry, "reconcile": settlement, "import_feed": settlement}


def sim_refs(symbols):
    out = []
    for s in symbols:
        m = MODS.get(s)
        if not m:
            continue
        try:
            fn = getattr(m, s)
            out.append({"source": "simulation_model", "path": "forgex_m06/" + m.__name__.split(".")[-1] + ".py",
                        "line": inspect.getsourcelines(fn)[1], "symbol": s})
        except (OSError, AttributeError, TypeError):
            pass
    return out


def repo_refs(cats, repo_index, limit=6):
    out = []
    if not repo_index:
        return out
    for c in cats:
        for h in repo_index.get("categories", {}).get(c, [])[:3]:
            out.append({"source": "repository", "path": h["path"], "line": h["line"], "symbol": c})
    return out[:limit]


class Collector:
    """Builds de-duplicated findings for one run."""

    def __init__(self, run_id, scenario_id, steps, config, repo_index=None):
        self.run_id, self.sid, self.steps, self.config, self.repo = run_id, scenario_id, steps, config, repo_index
        self.items: dict[str, dict] = {}

    def add(self, ftype, expected, actual, evidence, trace_seq=(), confidence=0.9, basis="deterministic invariant violation in the simulation model", summary=None, family=None):
        meta = CAT[ftype]
        f = self.items.get(ftype)
        if f:
            f["evidence"].append(jsonable(evidence))
            f["trace_seq"] = sorted(set(f["trace_seq"]) | set(trace_seq))
            return f
        n = len(self.items) + 1
        f = {"finding_id": f"{self.run_id}-F{n:02d}", "run_id": self.run_id, "scenario_id": self.sid, "failure_type": ftype,
             "title": meta[1], "reproduction_steps": [f"POST /api/v1/fintech-lab/runs {{\"config\": <stored config>, \"seed\": {self.config['seed']}}} (deterministic replay)"] + list(self.steps),
             "expected_result": expected, "actual_result": actual, "root_cause_hypothesis": meta[2],
             "affected_code_refs": sim_refs(meta[5]) + repo_refs(meta[6], self.repo),
             "severity": meta[0], "confidence": confidence, "confidence_basis": basis, "recommended_fix": meta[3],
             "fix_overrides": meta[4], "evidence": [jsonable(evidence)], "trace_seq": sorted(set(trace_seq)),
             "family": family or ftype, "synthetic": True, "classification": "SIMULATED_FINDING", "disclaimer": DISCLAIMER,
             "summary": summary or actual}
        self.items[ftype] = f
        return f
