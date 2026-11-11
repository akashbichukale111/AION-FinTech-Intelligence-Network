"""Secret detection. Values are NEVER stored: only location, type and a redacted marker."""
from __future__ import annotations

import posixpath
import re

from .context import Ctx

PATTERNS = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
]
ASSIGN = re.compile(r"""(?i)\b([\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key)[\w.-]*)\s*[:=]\s*["']([^"'\s]{6,})["']""")
ENV_LINE = re.compile(r"^\s*([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)[A-Z0-9_]*)\s*=\s*(\S{6,})")
PLACEHOLDER = re.compile(r"(?i)^(\$\{|<|%|\{\{)|changeme|your[_-]|example|xxx|dummy|placeholder|^test|password$|^secret$|\*\*\*|redacted")


def scan(ctx: Ctx, limit: int = 100) -> int:
    n = 0
    for f in ctx.files:
        if f.category not in ("source", "config", "infra", "test") or f.size > 500_000:
            continue
        name = posixpath.basename(f.path).lower()
        is_env = name.startswith(".env") and not name.endswith((".example", ".sample", ".template"))
        for i, l in enumerate(ctx.lines(f.path), 1):
            if n >= limit:
                return n
            hit = None
            for label, rx in PATTERNS:
                if rx.search(l):
                    hit = (label, f"{label} pattern [REDACTED]")
                    break
            if not hit:
                m = ASSIGN.search(l)
                if m and not PLACEHOLDER.search(m.group(2)):
                    hit = ("Hard-coded credential assignment", f"{m.group(1)} = [REDACTED]")
                elif is_env:
                    m = ENV_LINE.match(l)
                    if m and not PLACEHOLDER.search(m.group(2)):
                        hit = ("Credential in .env file", f"{m.group(1)}=[REDACTED]")
            if hit:
                n += 1
                ctx.reg.add("security", f"Possible secret: {hit[0]}", f.path, "regex pattern match",
                            "medium" if f.category != "test" else "low", "direct",
                            {"type": hit[0], "snippet": hit[1], "value": "[REDACTED]"}, lines=[i], severity="high" if "key" in hit[0].lower() else "medium")
    return n
