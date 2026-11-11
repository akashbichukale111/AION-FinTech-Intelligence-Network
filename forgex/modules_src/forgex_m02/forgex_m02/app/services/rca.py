"""Rule-based root-cause hypothesis generation. Output is ALWAYS a hypothesis, never a confirmed cause."""
from decimal import Decimal as D
from ..util import tokens

def label(c):
    return "high" if c >= D("0.60") else "medium" if c >= D("0.35") else "low"

def build(task_type, text, logs, tpls, scan, ctx, ev):
    ttok, ltok = tokens(text), tokens(logs)
    score = {r["path"]: r["score"] for r in ctx["relevant"]}
    out = []
    for t in tpls:
        for h in t["hypotheses"]:
            sig = tokens(" ".join(h["signals"]))
            matched = []
            for f in scan["files"]:
                if f["is_test"]:
                    continue
                ft = tokens(f["path"]) | set().union(*[tokens(s) for s in f["symbols"]])
                if sig & ft:
                    matched.append((score.get(f["path"], 0), f["path"], sorted(sig & ft)))
            matched.sort(key=lambda x: (-x[0], x[1]))
            top = matched[:4]
            ids = [ev.add("file", p, "signals: " + ", ".join(s), p) for _, p, s in top]
            basis = [("base", D("0.15"))]
            if top: basis.append((f"{len(top)} code location(s) match hypothesis signals", D("0.10") * min(len(top), 3)))
            if sig & ttok: basis.append(("signals present in task description", D("0.10")))
            if sig & ltok: basis.append(("signals present in supplied logs", D("0.10")))
            if len(t["matched_keywords"]) >= 2: basis.append(("multiple template keywords matched", D("0.05")))
            conf = min(sum(v for _, v in basis), D("0.80"))
            out.append({"id": h["id"], "title": h["title"], "explanation": h["explanation"], "status": "hypothesis_unverified",
                        "source": f"template:{t['key']}", "confidence": str(conf), "confidence_label": label(conf),
                        "confidence_basis": [{"factor": n, "points": str(v)} for n, v in basis],
                        "evidence_ids": ids, "alternatives": h["alternatives"], "investigation": h["investigation"],
                        "note": "Not a confirmed root cause until the investigation steps verify it."})
    if not out and task_type in ("bug", "security", "performance", "fintech_reliability") and ctx["relevant"]:
        top = ctx["relevant"][:3]
        conf = min(D("0.15") + D("0.05") * len(top), D("0.30"))
        out.append({"id": "generic.1", "title": "Defect may be located in the highest-ranked relevant files",
                    "explanation": "No domain template matched; this only reflects lexical/structural overlap between the task and Module 01 data.",
                    "status": "hypothesis_unverified", "source": "rule:relevance", "confidence": str(conf), "confidence_label": label(conf),
                    "confidence_basis": [{"factor": "base + relevant files", "points": str(conf)}],
                    "evidence_ids": [r["evidence_id"] for r in top], "alternatives": ["Cause is outside the retrieved files", "Configuration or data issue"],
                    "investigation": ["Reproduce the issue and trace the call path through these files.", "Review recent changes to these files."],
                    "note": "Low-information hypothesis."})
    return out
