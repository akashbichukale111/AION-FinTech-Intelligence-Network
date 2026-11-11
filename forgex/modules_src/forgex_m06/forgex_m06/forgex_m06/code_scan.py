"""Repository intelligence adapter (Module 01 contract, local implementation): locates payment services,
transaction handlers, models, ledgers, webhooks, retry logic, idempotency mechanisms and tests."""
from __future__ import annotations
import os, re
from pathlib import Path

EXT = {".py", ".js", ".ts", ".java", ".go", ".rb", ".kt", ".cs", ".php", ".rs"}
SKIP = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", "target", ".next", ".tox"}
CATS = {
    "payment_service": r"(class|def|function|func)\s+\w*(payment|charge|checkout|capture|authori[sz]e)\w*",
    "transaction_handler": r"(class|def|function|func)\s+\w*(transaction|transfer|settle)\w*|@transactional|BEGIN\s+TRANSACTION|\.transaction\(|atomic\(",
    "db_model": r"class\s+\w+\((?:\w+\.)?(Base|Model|db\.Model)\)|CREATE\s+TABLE|@Entity|models\.Model|__tablename__",
    "ledger": r"\b(ledger|journal[_ ]?entr\w*|double[- _]?entry|debit|credit)\b",
    "webhook": r"webhook",
    "retry": r"\b(retry|retries|backoff|max_attempts|tenacity|retryable)\b",
    "idempotency": r"idempot|idem_key|Idempotency-Key|request_id\s*(==|in)",
}
RISKS = {
    "float_money": (r"\bfloat\s*\(|:\s*float\b.*(amount|price|total|balance|fee)|(amount|price|balance|total|fee)\w*\s*[:=]\s*float", "Floating point used for monetary values"),
    "retry_without_backoff": None, "webhook_without_dedup": None, "mutation_without_idempotency": None, "ledger_without_transaction": None,
}
SECRET = re.compile(r"(sk_live_\w+|AKIA[0-9A-Z]{12,}|(?i:secret|password|api[_-]?key)\s*[:=]\s*['\"][^'\"]{6,}['\"])")
PAN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def redact(s: str) -> str:
    return PAN.sub("[REDACTED-NUMBER]", SECRET.sub("[REDACTED-SECRET]", s))[:160]


def scan(path: str, allowed_roots: list[str] | None = None, max_files=4000) -> dict:
    root = Path(path).expanduser().resolve()
    if allowed_roots is not None and not any(str(root).startswith(str(Path(a).resolve())) for a in allowed_roots):
        raise ValueError("repository path is outside the allowed roots (set FORGEX_REPO_ROOTS)")
    if not root.is_dir():
        raise ValueError("repository path does not exist or is not a directory")
    cats: dict[str, list] = {k: [] for k in CATS}
    tests, files_scanned = [], 0
    per_file: dict[str, set] = {}
    has = {"backoff": False, "dedup": False, "txn": False}
    compiled = {k: re.compile(v, re.I) for k, v in CATS.items()}
    risk_hits = {k: [] for k in RISKS}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in fn:
            fp = Path(dp) / f
            if fp.suffix not in EXT or files_scanned >= max_files:
                continue
            try:
                if fp.stat().st_size > 512_000 or fp.is_symlink():
                    continue
                text = fp.read_text(errors="ignore")
            except OSError:
                continue
            files_scanned += 1
            rel = str(fp.relative_to(root))
            is_test = bool(re.search(r"(^|/)(tests?|__tests__|spec)/|test_|_test\.|\.test\.|\.spec\.", rel))
            found = set()
            for i, line in enumerate(text.splitlines(), 1):
                for c, rx in compiled.items():
                    if rx.search(line):
                        found.add(c)
                        if not is_test and len(cats[c]) < 60:
                            cats[c].append({"path": rel, "line": i, "text": redact(line.strip())})
                if not is_test and re.search(RISKS["float_money"][0], line, re.I) and len(risk_hits["float_money"]) < 20:
                    risk_hits["float_money"].append({"path": rel, "line": i, "text": redact(line.strip())})
            if is_test:
                tests.append({"path": rel, "covers": sorted(found)})
            else:
                per_file[rel] = found
                low = text.lower()
                has["backoff"] |= bool(re.search(r"backoff|jitter|sleep\(|delay", low)) and "retry" in found
                has["dedup"] |= bool(re.search(r"event_id|processed_events|already_processed|unique", low)) and "webhook" in found
                has["txn"] |= bool(re.search(r"transaction|atomic|commit|begin", low)) and "ledger" in found
    signals = []

    def sig(code, sev, msg, hits):
        signals.append({"code": code, "severity": sev, "message": msg, "evidence": hits[:5], "source": "static_heuristic", "confidence": 0.5})
    if risk_hits["float_money"]:
        sig("STATIC_FLOAT_MONEY", "high", RISKS["float_money"][1], risk_hits["float_money"])
    if cats["retry"] and not has["backoff"]:
        sig("STATIC_RETRY_WITHOUT_BACKOFF", "medium", "Retry logic found with no backoff/delay/jitter keywords", cats["retry"])
    if cats["webhook"] and not has["dedup"]:
        sig("STATIC_WEBHOOK_WITHOUT_DEDUP", "high", "Webhook handlers found with no event-id deduplication keywords", cats["webhook"])
    if cats["payment_service"] and not cats["idempotency"]:
        sig("STATIC_NO_IDEMPOTENCY", "high", "Payment services found but no idempotency mechanism detected", cats["payment_service"])
    if cats["ledger"] and not has["txn"]:
        sig("STATIC_LEDGER_NO_TRANSACTION", "medium", "Ledger code found with no transaction/atomic keywords", cats["ledger"])
    return {"root": str(root), "files_scanned": files_scanned, "categories": cats, "category_counts": {k: len(v) for k, v in cats.items()},
            "tests": tests[:100], "static_signals": signals, "note": "Static keyword heuristics - not proof of defects. Read-only scan; nothing is modified."}
