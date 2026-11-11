"""AuthN/Z (API keys + roles), rate limiting, redaction, log-injection prevention, audit."""
import hashlib, hmac, os, re, secrets, sys, threading, time
from fastapi import Header, HTTPException
from .config import RATE_PER_MIN
from .db import j, x

PERMS = {"viewer": {"read"}, "ingest": {"ingest"}, "analyst": {"read", "write"},
         "admin": {"read", "write", "ingest", "admin"}}
_keys = None
_rl, _lock = {}, threading.Lock()

def keys():
    """M09_API_KEYS="key1:admin,key2:ingest,key3:viewer". If unset, a random admin key is generated."""
    global _keys
    if _keys is None:
        _keys = {}
        for part in filter(None, (p.strip() for p in os.getenv("M09_API_KEYS", "").split(","))):
            k, _, r = part.partition(":")
            if r in PERMS: _keys[k] = r
        if not _keys:
            k = secrets.token_urlsafe(24); _keys[k] = "admin"
            print(f"[M09] M09_API_KEYS not set -> temporary admin key: {k}", file=sys.stderr)
    return _keys

def need(perm):
    def dep(x_api_key: str = Header(None)):
        if not x_api_key: raise HTTPException(401, "missing X-API-Key")
        role = None
        for k, r in keys().items():
            if hmac.compare_digest(k.encode(), x_api_key.encode()): role = r
        if not role: raise HTTPException(401, "invalid API key")
        if perm not in PERMS[role]: raise HTTPException(403, f"role '{role}' lacks '{perm}' permission")
        actor = hashlib.sha256(x_api_key.encode()).hexdigest()[:8] + ":" + role
        now = time.time()
        with _lock:
            w, c = _rl.get(actor, (now, 0))
            if now - w > 60: w, c = now, 0
            if c >= RATE_PER_MIN: raise HTTPException(429, "rate limit exceeded")
            _rl[actor] = (w, c + 1)
        return actor
    return dep

# ---------- redaction ----------
_PATS = [
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization|cvv|otp)\b([\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}&]+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}\b"), "[JWT]"),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "[AWS_KEY]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
]
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_PHONE = re.compile(r"(?<![\w.])(?:\+91[ -]?)?[6-9]\d{9}(?![\w.])")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENS_KEY = re.compile(r"(?i)(.*[_.-])?(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|cvv|card[_-]?(number|no)?|pan|ssn|aadhaar|pin|private[_-]?key|email|phone|mobile)([_.-].*)?")

def luhn(d):
    s, alt = 0, False
    for ch in reversed(d):
        n = int(ch)
        if alt:
            n *= 2
            if n > 9: n -= 9
        s += n; alt = not alt
    return len(d) >= 13 and s % 10 == 0

def clean(s, n=4096):
    """Log-injection prevention: neutralise CR/LF and control characters, cap length."""
    s = str(s).replace("\r", "\\r").replace("\n", "\\n").replace("\t", " ")
    return _CTRL.sub(lambda m: "\\x%02x" % ord(m.group()), s)[:n]

def redact_text(s):
    for rx, rep in _PATS: s = rx.sub(rep, s)
    s = _CARD.sub(lambda m: "[CARD]" if luhn(re.sub(r"\D", "", m.group())) else m.group(), s)
    return _PHONE.sub("[PHONE]", s)

def safe_text(s, n=4096): return redact_text(clean(s, n))

def redact_obj(o, depth=0):
    if depth > 4: return "[TRUNCATED]"
    if isinstance(o, dict):
        return {clean(k, 64): ("[REDACTED]" if _SENS_KEY.fullmatch(str(k)) else redact_obj(v, depth + 1)) for k, v in list(o.items())[:64]}
    if isinstance(o, (list, tuple)): return [redact_obj(v, depth + 1) for v in o[:50]]
    if isinstance(o, str): return safe_text(o, 1024)
    if isinstance(o, (int, float, bool)) or o is None: return o
    return safe_text(str(o), 256)

def audit(actor, action, target="", detail=None):
    x("INSERT INTO audit(ts,actor,action,target,detail) VALUES(?,?,?,?,?)", (time.time(), actor, action, target, j(redact_obj(detail or {}))))
