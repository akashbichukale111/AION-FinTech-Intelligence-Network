"""Double-entry ledger + integrity validator (Decimal only)."""
from __future__ import annotations
from collections import Counter, defaultdict
from decimal import Decimal
from .money import D, ZERO, MoneyError, scale, jsonable


class Ledger:
    def __init__(self):
        self.journals: list[dict] = []
        self.n = 0

    def post(self, txn_id, kind, lines, status="committed", reverses=None):
        self.n += 1
        j = {"journal_id": f"J{self.n:05d}", "txn_id": txn_id, "kind": kind, "status": status,
             "reverses": reverses, "synthetic": True,
             "lines": [{"account": a, "debit": d, "credit": c} for a, d, c in lines]}
        self.journals.append(j)
        return j

    def find(self, txn_id, kind):
        for j in self.journals:
            if j["txn_id"] == txn_id and j["kind"] == kind and j["status"] == "committed":
                return j

    def balances(self) -> dict:
        b = defaultdict(lambda: ZERO)
        for j in self.journals:
            for l in j["lines"]:
                b[l["account"]] += D(l["debit"]) - D(l["credit"])
        return dict(b)

    def snapshot(self):
        return jsonable(self.journals)


def validate_ledger(journals: list[dict], reported_balances: dict | None = None) -> dict:
    parsed, precision, floats, consistency, atomicity = [], [], [], [], []
    for j in journals:
        jid = j.get("journal_id", "?")
        lines = []
        for i, l in enumerate(j.get("lines", [])):
            try:
                d, c = D(l.get("debit", 0)), D(l.get("credit", 0))
            except MoneyError as e:
                floats.append(f"{jid}[{i}]: {e}")
                continue
            for name, v in (("debit", d), ("credit", c)):
                if scale(v) > 2:
                    precision.append(f"{jid}[{i}].{name}={v} has more than 2 decimal places")
            if d < 0 or c < 0:
                consistency.append(f"{jid}[{i}]: negative amount")
            if d > 0 and c > 0:
                consistency.append(f"{jid}[{i}]: line has both debit and credit")
            if d == 0 and c == 0:
                consistency.append(f"{jid}[{i}]: zero-value line")
            lines.append((l.get("account"), d, c))
        if len(lines) < 2:
            consistency.append(f"{jid}: fewer than 2 lines")
        if not all(a for a, _, _ in lines):
            consistency.append(f"{jid}: line without account")
        if j.get("status", "committed") != "committed":
            atomicity.append(f"{jid}: status={j.get('status')} (partial write visible in ledger, txn={j.get('txn_id')})")
        parsed.append((j, lines))

    unbalanced = []
    for j, lines in parsed:
        dr, cr = sum((d for _, d, _ in lines), ZERO), sum((c for _, _, c in lines), ZERO)
        if dr != cr:
            unbalanced.append(f"{j.get('journal_id')}: debit {dr} != credit {cr} (diff {dr - cr})")

    uniq = []
    ids = Counter(j.get("journal_id") for j, _ in parsed)
    uniq += [f"journal_id {k} appears {v} times" for k, v in ids.items() if v > 1]
    sig = Counter((j.get("txn_id"), j.get("kind"), tuple(sorted((a, str(d), str(c)) for a, d, c in ls)))
                  for j, ls in parsed if j.get("status", "committed") == "committed" and not j.get("reverses"))
    uniq += [f"identical posting for txn={k[0]} kind={k[1]} appears {v} times (duplicate posting)"
             for k, v in sig.items() if v > 1]

    bal = defaultdict(lambda: ZERO)
    for _, ls in parsed:
        for a, d, c in ls:
            bal[a] += d - c
    recon = []
    total = sum(bal.values(), ZERO)
    if total != 0:
        recon.append(f"sum of all account balances is {total}, expected 0.00")
    for a, rep in (reported_balances or {}).items():
        try:
            r = D(rep)
        except MoneyError as e:
            recon.append(f"reported balance for {a}: {e}")
            continue
        if bal.get(a, ZERO) != r:
            recon.append(f"account {a}: computed {bal.get(a, ZERO)} != reported {r}")

    rev = []
    byid = {j.get("journal_id"): ls for j, ls in parsed}
    seen = Counter()
    for j, ls in parsed:
        o = j.get("reverses")
        if not o:
            continue
        seen[o] += 1
        if o not in byid:
            rev.append(f"{j.get('journal_id')} reverses unknown journal {o}")
            continue
        if Counter((a, c, d) for a, d, c in byid[o]) != Counter((a, d, c) for a, d, c in ls):
            rev.append(f"{j.get('journal_id')} is not an exact mirror of {o}")
    rev += [f"journal {k} reversed {v} times" for k, v in seen.items() if v > 1]

    checks = [
        ("debit_credit_balance", unbalanced), ("journal_consistency", consistency + floats),
        ("transaction_uniqueness", uniq), ("account_balance_reconciliation", recon),
        ("reversal_correctness", rev), ("rounding_and_precision", precision),
        ("atomic_transaction_behavior", atomicity)]
    return {"valid": not any(v for _, v in checks),
            "checks": [{"invariant": n, "passed": not v, "violations": v} for n, v in checks],
            "account_balances": jsonable(dict(bal)), "journal_count": len(journals)}
