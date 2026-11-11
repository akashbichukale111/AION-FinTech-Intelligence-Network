"""Dependency manifest/lockfile parsing. Local evidence only: no network, no invented versions."""
from __future__ import annotations

import json
import posixpath
import re
import tomllib
from typing import Any, Callable

from .context import Ctx
from .evidence import snip


def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def _find(lines: list[str], needle: str) -> int | None:
    n = needle.lower()
    for i, l in enumerate(lines, 1):
        if n in l.lower():
            return i
    return None


def _dep(name, spec, version, eco, scope, direct, manifest, line, raw, source="manifest", risk=None):
    return {"name": name, "spec": spec, "version": version, "ecosystem": eco, "scope": scope,
            "direct": direct, "manifest": manifest, "line": line, "raw": snip(raw),
            "source": source, "risk": risk}


def _req(s: str):
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$", s.strip())
    if not m:
        return None
    spec = m.group(2).split(";")[0].strip()
    pin = re.match(r"^===?\s*([^\s,]+)$", spec)
    return m.group(1), spec, (pin.group(1) if pin else None)


# ---- parsers -------------------------------------------------------------------------------
def p_requirements(ctx: Ctx, path: str):
    out = []
    scope = "dev" if re.search(r"(dev|test)", posixpath.basename(path).lower()) else "runtime"
    for i, l in enumerate(ctx.lines(path), 1):
        s = re.split(r"\s+#", l.strip())[0].strip()
        if not s or s.startswith(("#", "-r", "-c", "--")):
            continue
        if s.startswith("-e"):
            s = s[2:].strip()
        if s.startswith(("git+", "http://", "https://")):
            m = re.search(r"#egg=([\w.-]+)", s)
            out.append(_dep(m.group(1) if m else s[:60], s, None, "pypi", scope, True, path, i, s, risk="vcs-url"))
            continue
        r = _req(s)
        if not r:
            continue
        n, spec, pin = r
        risk = "vcs-url" if spec.startswith("@") else ("unpinned" if not spec else None)
        out.append(_dep(n, spec or "*", pin, "pypi", scope, True, path, i, s, risk=risk))
    return out


def p_pyproject(ctx: Ctx, path: str):
    data = tomllib.loads(ctx.text(path))
    lines = ctx.lines(path)
    out = []

    def add_req(s, scope):
        r = _req(s)
        if r:
            n, spec, pin = r
            out.append(_dep(n, spec or "*", pin, "pypi", scope, True, path, _find(lines, n), s,
                            risk="unpinned" if not spec else None))

    proj = data.get("project", {})
    for s in proj.get("dependencies", []):
        add_req(s, "runtime")
    for lst in proj.get("optional-dependencies", {}).values():
        for s in lst:
            add_req(s, "optional")
    for lst in data.get("dependency-groups", {}).values():
        for s in lst:
            if isinstance(s, str):
                add_req(s, "dev")
    poetry = data.get("tool", {}).get("poetry", {})

    def poet(tbl, scope):
        for n, v in (tbl or {}).items():
            if n.lower() == "python":
                continue
            spec = v if isinstance(v, str) else (v.get("version", "*") if isinstance(v, dict) else "*")
            pin = spec if re.fullmatch(r"\d+(\.\d+)*", spec) else None
            out.append(_dep(n, spec, pin, "pypi", scope, True, path, _find(lines, n), f"{n} = {spec}",
                            risk="unpinned" if spec == "*" else None))

    poet(poetry.get("dependencies"), "runtime")
    poet(poetry.get("dev-dependencies"), "dev")
    for g in poetry.get("group", {}).values():
        poet(g.get("dependencies"), "dev")
    return out


def p_package_json(ctx: Ctx, path: str):
    data = json.loads(ctx.text(path))
    if not isinstance(data, dict):
        raise ValueError("package.json is not an object")
    lines = ctx.lines(path)
    out = []
    for key, scope in (("dependencies", "runtime"), ("devDependencies", "dev"),
                       ("peerDependencies", "peer"), ("optionalDependencies", "optional")):
        for n, v in (data.get(key) or {}).items():
            v = str(v)
            pin = v if re.fullmatch(r"\d+\.\d+\.\d+([-+][\w.]+)?", v) else None
            if v.strip() in ("*", "", "latest", "x"):
                risk = "wildcard"
            elif re.match(r"^(git|github:|gitlab:|https?:|file:|link:)", v) or \
                    re.match(r"^[\w.-]+/[\w.-]+(#.*)?$", v):
                risk = "vcs-url"
            else:
                risk = None
            out.append(_dep(n, v, pin, "npm", scope, True, path, _find(lines, f'"{n}"'), f'"{n}": "{v}"', risk=risk))
    return out


def p_package_lock(ctx: Ctx, path: str):
    data = json.loads(ctx.text(path))
    out = []
    pk = data.get("packages")
    if isinstance(pk, dict):
        root = pk.get("", {}) or {}
        direct = set()
        for k in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            direct |= set((root.get(k) or {}).keys())
        for key, val in pk.items():
            if not key or not isinstance(val, dict):
                continue
            n = key.rsplit("node_modules/", 1)[-1]
            out.append(_dep(n, val.get("version", ""), val.get("version"), "npm",
                            "dev" if val.get("dev") else "runtime",
                            n in direct and key.count("node_modules/") == 1, path, None,
                            f"{n}@{val.get('version')}", source="lockfile"))
    elif isinstance(data.get("dependencies"), dict):  # lockfile v1
        for n, v in data["dependencies"].items():
            ver = v.get("version") if isinstance(v, dict) else None
            out.append(_dep(n, ver or "", ver, "npm", "runtime", None, path, None, f"{n}@{ver}", source="lockfile"))
    return out


def p_poetry_lock(ctx: Ctx, path: str):
    data = tomllib.loads(ctx.text(path))
    return [_dep(p["name"], p.get("version", ""), p.get("version"), "pypi",
                 "dev" if p.get("category") == "dev" else "runtime", None, path, None,
                 f"{p['name']}=={p.get('version')}", source="lockfile")
            for p in data.get("package", []) if isinstance(p, dict) and "name" in p]


def p_pom(ctx: Ctx, path: str):
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), ctx.text(path), flags=re.S)
    out = []

    def tag(block, t):
        m = re.search(rf"<{t}>\s*([^<]+?)\s*</{t}>", block)
        return m.group(1) if m else None

    for m in re.finditer(r"<(dependency|parent)>(.*?)</\1>", text, re.S):
        g, a, v, sc = (tag(m.group(2), t) for t in ("groupId", "artifactId", "version", "scope"))
        if not a:
            continue
        line = text[: m.start()].count("\n") + 1
        pin = v if v and not v.startswith("${") else None
        scope = "parent" if m.group(1) == "parent" else ("dev" if sc == "test" else "runtime")
        risk = "wildcard" if v and v.upper() in ("LATEST", "RELEASE") else None
        out.append(_dep(f"{g}:{a}" if g else a, v or "(managed)", pin, "maven", scope, True, path, line,
                        f"{g}:{a}:{v}", risk=risk))
    return out


RE_GRADLE = re.compile(
    r"""\b(implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|"""
    r"""annotationProcessor|classpath|compile|testCompile)\s*\(?\s*["']([\w.\-]+):([\w.\-]+)(?::([^"')]+))?["']""")


def p_gradle(ctx: Ctx, path: str):
    out = []
    for i, l in enumerate(ctx.lines(path), 1):
        m = RE_GRADLE.search(l)
        if m:
            v = m.group(4)
            out.append(_dep(f"{m.group(2)}:{m.group(3)}", v or "(managed)", v if v and "$" not in v else None,
                            "maven", "dev" if m.group(1).startswith("test") else "runtime", True, path, i, l))
    return out


def p_gomod(ctx: Ctx, path: str):
    out, in_block = [], False
    for i, l in enumerate(ctx.lines(path), 1):
        s = l.strip()
        if s.startswith("require ("):
            in_block = True
            continue
        if in_block and s == ")":
            in_block = False
            continue
        m = None
        if in_block:
            m = re.match(r"^(\S+)\s+(v\S+)(\s*//\s*indirect)?", s)
        elif s.startswith("require "):
            m = re.match(r"^require\s+(\S+)\s+(v\S+)(\s*//\s*indirect)?", s)
        if m:
            out.append(_dep(m.group(1), m.group(2), m.group(2), "go", "runtime", not m.group(3), path, i, s))
    return out


def p_composer(ctx: Ctx, path: str):
    data = json.loads(ctx.text(path))
    lines = ctx.lines(path)
    out = []
    for key, scope in (("require", "runtime"), ("require-dev", "dev")):
        for n, v in (data.get(key) or {}).items():
            if n == "php" or n.startswith("ext-"):
                continue
            v = str(v)
            risk = "wildcard" if v in ("*", "dev-master", "dev-main") else None
            pin = v if re.fullmatch(r"\d+\.\d+(\.\d+)?", v) else None
            out.append(_dep(n, v, pin, "composer", scope, True, path, _find(lines, f'"{n}"'), f'"{n}": "{v}"', risk=risk))
    return out


def _pick(name: str):
    if re.fullmatch(r"requirements[\w.-]*\.txt|constraints\.txt", name):
        return "requirements", "pypi", "manifest", p_requirements
    table: dict[str, tuple[str, str, str, Callable | None]] = {
        "pyproject.toml": ("pyproject", "pypi", "manifest", p_pyproject),
        "package.json": ("package.json", "npm", "manifest", p_package_json),
        "package-lock.json": ("package-lock.json", "npm", "lockfile", p_package_lock),
        "npm-shrinkwrap.json": ("package-lock.json", "npm", "lockfile", p_package_lock),
        "poetry.lock": ("poetry.lock", "pypi", "lockfile", p_poetry_lock),
        "pom.xml": ("pom.xml", "maven", "manifest", p_pom),
        "build.gradle": ("gradle", "maven", "manifest", p_gradle),
        "build.gradle.kts": ("gradle", "maven", "manifest", p_gradle),
        "go.mod": ("go.mod", "go", "manifest", p_gomod),
        "composer.json": ("composer.json", "composer", "manifest", p_composer),
        "yarn.lock": ("yarn.lock", "npm", "lockfile", None),
        "pnpm-lock.yaml": ("pnpm-lock.yaml", "npm", "lockfile", None),
        "composer.lock": ("composer.lock", "composer", "lockfile", None),
        "pipfile.lock": ("Pipfile.lock", "pypi", "lockfile", None),
        "uv.lock": ("uv.lock", "pypi", "lockfile", None),
        "go.sum": ("go.sum", "go", "lockfile", None),
    }
    return table.get(name)


LOCK_FOR = {
    "npm": {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"},
    "composer": {"composer.lock"},
    "go": {"go.sum"},
}


def analyze(ctx: Ctx) -> dict[str, Any]:
    reg = ctx.reg
    manifests: list[dict] = []
    deps: list[dict] = []
    names_by_dir: dict[str, set[str]] = {}
    for f in ctx.files:
        names_by_dir.setdefault(posixpath.dirname(f.path), set()).add(posixpath.basename(f.path).lower())
    for f in ctx.files:
        base = posixpath.basename(f.path).lower()
        spec = _pick(base)
        if not spec:
            continue
        mtype, eco, kind, fn = spec
        rec = {"path": f.path, "type": mtype, "ecosystem": eco, "kind": kind,
               "parsed": fn is not None, "dependency_count": 0, "error": None, "lockfile": None,
               "lockfile_expected": False}
        if fn is None:
            rec["error"] = "Lockfile format not parsed (presence recorded only)."
        else:
            try:
                items = fn(ctx, f.path)
                rec["dependency_count"] = len(items)
                deps.extend(items[:8000])
            except Exception as e:  # malformed manifests must never abort a scan
                rec["parsed"] = False
                rec["error"] = f"{type(e).__name__}: {snip(e, 120)}"
        d = posixpath.dirname(f.path)
        siblings = names_by_dir.get(d, set())
        if kind == "manifest" and eco in LOCK_FOR and base in ("package.json", "composer.json", "go.mod"):
            rec["lockfile_expected"] = True
            found = sorted(siblings & LOCK_FOR[eco])
            rec["lockfile"] = posixpath.join(d, found[0]) if found else None
        manifests.append(rec)

    # direct / transitive marking for poetry.lock
    direct_names: dict[str, set[str]] = {}
    for d in deps:
        if d["source"] == "manifest" and d["ecosystem"] == "pypi":
            direct_names.setdefault(posixpath.dirname(d["manifest"]), set()).add(_norm(d["name"]))
    for d in deps:
        if d["manifest"].endswith("poetry.lock"):
            s = direct_names.get(posixpath.dirname(d["manifest"]))
            d["direct"] = (_norm(d["name"]) in s) if s else None

    # manifest findings
    for m in manifests:
        if m["kind"] == "manifest" and m["parsed"]:
            sample = [x["raw"] for x in deps if x["manifest"] == m["path"]][:5]
            reg.add("dependency", f"{m['type']} manifest parsed: {m['dependency_count']} declared dependencies",
                    m["path"], "manifest parser", "high", "direct",
                    {"dependency_count": m["dependency_count"], "sample": sample})
        elif m["error"] and m["parsed"] is False and m["kind"] == "manifest":
            reg.add("dependency", "Manifest could not be parsed", m["path"], "manifest parser", "high",
                    "unknown", {"error": m["error"]}, severity="low")
    # risky declarations
    per_manifest_unpinned: dict[str, list[dict]] = {}
    risky = 0
    for d in deps:
        if d["source"] != "manifest" or not d["risk"]:
            continue
        if d["risk"] == "unpinned" and d["ecosystem"] == "pypi":
            per_manifest_unpinned.setdefault(d["manifest"], []).append(d)
        elif risky < 40:
            risky += 1
            reg.add("dependency", f"Potentially risky declaration ({d['risk']}): {d['name']}", d["manifest"],
                    "manifest parser", "high", "direct", {"declaration": d["raw"], "risk": d["risk"]},
                    lines=[d["line"]] if d["line"] else None, severity="low")
    for mp, items in per_manifest_unpinned.items():
        reg.add("dependency", f"{len(items)} dependencies declared without any version constraint", mp,
                "manifest parser", "high", "direct",
                {"examples": [i["raw"] for i in items[:8]]},
                lines=[items[0]["line"]] if items[0]["line"] else None, severity="low")

    manifest_deps = [d for d in deps if d["source"] == "manifest"]
    lock_deps = [d for d in deps if d["source"] == "lockfile"]
    by_eco: dict[str, int] = {}
    for d in manifest_deps:
        by_eco[d["ecosystem"]] = by_eco.get(d["ecosystem"], 0) + 1
    summary = {
        "manifest_files": len([m for m in manifests if m["kind"] == "manifest"]),
        "lockfiles": [m["path"] for m in manifests if m["kind"] == "lockfile"],
        "direct_declared": len(manifest_deps),
        "locked_entries": len(lock_deps),
        "transitive_from_lockfiles": len([d for d in lock_deps if d["direct"] is False]),
        "by_ecosystem": by_eco,
        "exactly_pinned": len([d for d in manifest_deps if d["version"]]),
        "risky": len([d for d in manifest_deps if d["risk"]]),
        "outdated_check": "not performed (no reliable local registry metadata; versions are never invented)",
    }
    return {"manifests": manifests, "dependencies": deps, "summary": summary}
