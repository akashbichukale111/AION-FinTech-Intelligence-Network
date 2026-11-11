"""Context retrieval over Module 01 scan data (no repository scanning here)."""
import re
from datetime import timedelta
from ..util import tokens, utcnow, parse_ts

STALE_DAYS = 7

class Evidence:
    """Structured evidence references pointing into Module 01 data (no copying of large reports)."""
    def __init__(self, repo_id, scan_id):
        self.repo_id, self.scan_id, self.items, self._idx = repo_id, scan_id, [], {}
    def add(self, kind, ref, detail, path=None):
        k = (kind, ref)
        if k in self._idx:
            return self._idx[k]
        eid = f"E{len(self.items) + 1}"
        self.items.append({"id": eid, "kind": kind, "ref": ref, "path": path, "detail": detail,
                           "source": "module01" if kind != "task_text" and kind != "log" else "task",
                           "repo_id": self.repo_id, "scan_id": self.scan_id})
        self._idx[k] = eid
        return eid

def mod_name(p):
    m = re.sub(r"\.[A-Za-z0-9]+$", "", p).replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m

def resolve(imp, mods):
    n = re.sub(r"\.(py|ts|js|tsx|jsx|java|go)$", "", imp.strip().lstrip("./").replace("/", "."))
    for p, m in mods.items():
        if m == n or m.endswith("." + n):
            return p
    return None

def freshness(scan):
    ts = parse_ts(scan.get("scanned_at"))
    if not ts:
        return {"stale": True, "age_days": None, "warning": "Scan has no valid timestamp; freshness unknown."}
    age = utcnow() - ts
    stale = age > timedelta(days=STALE_DAYS)
    return {"stale": stale, "age_days": round(age.total_seconds() / 86400, 1),
            "warning": f"Scan is {age.days} days old; re-scan in Module 01 before relying on this plan." if stale else None}

def build_graph(scan):
    files = scan["files"]
    mods = {f["path"]: mod_name(f["path"]) for f in files}
    fwd = {f["path"]: sorted({r for i in f["imports"] if (r := resolve(i, mods)) and r != f["path"]}) for f in files}
    rev = {p: [] for p in fwd}
    for p, ds in fwd.items():
        for d in ds:
            rev[d].append(p)
    return fwd, rev

def retrieve(scan, text, logs, signals, ev: Evidence, limit=12):
    ttok, ltok = tokens(text), tokens(logs)
    sig = tokens(" ".join(signals))
    full = f"{text}\n{logs}".lower()
    fwd, rev = build_graph(scan)
    scored = []
    for f in scan["files"]:
        if f["is_test"]:
            continue
        ptok = tokens(f["path"])
        stok = set().union(*[tokens(s) for s in f["symbols"]])
        s, why = 0, []
        if f["path"].lower() in full:
            s += 8; why.append("path named in task/logs")
        if o := ptok & (ttok | ltok):
            s += 3 * len(o); why.append("path terms: " + ", ".join(sorted(o)))
        if o := stok & (ttok | ltok):
            s += 2 * len(o); why.append("symbols: " + ", ".join(sorted(o)))
        if o := (ptok | stok) & sig:
            s += len(o); why.append("domain signals: " + ", ".join(sorted(o)))
        if s:
            scored.append((s, f, why))
    scored.sort(key=lambda x: (-x[0], x[1]["path"]))
    relevant = [{"path": f["path"], "score": s, "reasons": w, "language": f.get("language"), "kind": f["kind"],
                 "evidence_id": ev.add("file", f["path"], "; ".join(w), f["path"])} for s, f, w in scored[:limit]]
    rpaths = {r["path"] for r in relevant}
    stems = set().union(*[tokens(p.rsplit("/", 1)[-1]) for p in rpaths]) if rpaths else set()
    tests = []
    for f in scan["files"]:
        if not f["is_test"]:
            continue
        linked = [d for d in fwd[f["path"]] if d in rpaths]
        if linked or (tokens(f["path"].rsplit("/", 1)[-1]) & stems):
            why = ("imports " + ", ".join(linked)) if linked else "name matches relevant module"
            tests.append({"path": f["path"], "reason": why, "evidence_id": ev.add("test", f["path"], why, f["path"])})
    dependents = {}
    for p in rpaths:
        for d in rev.get(p, []):
            if d not in rpaths:
                dependents.setdefault(d, []).append(p)
    deps_out = [{"path": d, "depends_on": sorted(v), "evidence_id": ev.add("dependency", d, "imports " + ", ".join(sorted(v)), d)}
                for d, v in sorted(dependents.items())]
    scope = rpaths | set(dependents)
    apis = [{**a, "evidence_id": ev.add("api", f"{a.get('method')} {a.get('path')}", f"served by {a.get('file')}", a.get("file"))}
            for a in scan["apis"] if a.get("file") in scope]
    allsig = ttok | ltok | sig
    configs = []
    for c in scan["configs"]:
        ct = tokens(c.get("path", "")) | set().union(*[tokens(k) for k in c.get("keys", [])])
        if ct & allsig:
            configs.append({**c, "evidence_id": ev.add("config", c["path"], "keys: " + ", ".join(c.get("keys", [])[:6]), c["path"])})
    imported_roots = {i.split(".")[0].split("/")[0] for f in scan["files"] if f["path"] in rpaths for i in f["imports"]}
    pkgs = [{**d, "evidence_id": ev.add("package", d["name"], f"{d.get('name')} {d.get('version', '')} ({d.get('manifest', '')})")}
            for d in scan["dependencies"] if d.get("name") and (d["name"].lower() in imported_roots or tokens(d["name"]) & (ttok | ltok))]
    return {"relevant": relevant, "tests": tests, "dependents": deps_out, "apis": apis, "configs": configs, "packages": pkgs,
            "edges": [[d, p] for d, v in dependents.items() for p in v]}
