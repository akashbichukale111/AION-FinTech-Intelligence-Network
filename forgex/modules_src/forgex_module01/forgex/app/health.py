"""Codebase health signals (engineering signals, not proof of defects)."""
from __future__ import annotations

import posixpath
from collections import defaultdict
from typing import Any

from . import config
from .context import Ctx
from . import secrets_scan


def _sccs(nodes, adj):
    index, low, on, stack, out, i = {}, {}, set(), [], [], 0
    for s in nodes:
        if s in index:
            continue
        index[s] = low[s] = i; i += 1
        stack.append(s); on.add(s)
        work = [(s, iter(adj.get(s, ())))]
        while work:
            v, it = work[-1]
            for w in it:
                if w not in index:
                    index[w] = low[w] = i; i += 1
                    stack.append(w); on.add(w)
                    work.append((w, iter(adj.get(w, ()))))
                    break
                elif w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    u = work[-1][0]
                    low[u] = min(low[u], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop(); on.discard(w); comp.append(w)
                        if w == v:
                            break
                    out.append(comp)
    return out


def analyze(ctx: Ctx, deps: dict[str, Any], arch: dict[str, Any]) -> dict[str, Any]:
    reg = ctx.reg
    ind: dict[str, Any] = {}
    files = ctx.files
    src = [f for f in files if f.category == "source"]

    # very large files
    large = sorted([f for f in files if f.category in ("source", "test") and
                    (f.lines > config.LARGE_FILE_LINES or f.size > config.MAX_READ_BYTES)], key=lambda f: -f.lines)
    ind["large_files"] = len(large)
    for f in large[:25]:
        reg.add("health", f"Very large file ({f.lines} lines, {f.size} bytes)", f.path, "line count", "high", "direct",
                {"lines": f.lines, "bytes": f.size, "threshold_lines": config.LARGE_FILE_LINES},
                severity="medium" if f.lines > 3 * config.LARGE_FILE_LINES else "low")

    # connectivity
    tests = {f.path for f in files if f.category == "test"}
    fan_in: dict[str, int] = defaultdict(int)
    fan_out: dict[str, int] = defaultdict(int)
    adj: dict[str, set[str]] = defaultdict(set)
    first_line: dict[tuple[str, str], int] = {}
    for e in arch["import_edges"]:
        if e["from"] in tests or e["to"] in tests:
            continue
        if e["to"] not in adj[e["from"]]:
            adj[e["from"]].add(e["to"])
            fan_in[e["to"]] += 1
            fan_out[e["from"]] += 1
            first_line[(e["from"], e["to"])] = e["line"]
    hot = sorted(((p, fan_in[p], fan_out[p]) for p in set(fan_in) | set(fan_out)
                  if fan_in[p] >= config.HIGH_FAN_IN or fan_out[p] >= config.HIGH_FAN_OUT), key=lambda t: -(t[1] + t[2]))
    ind["highly_connected_modules"] = len(hot)
    for p, fi, fo in hot[:15]:
        reg.add("health", f"Highly connected module (fan-in {fi}, fan-out {fo})", p, "static import graph", "medium",
                "inferred", {"fan_in": fi, "fan_out": fo, "thresholds": [config.HIGH_FAN_IN, config.HIGH_FAN_OUT]})

    # cycles
    nodes = sorted(set(adj) | {t for s in adj.values() for t in s})
    cycles = [c for c in _sccs(nodes, adj) if len(c) > 1]
    ind["circular_dependency_groups"] = len(cycles)
    for c in sorted(cycles, key=len, reverse=True)[:20]:
        cs = sorted(c)
        ln = next((first_line[(a, b)] for a in cs for b in cs if (a, b) in first_line), None)
        reg.add("health", f"Circular import group of {len(cs)} files", cs[0], "Tarjan SCC over resolved imports", "high",
                "inferred", {"files": cs[:20], "size": len(cs)}, lines=[ln] if ln else None, severity="medium")

    # duplicates
    groups: dict[str, list[str]] = defaultdict(list)
    for f in files:
        if f.sha and f.size >= config.DUP_MIN_BYTES and f.category in ("source", "test", "config") \
                and not posixpath.basename(f.path).endswith((".lock", "package-lock.json")):
            groups[f.sha].append(f.path)
    dups = [v for v in groups.values() if len(v) > 1]
    ind["duplicate_content_groups"] = len(dups)
    for v in sorted(dups, key=len, reverse=True)[:20]:
        reg.add("health", f"{len(v)} files with identical content", sorted(v)[0], "SHA-256 content hash", "high", "direct",
                {"paths": sorted(v)[:10]}, severity="low")

    # configuration gaps
    names = {posixpath.basename(f.path).lower() for f in files}
    code_langs = {f.language for f in src}

    def gap(title, sev, ev, conf="medium"):
        reg.add("health", title, None, "absence in scanned files", conf, "direct", ev, severity=sev)

    if src:
        if not any(n.startswith("readme") for n in names):
            gap("No README found", "low", {"checked": "README*"})
        if not any(f.category == "test" for f in files):
            gap("No test files detected", "medium", {"checked": "test dirs and test-file naming patterns"})
        if ".gitignore" not in names:
            gap("No .gitignore found", "low", {"checked": ".gitignore"})
        if not any(f.category == "infra" and (".github/workflows" in f.path or "gitlab-ci" in f.path or "jenkins" in f.path.lower()
                                               or ".circleci" in f.path or "azure-pipelines" in f.path) for f in files):
            gap("No CI/CD configuration detected", "low", {"checked": "GitHub Actions, GitLab CI, Jenkins, CircleCI, Azure"})
        if code_langs & {"Python", "JavaScript", "TypeScript", "Java", "PHP", "Go"} and not deps["manifests"]:
            gap("Source code present but no dependency manifest found", "medium", {"languages": sorted(code_langs)})
    for m in deps["manifests"]:
        if m["lockfile_expected"] and not m["lockfile"]:
            reg.add("health", f"{m['type']} has no adjacent lockfile", m["path"], "manifest/lockfile pairing", "high",
                    "direct", {"manifest": m["path"], "note": "builds may not be reproducible"}, severity="low")
    by_dir = defaultdict(set)
    for m in deps["manifests"]:
        if m["kind"] == "lockfile" and m["ecosystem"] == "npm":
            by_dir[posixpath.dirname(m["path"])].add(posixpath.basename(m["path"]))
    for d, s in by_dir.items():
        if len(s) > 1:
            reg.add("health", "Multiple JavaScript lockfiles in one directory", posixpath.join(d, sorted(s)[0]),
                    "lockfile inventory", "high", "direct", {"lockfiles": sorted(s)}, severity="low")
    pyd = defaultdict(set)
    for m in deps["manifests"]:
        if m["ecosystem"] == "pypi" and m["kind"] == "manifest" and m["dependency_count"]:
            pyd[posixpath.dirname(m["path"])].add(m["path"])
    for d, s in pyd.items():
        kinds = {posixpath.basename(x) for x in s}
        if "pyproject.toml" in kinds and "requirements.txt" in kinds:
            reg.add("health", "Python dependencies declared in both pyproject.toml and requirements.txt", sorted(s)[0],
                    "manifest comparison", "medium", "inferred", {"files": sorted(s)}, severity="low")

    # organisation
    per_dir: dict[str, int] = defaultdict(int)
    for f in src:
        per_dir[posixpath.dirname(f.path)] += 1
    root_src = per_dir.get("", 0)
    if root_src > 15:
        reg.add("health", f"{root_src} source files at repository root", None, "directory statistics", "medium", "inferred",
                {"count": root_src}, severity="low")
    for d, n in sorted(per_dir.items(), key=lambda kv: -kv[1])[:3]:
        if d and n > 40:
            reg.add("health", f"{n} source files in a single directory", d, "directory statistics", "medium", "inferred",
                    {"count": n}, severity="low")
    deep = [f for f in files if f.path.count("/") > 8]
    if deep:
        reg.add("health", f"{len(deep)} files nested deeper than 8 levels", deep[0].path, "path depth", "high", "direct",
                {"examples": [f.path for f in deep[:5]]}, severity="low")

    # disconnected code
    entry_paths = {e["path"] for e in arch["entry_points"]}
    framework_files = {r["file"] for r in arch["routes"]} | {m["file"] for m in arch["models"]}
    disc = [f.path for f in src if f.language in ("Python", "JavaScript", "TypeScript", "Java")
            and f.path not in adj and f.path not in fan_in and f.path not in entry_paths
            and f.path not in framework_files and posixpath.basename(f.path) != "__init__.py" and f.lines > 5]
    ind["potentially_disconnected_files"] = len(disc)
    if disc:
        reg.add("health", f"{len(disc)} source files have no resolved imports in or out", disc[0], "import graph analysis", "low",
                "inferred", {"examples": disc[:25], "caveat": "may be loaded dynamically, via reflection, config or tooling"},
                severity="info")

    ind["possible_secrets"] = secrets_scan.scan(ctx)
    ind["parse_errors"] = len(arch["parse_errors"])
    ind["test_to_source_links"] = len(arch["test_links"])
    linked = {t["source"] for t in arch["test_links"]}
    ind["source_files_with_linked_tests_pct"] = round(100 * len(linked) / len(src), 1) if src else None
    return {"indicators": ind, "hotspots": [{"path": p, "fan_in": a, "fan_out": b} for p, a, b in hot[:15]],
            "circular_groups": [sorted(c)[:20] for c in cycles[:20]]}
