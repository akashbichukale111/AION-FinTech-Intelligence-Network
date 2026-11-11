"""F. Coverage intelligence: parse real reports only (Cobertura XML, LCOV, JaCoCo XML). Never invents numbers."""
from __future__ import annotations
import difflib, re
import xml.etree.ElementTree as ET
from pathlib import Path

CRIT = re.compile(r"payment|refund|ledger|transaction|webhook|settle|idempot|auth|billing|money|charge", re.I)

def _norm(path, known):
    p = path.replace("\\", "/").lstrip("./")
    if p in known: return p
    best = [k for k in known if k.endswith("/" + p) or p.endswith("/" + k) or k.endswith(p)]
    return min(best, key=len) if best else p

def parse_cobertura(text, known=()):
    root, files = ET.fromstring(text), {}
    for cls in root.iter("class"):
        fn = _norm(cls.get("filename", ""), known)
        d = files.setdefault(fn, {"lines": {}, "branches": {}})
        for ln in cls.iter("line"):
            n, hits = int(ln.get("number")), int(ln.get("hits", 0))
            d["lines"][n] = max(d["lines"].get(n, 0), hits)
            m = re.match(r"\d+%\s*\((\d+)/(\d+)\)", ln.get("condition-coverage", ""))
            if ln.get("branch") == "true" and m:
                c, t = int(m.group(1)), int(m.group(2))
                oc, ot = d["branches"].get(n, (0, 0)); d["branches"][n] = (max(c, oc), max(t, ot))
    return files

def parse_lcov(text, known=()):
    files, cur = {}, None
    for line in text.splitlines():
        if line.startswith("SF:"): cur = files.setdefault(_norm(line[3:].strip(), known), {"lines": {}, "branches": {}})
        elif cur is not None and line.startswith("DA:"):
            n, h = line[3:].split(",")[:2]; cur["lines"][int(n)] = int(h)
        elif cur is not None and line.startswith("BRDA:"):
            n, _, _, taken = line[5:].split(",")[:4]
            c, t = cur["branches"].get(int(n), (0, 0)); cur["branches"][int(n)] = (c + (taken not in ("-", "0")), t + 1)
    return files

def parse_jacoco(text, known=()):
    root, files = ET.fromstring(text), {}
    for pkg in root.iter("package"):
        for sf in pkg.iter("sourcefile"):
            fn = _norm(f"{pkg.get('name', '')}/{sf.get('name')}", known)
            d = files.setdefault(fn, {"lines": {}, "branches": {}})
            for ln in sf.iter("line"):
                n = int(ln.get("nr")); d["lines"][n] = 1 if int(ln.get("ci", 0)) > 0 else 0
                mb, cb = int(ln.get("mb", 0)), int(ln.get("cb", 0))
                if mb + cb: d["branches"][n] = (cb, mb + cb)
    return files

def find_and_parse(workspace, known=()):
    """Return (coverage|None, source_report_path|None, notes)."""
    ws = Path(workspace)
    for pat, fn in (("coverage.xml", parse_cobertura), ("**/cobertura*.xml", parse_cobertura), ("**/lcov.info", parse_lcov),
                    ("**/jacoco.xml", parse_jacoco), ("**/site/jacoco/jacoco.xml", parse_jacoco)):
        for p in sorted(ws.glob(pat)):
            if "node_modules" in p.parts: continue
            try:
                files = fn(p.read_text(errors="replace"), known)
            except Exception as e:
                return None, str(p.relative_to(ws)), [f"report unreadable: {e}"]
            if files: return summarize(files), p.relative_to(ws).as_posix(), []
    return None, None, ["no coverage report found in workspace"]

def summarize(files):
    out, tl, tc, tb, tbc = {}, 0, 0, 0, 0
    for f, d in files.items():
        lines, br = d["lines"], d["branches"]
        cov = sum(1 for h in lines.values() if h > 0)
        bt, bc = sum(t for _, t in br.values()), sum(c for c, _ in br.values())
        out[f] = {"lines_total": len(lines), "lines_covered": cov,
                  "line_pct": round(100 * cov / len(lines), 2) if lines else None,
                  "branches_total": bt, "branches_covered": bc,
                  "branch_pct": round(100 * bc / bt, 2) if bt else None,
                  "uncovered_lines": sorted(n for n, h in lines.items() if h == 0)}
        tl += len(lines); tc += cov; tb += bt; tbc += bc
    return {"files": out, "total": {"lines_total": tl, "lines_covered": tc, "line_pct": round(100 * tc / tl, 2) if tl else None,
                                    "branches_total": tb, "branches_covered": tbc, "branch_pct": round(100 * tbc / tb, 2) if tb else None,
                                    "branch_supported": tb > 0}}

def changed_lines(before, after):
    """1-based line numbers in `after` that were added/modified."""
    if after is None: return []
    a = (before or "").splitlines(); b = after.splitlines(); out = []
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"): out += range(j1 + 1, j2 + 1)
    return out

def changed_uncovered(cov, changes):
    if not cov: return {"available": False, "reason": "no coverage data", "files": []}
    rows, tot, hit = [], 0, 0
    for ch in changes:
        lines = changed_lines(ch.get("before"), ch.get("after"))
        if not lines: continue
        fc = cov["files"].get(ch["path"])
        if not fc:
            rows.append({"path": ch["path"], "status": "not_in_report", "changed_lines": len(lines), "uncovered": [], "critical": bool(CRIT.search(ch["path"]))})
            continue
        unc = sorted(set(lines) & set(fc["uncovered_lines"]))
        measurable = len(set(lines) & set(fc["uncovered_lines"])) + sum(1 for n in lines if n not in fc["uncovered_lines"] and fc["lines_total"])
        tot += len(lines); hit += len(lines) - len(unc)
        rows.append({"path": ch["path"], "status": "measured", "changed_lines": len(lines), "uncovered": unc,
                     "critical": bool(CRIT.search(ch["path"])), "changed_line_pct": round(100 * (len(lines) - len(unc)) / len(lines), 2)})
    return {"available": True, "files": rows, "changed_line_pct": round(100 * hit / tot, 2) if tot else None,
            "critical_untested": [r for r in rows if r["critical"] and (r["uncovered"] or r["status"] == "not_in_report")]}

def compare(before, after):
    if not before or not after: return {"available": False, "reason": "coverage missing on one side"}
    d = {}
    for f in set(before["files"]) | set(after["files"]):
        b, a = before["files"].get(f, {}).get("line_pct"), after["files"].get(f, {}).get("line_pct")
        if b != a: d[f] = {"before": b, "after": a, "delta": None if b is None or a is None else round(a - b, 2)}
    bt, at = before["total"]["line_pct"], after["total"]["line_pct"]
    return {"available": True, "total_before": bt, "total_after": at,
            "total_delta": None if bt is None or at is None else round(at - bt, 2), "files": d}
