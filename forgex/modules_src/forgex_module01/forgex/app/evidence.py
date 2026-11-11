"""Evidence registry: every finding carries path, lines, method, confidence, evidence."""
from __future__ import annotations

from typing import Any


def snip(s: Any, n: int = 200) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


class Registry:
    def __init__(self) -> None:
        self.findings: list[dict[str, Any]] = []

    def add(
        self,
        category: str,
        title: str,
        path: str | None,
        method: str,
        confidence: str,          # high | medium | low
        nature: str,              # direct | inferred | unknown
        evidence: Any,
        lines: list[int] | None = None,
        severity: str = "info",   # info | low | medium | high
    ) -> str:
        fid = f"F-{len(self.findings) + 1:04d}"
        self.findings.append(
            {
                "id": fid, "category": category, "title": title, "path": path,
                "lines": lines, "method": method, "confidence": confidence,
                "nature": nature, "severity": severity, "evidence": evidence,
            }
        )
        return fid
