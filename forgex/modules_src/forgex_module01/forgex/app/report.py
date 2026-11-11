from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from . import config

LIMITATIONS = [
    "Static analysis only: no code is executed, so dynamically loaded modules, reflection and runtime routing are not seen.",
    "Import graphs are resolved for Python, JavaScript/TypeScript/Vue (relative and '@/' imports) and Java; Go, PHP, Ruby and others are not resolved.",
    "Route, model and entry-point detection is pattern-based and may miss custom frameworks or unusual syntax.",
    "Outdated/vulnerable dependency checks are NOT performed: no reliable local registry or advisory metadata exists, and none is invented.",
    "yarn.lock, pnpm-lock.yaml, Pipfile.lock, uv.lock, composer.lock and go.sum are recorded but not parsed; transitive data comes from package-lock.json and poetry.lock only.",
    "Pom property placeholders (${...}) and Gradle variables are not resolved; such versions are shown as declared.",
    "Files above the read cap, binary files and ignored directories (node_modules, venvs, build output, .git, ...) are not analysed.",
    "Health indicators are engineering signals, not proof of defects. 'Inferred' findings carry lower certainty than 'direct' ones.",
]


def assemble(name, files, meta, stack, deps, arch, health, reg, tree, duration) -> dict[str, Any]:
    cats = Counter(f.category for f in files)
    langs = stack["languages"]
    limits = list(LIMITATIONS)
    if meta["truncated"]:
        limits.insert(0, f"Scan truncated at {config.MAX_FILES} files; results are partial.")
    if arch["parse_errors"]:
        limits.append(f"{len(arch['parse_errors'])} Python files could not be parsed (syntax errors) and were excluded from the import graph.")
    bad = [m for m in deps["manifests"] if m["error"] and m["kind"] == "manifest"]
    if bad:
        limits.append(f"{len(bad)} manifest(s) could not be parsed: " + ", ".join(m["path"] for m in bad[:5]))
    overview = {
        "name": name, "total_files": len(files),
        "source_files": cats["source"], "test_files": cats["test"], "config_files": cats["config"],
        "infra_files": cats["infra"], "doc_files": cats["docs"], "asset_files": cats["asset"], "other_files": cats["other"],
        "total_lines": sum(f.lines for f in files if f.category in ("source", "test")),
        "total_bytes": sum(f.size for f in files),
        "primary_language": langs[0]["name"] if langs else None,
        "monorepo": arch["monorepo"], "component_count": len(arch["components"]),
        "scan": {"duration_seconds": round(duration, 2), "truncated": meta["truncated"], "skipped": meta["skipped"],
                 "ignored_directories": meta["ignored_dirs"]},
    }
    return {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "overview": overview,
        "stack": stack, "tree": tree, "dependencies": deps, "architecture": arch, "health": health,
        "findings": reg.findings, "limitations": limits,
    }


def to_markdown(r: dict[str, Any]) -> str:
    o, s, a, h, d = r["overview"], r["stack"], r["architecture"], r["health"], r["dependencies"]
    L: list[str] = [f"# Architecture Intelligence Report: {o['name']}", f"_Generated {r['generated_at']} by FORGEX Module 01_", ""]
    L += ["## 1. Repository overview", "",
          f"- Files: **{o['total_files']}** (source {o['source_files']}, tests {o['test_files']}, config {o['config_files']}, "
          f"infra {o['infra_files']}, docs {o['doc_files']}, assets {o['asset_files']}, other {o['other_files']})",
          f"- Code lines (source+tests): **{o['total_lines']}**", f"- Primary language: {o['primary_language'] or 'unknown'}",
          f"- Monorepo / multi-component: {'yes' if o['monorepo'] else 'no'} ({o['component_count']} components)",
          f"- Scan duration: {o['scan']['duration_seconds']}s; truncated: {o['scan']['truncated']}", ""]
    L += ["## 2. Detected technology stack", ""]
    for key, label in (("languages", "Languages"), ("frameworks", "Frameworks"), ("package_managers", "Package managers"),
                       ("infrastructure", "Infrastructure"), ("databases", "Databases")):
        items = s[key]
        L.append(f"**{label}:** " + (", ".join(
            f"{i['name']}" + (f" ({i['percent']}%)" if 'percent' in i else "") + (f" [{i['finding_id']}]" if 'finding_id' in i else "")
            for i in items) if items else "none detected"))
    L += ["", "## 3. Directory structure", "", "```"]

    def walk(n, depth, out):
        if depth > 3:
            return
        for c in n.get("children", []):
            if c["type"] == "dir":
                out.append("  " * depth + f"{c['name']}/ ({c['files']} files)")
                walk(c, depth + 1, out)
    walk(r["tree"], 0, L)
    L += ["```", "", "## 4. Dependency inventory", ""]
    sm = d["summary"]
    L += [f"- Direct declarations: {sm['direct_declared']}; locked entries: {sm['locked_entries']}; "
          f"transitive (lockfile): {sm['transitive_from_lockfiles']}; risky declarations: {sm['risky']}",
          f"- Outdated check: {sm['outdated_check']}", "", "| Name | Spec | Ecosystem | Scope | Manifest:line |", "|---|---|---|---|---|"]
    direct = [x for x in d["dependencies"] if x["source"] == "manifest"]
    for x in direct[:150]:
        L.append(f"| {x['name']} | {x['spec']} | {x['ecosystem']} | {x['scope']} | {x['manifest']}:{x['line'] or '?'} |")
    if len(direct) > 150:
        L.append(f"\n_{len(direct) - 150} more in the JSON report._")
    L += ["", "## 5. Architecture map", ""]
    for c in a["components"]:
        L.append(f"- **{c['name']}** `{c['path']}`: role *{c['role']}* (inferred), frameworks: {', '.join(c['frameworks']) or 'none recognised'} [{c['finding_id']}]")
    L += ["", f"Routes: {len(a['routes'])}; models/schemas: {len(a['models'])}; import edges: {len(a['import_edges'])}; "
          f"data-access sites: {len(a['data_access'])}", ""]
    L += ["## 6. Service and module relationships", ""]
    for rel in a["relationships"][:40]:
        L.append(f"- {rel['from']} -> {rel['to']} ({rel['type']}, {rel['nature']}, {rel['confidence']})")
    for e in a["module_graph"]["edges"][:25]:
        L.append(f"- module {e['from']} -> {e['to']} (x{e['weight']} imports, inferred)")
    L += ["", "## 7. Entry points", ""]
    for e in a["entry_points"][:30]:
        L.append(f"- {e['kind']}: `{e['path']}`{':' + str(e['line']) if e['line'] else ''} ({e['nature']}, {e['confidence']})")
    L += ["", "## 8. Configuration and infrastructure overview", ""]
    for i in s["infrastructure"]:
        L.append(f"- {i['name']}: {', '.join(i['files'][:4])}")
    L.append(f"- Config files at shallow depth: {len(a['configuration'])}")
    L += ["", "## 9. Codebase health indicators", ""]
    for k, v in h["indicators"].items():
        L.append(f"- {k.replace('_', ' ')}: {v}")
    L += ["", "## 10. Findings, confidence and evidence references", "",
          "| ID | Category | Severity | Nature | Confidence | Title | Evidence location |", "|---|---|---|---|---|---|---|"]
    for f in r["findings"][:400]:
        loc = (f["path"] or "-") + (f":{f['lines'][0]}" if f.get("lines") else "")
        L.append(f"| {f['id']} | {f['category']} | {f['severity']} | {f['nature']} | {f['confidence']} | {f['title'].replace('|', '/')} | {loc} |")
    if len(r["findings"]) > 400:
        L.append(f"\n_{len(r['findings']) - 400} more findings in the JSON report._")
    L += ["", "## 11. Analysis limitations", ""] + [f"- {x}" for x in r["limitations"]]
    return "\n".join(L) + "\n"
