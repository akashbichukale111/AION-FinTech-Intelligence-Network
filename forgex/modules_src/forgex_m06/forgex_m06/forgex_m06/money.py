"""Money primitives. Decimal only - floats are rejected everywhere in the lab."""
from __future__ import annotations
import hashlib, json, re
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_DOWN

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
FEE_RATE = Decimal("0.029")


class MoneyError(ValueError):
    pass


def D(v) -> Decimal:
    if isinstance(v, (bool, float)):
        raise MoneyError("float/bool not allowed for money; pass str, int or Decimal")
    if isinstance(v, Decimal):
        d = v
    else:
        try:
            d = Decimal(str(v).strip())
        except InvalidOperation:
            raise MoneyError(f"invalid amount {v!r}")
    if not d.is_finite():
        raise MoneyError(f"non-finite amount {v!r}")
    return d


def q(v, rounding=ROUND_HALF_EVEN) -> Decimal:
    return D(v).quantize(CENT, rounding=rounding)


def scale(d: Decimal) -> int:
    return max(0, -d.as_tuple().exponent)


def allocate(total, parts: int) -> list[Decimal]:
    """Largest-remainder allocation: shares always sum exactly to total."""
    cents = int(q(total) / CENT)
    base, rem = divmod(cents, parts)
    return [q(Decimal(base + (1 if i < rem else 0)) * CENT) for i in range(parts)]


def jsonable(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(x) for x in o]
    if isinstance(o, (set, frozenset)):
        return sorted(jsonable(x) for x in o)
    return o


def canon(o) -> str:
    return json.dumps(jsonable(o), sort_keys=True, separators=(",", ":"), default=str)


def digest(o, n: int = 64) -> str:
    return hashlib.sha256(canon(o).encode()).hexdigest()[:n]


def _luhn(s: str) -> bool:
    t, alt = 0, False
    for ch in reversed(s):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        t += d
        alt = not alt
    return t % 10 == 0


def contains_pan(obj) -> bool:
    """Reject payloads that look like real card numbers (13-19 digits passing Luhn)."""
    if isinstance(obj, str):
        return any(_luhn(m) for m in re.findall(r"(?<!\d)\d{13,19}(?!\d)", obj))
    if isinstance(obj, dict):
        return any(contains_pan(k) or contains_pan(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(contains_pan(v) for v in obj)
    return False
