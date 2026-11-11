import re
from datetime import datetime, timezone

STOP = {"the","and","for","with","that","this","from","not","are","was","when","has","have","but","get","set",
        "into","than","then","its","can","all","use","our","bug","fix","issue","error"}

def _stem(t):
    return t[:-1] if len(t) > 4 and t.endswith("s") and not t.endswith("ss") else t

def tokens(text):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "").lower()
    return {_stem(t) for t in re.findall(r"[a-z][a-z0-9]{2,}", s) if t not in STOP}

def utcnow():
    return datetime.now(timezone.utc)

def iso(d):
    return d.isoformat() if d else None

def parse_ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None
