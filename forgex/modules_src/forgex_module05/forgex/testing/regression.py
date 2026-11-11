"""E. Regression impact analysis (before/after)."""
from __future__ import annotations
import ast, json, re
from collections import deque
from pathlib import Path

from .coverage import changed_lines

ROUTE = re.compile(r"""@\s*[\w.]+\.(get|post|put|patch|delete|route)\(\s*['"]([^'"]+)['"](?:[^)]*methods\s*=\s*\[([^\]]*)\])?""")
EXPRESS = re.compile(r"""\b(?:app|router)\.(get|post|put|patch|delete)\(\s*['"`]([^'"`]+)['"`]""")
SPRING = re.compile(r"""@(Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*)?\{?\s*"([^"]+)\"""")
MIGRATION_PATH = re.compile(r"migrat|alembic|flyway|db/changelog|\.sql$", re.I)
MODEL_RX = re.compile(r"class \w+\((?:[\w.]*Base|db\.Model|models\.Model|[\w.]*Model)\)|@Entity\b|sequelize\.define")
MIG_RULES = [
    (r"\bDROP\s+TABLE\b|op\.drop_table", "high", "drops a table (data loss, breaks old readers)"),
    (r"\bDROP\s+COLUMN\b|op\.drop_column", "high", "drops a column (data loss, breaks old readers)"),
    (r"\bTRUNCATE\b", "high", "truncates data"),
    (r"\bDELETE\s+FROM\s+\w+\s*;", "high", "DELETE without WHERE"),
    (r"\bRENAME\s+(COLUMN|TO|TABLE)\b|op\.(rename_table|alter_column\([^)]*new_column_name)", "medium", "rename breaks code deployed before the migration"),
    (r"SET\s+NOT\s+NULL|ADD\s+COLUMN[^;]*\bNOT\s+NULL(?![^;]*\bDEFAULT\b)|nullable\s*=\s*False", "medium", "NOT NULL on existing data without a default may fail or lock"),
    (r"ALTER\s+COLUMN[^;]*\bTYPE\b|op\.alter_column\([^)]*type_", "medium", "column type change may rewrite the table / lose precision"),
    (r"CREATE\s+(UNIQUE\s+)?INDEX(?!\s+CONCURRENTLY)", "low", "index build can lock writes on large tables (use CONCURRENTLY where supported)"),
]

def reverse_graph(graph):
    rev = {}
    for f, deps in graph.items():
        for d in deps: rev.setdefault(d, set()).add(f)
    return rev

def dependents(graph, files, depth=3):
    rev, seen, q = reverse_graph(graph), set(files), deque((f, 0) for f in files)
    out = {}
    while q:
        f, d = q.popleft()
        if d >= depth: continue
        for p in rev.get(f, ()):
            if p not in seen: seen.add(p); out[p] = d + 1; q.append((p, d + 1))
    return out

def _routes(text):
    r = {}
    for verb, path, methods in ROUTE.findall(text):
        for v in ([m.strip(" '\"").lower() for m in methods.split(",")] if verb == "route" and methods else [verb if verb != "route" else "get"]):
            r[(v, path)] = True
    for v, p in EXPRESS.findall(text): r[(v, p)] = True
    for v, p in SPRING.findall(text): r[(v.lower() if v != "Request" else "any", p)] = True
    return set(r)

def _models(text):
    out = {}
    try: tree = ast.parse(text)
    except SyntaxError: return out
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and any("BaseModel" in ast.unparse(b) or "Schema" in ast.unparse(b) for b in n.bases):
            out[n.name] = {s.target.id: (ast.unparse(s.annotation), s.value is not None) for s in n.body
                           if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)}
    return out

def _py_api(text):
    out = {}
    try: tree = ast.parse(text)
    except SyntaxError: return out
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_"):
            a = n.args
            pos = a.posonlyargs + a.args
            defaults = len(a.defaults)
            params = [(x.arg, i >= len(pos) - defaults) for i, x in enumerate(pos)]
            params += [(x.arg, d is not None) for x, d in zip(a.kwonlyargs, a.kw_defaults)]
            out[n.name] = {"params": params, "vararg": bool(a.vararg), "kwarg": bool(a.kwarg), "returns": ast.unparse(n.returns) if n.returns else None}
        elif isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
            out[n.name] = {"class": True, "methods": sorted(m.name for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("_"))}
    return out

def api_contract_changes(ch):
    b, a, path, out = ch.get("before") or "", ch.get("after") or "", ch["path"], []
    if path.endswith(".json") and re.search(r"openapi|swagger", path, re.I):
        try:
            pb, pa = json.loads(b or "{}").get("paths", {}), json.loads(a or "{}").get("paths", {})
            for p, ops in pb.items():
                for m in ops:
                    if m not in pa.get(p, {}): out.append({"file": path, "severity": "high", "kind": "endpoint_removed", "detail": f"{m.upper()} {p} removed from OpenAPI spec"})
        except ValueError: pass
        return out
    rb, ra = _routes(b), _routes(a)
    for v, p in sorted(rb - ra):
        out.append({"file": path, "severity": "high", "kind": "endpoint_removed_or_changed", "detail": f"{v.upper()} {p} no longer declared (removed, renamed or method changed)"})
    for v, p in sorted(ra - rb):
        out.append({"file": path, "severity": "low", "kind": "endpoint_added", "detail": f"{v.upper()} {p} added"})
    mb, ma = _models(b), _models(a)
    for name, fields in mb.items():
        if name not in ma: out.append({"file": path, "severity": "high", "kind": "schema_removed", "detail": f"model {name} removed"}); continue
        for f, (t, dflt) in fields.items():
            if f not in ma[name]: out.append({"file": path, "severity": "high", "kind": "field_removed", "detail": f"{name}.{f} removed"})
            elif ma[name][f][0] != t: out.append({"file": path, "severity": "high", "kind": "field_type_changed", "detail": f"{name}.{f}: {t} -> {ma[name][f][0]}"})
        for f, (t, dflt) in ma[name].items():
            if f not in fields and not dflt: out.append({"file": path, "severity": "medium", "kind": "required_field_added", "detail": f"{name}.{f} is new and required (breaks existing request payloads)"})
    return out

def compat_risks(ch):
    b, a, path = ch.get("before"), ch.get("after"), ch["path"]
    if b is None: return []
    out = []
    if a is None:
        return [{"file": path, "severity": "high", "kind": "file_removed", "detail": "file deleted; importers will break"}]
    if path.endswith(".py"):
        pb, pa = _py_api(b), _py_api(a)
        for name, sb in pb.items():
            sa = pa.get(name)
            if sa is None: out.append({"file": path, "severity": "high", "kind": "public_symbol_removed", "detail": f"{name} removed"}); continue
            if sb.get("class"):
                for m in set(sb["methods"]) - set(sa.get("methods", [])): out.append({"file": path, "severity": "high", "kind": "public_method_removed", "detail": f"{name}.{m} removed"})
                continue
            nb, na = [p for p, _ in sb["params"]], [p for p, _ in sa["params"]]
            for p in nb:
                if p not in na and not sa["kwarg"]: out.append({"file": path, "severity": "high", "kind": "parameter_removed", "detail": f"{name}({p}) removed"})
            for p, has_default in sa["params"]:
                if p not in nb and not has_default: out.append({"file": path, "severity": "high", "kind": "required_parameter_added", "detail": f"{name}: new required parameter '{p}'"})
            if [p for p in nb if p in na] != [p for p in na if p in nb]: out.append({"file": path, "severity": "medium", "kind": "parameter_order_changed", "detail": f"{name}: parameter order changed"})
            if sb["returns"] != sa["returns"] and sb["returns"]: out.append({"file": path, "severity": "medium", "kind": "return_type_changed", "detail": f"{name}: {sb['returns']} -> {sa['returns']}"})
    elif path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs")):
        ex = lambda t: set(re.findall(r"export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|interface|type)\s+(\w+)", t))
        for n in ex(b) - ex(a): out.append({"file": path, "severity": "high", "kind": "public_symbol_removed", "detail": f"export {n} removed"})
    elif path.endswith(".java"):
        pm = lambda t: set(re.findall(r"public\s+(?:static\s+)?[\w<>\[\],\s]+?\s+(\w+)\s*\(([^)]*)\)", t))
        for n, args in pm(b) - pm(a): out.append({"file": path, "severity": "medium", "kind": "public_method_changed_or_removed", "detail": f"{n}({args.strip()}) changed or removed"})
    return out

def migration_risks(changes):
    out, paths = [], [c["path"] for c in changes]
    has_mig = any(MIGRATION_PATH.search(p) for p in paths)
    for ch in changes:
        a = ch.get("after") or ""
        if MIGRATION_PATH.search(ch["path"]):
            added = [a.splitlines()[n - 1] for n in changed_lines(ch.get("before"), a) if n - 1 < len(a.splitlines())]
            blob = "\n".join(added)
            for rx, sev, msg in MIG_RULES:
                if re.search(rx, blob, re.I): out.append({"file": ch["path"], "severity": sev, "kind": "migration", "detail": msg})
        elif MODEL_RX.search(a) and a != ch.get("before") and not has_mig and ch.get("before") is not None:
            if _models_changed(ch): out.append({"file": ch["path"], "severity": "medium", "kind": "model_without_migration", "detail": "persistence model changed but no migration is part of the change"})
    return out

def _models_changed(ch):
    cls = lambda t: {m.group(0) for m in re.finditer(r"^\s+\w+\s*[:=]\s*(?:Column|models\.|mapped_column|db\.Column|Field)\(?.*$", t or "", re.M)}
    return cls(ch.get("before")) != cls(ch.get("after"))

def analyze(results, base_results, changes, discovery, changed_extra=()):
    graph = discovery.get("import_graph", {}) if discovery else {}
    tests = discovery.get("test_files", []) if discovery else []
    changed = sorted({c["path"] for c in changes} | set(changed_extra))
    deps = dependents(graph, changed)
    impacted_src = set(changed) | set(deps)
    affected = {}
    for t in tests:
        if t["path"] in changed: affected[t["path"]] = "test file modified"; continue
        for s in t.get("sources", []):
            if s["path"] in impacted_src:
                affected.setdefault(t["path"], f"covers {s['path']}" + ("" if s["path"] in changed else f" (depends on changed code, depth {deps[s['path']]})"))
        for d in graph.get(t["path"], []):
            if d in impacted_src: affected.setdefault(t["path"], f"imports {d}")
    cur = {r["name"]: r["status"] for r in results}
    base = {r["name"]: r["status"] for r in (base_results or [])}
    ok, bad = {"passed"}, {"failed", "error"}
    newly_failing = sorted(n for n, s in cur.items() if s in bad and base.get(n) in ok)
    newly_passing = sorted(n for n, s in cur.items() if s in ok and base.get(n) in bad)
    missing = sorted(n for n in base if n not in cur)
    new_tests = sorted(n for n in cur if n not in base) if base else []
    api, compat = [], []
    for ch in changes:
        api += api_contract_changes(ch); compat += compat_risks(ch)
    mig = migration_risks(changes)
    sev = [x["severity"] for x in api + compat + mig]
    risk = "high" if newly_failing or "high" in sev else "medium" if "medium" in sev or missing else "low"
    return {"comparison_available": bool(base), "changed_files": changed, "affected_tests": [{"path": k, "reason": v} for k, v in sorted(affected.items())],
            "dependent_modules": [{"path": k, "depth": v} for k, v in sorted(deps.items(), key=lambda kv: (kv[1], kv[0]))],
            "newly_failing": newly_failing, "newly_passing": newly_passing, "missing_tests": missing, "new_tests": new_tests,
            "api_contract_changes": [x for x in api if x["kind"] != "endpoint_added"] + [x for x in api if x["kind"] == "endpoint_added"],
            "migration_risks": mig, "compatibility_risks": compat, "risk_level": risk}
