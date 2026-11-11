"""FORGEX Module 09 configuration. Everything is overridable via environment variables."""
import json, os

def _i(k, d): return int(os.getenv(k, str(d)))

MAX_BATCH = _i("M09_MAX_BATCH", 5000)            # records per ingest request
MAX_BODY = _i("M09_MAX_BODY", 8_000_000)         # bytes per request
RETENTION_ROWS = _i("M09_RETENTION_ROWS", 400_000)  # per telemetry table (bounded storage)
MAX_AGE_DAYS = _i("M09_MAX_AGE_DAYS", 30)
FUTURE_SKEW_S = 300
RATE_PER_MIN = _i("M09_RATE_PER_MIN", 600)       # requests / key / minute

THRESHOLDS = {
    "bucket_s": 60, "min_points": 8, "z": 4.0, "min_requests": 5, "min_errors": 2,
    "error_rate": 0.05, "latency_p95_ms": 500.0, "cpu": 90.0, "memory": 90.0,
    "db_pool": 0.9, "queue": 1000.0, "pattern_count": 3,
    "deploy_window_s": 1800, "link_gap_s": 900,
}
try: THRESHOLDS.update(json.loads(os.getenv("M09_THRESHOLDS", "{}")))
except ValueError: pass

FINTECH_RE = r"payment|checkout|ledger|settle|refund|webhook|recon|billing|payout"

SLO_DEFAULT = {"availability": 99.9, "latency_ms": 500, "latency_target": 99.0, "error_rate_pct": 1.0}

def slo_for(service):
    cfg = dict(SLO_DEFAULT)
    p = os.getenv("M09_SLO_FILE")
    if p and os.path.exists(p):
        try:
            allc = json.load(open(p))
            cfg.update(allc.get("default", {})); cfg.update(allc.get(service, {}))
        except (OSError, ValueError): pass
    return cfg
